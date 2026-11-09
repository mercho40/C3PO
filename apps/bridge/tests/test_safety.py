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
        self.armed = False  # the Nav2 gate: the geofence applies only while it is open

    def latest_report(self):
        return self.report, self.age

    def disable(self, reason=""):
        self.disabled.append(reason)

    def enable(self, reason="", ttl_s=0.0):
        self.enabled = (reason, ttl_s)  # arm_navigation's gate, never a real one
        self.armed = True

    def is_enabled(self):
        return self.armed

    def status(self):
        return {"enabled": self.armed}


def _report(*objects, detector_online=True, omitted=0, lidar_online=True):
    return {
        "report_version": 1,
        "detector_online": detector_online,
        "lidar_online": lidar_online,
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
    # Diagonal: the speed over the floor is capped, not each axis (that was 0.42 m/s).
    _locomotion.send_velocity(2.0, -2.0, 5.0, height=0.78)
    edge = safety.MAX_LINEAR_MPS / 2**0.5
    assert sent[-1][:3] == pytest.approx((edge, -edge, safety.MAX_YAW_RPS))
    assert sent[-1][3] <= 1.0  # the firmware deadman, on every setpoint


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")])
@pytest.mark.parametrize("axis", [0, 1, 2])
def test_a_non_finite_axis_stops_that_axis(link, axis, bad):
    # NaN used to clamp to the full cap; a broken number must mean stop, not full speed.
    request = [0.2, 0.1, 0.2]
    request[axis] = bad
    out = safety.govern(*request)
    assert out[axis] == 0.0
    assert all(v != 0.0 for i, v in enumerate(out) if i != axis)


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
    link.age = 1.5  # older than PERCEPTION_STALE_S: the lidar reading is stale too
    _locomotion.send_velocity(0.3, 0.0, 0.0, height=0.78)
    assert sent[-1][0] == 0.3  # the console walk pad works without perception
    safety.reset()
    # go_to_object's walk: autonomous, not Nav2, so a stale lidar slows it (person rule), never zeroes it.
    _locomotion.send_velocity(0.3, 0.0, 0.0, height=0.78, autonomous=True)
    assert sent[-1][0] == pytest.approx(0.3 * safety.PERSON_SLOW_FACTOR)


@pytest.mark.parametrize(
    "report",
    [
        _report(detector_online=False),  # nav up, detector down
        _report(omitted=3),  # truncated: a person may be in what was dropped
        _report({"label": "person", "range_m": "near"}),  # unreadable range
    ],
)
def test_unknown_is_not_clear_for_nav2(sent, link, report):
    link.report = report
    perception_link._default_forward(0.3, 0.0, 0.0)  # Nav2's one path to the legs
    assert sent[-1][0] == pytest.approx(0.3 * safety.PERSON_SLOW_FACTOR)


def test_a_stale_person_in_a_fresh_report_is_unknown_not_clear(sent, link):
    """X16: world_model_publisher re-emits a detection, so its age is the capture age."""
    link.report = _report(dict(person(3.0), age_s=2.0))  # report 0.1 s old, person 2 s
    assert safety.person_proximity() == (3.0, False)  # range kept, not fresh
    perception_link._default_forward(0.3, 0.0, 0.0)
    assert sent[-1][0] == pytest.approx(0.3 * safety.PERSON_SLOW_FACTOR)


def test_a_stale_but_close_person_still_stops_the_base_and_blocks_the_arm(sent, link):
    """Review 2026-11-04: 'not fresh' must not read as 'clear' for someone known to be close."""
    link.report = _report(dict(person(0.4), age_s=1.2))
    _locomotion.send_velocity(0.3, 0.0, 0.0, height=0.78)  # manual driving
    assert sent[-1][0] == 0.0
    assert safety.status()["last_reason"] == "person_within_stop_distance"
    link.report = _report(dict(person(0.6), age_s=1.2))
    assert "inside arm's reach" in (safety.tool_refusal("set_hand", {"c3po": {"tier": "T2"}}) or "")


@pytest.mark.parametrize("extra", [{"age_s": 0.2}, {}])  # fresh, and pre-D5 images without age_s
def test_a_fresh_or_unaged_person_still_slows_and_blocks_reach(sent, link, extra):
    link.report = _report(dict(person(1.0), **extra))
    assert safety.person_proximity() == (1.0, True)
    _locomotion.send_velocity(0.3, 0.0, 0.0, height=0.78)
    assert sent[-1][0] == pytest.approx(0.3 * safety.PERSON_SLOW_FACTOR)
    link.report = _report(dict(person(0.6), **extra))
    assert "inside arm's reach" in (safety.tool_refusal("set_hand", {"c3po": {"tier": "T2"}}) or "")


def test_nav2_with_no_perception_link_at_all_is_stopped(sent, monkeypatch):
    monkeypatch.setattr(perception_link, "_link_singleton", None)
    perception_link._default_forward(0.3, 0.0, 0.0)
    assert sent[-1][0] == 0.0
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


def _grant(tool, args):
    """Grant the way /safety/confirm does: bound to the real canonical arguments."""
    from bridge import mcp_server

    listed = mcp_server.mcp._tool_manager.get_tool(tool)
    return safety.grant_confirmation(tool, mcp_server._canonical_args(listed, args), via="console")


@pytest.mark.asyncio
async def test_free_form_arm_and_hand_are_t2_and_stops_are_t0(mcp):
    tiers = await _tools(mcp)
    assert {n for n, t in tiers.items() if t == "T2"} == {
        "move_arm",
        "set_hand",
        "reach",
        "pick",
        "place",
        "handover",
    }
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
    _grant("set_hand", {"side": "right", "closure": 0.5})
    with pytest.raises(ToolError):
        await mcp.call_tool("move_arm", {"elbow_deg": 10})  # not the tool confirmed
    await mcp.call_tool("set_hand", {"side": "right", "closure": 0.5})
    with pytest.raises(ToolError):
        await mcp.call_tool("set_hand", {"side": "right", "closure": 0.5})


@pytest.mark.asyncio
async def test_an_expired_confirmation_is_refused(mcp, link, monkeypatch):
    _grant("set_hand", {"side": "right", "closure": 0.5})
    now = safety.time.monotonic()
    monkeypatch.setattr(safety.time, "monotonic", lambda: now + safety.CONFIRM_TTL_S + 1)
    with pytest.raises(ToolError):
        await mcp.call_tool("set_hand", {"side": "right", "closure": 0.5})


@pytest.mark.asyncio
async def test_t2_is_refused_with_a_person_in_reach_even_when_confirmed(mcp, link):
    link.report = _report(person(0.6))
    _grant("set_hand", {"side": "right", "closure": 0.5})
    with pytest.raises(ToolError, match="inside arm's reach"):
        await mcp.call_tool("set_hand", {"side": "right", "closure": 0.5})
    link.report = _report(person(2.0))
    # The refusal spent the grant: the operator confirms again once the person is clear.
    with pytest.raises(ToolError, match="needs an operator's confirmation"):
        await mcp.call_tool("set_hand", {"side": "right", "closure": 0.5})


@pytest.mark.asyncio
async def test_a_grant_is_bound_to_its_arguments(mcp, link):
    """11.1: a grant for one argument set cannot be spent on another."""
    _grant("set_hand", {"closure": 0.5})
    with pytest.raises(ToolError, match="exactly the same arguments"):
        await mcp.call_tool("set_hand", {"closure": 0.9})
    # Same arguments in another key order, as a string, with the defaults written out: once.
    await mcp.call_tool("set_hand", {"closure": "0.5", "side": "right", "shape": None})
    with pytest.raises(ToolError, match="needs an operator's confirmation"):
        await mcp.call_tool("set_hand", {"closure": 0.5})


@pytest.mark.asyncio
async def test_invalid_t2_arguments_are_reported_but_parar_comes_first(mcp, link):
    from bridge import estop

    with pytest.raises(ToolError, match="invalid arguments for set_hand"):
        await mcp.call_tool("set_hand", {"closure": "abc"})
    estop.signal_stop()
    with pytest.raises(ToolError, match="PARAR"):
        await mcp.call_tool("set_hand", {"closure": "abc"})


@pytest.mark.asyncio
async def test_the_confirmable_set_is_t2_plus_high_danger_t1(mcp):
    """1.3: pinned from the live catalogue, so a new high-danger T1 shows up in review."""
    gated = {t.name for t in await mcp.list_tools() if safety.needs_confirmation(t.meta)}
    assert gated == {
        "move_arm", "set_hand", "reach", "pick", "place", "handover",
        "arm_navigation", "start_walking_waist", "zero_torque",
    }  # fmt: skip


HIGH_T1 = (
    ("start_walking_waist", {}),
    ("arm_navigation", {"reason": "test arm", "seconds": 5}),
    ("zero_torque", {}),
)


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "args"), HIGH_T1)
async def test_high_danger_t1_needs_a_confirmation_and_runs_once_after_one(mcp, link, tool, args):
    with pytest.raises(ToolError, match="a high-danger tool and needs an operator's confirmation"):
        await mcp.call_tool(tool, args)
    _grant(tool, args)
    await mcp.call_tool(tool, args)
    with pytest.raises(ToolError, match="needs an operator's confirmation"):
        await mcp.call_tool(tool, args)


