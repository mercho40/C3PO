"""go_to_object: walk to something the camera sees, or saw, without Nav2.

The target comes from the detector (live first, then object memory) as a
range and bearing RELATIVE to the robot. Relative, because object memory lives
in the perception report's frame and walk_to steers on the bridge's vendor
odometry: the two frames differ, but a range and a bearing do not care. The
walk itself is walk_to, so the governor's caps and the person rule apply.

A live detection's bearing was measured where the robot stood at capture, so
it is applied to the odometry pose of that moment (StateSampler.pose_at). While
walking, Follow re-aims at fresh sightings about once a second and gives up
when the target is in view and missing for LOST_AFTER_S.

shortcut: no obstacle avoidance on this camera-only walk; the tool prefers a
Nav2 goal whenever Nav2 is active and a person armed the gate.
"""

from __future__ import annotations

import math
from typing import Any

MAX_RANGE_M = 4.0  # an open-loop walk further than this drifts too far to trust
MEMORY_MAX_AGE_S = 600.0
# Capture-to-now age a live detection may have. pose_at corrects the motion since
# capture, so this only has to sit inside the ~2 s pose history, with margin.
LIVE_MAX_AGE_S = 1.0
# Follow: a sighting further than JUMP_M from the current target is another
# object; nor may the target creep further than DRIFT_M from the first fix.
JUMP_M = 1.0
DRIFT_M = 1.5
LOST_AFTER_S = 3.0
# Closer than this the pitched head camera cuts the object out of frame: keep the last fix.
NEAR_M = 1.2


def live_matches(
    query: str, objects: list[dict[str, Any]], live_age_s: float | None
) -> tuple[list[tuple[dict[str, Any], float | None]], bool]:
    """Detections of `query` young enough to aim at, each with its age since capture
    (None: no age_s on the wire, accepted as before E1), and whether one was too old."""
    from bridge.object_memory import label_matches

    fresh: list[tuple[dict[str, Any], float | None]] = []
    stale = False
    for o in objects:
        if not (
            label_matches(str(o.get("label", "")), query)
            and isinstance(o.get("range_m"), (int, float))
            and isinstance(o.get("bearing_deg"), (int, float))
        ):
            continue
        if not isinstance(o.get("age_s"), (int, float)):
            fresh.append((o, None))
            continue
        age = float(o["age_s"]) + (live_age_s or 0.0)
        if age <= LIVE_MAX_AGE_S:
            fresh.append((o, age))
        else:
            stale = True
    return fresh, stale


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
    live_age_s: float | None = None,
) -> dict[str, Any]:
    """Pick the target. Returns {range_m, bearing_deg, label, source} or {error, ...}.

    `live_age_s` is the report's own age; live targets carry `age_s` since capture
    (None when the detector sent none).
    """
    from bridge.object_memory import label_matches

    seen = sorted({str(o.get("label")) for o in live_objects})
    fresh, stale = live_matches(query, live_objects, live_age_s)
    if fresh:
        best, age = max(fresh, key=lambda m: float(m[0].get("confidence") or 0.0))
        return {
            "label": best["label"],
            "range_m": float(best["range_m"]),
            "bearing_deg": float(best["bearing_deg"]),
            "source": "live",
            "age_s": age,
        }
    if stale:
        return {
            "error": "stale_detection",
            "detail": (
                f"the {query!r} detection is older than {LIVE_MAX_AGE_S} s. Look at it "
                "again (turn toward it or look_around), then call go_to_object again."
            ),
            "in_view": seen,
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


class Follow:
    """Re-aims a walk at fresh sightings of its target, in the frame walk_to steers in.

    A miss counts only while the target should be in the camera's view (object
    memory's rule): not looked at is not absent. Memory targets
    (abort_when_lost=False) were out of view to begin with, so they only re-aim.
    """

    def __init__(self, target_xy: tuple[float, float], now: float, abort_when_lost: bool) -> None:
        self.first = self.target = target_xy
        self.last_ok = now
        self.abort_when_lost = abort_when_lost

    def update(
        self,
        candidates: list[tuple[float, float]],
        pose: tuple[float, float, float],
        now: float,
    ) -> tuple[float, float] | None:
        """`pose` is the robot's (x, y, yaw) now. The target to walk to, or None once lost."""
        from bridge.object_memory import (
            FORGET_MAX_RANGE_M,
            FORGET_MIN_RANGE_M,
            FOV_HALF_DEG,
            egocentric,
        )

        here = {"x_m": pose[0], "y_m": pose[1], "yaw_deg": math.degrees(pose[2])}
        ok = [
            c
            for c in candidates
            if math.dist(c, self.target) <= JUMP_M
            and math.dist(c, self.first) <= DRIFT_M
            and egocentric(here, *c)[0] <= MAX_RANGE_M
        ]
        if ok:
            self.target = min(ok, key=lambda c: math.dist(c, self.target))
            self.last_ok = now
            return self.target
        rng, bearing = egocentric(here, *self.target)
        in_view = abs(bearing) <= FOV_HALF_DEG and FORGET_MIN_RANGE_M <= rng <= FORGET_MAX_RANGE_M
        if not in_view or rng <= NEAR_M:
            self.last_ok = now
        if self.abort_when_lost and now - self.last_ok > LOST_AFTER_S:
            return None
        return self.target
