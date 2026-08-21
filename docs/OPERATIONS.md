# C3PO — Operations

Where each piece runs, how it deploys, who owns the robot at any moment, and the traps
that have already cost real debugging time. How the system fits together is
`docs/ARCHITECTURE.md`; what the hardware presents is `docs/ROBOT-HARDWARE.md`; the
reverse-engineered control API is `docs/ROBOT-API.md`; why choices were made is
`docs/DECISIONS.md`. Per-app dev docs live in each app's README.

---

## 1. Topology

```mermaid
flowchart TB
  subgraph CLOUD["Cloud"]
    VERCEL["apps/web — Vercel"]
    NEON["Postgres — Neon"]
  end

  subgraph LAN["School LAN (EDU-Special)"]
    UBUNTU["apps/back — Ubuntu box<br/><small>compiled Bun binary + systemd</small>"]
    subgraph JETSON["G1 Jetson (g1-orin.local)"]
      BRIDGE["apps/bridge<br/><small>uv, SIM_MODE=real</small>"]
      PERCEPTION["apps/perception<br/><small>two containers</small>"]
    end
  end

  CTRL["G1 control board"]
  LIDAR["Livox Mid-360"]

  VERCEL -->|HTTPS| UBUNTU
  UBUNTU -->|"MCP/HTTP via SSH tunnel"| BRIDGE
  UBUNTU --> NEON
  PERCEPTION -->|"DDS, own domain"| BRIDGE
  BRIDGE -->|"DDS domain 0, eth0"| CTRL
  PERCEPTION -->|"internal LAN"| LIDAR
```

| Component         | Where                     | How it deploys                                                     |
| ----------------- | ------------------------- | ------------------------------------------------------------------ |
| `apps/web`        | Vercel                    | git push (see `apps/web/README.md`)                                |
| `apps/back`       | Ubuntu box on the LAN     | `bun build --compile` → one self-contained binary + systemd (§5)   |
| Postgres          | Neon (managed, sa-east-1) | `drizzle-kit migrate` on deploy (§7)                               |
| `apps/bridge`     | G1 Jetson                 | `~/c3po` checkout; `git pull && stop_c3po && run_c3po`; boot unit  |
| `apps/perception` | G1 Jetson                 | `build_perception`, then `perception_up <stage>` — never automatic |

Neon is the live DB only; local dev runs against a Homebrew Postgres — setup in
`apps/back/README.md`.

**Why perception cannot move off the robot.** The Livox sits on the robot's _internal_
`192.168.123.0/24` network and the RealSense is USB-attached to the Jetson
(`docs/ROBOT-HARDWARE.md`) — neither is reachable from the school LAN. Even if they were,
that would put a navigation control loop across Wi-Fi. Perception and Nav2 stay onboard;
`back` is the only component that moved off the robot. Why the bridge must be onboard and
why `back` must not be is argued in `docs/ARCHITECTURE.md`.

**Sim topology.** Isaac Sim runs on a separate Ubuntu host, on DDS domain 1. The router
blocks sim-host→Mac UDP across VLANs, so a Mac-hosted bridge cannot receive state from
the sim — drive it from a bridge running locally on that box. Connection values for the
locally spawned sim server live in `.mcp.json` (`c3po-sim`).

---

## 2. Ports and addressing

The port map, in one place:

| Service                    | Host                    | Port  | Notes                                                                            |
| -------------------------- | ----------------------- | ----- | -------------------------------------------------------------------------------- |
| `apps/back` HTTP           | LAN box (dev: anywhere) | 3000  | Better Auth cookie                                                               |
| `apps/web` dev             | dev machine             | 3001  | Vite                                                                             |
| `apps/bridge` MCP (daemon) | Jetson, **loopback**    | 8001  | streamable HTTP `/mcp`; 8000 is held by `gemm-ai.service`                        |
| `apps/bridge` MCP (child)  | —                       | stdio | when an MCP client spawns it as a child process                                  |
| `apps/bridge` WS           | Jetson                  | 7077  | **planned, not built** — token must be enforced once off-loopback                |
| Vision MJPEG               | Jetson, **loopback**    | 8081  | `/live-camera`'s real-robot feed; only up with `perception_up perception`/`nav2` |
| Head camera via videohub   | Jetson, **loopback**    | 8001  | `/camera/*` on the bridge — the same feed **without** owning `/dev/video4`       |
| Isaac Sim DDS              | Ubuntu sim host         | 7400+ | UDP (CycloneDDS)                                                                 |
| G1 internal DDS            | control board           | 7400+ | multicast, wired internal LAN only                                               |

