"""turn: rotate the G1 in place by a yaw delta.

Pure rotation — sends `vyaw` to `rt/run_command/cmd` with zero linear
velocity. Same task-lifecycle shape as `walk_to`: cancellable via the
TaskRegistry, reports progress through `ctx.report_progress`.

Sign convention: positive `delta_yaw_radians` = counterclockwise (left turn)
from the robot's point of view, matching world-frame +yaw.

The walk policy responds quickly to yaw commands and slows as error
shrinks; we use a tighter tolerance (~3°) than walk_to's stop_distance.
"""

from __future__ import annotations

import asyncio
import math
import time
from typing import Any

import structlog

from bridge.skills._locomotion import (
    DEFAULT_HEIGHT,
    KP_YAW,
    LOOP_PERIOD_S,
    MAX_YAW_VEL,
    maybe_report_progress,
    teleop_conflict,
    send_velocity_async,
    stop_motion,
)
from bridge.skills.task_runtime import get_registry

log = structlog.get_logger(__name__)

DEFAULT_TOLERANCE_RAD = math.radians(3)  # ~3°

#: Consecutive in-tolerance samples, with no yaw commanded, before declaring
#: arrival.
#:
#: MEASURED 2026-08-26. The loop reported `reached` and the body settled 3.41°
#: from target on a 3° tolerance. `reached` was decided from ONE pose sample,
#: and leg odometry is noisy: a single reading dipping inside the band ended
#: the loop while the robot was really outside it.
#:
#: Confirming over several samples with the yaw command at ZERO asks the
#: question this skill actually claims to answer — not "was it briefly inside
#: the band" but "is it inside the band and no longer moving". That is what
#: works_real asserts for `turn`, so it is worth the samples.
STOP_CONFIRM_SAMPLES = 3


