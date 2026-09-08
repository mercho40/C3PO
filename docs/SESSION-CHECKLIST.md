# Robot session checklist

For a session with the physical G1 in front of you. Ordered so each step only
runs once the thing it depends on is proven — most bad sessions in this
project's history came from testing step 4 while step 1 was quietly broken.

Everything here uses tools that already exist (`c3po`, `c3po_health`,
`c3po_preflight`, `headset_check.py`). Nothing in this file needs to be
maintained separately from them.

**Read §0 before touching anything.** It is the step that is new, and skipping it
means testing yesterday's code while believing it is today's.

---

## 0. Deploy — the one that silently does nothing if you get it wrong

The robot runs from a git checkout at `~/c3po`, and
`/etc/systemd/system/c3po-bridge.service` is a **symlink into it**. So the unit
updates on `git pull` — **but only from a branch that contains the change**.

A pull on the wrong branch is a no-op that reports success, and the restart then
faithfully re-applies the old config. Check what you got; do not assume.

```bash
ssh c3po                     # or: ssh unitree@g1-orin.local
cd ~/c3po
git fetch --all
git status -sb               # WHICH BRANCH is this checkout on?
git pull
```

If the robot is on `main`, the work must be merged first (PR #2). If you want it
without merging, check out the branch explicitly:

```bash
git checkout worktree-vr-stereo-and-camera && git pull
```

**Then prove the file actually changed before restarting anything:**

```bash
grep BRIDGE_HOST ~/c3po/scripts/robot/c3po-bridge.service   # must say 127.0.0.1
```

If it still says `0.0.0.0`, the pull did not bring the change and everything
below is testing the old build. Stop and fix that first.

```bash
sudo systemctl daemon-reload
sudo systemctl restart c3po-bridge
ss -ltnp | grep 8001                                        # must NOT say 0.0.0.0
```

### The tunnel, and why one is enough

`/mcp` and `/camera/*` are routes on the **same process, same port**. One forward
covers the console, the agent and the headset picture:

```bash
ssh -N -L 8001:127.0.0.1:8001 -o ControlMaster=no c3po      # keep this running
```

`ControlMaster=no` is not decoration: a forward on a shared master evaporates
when the master idles out, which presents as a tunnel that worked and then
stopped.

⚠️ **`apps/back/.env` must point at the tunnel, not the LAN.** The bridge no
longer accepts LAN connections:

```
BRIDGE_URL=http://127.0.0.1:8001/mcp
```

If you forget, `apps/back` prints the fix to its own log on the first failed
connect — but you will have spent a minute thinking the robot is dead.

---

## 1. Is the stack up

```bash
c3po up operator      # bridge + camera perception + world model
c3po status
```

`c3po up` runs `c3po_health` itself at the end. Read it rather than skim it: it
reports **where the bridge is actually listening**, read from the socket, not
from any config file. A `bridge bind` problem line here means §0 did not land.

---

## 2. Verify the things this branch changed that hardware has never seen

These are the claims that are unproven until today. Each one is cheap and tells
you something specific.

### 2a. The DDS interface pin — `dds.init` must say `pinned`

`DDS_INTERFACE=eth0` reached nothing at all until now: the name went into a file
the SDK's domain never reads. It is now passed to `ChannelFactoryInitialize`.

```bash
journalctl -u c3po-bridge -n 200 | grep dds.init
```

- `interface_reason=pinned` → working as intended.
- `interface_reason=missing` → the name is not a NIC on this host. **Not fatal**:
  it falls back to autodetermine, which is exactly what shipped before, so the
  bridge still works. Fix `DDS_INTERFACE` in `apps/bridge/.env` at your leisure.
- `interface_reason=autodetermine` → nothing was configured. Fine on a laptop;
  onboard it means CycloneDDS picks among `eth0`/`wlan0`/`docker0` arbitrarily,
  and only `eth0` reaches the control board.

The last one matters more once perception is up, because containers raise
`docker0` — which is precisely when arbitrary selection starts picking wrong.

### 2b. The camera — CORS, and a stream that no longer leaks

```bash
curl -sS -D- -o /dev/null http://127.0.0.1:8001/camera/status \
     -H 'Origin: http://localhost:3001' | grep -i access-control
```

Expect `access-control-allow-origin: http://localhost:3001`. No header means the
headset's `crossOrigin="anonymous"` image cannot load and you get SIN IMAGEN
with everything else looking healthy.

```bash
curl -s http://127.0.0.1:8081/status      # vision container, if perception is up
```

Watch `clients` across a few page reloads. It must come back **down**. If it
climbs and stays, the client-leak fix is not in the running image — see §5.

### 2c. The lidar ring — an open room is no longer "offline"

```bash
curl -s http://127.0.0.1:8001/telemetry/scan | head -c 300
```

`lidar_online` is now computed from message **arrival** only. In a large open
space where every return is beyond range, expect `lidar_online: true` with
`free_space: null` — "the sensor is fine, nothing is in range". Previously this
reported the LiDAR as offline.

---

## 3. Headset

```bash
scripts/quest_setup.sh          # forwards the ports; prints which .env the
                                # camera port came from
```

Then, with the headset on and someone watching the robot:

```bash
uv run --project apps/bridge apps/bridge/scripts/headset_check.py
```

Supervised. It reports what the headset **shows**, which is not the same
question as whether the services are up.

---

## 4. Motion — last, and only after 1–3 are clean

```bash
c3po_preflight        # reports; changes nothing, starts nothing, arms nothing
```

Then the FSM sequence that this robot actually needs, in order. `damp` first,
always — it is the state the others are legal from:

| Step | Tool                  | What it does                              |
| ---- | --------------------- | ----------------------------------------- |
| 1    | `damp`                | zero stiffness, the canonical rest        |
| 2    | `prepare`             | stands up and takes its own weight        |
| 3    | `start_walking_waist` | mode 501 — the walk program for this body |

`start_walking` (500) has twice returned success and done nothing on this
chassis. Use 501.

**`stop_everything` is the e-stop** and needs no arguments by design. The
headset's PARAR button and the console dispatch the same call.

---

## 5. Known-unverified, so nobody reports these as new faults

- **The vision client-leak fix is not in the running image.**
  `vision/c3po_vision` is `COPY`d into the container at build time, so the fix
  stays inert until `c3po perception build` is re-run. If §2b shows `clients`
  climbing, this is why.
- **The mount calibration in `g1_odom_tf` is half applied.**
  `base_in_body_xyz` and `mount_yaw_deg` reach the static `body -> base_link`
  transform only; the dynamic `odom -> base_footprint` and the `/odom` Nav2
  steers on do not use them. `mount_yaw_deg` is 0.0 so that half is dormant, and
  setting it now logs a warning saying so. The horizontal offset (0.10 m) is
  live, which puts `base_footprint` ~10 cm from where the header says. **Fixing
  it wants a tape measure and this robot** — the values are labelled
  PLACEHOLDERS because nobody has measured them.
- **Nine skills are `works_real=False`**, and `UNVERIFIED_EXITS`, FSM label 802
  and the `measure_turn` yaw numbers still need robot time.
- **802 is probably not "run".** It was read live while the robot stood
  perfectly still. Resolve it by watching `fsm_id` transition during a
  supervised sequence.

---

## Rollback

The bridge is a git checkout, so rollback is a checkout:

```bash
cd ~/c3po && git checkout <sha> && c3po restart
```

If the bridge will not start at all, `bridge_sync` now refuses to start it when
the Unitree SDK patch fails to apply — deliberately. An unpatched SDK gives a
bridge that answers every health check and cannot command the robot, which is an
e-stop wired to nothing. The log names the fix:

```bash
cd ~/c3po/apps/bridge && ./scripts/postsync.sh
```
