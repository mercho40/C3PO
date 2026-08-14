"""Tests for `StateSampler.get_state()`'s battery field (`bridge.sdk.state`).

Battery is not in `LowState_` on the G1 — the humanoid `unitree_hg` `LowState_`
has no BMS field at all, unlike the quadruped `unitree_go` one. It arrives as
`BmsState_` on its own topic (`rt/lf/bmsstate`), which is why `battery_pct` read
`None` for months: nothing was subscribed to look.

That history is the reason for the null-handling tests below. "faults: [] and
battery: null" was previously reported for a robot whose pack nobody had ever
read, and null must keep meaning "not received", never "fine".

`StateSampler()` does no DDS work until `.start()`, so these construct one
directly and poke its private snapshots — same approach as `test_state_posture`.
"""

from __future__ import annotations

import time

from bridge.sdk import state


def _sampler(soc: int | None = None, *, soh: int = 100, current: int = -1500) -> state.StateSampler:
    # Timestamps must be *now*: `get_state` adds a staleness fault past 1 s, and
    # a fixed epoch would bury the battery assertions under it.
    now = time.time()
    sampler = state.StateSampler()
    sampler._lowstate = state._LowStateSnapshot(
        received_at=now,
        tick=1,
        mode_machine=5,
        motor_count=35,
        has_imu=True,
        raw_message_count=1,
    )
    if soc is not None:
        sampler._bms = state._BmsSnapshot(
            received_at=now,
            soc_pct=soc,
            soh_pct=soh,
            current_ma=current,
            raw_message_count=1,
        )
    return sampler


def test_battery_pct_is_none_before_any_bms_message():
    result = _sampler().get_state()

    assert result["battery_pct"] is None
    assert result["raw"]["battery_messages_received"] == 0
    assert result["raw"]["battery_age_s"] is None
    # Crucially: an unread pack must NOT be reported as a low-battery fault
    # either. Absence of data is not evidence in either direction.
    assert not any("battery" in f for f in result["faults"])


def test_battery_pct_reports_soc():
    result = _sampler(soc=87).get_state()

    assert result["battery_pct"] == 87
    assert result["raw"]["battery_soh_pct"] == 100
    assert result["raw"]["battery_current_ma"] == -1500
    assert result["faults"] == []


def test_low_battery_raises_a_fault_at_the_vendor_threshold():
    # The vendor's own predicate is `soc < 20`.
    assert _sampler(soc=20).get_state()["faults"] == []
    assert "low_battery_19pct" in _sampler(soc=19).get_state()["faults"]


def test_battery_survives_a_flat_pack_reading():
    # 0 is a real reading, not a missing one — it must not be confused with None
    # by any truthiness check on the way out.
    result = _sampler(soc=0).get_state()

    assert result["battery_pct"] == 0
    assert "low_battery_0pct" in result["faults"]


def test_bms_handler_populates_the_snapshot():
    """`_on_bms` reads soc/soh/current off the message and counts it."""

    class FakeBms:
        soc = 64
        soh = 98
        current = -2200

    sampler = _sampler()
    sampler._on_bms(FakeBms())

    assert sampler._bms.soc_pct == 64
    assert sampler._bms.soh_pct == 98
    assert sampler._bms.current_ma == -2200
    assert sampler._bms.raw_message_count == 1
    assert sampler._bms.received_at > 0.0

    sampler._on_bms(FakeBms())
    assert sampler._bms.raw_message_count == 2
