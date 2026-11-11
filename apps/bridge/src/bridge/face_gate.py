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
landmarks it gives a patch median each, deprojected to 3-D. One frame is too noisy, so a
check judges each landmark's median over up to FRAMES frames: the nose must stand at least
MIN_NOSE_M in front of the plane through the eyes and mouth corners, whatever that
plane's tilt, and the face must be FACE_WIDTH_M wide at that depth (a face on a phone is
a few cm), within MAX_M. That stops a phone, a screen and a print held flat or tilted;
a mask, or a print bent sharply around the nose, is beyond it. It sits on top of a login,
not instead of one.

THE HEADSET. A Quest operator cannot pass it while wearing the headset: the headset hides
the face. The operator stands at the robot, faces the head camera, then retries.

PEOPLE. Everyone enrolled passes, whatever their role (ROLES): the live face is matched
against each person (mean cosine over their vectors) and the best one at MATCH_COSINE or
above is who it is. FACE_DIR/people/<id>/ holds features.npy, person.json
({"id", "name", "role", "enrolled_at"}) and, for the console, photo.jpg: one small crop of
the face that consented to enrollment. The single-operator layout (operator.npy and
operator.json) is migrated into it on first load as a superadmin; while it cannot be, it
still passes as that person.

PRIVACY. Written by scripts/face_enroll.py or /face/enroll on the robot, owner-only (files
0600, folders 0700, no symlinks, atomic). Nothing is stored about a face nobody enrolled,
nothing leaves the robot but the enrolled person's own photo (to the signed console), and
neither faces nor features are logged.

SETTINGS. FACE_DIR/settings.json ({"gate", "greet", "greet_unknown"}, written by
/face/settings) overrides the environment, live: gate on with C3PO_FACE_GATE empty gates
the `confirm` class; gate off gates nothing. One that exists but cannot be trusted (mode,
owner, a symlink, not JSON) reads as gate ON: a gate the console turned on never comes
back off by itself.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import shutil
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
#: A check's pass: its detail is the person's id.
PASSED = "passed"
#: No face in view at all (refused as NOT_SEEN); the greeter's end of a visit.
NO_FACE = "no_face"
#: A live face that matches nobody: only a check that asked for it (the greeter).
UNKNOWN = "unknown_face"

ROLES = ("superadmin", "operator")
_ID = re.compile(r"[0-9a-f]{12}\Z")
#: The single-operator layout's id while it cannot be migrated (see `people`).
LEGACY_ID = "operator"


#: The class token: every tool that needs the operator's confirmation.
CONFIRM = "confirm"


def _parse(raw: str) -> frozenset[str]:
    names = {n.strip() for n in raw.split(",") if n.strip()}
    if names & NEVER_GATED:
        log.warning("face_gate.never_gated_ignored", tools=sorted(names & NEVER_GATED))
    return frozenset(names - NEVER_GATED)


GATED = _parse(os.environ.get("C3PO_FACE_GATE", ""))
MAX_M = _env_float("C3PO_FACE_GATE_MAX_M", 1.2)
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
PATCH_R = 5
#: Same window as the vision container's own `live` flag (stream.STALE_AFTER_S).
FRAME_STALE_S = 1.0
TIMEOUT_S = 1.5
#: One D435 frame's landmark depth is too noisy to judge on (2026-11-11, on the robot: 3 of
#: 6 frames of a real face at 1 m read it flat), so a check judges the median over frames.
FRAMES = 5
#: No new frame is started after this much of TIMEOUT_S: the rest is left to finish in.
FRAMES_BUDGET_S = TIMEOUT_S * 2 / 3
#: Valid depth readings each landmark needs across those frames.
MIN_READINGS = 3
HTTP_TIMEOUT_S = 1.0
#: Largest faces looked at per check, so a crowd cannot stretch it past TIMEOUT_S.
MAX_FACES = 3

