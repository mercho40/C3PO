"""The /c3po/nav_goal and /c3po/nav_status wire shapes. Pure: stdlib only.

    bridge -> /c3po/nav_goal    {"v":1,"id":"nav_…","x":1.0,"y":0.0,"yaw":0.0,"frame":"odom"}
                                {"v":1,"id":"nav_…","cancel":true}   (no id: cancel whatever is active)
    bridge <- /c3po/nav_status  {"v":1,"session":…,"global_frame":…,"nav2_ready":…,"pose":…,"goal":…}

D4 says the agent issues goals, not velocities. This is the seam where that
becomes true: the bridge never speaks a ROS action (D2 — the ROS graph stops at
the container boundary), it writes one JSON String on domain 42, and
goal_relay.py turns it into a NavigateToPose goal. Same wire pattern as
/c3po/world_summary and /c3po/costmap: std_msgs/String, so there is no second
hand-written IDL on the bridge side to keep in lockstep.

Kept out of goal_relay.py for the reason costmap_png.py is kept out of
costmap_publisher.py: this half can be wrong in ways nobody notices on a robot
(a yaw sign, a status code mapped to the wrong word), and it is testable on a
laptop only if it imports nothing from ROS. tests/test_nav_goal.py runs it, and
also feeds it the bridge's own goal messages so the two sides cannot drift.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional, Tuple

NAV_SCHEMA_VERSION = 1

# Frames a goal may name. Anything else is refused rather than passed to Nav2,
# which would accept the goal and then fail the TF lookup a cycle later — an
# "aborted" with no reason attached is the worst error to hand an LLM.
ALLOWED_FRAMES = ("map", "odom")

# A sanity bound, not a map size. A goal 100 m away in a lab is a units bug
# (millimetres, or a landmark from a different frame), never a real request.
MAX_GOAL_DISTANCE_M = 100.0

MAX_ID_LEN = 64

# action_msgs/msg/GoalStatus codes -> the words the bridge and the LLM see.
# CANCELING is still moving until Nav2 confirms, so it reads as active.
_GOAL_STATUS = {
    0: "pending",    # STATUS_UNKNOWN: sent, not yet answered
    1: "accepted",
    2: "active",
    3: "active",     # STATUS_CANCELING
    4: "succeeded",
    5: "canceled",
    6: "aborted",
}

TERMINAL_STATES = frozenset({"succeeded", "canceled", "aborted", "rejected"})


def _finite(d: Dict[str, Any], key: str, default: Optional[float] = None) -> float:
    raw = d.get(key, default)
    if raw is None or isinstance(raw, bool):
        raise ValueError(f"goal needs a numeric {key!r}")
    value = float(raw)
    if not math.isfinite(value):
        raise ValueError(f"goal {key!r} is not finite: {raw!r}")
    return value


def parse_goal(payload: Any) -> Dict[str, Any]:
    """Validate one decoded /c3po/nav_goal message. Raises ValueError/TypeError.

    Returns either {"cancel": True, "id": str|None} or
    {"cancel": False, "id", "x", "y", "yaw", "frame"} where frame may be ""
    (meaning: the relay's own global frame).
    """
    if not isinstance(payload, dict):
        raise TypeError(f"goal is {type(payload).__name__}, not an object")
    if payload.get("v") != NAV_SCHEMA_VERSION:
        raise ValueError(f"unsupported nav goal schema v={payload.get('v')!r}")

    goal_id = payload.get("id")
    if goal_id is not None and (
        not isinstance(goal_id, str) or not goal_id or len(goal_id) > MAX_ID_LEN
    ):
        raise ValueError("goal id must be a non-empty string of at most 64 chars")

    if payload.get("cancel") is True:
        return {"cancel": True, "id": goal_id}

    if goal_id is None:
        # Without an id the bridge cannot tell this goal's status from the
        # previous one's, and "succeeded" would be credited to the wrong request.
        raise ValueError("a goal needs an id")

    x, y = _finite(payload, "x"), _finite(payload, "y")
    yaw = _finite(payload, "yaw", 0.0)
    if math.hypot(x, y) > MAX_GOAL_DISTANCE_M:
        raise ValueError(f"goal ({x:.1f}, {y:.1f}) is over {MAX_GOAL_DISTANCE_M:.0f} m away")

    frame = payload.get("frame") or ""
    if frame and frame not in ALLOWED_FRAMES:
        raise ValueError(f"frame {frame!r} is not one of {ALLOWED_FRAMES}")
    return {"cancel": False, "id": goal_id, "x": x, "y": y, "yaw": yaw, "frame": frame}


def yaw_to_quaternion(yaw: float) -> Tuple[float, float, float, float]:
    """(x, y, z, w) for a rotation about +z. REP-103: yaw is CCW, +left."""
    return (0.0, 0.0, math.sin(yaw / 2.0), math.cos(yaw / 2.0))


def quaternion_to_yaw(x: float, y: float, z: float, w: float) -> float:
    return math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))


def state_from_goal_status(code: int) -> str:
    return _GOAL_STATUS.get(int(code), "pending")


def duration_s(sec: int, nanosec: int) -> float:
    return round(float(sec) + float(nanosec) * 1e-9, 2)


def goal_record(goal: Dict[str, Any], frame: str) -> Dict[str, Any]:
    """The per-goal half of a status message, before Nav2 has said anything."""
    return {
        "id": goal["id"],
        "state": "pending",
        "x": goal["x"],
        "y": goal["y"],
        "yaw": goal["yaw"],
        "frame": frame,
        "distance_remaining_m": None,
        "eta_s": None,
        "recoveries": 0,
        "nav_time_s": None,
        "reason": None,
    }


def status_payload(
    *,
    stamp_unix: float,
    session: str,
    global_frame: str,
    nav2_ready: bool,
    pose: Optional[Tuple[float, float, float]],
    goal: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    """One /c3po/nav_status message. Published on a timer whether or not a goal exists.

    The heartbeat is load-bearing, as it is for the world summary: silence
    cannot distinguish "no goal" from "the relay is gone", and the bridge reads
    a missing status as offline rather than idle. `pose` is the robot in
    `global_frame` (map when localized, else odom) — None until TF can say,
    never a guessed (0, 0).
    """
    return {
        "v": NAV_SCHEMA_VERSION,
        "stamp_unix": round(stamp_unix, 3),
        "session": session,
        "global_frame": global_frame,
        "nav2_ready": bool(nav2_ready),
        "pose": (
            None
            if pose is None
            else {"x_m": round(pose[0], 3), "y_m": round(pose[1], 3), "yaw_rad": round(pose[2], 4)}
        ),
        "goal": goal,
    }
