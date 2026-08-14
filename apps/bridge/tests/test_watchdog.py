"""Tests for the operator-link watchdog (`bridge.watchdog`).

The failure modes worth guarding are asymmetric. Not tripping when the link
dies mid-walk leaves a robot moving with nobody watching. But tripping when it
shouldn't — on an idle robot, or repeatedly during one outage — makes the
watchdog a nuisance operators disable, which is how safety systems actually
fail. Both directions are tested here.

Everything is injected (clock, active-task list, stop action) so these run with
no DDS, no robot, and no real time.
"""

from __future__ import annotations

from bridge.watchdog import LinkWatchdog


class FakeClock:
    """Manually advanced monotonic clock."""

    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


def make(active: int = 1, timeout_s: float = 1.5):
    """Watchdog with `active` running tasks and a recording stop action."""
    clock = FakeClock()
    stops: list[int] = []
    wd = LinkWatchdog(
        timeout_s=timeout_s,
        on_timeout=lambda: stops.append(1),
        list_active=lambda: [object()] * active,
        clock=clock,
    )
    return wd, clock, stops


# --------------------------------------------------------------------------
# Must trip
# --------------------------------------------------------------------------


def test_trips_when_link_silent_during_motion():
    wd, clock, stops = make(active=1)

    clock.advance(2.0)  # past the 1.5s timeout

    assert wd.check() is True
    assert stops == [1]


def test_does_not_trip_before_the_timeout():
    wd, clock, stops = make(active=1)

    clock.advance(1.4)

    assert wd.check() is False
    assert stops == []


# --------------------------------------------------------------------------
# Must NOT trip
# --------------------------------------------------------------------------


def test_never_trips_when_nothing_is_moving():
    # An idle robot with a flaky link needs no intervention. A watchdog that
    # fires here is one operators learn to disable.
    wd, clock, stops = make(active=0)

    clock.advance(60.0)

    assert wd.check() is False
    assert stops == []


def test_fires_once_per_outage_not_once_per_poll():
    wd, clock, stops = make(active=1)
    clock.advance(2.0)

    assert wd.check() is True
    for _ in range(10):
        clock.advance(1.0)
        assert wd.check() is False

    assert stops == [1], "one outage must produce exactly one stop"


def test_rearms_after_contact_resumes():
    wd, clock, stops = make(active=1)
    clock.advance(2.0)
    assert wd.check() is True

    # Operator comes back...
    wd.touch()
    clock.advance(1.0)
    assert wd.check() is False

    # ...then drops again. That's a new outage and must trip again.
    clock.advance(2.0)
    assert wd.check() is True
    assert stops == [1, 1]


def test_touch_resets_the_silence_window():
    wd, clock, stops = make(active=1)

    for _ in range(5):
        clock.advance(1.0)  # each under the 1.5s timeout
        wd.touch()

    assert wd.check() is False
    assert stops == []


# --------------------------------------------------------------------------
# Robustness
# --------------------------------------------------------------------------


def test_a_failing_stop_action_does_not_propagate():
    # The watchdog runs on its own thread; an exception escaping the check
    # would kill it silently and leave the robot unguarded for the rest of the
    # session.
    clock = FakeClock()

    def boom() -> None:
        raise RuntimeError("bridge is wedged")

    wd = LinkWatchdog(
        timeout_s=1.0,
        on_timeout=boom,
        list_active=lambda: [object()],
        clock=clock,
    )
    clock.advance(2.0)

    assert wd.check() is True  # tripped, swallowed the failure


def test_silent_for_reports_elapsed_time():
    wd, clock, _ = make(active=1)
    clock.advance(3.25)
    assert wd.silent_for() == 3.25


def test_default_timeout_is_sane():
    from bridge import watchdog

    # Long enough not to trip on a slow tool call, short enough that a walking
    # robot doesn't travel far before it fires.
    assert 0.5 <= watchdog.LINK_TIMEOUT_S <= 5.0


# --------------------------------------------------------------------------
# Arming
# --------------------------------------------------------------------------


def test_disabled_by_default():
    # Arming it while tool calls still block the transport would cancel every
    # long skill mid-run: "operator silent while a task runs" is the normal
    # state during a 60s walk_to, not a dead link.
    import importlib
    import os

    from bridge import watchdog as wd_mod

    saved = os.environ.pop("LINK_WATCHDOG", None)
    try:
        importlib.reload(wd_mod)
        assert wd_mod.ENABLED is False
    finally:
        if saved is not None:
            os.environ["LINK_WATCHDOG"] = saved
        importlib.reload(wd_mod)


def test_arms_when_explicitly_enabled(monkeypatch):
    import importlib

    from bridge import watchdog as wd_mod

    monkeypatch.setenv("LINK_WATCHDOG", "on")
    importlib.reload(wd_mod)
    try:
        assert wd_mod.ENABLED is True
    finally:
        monkeypatch.delenv("LINK_WATCHDOG", raising=False)
        importlib.reload(wd_mod)
