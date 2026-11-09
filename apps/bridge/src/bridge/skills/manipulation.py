"""reach / pick / place / handover: classical manipulation, no learned policy (PLAN Phase 5).

The HomeBody/HERO pattern, sized to what this robot has today:

    fresh detection (cam_xyz) --URDF camera mount--> torso_link point
        --> approach direction --> pre-grasp, grasp, lift waypoints
        --> IK for every waypoint BEFORE anything moves (kinematics.solve)
        --> open hand, slew, slew, close shape, slew --> hold

Everything protective is inherited, not re-implemented: the rt/arm_sdk driver (gapless engage
from the measured pose, 2 s weight ramp, 0.6 rad/s slew, stale-lowstate and FSM gates, the
TELEOP_ARM_ENABLED + SIM_MODE=real double switch), `arm_pose.slew`, and the T2 gate in
`safety.tool_refusal` (operator confirmation, person in reach) that runs before the tool does.
Added here, because these skills move the arm without a human choosing each joint:

* every waypoint must solve IK (within 1 cm, fingers within 10°) AND sit inside WORKSPACE,
  or nothing moves at all;
* a per-tick abort: a person inside arm's reach (except `handover`), the base walking, or the
  headset driving (the teleop lease; that refuses the start too);
* grasp targets come only from a detection younger than FRESH_DETECTION_S, never from object
  memory: memory is where the wallet *was*, and a grasp needs where it *is*.

Not here (say so instead of pretending): wrist-camera refinement, force sensing (the BrainCo
reports no reliable contact), self-collision beyond the box, regrasp on a miss. The caller
checks success with `verify("¿tenés <x> en la mano?")`.

SIM_MODE=stub returns the full plan (waypoints, IK errors) without moving, so the planner and
the eval suite can exercise pick/place on a laptop.
"""

from __future__ import annotations

import asyncio
import math
import os
import time
from typing import Any, Literal

import structlog

from bridge import estop, kinematics, safety
from bridge.skills import arm_pose
from bridge.skills.task_runtime import get_registry
from bridge.teleop import arm_sdk, hands
from bridge.teleop.retarget import ArmAngles

log = structlog.get_logger(__name__)

SIM_MODE = os.environ.get("SIM_MODE", "stub")

Side = Literal["left", "right"]

#: Where the tool centre may go, torso_link metres. In front of the chest, no lower than a
#: table at standing height, never behind the shoulder plane. `y` is per side: own side out to
#: 0.45 m, at most 0.12 m across the midline (the other arm and the torso live there).
WORKSPACE_X = (0.12, 0.55)
WORKSPACE_Z = (-0.30, 0.40)
WORKSPACE_Y_OWN = 0.45
WORKSPACE_Y_CROSS = 0.12

