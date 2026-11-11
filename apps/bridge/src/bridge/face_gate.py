"""The operator face gate: chosen skills run only with the enrolled operator's live face in view.

AN EXTRA GATE, NEVER A REPLACEMENT. `mcp_server.GovernedMCP.call_tool` runs it only after
`safety.tool_refusal` let the call through (PARAR, battery, person rule, the operator's
one-shot confirmation, checked but not yet spent), then runs that again to spend the
grant, so a face refusal leaves the confirmation for one retry within its TTL. That method
is what both transports dispatch through, so apps/back's /invoke, its agent and Claude
Code all meet it. Direct Python calls between tools skip it, which is why the `confirm`
class is the setting to use: no tool calls a confirmation-tier tool in Python.

`C3PO_FACE_GATE` is a comma list of tool names and the class token `confirm` (every tool
`safety.needs_confirmation` holds for: T2, and high-danger T1). Empty = off. The stop and
release skills are never gated, whatever it says. A name that is no tool of this bridge
widens the gate to `confirm` and is logged as an error (`check_config`): a misspelling
must not leave a skill unguarded, and the bridge must still start (it is the only stop).

FAILS CLOSED. No enrollment, no models, no vision container, a stale frame, depth from
another moment, any exception, another check still running, or no answer within
TIMEOUT_S: refused. The work runs on this module's own one-thread executor, so a wedged
check holds neither the event loop a stop is dispatched on nor the default executor.

LIVENESS FROM DEPTH. The vision container's depth is aligned to the colour frame the face
is found in, and `GET /depth_points` answers for that frame's stamp only. At YuNet's five
landmarks it gives a patch median each, deprojected to 3-D: the nose must stand at least
MIN_NOSE_M in front of the plane through the eyes and mouth corners, whatever that
plane's tilt, and the face must be FACE_WIDTH_M wide at that depth (a face on a phone is
a few cm), within MAX_M. That stops a phone, a screen and a print held flat or tilted;
a mask, or a print bent sharply around the nose, is beyond it. It sits on top of a login,
not instead of one.

THE HEADSET. A Quest operator cannot pass it while wearing the headset: the headset hides
the face. The operator stands at the robot, faces the head camera, then retries.

PRIVACY. Only SFace feature vectors are stored, at FACE_DIR/operator.npy (0600, directory
0700), written by scripts/face_enroll.py on the robot. No image is kept, nothing leaves the
robot, and neither faces nor features are logged.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import threading
import time
import urllib.parse
import urllib.request
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import structlog

from bridge import safety
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
#: Only a background check sees this; a gated call never does.
BUSY = "busy"


#: The class token: every tool that needs the operator's confirmation.
CONFIRM = "confirm"


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
#: How far the nose tip stands in front of the eyes-and-mouth-corners plane, at least.
#: A real one is ~2-3 cm; a print, tilted or not, is 0 plus noise.
MIN_NOSE_M = 0.015
#: Patch radius, /frame.jpg pixels, of each landmark's depth median.
PATCH_R = 3
#: Same window as the vision container's own `live` flag (stream.STALE_AFTER_S).
FRAME_STALE_S = 1.0
TIMEOUT_S = 1.5
HTTP_TIMEOUT_S = 1.0
#: Largest faces looked at per check, so a crowd cannot stretch it past TIMEOUT_S.
MAX_FACES = 3

_AT_ROBOT = (
    "The operator must stand at the robot (not in the headset, which hides the face) and "
    "face its head camera within {max_m} m; when they say they are there, retry once with "
    "the same arguments (a confirmation lasts {ttl} s, after that they confirm again)."
)
#: The whole refusal is fixed text: measurements stay in the bridge log, never in a reply.
_HINTS = {
    NOT_SEEN: "The enrolled operator's face is not in front of the head camera. " + _AT_ROBOT,
    NOT_LIVE: "A face matched the operator but did not pass the liveness check (a photo, "
    "a screen, or too far?). " + _AT_ROBOT,
    NOT_ENROLLED: "No operator is enrolled on this robot: they enroll on the robot with "
    "apps/bridge/scripts/face_enroll.py; tell them.",
    UNAVAILABLE: "The face check could not run (the bridge log says why). Tell the operator; "
    "do not retry in a loop.",
}

_lock = threading.RLock()
_models: tuple[Any, Any] | None = None
#: Held from submit to the end of a check, so a wedged check refuses the next at once
#: instead of queueing it. A plain Lock: the worker releases what the loop acquired.
_busy = threading.Lock()
#: The running check is the greeter's, which a gated call waits out. Loop-only state.
_background = False
#: Not asyncio's default executor: a check wedged on the vision container must not
#: take the workers asyncio.to_thread hands damp and the rest.
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="face_gate")


class Unavailable(Exception):
    """The check could not be made at all."""


def enrollment_path() -> Path:
    return FACE_DIR / "operator.npy"


def temp_path() -> Path:
    return FACE_DIR / ".operator.npy.tmp"


def name_path() -> Path:
    return FACE_DIR / "operator.json"


def _save_owner_only(path: Path, data: bytes) -> Path:
    """Write `data` to `path`, owner-only, atomically."""
    FACE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(FACE_DIR, 0o700)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        # EXCL + NOFOLLOW: never write through a file or link someone left there.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        raise Unavailable(f"{tmp} is left from an interrupted run: run with --delete") from None
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


def save_enrollment(features: Any) -> Path:
    """Write the operator's feature vectors, owner-only, atomically. Never images."""
    import io

    import numpy as np

    buf = io.BytesIO()
    np.save(buf, np.asarray(features, dtype=np.float32))
    return _save_owner_only(enrollment_path(), buf.getvalue())


