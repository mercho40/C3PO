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
reports no reliable contact), collision beyond arm-vs-torso and arm-vs-the-reported-table
(ARM_CAPSULES), regrasp on a miss. The caller
checks success with `verify("¿tenés <x> en la mano?")`.

SIM_MODE=stub returns the full plan (waypoints, IK errors) without moving, so the planner and
the eval suite can exercise pick/place on a laptop.
"""

from __future__ import annotations

import asyncio
import itertools
import math
import os
import time
from collections.abc import Callable, Sequence
from typing import Any, Literal, TypeVar

import structlog

from bridge import estop, kinematics, safety
from bridge.skills import arm_pose
from bridge.skills.task_runtime import Task, get_registry
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
#: Perception's reported table edge (support_table) can only bring that limit closer.
TUCK_X_M = (0.16, 0.20, 0.24)  # closest first; further out only where the closer is unreachable
#: How far the shoulder pitches back before the elbow bends: the elbow sits behind the chest,
#: so the forearm passes horizontal no further forward than the first tuck point.
SHOULDER_BACK_RAD = 0.7
#: Where the arm reaches comfortably, in front of its own shoulder (torso_link). Used to tell
#: the model how to walk when a target is out of reach.
COMFORT_X_M = 0.30
COMFORT_Y_OUT_M = 0.05
#: The torso no arm link may come near, torso_link (x, y, z) ranges: the bounds of MuJoCo
#: Menagerie unitree_g1's torso_link collision meshes (torso_link_rev_1_0.STL + logo_link),
#: rounded out 1 mm. Not head_link: it starts at z=0.28, above and behind the hand's workspace.
TORSO_BOX = ((-0.070, 0.085), (-0.109, 0.109), (-0.010, 0.313))
#: The same meshes narrow away from the shoulders: |y| stays under the line through each pair
#: of (z, |y|) points, edges of their convex hull seen from the front, +1 mm. The box alone
#: puts the belly 1 cm wider than it is, and the upper arm hangs only ~2 cm off it.
TORSO_FLANKS = (
    ((0.008, 0.069), (0.063, 0.093)),
    ((0.083, 0.098), (0.218, 0.109)),
    ((0.218, 0.109), (0.246, 0.104)),
)
#: Each arm link as a capsule that holds its Menagerie collision geometry: (name, a, b, t0, t1,
#: radius m), the t0..t1 stretch of the line from point a to point b of kinematics.forward
#: (joint_origins 0-6, then 7 = the TCP). tests/sim checks every vertex is inside.
#: shortcut: the hand is wrist_yaw_link's radius out to the TCP (the model's hand has no
#: collision geom); measure the BrainCo Revo2's palm and widen "hand" if it is thicker.
ARM_CAPSULES = (
    ("shoulder", 1, 2, 0.3, 0.7, 0.036),  # shoulder_roll_link: a 0.03 m cylinder
    ("upper_arm", 2, 3, 0.0, 1.0, 0.049),  # shoulder_yaw_link, elbow motor bulge inward
    ("forearm", 3, 4, 0.0, 1.0, 0.044),  # elbow_link
    ("wrist", 4, 5, 0.0, 1.0, 0.031),  # wrist_roll_link
    ("wrist", 5, 6, 0.0, 1.0, 0.035),  # wrist_pitch_link
    ("hand", 6, 7, 0.0, 1.0, 0.031),  # wrist_yaw_link
)
#: The first pose of a pick (the tuck) or place (above) holds the upper arm at least this far
#: out from the body (shoulder roll; URDF: out is negative for the right arm), tried in order.
#: IK's nearest tuck had it 5-20° in: the upper arm folded back along the flank, into the
#: torso on every pick in the twin. 0.7 plans the low and far picks, 0.35 the places.
SHOULDER_OUT_RAD = (0.7, 0.35)
#: The support under the object, as the planner sees it: (edge_x, top_z) in torso_link, a box
#: from the front face at edge_x away from the robot, top at top_z, down to the floor.
Table = tuple[float, float]
#: Every arm capsule stays this far from the table box, on every commanded pose. The hand's
#: capsule ends at the TCP, so a grasp needs its centre ~4 cm (radius + this) above the top.
TABLE_CLEARANCE_M = 0.01
TABLE_DEPTH_M = 2.0  # how far the box runs away from the robot, sideways and down
#: The report's support must come from the same frame as the object: its stamp within the
#: rounding of the summary's stamp - age_s (0.1 s steps) of that object's capture.
SUPPORT_SAME_CAPTURE_S = 0.06
#: Every PlanError.reason, the most telling first: when every approach fails, the refusal
#: names the first of these any of them hit (an arm that got there but met the table says
#: more than one that could not get there). `not_planned`: refused before planning.
PLAN_REASONS = (
    "table_clearance",
    "torso_clearance",
    "surface_clearance",
    "branch_flip",
    "joint_limits",
    "out_of_reach",
    "workspace",
    "not_planned",
)


class PlanError(Exception):
    """Nothing moved: the target or a waypoint is out of reach or out of bounds.

    `reason` is one of PLAN_REASONS, `part` the arm capsule a clearance refusal is about;
    `advice` and `hint` say what to change (reach_advice), when that was worked out.
    """

    def __init__(
        self,
        msg: str,
        reason: str = "not_planned",
        part: str | None = None,
        advice: dict[str, Any] | None = None,
        hint: str | None = None,
    ) -> None:
        super().__init__(msg)
        self.reason, self.part, self.advice, self.hint = reason, part, advice or {}, hint


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
    joint_limits: list[tuple[float, float]] | None = None,
) -> tuple[float, ...]:
    why = in_workspace(side, p)
    if why:
        raise PlanError(f"{name}: {why}", "workspace")
    r = kinematics.solve(side, p, pointing, seed=seed, joint_limits=joint_limits)
    if not r.reached:
        raise PlanError(
            f"{name}: out of reach (closest {r.position_error_m * 100:.0f} cm off, "
            f"fingers {math.degrees(r.direction_error_rad):.0f}° off)",
            "out_of_reach",
        )
    # Bound = URDF limits minus kinematics.LIMIT_MARGIN_RAD, NOT retarget.LIMITS: that box is
    # teleop's sign-error guard ("NOT the hardware limits", tighter by design) and rejects
    # 122/162 poses these plans need (limits_check.py); speed is bounded by the 0.6 rad/s slew.
    # IK already clips to this bound; checked again so a future solver change cannot slip past.
    for j, (v, (lo, hi)) in enumerate(zip(r.q, kinematics.limits(side), strict=True)):
        if not lo - 1e-9 <= v <= hi + 1e-9:
            raise PlanError(
                f"{name}: joint {j} at {math.degrees(v):.0f}° is outside its limits", "joint_limits"
            )
    return r.q


def _line(
    side: Side,
    name: str,
    start: tuple[float, float, float],
    end: tuple[float, float, float],
    pointing: tuple[float, float, float] | None,
    q_start: tuple[float, ...],
    table: Table | None = None,
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
                f"{LINE_STEP_M * 100:.0f} cm (elbow branch flip), the line is not followable",
                "branch_flip",
            )
        pieces = max(1, math.ceil(jump / LINE_JOINT_STEP_RAD))
        out += [
            tuple(a + (b - a) * j / pieces for a, b in zip(prev, q, strict=True))
            for j in range(1, pieces)
        ]
        out.append(q)
        prev = q
    _check_torso(side, name, out, table)
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
                "already out in front, release_arm_control first so it starts at the side",
                "surface_clearance",
            )


def _box_gap(p: Sequence[float], box: Sequence[tuple[float, float]]) -> float:
    """Euclidean distance from `p` to an axis-aligned box, 0 inside."""
    return math.hypot(*(max(lo - v, 0.0, v - hi) for v, (lo, hi) in zip(p, box, strict=True)))


def _torso_gap(p: Sequence[float]) -> float:
    """How far `p` is outside TORSO_BOX and TORSO_FLANKS, 0 inside: never more than the true
    distance (exact off the box faces), and convex in `p`."""
    gap = _box_gap(p, TORSO_BOX)
    for (za, ya), (zb, yb) in TORSO_FLANKS:
        k = (yb - ya) / (zb - za)
        gap = max(gap, (abs(p[1]) - ya - k * (p[2] - za)) / math.hypot(1.0, k))
    return gap


def _segment_gap(
    a: Sequence[float],
    b: Sequence[float],
    gap: Callable[[Sequence[float]], float] = _torso_gap,
    enough: float = math.inf,
) -> float:
    """The least `gap` along a -> b. Convex along the segment, so a ternary search; or, when
    that is at least `enough`, a lower bound of it that is too."""

    def at(t: float) -> float:
        return gap([u + (v - u) * t for u, v in zip(a, b, strict=True)])

    # Distances are 1-Lipschitz: nowhere on the segment below (gap(a) + gap(b) - |ab|) / 2.
    bound = (at(0.0) + at(1.0) - math.dist(a, b)) / 2
    if bound >= enough:
        return bound
    lo, hi = 0.0, 1.0
    for _ in range(25):  # (2/3)^25 of a link: under 0.1 mm
        m1, m2 = lo + (hi - lo) / 3, hi - (hi - lo) / 3
        lo, hi = (lo, m2) if at(m1) < at(m2) else (m1, hi)
    return at((lo + hi) / 2)


def _check_torso(
    side: Side, name: str, qs: list[tuple[float, ...]], table: Table | None = None
) -> None:
    """PlanError if any pose brings an arm link's capsule (ARM_CAPSULES) into the torso, or
    within TABLE_CLEARANCE_M of the table box."""
    box = None
    if table is not None:
        edge, top = table
        box = (
            (edge, edge + TABLE_DEPTH_M),
            (-TABLE_DEPTH_M, TABLE_DEPTH_M),
            (top - TABLE_DEPTH_M, top),
        )
    for i, q in enumerate(qs):
        hp = kinematics.forward(side, q)
        pts = (*hp.joint_origins, hp.position)
        for part, a, b, t0, t1, r in ARM_CAPSULES:
            ends = [
                [u + (v - u) * t for u, v in zip(pts[a], pts[b], strict=True)] for t in (t0, t1)
            ]
            gap = _segment_gap(ends[0], ends[1], enough=r) - r
            if gap < 0:
                raise PlanError(
                    f"{name} {i + 1}/{len(qs)}: the {part} would pass through the torso "
                    f"({-gap * 1000:.0f} mm into its clearance) — pick a target further out "
                    f"on the {side} arm's own side",
                    "torso_clearance",
                    part,
                )
            if box is not None:
                need = r + TABLE_CLEARANCE_M
                gap = _segment_gap(ends[0], ends[1], lambda p: _box_gap(p, box), need) - need
                if gap < 0:
                    raise PlanError(
                        f"{name} {i + 1}/{len(qs)}: the {part} would touch the table "
                        f"({-gap * 1000:.0f} mm into its {TABLE_CLEARANCE_M * 100:.0f} cm "
                        "clearance)",
                        "table_clearance",
                        part,
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


def _arm_out_limits(side: Side, out: float) -> list[tuple[float, float]]:
    """kinematics.limits with the shoulder roll held `out` rad out from the body."""
    lim = kinematics.limits(side)
    lo, hi = lim[1]
    lim[1] = (max(lo, out), hi) if side == "left" else (lo, min(hi, -out))
    return lim


def _transit(
    side: Side,
    pre: tuple[float, float, float],
    d: kinematics.Vec3,
    clear_z: float,
    seed: list[float] | None,
    out: float,
    table: Table | None = None,
) -> list[tuple[str, tuple[float, ...]]]:
    """From wherever the arm is to `pre` without sweeping through the surface:
    rise close to the body ("clear"), forward above the surface ("over"), straight down
    onto pre-grasp ("pre_grasp"). The descent is vertical, over the object's own footprint.
    """
    top = max(clear_z, pre[2])
    # Rise beside the body, on the arm's own side: never across the torso.
    sy = kinematics.forward(side, [0.0] * 7).joint_origins[0][1]
    y = sy + (COMFORT_Y_OUT_M if side == "left" else -COMFORT_Y_OUT_M)
    errors: list[PlanError] = []
    for x_tuck in sorted({min(x, pre[0]) for x in TUCK_X_M}):
        tuck = (x_tuck, y, top)
        # In front of the tuck may be over the desk; a reported edge only brings that closer.
        edge = x_tuck if table is None else min(x_tuck, table[0])
        try:
            q_tuck = _solve(side, "clear", tuck, d, seed, _arm_out_limits(side, out))
            if seed is None:
                rise = [q_tuck]  # no measured pose (stub, tests): nothing to rise from
            else:
                # Shoulder back (the arm swings behind, never forward), then the elbow and
                # wrist up with the shoulder still back, then the shoulder forward onto the
                # tuck. One joint-space move bends the elbow with the upper arm hanging: the
                # forearm swings out horizontally at desk height (robot, 2026-11-05).
                back = list(seed)
                back[0] = max(seed[0], q_tuck[0], SHOULDER_BACK_RAD)  # URDF pitch: + is back
                back[1] = q_tuck[1]  # and out: pitched back, the hanging arm passes the flank
                bent = (back[0], *q_tuck[1:])
                rise = (
                    _joint_path(seed, tuple(back))
                    + _joint_path(back, bent)
                    + _joint_path(list(bent), q_tuck)
                )
            _check_clear(side, "clear", rise, edge, clear_z)
            _check_torso(side, "clear", rise, table)
            break
        except PlanError as exc:
            errors.append(exc)
    else:
        raise _most_telling(errors)
    above = (pre[0], pre[1], top)
    over = _line(side, "over", tuck, above, d, rise[-1], table)
    _check_clear(side, "over", over, edge, clear_z)
    down = _line(side, "pre_grasp", above, pre, d, over[-1], table)
    return [
        *(("clear", q) for q in rise),
        *(("over", q) for q in over),
        *(("pre_grasp", q) for q in down),
    ]


def _most_telling(errors: list[PlanError]) -> PlanError:
    return min(errors, key=lambda e: PLAN_REASONS.index(e.reason))


def _refuse_all(errors: list[tuple[str, PlanError]]) -> PlanError:
    """Every attempt's reason in one PlanError, coded by the most telling (PLAN_REASONS)."""
    first = _most_telling([e for _, e in errors])
    return PlanError("; ".join(f"{k}: {e}" for k, e in errors), first.reason, first.part)


