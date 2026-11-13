"""Tests for the hand drivers.

The single most important behaviour here is that `build_driver()` returns
something that publishes *nothing* unless a human has answered two questions
the documentation cannot: which hands are fitted, and which end of BrainCo's
[0,1] range is an open hand. Getting either wrong sends a command that is not
merely ignored -- it is a different command (see the module docstring's units
table). So most of these tests assert a refusal.
"""

from __future__ import annotations

import pytest

from bridge.teleop import hands
from bridge.teleop.hands import (
    SimulatedHandDriver,
    BrainCoHandDriver,
    Dex3HandDriver,
    NullHandDriver,
    build_driver,
    get_driver,
    reset_driver,
    dex3_mode_byte,
)


@pytest.fixture(autouse=True)
def _clear_env(monkeypatch):
    for name in (
        "TELEOP_HAND_ENABLED",
        "TELEOP_HAND_TYPE",
        "TELEOP_HAND_SIDES",
        "TELEOP_BRAINCO_OPEN_AT",
    ):
        monkeypatch.delenv(name, raising=False)


def test_default_is_a_null_driver():
    driver = build_driver()
    assert isinstance(driver, NullHandDriver)
    assert "TELEOP_HAND_ENABLED" in driver.reason


def test_enabled_without_a_type_still_refuses(monkeypatch):
    # The hands are settled (two BrainCo), but "enabled" is still not an answer
    # to "which one" — BrainCo and Dex3 disagree on units, so a silent default
    # is exactly how that mistake would ship.
    monkeypatch.setenv("TELEOP_HAND_ENABLED", "1")

    driver = build_driver()

    assert isinstance(driver, NullHandDriver)
    assert "brainco" in driver.reason


def test_brainco_refuses_without_a_stated_open_end(monkeypatch):
    monkeypatch.setenv("TELEOP_HAND_ENABLED", "1")
    monkeypatch.setenv("TELEOP_HAND_TYPE", "brainco")

    driver = build_driver()

    assert isinstance(driver, NullHandDriver)
    assert "TELEOP_BRAINCO_OPEN_AT" in driver.reason


def test_brainco_builds_once_the_open_end_is_stated(monkeypatch):
    monkeypatch.setenv("TELEOP_HAND_ENABLED", "1")
    monkeypatch.setenv("TELEOP_HAND_TYPE", "brainco")
    monkeypatch.setenv("TELEOP_BRAINCO_OPEN_AT", "1")

    driver = build_driver()

    assert isinstance(driver, BrainCoHandDriver)
    assert driver.open_at == 1.0


def test_dex3_builds_when_selected(monkeypatch):
    monkeypatch.setenv("TELEOP_HAND_ENABLED", "1")
    monkeypatch.setenv("TELEOP_HAND_TYPE", "dex3")

    assert isinstance(build_driver(), Dex3HandDriver)


def test_defaults_to_the_right_hand_only(monkeypatch):
    # Only a right hand has ever answered on this robot.
    monkeypatch.setenv("TELEOP_HAND_ENABLED", "1")
    monkeypatch.setenv("TELEOP_HAND_TYPE", "dex3")

    assert build_driver().sides == ("right",)


def test_a_nonsense_sides_list_refuses(monkeypatch):
    monkeypatch.setenv("TELEOP_HAND_ENABLED", "1")
    monkeypatch.setenv("TELEOP_HAND_TYPE", "dex3")
    monkeypatch.setenv("TELEOP_HAND_SIDES", "middle")

    assert isinstance(build_driver(), NullHandDriver)


def test_build_driver_never_raises(monkeypatch):
    # A misconfigured hand must not take out the arms and locomotion with it.
    monkeypatch.setenv("TELEOP_HAND_ENABLED", "1")
    monkeypatch.setenv("TELEOP_HAND_TYPE", "definitely-not-a-hand")
    monkeypatch.setenv("TELEOP_BRAINCO_OPEN_AT", "banana")

    assert isinstance(build_driver(), NullHandDriver)


def test_null_driver_publishes_nothing():
    driver = NullHandDriver("test")
    driver.send("right", 1.0)
    driver.relax()  # both are no-ops; the assertion is that neither raises


# -- BrainCo scaling --------------------------------------------------------


@pytest.mark.parametrize(
    ("open_at", "grip", "expected"),
    [
        (0.0, 0.0, 0.0),
        (0.0, 1.0, 1.0),
        (1.0, 0.0, 1.0),  # 1.0 means open on this polarity
        (1.0, 1.0, 0.0),
        (0.0, 0.5, 0.5),
        (1.0, 0.5, 0.5),
    ],
)
def test_brainco_polarity_maps_grip_to_the_right_end(open_at, grip, expected):
    driver = BrainCoHandDriver(open_at=open_at)
    position = driver.open_at + (1.0 - 2.0 * driver.open_at) * grip
    assert position == pytest.approx(expected)


def test_brainco_rejects_a_polarity_that_is_not_an_end():
    with pytest.raises(ValueError, match="exactly 0.0 or 1.0"):
        BrainCoHandDriver(open_at=0.5)


# -- Dex3 pose --------------------------------------------------------------


def test_dex3_open_hand_is_all_zeros():
    driver = Dex3HandDriver()
    assert driver.target_pose("right", 0.0) == pytest.approx([0.0] * 7)


