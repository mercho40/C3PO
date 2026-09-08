# C3PO Bridge

Python 3.12 sidecar (uv-managed) that wraps `unitree_sdk2_python`, talks DDS (CycloneDDS) to Isaac Sim or a real Unitree G1, and exposes the robot's skill catalogue — locomotion, posture/gesture skills, landmark memory, task management, `stop_everything`, `get_state` — as MCP tools. The same skill code drives both targets; `SIM_MODE` selects the transport and dispatch path. How the bridge fits the rest of the system: [`docs/ARCHITECTURE.md`](../../docs/ARCHITECTURE.md).

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

(Or set it in `.env` and in `.mcp.json`'s env block. The robot's Jetson already ships a matching install — see `.env.example`.)

### 2. Python deps

```bash
cd apps/bridge
uv sync                  # installs Python 3.12 + cyclonedds + unitree_sdk2py + mcp
./scripts/postsync.sh    # patches unitree_sdk2py/__init__.py (upstream imports a non-shipped `b2`)
```

The `postsync.sh` patch must be re-applied after every `uv sync`. It's idempotent and safe to run anytime.

### 3. Configure

```bash
cp .env.example .env
```

`.env.example` is the authority on every variable — `SIM_MODE`, `ROBOT_HOST`, `DDS_DOMAIN_ID`, `DDS_INTERFACE`, `CYCLONEDDS_HOME`, `BRIDGE_*` — including the per-host values (Mac vs Jetson) in its comments. Don't duplicate those values elsewhere.

## Running

### The two MCP servers — know which machine you are about to move

The repo's `.mcp.json` defines two bridge entries, and the distinction is a safety property, not bookkeeping — the tool-name prefix tells you which machine is about to move:

| Server        | Tools                 | Target                                                                                                          |
| ------------- | --------------------- | --------------------------------------------------------------------------------------------------------------- |
| `c3po-sim`    | `mcp__c3po-sim__*`    | **Isaac Sim.** Spawned locally by Claude Code (`uv run … bridge.mcp_server`, `SIM_MODE=isaac`)                  |
| `c3po-bridge` | `mcp__c3po-bridge__*` | **The real G1.** `type: http` → `http://g1-orin.local:8001/mcp` — the onboard daemon over the LAN |

`c3po-sim` can never reach the real robot no matter what you set: it runs on the Mac, and the control board publishes DDS only on the robot's internal wired LAN (see `docs/ROBOT-HARDWARE.md`). Conversely, `mcp__c3po-bridge__*` tools command real hardware whenever the robot is reachable on the LAN.

### As an MCP child over stdio (sim / local dev)

Claude Code auto-spawns `c3po-sim` per `.mcp.json` (its env block there carries the sim settings). Manual run for debugging or other MCP clients — with `.env` configured:

```bash
uv run python -m bridge.mcp_server
```

### As a long-lived HTTP daemon (what runs on the robot)

`apps/back` and the `c3po-bridge` MCP entry connect over **streamable-http**, so a daemon has to be told to serve that transport:

```bash
BRIDGE_TRANSPORT=http BRIDGE_HOST=0.0.0.0 BRIDGE_PORT=8001 \
uv run python -m bridge.mcp_server
```

This is not optional polish. The default transport is **stdio**, which is right when an MCP client spawns the bridge as a child and talks over pipes — but a daemon's stdin is `/dev/null`, so on stdio it reads EOF and exits immediately, before it ever reaches the robot. The symptom is a process that "fails to start" with almost nothing in the log.

Onboard the G1 you do not run this by hand: `c3po up core` delegates the process lifecycle to `c3po-bridge.service` — see [`docs/OPERATIONS.md`](../../docs/OPERATIONS.md). The unit deliberately binds the daemon to the LAN; it can command the robot's legs and has no auth of its own.

### Driving the real robot from Claude Code