@pytest.mark.asyncio
@pytest.mark.parametrize(("tool", "args"), HIGH_T1)
async def test_confirmation_off_opens_high_danger_t1_too(mcp, link, monkeypatch, tool, args):
    monkeypatch.setattr(safety, "T2_CONFIRM", False)
    await mcp.call_tool(tool, args)


@pytest.mark.asyncio
async def test_the_reach_rule_is_t2_only(mcp, link):
    link.report = _report(person(0.6))
    _grant("start_walking_waist", {})
    await mcp.call_tool("start_walking_waist", {})


@pytest.mark.asyncio
async def test_t0_and_t1_tools_are_not_gated(mcp, link):
    await mcp.call_tool("get_state", {})
    await mcp.call_tool("walk_velocity", {"vx": 0.1})


@pytest.mark.asyncio
async def test_the_protocol_path_still_dispatches_through_the_gate(mcp, link):
    """Through a real ClientSession, not a Python call: an mcp upgrade that wires
    the low-level server around GovernedMCP.call_tool would fail here."""
    from mcp.shared.memory import create_connected_server_and_client_session

    from bridge import estop

    async with create_connected_server_and_client_session(mcp._mcp_server) as client:
        hand = await client.call_tool("set_hand", {"side": "right", "closure": 0.5})
        assert hand.isError and "confirmation" in hand.content[0].text
        estop.signal_stop()
        assert estop.stop_is_standing()
        walk = await client.call_tool("walk_velocity", {"vx": 0.1})
        assert walk.isError and "PARAR" in walk.content[0].text


