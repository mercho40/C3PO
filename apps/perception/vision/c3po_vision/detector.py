"""RealSense -> TensorRT -> grounding -> DDS. The whole vision container, in one loop.

    rt/c3po/objects    std_msgs::msg::dds_::String_  (JSON)   ~10 Hz, written

That is the only thing this container puts on the DDS wire, and this is the only
file in it that touches a device. It also, when `C3PO_VISION_STREAM=1`, serves
the colour frame as MJPEG on loopback for the operator console — `stream.py`
owns that entirely and explains why the video has to come from this process:
`/dev/videoN` has one owner, and it is this one. `grounding.py` holds the maths, `ros_idl.py`
holds the wire type, and both of those import on a laptop; everything that
needs a camera, a GPU or a DDS stack is here, behind a lazy import.

WHAT THIS PUBLISHES, AND WHY EVERY TICK
---------------------------------------
    {"v": 1, "stamp_unix": 1755530000.25, "objects": [...], "objects_omitted": 0}

`objects` are ALREADY EGOCENTRIC — label, range_m, bearing_deg, confidence,
exactly `world_model.Observation`'s keyword arguments. D2.2 option 1: this
container owns the base_link<-camera extrinsic as a constant and resolves
range/bearing itself, so there is no TF here, none in the nav container for
detections, and none in the bridge.

THE PUBLISH IS A HEARTBEAT AND IT GOES OUT ON EVERY SUCCESSFUL TICK, INCLUDING
THE EMPTY ONES. This is "absent is not empty" (D7) crossing a process boundary.
The nav container computes `detector_online` from message ARRIVAL, never from a
list being non-empty — so an online detector that sees nothing publishes
`objects: []` and that means "I looked, the room is clear", which is a different
and useful fact from silence. Delete the empty publish and the two facts merge
into one, which is exactly how a robot walks into something nobody saw.

The converse is just as load-bearing and is the reason for the failure handling
at the bottom of `run()`: WE DO NOT PUBLISH ON A TICK THAT FAILED. A frame grab
that threw, an inference that threw — those are not an empty scene, and
publishing `objects: []` for them would be this module asserting a clear room it
never looked at. We fall silent instead, the nav container flips
`detector_online: false` after 1.5 s, and the model is told in plain language
that nothing is looking. Silence is the honest answer to "did not look"; it is
the wrong answer to "looked and saw nothing". That asymmetry is the whole
design.

SYNTHETIC MODE (`C3PO_VISION_FAKE=1`) — NO CAMERA, NO CUDA, NO NUMPY
--------------------------------------------------------------------
Stage 3 brings the crossing up end to end with no sensors at all. In synthetic
mode the frame source is a hand-built depth frame (a plain list of rows) and the
"detector" returns fixed boxes over it, so the REAL grounding path — clipping,
the inner-region median, the deprojection, the extrinsic, the bearing sign —
runs on every tick. A fake that skipped straight to a canned
`{"label": ..., "bearing_deg": ...}` would test the transport and nothing else,
and the bearing sign is the thing most worth testing.

It also alternates a genuinely empty tick in, on purpose: the empty heartbeat is
a distinct wire state and Stage 3 should see both.

(The nav container has its own, separate fake — `launch/fake.launch.py` drives
/c3po/objects with `ros2 topic pub` and does not need this container to exist.
This mode is for exercising THIS file without a robot: the DDS write, the loop,
the JSON. Both fakes state the same bearing convention and must keep agreeing.)

LAZY IMPORTS ARE A REQUIREMENT, NOT A STYLE CHOICE
--------------------------------------------------
`import c3po_vision.detector` must work on a machine with no pyrealsense2, no
TensorRT, no pycuda, no numpy and no CycloneDDS — that is what lets the Stage 0
suite import this file's constants and its pure helpers on a Mac. So every one
of those lives inside the function or the constructor that needs it, and
`c3po_vision/__init__.py` imports no submodule at all.

PYTHON 3.8. This file runs under the l4t-jetpack:r35.3.1 image's interpreter
(JetPack 5's python3-libnvinfer hard-depends `python3 (>= 3.8), python3
(<< 3.9)`). No walrus-in-comprehension games, no `dict | dict`, no `list[str]`
annotations without the __future__ import.
"""

from __future__ import annotations

import json
import os
import signal
import sys
import time
from typing import Any, Dict, List, Optional, Sequence, Tuple

from c3po_vision.grounding import (
    DEFAULT_CAMERA_EXTRINSIC,
    MAX_VALID_DEPTH_M,
    MIN_VALID_DEPTH_M,
    CameraExtrinsic,
    Intrinsics,
    ground_box,
    to_observation,
)
# Stdlib-only at module scope (Pillow and numpy are lazy inside it), so this
# import keeps the "importable on a Mac" property the docstring above claims.
from c3po_vision import stream as stream_mod

# --------------------------------------------------------------------------
# Wire contract
# --------------------------------------------------------------------------

