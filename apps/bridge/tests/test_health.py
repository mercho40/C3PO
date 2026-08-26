"""The stack health report, including the bug that made it dangerous.

The shell version read the motion gate by substring-matching JSON, so a single
space after a colon made it report a closed gate for an armed one — in green, to
somebody deciding whether it was safe to stand near the robot.
"""

from __future__ import annotations

import json

from bridge.health import assess, render, repairs_for

ARMED = json.dumps({"enabled": True, "link": {"started": True, "domain_id": 42}})
CLOSED = json.dumps(
    {"enabled": False, "disabled_reason": "never armed", "link": {"started": True}}
)

BASE = dict(
    bridge_pid=1234,
    # No ring by default. With no stage enabled and no containers that is the
    # NORMAL state — the sensors belong to the other team most of the time —
    # so it must not colour the baseline every one of these tests builds on.
    scan_json=None,
    enabled_stage=None,
    perception_containers=[],
    gemm_containers=[],
)

RING = json.dumps(
    {
        "v": 1,
        "frame": "base_footprint",
        "a0_deg": -180.0,
        "step_deg": 3.0,
        "max_cm": 1200,
        "r_cm": [None] * 119 + [200],
        "age_s": 0.2,
        "stale": False,
    }
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


def test_an_unstarted_domain_link_is_a_problem():
    """The failure that presented as three unrelated symptoms and no error."""
    report = assess(gate_json='{"enabled": false, "link": {"started": false}}', **BASE)
    assert "domain 42 link" in problem_names(report)


# --- perception -------------------------------------------------------------


def test_an_enabled_unit_with_no_containers_is_a_problem():
    args = dict(BASE, enabled_stage="nav2", perception_containers=[])
    report = assess(gate_json=ARMED, **args)
    assert "perception (nav2)" in problem_names(report)


def test_containers_without_an_enabled_unit_are_normal():
    """`perception_up` by hand is the ordinary way to open a sensor window."""
    args = dict(BASE, enabled_stage=None, perception_containers=["c3po-perception-nav"])
    report = assess(gate_json=ARMED, **args)
    assert problem_names(report) == []
    assert "by hand" in detail_for(report, "perception")


def test_nothing_running_and_nothing_enabled_is_not_a_fault():
    report = assess(gate_json=ARMED, **BASE)
    assert problem_names(report) == []


# --- repair -----------------------------------------------------------------


def test_repair_targets_only_what_is_actually_broken():
    args = dict(BASE, enabled_stage="nav2", perception_containers=[])
    args["bridge_pid"] = None
    report = assess(gate_json=None, **args)
    assert repairs_for(report) == ["c3po-bridge", "c3po-perception@nav2"]


def test_a_healthy_stack_has_nothing_to_repair():
    assert repairs_for(assess(gate_json=ARMED, **BASE)) == []


def test_an_armed_gate_is_never_something_to_repair():
    """It is a fact to report, not a fault to fix — and 'repairing' it would
    mean restarting the bridge out from under a robot that is being driven."""
    report = assess(gate_json=ARMED, **BASE)
    assert repairs_for(report) == []


# --- rendering --------------------------------------------------------------


def test_the_report_says_healthy_when_it_is():
    assert "healthy" in render(assess(gate_json=ARMED, **BASE))


def test_the_report_counts_problems():
    args = dict(BASE)
    args["bridge_pid"] = None
    out = render(assess(gate_json=None, **args))
    assert "2 problem(s)" in out


# --- the lidar ring ---------------------------------------------------------
#
# The ring is drawn to an operator whose eyes are covered. "Is it arriving" is
# a question they cannot answer from inside the headset, so it has to be
# answerable from the robot's own side, in the command somebody already types.


def test_no_ring_and_no_perception_is_not_a_fault():
    """The daily state. A red line here every day trains people to ignore this.

    The Livox and the RealSense belong to the other team most of the time, and
    a health check that calls the normal case broken is worse than one that
    does not mention it.
    """
    report = assess(gate_json=ARMED, **BASE)
    assert "lidar ring" not in problem_names(report)
    assert "no perception stage" in detail_for(report, "lidar ring")


def test_no_ring_WHILE_perception_runs_is_a_fault():
    """Something is publishing and the ring is not reaching this process.

    This is the shape of the bug that has bitten this project five times: a
    component that is running, correct, and connected to nothing.
    """
    base = dict(BASE)
    base["enabled_stage"] = "fake"
    base["perception_containers"] = ["c3po-perception-nav"]
    report = assess(gate_json=ARMED, **base)
    assert "lidar ring" in problem_names(report)


def test_nav_up_by_hand_with_no_ring_is_a_NOTE_not_a_fault():
    """`perception_up` enables no unit, so we cannot tell which stage it is.

    `fake` owes a ring and `odometry` does not, and both run the same
    container. Calling this a fault would put a red line on a correctly
    running `odometry` — and this module already argues, about the
    enabled-unit check, that a health report which cries wolf gets ignored.
    Guessing in the other direction and staying silent would hide the real
    fault. So: say it, name the stages that owe a ring, flag nothing.
    """
    base = dict(BASE)
    base["perception_containers"] = ["c3po-perception-nav"]
    report = assess(gate_json=ARMED, **base)
    assert "lidar ring" not in problem_names(report)
    detail = detail_for(report, "lidar ring")
    assert "nav is up" in detail
    assert "fake" in detail


def test_the_ring_stages_are_the_ones_that_really_launch_the_publisher():
    """RING_STAGES must stay in step with apps/perception/bringup/spec.py.

    The list is the difference between "no ring is a fault" and "no ring is
    expected", and it lives in a different app from the launch files it
    describes. A stage renamed there would silently stop being checked here.
    """
    import re
    from pathlib import Path as _Path

    from bridge.health import RING_STAGES

    spec = _Path(__file__).resolve().parents[3] / "apps" / "perception" / "bringup" / "spec.py"
    declared = set(re.findall(r'^    "([a-z0-9-]+)": Stage\(', spec.read_text(), re.M))
    assert declared, "the stage regex found nothing — it has drifted"
    missing = sorted(set(RING_STAGES) - declared)
    assert not missing, "RING_STAGES names stages that no longer exist: {}".format(missing)


def test_a_live_ring_reports_how_many_bearings_saw_something():
    base = dict(BASE)
    base["scan_json"] = RING
    base["perception_containers"] = ["c3po-perception-nav"]
    report = assess(gate_json=ARMED, **base)
    detail = detail_for(report, "lidar ring")
    assert "lidar ring" not in problem_names(report)
    assert "1/120" in detail
    assert "base_footprint" in detail


def test_a_stale_ring_is_a_problem_even_though_it_arrived():
    """The dangerous one. A ring that stopped updating looks like a still room.

    `present and stale` is not a lesser version of `present`; it is the state
    where the operator is being shown a memory and told nothing.
    """
    base = dict(BASE)
    base["scan_json"] = json.dumps(
        {"v": 1, "r_cm": [200], "stale": True, "age_s": 4.1, "frame": "base_footprint"}
    )
    base["perception_containers"] = ["c3po-perception-nav"]
    report = assess(gate_json=ARMED, **base)
    assert "lidar ring" in problem_names(report)
    assert "STALE" in detail_for(report, "lidar ring")


def test_a_stale_string_is_not_treated_as_true_or_false_by_truthiness():
    """Same rule as the gate: `is True`, never truthiness.

    A JSON `"stale": "false"` is a truthy string. Reading it loosely would
    report a fresh ring as stale, or worse, the reverse.
    """
    base = dict(BASE)
    base["scan_json"] = json.dumps(
        {"v": 1, "r_cm": [200, None], "stale": "false", "frame": "base_footprint"}
    )
    base["perception_containers"] = ["c3po-perception-nav"]
    report = assess(gate_json=ARMED, **base)
    assert "lidar ring" not in problem_names(report)
    assert "1/2" in detail_for(report, "lidar ring")


def test_a_503_hint_body_reads_as_no_ring_not_as_a_crash():
    """The bridge answers 503 with a JSON hint when nothing is publishing.

    `_parse` returns None for anything without the shape, so the hint body
    degrades to "no ring" rather than being mistaken for one.
    """
    base = dict(BASE)
    base["scan_json"] = json.dumps({"error": "no scan received", "hint": "..."})
    report = assess(gate_json=ARMED, **base)
    # No stage running, so this is the benign branch — but crucially it did not
    # come back as a ring with 0 bearings, which would read as a clear room.
    assert "lidar ring" not in problem_names(report)
    assert "0/0" not in detail_for(report, "lidar ring")


def test_a_broken_ring_is_never_repaired_by_restarting_the_bridge():
    """The ring comes from the nav container; the bridge only relays it.

    Restarting the bridge to fix a missing scan would take teleop down and
    leave the actual cause untouched.
    """
    base = dict(BASE)
    base["enabled_stage"] = "fake"
    base["perception_containers"] = ["c3po-perception-nav"]
    report = assess(gate_json=ARMED, **base)
    assert "c3po-bridge" not in repairs_for(report)


# --- liveness detection -----------------------------------------------------
#
# Caught on the robot 2026-08-27: c3po_health printed "bridge DOWN" while the
# bridge was answering /telemetry/scan and /telemetry/gate. The unit had been
# switched to Type=exec, which writes no pidfile, so the only pidfile on disk
# was a three-day-old leftover naming a dead process.


def test_a_bridge_that_answers_is_running_even_with_no_pidfile():
    """Proof of life beats lifecycle bookkeeping.

    Type=exec starts the interpreter directly and writes nothing. Reporting
    DOWN for a process that just returned a gate reading is the same class of
    error as this module's original sin.
    """
    report = assess(gate_json=ARMED, **dict(BASE, bridge_pid=None))
    assert "bridge" not in problem_names(report)
    assert "answering" in detail_for(report, "bridge")


def test_a_live_pidfile_is_still_preferred_when_present():
    report = assess(gate_json=ARMED, **dict(BASE, bridge_pid=4242))
    assert "pid 4242" in detail_for(report, "bridge")


def test_no_pidfile_and_no_answer_is_still_DOWN():
    """The one case that must stay a problem.

    Nothing on the port and nothing in the pidfile is a dead bridge, and a
    dead bridge means no stop_everything — which is the reason this check
    exists at all.
    """
    report = assess(gate_json=None, **dict(BASE, bridge_pid=None))
    assert "bridge" in problem_names(report)
    assert "DOWN" in detail_for(report, "bridge")


def test_a_stale_pidfile_does_not_resurrect_a_dead_bridge():
    """`_read_pid` returns None for a pid that is not alive, so this is the
    same input as "no pidfile" — asserted so the two paths cannot drift."""
    report = assess(gate_json=None, **dict(BASE, bridge_pid=None))
    assert "DOWN" in detail_for(report, "bridge")