`DDS_DOMAIN_ID`: Isaac Sim is `1`, the real G1 is `0` (set per host in `apps/bridge/.env`;
see `apps/bridge/.env.example`).

**Address the robot by name, never by IP.** The Jetson is `g1-orin.local` (mDNS,
`avahi-daemon` onboard; SSH user `unitree`, `c3po` Host alias in `~/.ssh/config`). Its
DHCP lease has already moved twice, and one of the old addresses later answered as a
_different device_ — a stale IP does not fail closed, it reaches the wrong machine. Use
the name in `~/.ssh/config`, in `BRIDGE_URL`, everywhere. Lease history, MAC, Wi-Fi
whitelist, and the vendor route trap that black-holes the robot's egress are in
`docs/ROBOT-HARDWARE.md` — the route trap must be re-checked after any Unitree OTA.

---

## 3. DDS domain map

| Participant                      | Domain     | Why                                                                                                                                   |
| -------------------------------- | ---------- | ------------------------------------------------------------------------------------------------------------------------------------- |
| G1 control board, `ai_odom_node` | 0          | Vendor's, not ours to change                                                                                                          |
| `apps/bridge`                    | 0 (+ ours) | Must be on 0 to reach the control board                                                                                               |
| `apps/perception`                | ours — 42  | Keeps our Nav2/TF/costmaps away from `gemm`'s                                                                                         |
| `gemm` stack                     | 0          | Theirs — incl. `gemm-ai.service`, a live domain-0 participant pinned to `eth0`, not just a port-holder (`docs/ROBOT-HARDWARE.md` §10) |

The bridge spans the boundary — it is the actuation chokepoint (`docs/DECISIONS.md`
D2.1). **It must never subscribe to a bare `/cmd_vel`**: on a shared domain that would
mean another stack's planner driving the robot through our actuation path. Perception
publishes `/c3po/cmd_vel` and the bridge decides what becomes motion.

---

## 4. One owner of the robot

Two independent stacks share the G1 — ours and the colleague's `gemm` workspace. The
sensors have exactly one OS-level owner each, and the control API cannot be
domain-isolated: both stacks must sit on DDS domain 0 to reach the control board at all,
and the firmware arbitrates nothing (every vendor client runs `enableLease=false`). The
invariant is **one commander of the robot at a time**, and it is entirely ours to
enforce. The full rationale, the sensor-ownership facts, and the list of known
other-commander processes live in the `scripts/robot/_common.sh` header; the `gemm`
stack itself is described in `docs/ROBOT-HARDWARE.md`.

### The durable install

`./scripts/robot/install_stack.sh` (needs sudo, run on the robot) installs the whole stack
as systemd units, symlinked from the checkout so `git pull` updates them and only a
`daemon-reload` is needed:

| Unit                              | What it is                                                                                                                              |
| --------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| `c3po-bridge.service`             | The bridge. **Enabled** — it owns `stop_everything`, so it should always be up                                                          |
| `c3po-perception@<stage>.service` | Templated: the STAGE is the instance name (`c3po-perception@nav2-fake`). Calls `perception_up` rather than duplicating the docker flags |
| `c3po-health.timer`               | Every 2 min: restarts a dead bridge, or a perception unit whose containers vanished                                                     |

**What it deliberately does NOT do**, and each of these is a decision rather than an
omission:

- **No sensor-claiming stage is enabled at boot.** `nav2` and `perception` take the Livox
  _and_ the RealSense from the other team; enabling either would take them again on every
  power cycle, including reboots nobody intended. Only `nav2-fake` — which claims nothing —
  is safe to enable. Start the sensor stages by hand inside an agreed window.
- **Nav2's lifecycle is not autostarted.** `autostart: false` is a safety decision: container
  start must never be the same event as "the robot is ready to be driven".
- **Nothing installed can arm the gate.** A watchdog that can start motion is not a watchdog.

**Boot does not require the network.** `run_c3po` syncs dependencies only when `uv.lock`'s
_hash_ has changed (not its mtime — `git pull` rewrites those), and a failed sync with a
usable venv left over is a loud warning rather than a refusal to start. A robot that cannot
be stopped is a far worse failure than one running yesterday's dependencies.