# Bumped when the shape changes in a way the consumer would notice. The nav
# container REJECTS anything else loudly (`unsupported objects schema v=`)
# rather than half-parsing it, which is the only way a version number is worth
# having.
OBJECTS_SCHEMA_VERSION = 1

# ROS topic /c3po/objects. rmw_cyclonedds_cpp prefixes ROS topics with `rt/`,
# so this string is what a plain DDS writer must use to be seen by the rclpy
# subscription in world_model_publisher.py. A typo here does not raise — it
# delivers nothing, forever, and looks exactly like a camera that sees nothing.
OBJECTS_TOPIC = os.environ.get("C3PO_OBJECTS_TOPIC", "rt/c3po/objects")

DOMAIN_ID = int(os.environ.get("C3PO_DDS_DOMAIN", "42"))

# Matches MAX_OBJECTS_ON_WIRE in world_model_publisher.py. Truncating here as
# well as there is not redundant: whoever truncates must COUNT, and the counts
# are summed the whole way to the model (ours -> objects_omitted -> the
# bridge's extra_omitted). A model shown 8 of 40 obstacles with no indication
# the other 32 exist will reason confidently about a scene it cannot see.
MAX_OBJECTS_ON_WIRE = 32

# --------------------------------------------------------------------------
# Tuning. Every one of these is an env var because Stage 6 tunes them against a
# bag, on the robot, without a rebuild — an image rebuild is ~25 min here.
# --------------------------------------------------------------------------

TICK_HZ = float(os.environ.get("C3PO_VISION_HZ", "10"))

# Stage 7's fallback if memory or rates fail is "drop YOLO input 640->480 and
# the detector to 5 Hz before concluding the stack does not fit"
# (apps/perception/README.md). Both are env vars for exactly that reason.
INPUT_SIZE = int(os.environ.get("C3PO_VISION_IMGSZ", "640"))
COLOR_W = int(os.environ.get("C3PO_VISION_COLOR_W", "640"))
COLOR_H = int(os.environ.get("C3PO_VISION_COLOR_H", "480"))
COLOR_FPS = int(os.environ.get("C3PO_VISION_COLOR_FPS", "30"))

CONF_THRESHOLD = float(os.environ.get("C3PO_VISION_CONF", "0.35"))
NMS_IOU = float(os.environ.get("C3PO_VISION_IOU", "0.45"))

# Above this in base_link the thing is ceiling, signage or a light fitting, and
# the robot is 1.3 m tall. Reporting them costs tokens and invites the model to
# plan around an obstacle it will walk under.
MAX_HEIGHT_M = float(os.environ.get("C3PO_VISION_MAX_HEIGHT_M", "2.0"))

MIN_DEPTH_M = float(os.environ.get("C3PO_VISION_MIN_DEPTH_M", str(MIN_VALID_DEPTH_M)))
MAX_DEPTH_M = float(os.environ.get("C3PO_VISION_MAX_DEPTH_M", str(MAX_VALID_DEPTH_M)))

ENGINE_PATH = os.environ.get("C3PO_ENGINE", "/opt/c3po/engines/yolo11n.fp16.plan")

# The labels MUST come from the same checkpoint the ONNX was exported from —
# tools/export_yolo11_onnx.py writes this file out of `model.names` for exactly
# that reason. A hardcoded COCO list would make a fine-tuned model silently
# report "person" for a traffic cone, and nothing downstream could tell.
LABELS_PATH = os.environ.get("C3PO_LABELS", "/opt/c3po/models/labels.txt")

# Stop the container rather than loop forever on a wedged camera. `--restart no`
# (perception_up) means stopping is final and visible; the nav container's
# summary flips to detector offline, which is the correct thing for the model to
# be told. Silently retrying a dead D435i for an hour is not.
MAX_CONSECUTIVE_FAILURES = int(os.environ.get("C3PO_VISION_MAX_FAILURES", "20"))


def _env_flag(name: str, default: str = "0") -> bool:
    return os.environ.get(name, default).strip().lower() in ("1", "true", "yes", "on")


def camera_extrinsic_from_env() -> CameraExtrinsic:
    """The mount, overridable without a rebuild. UNMEASURED by default.

    grounding.DEFAULT_CAMERA_EXTRINSIC is a reasoned default and says so twice;
    Stage 6 owes this project a tape measure. Until then these four env vars are
    how a measurement gets applied at 2 a.m. without a 25-minute image build.
    """
    d = DEFAULT_CAMERA_EXTRINSIC
    return CameraExtrinsic(
        x_m=float(os.environ.get("C3PO_CAMERA_X_M", str(d.x_m))),
        y_m=float(os.environ.get("C3PO_CAMERA_Y_M", str(d.y_m))),
        z_m=float(os.environ.get("C3PO_CAMERA_Z_M", str(d.z_m))),
        pitch_deg=float(os.environ.get("C3PO_CAMERA_PITCH_DEG", str(d.pitch_deg))),
        yaw_deg=float(os.environ.get("C3PO_CAMERA_YAW_DEG", str(d.yaw_deg))),
    )


