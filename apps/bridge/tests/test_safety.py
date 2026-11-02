"""The safety governor (`bridge.safety`) and the gates it puts on the MCP path.

Every test here is about a path the LLM drives: velocity reaches the legs only
through `_locomotion.send_velocity`, and a tool runs only through the MCP
server's `call_tool`. If either can be walked around, a test here fails.
"""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from bridge import safety
from bridge.sdk import g1_rpc, perception_link
from bridge.skills import _locomotion, stop_everything


class FakeLink:
    def __init__(self, report, age=0.1):
        self.report, self.age = report, age
        self.disabled = []

    def latest_report(self):
        return self.report, self.age

    def disable(self, reason=""):
        self.disabled.append(reason)


def _report(*objects, detector_online=True, omitted=0):
    return {
        "report_version": 1,
        "detector_online": detector_online,
        "objects": list(objects),
        "objects_omitted": omitted,
    }


def person(range_m):
    return {"label": "person", "range_m": range_m, "bearing_deg": 0.0, "confidence": 0.9}


@pytest.fixture
def link(monkeypatch):
    fake = FakeLink(_report())
    monkeypatch.setattr(perception_link, "_link_singleton", fake)
    return fake


@pytest.fixture
def sent(monkeypatch):
    """What reached SET_VELOCITY, through the real dispatch path."""
    monkeypatch.setattr(_locomotion, "SIM_MODE", "real")
    calls: list[tuple[float, float, float, float]] = []
    monkeypatch.setattr(
        g1_rpc,
        "call_set_velocity",
        lambda vx, vy, w, d: calls.append((vx, vy, w, d)) or (0, ""),
    )
    return calls


# -- caps ---------------------------------------------------------------------


def test_dispatch_caps_speed_and_yaw(sent, link):
    _locomotion.send_velocity(2.0, -2.0, 5.0, height=0.78)
    assert sent[-1][:3] == (safety.MAX_LINEAR_MPS, -safety.MAX_LINEAR_MPS, safety.MAX_YAW_RPS)
    assert sent[-1][3] <= 1.0  # the firmware deadman, on every setpoint


def test_no_env_value_raises_the_caps_past_the_hard_ceiling(monkeypatch):
    monkeypatch.setenv("SAFETY_MAX_LINEAR_MPS", "3.0")
    monkeypatch.setenv("SAFETY_MAX_YAW_RPS", "9.0")
    try:
        importlib.reload(safety)
        assert safety.MAX_LINEAR_MPS == safety.HARD_MAX_LINEAR_MPS == 0.5
        assert safety.MAX_YAW_RPS == safety.HARD_MAX_YAW_RPS
    finally:
        monkeypatch.delenv("SAFETY_MAX_LINEAR_MPS")
        monkeypatch.delenv("SAFETY_MAX_YAW_RPS")
        importlib.reload(safety)
    assert safety.MAX_LINEAR_MPS == 0.3


# -- acceleration -------------------------------------------------------------


def test_a_stream_ramps_instead_of_stepping(link, monkeypatch):
    clock = iter([10.0, 10.02, 10.04])
    monkeypatch.setattr(safety.time, "monotonic", lambda: next(clock))
    safety.govern(0.0, 0.0, 0.0)
    first = safety.govern(0.3, 0.0, 0.0)[0]
    second = safety.govern(0.3, 0.0, 0.0)[0]
    assert first == pytest.approx(safety.MAX_ACCEL_MPS2 * 0.02)
    assert second == pytest.approx(2 * safety.MAX_ACCEL_MPS2 * 0.02)


def test_braking_is_never_rate_limited(link, monkeypatch):
    clock = iter([10.0, 10.01, 10.02])
    monkeypatch.setattr(safety.time, "monotonic", lambda: next(clock))
    assert safety.govern(0.3, 0.0, 0.3)[0] == 0.3  # from rest
    assert safety.govern(0.0, 0.0, 0.0) == (0.0, 0.0, 0.0)  # 10 ms later: full stop
    assert safety.govern(0.0, 0.0, 0.0) == (0.0, 0.0, 0.0)


def test_a_reversal_brakes_to_zero_then_ramps(link, monkeypatch):
    clock = iter([10.0, 10.02])
    monkeypatch.setattr(safety.time, "monotonic", lambda: next(clock))
    safety.govern(0.3, 0.0, 0.0)
    assert safety.govern(-0.3, 0.0, 0.0)[0] == pytest.approx(-safety.MAX_ACCEL_MPS2 * 0.02)


