"""Walking and turning: walk_to and turn (closed loop on pose), walk_velocity (open loop, real only)."""

from __future__ import annotations

import asyncio
import math
import uuid
from typing import Annotated

import structlog
from mcp.server.fastmcp import Context
from pydantic import Field

from bridge import config
from bridge.server import mcp
from bridge.skill_meta import meta as skill_meta

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Tool: walk_to
# ---------------------------------------------------------------------------


@mcp.tool(
    meta=skill_meta(
        classification="locomotion",
        danger_level="medium",
        status="real",
        cancellable=True,
        expected_duration_s=20.0,
        works_sim=True,
        works_real=True,
        preconditions=["robot_upright", "battery_pct_gt_15", "no_active_walk_task"],
        typical_failure_modes=["path_blocked", "timeout", "no_pose"],
    )
)
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
        sim_mode=config.SIM_MODE,
    )

    if config.SIM_MODE == "stub":
        task_id = f"tsk_{uuid.uuid4().hex[:12]}"
        # `await`, not `time.sleep`: these handlers are async, and a blocking
        # sleep here freezes the whole MCP event loop — including the
        # `cancel_task` and `stop_everything` calls that are supposed to be
        # able to interrupt a movement while it is happening.
        await asyncio.sleep(0.2)
        return {
            "task_id": task_id,
            "status": "ok",
            "message": (
                f"[STUB] Would walk to ({target_x_meters_world_frame:.2f}, "
                f"{target_y_meters_world_frame:.2f}) and stop within "
                f"{stop_distance_m:.2f} m. No motion executed."
            ),
            "env": config.SIM_MODE,
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
    return {**result, "env": config.SIM_MODE}


# ---------------------------------------------------------------------------
# Tool: turn
# ---------------------------------------------------------------------------


@mcp.tool(
    meta=skill_meta(
        classification="locomotion",
        danger_level="low",
        status="real",
        cancellable=True,
        expected_duration_s=12.0,
        works_sim=True,
        # Measured on the robot 2026-08-26 — see the docstring for the numbers
        # and for what this claim does NOT cover.
        works_real=True,
        preconditions=["robot_upright", "no_active_turn_task"],
        typical_failure_modes=["timeout", "no_pose"],
    )
)
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

    **WATCHED ON HARDWARE 2026-08-26.** `works_real` was False through two
    separate objections and both are now discharged. The yaw sign was settled
    on 2026-08-20 (positive is counterclockwise, measured three times off
    `rt/odommodestate`). The closed loop — this skill reading pose, computing
    an error and deciding when to stop — was measured on the robot with an
    operator watching, feet on the ground and the gantry slack, via
    `scripts/measure_turn.py`:

        commanded  achieved  residual  ratio  reached  elapsed
          +40.0     +37.48     2.52    1.07    true     4.9 s
          +40.0     +36.67     3.33    1.09    true     4.0 s

    Both converged, both stopped on their own, and the operator confirmed each
    rotation was smooth. That is what this flag asserts.

    WHAT THE CLAIM DOES NOT COVER, recorded so nobody reads more into it:

    * ACCURACY IS ABOUT +/-3.5 DEGREES on a 40 degree command, not the 3 degree
      tolerance. `tolerance_radians` is when the loop stops STEERING; the
      firmware then holds the last velocity for up to a second and the body
      coasts past that point. `reached` and `final_yaw_error_radians` are
      therefore sampled at different instants and can disagree — run 2 above
      decided it had arrived and settled 3.41 degrees out. Neither number is
      wrong; they answer different questions.
    * THE STOP FIRES ON A SINGLE UNFILTERED POSE SAMPLE. Leg odometry is noisy,
      so one reading dipping inside tolerance ends the loop. Requiring N
      consecutive in-tolerance samples would tighten this, and is the obvious
      next improvement.
    * Under-travel measured **1.08x**, not the ~2.2x this docstring previously
      predicted. That figure was stale; timeouts do not need to be generous.
      Both runs converged in under 5 seconds. An earlier 20 s timeout was a
      wedged bridge RPC channel, not a slow loop.
    """
    log.info(
        "turn.called",
        delta_yaw_radians=delta_yaw_radians,
        timeout_s=timeout_s,
        tolerance_degrees=tolerance_degrees,
        sim_mode=config.SIM_MODE,
    )

    if config.SIM_MODE == "stub":
        task_id = f"tsk_{uuid.uuid4().hex[:12]}"
        # `await`, not `time.sleep`: these handlers are async, and a blocking
        # sleep here freezes the whole MCP event loop — including the
        # `cancel_task` and `stop_everything` calls that are supposed to be
        # able to interrupt a movement while it is happening.
        await asyncio.sleep(0.2)
        return {
            "task_id": task_id,
            "status": "ok",
            "message": (
                f"[STUB] Would rotate by {delta_yaw_radians:+.4f} rad "
                f"(~{delta_yaw_radians * 180 / 3.14159:.1f}°). No motion executed."
            ),
            "env": config.SIM_MODE,
            "stub": True,
        }

    from bridge.skills.turn import run as run_turn

    result = await run_turn(
        delta_yaw_radians=delta_yaw_radians,
        timeout_s=timeout_s,
        tolerance_radians=math.radians(tolerance_degrees),
        ctx=ctx,
    )
    return {**result, "env": config.SIM_MODE}


# ---------------------------------------------------------------------------
# Tool: walk_velocity
# ---------------------------------------------------------------------------


@mcp.tool(
    meta=skill_meta(
        classification="locomotion",
        danger_level="medium",
        status="real",
        cancellable=True,
        expected_duration_s=3.0,
        works_sim=False,
        works_real=False,  # NOT YET LIVE-TESTED -- see the tool's own docstring.
        preconditions=["real_hardware_only"],
        typical_failure_modes=["rpc_error", "not_applicable_in_sim"],
    )
)
async def walk_velocity(
    ctx: Context,
    vx: Annotated[
        float,
        Field(
            ge=-0.3,
            le=0.3,
            description="Forward/backward body-frame velocity, m/s. Positive = forward.",
        ),
    ] = 0.0,
    vy: Annotated[
        float,
        Field(
            ge=-0.3,
            le=0.3,
            description="Lateral (strafe) body-frame velocity, m/s. Positive = left.",
        ),
    ] = 0.0,
    vyaw: Annotated[
        float,
        Field(
            ge=-0.3,
            le=0.3,
            description="Yaw rate, rad/s. Positive = counterclockwise.",
        ),
    ] = 0.0,
    duration_s: Annotated[
        float,
        Field(
            ge=0.1,
            le=3.0,
            description=(
                "How long to sustain this velocity before automatically stopping. "
                "Capped at 3s per call — no pose feedback exists to know when to "
                "stop early, so keep each call's blast radius small. For sustained "
                "motion, call this repeatedly, checking get_state() between calls."
            ),
        ),
    ] = 1.0,
) -> dict:
    """Command a raw body-frame velocity — real G1 hardware only, no pose needed.

    Unlike walk_to/turn (blocked on real hardware — no world-frame pose
    source wired yet), this is open-loop: same fire-and-forget pattern
    xr_teleoperate's own controller-button locomotion uses: short setpoints
    re-issued for duration_s, then zero. Speed is also capped by the safety
    governor, which slows or stops the base when a person is close.

    Isaac Sim: not applicable — walk_to/turn already give closed-loop
    pose-based control there, which is strictly better than this open-loop
    fallback. Use those instead in sim.

    NOT YET LIVE-TESTED against real hardware (api_id 7105/SetVelocity is
    confirmed to exist in the official SDK and shares the same DDS RPC
    pattern as the verified-live posture/gesture calls, but hasn't itself
    been dispatched against a real robot yet — the very first live call
    should be a short, small vx before trusting this further).
    """
    log.info(
        "walk_velocity.called",
        vx=vx,
        vy=vy,
        vyaw=vyaw,
        duration_s=duration_s,
        sim_mode=config.SIM_MODE,
    )

    if config.SIM_MODE == "stub":
        task_id = f"tsk_{uuid.uuid4().hex[:12]}"
        # `await`, not `time.sleep`: these handlers are async, and a blocking
        # sleep here freezes the whole MCP event loop — including the
        # `cancel_task` and `stop_everything` calls that are supposed to be
        # able to interrupt a movement while it is happening.
        await asyncio.sleep(0.2)
        return {
            "task_id": task_id,
            "status": "ok",
            "message": f"[STUB] Would command velocity ({vx}, {vy}, {vyaw}) for {duration_s}s.",
            "env": config.SIM_MODE,
            "stub": True,
        }

    if config.SIM_MODE != "real":
        return {
            "status": "not_applicable",
            "message": (
                f"walk_velocity is real-hardware-only (SIM_MODE={config.SIM_MODE}). "
                "Use walk_to/turn in Isaac Sim — closed-loop pose control is "
                "available there and is strictly better than this open-loop fallback."
            ),
            "env": config.SIM_MODE,
        }

    from bridge.skills.walk_velocity import run as run_walk_velocity

    result = await run_walk_velocity(vx=vx, vy=vy, vyaw=vyaw, duration_s=duration_s, ctx=ctx)
    return {**result, "env": config.SIM_MODE}
