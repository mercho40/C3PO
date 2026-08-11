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

**But note the distinction that actually matters:** _don't depend on their packages_ is not
the same as _don't use open source_. Running our own Nav2 with our own config is ours.
Importing `gemm_navigation` is not. Every third-party component below is one we own the
deployment of.

**Consequence:** two independent stacks run on one robot with **no technical interlock**.
That is a standing operational risk, not a solved problem — see `MENTAL-MODEL.md` §8.

---

## D2 — ROS 2 is a perception _subsystem_, not our architecture

**Decided:** run ROS 2 Humble in **our own container** for perception and navigation only.
C3PO's Python keeps **zero ROS dependencies** and consumes the output as raw DDS topics.

**Why this works:** ROS 2 _is_ DDS. Our bridge already speaks CycloneDDS on domain 0, and
we already parse a ROS type off the wire (`nav_msgs::msg::dds_::Odometry_` on
`rt/state_estimator/*`). A ROS 2 node publishing on the same domain is directly readable by
us — no `rclpy`, no ROS in `apps/bridge`.

**Why a container:** the robot is Ubuntu 20.04. Humble needs 22.04. The native Foxy is EOL
_and_ its CLI segfaults on this machine (`ROBOT-INVENTORY.md` §2). Containerising is the
only sane path, and it also pins the runtime against Unitree OTA churn.

**Why not write our own SLAM:** there is no credible standalone LiDAR-inertial odometry
outside ROS. Writing one is a research project, not a sprint. This decision buys FAST-LIO2
and the Livox driver without infecting our codebase.

**The boundary rule:** perception publishes DDS topics; the bridge subscribes. If we ever
find ourselves importing `rclpy` into `apps/bridge`, this decision has been violated.

### D2.1 — Where exactly the line falls

```
ROS 2 container                    │  apps/bridge  (no ROS)
───────────────────────────────────┼──────────────────────────────────────
Livox driver                       │
FAST-LIO2   → odometry, map        │
RealSense   → depth, RGB           │
YOLO11      → detections           │
Nav2        → /cmd_vel  ───────────┼──►  subscribe, convert to SET_VELOCITY (7105)
                                   │     stop_everything · task registry · watchdog
                                   │     MCP server · skills · world-model snapshot
```

**All actuation goes through the bridge. No exceptions.** Nav2 does not command the robot;
it emits `/cmd_vel` and the bridge decides whether to forward it.

Three reasons this is worth its cost:

1. **The robot's control API is raw DDS.** `/api/sport/request` (api_id 7101/7105/7106) is
   Unitree's own RPC, reached via `unitree_sdk2py`. Driving it from ROS would require the
   vendor's `unitree_ros2` message package as an extra translation layer — which is what
   `gemm` does and what D1 rules out. Direct is the _shorter_ path here, not a detour.
2. **Safety has to have one chokepoint.** `stop_everything`, the cancel tokens, the 1 s
   velocity deadman and the link watchdog all live in the bridge. If some commands reached
   the robot through a ROS node instead, "stop everything" would stop only some things.
3. **Independence from a container we'll be rebuilding constantly.** Humble ships Python
   3.10 against our 3.12, and more importantly, going ROS-native would put the stop path
   inside the same image we're iterating on for perception. Today, a broken or rebuilding
   perception container cannot stop us halting the robot.

### D2.2 — What this costs

**We must define a few ROS IDL types ourselves** to read ROS topics without ROS:
`geometry_msgs/Twist`, `nav_msgs/Odometry`, probably `sensor_msgs/PointCloud2`.

Bounded work — these definitions are small and frozen — and they can be _generated_ rather
than hand-written: `idlc` ships with the CycloneDDS install already on the robot
(`~/cyclonedds_ws/install/cyclonedds/bin/idlc`). Note we've already proven this direction
works, by decoding `unitree_go::SportModeState_` off `rt/odommodestate`.

