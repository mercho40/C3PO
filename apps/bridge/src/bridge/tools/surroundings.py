"""`describe_surroundings` — the world-model snapshot (docs/DECISIONS.md D7).

`surroundings_snapshot` is shared with the read-only `/telemetry/surroundings`
route so the console and the agent can never be shown different scenes.
"""

from __future__ import annotations

import math

import structlog

from bridge.config import SIM_MODE
from bridge.server import mcp
from bridge.skill_meta import meta as skill_meta

log = structlog.get_logger(__name__)


# ---------------------------------------------------------------------------
# Tool: describe_surroundings
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

    Today the perception stack (LiDAR/camera/detector) is not deployed, so this
    honestly reports everything offline rather than an empty scene. Pose comes
    through once the bridge is running against a robot.
    """
    return surroundings_snapshot()
