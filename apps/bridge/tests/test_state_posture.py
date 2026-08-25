"""Tests for `StateSampler.get_state()`'s posture field (`bridge.sdk.state`).

Regression coverage for the mode_machine/mode_label mismatch: `LowState_
.mode_machine` isn't the locomotion FSM index `g1_protocol.mode_label()`
decodes (that's `sportmodestate.mode`, which has no DDS-decodable type for
G1 in this SDK — see README "Known issues"). Real mode must not mislabel it.

`StateSampler()` itself does no DDS work (only `.start()` does), so these
construct one directly and poke its private snapshot fields — no mocking
needed.
"""

from __future__ import annotations

from bridge.sdk import g1_protocol, state


def _sampler_with_lowstate(mode_machine: int) -> state.StateSampler:
    sampler = state.StateSampler()
    sampler._lowstate = state._LowStateSnapshot(
        received_at=1_000_000_000.0,
        tick=1,
        mode_machine=mode_machine,
        motor_count=35,
        has_imu=True,
        raw_message_count=1,
    )
    return sampler


def test_real_mode_posture_is_unknown_until_fsm_polled(monkeypatch):
    monkeypatch.setattr(state, "_SIM_MODE", "real")
    sampler = _sampler_with_lowstate(mode_machine=5)

    result = sampler.get_state()

    # No FSM reading yet — must not fall back to mislabelling mode_machine.
    assert result["posture"] == "unknown"
    # Raw mode_machine still surfaces for anyone who wants it.
    assert result["raw"]["mode_machine"] == 5


def test_real_mode_posture_uses_polled_fsm_id(monkeypatch):
    monkeypatch.setattr(state, "_SIM_MODE", "real")
    sampler = _sampler_with_lowstate(mode_machine=5)
    # Values observed together on hardware: mode_machine 5, FSM id 802.
    sampler._fsm = state._FsmSnapshot(received_at=1_000_000_000.0, fsm_id=802, fsm_mode=0)

    result = sampler.get_state()

    assert result["posture"] == g1_protocol.mode_label(802)
    assert result["raw"]["fsm_id"] == 802
    # The whole point: posture must track the FSM id, not mode_machine.
    assert result["posture"] != g1_protocol.mode_label(5)


def test_real_mode_posture_never_labels_mode_machine(monkeypatch):
    # Regression guard for the two being conflated. mode_machine=1 would decode
    # to "damp" if wrongly labelled; with FSM id 500 it must read as walk.
    monkeypatch.setattr(state, "_SIM_MODE", "real")
    sampler = _sampler_with_lowstate(mode_machine=1)
    sampler._fsm = state._FsmSnapshot(received_at=1_000_000_000.0, fsm_id=500, fsm_mode=0)

    assert sampler.get_state()["posture"] == g1_protocol.mode_label(500)


def test_sim_mode_posture_still_uses_mode_label(monkeypatch):
    # Isaac Sim happens to populate mode_machine with the real FSM value as a
    # convenience — trusting mode_label() there is the documented exception.
    monkeypatch.setattr(state, "_SIM_MODE", "isaac")
    sampler = _sampler_with_lowstate(mode_machine=1)  # Mode.DAMP

    result = sampler.get_state()

    assert result["posture"] == "damp"
