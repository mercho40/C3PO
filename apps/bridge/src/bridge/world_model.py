"""The world-model contract — what perception hands the agent (SPEC/D7).

A language model cannot consume 50 Hz point clouds or 30 fps RGB. The thing
that makes autonomy work is not the sensors, it's the layer that turns them
into a few hundred tokens the model can reason over. This module defines that
layer's output, so detectors, models and even the LiDAR stay swappable behind
a stable shape.

Design rules, each of which is load-bearing:

**Egocentric, not world-frame.** Ranges and bearings from the robot, because
that is what maps onto the commands it can issue ("turn left 30°") and what a
model reasons about naturally. World coordinates would force the model to do
trigonometry it is bad at. Bearing is degrees, `0` straight ahead, **positive
to the left (counter-clockwise)** — the same sign convention as `turn`'s
`delta_yaw_radians`. A mismatch here would be a sign-flip bug baked into the
interface itself.

**Absent is not empty.** The single most important rule. If the detector is
offline, `objects` must not be an empty list — an empty list means "I looked
and there is nothing there", which is exactly how a robot walks into a wall it
never saw. Every source carries an explicit status, and anything degraded is
also stated in plain language in `notes`, because that is what the model
actually reads. This is the same false-negative failure as reporting a skill
failed when the robot obeyed: the dangerous direction to be wrong in.

**Truncation is always declared.** A model shown 8 of 40 obstacles, with no
indication the other 32 exist, will reason confidently about a scene it cannot
see. `objects_omitted` is never silently zero.

**Everything carries an age.** A 4-second-old detection is a different fact
from a fresh one when you are moving.

Nothing here imports DDS or perception. The builder composes whatever sources
are available and degrades explicitly when they are not — which is why the
whole contract is testable today, with no robot and no perception stack.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from typing import Any, Literal

# Bumped when the shape changes in a way a consumer would notice. The agent's
# prompt describes this contract, so a silent change is a prompt/behaviour bug.
WORLD_MODEL_VERSION = 1

# Above this, a detection is old enough that the model should not act on it
# without re-checking.
STALE_AFTER_S = 2.0

# How many objects reach the model. The cap exists to protect the token budget;
# what matters is that whatever is dropped is *counted*, never silently lost.
MAX_OBJECTS = 8

SourceStatus = Literal["ok", "stale", "offline"]


@dataclass
class Observation:
    """One thing perception believes is out there, relative to the robot."""

    label: str
    range_m: float
    # Degrees. 0 = straight ahead, positive = left (CCW), range (-180, 180].
    bearing_deg: float
    confidence: float | None = None
    age_s: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "label": self.label,
            "range_m": round(self.range_m, 2),
            "bearing_deg": round(self.bearing_deg, 1),
        }
        if self.confidence is not None:
            d["confidence"] = round(self.confidence, 2)
        if self.age_s:
            d["age_s"] = round(self.age_s, 1)
        return d


@dataclass
class FreeSpace:
    """Distance to the nearest obstacle per coarse sector, in metres.

    Four sectors rather than a full scan: this is a reasoning aid for deciding
    "can I go that way", not a costmap. Nav2 owns real obstacle avoidance
    (D4) — if this ever needs finer resolution, that is a sign the model is
    being asked to do a planner's job.
    """

    ahead_m: float | None = None
    left_m: float | None = None
    right_m: float | None = None
    behind_m: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            k: round(v, 2)
            for k, v in (
                ("ahead_m", self.ahead_m),
                ("left_m", self.left_m),
                ("right_m", self.right_m),
                ("behind_m", self.behind_m),
            )
            if v is not None
        }


@dataclass
class WorldModel:
    """One compact, egocentric snapshot of what the robot can perceive."""

    pose: dict[str, float] | None = None
    objects: list[Observation] = field(default_factory=list)
    objects_omitted: int = 0
    free_space: FreeSpace | None = None
    landmarks: list[Observation] = field(default_factory=list)
    # Per-source health. A key missing from here is itself a bug: every source
    # the contract can carry should report, so "offline" is always explicit.
    sources: dict[str, SourceStatus] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)
    captured_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "version": WORLD_MODEL_VERSION,
            "sources": dict(self.sources),
        }
        if self.pose is not None:
            d["pose"] = self.pose
        if self.objects:
            d["objects"] = [o.to_dict() for o in self.objects]
        if self.objects_omitted:
            d["objects_omitted"] = self.objects_omitted
        if self.free_space is not None:
            fs = self.free_space.to_dict()
            if fs:
                d["free_space"] = fs
        if self.landmarks:
            d["landmarks"] = [lm.to_dict() for lm in self.landmarks]
        # Always last and always present when non-empty: this is the field the
        # model is most likely to act on.
        if self.notes:
            d["notes"] = list(self.notes)
        return d

    def approx_tokens(self) -> int:
        """Rough token estimate of the serialized snapshot (~4 chars/token).

        The contract's whole purpose is fitting in a model's context beside
        everything else, so its size is a property worth asserting on, not an
        emergent accident.
        """
        return len(json.dumps(self.to_dict(), separators=(",", ":"))) // 4


def _degrade(sources: dict[str, SourceStatus], notes: list[str]) -> None:
    """Turn source health into plain language the model will actually read."""
    if sources.get("detector") == "offline":
        notes.append(
            "Object detection is OFFLINE — this is not an empty scene. "
            "Do not assume the path is clear."
        )
    elif sources.get("detector") == "stale":
        notes.append("Object detection is stale; treat obstacles as uncertain.")

    if sources.get("lidar") == "offline":
        notes.append(
            "LiDAR is OFFLINE — free-space distances are unavailable, not infinite."
        )
    if sources.get("pose") == "offline":
        notes.append("Pose is unavailable; relative motion cannot be verified.")


def build(
    *,
    pose: dict[str, float] | None = None,
    pose_age_s: float | None = None,
    objects: list[Observation] | None = None,
    detector_online: bool = False,
    free_space: FreeSpace | None = None,
    lidar_online: bool = False,
    landmarks: list[Observation] | None = None,
    max_objects: int = MAX_OBJECTS,
) -> WorldModel:
    """Compose a snapshot, degrading explicitly for whatever is missing.

    Callers pass what they have. Anything they don't pass is reported as
    `offline` rather than silently omitted — a consumer must never have to
    infer the difference between "nothing detected" and "nothing looked".
    """
    notes: list[str] = []
    sources: dict[str, SourceStatus] = {}

    # Pose
    if pose is None:
        sources["pose"] = "offline"
    elif pose_age_s is not None and pose_age_s > STALE_AFTER_S:
        sources["pose"] = "stale"
    else:
        sources["pose"] = "ok"

    # Objects. `detector_online` is separate from the list being empty on
    # purpose: an online detector that sees nothing is a real, useful fact.
    found = list(objects or [])
    if not detector_online:
        sources["detector"] = "offline"
        # Anything handed to us without a working detector is not trustworthy.
        found = []
    elif found and max(o.age_s for o in found) > STALE_AFTER_S:
        sources["detector"] = "stale"
    else:
        sources["detector"] = "ok"

    # Nearest first — the model should read the thing most likely to matter.
    found.sort(key=lambda o: o.range_m)
    omitted = max(0, len(found) - max_objects)
    kept = found[:max_objects]

    # Free space
    if not lidar_online or free_space is None:
        sources["lidar"] = "offline"
        free_space = None
    else:
        sources["lidar"] = "ok"

    _degrade(sources, notes)

    if omitted:
        notes.append(f"{omitted} more object(s) detected but not listed (nearest shown).")

    return WorldModel(
        pose=pose,
        objects=kept,
        objects_omitted=omitted,
        free_space=free_space,
        landmarks=list(landmarks or []),
        sources=sources,
        notes=notes,
    )


def offline() -> WorldModel:
    """A snapshot with no perception at all — today's real state.

    The perception container (D2/D3) does not exist yet. Returning this, rather
    than an empty-looking scene, is what stops the agent reasoning as though it
    can see.
    """
    return build()
