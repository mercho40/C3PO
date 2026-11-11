"""The operator face gate: fails closed, never gates a stop, sits on top of the tier gate.

Detector, recognizer and depth are fakes here; the last test runs the real YuNet + SFace
models when scripts/install_face_models.sh has put them under C3PO_FACE_DIR.
"""

from __future__ import annotations

import asyncio
import os
import stat
import sys
import threading
import time
from pathlib import Path

import numpy as np
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from bridge import face_gate

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import face_enroll  # noqa: E402

OPERATOR = np.eye(128, dtype=np.float32)[0]
STRANGER = np.eye(128, dtype=np.float32)[1]
FX = 600.0
REAL_FACES = face_gate.faces


def _row(x: float, w: float) -> np.ndarray:
    """A YuNet row: x, y, w, h, five landmarks, score."""
    return np.array([x, 100.0, w, w * 1.2, *([0.0] * 10), 0.95], dtype=np.float32)


@pytest.fixture
def scene(tmp_path, monkeypatch):
    """A gated `pick`, an enrolled operator, and a camera the test fills in."""
    monkeypatch.setattr(face_gate, "FACE_DIR", tmp_path / "face")
    monkeypatch.setattr(face_gate, "GATED", frozenset({"pick", "get_state"}))
    face_gate.save_enrollment(np.stack([OPERATOR, OPERATOR]))
    s: dict = {"stamp": time.time(), "faces": [], "who": {}, "depth": {}}
    monkeypatch.setattr(face_gate, "frame", lambda: ("img", s["stamp"]))
    monkeypatch.setattr(face_gate, "faces", lambda img: s["faces"])
    monkeypatch.setattr(face_gate, "feature", lambda img, row: s["who"][float(row[0])])

    def depth_box(stamp, box):
        d = {"stamp": stamp, "fx": FX, "valid_frac": 1.0, "median_m": 1.0, "relief_m": 0.03}
        return {**d, **s["depth"]}

    monkeypatch.setattr(face_gate, "depth_box", depth_box)
    return s


def _face(s: dict, x: float, who: np.ndarray, width_m: float = 0.15, at_m: float = 1.0) -> None:
    s["faces"].append(_row(x, width_m * FX / at_m))
    s["who"][x] = who


def _code(refusal: str | None) -> str | None:
    return None if refusal is None else refusal.split("(", 1)[1].split(")", 1)[0]


async def test_the_live_operator_passes(scene):
    _face(scene, 10, OPERATOR)
    assert await face_gate.refusal("pick") is None


async def test_a_stranger_is_not_the_operator(scene):
    _face(scene, 10, STRANGER)
    assert _code(await face_gate.refusal("pick")) == face_gate.NOT_SEEN


async def test_no_face_is_not_seen(scene):
    assert _code(await face_gate.refusal("pick")) == face_gate.NOT_SEEN


async def test_two_faces_pass_only_when_one_is_the_live_operator(scene):
    _face(scene, 10, STRANGER)
    _face(scene, 300, STRANGER)
    assert _code(await face_gate.refusal("pick")) == face_gate.NOT_SEEN
    scene["who"][300] = OPERATOR
    assert await face_gate.refusal("pick") is None


async def test_a_photo_on_a_phone_is_too_small(scene):
    _face(scene, 10, OPERATOR, width_m=0.05, at_m=0.5)
    scene["depth"] = {"median_m": 0.5}
    refusal = await face_gate.refusal("pick")
    assert _code(refusal) == face_gate.NOT_LIVE and "cm wide" in refusal


async def test_a_printed_photo_is_flat(scene):
    _face(scene, 10, OPERATOR)
    scene["depth"] = {"relief_m": 0.003}
    refusal = await face_gate.refusal("pick")
    assert _code(refusal) == face_gate.NOT_LIVE and "flat" in refusal


async def test_past_max_range_is_not_live(scene):
    _face(scene, 10, OPERATOR, at_m=1.8)
    scene["depth"] = {"median_m": 1.8}
    assert _code(await face_gate.refusal("pick")) == face_gate.NOT_LIVE


async def test_no_depth_on_the_face_is_not_live(scene):
    _face(scene, 10, OPERATOR)
    scene["depth"] = {"valid_frac": 0.2}
    assert _code(await face_gate.refusal("pick")) == face_gate.NOT_LIVE


async def test_depth_from_another_moment_fails_closed(scene):
    _face(scene, 10, OPERATOR)
    scene["depth"] = {"stamp": scene["stamp"] - 0.2}
    assert _code(await face_gate.refusal("pick")) == face_gate.UNAVAILABLE


async def test_a_stale_frame_fails_closed(scene):
    _face(scene, 10, OPERATOR)
    scene["stamp"] = time.time() - face_gate.FRAME_STALE_S - 0.5
    assert _code(await face_gate.refusal("pick")) == face_gate.UNAVAILABLE


async def test_no_enrollment_is_refused(scene):
    _face(scene, 10, OPERATOR)
    face_gate.enrollment_path().unlink()
    assert _code(await face_gate.refusal("pick")) == face_gate.NOT_ENROLLED


async def test_an_enrollment_others_can_read_or_write_is_ignored(scene):
    _face(scene, 10, OPERATOR)
    os.chmod(face_gate.enrollment_path(), 0o644)
    assert _code(await face_gate.refusal("pick")) == face_gate.NOT_ENROLLED


async def test_no_vision_container_fails_closed(scene, monkeypatch):
    def down():
        raise OSError("connection refused")

    monkeypatch.setattr(face_gate, "frame", down)
    assert _code(await face_gate.refusal("pick")) == face_gate.UNAVAILABLE


