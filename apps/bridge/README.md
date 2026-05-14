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

## Phase status

- [x] **Phase 0a** — stub MCP server (`get_state`, `walk_to`, `say`) — wiring validated end-to-end via Claude Code
- [x] **Phase 0b** — real DDS handshake to Isaac Sim, live `get_state` (pose + posture + tick at ~100 Hz)
- [ ] **Phase 1** — rest of the skill catalogue (`stand_up`, `sit_down`, `damp`, `turn`, `look`, `describe_scene`, `wave`, `point_at`, `say` real, `remember_landmark`, `recall_landmark`, `stop_everything`); MCP `progressToken` streaming; cancel
- [ ] **Phase 4** — voice loop (wake word, Deepgram STT, Cartesia TTS)
- See `docs/SPEC.md` §12 for the full plan

## Architecture

```
apps/bridge/src/bridge/
  mcp_server.py        FastMCP stdio server — three tools today
  sdk/
    connection.py      Generates CycloneDDS unicast peer XML + initialises ChannelFactory
    state.py           Subscribes to rt/lowstate + rt/sim_state; exposes get_state() shape
  skills/
    walk_to.py         Body-frame velocity loop, yaw correction + yaw-gating
```

## Known issues

- **`unitree_sdk2py` upstream `__init__.py` is broken** — imports a `b2` submodule that isn't shipped. Local patch via `scripts/postsync.sh`. Long-term: fork upstream or wait for a fix.
- **macOS multicast for DDS is unreliable.** Worked around by generating a unicast peer XML at startup (see `sdk/connection.py`).
- **Walk policy is conservative** — effective forward speed is ~10–15% of commanded velocity. Build generous timeouts into `walk_to` calls.
