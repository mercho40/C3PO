"""The operator face gate: fails closed, never gates a stop, sits on top of the tier gate.

Detector, recognizer and depth are fakes here; the last test runs the real YuNet + SFace
models when scripts/install_face_models.sh has put them under C3PO_FACE_DIR.
"""

from __future__ import annotations

import asyncio
import json
import os
import stat
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import numpy as np
import pytest
from mcp.server.fastmcp.exceptions import ToolError

from bridge import face_gate

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import face_enroll  # noqa: E402

_E = np.eye(128, dtype=np.float32)
OPERATOR = _E[0]
#: The operator again, another frame: cosine 0.71 to the first.
OPERATOR_2 = (_E[0] + _E[1]) / np.sqrt(2)
#: Cosine 0.71 to OPERATOR_2 but 0 to OPERATOR: max would pass them, the mean (0.35) does not.
STRANGER = _E[1]
FX, FY, CX, CY = 600.0, 600.0, 320.0, 240.0
H, W = 480, 640
#: Eyes, nose base, mouth corners on the face's plane, metres (x right, y down).
LANDMARKS_M = np.array(
    [[-0.032, -0.035, 0], [0.032, -0.035, 0], [0, 0, 0], [-0.025, 0.035, 0], [0.025, 0.035, 0]]
)
REAL_FACES = face_gate.faces


def _render(at_m, yaw=0.0, pitch=0.0, nose_m=0.025, noise_mm=0.0, hole=None):
    """Aligned depth (mm, H x W) of a face-sized surface `at_m` away, turned by yaw and
    pitch (degrees), whose nose stands `nose_m` proud (0: a print), and its five
    landmark pixels, as YuNet would place them."""
    a, b = np.radians(yaw), np.radians(pitch)
    turn = np.array([[np.cos(a), 0, np.sin(a)], [0, 1, 0], [-np.sin(a), 0, np.cos(a)]]) @ np.array(
        [[1, 0, 0], [0, np.cos(b), -np.sin(b)], [0, np.sin(b), np.cos(b)]]
    )
    centre, normal = np.array([0.0, 0.0, at_m]), turn @ [0.0, 0.0, -1.0]  # towards the camera
    pts = centre + LANDMARKS_M @ turn.T
    pts[2] += nose_m * normal
    marks = np.c_[FX * pts[:, 0] / pts[:, 2] + CX, FY * pts[:, 1] / pts[:, 2] + CY]
    v, u = np.mgrid[0:H, 0:W]
    rays = np.stack([(u - CX) / FX, (v - CY) / FY, np.ones((H, W))], axis=-1)
    z = (normal @ centre) / (rays @ normal)  # where each pixel's ray meets the plane
    sigma_px = 0.015 * FX / at_m
    r2 = (u - marks[2, 0]) ** 2 + (v - marks[2, 1]) ** 2
    z -= nose_m * -normal[2] * np.exp(-r2 / (2 * sigma_px**2))
    z += np.random.default_rng(0).normal(0.0, noise_mm / 1000, z.shape)
    depth = np.clip(z * 1000, 0, None).astype(np.uint16)
    if hole is not None:
        hu, hv = np.round(marks[hole]).astype(int)
        depth[hv - 8 : hv + 9, hu - 8 : hu + 9] = 0
    return depth, marks