#: Enrollment: pause between accepted frames, so the vectors are not all one instant.
ENROLL_INTERVAL_S = 0.4
#: A whole enrollment, below apps/back's 30 s relay timeout: past it, nothing is stored.
ENROLL_DEADLINE_S = 20.0
#: The console's photo: the face box padded by this much of its size, at most PHOTO_PX.
PHOTO_PAD = 0.25
PHOTO_PX = 160

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
    NOT_ENROLLED: "No one is enrolled on this robot: they enroll on the robot with "
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
#: settings.json as last read or written; None = read it on next use.
_stored: dict[str, Any] | None = None
#: The old layout could not be migrated: not retried (nor logged) until the bridge restarts.
_migration_stuck = False
#: The last frame a background check looked at, for /face/seen: boxes, who, live. No features.
_seen: dict[str, Any] = {"stamp": None, "width": None, "height": None, "faces": []}
#: Not asyncio's default executor: a check wedged on the vision container must not
#: take the workers asyncio.to_thread hands damp and the rest.
_pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="face_gate")


class Unavailable(Exception):
    """The check could not be made at all."""


class Refused(Unavailable):
    """Enrollment refused: `code` is no_live_face, not_same_person or exists."""

    def __init__(self, code: str, reason: str) -> None:
        super().__init__(reason)
        self.code = code


def enrollment_path() -> Path:
    return FACE_DIR / "operator.npy"


def temp_path() -> Path:
    return FACE_DIR / ".operator.npy.tmp"


def name_path() -> Path:
    return FACE_DIR / "operator.json"


def people_dir() -> Path:
    return FACE_DIR / "people"


def settings_path() -> Path:
    return FACE_DIR / "settings.json"


def _folders(path: Path) -> list[Path]:
    """FACE_DIR and every folder below it down to `path`'s."""
    folders = [FACE_DIR]
    for part in path.parent.relative_to(FACE_DIR).parts:
        folders.append(folders[-1] / part)
    return folders


def _save_owner_only(path: Path, data: bytes) -> Path:
    """Write `data` to `path`, owner-only, atomically."""
    FACE_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
    for folder in _folders(path):
        folder.mkdir(mode=0o700, exist_ok=True)
        if not stat.S_ISDIR(os.lstat(folder).st_mode):  # a symlink: not ours to write into
            raise Unavailable(f"{folder} is not a plain folder")
        os.chmod(folder, 0o700)
    tmp = path.with_name(f".{path.name}.tmp")
    try:
        # EXCL + NOFOLLOW: never write through a file or link someone left there.
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    except FileExistsError:
        st = os.lstat(tmp)
        if not (stat.S_ISREG(st.st_mode) and st.st_uid == os.geteuid()):
            raise Unavailable(
                f"{tmp} is left from an interrupted run: run face_enroll.py with --delete"
            ) from None
        # A power cut's leftover: a plain file of ours, in folders just made ours alone.
        os.unlink(tmp)
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)
    return path


def _npy(features: Any) -> bytes:
    import io

    import numpy as np

    buf = io.BytesIO()
    np.save(buf, np.asarray(features, dtype=np.float32))
    return buf.getvalue()


def save_enrollment(features: Any) -> Path:
    """The single-operator layout, which `people` migrates: for tests and old robots only."""
    return _save_owner_only(enrollment_path(), _npy(features))


def save_name(name: str) -> Path:
    """The single-operator layout's name file (see save_enrollment)."""
    return _save_owner_only(name_path(), json.dumps({"name": name}).encode())


def _owner_only(st: os.stat_result, kind: str) -> bool:
    """Ours and nobody else's: else someone else's face could be in the enrollment."""
    if st.st_uid == os.geteuid() and not st.st_mode & 0o077:
        return True
    log.warning(
        "face_gate.enrollment_ignored",
        reason=f"{kind}_not_owner_only",
        mode=oct(st.st_mode & 0o777),
    )
    return False


