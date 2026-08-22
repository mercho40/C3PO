"""A stop signal that crosses process boundaries.

`stop_everything` cancels every task in the `TaskRegistry`. That registry is an
in-memory per-process singleton — which is fine while everything that can move
the robot lives in one process, and silently wrong the moment it does not.

It does not. The MCP server (port 8001) and the teleop stream (port 8767) are
separate processes, by design: a 30 Hz control stream has no business sharing a
request/response server's event loop. So a teleop session registered its task
in *its* registry, `stop_everything` cancelled tasks in *the bridge's*, and the
two never met.

Found on the robot 2026-08-20: PARAR was pressed mid-turn and the robot kept
rotating for another 27 degrees. Every unit test passed because they ran both
halves in one process.

The fix is deliberately the dumbest thing that works across processes on one
host: a file whose mtime is the time of the last stop. `stop_everything`
touches it; anything that can move the robot stats it and compares against when
its own work began.

Why a file and not DDS, a socket, or a lock:

* it needs no listener, so a stop still lands on a process that is wedged,
  mid-reconnect, or started after the stop;
* `os.stat` is a syscall on a path that is almost certainly in cache — cheap
  enough to do at 50 Hz without thinking about it;
* it survives a restart, so a process that comes up seconds after a stop can
  still see that one happened, rather than starting clean into a robot someone
  just halted;
* it has no failure mode that *loses* a stop. A missing file reads as "no stop
  yet", and the only way to miss a real one is a clock going backwards.

It is not a lock and does not arbitrate anything. It answers exactly one
question: *has somebody hit stop since I started?*

There is a second sentinel beside it, `stop_acknowledged`, and it exists
because the first one on its own has no way to end. A stop that can only be
observed is a stop that every process must treat as permanent, so something has
to record that an operator deliberately cleared it. Keeping that record in the
same place, with the same properties, means a teleop server that restarts ten
seconds after a stop still knows whether that stop was resolved or is still
standing — and starts latched if it was not.

The asymmetry between them is deliberate: *anything* may signal a stop, and
only an operator at the controls may acknowledge one.
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import structlog

log = structlog.get_logger(__name__)

#: Shared runtime directory for the durable stop sentinel and teleop sidecar
#: state. The installer/runtime commands create it as needed.
DEFAULT_RUN_DIR = Path(os.environ.get("C3PO_RUN_DIR", str(Path.home() / ".c3po" / "run")))
SENTINEL_NAME = "stop_everything"


def sentinel_path() -> Path:
    return DEFAULT_RUN_DIR / SENTINEL_NAME


def signal_stop() -> float:
    """Record that a stop happened. Returns the timestamp written.

    Best-effort by construction: this runs *after* the in-process cancellation
    in `stop_everything`, and must never be able to turn a working e-stop into
    a failed one. A read-only filesystem or a missing directory costs the
    cross-process half of the signal, not the half that already worked.
    """
    now = time.time()
    try:
        DEFAULT_RUN_DIR.mkdir(parents=True, exist_ok=True)
        path = sentinel_path()
        path.write_text(f"{now}\n")
        # write_text sets mtime, but say so explicitly: mtime is the contract
        # every reader uses, and a filesystem with coarse timestamps would
        # otherwise make two stops a second apart indistinguishable.
        os.utime(path, (now, now))
        return now
    except OSError as exc:
        log.warning("estop.sentinel_write_failed", path=str(sentinel_path()), error=str(exc))
        return now


def last_stop_at() -> float:
    """When the last stop was signalled, or 0.0 if there has never been one."""
    try:
        return sentinel_path().stat().st_mtime
    except OSError:
        return 0.0


ACK_NAME = "stop_acknowledged"


def ack_path() -> Path:
    return DEFAULT_RUN_DIR / ACK_NAME


def record_ack() -> float:
    """Record that an operator cleared the standing stop. Returns the timestamp.

    Acknowledges every stop up to now rather than one specific timestamp: the
    operator is at the controls confirming they are ready to resume, and that
    statement covers whatever led to the halt, not one particular signal of it.

    Best-effort in the same way `signal_stop` is, but the failure leans the
    other way. A write that fails leaves the stop looking unacknowledged, so
    the next session starts latched — annoying, and the safe direction.
    """
    now = time.time()
    try:
        DEFAULT_RUN_DIR.mkdir(parents=True, exist_ok=True)
        path = ack_path()
        path.write_text(f"{now}\n")
        os.utime(path, (now, now))
        return now
    except OSError as exc:
        log.warning("estop.ack_write_failed", path=str(ack_path()), error=str(exc))
        return now


def last_ack_at() -> float:
    """When a stop was last acknowledged, or 0.0 if never."""
    try:
        return ack_path().stat().st_mtime
    except OSError:
        return 0.0


def stop_is_standing() -> bool:
    """True if a stop has been signalled and not since acknowledged.

    This is the question a process should ask at startup, and the reason the
    ack file exists: without it, a bridge restarted moments after an e-stop
    comes up with no memory of it and happily accepts the next frame.
    """
    stop_at = last_stop_at()
    return stop_at > 0.0 and stop_at > last_ack_at()


# -- the teleop lease -------------------------------------------------------
#
# Same mechanism, different question: *is somebody driving right now?*
#
# `rt/run_command/cmd` has no arbitration. Two processes writing velocity at
# 20-50 Hz produce a robot that obeys whichever message landed last, alternating
# tens of times a second, and neither writer can tell. Both exist and both are
# reachable at once: the MCP server runs walk_to / turn / walk_velocity, and the
# teleop stream runs the headset — separate processes, no shared registry, no
# shared lock.
#
# The realistic collision is not exotic. The operator is in the headset walking
# the robot across a room while the agent, asked in the chat panel to "go to the
# door", starts a walk_to. Both are legitimate requests. Only one can be obeyed.
#
# The person wearing the headset wins, and it is not close. They are in the room
# with the robot, they can see what it is about to hit, and they did not ask a
# language model for permission to move their own hands. An agent's plan can
# wait; the human's reflexes cannot.
#
# A lease rather than a lock, because a lock's failure mode here is a robot
# nobody can drive: hold one, crash, and locomotion is refused until somebody
# finds the stale file. A lease that has to be renewed expires on its own.

LEASE_NAME = "teleop_driving"

#: How long a lease stays valid without renewal. The dispatch loop renews at
#: 20-50 Hz, so this is generous by two orders of magnitude — it is sized so
#: that a teleop server which dies mid-session frees locomotion in about a
#: second, not so that a busy one keeps it by a hair.
LEASE_TTL_S = 2.0


def renew_teleop_lease() -> None:
    """Say that a teleop session is actively driving. Cheap enough for 50 Hz."""
    try:
        DEFAULT_RUN_DIR.mkdir(parents=True, exist_ok=True)
        path = DEFAULT_RUN_DIR / LEASE_NAME
        now = time.time()
        path.touch(exist_ok=True)
        os.utime(path, (now, now))
    except OSError:
        # Never fatal, and deliberately not logged: this runs every control
        # tick, and a warning here would be a log line 50 times a second. The
        # consequence of failing is that skills are not held off, which is the
        # behaviour we had before the lease existed.
        pass


def release_teleop_lease() -> None:
    """Drop the lease immediately, rather than waiting out the TTL."""
    try:
        (DEFAULT_RUN_DIR / LEASE_NAME).unlink(missing_ok=True)
    except OSError:
        pass


def teleop_is_driving() -> bool:
    """True if a teleop session renewed its lease within LEASE_TTL_S."""
    try:
        age = time.time() - (DEFAULT_RUN_DIR / LEASE_NAME).stat().st_mtime
    except OSError:
        return False
    return age <= LEASE_TTL_S
