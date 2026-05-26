"""walk_to: drive the G1 toward a world-frame XY target until within stop_distance.

Uses Isaac Sim's velocity command channel `rt/run_command/cmd` (see
`_locomotion.py`). The walk policy is body-frame, so we rotate the
world-frame error into the robot's frame each loop iteration.

Integrates with `task_runtime.TaskRegistry`: cancellable via the task's
cancel event, reports progress through `ctx.report_progress` when invoked
through MCP. Returns the full `Task.to_dict()` shape.
"""

from __future__ import annotations

import math
import time
from typing import Any

import structlog

from bridge.skills._locomotion import (
    DEFAULT_HEIGHT,
    KP_LIN,
    KP_YAW,
    LOOP_PERIOD_S,
    MAX_BACK_VEL,
    MAX_FWD_VEL,
    MAX_LAT_VEL,
    MAX_YAW_VEL,
    maybe_report_progress,
    send_velocity,
    stop_motion,
)
from bridge.skills.task_runtime import get_registry

log = structlog.get_logger(__name__)


async def run(
    target_x: float,
    target_y: float,
    stop_distance_m: float = 1.0,
    timeout_s: float = 60.0,
    height: float = DEFAULT_HEIGHT,
    ctx: Any | None = None,
) -> dict[str, Any]:
    """Drive the G1 to (target_x, target_y) world-frame and stop near it.

    Returns the full task dict (see `Task.to_dict`).
    """
    import asyncio

    from bridge.sdk.state import get_sampler

    task = get_registry().create("walk_to")
    sampler = get_sampler()

    try:
        initial = sampler.get_state()
        pose = initial.get("pose")
        if pose is None:
            task.status = "failed"
            task.phase = "no_pose"
            task.error = "no pose available — sim_state hasn't been received"
            task.ended_at = time.time()
            log.warning("walk_to.no_pose", task_id=task.task_id)
            return task.to_dict()

        start_x = float(pose["x_meters_world"])
        start_y = float(pose["y_meters_world"])
        initial_distance = math.hypot(target_x - start_x, target_y - start_y)

        log.info(
            "walk_to.start",
            task_id=task.task_id,
            target=(target_x, target_y),
            start=(start_x, start_y),
            distance=initial_distance,
            stop_distance_m=stop_distance_m,
            timeout_s=timeout_s,
        )

        if initial_distance <= stop_distance_m:
            task.status = "completed"
            task.phase = "already_within_stop_distance"
            task.progress = 1.0
            task.ended_at = time.time()
            task.result = {
                "arrived": True,
                "initial_distance_m": initial_distance,
                "final_distance_m": initial_distance,
                "displacement_m": 0.0,
                "final_pose": {
                    "x_meters_world": start_x,
                    "y_meters_world": start_y,
                    "yaw_radians_world": float(pose["yaw_radians_world"]),
                },
            }
            await stop_motion(height)
            return task.to_dict()

        task.phase = "walking"
        deadline = time.time() + timeout_s
        last_reported = 0.0
        arrived = False
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

            x = float(pose["x_meters_world"])
            y = float(pose["y_meters_world"])
            yaw = float(pose["yaw_radians_world"])

            dx_world = target_x - x
            dy_world = target_y - y
            distance = math.hypot(dx_world, dy_world)
            if distance <= stop_distance_m:
                arrived = True
                break

            task.progress = max(0.0, min(0.999, 1.0 - distance / initial_distance))
            last_reported = await maybe_report_progress(
                ctx, task, f"{distance:.2f} m to go", last_reported
            )

            # Rotate world-frame error into body frame.
            cos_y = math.cos(yaw)
            sin_y = math.sin(yaw)
            dx_body = cos_y * dx_world + sin_y * dy_world
            dy_body = -sin_y * dx_world + cos_y * dy_world

            target_yaw = math.atan2(dy_world, dx_world)
            yaw_err = math.atan2(math.sin(target_yaw - yaw), math.cos(target_yaw - yaw))
            vyaw = max(-MAX_YAW_VEL, min(MAX_YAW_VEL, KP_YAW * yaw_err))

            # Yaw gate: slow forward velocity when not facing target.
            gate = max(0.0, math.cos(yaw_err))
            vx = max(MAX_BACK_VEL, min(MAX_FWD_VEL, KP_LIN * dx_body * gate))
            vy = max(-MAX_LAT_VEL, min(MAX_LAT_VEL, KP_LIN * dy_body * gate))

            send_velocity(vx, vy, vyaw, height)
            await asyncio.sleep(LOOP_PERIOD_S)

        task.phase = "stopping"
        await stop_motion(height)

        final = sampler.get_state().get("pose") or {}
        final_x = float(final.get("x_meters_world", 0.0))
        final_y = float(final.get("y_meters_world", 0.0))
        final_distance = math.hypot(target_x - final_x, target_y - final_y)

        if cancelled:
            task.status = "cancelled"
            task.phase = "cancelled"
        elif arrived:
            task.status = "completed"
            task.phase = "arrived"
            task.progress = 1.0
        else:
            task.status = "completed"
            task.phase = "timeout"

        task.ended_at = time.time()
        task.result = {
            "arrived": arrived,
            "initial_distance_m": initial_distance,
            "final_distance_m": final_distance,
            "displacement_m": math.hypot(final_x - start_x, final_y - start_y),
            "final_pose": {
                "x_meters_world": final_x,
                "y_meters_world": final_y,
                "yaw_radians_world": float(final.get("yaw_radians_world", 0.0)),
            },
        }

        log.info(
            "walk_to.done",
            task_id=task.task_id,
            status=task.status,
            phase=task.phase,
            final=(final_x, final_y),
            final_distance=final_distance,
            duration_s=round(task.ended_at - task.started_at, 2),
        )
        return task.to_dict()

    except Exception as exc:
        task.status = "failed"
        task.phase = "exception"
        task.error = repr(exc)
        task.ended_at = time.time()
        log.exception("walk_to.failed", task_id=task.task_id)
        try:
            await stop_motion(height)
        except Exception:
            pass
        return task.to_dict()
