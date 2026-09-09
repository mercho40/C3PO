"""Tests for velocity dispatch (`bridge.skills._locomotion`).

This fails *silently* in production — a velocity command sent to the wrong
channel is a no-op — so it is worth pinning down in tests that never touch DDS.

`_locomotion.SIM_MODE` is read once at import, so tests monkeypatch the module
attribute rather than the environment.

The DDS interface pin used to be tested here too; it lives in
test_dds_connection.py now.
"""

from __future__ import annotations

import json

import pytest

from bridge.sdk import g1_protocol, g1_rpc
from bridge.skills import _locomotion


# --------------------------------------------------------------------------
# Velocity dispatch
# --------------------------------------------------------------------------


def test_real_mode_sends_set_velocity_rpc(monkeypatch):
    monkeypatch.setattr(_locomotion, "SIM_MODE", "real")
    seen: dict = {}

    def fake_call_set_velocity(vx, vy, omega, duration):
        seen.update(vx=vx, vy=vy, omega=omega, duration=duration)
        return 0, ""

    monkeypatch.setattr(g1_rpc, "call_set_velocity", fake_call_set_velocity)

    # Values chosen inside the real-hardware envelope so this test covers
    # dispatch shape only -- clamping is asserted separately below. (It used
    # to pass 0.4 vx, which now clamps to 0.3 and would conflate the two.)
    _locomotion.send_velocity(0.2, -0.1, 0.2, height=0.78)

    assert seen["vx"] == 0.2
    assert seen["vy"] == -0.1
    assert seen["omega"] == 0.2
    # The firmware-side deadman. If this ever reads 864000, the robot keeps
    # walking after the bridge dies.
    assert seen["duration"] == _locomotion.VELOCITY_DURATION_S
    assert seen["duration"] <= 2.0


def test_real_mode_clamps_to_hardware_vetted_caps(monkeypatch):
    """walk_to/turn size setpoints against sim caps; real dispatch must not.

    The sim caps (MAX_FWD_VEL=1.0, MAX_YAW_VEL=1.57) are 3-5x the only speeds
    ever measured on this physical robot, and both controllers saturate them
    on any target more than about a metre away. This clamp is what stands
    between "real-mode odom got wired" and "a humanoid was commanded to 1 m/s
    on an unmeasured path", so it is pinned here rather than left to review.
    """
    from bridge.skills.walk_velocity import MAX_LINEAR_VEL, MAX_YAW_VEL

    monkeypatch.setattr(_locomotion, "SIM_MODE", "real")
    seen: dict = {}
    monkeypatch.setattr(
        g1_rpc,
        "call_set_velocity",
        lambda vx, vy, omega, duration: seen.update(vx=vx, vy=vy, omega=omega) or (0, ""),
    )

    # Ask for the sim caps, which is exactly what a saturated controller does.
    _locomotion.send_velocity(
        _locomotion.MAX_FWD_VEL, _locomotion.MAX_LAT_VEL, _locomotion.MAX_YAW_VEL, height=0.78
    )
    assert seen["vx"] == MAX_LINEAR_VEL
    assert seen["vy"] == MAX_LINEAR_VEL
    assert seen["omega"] == MAX_YAW_VEL

    # ...and in the negative direction, where MAX_BACK_VEL is the sim cap.
    _locomotion.send_velocity(
        _locomotion.MAX_BACK_VEL, -_locomotion.MAX_LAT_VEL, -_locomotion.MAX_YAW_VEL, height=0.78
    )
    assert seen["vx"] == -MAX_LINEAR_VEL
    assert seen["vy"] == -MAX_LINEAR_VEL
    assert seen["omega"] == -MAX_YAW_VEL


