"""Introspection tools: what the robot is doing and which controller is loaded."""

from __future__ import annotations

import structlog

from bridge.config import SIM_MODE
from bridge.server import mcp
from bridge.skill_meta import meta as skill_meta
from bridge.watchdog import get_watchdog

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
    log.info("get_state.called", sim_mode=SIM_MODE)
    # Operator liveness. `get_state` is the poll an operator (or the console's
    # own 2s loop) runs while watching the robot, which is exactly the signal
    # watchdog.py's docstring nominates as meaningful. Without a call like
    # this, `_last_contact` is only ever set in `start()` and the watchdog
    # measures its own uptime instead of the link -- so with LINK_WATCHDOG=on
    # it would safe-stop a healthy session as soon as any task outlived
    # LINK_TIMEOUT_S, then latch inert with nothing left to re-arm it.
    get_watchdog().touch()

    if SIM_MODE == "stub":
        return {
            "pose": {
                "x_meters_world": 0.0,
                "y_meters_world": 0.0,
                "yaw_radians_world": 0.0,
            },
            "battery_pct": 87.0,
            "posture": "standing",
            "faults": [],
            "env": SIM_MODE,
            "stub": True,
        }

    from bridge.sdk.state import get_sampler

    state = get_sampler().get_state()
    return {**state, "env": SIM_MODE, "stub": False}


@mcp.tool(
    meta=skill_meta(
        classification="introspection",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=0.1,
        works_sim=False,
        works_real=False,
        typical_failure_modes=["bridge_disconnected", "motion_switcher_no_answer"],
    )
)
async def check_motion_mode() -> dict:
    """Which motion controller currently owns the robot. Read-only.

    **Run this first whenever the robot accepts commands and does nothing.**

    The sport service answers `code 0` both when an FSM transition is refused
    and when there is no controller loaded to perform it, so those two very
    different situations are indistinguishable from the reply alone. This tool
    tells them apart in one call.

    Returns `mode_name`. Empty means **no controller is loaded** — the robot is
    in debug mode, nothing will move, and `get_state` will report `fsm_id=null`
    and `posture="unknown"`. That is normal after a teleoperation session:
    `xr_teleoperate` releases the mode on the way in and does not restore it.

    Recovering from that means `SelectMode('ai')`, which this bridge
    deliberately cannot send — loading a controller onto a robot someone else
    may be driving is an operator decision, not a tool call. See
    `docs/ROBOT-API.md` §3.
    """
    log.info("check_motion_mode.called", sim_mode=SIM_MODE)

    if SIM_MODE != "real":
        return {
            "mode_name": None,
            "note": f"SIM_MODE={SIM_MODE} has no motion_switcher service.",
            "env": SIM_MODE,
        }

    import asyncio

    from bridge.sdk import g1_rpc

    code, result = await asyncio.to_thread(g1_rpc.check_motion_mode)
    if result is None:
        return {"mode_name": None, "rpc_code": code, "error": "no_answer", "env": SIM_MODE}

    name = result["name"]
    return {
        "mode_name": name,
        "form": result["form"],
        "rpc_code": code,
        "controller_loaded": bool(name),
        "note": (
            "A controller is loaded; FSM commands should be acted on."
            if name
            else "NO controller loaded — nothing will move, and the sport "
            "service will still answer code 0. Needs SelectMode('ai'), which "
            "an operator must send."
        ),
        "env": SIM_MODE,
    }
