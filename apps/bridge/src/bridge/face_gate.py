"""The operator face gate: chosen skills run only with the enrolled operator's live face in view.

AN EXTRA GATE, NEVER A REPLACEMENT. `mcp_server.GovernedMCP.call_tool` runs it only after
`safety.tool_refusal` let the call through (PARAR, battery, person rule, the operator's
one-shot confirmation), and that method is what both transports dispatch through, so
apps/back's /invoke, its agent and Claude Code all meet it. Off unless `C3PO_FACE_GATE`
names skills; the stop and release skills are never gated, whatever it says.

FAILS CLOSED. No enrollment, no models, no vision container, a stale frame, depth from
another moment, any exception, or no answer within TIMEOUT_S: refused. The work runs in a
thread, so a gated check never holds the event loop a stop is dispatched on.

LIVENESS FROM DEPTH. The vision container's depth is aligned to the colour frame the face
is found in, and `GET /depth_box` answers for that frame's stamp only. A live face is
FACE_WIDTH_M wide at its measured depth (a face on a phone is a few cm) and has relief
(paper is flat), within MAX_M. That stops a phone and a flat print; a mask or a curved
life-size print is beyond it. It sits on top of a login, not instead of one.

PRIVACY. Only SFace feature vectors are stored, at FACE_DIR/operator.npy (0600, directory
0700), written by scripts/face_enroll.py on the robot. No image is kept, nothing leaves the
robot, and neither faces nor features are logged.
"""

from __future__ import annotations

import asyncio
import json
import os
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any

import structlog

from bridge.safety import _env_float

log = structlog.get_logger(__name__)

#: Never gated, even if named: a stop or a release must never wait on a camera.
NEVER_GATED = frozenset(
    {
        "stop_everything",
        "soft_stop",
        "damp",
        "release_arm",
        "release_arm_control",
        "open_hands",
        "cancel_task",
        "cancel_navigation",
        "disarm_navigation",
    }
)

NOT_SEEN = "operator_face_not_seen"
NOT_LIVE = "operator_face_not_live"
NOT_ENROLLED = "operator_face_not_enrolled"
UNAVAILABLE = "operator_face_check_unavailable"


def _parse(raw: str) -> frozenset[str]:
    names = {n.strip() for n in raw.split(",") if n.strip()}
    if names & NEVER_GATED:
        log.warning("face_gate.never_gated_ignored", tools=sorted(names & NEVER_GATED))
    return frozenset(names - NEVER_GATED)


GATED = _parse(os.environ.get("C3PO_FACE_GATE", ""))
MAX_M = _env_float("C3PO_FACE_GATE_MAX_M", 1.5)
FACE_DIR = Path(os.environ.get("C3PO_FACE_DIR") or Path.home() / ".c3po" / "face")
YUNET = "face_detection_yunet_2023mar.onnx"
SFACE = "face_recognition_sface_2021dec.onnx"

#: OpenCV's SFace threshold for "same person" by cosine similarity (opencv_zoo
#: models/face_recognition_sface, and samples/dnn/face_detect.py). Higher is stricter.
MATCH_COSINE = 0.363
#: YuNet's own default score floor.
DETECT_SCORE = 0.9
#: Physical face width, from the box's pixels and its depth. A phone shows a few cm.
FACE_WIDTH_M = (0.11, 0.20)
#: p90 - p10 of depth over the inner face. Paper is flat; a nose is not.
MIN_RELIEF_M = 0.01
MIN_VALID_FRAC = 0.5
#: Same window as the vision container's own `live` flag (stream.STALE_AFTER_S).
FRAME_STALE_S = 1.0
TIMEOUT_S = 1.5
HTTP_TIMEOUT_S = 1.0
#: Largest faces looked at per check, so a crowd cannot stretch it past TIMEOUT_S.
MAX_FACES = 3