async def test_missing_models_fail_closed(scene, monkeypatch):
    import cv2  # noqa: F401 - up front, as main() does: a cold import outlasts TIMEOUT_S

    monkeypatch.setattr(face_gate, "faces", REAL_FACES)
    monkeypatch.setattr(face_gate, "_models", None)
    assert not (face_gate.FACE_DIR / "models").exists()
    refusal = await face_gate.refusal("pick")
    assert _code(refusal) == face_gate.UNAVAILABLE and "install_face_models" in refusal


async def test_a_slow_check_times_out_closed(scene, monkeypatch):
    monkeypatch.setattr(face_gate, "TIMEOUT_S", 0.05)
    monkeypatch.setattr(face_gate, "frame", lambda: (time.sleep(0.3), ("img", time.time()))[1])
    started = time.monotonic()
    assert _code(await face_gate.refusal("pick")) == face_gate.UNAVAILABLE
    assert time.monotonic() - started < 0.25


async def test_gate_off_changes_nothing(scene, monkeypatch):
    monkeypatch.setattr(face_gate, "GATED", face_gate._parse(""))
    monkeypatch.setattr(face_gate, "frame", lambda: pytest.fail("the camera was read"))
    assert await face_gate.refusal("pick") is None


async def test_stop_skills_are_never_gated(scene, monkeypatch):
    assert face_gate._parse("pick, stop_everything,soft_stop,release_arm") == {"pick"}
    monkeypatch.setattr(face_gate, "GATED", face_gate.NEVER_GATED)
    monkeypatch.setattr(face_gate, "frame", lambda: pytest.fail("the camera was read"))
    for name in face_gate.NEVER_GATED:
        assert await face_gate.refusal(name) is None


# -- on the MCP dispatch path -------------------------------------------------


async def test_a_gated_tool_is_refused_over_mcp(scene):
    from bridge import mcp_server

    with pytest.raises(ToolError, match=face_gate.NOT_SEEN):
        await mcp_server.mcp.call_tool("get_state", {})
    _face(scene, 10, OPERATOR)
    await mcp_server.mcp.call_tool("get_state", {})


async def test_the_confirmation_gate_still_comes_first(scene, monkeypatch):
    from bridge import mcp_server, safety

    monkeypatch.setattr(safety, "T2_CONFIRM", True)
    monkeypatch.setattr(face_gate, "frame", lambda: pytest.fail("the face was checked first"))
    with pytest.raises(ToolError, match="needs an operator's confirmation"):
        await mcp_server.mcp.call_tool("pick", {"label": "cup"})


async def test_a_stop_is_not_delayed_by_a_gated_check(scene, monkeypatch):
    from bridge import mcp_server

    release = threading.Event()
    monkeypatch.setattr(face_gate, "frame", lambda: (release.wait(2.0), ("img", time.time()))[1])
    gated = asyncio.create_task(mcp_server.mcp.call_tool("get_state", {}))
    await asyncio.sleep(0.05)
    started = time.monotonic()
    await mcp_server.mcp.call_tool("stop_everything", {})
    assert time.monotonic() - started < 0.5
    assert not gated.done()
    release.set()
    with pytest.raises(ToolError):
        await gated


# -- enrollment ---------------------------------------------------------------


def test_enrollment_is_owner_only_features_and_delete_removes_it(scene, capsys, monkeypatch):
    os.chmod(face_gate.FACE_DIR, 0o755)
    face_gate.enrollment_path().unlink()
    _face(scene, 10, OPERATOR)
    monkeypatch.setattr(face_enroll, "INTERVAL_S", 0.0)

    assert face_enroll.main(["--yes", "--frames", "3"]) == 0
    path = face_gate.enrollment_path()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(face_gate.FACE_DIR.stat().st_mode) == 0o700
    stored = np.load(path, allow_pickle=False)
    assert stored.shape == (3, 128) and stored.dtype == np.float32
    assert "SOLO" in capsys.readouterr().out

    assert face_enroll.main(["--delete"]) == 0
    assert not path.exists()


def test_enrollment_refuses_two_faces_and_a_flat_face(scene, monkeypatch):
    face_gate.enrollment_path().unlink()
    monkeypatch.setattr(face_enroll, "INTERVAL_S", 0.0)
    _face(scene, 10, OPERATOR)
    _face(scene, 300, STRANGER)
    assert face_enroll.main(["--yes", "--frames", "2"]) == 1
    scene["faces"].pop()
    scene["depth"] = {"relief_m": 0.0}
    assert face_enroll.main(["--yes", "--frames", "2"]) == 1
    assert not face_gate.enrollment_path().exists()


# -- the real models, when installed ------------------------------------------


@pytest.mark.skipif(
    not (face_gate.FACE_DIR / "models" / face_gate.SFACE).is_file(),
    reason="no face models: run scripts/install_face_models.sh (C3PO_FACE_DIR)",
)
def test_the_real_models_load_and_run(monkeypatch):
    monkeypatch.setattr(face_gate, "_models", None)
    rng = np.random.default_rng(0)
    assert face_gate.faces(np.full((480, 640, 3), 127, np.uint8)) == []
    noise = rng.integers(0, 255, (480, 640, 3), dtype=np.uint8)
    other = rng.integers(0, 255, (480, 640, 3), dtype=np.uint8)
    row = _row(200, 160)
    row[4:14] = [250, 270, 310, 270, 280, 300, 255, 330, 305, 330]  # eyes, nose, mouth corners
    feat = face_gate.feature(noise, row)
    assert feat.shape == (128,)
    assert face_gate.similarity(feat, feat[None, :]) == pytest.approx(1.0, abs=1e-5)
    assert face_gate.similarity(face_gate.feature(other, row), feat[None, :]) < 1.0