def read_owner_only(path: Path) -> bytes | None:
    """A plain file of ours alone, in plain folders of ours alone from FACE_DIR down; else None."""
    try:
        folders = [os.lstat(d) for d in _folders(path)]
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    except FileNotFoundError:
        return None
    except OSError as exc:  # ELOOP: a symlink, which the operator did not write
        log.warning("face_gate.enrollment_ignored", reason="not_a_plain_file", error=str(exc))
        return None
    with os.fdopen(fd, "rb") as f:
        st = os.fstat(f.fileno())
        if not (all(stat.S_ISDIR(d.st_mode) for d in folders) and stat.S_ISREG(st.st_mode)):
            log.warning("face_gate.enrollment_ignored", reason="not_a_plain_file")
            return None
        if not (all(_owner_only(d, "directory") for d in folders) and _owner_only(st, "file")):
            return None
        return f.read()


def _features(path: Path) -> Any:
    """The feature vectors (N x 128) at `path`, or None when not trustworthy."""
    import io

    import numpy as np

    raw = read_owner_only(path)
    if raw is None:
        return None
    features = np.load(io.BytesIO(raw), allow_pickle=False)
    if features.ndim != 2 or not features.shape[0] or features.shape[1] != 128:
        raise Unavailable("the enrollment file is malformed; re-enroll")
    if not (np.isfinite(features).all() and np.linalg.norm(features, axis=1).all()):
        # A NaN or zero row makes every similarity NaN, and NaN < threshold is False.
        log.warning("face_gate.enrollment_ignored", reason="nan_or_zero_vector")
        return None
    return features


def _json(path: Path) -> dict[str, Any] | None:
    raw = read_owner_only(path)
    try:
        data = json.loads(raw) if raw is not None else None
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def enrolled() -> Any:
    """The single-operator layout's features, or None (see `people`)."""
    return _features(enrollment_path())


def enrolled_name() -> str | None:
    """The single-operator layout's name, or None."""
    name = (_json(name_path()) or {}).get("name")
    return (name.strip() or None) if isinstance(name, str) else None


def clean_name(raw: str) -> str:
    """A display name: 1 to 40 printable characters, spaces collapsed. Else ValueError."""
    name = " ".join(str(raw).split())
    if not 0 < len(name) <= 40 or not name.isprintable():
        raise ValueError("a name of 1 to 40 printable characters")
    return name


def person(pid: str) -> dict[str, Any] | None:
    """One enrolled person, features included, or None (no such id, or not trustworthy)."""
    if not _ID.match(pid):
        return None
    folder = people_dir() / pid
    info = _json(folder / "person.json")
    if info is None or info.get("id") != pid or info.get("role") not in ROLES:
        return None
    if not isinstance(info.get("name"), str):
        return None
    try:
        features = _features(folder / "features.npy")
    except Unavailable:
        log.warning("face_gate.enrollment_ignored", reason="malformed_features")
        return None
    if features is None:
        return None
    return {
        "id": pid,
        "name": info["name"],
        "role": info["role"],
        "enrolled_at": info.get("enrolled_at"),
        "has_photo": read_owner_only(folder / "photo.jpg") is not None,
        "features": features,
    }


def public(p: dict[str, Any]) -> dict[str, Any]:
    """A person without their features, for the console."""
    return {k: p[k] for k in ("id", "name", "role", "enrolled_at", "has_photo")}


def _migrate() -> None:
    """operator.npy (+ operator.json) into people/ as a superadmin; the old files go only
    once the new ones read back. Never over someone enrolled since under that name. Any
    failure is logged once and leaves them in place (passing as LEGACY_ID) until a restart."""
    global _migration_stuck
    if _migration_stuck or not os.path.lexists(enrollment_path()):
        return
    with _lock:
        try:
            features = enrolled()
            if features is None:
                return
            name = enrolled_name() or "Oliver"
            if any(p["name"].casefold() == name.casefold() for p in people_listing()):
                raise Unavailable(f"someone is already enrolled as {name}")
            p = add_person(name, "superadmin", features)
            if person(p["id"]) is None:
                raise Unavailable("the migrated person does not read back")
            for old in (name_path(), enrollment_path()):
                old.unlink(missing_ok=True)
            log.warning("face_gate.migrated", person=p["id"])
        except Exception as exc:  # noqa: BLE001 - the old enrollment still passes, below
            _migration_stuck = True
            log.error(
                "face_gate.migration_failed",
                error=str(exc),
                fix=f"the old enrollment stays as id {LEGACY_ID!r}: delete it, or fix and restart",
            )