**TF is the real open question.** ROS's transform tree is genuinely useful and we will need
`base_link ↔ camera ↔ lidar ↔ map`. Inside the container that's free; on our side of the
line it is not. Options, in rough order of preference:

1. Keep all TF-dependent work inside the container and let it publish _already-resolved_
   quantities (e.g. object positions in the robot's base frame) — the bridge then needs no
   transforms at all. This fits D7, which already says perception hands over egocentric,
   pre-digested structure.
2. Consume `/tf` and do the lookups ourselves — real work, and reimplementing a solved
   problem.
3. Move the world-model builder into the container as a ROS node that publishes one
   summary topic. The bridge stays TF-free and the LLM-facing contract is unchanged.

Option 1 is the current intent. If we find ourselves reaching for option 2, that's a signal
the line is in the wrong place.

### D2.3 — The alternative we rejected

Going fully ROS-native is a legitimate architecture: TF for free, no dual type system, and
the whole ecosystem's tooling. We rejected it because it makes the MCP server and the safety
layer into ROS nodes, coupling the LLM surface to ROS lifecycle and putting the stop path
inside the perception container. That trade seems worse for this project — but it is not
obviously wrong, and if maintaining IDL types becomes a running sore, it is the fallback.

---

## D3 — Perception pipeline

| Stage                | Component                    | Notes                                                                                                         |
| -------------------- | ---------------------------- | ------------------------------------------------------------------------------------------------------------- |
| LiDAR driver         | `livox_ros_driver2`          | Mid-360 at `192.168.123.120`, ports 56100–56500                                                               |
| LiDAR odometry + map | **FAST-LIO2**                | Use the G1 humanoid fork — the Mid-360 is mounted **upside down** on this robot and it handles the extrinsics |
| Camera               | RealSense D435i              | Confirmed usable and ours to drive                                                                            |
| Object detection     | **YOLO11 + TensorRT**        | 60+ FPS at <15 W on Orin NX — cheap enough to run continuously                                                |
| 3D grounding         | RealSense depth × YOLO boxes | This is the step that turns detection into _spatial_ knowledge                                                |

**Compute budget (16 GB):** YOLO11 in TensorRT is a few hundred MB; FAST-LIO2 is mostly
CPU. Comfortable headroom. It is the _local LLM_ option that would blow this budget, which
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

**Nav2 does not drive the robot directly.** It emits `/cmd_vel`; the bridge subscribes and
converts to `SET_VELOCITY` — see D2.1. This is what keeps `stop_everything` meaningful, and
it also gives us a natural place to clamp or veto a planner command before it reaches the
legs.

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

| Function      | Where          | Component                                   |
| ------------- | -------------- | ------------------------------------------- |
| Wake word     | **local**, CPU | openWakeWord / tflite (`hey_claude.tflite`) |
| "Stop" phrase | **local**, CPU | same model, second keyword — see below      |
| Speech → text | cloud          | Deepgram streaming                          |
| Text → speech | cloud          | Cartesia                                    |

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

**Implemented** in `apps/bridge/src/bridge/world_model.py`, exposed as the
`describe_surroundings` MCP tool, and covered by tests that run with no robot and no
perception stack.

```jsonc
{
  "version": 1,
  "sources": { "pose": "ok", "detector": "offline", "lidar": "ok" },
  "pose": { "x_m": 1.2, "y_m": -0.4, "yaw_deg": 33.8 },
  "objects": [{ "label": "person", "range_m": 2.1, "bearing_deg": -15, "confidence": 0.91, "age_s": 0.3 }],
  "objects_omitted": 4,
  "free_space": { "ahead_m": 3.4, "left_m": 1.1, "right_m": 2.8, "behind_m": 5.0 },
  "landmarks": [{ "name": "kitchen", "range_m": 4.2, "bearing_deg": 30 }],
  "notes": ["Object detection is OFFLINE — this is not an empty scene. Do not assume the path is clear."]
}
```

Four rules, each load-bearing:

**Egocentric, not world-frame.** Range and bearing from the robot, because that maps onto
the commands it can issue. Bearing is degrees, `0` ahead, **positive to the left (CCW)** —
the same sign as `turn`'s `delta_yaw_radians`. Any other choice bakes a sign-flip bug into
the interface, and every "turn toward it" goes the wrong way.

**Absent is not empty — the most important rule.** An offline detector must not produce
`objects: []`, because an empty list means "I looked and there is nothing there", which is
precisely how a robot walks into something it never saw. Every source carries an explicit
status and every degradation is restated in plain language in `notes`, since that is what
the model actually reads. Same false-negative class as reporting a skill failed when the
robot obeyed.

**Truncation is declared.** `objects_omitted` is never silently zero. A model shown 8 of 40
obstacles with no indication of the rest will reason confidently about a scene it cannot
see. Nearest are kept, because proximity is what matters.

**Everything carries an age.** A 4-second-old detection is a different fact from a fresh one
when you are moving; sources go `stale` before they go missing.

The snapshot is versioned, and a test asserts a busy one stays under ~300 tokens —
perception must not crowd out the conversation it exists to inform.

`free_space` is four coarse sectors on purpose. It answers "can I go that way", not "plan me
a path" — Nav2 owns real obstacle avoidance (D4). Needing finer resolution here would mean
the model is being asked to do a planner's job.

Today the tool reports every source `offline`, which is the honest state until the
perception container exists — and is exactly why the contract could be built and tested
before any of it.

---

## D8 — Shell access: designed, not bolted on

**Decided in principle, design pending.** The agent gets shell access, but as a _bounded
capability_, not a raw `subprocess.run` tool.

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

## A note on ROS 2 vs DDS

They are not alternatives — ROS 2 _runs on_ DDS. DDS is the transport standard (CycloneDDS,
FastDDS); ROS 2 is a framework on top of it, adding packages, TF, node lifecycle, launch and
the client libraries. Roughly: DDS is HTTP, ROS 2 is Django — and you can `curl` a Django
app without installing Django.

Evidence from this robot, all observed directly:

| Observed                                         | What it means                                                        |
| ------------------------------------------------ | -------------------------------------------------------------------- |
| `rt/lowstate`, `rt/odommodestate`                | `rt/` is ROS 2's topic-name convention                               |
| `rq/…Request`, `rr/…Reply`                       | ROS 2's service convention                                           |
| `nav_msgs::msg::dds_::Odometry_`                 | A ROS 2 message type, on the wire, as plain DDS                      |
| 100+ topics but an almost-empty `ros2 node list` | Unitree publishes raw DDS with ROS-style naming and **no ROS nodes** |

We read the robot's pose today with zero ROS installed. That is the whole basis of D2.

(ROS _1_ genuinely was an alternative — it had its own TCPROS transport and a central
master. ROS 2 replaced that with DDS in 2017, which is where the confusion comes from.)

One consequence worth remembering: because ROS 2 topics _are_ DDS topics, any ROS stack
sharing our domain sees us and we see it. That is a feature for our own container — and
precisely why the `gemm` interlock (D1) matters. Nothing at the transport layer prevents two
stacks from both publishing velocity commands.

---

## Open questions

1. Reactive primitives alongside Nav2 goals? (D4)
2. Where audio I/O actually lives — ALSA or the robot's DDS audio APIs? (D6)
3. How much must keep working with no network? Currently: wake word, "stop", the firmware
   velocity deadman, and Nav2 once a goal is set. Everything else is cloud-dependent.
4. Shell policy specifics (D8)
5. Interlock with the `gemm` stack — still social, not technical (D1)
6. TF: keep it entirely inside the container (preferred), or consume `/tf` in the bridge?
   (D2.2) — decide before the world-model builder is written, since it determines which side
   of the line that code lives on.
