"""Named-pose memory tools over `bridge.skills.landmarks`."""

from __future__ import annotations

from typing import Annotated

import structlog
from pydantic import Field

from bridge.config import SIM_MODE
from bridge.server import mcp
from bridge.skill_meta import meta as skill_meta
from bridge.tools.state import get_state

log = structlog.get_logger(__name__)


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

    landmark = get_landmark_store().remember(name, pose, frame_tick=_current_frame_tick())
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
        "landmarks": [_with_frame_status(lm.to_dict(), lm) for lm in landmarks],
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