def people() -> list[dict[str, Any]]:
    """Everyone enrolled (see `person`), migrating the single-operator layout first."""
    _migrate()
    try:
        ids = sorted(os.listdir(people_dir()))
    except OSError:
        ids = []
    found = [p for p in map(person, ids) if p is not None]
    try:
        legacy = enrolled() if os.path.lexists(enrollment_path()) else None
    except Unavailable:
        legacy = None
    if legacy is not None:  # migration failed: never lose the operator
        name = enrolled_name() or "Oliver"
        found.append(
            {"id": LEGACY_ID, "name": name, "role": "superadmin", "enrolled_at": None}
            | {"has_photo": False, "features": legacy}
        )
    return found


def add_person(name: str, role: str, features: Any, photo: bytes | None = None) -> dict[str, Any]:
    """Enroll `name` (replacing whoever has that name, any case) as `role`. Returns them."""
    if role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}")
    name = clean_name(name)
    with _lock:
        same = [p["id"] for p in people_listing() if p["name"].casefold() == name.casefold()]
        pid = same[0] if same else secrets.token_hex(6)
        folder = people_dir() / pid
        try:
            _save_owner_only(folder / "features.npy", _npy(features))
            if photo is not None:
                _save_owner_only(folder / "photo.jpg", photo)
            else:
                (folder / "photo.jpg").unlink(missing_ok=True)
            info = {"id": pid, "name": name, "role": role, "enrolled_at": round(time.time(), 3)}
            _save_owner_only(folder / "person.json", json.dumps(info).encode())  # last: a person
        except BaseException:
            if not same:  # a half-written new person: no biometrics without a person.json
                shutil.rmtree(folder, ignore_errors=True)
            raise
    return info | {"has_photo": photo is not None}


def people_listing() -> list[dict[str, Any]]:
    """Everyone in people/ (not the legacy fallback), without features; no migration."""
    try:
        ids = sorted(os.listdir(people_dir()))
    except OSError:
        return []
    return [public(p) for p in map(person, ids) if p is not None]


def _forget_legacy() -> None:
    for old in (enrollment_path(), temp_path(), name_path()):
        old.unlink(missing_ok=True)
    name_path().with_name(".operator.json.tmp").unlink(missing_ok=True)


def _forget_seen() -> None:
    """/face/seen must not keep naming someone who was just deleted."""
    global _seen
    _seen = {"stamp": None, "width": None, "height": None, "faces": []}


def delete_person(pid: str) -> bool:
    """Remove a person's folder, features, photo and all (LEGACY_ID: the old layout's files).
    False when there is no such id."""
    with _lock:
        if pid == LEGACY_ID and os.path.lexists(enrollment_path()):
            _forget_legacy()
        elif _ID.match(pid) and os.path.lexists(people_dir() / pid):
            shutil.rmtree(people_dir() / pid)  # refuses a symlinked folder; never follows one
        else:
            return False
        _forget_seen()
    return True


def delete_all() -> None:
    """Everyone, and the single-operator layout and its leftovers."""
    with _lock:
        if os.path.lexists(people_dir()):
            shutil.rmtree(people_dir())
        _forget_legacy()
        settings_path().with_name(f".{settings_path().name}.tmp").unlink(missing_ok=True)
        _forget_seen()


def settings() -> dict[str, bool]:
    """{"gate", "greet", "greet_unknown"}: settings.json over the environment's defaults."""
    global _stored
    if _stored is None:
        stored = _json(settings_path())
        if stored is None and os.path.lexists(settings_path()):
            # Fail closed: it may be the console's "gate on"; off would unguard every skill.
            log.error(
                "face_gate.settings_untrusted",
                path=str(settings_path()),
                gate="on",
                fix="switch any setting in the console to rewrite it (0600, folders 0700)",
            )
            stored = {"gate": True}
        _stored = stored or {}
    defaults = {
        "gate": bool(GATED),
        "greet": os.environ.get("C3PO_FACE_GREET", "") == "1",
        "greet_unknown": False,
    }
    return {
        k: v if not isinstance(_stored.get(k), bool) else _stored[k] for k, v in defaults.items()
    }


