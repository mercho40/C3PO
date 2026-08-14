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

### As an MCP server for Claude Code (recommended)

The repo's `.mcp.json` already has a `c3po-bridge` entry that points here. With the bridge configured, Claude Code auto-launches it on startup; tools like `mcp__c3po-bridge__get_state` and `walk_to` become available in the session.

Manual run (for debugging / non-Claude-Code clients):

```bash
CYCLONEDDS_HOME=$HOME/.local/cyclonedds-0.10.2 \
SIM_MODE=isaac ROBOT_HOST=<sim-host-ip> DDS_DOMAIN_ID=1 \
uv run python -m bridge.mcp_server
```

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
| `scripts/postsync.sh`         | Patch unitree_sdk2py's broken `__init__.py` after `uv sync`                                                                          |

All scripts assume `CYCLONEDDS_HOME`, `ROBOT_HOST`, and `DDS_DOMAIN_ID` are set in the environment.

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
- [ ] **Phase 4** — voice loop (wake word, Deepgram STT, Cartesia TTS)
- See `docs/SPEC.md` §12 for the full plan

## Architecture

```
apps/bridge/src/bridge/
  mcp_server.py        FastMCP stdio server — three tools today
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
    _g1_request.py      Posture/gesture dispatcher — stub / sim (logged-only) / real (g1_rpc)
```

## Known issues

- **`unitree_sdk2py` upstream `__init__.py` is broken** — imports a `b2` submodule that isn't shipped. Local patch via `scripts/postsync.sh`. Long-term: fork upstream or wait for a fix.
- **macOS multicast for DDS is unreliable.** Worked around by generating a unicast peer XML at startup (see `sdk/connection.py`).
- **Walk policy is conservative** — effective forward speed is ~10–15% of commanded velocity. Build generous timeouts into `walk_to` calls. (Sim-only today — see Phase 1b above for why real-hardware `walk_to`/`turn` don't work yet.)
- **`get_state().posture` is `"not_available_over_dds"` in real mode** — `LowState_.mode_machine` isn't the locomotion FSM index `g1_protocol.mode_label()` decodes (that's `sportmodestate.mode`, which has no DDS-decodable type for G1 in this SDK). Don't re-wire `mode_label(mode_machine)` for real mode without confirming what `mode_machine` actually encodes on G1 (looks like a hardware/arm-config variant, not FSM state).
- **`stop_everything`'s real-hardware fallback was a no-op — fixed 2026-08-07.** Its safety burst published to `rt/run_command/cmd` (sim-only). It now also dispatches `damp` via `g1_rpc` when `SIM_MODE=real`. Not yet live-tested (robot was offline) — smoke-test this specifically before relying on it.
- **`g1_protocol.Mode.SQUAT` (2) is unverified** — the reference implementation never sends it for G1; both its "Squat" and "Squat-Up" buttons send `SQUAT_UP` (706). The `squat` skill now sends 706. `Mode.SQUAT=2` and the `can_transition` FSM rules that reference it are unexercised — treat with suspicion if you rely on them.