FRESH_DETECTION_S = 1.0
PREGRASP_BACK_M = 0.10
LIFT_M = 0.08
#: Depth reads the object's FRONT surface; the grasp closes around its middle.
#: shortcut: one constant for every object; use the mask's depth spread once wrist cameras exist.
GRASP_DEPTH_OFFSET_M = 0.02
#: How long a closing hand gets before the arm lifts.
CLOSE_SETTLE_S = 0.8
#: Chest height, a forearm in front: where a person takes something from the hand.
HANDOVER_POINT = (0.38, 0.12, 0.18)  # y mirrored for the right arm
#: Fingers horizontal first, then tipped down: side grasp of an upright object, then from above.
APPROACH_TILTS_DEG = (0.0, 30.0, 60.0)
#: Approach, lift, lower and retreat are straight Cartesian lines, cut into sub-waypoints this
#: far apart. arm_sdk slews each joint on its own, so ONE joint-space move from pre-grasp to
#: grasp swept the hand sideways (audit §4: median 5 cm, max 24 cm). Over 1 cm it cannot.
LINE_STEP_M = 0.01
#: Largest joint change allowed between two 1 cm sub-waypoints. A smooth line moves a joint a
#: few hundredths of a radian per cm; an IK branch flip (elbow up <-> down) moves it ~1 rad.
LINE_MAX_DQ_RAD = 0.25
#: Largest joint change between two COMMANDED poses on a line. Near a straight elbow 1 cm of
#: hand travel takes ~0.2 rad of elbow against opposite shoulder motion; slewed joint by joint
#: that swings the hand ~3 cm off the line. Joint-interpolating in 0.03 rad steps keeps the
#: joints moving together.
LINE_JOINT_STEP_RAD = 0.03
#: The hand stays this far above the support surface whenever it is in front of the body.
#: One joint-space slew from the arm hanging to pre-grasp swept the hand forward through desk
#: height into the desk edge (robot, 2026-11-05).
SURFACE_MARGIN_M = 0.08
#: The hand rises and lowers no further forward than this (torso_link x): elbow flexed,
#: shoulder back. Everything in front of it is treated as possibly over the desk, so the desk
#: edge must be no closer than this (on 2026-11-05 the robot stood 0.3-0.4 m from it).
#: shortcut: a constant desk-edge proxy; use a fitted support plane once perception reports one.
TUCK_X_M = (0.16, 0.20, 0.24)  # closest first; further out only where the closer is unreachable
#: How far the shoulder pitches back before the elbow bends: the elbow sits behind the chest,
#: so the forearm passes horizontal no further forward than the first tuck point.
SHOULDER_BACK_RAD = 0.7
#: Where the arm reaches comfortably, in front of its own shoulder (torso_link). Used to tell
#: the model how to walk when a target is out of reach.
COMFORT_X_M = 0.30
COMFORT_Y_OUT_M = 0.05
#: The torso no arm point may enter, torso_link (x, y, z) ranges. x and z: the URDF torso
#: inertia's equivalent solid box (0.11 x 0.18 x 0.27 m about the COM at z=0.15), rounded out,
#: z down to the waist. y: that box says +-0.09 (the shoulder joints sit at +-0.10), but the
#: tuck pose for a target far out to the side passes the forearm 0.088 m off the midline.
#: shortcut: y narrowed to 0.08, points only, no link radius, no mesh (not vendored); measure
#: the torso's flank at belly height and use the STL hull with capsule arm links once a
#: crossing is seen to clip, or the tuck pose grazes the torso on the robot.
TORSO_BOX = ((-0.06, 0.06), (-0.08, 0.08), (-0.05, 0.30))
_ARM_POINTS = ("elbow", "wrist", "wrist", "wrist", "hand")  # joint_origins[3:] and the TCP


class PlanError(Exception):
    """Nothing moved: the target or a waypoint is out of reach or out of bounds."""


def in_workspace(side: Side, p: tuple[float, float, float]) -> str | None:
    """Why `p` (torso_link) is out of bounds for this arm, or None."""
    x, y, z = p
    own = -y if side == "right" else y
    if not WORKSPACE_X[0] <= x <= WORKSPACE_X[1]:
        return f"x={x:.2f} m is outside {WORKSPACE_X} in front of the torso"
    if not WORKSPACE_Z[0] <= z <= WORKSPACE_Z[1]:
        return f"z={z:.2f} m is outside {WORKSPACE_Z} relative to the torso"
    if not -WORKSPACE_Y_CROSS <= own <= WORKSPACE_Y_OWN:
        return f"y={y:.2f} m is too far {'across' if own < 0 else 'out'} for the {side} arm"
    return None


def _measured_q(side: Side) -> list[float] | None:
    """The arm's measured angles (URDF convention), from the driver if it holds the arm."""
    driver = arm_sdk.get_driver()
    if driver.engaged:
        q = driver.target_angles
    else:
        try:
            from bridge.sdk.state import get_sampler

            q = tuple(get_sampler().get_arm_state()["arm_q"] or ())
        except Exception:  # noqa: BLE001 — no state is a seedless solve, not a failure
            return None
    if len(q) != 14:
        return None
    return list(q[0:7] if side == "left" else q[7:14])


def _solve(
    side: Side,
    name: str,
    p: tuple[float, float, float],
    pointing: tuple[float, float, float] | None,
    seed: list[float] | None,
) -> tuple[float, ...]:
    why = in_workspace(side, p)
    if why:
        raise PlanError(f"{name}: {why}")
    r = kinematics.solve(side, p, pointing, seed=seed)
    if not r.reached:
        raise PlanError(
            f"{name}: out of reach (closest {r.position_error_m * 100:.0f} cm off, "
            f"fingers {math.degrees(r.direction_error_rad):.0f}° off)"
        )
    # Bound = URDF limits minus kinematics.LIMIT_MARGIN_RAD, NOT retarget.LIMITS: that box is
    # teleop's sign-error guard ("NOT the hardware limits", tighter by design) and rejects
    # 122/162 poses these plans need (limits_check.py); speed is bounded by the 0.6 rad/s slew.
    # IK already clips to this bound; checked again so a future solver change cannot slip past.
    for j, (v, (lo, hi)) in enumerate(zip(r.q, kinematics.limits(side), strict=True)):
        if not lo - 1e-9 <= v <= hi + 1e-9:
            raise PlanError(f"{name}: joint {j} at {math.degrees(v):.0f}° is outside its limits")
    return r.q