def update_settings(changes: dict[str, bool]) -> dict[str, bool]:
    """Merge and persist (0600); applies at once. Unknown keys or non-bool values: ValueError."""
    global _stored
    with _lock:  # read, merge, write: two at once must not lose one
        current = settings()
        if not set(changes) <= set(current) or not all(
            isinstance(v, bool) for v in changes.values()
        ):
            raise ValueError(f"settings take booleans for {', '.join(sorted(current))}")
        new = current | changes
        _save_owner_only(settings_path(), json.dumps(new).encode())
        _stored = new
        return dict(new)


def models_installed() -> bool:
    return all((FACE_DIR / "models" / n).is_file() for n in (YUNET, SFACE))


def models() -> tuple[Any, Any]:
    """(YuNet detector, SFace recognizer), loaded once."""
    global _models
    with _lock:
        if _models is None:
            paths = [FACE_DIR / "models" / name for name in (YUNET, SFACE)]
            missing = [p.name for p in paths if not p.is_file()]
            if missing:  # before the import: a cold `import cv2` holds the GIL for seconds
                raise Unavailable(
                    f"missing {', '.join(missing)}: run scripts/install_face_models.sh"
                )
            import cv2

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


def landmarks_3d(row: Any, stamp: float) -> tuple[list[Any], float]:
    """This face's five landmarks in metres from the camera (None: no depth reading), and its
    pixel width over fx. Raises Unavailable when /depth_points cannot be trusted."""
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
    # Right eye, left eye, nose, right and left mouth corner.
    points = [
        np.array([(u - cx) * z / fx, (v - cy) * z / fy, z])
        if isinstance(z, (int, float)) and z > 0
        else None
        for (u, v), z in zip(marks, zs)
    ]
    return points, float(row[2]) / fx


def judge(samples: list[tuple[list[Any], float]], min_readings: int = 1) -> str | None:
    """None when the per-landmark median of these frames is a live face; else why not, with
    measurements: for the log only. A flat print stays flat under a median."""
    import numpy as np

    readings = [[pts[i] for pts, _ in samples if pts[i] is not None] for i in range(5)]
    if min(len(r) for r in readings) < max(min_readings, 1):
        return "a landmark (eyes, nose, mouth corners) has no depth reading"
    p = np.array([np.median(r, axis=0) for r in readings])
    dist = float(np.median(p[:, 2]))
    if dist > MAX_M:
        return f"it is {dist:.2f} m away, past {MAX_M} m"
    width = float(np.median([w for _, w in samples])) * dist
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


def liveness(row: Any, stamp: float) -> str | None:
    """One frame's verdict, for enrollment (which retries frames); the gate judges several."""
    return judge([landmarks_3d(row, stamp)])


def _check(unknown: bool = False, record: bool = False) -> tuple[str, str]:
    """`_look`, and with `record` its last frame's faces kept for /face/seen."""
    view: dict[str, Any] = {}
    outcome = _look(unknown, view)
    if record:
        _record(view)
    return outcome


def _record(view: dict[str, Any]) -> None:
    global _seen
    if "stamp" not in view:  # no frame was taken (nobody enrolled)
        return
    names = {p["id"]: p for p in view["known"]}
    out = []
    for row, pid in view["faces"]:
        key = pid if pid is not None else ("" if row is view["unknown_row"] else None)
        live = None
        if key is not None and view["samples"].get(key):
            live = judge(view["samples"][key], MIN_READINGS) is None
        p = names.get(pid or "")
        out.append(
            {
                "box": [round(float(v), 1) for v in row[:4]],
                "name": p["name"] if p else None,
                "role": p["role"] if p else None,
                "known": p is not None,
                "live": live,
            }
        )
    _seen = {k: view[k] for k in ("stamp", "width", "height")} | {"faces": out}


def seen() -> dict[str, Any]:
    """For /face/seen: the last background check's frame, and how old it is."""
    s = _seen
    return s | {"age_s": None if s["stamp"] is None else round(time.time() - s["stamp"], 2)}