**Before letting a planner drive:** `c3po_preflight`. It checks the things `c3po_health`
does not — whether anything else can command the legs, whether the LiDAR is real or
synthetic, whether the gate is already armed — and refuses to print a reassuring summary
when any of them is unknown. It reports that the velocity clamps are **unmeasured** every
single time, because they are.

### Stack controls

`scripts/robot/`. `./scripts/robot/install_robot_scripts.sh` symlinks the stack controls
onto PATH — including `c3po_health`, `c3po_preflight` and `stop_perception`, which are
meant to be typed by bare name by somebody standing next to the robot. `build_perception`
and `measure.sh` are linked too; what each command _guarantees_ — the mechanics are in the
scripts' own headers:

| Command                 | Guarantees                                                                                                                                                                                                                  |
| ----------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `run_c3po`              | Stops `gemm` first (unless `C3PO_NO_TAKEOVER=1`); refuses if another commander or an untracked bridge is alive; syncs deps + re-applies the `postsync.sh` patch; starts the bridge and waits for its port, not just its pid |
| `stop_c3po`             | SIGTERM→SIGKILL the bridge _and its whole process tree_, then verifies no bridge survived; stops both perception containers                                                                                                 |
| `run_gemm`              | Stops our stack first, then starts their containers                                                                                                                                                                         |
| `stop_perception`       | Stops **only** the perception containers, leaving the bridge and its closed gate up — "perception is down" must never be the reason the robot is safe                                                                       |
| `c3po_health`           | Is the stack up? Reports the bridge, whether it _answers_, the domain-42 link, the perception stage and the co-tenant. `--repair` restarts dead units and nothing else                                                      |
| `c3po_preflight`        | Is it safe to let Nav2 drive? Blocks on a down bridge, another commander, an offline LiDAR or a **synthetic** perception stage. Changes nothing, arms nothing                                                               |
| `stop_gemm`             | `docker stop` (not `down`) so a docker-daemon restart does not resurrect them — though the container has been observed returning anyway (§9); warns about a `cmd_vel_to_loco` surviving outside docker                      |
| `perception_up <stage>` | States which shared sensors the stage claims _before_ claiming them; `fake` claims none                                                                                                                                     |

Starting either stack stops the other — forced by sensor ownership, not policy; doing it
in the scripts turns a confusing mid-startup `EBUSY` into an explicit "gemm was stopped
for you".

`C3PO_NO_TAKEOVER=1` means exactly one thing: start the bridge **without** stopping
`gemm`. It exists for the boot unit — a machine powering on is not a person asking to
own the robot.

**⚠️ `stop_gemm` does not stop `gemm-ai.service`.** It filters running _containers_, and
`gemm-ai` is a systemd unit. That service is a voice/vision assistant — verified to
issue no motion commands, so the one-commander invariant holds — but it binds
`0.0.0.0:8000`, which is why our bridge listens on 8001. If it must go:
`sudo systemctl stop gemm-ai` — coordinate first, it is theirs. Details in
`docs/ROBOT-HARDWARE.md`.

**⚠️ Stopping the bridge removes `stop_everything`.** The physical e-stop and the
firmware's 1 s `SET_VELOCITY` deadman still apply — they always do — but do not
`stop_c3po` while the robot is mid-task expecting to be cancellable.

Perception is **never** started by `run_c3po` or any boot path. Claiming the RealSense
and the Livox is a different conversation with the other team than "the bridge is mine",
so it is one explicit command: `perception_up <stage>`.

---

## 5. Per-component deploy

### `apps/web` → Vercel

Push to deploy. Env: see `apps/web/.env.example` (`BETTER_AUTH_SECRET` must match
`back`). Runtime and adapter constraints are in `apps/web/README.md`.

Since `back` sits on a private LAN, the console only works where that box is reachable —
fine for on-site use; exposing `back` publicly is a deliberate decision, never a side
effect.

### `apps/back` → Ubuntu LAN box

Builds to a single self-contained binary — no `node_modules` at runtime:

```bash
bun run build                      # → ./server
scp apps/back/server ubuntu-box:/opt/c3po/server.new
bunx drizzle-kit migrate           # migrate FIRST — deploy order matters (§7)
mv server.new server && systemctl restart c3po-back
```

