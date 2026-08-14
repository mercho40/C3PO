"""Tests for StateSampler._on_sim_state — the nested-JSON unwrap + quaternion
yaw math that feeds `get_state()`'s `pose` field in sim modes. Had zero
direct coverage (test_state_posture.py only covers the mode_machine/posture
path), including the parse-failure branch that's supposed to fail closed
(log + skip) rather than crash the DDS subscriber callback.

`StateSampler()` does no DDS work until `.start()` is called, so these
construct one directly and feed `_on_sim_state` a fake message object with
just the `.data` attribute it actually reads.
"""

from __future__ import annotations

import json
import math
from types import SimpleNamespace

import pytest

from bridge.sdk import state


def _msg(payload: dict) -> SimpleNamespace:
    return SimpleNamespace(data=json.dumps(payload))


def test_on_sim_state_parses_nested_json_string_and_updates_pose():
    sampler = state.StateSampler()
    outer = {
        "init_state": json.dumps(
            {"articulation": {"robot": {"root_pose": [[1.5, 2.5, 0.78, 1.0, 0.0, 0.0, 0.0]]}}}
        )
    }

    sampler._on_sim_state(_msg(outer))

    assert sampler._sim.x_meters_world == 1.5
    assert sampler._sim.y_meters_world == 2.5
    assert sampler._sim.z_meters_world == 0.78
    assert sampler._sim.yaw_radians_world == pytest.approx(0.0)
    assert sampler._sim.raw_message_count == 1


def test_on_sim_state_accepts_inner_dict_not_only_json_string():
    # `inner_raw` is used as-is when it's already a dict, not re-parsed.
    sampler = state.StateSampler()
    outer = {"init_state": {"articulation": {"robot": {"root_pose": [[3.0, 4.0, 0.0, 1.0, 0.0, 0.0, 0.0]]}}}}

    sampler._on_sim_state(_msg(outer))

    assert sampler._sim.x_meters_world == 3.0
    assert sampler._sim.y_meters_world == 4.0


def test_on_sim_state_computes_yaw_from_quaternion():
    sampler = state.StateSampler()
    half = math.sqrt(2) / 2  # 90-degree yaw: qw=qz=cos/sin(45deg)
    outer = {"init_state": json.dumps({"articulation": {"robot": {"root_pose": [[0, 0, 0, half, 0, 0, half]]}}})}

    sampler._on_sim_state(_msg(outer))

    assert sampler._sim.yaw_radians_world == pytest.approx(math.pi / 2, abs=1e-6)


def test_on_sim_state_malformed_json_does_not_crash_or_update():
    sampler = state.StateSampler()

    sampler._on_sim_state(SimpleNamespace(data="not json at all"))  # must not raise

    assert sampler._sim.raw_message_count == 0


def test_on_sim_state_missing_expected_keys_does_not_crash_or_update():
    sampler = state.StateSampler()
    outer = {"init_state": json.dumps({"nope": {}})}

    sampler._on_sim_state(_msg(outer))  # KeyError swallowed, not propagated

    assert sampler._sim.raw_message_count == 0


def test_on_sim_state_short_pose_array_does_not_crash_or_update():
    sampler = state.StateSampler()
    outer = {"init_state": json.dumps({"articulation": {"robot": {"root_pose": [[1.0, 2.0]]}}})}  # too short

    sampler._on_sim_state(_msg(outer))  # ValueError from unpacking, swallowed

    assert sampler._sim.raw_message_count == 0


def test_get_state_reflects_sim_pose_after_on_sim_state(monkeypatch):
    monkeypatch.setattr(state, "_SIM_MODE", "isaac")
    sampler = state.StateSampler()
    sampler._lowstate = state._LowStateSnapshot(
        received_at=1_000_000_000.0, tick=1, mode_machine=1, motor_count=35, has_imu=True, raw_message_count=1
    )
    outer = {"init_state": json.dumps({"articulation": {"robot": {"root_pose": [[1.0, 2.0, 0.0, 1.0, 0.0, 0.0, 0.0]]}}})}
    sampler._on_sim_state(_msg(outer))

    result = sampler.get_state()

    assert result["pose"]["x_meters_world"] == 1.0
    assert result["pose"]["y_meters_world"] == 2.0
    assert result["pose"]["z_meters_world"] == 0.0
    assert result["pose"]["yaw_radians_world"] == pytest.approx(0.0)
