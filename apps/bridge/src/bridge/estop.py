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

#: Same directory the robot scripts keep pidfiles in, so a deployment already
#: creates it and `stop_c3po` already knows where to look.
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