Keep the previous binary alongside for rollback. Env: see `apps/back/.env.example` — it
is the authority for every variable, including the TIC AI gateway gotchas (plain HTTP,
ORT-network-only, hand-issued keys). Two things to verify per deploy target:

- **The box must have a route to the TIC AI gateway.** Without one, `back` boots and
  serves `/health`, `/skills`, `/state` — and fails every `/agent` call. This rules out
  Vercel for `back` and is a thing to _test_ on the LAN box, not assume. Gateway outages
  also happen; expect `/agent` to be down while the rest of `back` is fine — a 2026-08-18
  nginx 502 stretch surfaced to the operator as _"Failed after 3 attempts. Last error:
  Bad Gateway"_ after ~6 s (the AI SDK retries a 5xx twice; the HTML body never leaves
  `APICallError.responseBody`).
- **The three AI SDK packages move as a set**: `ai` + `@ai-sdk/openai-compatible` in
  `back`, `ai` + `@ai-sdk/svelte` in `web`. Their major numbers differ by design
  (currently majors 7 / 3 / 5 — exact pins in `apps/back/package.json` and
  `apps/web/package.json`), each carrying a provider-specification version. `ai@7`
  speaks spec v4 and refuses a v3 model with `UnsupportedModelVersionError` **on the
  first request only** — a mismatch typechecks and links, so CI (type-check only)
  passes it and `bun build --compile` freezes it into the shipped binary. Bump all
  three in one change and exercise one real `/agent` turn before shipping. npm
  publishes `ai-v6`/`ai-v5` dist-tags on all three if a line must be walked back.

### `apps/bridge` → G1 Jetson

A git checkout at `~/c3po`; Python and `uv` under `~/.local`; CycloneDDS prebuilt on the
box (paths in `apps/bridge/.env.example`). Deploy:

```bash
ssh c3po 'bash -lc "cd ~/c3po && git pull && stop_c3po && run_c3po"'
```

`run_c3po` runs `uv sync` and re-applies `scripts/postsync.sh` itself — a dependency
change needs no manual step. Config lives in `apps/bridge/.env` (not in git; template and
per-host values in `apps/bridge/.env.example`).

- **⚠️ A pull is a `stop_everything` outage.** The deploy line implies
  `stop_c3po` → `run_c3po` — a window with no `stop_everything` (§4). Do it with the
  robot in damp/zero_torque, never mid-task.
- **Transport trap.** The bridge's own default transport is **stdio** — correct when an
  MCP client spawns it as a child over pipes, fatal as a daemon: stdin is `/dev/null`,
  it reads EOF and exits before ever reaching the robot, presenting as a failed start
  with an empty log. `run_c3po` supplies `BRIDGE_TRANSPORT=http` /
  `BRIDGE_HOST=127.0.0.1` / `BRIDGE_PORT=8001` as defaults; `.env` only overrides.
- **Loopback is deliberate.** The bridge can command the legs and has no authentication
  of its own, so it must never bind to the school LAN. Reach it through an SSH tunnel
  with `ControlMaster=no` (a forward on a shared master evaporates when the master idles
  out) — the exact command and the never-spawn-a-second-bridge rule are in
  `apps/bridge/README.md`.
- **Non-interactive SSH PATH trap.** `~/.local/bin` is added by `~/.profile`, which a
  plain `ssh c3po 'run_c3po'` never sources — `uv` and the stack controls are not found.
  Use `bash -lc`, or absolute paths. The boot unit sets an explicit `PATH` for the same
  reason.
- **Boot unit.** `scripts/robot/c3po-bridge.service`, installed by
  `scripts/robot/install_boot_unit.sh`, enabled on the robot. It starts the bridge with
  `C3PO_NO_TAKEOVER=1` — a reboot brings the bridge (and `stop_everything`) up without
  stealing the robot from the colleague's stack, and it never initiates motion.
- **⚠️ Symlink trap.** `/etc/systemd/system/c3po-bridge.service` is a **symlink into the
  checkout** — that is what lets `git pull` update the unit — so deleting the file from
  the repo dangles the unit, and the next boot has no bridge and therefore no
  `stop_everything`. If the unit is ever genuinely retired:
  `sudo systemctl disable --now c3po-bridge` on the robot **first**, then remove the
  file — never the other way round.