_HINTS = {
    NOT_SEEN: "Ask the operator to face the head camera, within {max_m} m, and retry once.",
    NOT_LIVE: "Ask the operator to face the camera in person (no photo, no screen), within {max_m} m.",
    NOT_ENROLLED: "The operator enrolls on the robot with apps/bridge/scripts/face_enroll.py; tell them.",
    UNAVAILABLE: "Tell the operator the face check could not run; do not retry in a loop.",
}

_lock = threading.RLock()
_models: tuple[Any, Any] | None = None


class Unavailable(Exception):
    """The check could not be made at all."""


def enrollment_path() -> Path:
    return FACE_DIR / "operator.npy"


def save_enrollment(features: Any) -> Path:
    """Write the operator's feature vectors, owner-only, atomically. Never images."""
    import numpy as np

    FACE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(FACE_DIR, 0o700)
    path, tmp = enrollment_path(), FACE_DIR / ".operator.npy.tmp"
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        np.save(f, np.asarray(features, dtype=np.float32))
    os.chmod(tmp, 0o600)
    os.replace(tmp, path)
    return path


def enrolled() -> Any:
    """The enrolled features (N x 128), or None when there is no trustworthy enrollment."""
    import numpy as np

    path = enrollment_path()
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        return None
    if mode & 0o077:
        # Writable or readable by others: someone else's face could be in it.
        log.warning("face_gate.enrollment_ignored", reason="not_owner_only", mode=oct(mode & 0o777))
        return None
    features = np.load(path, allow_pickle=False)
    if features.ndim != 2 or not features.shape[0] or features.shape[1] != 128:
        raise Unavailable("the enrollment file is malformed; re-enroll")
    return features


def models() -> tuple[Any, Any]:
    """(YuNet detector, SFace recognizer), loaded once."""
    global _models
    with _lock:
        if _models is None:
            import cv2

            paths = [FACE_DIR / "models" / name for name in (YUNET, SFACE)]
            missing = [p.name for p in paths if not p.is_file()]
            if missing:
                raise Unavailable(
                    f"missing {', '.join(missing)}: run scripts/install_face_models.sh"
                )
            _models = (
                cv2.FaceDetectorYN.create(str(paths[0]), "", (320, 320), DETECT_SCORE),
                cv2.FaceRecognizerSF.create(str(paths[1]), ""),
            )
        return _models


def warm() -> None:
    """Load the models ahead of the first gated call; a failure is logged, and that call refuses."""
    try:
        models()
    except Exception as exc:  # noqa: BLE001 - logged here, refused at call time
        log.error("face_gate.models_unavailable", error=str(exc))


def _get(path: str) -> tuple[bytes, Any]:
    from bridge.sdk import camera_relay

    with urllib.request.urlopen(camera_relay.vision_url(path), timeout=HTTP_TIMEOUT_S) as resp:
        return resp.read(), resp.headers


def frame() -> tuple[Any, float]:
    """The vision container's newest colour frame (BGR) and its stamp."""
    import cv2
    import numpy as np

    body, headers = _get("frame.jpg")
    if headers.get("X-Frame-Stamp") is None:
        raise Unavailable(
            "the vision container's /frame.jpg carries no stamp (too old a container)"
        )
    img = cv2.imdecode(np.frombuffer(body, np.uint8), cv2.IMREAD_COLOR)
    if img is None:
        raise Unavailable("the camera frame did not decode")
    return img, float(headers["X-Frame-Stamp"])


def fresh_frame() -> tuple[Any, float]:
    img, stamp = frame()
    age = time.time() - stamp
    if age > FRAME_STALE_S:
        raise Unavailable(f"the newest camera frame is {age:.1f} s old")
    return img, stamp


def depth_box(stamp: float, box: tuple[float, float, float, float]) -> dict:
    """The vision container's depth stats inside `box` of the frame with this stamp."""
    query = dict(zip(("x0", "y0", "x1", "y1"), (f"{v:.1f}" for v in box)), stamp=repr(stamp))
    body, _ = _get("depth_box?" + urllib.parse.urlencode(query))
    return dict(json.loads(body))


