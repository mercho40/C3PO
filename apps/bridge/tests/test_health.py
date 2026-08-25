"""The stack health report, including the bug that made it dangerous.

The shell version read the motion gate by substring-matching JSON, so a single
space after a colon made it report a closed gate for an armed one — in green, to
somebody deciding whether it was safe to stand near the robot.
"""

from __future__ import annotations

import json

from bridge import health
from bridge.health import assess, render

ARMED = json.dumps({"enabled": True, "link": {"started": True, "domain_id": 42}})
CLOSED = json.dumps(
    {"enabled": False, "disabled_reason": "never armed", "link": {"started": True}}
)

BASE = dict(
    bridge_pid=1234,
    perception_containers=[],
    gemm_containers=[],
)


def detail_for(report, name):
    return next(c.detail for c in report.checks if c.name == name)


def problem_names(report):
    return [c.name for c in report.checks if c.problem]


# --- the gate ---------------------------------------------------------------


def test_an_armed_gate_is_reported_as_armed():
    report = assess(gate_json=ARMED, **BASE)
    assert "ARMED" in detail_for(report, "cmd_vel gate")


def test_an_armed_gate_is_still_armed_with_whitespace_in_the_json():
    """THE BUG. `*'"enabled":true'*` does not match `"enabled": true`.

    The shell version fell through to its default branch and printed
    "closed (default)" for a gate that could drive the robot. Nothing promises
    compact JSON; that the bridge emits it is an implementation detail of the
    response class.
    """
    spaced = '{ "enabled" : true , "link" : { "started" : true } }'
    report = assess(gate_json=spaced, **BASE)
    assert "ARMED" in detail_for(report, "cmd_vel gate")


def test_a_closed_gate_carries_the_reason():
    report = assess(gate_json=CLOSED, **BASE)
    detail = detail_for(report, "cmd_vel gate")
    assert "closed" in detail and "never armed" in detail


def test_a_string_false_is_not_treated_as_armed():
    """`is True`, not truthiness — "false" is a truthy string."""
    report = assess(gate_json='{"enabled": "false", "link": {"started": true}}', **BASE)
    assert "ARMED" not in detail_for(report, "cmd_vel gate")


def test_an_unarmed_gate_is_not_a_problem():
    """Closed is the correct resting state. Flagging it would train people to
    ignore this report."""
    report = assess(gate_json=CLOSED, **BASE)
    assert "cmd_vel gate" not in problem_names(report)


# --- reachability -----------------------------------------------------------


def test_no_answer_from_the_bridge_is_a_problem():
    report = assess(gate_json=None, **BASE)
    assert "bridge http" in problem_names(report)


def test_a_malformed_body_is_cannot_tell_rather_than_a_crash():
    report = assess(gate_json="<html>502 Bad Gateway</html>", **BASE)
    assert "bridge http" in problem_names(report)


def test_a_dead_bridge_is_a_problem():
    args = dict(BASE)
    args["bridge_pid"] = None
    report = assess(gate_json=None, **args)
    assert "bridge" in problem_names(report)


def test_bridge_pid_comes_from_systemd_not_a_second_pidfile(monkeypatch):
    monkeypatch.setattr(health, "_run", lambda argv: "4321\n")
    assert health._service_main_pid("c3po-bridge.service") == 4321


def test_an_inactive_systemd_unit_has_no_bridge_pid(monkeypatch):
    monkeypatch.setattr(health, "_run", lambda argv: "0\n")
    assert health._service_main_pid("c3po-bridge.service") is None


def test_an_unstarted_domain_link_is_a_problem():
    """The failure that presented as three unrelated symptoms and no error."""
    report = assess(gate_json='{"enabled": false, "link": {"started": false}}', **BASE)
    assert "domain 42 link" in problem_names(report)


# --- perception -------------------------------------------------------------


def test_running_perception_containers_are_reported_without_inventing_a_unit():
    args = dict(
        BASE,
        perception_containers=["c3po-perception-nav", "c3po-perception-stt"],
    )
    report = assess(gate_json=ARMED, **args)
    assert problem_names(report) == []
    assert "c3po-perception-nav" in detail_for(report, "perception")
    assert "c3po-perception-stt" in detail_for(report, "perception")


def test_perception_off_is_not_a_fault():
    report = assess(gate_json=ARMED, **BASE)
    assert problem_names(report) == []
    assert "off" in detail_for(report, "perception")


def test_status_rejects_the_removed_repair_flag():
    assert health.main(["--repair"]) == 2


# --- rendering --------------------------------------------------------------


def test_the_report_says_healthy_when_it_is():
    assert "healthy" in render(assess(gate_json=ARMED, **BASE))


def test_the_report_counts_problems():
    args = dict(BASE)
    args["bridge_pid"] = None
    out = render(assess(gate_json=None, **args))
    assert "2 problem(s)" in out
