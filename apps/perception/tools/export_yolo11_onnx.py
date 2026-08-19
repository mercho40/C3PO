#!/usr/bin/env python3
"""Export YOLO11 to an ONNX that TensorRT 8.5.2.2 will actually parse. RUNS OFF-ROBOT.

    uv run --with ultralytics --with onnx python \
        apps/perception/tools/export_yolo11_onnx.py yolo11n.pt out/
    scp out/yolo11n.onnx out/labels.txt c3po:~/.c3po/models/

Then `build_perception engine` turns the ONNX into a TRT plan ON the Jetson, in
a named volume, because a plan is tied to the exact GPU, TRT version and build
flags and cannot travel. This script does the half that does NOT need the robot:
ultralytics + torch is a multi-GB install that has no business on a 16 GB Jetson
shared with another team, and the export is deterministic.

WHY THIS SCRIPT EXISTS AT ALL, GIVEN IT IS ~FOUR LINES OF ULTRALYTICS
---------------------------------------------------------------------
Because the default flags produce a file that TensorRT 8.5 rejects, and because
the ways it rejects them are not obviously about the export.

1. OPSET. ultralytics defaults to opset 20. TRT 8.5's ONNX parser tops out at
   17 — `onnx-tensorrt` branch `release/8.5-GA`, `docs/operators.md` line 1:
   "TensorRT 8.5 supports operators up to Opset 17". We export 16 and refuse
   anything above 17 below.

   NOT ir_version. TRT 8.5's parser only LOGS the IR version
   (`ModelImporter.cpp:698`); the "max supported IR version: 8" hard error that
   people find when they search is an ONNX **Runtime** message, and current
   ultralytics clamps ir_version to 10 regardless. Checking it here would make
   this script refuse files that parse perfectly. Opset is what matters.

2. nms=False. `nms=True` does parse on 8.5, but it injects NonZero, GatherND,
   ScatterND and NonMaxSuppression — all added in 8.5 GA itself — plus
   data-dependent output shapes, which require `enqueueV3` and an
   `IOutputAllocator` in the runtime instead of the fixed allocations
   detector.py uses. That is a large amount of fragile surface to buy ~1 ms
   against a ~5-8 ms inference. NMS stays on the CPU, in detector.py.

3. dynamic=False. A symbolic input dimension makes the engine an optimisation
   PROFILE problem: trtexec needs --minShapes/--optShapes/--maxShapes, and
   `../vision/entrypoint.sh` passes none of those. The failure is at engine
   build time, on the robot, in the container's first start.

4. simplify=False. onnxsim is another dependency, it is not needed for the ops
   YOLO11 emits, and a graph rewrite between here and the parser is one more
   thing that can differ between the file you validated and the file TRT reads.

5. THE LABELS COME FROM THE CHECKPOINT. `model.names`, from the same file we
   just exported, never a hardcoded COCO list. A fine-tuned checkpoint with a
   COCO labels file reports "person" for a traffic cone — the detector is
   technically working, the ranges are right, and every word the LLM is given
   is wrong. Nothing downstream can catch that.

WHAT "REFUSE" MEANS HERE
------------------------
Every check below exits non-zero rather than warning. The next step after this
script is a 2-5 minute engine build inside a container on a robot that is half
somebody else's; a warning printed here is a warning nobody reads until then.

This file is the only thing in apps/perception that runs on a normal machine
with a normal Python. It imports ultralytics and onnx, which is exactly why it
lives in tools/ and not in vision/c3po_vision/ — nothing in the vision image may
depend on it.
"""

from __future__ import annotations

import argparse
import shutil
import sys
from pathlib import Path
from typing import NoReturn

# The export opset, and the ceiling that makes it non-negotiable. See the
# module docstring: 20 is ultralytics' default and TRT 8.5 rejects it.
EXPORT_OPSET = 16
TRT85_MAX_OPSET = 17

IMGSZ = 640
BATCH = 1

# The only domain TensorRT 8.5 can parse without a hand-written plugin. A node
# or an opset import from anywhere else (`com.microsoft`, `ai.onnx.contrib`, a
# custom exporter's own namespace) is a build failure on the robot with a
# message about an unsupported node, three steps removed from the cause.
STANDARD_DOMAINS = ("", "ai.onnx")


