"""Tests for the set_hand / open_hands skills (`bridge.skills.hand`).

The driver seam (`bridge.teleop.hands`) already tests wire formats and the
polarity refusal; here the concern is the skill layer: honest stub/sim
branches, unconfigured hands reported rather than guessed, sides outside
TELEOP_HAND_SIDES skipped loudly, and the no-dead-man warning on every grip.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from bridge import config
from bridge.skills import hand
from bridge.teleop import hands


class FakeHandDriver(hands.HandDriver):
    name = "brainco"

    def __init__(self, sides=("right",)) -> None:
        self.sides = tuple(sides)
        self.sent: list[tuple[str, float]] = []

    def send(self, side, grip) -> None:
        self.sent.append((side, grip))


@pytest.mark.asyncio
async def test_stub_mode_is_self_identifying():
    result = await hand.run_set("right", 0.5)
    assert result["stub"] is True
    assert result["status"] == "ok"


@pytest.mark.asyncio
async def test_sim_mode_is_not_applicable_not_fake_success(monkeypatch):
    monkeypatch.setattr(hand, "SIM_MODE", "isaac")
    result = await hand.run_set("right", 1.0)
    assert result["status"] == "not_applicable"

    result = await hand.run_open()
    assert result["status"] == "not_applicable"


@pytest.mark.asyncio
async def test_unconfigured_hands_report_the_reason(monkeypatch):
    monkeypatch.setattr(hand, "SIM_MODE", "real")
    monkeypatch.delenv("TELEOP_HAND_ENABLED", raising=False)

    result = await hand.run_set("right", 1.0)
    assert result["status"] == "unavailable"
    assert "TELEOP_HAND_ENABLED" in result["message"]


@pytest.mark.asyncio
async def test_grip_publishes_and_warns_about_the_missing_deadman(monkeypatch):
    monkeypatch.setattr(hand, "SIM_MODE", "real")
    fake = FakeHandDriver(sides=("right",))
    monkeypatch.setattr(hands, "get_driver", lambda: fake)

    result = await hand.run_set("right", 0.7)

    assert fake.sent == [("right", 0.7)]
    assert result["status"] == "ok"
    assert "dead-man" in result["warning"]


@pytest.mark.asyncio
async def test_closure_is_clamped_to_unit_range(monkeypatch):
    monkeypatch.setattr(hand, "SIM_MODE", "real")
    fake = FakeHandDriver(sides=("right",))
    monkeypatch.setattr(hands, "get_driver", lambda: fake)

    await hand.run_set("right", 3.0)
    assert fake.sent == [("right", 1.0)]


@pytest.mark.asyncio
async def test_unconfigured_side_is_skipped_loudly(monkeypatch):
    """Only the right hand has ever answered (the left was found unplugged) —
    asking for 'both' must say the left didn't move, not imply it did."""
    monkeypatch.setattr(hand, "SIM_MODE", "real")
    fake = FakeHandDriver(sides=("right",))
    monkeypatch.setattr(hands, "get_driver", lambda: fake)

    result = await hand.run_set("both", 1.0)

    assert result["driven_sides"] == ["right"]
    assert result["skipped_sides"] == ["left"]
    assert "TELEOP_HAND_SIDES" in result["message"]
    assert fake.sent == [("right", 1.0)]


@pytest.mark.asyncio
async def test_open_hands_relaxes_every_configured_side(monkeypatch):
    monkeypatch.setattr(hand, "SIM_MODE", "real")
    fake = FakeHandDriver(sides=("left", "right"))
    monkeypatch.setattr(hands, "get_driver", lambda: fake)

    result = await hand.run_open()

    # HandDriver.relax() sends 0.0 to every driven side.
    assert set(fake.sent) == {("left", 0.0), ("right", 0.0)}
    assert result["status"] == "ok"
    assert result["opened_sides"] == ["left", "right"]


class _NoServer(FakeHandDriver):
    """brainco_hand_server not started yet, as after the 2026-11-12 reboot."""

    def server_heard(self, side) -> bool:
        return False


@pytest.mark.asyncio
async def test_a_grip_with_no_hand_server_is_not_ok(monkeypatch):
    monkeypatch.setattr(hand, "SIM_MODE", "real")
    fake = _NoServer(sides=("right",))
    monkeypatch.setattr(hands, "get_driver", lambda: fake)

    result = await hand.run_set("right", 1.0)

    assert result["status"] == "unavailable"
    assert result["silent_sides"] == ["right"]
    assert "brainco_hand_server" in result["message"]
    assert fake.sent == [], "a close nobody confirmed must not be queued for a late server"


@pytest.mark.asyncio
async def test_open_hands_with_no_hand_server_still_sends_but_says_so(monkeypatch):
    monkeypatch.setattr(hand, "SIM_MODE", "real")
    fake = _NoServer(sides=("right",))
    monkeypatch.setattr(hands, "get_driver", lambda: fake)

    result = await hand.run_open()

    assert fake.sent == [("right", 0.0)]
    assert result["status"] == "unavailable"
    assert result["opened_sides"] == []
    assert "brainco_hand_server" in result["message"]


def test_brainco_hears_its_server_only_while_state_arrives(monkeypatch):
    import unitree_sdk2py.core.channel as channel

    handlers = {}

    class _Sub:
        def __init__(self, topic, _type) -> None:
            self.topic = topic

        def Init(self, handler, _depth) -> None:
            handlers[self.topic] = handler

    monkeypatch.setattr(channel, "ChannelSubscriber", _Sub)
    driver = hands.BrainCoHandDriver(open_at=0.0, sides=("right",))

    assert driver.server_heard("right", within_s=0.1) is False
    handlers["rt/brainco/right/state"](SimpleNamespace(states=[]))
    assert driver.server_heard("right", within_s=0.1) is True


