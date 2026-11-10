# Test day: safety governor (PLAN 0.2)

Branch `feat/autonomy`. Code: `apps/bridge/src/bridge/safety.py`. Verified on the robot
on 2026-11-03: the PARAR latch, «Reanudar» and the console «Confirmar» button. The rest
was tested offline only (`uv run pytest`).

**Robot on the gantry for steps 1–6.** One person holds the remote the whole time
(L2+B damps; it outranks all of this).

## What changed, in one paragraph

Every real `SET_VELOCITY` now goes through one governor in
`_locomotion.send_velocity`: speed and yaw caps (0.3 m/s, 0.3 rad/s, the same
numbers as before; no setting can push them past 0.5 m/s or 0.8 rad/s), an
acceleration cap (1.0 m/s², 1.0 rad/s², braking is never limited), and the
person rule (a `person` closer than 1.5 m means ×0.4, closer than 0.5 m means
stop). Nav2 with no fresh detector data is slowed to ×0.4; manual driving is
not. `walk_velocity` now re-sends short 1 s setpoints at 20 Hz instead of one
setpoint lasting up to 3 s. Every `gesture` tool with danger ≠ low is T2
(`skill_meta.derive_tier`): `move_arm`, `set_hand`, `reach`, `pick`, `place`,
`handover`. They are refused unless an operator confirms each call. A new
`soft_stop` tool stops the base and never damps. A PARAR latches: until
«Reanudar», every non-T0 tool except `damp` is refused (`docs/OPERATIONS.md` §4).

## Before you start

1. Pick a long random string and put the same value in **both** files:
   `apps/bridge/.env` (on the robot) and `apps/back/.env`:
   `SAFETY_CONFIRM_SECRET=<value>`.
2. Deploy the branch and restart the bridge. The bridge reads all `SAFETY_*`
   values only when it starts, so restart it after any change.
3. `curl -s http://127.0.0.1:8001/safety/status | jq` (through the tunnel).
   **Expect:** `caps.linear_mps 0.3`, `caps.yaw_rps 0.3`, `t2_confirm true`,
   `confirm_configured true`, `stale_policy "slow"`, `person_rule true`,
   `stop_standing false`.
   Keep this command handy: every step below reads it.

## Checklist (in order)