def test_the_http_app_serves_mcp_and_the_operator_routes(mcp):
    paths = {r.path for r in mcp.streamable_http_app().routes}
    assert {
        "/mcp", "/safety/ack", "/safety/confirm", "/safety/status",
        "/camera/status", "/camera/frame.jpg", "/camera/stream.mjpg",
    } <= paths  # fmt: skip


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
    assert not_t2.status_code == 400 and b"not_confirmable" in not_t2.body
    ok = await mcp_server.safety_confirm(FakeRequest({"tool": "set_hand"}, secret="s3cret"))
    assert ok.status_code == 200
    assert safety.status()["pending_confirmations"] == ["set_hand"]


@pytest.mark.asyncio
@pytest.mark.parametrize("args", ["x", {"closure": "abc"}, None])
async def test_confirm_route_refuses_arguments_the_tool_would_refuse(monkeypatch, args):
    from bridge import mcp_server

    monkeypatch.setattr(safety, "CONFIRM_SECRET", "s3cret")
    body = {"tool": "set_hand", "args": args}
    resp = await mcp_server.safety_confirm(FakeRequest(body, secret="s3cret"))
    assert resp.status_code == 400 and b"invalid_arguments" in resp.body
    assert safety.status()["pending_confirmations"] == []


@pytest.mark.asyncio
async def test_the_console_grant_runs_the_call_it_was_made_for(mcp, link, monkeypatch):
    from bridge import mcp_server

    monkeypatch.setattr(safety, "CONFIRM_SECRET", "s3cret")
    for closure in ("0.5", 0.7):  # two grants for one tool: still one name in the list
        body = {"tool": "set_hand", "args": {"closure": closure}}
        resp = await mcp_server.safety_confirm(FakeRequest(body, secret="s3cret"))
        assert resp.status_code == 200
    assert safety.status()["pending_confirmations"] == ["set_hand"]
    await mcp.call_tool("set_hand", {"side": "right", "closure": 0.5})
    await mcp.call_tool("set_hand", {"closure": 0.7})
    with pytest.raises(ToolError):
        await mcp.call_tool("set_hand", {"closure": 0.5})


