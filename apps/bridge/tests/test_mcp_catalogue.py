"""Pins the MCP tool surface — the bridge's actual contract with the outside world.

Nothing else in this package imports `bridge.mcp_server`. That is a problem
worth a dedicated test: every tool is registered by an `@mcp.tool()` decorator
and invoked by name over the wire, so a tool that gets renamed, or lost to a bad
merge, or accidentally dropped out of the module, breaks a robot capability
without failing anything. `apps/back` binds to these names as strings from
TypeScript, so nothing on that side catches it either.

Importing this module is safe only because `SIM_MODE=stub` short-circuits the
DDS init at import time (`mcp_server.py`, the `if SIM_MODE != "stub"` guard).
The fixture sets it before the first import for that reason — under any other
mode, importing would open DDS sockets.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("SIM_MODE", "stub")


@pytest.fixture(scope="module")
def tools() -> dict:
    from bridge.mcp_server import mcp

    import asyncio

    listed = asyncio.run(mcp.list_tools())
    return {t.name: t for t in listed}


# The complete surface. Adding a tool means adding it here — deliberately, so
# that "the robot gained a capability" is a visible line in a diff rather than
# something that appears silently.
EXPECTED_TOOLS = {
    # state / introspection
    "get_state",
    "check_motion_mode",
    "list_active_tasks",
    "describe_surroundings",
    # postures and mode
    "damp",
    "zero_torque",
    "prepare",
    "balance_stand",
    "start_walking",
    "start_walking_waist",
    "squat",
    "sit_g1",
    "lie_up",
    # locomotion
    "walk_to",
    "turn",
    # gestures
    "wave",
    "point_at",
    "shake_hand",
    "hug",
    "clap",
    "release_arm",
    # world model
    "remember_landmark",
    "recall_landmark",
    "list_landmarks",
    "forget_landmark",
    # control
    "cancel_task",
    "stop_everything",
    "say",
}


def test_tool_surface_is_exactly_what_we_expect(tools):
    actual = set(tools)
    assert actual == EXPECTED_TOOLS, (
        f"missing: {sorted(EXPECTED_TOOLS - actual)}  unexpected: {sorted(actual - EXPECTED_TOOLS)}"
    )


def test_every_tool_has_a_description(tools):
    """The docstring IS the product — it is what the LLM reads to choose a tool.

    A tool with no description is unusable by an agent even though it is
    perfectly callable, which is exactly the kind of failure that does not
    surface until someone is standing next to a robot wondering why it ignored
    them.
    """
    undocumented = [name for name, t in tools.items() if not (t.description or "").strip()]
    assert not undocumented, f"tools with no description: {undocumented}"


def test_motion_tools_say_what_they_do_to_a_real_robot(tools):
    """Anything that can move the robot must describe the physical consequence.

    Not a style rule. These descriptions are the only thing standing between an
    LLM and a humanoid: a tool called `prepare` whose description does not
    mention that the robot stands up and takes its own weight is a trap.
    """
    movers = {
        "damp",
        "prepare",
        "balance_stand",
        "start_walking",
        "start_walking_waist",
        "squat",
        "sit_g1",
        "lie_up",
        "zero_torque",
        "walk_to",
        "turn",
    }
    for name in movers:
        desc = (tools[name].description or "").lower()
        assert len(desc) > 80, f"{name}: description too thin to be safe ({len(desc)} chars)"


def test_stop_everything_is_always_available_and_cheap(tools):
    """The halt path must never acquire preconditions.

    If `stop_everything` ever grows required arguments, an agent in trouble has
    to construct them correctly while the robot is moving.
    """
    schema = tools["stop_everything"].inputSchema or {}
    assert not schema.get("required"), "stop_everything must take no required arguments"


def test_read_only_tools_take_no_required_arguments(tools):
    """get_state and check_motion_mode are the two diagnostics.

    check_motion_mode in particular is the first call when the robot accepts
    commands and does nothing, so it has to be trivially callable.
    """
    for name in ("get_state", "check_motion_mode", "list_active_tasks", "list_landmarks"):
        schema = tools[name].inputSchema or {}
        assert not schema.get("required"), f"{name} should take no required arguments"


def test_stub_get_state_declares_its_env(tools):
    """Stub payloads must be self-identifying.

    A caller that cannot tell stub data from robot data will eventually treat
    one as the other. Both `env` and `stub` are part of that contract.
    """
    from bridge.mcp_server import get_state

    payload = get_state()
    assert payload["env"] == "stub"
    assert payload["stub"] is True


def test_stub_get_state_shape_drift_is_recorded():
    """Documents a KNOWN drift rather than asserting the ideal.

    The stub `get_state` pose carries no `z_meters_world` and there is no `raw`
    block, while `StateSampler.get_state()` on isaac/real returns both. So a
    client developed against stub meets a different contract than one developed
    against the robot.

    This is pinned, not fixed: changing the stub payload is a contract change
    that `apps/back` may depend on, and it should be made deliberately rather
    than as a side effect of a test. If you do fix it, this test should fail and
    be deleted.
    """
    from bridge.mcp_server import get_state

    payload = get_state()
    assert "z_meters_world" not in payload["pose"], "stub pose gained z — update the contract note"
    assert "raw" not in payload, "stub gained a raw block — update the contract note"


@pytest.mark.asyncio
async def test_say_is_no_longer_a_stub_on_real(monkeypatch):
    """`say` must actually reach the voice service on real hardware.

    It was a logging stub for a long time while its description promised TTS —
    the kind of gap an agent cannot detect, because a stub returns success.
    """
    import bridge.mcp_server as server
    from bridge.sdk import g1_protocol, g1_rpc

    monkeypatch.setattr(server, "SIM_MODE", "real")
    seen: dict = {}

    def fake_speak(text: str, speaker_id: int):
        seen["text"] = text
        seen["speaker_id"] = speaker_id
        return 0, ""

    monkeypatch.setattr(g1_rpc, "speak", fake_speak)

    result = await server.say(text="hello", language="chinese")

    assert seen["text"] == "hello"
    assert seen["speaker_id"] == g1_protocol.Speaker.CHINESE
    assert result["stub"] is False
    assert result["status"] == "ok"


def test_tts_index_increments_unlike_the_vendor_client():
    """The vendor's TtsMaker does `self.tts_index += self.tts_index` from 0.

    That leaves `index` at 0 on every call. If the firmware dedupes on it, the
    second utterance onward is silently dropped. We send our own index; this
    pins that it actually moves.
    """
    import json

    from bridge.sdk import g1_rpc

    sent: list[int] = []

    class FakeClient:
        def call_raw(self, api_id: int, param: str):
            sent.append(json.loads(param)["index"])
            return 0, ""

    g1_rpc._voice_client = FakeClient()  # type: ignore[assignment]
    try:
        g1_rpc.speak("one")
        g1_rpc.speak("two")
        g1_rpc.speak("three")
    finally:
        g1_rpc._voice_client = None

    assert sent == sorted(set(sent)), f"index must be strictly increasing, got {sent}"
    assert len(set(sent)) == 3, f"vendor bug reproduced — index stuck: {sent}"
