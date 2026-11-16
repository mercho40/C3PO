"""How well `vlm.blur_faces` hides faces: the distance limit, then recall on labelled photos.

Every frame `look`, `verify` and the VLM tools send to Gemini goes through
`blur_faces` first (vlm.py). Its tests stub the detector, so until this script
nobody had a number for how many faces it actually finds.

    uv run python scripts/face_blur_eval.py                  # the distance limit only
    uv run python scripts/face_blur_eval.py --images DIR     # + recall, precision, latency

THE LIMIT IS GEOMETRY, NOT A MEASUREMENT. Detection runs on a copy at most
`vlm._DETECT_W` px wide and Haar's smallest face is `vlm._MIN_FACE_PX` px of it,
so a face farther than `face_w * focal_px * scale / min_px` is never found,
whatever the light. Below that distance it MAY be found; only `--images` says
how often.

LABELS. DIR holds the images and one `labels.csv`, a header and one row per face,
box corners in that image's own pixels:

    file,x0,y0,x1,y1
    expo_001.jpg,812,240,880,318
    expo_001.jpg,1400,260,1452,322
    expo_002.jpg,,,,

A row with empty corners lists an image with no face in it, so a false positive
there still counts. Images not in the file are skipped.

A labelled face counts as hidden when one pixelated region covers at least half
of its box (one region, not their union, so recall is never overstated). A
region counts as a hit when its centre is inside a labelled box. Latency is the
whole `blur_faces` call (decode, detect, pixelate, encode) on THIS machine; the
Orin's number has to be measured on the Orin.
"""

from __future__ import annotations

import argparse
import csv
import math
import os
import statistics
import sys
import time
from collections.abc import Iterable
from pathlib import Path

from bridge import vlm

Box = tuple[int, int, int, int]

#: Adult face width, cheekbone to cheekbone. Haar's box is about that wide.
FACE_W_M = 0.14
#: Width of each feed `_grab_frame` can hand to blur_faces. videohub's SOF0 is in
#: sdk/videohub.py; the vision container's colour stream is detector.py's COLOR_W.
FEED_W_PX = {"videohub": 1920, "vision": 640}
#: Share of a labelled box one pixelated region must cover for the face to count.
COVERED = 0.5


def max_face_distance_m(
    image_w: int,
    hfov_deg: float,
    *,
    face_w_m: float = FACE_W_M,
    detect_w: int = vlm._DETECT_W,
    min_px: int = vlm._MIN_FACE_PX,
) -> float:
    """Farthest distance at which a face is still `min_px` wide in the detection copy."""
    focal_px = (image_w / 2) / math.tan(math.radians(hfov_deg) / 2)
    scale = min(1.0, detect_w / image_w)
    return face_w_m * focal_px * scale / min_px


def parse_labels(lines: Iterable[str]) -> dict[str, list[Box]]:
    """labels.csv -> {file: [(x0, y0, x1, y1), ...]}. Raises ValueError naming the line."""
    rows = csv.reader(lines)
    if next(rows, None) != ["file", "x0", "y0", "x1", "y1"]:
        raise ValueError("line 1: the header must be file,x0,y0,x1,y1")
    out: dict[str, list[Box]] = {}
    for number, row in enumerate(rows, 2):
        if not row:
            continue
        if len(row) != 5 or not row[0].strip():
            raise ValueError(f"line {number}: expected file,x0,y0,x1,y1, got {row}")
        boxes = out.setdefault(row[0].strip(), [])
        corners = [c.strip() for c in row[1:]]
        if not any(corners):
            continue  # an image with no face in it
        try:
            x0, y0, x1, y1 = (int(c) for c in corners)
        except ValueError:
            raise ValueError(f"line {number}: corners must be whole pixels, got {row}") from None
        if x1 <= x0 or y1 <= y0:
            raise ValueError(f"line {number}: x1/y1 must be past x0/y0, got {row}")
        boxes.append((x0, y0, x1, y1))
    return out


def coverage(face: Box, region: Box) -> float:
    """Share of `face` inside `region`."""
    w = min(face[2], region[2]) - max(face[0], region[0])
    h = min(face[3], region[3]) - max(face[1], region[1])
    return max(0, w) * max(0, h) / ((face[2] - face[0]) * (face[3] - face[1]))


def score(faces: list[Box], regions: list[Box]) -> tuple[list[Box], int]:
    """(faces left visible, regions that are on a face)."""
    missed = [f for f in faces if not any(coverage(f, r) >= COVERED for r in regions)]
    hits = sum(
        1
        for r in regions
        if any(f[0] <= (r[0] + r[2]) / 2 <= f[2] and f[1] <= (r[1] + r[3]) / 2 <= f[3] for f in faces)
    )
    return missed, hits


def _limits() -> None:
    print(f"detection copy: at most {vlm._DETECT_W} px wide; smallest face: {vlm._MIN_FACE_PX} px")
    for feed, width in FEED_W_PX.items():
        hfov = vlm.HFOV_DEG[feed]
        scale = min(1.0, vlm._DETECT_W / width)
        d = max_face_distance_m(width, hfov)
        print(
            f"  {feed}: {width} px at {hfov} deg HFOV, detected at x{scale:.2f} -> a "
            f"{FACE_W_M * 100:.0f} cm face is never found past {d:.1f} m"
        )


def _evaluate(directory: Path) -> int:
    import cv2
    import numpy as np

    os.environ["C3PO_FACE_BLUR"] = "1"  # an operator's opt-out would measure nothing
    labels = parse_labels((directory / "labels.csv").read_text().splitlines())
    if not labels:
        print("labels.csv lists no images", file=sys.stderr)
        return 1
    n_faces = n_hidden = n_regions = n_hits = 0
    times_ms: list[float] = []
    for name, faces in labels.items():
        data = (directory / name).read_bytes()
        img = cv2.imdecode(np.frombuffer(data, np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            print(f"{name}: does not decode", file=sys.stderr)
            return 1
        start = time.perf_counter()
        vlm.blur_faces(data)
        times_ms.append((time.perf_counter() - start) * 1000)
        regions = vlm.face_regions(img)
        missed, hits = score(faces, regions)
        n_faces, n_hidden = n_faces + len(faces), n_hidden + len(faces) - len(missed)
        n_regions, n_hits = n_regions + len(regions), n_hits + hits
        for f in missed:
            print(f"  missed: {name} {f} ({f[2] - f[0]} px wide)")
    recall = n_hidden / n_faces if n_faces else float("nan")
    precision = n_hits / n_regions if n_regions else float("nan")
    times_ms.sort()
    p95 = times_ms[min(len(times_ms) - 1, int(0.95 * len(times_ms)))]
    print(
        f"{len(labels)} images, {n_faces} faces: recall {recall:.2f} ({n_hidden}/{n_faces}), "
        f"precision {precision:.2f} ({n_hits}/{n_regions} regions on a face)"
    )
    print(
        f"blur_faces latency on this machine: median {statistics.median(times_ms):.0f} ms, "
        f"p95 {p95:.0f} ms, max {times_ms[-1]:.0f} ms"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--images", type=Path, help="a directory with labels.csv (see above)")
    args = parser.parse_args(argv)
    _limits()
    return _evaluate(args.images) if args.images else 0


if __name__ == "__main__":
    sys.exit(main())