# -- the PARAR latch -----------------------------------------------------------


@pytest.mark.asyncio
async def test_a_standing_stop_refuses_motion_until_an_operator_resumes(mcp, link, monkeypatch):
    from bridge import estop, mcp_server

    estop.signal_stop()
    assert safety.status()["stop_standing"] is True
    for name, args in (("walk_velocity", {"vx": 0.1}), ("wave", {})):
        with pytest.raises(ToolError, match="PARAR"):
            await mcp.call_tool(name, args)
    await mcp.call_tool("get_state", {})  # T0 still answers: the agent can see why

    monkeypatch.setattr(safety, "CONFIRM_SECRET", "s3cret")
    denied = await mcp_server.safety_ack(FakeRequest({}, secret="nope"))
    assert denied.status_code == 403 and safety.status()["stop_standing"] is True
    ok = await mcp_server.safety_ack(FakeRequest({}, secret="s3cret"))
    assert ok.status_code == 200 and safety.status()["stop_standing"] is False
    await mcp.call_tool("walk_velocity", {"vx": 0.1})


def test_damp_is_allowed_while_stopped_and_a_new_stop_latches_again(monkeypatch):
    from bridge import estop

    estop.signal_stop()
    assert safety.tool_refusal("damp", {"c3po": {"tier": "T1"}}) is None
    assert "PARAR" in safety.tool_refusal("prepare", {"c3po": {"tier": "T1"}})
    assert "PARAR" in safety.tool_refusal("move_arm", {"c3po": {"tier": "T2"}})
    estop.record_ack()
    assert safety.tool_refusal("prepare", {"c3po": {"tier": "T1"}}) is None
    now = safety.time.time()
    estop.sentinel_path().touch()
    import os

    os.utime(estop.sentinel_path(), (now + 1, now + 1))  # a later PARAR
    assert "PARAR" in safety.tool_refusal("prepare", {"c3po": {"tier": "T1"}})


def test_nothing_on_the_tool_surface_can_clear_a_stop():
    """record_ack is reachable from the console route and the headset only."""
    src = Path(safety.__file__).with_name("mcp_server.py").read_text()
    callers = [
        node.name
        for node in ast.walk(ast.parse(src))
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and any(
            isinstance(c, ast.Call)
            and getattr(c.func, "attr", getattr(c.func, "id", "")) == "record_ack"
            for c in ast.walk(node)
        )
    ]
    assert callers == ["safety_ack"]


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


# -- review 2026-11-03: the latch cannot be walked around ---------------------

#: T0 passes the PARAR latch, so every T0 tool must move nothing (or only stop).
#: Adding a tool here is a claim about the robot: check its body calls no mover.
T0_MOVES_NOTHING = {
    "arm_state", "ask_user", "cancel_task", "check_motion_mode", "describe_surroundings",
    "find_object", "forget_landmark", "forget_object", "get_state", "list_active_tasks",
    "list_landmarks", "list_objects", "listen", "look", "navigation_status",
    "recall_landmark", "recall_object", "remember_landmark", "say", "soft_stop",
    "stop_everything", "verify",
}  # fmt: skip