def save_name(name: str) -> Path:
    """The operator's display name, for the greeter. Nothing biometric."""
    return _save_owner_only(name_path(), json.dumps({"name": name}).encode())


def _owner_only(st: os.stat_result, kind: str) -> bool:
    """Ours and nobody else's: else someone else's face could be in the enrollment."""
    if st.st_uid == os.geteuid() and not st.st_mode & 0o077:
        return True
    log.warning(
        "face_gate.enrollment_ignored", reason=f"{kind}_not_owner_only", mode=oct(st.st_mode & 0o777)
    )
    return False


def enrolled() -> Any:
    """The enrolled features (N x 128), or None when there is no trustworthy enrollment."""
    import numpy as np

    try:
        folder = os.lstat(FACE_DIR)
        fd = os.open(enrollment_path(), os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError as exc:  # ELOOP: a symlink, which the operator did not write
        log.warning("face_gate.enrollment_ignored", reason="not_a_plain_file", error=str(exc))
        return None
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not (stat.S_ISDIR(folder.st_mode) and stat.S_ISREG(st.st_mode)):
            log.warning("face_gate.enrollment_ignored", reason="not_a_plain_file")
            return None
        if not (_owner_only(folder, "directory") and _owner_only(st, "file")):
            return None
        features = np.load(f, allow_pickle=False)
    if features.ndim != 2 or not features.shape[0] or features.shape[1] != 128:
        raise Unavailable("the enrollment file is malformed; re-enroll")
    if not (np.isfinite(features).all() and np.linalg.norm(features, axis=1).all()):
        # A NaN or zero row makes every similarity NaN, and NaN < threshold is False.
        log.warning("face_gate.enrollment_ignored", reason="nan_or_zero_vector")
        return None
    return features


def enrolled_name() -> str | None:
    """The enrolled operator's display name, or None (none set, or the file is not trustworthy)."""
    try:
        folder = os.lstat(FACE_DIR)
        fd = os.open(name_path(), os.O_RDONLY | os.O_NOFOLLOW)
    except OSError:  # missing, or a symlink
        return None
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not (stat.S_ISDIR(folder.st_mode) and stat.S_ISREG(st.st_mode)):
            return None
        if not (_owner_only(folder, "directory") and _owner_only(st, "file")):
            return None
        try:
            name = json.loads(f.read(4096)).get("name")
        except (ValueError, AttributeError):
            return None
    return (name.strip() or None) if isinstance(name, str) else None


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


def depth_points(stamp: float, points: list[tuple[float, float]]) -> dict:
    """The vision container's patch-median depth at `points` of the frame with this stamp."""
    pts = ";".join(f"{x:.1f},{y:.1f}" for x, y in points)
    query = {"pts": pts, "r": PATCH_R, "stamp": repr(stamp)}
    body, _ = _get("depth_points?" + urllib.parse.urlencode(query))
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
    """None when this face is live; otherwise why not, with measurements: for the log only."""
    import numpy as np

    marks = [(float(row[4 + 2 * i]), float(row[5 + 2 * i])) for i in range(5)]
    d = depth_points(stamp, marks)
    if d.get("stamp") != stamp:
        raise Unavailable("the depth is not from the frame the face was found in")
    try:
        fx, fy, cx, cy = (float(d[k]) for k in ("fx", "fy", "cx", "cy"))
    except (KeyError, TypeError, ValueError):
        raise Unavailable("/depth_points sent no intrinsics (too old a vision container)") from None
    zs = d.get("depth_m")
    if not isinstance(zs, list) or len(zs) != 5:
        raise Unavailable("/depth_points did not answer for the five landmarks")
    if not all(isinstance(z, (int, float)) and z > 0 for z in zs):
        return "a landmark (eyes, nose, mouth corners) has no depth reading"
    # Right eye, left eye, nose, right and left mouth corner, in metres from the camera.
    p = np.array([((u - cx) * z / fx, (v - cy) * z / fy, z) for (u, v), z in zip(marks, zs)])
    dist = float(np.median(p[:, 2]))
    if dist > MAX_M:
        return f"it is {dist:.2f} m away, past {MAX_M} m"
    width = float(row[2]) * dist / fx
    if not FACE_WIDTH_M[0] <= width <= FACE_WIDTH_M[1]:
        return f"it is {width * 100:.0f} cm wide, not a real face's size (a photo or a screen?)"
    rim = p[[0, 1, 3, 4]]
    centre = rim.mean(axis=0)
    normal = np.linalg.svd(rim - centre)[2][-1]  # least-squares plane: any tilt
    if normal @ centre > 0:
        normal = -normal  # towards the camera, which is the origin
    nose = float((p[2] - centre) @ normal)
    if not nose >= MIN_NOSE_M:
        return f"the nose is {nose * 100:.1f} cm proud of the eyes and mouth (flat: a print?)"
    return None


def _check() -> tuple[str, str] | None:
    """None when the enrolled operator's live face is in view; else (reason code, detail)."""
    enrolled_features = enrolled()
    if enrolled_features is None:
        return NOT_ENROLLED, "no operator is enrolled on this robot"
    img, stamp = fresh_frame()
    not_live = None
    for row in faces(img):
        if not similarity(feature(img, row), enrolled_features) >= MATCH_COSINE:
            continue  # `not >=`: a NaN similarity is no match
        why = liveness(row, stamp)
        if why is None:
            return None
        not_live = why
    if not_live is not None:
        return NOT_LIVE, f"the operator's face was recognised but {not_live}"
    return NOT_SEEN, "the enrolled operator's face is not in front of the head camera"


def _run_check() -> tuple[str, str] | None:
    try:
        return _check()
    finally:
        _busy.release()


async def live_check(*, background: bool = False) -> tuple[str, str] | None:
    """`_check` on the gate's own worker, bounded by TIMEOUT_S; any failure is an outcome, never a pass.

    A background check (the greeter) never waits: (BUSY, ...) when a check is running. A
    gated call waits out a background check, up to TIMEOUT_S, instead of being refused by it;
    any other running check refuses it at once.
    """
    global _background
    deadline = time.monotonic() + TIMEOUT_S
    while not _busy.acquire(blocking=False):
        if background:
            return BUSY, "another face check is still running"
        if not _background or time.monotonic() >= deadline:
            return UNAVAILABLE, "another face check is still running"
        await asyncio.sleep(0.02)
    _background = background
    try:
        job = asyncio.get_running_loop().run_in_executor(_pool, _run_check)
    except BaseException:
        _busy.release()
        raise
    try:
        return await asyncio.wait_for(job, TIMEOUT_S)
    except TimeoutError:
        return UNAVAILABLE, f"the check took longer than {TIMEOUT_S} s"
    except Exception as exc:  # noqa: BLE001 - every failure to check is a refusal, never a pass
        return UNAVAILABLE, f"the check failed ({exc})"


def gated(name: str, meta: dict[str, Any] | None = None) -> bool:
    """Whether a call of this tool (with this `_meta`) needs the operator's face."""
    if name in NEVER_GATED:
        return False
    return name in GATED or (CONFIRM in GATED and safety.needs_confirmation(meta))


def check_config(tools: Iterable[str]) -> list[str]:
    """A C3PO_FACE_GATE entry that names no tool of this bridge widens the gate to `confirm`.

    Fail closed without exiting: a bridge that refuses to boot has no stop_everything
    either, and under Restart=always it loops. So a misspelling gates every
    confirmation-tier tool on top of the names that do exist, and says so loudly.
    """
    global GATED
    unknown = sorted(GATED - set(tools) - {CONFIRM})
    if unknown:
        GATED = frozenset((GATED - set(unknown)) | {CONFIRM})
        log.error(
            "face_gate.unknown_names",
            unknown=unknown,
            gated=sorted(GATED),
            fix="no such tool: every confirmation-tier tool is now gated; fix the name",
        )
    return unknown


async def refusal(name: str, meta: dict[str, Any] | None = None) -> str | None:
    """Why this call must not run for want of the operator's face, or None. Instant when not gated."""
    if not gated(name, meta):
        return None
    started = time.monotonic()
    outcome = await live_check()
    ms = round((time.monotonic() - started) * 1000)
    if outcome is None:
        log.warning("face_gate.passed", tool=name, ms=ms)
        return None
    code, detail = outcome
    log.warning("face_gate.refused", tool=name, reason=code, detail=detail, ms=ms)
    hint = _HINTS[code].format(max_m=MAX_M, ttl=int(safety.CONFIRM_TTL_S))
    return f"refused ({code}): {name} needs the enrolled operator's live face. {hint}"