def _line(
    side: Side,
    name: str,
    start: tuple[float, float, float],
    end: tuple[float, float, float],
    pointing: tuple[float, float, float] | None,
    q_start: tuple[float, ...],
) -> list[tuple[float, ...]]:
    """IK for every LINE_STEP_M along start -> end, each seeded by the last; ends at `end`.

    PlanError if any point is unreachable or IK jumps branch between neighbours: a branch
    flip is the arm swinging through a different elbow pose, which is exactly the sideways
    sweep this exists to prevent.
    """
    n = max(1, math.ceil(math.dist(start, end) / LINE_STEP_M))
    out: list[tuple[float, ...]] = []
    prev = q_start
    for i in range(1, n + 1):
        k = i / n
        p = (
            start[0] + (end[0] - start[0]) * k,
            start[1] + (end[1] - start[1]) * k,
            start[2] + (end[2] - start[2]) * k,
        )
        q = _solve(side, f"{name} {i}/{n}", p, pointing, list(prev))
        jump = max(abs(a - b) for a, b in zip(q, prev, strict=True))
        if jump > LINE_MAX_DQ_RAD:
            raise PlanError(
                f"{name} {i}/{n}: IK jumped {math.degrees(jump):.0f}° in one joint over "
                f"{LINE_STEP_M * 100:.0f} cm (elbow branch flip), the line is not followable"
            )
        pieces = max(1, math.ceil(jump / LINE_JOINT_STEP_RAD))
        out += [
            tuple(a + (b - a) * j / pieces for a, b in zip(prev, q, strict=True))
            for j in range(1, pieces)
        ]
        out.append(q)
        prev = q
    _check_torso(side, name, out)
    return out


def _approach(side: Side, p: tuple[float, float, float], tilt_deg: float) -> kinematics.Vec3:
    """Horizontal direction from the shoulder to the object, tipped down by `tilt_deg`."""
    sx, sy, _ = kinematics.forward(side, [0.0] * 7).joint_origins[0]
    dx, dy = p[0] - sx, p[1] - sy
    h = math.hypot(dx, dy) or 1.0
    t = math.radians(tilt_deg)
    return kinematics.unit((dx / h * math.cos(t), dy / h * math.cos(t), -math.sin(t)))


def _offset(p: tuple[float, ...], d: tuple[float, ...], k: float) -> tuple[float, float, float]:
    return (p[0] + d[0] * k, p[1] + d[1] * k, p[2] + d[2] * k)


Step = tuple[str, str, Any]  # ("arm", phase, q) | ("hand", phase, shape) | ("wait", phase, s)


def _check_clear(
    side: Side, name: str, qs: list[tuple[float, ...]], edge_x: float, clear_z: float
) -> None:
    """PlanError if any pose puts the hand in front of `edge_x` below `clear_z`."""
    for i, q in enumerate(qs):
        x, _, z = kinematics.forward(side, q).position
        if x > edge_x + kinematics.POS_TOL_M and z < clear_z - kinematics.POS_TOL_M:
            raise PlanError(
                f"{name} {i + 1}/{len(qs)}: the hand would pass {x:.2f} m forward at "
                f"z={z:.2f} m, below the surface clearance z={clear_z:.2f} m — if the arm is "
                "already out in front, release_arm_control first so it starts at the side"
            )


def _check_torso(side: Side, name: str, qs: list[tuple[float, ...]]) -> None:
    """PlanError if any pose puts the elbow, wrist or hand inside TORSO_BOX."""
    for i, q in enumerate(qs):
        hp = kinematics.forward(side, q)
        for part, p in zip(_ARM_POINTS, (*hp.joint_origins[3:], hp.position), strict=True):
            if all(lo < v < hi for v, (lo, hi) in zip(p, TORSO_BOX, strict=True)):
                raise PlanError(
                    f"{name} {i + 1}/{len(qs)}: the {part} would pass through the torso at "
                    f"({p[0]:.2f}, {p[1]:.2f}, {p[2]:.2f}) m — pick a target further out "
                    f"on the {side} arm's own side"
                )