@pytest.mark.asyncio
async def test_every_t0_tool_is_known_to_move_nothing(mcp):
    t0 = {n for n, tier in (await _tools(mcp)).items() if tier == "T0"}
    assert t0 <= T0_MOVES_NOTHING, f"new T0 tools, check they cannot move: {t0 - T0_MOVES_NOTHING}"
    assert (await _tools(mcp))["look_around"] == "T1"  # it turns the robot (C2)


def test_no_velocity_leaves_the_bridge_while_parar_stands(sent, link):
    from bridge import estop

    estop.signal_stop()
    _locomotion.send_velocity(0.2, 0.0, 0.3, height=0.78)  # e.g. look_around -> turn, internally
    assert sent[-1][:3] == (0.0, 0.0, 0.0)
    estop.record_ack()
    _locomotion.send_velocity(0.2, 0.0, 0.0, height=0.78)
    assert sent[-1][0] > 0.0


@pytest.mark.asyncio
async def test_the_arms_are_not_taken_while_parar_stands(monkeypatch):
    from bridge import estop
    from bridge.teleop import arm_sdk

    estop.signal_stop()
    with pytest.raises(arm_sdk.ArmSdkUnavailable, match="PARAR"):
        await arm_sdk.ArmSdkDriver().engage()


@pytest.mark.asyncio
async def test_a_stop_voids_pending_confirmations(sent, monkeypatch):
    monkeypatch.setattr(stop_everything, "SIM_MODE", "real", raising=False)
    _grant("move_arm", {"elbow_deg": 10})
    await stop_everything.run()
    assert safety.status()["pending_confirmations"] == []


@pytest.mark.asyncio
async def test_resume_refuses_a_stop_newer_than_the_one_the_operator_saw(monkeypatch):
    import time

    from bridge import estop, mcp_server

    monkeypatch.setattr(safety, "CONFIRM_SECRET", "s3cret")
    estop.signal_stop()
    seen = estop.last_stop_at()  # the stop the operator saw
    time.sleep(0.01)
    estop.signal_stop()  # then someone else's
    later = estop.last_stop_at()
    stale = await mcp_server.safety_ack(FakeRequest({"stopped_at": seen}, secret="s3cret"))
    assert stale.status_code == 409 and safety.status()["stop_standing"] is True
    ok = await mcp_server.safety_ack(FakeRequest({"stopped_at": later}, secret="s3cret"))
    assert ok.status_code == 200 and safety.status()["stop_standing"] is False


# -- 11.2: a stop that cannot be written still latches this process -----------


def _unwritable_run_dir(monkeypatch, tmp_path):
    from bridge import estop

    (tmp_path / "nope").write_text("not a directory")
    monkeypatch.setattr(estop, "DEFAULT_RUN_DIR", tmp_path / "nope" / "nested")


def test_a_stop_that_cannot_be_written_still_latches(sent, link, monkeypatch, tmp_path):
    from bridge import estop

    writable = estop.DEFAULT_RUN_DIR
    _unwritable_run_dir(monkeypatch, tmp_path)
    assert estop.signal_stop() is False
    assert safety.status()["stop_standing"] is True
    assert "PARAR" in safety.tool_refusal("walk_velocity", {"c3po": {"tier": "T1"}})
    _locomotion.send_velocity(0.2, 0.0, 0.3, height=0.78)
    assert sent[-1][:3] == (0.0, 0.0, 0.0)
    assert estop.record_ack() is False  # an ack that cannot land leaves it standing
    assert safety.status()["stop_standing"] is True

    monkeypatch.setattr(estop, "DEFAULT_RUN_DIR", writable)
    assert estop.record_ack() is True
    assert safety.status()["stop_standing"] is False
    assert safety.tool_refusal("walk_velocity", {"c3po": {"tier": "T1"}}) is None


