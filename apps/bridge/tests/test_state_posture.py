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

from bridge.sdk import state


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


def test_real_mode_posture_is_not_available_over_dds(monkeypatch):
    monkeypatch.setattr(state, "_SIM_MODE", "real")
    sampler = _sampler_with_lowstate(mode_machine=5)

    result = sampler.get_state()

    assert result["posture"] == "not_available_over_dds"
    # Raw mode_machine still surfaces for anyone who wants it.
    assert result["raw"]["mode_machine"] == 5


def test_sim_mode_posture_still_uses_mode_label(monkeypatch):
    # Isaac Sim happens to populate mode_machine with the real FSM value as a
    # convenience — trusting mode_label() there is the documented exception.
    monkeypatch.setattr(state, "_SIM_MODE", "isaac")
    sampler = _sampler_with_lowstate(mode_machine=1)  # Mode.DAMP

    result = sampler.get_state()

    assert result["posture"] == "damp"
