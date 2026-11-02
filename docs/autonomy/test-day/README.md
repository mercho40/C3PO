# Test day — run order

One branch carries everything: **`feat/autonomy`** (= `integrate/october` + the five
autonomy workstreams). Deploy it once; the per-area checklists below each say "deploy
`auto/<x>`" — ignore that, they are all in `feat/autonomy`.

Order matters: **nothing new moves before the safety checks pass**, and nothing moves on
its own before it has moved on the gantry.

| #   | Area                                            | Checklist                                                                    | Moves the robot?      |
| --- | ----------------------------------------------- | ---------------------------------------------------------------------------- | --------------------- |
| 0   | Prep (tonight / before leaving)                 | this file §0                                                                 | no                    |
| 1   | Deploy + rebuild images                         | this file §1                                                                 | no                    |
| 2   | Safety governor                                 | [`safety.md`](safety.md)                                                     | gantry                |
| 3   | Vision tools (look / find_object / verify)      | [`vlm.md`](vlm.md) §1–5                                                      | no                    |
| 4   | YOLOE engine, speed, sanity                     | [`perception.md`](perception.md) §2–4                                        | no                    |
| 5   | Object memory + `look_around`                   | [`memory-eval.md`](memory-eval.md) §1–6                                      | turns in place        |
| 6   | Nav2 goals, map, landmark                       | [`nav.md`](nav.md) §2–7                                                      | gantry → 0.5 m → room |
| 7   | **Put together:** "buscá la billetera"          | this file §7                                                                 | yes                   |
| 8   | Measure: eval T0 subset, YOLOE test-set capture | [`memory-eval.md`](memory-eval.md) §7, [`perception.md`](perception.md) §5–7 | little                |

Someone holds the e-stop for every step from 2 on. Rollback flags are at the bottom.

## 0. Prep

- [ ] Branch on the remote so the robot can `git fetch` it (`git push -u origin feat/autonomy`).
- [ ] YOLOE ONNX files are already exported on Oliver's Mac in `~/c3po-models/yoloe/out/` and
      `out-raw/` (regenerate with [`perception.md`](perception.md) §0 if lost). rsync them per §0 there.
- [ ] `.env` values:

| File                                                | Key                     | Value                                                                 |
| --------------------------------------------------- | ----------------------- | --------------------------------------------------------------------- |
| `apps/bridge/.env` (robot)                          | `GEMINI_API_KEY`        | a Gemini API key (Robotics-ER 2 preview)                              |
| `apps/bridge/.env` (robot) **and** `apps/back/.env` | `SAFETY_CONFIRM_SECRET` | same long random string in both — T2 (arm/hand) is refused without it |
| `apps/back/.env`                                    | `AGENT_MAX_STEPS`       | `40`, or delete it — a leftover `12` keeps the old cap                |
| `apps/back/.env`                                    | `AGENT_TURN_BUDGET_S`   | `240`                                                                 |

- [ ] Robot has internet (Gemini) **and** campus (TIC AI): `vlm.md` §1.
- [ ] Tests green on the Mac: bridge 906 passed / 13 skipped, back 164, perception 314 (+6 with numpy).

## 1. Deploy (robot stopped, nobody on it)

```bash
ssh c3po
cd ~/c3po && git fetch && git checkout feat/autonomy && git pull
./scripts/robot/c3po install && c3po restart     # bridge: all new tools, governor
c3po perception build vision                     # ~25 min — start this first
c3po perception build nav                        # + slam_toolbox layer, a few min
```

Then `curl -s http://127.0.0.1:8001/safety/status | jq` — expect caps 0.3 / 0.3,
`t2_confirm true`, `confirm_configured true` (details in `safety.md`).

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
| MPPI                                    | `C3PO_NAV_CONTROLLER=dwb` (default)                                   | prefix on `c3po up nav2…`                 |
| YOLOE → YOLO11n                         | `C3PO_ENGINE` / `C3PO_LABELS` back to the yolo11n plan + `labels.txt` | vision container env (`perception.md` §8) |

## Known limits — not bugs to file tomorrow

- Gemini response format unverified (no key tonight); the client accepts all known shapes.
- `find_object` has bearing but borrows range from a detector object within ±8°; null otherwise (the bridge gets no depth).
- Object memory is in FAST-LIO `odom`: correct across bridge restarts, **wrong after a nav-container restart** until the saved map (Phase 3) feeds a `map` frame.
- The LLM's world summary still lists at most 8 detector objects (`world_model.py MAX_OBJECTS`); memory holds up to 200.
- `ask_user` speaks the question but does not wait for the answer.
- MPPI is configured for vy 0.3 but the bridge clamps vy to ±0.20.
- Never `arm_navigation` while running `nav2-fake` — the fake never moves, Nav2 keeps commanding, and an open gate sends that to the real legs.
- Arms/hands (Phases 5–6) are not part of this branch beyond the T2 gate.
