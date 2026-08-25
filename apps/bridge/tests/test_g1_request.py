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

    def fake_call_sport_api(api_id: int, data: int):
        seen["api_id"] = api_id
        seen["mode"] = data
        return 0, ""

    monkeypatch.setattr(g1_rpc, "call_sport_api", fake_call_sport_api)

    result = await _g1_request.run_g1_request("damp")

    assert seen["mode"] == g1_protocol.Mode.DAMP
    assert seen["api_id"] == g1_protocol.API_ID_G1_STATE
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

    # 26 is `wave_above_head` in the robot's own GetActionList. Pin the NUMBER
    # as well as the symbol: this id has now been renamed twice while staying
    # 26, and a renamed enum member must not be able to smuggle a different
    # number through.
    assert seen["gesture"] == g1_protocol.Gesture.WAVE_ABOVE_HEAD
    assert seen["gesture"] == 26
    assert result["result"]["topic_kind"] == "arm_request"


@pytest.mark.asyncio
async def test_real_mode_nonzero_rpc_code_marks_task_failed(monkeypatch):
    monkeypatch.setattr(_g1_request, "SIM_MODE", "real")
    # RPC_ERR_CLIENT_API_TIMEOUT from unitree_sdk2py.rpc.internal — firmware
    # didn't ack in time. Should surface as a failed task, not a crash.
    monkeypatch.setattr(g1_rpc, "call_sport_api", lambda api_id, data: (3104, None))

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
    monkeypatch.setattr(
        g1_rpc, "call_sport_api", lambda api_id, data: seen.setdefault("mode", data) or (0, "")
    )

    await _g1_request.run_g1_request("squat")

    assert seen["mode"] == 706
    assert seen["mode"] == g1_protocol.Mode.SQUAT_UP


@pytest.mark.asyncio
async def test_balance_stand_goes_to_7102_not_the_posture_api(monkeypatch):
    # Regression test for a whole bug class, not just this skill. The sport
    # service carries several api_ids — 7101 posture, 7102 balance mode, 7105
    # velocity — so a dispatcher that assumes 7101 would send balance_stand's
    # data (0) as a *mode index*, and 0 is ZERO_TORQUE: a standing robot would
    # go limp instead of engaging its balance controller.
    monkeypatch.setattr(_g1_request, "SIM_MODE", "real")
    seen: dict = {}

    def fake_call_sport_api(api_id: int, data: int):
        seen["api_id"] = api_id
        seen["data"] = data
        return 0, ""

    monkeypatch.setattr(g1_rpc, "call_sport_api", fake_call_sport_api)

    result = await _g1_request.run_g1_request("balance_stand")

    assert seen["api_id"] == g1_protocol.API_ID_LOCO_SET_BALANCE_MODE
    assert seen["api_id"] != g1_protocol.API_ID_G1_STATE
    assert seen["data"] == g1_protocol.BalanceMode.BALANCE_STAND
    assert result["status"] == "completed"


def test_every_gesture_id_is_in_the_official_action_table():
    """Guard against re-inventing action ids.

    The set below is the ROBOT's, read live on 2026-08-15 via GetActionList
    (arm service, api_id 7107) — 23 preset actions. It outranks the published
    table, which omits ids this firmware actually has, and it is why `point_at`
    is back on 36 (`forward_push`) after a day on 23.
    """
    # The robot's own GetActionList, read live 2026-08-15.
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

    for gesture in g1_protocol.Gesture:
        assert gesture.value in official, f"{gesture.name}={gesture.value} is not a vendor action"

    # And every arm skill must dispatch one of them.
    for name, req in g1_protocol.SKILL_REQUESTS.items():
        if req.topic_kind == "arm_request":
            assert req.data in official, f"skill {name!r} sends unknown action {req.data}"


def test_topics_for_refuses_unknown_sim_mode():
    """An unrecognized SIM_MODE must not silently resolve to the real robot.

    Regression test for a fail-open default: `topics_for` used to end in a
    bare `return REAL_TOPICS`, so "Real", "production", "" or a trailing space
    all selected real-hardware topics and dispatched live DDS RPCs, while the
    link watchdog and FSM poller (which match `== "real"` exactly) stayed off.
    """
    for bad in ("Real", "REAL", "real ", "production", "", "isaac2"):
        with pytest.raises(ValueError, match="unknown SIM_MODE"):
            g1_protocol.topics_for(bad)

    # And the known-good values still resolve, to the right side.
    assert g1_protocol.topics_for("real") is g1_protocol.REAL_TOPICS
    for good in ("isaac", "mujoco_local", "stub"):
        assert g1_protocol.topics_for(good) is g1_protocol.SIM_TOPICS


# --- an ack is not a transition ---------------------------------------------
#
# `SetFsmId` answers rpc_code 0 and does NOTHING in at least two situations met
# on 2026-08-20: asking for FSM 4 from a walk program, and asking for anything
# while no motion controller is loaded. Both reported "completed", which sent
# us checking cables and DDS config instead of the robot's state.


async def test_a_posture_that_never_transitions_says_so(monkeypatch):
    from bridge.sdk import g1_rpc
    from bridge.skills import _g1_request

    monkeypatch.setattr(_g1_request, "SIM_MODE", "real")
    monkeypatch.setattr(g1_rpc, "call_sport_api", lambda api_id, data: (0, ""))
    # The robot acks, and stays exactly where it was.
    monkeypatch.setattr(_g1_request, "_await_fsm", _fake_fsm(501))

    result = await _g1_request.run_g1_request("prepare", None)

    assert result["status"] == "completed", "the RPC did succeed — status must not lie about that"
    assert result["phase"] == "acked_no_transition"
    assert result["result"]["transitioned"] is False
    assert result["result"]["fsm_after"] == 501
    assert "controller" in result["result"]["note"]


async def test_a_posture_that_lands_reports_transitioned(monkeypatch):
    from bridge.sdk import g1_rpc
    from bridge.skills import _g1_request

    monkeypatch.setattr(_g1_request, "SIM_MODE", "real")
    monkeypatch.setattr(g1_rpc, "call_sport_api", lambda api_id, data: (0, ""))
    monkeypatch.setattr(_g1_request, "_await_fsm", _fake_fsm(4))

    result = await _g1_request.run_g1_request("prepare", None)

    assert result["phase"] == "dispatched"
    assert result["result"]["transitioned"] is True


async def test_an_unreadable_fsm_is_unverified_not_failed(monkeypatch):
    # If the check itself cannot run, the dispatch still succeeded. Reporting
    # "did not transition" on missing evidence is the overreach this guards.
    from bridge.sdk import g1_rpc
    from bridge.skills import _g1_request

    monkeypatch.setattr(_g1_request, "SIM_MODE", "real")
    monkeypatch.setattr(g1_rpc, "call_sport_api", lambda api_id, data: (0, ""))
    monkeypatch.setattr(_g1_request, "_await_fsm", _fake_fsm(None))

    result = await _g1_request.run_g1_request("prepare", None)

    assert result["status"] == "completed"
    assert result["phase"] == "dispatched", "unverified must not read as failed"
    assert result["result"]["transitioned"] is None


def _fake_fsm(value):
    async def _fake(target, timeout_s):
        return value

    return _fake
