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

**Cancellation happens synchronously, before anything can block.** Every
`cancel_event` is set at the top of `run()` with no awaits in between, so the
skills begin stopping themselves immediately regardless of what the RPC path
does afterwards.

**The blocking RPCs then run off the event loop, and this used to be wrong.**
This function was fully synchronous, justified by "with stdio MCP, no
concurrent tools can run anyway". That justification expired: onboard the G1
the bridge serves streamable-HTTP in a single process (`run_c3po` sets
`BRIDGE_TRANSPORT=http`), where concurrent requests are exactly what happens.
Meanwhile the Damp fallback below can retry 3x against a 10 s client timeout,
so a degraded link — the case the retry exists for — could wedge the whole
event loop for ~30 s. During that window a second stop press and every
`get_state` poll went unanswered: the e-stop made the robot unreachable
precisely when the operator was pressing it again. The RPCs now go through
`asyncio.to_thread`, and the retry loop carries a total time budget on top.

The real-hardware Damp fallback retries up to REAL_DAMP_MAX_ATTEMPTS times on
RPC failure (a dropped packet shouldn't mean a silently-failed e-stop), but
stops retrying once REAL_DAMP_TOTAL_BUDGET_S has elapsed. The sim-mode path
(task cancellation + zero-velocity burst) stays fast and single-shot.

Returns a list of cancelled task IDs and the duration of the stop burst.
"""

from __future__ import annotations

import asyncio
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
# Ceiling on the whole retry loop. Each attempt can sit on the sport client's
# 10 s timeout, so attempt-count alone bounds this at ~30 s — far too long for
# the call an operator makes when something is going wrong. Past this budget we
# stop retrying and report honestly rather than keep trying in silence.
REAL_DAMP_TOTAL_BUDGET_S = 4.0

SIM_MODE = os.environ.get("SIM_MODE", "stub")


async def run(height: float = DEFAULT_HEIGHT) -> dict[str, Any]:
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

    # Off-thread from here down. Every cancel_event above is already set, so
    # the skills are stopping themselves whatever happens next; what this buys
    # is an event loop that can still answer a second stop press or a state
    # poll while these blocking DDS calls are in flight.
    start = time.time()
    await asyncio.to_thread(
        stop_motion_sync, height=height, duration_s=STOP_BURST_DURATION_S
    )
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

        deadline = time.time() + REAL_DAMP_TOTAL_BUDGET_S
        budget_exhausted = False
        for attempt in range(1, REAL_DAMP_MAX_ATTEMPTS + 1):
            real_damp_attempts = attempt
            real_damp_rpc_code, _ = await asyncio.to_thread(
                g1_rpc.call_sport, g1_protocol.Mode.DAMP
            )
            if real_damp_rpc_code == 0:
                break
            log.warning(
                "stop_everything.real_damp_fallback.retry",
                attempt=attempt,
                rpc_code=real_damp_rpc_code,
            )
            if time.time() >= deadline:
                # One slow attempt can consume the whole budget on its own.
                budget_exhausted = True
                log.warning(
                    "stop_everything.real_damp_fallback.budget_exhausted",
                    attempts=real_damp_attempts,
                    budget_s=REAL_DAMP_TOTAL_BUDGET_S,
                )
                break
            if attempt < REAL_DAMP_MAX_ATTEMPTS:
                await asyncio.sleep(REAL_DAMP_RETRY_DELAY_S)
        log.warning(
            "stop_everything.real_damp_fallback",
            rpc_code=real_damp_rpc_code,
            attempts=real_damp_attempts,
            succeeded=real_damp_rpc_code == 0,
            budget_exhausted=budget_exhausted,
        )

    return {
        "cancelled_task_ids": cancelled_ids,
        "cancelled_count": len(cancelled_ids),
        "stop_burst_duration_s": round(duration, 3),
        "real_damp_fallback_rpc_code": real_damp_rpc_code,
        "real_damp_fallback_attempts": real_damp_attempts,
        "real_damp_fallback_succeeded": (real_damp_rpc_code == 0) if SIM_MODE == "real" else None,
    }