Rollback: the bridge is a git checkout — `git checkout <sha> && run_c3po`.

### When the robot ignores everything

Symptom: every posture and locomotion command returns **rpc_code 0** and the
robot does not move. `get_state` reports `posture="unknown"` and `fsm_id=None`,
and the bridge log fills with `rpc_code=3102` from the FSM poller.

This is almost never a network fault, and it looks exactly like one — we
checked cables, `DDS_INTERFACE`, `ROBOT_HOST` and the peer config before
finding it on 2026-08-20. The robot has **no motion controller loaded**. The
colleague's `xr_teleoperate` calls `Enter_Debug_Mode()`, which loops
`ReleaseMode()` until nothing is loaded, and that state lives in the robot — so
killing their processes does not undo it, and neither does restarting ours.

Diagnose and fix, in that order:

```bash
cd ~/c3po/apps/bridge && set -a && . ./.env && set +a
uv run python scripts/select_motion_mode.py --check-only   # empty name = this
uv run python scripts/select_motion_mode.py                # robot supported!
```

`check_motion_mode` is the diagnostic that distinguishes this from a wrong FSM
id, because both answer `code 0` from the sport service and look identical
otherwise. Expect to need this **once per session** whenever the robot is
shared.

### Putting a Quest on the console

WebXR refuses `immersive-vr` outside a **secure context**, so browsing the headset
to `http://<mac-ip>:3001` gives a page where `navigator.xr` is simply undefined —
/vr-control reports "WebXR no está disponible" with nothing visibly wrong. HTTPS
with a self-signed cert works but means clicking through a certificate warning
inside a headset, per port.

`http://localhost` **is** a secure context (a potentially trustworthy origin, and
Quest Browser is Chromium), so the answer is USB: `scripts/quest_setup.sh` uses
`adb reverse` to forward the headset's own localhost to this machine. The page
becomes secure-context, `ws://localhost:8767` is same-scheme so there is no
mixed-content problem, and nothing is exposed to the school LAN — a bonus, given
the teleop socket has no authentication of its own.

The script verifies every port is listening **before** forwarding, because a
forward to a dead port succeeds now and fails later, in the headset, as a page
that will not load. Needs `adb` (`brew install --cask android-platform-tools`),
developer mode on the headset, and the in-headset "Allow USB debugging?" prompt
accepted — that last one is easy to miss. ⚠️ Not yet tested with a real headset.

### The VR teleop stream on the Jetson 🔧

One more process now runs beside the bridge, for `/vr-control`. It is not under
`run_c3po` or the boot unit — it is started by hand, per session, because it exists to
serve a person who is currently wearing a headset. The camera comes from
`apps/perception`'s vision container (`perception_up perception`, port 8081), which is the
process that owns the D435i.

| Process                | Start        | Port | What it is                                                      |
| ---------------------- | ------------ | ---- | --------------------------------------------------------------- |
| `bridge.teleop.server` | `run_teleop` | 8767 | Head yaw + both wrists + finger closure from the headset, 30 Hz |

The port numbering is not arbitrary and the constraint is tight — everything else on this
Jetson is already spoken for: **8000** `gemm-ai.service`, **8001** our bridge, **8081** perception's
vision MJPEG, **8765** the colleague's `foxglove_bridge`, **55555/60000** teleimager
itself (`docs/ROBOT-HARDWARE.md`).

It binds loopback and **has no authentication at all** — less even than the MCP transport,
which at least sits behind `apps/back`'s session guard, and it carries live setpoints for
the arms. Tunnel it:

```bash
ssh -N -o ControlMaster=no \
    -L 8001:127.0.0.1:8001 \
    -L 8081:127.0.0.1:8081 \
    -L 8767:127.0.0.1:8767 c3po
```

`ControlMaster=no` matters: a forward on the shared master evaporates when the master idles
out, and the failure then presents as an unreachable bridge with no obvious cause.

**One commander at a time, and the teleop server is one.** While a session is open it is
the only writer of `SetVelocity` — `/vr-control` suspends its own `walk_velocity` loop and
routes the walk buttons through the stream instead. Do not drive the robot from Claude Code
or the console while someone is wearing the headset; nothing at the DDS layer will stop you
(every vendor client is `enableLease=false`, so whoever publishes is obeyed) and the two
sets of setpoints will simply interleave.

