"""Navigation: the Nav2 gate (arm/disarm_navigation), Nav2 goals and their monitor, and
go_to_object, which walks to what the camera sees with Nav2 when it may and walk_to when not."""

from __future__ import annotations

import asyncio
import math
import time
from typing import Annotated, Any, Literal

import structlog
from mcp.server.fastmcp import Context
from pydantic import Field

from bridge import config, motion_announce, safety, server
from bridge.server import mcp
from bridge.skill_meta import meta as skill_meta
from bridge.tools.memory import memory_pose

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Tool: go_to_object
# ---------------------------------------------------------------------------


def _live_detections() -> tuple[list[dict], float | None]:
    """The raw report's objects and the report's age; none unless the detector is online.

    Raw, not surroundings_snapshot(): the snapshot loses the difference between
    a missing age_s and a zero one.
    """
    try:
        from bridge.sdk.perception_link import get_link

        report, age = get_link().latest_report()
    except Exception:
        log.exception("go_to_object.perception_read_failed")
        return [], None
    if not report or age is None or not report.get("detector_online"):
        return [], None
    return [o for o in report.get("objects") or [] if isinstance(o, dict)], age


@mcp.tool(
    meta=skill_meta(
        classification="locomotion",
        danger_level="medium",
        status="real",
        cancellable=True,
        expected_duration_s=20.0,
        works_sim=False,
        # Composed of walk_to (works_real) and the detector; not yet watched end to end.
        works_real=True,
        preconditions=["robot_upright", "battery_pct_gt_15", "no_active_walk_task"],
        typical_failure_modes=[
            "not_found",
            "stale_detection",
            "too_far",
            "no_pose",
            "target_lost",
            "drift",
        ],
    )
)
async def go_to_object(
    ctx: Context,
    query: Annotated[
        str,
        Field(
            min_length=1,
            max_length=100,
            description="What to walk to, any language: 'silla', 'the chair', 'mochila', 'persona'.",
        ),
    ],
    stop_distance_m: Annotated[
        float,
        Field(
            ge=0.5,
            le=2.0,
            description="Stop this far from the object, metres: from the robot's front to the object's near side.",
        ),
    ] = 0.7,
    choose: Annotated[
        Literal["nearest", "farthest", "left", "right", "center", "confidence"],
        Field(
            description=(
                "Which one when several match: «la más cercana» nearest, «la más lejana» "
                "farthest, «la de la izquierda» left, «la de la derecha» right, «la de "
                "enfrente» center (most straight ahead); confidence = the detector's surest."
            ),
        ),
    ] = "nearest",
) -> dict:
    """Walk to an object the camera sees now, or saw in the last 10 minutes.

    Use this for "andá hasta la silla" / "go to the backpack". It needs NO
    map: it takes the object's range and bearing from the detector (or from
    object memory, so `look_around` first helps) and stops with the robot's
    front `stop_distance_m` from the object's near side. Without a near-side
    reading (memory, an older detector) the stop is measured from the object's
    centre and `target.range_to` says "centre". Refuses targets over 4 m, and
    live detections over 1 s old (`stale_detection`: look again). Two result
    shapes:

    - Nav2 active and the gate already armed by a person: status 'started',
      `via: 'nav2'` and a task_id at once; Nav2 plans around obstacles. Poll
      navigation_status(). This tool never arms the gate.
    - Otherwise: a camera-only walk that blocks until it ends, the walk_to task
      dict with `via: 'walk_to'` and `nav2_refused` (why Nav2 was not used).
      No obstacle avoidance: it walks straight at the target, re-aiming at it
      about once a second; the person rule still slows and stops it. Phase
      'target_lost' when the object stayed out of the detections while in view.

    Both carry `target` (label, range, bearing, live or memory). The camera
    walk also carries `clearance_m` (front to near side, the number to tell the
    user) and a `note` saying what was measured; `final_distance_m` is from the
    pelvis, never the gap.

    Several matches: `choose` picks one ("nearest" unless the user said which:
    «la de la izquierda» -> "left"). One already inside the stop distance is
    skipped while another is further; when every match is, nothing walks and
    the phase is 'already_within_stop_distance'. `target.candidates` says how
    many matched and `target.chosen_by` how one was picked.
    """
    import time as _time

    from bridge.object_memory import egocentric, get_object_memory
    from bridge.sdk.state import get_sampler
    from bridge.skills import go_to_object as g

    if config.SIM_MODE != "real":
        return {"error": "not_applicable", "detail": f"real hardware only (SIM_MODE={config.SIM_MODE})"}
    objects, report_age = _live_detections()
    mem_pose, frame = memory_pose()
    target = g.resolve(
        query,
        objects,
        get_object_memory().list_all(),
        mem_pose,
        _time.time(),
        frame=frame,
        live_age_s=report_age,
        choose=choose,
        stop_distance_m=stop_distance_m,
    )
    if "error" in target:
        return target
    others = target.pop("others")
    # aim_rng: near face if measured, else centre; stop: base_link's stop from that point.
    aim_rng, stop = g.approach(target, stop_distance_m)
    if aim_rng > g.MAX_RANGE_M:
        return {
            "error": "too_far",
            "detail": f"{target['label']} is {aim_rng:.1f} m away; walk closer first (max {g.MAX_RANGE_M} m).",
            "target": target,
        }
    rounded = {k: (round(v, 2) if isinstance(v, float) else v) for k, v in target.items()}
    if aim_rng <= stop:
        done = {
            "status": "completed",
            "phase": "already_within_stop_distance",
            "result": {"arrived": True, "final_distance_m": aim_rng},
        }
        return {**done, "via": "none", **g.report(done, target), "target": rounded, "env": config.SIM_MODE}
    sampler = get_sampler()
    pose = (sampler.get_state() or {}).get("pose") or {}
    try:
        x, y, yaw = (float(pose[k]) for k in ("x_meters_world", "y_meters_world", "yaw_radians_world"))
    except (KeyError, TypeError, ValueError):
        return {"error": "no_pose", "detail": "the bridge has no odometry pose to steer by"}

    def aim_from(
        age: float | None, now_pose: tuple[float, float, float]
    ) -> tuple[float, float, float] | None:
        # The bearing was measured where the robot stood at capture, not where it stands now.
        return now_pose if age is None else sampler.pose_at(_time.monotonic() - age)

    if target["source"] == "live" and target["age_s"] is None:
        log.info("go_to_object.ageless_detection", label=target["label"])
    at = aim_from(target.get("age_s"), (x, y, yaw))
    if at is None:
        return {
            "error": "stale_detection",
            "detail": "the detection is older than the odometry history; look at it again.",
            "target": rounded,
        }
    tx, ty = g.world_target(*at, aim_rng, target["bearing_deg"])
    log.info("go_to_object.called", query=query, target=target, world_target=(tx, ty))

    # Nav2 when it is up and a person already armed the gate. Never armed here.
    from bridge.skills.walk_to import active_walk

    refusal = _nav_refusal()
    nav_pose = None
    # navigate_to aside: a new goal replaces the running one, as in _nav_refusal.
    if refusal is None and active_walk(ignore=("navigate_to",)) is not None:
        refusal = {"reason": "walk_task_active"}  # the no_active_walk_task precondition
    if refusal is None:
        from bridge.sdk.nav_link import get_nav_link
        from bridge.skills.landmarks import nav_pose_from_status

        nav_pose = nav_pose_from_status(get_nav_link().latest_status()[0])
        if nav_pose is None:
            refusal = {"reason": "no_nav_pose"}
    # The object relative to the robot NOW on vendor odometry, applied to Nav2's
    # own pose: the two frames' absolute coordinates are never mixed.
    rng_now, brg_now = egocentric({"x_m": x, "y_m": y, "yaw_deg": math.degrees(yaw)}, tx, ty)
    if nav_pose is not None and rng_now > stop:
        gx, gy = g.world_target(
            nav_pose["x"], nav_pose["y"], nav_pose["yaw"], rng_now - stop, brg_now
        )
        # The camera walk below announces in walk_to.run; Nav2's goal goes out here.
        if not await motion_announce.before_motion():
            return {"error": "stopped", "detail": server.STOPPED_DURING_WARNING, "target": rounded}
        started = await _start_navigation(
            gx,
            gy,
            None,
            nav_pose["frame"],
            {
                "kind": "object",
                **{k: target[k] for k in ("label", "source", "candidates", "chosen_by")},
            },
        )
        if started.get("status") != "refused":
            return {**started, "via": "nav2"}
        refusal = started

    follow = g.Follow(
        (tx, ty),
        _time.monotonic(),
        abort_when_lost=target["source"] == "live",
        others=[g.world_target(*at, r, b) for r, b in others],
    )

    def retarget() -> tuple[float, float] | None:
        now_pose = sampler.pose_at(_time.monotonic())
        if now_pose is None:
            return follow.target  # walk_to deals with a missing pose itself
        live, live_age = _live_detections()
        points = []
        for o, age in g.live_matches(target["label"], live, live_age)[0]:
            seen_from = aim_from(age, now_pose)
            # Same kind of range as the walk: a near-face walk skips sightings without one.
            rng = g.aim_range(o, to_near_face="near_m" in target)
            if seen_from is not None and rng is not None:
                points.append(g.world_target(*seen_from, rng, float(o["bearing_deg"])))
        old = follow.target
        new = follow.update(points, now_pose, _time.monotonic())
        if new is None:
            log.warning("go_to_object.target_lost", last=old)
        elif new != old:
            log.info("go_to_object.retarget", old=old, new=new)
        return new

    from bridge.skills.walk_to import run as run_walk_to

    # autonomous: a model-chosen walk with no obstacle avoidance, so no live detector stops it.
    result = await run_walk_to(
        target_x=tx,
        target_y=ty,
        stop_distance_m=stop,
        timeout_s=60.0,
        ctx=ctx,
        autonomous=True,
        retarget=retarget,
    )
    via: dict[str, Any] = {"via": "walk_to"}
    if refusal is not None:
        via["nav2_refused"] = refusal["reason"]
    return {**result, **via, **g.report(result, target), "target": rounded, "env": config.SIM_MODE}


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
    log.warning("arm_navigation.called", seconds=seconds, reason=reason, sim_mode=config.SIM_MODE)

    from bridge.sdk.perception_link import get_link

    link = get_link()
    status_before = link.status()
    if not status_before.get("cmd_vel_received"):
        # Not refused — armed anyway, and told. A planner that has not published
        # yet is normal during bring-up, and refusing here would make arming
        # depend on timing the operator cannot see.
        log.warning("arm_navigation.no_planner_traffic")

    # Before enable(): whether this renews an open gate decides if the fence keeps its centre.
    safety.anchor_geofence(renewing=bool(status_before.get("enabled")))
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
        # False: the governor zeroes every Nav2 setpoint until the lidar reports again.
        "obstacles_fresh": safety.obstacles_fresh(),
        # inside null with a radius set: no pose, so every Nav2 setpoint is zeroed.
        "geofence": safety.geofence(),
        "warning": (
            "The robot can now be driven by Nav2. A person must be watching "
            "with the e-stop in reach. The gate closes itself in "
            f"{seconds:g}s, or immediately on stop_everything."
        ),
        "env": config.SIM_MODE,
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
    log.warning("disarm_navigation.called", reason=reason, sim_mode=config.SIM_MODE)

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
        "env": config.SIM_MODE,
    }