def log(event: str, **fields: Any) -> None:
    """One line per event on stderr, key=value.

    Not structlog: this container has four pip packages and adding a fifth to
    format log lines is not a trade worth making. Not stdout either — stdout is
    where a `--dry-run` operator wants the JSON and nothing else.
    """
    parts = [f"{k}={v}" for k, v in sorted(fields.items())]
    sys.stderr.write("[detector] {} {}\n".format(event, " ".join(parts)))
    sys.stderr.flush()


def load_labels(path: str) -> List[str]:
    """One class name per line, in the model's class-index order.

    Missing file is NOT fatal: an unlabelled detection still has a range and a
    bearing, and "obstacle at 1.2 m, 30 degrees left" is worth more to a planner
    than a crash. It is loud, though — a detector reporting `class_37` at every
    tick is a misconfiguration somebody has to see.
    """
    try:
        with open(path, "r") as fh:
            names = [line.strip() for line in fh if line.strip()]
        if not names:
            raise ValueError("labels file is empty")
        return names
    except Exception as exc:
        log("labels.missing", path=path, error=repr(exc),
            consequence="objects will be reported as class_<index>")
        return []


def label_for(labels: Sequence[str], index: int) -> str:
    if 0 <= index < len(labels):
        return labels[index]
    return f"class_{index}"


# --------------------------------------------------------------------------
# Frame sources
# --------------------------------------------------------------------------


class RealSenseSource:
    """The D435i, colour + depth ALIGNED TO COLOUR.

    The alignment is what lets grounding.py index one (u, v) into both images.
    Doing it the other way round (colour aligned to depth) would mean the boxes
    and the depth no longer share a pixel grid, and the error it produces is a
    plausible-looking range on the wrong object.

    `pyrealsense2` here is the stock PyPI V4L2 build (see ../Dockerfile), which
    is why this needs /dev/video*, /run/udev and both cgroup rules rather than
    a udev rules file installed on a shared robot.
    """

    def __init__(self) -> None:
        self._rs: Any = None
        self._pipeline: Any = None
        self._align: Any = None
        self.intrinsics: Optional[Intrinsics] = None
        self.depth_scale = 0.001

    def start(self) -> None:
        import pyrealsense2 as rs  # lazy: see the module docstring

        self._rs = rs
        config = rs.config()
        config.enable_stream(rs.stream.color, COLOR_W, COLOR_H, rs.format.bgr8, COLOR_FPS)
        config.enable_stream(rs.stream.depth, COLOR_W, COLOR_H, rs.format.z16, COLOR_FPS)

        self._pipeline = rs.pipeline()
        profile = self._pipeline.start(config)
        self._align = rs.align(rs.stream.color)

        # Queried, never assumed. The nominal 0.001 is right on most D435i units
        # and wrong on some; assuming it is a metre/millimetre error that reads
        # as "everything is 1000x too close" and would fail closed into a robot
        # that thinks the room is full.
        self.depth_scale = float(profile.get_device().first_depth_sensor().get_depth_scale())

        intr = profile.get_stream(rs.stream.color).as_video_stream_profile().intrinsics
        self.intrinsics = Intrinsics(
            fx=float(intr.fx), fy=float(intr.fy),
            cx=float(intr.ppx), cy=float(intr.ppy),
            width=int(intr.width), height=int(intr.height),
        )
        log("camera.ready", w=intr.width, h=intr.height, fx=round(intr.fx, 1),
            fy=round(intr.fy, 1), ppx=round(intr.ppx, 1), ppy=round(intr.ppy, 1),
            depth_scale=self.depth_scale)

    def read(self) -> Tuple[Any, Any]:
        """(colour BGR HxWx3 uint8, depth HxW uint16). Raises on timeout.

        Raising is deliberate — see `run()`. A timed-out frame is "did not
        look", and this module's contract is that "did not look" is silence,
        never an empty object list.
        """
        frames = self._align.process(self._pipeline.wait_for_frames())
        color = frames.get_color_frame()
        depth = frames.get_depth_frame()
        if not color or not depth:
            raise RuntimeError("incomplete frameset from the D435i")

        import numpy as np

        return np.asanyarray(color.get_data()), np.asanyarray(depth.get_data())

    def close(self) -> None:
        if self._pipeline is not None:
            try:
                self._pipeline.stop()
            except Exception:
                pass


