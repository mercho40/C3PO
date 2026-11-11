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
import threading
import time

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
    # Saturated on both axes: the governor caps the vector (safety.govern), so each is cap/sqrt(2).
    edge = MAX_LINEAR_VEL / 2**0.5
    assert seen["vx"] == pytest.approx(edge)
    assert seen["vy"] == pytest.approx(edge)
    assert seen["omega"] == MAX_YAW_VEL

    # ...and in the negative direction, where MAX_BACK_VEL is the sim cap.
    # From rest: straight after the forward command this is a reversal, which
    # the governor's acceleration cap ramps (test_safety.py covers that).
    from bridge import safety

    safety.reset()
    _locomotion.send_velocity(
        _locomotion.MAX_BACK_VEL, -_locomotion.MAX_LAT_VEL, -_locomotion.MAX_YAW_VEL, height=0.78
    )
    assert seen["vx"] == pytest.approx(-edge)
    assert seen["vy"] == pytest.approx(-edge)
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


def _timed_burst(monkeypatch, call_set_velocity):
    """Run a real-mode 0.4 s stop burst; returns `(sent, acked, elapsed_s)`."""
    monkeypatch.setattr(_locomotion, "SIM_MODE", "real")
    monkeypatch.setattr(g1_rpc, "call_set_velocity", call_set_velocity)
    started = time.monotonic()
    sent, acked = _locomotion.stop_motion_sync(height=0.78, duration_s=0.4)
    return sent, acked, time.monotonic() - started


def test_a_hung_reply_does_not_hold_the_stop_burst(monkeypatch):
    """SET_VELOCITY waits up to SPORT_TIMEOUT_S (10 s) for its ack. One slow
    reply held the whole burst, and acked == 0 then damped a standing robot."""
    hang = threading.Event()
    calls: list = []

    def call_set_velocity(*a):
        first = not calls
        calls.append(a)
        if first:
            hang.wait(5.0)
            return 3104, None
        return 0, ""

    try:
        sent, acked, elapsed = _timed_burst(monkeypatch, call_set_velocity)
    finally:
        hang.set()

    assert elapsed < 0.4 + _locomotion.STOP_ACK_GRACE_S + 0.5
    assert sent >= 10, "the zero setpoints keep their 50 Hz schedule"
    assert sent - 3 <= acked <= sent - 1


def test_a_burst_nobody_answers_ends_on_time_with_no_ack(monkeypatch):
    hang = threading.Event()

    def call_set_velocity(*a):
        hang.wait(5.0)
        return 3104, None

    try:
        sent, acked, elapsed = _timed_burst(monkeypatch, call_set_velocity)
    finally:
        hang.set()

    assert elapsed < 0.4 + _locomotion.STOP_ACK_GRACE_S + 0.5
    assert sent >= 10
    assert acked == 0


def test_a_slow_reply_inside_the_grace_window_still_counts(monkeypatch):
    def call_set_velocity(*a):
        time.sleep(0.1)
        return 0, ""

    sent, acked, _ = _timed_burst(monkeypatch, call_set_velocity)

    assert acked == sent


def test_a_burst_on_a_cold_sport_client_is_still_heard(monkeypatch):
    """The vendor client's first Init takes >= 0.7 s. Built inside the burst's
    window, every setpoint queued on it, acked came back 0, and the stop damped
    a standing robot that heard it."""

    class ColdClient:
        def __init__(self, *a, **kw):
            time.sleep(0.75)

        def Init(self):
            pass

        def call_raw(self, api_id, parameter):
            return 0, ""

    monkeypatch.setattr(_locomotion, "SIM_MODE", "real")
    monkeypatch.setattr(g1_rpc, "_sport_client", None)
    monkeypatch.setattr(g1_rpc, "_G1Client", ColdClient)

    sent, acked = _locomotion.stop_motion_sync(height=0.78, duration_s=0.4)

    assert sent >= 10
    assert acked == sent


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
