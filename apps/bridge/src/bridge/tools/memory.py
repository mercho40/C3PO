"""Object memory: recall/list/forget_object, look_around, and the D7 surroundings snapshot."""

from __future__ import annotations

import asyncio
import functools
import math
import time
from typing import Annotated

import structlog
from mcp.server.fastmcp import Context
from pydantic import Field

from bridge import config
from bridge.server import mcp
from bridge.skill_meta import meta as skill_meta
from bridge.tools import locomotion

log = structlog.get_logger(__name__)


# ===========================================================================
# BEGIN object memory — recall_object / list_objects / forget_object /
# look_around (docs/autonomy/PLAN.md Phase 4.1-4.2). Kept in one block so the
# parallel autonomy branches merge cleanly. Risk tier: the three memory tools
# are T0 (read/forget a table, no motion); look_around is T1 (turns in place,
# through `turn` and its checks).
# ===========================================================================

# look_around: 60 deg steps overlap the D435i's ~69 deg view, and the pause
# after each turn gives the 4 Hz reports time to land while the body is still
# (object_memory ignores frames taken mid-turn).
LOOK_STEP_DEG = 60.0


LOOK_DWELL_S = 2.0


# How many remembered objects ride along in describe_surroundings.
MEMORY_SUMMARY_MAX = 6


def memory_pose() -> tuple[dict | None, str | None]:
    """The pose memory entries are measured against: the perception report's,
    else the vendor odometry under this run's frame (object_memory.report_pose).
    None when there is no fresh report, or neither pose.
    """
    from bridge.object_memory import report_pose

    try:
        from bridge.sdk.perception_link import get_link

        report, _ = get_link().latest_report()
    except Exception:
        log.exception("object_memory.pose_failed")
        return None, None
    if not report:
        return None, None
    return report_pose(report) or (None, None)


def _with_object_memory(build):  # noqa: ANN001, ANN202
    """Add a short `memory` section to the D7 snapshot, so the agent sees what
    it remembers without asking. Objects seen once are left out (a single
    frame is as likely a false positive); recall_object still has them."""

    @functools.wraps(build)
    def wrapper() -> dict:
        snapshot = build()
        try:
            from bridge.object_memory import get_object_memory

            pose, frame = memory_pose()
            now = time.time()
            entries = [e for e in get_object_memory().list_all() if e.seen_count >= 2]
            if entries:
                shown = []
                for e in entries[:MEMORY_SUMMARY_MAX]:
                    d = e.describe(pose, frame, now)
                    shown.append(
                        {k: d[k] for k in ("id", "label", "distance_m", "bearing_deg",
                                           "last_seen_s_ago") if k in d}
                    )
                snapshot["memory"] = {"objects": shown}
                if len(entries) > MEMORY_SUMMARY_MAX:
                    snapshot["memory"]["omitted"] = len(entries) - MEMORY_SUMMARY_MAX
        except Exception:
            log.exception("describe_surroundings.memory_failed")
        return snapshot

    return wrapper


@mcp.tool(
    meta=skill_meta(
        classification="memory",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=0.05,
        works_sim=True,
        works_real=False,
        typical_failure_modes=["not_found", "pose_unavailable"],
    )
)
def recall_object(
    query: Annotated[
        str,
        Field(
            min_length=1,
            max_length=64,
            description=(
                "What to look for, in Spanish or English: 'billetera', 'la tele', "
                "'cup'. Matched against the detector's English labels."
            ),
        ),
    ],
) -> dict:
    """Where the robot last SAW an object — memory, not a live look.

    Returns matching remembered objects, most recently seen first, each with
    `distance_m` and `bearing_deg` from where the robot is NOW (bearing: 0 ahead,
    positive LEFT, so `turn` can face it directly), `last_seen_s_ago` and
    `seen_count`. `x_m`/`y_m` are in `frame` (perception's odometry — the same
    frame Nav2 calls `odom`), which is NOT the frame `walk_to` uses: pass them as
    `navigate_to(x, y, frame=frame)` when navigation is armed, otherwise turn by
    `bearing_deg` and walk by `distance_m`. A `vendor_odom@...` frame (lidar down)
    is not Nav2's: turn and walk.

    `not_found` comes with `known_labels`: everything in memory. If one of them
    is what was asked for under another name, call again with that label. Not
    in memory means not seen yet, not "absent": use look_around to look.
    """
    from bridge.object_memory import get_object_memory

    memory = get_object_memory()
    pose, frame = memory_pose()
    found = memory.recall(query)
    if not found:
        return {"status": "not_found", "query": query, "known_labels": memory.labels()}
    now = time.time()
    result: dict = {
        "status": "ok",
        "query": query,
        "count": len(found),
        "objects": [e.describe(pose, frame, now) for e in found],
    }
    if pose is None:
        result["note"] = (
            "Perception pose unavailable: distance/bearing from here cannot be given."
        )
    return result