def test_sim_mode_is_not_clamped_to_the_real_caps(monkeypatch):
    # The sim caps are tuned and exercised against Isaac Sim; tightening them
    # there would change validated sim behaviour for no safety gain.
    monkeypatch.setattr(_locomotion, "SIM_MODE", "isaac")
    written: list = []

    class FakePublisher:
        def Write(self, msg):
            written.append(msg.data)

    monkeypatch.setattr(_locomotion, "_get_publisher", lambda: FakePublisher())

    _locomotion.send_velocity(_locomotion.MAX_FWD_VEL, 0.0, 0.0, height=0.78)

    assert json.loads(written[0].replace("'", '"'))[0] == _locomotion.MAX_FWD_VEL


def test_real_mode_never_touches_the_sim_publisher(monkeypatch):
    # The sim channel `rt/run_command/cmd` isn't subscribed by real firmware,
    # so reaching the publisher at all on real hardware is the bug this whole
    # change exists to fix.
    monkeypatch.setattr(_locomotion, "SIM_MODE", "real")
    monkeypatch.setattr(g1_rpc, "call_set_velocity", lambda *a: (0, ""))

    def explode():
        raise AssertionError("sim publisher must not be constructed on real")

    monkeypatch.setattr(_locomotion, "_get_publisher", explode)

    _locomotion.send_velocity(0.1, 0.0, 0.0, height=0.78)


def test_real_mode_rpc_error_does_not_raise(monkeypatch):
    # Called at 50 Hz inside a control loop: one bad setpoint must not abort
    # the skill before it runs its own stop sequence.
    monkeypatch.setattr(_locomotion, "SIM_MODE", "real")
    monkeypatch.setattr(g1_rpc, "call_set_velocity", lambda *a: (3104, None))

    _locomotion.send_velocity(0.1, 0.0, 0.0, height=0.78)


@pytest.mark.parametrize("mode", ["isaac", "mujoco_local"])
def test_sim_modes_publish_list_string_to_run_command(monkeypatch, mode):
    monkeypatch.setattr(_locomotion, "SIM_MODE", mode)
    written: list = []

    class FakePublisher:
        def Write(self, msg):
            written.append(msg.data)

    monkeypatch.setattr(_locomotion, "_get_publisher", lambda: FakePublisher())
    monkeypatch.setattr(
        g1_rpc,
        "call_set_velocity",
        lambda *a: pytest.fail("sim must not use the real RPC path"),
    )

    _locomotion.send_velocity(0.5, 0.25, -0.75, height=0.8)

    assert written == ["[0.5, 0.25, -0.75, 0.8]"]


def test_stop_motion_sync_zeroes_velocity_on_real(monkeypatch):
    # stop_everything's safety fallback runs through here.
    monkeypatch.setattr(_locomotion, "SIM_MODE", "real")
    calls: list = []
    monkeypatch.setattr(g1_rpc, "call_set_velocity", lambda *a: (calls.append(a), (0, ""))[1])

    _locomotion.stop_motion_sync(height=0.78, duration_s=0.05)

    assert calls, "stop must emit at least one setpoint"
    assert all(c[:3] == (0, 0, 0) for c in calls)


# --------------------------------------------------------------------------
# SET_VELOCITY parameter shape
# --------------------------------------------------------------------------


def test_set_velocity_parameter_matches_vendor_schema(monkeypatch):
    # Shape comes from the vendor's g1_loco_client.hpp:
    #   js["velocity"] = [vx, vy, omega]; js["duration"] = duration
    captured: dict = {}

    class FakeClient:
        def call_raw(self, api_id, parameter):
            captured.update(api_id=api_id, parameter=parameter)
            return 0, ""

    monkeypatch.setattr(g1_rpc, "_get_sport_client", lambda: FakeClient())

    g1_rpc.call_set_velocity(0.3, 0.0, -0.2, 1.0)

    assert captured["api_id"] == g1_protocol.API_ID_LOCO_SET_VELOCITY == 7105
    assert json.loads(captured["parameter"]) == {
        "velocity": [0.3, 0.0, -0.2],
        "duration": 1.0,
    }


# The DDS interface-pin tests that lived here moved to test_dds_connection.py
# when the pin was made to actually reach the SDK.