def test_a_restarted_bridge_opens_the_hands(monkeypatch):
    """A BrainCo holds its last command; nothing reopened a grip left closed by the
    process that died (seven bridge restarts on 2026-11-12)."""
    import time

    from bridge import mcp_server

    fake = FakeHandDriver(sides=("left", "right"))
    monkeypatch.setattr(config, "SIM_MODE", "real")
    monkeypatch.setattr(hands, "get_driver", lambda: fake)

    mcp_server._open_hands_at_boot()
    deadline = time.monotonic() + 2.0
    while len(fake.sent) < 2 and time.monotonic() < deadline:
        time.sleep(0.01)

    assert set(fake.sent) == {("left", 0.0), ("right", 0.0)}


def test_main_opens_the_hands_before_serving(monkeypatch):
    from bridge import face_gate, mcp_server

    order: list[str] = []

    async def serve() -> None:
        order.append("serve")

    monkeypatch.setattr(face_gate, "warm", lambda: None)
    monkeypatch.setattr(mcp_server, "_open_hands_at_boot", lambda: order.append("hands"))
    monkeypatch.setattr(mcp_server, "_run_stdio", serve)
    monkeypatch.setattr(mcp_server, "_run_streamable_http", serve)
    mcp_server.main()

    assert order == ["hands", "serve"]


class ShapeRecorder(FakeHandDriver):
    def __init__(self, sides=("right",)) -> None:
        super().__init__(sides)
        self.shapes: list[tuple[str, tuple[float, ...]]] = []

    def send_shape(self, side, closures) -> None:
        self.shapes.append((side, tuple(closures)))


@pytest.fixture
def recorder(monkeypatch):
    monkeypatch.setattr(hand, "SIM_MODE", "real")
    fake = ShapeRecorder()
    monkeypatch.setattr(hands, "get_driver", lambda: fake)
    return fake


@pytest.mark.asyncio
async def test_single_finger_closes_on_an_open_base(recorder):
    result = await hand.run_set("right", 0.0, fingers={"index": 1.0})
    assert recorder.shapes == [("right", (0.0, 0.0, 1.0, 0.0, 0.0, 0.0))]
    assert result["fingers"]["index"] == 1.0 and result["fingers"]["thumb"] == 0.0
    assert result["closure"] == 1.0 and "dead-man" in result["warning"]


@pytest.mark.asyncio
async def test_fingers_override_closure_base(recorder):
    await hand.run_set("right", 1.0, fingers={"index": 0.0, "thumb": 0.2})
    assert recorder.shapes == [("right", (0.2, 1.0, 0.0, 1.0, 1.0, 1.0))]


@pytest.mark.asyncio
async def test_fingers_override_shape_base(recorder):
    await hand.run_set("right", 0.0, shape="power", fingers={"pinky": 0.0})
    assert recorder.shapes == [("right", hands.SHAPES["power"][:5] + (0.0,))]


@pytest.mark.asyncio
async def test_unknown_finger_moves_nothing(recorder):
    result = await hand.run_set("right", 0.0, fingers={"indice": 1.0})
    assert result["status"] == "failed" and result["error"] == "unknown_finger"
    assert result["unknown"] == ["indice"]
    assert recorder.shapes == [] and recorder.sent == []


@pytest.mark.asyncio
async def test_out_of_range_finger_moves_nothing(recorder):
    result = await hand.run_set("right", 0.0, fingers={"ring": 1.5})
    assert result["error"] == "finger_closure_out_of_range"
    assert recorder.shapes == [] and recorder.sent == []


def test_confirmation_is_bound_to_the_fingers():
    """A grant for one finger set must not cover another: fingers are in the canonical args."""
    from bridge import mcp_server

    tool = mcp_server.mcp._tool_manager.get_tool("set_hand")
    plain = mcp_server._canonical_args(tool, {"side": "right"})
    index = mcp_server._canonical_args(tool, {"side": "right", "fingers": {"index": 1}})
    pinky = mcp_server._canonical_args(tool, {"side": "right", "fingers": {"pinky": 1}})
    assert len({plain, index, pinky}) == 3


def test_tool_schema_rejects_unknown_finger_and_range():
    import pydantic

    from bridge import mcp_server

    tool = mcp_server.mcp._tool_manager.get_tool("set_hand")
    for bad in ({"indice": 1}, {"ring": 1.5}):
        with pytest.raises(pydantic.ValidationError):
            mcp_server._canonical_args(tool, {"side": "right", "fingers": bad})


def test_v_sign_raises_index_and_middle_and_closes_the_rest():
    # «saludo peronista»: SHAPES order is thumb, thumb_aux, index, middle, ring, pinky.
    from bridge.teleop import hands

    thumb, _aux, index, middle, ring, pinky = hands.SHAPES["v_sign"]
    assert index == middle == 0.0
    assert min(thumb, ring, pinky) >= 0.9  # the thumb tucked too, not showing


def test_saludo_peronista_is_the_v_sign_by_name():
    """A model that skips the description still finds «saludo peronista» in the enum."""
    from bridge import mcp_server

    assert hands.SHAPES["saludo_peronista"] == hands.SHAPES["v_sign"]
    schema = mcp_server.mcp._tool_manager.get_tool("set_hand").parameters
    assert "saludo_peronista" in json.dumps(schema["properties"]["shape"])
