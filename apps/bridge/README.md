# C3PO Bridge

Python 3.12 sidecar that wraps the Unitree G1 SDK and exposes it to LLMs over MCP. Talks DDS (CycloneDDS) to Isaac Sim on a separate Ubuntu host or to a real G1 on the LAN. Same code path for both.

See [`../../docs/SPEC.md`](../../docs/SPEC.md) for the full architecture.

## Setup

### 1. CycloneDDS C library (macOS — first time only)

`unitree_sdk2_python` pins `cyclonedds==0.10.2` (Python bindings), which builds against the matching C library. There is no Homebrew formula for it, so build from source once:

```bash
brew install cmake
mkdir -p ~/Developer/cyclonedds-build && cd ~/Developer/cyclonedds-build
git clone --depth 1 --branch 0.10.2 https://github.com/eclipse-cyclonedds/cyclonedds.git
cd cyclonedds && mkdir build && cd build
cmake -DCMAKE_INSTALL_PREFIX=$HOME/.local/cyclonedds-0.10.2 \
      -DBUILD_IDLC=ON -DBUILD_TESTING=OFF -DBUILD_DDSPERF=OFF \
      -DENABLE_SECURITY=NO -DCMAKE_POLICY_VERSION_MINIMUM=3.5 ..
cmake --build . --target install -j $(sysctl -n hw.ncpu)
```

Then export the install prefix when running anything in this workspace:

```bash
export CYCLONEDDS_HOME=$HOME/.local/cyclonedds-0.10.2
```

