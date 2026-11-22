"""Camera-mount calibration (bridge/camera_cal.py, scripts/camera_calibrate.py) and the
C3PO_CAMERA_* override of kinematics.camera_to_torso."""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

from bridge import camera_cal as cal
from bridge import kinematics as k

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import camera_calibrate  # noqa: E402

#: The URDF d435_joint, which camera_to_torso used before the override existed.
URDF_XYZ = (0.0576235, 0.01753, 0.42987)
URDF_PITCH = 0.8307767239493009
#: The robot's ~/.c3po/perception.env on 2026-11-16 (pelvis frame).
ROBOT_ENV = {
    "C3PO_CAMERA_X_M": "0.05366",
    "C3PO_CAMERA_Y_M": "0.01753",
    "C3PO_CAMERA_Z_M": "0.47387",
    "C3PO_CAMERA_PITCH_DEG": "47.6",
}
#: A bent-knee stance like FSM 501's: hips and ankles pitched to keep the feet flat.
STANCE = {
    "left_hip_pitch_joint": -0.3,
    "left_knee_joint": 0.6,
    "left_ankle_pitch_joint": -0.3,
    "right_hip_pitch_joint": -0.3,
    "right_knee_joint": 0.6,
    "right_ankle_pitch_joint": -0.3,
}
#: Tape (ahead, left, height): a table, the floor, a shelf, at 0.4-1.1 m.
TAPES = [(0.45, -0.15, 0.80), (0.70, 0.05, 0.60), (1.10, 0.20, 0.05), (0.55, -0.25, 1.00)]


@pytest.fixture(autouse=True)
def _mount_env(monkeypatch):
    for name in k.CAMERA_ENV:
        monkeypatch.delenv(name, raising=False)
    k._camera_mount.cache_clear()
    yield
    k._camera_mount.cache_clear()


def _optical(p_torso, xyz, pitch_deg):
    """The cam_xyz a camera mounted at (xyz, pitch) in torso_link reads for a torso_link point."""
    rot = k._rpy(0.0, math.radians(pitch_deg), 0.0)
    d = k._sub(p_torso, xyz)
    link = [sum(rot[r][c] * d[r] for r in range(3)) for c in range(3)]
    return (-link[1], -link[2], link[0])


def _synth(true_xyz, true_pitch, q=None, torso_height_m=None):
    out = []
    for t in TAPES:
        m = cal.tape_to_torso(t, q=q, torso_height_m=torso_height_m)
        out.append((_optical(m, true_xyz, true_pitch), m))
    return out


def test_no_override_keeps_the_urdf_mount_exactly():
    xyz, rot = k._camera_mount()
    assert xyz == URDF_XYZ
    assert rot == k._rpy(0.0, URDF_PITCH, 0.0)
    assert k.camera_to_torso((0.1, -0.2, 1.0)) == k._add(URDF_XYZ, k._mv(rot, (1.0, -0.1, 0.2)))


def test_the_robots_perception_env_is_the_urdf_mount_in_the_pelvis(monkeypatch):
    p = (0.1, -0.2, 1.0)
    before = k.camera_to_torso(p)
    for name, v in ROBOT_ENV.items():
        monkeypatch.setenv(name, v)
    k._camera_mount.cache_clear()
    assert k.camera_to_torso(p) == pytest.approx(before, abs=1e-9)


def test_an_override_moves_camera_to_torso(monkeypatch):
    monkeypatch.setenv("C3PO_CAMERA_PITCH_DEG", "52")
    monkeypatch.setenv("C3PO_CAMERA_Z_M", "0.494")
    k._camera_mount.cache_clear()
    xyz, rot = k._camera_mount()
    assert xyz == pytest.approx((0.0576235, 0.01753, 0.45), abs=1e-9)  # 0.494 - 0.044 waist
    assert k.pitch_yaw_deg(rot) == pytest.approx((52.0, 0.0))


def test_the_standing_torso_height_comes_from_the_legs():
    straight = cal.torso_in_tape({})
    bent = cal.torso_in_tape(STANCE)
    assert straight[2] == pytest.approx(0.836, abs=0.002)  # pelvis 0.792 + waist 0.044
    assert 0.79 < bent[2] < straight[2] - 0.02  # thigh + shin ~0.6 m, cos(0.3) ~0.955
    assert abs(bent[1]) < 1e-9


@pytest.mark.parametrize("stance", [{}, STANCE])
def test_the_fit_recovers_a_mount_off_the_cad(stance):
    true_xyz, true_pitch = (0.07, 0.01753, 0.45), 52.0
    fit = cal.fit(_synth(true_xyz, true_pitch, q=stance))
    assert fit.fitted == ("pitch", "z", "x")
    assert fit.pitch_deg == pytest.approx(true_pitch, abs=0.01)
    assert fit.xyz == pytest.approx(true_xyz, abs=1e-4)
    assert max(math.dist((0, 0, 0), r) for r in fit.residuals_m) < 1e-6


def test_one_sample_fits_pitch_and_z_and_holds_x():
    true_xyz, true_pitch = (0.0576235, 0.01753, 0.45), 52.0
    fit = cal.fit(_synth(true_xyz, true_pitch, torso_height_m=0.80)[:1])
    assert fit.fitted == ("pitch", "z")
    assert fit.pitch_deg == pytest.approx(true_pitch, abs=0.01)
    assert fit.xyz[2] == pytest.approx(0.45, abs=1e-4)


def test_noisy_tape_still_lands_close():
    true_xyz, true_pitch = (0.0576235, 0.01753, 0.45), 52.0
    noise = [(0.01, 0, -0.01), (-0.01, 0, 0.01), (0.0, 0, 0.01), (0.01, 0, 0.0)]
    pairs = [
        (c, k._add(m, n))
        for (c, m), n in zip(_synth(true_xyz, true_pitch, q={}), noise, strict=True)
    ]
    fit = cal.fit(pairs)
    assert fit.pitch_deg == pytest.approx(true_pitch, abs=1.5)
    assert fit.xyz[2] == pytest.approx(0.45, abs=0.03)


def test_env_lines_round_trip_through_the_override(monkeypatch):
    true_xyz, true_pitch = (0.07, 0.01753, 0.45), 52.0
    fit = cal.fit(_synth(true_xyz, true_pitch, q={}))
    for name, v in zip(k.CAMERA_ENV, fit.env(), strict=True):
        monkeypatch.setenv(name, repr(v))
    k._camera_mount.cache_clear()
    for cam, m in _synth(true_xyz, true_pitch, q={}):
        assert k.camera_to_torso(cam) == pytest.approx(m, abs=1e-6)


def test_the_script_offline(tmp_path, capsys):
    samples = tmp_path / "cal.jsonl"
    cams = [c for c, _ in _synth((0.0576235, 0.01753, 0.45), 52.0, torso_height_m=0.80)]
    for cam, tape in zip(cams, TAPES, strict=True):
        argv = [
            "--samples",
            str(samples),
            "--cam",
            *map(str, cam),
            "--torso-height-m",
            "0.80",
            "--ahead-m",
            str(tape[0]),
            "--left-m",
            str(tape[1]),
            "--height-m",
            str(tape[2]),
        ]
        assert camera_calibrate.main(argv) == 0
    out = capsys.readouterr().out
    assert "C3PO_CAMERA_PITCH_DEG=52.00" in out
    assert "C3PO_CAMERA_Z_M=0.49400" in out  # 0.45 in torso_link + the waist's 0.044
    assert "C3PO_PICK_Z_OFFSET_M=0" in out
    assert camera_calibrate.main(["--samples", str(samples), "--reset"]) == 0
    assert not samples.exists()
