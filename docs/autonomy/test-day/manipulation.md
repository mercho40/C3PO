# Test day: arms and hands (PLAN Phase 5): reach, pick, place, handover

Branch `feat/autonomy`. Code: `apps/bridge/src/bridge/kinematics.py` (IK over Unitree's URDF) and
`apps/bridge/src/bridge/skills/manipulation.py`. Tested offline only (`uv run pytest`: IK
round-trips, plan order, the per-tick person abort). On the robot, 2026-11-03: the §1 URDF sign
check ✅ and the §2 step 4 `reach` ✅ within 1 cm. Hands not run: neither answers (BACKLOG 5.1).

**Do this only after `safety.md` steps 8–11 passed** (T2 confirmations work, the reach block
works). Robot standing (FSM 4 or 501; `arm_sdk.ALLOWED_FSM_IDS` is {4, 500, 501}, 500 is
not this chassis's walk program, and 802 is refused), on the gantry for §1–4, not walking. One person on the
remote; a second person watches the arm and calls the stop. Nothing fragile within 70 cm of the
shoulders.

## What changed, in one paragraph

The arms can now be sent to a 3-D point instead of joint angles. `reach` solves inverse
kinematics over the G1 URDF (rev 1.0, the variant xr_teleoperate uses on this robot). `pick`
takes a fresh detection's camera point, maps it to the torso through the URDF's camera mount,
plans curl the hand (`power`, 0.8 s) → clear the body and go over the object → 10 cm pre-grasp →
open (0.8 s) → grasp → close → 8 cm lift, solves every pose first, and only then moves (1439ace).
`place` lowers, opens and backs away until the palm is behind the table edge. `handover` holds the hand out at chest height and
is the only arm skill allowed with a person in reach. `arm_state` (T0) reports where each hand
is by forward kinematics and moves nothing. `set_hand` gained `shape=` (`power`, `pinch`,
`hook`, `point`, `open`). Every motion skill is T2 (one console confirmation per call), aborts
mid-motion if a person comes inside 0.8 m or the base starts walking, and uses the same
`rt/arm_sdk` driver as `move_arm` (2 s ramp, 0.6 rad/s slew). The waist is held while arm_sdk is
on (9cdc3b3). `pick` and `place` move in straight ~1 cm steps inside the URDF limits (e002c0a).
Stale perception aborts the motion (f1c95d7). Release freezes the arm before the 2 s hand-back
(baf1894).

## Before you start

`apps/bridge/.env` on the robot, then restart the bridge:

| Key                      | Value                                                     |
| ------------------------ | --------------------------------------------------------- |
| `TELEOP_ARM_ENABLED`     | `1`                                                       |
| `TELEOP_HAND_ENABLED`    | `1`                                                       |
| `TELEOP_HAND_TYPE`       | `simulated` until a hand answers (5.1), then `brainco`    |
| `TELEOP_HAND_SIDES`      | `right` (add `left` only once the left hand has answered) |
| `TELEOP_BRAINCO_OPEN_AT` | `0` or `1`, from `scripts/hand_probe.py`. Never guess it. |

Neither BrainCo hand answers on any of the four FT4232 lines, even through the vendor
`brainco_hand_server` (2026-11-06). With `simulated`, no finger moves and the result says nothing
was gripped. Invoke tools from the console agent, or directly through Claude Code over MCP
(`vlm.md` §4). Each T2 call needs one «Confirmar» press on the tool card or in the VR menu
(`safety.md` step 9).

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
| 7   | Repeat 4, and halfway through have someone step to 0.6 m in front (perception up).     | Aborts: `phase aborted:person_in_reach`, the arm ramps back to the firmware controller. Watch the 2 s hand-back for a sweep (11.4).                                                           |
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

> ⚠️ **`pick` refuses on the robot until `C3PO_ALLOW_PICK=1` is set in apps/bridge/.env.**
> Set it only for this section, on the gantry, after the touch check below. I1 (dba2a35)
> checks every arm link as a capsule against the torso, and the MuJoCo twin now finds no
> contact in any of the six planned picks, but the real arm has not been tried. Watch the
> new shoulder-out tuck (the elbow juts out to the side) and keep the desk edge at least
> 0.24 m in front of the torso (with perception reporting the table, §8 covers it). Remove
> the flag again when the section is done.

Setup: an **opaque** plastic cup or a TV remote on a counter or high table, **0.80–0.95 m above
the floor**, 25–35 cm in front of the chest, a little to the right. The G1 arm is short (~0.48 m
to the palm): a 0.75 m coffee table is usually out of reach. Perception up, the object in the
head camera's view. Nobody inside 1 m.

| #   | Do                                                                                                                                                | Expect                                                                                                                                                          |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 11  | `describe_surroundings`: the cup is listed. Then confirm + `pick` label "cup".                                                                    | Plan in `result.plan` (11 entries, `curl` to `lift`). Hand curls, arm goes to 10 cm short, hand opens, moves in, closes, lifts 8 cm, holds. `hand_closed true`. |
| 12  | `verify("¿tenés un vaso en la mano?")`                                                                                                            | Yes. Record: success, the miss direction in cm if not (too high / short / left), and `target_torso_m`.                                                          |
| 13  | Confirm + `place` side right (no `onto`).                                                                                                         | Lowers 8 cm, opens, rises, draws back toward the body, hands the arm back.                                                                                      |
| 14  | Repeat 11–13 at 5 positions (the PLAN 5 exit grid). If several miss the same way, that is the camera mount (§6) or the TCP (§4), not the planner. | ≥ 3/5 today is a good first day. PLAN exit is ≥ 70 % over 20.                                                                                                   |

## 6. If picks miss consistently: the camera mount

`pick` maps the camera point through the URDF's D435 mount, which is tilted **47.6° nose-down**.
It does not use the vision container's `C3PO_CAMERA_*` extrinsic. That extrinsic now defaults to
the same CAD mount (seen from the pelvis), and a test pins the two together, so a consistent miss
means the CAD disagrees with the robot. It affects every `range_m` the detector reports (and so
the person rule and object memory), not just arms. Verify it in `perception.md` §4: an object on
the floor 1.0 m ahead (tape) should report `range_m ≈ 1.0`. If it does not, measure the mount,
put it in `~/.c3po/perception.env` (`C3PO_CAMERA_*`) and measure again. Write down both readings.
That override moves the detector's ranges only; `pick` keeps the URDF mount.

## 7. Handover (5 min)

| #   | Do                                                                                          | Expect                                                                                                      |
| --- | ------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------- |
| 15  | Holding the cup (step 11), a person stands 0.6 m in front. Confirm + `handover` side right. | Runs even with the person in reach (the only arm skill that does). Hand at chest height, ~38 cm out, holds. |
| 16  | The person takes the cup, then `set_hand shape open`, then `release_arm_control`.           | Opens, arm returns. Nothing opens by itself: no touch sensing.                                              |

## 8. Picking from a reported table (pending robot test, dbf876c)

Only on a vision image rebuilt from this branch, after the support check in `perception.md`
§4. The planner now keeps every arm link 1 cm clear of the table perception reports
under the object, and a refused plan says what to change (`docs/OPERATIONS.md` §4, "When a
pick or place refuses"). With the face gate on (`safety.md` 25), each pick also needs the
operator's face. Same setup as §5: gantry, `C3PO_ALLOW_PICK=1` for this section only,
nobody within 1 m, the robot square to the table.

From the open at pre-grasp on, the table check models the open hand: `FINGER_M` (0.09 m
from the tool point to the fingertips) and `HAND_OPEN_RADIUS_M` (0.04 m) in
`skills/manipulation.py` (e346c31). Both are guesses. With them most tabletop picks refuse
with `table_clearance`: in the simulator only 1 in 20 random table scenes plan (3 in 200),
and those objects sit at least 10 cm above the top. So do row 22 first and set both from
it; until then expect row 17 to refuse. A pick that starts with something already in the
hand drops it when the hand opens at pre-grasp: start each pick with the hand empty.

| #   | Do                                                                                                                                                                                                                 | Expect                                                                                                                                                                                                                                                                                                                                                                                                                   |
| --- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| 17  | A cup on the table as in §5. Read its `support` (`perception.md` §4). Confirm + `pick` label "cup".                                                                                                                | `result.table_torso_m` is `[edge, top]` in the torso frame: `top` ≈ `support.z_m` − 0.044, and `edge` ≈ the taped edge distance (never further than the cup). The hand curls, rises beside the body, passes 8 cm over the cup's centre height, comes down 10 cm short of it, opens, moves in, closes and lifts 8 cm. Nothing touches the table. If the hand gets within about 3 cm of the table before the grasp: PARAR. |
| 18  | While the arm holds after the lift, tape the gap between the bottom of the hand and the table top.                                                                                                                 | Gap ≈ `target_torso_m[2]` + 0.08 − `table_torso_m[1]` − 0.03 (the hand's half-thickness), within about 2 cm. A smaller real gap means the reported top is too high or the arm sags: write both numbers down and stop picking.                                                                                                                                                                                            |
| 19  | A phone lying flat where the cup stood in row 17. Confirm + `pick` label "mobile phone".                                                                                                                           | Nothing moves. `status failed`, `phase planning`, `result.reason` `table_clearance`, `result.advice.problem` `table_clearance`, and the console shows «La mano tocaría la mesa: el objeto es muy bajo para agarrarlo. Caminar no lo arregla.» (another part may be named: «La muñeca …»).                                                                                                                                |
| 20  | Move the cup back on the table, out of reach (if the pick plans, it runs as in row 17: move the cup further). Confirm + `pick`. Then walk as advised, look again and pick again.                                   | Refused with `result.advice.step_forward_m` and a note like «Acercate ~0.08 m, después mirá de nuevo.». The advice keeps the table edge at least 0.25 m from the torso: tape it after the walk, and check the hips stay clear. An object out of reach even with the robot at the edge gives `behind_table_edge` («El objeto está … detrás del borde de la mesa …»).                                                      |
| 21  | Listen to the agent pass on the refusals of rows 19 and 20 (voice, or the headset with `HEADSET_SPEAK_REPLY=1`).                                                                                                   | The console card and the spoken reply read as plain Spanish: what to do, or why walking will not help. Write down any hint that reads or sounds badly (the `~` and the decimal point in «~0.08 m» are the likely ones).                                                                                                                                                                                                  |
| 22  | Before row 17, hand configured, arm held by `reach`. `set_hand shape open`: measure from the tool point (palm centre) to the fingertips, and the palm's half-width. Then `set_hand shape power`: measure the fist. | Write the numbers down and put them in `skills/manipulation.py`: `FINGER_M`, `HAND_OPEN_RADIUS_M`, and the `"hand"` radius in `ARM_CAPSULES` (0.031 m today) if the fist is thicker. In `power` the thumb curls across the palm and nothing sticks out past the fingers. Restart the bridge.                                                                                                                             |
| 23  | `set_hand shape power`, then `set_hand shape open`, with a stopwatch or a video.                                                                                                                                   | The hand is fully open within 0.8 s (`CLOSE_SETTLE_S`, the wait before the arm moves in). If not, write down the real time.                                                                                                                                                                                                                                                                                              |
| 24  | Holding the cup, the table edge taped at 0.20–0.25 m from the torso. Confirm + `place` side right `onto` "dining table" (or the label perception gives it).                                                        | The hand comes down, opens, rises, and draws back until the palm is behind the edge before the arm is released. The fingers may overhang the edge; nothing lowers onto the top. With the edge nearer than about 0.2 m: refused with `table_clearance` and nothing moves.                                                                                                                                                 |
| 25  | `C3PO_CAMERA_PITCH_DEG` a few degrees off its default in `~/.c3po/perception.env`, `c3po up`, then row 17's pick again. Put the default back after.                                                                | `result.table_torso_m` and `result.target_torso_m` match the run without the override within about 1 cm: the bridge maps table and object through the same URDF mount. A table that moves while the cup stays put means `cam_top_xyz` / `cam_near_xyz` are missing (an old vision image).                                                                                                                                |
| 26  | After rows 22–23 changed a number: rows 17–18 again, and the touch check of §5.                                                                                                                                    | As before: nothing touches the table or the torso.                                                                                                                                                                                                                                                                                                                                                                       |

## Rollback

| Problem                         | Do                                                                                                                      |
| ------------------------------- | ----------------------------------------------------------------------------------------------------------------------- |
| Anything moves wrong            | Remote, or `stop_everything` (releases the arm, opens the hands). It also latches: «Reanudar» before the next arm call. |
| Arms must not move today at all | `TELEOP_ARM_ENABLED=0`, restart the bridge. `pick` etc. then refuse with the reason.                                    |
| Hands only                      | `TELEOP_HAND_ENABLED=0`. `reach` still works; `pick` refuses ("hand not configured").                                   |

## Known limits (not bugs to file tomorrow)

- No touch or force sensing: success is `verify`, a camera judgment.
- Collision is checked arm-against-torso and arm-against-the-reported-table only (capsules,
  `ARM_CAPSULES`). The other arm is not checked. The table's front edge is taken as square to
  the robot through one point, so an edge seen at an angle is nearer on one side.
- Until row 22 sets `FINGER_M` and `HAND_OPEN_RADIUS_M`, most tabletop picks refuse with
  `table_clearance` (in the simulator, planned objects sit at least 10 cm above the top). A
  phone or a wallet lying flat always refuses.
- A pick that starts with something already in the hand drops it: the hand opens at
  pre-grasp.
- Grasp point = the detection's box centre at its median depth + 2 cm. Thin or tilted objects
  (a wallet lying flat) will often miss; `pinch` from above (`APPROACH_TILTS_DEG` 60°) is the
  attempt, not a promise.
- The person check runs per tick but on 4 Hz detector data with the unmeasured range above.
- Waist is assumed upright: camera and arms are both on `torso_link`, so waist yaw/pitch does not
  change the camera→hand transform, but a bent waist changes what is reachable.
- Left arm: same code, and the left hand has never answered. Leave `TELEOP_HAND_SIDES=right`.
- Not built: wrist cameras and hand-eye calibration, learned skills (Phase 6). The MuJoCo twin
  runs in CI (`tests/sim`), with 20 random table scenes per run.
