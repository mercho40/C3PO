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


# These read the FILE, not last_stop_at(): in the process that signalled, the
# in-process copy would pass them even if the sentinel were never written.


def test_a_stop_is_visible_afterwards():
    before = time.time()
    assert estop.signal_stop() is True
    assert estop._file_stop_at() >= before


def test_a_second_stop_is_distinguishable_from_the_first():
    estop.signal_stop()
    first = estop._file_stop_at()
    time.sleep(0.05)
    estop.signal_stop()
    assert estop._file_stop_at() > first


def test_the_in_process_copy_and_the_file_agree():
    estop.signal_stop()
    assert estop._file_stop_at() == pytest.approx(estop._signalled_at, abs=1e-3)
    assert estop.last_stop_at() == pytest.approx(estop._file_stop_at(), abs=1e-3)


def test_signalling_never_raises_on_an_unwritable_path(monkeypatch, tmp_path):
    # This runs after in-process cancellation, so it must never be able to turn
    # a working e-stop into a failed one.
    monkeypatch.setattr(estop, "DEFAULT_RUN_DIR", tmp_path / "nope" / "nested")
    (tmp_path / "nope").write_text("not a directory")
    before = time.time()
    assert estop.signal_stop() is False  # must not raise
    # ...and must not unlatch this process: the stop it failed to write still stands.
    assert estop._file_stop_at() == 0.0
    assert estop.last_stop_at() >= before
    assert estop.stop_is_standing() is True


def test_a_deleted_sentinel_does_not_unlatch_the_process_that_stopped():
    estop.signal_stop()
    estop.sentinel_path().unlink()
    assert estop.stop_is_standing() is True


def test_a_stop_in_another_process_is_visible_here(tmp_path):
    """The actual regression, with a real second interpreter.

    Nothing is shared but the filesystem — which is exactly the situation the
    bridge and the teleop stream are in.
    """
    env = {**os.environ, "C3PO_RUN_DIR": str(tmp_path)}
    before = time.time()

    subprocess.run(
        [sys.executable, "-c", "from bridge.estop import signal_stop; signal_stop()"],
        env=env,
        check=True,
        capture_output=True,
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
    result = asyncio.run(stop_everything.run())

    assert result["sentinel_written"] is True
    assert estop._file_stop_at() >= before, (
        "stop_everything did not signal across processes — a teleop stream in "
        "another process would keep driving the robot."
    )


def test_stop_everything_reports_a_sentinel_it_could_not_write(tmp_path, monkeypatch):
    """The operator must learn the teleop process may not have heard the stop."""
    import asyncio

    from bridge.skills import stop_everything

    monkeypatch.setattr(stop_everything, "stop_motion_sync", lambda **kw: None)
    monkeypatch.setattr(stop_everything, "SIM_MODE", "stub")
    (tmp_path / "nope").write_text("not a directory")
    monkeypatch.setattr(estop, "DEFAULT_RUN_DIR", tmp_path / "nope" / "nested")

    result = asyncio.run(stop_everything.run())

    assert result["sentinel_written"] is False
    assert estop.stop_is_standing() is True


# -- A2: a stop signalled elsewhere cancels what runs here --------------------


def _stamp_elsewhere(at: float) -> None:
    """What another process's signal_stop leaves: the file, not our _signalled_at."""
    path = estop.sentinel_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"{at}\n")
    os.utime(path, (at, at))


async def test_a_stop_from_another_process_cancels_a_walk_running_here(tmp_path, monkeypatch):
    """The headset's PARAR down the teleop socket only stamps the sentinel. It
    latched this process, but a walk, an arm move or a Nav2 monitor already
    running in it went on. The stop run here must not re-stamp the file either:
    /safety/ack refuses (409) a stop newer than the one the operator saw."""
    import asyncio

    from bridge.sdk import g1_rpc
    from bridge.skills import _locomotion, stop_everything, walk_velocity
    from bridge.skills.task_runtime import get_registry

    monkeypatch.setattr(stop_everything, "stop_motion_sync", lambda **kw: (20, 20))
    monkeypatch.setattr(stop_everything, "SIM_MODE", "stub")
    monkeypatch.setattr(_locomotion, "SIM_MODE", "real")
    monkeypatch.setattr(g1_rpc, "call_set_velocity", lambda *a: (0, ""))

    watcher = asyncio.create_task(stop_everything.watch_foreign_stops())
    walk = asyncio.ensure_future(walk_velocity.run(vx=0.1, vy=0.0, vyaw=0.0, duration_s=3.0))
    try:
        await asyncio.sleep(0.05)
        (task,) = get_registry().list_active()
        await asyncio.to_thread(
            subprocess.run,
            [sys.executable, "-c", "from bridge.estop import signal_stop; signal_stop()"],
            env={**os.environ, "C3PO_RUN_DIR": str(tmp_path)},
            check=True,
            capture_output=True,
        )
        stamped = estop._file_stop_at()
        assert stamped > 0.0
        started = time.monotonic()
        while not task.cancel_event.is_set() and time.monotonic() - started < 2.0:
            await asyncio.sleep(0.005)
        cancelled_after = time.monotonic() - started

        result = await asyncio.wait_for(walk, 2.0)
    finally:
        watcher.cancel()
        walk.cancel()

    assert cancelled_after < 0.3
    assert result["status"] == "cancelled"
    assert estop._file_stop_at() == stamped, "re-stamped: /safety/ack would answer 409"
    assert estop._signalled_at == 0.0
    assert estop.stop_is_standing() is True