def plan_pick(
    side: Side,
    target: tuple[float, float, float],
    shape: str,
    seed: list[float] | None,
    table: Table | None = None,
) -> list[Step]:
    """Open, clear, over, pre-grasp, grasp, close, lift — every pose solved, or PlanError.

    The hand passes SURFACE_MARGIN_M over the reported table top, or over the object's own
    z without one or when that is higher (it is above the real desk top, so it errs high).
    """
    clear_z = max(target[2], table[1] if table else target[2]) + SURFACE_MARGIN_M
    errors: list[tuple[str, PlanError]] = []
    for out, tilt in itertools.product(SHOULDER_OUT_RAD, APPROACH_TILTS_DEG):
        d = _approach(side, target, tilt)
        grasp = _offset(target, d, GRASP_DEPTH_OFFSET_M)
        pre = _offset(grasp, d, -PREGRASP_BACK_M)
        lifted = (grasp[0], grasp[1], grasp[2] + LIFT_M)
        try:
            transit = _transit(side, pre, d, clear_z, seed, out, table)
            approach = _line(side, "grasp", pre, grasp, d, transit[-1][1], table)
            lift = _line(side, "lift", grasp, lifted, d, approach[-1], table)
        except PlanError as exc:
            errors.append((f"tilt {tilt:.0f}°, shoulder {math.degrees(out):.0f}° out", exc))
            continue
        return [
            ("hand", "open", "open"),
            *(("arm", phase, q) for phase, q in transit),
            *(("arm", "grasp", q) for q in approach),
            ("hand", "close", shape),
            ("wait", "closing", CLOSE_SETTLE_S),
            *(("arm", "lift", q) for q in lift),
        ]
    raise _refuse_all(errors)


