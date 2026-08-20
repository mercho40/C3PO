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

Registered in `.mcp.json` as `c3po-sim` — a spawned child process speaking
stdio, the default transport. The `c3po-bridge` entry in `.mcp.json` is a
different thing: the daemon running onboard the Jetson, reached over HTTP
(port 8001 via the SSH tunnel) — i.e. the REAL robot.
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

    G1 firmware `wave_above_head` (api_id=7106, data=26). The robot's own
    GetActionList reports this action as UNGATED — no FSM and no mode_machine
    requirement — and it executed on hardware 2026-08-15 from fsm_id 802,
    taking 7.3 s because the arm service acks on completion of the motion, not
    on receipt. Only one action in the whole table (`turn_back_wave`, id 1) is
    FSM-gated.

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
        works_real=False,
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


@mcp.tool(
    meta=skill_meta(
        classification="posture",
        danger_level="high",
        status="real",
        cancellable=False,
        expected_duration_s=3.0,
        works_sim=False,
        works_real=True,
        preconditions=["fsm_state_is_preparation", "operator_present"],
        typical_failure_modes=["fsm_transition_rejected", "no_motion_controller_loaded"],
    )
)
async def start_walking_waist(ctx: Context) -> dict:
    """Enter Walk Motion-3Dof-waist (FSM 501) — the 29-DoF walk program.

    500 and 501 are two different walk programs chosen by how many degrees of
    freedom the waist has, not a generic start and a variant. Unitree documents
    `mode_machine` as `4:23-Dof; 5:29-Dof; 6:27-Dof`, and this robot reports
    **5** — so 501 is the program it actually implements, and 500 the
    other variant's.

    That matters because `start_walking` (500) has been observed returning rpc
    code 0 while never leaving StandUp. A recognised-but-not-implemented id
    looks exactly like that: accepted, then declined by the controller.

    **Solved: 501 IS this robot's walk program — it walked under it on
    2026-08-15.** Still enter it from a supervised window with the operator
    ready to damp, not from an autonomous plan. See
    `docs/ROBOT-API.md` §12.

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
        works_real=False,
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
        works_real=False,
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
        works_real=False,
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
        works_real=False,
        typical_failure_modes=["fsm_transition_rejected", "transport_unsupported"],
    )
)
async def squat(ctx: Context) -> dict:
    """Enter Squat mode — robot crouches to a lowered stance.

    G1 firmware Squat (mode 2) and SquatUp (706) collapse to the same
    physical pose at different control gains. From Squat you can only go to
    Damp on the FSM, so this is a terminal posture until you reset.

    ⚠️ Observed 2026-08-15: sending this from Damp returns rpc code 0 and does
    NOT transition — `fsm_id` stays 1. That contradicts Unitree's own G1
    example, which bring-ups via Damp -> Squat2StandUp(706) -> Move. On this
    robot the route that works is `prepare` (4), then 501. Do not build a
    bring-up on 706 here.

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
        Literal["spanish", "english", "chinese"],
        Field(
            description=(
                "Language of `text`. **spanish** is this deployment's operating "
                "language and the default; it is synthesised on the robot and "
                "played as audio, so it does not use the firmware's voices at "
                "all. english/chinese use the firmware's own two voices. Match "
                "this to the text you actually wrote — labelling Spanish as "
                "english does not fail, it produces an English voice attempting "
                "Spanish phonemes. One language per utterance; send separate "
                "calls to switch."
            )
        ),
    ] = "spanish",
) -> dict:
    """Speak text aloud through the robot's own speaker (voice service, TTS).

    TWO DIFFERENT MECHANISMS LIVE BEHIND ONE TOOL, and the split is forced by
    the firmware rather than chosen:

      spanish  -> synthesised locally by Piper (`es_AR/daniela`), resampled to
                  16 kHz, and pushed as PCM through `PlayStream` (1003).
      en / zh  -> the firmware's own TTS (1001), `speaker_id` 1 / 0.

    The firmware has exactly two voices and no third. It was verified on this
    robot that neither reads Spanish intelligibly, and — the part that matters —
    passing Spanish to firmware TTS returns **rpc_code 0** while emitting
    unusable audio. A false success, which is why Spanish never touches that
    path here and why this tool refuses loudly instead of falling back to it
    when local synthesis is unavailable. Silence with an explanation beats
    confident noise. See `docs/DECISIONS.md` D6.1/D6.3.

    Everything is local and offline: no cloud, no API key, and Spanish still
    works with the network down — which is exactly when "estoy atascado" is
    worth saying. Logs only on stub and sim, which have no speaker.

    INTERRUPTION IS FREE AND IMPLICIT for Spanish. `PlayStream`'s `stream_id` is
    the interrupt model — a new id interrupts whatever is playing — and each
    call here mints a new one. So a second `say` barges in over the first rather
    than queueing behind it, with no stop call needed. Firmware TTS has no such
    model and no documented behaviour for overlapping calls.

    The robot's speaker is SHARED with the co-tenant `gemm` stack and there is
    no arbitration. We publish under our own `app_name` ("c3po"), so their
    assistant cannot stop our speech and we cannot stop theirs — expect
    occasional talking over each other; it is not a bug to fix here.

    Worth reaching for more than it sounds. Speech is not gated by the
    locomotion FSM, so it is a channel the robot still has when motion is being
    refused — which is a situation this robot gets into. Saying what you are
    about to do, or that you are stuck, is usually better than silence when a
    person is standing next to a humanoid.

    Measured rather than assumed, 2026-08-21: the voice service answered
    GET_VOLUME with rpc_code 0 while fsm_id was 0 (ZeroTorque) — the robot limp,
    with motion categorically unavailable and another stack owning it. What that
    establishes is that the SERVICE is reachable in that state; it is not proof
    that audio reached the speaker, which cannot be checked without making noise
    in a shared room. That confirmation has since happened: Spanish played audibly
    through the speaker on 2026-08-21, so the whole path is proven, not merely
    accepted by the RPC.

    One utterance at a time, and one language per utterance: the firmware has
    no mixed Chinese/English voice. There is also no documented behaviour for
    calling it while speech is already playing, so this is not the tool to use
    for anything that must be interruptible — `PlayStream` is, and its
    `stream_id` is the interrupt model (same id concatenates, new id barges in).
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
    import time

    from bridge.sdk import g1_protocol, g1_rpc

    if language == "spanish":
        from bridge.skills import tts

        ok, why = tts.available()
        if not ok:
            # Refuse rather than degrade. The available fallback is the English
            # voice reading Spanish, which is precisely the rpc_code-0-and-noise
            # failure this path exists to avoid — it would look like success in
            # the logs and be unusable in the room.
            return {
                "status": "failed",
                "spoken": None,
                "language": language,
                "error": "spanish_tts_unavailable",
                "detail": why,
                "note": (
                    "Refusing to fall back to the English voice: it reads Spanish "
                    "unintelligibly while reporting success. Install Piper, or "
                    "call again with language='english' and English text."
                ),
                "env": SIM_MODE,
                "stub": False,
            }

        # One stream_id per utterance, minted fresh so this call interrupts
        # whatever is currently playing. Milliseconds is the vendor's own
        # convention; it only has to be different from the last one.
        stream_id = f"c3po-{int(time.time() * 1000)}"

        def _synth_and_play() -> tuple[int, str | None, float]:
            pcm = tts.synthesize(text)
            code, data = g1_rpc.play_pcm(pcm, stream_id)
            return code, data, tts.duration_s(pcm)

        try:
            # Both halves off the event loop. Synthesis is CPU-bound and
            # PlayStream is a blocking RPC per chunk; on the loop, a long
            # sentence would stall every other tool call — stop_everything
            # included, which is the one that must never wait behind speech.
            code, data, seconds = await asyncio.to_thread(_synth_and_play)
        except tts.TtsUnavailable as exc:
            return {
                "status": "failed", "spoken": None, "language": language,
                "error": "spanish_tts_failed", "detail": str(exc),
                "env": SIM_MODE, "stub": False,
            }

        return {
            "status": "ok" if code == 0 else "failed",
            "spoken": text if code == 0 else None,
            "language": language,
            "rpc_code": code,
            "rpc_data": data,
            # PlayStream acks on RECEIPT, not on completion — the vendor's own
            # example fires it and sleeps a fixed 3 s. So this is computed from
            # the bytes sent and is the only honest answer to "is it still
            # talking?". Nothing here waits for it.
            "speech_seconds": round(seconds, 2),
            "stream_id": stream_id,
            "via": "piper+playstream",
            "error": None if code == 0 else f"rpc_error_code_{code}",
            "env": SIM_MODE,
            "stub": False,
        }

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
        "via": "firmware_tts",
        "error": None if code == 0 else f"rpc_error_code_{code}",
        "env": SIM_MODE,
        "stub": False,
    }


# ---------------------------------------------------------------------------
# Tools: arm_navigation / disarm_navigation
# ---------------------------------------------------------------------------

@mcp.tool(
    meta=skill_meta(
        classification="locomotion",
        danger_level="high",
        status="real",
        cancellable=False,
        expected_duration_s=0.1,
        works_sim=True,
        works_real=True,
        typical_failure_modes=["perception_link_down", "no_planner_traffic"],
    )
)
def arm_navigation(
    reason: Annotated[
        str,
        Field(
            min_length=3,
            max_length=200,
            description=(
                "Why you are arming, in one line. This is written to the log "
                "that gets read after something goes wrong, so say what the "
                "robot is about to do and who asked for it."
            ),
        ),
    ],
    seconds: Annotated[
        float,
        Field(
            ge=1.0,
            le=120.0,
            description=(
                "How long the gate stays open. It closes itself after this "
                "whatever happens — keep it to the length of the manoeuvre you "
                "are actually supervising, not the length of your plan."
            ),
        ),
    ] = 30.0,
) -> dict:
    """Let Nav2 drive the robot. THE MOST DANGEROUS TOOL HERE — read this.

    Nav2 plans continuously and publishes velocities at 20 Hz whether or not
    anything is listening. Everything it produces is refused by a default-closed
    gate in this process; this tool opens that gate. Nothing else in the system
    does, and until it is called the robot cannot be driven by the planner at
    all, no matter what the planner decides.

    So this is the single point where a planning stack becomes a moving 1.3 m
    humanoid. Treat it as such:

    - **A person must be watching.** Not "nearby" — watching, with the remote in
      hand and a thumb on the e-stop. Nav2 obeys a costmap, and a costmap can be
      wrong in ways that look fine on a screen.
    - **Arm for the manoeuvre, not the plan.** The gate closes itself after
      `seconds`. That timeout is the safety property; renewing it is cheap and
      forgetting to close it is exactly the failure the timeout exists for.
    - **`stop_everything` closes it**, synchronously and before anything else it
      does. Without that, stopping would only pause: the burst halts the gait
      and Nav2's next tick would walk the robot on 50 ms later.

    Arming does not command motion by itself. If no planner is publishing, the
    gate opens onto silence and closes again — which is exactly what the returned
    `cmd_vel_age_s` tells you: `null` means nothing is planning, so arming was
    pointless rather than dangerous.
    """
    log.warning("arm_navigation.called", seconds=seconds, reason=reason,
                sim_mode=SIM_MODE)

    from bridge.sdk.perception_link import get_link

    link = get_link()
    status_before = link.status()
    if not status_before.get("cmd_vel_received"):
        # Not refused — armed anyway, and told. A planner that has not published
        # yet is normal during bring-up, and refusing here would make arming
        # depend on timing the operator cannot see.
        log.warning("arm_navigation.no_planner_traffic")

    link.enable(reason=reason, ttl_s=seconds)
    status = link.status()
    return {
        "status": "armed",
        "seconds": seconds,
        "reason": reason,
        "expires_in_s": status.get("arm_expires_in_s"),
        # None here means no planner is publishing: the gate is open onto
        # nothing, so nothing will move until Nav2 starts.
        "cmd_vel_age_s": status.get("cmd_vel_age_s"),
        "clamps": status.get("clamps"),
        "warning": (
            "The robot can now be driven by Nav2. A person must be watching "
            "with the e-stop in reach. The gate closes itself in "
            f"{seconds:g}s, or immediately on stop_everything."
        ),
        "env": SIM_MODE,
    }


@mcp.tool(
    meta=skill_meta(
        classification="locomotion",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=0.1,
        works_sim=True,
        works_real=True,
    )
)
def disarm_navigation(
    reason: Annotated[
        str, Field(max_length=200, description="Why, for the log.")
    ] = "operator disarmed",
) -> dict:
    """Close the gate. Nav2 keeps planning; nothing it plans reaches the legs.

    Always safe to call, including when already closed. Prefer this over waiting
    for the arm timeout once a manoeuvre is finished — the timeout is a backstop
    for the case where somebody forgot, not the normal way to end.

    This is NOT a stop: it prevents further motion rather than arresting motion
    already underway. `stop_everything` is what halts the robot, and it closes
    this gate as its first act.
    """
    log.warning("disarm_navigation.called", reason=reason, sim_mode=SIM_MODE)

    from bridge.sdk.perception_link import get_link

    link = get_link()
    was_open = link.is_enabled()
    link.disable(reason=reason)
    return {
        "status": "disarmed",
        "was_armed": was_open,
        "reason": reason,
        "note": (
            "Nav2 is still planning and still publishing; the gate now refuses "
            "all of it. This prevents motion — it does not arrest motion already "
            "in progress. Use stop_everything for that."
        ),
        "env": SIM_MODE,
    }


# ---------------------------------------------------------------------------
# Tool: listen
# ---------------------------------------------------------------------------

@mcp.tool(
    meta=skill_meta(
        classification="perception",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=0.1,
        works_sim=False,
        works_real=True,
        typical_failure_modes=["mic_never_opened", "stt_not_installed"],
    )
)
async def listen(
    wait_s: Annotated[
        float,
        Field(
            ge=0.0,
            le=30.0,
            description=(
                "0 (the default) returns instantly with whatever has already "
                "been heard — use this while doing something else. Give it a "
                "few seconds ONLY when you have just asked a question and are "
                "waiting for the answer; the call blocks for that long."
            ),
        ),
    ] = 0.0,
) -> dict:
    """What has been said to the robot. Returns instantly by default.

    THE ROBOT IS ALWAYS LISTENING WHEN IT CAN BE — a background thread consumes
    the microphone continuously, so speech is transcribed before you ask for it.
    This tool reads that buffer; it does not start listening, and calling it more
    often does not make the robot hear more.

    Each call CONSUMES what it returns. Two calls in a row will not show the same
    utterance twice, so act on what you get.

    WHETHER THE ROBOT HEARS CONTINUOUSLY DEPENDS ON ITS AUDIO SOURCE, and
    `always_listening` in the result says which you have:

        true   a USB microphone is attached to the Jetson. Continuous, no
               button, and an empty result genuinely means nobody spoke.
        false  the robot's own mic array is in use. It is PUSH-TO-TALK: it
               streams only while a person holds L1+L2 on the remote
               (`docs/ROBOT-HARDWARE.md` §8.2), and no RPC can open it —
               `vui_service` has no capture function. An empty result then
               usually means nobody is holding the remote, NOT that the room is
               silent.

    In the push-to-talk case the two are still distinguishable:

        mic_ever_open: false   nobody has held the button; the robot has never
                               had the chance to hear anything
        mic_ever_open: true    audio has arrived before; empty now means quiet

    Never conclude "nobody answered" from an empty result without checking that
    flag. If it is false, say out loud that you cannot hear unless they hold the
    button — the person may not know.

    Use `wait_s=0` while walking or working: it costs nothing and cannot stall
    anything. Reach for a few seconds only right after asking a question, and
    remember that the whole bridge — including `stop_everything` — waits with
    you for that long.
    """
    if SIM_MODE != "real":
        return {
            "status": "ok", "heard": [], "transcript": "", "stop_heard": None,
            "mic_ever_open": False,
            "note": f"SIM_MODE={SIM_MODE} has no microphone.",
            "env": SIM_MODE, "stub": True,
        }

    import asyncio

    from bridge.skills import listen as listen_skill

    listener = listen_skill.get_mic_listener()
    if not listener.is_running():
        ok, why = listen_skill.available()
        if not ok:
            return {
                "status": "failed", "heard": [], "transcript": "",
                "error": "stt_not_installed", "detail": why,
                "env": SIM_MODE, "stub": False,
            }
        listener.start()

    items = listener.poll()
    if not items and wait_s > 0:
        # Poll rather than block on a condition: the listener is a plain thread
        # and this keeps the wait cancellable and bounded, with the event loop
        # free the whole time.
        deadline = asyncio.get_running_loop().time() + wait_s
        while not items and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.25)
            items = listener.poll()

    diag = listener.diagnostics()
    source = listen_skill.describe_audio_source()
    speech = [i for i in items if i["kind"] == "speech"]
    stops = [i for i in items if i["kind"] == "stop"]

    return {
        "status": "ok",
        "heard": [{"text": i["text"], "age_s": i["age_s"]} for i in speech],
        "transcript": " ".join(i["text"] for i in speech).strip(),
        # Reported, not acted on. Whoever is holding the remote to make the robot
        # hear at all already has a physical e-stop under their thumb.
        "stop_heard": stops[0]["text"] if stops else None,
        "mic_ever_open": diag["mic_ever_open"],
        "seconds_since_audio": diag["seconds_since_audio"],
        "listener_error": diag["error"],
        # Whether the robot is listening continuously or only while a button is
        # held. The agent needs this to interpret silence: with always_on true,
        # an empty result really does mean nobody spoke.
        "always_listening": source["always_on"],
        "audio_source": source["source"],
        "note": (
            None if diag["mic_ever_open"] or source["always_on"]
            else "The microphone has never opened. The robot's own mic is "
                 "push-to-talk — it hears nothing unless somebody holds L1+L2 on "
                 "the remote. This is NOT silence in the room. For continuous "
                 "listening a USB microphone has to be plugged into the Jetson."
        ),
        "env": SIM_MODE, "stub": False,
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

    # THE PERCEPTION REPORT, if the container is up and publishing on domain 42.
    #
    # This was hardcoded to detector_online=False with a comment saying
    # perception was not deployed. It has been deployed for a while: the link
    # runs, reports arrive, and `world_model.from_report` — written for exactly
    # this, fully tested — was called from nowhere. The robot's eyes were built
    # and not plugged in, the same way the link itself was.
    #
    # `latest_report()` returns None for a container that is absent, one whose
    # report is older than REPORT_OFFLINE_AFTER_S, and one whose version was
    # refused. All three are the same fact to a model — nothing looked — and
    # from_report degrades all three identically. That is why the fallback below
    # is not a special case but the honest snapshot.
    report = None
    report_age = None
    try:
        from bridge.sdk.perception_link import get_link

        report, report_age = get_link().latest_report()
    except Exception:
        # Never let a perception fault take down the one tool an operator uses
        # to ask what is going on. Degrading to "offline" is always available.
        log.exception("describe_surroundings.perception_read_failed")

    if report is not None:
        merged = dict(report)
        # The container reports the pose FAST-LIO believes; the bridge reports
        # the one the control board believes. Prefer perception's — it is the
        # frame the objects were measured in, so mixing the two would place
        # detections against a pose they were never relative to. Fall back to
        # ours only when the container has none.
        if not merged.get("pose") and pose is not None:
            merged["pose"] = pose
            merged["pose_age_s"] = pose_age
        snapshot = world_model.from_report(merged, landmarks=landmarks).to_dict()
        if report_age is not None:
            snapshot["report_age_s"] = report_age
        return snapshot

    return world_model.build(
        pose=pose,
        pose_age_s=pose_age,
        landmarks=landmarks,
        # No usable report: absent, stale, or version-refused. Explicitly
        # offline rather than an empty scene — "nothing detected" and "nothing
        # looked" must never be the same answer (D7).
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

# ---------------------------------------------------------------------------
# Telemetry over plain HTTP — NOT MCP
# ---------------------------------------------------------------------------
#
# WHY THIS IS NOT AN MCP TOOL. Two independent reasons, either sufficient.
#
# 1. It is not for the model. A 240x240 PNG is meaningless to an LLM and would
#    be a catastrophe for the token budget — D7 sizes the model's view of the
#    world at a few hundred tokens on purpose. This is for the OPERATOR's eyes,
#    and the operator's console is a browser.
#
# 2. It is the one path that works today. `apps/back` cannot reach this server
#    over MCP at all: the SDK's StreamableHTTPClientTransport dies under Bun
#    with "socket connection was closed unexpectedly", while the identical code
#    under Node connects fine (docs/OPERATIONS.md). That break is specific to
#    the SDK transport holding a long-lived stream — a plain `fetch()` in Bun to
#    this same server already works. So a plain GET is not a workaround, it is
#    the correct shape for read-only telemetry AND it happens to be the first
#    robot data that can reach the web while that blocker stands.
#
# READ-ONLY, AND STRUCTURALLY SO. This route reads a cached payload the link
# already received. It cannot arm the gate, cannot publish, and cannot reach
# anything that actuates — the whole cmd_vel path is untouched by it. The bridge
# still binds loopback with no auth of its own, so this is reached the same way
# everything else is: through the SSH tunnel, not from the school LAN.


@mcp.custom_route("/telemetry/costmap.png", methods=["GET"])
async def costmap_png(request):  # noqa: ANN001, ANN201 - starlette types
    """Nav2's global costmap as an indexed PNG, or 503 when there is none.

    503 rather than a blank image, deliberately: a blank map and "no map yet"
    are different facts, and an operator who cannot tell them apart is the map
    equivalent of `objects: []` from an offline detector. The metadata a
    renderer needs to place the image — resolution, origin, size, age — rides on
    headers so the body stays a plain PNG the browser can put in an <img>.
    """
    from starlette.responses import JSONResponse, Response

    from bridge.sdk.perception_link import COSTMAP_STALE_AFTER_S, get_link

    payload, age = get_link().latest_costmap()
    if payload is None:
        return JSONResponse(
            {
                "error": "no costmap received",
                "hint": (
                    "the costmap comes from Nav2's global_costmap, so it exists only "
                    "while a nav2 stage is up: `perception_up nav2-fake` (no sensors) "
                    "or `perception_up nav2` (claims both sensors)"
                ),
            },
            status_code=503,
        )

    import base64

    try:
        png = base64.b64decode(payload["png_base64"])
    except Exception:
        return JSONResponse({"error": "costmap payload is not decodable"}, status_code=502)

    stale = age is not None and age > COSTMAP_STALE_AFTER_S
    return Response(
        content=png,
        media_type="image/png",
        headers={
            # Placement, so the console can put the map under the robot marker.
            # origin is the pose of the BOTTOM-LEFT cell in `frame`; the PNG is
            # top-down, so draw from (origin_x, origin_y + height*res) downward.
            "X-C3PO-Frame": str(payload.get("frame_id", "")),
            "X-C3PO-Width": str(payload.get("width", "")),
            "X-C3PO-Height": str(payload.get("height", "")),
            "X-C3PO-Resolution-M": str(payload.get("resolution_m", "")),
            "X-C3PO-Origin-X-M": str(payload.get("origin_x_m", "")),
            "X-C3PO-Origin-Y-M": str(payload.get("origin_y_m", "")),
            # Age travels with the image so a stale map can be SHOWN AS stale
            # rather than shown as current. Never cache: at 1 Hz a cached map is
            # a lie within a second.
            "X-C3PO-Age-S": "" if age is None else f"{age:.2f}",
            "X-C3PO-Stale": "true" if stale else "false",
            "Cache-Control": "no-store",
        },
    )


@mcp.custom_route("/telemetry/gate", methods=["GET"])
async def gate_status(request):  # noqa: ANN001, ANN201 - starlette types
    """The cmd_vel gate's own account of itself: what it got, what it refused.

    perception_link.status() has always described itself as "what Stage 4 reads
    while Nav2 plans against a shut gate", but nothing exposed it — no tool, no
    route — so the one property Stage 4 exists to prove was the one property
    that could not be observed from outside the process.

    The interesting reading is the CONJUNCTION, and it is worth stating because
    either half alone is misleading:

        cmd_vel_received      climbing   - Nav2 is planning and publishing
        dropped_while_disabled climbing  - every one of them was refused
        last_sent             null       - nothing ever reached the robot

    `dropped_while_disabled` rising on its own could just mean nothing is
    listening. `last_sent: null` on its own could just mean nothing was ever
    sent. Together they say the gate is doing its job under live load, which is
    a different and much stronger claim than "the robot did not move".

    READ-ONLY, AND STRUCTURALLY SO — same as the costmap route above. It reads
    counters. There is no arm/disarm here and there must never be: arming is a
    deliberate, audited, expiring action (`arm_navigation`), not a GET.
    """
    from starlette.responses import JSONResponse

    from bridge.sdk.perception_link import get_link

    # diagnostics() already embeds status() as its "gate" key, so it is the only
    # call made here — asking for both would evaluate the gate twice and could
    # report two different instants under one timestamp.
    diag = get_link().diagnostics()
    body = dict(diag.get("gate") or {})
    # Whether anything is publishing at all — otherwise a quiet gate and a quiet
    # DOMAIN look identical, and someone spends an afternoon debugging a gate
    # that never had a single message to refuse.
    body["link"] = {
        "started": diag.get("started"),
        "domain_id": diag.get("domain_id"),
        "reports_received": diag.get("reports_received"),
    }
    return JSONResponse(body, headers={"Cache-Control": "no-store"})


def main() -> None:
    """Run the MCP server.

    Default transport is stdio (Claude Code / Claude Desktop). Set
    ``BRIDGE_TRANSPORT=http`` to serve FastMCP's streamable-http transport at
    ``http://{BRIDGE_HOST}:{BRIDGE_PORT}/mcp`` — how ``apps/back`` connects.
    """
    # Operator-link watchdog (docs/ARCHITECTURE.md §7). Real hardware only, and OFF unless
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

    # JOIN DOMAIN 42. Nothing did this before, and the omission was invisible
    # because every symptom looked like a problem somewhere else:
    #
    #   * /telemetry/costmap.png returned 503 "no costmap received" while Nav2
    #     was publishing costmaps a process away — indistinguishable from Nav2
    #     not running, which is exactly what the hint text suggests.
    #   * `ros2 topic info /c3po/cmd_vel --verbose` reported Subscription
    #     count: 0, so the cmd_vel gate had nothing to refuse. A gate that
    #     never receives cannot be shown to be closed; it is just absent.
    #   * describe_surroundings kept reporting perception offline, which reads
    #     as an honest "not deployed yet" rather than as an unwired link.
    #
    # PerceptionLink was fully written and tested throughout — it was simply
    # never started, so domain 42 had no participant on this side at all.
    #
    # STARTING IS NOT ARMING, and that separation is what makes this safe to do
    # at boot. start() creates the participant and the readers and nothing
    # else; the gate stays default-closed with disabled_reason "never armed".
    # _tick() returns before touching _forward while the gate is shut, so
    # _default_forward — the one path where a Twist becomes SET_VELOCITY —
    # remains unreachable until something arms the gate deliberately. Nothing
    # can today: `arm_navigation` is Stage 8 and does not exist yet.
    #
    # Failure here must not take the bridge down. A robot whose MCP server
    # refuses to boot has no stop_everything, which is a far worse state than
    # one that cannot see its costmap — and this is the first thing that runs
    # after a reboot, where DDS is exactly what is flaky (see the eth0 boot
    # race in docs/OPERATIONS.md).
    if SIM_MODE in ("real", "isaac"):
        # Local import for the same reason the routes use one: module scope must
        # stay free of CycloneDDS so the bridge imports on a laptop.
        from bridge.sdk.perception_link import get_link

        try:
            get_link().start()
            log.info("perception.link.started", domain=42, gate="closed")
        except Exception:
            log.exception("perception.link.start_failed")

        # START LISTENING AT BOOT, not on the first `listen` call. Otherwise the
        # first thing anyone says is the one thing the robot cannot hear: the
        # buffer would only begin filling once the agent thought to ask, and
        # people speak before they are asked to.
        #
        # This is cheap while nobody is talking — the mic is push-to-talk, so a
        # closed feed delivers no packets and the thread sits in a socket
        # timeout. There is no audio to decode until somebody holds L1+L2.
        #
        # And that button is the privacy boundary, which is worth being explicit
        # about: the robot transcribes only while a person is deliberately
        # holding a control to talk to it. Transcripts live in a bounded
        # in-memory buffer and are never written to disk.
        try:
            from bridge.skills.listen import available as stt_available
            from bridge.skills.listen import get_mic_listener

            ok, why = stt_available()
            if ok:
                get_mic_listener().start()
                log.info("mic_listener.started")
            else:
                # Not an error: the bridge runs fine without ears, and `listen`
                # reports the reason with the fix attached when asked.
                log.info("mic_listener.disabled", reason=why)
        except Exception:
            log.exception("mic_listener.start_failed")

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