class SyntheticSource:
    """A depth frame built by hand. No camera, no numpy, no CUDA.

    The frame is built ONCE and reused: at 640x480 a fresh list-of-lists per
    tick is 300k allocations for no information gain, and grounding only ever
    reads the pixels inside a box anyway.

    The scene deliberately mirrors launch/fake.launch.py's FAKE_OBJECTS so the
    two fakes tell the same story — a `person` slightly to the RIGHT and a
    `chair` well to the LEFT — which is what makes "chair has a POSITIVE
    bearing" checkable by eye in either one.
    """

    # (label, x0, y0, x1, y1, depth_mm, confidence). Boxes are in the COLOUR
    # image, so the left-of-centre box is the one that must come out positive.
    SCENE = (
        ("person", 340, 180, 420, 400, 2400, 0.88),
        ("chair", 90, 250, 190, 400, 1600, 0.71),
    )

    def __init__(self) -> None:
        self.intrinsics = Intrinsics(
            fx=600.0, fy=600.0, cx=COLOR_W / 2.0, cy=COLOR_H / 2.0,
            width=COLOR_W, height=COLOR_H,
        )
        self.depth_scale = 0.001
        self._frame: Optional[List[List[int]]] = None
        self._tick = 0

    def start(self) -> None:
        rows = [[0] * COLOR_W for _ in range(COLOR_H)]
        for _label, x0, y0, x1, y1, mm, _conf in self.SCENE:
            for v in range(max(0, y0), min(COLOR_H, y1)):
                row = rows[v]
                for u in range(max(0, x0), min(COLOR_W, x1)):
                    row[u] = mm
        self._frame = rows
        log("synthetic.ready", objects=len(self.SCENE), w=COLOR_W, h=COLOR_H,
            note="no camera, no CUDA — Stage 3")

    def read(self) -> Tuple[Any, Any]:
        self._tick += 1
        return None, self._frame

    def boxes(self) -> List[Tuple[int, float, Tuple[float, float, float, float]]]:
        """Detections for this tick, as (class_index, confidence, box).

        Every fourth tick is EMPTY on purpose. `objects: []` with the heartbeat
        still going out is a distinct, meaningful wire state — "I looked, the
        room is clear" — and a fake that never produces it lets the empty path
        ship untested.
        """
        if self._tick % 4 == 0:
            return []
        out = []
        for i, (_label, x0, y0, x1, y1, _mm, conf) in enumerate(self.SCENE):
            out.append((i, conf, (float(x0), float(y0), float(x1), float(y1))))
        return out

    def labels(self) -> List[str]:
        return [entry[0] for entry in self.SCENE]

    def close(self) -> None:
        self._frame = None


# --------------------------------------------------------------------------
# TensorRT
# --------------------------------------------------------------------------


