# C3PO — Decision Records

Why the stack is shaped the way it is, recorded so each choice can be revisited on evidence
rather than re-argued from scratch. The D-numbers are load-bearing anchors — cited from
`apps/perception/README.md`, `docs/OPERATIONS.md`, and bridge code docstrings
(`world_model.py`, `ros_idl.py`); do not renumber. Companions: `ARCHITECTURE.md` (how it
fits together), `OPERATIONS.md` (running it), `ROBOT-API.md` / `ROBOT-HARDWARE.md` (what
the robot is). Core hardware at decision time: Jetson Orin NX 16 GB, Livox Mid-360,
RealSense D435i.

## D1 — Nothing from the `gemm` stack

**Decided:** C3PO shares no code, packages or configuration with the colleague's `gemm`
workspace on the robot.

**The distinction that actually matters:** _don't depend on their packages_ is not the same
as _don't use open source_. Running our own Nav2 with our own config is ours. Importing
`gemm_navigation` is not. Every third-party component below is one we own the deployment of.

**Consequence:** two independent stacks on one robot, one owner at a time — the controls
and the exclusivity model live in `OPERATIONS.md`.

### D1.1 — Reaffirmed, after considering the opposite

Using `gemm`'s Nav2 directly was seriously weighed and rejected. Recorded because the
argument for it was good, and will come back.

**What prompted the question:** the sensors turned out to be _exclusive_. The RealSense is
a V4L2 device with one owner, the Livox driver binds fixed UDP ports, and one Orin NX will
not comfortably run two Nav2 stacks. "Run our own, in parallel, from the same sensors" is
not physically available — either we share their stack, or only one stack runs at a time.

**The case for using theirs:** it exists and is tuned for this robot, including
`odom_tf_bridge`, which builds the `map → odom → base_link` chain the vendor never
publishes and flattens pelvis pitch/roll so costmap height filtering keeps meaning "metres
above the floor" instead of oscillating with each step — real work we would redo. Crucially
it would **not** have cost the actuation chokepoint: consuming `/cmd_vel` through our own
bridge preserves D2.1 intact.

**Why we still said no:** availability coupling (their container down = our navigation
down, and it restarts on every boot), change coupling (their launch args and costmap params
would land in our autonomy unreviewed), and the saving is smaller than it looks — their
stack has **no object detector**, so D7's `objects[]` stays empty and we need our own
perception container regardless.

**What would reverse it:** nothing structural — the seam is identical either way
(`navigate_to_pose` in, `/cmd_vel` out), so adopting their Nav2 later is a config change.

## D2 — ROS 2 is a perception _subsystem_, not our architecture

**Decided:** run ROS 2 Humble in **our own container** for perception and navigation only.
C3PO's Python keeps **zero ROS dependencies** and consumes the output as raw DDS topics.

**Why this works:** ROS 2 _is_ DDS (see the appendix). The bridge already speaks CycloneDDS
on domain 0, and a ROS 2 node publishing on a domain we listen to is directly readable by
us — no `rclpy`, no ROS in `apps/bridge`. Proven in both directions: the bridge decodes the
vendor's ROS-style topics (`unitree_go::SportModeState_` on `rt/odommodestate` — see
`g1_protocol.py`, which deliberately avoids `rt/state_estimator/*`'s `nav_msgs::Odometry_`)
and Nav2's own `geometry_msgs/Twist` (`ros_idl.py`), all with zero ROS installed.

**Why a container:** Humble needs a newer Ubuntu than the Jetson runs, and the native Foxy
is EOL with a CLI that segfaults on this machine (`ROBOT-HARDWARE.md`). Containerising is
the only sane path, and it also pins the runtime against Unitree OTA churn.

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
Nav2        → /c3po/cmd_vel ───────┼──►  subscribe, convert to SET_VELOCITY
                                   │     stop_everything · task registry · watchdog
                                   │     MCP server · skills · world-model snapshot