def _joint_path(q_from: list[float], q_to: tuple[float, ...]) -> list[tuple[float, ...]]:
    """q_from -> q_to in LINE_JOINT_STEP_RAD pieces: every joint moves together, as planned,
    instead of each finishing on its own under arm_sdk's per-joint slew."""
    jump = max(abs(b - a) for a, b in zip(q_from, q_to, strict=True))
    n = max(1, math.ceil(jump / LINE_JOINT_STEP_RAD))
    return [
        tuple(a + (b - a) * i / n for a, b in zip(q_from, q_to, strict=True))
        for i in range(1, n + 1)
    ]


def _transit(
    side: Side,
    pre: tuple[float, float, float],
    d: kinematics.Vec3,
    clear_z: float,
    seed: list[float] | None,
) -> list[tuple[str, tuple[float, ...]]]:
    """From wherever the arm is to `pre` without sweeping through the surface:
    rise close to the body ("clear"), forward above the surface ("over"), straight down
    onto pre-grasp ("pre_grasp"). The descent is vertical, over the object's own footprint.
    """
    top = max(clear_z, pre[2])
    # Rise beside the body, on the arm's own side: never across the torso.
    sy = kinematics.forward(side, [0.0] * 7).joint_origins[0][1]
    y = sy + (COMFORT_Y_OUT_M if side == "left" else -COMFORT_Y_OUT_M)
    errors = []
    for edge in sorted({min(x, pre[0]) for x in TUCK_X_M}):
        tuck = (edge, y, top)
        try:
            q_tuck = _solve(side, "clear", tuck, d, seed)
            if seed is None:
                rise = [q_tuck]  # no measured pose (stub, tests): nothing to rise from
            else:
                # Shoulder back (the arm swings behind, never forward), then the elbow and
                # wrist up with the shoulder still back, then the shoulder forward onto the
                # tuck. One joint-space move bends the elbow with the upper arm hanging: the
                # forearm swings out horizontally at desk height (robot, 2026-11-05).
                back = list(seed)
                back[0] = max(seed[0], q_tuck[0], SHOULDER_BACK_RAD)  # URDF pitch: + is back
                bent = (back[0], *q_tuck[1:])
                rise = (
                    _joint_path(seed, tuple(back))
                    + _joint_path(back, bent)
                    + _joint_path(list(bent), q_tuck)
                )
            _check_clear(side, "clear", rise, edge, clear_z)
            _check_torso(side, "clear", rise)
            break
        except PlanError as exc:
            errors.append(str(exc))
    else:
        raise PlanError(errors[0])
    above = (pre[0], pre[1], top)
    over = _line(side, "over", tuck, above, d, rise[-1])
    _check_clear(side, "over", over, edge, clear_z)
    down = _line(side, "pre_grasp", above, pre, d, over[-1])
    return [
        *(("clear", q) for q in rise),
        *(("over", q) for q in over),
        *(("pre_grasp", q) for q in down),
    ]


def plan_pick(
    side: Side, target: tuple[float, float, float], shape: str, seed: list[float] | None
) -> list[Step]:
    """Open, clear, over, pre-grasp, grasp, close, lift — every pose solved, or PlanError.

    shortcut: the object's own z stands in for the support surface (it is above the real
    desk top, so it errs high); use a support plane from perception once it reports one.
    """
    clear_z = target[2] + SURFACE_MARGIN_M
    errors = []
    for tilt in APPROACH_TILTS_DEG:
        d = _approach(side, target, tilt)
        grasp = _offset(target, d, GRASP_DEPTH_OFFSET_M)
        pre = _offset(grasp, d, -PREGRASP_BACK_M)
        lifted = (grasp[0], grasp[1], grasp[2] + LIFT_M)
        try:
            transit = _transit(side, pre, d, clear_z, seed)
            approach = _line(side, "grasp", pre, grasp, d, transit[-1][1])
            lift = _line(side, "lift", grasp, lifted, d, approach[-1])
        except PlanError as exc:
            errors.append(f"tilt {tilt:.0f}°: {exc}")
            continue
        return [
            ("hand", "open", "open"),
            *(("arm", phase, q) for phase, q in transit),
            *(("arm", "grasp", q) for q in approach),
            ("hand", "close", shape),
            ("wait", "closing", CLOSE_SETTLE_S),
            *(("arm", "lift", q) for q in lift),
        ]
    raise PlanError("; ".join(errors))


