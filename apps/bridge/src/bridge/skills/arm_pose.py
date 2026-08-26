"""move_arm: pose the arms joint-by-joint through `rt/arm_sdk`.

The preset gestures (`gesture.py`) can only play the firmware's canned
animations. This skill is the free-form counterpart: it drives individual
shoulder/elbow/wrist joints to requested angles by blending into the running
motion controller over `rt/arm_sdk` — the same path, driver and safety
envelope the VR teleop uses (`bridge.teleop.arm_sdk.ArmSdkDriver`).

Everything protective lives in the driver and is inherited wholesale, not
reimplemented: the measured-pose gapless engage, the 2 s weight ramp, the
0.6 rad/s slew limit, the jump-confirm filter, the stale-lowstate and FSM
gates, the gesture-contention refusal, and the double enablement switch
(`TELEOP_ARM_ENABLED=1` **and** `SIM_MODE=real` — see that module's docstring
for why an env var is not enough on this path). This module only decides
*where* the arm should go and *when* it has arrived.

Angles are given in the retargeting map's **semantic** convention — identical
left and right, positive roll = away from the body — and clamped to
`retarget.LIMITS`, the deliberately-tight software envelope. The per-robot
wiring signs (`retarget.JOINT_SIGNS`, measured for the right shoulder/elbow on
2026-08-20, inferred elsewhere) are applied at the last moment, so a future
sign correction lands in exactly one place.

The driver freezes its target when frames stop arriving (its dead-man), so
this skill keeps re-sending the target at ~20 Hz until the slew settles rather
than commanding once and hoping. That repetition is also what satisfies the
driver's jump-confirm filter for large moves.

`hold=True` (the default) leaves the driver engaged holding the pose — the
arm stays under software control until `release_arm_control`, another
`move_arm`, or `stop_everything`. `hold=False` ramps authority back to the
built-in controller once the pose is reached.
"""

from __future__ import annotations

import asyncio
import math
import os
import time
from typing import Any, Literal

import structlog

from bridge.skills.task_runtime import get_registry
from bridge.teleop import arm_sdk
from bridge.teleop.retarget import JOINT_NAMES, JOINT_SIGNS, LIMITS, ArmAngles, clamp

log = structlog.get_logger(__name__)

SIM_MODE = os.environ.get("SIM_MODE", "stub")

Side = Literal["left", "right", "both"]

#: Settled when every commanded joint is within this of its target. The
#: driver's slew moves 0.012 rad per 50 Hz tick, so one tick of slack.
SETTLE_TOLERANCE_RAD = 0.02

#: How often the target is re-sent while waiting. Well inside the driver's
#: FRAME_TIMEOUT_S (0.5 s) so the dead-man never freezes a live move.
COMMAND_HZ = 20

#: Headroom past the physics: weight ramp + worst-case slew + margin.
SETTLE_MARGIN_S = 3.0


def semantic_to_wiring(side: Literal["left", "right"], joints_rad: dict[str, float]) -> dict[str, float]:
    """Clamp semantic angles to LIMITS and apply this robot's wiring signs.

    Pure, and exported for tests: this is the one transformation between what
    the caller asked for and what goes on the wire, so it is the one to pin.
    """
    wiring: dict[str, float] = {}
    for name, angle in joints_rad.items():
        low, high = LIMITS[name]
        wiring[name] = JOINT_SIGNS[side][name] * clamp(angle, low, high)
    return wiring


def _compose_targets(
    side: Side, joints_rad: dict[str, float], base: tuple[float, ...]
) -> tuple[ArmAngles | None, ArmAngles | None, dict[str, dict[str, float]]]:
    """Build per-arm wiring-frame targets over the driver's current targets.

    `base` is the driver's 14-value target tuple (left then right, wiring
    frame). Joints the caller did not name keep their current target, so a
    partial command moves only what it names.
    """
    per_side: dict[str, dict[str, float]] = {}
    left: ArmAngles | None = None
    right: ArmAngles | None = None

    if side in ("left", "both"):
        values = list(base[0:7])
        wiring = semantic_to_wiring("left", joints_rad)
        for name, value in wiring.items():
            values[JOINT_NAMES.index(name)] = value
        left = ArmAngles(*values)
        per_side["left"] = wiring
    if side in ("right", "both"):
        values = list(base[7:14])
        wiring = semantic_to_wiring("right", joints_rad)
        for name, value in wiring.items():
            values[JOINT_NAMES.index(name)] = value
        right = ArmAngles(*values)
        per_side["right"] = wiring
    return left, right, per_side