def _look(unknown: bool, view: dict[str, Any]) -> tuple[str, str]:
    """(PASSED, person id) when an enrolled person's live face is in view; else (reason, detail).

    Up to FRAMES fresh frames within FRAMES_BUDGET_S, stopping once decided. Identity: the
    same person was the best match in a majority of the frames with a face. Liveness:
    `judge` on that person's face over those frames, each landmark from at least
    MIN_READINGS of them. `unknown`: the largest face when it matches nobody is judged the
    same way, as person "", and a live one is (UNKNOWN, ...); nothing about it is kept.
    """
    known = people()
    if not known:
        return NOT_ENROLLED, "no one is enrolled on this robot"
    models()  # missing models refuse here, before `frame` imports cv2
    deadline = time.monotonic() + FRAMES_BUDGET_S
    faced = 0
    matched: dict[str, int] = {}
    samples: dict[str, list[tuple[list[Any], float]]] = {}
    view.update(known=known, samples=samples)
    for taken in range(1, FRAMES + 1):
        # shortcut: two fetches can return the same frame, counted twice; fine at 5 frames in 1 s.
        img, stamp = fresh_frame()
        rows = faces(img)
        best: tuple[float, Any, str] | None = None
        each: list[tuple[Any, str | None]] = []
        for row in rows:
            feat = feature(img, row)
            mine: tuple[float, str] | None = None
            for p in known:
                sim = similarity(feat, p["features"])
                if sim >= MATCH_COSINE and (mine is None or sim > mine[0]):  # NaN: no match
                    mine = (sim, p["id"])
            each.append((row, None if mine is None else mine[1]))
            if mine is not None and (best is None or mine[0] > best[0]):
                best = (mine[0], row, mine[1])
        faced += bool(rows)
        if best is None and rows and unknown:
            best = (0.0, rows[0], "")
        view.update(stamp=stamp, width=int(img.shape[1]), height=int(img.shape[0]), faces=each)
        view["unknown_row"] = best[1] if best is not None and best[2] == "" else None
        if best is not None:
            matched[best[2]] = matched.get(best[2], 0) + 1
            samples.setdefault(best[2], []).append(landmarks_3d(best[1], stamp))
        left = FRAMES - taken
        for pid, n in matched.items():
            if 2 * n > faced and judge(samples[pid], MIN_READINGS) is None:
                return (PASSED, pid) if pid else (UNKNOWN, "a live face that matches nobody")
        top = max(matched.values(), default=0)
        if (
            2 * (top + left) <= faced + left  # a majority is out of reach
            or max(map(len, samples.values()), default=0) + left < MIN_READINGS
            or time.monotonic() >= deadline
        ):
            break
    if not faced:
        return NO_FACE, "no face is in front of the head camera"
    who = max(matched, key=lambda k: matched[k], default=None)
    if who is None or who == "" or not 2 * matched[who] > faced:
        return NOT_SEEN, "no enrolled person's face is in front of the head camera"
    why = judge(samples[who], MIN_READINGS)
    return NOT_LIVE, (
        f"an enrolled face was recognised but {why} ({len(samples[who])} of {taken} frames)"
    )


def _run_check(unknown: bool, record: bool) -> tuple[str, str]:
    try:
        return _check(unknown, record)
    finally:
        _busy.release()


async def live_check(*, background: bool = False, unknown: bool = False) -> tuple[str, str]:
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
        job = asyncio.get_running_loop().run_in_executor(_pool, _run_check, unknown, background)
    except BaseException:
        _busy.release()
        raise
    try:
        return await asyncio.wait_for(job, TIMEOUT_S)
    except TimeoutError:
        return UNAVAILABLE, f"the check took longer than {TIMEOUT_S} s"
    except Exception as exc:  # noqa: BLE001 - every failure to check is a refusal, never a pass
        return UNAVAILABLE, f"the check failed ({exc})"


def gated_tools() -> frozenset[str]:
    """What is gated now: C3PO_FACE_GATE, or `confirm` when the settings turned it on bare."""
    if not settings()["gate"]:
        return frozenset()
    return GATED or frozenset({CONFIRM})


