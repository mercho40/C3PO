"""The two stops: stop_everything (latches PARAR, lets go of everything) and soft_stop.

Classification "safety", so both are T0 (skill_meta.derive_tier): GovernedMCP lets
them through a standing PARAR, the face gate never gates them, and apps/back's
admin gate lets anyone press them. The spoken stop in routes/voice.py calls
`stop_everything` here, the same function the MCP tool runs.
"""

from __future__ import annotations

import structlog

from bridge import config
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
    log.warning("stop_everything.called", sim_mode=config.SIM_MODE)

    if config.SIM_MODE == "stub":
        return {
            "cancelled_task_ids": [],
            "cancelled_count": 0,
            "stop_burst_duration_s": 0.0,
            "env": config.SIM_MODE,
            "stub": True,
        }

    from bridge.skills.stop_everything import run as run_stop

    result = await run_stop()
    return {**result, "env": config.SIM_MODE}


@mcp.tool(
    meta=skill_meta(
        classification="safety",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=0.5,
        works_sim=True,
        works_real=False,  # The burst is stop_everything's, observed on the
        # robot; this tool as a whole has not been run there yet.
        typical_failure_modes=["burst_unacknowledged", "bridge_disconnected"],
    )
)
async def soft_stop() -> dict:
    """Stop walking and stay standing — zero velocity, never goes limp.

    Closes the Nav2 gate, cancels the Nav2 goal and running tasks, and sends a
    ~0.4 s zero-velocity burst. Unlike stop_everything it does not latch the e-stop, does not let go
    of the arms or hands, and never damps — so it cannot drop the robot. Use it
    to end a walk. If the result says `heard: false`, the robot did not
    acknowledge the stop: call stop_everything.
    """
    log.warning("soft_stop.called", sim_mode=config.SIM_MODE)
    if config.SIM_MODE == "stub":
        return {"cancelled_task_ids": [], "cancelled_count": 0, "env": config.SIM_MODE, "stub": True}

    from bridge.skills.stop_everything import soft_stop as run_soft_stop

    return {**await run_soft_stop(), "env": config.SIM_MODE}
