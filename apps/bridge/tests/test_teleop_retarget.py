"""Tests for the wrist-pose -> joint-angle mapping.

Pure geometry, so these can assert real numbers rather than "it ran". The
cases are chosen to pin the properties an operator would actually notice:
arm down is neutral, arm forward is a forward pitch, arm out to the side is
abduction, extending straightens the elbow, and turning your head does not
sweep the arms.

They deliberately do NOT assert the robot's joint *signs* -- those are
unverified (`retarget.JOINT_SIGNS`) and settling them is a job for
`scripts/arm_sign_check.py` on real hardware. What is asserted is that the
sign table is applied consistently and that mirroring behaves like a body.
"""

from __future__ import annotations

import math

import pytest

from bridge.teleop import retarget
from bridge.teleop.protocol import HandSample
from bridge.teleop.retarget import (
    DEFAULT_ARM_LENGTH_M,
    JOINT_NAMES,
    LIMITS,
    calibrate_arm_length,
    quat_to_matrix,
    retarget_arm,
    shoulder_origin,
)

IDENTITY_QUAT = (0.0, 0.0, 0.0, 1.0)
HEAD = (0.0, 1.60, 0.0)


def hand_at(x: float, y: float, z: float, grip: float = 0.0) -> HandSample:
    return HandSample(position=(x, y, z), orientation=IDENTITY_QUAT, grip=grip)


def test_joint_tables_agree():
    # A mismatch here is an off-by-one that swaps two joints' limits silently.
    assert tuple(LIMITS.keys()) == JOINT_NAMES
    for side in ("left", "right"):
        assert tuple(retarget.JOINT_SIGNS[side].keys()) == JOINT_NAMES


def test_shoulder_origin_is_below_and_lateral_to_the_head():
    right = shoulder_origin(HEAD, 0.0, "right")
    left = shoulder_origin(HEAD, 0.0, "left")

    assert right[1] < HEAD[1] and left[1] < HEAD[1]
    assert right[0] == pytest.approx(retarget.SHOULDER_HALF_WIDTH_M)
    assert left[0] == pytest.approx(-retarget.SHOULDER_HALF_WIDTH_M)


def test_shoulder_origin_rotates_with_head_yaw():
    # Turned 90 degrees left, the right shoulder should have swung round to
    # where "behind" was, not stayed put.
    turned = shoulder_origin(HEAD, math.pi / 2, "right")
    assert turned[0] == pytest.approx(0.0, abs=1e-9)
    assert turned[2] == pytest.approx(-retarget.SHOULDER_HALF_WIDTH_M)


def test_arm_hanging_straight_down_is_neutral():
    origin = shoulder_origin(HEAD, 0.0, "right")
    hand = hand_at(origin[0], origin[1] - DEFAULT_ARM_LENGTH_M, origin[2])

    angles = retarget_arm("right", HEAD, 0.0, hand)

    assert angles.shoulder_pitch == pytest.approx(0.0, abs=1e-6)
    assert angles.shoulder_roll == pytest.approx(0.0, abs=1e-6)
    assert angles.elbow == pytest.approx(0.0, abs=1e-6)


def test_arm_straight_out_in_front_pitches_ninety_degrees():
    origin = shoulder_origin(HEAD, 0.0, "right")
    # -Z is forward in WebXR.
    hand = hand_at(origin[0], origin[1], origin[2] - DEFAULT_ARM_LENGTH_M)

    angles = retarget_arm("right", HEAD, 0.0, hand)

    # Sign-applied, like the roll test below. `shoulder_pitch` was MEASURED as
    # -1 on the robot (2026-08-20), so "reach forward" is a negative joint
    # angle on this body — asserting the raw +pi/2 here would pin the
    # assumption rather than the measurement.
    expected = (math.pi / 2) * retarget.JOINT_SIGNS["right"]["shoulder_pitch"]
    assert angles.shoulder_pitch == pytest.approx(expected, abs=1e-6)
    assert angles.elbow == pytest.approx(0.0, abs=1e-6)


def test_arm_out_to_the_side_is_abduction_and_mirrors_between_arms():
    for side, direction in (("right", 1.0), ("left", -1.0)):
        origin = shoulder_origin(HEAD, 0.0, side)
        hand = hand_at(origin[0] + direction * DEFAULT_ARM_LENGTH_M, origin[1], origin[2])
        angles = retarget_arm(side, HEAD, 0.0, hand)
        # Clamped to the 90-degree envelope, and opposite in robot joint space
        # because the two arms share a body-frame roll axis.
        expected = math.radians(90) * retarget.JOINT_SIGNS[side]["shoulder_roll"]
        assert angles.shoulder_roll == pytest.approx(expected, abs=1e-6)