def plan_place(
    side: Side,
    target: tuple[float, float, float],
    seed: list[float] | None,
    table: Table | None = None,
) -> list[Step]:
    """Lower onto `target`, open, then the reverse of a pick's transit: straight up clear of
    the surface ("retreat"), back toward the body ("withdraw"). Releasing the arm afterwards
    lowers it beside the body, behind the desk edge."""
    errors: list[tuple[str, PlanError]] = []
    for out in SHOULDER_OUT_RAD:
        try:
            return _plan_place(side, target, seed, out, table)
        except PlanError as exc:
            errors.append((f"shoulder {math.degrees(out):.0f}° out", exc))
    raise _refuse_all(errors)


def _plan_place(
    side: Side,
    target: tuple[float, float, float],
    seed: list[float] | None,
    out: float,
    table: Table | None,
) -> list[Step]:
    d = _approach(side, target, 0.0)
    above = (target[0], target[1], target[2] + LIFT_M)
    surface = max(target[2], table[1] if table else target[2])
    clear = (target[0], target[1], max(above[2], surface + SURFACE_MARGIN_M))
    q_above = _solve(side, "above", above, d, seed, _arm_out_limits(side, out))
    _check_torso(side, "above", [q_above], table)
    down = _line(side, "down", above, target, d, q_above, table)
    up = _line(side, "retreat", target, clear, d, down[-1], table)
    errors = []
    for x in TUCK_X_M:
        try:
            back = _line(
                side, "withdraw", clear, (min(x, target[0]), target[1], clear[2]), d, up[-1], table
            )
            break
        except PlanError as exc:
            errors.append(exc)
    else:
        raise _most_telling(errors)
    return [
        ("arm", "above", q_above),
        *(("arm", "down", q) for q in down),
        ("hand", "open", "open"),
        ("wait", "opening", CLOSE_SETTLE_S),
        *(("arm", "retreat", q) for q in up),
        *(("arm", "withdraw", q) for q in back),
    ]


