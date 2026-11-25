"""run_sequence: arm, hand, gestures and speech as one action, confirmed once."""

from __future__ import annotations

import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from bridge import estop, mcp_server, motion_announce, safety, server
from bridge.tools import sequence
from bridge.skills import manipulation

STEPS = [
    {"tool": "move_arm", "args": {"side": "right", "shoulder_pitch_deg": 80}},
    {"tool": "set_hand", "args": {"side": "right", "shape": "saludo_peronista"}},
    {"tool": "say", "args": {"text": "¡Viva!"}},
]


@pytest.fixture
def ran(monkeypatch):
    """The steps that reached their tool, in order; run_sequence itself runs for real."""
    monkeypatch.setattr(safety, "T2_CONFIRM", True)
    monkeypatch.setattr(motion_announce, "applies", lambda name, meta: False)
    monkeypatch.setattr(sequence, "SEQUENCE_ARM_HOLD_S", 0.0)  # tests of the lowering set it
    monkeypatch.setattr(manipulation, "CLOSE_SETTLE_S", 0.0)  # the hand-settle test sets it
    manager = mcp_server.mcp._tool_manager
    real = manager.call_tool

    class Calls(list):
        answers: dict[str, dict] = {}

    calls = Calls()
    answers = calls.answers = {}

    async def call_tool(name, args, context=None, convert_result=False):
        if name == "run_sequence":
            return await real(name, args, context=context, convert_result=convert_result)
        calls.append((name, args))
        return answers.get(name, {"status": "ok"})

    monkeypatch.setattr(manager, "call_tool", call_tool)
    return calls


def _grant(steps):
    listed = mcp_server.mcp.registered_tool("run_sequence")
    args = server.canonical_args(listed, {"steps": steps})
    safety.grant_confirmation("run_sequence", args, via="console")


async def _run(steps):
    out = await mcp_server.mcp.call_tool("run_sequence", {"steps": steps})
    if isinstance(out, tuple):  # (content, structured), depending on the mcp version
        return out[1].get("result", out[1])
    return json.loads(out[0].text)


@pytest.mark.asyncio
async def test_one_confirmation_runs_every_step_in_order(ran):
    with pytest.raises(ToolError, match="confirmation"):
        await mcp_server.mcp.call_tool("run_sequence", {"steps": STEPS})
    assert ran == []
    _grant(STEPS)
    result = await _run(STEPS)
    assert result["status"] == "completed"
    assert [name for name, _ in ran] == ["move_arm", "set_hand", "say"]
    # One grant covers this list once: the same list again needs a new «Confirmar».
    with pytest.raises(ToolError, match="confirmation"):
        await mcp_server.mcp.call_tool("run_sequence", {"steps": STEPS})


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "bad",
    [
        {"tool": "walk_to", "args": {"x": 1, "y": 0}},  # locomotion is not chainable
        {"tool": "set_hand", "args": {"shape": "fist_bump"}},  # not a shape set_hand takes
        {"tool": "run_sequence", "args": {"steps": []}},  # no nesting
    ],
)
async def test_a_bad_step_anywhere_moves_nothing(ran, bad):
    steps = [STEPS[0], bad]
    _grant(steps)
    result = await _run(steps)
    assert result["status"] == "failed" and result["reason"] == "invalid_step"
    assert result["step"] == 1
    assert ran == []


@pytest.mark.asyncio
async def test_the_chain_stops_at_the_first_failed_step(ran):
    ran.answers["set_hand"] = {"status": "failed", "error": "hands_not_configured"}
    _grant(STEPS)
    result = await _run(STEPS)
    assert result["status"] == "failed" and result["step"] == 1 and result["tool"] == "set_hand"
    assert [name for name, _ in ran] == ["move_arm", "set_hand"]


@pytest.mark.asyncio
async def test_parar_still_stops_each_step(ran):
    estop.signal_stop()
    result = await mcp_server.mcp._tool_manager.call_tool("run_sequence", {"steps": STEPS})
    assert result["status"] == "refused" and result["step"] == 0
    assert "PARAR" in result["detail"]
    assert ran == []


def test_every_chainable_tool_exists_and_none_walks():
    for name in sequence.SEQUENCE_TOOLS:
        tool = mcp_server.mcp.registered_tool(name)
        assert tool is not None, name
        assert tool.meta["c3po"]["classification"] in ("gesture", "speech"), name
    tier = mcp_server.mcp.registered_tool("run_sequence").meta["c3po"]["tier"]
    assert tier == "T2"  # always confirmed, and offered by voice like the arm and hand


