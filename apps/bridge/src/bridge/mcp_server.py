"""C3PO bridge — stdio MCP server.

Exposes the robot's skills as MCP tools so Claude Code (or any MCP client)
can drive the bridge directly.

Modes:
- `SIM_MODE=stub`: tools log and return fake data — for wiring validation.
- `SIM_MODE=isaac`: DDS is initialised at import; `get_state` reads live
  `rt/lowstate` + `rt/sim_state`; `walk_to` drives Isaac Sim via
  `rt/run_command/cmd` with a body-frame velocity loop.

Long-running tools (today: `walk_to`) use the shared task lifecycle in
`bridge.skills.task_runtime`: each invocation creates a `Task` row, the
skill checks `task.cancel_event` between iterations, and progress is
emitted through MCP's `ctx.report_progress` when a `Context` is passed.
The companion `cancel_task` and `list_active_tasks` tools provide
visibility / control across the registry.

Run:
    uv run python -m bridge.mcp_server

Registered in `.mcp.json` as `c3po-bridge`. Default transport is stdio, which
is what Claude Code's MCP client expects.
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Annotated, Literal

import structlog
from mcp.server.fastmcp import Context, FastMCP
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
        "mode, get_state reads live DDS state and walk_to/turn actually drive "
        "the robot. Long-running tools return a task_id; use cancel_task to "
        "interrupt one task, stop_everything to halt all motion, and "
        "list_active_tasks to see what's in flight."
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

    from bridge.sdk.state import get_sampler

    state = get_sampler().get_state()
    return {**state, "env": SIM_MODE, "stub": False}


# ---------------------------------------------------------------------------
# Tool: walk_to
# ---------------------------------------------------------------------------

@mcp.tool()
async def walk_to(
    ctx: Context,
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
                "Isaac Sim policy walks at ~10-15% of commanded velocity, so allow "
                "generous time per metre."
            ),
        ),
    ] = 60.0,
) -> dict:
    """Walk the robot toward a world-frame XY position and stop near it.

    Stub mode: logs and returns a fake result with a task_id.
    Isaac/real mode: creates a Task in the registry and drives the robot via
    `rt/run_command/cmd` with a body-frame velocity loop. Emits progress
    notifications via `ctx.report_progress` during the walk. Returns the
    final task dict when motion finishes (arrived / timeout / cancelled / failed).

    To interrupt in flight, call `cancel_task(task_id)` from another MCP
    session or direct Python — for the current stdio transport, mid-flight
    cancel from the same Claude session isn't possible while this tool blocks.
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

    result = await run_walk_to(
        target_x=target_x_meters_world_frame,
        target_y=target_y_meters_world_frame,
        stop_distance_m=stop_distance_m,
        timeout_s=timeout_s,
        ctx=ctx,
    )
    return {**result, "env": SIM_MODE}


# ---------------------------------------------------------------------------
# Tool: turn
# ---------------------------------------------------------------------------

@mcp.tool()
async def turn(
    ctx: Context,
    delta_yaw_radians: Annotated[
        float,
        Field(
            ge=-6.2832,
            le=6.2832,
            description=(
                "How far to rotate, in radians. POSITIVE = counterclockwise "
                "(left turn from the robot's point of view); NEGATIVE = clockwise "
                "(right turn). 90° left ≈ 1.5708; 180° ≈ 3.1416 (or -3.1416)."
            ),
        ),
    ],
    timeout_s: Annotated[
        float,
        Field(
            ge=5.0,
            le=120.0,
            description=(
                "Maximum seconds to spend rotating before giving up. Walk policy "
                "yaw is slow under small errors — allow 30s+ per 90° if accuracy matters."
            ),
        ),
    ] = 30.0,
    tolerance_degrees: Annotated[
        float,
        Field(
            ge=0.5,
            le=20.0,
            description="Stop when within this many degrees of the target yaw.",
        ),
    ] = 3.0,
) -> dict:
    """Rotate the robot in place by a yaw delta.

    Stub mode: logs and returns a fake task dict.
    Isaac/real mode: creates a Task and drives `rt/run_command/cmd` with pure
    yaw velocity until error is within tolerance or timeout elapses. Reports
    progress via `ctx.report_progress`. Cancellable via `cancel_task` or
    `stop_everything`.
    """
    log.info(
        "turn.called",
        delta_yaw_radians=delta_yaw_radians,
        timeout_s=timeout_s,
        tolerance_degrees=tolerance_degrees,
        sim_mode=SIM_MODE,
    )

    if SIM_MODE == "stub":
        task_id = f"tsk_{uuid.uuid4().hex[:12]}"
        time.sleep(0.2)
        return {
            "task_id": task_id,
            "status": "ok",
            "message": (
                f"[STUB] Would rotate by {delta_yaw_radians:+.4f} rad "
                f"(~{delta_yaw_radians * 180 / 3.14159:.1f}°). No motion executed."
            ),
            "env": SIM_MODE,
            "stub": True,
        }

    import math

    from bridge.skills.turn import run as run_turn

    result = await run_turn(
        delta_yaw_radians=delta_yaw_radians,
        timeout_s=timeout_s,
        tolerance_radians=math.radians(tolerance_degrees),
        ctx=ctx,
    )
    return {**result, "env": SIM_MODE}


# ---------------------------------------------------------------------------
# Tool: stop_everything
# ---------------------------------------------------------------------------