**Both hardware paths in the teleop server ship disabled** and stay that way until a person
has run the corresponding check on the robot:

| Env                                                                             | Unblocked by                | Why it is gated                                                                                                                                                            |
| ------------------------------------------------------------------------------- | --------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `TELEOP_ARM_ENABLED=1`                                                          | `scripts/arm_sign_check.py` | No source gives the positive direction of any G1 arm joint                                                                                                                 |
| `TELEOP_HAND_ENABLED=1` + `TELEOP_HAND_TYPE=brainco` + `TELEOP_BRAINCO_OPEN_AT` | inspection                  | The hands are **two BrainCo** (settled 2026-08-19). Units are [0,1], not Dex3 radians — and BrainCo has **no firmware deadman**, so any hold must be bounded by the bridge |

Put them in `apps/bridge/.env` once settled, not on the command line, so the answer
survives the next session. Head-yaw turning and the walk axis are not gated — they ride on
`_locomotion.send_velocity_async`, which already carries the hardware clamp and sits above
the firmware's own `duration` deadman.

⚠️ **The teleop stream has never run against the robot.**

### `apps/perception` → G1 Jetson

Built with `scripts/robot/build_perception`, run with `perception_up <stage>` — and
never by `run_c3po` or a boot path (§4). Architecture, stages, and thresholds:
`apps/perception/README.md`.

---

## 6. Secrets

| Secret               | Web | Back |   Robot   |
| -------------------- | :-: | :--: | :-------: |
| `AGENT_API_KEY`      |  —  |  ✅  | **never** |
| `DATABASE_URL`       |  —  |  ✅  | **never** |
| `BETTER_AUTH_SECRET` | ✅  |  ✅  |     —     |

**The robot holds no model or database credentials**, because the agent runs in `back`.
That is a real security property: the robot is physically accessible, shared with
another team, and runs third-party containers. It is also easy to lose by accident —
moving the agent onboard for "lower latency" would hand an API key to the least trusted
machine in the system. Don't.

---

## 7. Migrations

`drizzle-kit migrate` against Neon, **before** restarting `back` — migrate-then-restart
is the deploy order.

`apps/back/migrations/meta/` **must stay committed**. It was gitignored once, which left
drizzle with no journal: every `generate` on a fresh clone emitted a full-schema `0000`
migration that collided with what was already applied. Re-adding `meta/` to `.gitignore`
breaks incremental migrations repo-wide.

Migrations are **additive-only**. There is no rollback story; adding one before the
first destructive migration is cheaper than after. Local-dev workarounds live in
`apps/back/README.md`.

---

## 8. CI / CD

CI (`.github/workflows/ci.yml`) runs type-check across all workspaces plus the bridge's
pytest. `apps/perception`'s pytest suites are **not** in CI yet, even though its Stage 0
cites them as the verification step. There is **no CD**: web is automatic on Vercel;
`back` would need a runner that can reach the LAN box (self-hosted, or a manual
`make deploy`); the bridge deploy is the one-liner in §5.

---

## 9. Known operational issues

Plain facts, not a roadmap. Each stays here until fixed.

- **Written and never run on hardware.** These are not suspected broken — they
  are untested, which is a different claim and a weaker one. Several can be
  checked in a single bring-up, so they are listed together:

  | What                                                    | How to check it                                                                                                         |
  | ------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
  | The camera relay (`:8001/camera` picking the live feed) | `take_camera`, then `curl :8001/camera/status` — `source` should flip from `videohub` to `vision` with no config change |
  | `build_perception` (now a shim over `bringup/build.py`) | `build_perception vision --dry-run`, then a real `vision` build                                                         |
  | `measure.sh`'s sampling loop (its parsing is tested)    | `measure.sh idle 90` — the verdict table should fill in, not read UNKNOWN                                               |
  | The `np.bool` fix in the detector                       | a build with a COLD engine cache; it is confirmed on a warm one                                                         |
  | The `Type=exec` bridge unit                             | its own entry below                                                                                                     |
  | The voice loop end to end                               | start it from the dashboard and say something to the robot                                                              |

  The last one is the only one that can move the robot, and it should be done
  with somebody's hand near the e-stop: the loop's whole job is turning
  overheard speech into tool calls.

