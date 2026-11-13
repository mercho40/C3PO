"""bridge.kinematics: the URDF reads as the G1 we know, and IK inverts FK."""

from __future__ import annotations

import math
import random

import pytest

from bridge import kinematics as k


@pytest.mark.parametrize("side", ["left", "right"])
def test_arm_chain_is_the_seven_joints_in_motor_order(side):
    names = [j.name for j in k.arm_chain(side)]
    assert names == [f"{side}_{s}_joint" for s in k._SUFFIXES]


def test_zero_pose_matches_the_g1_shape():
    # URDF zero: upper arm hanging, elbow bent 90 degrees, forearm forward.
    r = k.forward("right", [0.0] * 7)
    l_ = k.forward("left", [0.0] * 7)
    assert r.position[0] > 0.2 and r.position[1] < -0.1  # in front, on the right
    assert l_.position[1] == pytest.approx(-r.position[1], abs=1e-3)  # mirror, to CAD rounding
    assert r.pointing == pytest.approx((1.0, 0.0, 0.0), abs=1e-3)


def test_ik_inverts_fk_inside_the_limits():
    rng = random.Random(7)
    for _ in range(30):
        q = [rng.uniform(lo, hi) * 0.6 for lo, hi in k.limits("right")]
        hp = k.forward("right", q)
        sol = k.solve("right", hp.position, hp.pointing)
        assert sol.reached
        got = k.forward("right", sol.q)
        assert math.dist(got.position, hp.position) < k.POS_TOL_M
        for v, (lo, hi) in zip(sol.q, k.limits("right"), strict=True):
            assert lo - 1e-9 <= v <= hi + 1e-9


def test_up_fixes_the_roll_about_the_fingers():
    # Any pose's own roll is reachable: ask for it with `up` and get it back, from elsewhere.
    rng = random.Random(3)
    for _ in range(10):
        q = [rng.uniform(lo, hi) * 0.5 for lo, hi in k.limits("right")]
        hp = k.forward("right", q)
        y = k._cross(hp.joint_axes[6], hp.pointing)
        want = [sum(a[i] * w for a, w in zip((hp.pointing, y, hp.joint_axes[6]), k.THUMB_AXIS))
                for i in range(3)]  # fmt: skip
        sol = k.solve("right", hp.position, hp.pointing, up=want)
        assert sol.reached, sol
        got = k.forward("right", sol.q)
        got_y = k._cross(got.joint_axes[6], got.pointing)
        thumb = [sum(a[i] * w for a, w in zip((got.pointing, got_y, got.joint_axes[6]), k.THUMB_AXIS))
                 for i in range(3)]  # fmt: skip
        assert math.acos(min(1.0, sum(a * b for a, b in zip(thumb, want)))) < k.DIR_TOL_RAD


def test_out_of_reach_is_reported_not_faked():
    sol = k.solve("right", (0.9, -0.2, 0.0))
    assert not sol.reached
    assert sol.position_error_m > 0.2


def test_camera_is_tilted_down_as_in_the_cad():
    lens = k.camera_to_torso((0.0, 0.0, 0.0))
    ahead = k.camera_to_torso((0.0, 0.0, 1.0))
    tilt = math.degrees(math.atan2(lens[2] - ahead[2], ahead[0] - lens[0]))
    assert tilt == pytest.approx(47.6, abs=0.5)
    # Optical +x is image-right, which is the robot's right (-y).
    assert k.camera_to_torso((0.1, 0.0, 0.0))[1] < lens[1]



@pytest.mark.parametrize("side", ["left", "right"])
@pytest.mark.parametrize(
    "q", [[0.3, -0.2, 0.4, 1.0, 0.2, -0.3, 0.5], [-1.2, 0.5, -0.3, 0.4, 0.0, 0.6, -0.2]]
)
def test_gravity_torques_are_the_slope_of_the_arms_potential_energy(side, q):
    # A held arm needs tau_i = dV/dq_i, V = g * sum(m * z_com), the hand included.
    def energy(qq):
        return sum(m * k.GRAVITY_M_S2 * com[2] for m, com, _ in k.masses_and_coms(side, qq))

    h = 1e-6
    slope = [
        (energy([*q[:i], q[i] + h, *q[i + 1 :]]) - energy([*q[:i], q[i] - h, *q[i + 1 :]])) / (2 * h)
        for i in range(7)
    ]
    assert k.gravity_torques(side, q) == pytest.approx(slope, abs=1e-6)


def test_the_arm_held_out_in_front_needs_the_shoulder_to_lift_it(monkeypatch):
    # Shoulder pitch -90°, elbow 90° (its zero is bent): the arm straight out in front, the
    # hand ~0.45 m out. Lifting is negative pitch; the 0.5 kg hand alone is ~2 N·m of it.
    out_front = [-math.pi / 2, 0, 0, math.pi / 2, 0, 0, 0]
    assert k.forward("right", out_front).position[0] > 0.4
    with_hand = k.gravity_torques("right", out_front)[0]
    assert -10.0 < with_hand < -5.0
    monkeypatch.setattr(k, "HAND_MASS_KG", 0.0)
    assert k.gravity_torques("right", out_front)[0] - with_hand == pytest.approx(2.1, abs=0.3)
