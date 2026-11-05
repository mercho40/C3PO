"""Object memory — where the robot last saw things (docs/autonomy/PLAN.md Phase 4.1).

The world model (D7) is a snapshot: it says what the detector sees *now*, and
an object that left the camera's view leaves the snapshot with it. "¿Dónde dejé
la billetera?" needs the opposite — a record that outlives the frame. This is
the lightweight object table from docs/autonomy/research/03-mapping-navigation.md
§1.3, kept as small as the question:

    label, x, y, frame, seen_count, first_seen, last_seen, best confidence, last range

How it is fed: every accepted perception report (`rt/c3po/world_summary`, 4 Hz)
carries the egocentric detections AND the pose they were measured against, so
one report is one batch. `observe()` turns each range/bearing into a point in
the pose's frame, merges it into a same-label entry within MERGE_RADIUS_M, or
starts a new one.

The frame is recorded per entry and never assumed. Today the report's pose is
FAST-LIO's `/odom` — NOT the vendor odometry `walk_to` and landmarks use — and
it re-origins whenever the nav container restarts. A later map frame (Phase 3
relocalization) only has to put `pose_frame` in the report; entries from
another frame are kept but get no distance/bearing, because a point in one
frame measured against a pose in another is a confident wrong answer.

Forgetting ("forget when re-observed absent"): when an entry's spot is inside
the camera's view, in depth range and not hidden behind something nearer, a
frame that does not see it counts as a miss. MISSES_TO_FORGET consecutive
misses remove it. A sighting resets the count. Frames taken while the robot is
turning fast are ignored for both sightings and misses: the report pairs the
newest pose with detections up to ~1.5 s old, so at turn speed a bearing lands
tens of degrees off.

Pure logic: no DDS, no MCP. The bridge wires `observe_report` to the perception
link in `mcp_server.main()`.
"""

from __future__ import annotations

import json
import math
import os
import tempfile
import threading
import time
import unicodedata
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

import structlog

log = structlog.get_logger(__name__)

# Same-label detections closer than this are one object (03 §1.3).
MERGE_RADIUS_M = 0.5
# Running-mean weight cap: after this many sightings a new one still moves the
# point by 1/N, so an object nudged 30 cm drifts over instead of being frozen.
POSITION_WEIGHT_CAP = 10

# The spot is "in view" when inside this half-angle and range window. The D435i
# colour HFOV is ~69°, so ±30° leaves a margin where a half-cropped object at
# the image edge is not counted as missing.
FOV_HALF_DEG = 30.0
FORGET_MIN_RANGE_M = 0.5  # closer than this it can be under the camera's view
FORGET_MAX_RANGE_M = 3.0  # beyond this small objects are not detected reliably
# Something detected nearer on (about) the same bearing may be hiding the entry.
OCCLUSION_BEARING_DEG = 10.0
OCCLUSION_MARGIN_M = 0.3
# Consecutive in-view misses before an entry is dropped. At 4 Hz, ~2 s of
# looking straight at the spot.
MISSES_TO_FORGET = 8

# Ignore frames while turning faster than this (see module docstring).
MAX_YAW_RATE_DEG_S = 30.0
# A previous pose older than this says nothing about the current rate.
RATE_WINDOW_S = 1.0

# Count-only changes (seen_count, last_seen) are flushed at most this often;
# new and removed entries are written at once.
SAVE_EVERY_S = 5.0
# Bound on the table. The least recently seen entry goes first.
MAX_ENTRIES = 200

# The pose frame when the report does not name one: world_model_publisher reads
# FAST-LIO's /odom.
DEFAULT_FRAME = "odom"

