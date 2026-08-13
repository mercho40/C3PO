"""Tests for g1_protocol.can_transition()/is_locomotion_state() — pure logic,
zero dependencies, and until now zero test coverage despite being exactly
what docs/SPEC.md §16.5's FSM diagram claims to represent. Written by
reading can_transition()'s actual rules line by line and checking the
diagram's claims are true of the code, not just true of my own reading of
the code when I drew it.
"""

from __future__ import annotations

from bridge.sdk.g1_protocol import Mode, can_transition, is_locomotion_state

DAMP_TARGETS = (Mode.ZERO_TORQUE, Mode.PREPARATION, Mode.SQUAT_UP, Mode.LIE_UP)
PREPARATION_TARGETS = (Mode.DAMP, Mode.WALK, Mode.WALK_WAIST, Mode.RUN)


def test_damp_can_reach_all_four_gated_targets():
    for target in DAMP_TARGETS:
        assert can_transition(Mode.DAMP, target) is True


def test_only_damp_can_reach_the_four_gated_targets():
    non_damp_states = (Mode.ZERO_TORQUE, Mode.PREPARATION, Mode.WALK, Mode.RUN, Mode.SQUAT, 703)  # 703=Seating-ish
    for current in non_damp_states:
        for target in DAMP_TARGETS:
            if current == target:
                continue  # self-transition isn't the thing being tested here
            assert can_transition(current, target) is False, f"{current} -> {target} should be rejected"


def test_zero_torque_only_accepts_damp():
    assert can_transition(Mode.ZERO_TORQUE, Mode.DAMP) is True
    assert can_transition(Mode.ZERO_TORQUE, Mode.WALK) is False
    assert can_transition(Mode.ZERO_TORQUE, Mode.SQUAT) is False


def test_squat_only_accepts_damp():
    assert can_transition(Mode.SQUAT, Mode.DAMP) is True
    assert can_transition(Mode.SQUAT, Mode.WALK) is False


def test_preparation_fans_out_to_locomotion_and_damp_only():
    for target in PREPARATION_TARGETS:
        assert can_transition(Mode.PREPARATION, target) is True
    # Preparation can't jump straight to ZeroTorque/SquatUp/LieUp -- must go
    # through Damp first, per the "only Damp" rule for those three.
    for target in (Mode.ZERO_TORQUE, Mode.SQUAT_UP, Mode.LIE_UP):
        assert can_transition(Mode.PREPARATION, target) is False


def test_locomotion_states_can_return_to_damp():
    # damp() skill's own docstring claims this; verify the FSM guard agrees.
    for current in (Mode.WALK, Mode.WALK_WAIST, Mode.RUN):
        assert can_transition(current, Mode.DAMP) is True


def test_locomotion_states_not_gated_beyond_the_damp_target_rule():
    # Nothing in can_transition() restricts leaving Walk/Run for a target
    # that isn't one of the four Damp-gated ones -- confirming the diagram's
    # "not further restricted" note is actually true of the code, not just
    # asserted in a comment.
    assert can_transition(Mode.WALK, Mode.RUN) is True
    assert can_transition(Mode.RUN, Mode.WALK_WAIST) is True


def test_is_locomotion_state_covers_walk_walk_waist_run_and_alt_run():
    assert is_locomotion_state(Mode.WALK) is True
    assert is_locomotion_state(Mode.WALK_WAIST) is True
    assert is_locomotion_state(Mode.RUN) is True
    assert is_locomotion_state(802) is True  # alternative Run index, per g1_protocol's own comment


def test_is_locomotion_state_false_for_non_locomotion_modes():
    assert is_locomotion_state(Mode.DAMP) is False
    assert is_locomotion_state(Mode.PREPARATION) is False
    assert is_locomotion_state(Mode.SQUAT_UP) is False