Start the bridge onboard with `c3po up core`, then point the MCP client directly
at `http://g1-orin.local:8001/mcp`. The repository's local `.mcp.json` already
uses that URL. The mDNS name matters because the robot's DHCP address moves.

**Why not spawn it over SSH instead**, which would need no tunnel:

```jsonc
// Tempting. Do not do this.
{
  "command": "ssh",
  "args": ["c3po", "bash -lc '… python -m bridge.mcp_server'"],
}
```

That starts a _second_ bridge process beside `c3po-bridge.service` — two processes able to command the legs through the same API, the exact condition `c3po up` refuses (one-commander invariant: `docs/OPERATIONS.md`). Keep one systemd-owned bridge and reach it directly.

### The teleop stream (Quest arm mirroring)

A third process, beside the MCP server and the camera relay:

```bash
uv run python -m bridge.teleop.server     # WebSocket on 127.0.0.1:8767
```

It carries head yaw, both wrists and finger closure from the headset at ~30 Hz
and is the only commander of locomotion while a session is open. Deliberately
not MCP: a stream of expiring setpoints is a different shape from a task, and
routing it through JSON-RPC would put a round-trip and a task-registry entry in
front of every frame. The onboard launcher binds it to the LAN with no auth of
its own. The Python module retains a loopback default for ad-hoc developer runs.

**Both hardware paths are off by default**, and stay off until a person has
verified what the documentation cannot tell us:

| Path                | Gate                                         | What has to happen first                                                                                                                |
| ------------------- | -------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| Arms (`rt/arm_sdk`) | `TELEOP_ARM_ENABLED=1` + `SIM_MODE=real`     | `scripts/arm_sign_check.py` — no source gives the positive direction of any G1 arm joint                                                |
| Fingers             | `TELEOP_HAND_ENABLED=1` + `TELEOP_HAND_TYPE` | `scripts/hand_probe.py` — which hands are fitted is unresolved, and the two candidates disagree on topic, type, motor count _and units_ |

With neither set, the stream still runs: head yaw turns the robot and the walk
axis drives it, which is the part that rides on already-vetted machinery
(`_locomotion.send_velocity_async`, the same hardware clamp `walk_to` uses).

### Direct skill calls (no MCP)

Skills are async — call them with `asyncio.run`:

```python
# from apps/bridge/, with env set
import asyncio
import bridge.mcp_server   # initialises DDS + state subscribers at import
from bridge.skills.walk_to import run
print(asyncio.run(run(target_x=1.0, target_y=0.0, stop_distance_m=0.4, timeout_s=60)))
```

## Diagnostic scripts

