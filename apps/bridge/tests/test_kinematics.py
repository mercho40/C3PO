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
