"""Locomotion tools: closed-loop `walk_to` / `turn` and open-loop `walk_velocity`.

The target-specific half of motion lives in `bridge.skills._locomotion.send_velocity`;
these wrappers only validate, log, and stub (docs/ARCHITECTURE.md §4).
"""

from __future__ import annotations

import asyncio
import math
import uuid
from typing import Annotated

import structlog
from mcp.server.fastmcp import Context
from pydantic import Field

from bridge.config import SIM_MODE
from bridge.server import mcp
from bridge.skill_meta import meta as skill_meta
from bridge.tools import _background

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
    wait: Annotated[bool, Field(description=_background.WAIT_DESCRIPTION)] = True,
) -> dict:
    """Walk the robot toward a world-frame XY position and stop near it.

    Stub mode: logs and returns a fake result with a task_id.
    Isaac/real mode: creates a Task in the registry and drives the robot via
    `rt/run_command/cmd` with a body-frame velocity loop. Emits progress
    notifications via `ctx.report_progress` during the walk. Returns the
    final task dict when motion finishes (arrived / timeout / cancelled / failed).

    With `wait=false` it returns the task_id immediately and walks in the
    background: poll `get_task` / `wait_task`, observe the world in between,
    and `cancel_task(task_id)` to stop early — from the same session. With the
    default `wait=true`, mid-flight cancel needs another connection
    (`stop_everything` always works).
    """
    log.info(
        "walk_to.called",
        target=(target_x_meters_world_frame, target_y_meters_world_frame),
        stop_distance_m=stop_distance_m,
        timeout_s=timeout_s,
        wait=wait,
        sim_mode=SIM_MODE,
    )

    if not wait:
        task = _background.create("walk_to")
        if SIM_MODE == "stub":
            message = (
                f"[STUB] Would walk to ({target_x_meters_world_frame:.2f}, "
                f"{target_y_meters_world_frame:.2f}) and stop within "
                f"{stop_distance_m:.2f} m. No motion executed."
            )
            return _background.start(task, lambda: _background.stub_run(task, message))

        from bridge.skills.walk_to import run as run_walk_to

        return _background.start(
            task,
            lambda: run_walk_to(
                target_x=target_x_meters_world_frame,
                target_y=target_y_meters_world_frame,
                stop_distance_m=stop_distance_m,
                timeout_s=timeout_s,
                task=task,
            ),
        )

    if SIM_MODE == "stub":
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


@mcp.tool(
    meta=skill_meta(
        classification="locomotion",
        danger_level="low",
        status="real",
        cancellable=True,
        expected_duration_s=12.0,
        works_sim=True,
        works_real=False,
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
    wait: Annotated[bool, Field(description=_background.WAIT_DESCRIPTION)] = True,
) -> dict:
    """Rotate the robot in place by a yaw delta.

    Stub mode: logs and returns a fake task dict.
    Isaac/real mode: creates a Task and drives `rt/run_command/cmd` with pure
    yaw velocity until error is within tolerance or timeout elapses. Reports
    progress via `ctx.report_progress`. Cancellable via `cancel_task` or
    `stop_everything`. With `wait=false` it returns the task_id immediately and
    rotates in the background — poll `get_task` / `wait_task`.

    **The yaw sign is no longer the blocker.** `works_real` was set False on
    2026-08-15 because "turn's yaw sign convention is still unverified and may
    rotate the wrong way". That is now settled: on 2026-08-20 a commanded
    positive yaw rotated the robot counterclockwise, measured three times off
    `rt/odommodestate` (+5.26, +5.77 and again in a full run), through the same
    `send_velocity` path this skill uses. Positive really is left.

    It stays False anyway, and deliberately. `works_real` means *a human has
    watched this skill run* — and nobody has watched `turn`. What was verified
    is the sign convention it shares with the teleop stream, not this skill's
    own closed loop: it reads pose, computes an error, and decides when to
    stop, and none of that has executed on hardware. Flipping the flag on
    evidence about a shared component would be exactly the kind of claim the
    flag exists to prevent.

    What it needs is one supervised run: a small delta (30-45 degrees) with
    room to rotate, watching whether it converges and stops inside tolerance.
    Expect it to be slow — measured yaw under-travels command by ~2.2x on this
    body, so allow generous timeouts.
    """
    log.info(
        "turn.called",
        delta_yaw_radians=delta_yaw_radians,
        timeout_s=timeout_s,
        tolerance_degrees=tolerance_degrees,
        wait=wait,
        sim_mode=SIM_MODE,
    )

    if not wait:
        task = _background.create("turn")
        if SIM_MODE == "stub":
            message = (
                f"[STUB] Would rotate by {delta_yaw_radians:+.4f} rad "
                f"(~{delta_yaw_radians * 180 / 3.14159:.1f}°). No motion executed."
            )
            return _background.start(task, lambda: _background.stub_run(task, message))

        from bridge.skills.turn import run as run_turn

        return _background.start(
            task,
            lambda: run_turn(
                delta_yaw_radians=delta_yaw_radians,
                timeout_s=timeout_s,
                tolerance_radians=math.radians(tolerance_degrees),
                task=task,
            ),
        )

    if SIM_MODE == "stub":
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
            "env": SIM_MODE,
            "stub": True,
        }

    from bridge.skills.turn import run as run_turn

    result = await run_turn(
        delta_yaw_radians=delta_yaw_radians,
        timeout_s=timeout_s,
        tolerance_radians=math.radians(tolerance_degrees),
        ctx=ctx,
    )
    return {**result, "env": SIM_MODE}


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
    xr_teleoperate's own controller-button locomotion uses. The G1 firmware
    sustains the velocity for duration_s and stops on its own.

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
        sim_mode=SIM_MODE,
    )

    if SIM_MODE == "stub":
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
            "env": SIM_MODE,
            "stub": True,
        }

    if SIM_MODE != "real":
        return {
            "status": "not_applicable",
            "message": (
                f"walk_velocity is real-hardware-only (SIM_MODE={SIM_MODE}). "
                "Use walk_to/turn in Isaac Sim — closed-loop pose control is "
                "available there and is strictly better than this open-loop fallback."
            ),
            "env": SIM_MODE,
        }

    from bridge.skills.walk_velocity import run as run_walk_velocity

    result = await run_walk_velocity(vx=vx, vy=vy, vyaw=vyaw, duration_s=duration_s, ctx=ctx)
    return {**result, "env": SIM_MODE}
