"""Tests for the teleop wire protocol.

This parser is the only thing standing between a browser on the operator's
head and a 35-slot motor command array, so the tests here are mostly about
what it *refuses*. The happy path is one test; the rest are the ways a frame
can be wrong.
"""

from __future__ import annotations

import json

import pytest

from bridge.teleop.protocol import (
    MAX_FRAME_BYTES,
    MAX_HAND_DISTANCE_M,
    PROTOCOL_VERSION,
    FrameError,
    parse_frame,
)


def _frame(**overrides):
    base = {
        "v": PROTOCOL_VERSION,
        "seq": 1,
        "t": 100.0,
        "enabled": True,
        "walk": 0.0,
        "arms": False,
        "head": {"yaw": 0.1, "pos": [0.0, 1.6, 0.0]},
        "hands": {},
    }
    base.update(overrides)
    return json.dumps(base)


def test_parses_a_minimal_valid_frame():
    frame = parse_frame(_frame())

    assert frame.seq == 1
    assert frame.enabled is True
    assert frame.head_yaw == pytest.approx(0.1)
    assert frame.head_position == (0.0, 1.6, 0.0)
    assert frame.left is None and frame.right is None
    assert frame.any_hand_tracked is False


def test_parses_a_tracked_hand():
    frame = parse_frame(
        _frame(hands={"right": {"tracked": True, "pos": [0.3, 1.3, -0.2], "quat": [0, 0, 0, 1], "grip": 0.4}})
    )

    assert frame.right is not None
    assert frame.right.position == (0.3, 1.3, -0.2)
    assert frame.right.grip == pytest.approx(0.4)
    assert frame.left is None


def test_untracked_hand_is_none_not_an_error():
    # A hand leaving the tracking volume is normal operation. It must not kill
    # the frame, because that would drop the other arm and the walk axis too.
    frame = parse_frame(_frame(hands={"left": {"tracked": False}}))
    assert frame.left is None


def test_hand_beyond_reach_is_treated_as_untracked():
    far = [0.0, 1.6 + MAX_HAND_DISTANCE_M + 0.5, 0.0]
    frame = parse_frame(_frame(hands={"right": {"tracked": True, "pos": far, "quat": [0, 0, 0, 1]}}))
    assert frame.right is None


def test_rejects_unknown_protocol_version():
    with pytest.raises(FrameError, match="unsupported protocol version"):
        parse_frame(_frame(v=99))


def test_rejects_oversized_frame():
    with pytest.raises(FrameError, match="too large"):
        parse_frame("x" * (MAX_FRAME_BYTES + 1))


def test_rejects_non_json():
    with pytest.raises(FrameError, match="not valid JSON"):
        parse_frame("{not json")


def test_rejects_json_that_is_not_an_object():
    with pytest.raises(FrameError, match="expected a JSON object"):
        parse_frame("[1, 2, 3]")


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-Infinity"])
def test_rejects_non_finite_head_yaw(bad):
    # json.loads accepts these by default, they survive every arithmetic
    # operation, and NaN compares false against every clamp bound -- so a NaN
    # yaw would pass straight through the deadzone check into a motor command.
    raw = _frame().replace('"yaw": 0.1', f'"yaw": {bad}')
    with pytest.raises(FrameError, match="not finite"):
        parse_frame(raw)


def test_rejects_non_finite_hand_position():
    raw = _frame(
        hands={"right": {"tracked": True, "pos": [0.0, 0.0, 0.0], "quat": [0, 0, 0, 1]}}
    ).replace('"pos": [0.0, 0.0, 0.0]', '"pos": [NaN, 0.0, 0.0]')
    with pytest.raises(FrameError, match="not finite"):
        parse_frame(raw)


def test_rejects_non_unit_quaternion():
    # A half-length quaternion builds a rotation matrix that scales as well as
    # rotates, which reads downstream as a real but wrong hand direction.
    with pytest.raises(FrameError, match="not a unit quaternion"):
        parse_frame(
            _frame(hands={"right": {"tracked": True, "pos": [0.3, 1.3, -0.2], "quat": [0, 0, 0, 0.5]}})
        )


def test_normalises_a_slightly_off_unit_quaternion():
    frame = parse_frame(
        _frame(hands={"right": {"tracked": True, "pos": [0.3, 1.3, -0.2], "quat": [0, 0, 0, 1.05]}})
    )
    assert frame.right is not None
    norm = sum(c * c for c in frame.right.orientation) ** 0.5
    assert norm == pytest.approx(1.0)


def test_rejects_grip_out_of_range():
    with pytest.raises(FrameError, match="out of range"):
        parse_frame(
            _frame(hands={"right": {"tracked": True, "pos": [0.3, 1.3, -0.2], "quat": [0, 0, 0, 1], "grip": 1.5}})
        )


def test_rejects_yaw_outside_pi():
    with pytest.raises(FrameError, match=r"outside \[-pi, pi\]"):
        parse_frame(_frame(head={"yaw": 4.0, "pos": [0, 1.6, 0]}))


def test_rejects_negative_seq():
    with pytest.raises(FrameError, match="negative"):
        parse_frame(_frame(seq=-1))


def test_missing_enabled_reads_as_released():
    # The dead-man has to fail closed. A client that forgets the field, or a
    # hand-crafted frame that omits it, must not read as "operator is holding".
    payload = json.loads(_frame())
    del payload["enabled"]
    assert parse_frame(json.dumps(payload)).enabled is False


def test_missing_arms_reads_as_not_requested():
    payload = json.loads(_frame())
    del payload["arms"]
    assert parse_frame(json.dumps(payload)).arms is False


def test_walk_axis_is_clamped_not_rejected():
    # Clamping keeps the head yaw and both arms in the same frame usable.
    assert parse_frame(_frame(walk=5.0)).walk == 1.0
    assert parse_frame(_frame(walk=-5.0)).walk == -1.0
