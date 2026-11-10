"""The numbers g1_odom_tf hands Nav2, on a laptop with no ROS.

`odom_math` is stdlib-only so these can run here: every one of them, if wrong,
still produces a plausible /odom, and the symptom is "the controller is
unstable" rather than an error.
"""

from __future__ import annotations

import math

import pytest
from c3po_perception import odom_math
from conftest import NAV_PKG


def _yaw_quat(yaw: float) -> tuple[float, float, float, float]:
    return (0.0, 0.0, math.sin(yaw / 2), math.cos(yaw / 2))


class TestFlatPose:
    def test_the_inverted_mount_flips_y_and_yaw(self):
        # camera_init is upside down (z down), so a left turn there is a right
        # turn on the floor, and its +y is the robot's -y.
        x, y, yaw = odom_math.flat_pose(1.0, 2.0, *_yaw_quat(0.3))
        assert (x, y) == (1.0, -2.0)
        assert yaw == pytest.approx(-0.3)

    def test_a_pitching_torso_does_not_rotate_the_footprint(self):
        # Yaw 0.5 then a 0.2 rad pitch, as a step would add.
        hy, hp = 0.25, 0.1
        q = (
            -math.sin(hy) * math.sin(hp),
            math.cos(hy) * math.sin(hp),
            math.sin(hy) * math.cos(hp),
            math.cos(hy) * math.cos(hp),
        )
        _x, _y, yaw = odom_math.flat_pose(0.0, 0.0, *q)
        assert yaw == pytest.approx(-0.5)

    def test_rpy_round_trip(self):
        q = odom_math.rpy_to_quat(0.1, -0.2, 2.5)
        assert odom_math.quat_to_rpy(*q) == pytest.approx((0.1, -0.2, 2.5))


def test_fastlio_twist_flips_y_and_z():
    # body is roll-pi from base_link.
    assert odom_math.fastlio_twist(0.4, 0.1, 0.2) == [0.4, -0.1, -0.2]


class TestDifferentiatedTwist:
    def test_the_first_sample_keeps_the_estimate(self):
        v = [0.1, 0.2, 0.3]
        assert odom_math.differentiated_twist(v, None, 1.0, 0.0, 0.0, 0.0) == v

    def test_velocity_is_in_the_robot_frame_not_the_world(self):
        # Facing +y and walking +y at 1 m/s is FORWARD: vx, not vy.
        yaw = math.pi / 2
        v = odom_math.differentiated_twist(
            [0.0, 0.0, 0.0], (0.0, 0.0, 0.0, yaw), 0.1, 0.0, 0.1, yaw
        )
        assert v[0] == pytest.approx(0.35 * 1.0)
        assert v[1] == pytest.approx(0.0, abs=1e-12)
        assert v[2] == 0.0

    def test_yaw_rate_unwraps_across_pi(self):
        # 3.1 -> -3.1 is a 0.083 rad left turn, not a 6.2 rad right one.
        v = odom_math.differentiated_twist(
            [0.0, 0.0, 0.0], (0.0, 0.0, 0.0, 3.1), 0.1, 0.0, 0.0, -3.1
        )
        assert v[2] == pytest.approx(0.35 * (2 * math.pi - 6.2) / 0.1)

    def test_a_long_gap_zeroes_the_estimate(self):
        v = odom_math.differentiated_twist([0.5, 0.0, 0.2], (0.0, 0.0, 0.0, 0.0), 0.5, 1, 0, 0)
        assert v == [0.0, 0.0, 0.0]

    def test_two_samples_in_one_millisecond_keep_the_estimate(self):
        v = [0.5, 0.0, 0.2]
        assert odom_math.differentiated_twist(v, (0.0, 0.0, 0.0, 0.0), 0.0005, 1, 0, 0) == v


def test_the_node_uses_this_module_not_a_copy():
    """g1_odom_tf imports rclpy, so read it: the maths must not drift back inline."""
    src = (NAV_PKG / "c3po_perception" / "g1_odom_tf.py").read_text()
    assert "from c3po_perception.odom_math import" in src
    assert "def quat_to_rpy" not in src
    assert "math.atan2" not in src