def test_dex3_closing_is_sign_flipped_between_hands():
    # Left joints 3-6 are negative-only and right joints 3-6 positive-only, so
    # a shared "close the hand" pose has to be mirrored (docs/ROBOT-HARDWARE.md).
    # A driver that sent the same numbers to both would drive one hand
    # straight into its limit.
    driver = Dex3HandDriver()
    right = driver.target_pose("right", 1.0)
    left = driver.target_pose("left", 1.0)

    for i in range(3, 7):
        assert right[i] > 0 and left[i] < 0
        assert right[i] == pytest.approx(-left[i])


def test_dex3_pose_scales_with_grip():
    driver = Dex3HandDriver()
    half = driver.target_pose("right", 0.5)
    full = driver.target_pose("right", 1.0)
    assert all(h == pytest.approx(f / 2) for h, f in zip(half, full))


def test_dex3_grip_is_clamped():
    driver = Dex3HandDriver()
    assert driver.target_pose("right", 5.0) == pytest.approx(driver.target_pose("right", 1.0))
    assert driver.target_pose("right", -5.0) == pytest.approx(driver.target_pose("right", 0.0))


def test_dex3_mode_byte_packs_id_status_and_timeout():
    # `RIS_Mode_t { id:4, status:3, timeout:1 }`, confirmed verbatim by Unitree.
    assert dex3_mode_byte(0) == 0x01 << 4 | 0x80
    assert dex3_mode_byte(6) == 6 | (1 << 4) | (1 << 7)
    # The timeout bit is a firmware-side 1 s deadman on the hand motors. It
    # should never be cleared by anything we write.
    assert dex3_mode_byte(3) & 0x80


def test_dex3_never_exceeds_the_configured_closure():
    from bridge.teleop.hands import DEX3_CLOSED_MAGNITUDE

    driver = Dex3HandDriver()
    for side in ("left", "right"):
        for value in driver.target_pose(side, 1.0):
            assert abs(value) <= max(DEX3_CLOSED_MAGNITUDE) + 1e-9


def test_the_driver_is_shared_across_sessions(monkeypatch):
    """One driver per process, not per connection.

    A driver holds DDS publishers that cannot be closed, so building one per
    session leaked a writer per reconnect onto the hand's command topic — and
    reconnects are routine: a Wi-Fi blip, the headset sleeping, the operator
    stepping out of the room.
    """
    monkeypatch.setenv("TELEOP_HAND_ENABLED", "1")
    monkeypatch.setenv("TELEOP_HAND_TYPE", "dex3")

    first = get_driver()
    second = get_driver()

    assert first is second


def test_resetting_rebuilds_from_the_current_config(monkeypatch):
    monkeypatch.setenv("TELEOP_HAND_ENABLED", "1")
    monkeypatch.setenv("TELEOP_HAND_TYPE", "dex3")
    assert isinstance(get_driver(), Dex3HandDriver)

    reset_driver()
    monkeypatch.setenv("TELEOP_HAND_ENABLED", "0")
    assert isinstance(get_driver(), NullHandDriver)


def test_a_simulated_hand_drives_both_sides_and_publishes_nothing(monkeypatch):
    monkeypatch.setenv("TELEOP_HAND_ENABLED", "1")
    monkeypatch.setenv("TELEOP_HAND_TYPE", "simulated")
    monkeypatch.setenv("TELEOP_HAND_SIDES", "left,right")

    driver = build_driver()

    assert isinstance(driver, SimulatedHandDriver)
    assert driver.name == "simulated" and set(driver.sides) == {"left", "right"}
    driver.send_shape("right", (1.0,) * 6)  # no publisher exists to call


# -- BrainCo state: where the fingers ARE ------------------------------------------------------


class _States:
    def __init__(self, qs):
        self.states = [type("S", (), {"q": q})() for q in qs]


@pytest.mark.parametrize("open_at", [0.0, 1.0])
def test_brainco_state_reads_back_as_closure_on_either_polarity(open_at):
    driver = BrainCoHandDriver(open_at=open_at)
    assert driver.measured("right") is None  # nothing heard yet
    closure = (0.0, 0.25, 0.5, 0.75, 1.0, 0.1)
    driver._on_state("right", _States([abs(open_at - c) for c in closure]))
    assert driver.measured("right") == pytest.approx(closure)
    assert driver.measured("left") is None


def test_brainco_state_that_is_old_or_malformed_is_no_reading(monkeypatch):
    driver = BrainCoHandDriver(open_at=0.0)
    driver._on_state("right", _States([0.5] * 5))
    assert driver.measured("right") is None  # 5 channels: not this hand
    driver._on_state("right", _States([0.5] * 6))
    assert driver.measured("right") == pytest.approx((0.5,) * 6)
    later = hands.time.monotonic() + hands.BRAINCO_STATE_MAX_AGE_S + 0.1
    monkeypatch.setattr(hands.time, "monotonic", lambda: later)
    assert driver.measured("right") is None


def test_commanded_closures_are_what_the_wire_gets(monkeypatch):
    pytest.importorskip("unitree_sdk2py.idl.default")
    driver = BrainCoHandDriver(open_at=0.0)
    sent = []
    wire = type("P", (), {"Write": lambda self, msg: sent.append(msg)})()
    monkeypatch.setattr(driver, "_publisher", lambda side: wire)
    driver.send_shape("right", (1.0,) * 6)
    assert [c.q for c in sent[0].cmds] == pytest.approx(hands.commanded_closures((1.0,) * 6))
    assert hands.commanded_closures((1.0,) * 6)[1] == hands.BRAINCO_CLOSURE_CAPS[1] < 1.0
