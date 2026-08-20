"""Shared fixtures. Chiefly: keep the tests out of the developer's real e-stop state.

The stop sentinel is a file under `~/.c3po/run`, and it is deliberately durable
— a stop must survive a restart. That makes it shared mutable state that the
test suite would otherwise both read and write.

It bit for real. A stop pressed on the robot left the sentinel on disk, and
once `TeleopSession` learned to inherit a standing stop (correctly — see
`check_estop`), nineteen unrelated tests began failing on a machine where
someone had pressed PARAR hours earlier. The tests were right and the
robot's history was leaking into them.

So every test gets its own run directory. Two mechanisms, because the sentinel
is reached two ways: the module global for in-process readers, and the
environment variable for anything that spawns a real second interpreter.
"""

from __future__ import annotations

import pytest

from bridge import estop


@pytest.fixture(autouse=True)
def isolated_run_dir(tmp_path, monkeypatch):
    run_dir = tmp_path / "run"
    run_dir.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(estop, "DEFAULT_RUN_DIR", run_dir)
    monkeypatch.setenv("C3PO_RUN_DIR", str(run_dir))
    return run_dir


@pytest.fixture(autouse=True)
def fresh_hand_driver():
    """The hand driver is a process singleton, so it must not outlive a test.

    It is built from environment variables that tests monkeypatch, and caching
    it across tests would mean the first test to touch it decides what every
    later one sees.
    """
    from bridge.teleop import hands

    hands.reset_driver()
    yield
    hands.reset_driver()