async def test_the_watcher_skips_old_stops_and_this_process_own(monkeypatch):
    import asyncio

    from bridge.skills import stop_everything

    runs: list[dict] = []

    async def fake_run(**kwargs):
        runs.append(kwargs)
        return {}

    monkeypatch.setattr(stop_everything, "run", fake_run)
    monkeypatch.setattr(stop_everything, "FOREIGN_STOP_POLL_S", 0.01)
    _stamp_elsewhere(time.time() - 5.0)  # before the bridge started: already latched

    watcher = asyncio.create_task(stop_everything.watch_foreign_stops())
    try:
        await asyncio.sleep(0.05)
        assert runs == []
        estop.signal_stop()  # this process's own PARAR: its run() did the work
        await asyncio.sleep(0.05)
        assert runs == []
        _stamp_elsewhere(time.time() + 0.01)
        await asyncio.sleep(0.1)
    finally:
        watcher.cancel()

    assert runs == [{"signal": False}]  # once per stop, and without re-stamping


async def test_a_failing_stop_is_not_retried_and_the_watcher_lives_on(monkeypatch):
    """Retrying the same stamp would resend Damp every poll."""
    import asyncio

    from bridge.skills import stop_everything

    runs: list[dict] = []

    async def failing_run(**kwargs):
        runs.append(kwargs)
        raise RuntimeError("dds down")

    monkeypatch.setattr(stop_everything, "run", failing_run)
    monkeypatch.setattr(stop_everything, "FOREIGN_STOP_POLL_S", 0.01)

    watcher = asyncio.create_task(stop_everything.watch_foreign_stops())
    try:
        await asyncio.sleep(0.03)
        _stamp_elsewhere(time.time())
        await asyncio.sleep(0.1)
        assert len(runs) == 1
        _stamp_elsewhere(time.time() + 0.01)
        await asyncio.sleep(0.1)
        assert len(runs) == 2
        assert not watcher.done()
    finally:
        watcher.cancel()


@pytest.mark.parametrize("runner", ["_run_streamable_http", "_run_stdio"])
async def test_the_bridge_watches_for_as_long_as_it_serves(monkeypatch, runner):
    """Process-wide, not FastMCP's lifespan: that one is per MCP session, and
    with apps/back down there is no session at all."""
    import asyncio

    import uvicorn

    import bridge.mcp_server as server
    from bridge.skills import stop_everything

    state: dict[str, bool] = {}

    async def fake_watch():
        state["started"] = True
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            state["cancelled"] = True
            raise

    async def fake_serve(*args, **kwargs):
        await asyncio.sleep(0.05)
        state["watching_while_serving"] = state.get("started", False)

    monkeypatch.setattr(stop_everything, "watch_foreign_stops", fake_watch)
    monkeypatch.setattr(stop_everything, "SIM_MODE", "real")
    monkeypatch.setattr(uvicorn.Server, "serve", fake_serve)
    monkeypatch.setattr(server.mcp, "run_stdio_async", fake_serve)

    await getattr(server, runner)()
    await asyncio.sleep(0.01)  # let the cancellation land

    assert state == {"started": True, "watching_while_serving": True, "cancelled": True}


async def test_stub_has_no_watcher_its_stop_tool_is_a_no_op_too(monkeypatch):
    from bridge.skills import stop_everything

    async def fake_watch():
        raise AssertionError("watched in stub")

    monkeypatch.setattr(stop_everything, "watch_foreign_stops", fake_watch)
    monkeypatch.setattr(stop_everything, "SIM_MODE", "stub")

    async with stop_everything.watching_foreign_stops():
        pass


@pytest.mark.parametrize(
    ("transport", "runner"), [("http", "_run_streamable_http"), ("stdio", "_run_stdio")]
)
def test_main_serves_each_transport_through_its_watched_runner(monkeypatch, transport, runner):
    import bridge.mcp_server as server

    ran: list[str] = []

    async def fake_runner():
        ran.append(runner)

    monkeypatch.setattr(server, "SIM_MODE", "stub")
    monkeypatch.setattr(server, "BRIDGE_TRANSPORT", transport)
    monkeypatch.setattr(server, runner, fake_runner)

    server.main()

    assert ran == [runner]


async def test_a_foreign_stop_beside_this_process_own_is_harmless(monkeypatch):
    """Both run at once when back's PARAR and the headset's land together."""
    import asyncio

    from bridge.skills import stop_everything
    from bridge.skills.task_runtime import get_registry

    monkeypatch.setattr(stop_everything, "stop_motion_sync", lambda **kw: (20, 20))
    monkeypatch.setattr(stop_everything, "SIM_MODE", "stub")
    task = get_registry().create("fake_walk")
    try:
        own, foreign = await asyncio.gather(
            stop_everything.run(), stop_everything.run(signal=False)
        )
    finally:
        task.status = "cancelled"
        task.ended_at = time.time()

    assert task.cancel_event.is_set()
    assert own["cancelled_task_ids"] + foreign["cancelled_task_ids"] == [task.task_id]
    assert (own["sentinel_written"], foreign["sentinel_written"]) == (True, None)
