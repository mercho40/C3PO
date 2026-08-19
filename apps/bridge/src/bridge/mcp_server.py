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

import math
import os
import time
import uuid
from typing import Annotated, Literal

import structlog
from mcp.server.fastmcp import Context, FastMCP
from pydantic import Field

from bridge import watchdog
from bridge.skill_meta import meta as skill_meta
from bridge.watchdog import get_watchdog

log = structlog.get_logger(__name__)

SIM_MODE = os.environ.get("SIM_MODE", "stub")
ROBOT_HOST = os.environ.get("ROBOT_HOST", "127.0.0.1")
DDS_DOMAIN_ID = int(os.environ.get("DDS_DOMAIN_ID", "0"))
# Pin CycloneDDS to one NIC. Empty = autodetermine, correct on a dev machine.
# Onboard the G1's Jetson this must be `eth0` — see `init_dds`.
DDS_INTERFACE = os.environ.get("DDS_INTERFACE", "").strip() or None

# Back↔bridge link: how this server is reached. Default is stdio (what Claude
# Code's MCP client expects). Set BRIDGE_TRANSPORT=http to expose FastMCP's
# streamable-http transport so `apps/back` can connect as an MCP client
# (see apps/back/src/bridge/client.ts). stdio stays untouched either way.
BRIDGE_TRANSPORT = os.environ.get("BRIDGE_TRANSPORT", "stdio")
BRIDGE_HOST = os.environ.get("BRIDGE_HOST", "127.0.0.1")
BRIDGE_PORT = int(os.environ.get("BRIDGE_PORT", "8000"))

# Initialize DDS up-front when not in stub mode. We do this at import time so
# the subscriber is alive and accumulating LowState messages before the first
# tool call lands.
if SIM_MODE != "stub":
    from bridge.sdk.connection import init_dds
    from bridge.sdk.state import get_sampler

    init_dds(robot_host=ROBOT_HOST, domain_id=DDS_DOMAIN_ID, interface=DDS_INTERFACE)
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
    host=BRIDGE_HOST,
    port=BRIDGE_PORT,
)


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
        works_real=False,
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
        time.sleep(0.2)
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


# ---------------------------------------------------------------------------
# Tools: G1 high-level posture + gesture skills
# ---------------------------------------------------------------------------
# Each of these wraps a request to either `rt/api/sport/request` (api_id=7101,
# full-body modes) or `rt/api/arm/request` (api_id=7106, upper-limb gestures).
# Isaac Sim's `unitree_sim_isaaclab` scene doesn't subscribe to those topics,
# so under SIM_MODE=isaac the dispatcher returns `phase=logged_only` — the
# request was constructed but not delivered. Under SIM_MODE=real these
# dispatch for real over DDS RPC (`bridge.sdk.g1_rpc`); no WebRTC involved.
#
# Adding more skills (zero_torque, sit_g1, lie_up, squat, shake_hand, hug,
# clap, release_arm, …) is a one-liner — see g1_protocol.SKILL_REQUESTS for
# the full catalogue.


@mcp.tool(
    meta=skill_meta(
        classification="posture",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=1.0,
        works_sim=False,
        works_real=True,
        preconditions=["fsm_state_in_{preparation,walk,walk_waist,run,squat,zero_torque}"],
        typical_failure_modes=["fsm_transition_rejected", "transport_unsupported"],
    )
)
async def damp(ctx: Context) -> dict:
    """Engage damping mode — set all joints to zero stiffness. Safety transition.

    On the G1 FSM: only legal from Preparation, Walk, Walk(waist), Run, Squat,
    ZeroTorque. From Damp you can transition to ZeroTorque, Preparation,
    SquatUp, or LieUp. This is the canonical "come to rest" target.

    Isaac Sim: logged only (sim doesn't subscribe to `rt/api/sport/request`).
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("damp", ctx), "env": SIM_MODE}


@mcp.tool(
    meta=skill_meta(
        classification="posture",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=2.0,
        works_sim=False,
        works_real=True,
        preconditions=["fsm_state_is_damp"],
        typical_failure_modes=["fsm_transition_rejected", "transport_unsupported"],
    )
)
async def prepare(ctx: Context) -> dict:
    """Enter Preparation mode — required gateway to Walk / Walk(waist) / Run.

    On the G1 FSM: legal only from Damp. From Preparation, you can transition
    to Walk, Walk(waist), Run, or back to Damp.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("prepare", ctx), "env": SIM_MODE}


