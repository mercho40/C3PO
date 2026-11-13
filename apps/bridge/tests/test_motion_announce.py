"""The spoken warning before motion: «Atención, todos los humanos córranse: voy a …»."""

from __future__ import annotations

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from bridge import mcp_server, motion_announce

T1 = {"c3po": {"tier": "T1", "danger_level": "low", "classification": "gesture"}}


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    monkeypatch.setattr(motion_announce, "_last", ("", 0.0))
    monkeypatch.delenv("C3PO_ANNOUNCE_MOTION", raising=False)
    monkeypatch.delenv("C3PO_ANNOUNCE_MOTION_TEXT", raising=False)


@pytest.fixture
def events(monkeypatch):
    seen: list[str] = []

    async def speak(text: str) -> dict:
        seen.append(f"speak:{text}")
        return {"status": "ok"}

    monkeypatch.setattr(mcp_server, "_announce_speak", speak)
    return seen


def _server(seen: list[str]) -> mcp_server.GovernedMCP:
    server = mcp_server.GovernedMCP(name="t")

    @server.tool(name="wave", meta=T1)
    async def wave() -> dict:
        seen.append("moved")
        return {"status": "ok"}

    return server


async def test_a_moving_tool_is_announced_before_it_runs(events):
    await _server(events).call_tool("wave", {})
    assert events == [
        "speak:Atención, todos los humanos córranse: voy a saludar con la mano.",
        "moved",
    ]


async def test_a_stop_during_the_warning_means_no_motion(events, monkeypatch):
    spoke = []

    async def speak_then_parar(text: str) -> dict:
        spoke.append(text)  # a PARAR lands while the warning plays
        return {"status": "ok"}

    monkeypatch.setattr(mcp_server, "_announce_speak", speak_then_parar)
    monkeypatch.setattr(motion_announce.estop, "last_stop_at", lambda: 5.0 if spoke else 0.0)
    with pytest.raises(ToolError, match="nothing moved"):
        await _server(events).call_tool("wave", {})
    assert "moved" not in events


async def test_the_switch_off_moves_without_speaking(events, monkeypatch):
    monkeypatch.setenv("C3PO_ANNOUNCE_MOTION", "0")
    await _server(events).call_tool("wave", {})
    assert events == ["moved"]


async def test_a_refused_t2_call_says_nothing(events):
    with pytest.raises(ToolError):
        await mcp_server.mcp.call_tool("pick", {"label": "bottle"})  # no confirmation
    assert events == []


def test_stops_releases_teleop_and_reads_are_never_announced():
    meta = {t.name: t.meta for t in mcp_server.mcp._tool_manager.list_tools()}
    for name in ("walk_velocity", "stop_everything", "soft_stop", "damp", "open_hands",
                 "release_arm_control", "get_state", "say", "describe_surroundings"):
        assert not motion_announce.applies(name, meta[name]), name
    for name in ("walk_to", "go_to_object", "turn", "pick", "set_hand", "move_arm", "wave",
                 "prepare", "start_walking_waist"):
        assert motion_announce.applies(name, meta[name]), name


def test_phrases():
    assert motion_announce.phrase("pick", {"label": "bottle"}) == (
        "Atención, todos los humanos córranse: voy a agarrar la botella."
    )
    assert motion_announce.action("go_to_object", {"query": "chair"}) == "ir hasta la silla"
    assert motion_announce.action("set_hand", {"side": "right"}) == "mover la mano derecha"
    assert motion_announce.action("move_arm", {"side": "both"}) == "mover los dos brazos"
    assert motion_announce.action("turn", {"delta_yaw_radians": -1.5708}) == (
        "girar 90 grados a la derecha"
    )
    assert motion_announce.action("pick", {"label": "soporte negro"}) == "agarrar soporte negro"
    assert motion_announce.action("unknown_tool", {}) == "moverme"


async def test_the_same_warning_twice_within_seconds_is_said_once(events):
    server = _server(events)
    await server.call_tool("wave", {})
    await server.call_tool("wave", {})
    assert [e for e in events if e.startswith("speak:")] == [
        "speak:Atención, todos los humanos córranse: voy a saludar con la mano."
    ]


async def test_a_broken_speaker_does_not_block_the_motion(events, monkeypatch):
    async def broken(text: str) -> dict:
        raise RuntimeError("no speaker")

    monkeypatch.setattr(mcp_server, "_announce_speak", broken)
    await _server(events).call_tool("wave", {})
    assert events == ["moved"]
