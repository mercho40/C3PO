# C3PO — Stack Decisions

Technology choices for the autonomy stack, with the reasoning behind each so they can be
revisited on evidence rather than re-argued from scratch.

Companion docs: `SPEC.md` (design), `MENTAL-MODEL.md` (how it fits together),
`ROBOT-INVENTORY.md` (what the hardware presents).

Decided **2026-08-11**. Hardware: Jetson Orin NX **16 GB**, Livox Mid-360, RealSense D435i.

---

## D1 — Nothing from the `gemm` stack

**Decided:** C3PO shares no code, packages or configuration with the colleague's `gemm`
workspace on the robot.

**But note the distinction that actually matters:** *don't depend on their packages* is not
the same as *don't use open source*. Running our own Nav2 with our own config is ours.
Importing `gemm_navigation` is not. Every third-party component below is one we own the
deployment of.

**Consequence:** two independent stacks run on one robot with **no technical interlock**.
That is a standing operational risk, not a solved problem — see `MENTAL-MODEL.md` §8.

---

## D2 — ROS 2 is a perception *subsystem*, not our architecture

**Decided:** run ROS 2 Humble in **our own container** for perception and navigation only.
C3PO's Python keeps **zero ROS dependencies** and consumes the output as raw DDS topics.

**Why this works:** ROS 2 *is* DDS. Our bridge already speaks CycloneDDS on domain 0, and
we already parse a ROS type off the wire (`nav_msgs::msg::dds_::Odometry_` on
`rt/state_estimator/*`). A ROS 2 node publishing on the same domain is directly readable by
us — no `rclpy`, no ROS in `apps/bridge`.

**Why a container:** the robot is Ubuntu 20.04. Humble needs 22.04. The native Foxy is EOL
*and* its CLI segfaults on this machine (`ROBOT-INVENTORY.md` §2). Containerising is the
only sane path, and it also pins the runtime against Unitree OTA churn.

**Why not write our own SLAM:** there is no credible standalone LiDAR-inertial odometry
outside ROS. Writing one is a research project, not a sprint. This decision buys FAST-LIO2
and the Livox driver without infecting our codebase.

**The boundary rule:** perception publishes DDS topics; the bridge subscribes. If we ever
find ourselves importing `rclpy` into `apps/bridge`, this decision has been violated.

---

## D3 — Perception pipeline

| Stage | Component | Notes |
| --- | --- | --- |
| LiDAR driver | `livox_ros_driver2` | Mid-360 at `192.168.123.120`, ports 56100–56500 |
| LiDAR odometry + map | **FAST-LIO2** | Use the G1 humanoid fork — the Mid-360 is mounted **upside down** on this robot and it handles the extrinsics |
| Camera | RealSense D435i | Confirmed usable and ours to drive |
| Object detection | **YOLO11 + TensorRT** | 60+ FPS at <15 W on Orin NX — cheap enough to run continuously |
| 3D grounding | RealSense depth × YOLO boxes | This is the step that turns detection into *spatial* knowledge |

**Compute budget (16 GB):** YOLO11 in TensorRT is a few hundred MB; FAST-LIO2 is mostly
CPU. Comfortable headroom. It is the *local LLM* option that would blow this budget, which
is why we don't take it (D5).

---

## D4 — Nav2 for navigation

**Decided:** run Nav2 (our own config) as the motion layer. The agent issues **goals**, not
velocities.

**Consequence — and this is a big one:** obstacle avoidance, path planning and recovery
behaviours stop being our problem. It also means `walk_to`'s sim-fitted gains largely stop
mattering for autonomous movement, since Nav2 owns the velocity loop and we hand it poses.

`walk_to`/`turn` remain as **low-level primitives** for teleop, testing and any situation
where Nav2 is not running. They still need their axis signs and scaling measured.