def test_no_chainable_tool_defers_its_warning():
    """run_sequence speaks each step's warning itself; a SELF tool would speak it twice."""
    from bridge import motion_announce

    assert not set(sequence.SEQUENCE_TOOLS) & motion_announce.SELF


@pytest.mark.asyncio
async def test_with_previous_moves_the_hand_while_the_arm_is_still_moving(ran, monkeypatch):
    import asyncio

    manager = mcp_server.mcp._tool_manager
    inner = manager.call_tool
    order: list[str] = []

    async def slow_arm(name, args, context=None, convert_result=False):
        if name == "move_arm":
            order.append("arm start")
            await asyncio.sleep(0.05)
            order.append("arm done")
        elif name == "set_hand":
            order.append("hand")
        return await inner(name, args, context=context, convert_result=convert_result)

    monkeypatch.setattr(manager, "call_tool", slow_arm)
    steps = [STEPS[0], {**STEPS[1], "with_previous": True}]
    _grant(steps)
    result = await _run(steps)
    assert result["status"] == "completed"
    assert order == ["arm start", "hand", "arm done"]
    assert [d["tool"] for d in result["done"]] == ["move_arm", "set_hand"]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "steps",
    [
        [{**STEPS[1], "with_previous": True}],  # nothing before it
        [STEPS[2], {**STEPS[1], "with_previous": True}],  # speech is not a partner
        [STEPS[0], {**STEPS[2], "with_previous": True}],  # only a hand step runs alongside
    ],
)
async def test_with_previous_is_only_a_hand_step_after_an_arm_move(ran, steps):
    _grant(steps)
    result = await _run(steps)
    assert result["status"] == "failed" and result["reason"] == "invalid_step"
    assert ran == []


@pytest.mark.asyncio
async def test_a_raised_arm_comes_down_by_itself_after_the_sequence(ran, monkeypatch):
    monkeypatch.setattr(sequence, "SEQUENCE_ARM_HOLD_S", 0.3)
    _grant(STEPS)
    result = await _run(STEPS)
    assert [name for name, _ in ran] == ["move_arm", "set_hand", "say", "release_arm_control"]
    assert result["arm_held_s"] >= 0.3


@pytest.mark.asyncio
async def test_keep_arm_up_or_an_explicit_release_skips_the_lowering(ran, monkeypatch):
    monkeypatch.setattr(sequence, "SEQUENCE_ARM_HOLD_S", 0.3)
    listed = mcp_server.mcp.registered_tool("run_sequence")
    args = server.canonical_args(listed, {"steps": STEPS, "keep_arm_up": True})
    safety.grant_confirmation("run_sequence", args, via="console")
    out = await mcp_server.mcp.call_tool("run_sequence", {"steps": STEPS, "keep_arm_up": True})
    assert "release_arm_control" not in [name for name, _ in ran]
    released = [*STEPS, {"tool": "release_arm_control", "args": {}}]
    ran.clear()
    _grant(released)
    await _run(released)
    assert [name for name, _ in ran].count("release_arm_control") == 1
    assert out is not None


@pytest.mark.asyncio
async def test_a_hand_step_settles_before_the_next_step(ran, monkeypatch):
    """2026-11-16: a close then an open went out in the same second; the hand never closed."""
    import time

    monkeypatch.setattr(manipulation, "CLOSE_SETTLE_S", 0.2)
    manager = mcp_server.mcp._tool_manager
    inner = manager.call_tool
    at: list[tuple[str, float]] = []

    async def timed(name, args, context=None, convert_result=False):
        at.append((name, time.monotonic()))
        return await inner(name, args, context=context, convert_result=convert_result)

    monkeypatch.setattr(manager, "call_tool", timed)
    close = {"tool": "set_hand", "args": {"side": "right", "closure": 1}}
    steps = [close, {"tool": "open_hands", "args": {}}]
    _grant(steps)
    assert (await _run(steps))["status"] == "completed"
    (_, t_close), (_, t_open) = [a for a in at if a[0] != "run_sequence"]
    assert t_open - t_close >= 0.2

    # with_previous: the hand moves WITH the arm, so nothing waits between them.
    at.clear()
    steps = [STEPS[0], {**close, "with_previous": True}]
    _grant(steps)
    assert (await _run(steps))["status"] == "completed"
    (_, t_arm), (_, t_hand) = [a for a in at if a[0] != "run_sequence"]
    assert t_hand - t_arm < 0.1
