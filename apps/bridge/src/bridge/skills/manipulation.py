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
* a per-tick abort: a person inside arm's reach (except `handover`), or the base walking;
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

from bridge import kinematics, safety
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
RETREAT_M = 0.08
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


def plan_pick(
    side: Side, target: tuple[float, float, float], shape: str, seed: list[float] | None
) -> list[Step]:
    """Open, pre-grasp, grasp, close, lift — every pose solved, or PlanError."""
    errors = []
    for tilt in APPROACH_TILTS_DEG:
        d = _approach(side, target, tilt)
        grasp = _offset(target, d, GRASP_DEPTH_OFFSET_M)
        pre = _offset(grasp, d, -PREGRASP_BACK_M)
        lifted = (grasp[0], grasp[1], grasp[2] + LIFT_M)
        try:
            q_pre = _solve(side, "pre-grasp", pre, d, seed)
            approach = _line(side, "grasp", pre, grasp, d, q_pre)
            lift = _line(side, "lift", grasp, lifted, d, approach[-1])
        except PlanError as exc:
            errors.append(f"tilt {tilt:.0f}°: {exc}")
            continue
        return [
            ("hand", "open", "open"),
            ("arm", "pre_grasp", q_pre),
            *(("arm", "grasp", q) for q in approach),
            ("hand", "close", shape),
            ("wait", "closing", CLOSE_SETTLE_S),
            *(("arm", "lift", q) for q in lift),
        ]
    raise PlanError("; ".join(errors))


def plan_place(
    side: Side, target: tuple[float, float, float], seed: list[float] | None
) -> list[Step]:
    """Lower onto `target`, open, back away."""
    d = _approach(side, target, 0.0)
    above = (target[0], target[1], target[2] + LIFT_M)
    q_above = _solve(side, "above", above, d, seed)
    down = _line(side, "down", above, target, d, q_above)
    back = _line(side, "retreat", target, _offset(target, d, -RETREAT_M), d, down[-1])
    return [
        ("arm", "above", q_above),
        *(("arm", "down", q) for q in down),
        ("hand", "open", "open"),
        ("wait", "opening", CLOSE_SETTLE_S),
        *(("arm", "retreat", q) for q in back),
    ]


def handover_point(side: Side) -> tuple[float, float, float]:
    x, y, z = HANDOVER_POINT
    return (x, -y if side == "right" else y, z)


# --- targets --------------------------------------------------------------------------------


def detected_target(
    label: str | None, track_id: int | None
) -> tuple[tuple[float, float, float], dict[str, Any]]:
    """The freshest matching detection, in torso_link. PlanError if there is none."""
    from bridge.object_memory import label_matches
    from bridge.sdk.perception_link import existing_link

    link = existing_link()
    report, age = link.latest_report() if link is not None else (None, None)
    if not report or age is None or age > FRESH_DETECTION_S:
        raise PlanError("no fresh detection report (perception offline or stale) — look first")
    best: dict[str, Any] | None = None
    for obj in report.get("objects") or []:
        if not isinstance(obj, dict) or not isinstance(obj.get("cam_xyz"), list):
            continue
        # The report can be fresh while the frame it repeats is not (age_s: since capture).
        if age + float(obj.get("age_s") or 0) > FRESH_DETECTION_S:
            continue
        if track_id is not None and obj.get("track_id") != track_id:
            continue
        if label and not label_matches(str(obj.get("label", "")), label):
            continue
        if best is None or float(obj.get("confidence") or 0) > float(best.get("confidence") or 0):
            best = obj
    if best is None:
        what = f"track {track_id}" if track_id is not None else repr(label)
        raise PlanError(
            f"{what} is not in the current detections with a 3-D point — "
            "turn toward it, or use find_object, then retry"
        )
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


# --- the four skills --------------------------------------------------------------------------


async def pick(
    label: str | None,
    side: str = "auto",
    shape: str = "power",
    track_id: int | None = None,
    ctx: Any | None = None,
) -> dict[str, Any]:
    if shape not in hands.SHAPES or shape == "open":
        return _refused(
            "pick", f"unknown_shape_{shape}", shapes=sorted(set(hands.SHAPES) - {"open"})
        )
    try:
        target, obj = detected_target(label, track_id)
        arm = choose_side(side, target)
        steps = plan_pick(arm, target, shape, _measured_q(arm))
    except PlanError as exc:
        return _refused("pick", str(exc))
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