def plan_place(
    side: Side, target: tuple[float, float, float], seed: list[float] | None
) -> list[Step]:
    """Lower onto `target`, open, then the reverse of a pick's transit: straight up clear of
    the surface ("retreat"), back toward the body ("withdraw"). Releasing the arm afterwards
    lowers it beside the body, behind the desk edge."""
    d = _approach(side, target, 0.0)
    above = (target[0], target[1], target[2] + LIFT_M)
    clear = (target[0], target[1], max(above[2], target[2] + SURFACE_MARGIN_M))
    q_above = _solve(side, "above", above, d, seed)
    _check_torso(side, "above", [q_above])
    down = _line(side, "down", above, target, d, q_above)
    up = _line(side, "retreat", target, clear, d, down[-1])
    errors = []
    for x in TUCK_X_M:
        try:
            back = _line(
                side, "withdraw", clear, (min(x, target[0]), target[1], clear[2]), d, up[-1]
            )
            break
        except PlanError as exc:
            errors.append(str(exc))
    else:
        raise PlanError(errors[0])
    return [
        ("arm", "above", q_above),
        *(("arm", "down", q) for q in down),
        ("hand", "open", "open"),
        ("wait", "opening", CLOSE_SETTLE_S),
        *(("arm", "retreat", q) for q in up),
        *(("arm", "withdraw", q) for q in back),
    ]


def reach_advice(side: Side, target: tuple[float, float, float]) -> str:
    """How to walk so `target` sits where this arm reaches comfortably: in front of its own
    shoulder, COMFORT_X_M ahead. Checked by planning the moved target; says so if even
    that would not reach (a height problem walking cannot fix)."""
    sy = kinematics.forward(side, [0.0] * 7).joint_origins[0][1]
    good = (COMFORT_X_M, sy + (COMFORT_Y_OUT_M if side == "left" else -COMFORT_Y_OUT_M), target[2])
    fwd, left = target[0] - good[0], target[1] - good[1]
    moves = []
    if abs(fwd) >= 0.03:
        moves.append(
            f"{abs(fwd):.2f} m {'forward' if fwd > 0 else 'back'} (walk_velocity vx{'>' if fwd > 0 else '<'}0)"
        )
    if abs(left) >= 0.03:
        moves.append(
            f"{abs(left):.2f} m to the {'LEFT' if left > 0 else 'RIGHT'} (walk_velocity vy{'>' if left > 0 else '<'}0)"
        )
    try:
        plan_pick(side, good, "power", None)
    except PlanError:
        return (
            f"the object's height (z={target[2]:.2f} m from the torso) is out of the {side} arm's "
            "range; walking will not fix that — pick it from a different surface or ask for help"
        )
    if not moves:
        return f"it is already in front of the {side} shoulder; look again for a fresh detection and retry"
    return (
        f"step {' and '.join(moves)} so the object sits {COMFORT_X_M:.2f} m in front of the "
        f"{side} shoulder, then look again and retry pick"
    )


def handover_point(side: Side) -> tuple[float, float, float]:
    x, y, z = HANDOVER_POINT
    return (x, -y if side == "right" else y, z)


# --- targets --------------------------------------------------------------------------------


def label_tier(label: str, query: str) -> int | None:
    """0: the same label (case/accents ignored). 1: the whole query's singular or Spanish
    translation ('cups', 'taza' -> 'cup'). None otherwise — never a partial match: 'Dixie cup'
    is not 'cup', and grabbing a different object is worse than asking."""
    from bridge.object_memory import _STOPWORDS, SYNONYMS, _fold, _singular

    have, want = _fold(label), _fold(query)
    if have == want:
        return 0
    phrase = " ".join(w for w in want.split() if w not in _STOPWORDS)
    forms = {phrase, _singular(phrase)}
    if have in forms or any(have in SYNONYMS.get(f, ()) for f in forms):
        return 1
    return None


