"""Tests for the `walk_velocity` MCP tool (`bridge.mcp_server`) -- had zero
coverage despite being the one MCP tool whose safety depends on two
independent bound definitions (the `Field(ge=.../le=...)` the MCP client
sees, and the skill's own internal `MAX_LINEAR_VEL`/`MAX_YAW_VEL`/
`MAX_DURATION_S` clamps) staying in sync. Nothing enforced that today --
this guards against the two silently drifting apart.

Importing `bridge.mcp_server` triggers `init_dds()` at module scope unless
SIM_MODE=="stub" at import time (see the module docstring) -- these tests
rely on SIM_MODE defaulting to "stub" in the test process (no SIM_MODE env
var set), matching every other test file in this suite.
"""

from __future__ import annotations

import inspect
import typing

import pytest

from bridge import mcp_server
from bridge.skills import walk_velocity as walk_velocity_skill


def _bounds(param_name: str) -> tuple[float, float]:
    sig = inspect.signature(mcp_server.walk_velocity, eval_str=True)
    annotation = sig.parameters[param_name].annotation
    _, field_info = typing.get_args(annotation)
    constraints = {type(c).__name__: c for c in field_info.metadata}
    return constraints["Ge"].ge, constraints["Le"].le


def test_mcp_tool_bounds_match_skill_clamps_for_linear_and_yaw_velocity():
    for param in ("vx", "vy", "vyaw"):
        lo, hi = _bounds(param)
        assert lo == -walk_velocity_skill.MAX_LINEAR_VEL if param != "vyaw" else lo == -walk_velocity_skill.MAX_YAW_VEL
        assert hi == walk_velocity_skill.MAX_LINEAR_VEL if param != "vyaw" else hi == walk_velocity_skill.MAX_YAW_VEL


def test_mcp_tool_bounds_match_skill_duration_ceiling():
    lo, hi = _bounds("duration_s")
    assert hi == walk_velocity_skill.MAX_DURATION_S
    assert lo > 0.0  # a floor exists; exact value isn't safety-critical like the ceiling


@pytest.mark.asyncio
async def test_stub_mode_returns_fake_result_without_dispatching(monkeypatch):
    monkeypatch.setattr(mcp_server, "SIM_MODE", "stub")
    called = []
    monkeypatch.setattr(walk_velocity_skill, "run", lambda **kw: called.append(kw))

    result = await mcp_server.walk_velocity(ctx=None, vx=0.1, vy=0.0, vyaw=0.0, duration_s=1.0)

    assert called == []
    assert result["stub"] is True
    assert result["env"] == "stub"
    assert "task_id" in result


@pytest.mark.asyncio
async def test_isaac_mode_is_not_applicable_and_does_not_dispatch(monkeypatch):
    monkeypatch.setattr(mcp_server, "SIM_MODE", "isaac")
    called = []
    monkeypatch.setattr(walk_velocity_skill, "run", lambda **kw: called.append(kw))

    result = await mcp_server.walk_velocity(ctx=None, vx=0.1, vy=0.0, vyaw=0.0, duration_s=1.0)

    assert called == []
    assert result["status"] == "not_applicable"
    assert result["env"] == "isaac"


@pytest.mark.asyncio
async def test_real_mode_dispatches_to_the_skill_and_tags_env(monkeypatch):
    monkeypatch.setattr(mcp_server, "SIM_MODE", "real")

    async def fake_run(vx, vy, vyaw, duration_s, ctx=None):
        return {"status": "completed", "phase": "duration_elapsed"}

    monkeypatch.setattr(walk_velocity_skill, "run", fake_run)

    result = await mcp_server.walk_velocity(ctx=None, vx=0.1, vy=0.0, vyaw=0.0, duration_s=1.0)

    assert result["status"] == "completed"
    assert result["env"] == "real"
