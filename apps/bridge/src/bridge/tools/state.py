"""get_state: the robot's pose, battery, posture and faults, and the operator's liveness poll."""

from __future__ import annotations


import structlog

from bridge import config, watchdog
from bridge.server import mcp
from bridge.skill_meta import meta as skill_meta

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Tool: get_state
# ---------------------------------------------------------------------------


@mcp.tool(
    meta=skill_meta(
        classification="introspection",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=0.05,
        works_sim=True,
        works_real=True,
        typical_failure_modes=["bridge_disconnected", "no_state_received_yet"],
    )
)
def get_state() -> dict:
    """Return the robot's current state: pose, battery, posture, faults.

    Behaviour by mode:
    - `stub`: returns hardcoded fake state for wiring validation.
    - `isaac` / `real`: composes the latest message from each of three
      subscriptions — lowstate, pose, and battery — plus the polled FSM id.

    Nulls are "not received yet", never "known to be fine". `battery_pct` is
    null on sim because Isaac publishes no BMS at all, and `fsm_id`/`posture`
    go null on real when **no motion controller is loaded** — check
    `motion_switcher` CheckMode before concluding the robot is broken
    (`docs/ROBOT-API.md` §3).
    """
    log.info("get_state.called", sim_mode=config.SIM_MODE)
    # Operator liveness. `get_state` is the poll an operator (or the console's
    # own 2s loop) runs while watching the robot, which is exactly the signal
    # watchdog.py's docstring nominates as meaningful. Without a call like
    # this, `_last_contact` is only ever set in `start()` and the watchdog
    # measures its own uptime instead of the link -- so with LINK_WATCHDOG=on
    # it would safe-stop a healthy session as soon as any task outlived
    # LINK_TIMEOUT_S, then latch inert with nothing left to re-arm it.
    watchdog.get_watchdog().touch()

    if config.SIM_MODE == "stub":
        return {
            "pose": {
                "x_meters_world": 0.0,
                "y_meters_world": 0.0,
                "yaw_radians_world": 0.0,
            },
            "battery_pct": 87.0,
            "posture": "standing",
            "faults": [],
            "env": config.SIM_MODE,
            "stub": True,
        }

    from bridge.sdk.state import get_sampler

    state = get_sampler().get_state()
    return {**state, "env": config.SIM_MODE, "stub": False}
