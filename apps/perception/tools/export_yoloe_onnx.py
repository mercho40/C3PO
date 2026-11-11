#!/usr/bin/env python3
"""Export YOLOE-26 (open vocabulary) to an ONNX TensorRT 8.5 will parse. RUNS OFF-ROBOT.

    # text-prompted: our ~1,200-name vocabulary baked into the weights
    uv run --with "ultralytics>=8.4" --with onnx python \\
        apps/perception/tools/export_yoloe_onnx.py yoloe-26s-seg.pt out/
    # prompt-free: the model's own 4,585-name vocabulary, no list
    uv run --with "ultralytics>=8.4" --with onnx python \\
        apps/perception/tools/export_yoloe_onnx.py yoloe-26s-seg-pf.pt out/

    scp out/yoloe-26s-seg.onnx out/yoloe-26s-seg.labels.txt c3po:~/.c3po/models/

The weights download on first use (ultralytics release assets); so does the
MobileCLIP text encoder `set_classes` needs (~250 MB, laptop only — it is not
in the exported graph). Engine build, env vars and rollback:
docs/autonomy/test-day/perception.md.

WHAT DIFFERS FROM export_yolo11_onnx.py, WHOSE CHECKS THIS REUSES VERBATIM
------------------------------------------------------------------------
1. THE VOCABULARY IS BAKED IN. `set_classes(vocab)` computes the text
   embeddings once and folds them into the head; the ONNX has no text encoder
   and cannot take new prompts. Changing apps/perception/vision/vocab.txt means
   re-exporting and rebuilding the engine. The prompt-free `-pf` checkpoints
   carry their own vocabulary and refuse --vocab.

2. THE NMS-FREE HEAD IS THE DEFAULT (`nms=False, agnostic_nms=True`). YOLOE-26
   has a one-to-one head; exported this way the graph ends in a TopK over the
   8,400 anchors and emits (1, 300, 6 + 32): x0 y0 x1 y1 score class + mask
   coefficients. Measured on an M3 with the real export: 0.2 ms to decode,
   against ~10 ms for the raw head — whose output is (1, 4 + 1223 + 32, 8400),
   42 MB of floats to copy off the GPU every frame. `agnostic_nms=True` picks
   ONE class per anchor, which also keeps `Mod` (TRT >= 8.5.0 only) out of the
   graph. Its new ops (TopK, ArgMax, GatherElements, Where, Expand,
   ConstantOfShape) are all in onnx-tensorrt 8.5's operator list, but this
   graph has NOT been parsed by TRT 8.5 yet — hence:

3. --raw-head: the fallback if trtexec on the Orin rejects the NMS-free graph.
   Same ops as YOLO11 plus ConvTranspose (the mask protos). detector.py
   decodes both layouts, told apart by shape.

4. LABELS ARE <stem>.labels.txt, NOT labels.txt, so copying them next to
   YOLO11n's files cannot overwrite the rollback's labels.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path

from export_yolo11_onnx import BATCH, EXPORT_OPSET, IMGSZ, fail, verify, write_labels

DEFAULT_VOCAB = Path(__file__).resolve().parents[1] / "vision" / "vocab.txt"


def read_vocab(path: Path) -> list[str]:
    """Non-blank, non-# lines, in order. Duplicates refused: they would split a class."""
    names = [ln.strip() for ln in path.read_text(encoding="utf-8").splitlines()]
    names = [n for n in names if n and not n.startswith("#")]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        fail(f"{path} lists {', '.join(dupes)} more than once")
    if not names:
        fail(f"{path} has no class names")
    return names


def export(weights: Path, out_dir: Path, vocab: Path | None, raw_head: bool) -> tuple[Path, Path]:
    try:
        from ultralytics import YOLOE
    except ImportError:
        fail('ultralytics>=8.4 is not installed: uv run --with "ultralytics>=8.4" --with onnx ...')

    prompt_free = "-pf" in weights.stem
    if prompt_free and vocab is not None:
        fail(f"{weights.name} is prompt-free: its vocabulary is built in, drop --vocab")

    model = YOLOE(str(weights))
    if not prompt_free:
        names = read_vocab(vocab or DEFAULT_VOCAB)
        print(f"set_classes: {len(names)} prompts (a minute on CPU)")
        model.set_classes(names)

    names_out = getattr(model, "names", None)
    if not names_out:
        fail(f"{weights} carries no `names` after set_classes — refusing to guess")

    path = model.export(
        format="onnx",
        opset=EXPORT_OPSET,
        dynamic=False,
        simplify=False,
        nms=None if raw_head else False,   # False = the one-to-one, NMS-free head
        agnostic_nms=True,
        imgsz=IMGSZ,
        batch=BATCH,
    )
    out_dir.mkdir(parents=True, exist_ok=True)
    onnx_path = out_dir / (weights.stem + ".onnx")
    if Path(path).resolve() != onnx_path.resolve():
        shutil.move(str(path), str(onnx_path))
    labels_path = out_dir / (weights.stem + ".labels.txt")
    write_labels(names_out, labels_path)
    return onnx_path, labels_path


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    parser.add_argument("weights", type=Path, help="yoloe-26s-seg.pt or yoloe-26s-seg-pf.pt")
    parser.add_argument("out_dir", type=Path)
    parser.add_argument("--vocab", type=Path, default=None,
                        help=f"one prompt per line (default: {DEFAULT_VOCAB})")
    parser.add_argument("--raw-head", action="store_true",
                        help="export the raw one-to-many head (CPU NMS) instead of the NMS-free one")
    args = parser.parse_args(argv)

    onnx_path, labels_path = export(args.weights, args.out_dir, args.vocab, args.raw_head)
    verify(onnx_path)

    name = onnx_path.name
    print(f"\nwrote {onnx_path} and {labels_path}")
    print(f"next:  scp {onnx_path} {labels_path} c3po:~/.c3po/models/")
    print("then, on the robot (docs/autonomy/test-day/perception.md):")
    print(f"  export C3PO_ONNX=/opt/c3po/models/{name}")
    print(f"  export C3PO_ENGINE=/opt/c3po/engines/{onnx_path.stem}.fp16.plan")
    print(f"  export C3PO_LABELS=/opt/c3po/models/{labels_path.name}")
    print("  c3po/scripts/robot/build_perception engine && c3po/scripts/robot/build_perception bench")
    return 0


if __name__ == "__main__":
    sys.exit(main())
