"""run_sequence: arm, hand, gestures and speech as one action, confirmed once."""

from __future__ import annotations

import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from bridge import estop, mcp_server, safety

STEPS = [
    {"tool": "move_arm", "args": {"side": "right", "shoulder_pitch_deg": 80}},
    {"tool": "set_hand", "args": {"side": "right", "shape": "saludo_peronista"}},
    {"tool": "say", "args": {"text": "¡Viva!"}},
]


@pytest.fixture
def ran(monkeypatch):
    """The steps that reached their tool, in order; run_sequence itself runs for real."""
    monkeypatch.setattr(safety, "T2_CONFIRM", True)
    monkeypatch.setattr(mcp_server.motion_announce, "applies", lambda name, meta: False)
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
    listed = mcp_server.mcp._tool_manager.get_tool("run_sequence")
    args = mcp_server._canonical_args(listed, {"steps": steps})
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
    for name in mcp_server.SEQUENCE_TOOLS:
        tool = mcp_server.mcp._tool_manager.get_tool(name)
        assert tool is not None, name
        assert tool.meta["c3po"]["classification"] in ("gesture", "speech"), name
    tier = mcp_server.mcp._tool_manager.get_tool("run_sequence").meta["c3po"]["tier"]
    assert tier == "T2"  # always confirmed, and offered by voice like the arm and hand