# Spanish words people say -> the detector's (COCO, English) labels. Not a
# translator: a query that matches nothing returns the known labels instead,
# so the LLM can pick the right one itself.
SYNONYMS: dict[str, tuple[str, ...]] = {
    "billetera": ("wallet", "handbag"),
    "cartera": ("handbag", "wallet"),
    "bolso": ("handbag", "backpack"),
    "mochila": ("backpack",),
    "reloj": ("watch", "clock"),
    "tele": ("tv",),
    "televisor": ("tv",),
    "television": ("tv",),
    "tv": ("tv",),
    "monitor": ("tv", "monitor"),
    "pantalla": ("tv", "monitor", "laptop"),
    "celular": ("cell phone",),
    "telefono": ("cell phone",),
    "movil": ("cell phone",),
    "control": ("remote",),
    "compu": ("laptop",),
    "computadora": ("laptop",),
    "notebook": ("laptop",),
    "teclado": ("keyboard",),
    "taza": ("cup",),
    "vaso": ("cup", "wine glass"),
    "copa": ("wine glass",),
    "botella": ("bottle",),
    "silla": ("chair",),
    "sillon": ("couch",),
    "sofa": ("couch",),
    "mesa": ("dining table",),
    "libro": ("book",),
    "planta": ("potted plant",),
    "heladera": ("refrigerator",),
    "persona": ("person",),
    "llave": ("keys", "key"),
    "anteojo": ("glasses",),
    "lente": ("glasses",),
    "tijera": ("scissors",),
    "paraguas": ("umbrella",),
    "pelota": ("sports ball",),
}
_STOPWORDS = {"el", "la", "los", "las", "un", "una", "mi", "mis", "tu", "the", "a", "my", "de"}


def default_path() -> Path:
    """Next to landmarks.json. Override with `OBJECT_MEMORY_PATH`."""
    override = os.environ.get("OBJECT_MEMORY_PATH")
    if override:
        return Path(override)
    return Path.home() / ".c3po" / "objects.json"


# -- geometry ------------------------------------------------------------------


def _norm180(deg: float) -> float:
    return (deg + 180.0) % 360.0 - 180.0


def to_world(pose: dict[str, float], range_m: float, bearing_deg: float) -> tuple[float, float]:
    """Egocentric range/bearing (0 ahead, + left/CCW) -> point in the pose's frame."""
    heading = math.radians(float(pose["yaw_deg"]) + bearing_deg)
    return (
        float(pose["x_m"]) + range_m * math.cos(heading),
        float(pose["y_m"]) + range_m * math.sin(heading),
    )


def egocentric(pose: dict[str, float], x: float, y: float) -> tuple[float, float]:
    """Point in the pose's frame -> (range_m, bearing_deg) from the robot."""
    dx, dy = x - float(pose["x_m"]), y - float(pose["y_m"])
    bearing = math.degrees(math.atan2(dy, dx)) - float(pose["yaw_deg"])
    return math.hypot(dx, dy), _norm180(bearing)


# -- label matching --------------------------------------------------------------


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text.lower().replace("_", " "))
    return "".join(c for c in decomposed if not unicodedata.combining(c)).strip()


def _singular(word: str) -> str:
    if word.endswith("es") and len(word) > 4 and word[:-2] in SYNONYMS:
        return word[:-2]
    if word.endswith("s") and len(word) > 3:
        return word[:-1]
    return word


def query_labels(query: str) -> set[str]:
    """Everything a query could mean as a detector label, folded."""
    words = [w for w in _fold(query).split() if w not in _STOPWORDS]
    out: set[str] = set()
    if words:
        out.add(" ".join(words))
    for word in words:
        for form in (word, _singular(word)):
            out.add(form)
            out.update(SYNONYMS.get(form, ()))
    return out


def label_matches(label: str, query: str) -> bool:
    folded = _fold(label)
    wanted = query_labels(query)
    # "glass" finds "wine glass"; whole words only, so "cup" never finds "cupboard".
    return folded in wanted or any(w in folded.split() for w in wanted)


# -- the store -----------------------------------------------------------------


@dataclass
class MemoryEntry:
    id: str
    label: str
    x_m: float
    y_m: float
    frame: str
    seen_count: int
    first_seen: float
    last_seen: float
    best_confidence: float | None
    last_range_m: float
    misses: int = 0

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> MemoryEntry:
        conf = d.get("best_confidence")
        return cls(
            id=str(d["id"]),
            label=str(d["label"]),
            x_m=float(d["x_m"]),
            y_m=float(d["y_m"]),
            frame=str(d.get("frame", DEFAULT_FRAME)),
            seen_count=int(d.get("seen_count", 1)),
            first_seen=float(d["first_seen"]),
            last_seen=float(d["last_seen"]),
            best_confidence=None if conf is None else float(conf),
            last_range_m=float(d.get("last_range_m", 0.0)),
            misses=int(d.get("misses", 0)),
        )

    def describe(
        self, pose: dict[str, float] | None, frame: str | None, now: float
    ) -> dict[str, Any]:
        """What the agent reads: where, how far from here, how long ago."""
        d: dict[str, Any] = {
            "id": self.id,
            "label": self.label,
            "x_m": round(self.x_m, 2),
            "y_m": round(self.y_m, 2),
            "frame": self.frame,
            "last_seen_s_ago": round(max(0.0, now - self.last_seen), 1),
            "seen_count": self.seen_count,
            "last_range_m": round(self.last_range_m, 2),
        }
        if self.best_confidence is not None:
            d["confidence"] = round(self.best_confidence, 2)
        if pose is not None and frame == self.frame:
            rng, bearing = egocentric(pose, self.x_m, self.y_m)
            d["distance_m"] = round(rng, 2)
            d["bearing_deg"] = round(bearing, 1)
        return d