def detected_target(
    label: str | None, track_id: int | None
) -> tuple[tuple[float, float, float], dict[str, Any]]:
    """The freshest matching detection, in torso_link. PlanError if there is none.

    `track_id` wins over `label`. Otherwise the exact label beats a translation, and the most
    confident detection wins within a tier. A miss lists what IS graspable, with track_ids.
    """
    from bridge.sdk.perception_link import existing_link

    link = existing_link()
    report, age = link.latest_report() if link is not None else (None, None)
    if not report or age is None or age > FRESH_DETECTION_S:
        raise PlanError("no fresh detection report (perception offline or stale) — look first")
    fresh = [
        obj
        for obj in report.get("objects") or []
        if isinstance(obj, dict)
        and isinstance(obj.get("cam_xyz"), list)
        # The report can be fresh while the frame it repeats is not (age_s: since capture).
        and age + float(obj.get("age_s") or 0) <= FRESH_DETECTION_S
    ]
    found: list[dict[str, Any]] = []
    if track_id is not None:
        found = [o for o in fresh if o.get("track_id") == track_id]
    elif label:
        tiers = [(label_tier(str(o.get("label", "")), label), o) for o in fresh]
        best_tier = min((t for t, _ in tiers if t is not None), default=None)
        found = [o for t, o in tiers if best_tier is not None and t == best_tier]
    if not found:
        what = f"track {track_id}" if track_id is not None else repr(label)
        seen = [
            f"{o.get('label')!r} track_id={o.get('track_id')} {float(o.get('range_m') or 0):.2f} m "
            f"{float(o.get('bearing_deg') or 0):+.0f}°"
            for o in fresh
            if o.get("label") != "person"
        ]
        raise PlanError(
            f"{what} is not in the current detections with a 3-D point. Graspable now: "
            + ("; ".join(seen) if seen else "nothing")
            + " — retry with that exact label or track_id, or turn toward it / find_object"
        )
    best = max(found, key=lambda o: float(o.get("confidence") or 0))
    p = kinematics.camera_to_torso(best["cam_xyz"])
    return p, best


def choose_side(requested: str, p: tuple[float, float, float]) -> Side:
    """`auto` → the arm on the object's side, if that hand is configured; else the other."""
    if requested in ("left", "right"):
        return requested  # type: ignore[return-value]
    preferred: Side = "right" if p[1] <= 0 else "left"
    configured = hands.get_driver().sides
    if configured and preferred not in configured:
        return configured[0]
    return preferred


# --- execution ------------------------------------------------------------------------------


def _abort_reason(allow_person: bool) -> str | None:
    if estop.teleop_is_driving():
        # The headset (its own process) drives the base or holds the arms on rt/arm_sdk.
        return "teleop_active"
    if safety.base_moving():
        return "base_moving"
    if safety.PERSON_RULE and not allow_person:
        nearest, fresh = safety.person_proximity()
        if not fresh:
            # A dead or lagging detector cannot see someone step in.
            return "perception_stale"
        if nearest is not None and nearest < safety.PERSON_REACH_M:
            return "person_in_reach"
    return None


def _plan_dict(steps: list[Step]) -> list[dict[str, Any]]:
    """One entry per phase: a line's sub-waypoints collapse into its final pose + a count."""
    out: list[dict[str, Any]] = []
    for kind, phase, value in steps:
        if kind == "arm":
            q_deg = [round(math.degrees(v), 1) for v in value]
            if out and out[-1].get("phase") == phase and "q_deg" in out[-1]:
                out[-1]["q_deg"] = q_deg
                out[-1]["waypoints"] += 1
            else:
                out.append({"phase": phase, "q_deg": q_deg, "waypoints": 1})
        else:
            out.append({"phase": phase, kind: value})
    return out