def fail(message: str) -> NoReturn:
    sys.stderr.write(f"REFUSED: {message}\n")
    raise SystemExit(2)


def export(weights: Path, out_dir: Path) -> Path:
    """Run the ultralytics export with the flags that matter. Returns the ONNX path."""
    try:
        from ultralytics import YOLO
    except ImportError:
        fail(
            "ultralytics is not installed. This script runs OFF-ROBOT:\n"
            "  uv run --with ultralytics --with onnx python "
            "apps/perception/tools/export_yolo11_onnx.py <weights.pt> <out_dir>"
        )

    model = YOLO(str(weights))

    # Read the names BEFORE the export, from the object we just loaded, so the
    # labels file cannot come from a different checkpoint than the graph.
    names = getattr(model, "names", None)
    if not names:
        fail(f"checkpoint {weights} carries no `names` — refusing to guess the classes")

    path = model.export(
        format="onnx",
        opset=EXPORT_OPSET,
        dynamic=False,
        simplify=False,
        nms=False,
        imgsz=IMGSZ,
        batch=BATCH,
    )

    out_dir.mkdir(parents=True, exist_ok=True)
    onnx_path = out_dir / (weights.stem + ".onnx")
    exported = Path(path)
    if exported.resolve() != onnx_path.resolve():
        shutil.move(str(exported), str(onnx_path))

    write_labels(names, out_dir / "labels.txt")
    return onnx_path


def write_labels(names, path: Path) -> None:
    """One class name per line, IN CLASS-INDEX ORDER.

    `model.names` is a dict keyed by int index in current ultralytics and a list
    in older ones. Sorting by the integer key is what makes line N of this file
    correspond to class index N, which is the entire contract detector.py's
    `label_for()` relies on. A dict that happened to iterate in insertion order
    would be right by luck, and wrong the day someone re-orders a data.yaml.
    """
    if isinstance(names, dict):
        ordered = [names[k] for k in sorted(names, key=int)]
    else:
        ordered = list(names)

    path.write_text("\n".join(str(n) for n in ordered) + "\n", encoding="utf-8")
    print(f"labels: {len(ordered)} classes -> {path}")