def _clamped_semantic(joints_rad: dict[str, float]) -> dict[str, float]:
    return {name: clamp(angle, *LIMITS[name]) for name, angle in joints_rad.items()}


async def run(
    side: Side,
    joints_deg: dict[str, float],
    hold: bool = True,
    ctx: Any | None = None,
) -> dict[str, Any]:
    """Slew the named joints to the given angles. Returns the task dict.

    `joints_deg` maps `retarget.JOINT_NAMES` entries to degrees in the
    semantic convention. Cancellable: a cancel (or stop_everything) hands the
    arms back to the built-in controller via the driver's ramp-down.
    """
    task = get_registry().create("move_arm")

    unknown = sorted(set(joints_deg) - set(JOINT_NAMES))
    if unknown or not joints_deg:
        task.status = "failed"
        task.phase = "bad_arguments"
        task.error = f"unknown_joints_{unknown}" if unknown else "no_joints_given"
        task.result = {"valid_joints": list(JOINT_NAMES)}
        task.ended_at = time.time()
        return task.to_dict()

    joints_rad = {name: math.radians(deg) for name, deg in joints_deg.items()}
    clamped_deg = {
        name: round(math.degrees(angle), 2)
        for name, angle in _clamped_semantic(joints_rad).items()
    }

    log.info(
        "move_arm.requested",
        task_id=task.task_id,
        side=side,
        joints_deg=joints_deg,
        clamped_deg=clamped_deg,
        hold=hold,
        sim_mode=SIM_MODE,
    )

    if SIM_MODE == "stub":
        task.status = "completed"
        task.phase = "stub"
        task.progress = 1.0
        task.result = {
            "side": side,
            "commanded_deg": clamped_deg,
            "hold": hold,
            "note": "Stub mode — no dispatch.",
        }
        task.ended_at = time.time()
        return task.to_dict()

    driver = arm_sdk.get_driver()
    try:
        # In any non-real mode the driver's own preconditions refuse with the
        # right message ("publishing there would be a silent no-op…"), so
        # there is deliberately no logged_only branch here — unlike the RPC
        # skills, nothing subscribes rt/arm_sdk in sim and pretending to
        # dispatch would claim motion that cannot happen.
        await driver.engage()
    except arm_sdk.ArmSdkUnavailable as exc:
        task.status = "failed"
        task.phase = "unavailable"
        task.error = str(exc)
        task.result = {"side": side, "commanded_deg": clamped_deg}
        task.ended_at = time.time()
        return task.to_dict()

    left, right, per_side_wiring = _compose_targets(side, joints_rad, driver.target_angles)
    wiring_targets = {
        arm: {name: round(value, 4) for name, value in wiring.items()}
        for arm, wiring in per_side_wiring.items()
    }

    # Worst-case travel decides the deadline: the slowest joint moves at the
    # driver's slew rate from wherever it is now.
    current = driver.current_angles
    deltas = []
    for angles, base_index in ((left, 0), (right, 7)):
        if angles is None:
            continue
        for i, target in enumerate(angles.as_tuple()):
            deltas.append(abs(target - current[base_index + i]))
    max_delta = max(deltas, default=0.0)
    deadline = (
        time.monotonic()
        + arm_sdk.RAMP_S
        + max_delta / arm_sdk.MAX_JOINT_RATE_RAD_S
        + SETTLE_MARGIN_S
    )

    period = 1.0 / COMMAND_HZ
    try:
        while True:
            if task.cancel_event.is_set():
                driver.request_release()
                task.status = "cancelled"
                task.phase = "cancelled"
                task.result = {
                    "side": side,
                    "commanded_deg": clamped_deg,
                    "wiring_targets_rad": wiring_targets,
                    "note": "Cancelled mid-slew; arm authority is ramping back down.",
                }
                task.ended_at = time.time()
                return task.to_dict()

            driver.command(left, right)

            settled = True
            now = driver.current_angles
            for angles, base_index in ((left, 0), (right, 7)):
                if angles is None:
                    continue
                for i, target in enumerate(angles.as_tuple()):
                    if abs(target - now[base_index + i]) > SETTLE_TOLERANCE_RAD:
                        settled = False
                        break
                if not settled:
                    break

            if settled and driver.weight >= 1.0:
                break

            if not driver.engaged:
                # The publish loop died underneath us (its failure latch has
                # the details). Do not report a pose that was never held.
                task.status = "failed"
                task.phase = "driver_failed"
                task.error = driver.status().get("failed") or "arm_sdk_disengaged"
                task.result = {"side": side, "commanded_deg": clamped_deg}
                task.ended_at = time.time()
                return task.to_dict()

            if time.monotonic() > deadline:
                driver.request_release()
                task.status = "failed"
                task.phase = "settle_timeout"
                task.error = "settle_timeout"
                task.result = {
                    "side": side,
                    "commanded_deg": clamped_deg,
                    "wiring_targets_rad": wiring_targets,
                    "note": (
                        "The slew never settled inside its deadline; authority "
                        "is being handed back to the built-in controller."
                    ),
                }
                task.ended_at = time.time()
                return task.to_dict()

            if ctx is not None and max_delta > 0:
                try:
                    remaining = max(
                        abs(t - driver.current_angles[b + i])
                        for angles, b in ((left, 0), (right, 7))
                        if angles is not None
                        for i, t in enumerate(angles.as_tuple())
                    )
                    await ctx.report_progress(
                        progress=1.0 - min(1.0, remaining / max_delta),
                        total=1.0,
                        message="slewing",
                    )
                except Exception:
                    pass  # progress is best-effort

            await asyncio.sleep(period)

        if not hold:
            await driver.release()

        task.status = "completed"
        task.phase = "holding" if hold else "released"
        task.progress = 1.0
        task.result = {
            "side": side,
            "commanded_deg": clamped_deg,
            "wiring_targets_rad": wiring_targets,
            "holding": hold,
            "note": (
                "Arms remain under rt/arm_sdk control — release_arm_control, "
                "another move_arm, or stop_everything hands them back."
                if hold
                else "Authority ramped back to the built-in controller."
            ),
        }
        task.ended_at = time.time()
        return task.to_dict()

    except asyncio.CancelledError:
        # External cancellation (client gone, FastMCP timeout). Same reasoning
        # as dance.py: mark the task so the registry can reap it, start the
        # ramp-down, and never swallow the cancellation itself.
        driver.request_release()
        task.status = "cancelled"
        task.phase = "cancelled"
        task.ended_at = time.time()
        log.info("move_arm.cancelled", task_id=task.task_id)
        raise

    except Exception as exc:
        driver.request_release()
        task.status = "failed"
        task.phase = "exception"
        task.error = repr(exc)
        task.ended_at = time.time()
        log.exception("move_arm.failed", task_id=task.task_id)
        return task.to_dict()


async def release(ctx: Any | None = None) -> dict[str, Any]:
    """Hand the arms back to the built-in controller (the `hold=True` escape).

    Safe to call when nothing is engaged; also clears the driver's failure
    latch, which is the documented "let go and ask again" reset.
    """
    driver = arm_sdk.get_driver()
    was_engaged = driver.engaged
    if SIM_MODE == "stub":
        return {"status": "ok", "was_engaged": was_engaged, "stub": True}

    await driver.release()
    driver.clear_failure()
    return {
        "status": "ok",
        "was_engaged": was_engaged,
        "weight": driver.weight,
        "stub": False,
    }