| Script                          | Purpose                                                                                                                                                                                                                                                                                                                                                                                                                                                                          |
| ------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `scripts/dds_scan.py`           | List DDS participants + topics across candidate domains — diagnose which domain a peer is on, what it publishes/subscribes                                                                                                                                                                                                                                                                                                                                                       |
| `scripts/peek_sim_state.py`     | Subscribe to `rt/sim_state` and print decoded pose JSON                                                                                                                                                                                                                                                                                                                                                                                                                          |
| `scripts/rotate.py <radians>`   | Rotate the robot in place by a yaw delta (see its docstring for the `--` trick with negative radians)                                                                                                                                                                                                                                                                                                                                                                            |
| `scripts/vr_smoke_test.py`      | **Supervised first-motion ladder** for the VR teleop path: read-only → speech → `wave` → `dance` → first `walk_velocity` → stop path. Prompts before every escalation, refuses to run against a stub, aborts on the first failure. Run it standing next to the robot with the e-stop in reach; `--skip-legs` omits the only stage that commands the legs                                                                                                                         |
| `scripts/teleop_smoke_test.py`  | **Supervised bring-up for the teleop stream**, acting as the browser so nothing needs a headset. Preflight -> confirm the arms are refused -> confirm a released dead-man commands nothing -> **settle the yaw sign** -> walk axis -> confirm a dropped socket stops the robot. Stage 4 is the one that matters: if the sign is inverted, turning your head left turns the robot right, and that is a bad thing to find out while wearing something that covers your eyes        |
| `scripts/arm_sign_check.py`     | **Settle the arm joint sign conventions.** Engages `rt/arm_sdk` from the measured pose, moves ONE joint by 12 degrees, holds, asks which way it went, returns to neutral. Prints a `JOINT_SIGNS` block to paste into `teleop/retarget.py`. Every prompt defaults to abort, and `--dry` rehearses the whole prompt sequence with no DDS and no motion. Run it standing next to a **standing** robot with the e-stop in reach — `arm_sdk` while walking is a reported balance loss |
| `scripts/select_motion_mode.py` | **Load a motion controller back on.** The fix for "every command returns rpc_code 0 and the robot does nothing" — which is what `xr_teleoperate` leaves behind: it calls `Enter_Debug_Mode()`, which releases the controller entirely, and that state survives killing their processes. Presents as `check_motion_mode` returning an empty name, 7001/7002 answering nothing, and `posture="unknown"`. Needed once per session on a shared robot                                 |
| `scripts/hand_probe.py`         | Passively subscribe to every candidate hand state topic and print what answers. Writes nothing. Largely historical now that the hands are settled as two BrainCo by inspection (`docs/ROBOT-HARDWARE.md`), but still the quickest way to confirm a hand is _connected_ — one was found unplugged                                                                                                                                                                                 |
| `scripts/postsync.sh`           | Patch unitree_sdk2py's broken `__init__.py` after `uv sync`                                                                                                                                                                                                                                                                                                                                                                                                                      |
| `scripts/mic_probe.py`          | Is the mic multicast flowing? Joins `239.168.123.161:5555` **bound to eth0** and counts packets. Verifies the join against `/proc/net/igmp` first, because binding the wrong interface returns zero packets with no error and a silent socket otherwise looks exactly like a gated microphone                                                                                                                                                                                    |
| `scripts/mic_wakeup_probe.py`   | The same count, **plus the remote's buttons** decoded from `LowState_.wireless_remote`. Settles whether audio is gated on wake-up mode: a run where nobody pressed anything and a run where the press changed nothing are the same column of zeros, and only the button field tells them apart. Reports INCONCLUSIVE rather than a confident zero                                                                                                                                |
| `scripts/listen_live.py`        | Live mic → Spanish transcript, printed as you speak. Prints packet counts either way, so a silent terminal can be told apart from "nobody is talking"                                                                                                                                                                                                                                                                                                                            |
| `scripts/listen_stdin.py`       | The same recogniser over **16 kHz mono PCM on stdin** — pipe a laptop microphone in over ssh. Makes the gated robot mic a source swap rather than a blocker                                                                                                                                                                                                                                                                                                                      |
| `scripts/install_piper.sh`      | Install Piper + the `es_AR` voice (Spanish TTS). Verifies against the server's `Content-Length` and resumes — a truncated model otherwise fails much later with `Model config doesn't exist`                                                                                                                                                                                                                                                                                     |
| `scripts/install_vosk.sh`       | Place the Vosk Spanish model for the spoken stop. Fetching it from the robot is the slow path (~7 KB/s); push it from a laptop instead                                                                                                                                                                                                                                                                                                                                           |

All scripts assume `CYCLONEDDS_HOME`, `ROBOT_HOST`, and `DDS_DOMAIN_ID` are set in the environment.

## Tests

```bash
uv run pytest        # all tests, see tests/
uv run pytest -v     # verbose
uv run ruff check src tests   # lint
uv run mypy src               # type-check
```

No DDS/hardware needed — everything that touches DDS is monkeypatched (`unitree_sdk2py`'s `ChannelPublisher`/`ChannelSubscriber`/RPC client are never actually constructed in test runs).

## Layout

```
src/bridge/
  mcp_server.py         FastMCP server — the skill catalogue as MCP tools; stdio or http transport
  skill_meta.py         safety/capability metadata attached to every tool (MCP _meta)
  watchdog.py           operator-link watchdog — safe the robot when the link drops
  world_model.py        world-model contract: what perception hands the agent
  sdk/
    connection.py       CycloneDDS init; generates unicast peer XML (macOS multicast workaround)
    state.py            state sampler — rt/lowstate + per-target pose source; real-mode FSM poller
    g1_protocol.py      api_ids, FSM mode table, services (reference: docs/ROBOT-API.md)
    g1_rpc.py           real-G1 RPC over plain DDS (sport/arm services) — no WebRTC
    perception_link.py  domain-42 link: world summaries in, Nav2 cmd_vel out
    ros_idl.py          hand-written ROS 2 IDL types (fixed, frozen shapes only)
  skills/
    _locomotion.py      the sim/real velocity seam (run_command JSON vs SET_VELOCITY RPC)
    _g1_request.py      posture/gesture dispatcher — stub / sim / real
    walk_to.py, turn.py cancellable locomotion tasks
    landmarks.py        remember/recall/list named poses
    task_runtime.py     Task records + registry, cooperative cancellation
    stop_everything.py  cancel-all + arm/hand release + zero-velocity burst (+ damp on real)
    walk_velocity.py    open-loop velocity, real hardware only — no pose needed
    dance.py            choreographed gesture sequence via call_arm()
    gesture.py          any preset action from the firmware's own 23-entry catalogue, by name
    arm_pose.py         move_arm — free-form joint posing via teleop's rt/arm_sdk driver
    hand.py             set_hand/open_hands — BrainCo grip scalar, no firmware dead-man
  teleop/               continuous teleoperation — a 30-60Hz control stream, not a task.
                        Own WebSocket ingest (8767), own process.
    protocol.py         wire frame -> validated dataclass; rejects NaN and non-unit quaternions
    retarget.py         operator wrist pose -> 7 joint angles per arm. Pure geometry, no IK
    arm_sdk.py          50Hz rt/arm_sdk LowCmd_ publisher — DISABLED unless TELEOP_ARM_ENABLED=1
    hands.py            grip scalar -> BrainCo [0,1] (or Dex3 radians, which this robot lacks)
    server.py           the session: WebSocket, three dead-men, dispatch
