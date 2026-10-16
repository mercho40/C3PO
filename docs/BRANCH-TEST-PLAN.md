# Hardware test plan for `integrate/october` (PR #3, #4, #5)

Everything in PR #3 passes CI and an end-to-end rehearsal against the stub
bridge, but none of it has run on the G1 or in the Quest. This is the ordered
list of what only the hardware can prove, with what "pass" looks like for each,
so one session produces the evidence the merge decision needs.

General procedure, safety rules and background live in
[SESSION-CHECKLIST.md](SESSION-CHECKLIST.md) and
[MONDAY-RUNBOOK.md](MONDAY-RUNBOOK.md). This file only adds what is new in this
branch. **Stop at the first failed gate** and write down what you saw — a
refusal is a result, not an obstacle.

People: one operator beside the robot with the physical e-stop in reach, one at
the Mac. Motion steps need the floor clear and the other team's agreement.

## What changed since the first session (2026-10-15)

The first hardware session ran **main @ 73d56a0 on the robot** (this branch was
not deployed) with this branch's console on the Mac. What it showed, and what
this branch now does about it:

| Seen on 2026-10-15                                                                                          | Now (commit)                                                                                                                                                                                          | Proven by |
| ----------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------- |
| PARAR **damped** a walking robot (`real_damp_fallback_succeeded: true`) — on the floor it would have fallen | damp only if the robot did not acknowledge the zero-velocity burst; it stays standing in 501 at zero velocity. `STOP_EVERYTHING_DAMP=always` restores the old behaviour (`1e91aad`)                   | D7        |
| `start_walking_waist` acked and stayed in FSM 4 — robot on the hanger, feet not loaded                      | the refusal now says to lower the hanger until the feet bear weight, and reports `fsm_mode`; the tile says it up front (`7cba1e2`)                                                                    | F0        |
| walk_velocity rows "duplicated" in the same millisecond, a forward 12 ms after a stop                       | not duplicates: rows were stamped on finish. But a press **could** wait ~0.9 s behind a skill-list refresh and land after its stop. Lookups no longer wait; rows are stamped at send time (`dc11842`) | D8        |
| after a reboot, `run_c3po` **stopped gemm-bringup** unasked, then failed on sudo's "a terminal is required" | gemm is never stopped without `C3PO_TAKE_OVER=1`; perception stages that need gemm's sensor refuse and print the command; no-TTY sudo fails with `ssh -t …` (`f0a47ff`)                               | B4        |
| —                                                                                                           | main's SvelteKit 3 update merged in; **needs Bun ≥ 1.4.3** on the Mac, and the preflight checks it (`ccd7d7d`, `52669da`)                                                                             | A0, C1    |

Already passed on 2026-10-15 (robot on main, console from this branch): login
and `/dashboard` (C2/C3); camera through back (D1, source `vision`); walking
from the console pad, ±0.2 m/s; `wave`, `shake_hand`; PARAR audited with the
user (D4 — but it damped); `damp` and `prepare` transitioned.

---

## A. Before leaving (Mac, ~10 min, no robot needed)