(Or set it in `.env` and in `.mcp.json`'s env block.)

### 2. Python deps

```bash
cd apps/bridge
uv sync                  # installs Python 3.12 + cyclonedds + unitree_sdk2py + mcp
./scripts/postsync.sh    # patches unitree_sdk2py/__init__.py (upstream imports a non-shipped `b2`)
```

The `postsync.sh` patch needs to be re-applied after every `uv sync`. It's idempotent and safe to run anytime.

### 3. Configure

```bash
cp .env.example .env
# Edit .env: set ROBOT_HOST to your Isaac Sim host IP, DDS_DOMAIN_ID (1 by default),
# CYCLONEDDS_HOME, and SIM_MODE=isaac (or stub for dry-run, or real later).
```

## Running

### Driving the REAL robot from Claude Code

`.mcp.json` defines two servers, and the distinction is a safety property rather
than bookkeeping — the tool names tell you which machine you are about to move:

| Server        | Tools                   | Target                                        |
| ------------- | ----------------------- | --------------------------------------------- |
| `c3po-bridge` | `mcp__c3po-bridge__*`   | Isaac Sim. Spawned locally, `SIM_MODE=isaac`   |
| `c3po-robot`  | `mcp__c3po-robot__*`    | **The real G1**, over HTTP to the onboard bridge |

`c3po-bridge` can never reach the real robot no matter what you set: it runs on
your Mac, and the control board publishes DDS only on the robot's internal wired
LAN (`CLAUDE.md`, topology). The bridge has to run onboard. So `c3po-robot`
points at the onboard daemon through an SSH tunnel:

```bash
# Keep this running in its own terminal. ControlMaster=no matters — a forward
# on the shared master evaporates when the master idles out, and the MCP server
# then fails with no obvious cause.
ssh -N -L 8001:127.0.0.1:8001 -o ControlMaster=no c3po
```

Then start the bridge onboard (`run_c3po`) and reconnect MCP in Claude Code.
Without the tunnel, `c3po-robot` simply fails to connect.

**Why not spawn it over SSH instead**, which would need no tunnel:

```jsonc
// Tempting. Do not do this.
{ "command": "ssh", "args": ["c3po", "bash -lc '… python -m bridge.mcp_server'"] }
```

That starts a *second* bridge process on the robot alongside the one `run_c3po`
manages — two processes able to command the legs through the same API, which is
the exact condition `warn_if_other_commander` and `stray_bridge_pids` exist to
prevent (`docs/DEPLOYMENT.md` §2). One bridge, reached over a tunnel, keeps the
actuation chokepoint singular.

### As an MCP server for Claude Code (recommended)

The repo's `.mcp.json` already has a `c3po-bridge` entry that points here. With the bridge configured, Claude Code auto-launches it on startup; tools like `mcp__c3po-bridge__get_state` and `walk_to` become available in the session.

Manual run (for debugging / non-Claude-Code clients):

```bash
CYCLONEDDS_HOME=$HOME/.local/cyclonedds-0.10.2 \
SIM_MODE=isaac ROBOT_HOST=<sim-host-ip> DDS_DOMAIN_ID=1 \
uv run python -m bridge.mcp_server
```

### As a long-lived HTTP daemon (what runs on the robot)

`apps/back` connects to the bridge as an MCP client over **streamable-http**, which means the
bridge has to be told to serve that transport:

```bash
BRIDGE_TRANSPORT=http BRIDGE_HOST=127.0.0.1 BRIDGE_PORT=8001 \
uv run python -m bridge.mcp_server
```

This is not optional polish. The default transport is **stdio**, which is right when an MCP
client spawns the bridge as a child and talks over pipes — but a daemon's stdin is
`/dev/null`, so on stdio it reads EOF and exits immediately, before it ever reaches the
robot. The symptom is a process that "fails to start" with almost nothing in the log.

Onboard the G1 you do not run this by hand: `run_c3po` (see `scripts/robot/`) supplies these
three defaults, stops the colleague's stack first, and re-applies `postsync.sh`. Note the
port is **8001**, not the code default of 8000 — `gemm-ai.service` holds 8000 on the Jetson
(`docs/ROBOT-INVENTORY.md` §5).

Keep it bound to loopback. The bridge can command the robot's legs and has no authentication
of its own, so it should be reached over an SSH tunnel rather than bound to a shared LAN.

### The teleop stream (Quest arm mirroring)

A third process, beside the MCP server and the camera relay:

```bash
uv run python -m bridge.teleop.server     # WebSocket on 127.0.0.1:8767
```

It carries head yaw, both wrists and finger closure from the headset at ~30 Hz
and is the only commander of locomotion while a session is open. Deliberately
not MCP: a stream of expiring setpoints is a different shape from a task, and
routing it through JSON-RPC would put a round-trip and a task-registry entry in
front of every frame. Loopback-bound with no auth of its own — tunnel it, same
as 8001 and 8766.

**Both hardware paths are off by default**, and stay off until a person has
verified what the documentation cannot tell us:

| Path | Gate | What has to happen first |
| --- | --- | --- |
| Arms (`rt/arm_sdk`) | `TELEOP_ARM_ENABLED=1` + `SIM_MODE=real` | `scripts/arm_sign_check.py` — no source gives the positive direction of any G1 arm joint |
| Fingers | `TELEOP_HAND_ENABLED=1` + `TELEOP_HAND_TYPE` | `scripts/hand_probe.py` — which hands are fitted is unresolved, and the two candidates disagree on topic, type, motor count *and units* |

With neither set, the stream still runs: head yaw turns the robot and the walk
axis drives it, which is the part that rides on already-vetted machinery
(`_locomotion.send_velocity_async`, the same hardware clamp `walk_to` uses).

### Direct skill calls (no MCP)

```python
# from apps/bridge/, with env set
import bridge.mcp_server   # initialises DDS + state subscribers
from bridge.skills.walk_to import run
result = run(target_x=1.0, target_y=0.0, stop_distance_m=0.4, timeout_s=60)
print(result)
```

## Diagnostic scripts

| Script                        | Purpose                                                                                                                              |
| ----------------------------- | ------------------------------------------------------------------------------------------------------------------------------------ |
| `scripts/dds_scan.py`         | List DDS participants + topics across candidate domains — diagnose which domain Isaac Sim is on, what topics it publishes/subscribes |
| `scripts/peek_sim_state.py`   | Subscribe to `rt/sim_state` and print decoded pose JSON                                                                              |
| `scripts/rotate.py <radians>` | Rotate the robot in place by a yaw delta (e.g. `python scripts/rotate.py 1.5708` for 90° CCW)                                        |
| `scripts/peek_camera_relay.py` | Connect to a running `camera_relay` over WebSocket for 5s, print frame count/size/fps — sanity-check the relay before trusting `/vr-control`'s camera panel |
| `scripts/vr_smoke_test.py`    | **Supervised first-motion ladder** for the VR teleop path: read-only → speech → `wave` → `dance` → first `walk_velocity` → stop path. Prompts before every escalation, refuses to run against a stub, aborts on the first failure. Run it standing next to the robot with the e-stop in reach; `--skip-legs` omits the only stage that commands the legs |
| `scripts/hand_probe.py`       | **Settle which hands are fitted.** Subscribes passively to every candidate hand state topic (Dex3, BrainCo, Inspire) for 15s and publishes nothing at all. One received message decides an argument `docs/ROBOT-PERIPHERALS.md` §4 has never been able to close, and until it is closed `teleop/hands.py` refuses to drive any finger. A positive result is conclusive; silence is not |
| `scripts/arm_sign_check.py`   | **Settle the arm joint sign conventions.** Engages `rt/arm_sdk` from the measured pose, moves ONE joint by 12 degrees, holds, asks which way it went, returns to neutral. Prints a `JOINT_SIGNS` block to paste into `teleop/retarget.py`. Every prompt defaults to abort. Run it standing next to a **standing** robot with the e-stop in reach — `arm_sdk` while walking is a reported balance loss |
| `scripts/postsync.sh`         | Patch unitree_sdk2py's broken `__init__.py` after `uv sync`                                                                          |

All scripts assume `CYCLONEDDS_HOME`, `ROBOT_HOST`, and `DDS_DOMAIN_ID` are set in the environment,
except `peek_camera_relay.py`, which only needs `CAMERA_RELAY_HOST`/`CAMERA_RELAY_PORT` (both optional,
same defaults as the relay itself).

## Tests

```bash
uv run pytest        # all tests, see tests/
uv run pytest -v     # verbose
uv run ruff check src tests   # lint
uv run mypy src               # type-check
```

No DDS/hardware needed — everything that touches DDS is monkeypatched (`unitree_sdk2py`'s `ChannelPublisher`/`ChannelSubscriber`/RPC client are never actually constructed in test runs).

## Phase status

- [x] **Phase 0a** — stub MCP server (`get_state`, `walk_to`, `say`) — wiring validated end-to-end via Claude Code
- [x] **Phase 0b** — real DDS handshake to Isaac Sim, live `get_state` (pose + posture + tick at ~100 Hz)
- [x] **Phase 1a (real hardware, 2026-08-07)** — posture/gesture skills (`damp`, `prepare`, `start_walking`, `wave`, `shake_hand`, `hug`, `clap`, `sit_g1`, `lie_up`, `squat`, `zero_torque`, `release_arm`) dispatch to a real G1 over **plain DDS RPC** — `bridge.sdk.g1_rpc`, built on `unitree_sdk2py.rpc.client.Client` (the same generic base as Go2's `SportClient`). No WebRTC needed — that assumption in the original `_g1_request.py` was wrong. Verified live: `damp` and `prepare` both got `rpc_code=0` acks from real firmware in <1s.
- [ ] **Phase 1b — real-hardware `pose`** — `walk_to`/`turn` still fail with `no_pose` on real G1: the only pose source wired (`state.py`'s `_sim_sub`) is Isaac Sim's JSON `rt/sim_state`, which doesn't exist on real firmware (confirmed: `unitree_hg` has no `SportModeState_` IDL type, unlike `unitree_go`). A candidate real source exists — `rt/utlidar/robot_pose` (G1 ships a mid360 LiDAR) — but the reference implementation (`legion1581/unitree_ui`) explicitly **skips enabling LiDAR for the G1 family** ("Explorer webview never toggles it on"), so this path is unverified even there. Needs live hardware testing (toggle `rt/utlidar/switch`, confirm `rt/utlidar/robot_pose` actually publishes, check message type) before wiring it in — don't ship an untested pose source for something that drives autonomous locomotion. **Update (2026-08-07, robot offline — desk research only, see `docs/SPEC.md` §17.2.1):** real-world G1 projects (`deepglint/FAST_LIO_LOCALIZATION_HUMANOID`) don't use `rt/utlidar/*` at all — they run their own FAST-LIO SLAM stack (ROS1) over the raw Mid360. `rt/utlidar/*` is likely quadruped-only/immature on G1. Treat this as a SLAM integration project (and a ROS1↔DDS bridging problem), not a quick topic-subscribe — re-scope before starting.
- [x] **Phase 1b-workaround (2026-08-13)** — `walk_velocity` sidesteps the pose blocker entirely: an open-loop body-frame velocity command (`bridge.sdk.g1_rpc.call_velocity`, api_id `7105`/`SetVelocity`, discovered in `unitree_sdk2py.g1.loco.g1_loco_client.LocoClient` during VR-teleop research) on the **same verified-live DDS RPC channel** as the posture commands — no pose feedback needed because it doesn't try to reach a target, just sustains a velocity for a capped duration (same pattern Unitree's own `xr_teleoperate` uses for its controller-button locomotion). Clamped hard to 0.3 m/s / 0.3 rad/s / 3s per call regardless of what's requested — no closed loop means no way to self-correct, so blind commands stay small. **Not yet live-tested** — `SetVelocity` itself hasn't been dispatched against real hardware (unlike `SetFsmId`/postures, which are verified); first live call should be a short, small `vx` before trusting this further. Doesn't replace Phase 1b — `walk_to`/`turn`'s closed-loop, arrive-at-a-target behavior still needs real pose.
- [ ] **Phase 1c** — rest of the skill catalogue polish: `look`, `describe_scene`; MCP `progressToken` streaming for more skills
- [x] `remember_landmark`/`recall_landmark` (+ `list_landmarks`/`forget_landmark`) — done 2026-08-08
- [x] **VR teleop, `apps/web`'s `/vr-control` (2026-08-19)** — Quest 3 control surface built on the existing skill catalogue, no new bridge protocol: hold-to-walk buttons and WebXR head-yaw turning both dispatch `walk_velocity`, presets dispatch `wave`/`shake_hand`/`hug`/`clap`/the new `dance` skill (below). **Not yet live-tested** — built and unit-tested against a Jetson that wasn't reachable from the dev machine at the time (`10.10.32.19` timed out over the school LAN's VPN). Verify each piece — `walk_velocity`, `dance`, the camera relay — individually before trusting the combined page.
- [x] **`dance` skill (2026-08-19)** — `bridge/skills/dance.py`. Not a single firmware mode (`Mode.DANCE=503` is unverified and unwired); instead sequences three already-verified `Gesture` ids (`BOTH_HANDS_UP`, `HIGH_FIVE`, `WAVE_UNDER_HEAD`) through the same `call_arm()` primitive `wave`/`clap`/`hug` use, interleaved with `RELEASE_ARM` to respect the arm's per-gesture latch (error 7401 otherwise). `works_real=False` — same "not yet live-tested" posture as `walk_velocity`.
- [x] **Camera relay (2026-08-19)** — `bridge/camera_relay.py`, a separate process (`bun run camera-relay` / `python -m bridge.camera_relay`) from the MCP server. Passively subscribes to `teleimager.image_server`'s existing ZeroMQ JPEG feed (`docs/ROBOT-PERIPHERALS.md` §2.4 — the only live camera feed on the robot, and a different transport than the sim's per-camera WebRTC) and re-publishes frames over WebSocket for `apps/web`'s `/vr-control` to consume. Never opens `/dev/video4` itself, so it can't contend for camera ownership the way a second `videohub_pc4`/`realsense2_camera_node`/teleimager instance would. Loopback-bound by default (`CAMERA_RELAY_PORT=8766`, chosen to avoid every other port already spoken for on the Jetson — see the module's own docstring). **Not yet live-tested** against the real teleimager process.
- [x] **Arm teleoperation (2026-08-19)** — `bridge/teleop/`, plus `apps/web`'s `/vr-control` arm-mirror panel. The operator's wrists drive the G1's arms through `rt/arm_sdk`, which blends into the running locomotion controller (`executed = motion*(1-w) + ours*w`) rather than bypassing it, so the legs stay under the built-in controller and this works while merely standing. **No IK**: `xr_teleoperate` solves full inverse kinematics against `g1_body29_hand14.urdf`, and that URDF is not in this repo — guessing link lengths would produce a solver that converges confidently on the wrong elbow. `retarget.py` maps the shoulder-to-wrist *direction* and the *fraction of the operator's own reach* instead, both scale-free, with reach measured once at calibration. **Both hardware paths ship disabled** — see the table under "The teleop stream" for what has to be verified first, and by whom. 253 tests pass and none of them have touched a robot.
- [ ] **Phase 4** — voice loop (wake word, Deepgram STT, Cartesia TTS)
- See `docs/SPEC.md` §12 for the full plan

## Architecture

```
apps/bridge/src/bridge/
  mcp_server.py        FastMCP stdio server — three tools today
  camera_relay.py      Separate process: teleimager ZeroMQ JPEG feed -> WebSocket,
                        for apps/web's /vr-control. Not yet live-tested.
  sdk/
    connection.py      Generates CycloneDDS unicast peer XML + initialises ChannelFactory
    state.py           Subscribes to rt/lowstate + rt/sim_state; exposes get_state() shape
    g1_rpc.py           Real-G1 posture/gesture/velocity dispatch — plain DDS RPC
                        (rt/api/sport|arm/request), no WebRTC. Same base as
                        unitree_sdk2py's Go2 SportClient.
  skills/
    walk_to.py         Body-frame velocity loop, yaw correction + yaw-gating (sim-only until Phase 1b)
    walk_velocity.py    Open-loop velocity command, real hardware only — sidesteps Phase 1b
                        (no pose needed), clamped hard, not yet live-tested
    dance.py            Choreographed gesture sequence via call_arm(), not yet live-tested
    _g1_request.py      Posture/gesture dispatcher — stub / sim (logged-only) / real (g1_rpc)
  teleop/               Continuous teleoperation — a 30-60Hz control stream, not
                        a task. Own WebSocket ingest, own process.
    protocol.py         Wire frame -> validated dataclass. Strict: rejects NaN,
                        non-unit quaternions, unknown versions; dead-man fails closed
    retarget.py         Operator wrist pose -> 7 joint angles per arm. Pure geometry,
                        no IK — we have no G1 URDF, so it maps direction + fraction
                        of the operator's own reach, both of which are scale-free
    arm_sdk.py          50Hz rt/arm_sdk LowCmd_ publisher: blend-weight ramp from the
                        measured pose, rate-limited slew, FSM/staleness/contention
                        preconditions. DISABLED unless TELEOP_ARM_ENABLED=1
    hands.py            Grip scalar -> Dex3 (radians) or BrainCo ([0,1]). No default
                        hand type, and no default for BrainCo's open-end polarity
    server.py           The session: WebSocket, three dead-men, dispatch
```

## Known issues

- **`unitree_sdk2py` upstream `__init__.py` is broken** — imports a `b2` submodule that isn't shipped. Local patch via `scripts/postsync.sh`. Long-term: fork upstream or wait for a fix.
- **macOS multicast for DDS is unreliable.** Worked around by generating a unicast peer XML at startup (see `sdk/connection.py`).
- **Walk policy is conservative** — effective forward speed is ~10–15% of commanded velocity. Build generous timeouts into `walk_to` calls. (Sim-only today — see Phase 1b above for why real-hardware `walk_to`/`turn` don't work yet.)
- **`get_state().posture` is `"not_available_over_dds"` in real mode** — `LowState_.mode_machine` isn't the locomotion FSM index `g1_protocol.mode_label()` decodes (that's `sportmodestate.mode`, which has no DDS-decodable type for G1 in this SDK). Don't re-wire `mode_label(mode_machine)` for real mode without confirming what `mode_machine` actually encodes on G1 (looks like a hardware/arm-config variant, not FSM state).
- **`stop_everything`'s real-hardware fallback was a no-op — fixed 2026-08-07.** Its safety burst published to `rt/run_command/cmd` (sim-only). It now also dispatches `damp` via `g1_rpc` when `SIM_MODE=real`. Not yet live-tested (robot was offline) — smoke-test this specifically before relying on it.
- **`g1_protocol.Mode.SQUAT` (2) is unverified** — the reference implementation never sends it for G1; both its "Squat" and "Squat-Up" buttons send `SQUAT_UP` (706). The `squat` skill now sends 706. `Mode.SQUAT=2` and the FSM transition rules that reference it are unexercised — treat with suspicion if you rely on them. Those rules are reference data in `g1_protocol.py`, not an enforced guard: a `can_transition()` helper existed but was never called by anything, and was removed rather than wired up, because encoding partly-unverified rules client-side would refuse transitions the firmware would have accepted.