def _patch_medians(depth, points, r):
    """The vision container's /depth_points, over a synthetic frame."""
    out = []
    for x, y in points:
        u, v = round(x), round(y)
        patch = depth[max(0, v - r) : v + r + 1, max(0, u - r) : u + r + 1]
        valid = np.sort(patch[patch > 0])
        out.append(valid[len(valid) // 2] / 1000 if 2 * len(valid) >= (2 * r + 1) ** 2 else None)
    return out


@pytest.fixture
def scene(tmp_path, monkeypatch):
    """A gated `pick`, an enrolled operator, and a camera the test fills in."""
    monkeypatch.setattr(face_gate, "FACE_DIR", tmp_path / "face")
    monkeypatch.setattr(face_gate, "GATED", frozenset({"pick", "get_state"}))
    face_gate.save_enrollment(np.stack([OPERATOR, OPERATOR_2]))
    s: dict = {"stamp": time.time(), "faces": [], "who": {}, "depth": {}, "frames": {}}
    monkeypatch.setattr(face_gate, "frame", lambda: ("img", s["stamp"]))
    monkeypatch.setattr(face_gate, "faces", lambda img: s["faces"])
    monkeypatch.setattr(face_gate, "feature", lambda img, row: s["who"][float(row[0])])

    def depth_points(stamp, points):
        depth = s["frames"][tuple(np.round(points, 1).ravel())]
        d = {"stamp": stamp, "fx": FX, "fy": FY, "cx": CX, "cy": CY}
        return {**d, "depth_m": _patch_medians(depth, points, face_gate.PATCH_R), **s["depth"]}

    monkeypatch.setattr(face_gate, "depth_points", depth_points)
    return s


def _face(s: dict, x: float, who: np.ndarray, width_m: float = 0.15, at_m: float = 1.0, **pose):
    """A face in view: `x` keys who it is, `pose` goes to _render."""
    depth, marks = _render(at_m, **pose)
    w = width_m * FX / at_m
    s["faces"].append(np.array([x, 100.0, w, w * 1.2, *marks.ravel(), 0.95], dtype=np.float32))
    s["who"][x] = who
    s["frames"][tuple(np.round(marks, 1).ravel())] = depth


@pytest.fixture(autouse=True)
def _no_check_left_running():
    """A check a test left wedged would make the next test's check "busy"."""
    yield
    assert face_gate._busy.acquire(timeout=2.0)
    face_gate._busy.release()


def _code(refusal: str | None) -> str | None:
    return None if refusal is None else refusal.split("(", 1)[1].split(")", 1)[0]


async def test_the_live_operator_passes(scene):
    _face(scene, 10, OPERATOR)
    assert await face_gate.refusal("pick") is None


async def test_a_stranger_is_not_the_operator(scene):
    """STRANGER matches one enrollment frame at 0.71: the mean over frames, not the max."""
    _face(scene, 10, STRANGER)
    assert _code(await face_gate.refusal("pick")) == face_gate.NOT_SEEN


@pytest.mark.parametrize("bad", [np.nan, 0.0])
async def test_a_nan_or_zero_enrollment_lets_nobody_in(scene, bad):
    """A NaN similarity is never below the threshold, so a stranger used to pass."""
    face_gate.enrollment_path().unlink()
    face_gate.save_enrollment(np.stack([OPERATOR, np.full(128, bad, np.float32)]))
    _face(scene, 10, STRANGER)
    assert _code(await face_gate.refusal("pick")) == face_gate.NOT_ENROLLED


async def test_a_nan_live_feature_is_no_match(scene):
    _face(scene, 10, np.full(128, np.nan, np.float32))
    assert _code(await face_gate.refusal("pick")) == face_gate.NOT_SEEN


async def test_no_face_is_not_seen(scene):
    assert _code(await face_gate.refusal("pick")) == face_gate.NOT_SEEN


async def test_two_faces_pass_only_when_one_is_the_live_operator(scene):
    _face(scene, 10, STRANGER)
    _face(scene, 300, STRANGER)
    assert _code(await face_gate.refusal("pick")) == face_gate.NOT_SEEN
    scene["who"][300] = OPERATOR
    assert await face_gate.refusal("pick") is None


@pytest.mark.parametrize("at_m", [0.5, 1.0, 1.5])
@pytest.mark.parametrize("yaw, pitch", [(0, 0), (20, 0), (0, 15), (-15, -10)])
async def test_a_real_face_passes_near_or_far_and_turned(scene, at_m, yaw, pitch):
    _face(scene, 10, OPERATOR, at_m=at_m, yaw=yaw, pitch=pitch)
    assert await face_gate.refusal("pick") is None


async def test_a_real_face_passes_through_far_range_sensor_noise(scene):
    _face(scene, 10, OPERATOR, at_m=1.5, noise_mm=8.4)
    assert await face_gate.refusal("pick") is None


@pytest.mark.parametrize("yaw, pitch", [(0, 0), (10, 0), (15, 0), (30, 0), (0, 15), (0, 30), (20, 20)])
async def test_a_life_size_print_is_flat_however_it_is_tilted(scene, yaw, pitch):
    """A box's p90 - p10 read a 10 degree tilt as 1.3 cm of relief; the landmark plane does not."""
    _face(scene, 10, OPERATOR, nose_m=0.0, yaw=yaw, pitch=pitch)
    assert _code(await face_gate.refusal("pick")) == face_gate.NOT_LIVE


async def test_a_print_is_flat_through_far_range_sensor_noise(scene):
    """8.4 mm of per-pixel noise at 1.5 m read as 2.1 cm of box relief."""
    _face(scene, 10, OPERATOR, nose_m=0.0, at_m=1.5, noise_mm=8.4)
    assert _code(await face_gate.refusal("pick")) == face_gate.NOT_LIVE


async def test_a_photo_on_a_phone_is_too_small(scene):
    _face(scene, 10, OPERATOR, width_m=0.05, at_m=0.5)
    assert _code(await face_gate.refusal("pick")) == face_gate.NOT_LIVE


async def test_past_max_range_is_not_live(scene):
    _face(scene, 10, OPERATOR, at_m=1.8)
    assert _code(await face_gate.refusal("pick")) == face_gate.NOT_LIVE


@pytest.mark.parametrize("landmark", range(5))
async def test_a_landmark_without_depth_is_not_live(scene, landmark):
    _face(scene, 10, OPERATOR, hole=landmark)
    assert _code(await face_gate.refusal("pick")) == face_gate.NOT_LIVE


async def test_a_vision_container_without_intrinsics_fails_closed(scene):
    _face(scene, 10, OPERATOR)
    scene["depth"] = {"cx": None}
    assert _code(await face_gate.refusal("pick")) == face_gate.UNAVAILABLE


async def test_a_refusal_carries_no_measurement_and_the_log_does(scene):
    """Any MCP caller reads the refusal: the reason code and fixed text, nothing measured."""
    from structlog.testing import capture_logs

    _face(scene, 10, OPERATOR, width_m=0.05, at_m=0.5)
    with capture_logs() as logs:
        refusal = await face_gate.refusal("pick")
    assert _code(refusal) == face_gate.NOT_LIVE
    assert "cm" not in refusal and "0.5" not in refusal
    assert "cm wide" in next(e for e in logs if e["event"] == "face_gate.refused")["detail"]


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


async def test_an_enrollment_in_a_folder_others_can_open_is_ignored(scene):
    _face(scene, 10, OPERATOR)
    os.chmod(face_gate.FACE_DIR, 0o755)
    assert _code(await face_gate.refusal("pick")) == face_gate.NOT_ENROLLED


async def test_an_enrollment_someone_else_owns_is_ignored(scene, monkeypatch):
    _face(scene, 10, OPERATOR)
    uid = os.geteuid()
    monkeypatch.setattr(face_gate.os, "geteuid", lambda: uid + 1)
    assert _code(await face_gate.refusal("pick")) == face_gate.NOT_ENROLLED


async def test_an_enrollment_symlink_is_ignored(scene):
    _face(scene, 10, OPERATOR)
    path = face_gate.enrollment_path()
    elsewhere = face_gate.FACE_DIR / "elsewhere.npy"
    path.rename(elsewhere)
    path.symlink_to(elsewhere)
    assert _code(await face_gate.refusal("pick")) == face_gate.NOT_ENROLLED


async def test_no_vision_container_fails_closed(scene, monkeypatch):
    def down():
        raise OSError("connection refused")

    monkeypatch.setattr(face_gate, "frame", down)
    assert _code(await face_gate.refusal("pick")) == face_gate.UNAVAILABLE


async def test_missing_models_fail_closed(scene, monkeypatch):
    import cv2  # noqa: F401 - up front, as main() does: a cold import outlasts TIMEOUT_S
    from structlog.testing import capture_logs

    monkeypatch.setattr(face_gate, "faces", REAL_FACES)
    monkeypatch.setattr(face_gate, "_models", None)
    assert not (face_gate.FACE_DIR / "models").exists()
    with capture_logs() as logs:
        assert _code(await face_gate.refusal("pick")) == face_gate.UNAVAILABLE
    assert "install_face_models" in next(e for e in logs if e["event"] == "face_gate.refused")["detail"]


async def test_a_slow_check_times_out_closed(scene, monkeypatch):
    monkeypatch.setattr(face_gate, "TIMEOUT_S", 0.05)
    monkeypatch.setattr(face_gate, "frame", lambda: (time.sleep(0.3), ("img", time.time()))[1])
    started = time.monotonic()
    assert _code(await face_gate.refusal("pick")) == face_gate.UNAVAILABLE
    assert time.monotonic() - started < 0.25


async def test_a_wedged_check_holds_no_shared_worker_and_queues_nothing(scene, monkeypatch):
    """asyncio.to_thread (damp's path) must not wait behind a check stuck on the vision
    container, and a burst of gated calls must not each wait TIMEOUT_S behind it."""
    asyncio.get_running_loop().set_default_executor(ThreadPoolExecutor(max_workers=1))
    monkeypatch.setattr(face_gate, "TIMEOUT_S", 0.1)
    release = threading.Event()
    monkeypatch.setattr(face_gate, "frame", lambda: (release.wait(5.0), ("img", time.time()))[1])
    try:
        assert _code(await face_gate.refusal("pick")) == face_gate.UNAVAILABLE  # still wedged
        started = time.monotonic()
        burst = await asyncio.gather(*(face_gate.refusal("pick") for _ in range(5)))
        assert {_code(r) for r in burst} == {face_gate.UNAVAILABLE}
        assert time.monotonic() - started < 0.05  # "busy" at once, not five timeouts
        assert await asyncio.wait_for(asyncio.to_thread(lambda: "free"), 0.5) == "free"
    finally:
        release.set()


async def test_gate_off_changes_nothing(scene, monkeypatch):
    monkeypatch.setattr(face_gate, "GATED", face_gate._parse(""))
    monkeypatch.setattr(face_gate, "frame", lambda: pytest.fail("the camera was read"))
    assert await face_gate.refusal("pick") is None


async def test_stop_skills_are_never_gated(scene, monkeypatch):
    assert face_gate._parse("pick, stop_everything,soft_stop,release_arm") == {"pick"}
    monkeypatch.setattr(face_gate, "GATED", face_gate.NEVER_GATED | {face_gate.CONFIRM})
    monkeypatch.setattr(face_gate, "frame", lambda: pytest.fail("the camera was read"))
    t2 = {"c3po": {"tier": "T2"}}
    for name in face_gate.NEVER_GATED:
        assert await face_gate.refusal(name, t2) is None


def _catalogue() -> dict:
    from bridge import mcp_server

    return {t.name: t.meta for t in mcp_server.mcp._tool_manager.list_tools()}


def test_the_confirm_class_gates_every_tool_that_needs_a_confirmation(monkeypatch):
    """By name the agent routed around a gated pick with move_arm + set_hand."""
    from bridge import safety

    monkeypatch.setattr(face_gate, "GATED", face_gate._parse("confirm"))
    tools = _catalogue()
    gated = {n for n, meta in tools.items() if face_gate.gated(n, meta)}
    assert gated == {n for n, meta in tools.items() if safety.needs_confirmation(meta)}
    assert {"pick", "place", "handover", "reach", "move_arm", "set_hand"} <= gated
    assert "get_state" not in gated and not gated & face_gate.NEVER_GATED
    monkeypatch.setattr(face_gate, "GATED", face_gate._parse("confirm, get_state"))
    assert face_gate.gated("get_state", tools["get_state"])


def test_a_name_that_is_no_tool_gates_the_whole_confirm_class(monkeypatch):
    """A misspelling used to gate nothing while the operator thought it guarded.

    It must fail closed without stopping the bridge: no bridge, no stop_everything.
    """
    from bridge import mcp_server

    tools = _catalogue()
    monkeypatch.setattr(face_gate, "GATED", face_gate._parse("confirm, get_state"))
    assert face_gate.check_config(tools) == []
    monkeypatch.setattr(face_gate, "GATED", face_gate._parse("get_state, pikc"))
    assert face_gate.check_config(tools) == ["pikc"]
    assert face_gate.GATED == {"get_state", face_gate.CONFIRM}
    assert face_gate.gated("pick", tools["pick"])
    assert not face_gate.gated("stop_everything", tools["stop_everything"])

    monkeypatch.setattr(face_gate, "GATED", face_gate._parse("pikc"))
    monkeypatch.setattr(face_gate, "warm", lambda: None)
    started = []

    async def served() -> None:
        started.append(True)

    monkeypatch.setattr(mcp_server, "_run_stdio", served)
    monkeypatch.setattr(mcp_server, "_run_streamable_http", served)
    mcp_server.main()
    assert started, "the bridge must still serve: it is the only stop_everything"
    assert face_gate.CONFIRM in face_gate.GATED


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


def _confirm(tool: str, args: dict) -> str:
    from bridge import mcp_server, safety

    key = mcp_server._canonical_args(mcp_server.mcp._tool_manager.get_tool(tool), args)
    safety.grant_confirmation(tool, key, via="console")
    return key


async def test_a_face_refusal_leaves_the_confirmation_for_one_retry(scene, monkeypatch):
    """The grant used to be spent before the face check, so a refusal burned it."""
    from bridge import mcp_server, safety

    monkeypatch.setattr(safety, "T2_CONFIRM", True)
    monkeypatch.setattr(face_gate, "GATED", face_gate._parse("confirm"))
    args = {"side": "right", "closure": 0.5}
    key = _confirm("set_hand", args)
    with pytest.raises(ToolError, match=face_gate.NOT_SEEN):
        await mcp_server.mcp.call_tool("set_hand", args)
    assert safety._consume_confirmation("set_hand", key, spend=False)
    _face(scene, 10, OPERATOR)
    await mcp_server.mcp.call_tool("set_hand", args)
    assert not safety._consume_confirmation("set_hand", key, spend=False)  # spent once


async def test_a_grant_cleared_during_the_face_check_refuses(scene, monkeypatch):
    """A PARAR clears every grant; one pressed while the face is checked still counts."""
    from bridge import mcp_server, safety

    monkeypatch.setattr(safety, "T2_CONFIRM", True)
    monkeypatch.setattr(face_gate, "GATED", face_gate._parse("confirm"))
    args = {"side": "right", "closure": 0.5}
    _confirm("set_hand", args)
    _face(scene, 10, OPERATOR)
    monkeypatch.setattr(
        face_gate, "frame", lambda: (safety.clear_confirmations("test"), ("img", scene["stamp"]))[1]
    )
    with pytest.raises(ToolError, match="needs an operator's confirmation"):
        await mcp_server.mcp.call_tool("set_hand", args)


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


def test_enrollment_name_is_owner_only_and_delete_removes_it(scene, monkeypatch):
    _face(scene, 10, OPERATOR)
    monkeypatch.setattr(face_enroll, "INTERVAL_S", 0.0)

    assert face_enroll.main(["--yes", "--frames", "2", "--name", " Oliver "]) == 0
    path = face_gate.name_path()
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert json.loads(path.read_text()) == {"name": "Oliver"}
    assert face_gate.enrolled_name() == "Oliver"
    os.chmod(path, 0o644)
    assert face_gate.enrolled_name() is None  # others could have written it
    os.chmod(path, 0o600)

    assert face_enroll.main(["--delete"]) == 0
    assert not path.exists() and face_gate.enrolled_name() is None


def test_re_enrolling_without_a_name_drops_the_old_one(scene, monkeypatch):
    _face(scene, 10, OPERATOR)
    monkeypatch.setattr(face_enroll, "INTERVAL_S", 0.0)
    face_gate.save_name("Someone else")
    assert face_enroll.main(["--yes", "--frames", "2"]) == 0
    assert not face_gate.name_path().exists()


def test_enrollment_refuses_two_faces_and_a_flat_face(scene, monkeypatch):
    face_gate.enrollment_path().unlink()
    monkeypatch.setattr(face_enroll, "INTERVAL_S", 0.0)
    _face(scene, 10, OPERATOR)
    _face(scene, 300, STRANGER)
    assert face_enroll.main(["--yes", "--frames", "2"]) == 1
    scene["faces"].clear()
    _face(scene, 10, OPERATOR, nose_m=0.0)
    assert face_enroll.main(["--yes", "--frames", "2"]) == 1
    assert not face_gate.enrollment_path().exists()


def test_enrollment_refuses_frames_of_two_people(scene, monkeypatch):
    face_gate.enrollment_path().unlink()
    monkeypatch.setattr(face_enroll, "INTERVAL_S", 0.0)
    _face(scene, 10, OPERATOR)
    who = iter([OPERATOR, OPERATOR_2, STRANGER])
    monkeypatch.setattr(face_gate, "feature", lambda img, row: next(who))
    assert face_enroll.main(["--yes", "--frames", "3"]) == 1
    assert not face_gate.enrollment_path().exists()


def test_saving_never_writes_through_a_planted_temp_file(scene, tmp_path):
    target = tmp_path / "victim"
    target.write_bytes(b"keep")
    face_gate.temp_path().symlink_to(target)
    with pytest.raises(face_gate.Unavailable, match="--delete"):
        face_gate.save_enrollment(np.stack([OPERATOR]))
    assert target.read_bytes() == b"keep"
    assert face_enroll.main(["--delete"]) == 0
    assert not face_gate.temp_path().is_symlink() and not face_gate.enrollment_path().exists()
    face_gate.save_enrollment(np.stack([OPERATOR]))
    assert face_gate.enrolled().shape == (1, 128)


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
    row = np.array([200, 100, 160, 192, *([0.0] * 10), 0.95], dtype=np.float32)
    row[4:14] = [250, 270, 310, 270, 280, 300, 255, 330, 305, 330]  # eyes, nose, mouth corners
    feat = face_gate.feature(noise, row)
    assert feat.shape == (128,)
    assert face_gate.similarity(feat, feat[None, :]) == pytest.approx(1.0, abs=1e-5)
    assert face_gate.similarity(face_gate.feature(other, row), feat[None, :]) < 1.0