# ===========================================================================
# BEGIN auto/nav — Nav2 goals: navigate_to / navigate_to_landmark /
# navigation_status / cancel_navigation (docs/autonomy/PLAN.md Phase 3, D4).
# One block on purpose, so parallel branches merge around it.
# ===========================================================================
#
# The agent issues GOALS; Nav2 owns the velocity loop; the bridge's gate still
# decides whether any of it reaches the legs (D2.1). These tools never open
# that gate. They refuse while it is shut and say how a person opens it,
# because "arm the robot so I can drive it" is a human decision, and a goal
# planned against a closed gate only runs Nav2's progress checker out.
#
# Long-running the way research 03 §6 asks: the tool returns a task_id at once
# and a background monitor follows the goal through the existing TaskRegistry,
# so `cancel_task`, `list_active_tasks` and `stop_everything` all reach it.

NAV_POLL_S = 0.25


# The relay answers a goal within one heartbeat (0.5 s). Three seconds of
# silence about OUR goal id means it never arrived.
NAV_ACK_TIMEOUT_S = 3.0
NAV_MAX_DURATION_S = 300.0


# The asyncio tasks, held so the loop cannot garbage-collect a running monitor.
_nav_monitors: set[asyncio.Task] = set()

_NAV_FIX = {
    "nav_relay_offline": (
        "No heartbeat from the nav container's goal_relay. Start the stack: "
        "`c3po up nav2` on the robot (docs/autonomy/test-day/nav.md)."
    ),
    "nav2_inactive": (
        "Nav2 is up but its lifecycle is not active (autostart is off on purpose). "
        "On the robot: docker exec c3po-perception-nav /entrypoint.sh ros2 service call "
        "/lifecycle_manager_navigation/manage_nodes nav2_msgs/srv/ManageLifecycleNodes "
        '"{command: 0}"'
    ),
    "gate_closed": (
        "The cmd_vel gate is closed, so nothing Nav2 plans can move the robot. A person "
        "watching the robot, with the e-stop in hand, must open it with "
        "arm_navigation(reason, seconds) for the length of this manoeuvre. This tool "
        "does not open it, and you should not open it on your own initiative."
    ),
    "teleop_active": (
        "A person in the headset is driving the robot; Nav2 would be a second commander "
        "on the base. Wait until they let go, then try again."
    ),
    "arm_held": (
        "The arms are held on rt/arm_sdk (move_arm, pick or reach), and walking under "
        "arm_sdk weight is untried on this robot. Call release_arm_control first."
    ),
    "walk_task_active": (
        "Another walk (walk_to, turn, walk_velocity or look_around) is driving the base; "
        "Nav2 would be a second commander. cancel_task it or wait, then try again."
    ),
}