@pytest.mark.asyncio
async def test_resume_says_so_when_the_ack_could_not_be_recorded(monkeypatch, tmp_path):
    from bridge import estop, mcp_server

    monkeypatch.setattr(safety, "CONFIRM_SECRET", "s3cret")
    writable = estop.DEFAULT_RUN_DIR
    _unwritable_run_dir(monkeypatch, tmp_path)
    estop.signal_stop()
    failed = await mcp_server.safety_ack(FakeRequest({}, secret="s3cret"))
    assert failed.status_code == 500 and b"ack_not_recorded" in failed.body
    assert safety.status()["stop_standing"] is True

    monkeypatch.setattr(estop, "DEFAULT_RUN_DIR", writable)
    ok = await mcp_server.safety_ack(FakeRequest({}, secret="s3cret"))
    assert ok.status_code == 200 and safety.status()["stop_standing"] is False


# -- low battery (1.10) --------------------------------------------------------


def _battery(monkeypatch, soc, *, age_s=0.0):
    """A sampler whose last BmsState_ said `soc`, `age_s` ago (as test_state_battery builds it)."""
    import time

    from bridge.sdk import state

    sampler = state.StateSampler()
    if soc is not None:
        sampler._bms = state._BmsSnapshot(received_at=time.time() - age_s, soc_pct=soc)
    monkeypatch.setattr(state, "_sampler_singleton", sampler)


def _refusal(tool, mcp_tools):
    return safety.tool_refusal(tool, mcp_tools[tool].meta, None)


@pytest.fixture
def listed(mcp):
    from bridge import mcp_server

    return {t.name: t for t in mcp_server.mcp._tool_manager.list_tools()}


def test_low_battery_refuses_exactly_the_long_and_arm_tools(listed, monkeypatch):
    """Pinned from the live catalogue: a new long tool shows up in review."""
    _battery(monkeypatch, 15)
    refused = {n for n in listed if "battery is 15%" in (_refusal(n, listed) or "")}
    assert refused == {
        "move_arm", "set_hand", "reach", "pick", "place", "handover",
        "walk_to", "go_to_object", "navigate_to", "navigate_to_landmark",
        "look_around", "dance", "arm_navigation",
    }  # fmt: skip
    for name in ("walk_velocity", "turn", "start_walking_waist", "open_hands",
                 "release_arm_control", "damp", "stop_everything"):  # fmt: skip
        assert "battery" not in (_refusal(name, listed) or ""), name


def test_a_long_t0_tool_is_never_battery_refused(monkeypatch):
    _battery(monkeypatch, 5)
    long_t0 = {"c3po": {"tier": "T0", "classification": "speech", "expected_duration_s": 60}}
    assert safety.tool_refusal("listen", long_t0) is None


@pytest.mark.parametrize(("soc", "age_s"), [(25, 0.0), (20, 0.0), (None, 0.0), (15, 120.0)])
def test_a_charged_unknown_or_stale_battery_is_allowed(listed, monkeypatch, soc, age_s):
    _battery(monkeypatch, soc, age_s=age_s)
    assert "battery" not in (_refusal("walk_to", listed) or "")


def test_no_sampler_is_an_unknown_battery(listed, monkeypatch):
    from bridge.sdk import state

    monkeypatch.setattr(state, "_sampler_singleton", None)
    assert safety.battery_pct() is None
    assert _refusal("walk_to", listed) is None


@pytest.mark.asyncio
async def test_a_battery_refusal_drops_the_grant_it_was_given(mcp, link, monkeypatch):
    # Otherwise the operator's click stays armed for 30 s after the call it was for failed.
    _battery(monkeypatch, 15)
    _grant("set_hand", {"closure": 0.5})
    with pytest.raises(ToolError, match="battery is 15%"):
        await mcp.call_tool("set_hand", {"closure": 0.5})
    assert safety.status()["pending_confirmations"] == []
    _battery(monkeypatch, 50)
    with pytest.raises(ToolError, match="needs an operator's confirmation"):
        await mcp.call_tool("set_hand", {"closure": 0.5})


# -- Nav2's obstacle source ----------------------------------------------------


