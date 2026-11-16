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
# base_link (pelvis) -> the robot's front. URDF g1_29dof_rev_1_0: toe contact spheres at
# x=0.12 (all joints zero), 0.09 standing; knees 0.03. Collision is meshes, so 0.15 with margin.
BODY_FRONT_M = 0.15
# base_link never stops closer than this to a near face, whatever stop_distance_m says.
MIN_BASE_TO_FACE_M = BODY_FRONT_M + 0.1


def aim_range(obj: dict[str, Any], to_near_face: bool) -> float | None:
    """The range to walk at: the near face (`near_m`) or the object's centre (`range_m`).
    None when a near-face walk meets a sighting without a near face."""
    value = obj.get("near_m") if to_near_face else obj.get("range_m")
    return float(value) if isinstance(value, (int, float)) else None


def approach(target: dict[str, Any], stop_distance_m: float) -> tuple[float, float]:
    """(range to walk at, how far base_link stops from that point).

    With `near_m` the robot's FRONT stops stop_distance_m from the near face, so base_link
    stops BODY_FRONT_M further back. Without it (old detector, memory) the stop is from the
    centre, as it always was.
    """
    if "near_m" in target:
        return target["near_m"], max(stop_distance_m + BODY_FRONT_M, MIN_BASE_TO_FACE_M)
    return target["range_m"], stop_distance_m


def report(walk: dict[str, Any], target: dict[str, Any]) -> dict[str, Any]:
    """What the model is told it measured: the front-to-near-face gap, or that it has none."""
    final = (walk.get("result") or {}).get("final_distance_m")
    if "near_m" not in target:
        return {
            "note": (
                "final_distance_m is from the robot's pelvis to the object's CENTRE "
                "(no near-face reading); its near side and the robot's front are closer."
            )
        }
    if not isinstance(final, (int, float)):
        return {}
    clearance = round(final - BODY_FRONT_M, 2)
    return {
        "clearance_m": clearance,
        "note": (
            f"clearance_m ({clearance} m) is the gap from the robot's front to the object's "
            "near side, measured by depth camera; say that number. final_distance_m is "
            "from the pelvis."
        ),
    }


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


def pick(
    candidates: list[tuple[dict[str, Any], float]], choose: str, stop_distance_m: float
) -> dict[str, Any]:
    """The candidate `choose` names, plus `candidates` (how many matched), `chosen_by` and
    `others` (the rest as (aim range, bearing), for Follow's lock). Each candidate is
    (target, confidence score). One already inside the stop is passed over while another
    is outside it: the nearest chair at the robot's feet is not the one meant.
    """

    def rng(t: dict[str, Any]) -> float:
        return approach(t, stop_distance_m)[0]

    outside = [c for c in candidates if rng(c[0]) > approach(c[0], stop_distance_m)[1]]
    # Positive bearing is LEFT (vlm.bearing_deg); max() of each key is the choice.
    keys: dict[str, Any] = {
        "nearest": lambda c: -rng(c[0]),
        "farthest": lambda c: rng(c[0]),
        "left": lambda c: c[0]["bearing_deg"],
        "right": lambda c: -c[0]["bearing_deg"],
        "center": lambda c: -abs(c[0]["bearing_deg"]),
        "confidence": lambda c: c[1],
    }
    best = max(outside or candidates, key=keys[choose])[0]
    others = [(rng(t), t["bearing_deg"]) for t, _ in candidates if t is not best]
    return {**best, "candidates": len(candidates), "chosen_by": choose, "others": others}


def resolve(
    query: str,
    live_objects: list[dict[str, Any]],
    memory: list[Any],
    mem_pose: dict[str, float] | None,
    now: float,
    frame: str | None = None,
    live_age_s: float | None = None,
    choose: str = "nearest",
    stop_distance_m: float = 0.0,
) -> dict[str, Any]:
    """Pick the target by `choose` (see pick). Returns {range_m, bearing_deg, label, source,
    candidates, chosen_by, others} plus `near_m` (near face) or `range_to: "centre"`, or
    {error, ...}.

    `live_age_s` is the report's own age; live targets carry `age_s` since capture
    (None when the detector sent none).
    """
    from bridge.object_memory import label_matches

    seen = sorted({str(o.get("label")) for o in live_objects})
    fresh, stale = live_matches(query, live_objects, live_age_s)
    if fresh:
        cands = []
        for o, age in fresh:
            live = {
                "label": o["label"],
                "range_m": float(o["range_m"]),
                "bearing_deg": float(o["bearing_deg"]),
                "source": "live",
                "age_s": age,
            }
            near = aim_range(o, to_near_face=True)
            live.update({"near_m": near} if near is not None else {"range_to": "centre"})
            cands.append((live, float(o.get("confidence") or 0.0)))
        return pick(cands, choose, stop_distance_m)
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
            cands = []
            for entry in recent:
                rng, bearing = _relative_from_memory(entry, mem_pose)
                mem = {
                    "label": entry.label,
                    "range_m": rng,
                    "bearing_deg": bearing,
                    "source": "memory",
                    "seen_s_ago": round(now - entry.last_seen, 1),
                    "range_to": "centre",
                }
                # "confidence" on memory keeps the old rule: the most recent sighting.
                cands.append((mem, entry.last_seen))
            return pick(cands, choose, stop_distance_m)
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

    def __init__(
        self,
        target_xy: tuple[float, float],
        now: float,
        abort_when_lost: bool,
        others: list[tuple[float, float]] | None = None,
    ) -> None:
        self.first = self.target = target_xy
        self.last_ok = now
        self.abort_when_lost = abort_when_lost
        # The candidates not chosen: a sighting nearer one of them is that object, not ours.
        self.others = others or []

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
            and all(math.dist(c, self.target) < math.dist(c, o) for o in self.others)
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