def _nav_refusal() -> dict | None:
    """Why a goal must not be sent right now, or None if it may."""
    from bridge.sdk.nav_link import get_nav_link
    from bridge.sdk.perception_link import get_link
    from bridge.locomotion import arm_hold_conflict, teleop_conflict
    from bridge.skills.walk_to import active_walk

    status, _ = get_nav_link().latest_status()
    if teleop_conflict() is not None:
        reason = "teleop_active"
    elif arm_hold_conflict() is not None:
        reason = "arm_held"
    elif active_walk(ignore=("navigate_to",)) is not None:  # a new goal replaces the old one
        reason = "walk_task_active"
    elif status is None:
        reason = "nav_relay_offline"
    elif not status.get("nav2_ready"):
        reason = "nav2_inactive"
    elif not get_link().is_enabled():
        reason = "gate_closed"
    else:
        return None
    return {"status": "refused", "reason": reason, "how_to_fix": _NAV_FIX[reason]}


def _nav_cancel_quiet(goal_id: str | None) -> None:
    try:
        from bridge.sdk.nav_link import get_nav_link

        get_nav_link().cancel(goal_id)
    except Exception:
        log.exception("nav.cancel_failed", goal_id=goal_id)


async def _monitor_navigation(task, goal_id: str) -> None:
    """Follow one goal to a terminal state, mirroring it into `task`."""
    from bridge.estop import teleop_is_driving
    from bridge.sdk.nav_link import get_nav_link
    from bridge.sdk.perception_link import get_link
    from bridge.locomotion import TELEOP_TAKEOVER

    nav, gate = get_nav_link(), get_link()
    started = time.monotonic()
    seen = False
    first_distance: float | None = None

    def end(status: str, phase: str, error: str | None = None) -> None:
        task.status, task.phase, task.error = status, phase, error
        task.ended_at = time.time()
        log.info("navigate_to.ended", task_id=task.task_id, goal_id=goal_id, phase=phase)

    try:
        while True:
            # Cancel first: stop_everything sets this AND closes the gate, and
            # it should read as the cancel it was, not as a gate failure.
            if task.cancel_event.is_set():
                _nav_cancel_quiet(goal_id)
                return end("cancelled", "cancelled")
            if not gate.is_enabled():
                # Cancel in Nav2 too. A goal left active there resumes walking
                # the moment somebody re-arms the gate for something else.
                _nav_cancel_quiet(goal_id)
                return end("failed", "gate_closed", "gate_closed: the arm TTL ran out or "
                           "the gate was disarmed mid-goal; the goal was cancelled")
            if teleop_is_driving():
                # The person in the headset wins, as in walk_to.
                _nav_cancel_quiet(goal_id)
                return end("failed", "teleop_takeover", TELEOP_TAKEOVER)
            now = time.monotonic()
            goal = nav.goal(goal_id)
            if goal is None:
                if nav.latest_status()[0] is None:
                    _nav_cancel_quiet(goal_id)
                    return end("failed", "nav_relay_offline", _NAV_FIX["nav_relay_offline"])
                if seen:
                    return end("cancelled", "superseded")  # someone sent a newer goal
                if now - started > NAV_ACK_TIMEOUT_S:
                    _nav_cancel_quiet(goal_id)
                    return end("failed", "goal_not_acknowledged",
                               "the relay never reported this goal")
            else:
                seen = True
                state = goal["state"]
                task.phase = state
                dist = goal.get("distance_remaining_m")
                if dist is not None and dist > 0 and first_distance is None:
                    first_distance = dist
                if dist is not None and first_distance:
                    task.progress = min(1.0, max(0.0, 1.0 - dist / first_distance))
                task.result = {**(task.result or {}), "feedback": {
                    k: goal.get(k)
                    for k in ("distance_remaining_m", "eta_s", "recoveries", "nav_time_s")
                }}
                if state == "succeeded":
                    task.progress = 1.0
                    return end("completed", "succeeded")
                if state == "canceled":
                    return end("cancelled", "canceled")
                if state in ("aborted", "rejected"):
                    return end("failed", state, goal.get("reason") or state)
            if now - started > NAV_MAX_DURATION_S:
                _nav_cancel_quiet(goal_id)
                return end("failed", "timeout", f"no result in {NAV_MAX_DURATION_S:.0f}s")
            await asyncio.sleep(NAV_POLL_S)
    except Exception as exc:
        log.exception("navigate_to.monitor_failed", goal_id=goal_id)
        _nav_cancel_quiet(goal_id)
        end("failed", "monitor_failed", f"{type(exc).__name__}: {exc}")