# -- the person-distance rule -------------------------------------------------


@pytest.mark.parametrize(
    ("range_m", "expected"),
    [(0.3, 0.0), (1.0, 0.3 * safety.PERSON_SLOW_FACTOR), (3.0, 0.3)],
)
def test_person_distance_scales_every_source(sent, link, range_m, expected):
    link.report = _report(person(range_m), {"label": "chair", "range_m": 0.2})
    _locomotion.send_velocity(0.3, 0.0, 0.0, height=0.78)  # manual
    assert sent[-1][0] == pytest.approx(expected)
    safety.reset()
    _locomotion.send_velocity(0.3, 0.0, 0.0, height=0.78, autonomous=True)
    assert sent[-1][0] == pytest.approx(expected)


def test_stale_perception_slows_autonomous_motion_only(sent, link):
    link.age = 1.5  # older than PERCEPTION_STALE_S
    _locomotion.send_velocity(0.3, 0.0, 0.0, height=0.78)
    assert sent[-1][0] == 0.3  # the console walk pad works without perception
    safety.reset()
    _locomotion.send_velocity(0.3, 0.0, 0.0, height=0.78, autonomous=True)
    assert sent[-1][0] == pytest.approx(0.3 * safety.PERSON_SLOW_FACTOR)


@pytest.mark.parametrize(
    "report",
    [
        None,  # no report at all
        _report(detector_online=False),  # nav up, detector down
        _report(omitted=3),  # truncated: a person may be in what was dropped
        _report({"label": "person", "range_m": "near"}),  # unreadable range
    ],
)
def test_unknown_is_not_clear_for_nav2(sent, link, report):
    link.report = report
    perception_link._default_forward(0.3, 0.0, 0.0)  # Nav2's one path to the legs
    assert sent[-1][0] == pytest.approx(0.3 * safety.PERSON_SLOW_FACTOR)


def test_nav2_with_no_perception_link_at_all_is_slowed(sent, monkeypatch):
    monkeypatch.setattr(perception_link, "_link_singleton", None)
    perception_link._default_forward(0.3, 0.0, 0.0)
    assert sent[-1][0] == pytest.approx(0.3 * safety.PERSON_SLOW_FACTOR)
    assert perception_link._link_singleton is None  # reading never creates a link


def test_stale_policy_stop(sent, link, monkeypatch):
    monkeypatch.setattr(safety, "STALE_POLICY", "stop")
    link.report = None
    perception_link._default_forward(0.3, 0.0, 0.3)
    assert sent[-1][:3] == (0.0, 0.0, 0.0)


# -- risk tiers on the MCP path -----------------------------------------------


@pytest.fixture
def mcp(monkeypatch):
    from bridge import mcp_server

    monkeypatch.setattr(safety, "T2_CONFIRM", True)
    return mcp_server.mcp


async def _tools(mcp):
    return {t.name: t.meta["c3po"]["tier"] for t in await mcp.list_tools()}


@pytest.mark.asyncio
async def test_free_form_arm_and_hand_are_t2_and_stops_are_t0(mcp):
    tiers = await _tools(mcp)
    assert {n for n, t in tiers.items() if t == "T2"} == {"move_arm", "set_hand"}
    for name in ("stop_everything", "soft_stop", "get_state", "say", "describe_surroundings"):
        assert tiers[name] == "T0", name
    for name in ("walk_to", "walk_velocity", "turn", "arm_navigation", "wave"):
        assert tiers[name] == "T1", name
    # Releases must never wait on a confirmation.
    assert tiers["open_hands"] != "T2" and tiers["release_arm_control"] != "T2"


@pytest.mark.asyncio
async def test_t2_over_mcp_is_refused_without_confirmation(mcp, link):
    with pytest.raises(ToolError, match="needs an operator's confirmation"):
        await mcp.call_tool("set_hand", {"side": "right", "closure": 0.5})


@pytest.mark.asyncio
async def test_a_confirmation_is_one_shot_and_per_tool(mcp, link):
    safety.grant_confirmation("set_hand", via="console")
    with pytest.raises(ToolError):
        await mcp.call_tool("move_arm", {"elbow_deg": 10})  # not the tool confirmed
    await mcp.call_tool("set_hand", {"side": "right", "closure": 0.5})
    with pytest.raises(ToolError):
        await mcp.call_tool("set_hand", {"side": "right", "closure": 0.5})