class TensorRTDetector:
    """YOLO11 on a prebuilt TRT plan. NMS on the CPU, on purpose.

    The plan is built by ../entrypoint.sh at first container start, because a
    plan is tied to the exact GPU, TRT version and build flags and therefore
    cannot be baked into the image (which is also why the image builds with no
    GPU and we never touch `default-runtime` on a daemon shared with the gemm
    team).

    NMS STAYS HERE, IN PYTHON. `nms=True` at export parses on TRT 8.5 but
    injects NonZero/GatherND/ScatterND/NonMaxSuppression — all added in 8.5 GA
    itself — plus data-dependent output shapes that need enqueueV3 and an
    IOutputAllocator, to buy about 1 ms against a ~5-8 ms inference. That is a
    large amount of fragile surface for a rounding error.
    """

    def __init__(self, engine_path: str = ENGINE_PATH) -> None:
        # All four imports are lazy. `pycuda.autoinit` is what creates the CUDA
        # context, and importing it at module scope would make this file
        # unimportable on any machine without a GPU.
        import numpy as np
        import pycuda.autoinit  # noqa: F401  (import side effect: CUDA context)
        import pycuda.driver as cuda
        import tensorrt as trt

        self._np = np
        self._cuda = cuda

        logger = trt.Logger(trt.Logger.WARNING)
        with open(engine_path, "rb") as fh, trt.Runtime(logger) as runtime:
            self._engine = runtime.deserialize_cuda_engine(fh.read())
        if self._engine is None:
            # The classic symptom of a container started WITHOUT `--runtime
            # nvidia`: the in-image TensorRT loads fine and then returns NULL
            # here, which reads like a TRT bug and is not one.
            raise RuntimeError(
                f"deserialize_cuda_engine returned None for {engine_path} — is the "
                "container running with `--runtime nvidia`?"
            )
        self._context = self._engine.create_execution_context()

        # Static shapes throughout: the export is dynamic=False, batch=1, which
        # is what lets this use execute_v2 and a single pair of fixed device
        # allocations rather than the enqueueV3 machinery.
        self._bindings: List[int] = []
        self._host: Dict[int, Any] = {}
        self._device: Dict[int, Any] = {}
        self._input_index: Optional[int] = None
        self._output_index: Optional[int] = None

        for i in range(self._engine.num_bindings):
            shape = tuple(self._engine.get_binding_shape(i))
            dtype = trt.nptype(self._engine.get_binding_dtype(i))
            host = np.empty(shape, dtype=dtype)
            device = cuda.mem_alloc(host.nbytes)
            self._host[i] = host
            self._device[i] = device
            self._bindings.append(int(device))
            if self._engine.binding_is_input(i):
                self._input_index = i
                self._input_shape = shape
            else:
                self._output_index = i
                self._output_shape = shape

        if self._input_index is None or self._output_index is None:
            raise RuntimeError(f"engine {engine_path} has no input or no output binding")

        self._stream = cuda.Stream()
        log("engine.ready", path=engine_path, input=self._input_shape,
            output=self._output_shape, trt=trt.__version__)

    # -- preprocessing ------------------------------------------------------

    def letterbox(self, image: Any) -> Tuple[Any, float, int, int]:
        """BGR HxWx3 uint8 -> NCHW float32 in [0, 1], plus the inverse mapping.

        Returns (blob, scale, pad_x, pad_y) where a model-space coordinate maps
        back to image space as `(m - pad) / scale`.

        THERE IS NO OpenCV IN THIS IMAGE and there is not going to be — the
        Dockerfile installs four pip packages and cv2 would drag in a large
        stack for one resize. So:
          * the common case is configured away. The colour stream is 640x480 and
            the network input is 640x640, so this is pure PADDING, scale 1.0,
            and not one pixel is resampled.
          * anything else falls back to a numpy nearest-neighbour resize. That
            is lower quality than the bilinear letterbox ultralytics trains
            with, which is a reason to keep the stream at the network's width,
            not a reason to add a dependency.

        Padding value 114 is ultralytics' own, and matching it matters: the
        network has seen that grey at the edges of every letterboxed training
        image.
        """
        np = self._np
        _batch, _channels, net_h, net_w = self._input_shape
        h, w = image.shape[0], image.shape[1]

        scale = min(net_w / float(w), net_h / float(h))
        new_w, new_h = round(w * scale), round(h * scale)

        if (new_w, new_h) != (w, h):
            ys = (np.arange(new_h) / scale).astype(np.int32).clip(0, h - 1)
            xs = (np.arange(new_w) / scale).astype(np.int32).clip(0, w - 1)
            resized = image[ys][:, xs]
        else:
            resized = image

        canvas = np.full((net_h, net_w, 3), 114, dtype=np.uint8)
        pad_x = (net_w - new_w) // 2
        pad_y = (net_h - new_h) // 2
        canvas[pad_y:pad_y + new_h, pad_x:pad_x + new_w] = resized

        # BGR -> RGB, HWC -> CHW, uint8 -> float32/255. Getting the channel
        # order wrong does not crash; it costs accuracy in a way that looks like
        # a bad model.
        blob = canvas[:, :, ::-1].transpose(2, 0, 1).astype(np.float32) / 255.0
        return np.ascontiguousarray(blob[None, ...]), scale, pad_x, pad_y

    # -- inference ----------------------------------------------------------

    def infer(self, image: Any) -> List[Tuple[int, float, Tuple[float, float, float, float]]]:
        """One colour frame -> [(class_index, confidence, (x0, y0, x1, y1))].

        Boxes come back in COLOUR-IMAGE pixels, which is the frame grounding.py
        expects and the frame the aligned depth shares.
        """
        np = self._np
        cuda = self._cuda

        blob, scale, pad_x, pad_y = self.letterbox(image)
        np.copyto(self._host[self._input_index], blob)

        cuda.memcpy_htod_async(
            self._device[self._input_index], self._host[self._input_index], self._stream
        )
        self._context.execute_async_v2(
            bindings=self._bindings, stream_handle=self._stream.handle
        )
        cuda.memcpy_dtoh_async(
            self._host[self._output_index], self._device[self._output_index], self._stream
        )
        self._stream.synchronize()

        return self.decode(
            self._host[self._output_index], scale, pad_x, pad_y,
            width=image.shape[1], height=image.shape[0],
        )

    # -- postprocessing -----------------------------------------------------

    def decode(
        self,
        output: Any,
        scale: float,
        pad_x: int,
        pad_y: int,
        *,
        width: int,
        height: int,
    ) -> List[Tuple[int, float, Tuple[float, float, float, float]]]:
        """YOLO11's raw head -> boxes in colour-image pixels, after class-wise NMS.

        The head is (1, 4 + num_classes, num_anchors) — cx, cy, w, h in NETWORK
        pixels, then per-class scores with the sigmoid already applied. There is
        NO objectness channel (that went away with v8); multiplying by one, as
        every v5-era snippet does, would silently halve every confidence.

        The transposed (1, num_anchors, 4 + num_classes) layout is accepted too,
        because which one you get depends on the exporter version and the
        failure mode of guessing wrong is a detector that finds nothing at all
        rather than an error.
        """
        np = self._np
        pred = np.asarray(output)
        if pred.ndim == 3:
            pred = pred[0]
        if pred.shape[0] < pred.shape[1]:
            pred = pred.T            # (anchors, 4 + nc)

        if pred.shape[1] < 5:
            return []

        boxes_xywh = pred[:, :4]
        scores_all = pred[:, 4:]
        class_ids = scores_all.argmax(axis=1)
        scores = scores_all[np.arange(scores_all.shape[0]), class_ids]

        keep = scores >= CONF_THRESHOLD
        if not keep.any():
            return []
        boxes_xywh = boxes_xywh[keep]
        scores = scores[keep]
        class_ids = class_ids[keep]

        # Network pixels -> colour-image pixels: undo the pad, then the scale.
        cx, cy = boxes_xywh[:, 0], boxes_xywh[:, 1]
        bw, bh = boxes_xywh[:, 2], boxes_xywh[:, 3]
        x0 = (cx - bw / 2.0 - pad_x) / scale
        y0 = (cy - bh / 2.0 - pad_y) / scale
        x1 = (cx + bw / 2.0 - pad_x) / scale
        y1 = (cy + bh / 2.0 - pad_y) / scale
        x0 = x0.clip(0, width - 1)
        y0 = y0.clip(0, height - 1)
        x1 = x1.clip(0, width - 1)
        y1 = y1.clip(0, height - 1)

        out: List[Tuple[int, float, Tuple[float, float, float, float]]] = []
        for cls in np.unique(class_ids):
            idx = np.where(class_ids == cls)[0]
            kept = self._nms(x0[idx], y0[idx], x1[idx], y1[idx], scores[idx], NMS_IOU)
            for k in kept:
                j = idx[k]
                out.append(
                    (int(cls), float(scores[j]),
                     (float(x0[j]), float(y0[j]), float(x1[j]), float(y1[j])))
                )
        out.sort(key=lambda d: d[1], reverse=True)
        return out

    def _nms(self, x0: Any, y0: Any, x1: Any, y1: Any, scores: Any, iou: float) -> List[int]:
        """Greedy IoU suppression within one class. Plain numpy, ~microseconds.

        Class-wise, not global: a chair overlapping a person is two objects, and
        a global NMS would delete one of them.
        """
        np = self._np
        areas = (x1 - x0 + 1.0) * (y1 - y0 + 1.0)
        order = scores.argsort()[::-1]
        keep: List[int] = []
        while order.size > 0:
            i = int(order[0])
            keep.append(i)
            if order.size == 1:
                break
            rest = order[1:]
            xx0 = np.maximum(x0[i], x0[rest])
            yy0 = np.maximum(y0[i], y0[rest])
            xx1 = np.minimum(x1[i], x1[rest])
            yy1 = np.minimum(y1[i], y1[rest])
            inter = np.maximum(0.0, xx1 - xx0 + 1.0) * np.maximum(0.0, yy1 - yy0 + 1.0)
            overlap = inter / (areas[i] + areas[rest] - inter)
            order = rest[overlap <= iou]
        return keep


