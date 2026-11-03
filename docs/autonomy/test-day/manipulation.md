# Test day: arms and hands (PLAN Phase 5): reach, pick, place, handover

Branch `feat/autonomy`. Code: `apps/bridge/src/bridge/kinematics.py` (IK over Unitree's URDF) and
`apps/bridge/src/bridge/skills/manipulation.py`. Tested offline only (`uv run pytest`: IK
round-trips, plan order, the per-tick person abort). **No arm has moved under this code yet.**

**Do this only after `safety.md` steps 8–11 passed** (T2 confirmations work, the reach block
works). Robot standing (FSM 4 or 500), on the gantry for §1–4, not walking. One person on the
remote; a second person watches the arm and calls the stop. Nothing fragile within 70 cm of the
shoulders.

## What changed, in one paragraph

The arms can now be sent to a 3-D point instead of joint angles. `reach` solves inverse
kinematics over the G1 URDF (rev 1.0, the variant xr_teleoperate uses on this robot). `pick`
takes a fresh detection's camera point, maps it to the torso through the URDF's camera mount,
plans open → 10 cm pre-grasp → grasp → close → 8 cm lift, solves every pose first, and only then
moves. `place` lowers, opens and backs away. `handover` holds the hand out at chest height and
is the only arm skill allowed with a person in reach. `arm_state` (T0) reports where each hand
is by forward kinematics and moves nothing. `set_hand` gained `shape=` (`power`, `pinch`,
`hook`, `point`, `open`). Every motion skill is T2 (one console confirmation per call), aborts
mid-motion if a person comes inside 0.8 m or the base starts walking, and uses the same
`rt/arm_sdk` driver as `move_arm` (2 s ramp, 0.6 rad/s slew).

## Before you start

`apps/bridge/.env` on the robot, then restart the bridge:

| Key                      | Value                                                     |
| ------------------------ | --------------------------------------------------------- |
| `TELEOP_ARM_ENABLED`     | `1`                                                       |
| `TELEOP_HAND_ENABLED`    | `1`                                                       |
| `TELEOP_HAND_TYPE`       | `brainco`                                                 |
| `TELEOP_HAND_SIDES`      | `right` (add `left` only once the left hand has answered) |
| `TELEOP_BRAINCO_OPEN_AT` | `0` or `1`, from `scripts/hand_probe.py`. Never guess it. |

`brainco_hand_server` must be running for the right hand (`--serial` per hand). Invoke tools
from the console agent, or directly through Claude Code over MCP (`vlm.md` §4). Each T2 call
needs `POST /skills/<tool>/confirm` first (`safety.md` step 9).

## 1. Is the URDF the robot? (no motion from us, 5 min)

The one thing that can make IK confidently wrong is a joint convention that differs from the
URDF. The URDF says **positive `right_shoulder_roll` moves the right arm toward the body**. The
2026-08-20 sign check recorded the opposite for the semantic map. Settle it passively:

| #   | Do                                                                                        | Expect                                                                                                                                                                                             |
| --- | ----------------------------------------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 1   | `arm_state` with the arms at rest.                                                        | `right.hand_m[1] < 0`, `left.hand_m[1] > 0`, and each `hand_m` roughly where you see that palm (torso frame: +x forward, +z up, origin near the waist). `driver.engaged false`.                    |
| 2   | Play the firmware `wave` (T1). While the right hand is up and out, call `arm_state` 2–3×. | `right.hand_m` z rises (> 0.2) and y goes **more negative** (outward), matching what you see. Measure the real hand with a tape from the chest centre: within ~5 cm of `hand_m`.                   |
| 3   | If y went positive while the real hand went out, or z disagrees by > 10 cm.               | **STOP. Do not run §2–6.** The motor convention is not the URDF's. Write down `q_deg` and what you saw; IK needs a sign table before it can be used. Everything else on this branch is unaffected. |

## 2. One reach, gantry (10 min)

| #   | Do                                                                                     | Expect                                                                                                                                                                                        |
| --- | -------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 4   | Confirm, then `reach` x 0.30, y -0.15, z 0.05, side right, fingers forward, hold true. | Slow (~34°/s) move after a 2 s ramp. The palm ends ~30 cm in front of the torso, 15 cm right, about elbow height, fingers forward. `result.target_torso_m` matches. Tape-check: within ~3 cm. |
| 5   | `reach` x 0.70 (too far).                                                              | `status failed`, `phase planning`, "out of reach", **nothing moves**.                                                                                                                         |
| 6   | `reach` y +0.30 with the right arm.                                                    | Refused "too far across", nothing moves.                                                                                                                                                      |
| 7   | Repeat 4, and halfway through have someone step to 0.6 m in front (perception up).     | Aborts: `phase aborted:person_in_reach`, the arm ramps back to the firmware controller.                                                                                                       |
| 8   | `release_arm_control`.                                                                 | Arm returns under the built-in controller over ~2 s.                                                                                                                                          |

## 3. Hand shapes (5 min, arm at rest)

| #   | Do                                                       | Expect                                                                                                                                                                                                   |
| --- | -------------------------------------------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 9   | `set_hand` side right, `shape power`, then `open_hands`. | All fingers curl round an imaginary bottle, thumb opposed. **If the thumb swings outward instead of across the palm**, `Thumb_aux` is inverted: fix channel 1 in `hands.SHAPES` (comment there says so). |
| 10  | `shape pinch`, `hook`, `point`, each followed by `open`. | Pinch: thumb meets index. Hook: thumb out, fingers curled. Point: index straight.                                                                                                                        |

## 4. Measure the tool centre (5 min)

`kinematics.GRASP_TCP_M = 0.10` is read off a drawing. With the arm held by `reach` from step 4,
measure from the wrist-yaw axis to the centre of the closed `power` grip. Put the number in
`kinematics.py`, restart the bridge. A 3 cm error here is a missed grasp.

## 5. First pick, off the gantry, standing (15 min)

Setup: an **opaque** plastic cup or a TV remote on a counter or high table, **0.80–0.95 m above
the floor**, 25–35 cm in front of the chest, a little to the right. The G1 arm is short (~0.48 m
to the palm): a 0.75 m coffee table is usually out of reach. Perception up, the object in the
head camera's view. Nobody inside 1 m.

| #   | Do                                                                                                                                                | Expect                                                                                                                         |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------ |
| 11  | `describe_surroundings`: the cup is listed. Then confirm + `pick` label "cup".                                                                    | Plan in `result.plan` (6 steps). Hand opens, arm goes to 10 cm short, moves in, closes, lifts 8 cm, holds. `hand_closed true`. |
| 12  | `verify("¿tenés un vaso en la mano?")`                                                                                                            | Yes. Record: success, the miss direction in cm if not (too high / short / left), and `target_torso_m`.                         |
| 13  | Confirm + `place` side right (no `onto`).                                                                                                         | Lowers 5 cm, opens, backs 8 cm, hands the arm back.                                                                            |
| 14  | Repeat 11–13 at 5 positions (the PLAN 5 exit grid). If several miss the same way, that is the camera mount (§6) or the TCP (§4), not the planner. | ≥ 3/5 today is a good first day. PLAN exit is ≥ 70 % over 20.                                                                  |

## 6. If picks miss consistently: the camera mount

`pick` maps the camera point through the URDF's D435 mount, which is tilted **47.6° nose-down**.
It does not use the vision container's `C3PO_CAMERA_*` extrinsic. That extrinsic defaults to
**pitch 0°**, which disagrees with the CAD by 47.6°. It affects every `range_m` the detector
reports (and so the person rule and object memory), not just arms. Check it in `perception.md` §4:
an object on the floor 1.0 m ahead (tape) should report `range_m ≈ 1.0`. If it does not, try
`C3PO_CAMERA_PITCH_DEG=47.6` on the vision container and measure again. Write down both readings.

## 7. Handover (5 min)

| #   | Do                                                                                          | Expect                                                                                                      |
| --- | ------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| 15  | Holding the cup (step 11), a person stands 0.6 m in front. Confirm + `handover` side right. | Runs even with the person in reach (the only arm skill that does). Hand at chest height, ~38 cm out, holds. |
| 16  | The person takes the cup, then `set_hand shape open`, then `release_arm_control`.           | Opens, arm returns. Nothing opens by itself: no touch sensing.                                              |

## Rollback

| Problem                         | Do                                                                                    |
| ------------------------------- | ------------------------------------------------------------------------------------- |
| Anything moves wrong            | Remote, or `stop_everything` (releases the arm, opens the hands).                     |
| Arms must not move today at all | `TELEOP_ARM_ENABLED=0`, restart the bridge. `pick` etc. then refuse with the reason.  |
| Hands only                      | `TELEOP_HAND_ENABLED=0`. `reach` still works; `pick` refuses ("hand not configured"). |

## Known limits (not bugs to file tomorrow)

- No touch or force sensing: success is `verify`, a camera judgment.
- No self-collision model: the workspace box (in front of the chest, ≤ 12 cm past the midline)
  stands in for it. The other arm is not checked.
- Grasp point = the detection's box centre at its median depth + 2 cm. Thin or tilted objects
  (a wallet lying flat) will often miss; `pinch` from above (`APPROACH_TILTS_DEG` 60°) is the
  attempt, not a promise.
- The person check runs per tick but on 4 Hz detector data with the unmeasured range above.
- Waist is assumed upright: camera and arms are both on `torso_link`, so waist yaw/pitch does not
  change the camera→hand transform, but a bent waist changes what is reachable.
- Left arm: same code, and the left hand has never answered. Leave `TELEOP_HAND_SIDES=right`.
- Not built: wrist cameras and hand-eye calibration, MuJoCo sim twin, learned skills (Phase 6).