def test_elbow_bends_as_the_operator_pulls_their_hand_in():
    origin = shoulder_origin(HEAD, 0.0, "right")
    fully_extended = retarget_arm(
        "right", HEAD, 0.0, hand_at(origin[0], origin[1] - DEFAULT_ARM_LENGTH_M, origin[2])
    )
    half_extended = retarget_arm(
        "right", HEAD, 0.0, hand_at(origin[0], origin[1] - DEFAULT_ARM_LENGTH_M / 2, origin[2])
    )

    assert fully_extended.elbow == pytest.approx(0.0, abs=1e-6)
    # acos(0.5) * 2 = 120 degrees, clamped by LIMITS to 110.
    assert half_extended.elbow == pytest.approx(LIMITS["elbow"][1])
    assert half_extended.elbow > fully_extended.elbow


def test_turning_the_head_does_not_sweep_the_arms():
    # The operator holds their arm out in front and turns on the spot. Their
    # arm turns with their body, so the joint angles must not change -- head
    # yaw is spoken for as the locomotion input.
    yaw = math.radians(37)
    origin_straight = shoulder_origin(HEAD, 0.0, "right")
    straight = retarget_arm(
        "right", HEAD, 0.0, hand_at(origin_straight[0], origin_straight[1], origin_straight[2] - 0.5)
    )

    origin_turned = shoulder_origin(HEAD, yaw, "right")
    # The same 0.5 m forward, in the turned frame.
    forward = (-math.sin(yaw) * 0.5, 0.0, -math.cos(yaw) * 0.5)
    turned = retarget_arm(
        "right",
        HEAD,
        yaw,
        hand_at(origin_turned[0] + forward[0], origin_turned[1] + forward[1], origin_turned[2] + forward[2]),
    )

    assert turned.shoulder_pitch == pytest.approx(straight.shoulder_pitch, abs=1e-6)
    assert turned.shoulder_roll == pytest.approx(straight.shoulder_roll, abs=1e-6)
    assert turned.elbow == pytest.approx(straight.elbow, abs=1e-6)


def test_every_output_is_inside_the_software_envelope():
    # Sweep a hemisphere of hand positions, including ones well outside human
    # reach, and assert nothing escapes the clamp in either sign.
    origin = shoulder_origin(HEAD, 0.0, "right")
    for pitch_deg in range(-180, 181, 15):
        for yaw_deg in range(-180, 181, 15):
            p, y = math.radians(pitch_deg), math.radians(yaw_deg)
            d = (math.cos(p) * math.sin(y), math.sin(p), -math.cos(p) * math.cos(y))
            hand = hand_at(origin[0] + d[0], origin[1] + d[1], origin[2] + d[2])
            angles = retarget_arm("right", HEAD, 0.0, hand, include_wrist=True)
            for name, value in zip(JOINT_NAMES, angles.as_tuple(), strict=True):
                low, high = LIMITS[name]
                sign = retarget.JOINT_SIGNS["right"][name]
                lo, hi = sorted((low * sign, high * sign))
                assert lo - 1e-9 <= value <= hi + 1e-9, f"{name} escaped: {value}"


def test_hand_at_the_shoulder_folds_rather_than_amplifying_noise():
    origin = shoulder_origin(HEAD, 0.0, "right")
    hand = hand_at(origin[0] + 0.01, origin[1] + 0.01, origin[2])

    angles = retarget_arm("right", HEAD, 0.0, hand)

    assert angles.shoulder_pitch == 0.0
    assert angles.shoulder_roll == 0.0
    assert angles.elbow == pytest.approx(LIMITS["elbow"][1])


def test_wrist_is_off_by_default():
    origin = shoulder_origin(HEAD, 0.0, "right")
    # Arm held out in front (not down -- see the degenerate-case test below),
    # hand rotated 90 degrees about Z relative to the forearm.
    rotated = HandSample(
        position=(origin[0], origin[1], origin[2] - 0.5),
        orientation=(0.0, 0.0, math.sin(math.pi / 4), math.cos(math.pi / 4)),
        grip=0.0,
    )

    without = retarget_arm("right", HEAD, 0.0, rotated)
    assert (without.wrist_roll, without.wrist_pitch, without.wrist_yaw) == (0.0, 0.0, 0.0)

    with_wrist = retarget_arm("right", HEAD, 0.0, rotated, include_wrist=True)
    assert any(abs(v) > 1e-6 for v in (with_wrist.wrist_roll, with_wrist.wrist_pitch, with_wrist.wrist_yaw))