| #   | Check                           | Command                                             | Pass                                                                                                                                                                    |
| --- | ------------------------------- | --------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| A0  | Bun is new enough               | `bun upgrade && bun --version`                      | ≥ 1.4.3 (package.json's `packageManager`). On 1.3.x apps/back dies with "Cannot find module '#back/...'"                                                                |
| A1  | Branch is current               | `git -C ../c3po-integrate pull`                     | fast-forward or "Already up to date"                                                                                                                                    |
| A2  | Dependencies                    | `bun install` (repo root)                           | no errors                                                                                                                                                               |
| A3  | `apps/back/.env`                | open the file                                       | `PORT=3000`, `BRIDGE_URL=http://127.0.0.1:8001/mcp`, your email in `ADMIN_EMAILS`                                                                                       |
| A4  | `apps/web/.env`                 | open the file                                       | `PUBLIC_API_URL=http://localhost:3000`, `PUBLIC_ROBOT_CAM_URL=http://localhost:3000/camera`, `PUBLIC_TELEOP_HOST=127.0.0.1`                                             |
| A5  | Database is migrated            | `cd apps/back && bun run db:migrate`                | no error; `chat.channel` and `tool_call_log.user_id/source` exist. A database built with `db:push` has no migration journal and this fails on 0000 — use a migrated one |
| A6  | Quest                           | charged, developer mode on, USB-C data cable packed | `adb devices` lists it as `device`                                                                                                                                      |
| A7  | Ports 3000/3001 free            | `lsof -nP -iTCP:3000 -iTCP:3001 -sTCP:LISTEN`       | nothing, or only this checkout                                                                                                                                          |
| A8  | Ethernet adapter + cable packed | —                                                   | —                                                                                                                                                                       |

A5 matters: this branch writes `tool_call_log.user_id` and `source`, and reads
`chat.channel`. Against an older database the audit rows are silently dropped
(the insert fails, by design never blocking the command) and `/chat` errors.

The school AI (`https://ia-tic.ort.edu.ar/api/v1`, https — http answers a 301) only resolves on the school network, so chat and voice can only be
checked there. A VPN such as Cloudflare WARP hides the school DNS: on
2026-10-15 the AI failed until WARP was disconnected.

Network that worked on 2026-10-15: the Mac on the robot's Wi-Fi
(`g1-robot`, robot at 192.168.77.1, ssh alias `c3po-wire`) and internet over
the Ethernet adapter on DHCP. Turn off auto-join for the school Wi-Fi, or the
Mac hops networks and the tunnel drops.

## B. On arrival: network and deploy (~10 min)

**First, agree with the other team** (gemm) who drives and for how long. Their
services start at boot (`g1-dashboard` on :8080, `g1-webrtc`, `g1-xr-debug`,
the `gemm-bringup` container). Since `f0a47ff` our scripts leave all of them
running. Two things genuinely cannot be shared:

- **a second motion commander** — `c3po up` refuses while one runs
  (`OTHER_COMMANDER_PATTERNS`); stopping it is their call;
- **the head camera** — `gemm-bringup`'s RealSense node holds `/dev/video4`, and
  then neither our perception (`vision`) nor Unitree's `videohub` feed has a
  picture. Without the camera the console still drives; for the headset demo,
  ask them to stop `gemm-bringup` for the slot, or with their OK run
  `C3PO_TAKE_OVER=1 c3po up operator`. Afterwards, `c3po gemm start` gives it
  back.

Only check out the branch on the robot if mercho40 has said yes to testing it
before the merge. **Without the deploy the robot keeps main's bridge, and PARAR
still damps** — keep it on the hanger for any stop test. Rollback is in §G.

Run everything that may need sudo **from a terminal** (`ssh -t c3po-wire`):
`run_c3po`'s `sudo systemctl start c3po-bridge.service` cannot prompt over a
plain `ssh host 'cmd'`.

```bash
ssh c3po-wire true || ssh c3po true          # cable (192.168.77.1) or Wi-Fi (g1-orin.local)
ssh c3po                                     # or c3po-wire; the rest runs on the robot
cd ~/c3po
git status -sb                               # note the branch; local changes? stop and ask
mkdir -p ~/.c3po && git rev-parse HEAD | tee ~/.c3po/previous-deploy   # what to roll back to
git fetch origin && git checkout integrate/october && git pull
grep BRIDGE_HOST scripts/robot/c3po-bridge.service  # must say 127.0.0.1
./scripts/robot/c3po install && c3po restart
c3po status                                  # read it, don't skim it
c3po up operator   # perception + world model, only once the other team agrees; E1's radar needs it
```

| #   | Pass                                                                                                                                                         |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| B1  | `c3po restart` prints "answers on port 8001"                                                                                                                 |
| B2  | `c3po status`: bridge bind is `127.0.0.1:8001`, gate closed, no problem lines                                                                                |
| B3  | `journalctl -u c3po-bridge -n 200 \| grep dds.init` says `interface_reason=pinned`                                                                           |
| B4  | with gemm running, `c3po up core` says "gemm is running … left alone" and `docker ps` still lists `gemm-bringup`                                             |
| B5  | after a reboot, `systemctl is-active c3po-bridge` is `active` without anyone starting it (on 2026-10-15 it was not — if not, `journalctl -u c3po-bridge -b`) |

`apps/perception/vision/c3po_vision/stream.py` changed in this branch, and it
is built into the image: the camera client-leak fix only runs after
`c3po perception build vision`. That build is long — start it now in a second
ssh session if you want to test it, otherwise skip §D3.

## C. Mac: tunnel, console, preflight (~5 min)

```bash
./scripts/robot_tunnel.sh c3po-wire   # terminal 1: 8001 + 8767, reconnects by itself, says if the bridge answers
bun run dev                            # terminal 2, from ../c3po-integrate: back :3000 + web :3001
./scripts/preflight.sh                 # terminal 3
./scripts/audit_watch.sh               # terminal 3 afterwards: every command the robot is sent, live
```

Open `http://localhost:3001/vr-control` once on the Mac before the headset: the
first load makes Vite build its dependencies, and doing that mid-session in the
Quest reloads the page.

| #   | Pass                                                                                                           |
| --- | -------------------------------------------------------------------------------------------------------------- |
| C1  | preflight: apps/back on 3000 is **this** checkout, bridge reachable, camera judged through back, no ✗          |
| C2  | `http://localhost:3001/login` → sign in (first time: sign up, restart back once, log shows `[admin] promoted`) |
| C3  | `/dashboard` and `/vr-control` load with no red banner you cannot explain                                      |

## D. Desk checks — robot in `damp` or standing still, no walking

| #   | What it proves (commit)                    | How                                                                                | Pass                                                                                                                          |
| --- | ------------------------------------------ | ---------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------- |
| D1  | Camera through back (`becb016`, `3458537`) | `/live-camera`, then the health page                                               | Picture visible; health page camera row green. If the camera is down, the page shows the bridge's reason, not "network error" |
| D2  | Snapshot route (`72454dd`)                 | snapshot button on `/live-camera`                                                  | a JPEG downloads                                                                                                              |
| D3  | Camera client leak (perception image)      | reload `/live-camera` 5×, then `curl -s http://127.0.0.1:8081/status` on the robot | `clients` goes back down. Only if the vision image was rebuilt in §B                                                          |
| D4  | PARAR is audited (`49f6528`)               | press PARAR in the console, then the query below                                   | robot stays still; newest row is `stop_everything`, `ok`, with your `user_id`                                                 |
| D5  | Voice can't enable walking (`9591464`)     | dashboard voice loop (school network): say "habilitá la marcha"                    | **no** `start_walking_waist` row in the audit log (high-danger skills are not offered to voice)                               |
| D6  | Spoken stop is audited (`49f6528`)         | voice loop: say the stop phrase                                                    | a `stop_everything` row with `source` = `voice`                                                                               |

| D7 | PARAR keeps the robot standing (`1e91aad`) — **only with this branch deployed, on the hanger, rope slack** | robot in 501, standing still; press PARAR | robot stays standing (does not go limp); button shows **"Velocidad cero"**, not "Sigue en movimiento"; the row's result has `stop_burst_acked` > 0 and `real_damp_fallback_reason` null. Then walk again: no re-prepare needed |
| D8 | Walk pad order (`dc11842`) | hold ▲ for ~3 s, release; repeat 3× | robot stops on release every time; in the query below the rows are in send order, each press's stop is the last row of its group, and no forward row comes after its stop |

```bash
psql -d c3po_integrate -c "select created_at, skill_name, source, user_id is not null as has_user, status, error from tool_call_log order by created_at desc limit 8"
# D7: what the stop did
psql -d c3po_integrate -Atc "select result from tool_call_log where skill_name='stop_everything' order by created_at desc limit 1"
```

(Use the database named in `apps/back/.env`.)

## E. Headset — supervised, robot standing

```bash
scripts/quest_setup.sh          # all ✓, then open http://localhost:3001/vr-control in the Quest
```

| #   | What it proves (commit)               | How                                                               | Pass                                                                                                                                                                   |
| --- | ------------------------------------- | ----------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| E1  | Observe-only (`8eb511b`, `a47428a`)   | tap **"Entrar solo para ver"**                                    | camera, radar and alerts show; "Solo vista — no comanda" badge; moving the stick, pressing A and the trigger send **nothing** (no new audit rows, robot does not move) |
| E2  | Latch reaches the headset (`a47428a`) | in observe mode, someone presses PARAR in the console             | the headset shows the stop alert                                                                                                                                       |
| E3  | PARAR from the headset                | full session ("Entrar"), press PARAR in the overlay               | robot stops; audit row                                                                                                                                                 |
| E4  | Preset menu (`8eb511b`)               | A cycles the menu; trigger fires **Saludar**                      | robot waves once; holding the trigger does **not** repeat it                                                                                                           |
| E5  | Camera reason in headset (`8eb511b`)  | opportunistic: whenever the picture is missing during the session | the headset says why (the same reason as the health page) instead of a black panel                                                                                     |

## F. Walking from the headset — last, floor clear, e-stop in hand

Sequence first, from the console: `damp` → `prepare` → **lower the hanger
until both feet bear weight (rope slack, still attached), wait ~5 s** →
`start_walking_waist` (mode 501).

| #   | What it proves (commit)      | How                | Pass                                                                                                                                                                                            |
| --- | ---------------------------- | ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| F0  | Walk enable from the console | the sequence above | posture reads walk (501). If it stays at 4, the console now says "No pasó a marcha: apoyá los pies en el piso" — load the feet; if R1+Y on the remote also fails, it is the robot, not our code |

| #   | What it proves (commit)                  | How                                                        | Pass                                                                      |
| --- | ---------------------------------------- | ---------------------------------------------------------- | ------------------------------------------------------------------------- |
| F1  | Stick walk                               | push the stick gently forward, then let go                 | walks forward; stops on release                                           |
| F2  | Stale tracking stops it                  | while walking, cover the headset's sensor / take it off    | walking stops within the staleness timeout                                |
| F3  | No re-arm after PARAR (`a47428a`)        | hold the stick forward, press PARAR, keep holding past 8 s | robot stays stopped until the stick returns to centre and is pushed again |
| F4  | Long command not cut at 60 s (`11c55d7`) | optional, only with space: a `walk_to` that takes > 60 s   | it completes or fails on its own terms; **not** a timeout at exactly 60 s |

## G. Rollback

```bash
cd ~/c3po
git checkout "$(cat ~/.c3po/previous-deploy)"
./scripts/robot/c3po install && c3po restart
```

## H. Optional: the proposal PRs

- **#4 (teleop on loopback)** — only if mercho40 OK'd it: check out
  `proposal/teleop-loopback`, `c3po install && c3po restart`, `c3po teleop start`,
  then on the robot `ss -ltnp | grep 8767` must show `127.0.0.1:8767`, and E/F
  must still work through the tunnel's `-L 8767`.
- **#5 (PARAR without DB)** — nothing to prove on the robot; it was tested on the
  Mac with Postgres stopped.

## Results (paste into PR #3)

2026-10-15 (robot on main @ 73d56a0, console on this branch): C2 ✓ C3 ✓
D1 ✓ (vision) D4 ✓ audited, **damped** · walk pad ✓ · wave/shake_hand ✓ ·
damp/prepare ✓ · start_walking_waist ✗ (acked, stayed at 4 — hanger) · robot
rebooted mid-session; gemm-bringup stopped by run_c3po (fixed in `f0a47ff`).
E/F not reached.

```
Date / people:
Robot commit before: ________   Branch tested: integrate/october @ ________
A ok? __  B1 __ B2 __ B3 __ B4 __ B5 __  C1 __ C2 __ C3 __
D1 __ D2 __ D3 __ D4 __ D5 __ D6 __ D7 __ D8 __
E1 __ E2 __ E3 __ E4 __ E5 __
F0 __ F1 __ F2 __ F3 __ F4 __
Rolled back? __   Notes:
```