| #   | Do                                                                                                                                                                                                                                                                                                                     | Expect                                                                                                                                                                                                                                                                                                                                  |
| --- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `c3po up core` (no perception). Console walk pad: forward 0.2 m/s, then turn.                                                                                                                                                                                                                                          | Walks and turns as on 2026-10-15. `/safety/status` `last_command` ≈ `[0.2, 0, 0]`, `last_reason null`, `perception_fresh false`.                                                                                                                                                                                                        |
| 2   | Headset walk (`c3po up teleop`), stick fully forward from standing.                                                                                                                                                                                                                                                    | Same feel as before, with up to 0.3 s of ramp to full speed. Stopping is instant.                                                                                                                                                                                                                                                       |
| 3   | `walk_velocity` vx 0.2 for 3 s from the console. Halfway through: `sudo systemctl kill -s KILL c3po-bridge`. Open (BACKLOG §2).                                                                                                                                                                                        | The robot stops **within about 1 s** of the kill. Before this branch it could walk on for up to 3 s. Restart the bridge.                                                                                                                                                                                                                |
| 4   | Start a 3 s `walk_velocity`, then invoke `soft_stop` (console or agent). Open (BACKLOG §2).                                                                                                                                                                                                                            | Stops at once and **stays standing, never limp**. The result has `heard: true`. Then check PARAR (`stop_everything`) still does what it did before: it stops, and damps only if the burst was not acknowledged. Then the topbar shows «PARAR ACTIVO», a `walk_velocity` is refused (_"PARAR … is standing"_), and «Reanudar» clears it. |
| 5   | Bring perception up (`c3po up`, the default profile). Wait for `perception_fresh: true`. One person walks slowly toward the front of the robot while the walk pad holds 0.2 m/s forward. Only after the camera pitch check (`return-checklist.md` §1).                                                                 | Over 1.5 m: full speed. 0.5–1.5 m: `last_reason "person_near"`, `last_command` ≈ 0.08 (0.2 × 0.4). Under 0.5 m: `"person_within_stop_distance"`, base stops. `nearest_person_m` follows the person. **Write down the range the bridge reports against a tape-measured range.** The detector range has never been measured.              |
| 6   | Stop the vision/perception containers while the walk pad is in use.                                                                                                                                                                                                                                                    | `perception_fresh` goes `false` within about 1 s. The walk pad goes back to **full speed**, because manual driving is not slowed when data is stale.                                                                                                                                                                                    |
| 7   | **Only if Stage 8 (Nav2 driving) is on today's plan.** Arm navigation with the detector down.                                                                                                                                                                                                                          | `last_reason "perception_unknown_slow"`. The robot moves at ≤ 0.12 m/s even when Nav2 asks for more. `/telemetry/gate` `last_sent` shows what Nav2 asked for; `/safety/status` `last_command` shows what reached the legs.                                                                                                              |
| 8   | T2: with `TELEOP_HAND_ENABLED=1`, ask the agent (or invoke directly) to `set_hand` closure 0.2.                                                                                                                                                                                                                        | Refused: _"needs an operator's confirmation first"_. Nothing moves.                                                                                                                                                                                                                                                                     |
| 9   | Press «Confirmar set_hand» on the tool card (✅ 2026-11-03). Fallback, straight to the bridge: `curl -X POST -H "x-c3po-confirm-secret: $S" -d '{"tool":"set_hand","args":{"side":"right","closure":0.2}}' http://127.0.0.1:8001/safety/confirm`, then invoke with the same arguments within 30 s, then a second time. | One press runs the refused call once, with the arguments shown. With curl: `granted: true`, the first call runs and the second is refused again (each grant covers one call). Calling `/safety/confirm` for `walk_to` returns 400 (only T2 and high-danger T1 take a grant).                                                            |
| 10  | With perception fresh, one person stands about 0.6 m in front. Confirm `set_hand` and invoke it.                                                                                                                                                                                                                       | Refused: _"inside arm's reach"_. Step back to over 0.8 m and invoke again. It now runs on the same grant.                                                                                                                                                                                                                               |
| 11  | Voice session.                                                                                                                                                                                                                                                                                                         | `damp` and `zero_torque` are never offered. Voice drops high-danger T1 (`arm_navigation`, `start_walking_waist`). T2 **is** offered in voice and refused until «Confirmar» (`runtime.ts` `offeredInMode`).                                                                                                                              |
| 12  | Pending robot test. While the robot says something long in Spanish (`say`, or a headset reply with `HEADSET_SPEAK_REPLY=1`), press PARAR.                                                                                                                                                                              | Speech stops within about a second, nothing more of the sentence follows, and `stop_everything` returns `speech_stop_rpc_code: 0`.                                                                                                                                                                                                      |
| 13  | Pending robot test. Say «emergencia», then «pará, pará», then a flat «detener», each from 1 m and from 3 m. Then talk normally near the robot for a minute.                                                                                                                                                            | The first two stop the robot every time. Record how often «detener» does (7 of 15 on synthesised speech). No stop from normal talk.                                                                                                                                                                                                     |
| 14  | Pending robot test, only with Nav2 on today's plan. Arm navigation, give a goal 4 m away (`SAFETY_GEOFENCE_M` default 3 m).                                                                                                                                                                                            | Nav2's velocity goes to zero at about 3 m with `reason=outside_geofence` in the bridge log; `/safety/status` shows `geofence.inside: false`. Walking back by hand still works.                                                                                                                                                          |
| 15  | Pending robot test. Arm navigation with the lidar faulted.                                                                                                                                                                                                                                                             | `arm_navigation` returns `obstacles_fresh: false`, and every Nav2 setpoint is zeroed with `reason=obstacle_source_stale`.                                                                                                                                                                                                               |
| 16  | Pending robot test. Robot standing in 501, gantry, nobody within 1 m. In /chat: _"hacé el gesto wave_above_head y mantenelo 10 segundos"_ (the tool card must show `gesture` with `hold_s` 10). While the pose is held, press PARAR.                                                                                   | The chat turn ends. The arm comes down within a few seconds, without a second command. `c3po logs` on the robot shows `gesture.cancelled`, then `gesture.cancel_released` with `rpc_code=0`. If the arm stays up: «Reanudar», then `gesture` with `release_arm`, and report it.                                                         |

## Rollback (env in `apps/bridge/.env`, then restart the bridge)

| Symptom                                        | Flag                                                                                                        |
| ---------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| Ramps feel laggy (teleop, walk pad)            | `SAFETY_MAX_ACCEL_MPS2=10` `SAFETY_MAX_YAW_ACCEL_RPS2=10`                                                   |
| Person rule fires on nothing / blocks work     | `SAFETY_PERSON_RULE=off` (turns off slow, stop and the T2 reach block)                                      |
| Nav2 too slow on the gantry with no detector   | `SAFETY_STALE_POLICY=ignore`, gantry only. `stop` is the strict option.                                     |
| T2 confirmation in the way                     | `SAFETY_T2_CONFIRM=off` (the reach block still applies)                                                     |
| `walk_velocity` stutters (a slow ack at 20 Hz) | Look for `locomotion.set_velocity.rpc_error` in the bridge log. To undo: revert the `walk_velocity` commit. |
| Anything else                                  | Roll back to `c8a17fd` (this branch's base) as in `docs/OPERATIONS.md` §5 ("Rollback").                     |

The caps can be set lower without risk: `SAFETY_MAX_LINEAR_MPS`, `SAFETY_MAX_YAW_RPS`.
Raise them only after measuring.

## Known limits (not bugs to file tomorrow)

- The person rule sees only what the head camera sees (69° forward). People
  beside or behind the robot do not count.
- The T2 reach check runs when the tool is called. It does not run while
  `move_arm` holds a pose.
- The headset stream runs in its own process, where no detections exist. It
  gets the caps and the ramp but never the person rule.
- A spoken «confirmo» works only from the headset's push-to-talk, decided by `back` with
  exactly one pending call on screen. The robot-mic paths are button-only.
- A grant covers one call with exactly the arguments shown, for 30 s. A PARAR drops every
  pending grant.
- The link watchdog is still off on purpose (`watchdog.py` has the 2026-11-02 note).