@pytest.mark.parametrize(
    ("report", "age"),
    [
        (None, None),  # no world summary at all
        (_report(lidar_online=False), 0.1),  # the Mid-360S scan fault, 2026-11-03
        (_report(), safety.PERCEPTION_STALE_S + 0.5),  # the summary stopped coming
    ],
)
def test_nav2_does_not_drive_blind(sent, link, report, age):
    """A stale costmap is a picture of obstacles that have moved: Nav2 gets zero."""
    link.report, link.age = report, age
    perception_link._default_forward(0.3, 0.0, 0.3)
    assert sent[-1][:3] == (0.0, 0.0, 0.0)
    assert safety.status()["last_reason"] == "obstacle_source_stale"
    # People driving still move: the console pad and walk_to are not Nav2.
    safety.reset()
    _locomotion.send_velocity(0.3, 0.0, 0.0, height=0.78)
    assert sent[-1][0] == 0.3


def test_go_to_object_walks_with_the_lidar_faulted(sent, link):
    """d6acd8b's blind rule is Nav2's: go_to_object steers on the camera, not the costmap."""
    link.report = _report(lidar_online=False)  # the Mid-360S scan fault, detector fresh
    _locomotion.send_velocity(0.3, 0.0, 0.0, height=0.78, autonomous=True)
    assert sent[-1][0] == 0.3
    # ...and the person rule still applies to it.
    link.report = _report(person(1.0), lidar_online=False)
    _locomotion.send_velocity(0.3, 0.0, 0.0, height=0.78, autonomous=True)
    assert sent[-1][0] == pytest.approx(0.3 * safety.PERSON_SLOW_FACTOR)


def test_nav2_drives_when_the_lidar_reports(sent, link):
    perception_link._default_forward(0.3, 0.0, 0.0)
    assert sent[-1][0] == 0.3


def test_the_blind_rule_holds_with_the_person_rule_off(sent, link, monkeypatch):
    monkeypatch.setattr(safety, "PERSON_RULE", False)
    link.report = _report(lidar_online=False)
    perception_link._default_forward(0.3, 0.0, 0.0)
    assert sent[-1][0] == 0.0


# -- the geofence --------------------------------------------------------------


class FakeSampler:
    def __init__(self, x=0.0, y=0.0, age=0.05):
        self.pose = (x, y, age)

    def latest_pose(self):
        return self.pose


@pytest.fixture
def odom(monkeypatch):
    from bridge.sdk import state

    fake = FakeSampler()
    monkeypatch.setattr(state, "_sampler_singleton", fake)
    return fake


def _from_rest():
    # Not safety.reset(): that would also forget the fence's centre.
    safety._last_at = None


def _armed_at(link, odom, x=0.0, y=0.0):
    odom.pose = (x, y, 0.05)
    safety.anchor_geofence(renewing=False)
    link.armed = True


def _nav2(odom, x, y=0.0, age=0.05):
    """One Nav2 tick with the robot at (x, y); what reached the legs."""
    odom.pose = (x, y, age)
    _from_rest()
    perception_link._default_forward(0.3, 0.0, 0.0)


def test_nav2_drives_inside_the_fence(sent, link, odom):
    _armed_at(link, odom)
    _nav2(odom, safety.GEOFENCE_M - 0.1)
    assert sent[-1][0] == 0.3
    assert safety.geofence()["inside"] is True


def test_nav2_is_zeroed_outside_the_fence_but_people_still_drive(sent, link, odom):
    _armed_at(link, odom, 1.0, 1.0)
    _nav2(odom, 1.0, 1.0 + safety.GEOFENCE_M + 0.1)
    assert sent[-1][:3] == (0.0, 0.0, 0.0)
    assert safety.status()["last_reason"] == "outside_geofence"
    assert safety.geofence()["inside"] is False
    # Teleop is never fenced: it is how the operator brings the robot back.
    _from_rest()
    _locomotion.send_velocity(0.3, 0.0, 0.0, height=0.78)
    assert sent[-1][0] == 0.3


def test_nav2_drives_again_once_back_inside(sent, link, odom):
    _armed_at(link, odom)
    _nav2(odom, safety.GEOFENCE_M + 0.5)
    assert sent[-1][0] == 0.0
    _nav2(odom, safety.GEOFENCE_M - 0.5)
    assert sent[-1][0] == 0.3


