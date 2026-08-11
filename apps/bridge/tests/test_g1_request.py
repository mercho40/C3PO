"""Tests for the posture/gesture dispatcher (`bridge.skills._g1_request`).

Covers the three SIM_MODE paths without touching DDS:
- stub: clean fake result, no dispatch attempted.
- isaac (Isaac Sim doesn't subscribe to sport/arm request topics): logged
  but not dispatched.
- real: dispatches via `bridge.sdk.g1_rpc` (mocked here — a real call would
  hit DDS) and maps the RPC result onto the task's status/phase.

`_g1_request.SIM_MODE` and `g1_rpc.call_sport`/`call_arm` are module-level,
so tests monkeypatch them directly rather than mutating the environment —
`_g1_request` reads `SIM_MODE` once at import time, so an env var set after
import wouldn't take effect.
"""

from __future__ import annotations

import pytest

from bridge.sdk import g1_protocol, g1_rpc
from bridge.skills import _g1_request


@pytest.mark.asyncio
async def test_stub_mode_is_clean_fake_no_dispatch(monkeypatch):
    monkeypatch.setattr(_g1_request, "SIM_MODE", "stub")

    result = await _g1_request.run_g1_request("damp")

    assert result["status"] == "completed"
    assert result["phase"] == "stub"
    assert result["result"]["note"] == "Stub mode — no dispatch."


@pytest.mark.asyncio
async def test_isaac_mode_logs_without_dispatch(monkeypatch):
    # Isaac Sim's scene doesn't subscribe to rt/api/{sport,arm}/request, so
    # topics_for("isaac") resolves sport_request/arm_request to None.
    monkeypatch.setattr(_g1_request, "SIM_MODE", "isaac")

    result = await _g1_request.run_g1_request("wave")

    assert result["status"] == "completed"
    assert result["phase"] == "logged_only"
    assert "doesn't subscribe" in result["result"]["note"]


@pytest.mark.asyncio
async def test_real_mode_dispatches_sport_request_via_g1_rpc(monkeypatch):
    monkeypatch.setattr(_g1_request, "SIM_MODE", "real")
    seen: dict = {}

    def fake_call_sport(mode: int):
        seen["mode"] = mode
        return 0, ""

    monkeypatch.setattr(g1_rpc, "call_sport", fake_call_sport)

    result = await _g1_request.run_g1_request("damp")

    assert seen["mode"] == g1_protocol.Mode.DAMP
    assert result["status"] == "completed"
    assert result["phase"] == "dispatched"
    assert result["result"]["rpc_code"] == 0
    assert result["error"] is None


@pytest.mark.asyncio
async def test_real_mode_dispatches_arm_request_via_g1_rpc(monkeypatch):
    monkeypatch.setattr(_g1_request, "SIM_MODE", "real")
    seen: dict = {}

    def fake_call_arm(gesture: int):
        seen["gesture"] = gesture
        return 0, ""

    monkeypatch.setattr(g1_rpc, "call_arm", fake_call_arm)

    result = await _g1_request.run_g1_request("wave")

    assert seen["gesture"] == g1_protocol.Gesture.HIGH_WAVE
    assert result["result"]["topic_kind"] == "arm_request"


@pytest.mark.asyncio
async def test_real_mode_nonzero_rpc_code_marks_task_failed(monkeypatch):
    monkeypatch.setattr(_g1_request, "SIM_MODE", "real")
    # RPC_ERR_CLIENT_API_TIMEOUT from unitree_sdk2py.rpc.internal — firmware
    # didn't ack in time. Should surface as a failed task, not a crash.
    monkeypatch.setattr(g1_rpc, "call_sport", lambda mode: (3104, None))

    result = await _g1_request.run_g1_request("prepare")

    assert result["status"] == "failed"
    assert result["phase"] == "rpc_error"
    assert result["error"] == "rpc_error_code_3104"


@pytest.mark.asyncio
async def test_real_mode_squat_sends_verified_mode_index(monkeypatch):
    # Regression test: Mode.SQUAT (2) is never sent by the reference
    # implementation for G1 — both its "Squat" and "Squat-Up" buttons send
    # SQUAT_UP (706). squat's SKILL_REQUESTS entry must match the verified
    # value, not the unverified enum member.
    monkeypatch.setattr(_g1_request, "SIM_MODE", "real")
    seen: dict = {}
    monkeypatch.setattr(g1_rpc, "call_sport", lambda mode: seen.setdefault("mode", mode) or (0, ""))

    await _g1_request.run_g1_request("squat")

    assert seen["mode"] == 706
    assert seen["mode"] == g1_protocol.Mode.SQUAT_UP