async def run(
    delta_yaw_radians: float,
    timeout_s: float = 30.0,
    tolerance_radians: float = DEFAULT_TOLERANCE_RAD,
    height: float = DEFAULT_HEIGHT,
    ctx: Any | None = None,
) -> dict[str, Any]:
    """Rotate by `delta_yaw_radians` (positive = left/CCW) and stop.

    Returns the full task dict.
    """
    from bridge.sdk.state import get_sampler

    task = get_registry().create("turn")

    # Checked before the sampler is built, because building one initialises
    # DDS — and a skill that is about to refuse has no business opening a
    # connection to the robot in order to say no. It also means this still
    # answers correctly on a machine whose DDS stack is not up, which is
    # exactly the situation someone is in when they are working out why
    # nothing moves.
    conflict = teleop_conflict()
    if conflict is not None:
        task.status = "failed"
        task.phase = "teleop_active"
        task.error = conflict
        task.ended_at = time.time()
        log.warning("turn.teleop_active", task_id=task.task_id)
        return task.to_dict()

    sampler = get_sampler()

    try:
        conflict = teleop_conflict()
        if conflict is not None:
            task.status = "failed"
            task.phase = "teleop_active"
            task.error = conflict
            task.ended_at = time.time()
            log.warning("turn.teleop_active", task_id=task.task_id)
            return task.to_dict()

        initial = sampler.get_state()
        pose = initial.get("pose")
        if pose is None:
            task.status = "failed"
            task.phase = "no_pose"
            task.error = "no pose available — sim_state hasn't been received"
            task.ended_at = time.time()
            log.warning("turn.no_pose", task_id=task.task_id)
            return task.to_dict()

        start_yaw = float(pose["yaw_radians_world"])
        target_yaw = start_yaw + delta_yaw_radians
        # Wrap the displayed target into [-π, π] for logs / results.
        target_yaw_wrapped = math.atan2(math.sin(target_yaw), math.cos(target_yaw))

        # Use the absolute delta for progress denominator (avoids div-by-zero on tiny deltas).
        delta_magnitude = max(abs(delta_yaw_radians), tolerance_radians)

        log.info(
            "turn.start",
            task_id=task.task_id,
            delta_yaw_degrees=math.degrees(delta_yaw_radians),
            start_yaw_degrees=math.degrees(start_yaw),
            target_yaw_degrees=math.degrees(target_yaw_wrapped),
            tolerance_degrees=math.degrees(tolerance_radians),
            timeout_s=timeout_s,
        )

        task.phase = "turning"
        deadline = time.time() + timeout_s
        last_reported = 0.0
        reached = False
        cancelled = False
        in_tolerance = 0

        while time.time() < deadline:
            if task.cancel_event.is_set():
                cancelled = True
                break

            state = sampler.get_state()
            pose = state.get("pose")
            if pose is None:
                await asyncio.sleep(LOOP_PERIOD_S)
                continue

            yaw = float(pose["yaw_radians_world"])
            err = math.atan2(math.sin(target_yaw - yaw), math.cos(target_yaw - yaw))
            if abs(err) <= tolerance_radians:
                # Inside the band: stop steering and confirm it holds. Commanding
                # zero rather than simply not commanding is deliberate — the
                # firmware keeps the last setpoint for up to a second, so
                # falling silent here would let the body coast through the band
                # and out the other side while we congratulated ourselves.
                in_tolerance += 1
                if in_tolerance >= STOP_CONFIRM_SAMPLES:
                    reached = True
                    break
                await send_velocity_async(0.0, 0.0, 0.0, height)
                await asyncio.sleep(LOOP_PERIOD_S)
                continue

            # Left the band again — a single sample proved nothing.
            in_tolerance = 0

            task.progress = max(0.0, min(0.999, 1.0 - abs(err) / delta_magnitude))
            last_reported = await maybe_report_progress(
                ctx,
                task,
                f"{math.degrees(err):+.1f}° to go",
                last_reported,
            )

            vyaw = max(-MAX_YAW_VEL, min(MAX_YAW_VEL, KP_YAW * err))
            await send_velocity_async(0.0, 0.0, vyaw, height)
            await asyncio.sleep(LOOP_PERIOD_S)

        task.phase = "stopping"
        await stop_motion(height)

        final = sampler.get_state().get("pose") or {}
        final_yaw = float(final.get("yaw_radians_world", start_yaw))
        final_err = math.atan2(math.sin(target_yaw - final_yaw), math.cos(target_yaw - final_yaw))

        if cancelled:
            task.status = "cancelled"
            task.phase = "cancelled"
        elif reached:
            task.status = "completed"
            task.phase = "reached"
            task.progress = 1.0
        else:
            task.status = "completed"
            task.phase = "timeout"

        task.ended_at = time.time()
        task.result = {
            "reached": reached,
            "start_yaw_radians_world": start_yaw,
            "target_yaw_radians_world": target_yaw_wrapped,
            "final_yaw_radians_world": final_yaw,
            "final_yaw_error_radians": final_err,
            "delta_yaw_radians_commanded": delta_yaw_radians,
        }

        log.info(
            "turn.done",
            task_id=task.task_id,
            status=task.status,
            phase=task.phase,
            final_yaw_degrees=math.degrees(final_yaw),
            final_err_degrees=math.degrees(final_err),
            duration_s=round(task.ended_at - task.started_at, 2),
        )
        return task.to_dict()

    except asyncio.CancelledError:
        # See walk_to's matching branch: CancelledError is a BaseException, so
        # `except Exception` misses it, the stop sequence is skipped, and the
        # task stays "running" forever in a registry that only reaps finished
        # ones.
        task.status = "cancelled"
        task.phase = "cancelled"
        task.ended_at = time.time()
        log.info("turn.cancelled", task_id=task.task_id)
        try:
            await asyncio.shield(asyncio.ensure_future(stop_motion(height)))
        except (Exception, asyncio.CancelledError):
            pass
        raise  # never swallow cancellation

    except Exception as exc:
        task.status = "failed"
        task.phase = "exception"
        task.error = repr(exc)
        task.ended_at = time.time()
        log.exception("turn.failed", task_id=task.task_id)
        try:
            await stop_motion(height)
        except Exception:
            pass
        return task.to_dict()