async def _start_navigation(
    x: float, y: float, yaw: float | None, frame: str | None, target: dict
) -> dict:
    """Refuse, or send one goal and return its task_id immediately."""
    from bridge.sdk.nav_link import get_nav_link
    from bridge.task_runtime import get_registry

    refusal = _nav_refusal()
    if refusal is not None:
        log.warning("navigate_to.refused", reason=refusal["reason"], target=target)
        return {**refusal, "target": target, "env": config.SIM_MODE}

    nav = get_nav_link()
    status, _ = nav.latest_status()
    if yaw is None:
        # Face the direction of travel. A default of 0 would make the robot
        # turn to face +x on arrival, which nobody asked for.
        pose = (status or {}).get("pose")
        yaw = math.atan2(y - pose["y_m"], x - pose["x_m"]) if pose else 0.0

    registry = get_registry()
    # One goal at a time. Nav2 preempts on its own; this keeps our tasks honest.
    for old in registry.list_active():
        if old.skill_name == "navigate_to":
            registry.cancel(old.task_id)

    goal_id = nav.send_goal(x, y, yaw, frame or "")
    task = registry.create("navigate_to")
    task.phase = "sent"
    task.result = {"goal_id": goal_id, "target": {**target, "x": x, "y": y, "yaw": yaw,
                                                  "frame": frame or (status or {}).get("global_frame")}}
    monitor = asyncio.create_task(_monitor_navigation(task, goal_id))
    _nav_monitors.add(monitor)
    monitor.add_done_callback(_nav_monitors.discard)
    return {
        "status": "started",
        "task_id": task.task_id,
        "goal_id": goal_id,
        "target": task.result["target"],
        "next": "Poll navigation_status() for progress; cancel_navigation() or "
        "stop_everything to stop.",
        "env": config.SIM_MODE,
    }