def test_wrist_mapping_is_neutral_when_the_arm_hangs_down():
    # Straight down is parallel to the world-up reference used to build the
    # forearm frame, so no perpendicular basis exists and the mapping has
    # nothing to measure against. Returning zero there is the right answer as
    # well as the safe one: an arm at rest should have a neutral wrist, not
    # whatever an ill-conditioned cross product happens to produce.
    origin = shoulder_origin(HEAD, 0.0, "right")
    down = HandSample(
        position=(origin[0], origin[1] - 0.5, origin[2]),
        orientation=(0.0, 0.0, math.sin(math.pi / 4), math.cos(math.pi / 4)),
        grip=0.0,
    )

    angles = retarget_arm("right", HEAD, 0.0, down, include_wrist=True)

    assert (angles.wrist_roll, angles.wrist_pitch, angles.wrist_yaw) == (0.0, 0.0, 0.0)


def test_calibration_measures_reach_and_stays_in_human_range():
    origin = shoulder_origin(HEAD, 0.0, "right")
    measured = calibrate_arm_length(HEAD, 0.0, hand_at(origin[0] + 0.7, origin[1], origin[2]), "right")
    assert measured == pytest.approx(0.7)

    # A tracking spike must not scale every later elbow angle.
    absurd = calibrate_arm_length(HEAD, 0.0, hand_at(origin[0] + 5.0, origin[1], origin[2]), "right")
    assert absurd == retarget.MAX_ARM_LENGTH_M


def test_calibrated_reach_normalises_the_elbow_across_operators():
    # A short-armed and a long-armed operator both fully extended should both
    # get a straight robot elbow. That is the whole point of measuring reach
    # rather than assuming metres.
    for arm_length in (0.45, 0.85):
        origin = shoulder_origin(HEAD, 0.0, "right")
        hand = hand_at(origin[0], origin[1] - arm_length, origin[2])
        angles = retarget_arm("right", HEAD, 0.0, hand, arm_length_m=arm_length)
        assert angles.elbow == pytest.approx(0.0, abs=1e-6)


def test_quat_to_matrix_is_a_rotation():
    q = (0.1, -0.3, 0.2, 0.927)
    norm = sum(c * c for c in q) ** 0.5
    q = tuple(c / norm for c in q)  # type: ignore[assignment]
    m = quat_to_matrix(q)

    # Orthonormal rows, and determinant +1 (a rotation, not a reflection).
    for row in m:
        assert sum(c * c for c in row) == pytest.approx(1.0)
    det = (
        m[0][0] * (m[1][1] * m[2][2] - m[1][2] * m[2][1])
        - m[0][1] * (m[1][0] * m[2][2] - m[1][2] * m[2][0])
        + m[0][2] * (m[1][0] * m[2][1] - m[1][1] * m[2][0])
    )
    assert det == pytest.approx(1.0)



def test_the_measured_signs_are_the_ones_in_the_table():
    """Pin what `arm_sign_check.py` actually measured on the robot.

    Not a tautology: these four were read off the physical arm at FSM 4 on
    2026-08-20, and `shoulder_pitch` came back OPPOSITE to the assumption the
    file shipped with. If someone later "tidies" the table back to all-ones,
    this fails and says why — the arm mirror would otherwise swing backward on
    every reach forward, which from inside a headset reads as the whole feature
    being broken.
    """
    right = retarget.JOINT_SIGNS["right"]
    assert right["shoulder_pitch"] == -1.0, "measured on hardware: reaching forward is NEGATIVE"
    assert right["shoulder_roll"] == 1.0
    assert right["shoulder_yaw"] == 1.0
    assert right["elbow"] == 1.0


def test_left_mirrors_right_where_bilateral_symmetry_says_it_must():
    # Roll and yaw share body-frame axes, so one physical motion has opposite
    # signs on the two arms. Pitch and elbow do not, so they match. The left
    # arm has NOT been measured — this pins the inference, and is exactly what
    # `arm_sign_check.py --side left` would replace with evidence.
    left, right = retarget.JOINT_SIGNS["left"], retarget.JOINT_SIGNS["right"]
    assert left["shoulder_pitch"] == right["shoulder_pitch"]
    assert left["elbow"] == right["elbow"]
    assert left["shoulder_roll"] == -right["shoulder_roll"]
    assert left["shoulder_yaw"] == -right["shoulder_yaw"]