async def execute(
    name: str,
    side: Side,
    steps: list[Step],
    *,
    summary: dict[str, Any],
    allow_person: bool = False,
    hold: bool = True,
    ctx: Any | None = None,
) -> dict[str, Any]:
    """Run a planned step list on one arm. Returns the task dict."""
    task = get_registry().create(name)
    task.result = {**summary, "side": side, "plan": _plan_dict(steps)}
    log.info(f"{name}.requested", task_id=task.task_id, side=side, sim_mode=SIM_MODE, **summary)

    def done(
        status: Literal["completed", "cancelled", "failed"],
        phase: str,
        error: str | None = None,
        **extra: Any,
    ) -> dict[str, Any]:
        task.status, task.phase, task.error = status, phase, error
        task.result = {**(task.result or {}), **extra}
        task.progress = 1.0 if status == "completed" else task.progress
        task.ended_at = time.time()
        return task.to_dict()

    if SIM_MODE == "stub":
        return done("completed", "stub", note="Stub mode — planned only, nothing moved.")
    if SIM_MODE != "real":
        return done(
            "failed",
            "not_applicable",
            f"arm motion is real-hardware-only (SIM_MODE={SIM_MODE}); nothing subscribes rt/arm_sdk",
        )
    needs_hand = any(kind == "hand" for kind, _, _ in steps)
    hand_driver = hands.get_driver()
    if needs_hand and side not in hand_driver.sides:
        return done(
            "failed",
            "hand_unavailable",
            f"the {side} hand is not configured (TELEOP_HAND_SIDES={','.join(hand_driver.sides) or 'none'})",
        )
    if needs_hand and hand_driver.name == "simulated":
        # The model must never report a grasp that did not happen.
        task.result = {
            **(task.result or {}),
            "hand": "simulated: the arm moved, but no finger moved and nothing was gripped",
        }
    why = _abort_reason(allow_person)
    if why:
        return done("failed", f"refused:{why}", why)

    driver = arm_sdk.get_driver()
    try:
        await driver.engage()
    except arm_sdk.ArmSdkUnavailable as exc:
        return done("failed", "unavailable", str(exc))

    hand_closed = False
    try:
        for i, (kind, phase, value) in enumerate(steps):
            task.phase = phase
            task.progress = i / len(steps)
            if kind in ("hand", "wait"):
                why = _abort_reason(allow_person)
                if why is None:
                    if kind == "hand":
                        await asyncio.to_thread(hand_driver.send_shape, side, hands.SHAPES[value])
                        hand_closed = value != "open"
                    else:
                        await asyncio.sleep(float(value))
                    why = _abort_reason(allow_person)
                if why is None:
                    continue
                driver.request_release()
                failure: str | None = f"aborted:{why}"
            else:
                angles = ArmAngles(*value)
                left, right = (angles, None) if side == "left" else (None, angles)
                failure = await arm_pose.slew(
                    driver, task, left, right, ctx, abort=lambda: _abort_reason(allow_person)
                )
            if failure == "driver_failed":
                return done(
                    "failed",
                    "driver_failed",
                    driver.status().get("failed") or "arm_sdk_disengaged",
                    failed_at=phase,
                    hand_closed=hand_closed,
                )
            if failure is not None:
                return done(
                    "cancelled" if failure == "cancelled" else "failed",
                    failure,
                    None if failure == "cancelled" else failure,
                    failed_at=phase,
                    hand_closed=hand_closed,
                    note="Arm authority is ramping back to the built-in controller."
                    + (" The hand is still closed: open_hands releases it." if hand_closed else ""),
                )
        if not hold:
            await driver.release()
        return done(
            "completed",
            "holding" if hold else "released",
            hand_closed=hand_closed,
            note=(
                "The arm holds this pose under rt/arm_sdk until place / release_arm_control / "
                "stop_everything."
                if hold
                else "Authority ramped back to the built-in controller."
            ),
        )
    except asyncio.CancelledError:
        driver.request_release()
        task.status, task.phase, task.ended_at = "cancelled", "cancelled", time.time()
        raise
    except Exception as exc:
        driver.request_release()
        log.exception(f"{name}.failed", task_id=task.task_id)
        return done("failed", "exception", repr(exc), hand_closed=hand_closed)


def _refused(name: str, error: str, **extra: Any) -> dict[str, Any]:
    """A plan that never reached the arm. Shaped like a task dict so callers need one path."""
    task = get_registry().create(name)
    task.status, task.phase, task.error = "failed", "planning", error
    task.result = {"moved": False, **extra}
    task.ended_at = time.time()
    return task.to_dict()


#: Pick and place stay refused on the robot until the gantry touch check (test-day
#: manipulation.md) shows the planned arm clears the torso. This flag lifts the fence for that
#: check and after it; read per call. Stub mode plans freely: nothing moves there.
ALLOW_PICK_ENV = "C3PO_ALLOW_PICK"


def _fenced(name: str) -> dict[str, Any] | None:
    if SIM_MODE != "real" or os.environ.get(ALLOW_PICK_ENV) == "1":
        return None
    return _refused(
        name,
        "pick_disabled_until_gantry_test",
        note=(
            f"{name} is off on the robot until the gantry touch check passes (test-day "
            f"manipulation.md). Nothing moved. For that check: {ALLOW_PICK_ENV}=1 in "
            "apps/bridge/.env, then restart the bridge."
        ),
    )


# --- the four skills --------------------------------------------------------------------------