_PART_ES = {
    "shoulder": "el hombro",
    "upper_arm": "el brazo",
    "forearm": "el antebrazo",
    "wrist": "la muñeca",
    "hand": "la mano",
}


def reach_advice(
    side: Side,
    target: tuple[float, float, float],
    table: Table | None = None,
    place: bool = False,
) -> tuple[str, dict[str, Any], str]:
    """How to walk so `target` sits where this arm reaches comfortably: in front of its own
    shoulder, COMFORT_X_M ahead, but never so close that the table edge comes nearer than
    TUCK_X_M[0]. Checked by planning the moved target (and table); says so if even that would
    not plan (a height or table problem walking cannot fix).

    Returns (English for the error, `advice` for the agent, a short Spanish hint). `advice` is
    positive metres under step_forward_m / step_back_m / step_left_m / step_right_m, or a
    `problem` (too_low, too_high, behind_table_edge, table_clearance, look_again) with the
    torso_link heights that explain it.
    """
    sy = kinematics.forward(side, [0.0] * 7).joint_origins[0][1]
    want = target[0] - COMFORT_X_M
    fwd = want if table is None else min(want, table[0] - TUCK_X_M[0])
    left = target[1] - (sy + (COMFORT_Y_OUT_M if side == "left" else -COMFORT_Y_OUT_M))
    moved = (target[0] - fwd, target[1] - left, target[2])

    def plans(p: tuple[float, float, float], tb: Table | None) -> PlanError | None:
        try:
            plan_place(side, p, None, tb) if place else plan_pick(side, p, "power", None, tb)
        except PlanError as exc:
            return exc
        return None

    failed = plans(moved, None if table is None else (table[0] - fwd, table[1]))
    if failed is not None:
        z = target[2]
        if (
            table is not None
            and fwd < want - 0.03
            and plans((COMFORT_X_M, *moved[1:]), None) is None
        ):
            behind = target[0] - table[0]
            return (
                f"the object is {behind:.2f} m behind the table edge: out of the {side} arm's "
                "reach even with the robot at the edge — slide it closer or ask for help",
                {"problem": "behind_table_edge", "behind_edge_m": round(behind, 2)},
                f"El objeto está {behind:.2f} m detrás del borde de la mesa: no llego ni pegado "
                "al borde. Acercalo o pedí ayuda.",
            )
        if table is not None and failed.reason == "table_clearance":
            above = z - table[1]
            return (
                f"the {failed.part} would touch the table wherever the robot stands: the "
                f"object's centre is only {above:.2f} m above the top (z={table[1]:+.2f} m from "
                "the torso) — walking will not fix that; pick something taller or ask for help",
                {
                    "problem": "table_clearance",
                    "part": failed.part,
                    "surface_z_m": round(table[1], 2),
                    "object_above_surface_m": round(above, 2),
                },
                f"{_PART_ES.get(failed.part or '', 'el brazo').capitalize()} tocaría la mesa: el "
                "objeto es muy bajo para agarrarlo. Caminar no lo arregla.",
            )
        low = z < sum(WORKSPACE_Z) / 2
        what = "surface" if table is not None else "object"
        h = table[1] if table is not None else z
        es = (
            f"{'La superficie' if table is not None else 'El objeto'} está muy "
            f"{('baj' if low else 'alt') + ('a' if table is not None else 'o')} para el brazo "
            f"(z={h:+.2f} m del torso): caminar no lo arregla."
        )
        return (
            f"the {what}'s height (z={h:.2f} m from the torso) is too {'low' if low else 'high'} "
            f"for the {side} arm; walking will not fix that — pick it from a different surface "
            "or ask for help",
            {
                "problem": "too_low" if low else "too_high",
                "object_z_m": round(z, 2),
                **({"surface_z_m": round(table[1], 2)} if table is not None else {}),
            },
            es,
        )
    advice: dict[str, Any] = {}
    moves, es_moves = [], []
    if abs(fwd) >= 0.03:
        advice["step_forward_m" if fwd > 0 else "step_back_m"] = round(abs(fwd), 2)
        moves.append(
            f"{abs(fwd):.2f} m {'forward' if fwd > 0 else 'back'} (walk_velocity vx{'>' if fwd > 0 else '<'}0)"
        )
        es_moves.append(f"{'acercate' if fwd > 0 else 'alejate'} ~{abs(fwd):.2f} m")
    if abs(left) >= 0.03:
        advice["step_left_m" if left > 0 else "step_right_m"] = round(abs(left), 2)
        moves.append(
            f"{abs(left):.2f} m to the {'LEFT' if left > 0 else 'RIGHT'} (walk_velocity vy{'>' if left > 0 else '<'}0)"
        )
        es_moves.append(f"correte ~{abs(left):.2f} m a la {'izquierda' if left > 0 else 'derecha'}")
    if not moves:
        return (
            f"it is already in front of the {side} shoulder; look again for a fresh detection and retry",
            {"problem": "look_again"},
            "Ya está frente al hombro: mirá de nuevo y reintentá.",
        )
    sits = (
        f"{COMFORT_X_M:.2f} m in front of the {side} shoulder"
        if fwd == want
        else f"{moved[0]:.2f} m ahead (the table edge stops the robot there)"
    )
    return (
        f"step {' and '.join(moves)} so the object sits {sits}, then look again and retry "
        f"{'place' if place else 'pick'}",
        advice,
        " y ".join(es_moves).capitalize() + ", después mirá de nuevo.",
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
    The detection keeps its `support` only if that comes from the same capture.
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
    try:
        capture = float(report["stamp_unix"]) - float(best.get("age_s") or 0)
        same = abs(float(best["support"]["stamp_unix"]) - capture) <= SUPPORT_SAME_CAPTURE_S
    except (KeyError, TypeError, ValueError):
        same = False
    if not same:
        best = {k: v for k, v in best.items() if k != "support"}
    return p, best


def support_table(obj: dict[str, Any], target: tuple[float, float, float]) -> Table | None:
    """The detection's reported support as the planner's table, or None (then the object's own
    z and the TUCK_X_M proxy stand in, as before reports had one).

    `support.z_m` is a base_link height and `near_edge_m` a base_link range along the object's
    bearing, so the edge point goes through kinematics.base_to_torso.
    shortcut: the front face is square to the robot through that one point; an edge seen at an
    angle is nearer on one side. Upgrade if perception reports the edge's direction.
    """
    s = obj.get("support")
    if not isinstance(s, dict):
        return None
    try:
        z = float(s["z_m"])
        d = None if s.get("near_edge_m") is None else float(s["near_edge_m"])
        b = math.radians(float(obj.get("bearing_deg") or 0))
    except (KeyError, TypeError, ValueError):
        return None
    if not math.isfinite(z) or (d is not None and not (math.isfinite(d) and d > 0)):
        return None
    r = d or 0.0
    edge, _, top = kinematics.base_to_torso((r * math.cos(b), r * math.sin(b), z))
    # The object stands on it, so its edge is no further than the object; unknown: the proxy.
    return (min(TUCK_X_M[0] if d is None else edge, target[0]), top)


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
    task: Task,
    side: Side,
    steps: list[Step],
    *,
    summary: dict[str, Any],
    allow_person: bool = False,
    hold: bool = True,
    ctx: Any | None = None,
) -> dict[str, Any]:
    """Run a planned step list on one arm, as `task` (created before planning). Returns the
    task dict."""
    name = task.skill_name
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

    if task.cancel_event.is_set():
        # A stop arrived while the plan was computed off the loop: nothing has moved yet.
        return done("cancelled", "cancelled", note="Stopped before anything moved.")
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


