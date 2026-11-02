# Test day: object memory and the eval suite

Branch `auto/memory`. Checks are in order: each one assumes the ones before it
passed. Write the result next to each step (pass / fail + what you saw).

Ways to call a tool: Claude Code with the `c3po-bridge` MCP server
(`.mcp.json`), or the console's skill panel (admin). To watch memory without
calling anything:

```sh
curl -s http://127.0.0.1:8001/telemetry/surroundings | jq .memory   # through scripts/robot_tunnel.sh
ssh c3po 'cat ~/.c3po/objects.json'
```

## 0. Before the robot (tonight, on the Mac)

| #   | Do                                                 | Expect                                                                                  |
| --- | -------------------------------------------------- | --------------------------------------------------------------------------------------- |
| 0.1 | `cd apps/bridge && uv run pytest -q`               | 841 passed, 13 skipped                                                                  |
| 0.2 | `cd apps/back && bun test`                         | 155 pass                                                                                |
| 0.3 | `bun apps/back/scripts/eval-agent.ts --dry-run`    | 96/96, every row 3/3. Anything else means `tasks.yaml` or the scorer broke              |
| 0.4 | On campus: stub bridge + `eval-agent.ts --tier T0` | Runs 54 trials, writes `runs.jsonl` + `summary.md`. The score is the planner's baseline |

## 1. Deploy and bring-up

| #   | Do                                                                                | Expect                                                                                          |
| --- | --------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------- |
| 1.1 | Deploy `auto/memory` (`git pull && ./scripts/robot/c3po install && c3po restart`) | Bridge answers; `list_objects` → `{"count": 0, ...}` (or what an earlier run left)              |
| 1.2 | `c3po up perception` (Livox + RealSense)                                          | `describe_surroundings` → `sources.detector: ok` and a `pose`. Without both, memory stays empty |
| 1.3 | `forget_object("all")`                                                            | Clean slate for the checks below                                                                |

## 2. Walk past objects

Put a **cup**, a **bottle** and a **chair** around the room, 1–3 m from the
path. Walk the robot past them with the console pad (slowly, ≤ 0.2 m/s), facing
each one for a few seconds.

| #   | Do                                     | Expect                                                                                                         |
| --- | -------------------------------------- | -------------------------------------------------------------------------------------------------------------- |
| 2.1 | `list_objects`                         | One entry per object (not one per frame). `seen_count` grows while it is in view                               |
| 2.2 | Measure: robot to the cup with a tape  | `distance_m` within ±0.3 m, `bearing_deg` sign right (left = positive). A wrong sign is a bug: stop and report |
| 2.3 | Turn the robot fast by hand on the pad | No new duplicate entries appear during the turn (frames above 30°/s are skipped)                               |
| 2.4 | `describe_surroundings`                | `memory.objects` lists what was seen at least twice, with distance/bearing from here                           |

## 3. recall_object

| #   | Do                                                       | Expect                                                          |
| --- | -------------------------------------------------------- | --------------------------------------------------------------- |
| 3.1 | `recall_object("taza")`, then `("botella")`, `("silla")` | `status: ok`, the right entry, label `cup` / `bottle` / `chair` |
| 3.2 | Turn so the cup is behind; `recall_object("cup")`        | `bearing_deg` near ±180, `distance_m` unchanged                 |
| 3.3 | `recall_object("billetera")` (none seen)                 | `not_found` with `known_labels: [bottle, chair, cup]`           |

## 4. look_around

Robot standing in open space, nobody within 1 m, operator holding the remote.

| #   | Do                                                     | Expect                                                                                         |
| --- | ------------------------------------------------------ | ---------------------------------------------------------------------------------------------- |
| 4.1 | Put an object behind the robot; `look_around()`        | Six 60° turns with a 2 s pause each, ~45 s. `new_objects` has the object behind                |
| 4.2 | Repeat; press PARAR during the second turn             | Robot stops; result `status: stopped`, `stopped_reason: stop_everything` (or `turn_cancelled`) |
| 4.3 | `c3po perception stop`, wait 3 s, then `look_around()` | `error: perception_offline`, and the robot does **not** move                                   |

## 5. Forgetting

| #   | Do                                                      | Expect                                                                            |
| --- | ------------------------------------------------------- | --------------------------------------------------------------------------------- |
| 5.1 | Take the bottle away; face its spot (1–3 m) for ~3 s    | Bottle gone from `list_objects`                                                   |
| 5.2 | Take the cup away while the robot faces elsewhere       | Cup stays in memory (not looked at = not forgotten)                               |
| 5.3 | Stand a person between the robot and the chair for ~3 s | Chair stays (hidden is not absent)                                                |
| 5.4 | Put a **glass** in view, then look at it for 10 s       | Note it: if depth fails on glass the detector drops it and it never enters memory |

## 6. Restart persistence

| #   | Do                                                           | Expect                                                                                                                    |
| --- | ------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------- |
| 6.1 | `c3po restart` (bridge only)                                 | `list_objects` has the same entries, same ids                                                                             |
| 6.2 | `c3po perception stop`, then `c3po perception up perception` | Entries still listed, but positions are now in a re-origined `odom`. Check one: likely wrong. Then `forget_object("all")` |

## 7. Eval suite, T0 subset

Tunnel up, `BRIDGE_URL` at the robot, on the campus network (TIC AI).

```sh
bun apps/back/scripts/eval-agent.ts --tier T0
```

Set each task's `start_state` before its three trials (the runner does not
wait, so run one task at a time with `--ids` when a setup is needed:
`t0-hay-taza`, `t0-donde-billetera`, `t0-cartel`). Expected: `summary.md`
with SR per tier and a Wilson interval. The Phase 1 exit asks ≥ 80 % on T0.
Record the run directory in the session notes.

## Unverified until the robot

- The camera extrinsic is UNMEASURED (`grounding.py`), so memory positions
  carry whatever error it has. 2.2 measures it end to end.
- FAST-LIO `odom` vs vendor odometry: memory uses the first; `walk_to` the
  second. The tools give distance/bearing for that reason.
- Forgetting thresholds (8 frames, ±30°, 0.5–3 m) are reasoned, not measured.
- Whether `tic-chat` calls `recall_object` for "¿dónde dejé…?" without a
  prompt hint is exactly what step 7 measures.
