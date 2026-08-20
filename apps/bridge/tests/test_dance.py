"""Tests for the choreographed gesture sequence (`bridge.skills.dance`).

All DDS calls are mocked via `bridge.sdk.g1_rpc.call_arm` — these test the
skill's sequencing/latch-avoidance/task-lifecycle logic, not the RPC dispatch
itself (covered by test_g1_request.py's coverage of the same underlying
primitive).

`dance.SIM_MODE` is module-level, read once at import time, so tests
monkeypatch it directly rather than mutating the environment (same reasoning
as test_g1_request.py and test_walk_velocity.py).
"""

from __future__ import annotations

import asyncio
import threading

import pytest

from bridge.sdk import g1_protocol, g1_rpc
from bridge.skills import dance


@pytest.mark.asyncio
async def test_stub_mode_is_clean_fake_no_dispatch(monkeypatch):
    monkeypatch.setattr(dance, "SIM_MODE", "stub")

    result = await dance.run()

    assert result["status"] == "completed"
    assert result["phase"] == "stub"
    assert result["result"]["note"] == "Stub mode — no dispatch."


@pytest.mark.asyncio
async def test_isaac_mode_logs_without_dispatch(monkeypatch):
    # Isaac Sim's scene doesn't subscribe to rt/api/arm/request, so
    # topics_for("isaac") resolves arm_request to None.
    monkeypatch.setattr(dance, "SIM_MODE", "isaac")

    result = await dance.run()

    assert result["status"] == "completed"
    assert result["phase"] == "logged_only"
    assert "doesn't subscribe" in result["result"]["note"]


@pytest.mark.asyncio
async def test_real_mode_dispatches_full_sequence_via_g1_rpc(monkeypatch):
    monkeypatch.setattr(dance, "SIM_MODE", "real")
    seen: list[int] = []

    def fake_call_arm(gesture: int):
        seen.append(gesture)
        return 0, ""

    monkeypatch.setattr(g1_rpc, "call_arm", fake_call_arm)

    result = await dance.run()

    assert seen == [int(g) for g in dance.SEQUENCE]
    assert result["status"] == "completed"
    assert result["phase"] == "done"
    assert len(result["result"]["steps"]) == len(dance.SEQUENCE)


def test_sequence_interleaves_release_arm_between_distinct_gestures():
    # Regression guard for the arm-latch hazard: a gesture completes and
    # latches, so the next DIFFERENT gesture id is refused (7401) unless
    # RELEASE_ARM(99) runs first. Every distinct gesture in SEQUENCE must be
    # immediately followed by RELEASE_ARM before the next distinct one.
    for i in range(len(dance.SEQUENCE) - 1):
        current, nxt = dance.SEQUENCE[i], dance.SEQUENCE[i + 1]
        if current != g1_protocol.Gesture.RELEASE_ARM:
            assert nxt == g1_protocol.Gesture.RELEASE_ARM, (
                f"step {i} ({current.name}) must be followed by RELEASE_ARM, got {nxt.name}"
            )


def test_every_sequence_gesture_is_in_the_official_action_table():
    # Same verified-id guard as test_g1_request.py's equivalent test — dance
    # bypasses SKILL_REQUESTS entirely (it's a custom multi-step skill like
    # walk_velocity, not a _g1_request.py one-shot dispatch), so nothing else
    # checks these ids against the robot's own GetActionList.
    official = {
        1,
        11,
        12,
        13,
        15,
        17,
        18,
        19,
        20,
        21,
        22,
        23,
        24,
        25,
        26,
        27,
        28,
        29,
        30,
        33,
        34,
        36,
        99,
    }
    for gesture in dance.SEQUENCE:
        assert gesture.value in official, f"{gesture.name}={gesture.value} is not a vendor action"


@pytest.mark.asyncio
async def test_rpc_error_marks_task_failed_and_stops_sequence(monkeypatch):
    monkeypatch.setattr(dance, "SIM_MODE", "real")
    calls: list[int] = []

    def fake_call_arm(gesture: int):
        calls.append(gesture)
        # Fail on the second call (the first RELEASE_ARM).
        return (3104, None) if len(calls) == 2 else (0, "")

    monkeypatch.setattr(g1_rpc, "call_arm", fake_call_arm)

    result = await dance.run()

    assert result["status"] == "failed"
    assert result["phase"] == "rpc_error"
    assert result["error"] == "rpc_error_code_3104"
    # Should not continue dispatching the rest of the sequence after a failure.
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_cancel_stops_before_next_step(monkeypatch):
    monkeypatch.setattr(dance, "SIM_MODE", "real")
    calls: list[int] = []
    # call_arm runs off-thread via asyncio.to_thread, so block the first call
    # on a real threading.Event until the test has had a deterministic chance
    # to cancel — an asyncio.sleep-based race would be flaky, since a trivial
    # fake call can finish the whole sequence before a short sleep elapses.
    release_first_call = threading.Event()

    def fake_call_arm(gesture: int):
        calls.append(gesture)
        if len(calls) == 1:
            release_first_call.wait(timeout=2.0)
        return 0, ""

    monkeypatch.setattr(g1_rpc, "call_arm", fake_call_arm)

    from bridge.skills.task_runtime import get_registry

    async def run_and_cancel():
        run_coro = dance.run()
        task = asyncio.ensure_future(run_coro)
        # Task registration happens synchronously before the (blocked) first
        # call, so this doesn't race the block above.
        while not get_registry().list_active():
            await asyncio.sleep(0.005)
        active = get_registry().list_active()
        assert len(active) == 1
        active[0].cancel_event.set()
        release_first_call.set()
        return await task

    result = await run_and_cancel()

    assert result["status"] == "cancelled"
    # Cancel is checked at the top of each loop iteration, so only the
    # already-in-flight first call completes before the sequence stops.
    assert len(calls) == 1