@mcp.tool(
    meta=skill_meta(
        classification="locomotion",
        danger_level="medium",
        status="real",
        cancellable=True,
        expected_duration_s=30.0,
        works_sim=False,
        works_real=False,
        preconditions=["nav2_active", "nav_gate_armed"],
        typical_failure_modes=[
            "nav_relay_offline", "nav2_inactive", "gate_closed", "aborted", "timeout",
        ],
    )
)
async def navigate_to(
    x: Annotated[float, Field(ge=-100.0, le=100.0, description="Goal X, metres, in `frame`.")],
    y: Annotated[float, Field(ge=-100.0, le=100.0, description="Goal Y, metres, in `frame`.")],
    yaw: Annotated[
        float | None,
        Field(ge=-math.pi, le=math.pi, description=(
            "Final heading, radians, CCW from +x. Omit to arrive facing the direction "
            "of travel."
        )),
    ] = None,
    frame: Annotated[
        Literal["map", "odom"] | None,
        Field(description=(
            "Nav2's frame for x/y. Omit for Nav2's global frame (`map` when the saved "
            "map is loaded, else `odom`). NOT get_state's world frame — that is the "
            "vendor leg odometry, a different origin."
        )),
    ] = None,
) -> dict:
    """Walk the robot to a pose with Nav2: planned path, obstacle avoidance, recovery.

    The robot physically walks there — a 1.3 m humanoid moving on its own path,
    up to ~0.35 m/s. Returns a `task_id` IMMEDIATELY; the walk continues in the
    background. Follow it with `navigation_status()`; stop it with
    `cancel_navigation()` or `stop_everything`.

    Coordinates are in Nav2's frame: read the robot's current Nav2 pose from
    `navigation_status().pose` and offset from that (e.g. 0.5 m ahead is
    x + 0.5·cos(yaw), y + 0.5·sin(yaw)). For named places prefer
    `navigate_to_landmark`.

    Refuses (status=refused, with `how_to_fix`) unless all three hold: the nav
    container is reporting, Nav2's lifecycle is active, and a human has opened
    the cmd_vel gate with `arm_navigation`. It never opens the gate itself. If
    the gate closes mid-walk (TTL, disarm, stop) the goal is cancelled.
    """
    target = {"kind": "pose"}
    log.warning("navigate_to.called", x=x, y=y, yaw=yaw, frame=frame, sim_mode=config.SIM_MODE)
    if config.SIM_MODE == "stub":
        return {"status": "stub", "message": f"[STUB] Would navigate to ({x:.2f}, {y:.2f}).",
                "env": config.SIM_MODE, "stub": True}
    return await _start_navigation(x, y, yaw, frame, target)