```

**All actuation goes through the bridge. No exceptions.** Nav2 does not command the robot;
it emits velocity on the namespaced `/c3po/cmd_vel` — never bare `/cmd_vel`, and the bridge
must never subscribe bare `/cmd_vel` on a shared domain, because nothing at the transport
layer stops another stack's planner from publishing there (see the appendix).

Three reasons this is worth its cost:

1. **The robot's control API is raw DDS.** `/api/sport/request` is Unitree's own RPC via
   `unitree_sdk2py` (`ROBOT-API.md`). Driving it from ROS would add the vendor's
   `unitree_ros2` message package as a translation layer — which is what `gemm` does and
   what D1 rules out. Direct is the _shorter_ path here, not a detour.
2. **Safety has to have one chokepoint.** `stop_everything`, the cancel tokens, the 1 s
   velocity deadman and the link watchdog all live in the bridge. Split actuation across a
   ROS node and "stop everything" would stop only some things.
3. **Independence from a container we rebuild constantly.** Humble ships Python 3.10
   against our 3.12, and going ROS-native would put the stop path inside the same image we
   iterate on for perception. A broken or rebuilding perception container cannot stop us
   halting the robot.

### D2.2 — What it costs, and how the cost was paid

We must define a few ROS IDL types ourselves to read ROS topics without ROS.

**Resolved: hand-written, not generated.** `apps/bridge/src/bridge/sdk/ros_idl.py` — "the
whole of what D2.2 costs us" — carries the admission rule (a type earns an IDL definition
only if its shape is fixed and frozen; everything else crosses as JSON inside
`std_msgs::msg::dds_::String_`, a type we already have) and the wire-layout traps that make
the typename strings and field order load-bearing. `idlc` ships in the CycloneDDS install
already onboard (`$CYCLONEDDS_HOME/bin`) and remains the generate option if the hand-kept
set ever grows beyond a handful.

**Resolved: TF stays entirely inside the container.** This was the real open question. Of
the three options — (1) container publishes _already-resolved_ egocentric quantities so the
bridge needs no transforms; (2) consume `/tf` and do lookups ourselves, reimplementing a
solved problem; (3) move the world-model builder into the container — option 1 got built
(`perception_link.py` plus the container's world-model publisher), and it fits D7 exactly.
If the bridge ever reaches for `/tf`, the line is in the wrong place.

### D2.3 — The alternative we rejected

Going fully ROS-native is a legitimate architecture: TF for free, no dual type system, and
the whole ecosystem's tooling. We rejected it because it makes the MCP server and the
safety layer into ROS nodes, coupling the LLM surface to ROS lifecycle and putting the stop
path inside the perception container. That trade seems worse for this project — but it is
not obviously wrong, and if maintaining the hand-written IDL types becomes a running sore,
it is the recorded fallback.

## D3 — Perception pipeline

| Stage                | Component                    | Notes                                                                                               |
| -------------------- | ---------------------------- | --------------------------------------------------------------------------------------------------- |
| LiDAR driver         | `livox_ros_driver2`          | Mid-360 — addressing and ports in `ROBOT-HARDWARE.md`                                               |
| LiDAR odometry + map | **FAST-LIO2** (upstream)     | the Mid-360 is mounted **upside down** on this robot — handled by one static TF, not a fork (below) |
| Camera               | RealSense D435i              | confirmed usable and ours to drive                                                                  |
| Object detection     | **YOLO11 + TensorRT**        | cheap enough to run continuously on the Orin                                                        |
| 3D grounding         | RealSense depth × YOLO boxes | the step that turns detection into _spatial_ knowledge                                              |

Two corrections to the original record, both from implementation research (the full
refuted-claims list lives in `apps/perception/README.md`):

- **The "G1 humanoid fork" of FAST-LIO was rejected.** Its Humble branch contains no Livox
  driver, its config enables online extrinsic estimation that silently absorbs the inverted
  mount's sign error, and its localization half is not SLAM. The chosen implementation is
  upstream FAST-LIO2 with nothing rotated in the sensor path and one static
  `odom → camera_init` transform correcting the inverted mount.
- **The original compute budget ("comfortable headroom", sub-15 W) was wrong in kind.** The
  16 GB is _unified_ CPU+GPU memory, and FAST-LIO2's ikd-Tree map grows with mapped area —
  "mostly CPU" is true of the solve and false of the memory. Plan on MAXN/25 W and measure;
  the measured baseline and harness live with `apps/perception/README.md`.

What stands: the pipeline composition, and the reason there is headroom at all — it is the
_local LLM_ option that would blow the budget, which is why D5 rules it out.

## D4 — Nav2 for navigation

**Decided:** run Nav2 (our own config) as the motion layer. The agent issues **goals**, not
velocities.

**Consequence — and this is a big one:** obstacle avoidance, path planning and recovery
behaviours stop being our problem. It also means `walk_to`'s sim-fitted gains largely stop
mattering for autonomous movement, since Nav2 owns the velocity loop and we hand it poses.
`walk_to`/`turn` remain as **low-level primitives** for teleop, testing and any situation
where Nav2 is not running; their axis signs and scaling still need measuring on real
hardware (`ROBOT-API.md`).

**Nav2 does not drive the robot directly.** It emits `/c3po/cmd_vel`; the bridge subscribes
and converts to `SET_VELOCITY` — see D2.1. This is what keeps `stop_everything` meaningful,
and it gives us a natural place to clamp or veto a planner command before it reaches the
legs.

**Open:** whether the agent also gets reactive primitives ("walk until obstacle", "turn
toward the doorway"). An LLM may plan better with those than with map goals in unmapped
space. Decide after the first real navigation run.

## D5 — Off-board LLM API for reasoning; no local LLM

**Decided:** reasoning runs off-board, over a hosted LLM API called from `apps/back`. No
local language model. Originally Claude through the Anthropic API; **the provider is
superseded by D5.1, everything else here stands.**

**Why:** the Orin NX 16 GB tops out around 3 B at INT4, or 8 B at Q4 — too weak to be the
brain, and it would contend with YOLO for the GPU. A local model would be strictly worse at
the one job we need done well.

**The rule that keeps this safe:** the LLM is **never in a control loop.** It issues
skills; the robot's own controller and Nav2 run the fast loops. Model latency must never be
able to destabilise the robot.

**SDK choice, deliberate:** `apps/back` uses the Vercel AI SDK (`ai` + a provider package),
not the model vendor's own SDK. This is what confined the D5.1 provider swap to a single
file (`src/agent/runtime.ts`) — do not "correct" it to a vendor SDK.

### D5.1 — Provider superseded 2026-08-18: TIC AI, not the Anthropic API

The internal agent (`POST /agent`) runs on **TIC AI**, ORT's OpenAI-compatible gateway in
front of models hosted on campus. The rest of D5 is unchanged: reasoning still off-board,
no model on the Orin, the LLM still never in a control loop. Scope: only the _internal_
agent moved — Claude Code driving the robot over MCP is untouched and first-class, and
`mcp[cli]` in `apps/bridge` is the MCP reference SDK, unrelated to which model reasons.

**Gained:** inference on university hardware and no external vendor account in the critical
path. Not gains we can claim: rate/quota limits are unpublished (a hand-issued key is
itself the rationing mechanism) and nothing says what `tic-chat` fronts — cost and model
quality are unknown, not better.

**Gave up:** adaptive thinking — `providerOptions.anthropic.thinking` and the
`AGENT_THINKING` env var went with it; a generic gateway has no equivalent. Not prompt
caching: never enabled pre-switch, so nothing regressed. And D6's "the brain is already
cloud" now means _campus network_ rather than internet — the gateway is reachable only from
inside ORT.

**Evidence:** `tic-chat`'s tool calling is confirmed exactly once (2026-08-18): a live run
authenticated, emitted a real tool call, took the result back and finished in 2 steps —
`finish_reason` `"stop"`, usage returned. One trivial tool, not the 28-skill catalogue;
whether it holds up across a full multi-step skill loop is still open.

The gateway's operational facts (base URL and scheme, key issuance, model roster, env vars)
have one home: `apps/back/.env.example`. The AI-SDK upgrade discipline the swap exposed
lives in `OPERATIONS.md`; the rationale half worth keeping here is **upgrade the line,
don't pin it** — holding the provider package on an old maintenance major to dodge one
change would have frozen three packages, where moving the whole line cost a single renamed
import.

## D6 — Voice: local wake word, cloud STT/TTS

**Decided:**

| Function      | Where          | Component                                   |
| ------------- | -------------- | ------------------------------------------- |
| Wake word     | **local**, CPU | openWakeWord / tflite (`hey_claude.tflite`) |
| "Stop" phrase | **local**, CPU | same model, second keyword — see below      |
| Speech → text | cloud          | Deepgram streaming                          |
| Text → speech | cloud          | Cartesia — **under review, see below**      |

**Why not local STT/TTS:** the brain is already cloud. If the network drops there is no
reply to speak, so local Whisper/Piper buys **zero** offline capability while competing
with YOLO for the GPU. **Why the wake word must be local:** continuously streaming a
microphone to the cloud is untenable on cost, bandwidth and privacy grounds.

**The safety exception:** the local wake-word model also detects **"stop"**, wired directly
to `stop_everything` without going through the agent. A spoken stop is the one voice
command that must work with the network down, and it must not wait on an LLM round-trip.

**Update — the audio-I/O question is answered, and the TTS row is under pressure.**
Investigated on hardware: `/api/audiohub` does not exist on this robot, the DDS RPC service
is literally named `voice`, and the robot has **onboard TTS** (Chinese and English only)
plus a `PlayStream` PCM path for anything else — cloud Cartesia is no longer _necessary_,
only optional for languages the firmware lacks. openWakeWord is not installed onboard.
Robot-side facts: `ROBOT-API.md` (voice service), `ROBOT-HARDWARE.md` (audio). A D6.1
revisiting the TTS row is owed when the voice loop is actually built.

## D7 — The world-model contract

**Decided:** perception never hands the agent raw data. It hands a **compact structured
snapshot**, on the order of a few hundred tokens. This is the highest-leverage interface in
the system: an LLM cannot consume 50 Hz point clouds or 30 fps RGB; the engineering that
makes autonomy work is the summarisation layer, not the sensors. Get the contract right and
detectors, models, even the LiDAR become swappable.

**The schema and its rules live with the code:** `apps/bridge/src/bridge/world_model.py`
(module docstring elaborates the rules; the shape is `to_dict()`, versioned, and pinned by
tests that run with no robot and no perception stack — including a ~300-token budget on a
busy snapshot). Exposed as the `describe_surroundings` MCP tool. The four rules, by name:

- **Egocentric, not world-frame** — range/bearing from the robot, `0°` ahead, positive CCW,
  the same sign as `turn`'s `delta_yaw_radians`. Any other choice bakes a sign-flip bug
  into the interface itself.
- **Absent is not empty** — the most important rule. An offline detector must not produce
  `objects: []` — an empty list means "I looked and there is nothing there", which is how a
  robot walks into something it never saw. Sources carry explicit statuses; every
  degradation is restated in plain language in `notes`.
- **Truncation is declared** — `objects_omitted` is never silently zero; nearest are kept.
- **Everything carries an age** — sources go `stale` before they go missing.

`free_space` is four coarse sectors on purpose. It answers "can I go that way", not "plan
me a path" — Nav2 owns real obstacle avoidance (D4). Needing finer resolution here would
mean the model is being asked to do a planner's job.

## D8 — Shell access: designed, not bolted on

**Decided in principle, design pending.** The agent gets shell access, but as a _bounded
capability_, not a raw `subprocess.run` tool. An LLM with a shell on a machine that can
walk is a different risk class from one in a container. Requirements before it ships:

- explicit allow/deny policy, not "anything goes"
- every invocation logged to `tool_call_log` with the session that caused it
- irreversible operations gated behind operator confirmation
- runs in the container, not on the Jetson host, unless a task genuinely needs the host

**Note:** the robot already exposes `/api/bashrunner/request` over DDS — a vendor path,
unevaluated (catalogue entry in `ROBOT-API.md`'s service catalogue). Evaluate it before
designing around it.

## D9 — Arm teleoperation: retargeting, not inverse kinematics

**Decided 2026-08-19**, while building `apps/bridge/src/bridge/teleop/`.

**Context.** The colleague's `xr_teleoperate` already drives this robot's arms from a Quest,
and it does so with full IK: casadi + pinocchio solving against `g1_body29_hand14.urdf`,
mapping the operator's wrist *pose* to fourteen joint angles. That is the better technique,
and reusing it was explicitly considered and explicitly authorised.

**What blocked it.** That URDF lives in their tree on the Jetson, and no G1 link lengths or
joint axes exist anywhere in this repo. The Jetson has not been reachable from the dev
machine, so their code has never actually been read — only the parts of its behaviour our own
live inspection recorded (`docs/ROBOT-HARDWARE.md`).

**Decided:** map **direction and extension** instead of solving for position.

- shoulder → wrist *direction* → shoulder pitch and roll
- *fraction of the operator's own reach* extended → elbow angle, by law of cosines on two
  equal links
- operator reach measured once, at calibration, from their first extended-arm frame

Every one of those quantities is scale-free, so none of them needs the robot's dimensions and
none of them needs the operator's. A 1.55 m and a 1.95 m operator both map "fully extended" to
"robot fully extended".

**What this costs.** The robot's hand does not end up where the operator's hand is, in metres.
Handing an object to someone at a measured point is out of reach until there is a real solver.

**Why it is still the right call.** The alternative was not "IK" — it was "IK with guessed link
lengths", which converges, reports success, and puts the elbow somewhere else. A solver that is
confidently wrong about a humanoid's arm is worse than a mapping that is honestly approximate.
And what the person wearing the headset means by "the robot copies my arms" is direction,
extension and rotation, all of which this delivers.

**Revisit when** the URDF is in hand — either measured off the robot or extracted with
permission. At that point `retarget.py` becomes one implementation behind a seam that
`arm_sdk.py` already has, and nothing above it changes.

### D9.1 — Both hardware paths ship disabled

Not caution for its own sake. Two specific facts are unknown and neither can be settled from
any document:

1. **No source gives the positive direction of any G1 arm joint.** The order is well
   established (`docs/ROBOT-API.md`); the signs are not. `scripts/arm_sign_check.py` settles
   them in about two minutes with a person watching.
2. **~~Which hands are fitted is unresolved~~ — settled 2026-08-19 while this was being
   written**: the operator looked at the robot and found **two BrainCo hands**, one of them
   physically unplugged during the earlier probe. So the units question is answered ([0,1],
   not Dex3 radians), and `hands.py`'s refusal to default is now a formality rather than a
   live unknown. One thing the settlement *adds*, though: a BrainCo has **no firmware
   deadman**, unlike the Dex3's timeout bit, so any hold has to be bounded by the bridge —
   which is a reason to keep the gate rather than drop it.

So `TELEOP_ARM_ENABLED` and `TELEOP_HAND_ENABLED` are not configuration. They are a record
that a human has done the corresponding check. Head-yaw turning and the walk axis are *not*
gated, because they ride on `_locomotion.send_velocity_async` — already-vetted machinery with
a hardware clamp and a firmware deadman under it.

---

## Closed decisions

Questions asked once and answered, kept so they are not re-opened by accident.

**`packages/shared` — designed, deliberately not built.** Eden Treaty already gives
web↔back end-to-end types with zero shared package; the codebase settled on TypeBox
(Elysia's `t`), so the sketched Zod package would _add_ a second schema library; and the
one real duplication — the Python bridge's skill definitions vs `apps/back`'s TS ones — is
not fixable by a TS-only package, because Python cannot import it. Revisit only with a
concrete second TS consumer that isn't already Eden-linked.

**WebRTC transport — superseded as the primary path.** It existed to reach a G1 we could
only talk to the way the phone app does; once we could SSH the Jetson, `real` became direct
DDS onboard. `/webrtcreq`/`/webrtcres` are themselves ordinary DDS topics — the WebRTC
interface was always a shim _over_ the native API, so going native skipped a translation
layer and its quirks. Retained as the only route needing no onboard install (the fallback
for a locked-down or OTA-reset robot): protocol notes in `ROBOT-API.md`'s WebRTC fallback
appendix. Do not build it speculatively.

**`rt/utlidar` as pose source — rejected.** The vendor's own app never enables the LiDAR
switch for the G1 family, and real-world G1 pose projects ignore `rt/utlidar/robot_pose`
entirely, running their own FAST-LIO over the raw Mid-360 — the pose feature is
quadruped-only or immature on G1. (The raw `rt/utlidar` cloud/IMU topics _do_ publish live
on this unit — `ROBOT-HARDWARE.md` — the rejection is of the vendor pose feature, not the
topic family.) The path is FAST-LIO2 in the perception container (D3).

**Native install first, containerize the bridge later.** Containerizing during hardware
bring-up stacks two unknowns — a DDS-in-container failure is hard to distinguish from a
real-robot failure while debugging both. Known-good baseline natively; `--network host`
plus a source bind-mount keep the eventual move cheap. The standing argument for eventually
doing it: the Jetson's CycloneDDS build sits in a home directory an OTA could clobber
(`ROBOT-HARDWARE.md`).

Also closed, briefly:

- **Embedding provider — Voyage `voyage-3-large`.** The planned pgvector columns are sized
  for it (not yet in `apps/back/src/db/schema.ts` — see `apps/back/README.md`). No
  embedding code exists yet, so nothing depends on the
  choice; TIC AI advertises a `tic-embed` on the same gateway and key, but `GET /models`
  did not list it — re-check the endpoint when memory/RAG work starts.
- **back↔bridge connection — both.** The bridge serves stdio for clients that spawn it
  (Claude Code) and streamable HTTP as a daemon (`apps/back`'s MCP client) simultaneously.
  Implemented; see `ARCHITECTURE.md`.
- **Single-robot data model for v1.** `organization_id` gates the org-scoped tables
  (`member`, `invitation`, `chat`; messages and `tool_call_log` hang off them, and
  `tool_call_log.chat_id` is nullable for skills fired outside a chat); adding a
  `robotId` later is non-breaking.
- **Wake-word model — stock placeholder for all of dev.** A custom "hey claude" model is a
  product call for whoever demos it; decide before any real voice demo, not now.
- **Audio I/O for v1 dev — Mac mic/speakers**, until the voice loop is built onboard (see
  the D6 update).
- **Postgres host — hosted**, decided at deploy time; where it runs is `OPERATIONS.md`'s
  fact.

## What we explicitly do not do

- No `gemm` code (D1)
- No `rclpy` in `apps/bridge` (D2)
- No local LLM as the reasoning model (D5)
- No LLM inside any control loop (D5)
- No writing our own SLAM (D2)

## Appendix: ROS 2 vs DDS

They are not alternatives — ROS 2 _runs on_ DDS. DDS is the transport standard (CycloneDDS,
FastDDS); ROS 2 is a framework on top of it, adding packages, TF, node lifecycle, launch
and the client libraries. Roughly: DDS is HTTP, ROS 2 is Django — and you can `curl` a
Django app without installing Django. (ROS _1_ genuinely was an alternative — its own
TCPROS transport, a central master. ROS 2 replaced that with DDS in 2017, which is where
the confusion comes from.)

This robot demonstrates it: Unitree publishes raw DDS using ROS 2's naming conventions
(`rt/` topics, `rq/`/`rr/` services) and ROS message types on the wire, with no ROS nodes
running — the catalogue is `ROBOT-API.md`. We read the robot's pose with zero ROS
installed. That is the whole basis of D2.

The consequence worth remembering: because ROS 2 topics _are_ DDS topics, any ROS stack
sharing our domain sees us and we see it. A feature for our own container — and precisely
why the `gemm` exclusivity (D1) matters: nothing at the transport layer prevents two stacks
from both publishing velocity commands.

## Open questions

1. Reactive primitives alongside Nav2 goals? (D4) — decide after the first real navigation
   run.
2. How much must keep working with no network? Currently: wake word, the spoken "stop", the
   firmware velocity deadman, and Nav2 once a goal is set. Everything else is
   cloud-dependent.
3. Shell policy specifics (D8).
4. D6.1 — cloud Cartesia vs onboard TTS + `PlayStream`, now that both exist. Decide when
   the voice loop is built.
5. Interlock with the `gemm` stack — still social, not technical (D1); tracked as an open
   item in `OPERATIONS.md`.
