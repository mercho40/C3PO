"""C3PO bridge — stdio MCP server (Phase 0a stubs).

Exposes the robot's skills as MCP tools so Claude Code (or any MCP client)
can drive the bridge directly. In Phase 0a all tools are stubs that log + return
fake data — no Unitree SDK, no DDS, no Isaac Sim involvement. Phase 0b replaces
the stubs with real DDS calls when SIM_MODE != 'stub'.

Run:
    uv run python -m bridge.mcp_server

Registered in `.mcp.json` as `c3po-bridge`. Default transport is stdio, which is
what Claude Code's MCP client expects.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Annotated, Literal

import structlog
from mcp.server.fastmcp import FastMCP
from pydantic import Field

log = structlog.get_logger(__name__)

SIM_MODE = os.environ.get("SIM_MODE", "stub")
ROBOT_HOST = os.environ.get("ROBOT_HOST", "127.0.0.1")
DDS_DOMAIN_ID = int(os.environ.get("DDS_DOMAIN_ID", "0"))

# Initialize DDS up-front when not in stub mode. We do this at import time so
# the subscriber is alive and accumulating LowState messages before the first
# tool call lands.
if SIM_MODE != "stub":
    from bridge.sdk.connection import init_dds
    from bridge.sdk.state import get_sampler

    init_dds(robot_host=ROBOT_HOST, domain_id=DDS_DOMAIN_ID)
    # Warm the subscriber singleton so messages start flowing immediately.
    get_sampler()

mcp = FastMCP(
    name="c3po-bridge",
    instructions=(
        "Tools for controlling a Unitree G1 humanoid robot (or its Isaac Sim "
        "emulation). In stub mode, tools log and return fake data. In isaac/real "
        "mode, get_state reads live DDS state; walk_to and say remain stubs "
        "until Phase 1."
    ),
)


# ---------------------------------------------------------------------------
# Tool: get_state
# ---------------------------------------------------------------------------

@mcp.tool()
def get_state() -> dict:
    """Return the robot's current state: pose, battery, posture, faults.

    Behaviour by mode:
    - `stub`: returns hardcoded fake state for wiring validation.
    - `isaac` / `real`: reads the latest `rt/lowstate` DDS message. Pose and
      battery come from separate topics not yet subscribed (Phase 1) — those
      fields are `null` until then. `posture` is derived from FSM mode.
    """
    log.info("get_state.called", sim_mode=SIM_MODE)

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

    # isaac / real / mujoco_local — read from the live DDS subscriber.
    from bridge.sdk.state import get_sampler

    state = get_sampler().get_state()
    return {**state, "env": SIM_MODE, "stub": False}


# ---------------------------------------------------------------------------
# Tool: walk_to
# ---------------------------------------------------------------------------

@mcp.tool()
def walk_to(
    target_x_meters_world_frame: Annotated[
        float, Field(description="Target X coordinate in meters, world frame.")
    ],
    target_y_meters_world_frame: Annotated[
        float, Field(description="Target Y coordinate in meters, world frame.")
    ],
    stop_distance_m: Annotated[
        float,
        Field(
            ge=0.3,
            le=5.0,
            description=(
                "How close to the target the robot should stop. Smaller values "
                "mean closer; minimum 0.3 m to avoid collisions."
            ),
        ),
    ] = 1.0,
    timeout_s: Annotated[
        float,
        Field(
            ge=5.0,
            le=300.0,
            description=(
                "Maximum seconds to spend walking before giving up. The current "
                "Isaac Sim policy walks at ~13% of commanded velocity, so allow "
                "generous time per metre."
            ),
        ),
    ] = 60.0,
) -> dict:
    """Walk the robot toward a world-frame XY position and stop near it.

    Stub mode: logs and returns a fake result.
    Isaac/real mode: drives the robot via Isaac Sim's velocity command channel
    (`rt/run_command/cmd`) until within `stop_distance_m` of the target or
    `timeout_s` elapses. Synchronous — returns when motion is finished.
    """
    log.info(
        "walk_to.called",
        target=(target_x_meters_world_frame, target_y_meters_world_frame),
        stop_distance_m=stop_distance_m,
        timeout_s=timeout_s,
        sim_mode=SIM_MODE,
    )

    if SIM_MODE == "stub":
        task_id = f"tsk_{uuid.uuid4().hex[:12]}"
        time.sleep(0.2)
        return {
            "task_id": task_id,
            "status": "ok",
            "message": (
                f"[STUB] Would walk to ({target_x_meters_world_frame:.2f}, "
                f"{target_y_meters_world_frame:.2f}) and stop within "
                f"{stop_distance_m:.2f} m. No motion executed."
            ),
            "env": SIM_MODE,
            "stub": True,
        }

    from bridge.skills.walk_to import run as run_walk_to

    result = run_walk_to(
        target_x=target_x_meters_world_frame,
        target_y=target_y_meters_world_frame,
        stop_distance_m=stop_distance_m,
        timeout_s=timeout_s,
    )
    return {**result, "env": SIM_MODE}


# ---------------------------------------------------------------------------
# Tool: say
# ---------------------------------------------------------------------------

@mcp.tool()
def say(
    text: Annotated[
        str,
        Field(
            min_length=1,
            max_length=500,
            description="What the robot should say. Will be spoken aloud via TTS.",
        ),
    ],
    voice: Annotated[
        Literal["default", "warm", "neutral", "robotic"],
        Field(description="Voice style. Phase 0a ignores this; logs only."),
    ] = "default",
) -> dict:
    """Make the robot speak aloud.

    Phase 0a: no audio output, only logging. Phase 4 wires Cartesia TTS.
    """
    log.info("say.called", text=text, voice=voice, sim_mode=SIM_MODE)
    return {
        "status": "ok",
        "spoken": text,
        "voice": voice,
        "env": SIM_MODE,
        "stub": True,
    }


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the MCP server on stdio (default for Claude Code / Claude Desktop)."""
    log.info("c3po-bridge.start", sim_mode=SIM_MODE, transport="stdio")
    mcp.run()  # default transport is stdio


if __name__ == "__main__":
    main()