**Open:** whether the agent also gets reactive primitives ("walk until obstacle", "turn
toward the doorway"). An LLM may plan better with those than with map goals in unmapped
space. Decide after the first real navigation run.

---

## D5 — Claude via API for reasoning; no local LLM

**Decided:** reasoning runs on Claude through the Anthropic API, in `apps/back`. No local
language model.

**Why:** the Orin NX 16 GB tops out around 3 B at INT4, or 8 B at Q4 — too weak to be the
brain, and it would contend with YOLO for the GPU. A local model would be strictly worse at
the one job we need done well.

**The rule that keeps this safe:** the LLM is **never in a control loop.** It issues skills;
the robot's own controller and Nav2 run the fast loops. Model latency must never be able to
destabilise the robot.

**Note:** `apps/back` uses the Vercel AI SDK (`ai` + `@ai-sdk/anthropic`), deliberately not
the official Anthropic SDK.

---

## D6 — Voice: local wake word, cloud STT/TTS

**Decided:**

| Function | Where | Component |
| --- | --- | --- |
| Wake word | **local**, CPU | openWakeWord / tflite (`hey_claude.tflite`) |
| "Stop" phrase | **local**, CPU | same model, second keyword — see below |
| Speech → text | cloud | Deepgram streaming |
| Text → speech | cloud | Cartesia |

**Why not local STT/TTS:** the brain is already cloud. If the network drops there is no
reply to speak, so local Whisper/Piper buys **zero** offline capability while competing with
YOLO for the GPU. Local would add complexity and cost quality for no availability gain.

**Why wake word must be local:** continuously streaming a microphone to the cloud is
untenable on cost, bandwidth and privacy grounds.

**The safety exception:** the local wake-word model also detects **"stop"**, wired directly
to `stop_everything` without going through the agent. A spoken stop is the one voice command
that must work with the network down, and it must not wait on an LLM round-trip.

**Open:** where audio I/O physically comes from. The Jetson enumerates only APE/HDMI
devices; the G1's 4-mic array and speaker are probably reached through the robot's own
`/api/audiohub`, `/api/voice` or `audio_msg` DDS topics rather than ALSA. Needs
investigation on hardware.

---

## D7 — The world-model contract

**Decided:** perception never hands the agent raw data. It hands a **compact structured
snapshot**, on the order of a few hundred tokens.

This is the highest-leverage interface in the system. An LLM cannot consume 50 Hz point
clouds or 30 fps RGB; the engineering that makes autonomy work is the summarisation layer,
not the sensors. Get this contract right and detectors, models and even the LiDAR become
swappable.

Shape (to be refined in code):

```jsonc
{
  "pose":      { "x": 1.2, "y": -0.4, "yaw": 0.59 },
  "objects":   [ { "label": "person", "range_m": 2.1, "bearing_deg": -15, "confidence": 0.91 } ],
  "free_space": { "ahead_m": 3.4, "left_m": 1.1, "right_m": 2.8 },
  "landmarks": [ { "name": "kitchen", "range_m": 4.2, "bearing_deg": 30 } ],
  "nav":       { "state": "idle", "goal": null }
}
```

Egocentric (range/bearing), not world coordinates — it's what the model reasons about
naturally and what maps to the commands it can issue. Landmarks tie into the existing
`landmarks`/`episodes` pgvector tables.

---

## D8 — Shell access: designed, not bolted on

**Decided in principle, design pending.** The agent gets shell access, but as a *bounded
capability*, not a raw `subprocess.run` tool.

An LLM with a shell on a machine that can walk is a different risk class from one in a
container. Requirements before it ships:

- explicit allow/deny policy, not "anything goes"
- every invocation logged to `tool_call_log` with the session that caused it
- irreversible operations gated behind operator confirmation
- runs in the container, not on the Jetson host, unless a task genuinely needs the host

**Note:** the robot already exposes `/api/bashrunner/request` over DDS. Vendor path exists;
we have not evaluated it.

---

## What we explicitly do not do

- No `gemm` code (D1)
- No `rclpy` in `apps/bridge` (D2)
- No local LLM as the reasoning model (D5)
- No LLM inside any control loop (D5)
- No writing our own SLAM (D2)

---

## Open questions

1. Reactive primitives alongside Nav2 goals? (D4)
2. Where audio I/O actually lives — ALSA or the robot's DDS audio APIs? (D6)
3. How much must keep working with no network? Currently: wake word, "stop", the firmware
   velocity deadman, and Nav2 once a goal is set. Everything else is cloud-dependent.
4. Shell policy specifics (D8)
5. Interlock with the `gemm` stack — still social, not technical (D1)