def _refused(task: Task, error: str, **extra: Any) -> dict[str, Any]:
    """A plan that never reached the arm. Shaped like a task dict so callers need one path."""
    task.status, task.phase, task.error = "failed", "planning", error
    task.result = {"moved": False, **extra}
    task.ended_at = time.time()
    return task.to_dict()


#: Pick and place stay refused on the robot until the gantry touch check (test-day
#: manipulation.md) shows the planned arm clears the torso. This flag lifts the fence for that
#: check and after it; read per call. Stub mode plans freely: nothing moves there.
ALLOW_PICK_ENV = "C3PO_ALLOW_PICK"


def _fenced(task: Task) -> dict[str, Any] | None:
    if SIM_MODE != "real" or os.environ.get(ALLOW_PICK_ENV) == "1":
        return None
    return _refused(
        task,
        "pick_disabled_until_gantry_test",
        note=(
            f"{task.skill_name} is off on the robot until the gantry touch check passes (test-day "
            f"manipulation.md). Nothing moved. For that check: {ALLOW_PICK_ENV}=1 in "
            "apps/bridge/.env, then restart the bridge."
        ),
    )


# --- the four skills --------------------------------------------------------------------------
#
# Each creates its task BEFORE planning, so a stop during the plan finds it (execute checks the
# cancel), and plans in a worker thread: pure-Python IK takes ~1 s for a refused pick, and the
# event loop runs the 50 Hz arm hold and the stop.


