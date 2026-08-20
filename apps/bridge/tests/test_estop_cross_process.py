"""The e-stop has to cross a process boundary, and once did not.

`stop_everything` runs in the MCP server's process. The teleop stream runs in
its own. `TaskRegistry` is an in-memory per-process singleton, so cancelling a
task in one registry says nothing to the other — and on the robot, 2026-08-20,
PARAR left it turning for another 27 degrees.

Every test that existed passed, because they all ran both halves in one
process. These do not: the tests below either drive the sentinel directly, or
run `stop_everything` in a genuinely separate interpreter.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time

import pytest

from bridge import estop


@pytest.fixture(autouse=True)
def _isolated_run_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(estop, "DEFAULT_RUN_DIR", tmp_path)
    return tmp_path


def test_no_stop_ever_reads_as_zero():
    # Must never read as "a stop just happened" — that would latch every
    # session on startup and make the robot undriveable.
    assert estop.last_stop_at() == 0.0


def test_a_stop_is_visible_afterwards():
    before = time.time()
    estop.signal_stop()
    assert estop.last_stop_at() >= before


def test_a_second_stop_is_distinguishable_from_the_first():
    first = estop.signal_stop()
    time.sleep(0.05)
    second = estop.signal_stop()
    assert second > first
    assert estop.last_stop_at() >= second


def test_signalling_never_raises_on_an_unwritable_path(monkeypatch, tmp_path):
    # This runs after in-process cancellation, so it must never be able to turn
    # a working e-stop into a failed one.
    monkeypatch.setattr(estop, "DEFAULT_RUN_DIR", tmp_path / "nope" / "nested")
    (tmp_path / "nope").write_text("not a directory")
    estop.signal_stop()  # must not raise
    assert estop.last_stop_at() == 0.0


def test_a_stop_in_another_process_is_visible_here(tmp_path):
    """The actual regression, with a real second interpreter.

    Nothing is shared but the filesystem — which is exactly the situation the
    bridge and the teleop stream are in.
    """
    env = {**os.environ, "C3PO_RUN_DIR": str(tmp_path)}
    before = time.time()

    subprocess.run(
        [sys.executable, "-c",
         "from bridge.estop import signal_stop; signal_stop()"],
        env=env, check=True, capture_output=True,
    )

    # Read it the way the teleop session does, against the same directory.
    assert (tmp_path / estop.SENTINEL_NAME).exists()
    mtime = (tmp_path / estop.SENTINEL_NAME).stat().st_mtime
    assert mtime >= before


def test_stop_everything_signals_the_sentinel(tmp_path, monkeypatch):
    """Belt and braces: the skill itself must write it, not just the helper."""
    import asyncio

    from bridge.skills import stop_everything

    monkeypatch.setattr(stop_everything, "stop_motion_sync", lambda **kw: None)
    monkeypatch.setattr(stop_everything, "SIM_MODE", "stub")

    before = time.time()
    asyncio.run(stop_everything.run())

    assert estop.last_stop_at() >= before, (
        "stop_everything did not signal across processes — a teleop stream in "
        "another process would keep driving the robot."
    )
