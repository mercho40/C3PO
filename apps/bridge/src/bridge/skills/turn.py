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
    send_velocity,
    stop_motion,
)
from bridge.skills.task_runtime import get_registry

log = structlog.get_logger(__name__)

DEFAULT_TOLERANCE_RAD = math.radians(3)  # ~3°


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
    sampler = get_sampler()

    try:
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
                reached = True
                break

            task.progress = max(0.0, min(0.999, 1.0 - abs(err) / delta_magnitude))
            last_reported = await maybe_report_progress(
                ctx,
                task,
                f"{math.degrees(err):+.1f}° to go",
                last_reported,
            )

            vyaw = max(-MAX_YAW_VEL, min(MAX_YAW_VEL, KP_YAW * err))
            send_velocity(0.0, 0.0, vyaw, height)
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