_T = TypeVar("_T")


async def _off_loop(task: Task, fn: Callable[..., _T], *args: Any) -> _T:
    """`fn(*args)` in a worker thread. Anything but a PlanError (the caller refuses those)
    ends `task` here, or it would stay "running" with no skill behind it."""
    try:
        return await asyncio.to_thread(fn, *args)
    except PlanError:
        raise
    except BaseException as exc:
        cancelled = isinstance(exc, asyncio.CancelledError)
        task.status = "cancelled" if cancelled else "failed"
        task.phase, task.error = ("cancelled", None) if cancelled else ("exception", repr(exc))
        task.ended_at = time.time()
        raise


def _advised(
    exc: PlanError, side: Side, target: tuple[float, float, float], table: Table | None, place: bool
) -> PlanError:
    """`exc` with reach_advice's walk (or why walking will not help) added."""
    text, advice, hint = reach_advice(side, target, table, place)
    if exc.part:
        advice.setdefault("part", exc.part)
    return PlanError(f"{exc}. {text}", exc.reason, exc.part, advice, hint)


def _refusal(exc: PlanError) -> dict[str, Any]:
    """_refused's extras for a plan that failed: its reason code, advice and the hint."""
    return {"reason": exc.reason, **({"advice": exc.advice, "note": exc.hint} if exc.hint else {})}