# --------------------------------------------------------------------------
# DDS
# --------------------------------------------------------------------------

# Identical in effect to apps/perception/config/cyclonedds-domain42.xml, which
# CYCLONEDDS_URI also points this container at. It is inlined anyway, and passed
# explicitly to `Domain(...)`, for the same reason the bridge's perception_link
# does it: a bare `DomainParticipant(42)` inherits whatever config the
# environment happens to carry, and on `lo` — which has NO MULTICAST flag on this
# Jetson — the wrong config produces a participant that starts cleanly and
# discovers nothing. There is no error for that. Passing the config makes this
# process correct regardless of how the container was launched.
#
# <MaxAutoParticipantIndex> is here because it is part of the DISCOVERY
# handshake, not a resource limit: a unicast <Peer> with no port is expanded
# into one SPDP locator per participant index 0..N-1, and N is also the range
# this process searches for its own free index. The default is 9, and both
# containers run --network host, so their ~13 domain-42 processes share one port
# space with the bridge. It must equal the value in the shared XML (32) on every
# side, or one side probes a subset of the other's ports and silently sees less.
_DOMAIN_XML = """<?xml version="1.0" encoding="UTF-8"?>
<CycloneDDS xmlns="https://cdds.io/config"><Domain id="42">
  <General><Interfaces><NetworkInterface name="lo" priority="default" multicast="false"/></Interfaces>
    <AllowMulticast>false</AllowMulticast></General>
  <Discovery><ParticipantIndex>auto</ParticipantIndex>
    <MaxAutoParticipantIndex>32</MaxAutoParticipantIndex>
    <Peers><Peer address="127.0.0.1"/></Peers></Discovery>
</Domain></CycloneDDS>"""


def _domain_xml(domain_id: int) -> str:
    """The XML above, with `<Domain id>` matching the domain we create.

    `Domain(id, cfg)` ignores any `<Domain>` block whose id does not match, and
    ignores it SILENTLY — the participant comes up on defaults (multicast on,
    interface autodetermine) and discovers nothing on `lo`. So if anyone
    overrides C3PO_DDS_DOMAIN, the id in the config has to move with it.
    """
    if domain_id == 42:
        return _DOMAIN_XML
    return _DOMAIN_XML.replace('<Domain id="42">', f'<Domain id="{domain_id}">', 1)