```

## Known issues

- **`unitree_sdk2py` upstream `__init__.py` is broken** — imports a `b2` submodule that isn't shipped. Local patch via `scripts/postsync.sh`. Long-term: fork upstream or wait for a fix.
- **The unicast peer XML is currently a no-op.** `sdk/connection.py` writes a unicast-peer/interface config and sets `CYCLONEDDS_URI` (intended as the macOS multicast workaround), but the vendor SDK's `ChannelFactoryInitialize` creates the domain with its own inline config, which overrides `CYCLONEDDS_URI` — so the bridge actually runs autodetermine + default multicast, and `DDS_INTERFACE` pinning does not reach CycloneDDS either. Verified empirically 2026-08-19; the pending fix and its constraints are tracked in `docs/ROBOT-API.md` (known divergences) and `apps/perception/README.md` (decisions list).
- **The sim walk policy is conservative** — effective forward speed is ~10–15% of commanded velocity, so build generous timeouts into `walk_to` calls. Those gains are fitted to the sim and will not transfer; real-hardware velocity semantics live in `docs/ROBOT-API.md`.
- **`LowState_.mode_machine` is not the locomotion FSM index** — verified on hardware (`mode_machine=5` while the FSM id was 802). Real-mode posture comes from the RPC FSM poller instead; the authoritative note is in `sdk/state.py`, the FSM story in `docs/ROBOT-API.md`.
- **`g1_protocol.Mode.SQUAT` (2) is unexercised** — the `squat` skill dispatches `SQUAT_UP` (706), matching the reference implementation. The FSM transition sets in `g1_protocol.py` are reference data, not an enforced guard (a `can_transition()` helper was removed unused). Details: `docs/ROBOT-API.md`.