def verify(onnx_path: Path) -> None:
    """Refuse anything TensorRT 8.5 cannot parse, here, where it is cheap."""
    try:
        import onnx
    except ImportError:
        fail("onnx is not installed — the checks below are the point of this script")

    model = onnx.load(str(onnx_path))
    onnx.checker.check_model(model)

    # --- opset -------------------------------------------------------------
    for entry in model.opset_import:
        domain = entry.domain
        if domain not in STANDARD_DOMAINS:
            fail(
                f"opset import for domain {domain!r}: TensorRT 8.5 has no parser for it "
                "and would need a hand-written plugin"
            )
        if entry.version > TRT85_MAX_OPSET:
            fail(
                f"opset {entry.version} > {TRT85_MAX_OPSET}. TensorRT 8.5's parser tops "
                f"out at {TRT85_MAX_OPSET} (onnx-tensorrt release/8.5-GA, "
                f"docs/operators.md line 1). ultralytics defaults to 20; this script "
                f"exports {EXPORT_OPSET}."
            )

    # --- node domains ------------------------------------------------------
    foreign = sorted({n.domain for n in model.graph.node if n.domain not in STANDARD_DOMAINS})
    if foreign:
        joined = ", ".join(repr(d) for d in foreign)
        fail(f"graph contains nodes from non-standard domains {joined} — "
             "each needs a TRT plugin")

    # --- NMS must not be in the graph --------------------------------------
    # This one is not about whether TRT can parse it — it can, on 8.5 — but
    # about what comes out the other end. `nms=True` changes the OUTPUT SHAPE
    # from the raw head (1, 4 + num_classes, num_anchors) to (1, max_det, 6),
    # and detector.py's `decode()` reads the raw head. The two are both valid
    # float tensors of rank 3, so nothing raises: the decoder just reads garbage
    # confidences off the wrong axis and the robot sees nothing, or sees things
    # that are not there. That is the failure this whole file exists to make
    # impossible, so it is checked even though the export above hardcodes
    # nms=False — the file on disk is what gets shipped, not the flag we passed.
    nms_ops = sorted(
        {n.op_type for n in model.graph.node}
        & {"NonMaxSuppression", "NonZero", "GatherND", "ScatterND"}
    )
    if nms_ops:
        joined = ", ".join(nms_ops)
        fail(
            f"graph contains end-to-end NMS ops ({joined}) — exported with nms=True. "
            "Those need data-dependent output shapes, i.e. enqueueV3 and an "
            "IOutputAllocator instead of the fixed allocations detector.py uses, and "
            "they change the output shape from the raw head decode() expects. Re-export "
            "with nms=False; NMS stays on the CPU."
        )

    # --- static shapes -----------------------------------------------------
    # A symbolic dim means the engine needs an optimisation profile
    # (--minShapes/--optShapes/--maxShapes), and ../vision/entrypoint.sh passes
    # none. The failure would land 2-5 minutes into a build on the robot.
    for tensor in list(model.graph.input) + list(model.graph.output):
        dims = tensor.type.tensor_type.shape.dim
        symbolic = [d.dim_param for d in dims if d.dim_param]
        if symbolic:
            fail(
                f"{tensor.name!r} has symbolic dimension(s) {', '.join(symbolic)} — "
                "export with dynamic=False. TRT would need an optimisation profile "
                "that entrypoint.sh does not pass."
            )

    inputs = [(t.name, [d.dim_value for d in t.type.tensor_type.shape.dim])
              for t in model.graph.input]
    outputs = [(t.name, [d.dim_value for d in t.type.tensor_type.shape.dim])
               for t in model.graph.output]

    if len(inputs) != 1:
        fail(f"expected exactly one input, found {len(inputs)}: {inputs}")
    if list(inputs[0][1]) != [BATCH, 3, IMGSZ, IMGSZ]:
        fail(
            f"input shape {inputs[0][1]} is not [{BATCH}, 3, {IMGSZ}, {IMGSZ}]. "
            "detector.py sizes its host and device buffers from the engine's binding "
            "shape and letterboxes to it; a different shape is fine ONLY if "
            "C3PO_VISION_IMGSZ moves with it."
        )

    # --- the record --------------------------------------------------------
    # Printed, not asserted. Every op YOLO11 emits at opset 16 is marked
    # supported by the 8.5 parser with no plugin (Conv, Mul, Sigmoid, Constant,
    # Concat, Add, Reshape, Split, MaxPool, Transpose, MatMul, Softmax, Resize,
    # Div, Slice, Sub, Shape, Gather). If this list ever grows something
    # unfamiliar, that is the thing to look up before spending 5 minutes on an
    # engine build.
    ops = sorted({n.op_type for n in model.graph.node})
    opsets = ", ".join(f"{e.domain or 'ai.onnx'}@{e.version}" for e in model.opset_import)
    print(f"opsets:  {opsets}")
    print(f"input:   {inputs[0][0]} {inputs[0][1]}")
    print("outputs: " + ", ".join(f"{name} {shape}" for name, shape in outputs))
    print("ops:     " + ", ".join(ops))


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Export YOLO11 to a TensorRT-8.5-parsable ONNX, plus labels.txt.",
    )
    parser.add_argument("weights", type=Path, help="the .pt checkpoint (e.g. yolo11n.pt)")
    parser.add_argument("out_dir", type=Path, help="where to write <stem>.onnx and labels.txt")
    args = parser.parse_args(argv)

    if not args.weights.exists():
        fail(f"no such checkpoint: {args.weights}")

    onnx_path = export(args.weights, args.out_dir)
    verify(onnx_path)

    print(f"\nwrote {onnx_path}")
    print(f"next:  scp {onnx_path} {args.out_dir / 'labels.txt'} c3po:~/.c3po/models/")
    print("then:  ssh c3po 'c3po/scripts/robot/build_perception engine'")
    return 0


if __name__ == "__main__":
    sys.exit(main())