@mcp.tool(
    meta=skill_meta(
        classification="locomotion",
        danger_level="medium",
        status="real",
        cancellable=True,
        expected_duration_s=30.0,
        works_sim=False,
        works_real=False,
        preconditions=["nav2_active", "nav_gate_armed", "landmark_has_nav_pose"],
        typical_failure_modes=[
            "not_found", "no_nav_pose", "map_not_loaded", "odom_frame_restarted",
            "gate_closed", "aborted",
        ],
    )
)
async def navigate_to_landmark(
    name: Annotated[str, Field(min_length=1, max_length=64, description="Saved landmark name.")],
) -> dict:
    """Walk the robot to a saved landmark with Nav2 ("andá a la cocina").

    The robot physically walks there on a planned path. Same contract as
    `navigate_to`: returns a `task_id` immediately, refuses while the nav gate
    is closed, and is stopped by `cancel_navigation()` or `stop_everything`.

    Uses the pose `remember_landmark` stored in Nav2's frame, never the vendor
    odometry pose. Refuses when that pose cannot be trusted: saved without the
    nav stack up (`no_nav_pose`), saved in the map while no map is loaded
    (`map_not_loaded`), or saved in odom before the nav container restarted
    (`odom_frame_restarted`). Re-save it with `remember_landmark` standing there.
    """
    from bridge.skills.landmarks import get_store as get_landmark_store
    from bridge.skills.landmarks import nav_target

    log.warning("navigate_to_landmark.called", name=name, sim_mode=config.SIM_MODE)
    landmark = get_landmark_store().recall(name)
    if landmark is None:
        return {"status": "not_found", "name": name, "env": config.SIM_MODE}
    if config.SIM_MODE == "stub":
        return {"status": "stub", "message": f"[STUB] Would navigate to landmark {name!r}.",
                "env": config.SIM_MODE, "stub": True}

    from bridge.sdk.nav_link import get_nav_link

    pose, why = nav_target(landmark, get_nav_link().latest_status()[0])
    if pose is None:
        fix = _NAV_FIX.get(why or "") or (
            "Walk the robot to that place with the nav stack up and call "
            f"remember_landmark({name!r}) again."
        )
        return {"status": "refused", "reason": why, "name": name, "how_to_fix": fix,
                "env": config.SIM_MODE}
    return await _start_navigation(
        pose["x"], pose["y"], pose["yaw"], pose["frame"], {"kind": "landmark", "name": name}
    )


