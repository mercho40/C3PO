# Test day — run order

One branch carries everything: **`feat/autonomy`** (= `integrate/october` + the five
autonomy workstreams). Deploy it once; the per-area checklists below each say "deploy
`auto/<x>`" — ignore that, they are all in `feat/autonomy`.

Order matters: **nothing new moves before the safety checks pass**, and nothing moves on
its own before it has moved on the gantry.

| #   | Area                                            | Checklist                                                                    | Moves the robot?      | Status 2026-11-03                                             |
| --- | ----------------------------------------------- | ---------------------------------------------------------------------------- | --------------------- | ------------------------------------------------------------- |
| 0   | Prep (tonight / before leaving)                 | this file §0                                                                 | no                    | —                                                             |
| 1   | Deploy + rebuild images                         | this file §1                                                                 | no                    | done; nav build cut off by the battery (4.5), re-run          |
| 2   | Safety governor                                 | [`safety.md`](safety.md)                                                     | gantry                | partial: latch, «Reanudar» and «Confirmar» ✅; steps 3-4 open |
| 3   | Vision tools (look / find_object / verify)      | [`vlm.md`](vlm.md) §1–5                                                      | no                    | ✅ Gemini `look`                                              |
| 4   | YOLOE engine, speed, sanity                     | [`perception.md`](perception.md) §2–4                                        | no                    | partial: engine and 40 ms ✅; pitch and test set open         |
| 5   | Object memory + `look_around`                   | [`memory-eval.md`](memory-eval.md) §1–6                                      | turns in place        | open                                                          |
| 6   | Nav2 goals, map, landmark                       | [`nav.md`](nav.md) §2–7                                                      | gantry → 0.5 m → room | blocked: Mid-360S faulted (4.1)                               |
| 6b  | Arms: URDF check, reach, shapes, pick, handover | [`manipulation.md`](manipulation.md)                                         | arms (gantry → stand) | partial: URDF sign and reach ✅; hands blocked (5.1)          |
| 7   | **Put together:** "buscá la billetera"          | this file §7                                                                 | yes                   | not run                                                       |
| 8   | Measure: eval T0 subset, YOLOE test-set capture | [`memory-eval.md`](memory-eval.md) §7, [`perception.md`](perception.md) §5–7 | little                | open                                                          |

Someone holds the e-stop for every step from 2 on. Rollback flags are at the bottom.

## 0. Prep

