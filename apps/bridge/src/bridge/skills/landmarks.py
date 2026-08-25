"""remember_landmark / recall_landmark / list_landmarks — named-pose memory.

Lets a skill sequence tag the robot's current pose with a name ("kitchen",
"charging_dock") and later recall it — e.g. as a `walk_to` target.

Persistence, and why it needs a frame guard
-------------------------------------------
Landmarks are stored to a JSON file so they survive a bridge restart. That is
worth having: the bridge gets restarted constantly during development, and the
odometry frame those coordinates live in does **not** belong to the bridge — it
is produced by `ai_odom_node` on the robot's control board, a separate machine.
Restart the bridge and the frame is still there, so the coordinates are still
valid.

Reboot the *robot*, though, and the estimator restarts with its origin wherever
it happens to be. Every saved coordinate silently becomes wrong — not missing,
wrong — and a landmark that navigates the robot to the wrong place is far worse
than one that was forgotten. So each landmark records the control board's
`tick` at save time. `tick` is monotonic within a boot, so a current tick lower
than the saved one means the board restarted and the frame is gone.

That guard reports rather than deletes: the *names* are still meaningful to an
operator ("we had a landmark called kitchen"), only the coordinates are not.
Callers must surface `frame_stale` instead of quietly using the pose — same
rule as the world model's "absent is not empty".

**Unverified:** that `tick` resets on a control-board reboot is inferred from
its being a boot-relative counter, not measured. It has never been observed
across a reboot. The failure mode if wrong is a stale landmark that is *not*
flagged, so treat a `frame_stale` of `false` on a rebooted robot as possible
until someone checks.

Coordinates are odometry, which drifts. Fine for "walk back to roughly there",
not a survey mark. Durable global coordinates need SLAM/map localisation
(D2/D3), at which point landmarks should be re-anchored to the map frame.
"""

from __future__ import annotations

import json
import os
import tempfile
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)


def default_path() -> Path:
    """Where `get_store()` persists. Override with `LANDMARKS_PATH`."""
    override = os.environ.get("LANDMARKS_PATH")
    if override:
        return Path(override)
    return Path.home() / ".c3po" / "landmarks.json"


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
    # Control-board `tick` when saved — identifies the odometry frame these
    # coordinates belong to. None when unknown (stub mode, or no lowstate yet).
    frame_tick: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "x_meters_world": self.x_meters_world,
            "y_meters_world": self.y_meters_world,
            "yaw_radians_world": self.yaw_radians_world,
            "saved_at": self.saved_at,
            "age_s": round(time.time() - self.saved_at, 1),
            "frame_tick": self.frame_tick,
        }

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> Landmark:
        return cls(
            name=str(d["name"]),
            x_meters_world=float(d["x_meters_world"]),
            y_meters_world=float(d["y_meters_world"]),
            yaw_radians_world=float(d["yaw_radians_world"]),
            saved_at=float(d.get("saved_at", time.time())),
            seq=int(d.get("seq", 0)),
            frame_tick=(int(d["frame_tick"]) if d.get("frame_tick") is not None else None),
        )


def frame_is_stale(saved_tick: int | None, current_tick: int | None) -> bool:
    """True when the frame a landmark was saved in no longer exists.

    `tick` counts up within one control-board boot, so a current value *below*
    the saved one means the board restarted and odometry re-origined. Unknown
    on either side means we cannot tell — and we say so by returning False
    rather than guessing, since callers surface staleness as a warning and a
    false alarm on every landmark would train operators to ignore it.
    """
    if saved_tick is None or current_tick is None:
        return False
    return current_tick < saved_tick


class LandmarkStore:
    """Thread-safe name -> pose store, optionally backed by a JSON file.

    `path=None` means in-memory only. Persistence is opt-in so tests and
    throwaway instances never touch a real file.
    """

    def __init__(self, path: Path | None = None) -> None:
        self._landmarks: dict[str, Landmark] = {}
        self._lock = threading.Lock()
        self._next_seq = 0
        self._path = path
        if path is not None:
            self._load()

    # -- persistence ---------------------------------------------------------

    def _load(self) -> None:
        assert self._path is not None
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
            entries = [Landmark.from_dict(d) for d in raw.get("landmarks", [])]
        except Exception as exc:
            # A corrupt file must not stop the bridge from starting — landmarks
            # are a convenience, and refusing to boot over them would take the
            # robot offline for a cosmetic feature.
            log.warning("landmarks.load_failed", path=str(self._path), error=str(exc))
            return
        self._landmarks = {lm.name: lm for lm in entries}
        self._next_seq = max((lm.seq for lm in entries), default=0)
        log.info("landmarks.loaded", count=len(entries), path=str(self._path))

    def _save_locked(self) -> None:
        """Write the store out. Caller must hold the lock."""
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {
                "version": 1,
                "landmarks": [
                    {
                        "name": lm.name,
                        "x_meters_world": lm.x_meters_world,
                        "y_meters_world": lm.y_meters_world,
                        "yaw_radians_world": lm.yaw_radians_world,
                        "saved_at": lm.saved_at,
                        "seq": lm.seq,
                        "frame_tick": lm.frame_tick,
                    }
                    for lm in self._landmarks.values()
                ],
            }
            # Write-then-rename: a crash mid-write leaves the previous file
            # intact rather than a truncated one that fails to parse on boot.
            fd, tmp = tempfile.mkstemp(
                dir=str(self._path.parent), prefix=".landmarks-", suffix=".json"
            )
            try:
                with os.fdopen(fd, "w") as fh:
                    json.dump(payload, fh, indent=2)
                os.replace(tmp, self._path)
            except Exception:
                Path(tmp).unlink(missing_ok=True)
                raise
        except Exception as exc:
            # Losing a write is better than failing the skill that triggered it.
            log.warning("landmarks.save_failed", path=str(self._path), error=str(exc))

    # -- operations ----------------------------------------------------------

    def remember(
        self,
        name: str,
        pose: dict[str, float],
        frame_tick: int | None = None,
    ) -> Landmark:
        with self._lock:
            self._next_seq += 1
            landmark = Landmark(
                name=name,
                x_meters_world=float(pose["x_meters_world"]),
                y_meters_world=float(pose["y_meters_world"]),
                yaw_radians_world=float(pose["yaw_radians_world"]),
                saved_at=time.time(),
                seq=self._next_seq,
                frame_tick=frame_tick,
            )
            self._landmarks[name] = landmark
            self._save_locked()
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
            existed = self._landmarks.pop(name, None) is not None
            if existed:
                self._save_locked()
            return existed


_store_singleton: LandmarkStore | None = None


def get_store() -> LandmarkStore:
    global _store_singleton
    if _store_singleton is None:
        _store_singleton = LandmarkStore(path=default_path())
    return _store_singleton
