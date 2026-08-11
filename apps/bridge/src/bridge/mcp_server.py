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
# Isaac Sim's `unitree_sim_isaaclab` scene doesn't subscribe to those topics,
# so under SIM_MODE=isaac the dispatcher returns `phase=logged_only` — the
# request was constructed but not delivered. Under SIM_MODE=real these
# dispatch for real over DDS RPC (`bridge.sdk.g1_rpc`); no WebRTC involved.
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


@mcp.tool()
async def zero_torque(ctx: Context) -> dict:
    """Enter Zero Torque mode — actuators receive no command. Limp robot.

    On the G1 FSM: legal only from Damp. From Zero Torque you can only go
    back to Damp. Use as the "fully off" terminal state when storing the
    robot or stepping away.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("zero_torque", ctx), "env": SIM_MODE}


@mcp.tool()
async def start_walking(ctx: Context) -> dict:
    """Enter Walk mode — locomotion FSM activates; arm gestures become available.

    On the G1 FSM: legal only from Preparation. Until you call this, walk_to /
    turn / wave / point_at won't get a meaningful response on real hardware.
    Typical sequence: damp → prepare → start_walking → walk_to / wave.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("start_walking", ctx), "env": SIM_MODE}


@mcp.tool()
async def sit_g1(ctx: Context) -> dict:
    """Enter Seating mode — robot adopts a seated posture.

    G1 firmware mode index 3 on api_id=7101. Typically reached from Damp;
    the exact accepted-from-set depends on firmware. Use for a calm,
    low-energy presentation state.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("sit_g1", ctx), "env": SIM_MODE}


@mcp.tool()
async def lie_up(ctx: Context) -> dict:
    """Enter Lie-Up mode — robot transitions to a face-up lying pose.

    G1 firmware mode index 702 on api_id=7101. Legal target from Damp.
    Useful pre-storage state and starting pose for recovery sequences.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("lie_up", ctx), "env": SIM_MODE}


@mcp.tool()
async def squat(ctx: Context) -> dict:
    """Enter Squat mode — robot crouches to a lowered stance.

    G1 firmware Squat (mode 2) and SquatUp (706) collapse to the same
    physical pose at different control gains. From Squat you can only go to
    Damp on the FSM, so this is a terminal posture until you reset.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("squat", ctx), "env": SIM_MODE}


@mcp.tool()
async def shake_hand(ctx: Context) -> dict:
    """Extend the right hand for a handshake.

    G1 firmware "shake hands" (api_id=7106, data=27). Requires a locomotion-
    active FSM state (Walk / Walk(waist) / Run) on real hardware.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("shake_hand", ctx), "env": SIM_MODE}


@mcp.tool()
async def hug(ctx: Context) -> dict:
    """Open arms wide for a hug.

    G1 firmware "hug" (api_id=7106, data=19). Requires a locomotion-active
    FSM state. The hug gesture is one of the four that the firmware hides
    while in Run mode — prefer Walk or Walk(waist) for this one.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("hug", ctx), "env": SIM_MODE}


@mcp.tool()
async def clap(ctx: Context) -> dict:
    """Bring hands together to clap.

    G1 firmware "clap" (api_id=7106, data=17). Requires a locomotion-active
    FSM state.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("clap", ctx), "env": SIM_MODE}


@mcp.tool()
async def release_arm(ctx: Context) -> dict:
    """Return arms to a neutral / idle position.

    G1 firmware "release arm" (api_id=7106, data=99). Call this after any
    other gesture to settle the arms back to their default hanging pose.

    Isaac Sim: logged only.
    """
    from bridge.skills._g1_request import run_g1_request

    return {**await run_g1_request("release_arm", ctx), "env": SIM_MODE}


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
# Tools: remember_landmark / recall_landmark / list_landmarks / forget_landmark
# ---------------------------------------------------------------------------


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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


@mcp.tool()
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

@mcp.tool()
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