@pytest.mark.parametrize("pose", [None, (0.0, 0.0, safety.POSE_STALE_S + 0.5)])
def test_nav2_is_zeroed_when_the_pose_is_unknown_or_stale(sent, link, odom, pose):
    _armed_at(link, odom)
    odom.pose = pose
    _from_rest()
    perception_link._default_forward(0.3, 0.0, 0.3)
    assert sent[-1][:3] == (0.0, 0.0, 0.0)
    assert safety.status()["last_reason"] == "geofence_pose_unknown"


def test_nav2_is_zeroed_with_no_sampler_at_all(sent, link, monkeypatch):
    from bridge.sdk import state

    monkeypatch.setattr(state, "_sampler_singleton", None)
    safety.anchor_geofence(renewing=False)
    link.armed = True
    perception_link._default_forward(0.3, 0.0, 0.0)
    assert sent[-1][0] == 0.0
    assert safety.geofence()["anchor"] is None


def test_arming_with_no_pose_holds_nav2_until_a_renewal_finds_one(sent, link, odom):
    odom.pose = None
    safety.anchor_geofence(renewing=False)
    link.armed = True
    _nav2(odom, 0.0)
    assert sent[-1][0] == 0.0
    safety.anchor_geofence(renewing=True)  # no centre yet, so a renewal takes this one
    _nav2(odom, 0.0)
    assert sent[-1][0] == 0.3


def test_a_renewal_keeps_the_centre_and_a_fresh_arm_moves_it(sent, link, odom):
    _armed_at(link, odom)
    odom.pose = (safety.GEOFENCE_M + 1.0, 0.0, 0.05)
    safety.anchor_geofence(renewing=True)
    _nav2(odom, safety.GEOFENCE_M + 1.0)
    assert sent[-1][0] == 0.0  # re-arming leg by leg cannot walk the fence away
    safety.anchor_geofence(renewing=False)
    _nav2(odom, safety.GEOFENCE_M + 1.0)
    assert sent[-1][0] == 0.3


def test_the_fence_is_off_at_zero_radius(sent, link, odom, monkeypatch):
    monkeypatch.setattr(safety, "GEOFENCE_M", 0.0)
    _armed_at(link, odom)
    _nav2(odom, 50.0)
    assert sent[-1][0] == 0.3
    odom.pose = None
    _from_rest()
    perception_link._default_forward(0.3, 0.0, 0.0)
    assert sent[-1][0] == 0.3
    assert safety.geofence()["radius_m"] is None


def test_an_unarmed_autonomous_walk_is_not_fenced(sent, link, odom):
    """go_to_object's walk_to is autonomous but not Nav2: no arm, no centre."""
    odom.pose = None
    _locomotion.send_velocity(0.3, 0.0, 0.0, height=0.78, autonomous=True)
    assert sent[-1][0] == 0.3


@pytest.mark.parametrize("raw", ["nan", "inf", "wide"])
def test_a_broken_radius_falls_back_to_the_default_never_off(monkeypatch, raw):
    monkeypatch.setenv("SAFETY_GEOFENCE_M", raw)
    try:
        importlib.reload(safety)
        assert safety.GEOFENCE_M == safety.GEOFENCE_DEFAULT_M
    finally:
        monkeypatch.delenv("SAFETY_GEOFENCE_M")
        importlib.reload(safety)


def test_arm_navigation_and_status_report_the_fence(link, odom):
    from bridge import mcp_server

    odom.pose = (2.0, -1.0, 0.05)
    out = mcp_server.arm_navigation(reason="test arm", seconds=5)
    assert out["geofence"] == {
        "radius_m": safety.GEOFENCE_M, "anchor": [2.0, -1.0], "distance_m": 0.0, "inside": True,
    }  # fmt: skip
    assert safety.status()["geofence"]["anchor"] == [2.0, -1.0]
    assert safety.status()["obstacles_fresh"] is True
