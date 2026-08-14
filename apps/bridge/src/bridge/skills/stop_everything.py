"""stop_everything: halt all motion and cancel any in-flight tasks.

Safety-critical, and fast in the common case (<1s). Two parts:
  1. Signal cancellation to every running task in the TaskRegistry. The
     skills observe `cancel_event` between iterations and stop motion
     themselves before returning.
  2. Independently send a zero-velocity burst for ~0.4 s via
     `_locomotion.stop_motion_sync`, in case no skill is currently looping
     (e.g. the policy is still moving from the last command) or in case
     the registry is out of sync with what's actually publishing. This
     goes out `rt/run_command/cmd` in sim, or as a real `SET_VELOCITY` RPC
     burst on real hardware (`_locomotion.send_velocity` dispatches on
     `SIM_MODE` — see that module) — real mode gets this AND the Damp
     fallback below, not just one or the other.

Synchronous because it's safety-critical — we don't want to yield the
event loop while halting the robot. With stdio MCP, no concurrent tools
can run anyway, so blocking the loop briefly is fine.

The real-hardware Damp fallback retries up to REAL_DAMP_MAX_ATTEMPTS times
on RPC failure (a dropped packet shouldn't mean a silently-failed e-stop) —
worst case, that pushes real-mode latency past the "<1s" figure above. The
sim-mode path (task cancellation + zero-velocity burst) stays fast and
single-shot regardless.

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
# Real-hardware Damp fallback is a single RPC over DDS with no built-in
# retry at the transport layer — unlike the sim burst above, a dropped
# packet here means the e-stop silently did nothing. Retry a few times
# with a short pause rather than trust one packet to land.
REAL_DAMP_MAX_ATTEMPTS = 3
REAL_DAMP_RETRY_DELAY_S = 0.15

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
    real_damp_attempts = 0
    if SIM_MODE == "real":
        from bridge.sdk import g1_protocol, g1_rpc

        for attempt in range(1, REAL_DAMP_MAX_ATTEMPTS + 1):
            real_damp_attempts = attempt
            real_damp_rpc_code, _ = g1_rpc.call_sport(g1_protocol.Mode.DAMP)
            if real_damp_rpc_code == 0:
                break
            log.warning(
                "stop_everything.real_damp_fallback.retry",
                attempt=attempt,
                rpc_code=real_damp_rpc_code,
            )
            if attempt < REAL_DAMP_MAX_ATTEMPTS:
                time.sleep(REAL_DAMP_RETRY_DELAY_S)
        log.warning(
            "stop_everything.real_damp_fallback",
            rpc_code=real_damp_rpc_code,
            attempts=real_damp_attempts,
            succeeded=real_damp_rpc_code == 0,
        )

    return {
        "cancelled_task_ids": cancelled_ids,
        "cancelled_count": len(cancelled_ids),
        "stop_burst_duration_s": round(duration, 3),
        "real_damp_fallback_rpc_code": real_damp_rpc_code,
        "real_damp_fallback_attempts": real_damp_attempts,
        "real_damp_fallback_succeeded": (real_damp_rpc_code == 0) if SIM_MODE == "real" else None,
    }
