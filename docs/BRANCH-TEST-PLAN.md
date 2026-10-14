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

---

## A. Before leaving (Mac, ~10 min, no robot needed)

| #   | Check                           | Command                                             | Pass                                                                                                                                                                    |
| --- | ------------------------------- | --------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
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

The school AI (`ia.ort.edu.ar`) only resolves on the school network, so chat
and voice can only be checked there.

## B. On arrival: network and deploy (~10 min)

Only check out the branch on the robot if mercho40 has said yes to testing it
before the merge. Rollback is in §G.

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

| #   | Pass                                                                               |
| --- | ---------------------------------------------------------------------------------- |
| B1  | `c3po restart` prints "answers on port 8001"                                       |
| B2  | `c3po status`: bridge bind is `127.0.0.1:8001`, gate closed, no problem lines      |
| B3  | `journalctl -u c3po-bridge -n 200 \| grep dds.init` says `interface_reason=pinned` |

`apps/perception/vision/c3po_vision/stream.py` changed in this branch, and it
is built into the image: the camera client-leak fix only runs after
`c3po perception build vision`. That build is long — start it now in a second
ssh session if you want to test it, otherwise skip §D3.

## C. Mac: tunnel, console, preflight (~5 min)

```bash
ssh -N -o ControlMaster=no -L 8001:127.0.0.1:8001 -L 8767:127.0.0.1:8767 c3po   # keep running
bun run dev                     # from ../c3po-integrate: back :3000 + web :3001
./scripts/preflight.sh
```

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

```bash
psql -d c3po_integrate -c "select created_at, skill_name, source, user_id is not null as has_user, status, error from tool_call_log order by created_at desc limit 8"
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

Sequence first, from the console: `damp` → `prepare` → `start_walking_waist`
(mode 501).

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

```
Date / people:
Robot commit before: ________   Branch tested: integrate/october @ ________
A ok? __  B1 __ B2 __ B3 __  C1 __ C2 __ C3 __
D1 __ D2 __ D3 __ D4 __ D5 __ D6 __
E1 __ E2 __ E3 __ E4 __ E5 __
F1 __ F2 __ F3 __ F4 __
Rolled back? __   Notes:
```