- [ ] `feat/autonomy` is on GitHub (PRs #7/#8/#9), but the robot has no internet on the cable:
      it gets the branch by a push from the Mac (§1).
- [ ] YOLOE ONNX files are already exported on Oliver's Mac in `~/c3po-models/yoloe/out/` and
      `out-raw/` (regenerate with [`perception.md`](perception.md) §0 if lost). rsync them per §0 there.
- [ ] `.env` values:

| File                                                | Key                     | Value                                                                                                         |
| --------------------------------------------------- | ----------------------- | ------------------------------------------------------------------------------------------------------------- |
| `apps/bridge/.env` (robot)                          | `GEMINI_API_KEY`        | a Gemini API key (Robotics-ER 2 preview)                                                                      |
| `apps/bridge/.env` (robot) **and** `apps/back/.env` | `SAFETY_CONFIRM_SECRET` | same long random string in both — T2 (arm/hand) is refused without it, and PARAR cannot be cleared without it |
| `apps/back/.env`                                    | `AGENT_MAX_STEPS`       | `40`, or delete it — a leftover `12` keeps the old cap                                                        |
| `apps/back/.env`                                    | `AGENT_TURN_BUDGET_S`   | `240`                                                                                                         |

- [ ] Robot has internet (Gemini) **and** campus (TIC AI): `vlm.md` §1.
- [ ] Tests green on the Mac: 0 failed in each suite. The bridge's skips are all `test_listen.py` (BACKLOG 9.1).

## 1. Deploy (robot stopped, nobody on it)

As in `docs/OPERATIONS.md` §5:

```bash
# Mac:
git push ssh://unitree@192.168.123.164/home/unitree/c3po feat/autonomy:refs/heads/incoming
python3 scripts/robot_internet.py unitree@192.168.123.164   # keep it running
# Robot (ssh -t: install asks for the sudo password):
docker ps                                        # gemm's containers must not hold the D435
cd ~/c3po && git merge --ff-only incoming
./scripts/robot/c3po install && c3po restart     # bridge: all new tools, governor
export http_proxy=http://127.0.0.1:18899 https_proxy=http://127.0.0.1:18899 no_proxy=localhost,127.0.0.1,::1
c3po perception build vision                     # ~25 min — start this first
c3po perception build nav                        # + slam_toolbox layer; cut off by the battery on 2026-11-03, re-run
```

The builds need `robot_internet.py` up and the proxy exported. `~/.c3po/perception.env`
holds the YOLOE paths and the URDF camera mount; `c3po up` and `c3po perception build` read
it, so nothing needs sourcing.

Then `curl -s http://127.0.0.1:8001/safety/status | jq` — expect caps 0.3 / 0.3,
`t2_confirm true`, `confirm_configured true`, `stop_standing false` (details in `safety.md`).

## 7. Put together — the first open-ended task

Only after 2–6 passed. Robot in the mapped room, an object (wallet, remote, phone) on a
table it has not seen yet, gate armed by the operator (`arm_navigation`).

Say or type: **"Buscá mi billetera y andá hasta ella."** Expected tool chain (watch the
console / `tool_call_log`):

1. `recall_object("billetera")` → `not_found`
2. `look_around()` → memory fills; or `find_object("wallet")` via Gemini
3. `recall_object("wallet")` → x, y, frame
4. `navigate_to(x, y, frame=…)` → task id → `navigation_status` until `succeeded`
5. `verify("¿hay una billetera delante tuyo?")` → yes
6. spoken report

Record: success, wall time, number of LLM steps, any intervention. Try 3 phrasings and
2 objects. If step 1–3 work but 4 doesn't, that's Phase 3 work, not a planner bug.

## Rollback flags (restart the affected process after changing)

| Feature                                 | Flag → safe value                                                     | Where                                     |
| --------------------------------------- | --------------------------------------------------------------------- | ----------------------------------------- |
| Person slow/stop rule                   | `SAFETY_PERSON_RULE=off`                                              | bridge `.env`                             |
| Stale-perception policy                 | `SAFETY_STALE_POLICY=ignore`                                          | bridge `.env`                             |
| T2 confirmation gate                    | `SAFETY_T2_CONFIRM=off` (only with e-stop in hand)                    | bridge `.env`                             |
| Collision Monitor (if Nav2 won't start) | `C3PO_NAV_COLLISION_MONITOR=0`                                        | prefix on `c3po up nav2…`                 |
| Saved map / localization                | unset `C3PO_NAV_SLAM`                                                 | prefix on `c3po up nav2…`                 |
| Arm skills                              | `TELEOP_ARM_ENABLED=0` (hands: `TELEOP_HAND_ENABLED=0`)               | bridge `.env`                             |
| MPPI                                    | `C3PO_NAV_CONTROLLER=dwb` (default)                                   | prefix on `c3po up nav2…`                 |
| Operator face gate                      | `C3PO_FACE_GATE=` (empty)                                             | bridge `.env`                             |
| Table under each object (`support`)     | `C3PO_VISION_SUPPORT=0`                                               | `~/.c3po/perception.env`, then `c3po up`  |
| YOLOE → YOLO11n                         | `C3PO_ENGINE` / `C3PO_LABELS` back to the yolo11n plan + `labels.txt` | vision container env (`perception.md` §8) |

## Known limits — not bugs to file tomorrow

- Gemini response format verified on 2026-11-03 (`look`). `look` and `verify` now run in `back` on the school vision model.
- `find_object` has bearing but borrows range from a detector object within ±8°; null otherwise (the bridge gets no depth).
- Object memory is in FAST-LIO `odom`: correct across bridge restarts, **wrong after a nav-container restart** until the saved map (Phase 3) feeds a `map` frame.
- The LLM's world summary still lists at most 8 detector objects (`world_model.py MAX_OBJECTS`); memory holds up to 200.
- `ask_user` speaks the question but does not wait for the answer.
- MPPI is configured for vy 0.3 but the bridge clamps vy to ±0.20.
- Never `arm_navigation` while running `nav2-fake` — the fake never moves, Nav2 keeps commanding, and an open gate sends that to the real legs.
- Arms: `reach` verified within ±1 cm on 2026-11-03; `pick`/`place`/`handover` not verified. Neither hand answers, so the bridge runs `TELEOP_HAND_TYPE=simulated` (5.1). Learned skills (Phase 6) are not built.
- The vision container's camera extrinsic now defaults to the URDF mount (pitch 47.6° nose-down, `grounding.py` `DEFAULT_CAMERA_EXTRINSIC`). It still needs a tape-measure check on the robot. Every detector `range_m` depends on it: `manipulation.md` §6 / `perception.md` §4.