def faces(img: Any) -> list[Any]:
    """YuNet rows (x, y, w, h, five landmarks, score), largest first, at most MAX_FACES."""
    detector, _ = models()
    with _lock:
        detector.setInputSize((img.shape[1], img.shape[0]))
        _, found = detector.detect(img)
    rows = [] if found is None else list(found)
    return sorted(rows, key=lambda r: -float(r[2]))[:MAX_FACES]


def feature(img: Any, row: Any) -> Any:
    _, recognizer = models()
    with _lock:
        return recognizer.feature(recognizer.alignCrop(img, row)).flatten()


def similarity(feat: Any, enrolled_features: Any) -> float:
    """Mean cosine against every enrolled vector: one lucky enrollment frame cannot pass a stranger."""
    import numpy as np

    a = feat / np.linalg.norm(feat)
    b = enrolled_features / np.linalg.norm(enrolled_features, axis=1, keepdims=True)
    return float(np.mean(b @ a))


def liveness(row: Any, stamp: float) -> str | None:
    """None when this face is live; otherwise why not, in words for the refusal."""
    x, y, w, h = (float(v) for v in row[:4])
    # The inner 60%: eyes, nose, cheeks. The full box takes in background beside the head,
    # whose depth step would read as relief on a flat print.
    d = depth_box(stamp, (x + 0.2 * w, y + 0.2 * h, x + 0.8 * w, y + 0.8 * h))
    if d.get("stamp") != stamp:
        raise Unavailable("the depth is not from the frame the face was found in")
    median, relief, fx = d.get("median_m"), d.get("relief_m"), d.get("fx")
    if not median or not fx or relief is None or (d.get("valid_frac") or 0.0) < MIN_VALID_FRAC:
        return "it has no depth reading"
    if median > MAX_M:
        return f"it is {median:.2f} m away, past {MAX_M} m"
    width = w * median / fx
    if not FACE_WIDTH_M[0] <= width <= FACE_WIDTH_M[1]:
        return f"it is {width * 100:.0f} cm wide, not a real face's size (a photo or a screen?)"
    if relief < MIN_RELIEF_M:
        return f"it is flat ({relief * 100:.1f} cm of relief; a printed photo?)"
    return None


def _check() -> tuple[str, str] | None:
    """None when the enrolled operator's live face is in view; else (reason code, detail)."""
    with _lock:
        enrolled_features = enrolled()
        if enrolled_features is None:
            return NOT_ENROLLED, "no operator is enrolled on this robot"
        img, stamp = fresh_frame()
        not_live = None
        for row in faces(img):
            if similarity(feature(img, row), enrolled_features) < MATCH_COSINE:
                continue
            why = liveness(row, stamp)
            if why is None:
                return None
            not_live = why
        if not_live is not None:
            return NOT_LIVE, f"the operator's face was recognised but {not_live}"
        return NOT_SEEN, "the enrolled operator's face is not in front of the head camera"


async def refusal(name: str) -> str | None:
    """Why this call must not run for want of the operator's face, or None. Instant when not gated."""
    if name not in GATED or name in NEVER_GATED:
        return None
    started = time.monotonic()
    outcome: tuple[str, str] | None
    try:
        outcome = await asyncio.wait_for(asyncio.to_thread(_check), TIMEOUT_S)
    except TimeoutError:
        outcome = UNAVAILABLE, f"the check took longer than {TIMEOUT_S} s"
    except Exception as exc:  # noqa: BLE001 - every failure to check is a refusal, never a pass
        outcome = UNAVAILABLE, f"the check failed ({exc})"
    ms = round((time.monotonic() - started) * 1000)
    if outcome is None:
        log.warning("face_gate.passed", tool=name, ms=ms)
        return None
    code, detail = outcome
    log.warning("face_gate.refused", tool=name, reason=code, detail=detail, ms=ms)
    return (
        f"refused ({code}): {name} needs the enrolled operator's live face in front of the "
        f"robot, and {detail}. {_HINTS[code].format(max_m=MAX_M)}"
    )
