"""What the bridge serves stays what it served: every tool, every HTTP route, the stop path.

A net under restructuring `mcp_server.py` into modules. contracts/bridge-tools.json
already pins each tool's tier, classification and parameters, but leaves out the
prose; the snapshots here keep everything a client can see, the descriptions the
model reads included, and the (methods, path, handler) of every HTTP route.

Regenerate only for a change that means to alter the surface, and say so in the
commit: `C3PO_UPDATE_SNAPSHOTS=1 uv run pytest tests/test_bridge_surface.py`.
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError
from bridge import config

SNAPSHOTS = Path(__file__).resolve().parent / "snapshots"
UPDATE = os.environ.get("C3PO_UPDATE_SNAPSHOTS") == "1"


def _matches_snapshot(name: str, value: Any) -> None:
    path = SNAPSHOTS / name
    text = json.dumps(value, indent=2, sort_keys=True, ensure_ascii=False) + "\n"
    if UPDATE:
        path.parent.mkdir(exist_ok=True)
        path.write_text(text)
    assert path.read_text() == text, (
        f"{name} changed. If the bridge's surface was meant to change, regenerate with "
        "C3PO_UPDATE_SNAPSHOTS=1 and say why in the commit; otherwise a move changed behaviour."
    )


def _tools() -> dict[str, Any]:
    from bridge.mcp_server import mcp

    listed = asyncio.run(mcp.list_tools())
    return {t.name: t.model_dump(mode="json", by_alias=True, exclude_none=True) for t in listed}


def _routes() -> list[dict[str, Any]]:
    from bridge import mcp_server

    out = []
    for route in mcp_server._http_app().routes:
        endpoint = getattr(route, "endpoint", None)
        out.append(
            {
                "path": route.path,
                "methods": sorted(getattr(route, "methods", None) or []),
                "handler": getattr(endpoint, "__name__", type(endpoint).__name__),
            }
        )
    return sorted(out, key=lambda r: (r["path"], r["methods"]))


def test_the_tool_catalogue_is_unchanged():
    """Names, descriptions, parameter schemas and `_meta` of all tools, prose included."""
    _matches_snapshot("bridge_tools.json", _tools())


def test_the_http_routes_are_unchanged():
    _matches_snapshot("bridge_routes.json", _routes())


# -- the stop path ---------------------------------------------------------------


@pytest.fixture
def standing_parar_and_face_gate(monkeypatch):
    """A PARAR is standing and the face gate guards every gated tool, with no face in view."""
    from bridge import estop, face_gate, safety

    monkeypatch.setattr(face_gate, "GATED", face_gate._parse("confirm, get_state"))
    monkeypatch.setattr(face_gate, "_stored", {"gate": True})
    monkeypatch.setattr(face_gate, "frame", lambda: pytest.fail("a stop read the camera"))
    monkeypatch.setattr(safety, "T2_CONFIRM", True)
    estop.signal_stop()
    assert estop.stop_is_standing()
    tools = _tools()
    # Guards the fixture: the gate is really on, and PARAR really refuses.
    assert face_gate.gated("get_state", tools["get_state"]["_meta"])
    assert safety.tool_refusal("walk_to", tools["walk_to"]["_meta"], "{}") is not None


@pytest.mark.parametrize("name", ["stop_everything", "soft_stop"])
async def test_the_stop_tools_are_t0_and_pass_the_gate_while_parar_stands(
    name, standing_parar_and_face_gate
):
    from bridge.mcp_server import mcp

    tool = mcp._tool_manager.get_tool(name)
    assert tool.meta["c3po"]["classification"] == "safety"
    assert tool.meta["c3po"]["tier"] == "T0"
    out = await mcp.call_tool(name, {})
    assert out, f"{name} returned nothing"


async def test_a_gated_tool_is_still_refused_in_that_state(standing_parar_and_face_gate):
    """Guards the test above: the same state refuses everything that is not a stop."""
    from bridge.mcp_server import mcp

    with pytest.raises(ToolError, match="PARAR"):
        await mcp.call_tool(
            "walk_to", {"target_x_meters_world_frame": 1.0, "target_y_meters_world_frame": 0.0}
        )


async def test_a_spoken_stop_reaches_the_stop_skill(monkeypatch):
    """The detector thread's callback lands on skills.stop_everything.run, on the bridge's loop."""
    from bridge.routes import voice as voice_routes
    from bridge.skills import stop_everything as stop_skill

    reached = asyncio.Event()

    async def run() -> dict:
        reached.set()
        return {"cancelled_task_ids": [], "cancelled_count": 0}

    monkeypatch.setattr(config, "SIM_MODE", "real")
    monkeypatch.setattr(stop_skill, "run", run)
    loop = asyncio.get_running_loop()
    await asyncio.to_thread(voice_routes._local_stop, loop, "parar")
    await asyncio.wait_for(reached.wait(), 2.0)


async def test_the_motion_warning_speaks_through_say_and_waits(monkeypatch):
    """server.py cannot import `say` (a cycle), so it is injected; this is that wiring."""
    from bridge import mcp_server, server  # noqa: F401 - registers every tool
    from bridge.tools import speech

    said = []

    async def say(text: str, **kwargs: object) -> dict:
        said.append((text, kwargs))
        return {"status": "ok"}

    monkeypatch.setattr(speech, "say", say)
    await server._announce_speak("Atención")
    assert said == [("Atención", {"wait_for_completion": True})]


def test_the_spoken_stop_and_the_stop_tool_are_one_function():
    """_local_stop looks stop_everything up in tools/stop.py at call time: the function
    the MCP tool runs, so a fix to one cannot miss the other."""
    import inspect

    from bridge.mcp_server import mcp
    from bridge.routes import voice
    from bridge.tools import stop

    assert mcp._tool_manager.get_tool("stop_everything").fn is stop.stop_everything
    assert "stop.stop_everything()" in inspect.getsource(voice._local_stop)
