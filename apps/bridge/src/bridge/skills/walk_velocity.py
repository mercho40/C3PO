"""walk_velocity: open-loop body-frame velocity command for real G1 hardware.

Unlike walk_to/turn (blocked on real hardware — no world-frame pose source
wired yet, see README "Phase 1b"), this doesn't need pose at all. It's the
same fire-and-forget pattern xr_teleoperate's own controller-button
locomotion uses: send a body-frame (vx, vy, vyaw) for `duration_s`, the
firmware sustains it and stops on its own when the duration elapses
(confirmed via `unitree_sdk2py`'s LocoClient.Move()/SetVelocity — repeated
calls are how continuous motion is built up, not a single unbounded one).

Deliberately conservative: no pose feedback means no way to detect "did it
actually go where I meant" or "did it hit something" — so both velocity
magnitude and duration are hard-capped here, independent of whatever the
caller asks for. An LLM agent driving this blind should only ever move in
small, correctable increments.
"""

from __future__ import annotations

import asyncio
import time
from typing import Any

import structlog

from bridge.sdk import g1_rpc
from bridge.skills.task_runtime import get_registry

log = structlog.get_logger(__name__)

# Matches xr_teleoperate's own default safety cap on its controller-button
# locomotion scheme (0.3, see xr_teleoperate issue #135) — not a sim-tuned
# number, an actual precedent for "safe on this real hardware."
MAX_LINEAR_VEL = 0.3  # m/s, vx/vy
MAX_YAW_VEL = 0.3  # rad/s

# Single-call duration ceiling. No pose feedback exists to know when to stop
# early, so keep each blind command's "blast radius" small — an agent that
# wants sustained motion should call this repeatedly, checking get_state()
# and its own judgement between calls, not issue one long blind command.
MAX_DURATION_S = 3.0


def _clamp(value: float, limit: float) -> float:
    return max(-limit, min(limit, value))


async def run(
    vx: float,
    vy: float,
    vyaw: float,
    duration_s: float = 1.0,
    ctx: Any | None = None,
) -> dict[str, Any]:
    """Command a body-frame velocity for `duration_s`, clamped to safety limits.

    Returns the full task dict. Cancellable: `cancel_task`/`stop_everything`
    can interrupt mid-wait, in which case a zero-velocity SetVelocity is sent
    immediately rather than waiting out the original duration.
    """
    task = get_registry().create("walk_velocity")

    requested = {"vx": vx, "vy": vy, "vyaw": vyaw, "duration_s": duration_s}
    clamped_vx = _clamp(vx, MAX_LINEAR_VEL)
    clamped_vy = _clamp(vy, MAX_LINEAR_VEL)
    clamped_vyaw = _clamp(vyaw, MAX_YAW_VEL)
    clamped_duration = max(0.0, min(duration_s, MAX_DURATION_S))
    clamped = {
        "vx": clamped_vx,
        "vy": clamped_vy,
        "vyaw": clamped_vyaw,
        "duration_s": clamped_duration,
    }

    log.info("walk_velocity.start", task_id=task.task_id, requested=requested, clamped=clamped)

    try:
        code, data = await asyncio.to_thread(
            g1_rpc.call_velocity, clamped_vx, clamped_vy, clamped_vyaw, clamped_duration
        )
        if code != 0:
            task.status = "failed"
            task.phase = "rpc_error"
            task.error = f"rpc_error_code_{code}"
            task.ended_at = time.time()
            task.result = {"requested": requested, "clamped": clamped, "rpc_code": code, "rpc_data": data}
            return task.to_dict()

        task.phase = "moving"
        deadline = time.time() + clamped_duration
        cancelled = False
        while time.time() < deadline:
            if task.cancel_event.is_set():
                cancelled = True
                break
            task.progress = max(0.0, min(0.999, 1.0 - (deadline - time.time()) / max(clamped_duration, 1e-6)))
            await asyncio.sleep(0.05)

        if cancelled:
            # Firmware would stop on its own once clamped_duration elapses, but
            # don't wait it out — command zero velocity immediately.
            await asyncio.to_thread(g1_rpc.call_velocity, 0.0, 0.0, 0.0, 0.5)
            task.status = "cancelled"
            task.phase = "cancelled"
        else:
            task.status = "completed"
            task.phase = "duration_elapsed"
            task.progress = 1.0

        task.ended_at = time.time()
        task.result = {"requested": requested, "clamped": clamped, "rpc_code": code, "rpc_data": data}
        log.info(
            "walk_velocity.done",
            task_id=task.task_id,
            status=task.status,
            duration_s=round(task.ended_at - task.started_at, 2),
        )
        return task.to_dict()

    except Exception as exc:
        task.status = "failed"
        task.phase = "exception"
        task.error = repr(exc)
        task.ended_at = time.time()
        log.exception("walk_velocity.failed", task_id=task.task_id)
        try:
            await asyncio.to_thread(g1_rpc.call_velocity, 0.0, 0.0, 0.0, 0.5)
        except Exception:
            pass
        return task.to_dict()
