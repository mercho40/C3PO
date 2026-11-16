#!/usr/bin/env python3
"""Recall per class and per distance band, on a folder of labelled robot frames.

    # on the robot, the EXACT production path (TensorRT plan + detector.decode_head):
    docker run --rm --runtime nvidia --entrypoint python3 \\
      -e C3PO_ENGINE=/opt/c3po/engines/yoloe-26s-seg.fp16.plan \\
      -e C3PO_LABELS=/opt/c3po/models/yoloe-26s-seg.labels.txt \\
      -v c3po-trt-engines:/opt/c3po/engines -v ~/.c3po/models:/opt/c3po/models:ro \\
      -v ~/c3po/apps/perception/tools:/opt/c3po/tools:ro -v ~/c3po-testset:/data \\
      c3po/perception-vision:r35.3.1 /opt/c3po/tools/eval_recall.py /data/labelled

    # on a laptop, any ultralytics model (.pt, or an exported .onnx — names ride along):
    uv run --with "ultralytics>=8.4" python apps/perception/tools/eval_recall.py \\
        ~/c3po-testset/labelled --backend ultralytics --model out/yoloe-26s-seg.onnx

THE FOLDER: <stem>.jpg per frame (capture_frames.py output), labelled as either
  <stem>.txt   YOLO format "class cx cy w h" (normalised), names in classes.txt
  <stem>.json  {"objects": [{"label": "wallet", "box": [x0, y0, x1, y1]}]} (pixels)
plus, optionally, <stem>.depth.png + meta.json (from --realsense capture).

DISTANCE BAND per labelled object: the median depth under its box when there is
a depth frame (0.5 m band: < 1.0 m; 1.5 m: < 2.25 m; else 3 m), otherwise the
filename prefix d0.5_/d1.5_/d3_, otherwise "unknown".

A HIT is a prediction at >= --iou overlap whose label is the ground-truth name
OR one of its aliases. Label the set with vocab.txt's names; the aliases map
those onto COCO's ("television set" <- "tv"), which is what makes the YOLO11n
baseline comparable. Extend them with --aliases file.json ({"name": ["alt"]}).
The headline is the per-band MEAN OF PER-CLASS RECALL — the PLAN Phase 2 exit
metric (>= 75 % at <= 1.5 m, >= 50 % at 3 m).
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from typing import Dict, List, Optional, Sequence, Tuple

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "vision"))

Box = Tuple[float, float, float, float]

DEFAULT_ALIASES: Dict[str, List[str]] = {
    "television set": ["tv"],
    "computer monitor": ["tv", "monitor"],
    "mobile phone": ["cell phone", "smartphone"],
    "computer mouse": ["mouse"],
    "remote control": ["remote"],
    "computer keyboard": ["keyboard"],
    "laptop": ["laptop computer"],
    "drinking glass": ["wine glass", "cup", "glass"],
    "mug": ["cup", "coffee cup"],
    "cup": ["mug", "coffee cup", "paper cup", "plastic cup"],
    "potted plant": ["houseplant", "flowerpot"],
    "eyeglasses": ["glasses", "sunglasses"],
    "wristwatch": ["watch", "smartwatch"],
    "keys": ["key"],
    "headphones": ["earphone", "earbuds"],
    "phone charger": ["charger", "charging cable"],
    "backpack": ["bag"],
    "handbag": ["bag", "purse"],
    "dining table": ["table", "desk"],
    "couch": ["sofa"],
    "sofa": ["couch"],
}

BANDS = ("0.5m", "1.5m", "3m")


def band_for(distance_m: Optional[float]) -> str:
    if distance_m is None:
        return "unknown"
    return "0.5m" if distance_m < 1.0 else "1.5m" if distance_m < 2.25 else "3m"


def band_from_name(stem: str) -> Optional[str]:
    m = re.match(r"d(0\.5|1\.5|3)(?:\.0)?_", os.path.basename(stem))
    return None if m is None else {"0.5": "0.5m", "1.5": "1.5m", "3": "3m"}[m.group(1)]


def iou(a: Sequence[float], b: Sequence[float]) -> float:
    ix = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    iy = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = ix * iy
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def read_yolo_labels(text: str, classes: Sequence[str], width: int, height: int) -> List[Tuple[str, Box]]:
    out = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 5:
            continue
        c, cx, cy, w, h = int(parts[0]), *(float(p) for p in parts[1:5])
        out.append((classes[c], ((cx - w / 2) * width, (cy - h / 2) * height,
                                 (cx + w / 2) * width, (cy + h / 2) * height)))
    return out


def match(
    gts: Sequence[Tuple[str, Box]],
    preds: Sequence[Tuple[str, float, Box]],
    aliases: Dict[str, List[str]],
    iou_thr: float = 0.5,
) -> List[bool]:
    """One bool per ground truth: did an unused, label-compatible prediction cover it?"""
    used = set()
    hits = []
    for label, gbox in gts:
        ok = {label, *aliases.get(label, [])}
        best, best_j = iou_thr, None
        for j, (plabel, _conf, pbox) in enumerate(preds):
            if j in used or plabel not in ok:
                continue
            o = iou(gbox, pbox)
            if o >= best:
                best, best_j = o, j
        if best_j is not None:
            used.add(best_j)
        hits.append(best_j is not None)
    return hits


def summarize(records: Sequence[Tuple[str, str, bool]]) -> dict:
    """[(class, band, hit)] -> per-class/band counts and the per-band mean of class recall."""
    cells: Dict[str, Dict[str, List[int]]] = {}
    for label, band, hit in records:
        cell = cells.setdefault(label, {}).setdefault(band, [0, 0])
        cell[0] += int(hit)
        cell[1] += 1
    mean = {}
    for band in sorted({b for _l, b, _h in records}):
        recalls = [c[band][0] / c[band][1] for c in cells.values() if band in c]
        mean[band] = sum(recalls) / len(recalls)
    return {"per_class": cells, "mean_class_recall": mean, "objects": len(records)}


def format_table(summary: dict) -> str:
    bands = [b for b in (*BANDS, "unknown") if b in summary["mean_class_recall"]]
    lines = ["| class | " + " | ".join(bands) + " |", "|---" * (len(bands) + 1) + "|"]
    for label in sorted(summary["per_class"]):
        row = summary["per_class"][label]
        lines.append(f"| {label} | " + " | ".join(
            f"{row[b][0]}/{row[b][1]}" if b in row else "-" for b in bands) + " |")
    lines.append("| **mean class recall** | " + " | ".join(
        f"**{summary['mean_class_recall'][b]:.0%}**" for b in bands) + " |")
    return "\n".join(lines)


# --- the impure half: files, depth, models -------------------------------------


def load_ground_truth(stem: str, width: int, height: int, classes: Sequence[str]):
    if os.path.exists(stem + ".json"):
        with open(stem + ".json") as fh:
            return [(o["label"], tuple(o["box"])) for o in json.load(fh)["objects"]]
    if os.path.exists(stem + ".txt"):
        with open(stem + ".txt") as fh:
            return read_yolo_labels(fh.read(), classes, width, height)
    return None


def make_predictor(args):
    """-> (predict(rgb ndarray, path) -> [(label, conf, box)]), per --backend."""
    if args.backend == "ultralytics":
        from ultralytics import YOLO

        model = YOLO(args.model)

        def predict_ultralytics(_rgb, path):
            r = model.predict(path, conf=args.conf, verbose=False)[0]
            return [(r.names[int(c)], float(s), tuple(float(v) for v in b))
                    for b, c, s in zip(r.boxes.xyxy.tolist(), r.boxes.cls.tolist(),
                                       r.boxes.conf.tolist())]
        return predict_ultralytics

    if args.conf is not None:
        os.environ["C3PO_VISION_CONF"] = str(args.conf)  # read at detector import
    from c3po_vision.detector import LABELS_PATH, TensorRTDetector, label_for, load_labels

    det, labels = TensorRTDetector(), load_labels(LABELS_PATH)

    def predict_trt(rgb, _path):
        return [(label_for(labels, d[0]), d[1], d[2])
                for d in det.infer(rgb[:, :, ::-1].copy())]  # the camera hands it BGR
    return predict_trt


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Recall per class and distance band.")
    ap.add_argument("folder")
    ap.add_argument("--backend", choices=("trt", "ultralytics"), default="trt")
    ap.add_argument("--model", help="ultralytics backend: .pt / .onnx")
    ap.add_argument("--conf", type=float, default=None, help="default: the detector's (0.35)")
    ap.add_argument("--iou", type=float, default=0.5)
    ap.add_argument("--aliases", help="JSON {name: [accepted labels]} merged over the defaults")
    ap.add_argument("--json", help="also write the summary here")
    args = ap.parse_args(argv)
    if args.backend == "ultralytics" and not args.model:
        ap.error("--backend ultralytics needs --model")
    if args.backend == "ultralytics" and args.conf is None:
        args.conf = 0.35

    import numpy as np
    from c3po_vision.grounding import median_depth_m
    from PIL import Image

    aliases = dict(DEFAULT_ALIASES)
    if args.aliases:
        with open(args.aliases) as fh:
            aliases.update(json.load(fh))
    classes: List[str] = []
    if os.path.exists(os.path.join(args.folder, "classes.txt")):
        with open(os.path.join(args.folder, "classes.txt")) as fh:
            classes = [ln.strip() for ln in fh if ln.strip()]
    depth_scale = 0.001
    if os.path.exists(os.path.join(args.folder, "meta.json")):
        with open(os.path.join(args.folder, "meta.json")) as fh:
            depth_scale = json.load(fh)["depth_scale"]

    predict = make_predictor(args)
    records: List[Tuple[str, str, bool]] = []
    for name in sorted(os.listdir(args.folder)):
        if not name.endswith(".jpg"):
            continue
        stem = os.path.join(args.folder, name[:-4])
        rgb = np.asarray(Image.open(stem + ".jpg").convert("RGB"))
        h, w = rgb.shape[:2]
        gts = load_ground_truth(stem, w, h, classes)
        if gts is None:
            continue
        depth = None
        if os.path.exists(stem + ".depth.png"):
            depth = np.asarray(Image.open(stem + ".depth.png")).astype(np.uint16)
        hits = match(gts, predict(rgb, stem + ".jpg"), aliases, args.iou)
        for (label, box), hit in zip(gts, hits):
            band = band_from_name(stem) or "unknown"
            if depth is not None:
                z, _n = median_depth_m(depth, box, width=w, height=h, depth_scale=depth_scale)
                band = band_for(z) if z is not None else band
            records.append((label, band, hit))

    if not records:
        print("no labelled frames found (need <stem>.jpg + <stem>.txt/.json)", file=sys.stderr)
        return 1
    summary = summarize(records)
    print(format_table(summary))
    if args.json:
        with open(args.json, "w") as fh:
            json.dump(summary, fh, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