@mcp.tool(
    meta=skill_meta(
        classification="gesture",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=3.0,
        works_sim=False,
        works_real=True,
        preconditions=["fsm_state_in_{walk,walk_waist,run}"],
        typical_failure_modes=["fsm_not_locomotion_state", "transport_unsupported"],
    )
)
async def wave(ctx: Context) -> dict:
    """Wave the upper arm — friendly greeting gesture.

    G1 firmware "high wave" (api_id=7106, data=26). Note: arm gestures require
    a locomotion-active FSM state (Walk / Walk(waist) / Run) on real hardware.

    Isaac Sim: logged only (sim doesn't subscribe to `rt/api/arm/request`).
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("wave", ctx), "env": SIM_MODE}


@mcp.tool(
    meta=skill_meta(
        classification="gesture",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=3.0,
        works_sim=False,
        # Was True while this tool's own docstring said the gesture had never
        # worked on hardware — the precise contradiction `skill_meta.py` warns
        # about, since `works_real` is supposed to mean a human watched it run.
        # Nobody has. Corrected to False rather than to a hoped-for True.
        works_real=False,
        preconditions=["fsm_state_in_{walk,walk_waist,run}"],
        typical_failure_modes=["fsm_not_locomotion_state", "transport_unsupported"],
    )
)
async def point_at(ctx: Context) -> dict:
    """Extend the arm forward — the closest thing to pointing.

    Dispatches vendor action **36** (`forward_push`, arm service api_id=7106),
    per `g1_protocol.SKILL_REQUESTS["point_at"]`. There is no "point" in
    Unitree's action table; this is the nearest real action.

    History, because this id has moved twice and the reasoning matters: 36 was
    briefly replaced by 23 on the belief that 36 "appears in no vendor
    artifact". That was wrong — the ROBOT's own `GetActionList` (read live
    2026-08-15) does list 36, gated on `mode_machine` [5, 6], and this robot
    reports 5. The published table is simply incomplete for this build, so 36
    is correct and is what ships. See `g1_protocol.py`'s Gesture docstring.

    **Never executed on this hardware** — neither id has been dispatched here,
    which is why `works_real` is False.

    Like `wave`, needs an FSM state that permits arm actions.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("point_at", ctx), "env": SIM_MODE}


@mcp.tool(
    meta=skill_meta(
        classification="posture",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=1.0,
        works_sim=False,
        works_real=True,
        preconditions=["fsm_state_is_damp"],
        typical_failure_modes=["fsm_transition_rejected", "transport_unsupported"],
    )
)
async def zero_torque(ctx: Context) -> dict:
    """Enter Zero Torque mode — actuators receive no command. Limp robot.

    On the G1 FSM: legal only from Damp. From Zero Torque you can only go
    back to Damp. Use as the "fully off" terminal state when storing the
    robot or stepping away.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("zero_torque", ctx), "env": SIM_MODE}


@mcp.tool(
    meta=skill_meta(
        classification="introspection",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=0.1,
        works_sim=False,
        works_real=True,
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


@mcp.tool(
    meta=skill_meta(
        classification="posture",
        danger_level="high",
        status="real",
        cancellable=False,
        expected_duration_s=3.0,
        works_sim=False,
        works_real=False,
        preconditions=["fsm_state_is_preparation", "operator_present"],
        typical_failure_modes=["fsm_transition_rejected", "no_motion_controller_loaded"],
    )
)
async def start_walking_waist(ctx: Context) -> dict:
    """Enter Walk Motion-3Dof-waist (FSM 501) — the 29-DoF walk program.

    500 and 501 are two different walk programs chosen by how many degrees of
    freedom the waist has, not a generic start and a variant. Unitree documents
    `mode_machine` as `4:23-Dof; 5:29-Dof; 6:27-Dof`, and this robot reports
    **5** — so 501 is likely the program it actually implements, and 500 the
    other variant's.

    That matters because `start_walking` (500) has been observed returning rpc
    code 0 while never leaving StandUp. A recognised-but-not-implemented id
    would look exactly like that: accepted, then declined by the controller.

    **Never executed on this robot.** Try it from a supervised window with the
    operator ready to damp, not from an autonomous plan. See
    `docs/ROBOT-API.md` §11.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("start_walking_waist", ctx), "env": SIM_MODE}