@pytest.mark.asyncio
async def test_an_expired_confirmation_is_refused(mcp, link, monkeypatch):
    safety.grant_confirmation("set_hand", via="console")
    now = safety.time.monotonic()
    monkeypatch.setattr(safety.time, "monotonic", lambda: now + safety.CONFIRM_TTL_S + 1)
    with pytest.raises(ToolError):
        await mcp.call_tool("set_hand", {"side": "right", "closure": 0.5})


@pytest.mark.asyncio
async def test_t2_is_refused_with_a_person_in_reach_even_when_confirmed(mcp, link):
    link.report = _report(person(0.6))
    safety.grant_confirmation("set_hand", via="console")
    with pytest.raises(ToolError, match="inside arm's reach"):
        await mcp.call_tool("set_hand", {"side": "right", "closure": 0.5})
    link.report = _report(person(2.0))
    await mcp.call_tool("set_hand", {"side": "right", "closure": 0.5})  # grant was kept


@pytest.mark.asyncio
async def test_t0_and_t1_tools_are_not_gated(mcp, link):
    await mcp.call_tool("get_state", {})
    await mcp.call_tool("walk_velocity", {"vx": 0.1})


def test_nothing_on_the_tool_surface_can_grant_a_confirmation():
    """`grant_confirmation` is called from the console route and nowhere else.

    The model sees only tools. If a tool body ever grants, the model can
    confirm itself, and T2 is decoration."""
    src = Path(safety.__file__).with_name("mcp_server.py").read_text()
    callers = [
        node.name
        for node in ast.walk(ast.parse(src))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(c, ast.Call) and getattr(c.func, "attr", "") == "grant_confirmation"
            for c in ast.walk(node)
        )
    ]
    assert callers == ["safety_confirm"]


# -- the console route --------------------------------------------------------


class FakeRequest:
    def __init__(self, body, secret=None):
        self._body = body
        self.headers = {"x-c3po-confirm-secret": secret} if secret is not None else {}

    async def json(self):
        return self._body


@pytest.mark.asyncio
async def test_confirm_route_fails_closed_without_a_secret(monkeypatch):
    from bridge import mcp_server

    monkeypatch.setattr(safety, "CONFIRM_SECRET", "")
    resp = await mcp_server.safety_confirm(FakeRequest({"tool": "set_hand"}, secret=""))
    assert resp.status_code == 503
    assert safety.status()["pending_confirmations"] == []


@pytest.mark.asyncio
async def test_confirm_route_checks_secret_and_tier(monkeypatch):
    from bridge import mcp_server

    monkeypatch.setattr(safety, "CONFIRM_SECRET", "s3cret")
    wrong = await mcp_server.safety_confirm(FakeRequest({"tool": "set_hand"}, secret="nope"))
    assert wrong.status_code == 403
    not_t2 = await mcp_server.safety_confirm(FakeRequest({"tool": "walk_to"}, secret="s3cret"))
    assert not_t2.status_code == 400
    ok = await mcp_server.safety_confirm(FakeRequest({"tool": "set_hand"}, secret="s3cret"))
    assert ok.status_code == 200
    assert safety.status()["pending_confirmations"] == ["set_hand"]


# -- soft stop ----------------------------------------------------------------


@pytest.mark.asyncio
async def test_soft_stop_closes_the_gate_and_never_damps(sent, monkeypatch):
    fake = FakeLink(None)
    monkeypatch.setattr(perception_link, "get_link", lambda: fake)
    monkeypatch.setattr(stop_everything, "SIM_MODE", "real")
    monkeypatch.setattr(
        g1_rpc, "call_set_velocity", lambda *a: sent.append(a) or (3104, None)
    )  # the robot hears nothing: stop_everything would damp here
    damp = []
    monkeypatch.setattr(g1_rpc, "call_sport", lambda mode: damp.append(mode) or (0, ""))

    result = await stop_everything.soft_stop()

    assert fake.disabled == ["soft_stop"]
    assert damp == []
    assert result["heard"] is False
    assert sent and all(a[:3] == (0.0, 0.0, 0.0) for a in sent)
