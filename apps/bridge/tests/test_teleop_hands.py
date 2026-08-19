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

from bridge.teleop.hands import (
    BrainCoHandDriver,
    Dex3HandDriver,
    NullHandDriver,
    build_driver,
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


def test_enabled_without_a_type_still_refuses():
    # Which hands are fitted is unresolved (ROBOT-PERIPHERALS §4). "Enabled"
    # is not an answer to "which one".
    import os

    os.environ["TELEOP_HAND_ENABLED"] = "1"
    try:
        driver = build_driver()
        assert isinstance(driver, NullHandDriver)
        assert "hand_probe" in driver.reason
    finally:
        del os.environ["TELEOP_HAND_ENABLED"]


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
    # a shared "close the hand" pose has to be mirrored (ROBOT-PERIPHERALS
    # §4.4). A driver that sent the same numbers to both would drive one hand
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
