"""Tests for the generic `gesture` skill — the full preset-action catalogue.

What matters: the catalogue matches the firmware table in g1_protocol (no
hand-typed drift), unknown names fail with the available list instead of
guessing, real mode dispatches the right id through call_arm, and the
rt/arm_sdk contention check refuses rather than provoking error 7400.
"""

from __future__ import annotations

import pytest

from bridge.sdk import g1_protocol, g1_rpc
from bridge.skills import gesture


def test_catalogue_is_exactly_the_firmware_table():
    assert set(gesture.GESTURE_CATALOGUE.values()) == set(g1_protocol.Gesture)
    # Names are the lowercased firmware strings, addressable case-insensitively.
    assert gesture.GESTURE_CATALOGUE["ultraman_ray"] == g1_protocol.Gesture.ULTRAMAN_RAY
    assert gesture.GESTURE_CATALOGUE["release_arm"] == g1_protocol.Gesture.RELEASE_ARM


@pytest.mark.asyncio
async def test_unknown_gesture_fails_with_the_available_list():
    result = await gesture.run("moonwalk")
    assert result["status"] == "failed"
    assert result["phase"] == "unknown_gesture"
    assert "high_five" in result["result"]["available"]
    # Taught actions are deliberately NOT dispatchable — the by-name wire
    # format is unproven. The refusal must say so rather than 404 silently.
    assert "Waist_Drum_Dance" in result["result"]["note"]


@pytest.mark.asyncio
async def test_stub_mode_is_honest_and_carries_gating(monkeypatch):
    monkeypatch.setattr(gesture, "SIM_MODE", "stub")
    result = await gesture.run("Turn_Back_Wave")  # case-insensitive
    assert result["status"] == "completed"
    assert result["phase"] == "stub"
    assert result["result"]["gesture"] == 1
    # The one FSM-gated action in the whole table must advertise its gate.
    assert result["result"]["requires_fsm"] == [500, 501]


@pytest.mark.asyncio
async def test_real_mode_dispatches_then_auto_releases(monkeypatch):
    """The default path: gesture, hold, release_arm — the latch never stands.

    Learned on hardware 2026-08-27: heart_both_hands was dispatched, the
    follow-up release never arrived (robot power-cycled mid-session), and the
    arms were left actively holding the pose under motor load."""
    monkeypatch.setattr(gesture, "SIM_MODE", "real")
    sent: list[int] = []
    monkeypatch.setattr(g1_rpc, "call_arm", lambda g: sent.append(g) or (0, ""))

    result = await gesture.run("high_five", hold_s=0.0)

    assert sent == [
        int(g1_protocol.Gesture.HIGH_FIVE),
        int(g1_protocol.Gesture.RELEASE_ARM),
    ]
    assert result["status"] == "completed"
    assert result["phase"] == "released"
    assert result["result"]["release_rpc_code"] == 0


@pytest.mark.asyncio
async def test_auto_release_false_leaves_the_latch_and_says_so(monkeypatch):
    monkeypatch.setattr(gesture, "SIM_MODE", "real")
    sent: list[int] = []
    monkeypatch.setattr(g1_rpc, "call_arm", lambda g: sent.append(g) or (0, ""))

    result = await gesture.run("heart_both_hands", auto_release=False)

    assert sent == [int(g1_protocol.Gesture.HEART_BOTH_HANDS)]
    assert result["phase"] == "dispatched"
    assert "latched" in result["result"]["note"]


@pytest.mark.asyncio
async def test_release_arm_itself_is_not_double_released(monkeypatch):
    monkeypatch.setattr(gesture, "SIM_MODE", "real")
    sent: list[int] = []
    monkeypatch.setattr(g1_rpc, "call_arm", lambda g: sent.append(g) or (0, ""))

    result = await gesture.run("release_arm", hold_s=0.0)

    assert sent == [int(g1_protocol.Gesture.RELEASE_ARM)]
    assert result["phase"] == "dispatched"


@pytest.mark.asyncio
async def test_failed_auto_release_is_reported_loudly(monkeypatch):
    """A gesture that ran but could not release is a standing latch — the
    result must say so instead of reading as a clean success."""
    monkeypatch.setattr(gesture, "SIM_MODE", "real")
    codes = iter([(0, ""), (7401, None)])
    monkeypatch.setattr(g1_rpc, "call_arm", lambda g: next(codes))

    result = await gesture.run("hug", hold_s=0.0)

    assert result["status"] == "completed"  # the gesture itself did run
    assert result["phase"] == "release_failed"
    assert "latched" in result["result"]["note"]