@mcp.tool(
    meta=skill_meta(
        classification="introspection",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=0.05,
        works_sim=False,
        works_real=False,
    )
)
def navigation_status() -> dict:
    """Where Nav2 is, and how the current goal is going. Read-only; moves nothing.

    `relay_online` / `nav2_ready` / `gate.enabled` are the three things a goal
    needs. `pose` is the robot in Nav2's `global_frame` — the frame
    `navigate_to` takes. `goal` carries state (pending / accepted / active /
    succeeded / aborted / canceled / rejected), distance_remaining_m, eta_s and
    recoveries. `task` is the newest navigate_to task from the task registry.
    """
    if config.SIM_MODE == "stub":
        return {"relay_online": False, "env": config.SIM_MODE, "stub": True}
    from bridge.sdk.nav_link import get_nav_link
    from bridge.sdk.perception_link import get_link
    from bridge.task_runtime import get_registry

    status, age = get_nav_link().latest_status()
    gate = get_link().status()
    nav_tasks = [t for t in get_registry().list_recent(50) if t.skill_name == "navigate_to"]
    return {
        "relay_online": status is not None,
        "status_age_s": None if age is None else round(age, 2),
        "nav2_ready": bool(status and status.get("nav2_ready")),
        "global_frame": (status or {}).get("global_frame"),
        "localized": (status or {}).get("global_frame") == "map",
        "pose": (status or {}).get("pose"),
        "goal": (status or {}).get("goal"),
        "gate": {"enabled": gate["enabled"], "disabled_reason": gate["disabled_reason"],
                 "arm_expires_in_s": gate["arm_expires_in_s"]},
        "task": nav_tasks[0].to_dict() if nav_tasks else None,
        "link": get_nav_link().diagnostics(),
        "env": config.SIM_MODE,
    }


@mcp.tool(
    meta=skill_meta(
        # Not "safety": in apps/back that word bypasses the admin gate, and
        # only stop_everything may carry it (test_mcp_catalogue).
        classification="locomotion",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=0.1,
        works_sim=False,
        works_real=False,
    )
)
def cancel_navigation() -> dict:
    """Cancel the active Nav2 goal and every navigate_to task. Always safe to call.

    Nav2 stops planning toward the goal and its velocity ramps to zero. The
    cmd_vel gate is left as it is; `stop_everything` is the harder stop — it
    also cancels the goal, closes the gate and halts the gait.
    """
    from bridge.task_runtime import get_registry

    log.warning("cancel_navigation.called", sim_mode=config.SIM_MODE)
    registry = get_registry()
    cancelled = [
        t.task_id for t in registry.list_active()
        if t.skill_name == "navigate_to" and registry.cancel(t.task_id)
    ]
    if config.SIM_MODE != "stub":
        _nav_cancel_quiet(None)
    return {"status": "ok", "cancelled_task_ids": cancelled, "env": config.SIM_MODE}


# ===========================================================================
# END auto/nav
# ===========================================================================
