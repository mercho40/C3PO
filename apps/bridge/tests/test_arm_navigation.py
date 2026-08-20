"""The gate: the single point where a planning stack becomes a moving humanoid.

Nav2 plans continuously and publishes at 20 Hz regardless of whether anything
listens. One boolean in this process decides whether any of it reaches the legs.
These tests are about that boolean, and about the one invariant that makes it
trustworthy: STOPPING MUST CLOSE IT.

Without that, `stop_everything` means "pause" — the zero-velocity burst halts
the gait, then Nav2's next tick arrives 50 ms later through a still-open gate
and the robot walks on. That is not a hypothetical: `disable()`'s own docstring
warns about it, and stop_everything did not call it until this file existed.
"""

from __future__ import annotations

import pathlib

from bridge.sdk import perception_link as pl


def fresh():
    sent: list[tuple[float, float, float]] = []
    now = {"t": 1000.0}
    link = pl.PerceptionLink(forward=lambda *v: sent.append(v),
                             clock=lambda: now["t"])
    return link, sent, now


# --- the gate itself --------------------------------------------------------

def test_closed_until_armed():
    link, *_ = fresh()
    assert link.is_enabled() is False
    assert link.status()["disabled_reason"] == "never armed"


def test_arming_opens_it_and_records_why():
    link, _, _ = fresh()
    link.enable(reason="walk to the door, supervised", ttl_s=30)
    assert link.is_enabled() is True
    assert link.status()["arm_expires_in_s"] == 30


def test_the_gate_closes_itself_when_the_ttl_expires():
    """The timeout IS the safety property. A gate left open because somebody
    forgot to close it is the same as no gate."""
    link, _, now = fresh()
    link.enable(reason="test", ttl_s=10)
    now["t"] += 9.9
    assert link.is_enabled() is True
    now["t"] += 0.2
    assert link.is_enabled() is False


def test_disarming_is_idempotent():
    """Always safe to call, including twice — an operator hitting it repeatedly
    under stress must not get an error instead of a closed gate."""
    link, _, _ = fresh()
    link.enable(reason="test")
    link.disable("done")
    link.disable("done again")
    assert link.is_enabled() is False


# --- the invariant ----------------------------------------------------------

def test_stop_everything_closes_the_gate_synchronously_and_first():
    """THE ONE THAT MATTERS.

    Read as source rather than executed: running stop_everything needs the task
    registry, the estop channel and DDS. What has to be true is structural — the
    gate close must be present, and must come BEFORE the awaits, or a planner
    tick can slip through the gap between the burst and the close.
    """
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "src" / "bridge" / "skills" / "stop_everything.py")
    body = src.read_text()
    run = body.split("async def run(", 1)[-1]

    assert "disable(" in run, (
        "stop_everything must close the nav gate — without it, stopping only "
        "pauses: Nav2's next tick walks the robot on 50 ms later")

    close_at = run.index("disable(")
    first_await = run.index("await ")
    assert close_at < first_await, (
        "the gate must close BEFORE the first await, or a planner tick can pass "
        "through while the stop is still being dispatched")


def test_a_broken_perception_link_cannot_prevent_a_stop():
    """Refusing to stop the robot because the gate could not be read would be
    the worst possible trade, so the close is wrapped."""
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "src" / "bridge" / "skills" / "stop_everything.py")
    run = src.read_text().split("async def run(", 1)[-1]
    gate = run[: run.index("registry = get_registry()")]
    assert "try:" in gate and "except" in gate


# --- what the gate actually does to traffic ---------------------------------

def test_nothing_reaches_the_legs_while_closed():
    link, sent, _ = fresh()
    for _ in range(20):
        link._apply_cmd_vel(0.3, 0.0, 0.0)
    link._tick()
    assert sent == [], "a closed gate must forward nothing"
    assert link.status()["dropped_while_disabled"] == 20


def test_disarming_mid_motion_stops_forwarding():
    """Prevents further motion. It does not arrest motion already underway —
    that is stop_everything's job, and the docstrings must not blur them."""
    link, sent, _ = fresh()
    link.enable(reason="test")
    link._apply_cmd_vel(0.3, 0.0, 0.0)
    link._tick()
    before = len(sent)
    link.disable("operator")
    link._apply_cmd_vel(0.3, 0.0, 0.0)
    link._tick()
    assert len(sent) == before or all(v == (0.0, 0.0, 0.0) for v in sent[before:]), (
        "after disarming, only braking zeros may be forwarded")


def test_the_arm_tool_requires_a_reason():
    """The log after an incident is only useful if it says who armed it and why,
    so `reason` is required rather than defaulted."""
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "src" / "bridge" / "mcp_server.py")
    tool = src.read_text().split("def arm_navigation(", 1)[-1].split(") -> dict:", 1)[0]
    assert "reason:" in tool
    assert "] = " not in tool.split("reason:", 1)[1].split("seconds:", 1)[0], (
        "reason must not have a default")
