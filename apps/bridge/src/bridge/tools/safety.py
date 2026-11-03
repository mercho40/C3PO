"""`stop_everything` — safety layer 3 of docs/ARCHITECTURE.md §7."""

from __future__ import annotations

import structlog

from bridge.config import SIM_MODE
from bridge.server import mcp
from bridge.skill_meta import meta as skill_meta

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Tool: stop_everything
# ---------------------------------------------------------------------------


@mcp.tool(
    meta=skill_meta(
        classification="safety",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=0.5,
        works_sim=True,
        works_real=True,
        typical_failure_modes=["bridge_disconnected"],
    )
)
async def stop_everything() -> dict:
    """Halt all motion immediately and cancel any in-flight tasks.

    Safety-critical: cancels every running task in the registry (each skill
    observes the cancel signal between iterations and ramps down velocity)
    AND independently sends a zero-velocity burst to the run-command channel
    for ~0.4 s in case the policy is still in motion.

    Cancellation is signalled synchronously; only the blocking DDS calls are
    awaited off-thread, so a slow or degraded link cannot wedge the event loop
    and leave a second stop press unanswered (see the skill's own docstring).

    Stub mode is a no-op aside from logging.
    """
    log.warning("stop_everything.called", sim_mode=SIM_MODE)

    if SIM_MODE == "stub":
        return {
            "cancelled_task_ids": [],
            "cancelled_count": 0,
            "stop_burst_duration_s": 0.0,
            "env": SIM_MODE,
            "stub": True,
        }

    from bridge.skills.stop_everything import run as run_stop

    result = await run_stop()
    return {**result, "env": SIM_MODE}
