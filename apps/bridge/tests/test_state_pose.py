"""Tests for world-pose sourcing (`bridge.sdk.state`).

The bug these exist to prevent: pose on real hardware was subscribed with the
wrong DDS type. That fails *silently* — DDS matches publisher to subscriber by
type, so a mismatch delivers nothing forever and looks identical to a robot
that simply isn't moving. walk_to and turn both abort on `pose is None`, so the
symptom surfaced far from the cause.

`StateSampler()` does no DDS work until `.start()`, so these construct one and
drive the message callbacks directly.
"""

from __future__ import annotations

import json
import math
import types

from bridge.sdk import g1_protocol, state


def _live_lowstate() -> state._LowStateSnapshot:
    return state._LowStateSnapshot(
        received_at=1_000_000_000.0, tick=1, mode_machine=1, motor_count=35,
        has_imu=True, raw_message_count=1,
    )


def _fake_odom_msg(x: float, y: float, z: float, yaw: float) -> types.SimpleNamespace:
    """Shape-compatible stand-in for unitree_go SportModeState_."""
    return types.SimpleNamespace(
        position=[x, y, z],
        imu_state=types.SimpleNamespace(rpy=[0.0, 0.0, yaw]),
    )


# --------------------------------------------------------------------------
# Topic profile
# --------------------------------------------------------------------------


def test_real_profile_has_a_distinct_odom_topic():
    real = g1_protocol.topics_for("real")
    # Must NOT reuse sportmodestate: that topic carries a unitree_hg type this
    # SDK has no IDL class for, which is what made pose unreachable before.
    assert real.odom == "rt/odommodestate"
    assert real.odom != real.sportmodestate


def test_sim_profile_has_no_odom_topic():
    # Sim pose rides on sportmodestate (rt/sim_state) instead.
    assert g1_protocol.topics_for("isaac").odom is None
    assert g1_protocol.topics_for("stub").odom is None


# --------------------------------------------------------------------------
# Odometry parsing (real)
# --------------------------------------------------------------------------


def test_odom_message_populates_pose():
    sampler = state.StateSampler()
    sampler._lowstate = _live_lowstate()

    sampler._on_odom(_fake_odom_msg(-0.186, -0.055, -0.057, 0.591))
    result = sampler.get_state()

    assert result["pose"] is not None
    assert math.isclose(result["pose"]["x_meters_world"], -0.186)
    assert math.isclose(result["pose"]["y_meters_world"], -0.055)
    # Yaw comes from imu_state.rpy[2], not a quaternion.
    assert math.isclose(result["pose"]["yaw_radians_world"], 0.591)


def test_odom_counts_messages_and_reports_source():
    sampler = state.StateSampler()
    sampler._lowstate = _live_lowstate()

    for i in range(3):
        sampler._on_odom(_fake_odom_msg(float(i), 0.0, 0.0, 0.0))

    raw = sampler.get_state()["raw"]
    assert raw["pose_messages_received"] == 3
    # Diagnosing a null pose shouldn't require reading the source.
    assert "pose_source" in raw


def test_malformed_odom_is_ignored_not_fatal():
    sampler = state.StateSampler()
    sampler._lowstate = _live_lowstate()

    sampler._on_odom(types.SimpleNamespace(position=[1.0], imu_state=None))

    assert sampler.get_state()["pose"] is None


# --------------------------------------------------------------------------
# Sim path still works
# --------------------------------------------------------------------------


def test_sim_state_json_still_parses_to_pose():
    sampler = state.StateSampler()
    sampler._lowstate = _live_lowstate()
    # Identity quaternion → yaw 0.
    payload = {
        "init_state": json.dumps(
            {"articulation": {"robot": {"root_pose": [[1.5, 2.5, 0.8, 1.0, 0.0, 0.0, 0.0]]}}}
        )
    }

    sampler._on_sim_state(types.SimpleNamespace(data=json.dumps(payload)))
    pose = sampler.get_state()["pose"]

    assert math.isclose(pose["x_meters_world"], 1.5)
    assert math.isclose(pose["y_meters_world"], 2.5)
    assert math.isclose(pose["yaw_radians_world"], 0.0, abs_tol=1e-9)


def test_pose_is_null_before_any_message():
    sampler = state.StateSampler()
    sampler._lowstate = _live_lowstate()

    assert sampler.get_state()["pose"] is None
