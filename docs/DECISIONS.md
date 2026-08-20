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

## D6 — Voice: local wake word, cloud STT, local TTS

**Decided:**

| Function      | Where          | Component                                   |
| ------------- | -------------- | ------------------------------------------- |
| Wake word     | **local**, CPU | openWakeWord / tflite — **Spanish phrase, D6.1**  |
| "Stop" phrase | **local**, CPU | same model, second keyword — see below           |
| Speech → text | cloud          | Deepgram streaming — handles Spanish             |
| Text → speech | cloud          | external synth → `PlayStream` — **required, D6.1** |

**Why not local STT:** the brain is already cloud, so if the network drops there is no
reply to speak and local Whisper buys **zero** offline capability while competing with
YOLO for the GPU. **This reasoning does NOT extend to TTS, and D6.2 reverses that half:**
the sentences most worth saying — "estoy atascado", a refusal, a warning — are exactly the
ones that must survive a network drop, and Piper needs no key and no network. **Why the wake word must be local:** continuously streaming a
microphone to the cloud is untenable on cost, bandwidth and privacy grounds.

**The safety exception:** the local wake-word model also detects **"stop"**, wired directly
to `stop_everything` without going through the agent. A spoken stop is the one voice
command that must work with the network down, and it must not wait on an LLM round-trip.

**Update — the audio-I/O question is answered.** Investigated on hardware:
`/api/audiohub` does not exist on this robot, the DDS RPC service is literally named
`voice`, and the robot has **onboard TTS** (Chinese and English only) plus a `PlayStream`
PCM path for anything else. openWakeWord is not installed onboard. Robot-side facts:
`ROBOT-API.md` (voice service), `ROBOT-HARDWARE.md` (audio).

### D6.1 — Spanish is the operating language, which makes external TTS mandatory

**Decided:** this deployment speaks and listens in **Spanish**. The operator console is
already in Spanish; the robot is not, and cannot be made so through the firmware.

That single fact reverses the previous update's conclusion. It read "cloud Cartesia is no
longer _necessary_, only optional for languages the firmware lacks" — Spanish **is** a
language the firmware lacks, so external synthesis is **necessary**, and it is on the
critical path rather than a nice-to-have.

| Function      | D6 said                          | With Spanish                                            |
| ------------- | -------------------------------- | ------------------------------------------------------- |
| Text → speech | onboard TTS, cloud optional      | **external synth → `PlayStream`, mandatory**            |
| Speech → text | Deepgram streaming               | unchanged — Deepgram handles Spanish                    |
| Wake word     | `hey_claude.tflite`              | **a Spanish phrase — a custom openWakeWord model**      |
| "Stop" phrase | English "stop"                   | **Spanish, and still the safety path**                  |

**Why the firmware cannot be made to do it.** `speaker_id` is 0 = Chinese, 1 = English,
there is no third voice, and it is verified on this robot that neither reads Spanish
intelligibly (`ROBOT-API.md` §7). This is a wall, not a missing argument. The dangerous
part is the failure mode: passing Spanish text to `say` returns **rpc_code 0** and produces
an English voice attempting Spanish phonemes. It reports success and is unusable — the same
false-success class this codebase keeps finding, and the reason `say`'s tool description now
refuses the case in words the model will read.

**The path out is already reverse-engineered.** `PlayStream` (`api_id` 1003/1004) takes
PCM, and its `stream_id` **is** the interrupt model: the same id concatenates chunks
gaplessly, a different id barges in with no `PlayStop` first. That is a better fit for a
robot that should stop talking when interrupted than `TtsMaker`, which has no documented
behaviour for being called mid-utterance. The co-tenant `gemm` stack already synthesises
externally and pushes PCM this way, so the path is proven on this hardware by someone else.

**There is no onboard ASR to fall back on.** `api_id` 1002 is registered by every vendor
client and **called by none**, purpose unknown, and a cross-checked vendor client has no ASR
function at all. Spanish STT is cloud, fed from the multicast mic path
(`239.168.123.161:5555`, 16 kHz mono s16le — `ROBOT-HARDWARE.md` §8.2).

**The part that needs a human decision, and it is not the TTS.** D6 wires a spoken stop
**directly to `stop_everything`, bypassing the agent**, so that it works with the network
down and waits on no LLM round-trip. In Spanish that means a custom-trained wake-word model
is **safety-critical code, on a dataset that does not exist yet**, which has to fire
reliably for a stressed non-native-English speaker — exactly the moment a spoken stop
matters. Decide before building it whether that model can be trained to a standard worth
trusting, or whether the physical e-stop remains the only stop that counts. Shipping a
spoken stop that works in demos and not in panic is worse than shipping none, because
people will rely on it.

**Not built.** No STT, no wake word, no `PlayStream` path. `say` works, in two languages
this deployment does not use.

### D6.2 — The voice stack, and where each piece runs

**Decided:** the loop is **local at both ends and cloud only in the middle**, and it is
split across two processes by *criticality*, not by convenience.

| Stage            | Runs in            | Component                                   | Why there                                                       |
| ---------------- | ------------------ | ------------------------------------------- | --------------------------------------------------------------- |
| Mic capture      | onboard            | UDP multicast join, `239.168.123.161:5555`  | the group is on `192.168.123.0/24`; unreachable off-robot        |
| **Stop phrase**  | **`apps/bridge`**  | openWakeWord ONNX, Spanish                  | shortest possible path to `stop_everything` — see below          |
| VAD + wake word  | voice process      | Silero VAD v5 + openWakeWord                | ML deps stay out of the process that owns the stop               |
| Speech → text    | `apps/back`        | Deepgram Nova-3, `es-419`                   | the key lives where every other key lives, never on the robot    |
| Text → speech    | onboard            | **Piper**, `es_AR` — local, offline         | no key, no network, Argentine accent                             |
| Playback         | `apps/bridge`      | `PlayStream` 1003/1004 + PCM in `.binary`   | actuation chokepoint; `_CallRequestWithParamAndBin` already ships |