def _plan_pick(
    arm: Side, side: str, target: tuple[float, float, float], shape: str, table: Table | None = None
) -> tuple[Side, list[Step]]:
    """(arm, steps) on `arm`, else on the other arm when `side` is auto and its hand is
    configured. PlanError says how to walk if neither plans."""
    try:
        return arm, plan_pick(arm, target, shape, _measured_q(arm), table)
    except PlanError as exc:
        other: Side = "left" if arm == "right" else "right"
        configured = hands.get_driver().sides
        if side == "auto" and (not configured or other in configured):
            try:
                return other, plan_pick(other, target, shape, _measured_q(other), table)
            except PlanError:
                pass
        raise _advised(exc, arm, target, table, False) from None


def _plan_place_advised(
    side: Side, target: tuple[float, float, float], seed: list[float] | None, table: Table | None
) -> list[Step]:
    try:
        return plan_place(side, target, seed, table)
    except PlanError as exc:
        raise _advised(exc, side, target, table, True) from None


async def pick(
    label: str | None,
    side: str = "auto",
    shape: str = "power",
    track_id: int | None = None,
    ctx: Any | None = None,
) -> dict[str, Any]:
    task = get_registry().create("pick")
    if (fenced := _fenced(task)) is not None:
        return fenced
    if shape not in hands.SHAPES or shape == "open":
        return _refused(task, f"unknown_shape_{shape}", shapes=sorted(set(hands.SHAPES) - {"open"}))
    try:
        target, obj = detected_target(label, track_id)
    except PlanError as exc:
        return _refused(task, str(exc))
    table = support_table(obj, target)
    arm = choose_side(side, target)
    if SIM_MODE == "real" and _measured_q(arm) is None:
        # Without the start pose the rise is one unchecked slew: the desk-edge hit.
        return _refused(
            task,
            "no measured arm pose (arm state offline): cannot plan a path clear of the surface",
        )
    try:
        arm, steps = await _off_loop(task, _plan_pick, arm, side, target, shape, table)
    except PlanError as exc:
        return _refused(task, str(exc), **_refusal(exc))
    summary = {
        "object": {
            k: obj.get(k) for k in ("label", "track_id", "confidence", "range_m", "depth_source")
        },
        "target_torso_m": [round(v, 3) for v in target],
        "table_torso_m": table and [round(v, 3) for v in table],
        "shape": shape,
        "next": f"verify('¿tenés {obj.get('label')} en la mano?') — no contact sensing here",
    }
    return await execute(task, arm, steps, summary=summary, ctx=ctx)


