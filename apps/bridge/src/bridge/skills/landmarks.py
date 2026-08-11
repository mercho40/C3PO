"""remember_landmark / recall_landmark / list_landmarks — named-pose memory.

Lets a skill sequence tag the robot's current world-frame pose with a name
("kitchen", "charging_dock") and later recall it — e.g. as a `walk_to`
target. In-memory only (same tradeoff as `task_runtime.TaskRegistry`): the
bridge process is the store, so landmarks don't survive a restart. Fine for
v1 — persistence is a `packages/shared`-backed follow-up once there's an
actual multi-session use case.

Real-G1 caveat: `remember_landmark` needs `get_state().pose`, which is null
on real hardware until Phase 1b (see apps/bridge/README.md) wires a
world-frame pose source. It fails cleanly with a clear reason rather than
storing a garbage landmark.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass
from typing import Any


@dataclass
class Landmark:
    name: str
    x_meters_world: float
    y_meters_world: float
    yaw_radians_world: float
    saved_at: float
    # Monotonic insertion counter, used for ordering. `saved_at` is wall-clock
    # and is kept only for display: two landmarks saved microseconds apart can
    # share a `time.time()` value, and because `sorted` is stable a tie there
    # silently collapses "most recent first" back into insertion order. Same
    # failure mode as ordering chat messages by timestamp.
    seq: int = 0

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "x_meters_world": self.x_meters_world,
            "y_meters_world": self.y_meters_world,
            "yaw_radians_world": self.yaw_radians_world,
            "saved_at": self.saved_at,
            "age_s": round(time.time() - self.saved_at, 1),
        }


class LandmarkStore:
    """Thread-safe in-memory name -> pose store."""

    def __init__(self) -> None:
        self._landmarks: dict[str, Landmark] = {}
        self._lock = threading.Lock()
        self._next_seq = 0

    def remember(self, name: str, pose: dict[str, float]) -> Landmark:
        with self._lock:
            self._next_seq += 1
            landmark = Landmark(
                name=name,
                x_meters_world=float(pose["x_meters_world"]),
                y_meters_world=float(pose["y_meters_world"]),
                yaw_radians_world=float(pose["yaw_radians_world"]),
                saved_at=time.time(),
                seq=self._next_seq,
            )
            self._landmarks[name] = landmark
        return landmark

    def recall(self, name: str) -> Landmark | None:
        with self._lock:
            return self._landmarks.get(name)

    def list_all(self) -> list[Landmark]:
        with self._lock:
            # By insertion counter, not timestamp — see `Landmark.seq`.
            return sorted(self._landmarks.values(), key=lambda lm: lm.seq, reverse=True)

    def forget(self, name: str) -> bool:
        with self._lock:
            return self._landmarks.pop(name, None) is not None


_store_singleton: LandmarkStore | None = None


def get_store() -> LandmarkStore:
    global _store_singleton
    if _store_singleton is None:
        _store_singleton = LandmarkStore()
    return _store_singleton