**Multicast is what makes the safety split free.** The mic is a multicast group, not a
device, so **two processes can join it independently**. The bridge joins it and runs
*nothing but* the stop detector — a ~1 MB ONNX on CPU, no STT, no synthesis, no network.
The voice process joins the same group for everything else. So **if the voice process
dies, hangs, or is being rebuilt, the spoken stop still works**, and it never crosses a
process boundary to reach `stop_everything`. That is the D6 safety exception implemented
rather than merely restated. It is also why the stop detector must not be "just another
subscriber" to a voice service: a shared process is a shared failure.

**Cloud STT does not put a key on the robot.** Audio goes robot → `back` → Deepgram; text
comes back. `back` already holds every credential, and the rule that **the robot holds no
cloud credentials** is a real security property — it is physically accessible, shared with
another team, and runs third-party containers. Nova-3 covers `es-419` at <300 ms streaming.
Local `faster-whisper` is the documented fallback and is *proven on this exact machine*:
`Systran/faster-whisper-base` is cached on-robot from the co-tenant's own mic→Whisper work.
It costs accuracy and Orin compute, and buys back only the network — which the agent needs
anyway, so it buys nothing the stop phrase does not already provide.

**TTS is local, and that is a change from D6.** D6 assumed cloud TTS; D6.1 established that
*external* synthesis is mandatory because the firmware has no Spanish voice. External does
not have to mean cloud. Piper runs on aarch64 from a prebuilt binary, streams raw 16-bit
mono PCM on `--output-raw`, needs no key, and works with the network down — which matters
precisely for the sentences worth saying when things are going wrong ("estoy atascado").

**The `es_AR` voice costs a resampler, and that is the trade to make consciously:**

| voice            | accent        | quality | rate      | vs `PlayStream`'s 16 kHz    |
| ---------------- | ------------- | ------- | --------- | ---------------------------- |
| `es_AR/daniela`  | **Argentine** | high    | 22 050 Hz | needs 22050→16000 (320/441)  |
| `es_ES/carlfm`   | Spain         | x_low   | 16 000 Hz | native, no resampling        |

`PlayStream` hard-rejects anything but 16 kHz mono 16-bit — both vendor examples enforce
it. Neither `ffmpeg` nor `sox` is installed on the robot, so a resampler is a Python
dependency we would be adding. Recommendation: **`es_AR/daniela` plus a polyphase
resample**, because the robot talking to Argentine students in a Spain accent is a daily
papercut and the resampler is one dependency, written once.

**Three constraints that are not ours to fix, and must be designed around:**

1. **The vendor assistant competes for the one speaker and cannot be disabled in
   software.** `vui_service` provides TTS, `PlayStream`, volume *and* the light strip — one
   service, so silencing the assistant silences us. `PlayStop` is scoped by `app_name`, so
   we cannot stop their stream and they cannot stop ours. Use our own `app_name`, expect
   contention, do not plan around removing it.
2. **The onboard ASR is unreachable by design.** `voice` api 1002 is registered by every
   vendor client and called by none, and the built-in recognition is gated on *wake-up
   mode*, switched by **L1+L2 on the remote or in the App** — a human prerequisite we
   cannot satisfy over DDS. This is why STT is ours and not the robot's.
3. **Audio is almost certainly not FSM-gated**, which makes speech the channel that still
   works when motion is being refused. Structural evidence only; the cheap confirmation is
   one `GET_VOLUME` in each reachable state.

**THE ONE TEST THAT GATES ALL OF THIS.** It is unknown whether the raw multicast mic feed
is gated on the same remote-controlled wake-up mode as the onboard ASR. If it is, there is
no microphone available to us over DDS at all and the entire listening half collapses —
every other decision here is downstream of that answer. The test is a **zero-risk, sub-minute,
no-motion** check: join `239.168.123.161:5555` bound to the `192.168.123.*` interface and
count packets for ten seconds, once with the assistant idle and once after L1+L2. Do it
**before** any of this is built. Note `INADDR_ANY` yields zero packets with no error, so a
silent result proves nothing unless the interface was bound correctly.

**The wake-word model is the real work, and it is safety-critical.** openWakeWord ships
English models only; other languages go through the documented synthetic-TTS training path
(Piper/Kokoro voices, ~13 k positive samples, GPU, Linux) — which is exactly what the
LAN H100 is for: **train off-robot, infer on-robot**, the same shape as the detector
fine-tune. Two models are needed and they are not equally forgiving: a conversational wake
phrase can afford false negatives, while the **stop phrase cannot**, and it has to fire for
a stressed speaker whose pronunciation degrades under exactly those conditions. Decide the
phrase for separability, not charm — short, distinct, and unlikely in ordinary speech.
D6.1's warning stands: a spoken stop that works in demos and not in panic is worse than
none, because people rely on it.

**Not built.** Nothing above exists. `apps/bridge` has `speak()` (firmware TTS) and no
`play_pcm()`; the SDK's `_CallRequestWithParamAndBin` is present and unused.

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