@mcp.tool(
    meta=skill_meta(
        classification="posture",
        danger_level="medium",
        status="real",
        cancellable=False,
        expected_duration_s=1.0,
        works_sim=False,
        works_real=False,
        preconditions=["robot_upright"],
        typical_failure_modes=["fsm_transition_rejected", "transport_unsupported"],
    )
)
async def balance_stand(ctx: Context) -> dict:
    """Engage the stand-and-balance controller (api_id=7102, balance_mode=0).

    The vendor's `BalanceStand()`. Distinct from the FSM postures above: it
    sets the *balance controller* mode rather than requesting a state change,
    so it goes to api_id 7102, not 7101.

    Why it exists as a tool: on 2026-08-12, with the robot standing and
    bearing its own weight, `start_walking` (the vendor's `Start()`,
    SetFsmId 500) returned code 0 and did **not** leave StandUp — and arm
    gestures then failed 7404 FSM_UNAVAILABLE. This is the one call in
    `g1_loco_client.hpp` between StandUp and Start that we had never sent.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("balance_stand", ctx), "env": SIM_MODE}


@mcp.tool(
    meta=skill_meta(
        classification="posture",
        danger_level="medium",
        status="real",
        cancellable=False,
        expected_duration_s=2.0,
        works_sim=False,
        works_real=True,
        preconditions=["fsm_state_is_preparation"],
        typical_failure_modes=["fsm_transition_rejected", "transport_unsupported"],
    )
)
async def start_walking(ctx: Context) -> dict:
    """Enter Walk mode — locomotion FSM activates; arm gestures become available.

    On the G1 FSM: legal only from Preparation. Until you call this, walk_to /
    turn / wave / point_at won't get a meaningful response on real hardware.
    Typical sequence: damp → prepare → start_walking → walk_to / wave.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("start_walking", ctx), "env": SIM_MODE}


@mcp.tool(
    meta=skill_meta(
        classification="posture",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=3.0,
        works_sim=False,
        works_real=True,
        preconditions=["fsm_state_is_damp"],
        typical_failure_modes=["fsm_transition_rejected", "transport_unsupported"],
    )
)
async def sit_g1(ctx: Context) -> dict:
    """Enter Seating mode — robot adopts a seated posture.

    G1 firmware mode index 3 on api_id=7101. Typically reached from Damp;
    the exact accepted-from-set depends on firmware. Use for a calm,
    low-energy presentation state.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("sit_g1", ctx), "env": SIM_MODE}


@mcp.tool(
    meta=skill_meta(
        classification="posture",
        danger_level="medium",
        status="real",
        cancellable=False,
        expected_duration_s=4.0,
        works_sim=False,
        works_real=True,
        preconditions=["fsm_state_is_damp"],
        typical_failure_modes=["fsm_transition_rejected", "transport_unsupported"],
    )
)
async def lie_up(ctx: Context) -> dict:
    """Enter Lie-Up mode — robot transitions to a face-up lying pose.

    G1 firmware mode index 702 on api_id=7101. Legal target from Damp.
    Useful pre-storage state and starting pose for recovery sequences.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("lie_up", ctx), "env": SIM_MODE}


@mcp.tool(
    meta=skill_meta(
        classification="posture",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=3.0,
        works_sim=False,
        works_real=True,
        typical_failure_modes=["fsm_transition_rejected", "transport_unsupported"],
    )
)
async def squat(ctx: Context) -> dict:
    """Enter Squat mode — robot crouches to a lowered stance.

    G1 firmware Squat (mode 2) and SquatUp (706) collapse to the same
    physical pose at different control gains. From Squat you can only go to
    Damp on the FSM, so this is a terminal posture until you reset.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("squat", ctx), "env": SIM_MODE}


@mcp.tool(
    meta=skill_meta(
        classification="gesture",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=3.0,
        works_sim=False,
        works_real=True,
        preconditions=["fsm_state_in_{walk,walk_waist,run}"],
        typical_failure_modes=["fsm_not_locomotion_state", "transport_unsupported"],
    )
)
async def shake_hand(ctx: Context) -> dict:
    """Extend the right hand for a handshake.

    G1 firmware "shake hands" (api_id=7106, data=27). Requires a locomotion-
    active FSM state (Walk / Walk(waist) / Run) on real hardware.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("shake_hand", ctx), "env": SIM_MODE}