@pytest.mark.asyncio
async def test_failed_gesture_does_not_auto_release(monkeypatch):
    """A refused dispatch latched nothing new; sending 99 after somebody
    else's failure would exceed this task's own footprint."""
    monkeypatch.setattr(gesture, "SIM_MODE", "real")
    sent: list[int] = []
    monkeypatch.setattr(g1_rpc, "call_arm", lambda g: sent.append(g) or (7404, None))

    result = await gesture.run("turn_back_wave", hold_s=0.0)

    assert sent == [int(g1_protocol.Gesture.TURN_BACK_WAVE)]
    assert result["status"] == "failed"


@pytest.mark.asyncio
async def test_rpc_error_7401_explains_the_arm_latch(monkeypatch):
    monkeypatch.setattr(gesture, "SIM_MODE", "real")
    monkeypatch.setattr(g1_rpc, "call_arm", lambda g: (7401, None))

    result = await gesture.run("hug")

    assert result["status"] == "failed"
    assert result["error"] == "rpc_error_code_7401"
    assert "release_arm" in result["result"]["note"]


@pytest.mark.asyncio
async def test_refuses_while_arm_sdk_holds_the_arms(monkeypatch):
    """Two owners of rt/arm_sdk is the documented cause of error 7400 — the
    gesture must be refused client-side, not sent to fail on the robot."""
    monkeypatch.setattr(gesture, "SIM_MODE", "real")
    monkeypatch.setattr(gesture, "_arm_sdk_engaged", lambda: True)
    called = []
    monkeypatch.setattr(g1_rpc, "call_arm", lambda g: called.append(g) or (0, ""))

    result = await gesture.run("wave_above_head")

    assert result["status"] == "failed"
    assert result["phase"] == "arm_sdk_engaged"
    assert called == []


@pytest.mark.asyncio
async def test_isaac_mode_logs_only(monkeypatch):
    monkeypatch.setattr(gesture, "SIM_MODE", "isaac")
    result = await gesture.run("refuse")
    assert result["status"] == "completed"
    assert result["phase"] == "logged_only"


@pytest.mark.asyncio
async def test_a_cancel_during_the_hold_still_releases_the_arm(monkeypatch):
    """An ended agent turn cancels its tool call (back aborts the MCP request).
    CancelledError skipped `except Exception`, so the hold was cut and the arm
    stayed latched in the pose, with the task pinned at "running"."""
    import asyncio

    from bridge.skills.task_runtime import get_registry

    monkeypatch.setattr(gesture, "SIM_MODE", "real")
    sent: list[int] = []
    monkeypatch.setattr(g1_rpc, "call_arm", lambda g: sent.append(g) or (0, ""))

    call = asyncio.ensure_future(gesture.run("high_five", hold_s=5.0))
    for _ in range(100):
        if sent:
            break
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.01)  # into the hold
    call.cancel()
    with pytest.raises(asyncio.CancelledError):
        await call
    for _ in range(100):
        if len(sent) == 2:
            break
        await asyncio.sleep(0.01)

    assert sent == [int(g1_protocol.Gesture.HIGH_FIVE), int(g1_protocol.Gesture.RELEASE_ARM)]
    # Not pinned "running": the watchdog reads active tasks as ongoing motion.
    assert not [t for t in get_registry().list_active() if t.skill_name == "gesture"]


@pytest.mark.asyncio
async def test_a_cancel_during_the_dispatch_releases_after_the_gesture_lands(monkeypatch):
    """The gesture's RPC thread keeps running after the cancel; the release
    waits for it rather than racing it on the one arm client."""
    import asyncio
    import threading

    monkeypatch.setattr(gesture, "SIM_MODE", "real")
    sent: list[int] = []
    gate = threading.Event()

    def call_arm(g: int) -> tuple[int, str]:
        if g != int(g1_protocol.Gesture.RELEASE_ARM):
            gate.wait(2.0)
        sent.append(g)
        return 0, ""

    monkeypatch.setattr(g1_rpc, "call_arm", call_arm)
    call = asyncio.ensure_future(gesture.run("high_five", hold_s=5.0))
    await asyncio.sleep(0.05)  # inside the dispatch
    call.cancel()
    await asyncio.sleep(0.05)
    assert sent == []  # nothing released while the gesture still runs
    gate.set()
    with pytest.raises(asyncio.CancelledError):
        await call
    for _ in range(200):
        if len(sent) == 2:
            break
        await asyncio.sleep(0.01)
    assert sent == [int(g1_protocol.Gesture.HIGH_FIVE), int(g1_protocol.Gesture.RELEASE_ARM)]
