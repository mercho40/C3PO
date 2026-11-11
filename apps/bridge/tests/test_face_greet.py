"""The face greeter: greets once per arrival, past a cooldown, never during a PARAR; check and speech faked."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading

import numpy as np
import pytest

from bridge import estop, face_gate, face_greet

SEEN, GONE, BUSY = None, (face_gate.NOT_SEEN, "x"), (face_gate.BUSY, "x")


@pytest.fixture
def greeter(tmp_path, monkeypatch):
    monkeypatch.setattr(face_gate, "FACE_DIR", tmp_path / "face")
    face_gate.save_enrollment(np.eye(128, dtype=np.float32)[:1])
    monkeypatch.setattr(face_gate, "models", lambda: None)
    monkeypatch.setattr(face_greet, "_last_at", None)
    monkeypatch.setattr(face_greet, "_speaking", lambda: False)
    monkeypatch.setattr(estop, "stop_is_standing", lambda: False)
    spoken: list[str] = []

    async def drive(*steps):
        """Run the greeter over these check outcomes; a callable step runs before the next one."""
        queue = list(steps)

        async def live_check(*, background=False):
            assert background
            while queue and callable(queue[0]):
                queue.pop(0)()
            if not queue:
                raise asyncio.CancelledError  # ends the loop
            return queue.pop(0)

        async def speak(text):
            spoken.append(text)

        monkeypatch.setattr(face_gate, "live_check", live_check)
        with pytest.raises(asyncio.CancelledError):
            await face_greet.run(speak, tick_s=0)
        return spoken

    return drive


def _past_cooldown():
    face_greet._last_at -= face_greet.COOLDOWN_S + 1


async def test_greets_once_on_recognition_and_not_again_within_the_cooldown(greeter):
    assert await greeter(GONE, SEEN, SEEN, GONE, SEEN) == ["Hola, ¿qué querés que haga?"]


async def test_greets_again_only_after_the_cooldown_and_a_gap(greeter):
    assert len(await greeter(SEEN, _past_cooldown, SEEN)) == 1  # never left the view
    assert len(await greeter(GONE, SEEN)) == 2


async def test_never_while_a_stop_stands_or_speech_plays(greeter, monkeypatch):
    monkeypatch.setattr(estop, "stop_is_standing", lambda: True)
    assert await greeter(SEEN) == []
    monkeypatch.setattr(estop, "stop_is_standing", lambda: False)
    monkeypatch.setattr(face_greet, "_speaking", lambda: True)
    assert await greeter(GONE, SEEN) == []


async def test_the_enrolled_name_is_spoken(greeter):
    face_gate.save_name("Oliver")
    assert await greeter(SEEN) == ["Hola Oliver, ¿qué querés que haga?"]


def test_without_a_name_the_greeting_drops_it_cleanly(monkeypatch):
    assert face_greet.greeting(None) == "Hola, ¿qué querés que haga?"
    monkeypatch.setattr(face_greet, "GREETING", "{name}, hola")
    assert face_greet.greeting("Ana") == "Ana, hola"


async def test_a_busy_tick_is_skipped_not_counted_as_gone(greeter, monkeypatch):
    monkeypatch.setattr(face_greet, "COOLDOWN_S", 0.0)
    assert len(await greeter(SEEN, BUSY, SEEN)) == 1


async def test_a_check_already_running_makes_the_greeter_skip_without_checking(monkeypatch):
    monkeypatch.setattr(face_gate, "_check", lambda: pytest.fail("ran a second check"))
    assert face_gate._busy.acquire(blocking=False)
    try:
        assert (await face_gate.live_check(background=True))[0] == face_gate.BUSY
    finally:
        face_gate._busy.release()


async def test_a_gated_call_waits_out_a_greeter_check_instead_of_refusing(monkeypatch):
    monkeypatch.setattr(face_gate, "GATED", frozenset({"pick"}))
    release = threading.Event()

    def slow_pass():
        release.wait(1.0)

    monkeypatch.setattr(face_gate, "_check", slow_pass)
    greeter_check = asyncio.create_task(face_gate.live_check(background=True))
    await asyncio.sleep(0.05)
    asyncio.get_running_loop().call_later(0.1, release.set)
    assert await face_gate.refusal("pick") is None
    assert await greeter_check is None


async def test_no_enrollment_no_greeting_no_crash(greeter, monkeypatch):
    face_gate.enrollment_path().unlink()
    monkeypatch.setattr(face_gate, "live_check", lambda **_: pytest.fail("checked"))
    await face_greet.run(lambda text: pytest.fail("spoke"), tick_s=0)
    assert face_greet.status()["on"] is False


async def test_it_runs_only_when_turned_on_and_not_in_stub(monkeypatch):
    started = []

    async def run(speak):
        started.append(speak)

    monkeypatch.setattr(face_greet, "run", run)
    for on, sim in ((False, "real"), (True, "stub"), (True, "real")):
        monkeypatch.setattr(face_greet, "ON", on)
        monkeypatch.setenv("SIM_MODE", sim)
        async with face_greet.greeting_operator(print):
            await asyncio.sleep(0)
    assert started == [print]


def test_off_by_default():
    env = {k: v for k, v in os.environ.items() if not k.startswith("C3PO_FACE_GREET")}
    out = subprocess.run(
        [sys.executable, "-c", "from bridge import face_greet; print(face_greet.ON)"],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "False"


async def test_status_reports_name_and_age_and_no_biometrics(greeter):
    face_gate.save_name("Oliver")
    await greeter(SEEN)
    s = face_greet.status()
    assert s["enrolled_name"] == "Oliver" and 0 <= s["last_greeted_age_s"] < 5
    assert set(s) == {"on", "enrolled_name", "last_greeted_age_s"}


async def test_safety_status_carries_the_greeter(greeter):
    import json

    from bridge import mcp_server

    body = json.loads((await mcp_server.safety_status(None)).body)
    assert body["face_greet"] == {"on": False, "enrolled_name": None, "last_greeted_age_s": None}
