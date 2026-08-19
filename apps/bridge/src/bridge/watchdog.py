"""Operator-link watchdog — make the robot safe when the operator goes silent.

docs/ARCHITECTURE.md §7 (safety model). Once the bridge runs onboard the Jetson (`SIM_MODE=real`), the
operator link is Wi-Fi. If it drops mid-task nothing upstream can stop the
robot, so something local has to.

**Scope is deliberately narrow, and smaller than the original design assumed.**
Every `SET_VELOCITY` we send carries `duration=1.0s`, so the *firmware* already
stops a walking robot within a second of the bridge going quiet — that deadman
sits below our software and needs no help. What it does not cover is everything
else a silent operator leaves running: a posture transition mid-flight, a task
looping on something other than velocity, a gesture the agent kicked off before
the link died. This watchdog covers those, and re-asserts the velocity stop for
good measure.

**It stops; it does not damp.** `stop_everything` damps because an operator
pressing it has decided the robot should go limp. A dropped Wi-Fi packet has
decided nothing — and damping a standing robot drops it on the floor. Link loss
is the *graceful* case: cancel the work, ramp velocity to zero, leave it
standing. Anything more aggressive turns a network blip into a fall.

**It only acts when something is actually moving.** An idle robot with a flaky
link needs no intervention, and firing on it would make the watchdog a nuisance
that operators disable — which is the real failure mode for safety systems.

Disabled by default, and that is not timidity
---------------------------------------------
Enabling this today would cancel every legitimate long task.

Both transports are request/response with a *blocking* tool call: while
`walk_to` runs for 60 s, no other call can arrive — the client is sitting on
the response. "Operator silent for 1.5 s while a task runs" is therefore the
normal state during any long skill, not evidence of a dead link. A timeout
watchdog cannot tell the two apart, so it would stop the robot mid-walk every
time.

It becomes correct once long skills are fire-and-forget: `walk_to` returns its
`task_id` immediately, the operator polls or subscribes for progress, and those
polls are a genuine liveness signal whose absence means something. That is
already the plan for the HTTP transport (see `skills/task_runtime`), and this
module is ready for it.

Until then `LINK_WATCHDOG=on` is required to arm it, and what actually protects
a real robot is:

  1. the firmware's 1 s `SET_VELOCITY` deadman — it stops a walking robot
     within a second of the bridge going quiet, below our software entirely;
  2. losing the SSH connection kills this process outright, which triggers (1);
  3. the physical e-stop, which outranks all of it.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Callable

import structlog

log = structlog.get_logger(__name__)

# How long the operator link may be silent before we make the robot safe.
# Comfortably longer than a slow tool call, short enough that a walking robot
# doesn't get far. Only counts when a task is actually running.
LINK_TIMEOUT_S = float(os.environ.get("LINK_TIMEOUT_MS", "1500")) / 1000.0

# How often we check. The check is cheap (a clock read and a list scan), so this
# is about reaction time, not cost.
POLL_INTERVAL_S = 0.2

# Off unless explicitly armed — see the module docstring. With today's blocking
# tool calls, an armed watchdog cancels long skills mid-run.
ENABLED = os.environ.get("LINK_WATCHDOG", "off").lower() in ("on", "1", "true")


class LinkWatchdog:
    """Trips a safe-stop when the operator link goes quiet during motion.

    Call `touch()` on every sign of life from the operator. The watchdog only
    trips when *both* conditions hold: nothing has touched it for
    `timeout_s`, and at least one task is running.
    """

    def __init__(
        self,
        *,
        timeout_s: float = LINK_TIMEOUT_S,
        on_timeout: Callable[[], None] | None = None,
        list_active: Callable[[], list] | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._timeout_s = timeout_s
        self._on_timeout = on_timeout or _default_safe_stop
        self._list_active = list_active or _default_list_active
        # Monotonic by default: a wall-clock step (NTP correcting the Jetson's
        # clock, which has no RTC battery) must not look like a link outage.
        self._clock = clock
        self._lock = threading.Lock()
        self._last_contact = clock()
        # Latches after tripping so one outage produces one stop, not a stop
        # every poll interval. Cleared by the next `touch()`.
        self._tripped = False
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    # -- operator contact ----------------------------------------------------

    def touch(self) -> None:
        """Record a sign of life from the operator, and re-arm after a trip."""
        with self._lock:
            self._last_contact = self._clock()
            self._tripped = False

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if self._thread is not None:
            return
        self.touch()
        self._thread = threading.Thread(
            target=self._loop, name="link-watchdog", daemon=True
        )
        self._thread.start()
        log.info("watchdog.started", timeout_s=self._timeout_s)

    def stop(self) -> None:
        self._stop.set()

    # -- the check itself ----------------------------------------------------

    def silent_for(self) -> float:
        with self._lock:
            return self._clock() - self._last_contact

    def should_trip(self) -> bool:
        """True when the link is silent, something is moving, and we haven't tripped."""
        with self._lock:
            if self._tripped:
                return False
            silent = self._clock() - self._last_contact
        if silent < self._timeout_s:
            return False
        # Checked last and outside the lock: `list_active` reaches into the task
        # registry, and holding our lock across that invites a deadlock.
        return len(self._list_active()) > 0

    def check(self) -> bool:
        """Run one check. Returns True if it tripped and the stop was issued."""
        if not self.should_trip():
            return False
        with self._lock:
            # Re-check under the lock so two callers can't both fire.
            if self._tripped:
                return False
            self._tripped = True
        log.warning(
            "watchdog.tripped",
            silent_for_s=round(self.silent_for(), 2),
            timeout_s=self._timeout_s,
        )
        try:
            self._on_timeout()
        except Exception:
            # A failed safe-stop must not kill the watchdog thread — the next
            # poll should be able to try again once re-armed.
            log.exception("watchdog.safe_stop_failed")
        return True

    def _loop(self) -> None:
        while not self._stop.wait(POLL_INTERVAL_S):
            try:
                self.check()
            except Exception:
                log.exception("watchdog.check_failed")


# ---------------------------------------------------------------------------
# Defaults, imported lazily so importing this module never pulls in DDS.
# ---------------------------------------------------------------------------


def _default_list_active() -> list:
    from bridge.skills.task_runtime import get_registry

    return get_registry().list_active()


def _default_safe_stop() -> None:
    """Cancel running work and ramp velocity to zero. Deliberately no damp."""
    from bridge.skills._locomotion import stop_motion_sync
    from bridge.skills.task_runtime import get_registry

    registry = get_registry()
    cancelled = [t.task_id for t in registry.list_active() if registry.cancel(t.task_id)]
    log.warning("watchdog.safe_stop", cancelled_task_ids=cancelled)
    # Skills observe their cancel event between iterations and stop themselves;
    # this burst covers the gap and anything the registry didn't know about.
    stop_motion_sync()


_watchdog_singleton: LinkWatchdog | None = None


def get_watchdog() -> LinkWatchdog:
    global _watchdog_singleton
    if _watchdog_singleton is None:
        _watchdog_singleton = LinkWatchdog()
    return _watchdog_singleton