@mcp.tool(
    meta=skill_meta(
        classification="gesture",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=3.0,
        works_sim=False,
        works_real=True,
        preconditions=["fsm_state_in_{walk,walk_waist}"],
        typical_failure_modes=["fsm_not_locomotion_state", "transport_unsupported"],
    )
)
async def hug(ctx: Context) -> dict:
    """Open arms wide for a hug.

    G1 firmware "hug" (api_id=7106, data=19). Requires a locomotion-active
    FSM state. The hug gesture is one of the four that the firmware hides
    while in Run mode — prefer Walk or Walk(waist) for this one.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("hug", ctx), "env": SIM_MODE}


@mcp.tool(
    meta=skill_meta(
        classification="gesture",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=2.0,
        works_sim=False,
        works_real=True,
        preconditions=["fsm_state_in_{walk,walk_waist,run}"],
        typical_failure_modes=["fsm_not_locomotion_state", "transport_unsupported"],
    )
)
async def clap(ctx: Context) -> dict:
    """Bring hands together to clap.

    G1 firmware "clap" (api_id=7106, data=17). Requires a locomotion-active
    FSM state.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("clap", ctx), "env": SIM_MODE}


@mcp.tool(
    meta=skill_meta(
        classification="gesture",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=1.0,
        works_sim=False,
        works_real=True,
        preconditions=["fsm_state_in_{walk,walk_waist,run}"],
        typical_failure_modes=["fsm_not_locomotion_state", "transport_unsupported"],
    )
)
async def release_arm(ctx: Context) -> dict:
    """Return arms to a neutral / idle position.

    G1 firmware "release arm" (api_id=7106, data=99). Call this after any
    other gesture to settle the arms back to their default hanging pose.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("release_arm", ctx), "env": SIM_MODE}


@mcp.tool(
    meta=skill_meta(
        classification="gesture",
        danger_level="low",
        status="real",
        cancellable=True,
        expected_duration_s=20.0,
        works_sim=False,
        works_real=False,  # NOT YET LIVE-TESTED -- new sequence, see the skill's own docstring.
        preconditions=["fsm_state_in_{walk,walk_waist,run}"],
        typical_failure_modes=[
            "fsm_not_locomotion_state",
            "arm_latched_7401",
            "transport_unsupported",
        ],
    )
)
async def dance(ctx: Context) -> dict:
    """Run a short choreographed gesture sequence.

    Not a single firmware mode -- `Mode.DANCE` (503) is unverified and
    unwired. Instead sequences several already-verified `Gesture` ids
    (BOTH_HANDS_UP, HIGH_FIVE, WAVE_UNDER_HEAD) through the same
    `call_arm()` primitive `wave`/`clap`/`hug` already use, interleaved with
    RELEASE_ARM to respect the arm's per-gesture latch (error 7401
    otherwise). Requires a locomotion-active FSM state, same as every other
    arm gesture. Cancellable between steps.

    Isaac Sim: logged only (sim doesn't subscribe to `rt/api/arm/request`).

    NOT YET LIVE-TESTED against real hardware -- the first live call should
    be watched closely, same caution as `walk_velocity`.
    """
    from bridge.skills.dance import run as run_dance

    return {**await run_dance(ctx), "env": SIM_MODE}


# ---------------------------------------------------------------------------
# Tool: say
# ---------------------------------------------------------------------------

@mcp.tool(
    meta=skill_meta(
        classification="speech",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=3.0,
        works_sim=False,
        works_real=True,
        typical_failure_modes=["voice_service_no_answer", "bridge_disconnected"],
    )
)
async def say(
    text: Annotated[
        str,
        Field(
            min_length=1,
            max_length=500,
            description="What the robot should say, spoken aloud through its own speaker.",
        ),
    ],
    language: Annotated[
        Literal["english", "chinese"],
        Field(
            description=(
                "Which voice to use. The robot cannot mix languages in one "
                "utterance — send separate calls instead."
            )
        ),
    ] = "english",
) -> dict:
    """Speak text aloud through the robot's own speaker (voice service, TTS).

    Real on hardware: on-robot text-to-speech, no cloud round-trip and no API
    key. Logs only on stub and sim, which have no speaker.

    Worth reaching for more than it sounds. Speech appears not to be gated by
    the locomotion FSM, so it is a channel the robot still has when motion is
    being refused — which is a situation this robot gets into. Saying what you
    are about to do, or that you are stuck, is usually better than silence when
    a person is standing next to a humanoid.

    One utterance at a time, and one language per utterance: the firmware has
    no mixed Chinese/English voice.
    """
    log.info("say.called", text=text, language=language, sim_mode=SIM_MODE)

    if SIM_MODE != "real":
        return {
            "status": "ok",
            "spoken": text,
            "language": language,
            "note": f"SIM_MODE={SIM_MODE} has no speaker — logged, not spoken.",
            "env": SIM_MODE,
            "stub": True,
        }

    import asyncio

    from bridge.sdk import g1_protocol, g1_rpc

    speaker_id = (
        g1_protocol.Speaker.CHINESE if language == "chinese" else g1_protocol.Speaker.ENGLISH
    )
    # Off the event loop: TTS acks on completion of synthesis, so a long
    # sentence would otherwise stall every other tool call — including
    # stop_everything.
    code, data = await asyncio.to_thread(g1_rpc.speak, text, speaker_id)

    return {
        "status": "ok" if code == 0 else "failed",
        "spoken": text if code == 0 else None,
        "language": language,
        "rpc_code": code,
        "rpc_data": data,
        "error": None if code == 0 else f"rpc_error_code_{code}",
        "env": SIM_MODE,
        "stub": False,
    }


# ---------------------------------------------------------------------------
# Tools: remember_landmark / recall_landmark / list_landmarks / forget_landmark
# ---------------------------------------------------------------------------


@mcp.tool(
    meta=skill_meta(
        classification="memory",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=0.1,
        works_sim=True,
        works_real=False,
        preconditions=["pose_available"],
        typical_failure_modes=["no_pose"],
    )
)
def remember_landmark(
    name: Annotated[
        str,
        Field(
            min_length=1,
            max_length=64,
            description="Name to save the current pose under, e.g. 'kitchen' or 'charging_dock'.",
        ),
    ],
) -> dict:
    """Save the robot's current world-frame pose under a name, for later `recall_landmark`.

    Needs a live pose, which now works on real hardware too (vendor odometry).
    Fails cleanly with `no_pose` if none is available — call `get_state` first
    if unsure.

    Landmarks persist across bridge restarts. They do NOT survive a robot
    reboot: odometry re-origins, so the coordinates stop referring to the same
    physical place. `recall_landmark` and `list_landmarks` report that as
    `frame_stale` — check it before walking anywhere.
    """
    from bridge.skills.landmarks import get_store as get_landmark_store

    state = get_state()
    pose = state.get("pose")
    if pose is None:
        return {"status": "failed", "name": name, "error": "no_pose"}

    landmark = get_landmark_store().remember(
        name, pose, frame_tick=_current_frame_tick()
    )
    log.info("remember_landmark.saved", name=name, pose=landmark.to_dict())
    return {"status": "ok", **landmark.to_dict()}


def _current_frame_tick() -> int | None:
    """Control-board `tick`, identifying the odometry frame landmarks live in.

    None when unknown (stub mode, or no lowstate yet) — callers must treat that
    as "cannot tell", never as "frame is fine".
    """
    if SIM_MODE == "stub":
        return None
    try:
        from bridge.sdk.state import get_sampler

        return int(get_sampler().get_state().get("raw", {}).get("tick") or 0) or None
    except Exception as exc:
        log.warning("frame_tick.unavailable", error=str(exc))
        return None


def _with_frame_status(payload: dict, landmark) -> dict:
    """Annotate a landmark result with whether its frame still exists."""
    from bridge.skills.landmarks import frame_is_stale

    stale = frame_is_stale(landmark.frame_tick, _current_frame_tick())
    payload["frame_stale"] = stale
    if stale:
        payload["warning"] = (
            "Saved before the robot rebooted: odometry re-origined, so these "
            "coordinates no longer refer to the same physical place. Do NOT "
            "walk to them — re-save the landmark from the robot's current pose."
        )
    return payload


@mcp.tool(
    meta=skill_meta(
        classification="memory",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=0.05,
        works_sim=True,
        works_real=True,
        typical_failure_modes=["not_found"],
    )
)
def recall_landmark(
    name: Annotated[
        str, Field(min_length=1, max_length=64, description="Landmark name to recall.")
    ],
) -> dict:
    """Recall a saved landmark pose — feed the x/y straight into `walk_to`.

    Check `frame_stale` first. When true, the robot rebooted since this was
    saved and the coordinates now point somewhere else entirely — re-save
    from the current pose instead of navigating to them."""
    from bridge.skills.landmarks import get_store as get_landmark_store

    landmark = get_landmark_store().recall(name)
    if landmark is None:
        return {"status": "not_found", "name": name}
    return _with_frame_status({"status": "ok", **landmark.to_dict()}, landmark)


@mcp.tool(
    meta=skill_meta(
        classification="memory",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=0.05,
        works_sim=True,
        works_real=True,
    )
)
def list_landmarks() -> dict:
    """List saved landmarks, most recently saved first.

    Each entry carries `frame_stale`; any that are true were saved before a
    robot reboot and their coordinates are no longer meaningful."""
    from bridge.skills.landmarks import get_store as get_landmark_store

    landmarks = get_landmark_store().list_all()
    return {
        "count": len(landmarks),
        "landmarks": [
            _with_frame_status(lm.to_dict(), lm) for lm in landmarks
        ],
    }


@mcp.tool(
    meta=skill_meta(
        classification="memory",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=0.05,
        works_sim=True,
        works_real=True,
        typical_failure_modes=["not_found"],
    )
)
def forget_landmark(
    name: Annotated[
        str, Field(min_length=1, max_length=64, description="Landmark name to delete.")
    ],
) -> dict:
    """Delete a saved landmark. Returns `status=not_found` if the name doesn't exist."""
    from bridge.skills.landmarks import get_store as get_landmark_store

    ok = get_landmark_store().forget(name)
    return {"status": "ok" if ok else "not_found", "name": name}


# ---------------------------------------------------------------------------
# Tool: cancel_task
# ---------------------------------------------------------------------------

@mcp.tool(
    meta=skill_meta(
        classification="task",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=0.3,
        works_sim=True,
        works_real=True,
        typical_failure_modes=["unknown_task_id", "cancel_already_requested", "task_not_running"],
    )
)
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

    Note: with the stdio MCP transport (Claude Code's default), this cannot
    interrupt a tool that the *same* session is currently waiting on — the
    bridge is busy handling that call. Confirmed 2026-08-07: the
    `BRIDGE_TRANSPORT=http` path apps/back uses does support this — two
    separate client sessions can talk to the server concurrently (verified
    against the stub bridge). Not yet confirmed against an actual in-flight
    `walk_to`/`turn` loop — that needs live pose data (sim or real hardware)
    to produce a task that runs long enough to interrupt.
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
# Tool: describe_surroundings
# ---------------------------------------------------------------------------

@mcp.tool(
    meta=skill_meta(
        classification="perception",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=0.1,
        works_sim=True,
        works_real=True,
        typical_failure_modes=["bridge_disconnected", "no_pose"],
    )
)
def describe_surroundings() -> dict:
    """Return a compact, egocentric snapshot of what the robot can perceive.

    Ranges are metres from the robot; bearings are degrees with 0 straight
    ahead and POSITIVE TO THE LEFT — the same sign as `turn`'s
    delta_yaw_radians, so a bearing can be turned toward directly.

    Read `sources` and `notes` before acting. A source reported as `offline`
    means that sense is NOT WORKING, which is different from it reporting
    nothing: an absent `objects` list with `detector: offline` does not mean
    the path is clear, it means nothing looked. `objects_omitted` counts
    obstacles that exist but were not listed.

    Today the perception stack (LiDAR/camera/detector) is not deployed, so this
    honestly reports everything offline rather than an empty scene. Pose comes
    through once the bridge is running against a robot.
    """
    from bridge import world_model
    from bridge.skills.landmarks import get_store

    if SIM_MODE == "stub":
        return world_model.offline().to_dict()

    # Pose is the one source that exists today; perception plugs in beside it
    # without changing this contract.
    pose = None
    pose_age = None
    try:
        from bridge.sdk.state import get_sampler

        state = get_sampler().get_state()
        if state.get("pose"):
            p = state["pose"]
            pose = {
                "x_m": round(float(p["x_meters_world"]), 2),
                "y_m": round(float(p["y_meters_world"]), 2),
                "yaw_deg": round(math.degrees(float(p["yaw_radians_world"])), 1),
            }
            pose_age = state.get("raw", {}).get("pose_age_s")
    except Exception as exc:  # never let telemetry break the snapshot
        log.warning("describe_surroundings.pose_failed", error=str(exc))

    landmarks = []
    if pose is not None:
        for lm in get_store().list_all():
            dx = lm.x_meters_world - float(pose["x_m"])
            dy = lm.y_meters_world - float(pose["y_m"])
            rng = math.hypot(dx, dy)
            bearing = math.degrees(
                math.atan2(dy, dx) - math.radians(float(pose["yaw_deg"]))
            )
            # Normalise into (-180, 180] so "left" and "right" stay meaningful.
            bearing = (bearing + 180.0) % 360.0 - 180.0
            landmarks.append(
                world_model.Observation(
                    label=lm.name, range_m=rng, bearing_deg=bearing
                )
            )

    return world_model.build(
        pose=pose,
        pose_age_s=pose_age,
        landmarks=landmarks,
        # Perception is not deployed yet — these stay False so the snapshot
        # says "offline" instead of implying a clear scene.
        detector_online=False,
        lidar_online=False,
    ).to_dict()


# ---------------------------------------------------------------------------
# Tool: list_active_tasks
# ---------------------------------------------------------------------------

@mcp.tool(
    meta=skill_meta(
        classification="task",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=0.05,
        works_sim=True,
        works_real=True,
    )
)
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

    # The other operator-liveness poll -- see the note in `get_state`. This is
    # the call a fire-and-forget client makes while waiting on a long skill,
    # which is precisely the case watchdog.py exists to cover.
    get_watchdog().touch()

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
    """Run the MCP server.

    Default transport is stdio (Claude Code / Claude Desktop). Set
    ``BRIDGE_TRANSPORT=http`` to serve FastMCP's streamable-http transport at
    ``http://{BRIDGE_HOST}:{BRIDGE_PORT}/mcp`` — how ``apps/back`` connects.
    """
    # Operator-link watchdog (SPEC §10.3). Real hardware only, and OFF unless
    # `LINK_WATCHDOG=on` — read `bridge/watchdog.py` before arming it.
    #
    # Short version: today's tool calls block the transport, so "operator
    # silent while a task runs" is the normal state during any long skill, not
    # a dead link. Arming it now would cancel every walk_to mid-stride. It
    # becomes correct once long skills return a task_id immediately and the
    # operator's progress polls become a real liveness signal.
    if SIM_MODE == "real" and watchdog.ENABLED:
        get_watchdog().start()
    elif SIM_MODE == "real":
        log.info("watchdog.disabled", reason="LINK_WATCHDOG not set")

    if BRIDGE_TRANSPORT in ("http", "streamable-http"):
        log.info(
            "c3po-bridge.start",
            sim_mode=SIM_MODE,
            transport="streamable-http",
            host=BRIDGE_HOST,
            port=BRIDGE_PORT,
        )
        mcp.run(transport="streamable-http")
    else:
        log.info("c3po-bridge.start", sim_mode=SIM_MODE, transport="stdio")
        mcp.run()  # default transport is stdio


if __name__ == "__main__":
    main()