def _detection(raw: Any) -> tuple[str, float, float, float | None] | None:
    """(label, range, bearing, confidence) from one wire object, or None.

    Extra fields (track_id, depth_source, ...) are ignored on purpose.
    """
    if not isinstance(raw, dict):
        return None
    try:
        label = str(raw["label"]).strip()
        rng = float(raw["range_m"])
        bearing = float(raw["bearing_deg"])
        conf = raw.get("confidence")
        conf = None if conf is None else float(conf)
    except (KeyError, TypeError, ValueError):
        return None
    if not label or not math.isfinite(rng) or not math.isfinite(bearing) or rng <= 0:
        return None
    return label, rng, bearing, conf


class ObjectMemory:
    """Thread-safe object table, optionally backed by a JSON file (`path=None`: memory only)."""

    def __init__(self, path: Path | None = None, clock=time.time) -> None:
        self._entries: dict[str, MemoryEntry] = {}
        self._lock = threading.Lock()
        self._path = path
        self._clock = clock
        self._next_id = 0
        self._last_pose: tuple[float, float] | None = None  # (time, yaw_deg)
        self._last_save = 0.0
        self._dirty = False
        if path is not None:
            self._load()

    # -- persistence ---------------------------------------------------------

    def _load(self) -> None:
        assert self._path is not None
        if not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text())
            entries = [MemoryEntry.from_dict(d) for d in raw.get("objects", [])]
        except Exception as exc:
            # Same rule as landmarks: a corrupt file must not stop the bridge.
            log.warning("object_memory.load_failed", path=str(self._path), error=str(exc))
            return
        self._entries = {e.id: e for e in entries}
        suffixes = [e.id.rsplit("_", 1)[-1] for e in entries]
        self._next_id = max((int(s) for s in suffixes if s.isdigit()), default=0)
        log.info("object_memory.loaded", count=len(entries), path=str(self._path))

    def _save_locked(self, now: float, force: bool) -> None:
        if self._path is None:
            return
        if not force and (not self._dirty or now - self._last_save < SAVE_EVERY_S):
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = {"version": 1, "objects": [asdict(e) for e in self._entries.values()]}
            # Write-then-rename, as landmarks.py: a crash mid-write keeps the old file.
            fd, tmp = tempfile.mkstemp(
                dir=str(self._path.parent), prefix=".objects-", suffix=".json"
            )
            try:
                with os.fdopen(fd, "w") as fh:
                    json.dump(payload, fh, indent=2)
                os.replace(tmp, self._path)
            except Exception:
                Path(tmp).unlink(missing_ok=True)
                raise
            self._dirty = False
            self._last_save = now
        except Exception as exc:
            log.warning("object_memory.save_failed", path=str(self._path), error=str(exc))

    def flush(self) -> None:
        with self._lock:
            self._save_locked(self._clock(), force=self._dirty)

    # -- feeding -------------------------------------------------------------

    def _turning_fast_locked(self, now: float, yaw_deg: float) -> bool:
        prev, self._last_pose = self._last_pose, (now, yaw_deg)
        if prev is None or not (0 < now - prev[0] <= RATE_WINDOW_S):
            return False
        return abs(_norm180(yaw_deg - prev[1])) / (now - prev[0]) > MAX_YAW_RATE_DEG_S

    def observe(
        self,
        pose: dict[str, float],
        objects: list[Any],
        frame: str = DEFAULT_FRAME,
        now: float | None = None,
    ) -> None:
        """One detection batch + the pose it was measured against.

        Call only for frames where the detector actually looked: an empty
        `objects` here means "looked and saw nothing", which counts misses.
        """
        now = self._clock() if now is None else now
        dets = [d for d in (_detection(o) for o in objects) if d is not None]
        with self._lock:
            if self._turning_fast_locked(now, float(pose["yaw_deg"])):
                return
            structural = False
            matched: set[str] = set()
            for label, rng, bearing, conf in dets:
                x, y = to_world(pose, rng, bearing)
                entry = self._nearest_locked(label, x, y, frame)
                if entry is None:
                    self._next_id += 1
                    entry = MemoryEntry(
                        id=f"obj_{self._next_id}",
                        label=label,
                        x_m=x,
                        y_m=y,
                        frame=frame,
                        seen_count=1,
                        first_seen=now,
                        last_seen=now,
                        best_confidence=conf,
                        last_range_m=rng,
                    )
                    self._entries[entry.id] = entry
                    structural = True
                else:
                    w = min(entry.seen_count, POSITION_WEIGHT_CAP)
                    entry.x_m = (entry.x_m * w + x) / (w + 1)
                    entry.y_m = (entry.y_m * w + y) / (w + 1)
                    entry.seen_count += 1
                    entry.last_seen = now
                    entry.last_range_m = rng
                    if conf is not None and conf > (entry.best_confidence or 0.0):
                        entry.best_confidence = conf
                entry.misses = 0
                matched.add(entry.id)

            for entry in list(self._entries.values()):
                if entry.id in matched or entry.frame != frame:
                    continue
                rng, bearing = egocentric(pose, entry.x_m, entry.y_m)
                in_view = abs(bearing) <= FOV_HALF_DEG
                if not (in_view and FORGET_MIN_RANGE_M <= rng <= FORGET_MAX_RANGE_M):
                    continue
                hidden = any(
                    abs(_norm180(b - bearing)) <= OCCLUSION_BEARING_DEG
                    and r < rng - OCCLUSION_MARGIN_M
                    for _, r, b, _ in dets
                )
                if hidden:
                    continue
                entry.misses += 1
                if entry.misses >= MISSES_TO_FORGET:
                    del self._entries[entry.id]
                    structural = True
                    log.info("object_memory.forgot", id=entry.id, label=entry.label)

            while len(self._entries) > MAX_ENTRIES:
                oldest = min(self._entries.values(), key=lambda e: e.last_seen)
                del self._entries[oldest.id]
                structural = True

            self._dirty = True
            self._save_locked(now, force=structural)

    def observe_report(self, report: dict[str, Any]) -> None:
        """Feed one perception report (`world_model_publisher`'s wire shape).

        A report with no pose, or whose detector is offline, says nothing about
        where things are — and must not count as "looked and saw nothing".
        """
        pose = report.get("pose")
        if not report.get("detector_online") or not isinstance(pose, dict):
            return
        objects = report.get("objects")
        if not isinstance(objects, list):
            return
        try:
            self.observe(pose, objects, frame=str(report.get("pose_frame") or DEFAULT_FRAME))
        except (KeyError, TypeError, ValueError) as exc:
            log.warning("object_memory.bad_report", error=str(exc))

    def _nearest_locked(self, label: str, x: float, y: float, frame: str) -> MemoryEntry | None:
        best, best_d = None, MERGE_RADIUS_M
        folded = _fold(label)
        for e in self._entries.values():
            if e.frame != frame or _fold(e.label) != folded:
                continue
            d = math.hypot(e.x_m - x, e.y_m - y)
            if d <= best_d:
                best, best_d = e, d
        return best

    # -- queries ---------------------------------------------------------------

    def list_all(self) -> list[MemoryEntry]:
        """Most recently seen first."""
        with self._lock:
            return sorted(self._entries.values(), key=lambda e: e.last_seen, reverse=True)

    def recall(self, query: str) -> list[MemoryEntry]:
        return [e for e in self.list_all() if label_matches(e.label, query)]

    def labels(self) -> list[str]:
        return sorted({e.label for e in self.list_all()})

    def forget(self, object_id: str) -> int:
        """Remove one entry by id, or every entry with `"all"`. Returns how many went."""
        with self._lock:
            if object_id == "all":
                gone = len(self._entries)
                self._entries.clear()
            else:
                gone = 1 if self._entries.pop(object_id, None) is not None else 0
            if gone:
                self._dirty = True
                self._save_locked(self._clock(), force=True)
            return gone


_memory_singleton: ObjectMemory | None = None


def get_object_memory() -> ObjectMemory:
    global _memory_singleton
    if _memory_singleton is None:
        _memory_singleton = ObjectMemory(path=default_path())
    return _memory_singleton