def gated(name: str, meta: dict[str, Any] | None = None) -> bool:
    """Whether a call of this tool (with this `_meta`) needs an enrolled person's face."""
    tools = gated_tools()
    if name in NEVER_GATED or not tools:
        return False
    return name in tools or (CONFIRM in tools and safety.needs_confirmation(meta))


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
    code, detail = await live_check()
    ms = round((time.monotonic() - started) * 1000)
    if code == PASSED:
        log.warning("face_gate.passed", tool=name, person=detail, ms=ms)
        return None
    code = NOT_SEEN if code in (NO_FACE, UNKNOWN) else code
    log.warning("face_gate.refused", tool=name, reason=code, detail=detail, ms=ms)
    hint = _HINTS[code].format(max_m=MAX_M, ttl=int(safety.CONFIRM_TTL_S))
    return f"refused ({code}): {name} needs the enrolled operator's live face. {hint}"


def photo(img: Any, row: Any) -> bytes:
    """The console's picture of a consenting person: their face box, padded, JPEG, small."""
    import cv2

    x, y, w, h = (float(v) for v in row[:4])
    pad = PHOTO_PAD * max(w, h)
    x0, y0 = max(0, int(x - pad)), max(0, int(y - pad))
    crop = img[y0 : int(y + h + pad), x0 : int(x + w + pad)]
    scale = PHOTO_PX / max(crop.shape[:2])
    if scale < 1:
        crop = cv2.resize(crop, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    ok, jpg = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 80])
    if not ok:
        raise Unavailable("the photo did not encode")
    return bytes(jpg)


def same_person(features: list[Any]) -> bool:
    """Every pair of frames matches at the gate's own threshold: no second face slipped in."""
    import numpy as np

    f = np.asarray(features, dtype=np.float64)
    f = f / np.linalg.norm(f, axis=1, keepdims=True)
    return bool(((f @ f.T) >= MATCH_COSINE).all())  # `>=`: a NaN is no match


def enroll(
    name: str,
    role: str,
    frames: int = 5,
    say: Any = lambda _msg: None,
    replace: bool = False,
) -> dict[str, Any]:
    """Enroll from `frames` frames each with exactly one live face, all the same person.

    Saves their vectors and ONE photo (the last frame's face). A name already enrolled
    (any case) is Refused "exists" unless `replace`. Raises Unavailable with why not,
    within ENROLL_DEADLINE_S; `say` hears each frame's verdict (no measurements of the face).
    """
    name = clean_name(name)  # before the camera: a bad name wastes no one's time
    if role not in ROLES:
        raise ValueError(f"role must be one of {', '.join(ROLES)}")
    known = people()  # migrates first, so the old operator is never enrolled over below
    if not replace and any(p["name"].casefold() == name.casefold() for p in known):
        raise Refused("exists", f"{name} is already enrolled; ask to replace them")
    models()  # missing models refuse here, before `frame` imports cv2
    deadline = time.monotonic() + ENROLL_DEADLINE_S
    features: list[Any] = []
    last: tuple[Any, Any] | None = None
    for attempt in range(1, frames * 4 + 1):
        if len(features) == frames or time.monotonic() >= deadline:
            break
        try:
            img, stamp = fresh_frame()
            rows = faces(img)
            why = (
                liveness(rows[0], stamp)
                if len(rows) == 1
                else f"{len(rows)} faces, need exactly one"
            )
        except OSError as exc:  # URLError is an OSError
            raise Unavailable(str(exc)) from None
        if why is not None:
            say(f"frame {attempt}: skipped, {why}")
            continue
        features.append(feature(img, rows[0]))
        last = img, rows[0]
        say(f"frame {attempt}: ok ({len(features)}/{frames})")
        time.sleep(ENROLL_INTERVAL_S)
    if len(features) < frames or last is None:
        raise Refused("no_live_face", f"only {len(features)} of {frames} frames had one live face")
    if not same_person(features):
        raise Refused("not_same_person", "the frames do not all show the same person")
    return add_person(name, role, features, photo(*last))
