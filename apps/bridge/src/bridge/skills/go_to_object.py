"""go_to_object: walk to something the camera sees, or saw, without Nav2.

The target comes from the detector (live first, then object memory) as a
range and bearing RELATIVE to the robot. Relative, because object memory lives
in the perception report's frame and walk_to steers on the bridge's vendor
odometry: the two frames differ, but a range and a bearing do not care. The
walk itself is walk_to, so the governor's caps and the person rule apply.

shortcut: open loop on one sighting, no obstacle avoidance and no re-aiming as
it walks; replace with a Nav2 goal once the lidar and the nav image are back.
"""

from __future__ import annotations

import math
from typing import Any

MAX_RANGE_M = 4.0  # an open-loop walk further than this drifts too far to trust
MEMORY_MAX_AGE_S = 600.0


def _relative_from_memory(entry: Any, mem_pose: dict[str, float]) -> tuple[float, float]:
    dx, dy = entry.x_m - mem_pose["x_m"], entry.y_m - mem_pose["y_m"]
    bearing = math.degrees(math.atan2(dy, dx)) - mem_pose["yaw_deg"]
    return math.hypot(dx, dy), (bearing + 180.0) % 360.0 - 180.0


def resolve(
    query: str,
    live_objects: list[dict[str, Any]],
    memory: list[Any],
    mem_pose: dict[str, float] | None,
    now: float,
    frame: str | None = None,
) -> dict[str, Any]:
    """Pick the target. Returns {range_m, bearing_deg, label, source} or {error, ...}."""
    from bridge.object_memory import label_matches

    live = [
        o
        for o in live_objects
        if label_matches(str(o.get("label", "")), query)
        and isinstance(o.get("range_m"), (int, float))
        and isinstance(o.get("bearing_deg"), (int, float))
    ]
    if live:
        best = max(live, key=lambda o: float(o.get("confidence") or 0.0))
        return {
            "label": best["label"],
            "range_m": float(best["range_m"]),
            "bearing_deg": float(best["bearing_deg"]),
            "source": "live",
        }
    if mem_pose is not None:
        recent = [
            e
            for e in memory
            if label_matches(e.label, query)
            and now - e.last_seen < MEMORY_MAX_AGE_S
            # Another frame (perception restarted, odometry reset) is another origin.
            and (frame is None or getattr(e, "frame", frame) == frame)
        ]
        if recent:
            entry = max(recent, key=lambda e: e.last_seen)
            rng, bearing = _relative_from_memory(entry, mem_pose)
            return {
                "label": entry.label,
                "range_m": rng,
                "bearing_deg": bearing,
                "source": "memory",
                "seen_s_ago": round(now - entry.last_seen, 1),
            }
    seen = sorted({str(o.get("label")) for o in live_objects})
    return {
        "error": "not_found",
        "detail": (
            f"no {query!r} in view or in the last {int(MEMORY_MAX_AGE_S / 60)} min of memory. "
            "Try look_around first, then call go_to_object again."
        ),
        "in_view": seen,
    }


def world_target(
    robot_x: float, robot_y: float, robot_yaw_rad: float, range_m: float, bearing_deg: float
) -> tuple[float, float]:
    """The object's point in the frame walk_to steers in (positive bearing = LEFT)."""
    heading = robot_yaw_rad + math.radians(bearing_deg)
    return robot_x + range_m * math.cos(heading), robot_y + range_m * math.sin(heading)