@mcp.tool(
    meta=skill_meta(
        classification="memory",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=0.05,
        works_sim=True,
        works_real=False,
    )
)
def list_objects() -> dict:
    """Everything in object memory, most recently seen first, with distance and
    bearing from the robot now (positive bearing = LEFT) and last-seen age."""
    from bridge.object_memory import get_object_memory

    pose, frame = memory_pose()
    now = time.time()
    entries = get_object_memory().list_all()
    return {"count": len(entries), "objects": [e.describe(pose, frame, now) for e in entries]}


@mcp.tool(
    meta=skill_meta(
        classification="memory",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=0.05,
        works_sim=True,
        works_real=False,
        typical_failure_modes=["not_found"],
    )
)
def forget_object(
    object_id: Annotated[
        str,
        Field(
            min_length=1,
            max_length=32,
            description="An `id` from recall_object/list_objects (e.g. 'obj_3'), or 'all'.",
        ),
    ],
) -> dict:
    """Delete one remembered object by id, or the whole memory with 'all'.

    Use 'all' after the perception container restarts: its odometry re-origins
    and every remembered position then points somewhere else."""
    from bridge.object_memory import get_object_memory

    removed = get_object_memory().forget(object_id)
    return {"status": "ok" if removed else "not_found", "object_id": object_id, "removed": removed}


