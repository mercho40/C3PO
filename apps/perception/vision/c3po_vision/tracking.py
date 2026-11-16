"""Frame-to-frame identity for detections: a greedy IoU tracker. Stdlib only.

`track_id` on /c3po/objects is what lets a consumer (the bridge's object memory)
say "the same mug as a second ago" instead of "a mug, again". It is an int,
unique for the life of the detector process, and it is NOT stable across a
container restart — consumers must treat it as a hint to merge, not a key.

Why IoU and not BoT-SORT: no new dependency in a five-package image, no
appearance model on a GPU shared with Whisper, and at 10 Hz a still or slowly
moving object overlaps itself heavily from tick to tick. What it does not do:
survive a fast head turn (the boxes stop overlapping) or an occlusion longer
than `max_missed` ticks. Both just mint a new id, which is the safe failure.

shortcut: no motion model; upgrade to BoT-SORT/ByteTrack if the object memory
shows ids churning while the robot turns.
"""

from __future__ import annotations

from typing import List, Optional, Sequence, Tuple

Box = Tuple[float, float, float, float]


def iou(a: Sequence[float], b: Sequence[float]) -> float:
    """Intersection over union of two (x0, y0, x1, y1) boxes; 0.0 if disjoint."""
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


class IouTracker:
    """Greedy best-IoU matching, one detection per track per tick.

    A detection keeps a track's id if it overlaps the track's last box by at
    least `iou_threshold` AND has the same label — or, with ~1,200 classes
    where "cup" and "mug" flicker on one object, any label at `relabel_iou`.
    A track not seen for more than `max_missed` consecutive ticks is forgotten.
    """

    def __init__(
        self, iou_threshold: float = 0.3, relabel_iou: float = 0.7, max_missed: int = 10
    ) -> None:
        self.iou_threshold = iou_threshold
        self.relabel_iou = relabel_iou
        self.max_missed = max_missed
        self._next_id = 1
        # id -> [label, box, missed]
        self._tracks: dict = {}

    def update(self, detections: Sequence[Tuple[str, Box]]) -> List[int]:
        """[(label, box), ...] for this tick -> one track id per detection, in order."""
        pairs = []
        for tid, (label, box, _missed) in self._tracks.items():
            for j, (dlabel, dbox) in enumerate(detections):
                overlap = iou(box, dbox)
                if overlap >= (self.iou_threshold if dlabel == label else self.relabel_iou):
                    pairs.append((overlap, tid, j))
        pairs.sort(reverse=True)

        ids: List[Optional[int]] = [None] * len(detections)
        used = set()
        for _overlap, tid, j in pairs:
            if tid in used or ids[j] is not None:
                continue
            used.add(tid)
            ids[j] = tid

        for tid in list(self._tracks):
            if tid not in used:
                self._tracks[tid][2] += 1
                if self._tracks[tid][2] > self.max_missed:
                    del self._tracks[tid]

        out: List[int] = []
        for j, (dlabel, dbox) in enumerate(detections):
            tid = ids[j]
            if tid is None:
                tid = self._next_id
                self._next_id += 1
            self._tracks[tid] = [dlabel, tuple(dbox), 0]
            out.append(tid)
        return out