async def pick(
    label: str | None,
    side: str = "auto",
    shape: str = "power",
    track_id: int | None = None,
    ctx: Any | None = None,
) -> dict[str, Any]:
    if (fenced := _fenced("pick")) is not None:
        return fenced
    if shape not in hands.SHAPES or shape == "open":
        return _refused(
            "pick", f"unknown_shape_{shape}", shapes=sorted(set(hands.SHAPES) - {"open"})
        )
    try:
        target, obj = detected_target(label, track_id)
    except PlanError as exc:
        return _refused("pick", str(exc))
    arm = choose_side(side, target)
    if SIM_MODE == "real" and _measured_q(arm) is None:
        # Without the start pose the rise is one unchecked slew: the desk-edge hit.
        return _refused(
            "pick",
            "no measured arm pose (arm state offline): cannot plan a path clear of the surface",
        )
    try:
        steps = plan_pick(arm, target, shape, _measured_q(arm))
    except PlanError as exc:
        other: Side = "left" if arm == "right" else "right"
        configured = hands.get_driver().sides
        if side != "auto" or (configured and other not in configured):
            return _refused("pick", f"{exc}. {reach_advice(arm, target)}")
        try:
            arm, steps = other, plan_pick(other, target, shape, _measured_q(other))
        except PlanError:
            return _refused("pick", f"{exc}. {reach_advice(arm, target)}")
    summary = {
        "object": {
            k: obj.get(k) for k in ("label", "track_id", "confidence", "range_m", "depth_source")
        },
        "target_torso_m": [round(v, 3) for v in target],
        "shape": shape,
        "next": f"verify('¿tenés {obj.get('label')} en la mano?') — no contact sensing here",
    }
    return await execute("pick", arm, steps, summary=summary, ctx=ctx)


async def place(
    side: Side,
    label: str | None = None,
    drop_m: float = 0.05,
    ctx: Any | None = None,
) -> dict[str, Any]:
    """Put down what the hand holds: onto a detected surface/object, or `drop_m` below here."""
    if (fenced := _fenced("place")) is not None:
        return fenced
    seed = _measured_q(side)
    try:
        if label:
            target, _obj = detected_target(label, None)
            target = (target[0], target[1], target[2] + 0.05)
        else:
            if seed is None:
                raise PlanError("no measured arm pose to place from")
            here = kinematics.forward(side, seed).position
            target = (here[0], here[1], here[2] - max(0.0, min(drop_m, 0.15)))
        steps = plan_place(side, target, seed)
    except PlanError as exc:
        return _refused("place", str(exc))
    summary = {"target_torso_m": [round(v, 3) for v in target], "onto": label}
    return await execute("place", side, steps, summary=summary, hold=False, ctx=ctx)


async def handover(side: Side, ctx: Any | None = None) -> dict[str, Any]:
    """Hold the hand out at chest height for a person to take what it holds."""
    p = handover_point(side)
    try:
        q = _solve(side, "handover", p, (1.0, 0.0, 0.0), _measured_q(side))
    except PlanError as exc:
        return _refused("handover", str(exc))
    summary = {
        "target_torso_m": list(p),
        "next": (
            "Say 'tomalo'. When the person says they have it (or 'listo'), call "
            "set_hand(side, shape='open'), then release_arm_control."
        ),
    }
    return await execute(
        "handover", side, [("arm", "extend", q)], summary=summary, allow_person=True, ctx=ctx
    )


async def reach(
    side: Side,
    point: tuple[float, float, float],
    frame: Literal["torso", "camera"] = "torso",
    fingers: Literal["forward", "down", "free"] = "forward",
    hold: bool = True,
    ctx: Any | None = None,
) -> dict[str, Any]:
    p = kinematics.camera_to_torso(point) if frame == "camera" else tuple(float(v) for v in point)
    pointing = {"forward": (1.0, 0.0, 0.0), "down": (0.0, 0.0, -1.0), "free": None}[fingers]
    try:
        q = _solve(side, "reach", p, pointing, _measured_q(side))  # type: ignore[arg-type]
    except PlanError as exc:
        return _refused("reach", str(exc))
    summary = {"target_torso_m": [round(v, 3) for v in p], "fingers": fingers}
    return await execute("reach", side, [("arm", "reach", q)], summary=summary, hold=hold, ctx=ctx)


def arm_state() -> dict[str, Any]:
    """Where each hand is by forward kinematics of the measured joints. Moves nothing."""
    out: dict[str, Any] = {"frame": "torso_link", "convention": "URDF (motor) radians"}
    for side in ("left", "right"):
        q = _measured_q(side)  # type: ignore[arg-type]
        if q is None:
            out[side] = {"error": "no rt/lowstate arm angles"}
            continue
        hp = kinematics.forward(side, q)  # type: ignore[arg-type]
        out[side] = {
            "q_deg": [round(math.degrees(v), 1) for v in q],
            "hand_m": [round(v, 3) for v in hp.position],
            "fingers_point": [round(v, 2) for v in hp.pointing],
            "in_workspace": in_workspace(side, hp.position) is None,  # type: ignore[arg-type]
        }
    out["driver"] = arm_sdk.get_driver().status()
    return out