class ObjectsPublisher:
    """The one writer. Domain, participant, topic and writer held for process life.

    cyclonedds-python's Entity.__del__ calls dds_delete, so dropping the Domain
    reference silently tears down the domain and every entity under it — writes
    then succeed and deliver nothing, with no error anywhere. This is the single
    most likely way to build this, have it work in a REPL, and have it publish
    into the void as a daemon. Hence: attributes on a long-lived object, never
    locals in a setup function.

    QOS IS RELIABLE, AND THAT IS NOT A DEFAULT. cyclonedds-python's `qos=None`
    is BEST_EFFORT, while the consumer is an rclpy `create_subscription(...,
    10)` whose default is RELIABLE. Offered BEST_EFFORT against requested
    RELIABLE does NOT match — no exception, no warning, no data, forever. The
    bridge's reader gets away with `qos=None` because it is the REQUESTING side
    and best-effort accepts a reliable offer; we are the offering side, so we
    have to say it.
    """

    def __init__(self, domain_id: int = DOMAIN_ID, topic: str = OBJECTS_TOPIC) -> None:
        self._domain_id = domain_id
        self._topic_name = topic
        self._domain: Any = None
        self._participant: Any = None
        self._topic: Any = None
        self._writer: Any = None
        self._String: Any = None

    def start(self) -> None:
        from cyclonedds.core import Policy, Qos
        from cyclonedds.domain import Domain, DomainParticipant
        from cyclonedds.pub import DataWriter
        from cyclonedds.topic import Topic
        from cyclonedds.util import duration

        from c3po_vision.ros_idl import String_

        self._String = String_
        self._domain = Domain(self._domain_id, _domain_xml(self._domain_id))
        self._participant = DomainParticipant(self._domain_id)
        self._topic = Topic(self._participant, self._topic_name, String_)
        self._writer = DataWriter(
            self._participant,
            self._topic,
            qos=Qos(
                Policy.Reliability.Reliable(duration(seconds=1)),
                Policy.History.KeepLast(10),
                Policy.Durability.Volatile,
            ),
        )
        log("dds.ready", domain=self._domain_id, topic=self._topic_name, qos="reliable")

    def publish(self, payload: Dict[str, Any]) -> None:
        self._writer.write(self._String(data=json.dumps(payload, separators=(",", ":"))))


class StdoutPublisher:
    """`C3PO_VISION_DRY_RUN=1`: the same payloads, on stdout, with no DDS at all.

    Exists so the loop, the grounding and the JSON can be run on a machine that
    has no CycloneDDS C library — a Mac. It is not a test double for the wire;
    the wire is tested by Stage 3 against a real container.
    """

    def start(self) -> None:
        log("dds.skipped", reason="C3PO_VISION_DRY_RUN=1", output="stdout")

    def publish(self, payload: Dict[str, Any]) -> None:
        sys.stdout.write(json.dumps(payload, separators=(",", ":")) + "\n")
        sys.stdout.flush()


# --------------------------------------------------------------------------
# The report
# --------------------------------------------------------------------------


def build_report(objects: List[Dict[str, Any]], omitted: int, stamp: float) -> Dict[str, Any]:
    """The published payload. Pure, so its shape is checkable without a camera.

    `objects` are already `Observation.to_dict()`-shaped (grounding.to_observation
    made them). `objects_omitted` is what WE dropped for the wire cap; the nav
    container adds its own truncation to it and the bridge adds a third, and all
    three are summed — a count that is silently zero is worse than no count.
    """
    return {
        "v": OBJECTS_SCHEMA_VERSION,
        "stamp_unix": round(stamp, 3),
        "objects": objects,
        "objects_omitted": int(omitted),
    }


def ground_all(
    detections: Sequence[Tuple[int, float, Tuple[float, float, float, float]]],
    depth: Any,
    intrinsics: Intrinsics,
    extrinsic: CameraExtrinsic,
    labels: Sequence[str],
    depth_scale: float,
) -> Tuple[List[Dict[str, Any]], int]:
    """2-D detections -> wire objects. Returns (objects, omitted).

    Ordering is by range, NEAREST FIRST, before the cap: if anything has to be
    dropped it must be the far things. A cap that dropped the nearest obstacle
    would be worse than no cap.

    A detection whose depth cannot be resolved is dropped, NOT reported at range
    0 — that would be an obstacle materialising inside the robot. Dropping one
    detection is not the same as an empty scene, and the tick still publishes.
    """
    grounded: List[Tuple[float, Dict[str, Any]]] = []
    for class_id, confidence, box in detections:
        g = ground_box(
            box, depth, intrinsics,
            extr=extrinsic,
            depth_scale=depth_scale,
            min_depth_m=MIN_DEPTH_M,
            max_depth_m=MAX_DEPTH_M,
        )
        if g is None:
            continue
        if g.z_m > MAX_HEIGHT_M:
            continue
        grounded.append((g.range_m, to_observation(label_for(labels, class_id), g, confidence)))

    grounded.sort(key=lambda pair: pair[0])
    objects = [obj for _rng, obj in grounded[:MAX_OBJECTS_ON_WIRE]]
    return objects, max(0, len(grounded) - MAX_OBJECTS_ON_WIRE)


