"""stop_everything: halt all motion and cancel any in-flight tasks.

Safety-critical and fast (<1s). Two parts:
  1. Signal cancellation to every running task in the TaskRegistry. The
     skills observe `cancel_event` between iterations and stop motion
     themselves before returning.
  2. Independently send a zero-velocity burst to `rt/run_command/cmd` for
     ~0.4 s, in case no skill is currently looping (e.g. the policy is
     still moving from the last command) or in case the registry is out
     of sync with what's actually publishing.

Synchronous because it's safety-critical — we don't want to yield the
event loop while halting the robot. With stdio MCP, no concurrent tools
can run anyway, so blocking the loop briefly is fine.

Returns a list of cancelled task IDs and the duration of the stop burst.
"""

from __future__ import annotations

import os
import time
from typing import Any

import structlog

from bridge.skills._locomotion import DEFAULT_HEIGHT, stop_motion_sync
from bridge.skills.task_runtime import get_registry

log = structlog.get_logger(__name__)

STOP_BURST_DURATION_S = 0.4

SIM_MODE = os.environ.get("SIM_MODE", "stub")


def run(height: float = DEFAULT_HEIGHT) -> dict[str, Any]:
    """Cancel all running tasks and send a zero-velocity burst."""
    registry = get_registry()
    active = registry.list_active()
    cancelled_ids: list[str] = []
    for task in active:
        if registry.cancel(task.task_id):
            cancelled_ids.append(task.task_id)

    log.warning(
        "stop_everything.requested",
        cancelled_count=len(cancelled_ids),
        cancelled_task_ids=cancelled_ids,
    )

    start = time.time()
    stop_motion_sync(height=height, duration_s=STOP_BURST_DURATION_S)
    duration = time.time() - start

    # `stop_motion_sync` above now dispatches per SIM_MODE, so on real hardware
    # it issues SET_VELOCITY(0,0,0) rather than the old sim-only no-op. Damp is
    # still sent after it, and is not redundant: zero velocity stops the gait,
    # whereas damp (verified live, see g1_rpc) zeroes joint stiffness. Belt and
    # braces on the one call that exists to make the robot stop.
    real_damp_rpc_code: int | None = None
    if SIM_MODE == "real":
        from bridge.sdk import g1_protocol, g1_rpc

        real_damp_rpc_code, _ = g1_rpc.call_sport(g1_protocol.Mode.DAMP)
        log.warning("stop_everything.real_damp_fallback", rpc_code=real_damp_rpc_code)

    return {
        "cancelled_task_ids": cancelled_ids,
        "cancelled_count": len(cancelled_ids),
        "stop_burst_duration_s": round(duration, 3),
        "real_damp_fallback_rpc_code": real_damp_rpc_code,
    }