@mcp.tool()
def stop_everything() -> dict:
    """Halt all motion immediately and cancel any in-flight tasks.

    Safety-critical: cancels every running task in the registry (each skill
    observes the cancel signal between iterations and ramps down velocity)
    AND independently sends a zero-velocity burst to the run-command channel
    for ~0.4 s in case the policy is still in motion. Synchronous and fast.

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

    result = run_stop()
    return {**result, "env": SIM_MODE}


# ---------------------------------------------------------------------------
# Tools: G1 high-level posture + gesture skills
# ---------------------------------------------------------------------------
# Each of these wraps a request to either `rt/api/sport/request` (api_id=7101,
# full-body modes) or `rt/api/arm/request` (api_id=7106, upper-limb gestures).
# Today, Isaac Sim's `unitree_sim_isaaclab` scene doesn't subscribe to those
# topics, so the dispatcher returns `phase=logged_only` — the request was
# constructed but not delivered. When the WebRTC Transport lands (spec §16)
# and SIM_MODE=real, the same skills produce actual motion.
#
# Adding more skills (zero_torque, sit_g1, lie_up, squat, shake_hand, hug,
# clap, release_arm, …) is a one-liner — see g1_protocol.SKILL_REQUESTS for
# the full catalogue.


@mcp.tool()
async def damp(ctx: Context) -> dict:
    """Engage damping mode — set all joints to zero stiffness. Safety transition.

    On the G1 FSM: only legal from Preparation, Walk, Walk(waist), Run, Squat,
    ZeroTorque. From Damp you can transition to ZeroTorque, Preparation,
    SquatUp, or LieUp. This is the canonical "come to rest" target.

    Isaac Sim: logged only (sim doesn't subscribe to `rt/api/sport/request`).
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("damp", ctx), "env": SIM_MODE}


@mcp.tool()
async def prepare(ctx: Context) -> dict:
    """Enter Preparation mode — required gateway to Walk / Walk(waist) / Run.

    On the G1 FSM: legal only from Damp. From Preparation, you can transition
    to Walk, Walk(waist), Run, or back to Damp.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("prepare", ctx), "env": SIM_MODE}


@mcp.tool()
async def wave(ctx: Context) -> dict:
    """Wave the upper arm — friendly greeting gesture.

    G1 firmware "high wave" (api_id=7106, data=26). Note: arm gestures require
    a locomotion-active FSM state (Walk / Walk(waist) / Run) on real hardware.

    Isaac Sim: logged only (sim doesn't subscribe to `rt/api/arm/request`).
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("wave", ctx), "env": SIM_MODE}


@mcp.tool()
async def point_at(ctx: Context) -> dict:
    """Extend the right arm forward — closest available "point" gesture.

    G1 firmware "forward push" (api_id=7106, data=36). Like `wave`, requires
    a locomotion-active FSM state on real hardware.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("point_at", ctx), "env": SIM_MODE}


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
# Tool: cancel_task
# ---------------------------------------------------------------------------

@mcp.tool()
def cancel_task(
    task_id: Annotated[
        str,
        Field(
            min_length=1,
            description=(
                "Task ID returned by a long-running tool (e.g. `walk_to`'s "
                "result.task_id). Sets the task's cancel signal; the running "
                "skill will observe it between iterations and stop motion cleanly."
            ),
        ),
    ],
) -> dict:
    """Request graceful cancellation of an in-flight task.

    Note: with the current stdio MCP transport, this cannot interrupt a tool
    that the *same* Claude session is currently waiting on — the bridge is
    busy handling that call. It's useful for: (a) direct Python clients,
    (b) tests, (c) a future HTTP MCP transport where multiple connections
    can interleave.
    """
    from bridge.skills.task_runtime import get_registry

    registry = get_registry()
    ok = registry.cancel(task_id)
    if not ok:
        existing = registry.get(task_id)
        if existing is None:
            return {"task_id": task_id, "ok": False, "reason": "unknown_task_id"}
        if existing.cancel_event.is_set():
            return {"task_id": task_id, "ok": False, "reason": "cancel_already_requested"}
        return {
            "task_id": task_id,
            "ok": False,
            "reason": f"task_not_running (status={existing.status})",
        }
    return {"task_id": task_id, "ok": True}


# ---------------------------------------------------------------------------
# Tool: list_active_tasks
# ---------------------------------------------------------------------------

@mcp.tool()
def list_active_tasks(
    include_recent: Annotated[
        bool,
        Field(
            description=(
                "If true, also include recently-completed tasks (up to 5 minutes "
                "old) so you can inspect the last walk's result."
            ),
        ),
    ] = False,
) -> dict:
    """List running tasks (and optionally recently-completed ones)."""
    from bridge.skills.task_runtime import get_registry

    registry = get_registry()
    active = [t.to_dict() for t in registry.list_active()]
    payload: dict = {"active": active, "active_count": len(active)}
    if include_recent:
        recent = [t.to_dict() for t in registry.list_recent(limit=10)]
        payload["recent"] = recent
    return payload


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------

def main() -> None:
    """Run the MCP server on stdio (default for Claude Code / Claude Desktop)."""
    log.info("c3po-bridge.start", sim_mode=SIM_MODE, transport="stdio")
    mcp.run()  # default transport is stdio


if __name__ == "__main__":
    main()