@mcp.tool(
    meta=skill_meta(
        classification="locomotion",
        danger_level="low",
        status="real",
        cancellable=True,
        expected_duration_s=45.0,
        works_sim=False,
        works_real=False,
        preconditions=["robot_upright", "no_active_turn_task"],
        typical_failure_modes=["perception_offline", "turn_failed", "stopped"],
    )
)
async def look_around(
    ctx: Context,
    total_degrees: Annotated[
        float,
        Field(
            ge=60.0,
            le=360.0,
            description="How far to sweep, to the LEFT (counterclockwise). 360 = full circle.",
        ),
    ] = 360.0,
) -> dict:
    """Turn in place in 60° steps, pausing after each so the camera can update
    object memory, and report what was seen.

    THE ROBOT PHYSICALLY ROTATES: each step is a `turn` call with all of its
    checks (teleop conflict, pose, cancel). A full circle takes ~45 s. Stops at
    the first failed turn, or as soon as its task is cancelled (cancel_task,
    soft_stop, stop_everything), pauses included. Refuses without moving when
    perception is offline — turning blind updates nothing.

    Returns `new_objects` (first seen during this sweep), `seen_again`, and
    `forgotten` (remembered here but now looked at and absent), each with
    distance/bearing from the final heading.
    """
    from bridge import estop
    from bridge.object_memory import get_object_memory
    from bridge.skills.task_runtime import get_registry
    from bridge.skills.walk_to import active_walk

    if memory_pose()[0] is None:
        return {
            "status": "failed",
            "error": "perception_offline",
            "message": "No fresh perception report with a pose: nothing to look with.",
            "env": config.SIM_MODE,
        }
    if (running := active_walk()) is not None:
        return {
            "status": "failed",
            "error": "walk_task_active",
            "message": f"walk task {running} is still running; cancel_task it or wait.",
            "env": config.SIM_MODE,
        }

    memory = get_object_memory()
    before = {e.id: e.last_seen for e in memory.list_all()}
    started = time.time()
    dwell = 0.0 if config.SIM_MODE == "stub" else LOOK_DWELL_S
    steps = max(1, round(total_degrees / LOOK_STEP_DEG))
    step_rad = math.radians(total_degrees / steps)

    # A registry task, so cancel_task, soft_stop and stop_everything reach the sweep,
    # dwells included: a bare sleep heard none of them.
    task = get_registry().create("look_around")

    async def look() -> None:
        try:
            await asyncio.wait_for(task.cancel_event.wait(), dwell)
        except TimeoutError:
            pass

    turned = 0.0
    stopped: str | None = None
    try:
        await look()  # look before the first turn, too
        for _ in range(steps):
            if estop.last_stop_at() > started:
                stopped = "stop_everything"
                break
            if task.cancel_event.is_set():
                break
            step = asyncio.ensure_future(
                locomotion.turn(ctx, delta_yaw_radians=step_rad, timeout_s=30.0, tolerance_degrees=5.0)
            )
            heard = asyncio.ensure_future(task.cancel_event.wait())
            try:
                await asyncio.wait((step, heard), return_when=asyncio.FIRST_COMPLETED)
            except BaseException:
                step.cancel()  # as a plain await did: the sweep's own cancellation reaches the turn
                raise
            finally:
                heard.cancel()
            if not step.done():
                # cancel_task names the sweep; the turn is its own registry task. While it
                # runs no other turn can, so every running turn is this one.
                registry = get_registry()
                for t in registry.list_active():
                    if t.skill_name == "turn":
                        registry.cancel(t.task_id)
            result = await step
            if task.cancel_event.is_set():
                break
            if result.get("status") not in ("ok", "completed"):
                stopped = f"turn_{result.get('status')}: {result.get('error') or result.get('phase')}"
                break
            turned += math.degrees(step_rad)
            await look()
        if stopped is None and task.cancel_event.is_set():
            stopped = "cancelled"
    except BaseException:
        stopped = stopped or "exception"
        raise
    finally:
        # Never left "running": the registry reaps only finished tasks.
        cancelled = task.cancel_event.is_set()
        task.status = "cancelled" if cancelled else "failed" if stopped else "completed"
        task.phase, task.ended_at = stopped or "done", time.time()

    pose, frame = memory_pose()
    now = time.time()
    after = memory.list_all()
    after_ids = {e.id for e in after}
    return {
        "status": "stopped" if stopped else "ok",
        **({"stopped_reason": stopped} if stopped else {}),
        "task_id": task.task_id,
        "turned_degrees": round(turned, 1),
        "new_objects": [e.describe(pose, frame, now) for e in after if e.id not in before],
        "seen_again": [
            e.describe(pose, frame, now)
            for e in after
            if e.id in before and e.last_seen > before[e.id]
        ],
        "forgotten": sorted(set(before) - after_ids),
        "env": config.SIM_MODE,
    }


# ===========================================================================
# END object memory
# ===========================================================================


# ---------------------------------------------------------------------------
# Tool: describe_surroundings
@_with_object_memory
def surroundings_snapshot() -> dict:
    """The D7 world-model snapshot, as a plain dict.

    Extracted so the `describe_surroundings` TOOL and the read-only
    `/telemetry/surroundings` ROUTE cannot drift apart. Two callers building
    the same snapshot independently is how an operator console ends up
    showing something subtly different from what the agent was told — and
    the whole point of D7 is that there is one contract, interpreted once.
    """

    from bridge import world_model
    from bridge.skills.landmarks import get_store

    if config.SIM_MODE == "stub":
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
            bearing = math.degrees(math.atan2(dy, dx) - math.radians(float(pose["yaw_deg"])))
            # Normalise into (-180, 180] so "left" and "right" stay meaningful.
            bearing = (bearing + 180.0) % 360.0 - 180.0
            landmarks.append(
                world_model.Observation(label=lm.name, range_m=rng, bearing_deg=bearing)
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

    `memory`, when present, lists objects seen EARLIER (seen at least twice),
    with distance/bearing from here; they may be out of view now. Ask
    `recall_object` for anything not listed.

    Today the perception stack (LiDAR/camera/detector) is not deployed, so this
    honestly reports everything offline rather than an empty scene. Pose comes
    through once the bridge is running against a robot.
    """
    return surroundings_snapshot()