async def place(
    side: Side,
    label: str | None = None,
    drop_m: float = 0.05,
    ctx: Any | None = None,
) -> dict[str, Any]:
    """Put down what the hand holds: onto a detected surface/object, or `drop_m` below here."""
    task = get_registry().create("place")
    if (fenced := _fenced(task)) is not None:
        return fenced
    seed = _measured_q(side)
    try:
        if label:
            target, obj = detected_target(label, None)
            target = (target[0], target[1], target[2] + 0.05)
            table = support_table(obj, target)
            steps = await _off_loop(task, _plan_place_advised, side, target, seed, table)
        else:
            if seed is None:
                raise PlanError("no measured arm pose to place from")
            here = kinematics.forward(side, seed).position
            target = (here[0], here[1], here[2] - max(0.0, min(drop_m, 0.15)))
            steps = await _off_loop(task, plan_place, side, target, seed)
    except PlanError as exc:
        return _refused(task, str(exc), **_refusal(exc))
    summary = {"target_torso_m": [round(v, 3) for v in target], "onto": label}
    return await execute(task, side, steps, summary=summary, hold=False, ctx=ctx)


async def handover(side: Side, ctx: Any | None = None) -> dict[str, Any]:
    """Hold the hand out at chest height for a person to take what it holds."""
    task = get_registry().create("handover")
    p = handover_point(side)
    try:
        q = await _off_loop(task, _solve, side, "handover", p, (1.0, 0.0, 0.0), _measured_q(side))
    except PlanError as exc:
        return _refused(task, str(exc))
    summary = {
        "target_torso_m": list(p),
        "next": (
            "Say 'tomalo'. When the person says they have it (or 'listo'), call "
            "set_hand(side, shape='open'), then release_arm_control."
        ),
    }
    return await execute(
        task, side, [("arm", "extend", q)], summary=summary, allow_person=True, ctx=ctx
    )


async def reach(
    side: Side,
    point: tuple[float, float, float],
    frame: Literal["torso", "camera"] = "torso",
    fingers: Literal["forward", "down", "free"] = "forward",
    hold: bool = True,
    ctx: Any | None = None,
) -> dict[str, Any]:
    task = get_registry().create("reach")
    p = kinematics.camera_to_torso(point) if frame == "camera" else tuple(float(v) for v in point)
    pointing = {"forward": (1.0, 0.0, 0.0), "down": (0.0, 0.0, -1.0), "free": None}[fingers]
    try:
        q = await _off_loop(task, _solve, side, "reach", p, pointing, _measured_q(side))
    except PlanError as exc:
        return _refused(task, str(exc))
    summary = {"target_torso_m": [round(v, 3) for v in p], "fingers": fingers}
    return await execute(task, side, [("arm", "reach", q)], summary=summary, hold=hold, ctx=ctx)


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
