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
    "walk_velocity",
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


def test_apps_back_does_not_reintroduce_a_second_catalogue():
    """There must be exactly one place that says what the robot can do.

    apps/back used to hold 28 hand-written skill files restating this server's
    tool names, descriptions and parameter schemas. They drifted into the LLM's
    prompt — a `voice` parameter the bridge did not accept, defaulted
    parameters marked required so the model invented walking timeouts, and
    `say` advertised as a stub after real TTS shipped. A name-comparison test
    caught none of it, because names were the only thing that still matched.

    back now derives the catalogue from `list_tools()`, so drift is structurally
    impossible rather than merely tested for. This guards that: a new
    `defineSkill(` under apps/back/src/skills means someone has started a second
    catalogue again, and the drift will be back within a few commits.
    """
    from pathlib import Path

    skills_dir = Path(__file__).resolve().parents[3] / "apps" / "back" / "src" / "skills"
    if not skills_dir.is_dir():
        pytest.skip(f"apps/back not present at {skills_dir}")

    offenders = [
        ts.name for ts in skills_dir.glob("*.ts") if "defineSkill(" in ts.read_text()
    ]
    assert not offenders, (
        "apps/back has hand-written skill definitions again: "
        f"{sorted(offenders)}. The catalogue comes from the bridge — add the tool "
        "here, with meta=skill_meta(...), and back picks it up."
    )


def test_every_tool_carries_complete_safety_metadata(tools):
    """`_meta` is how safety information reaches clients — including Claude Code.

    `.mcp.json` spawns this server over stdio directly, so Claude Code never
    traverses apps/back. Before this metadata existed here, every dangerLevel
    and works:false we had written lived only in TypeScript and was invisible
    to the client that actually drives the robot.

    A tool with no metadata is not a cosmetic gap — it is a tool an agent will
    treat as safe by default.
    """
    required = {
        "classification",
        "danger_level",
        "status",
        "cancellable",
        "expected_duration_s",
        "works",
        "preconditions",
        "typical_failure_modes",
    }
    for name, tool in tools.items():
        assert tool.meta, f"{name} has no _meta"
        c3po = tool.meta.get("c3po")
        assert c3po, f"{name}: _meta is not namespaced under 'c3po'"
        assert required <= set(c3po), f"{name}: missing {sorted(required - set(c3po))}"
        assert set(c3po["works"]) == {"sim", "real"}, f"{name}: malformed works"
        assert c3po["danger_level"] in {"low", "medium", "high"}, name
        assert c3po["status"] in {"real", "stub", "planned"}, name


def test_untested_motion_is_not_advertised_as_working(tools):
    """Skills never executed on hardware must not claim works.real.

    This is the flag that decides whether an agent will command a humanoid it
    has never successfully commanded before. These four are all accepted by the
    firmware — rpc code 0 — and none has been observed to do what it says.
    Flipping one to True because it "should" work is exactly the reasoning the
    flag exists to interrupt; it should be flipped by someone who watched it
    happen.
    """
    never_run_on_hardware = {
        "walk_to",           # SET_VELOCITY accepted; non-zero velocity never executed
        "turn",              # same path
        "start_walking_waist",  # 501 has never been sent to this robot
        "balance_stand",     # accepted with code 0, no observed effect
    }
    for name in never_run_on_hardware:
        works = tools[name].meta["c3po"]["works"]
        assert works["real"] is False, (
            f"{name} claims works.real — has someone actually watched it work? "
            "If so, say so in the commit; if not, this is how untested motion "
            "gets commanded at a robot with people near it."
        )