# --------------------------------------------------------------------------
# The loop
# --------------------------------------------------------------------------


class _Stopper:
    """SIGTERM/SIGINT -> a clean exit, so `docker stop` is not a 10 s kill.

    It also matters for DDS: a participant that is torn down properly withdraws
    from discovery, where a SIGKILLed one leaves the reader waiting out its
    liveliness lease.
    """

    def __init__(self) -> None:
        self.stop = False
        signal.signal(signal.SIGTERM, self._handle)
        signal.signal(signal.SIGINT, self._handle)

    def _handle(self, signum: int, _frame: Any) -> None:
        log("signal", signum=signum)
        self.stop = True


def run() -> int:
    fake = _env_flag("C3PO_VISION_FAKE")
    dry_run = _env_flag("C3PO_VISION_DRY_RUN")
    extrinsic = camera_extrinsic_from_env()

    log("start", fake=fake, dry_run=dry_run, hz=TICK_HZ, domain=DOMAIN_ID,
        topic=OBJECTS_TOPIC, conf=CONF_THRESHOLD,
        extrinsic=(
            f"x={extrinsic.x_m:.3f} y={extrinsic.y_m:.3f} z={extrinsic.z_m:.3f} "
            f"pitch={extrinsic.pitch_deg:.1f} yaw={extrinsic.yaw_deg:.1f}"
        ),
        extrinsic_status="UNMEASURED — Stage 6 owes this a tape measure")

    source = SyntheticSource() if fake else RealSenseSource()
    source.start()

    detector = None
    if fake:
        labels = source.labels()
    else:
        detector = TensorRTDetector()
        labels = load_labels(LABELS_PATH)

    publisher = StdoutPublisher() if dry_run else ObjectsPublisher()
    publisher.start()

    # Off unless asked for. The video is a convenience for a human watching;
    # `perception_up` turns it on for the stages that claim the camera, and a
    # bind failure leaves `video` None rather than taking the detector with it.
    video = stream_mod.from_env(os.environ.get)
    if video is not None:
        video.start()

    stopper = _Stopper()
    period = 1.0 / TICK_HZ if TICK_HZ > 0 else 0.1
    failures = 0
    ticks = 0
    next_tick = time.time()

    while not stopper.stop:
        now = time.time()
        if now < next_tick:
            time.sleep(min(period, next_tick - now))
            continue
        next_tick = now + period

        try:
            color, depth = source.read()
            if fake:
                detections = source.boxes()
            else:
                detections = detector.infer(color)
            # AFTER the frame grab and the inference both succeeded, and inside
            # the try for a reason: a tick that threw did not look at anything,
            # and feeding the viewer its last good frame would be this module
            # showing a picture of a room it is no longer watching. The frame
            # goes stale instead and `/status` says so — same rule as the
            # publish below, applied to pixels.
            if video is not None:
                video.offer(
                    stream_mod.test_pattern(COLOR_W, COLOR_H, ticks) if fake else color
                )
            objects, omitted = ground_all(
                detections, depth, source.intrinsics, extrinsic, labels, source.depth_scale
            )
        except Exception as exc:
            # NO PUBLISH ON A FAILED TICK. See the module docstring: an empty
            # object list means "I looked and the room is clear", and this
            # branch is precisely the case where we did not look. Silence is
            # the honest answer, and the nav container turns it into
            # `detector_online: false` plus a plain-language note within 1.5 s.
            failures += 1
            log("tick.failed", error=repr(exc), consecutive=failures,
                published="no — silence, not an empty scene")
            if failures >= MAX_CONSECUTIVE_FAILURES:
                log("giving.up", consecutive=failures,
                    consequence="container exits; world model reports the detector offline")
                source.close()
                if video is not None:
                    video.close()
                return 1
            continue

        failures = 0
        # THE HEARTBEAT. Unconditional, including when `objects` is empty.
        publisher.publish(build_report(objects, omitted, time.time()))

        ticks += 1
        if ticks % int(max(1.0, TICK_HZ) * 30) == 0:
            log("alive", ticks=ticks, last_objects=len(objects), omitted=omitted)

    source.close()
    if video is not None:
        video.close()
    log("stopped", ticks=ticks)
    return 0


def main() -> int:
    try:
        return run()
    except KeyboardInterrupt:
        return 0


if __name__ == "__main__":
    sys.exit(main())