- **The bridge unit's `Type=exec` cutover is prepared and NOT installed.**
  `scripts/robot/c3po-bridge-exec.service` replaces the hand-rolled pidfile,
  `nohup`, SIGTERM→SIGKILL escalation and process-tree kill in
  `run_c3po`/`stop_c3po` with what systemd already does — and removes the
  documented race where both write `~/.c3po/run/bridge.pid`, systemd waits for a
  pid that no longer exists, and `run_teleop` then refuses to start on the
  grounds that there is no e-stop. There is one. It became possible once the
  bridge started loading its own `.env` (`bridge/env_file.py`), which was the
  only thing `run_c3po` did that a unit could not.

  It is not live because it has never been started on the robot, and if it is
  wrong the bridge does not come up — which is the process that owns
  `stop_everything`. The cutover, its verification and its rollback are written
  at the bottom of that file; it is a two-minute swap with somebody present.

- **`back` → bridge under Bun: FIXED, on localhost at least.** This was recorded as a hard
  blocker — the MCP SDK's `StreamableHTTPClientTransport` failing under Bun 1.4.0 with
  _"The socket connection was closed unexpectedly"_ while the same code under Node listed
  all tools. Re-tested 2026-08-21 on **Bun 1.4.0 with SDK 1.29.0**: `apps/back`'s own
  `bridge/client.ts` connects, lists **31 tools**, and successfully calls `get_state`,
  `say` and `listen` against a stub bridge. The likeliest cause is the SDK upgrade; the
  original entry did not record which SDK version failed, which is why this took a
  re-test rather than a changelog read. **Record the dependency version next time a
  library bug is filed here.**

  **Now verified through the SSH tunnel too, 2026-08-22** — the condition the original
  failure was observed under. `apps/back`'s `bridge/client.ts` over `ssh -L
8001:127.0.0.1:8001` to the real robot: **33 tools listed**, `get_state` returned. So
  the tunnel's stream handling is not a factor and the fallback (plain request/response
  POSTs instead of the SDK transport) is not needed. The blocker is fully closed.

- **`BRIDGE_URL` default is wrong on both host and port** (`apps/back/.env.example`):
  the real target is an SSH tunnel to `g1-orin.local:8001`, not `127.0.0.1:8000`. The
  8000 default is also baked into code — `apps/back/src/bridge/client.ts` (`BRIDGE_URL`
  fallback) and the bridge's own `mcp_server.py` (`BRIDGE_PORT`) — while the robot
  deployment runs on 8001 (§2; `run_c3po` supplies it). The mismatch stands until fixed
  in code.
- **The planned `back` target host is unreachable.** `perrobot` (10.40.5.4) does not
  answer ping from the dev machine — a different VLAN, the same constraint that blocks
  sim→Mac DDS (§1). Deploying `back` needs that resolved or a different host.
- **No written agreement that `cmd_vel_to_loco` stays off.** The scripts detect and
  refuse (§4), but that is a backstop, not a substitute for the two teams agreeing who
  drives.
- **`gemm-bringup` comes back after an explicit stop.** Observed 2026-08-15: the
  container (`restart=unless-stopped`) was up again on its own after `run_c3po`'s
  explicit `docker stop` (§4) — "stopped" is not a permanent state. Verify with
  `docker ps` before counting on the sensors or the one-commander invariant.
- **The one-commander check has blind spots.** `OTHER_COMMANDER_PATTERNS` in
  `scripts/robot/_common.sh` covers `cmd_vel_to_loco|xr_teleoperate|brainco_hand_server`
  but not `unitree_slam` — its 1102 pose navigation closes its own PID velocity loop
  (`docs/ROBOT-API.md`) — or the returning gemm container's `gemm_robot_server`
  (audited: no `SetFsmId`/`SetVelocity`/`LocoClient`/`cmd_vel`/7101/7105 references, so
  no leg risk today; re-check if their backend grows). `warn_if_other_commander` should
  extend to both.
- **`bridge.log` is never rotated** — append-only structlog (path defined in
  `scripts/robot/_common.sh`). Add a logrotate entry before it matters, and make it
  cover the two perception container log streams and the CycloneDDS trace file
  perception adds, not just `bridge.log`.
- **Bridge WS `:7077` token is unenforced** — the WS transport is design, not built;
  once it leaves loopback the token is the only thing between the LAN and a humanoid's
  motion API (`docs/ARCHITECTURE.md`).
- **No migration rollback story** (§7).
- **No CD** (§8).
