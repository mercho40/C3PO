"""The face greeter: greets once per arrival, past a per-person cooldown, never during a PARAR;
a live stranger once per visit; check and speech faked."""

from __future__ import annotations

import asyncio
import os
import subprocess
import sys
import threading

import numpy as np
import pytest

from bridge import estop, face_gate, face_greet

GONE, BUSY = (face_gate.NOT_SEEN, "x"), (face_gate.BUSY, "x")
NOBODY, STRANGER = (face_gate.NO_FACE, "x"), (face_gate.UNKNOWN, "x")
UNKNOWN_HELLO = "Hola, no te conozco. ¿Cómo te llamás?"
SEEN = ANA = None  # (PASSED, id) of Oliver and Ana, set by the fixture


@pytest.fixture
def greeter(tmp_path, monkeypatch):
    global SEEN, ANA
    monkeypatch.setattr(face_gate, "FACE_DIR", tmp_path / "face")
    monkeypatch.setattr(face_gate, "_stored", {"greet": True})
    e = np.eye(128, dtype=np.float32)
    SEEN = (face_gate.PASSED, face_gate.add_person("Oliver", "superadmin", e[:1])["id"])
    ANA = (face_gate.PASSED, face_gate.add_person("Ana", "operator", e[1:2])["id"])
    monkeypatch.setattr(face_gate, "models", lambda: None)
    for name, value in (("_last_at", {}), ("_last_name", None), ("_last_greeted_at", None)):
        monkeypatch.setattr(face_greet, name, value)
    for name, value in (
        ("_unknown_count", 0),
        ("_unknown_seen_at", None),
        ("_unknown_greeted_at", None),
    ):
        monkeypatch.setattr(face_greet, name, value)
    monkeypatch.setattr(face_greet, "_speaking", lambda: False)
    monkeypatch.setattr(estop, "stop_is_standing", lambda: False)
    spoken: list[str] = []

    async def drive(*steps):
        """Run the greeter over these check outcomes; a callable step runs before the next one."""
        queue = list(steps)

        async def live_check(*, background=False, unknown=False):
            assert background and unknown == face_gate.settings()["greet_unknown"]
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
    for k in face_greet._last_at:
        face_greet._last_at[k] -= face_greet.COOLDOWN_S + 1


async def test_greets_once_on_recognition_and_not_again_within_the_cooldown(greeter):
    assert await greeter(GONE, SEEN, SEEN, GONE, SEEN) == ["Hola Oliver, ¿qué querés que haga?"]


async def test_the_cooldown_is_per_person(greeter):
    said = await greeter(SEEN, ANA, SEEN, GONE, ANA)
    assert said == ["Hola Oliver, ¿qué querés que haga?", "Hola Ana, ¿qué querés que haga?"]
    assert face_greet.status()["last_greeted_name"] == "Ana"


async def test_a_stranger_is_greeted_once_per_visit_and_past_its_cooldown(greeter, monkeypatch):
    monkeypatch.setattr(face_gate, "_stored", {"greet_unknown": True})
    monkeypatch.setattr(face_greet, "UNKNOWN_COOLDOWN_S", 0.0)
    # One visit (GONE is a face, not live or not decided: no gap), even with no cooldown.
    assert await greeter(STRANGER, STRANGER, GONE, STRANGER) == [UNKNOWN_HELLO]
    assert await greeter(STRANGER, NOBODY, STRANGER) == [UNKNOWN_HELLO] * 3  # a gap: a new visit
    monkeypatch.setattr(face_greet, "UNKNOWN_COOLDOWN_S", 120.0)
    assert await greeter(NOBODY, STRANGER) == [UNKNOWN_HELLO] * 3  # new visit, inside cooldown
    assert face_greet.unknown_seen()["count"] == 4
    assert 0 <= face_greet.unknown_seen()["last_age_s"] < 5


async def test_settings_off_idle_without_looking_and_apply_live(greeter, monkeypatch):
    monkeypatch.setattr(face_gate, "_stored", {"greet": False})
    ticks = []
    real = face_gate.settings

    def settings():
        ticks.append(1)
        if len(ticks) == 3:  # two idle ticks, then the console turns it on
            face_gate._stored["greet"] = True
        return real()

    monkeypatch.setattr(face_gate, "settings", settings)
    # The fake check would hand SEEN to an idle tick: only the third tick may look.
    assert await greeter(SEEN) == ["Hola Oliver, ¿qué querés que haga?"]


async def test_never_while_a_stop_stands_or_speech_plays(greeter, monkeypatch):
    monkeypatch.setattr(estop, "stop_is_standing", lambda: True)
    assert await greeter(SEEN) == []
    monkeypatch.setattr(estop, "stop_is_standing", lambda: False)
    monkeypatch.setattr(face_greet, "_speaking", lambda: True)
    assert await greeter(GONE, SEEN) == []


async def test_the_old_layout_is_greeted_by_its_name_when_it_cannot_migrate(greeter, monkeypatch):
    monkeypatch.setattr(face_gate, "_migrate", lambda: None)
    face_gate.save_enrollment(np.eye(128, dtype=np.float32)[2:3])
    face_gate.save_name("Oliver")
    assert await greeter((face_gate.PASSED, "operator")) == ["Hola Oliver, ¿qué querés que haga?"]


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

    def slow_pass(unknown, record):
        release.wait(1.0)
        return face_gate.PASSED, "someone"

    monkeypatch.setattr(face_gate, "_check", slow_pass)
    greeter_check = asyncio.create_task(face_gate.live_check(background=True))
    await asyncio.sleep(0.05)
    asyncio.get_running_loop().call_later(0.1, release.set)
    assert await face_gate.refusal("pick") is None
    assert await greeter_check == (face_gate.PASSED, "someone")


async def test_no_enrollment_no_greeting_no_crash(greeter):
    face_gate.delete_all()
    said = await greeter((face_gate.NOT_ENROLLED, "x"), (face_gate.NOT_ENROLLED, "x"))
    assert said == [] and face_greet.status()["on"] is False


async def test_it_runs_whenever_not_in_stub(monkeypatch):
    started = []

    async def run(speak):
        started.append(speak)

    monkeypatch.setattr(face_greet, "run", run)
    for sim in ("stub", "real"):
        monkeypatch.setenv("SIM_MODE", sim)
        async with face_greet.greeting_operator(print):
            await asyncio.sleep(0)
    assert started == [print]


def test_off_by_default():
    env = {k: v for k, v in os.environ.items() if not k.startswith("C3PO_FACE_GREET")}
    env["C3PO_FACE_DIR"] = "/nonexistent-c3po-face"
    code = "from bridge import face_gate; s = face_gate.settings(); print(s['greet'], s['greet_unknown'])"
    out = subprocess.run(
        [sys.executable, "-c", code],
        env=env,
        capture_output=True,
        text=True,
        check=True,
    )
    assert out.stdout.strip() == "False False"


async def test_status_reports_name_and_age_and_settings_and_no_biometrics(greeter):
    await greeter(SEEN)
    s = face_greet.status()
    assert s["last_greeted_name"] == "Oliver" and 0 <= s["last_greeted_age_s"] < 5
    assert s["settings"] == {"gate": False, "greet": True, "greet_unknown": False}
    assert set(s) == {"on", "settings", "last_greeted_name", "last_greeted_age_s", "unknown_seen"}


async def test_safety_status_carries_the_greeter(greeter):
    import json

    from bridge import mcp_server

    body = json.loads((await mcp_server.safety_status(None)).body)
    assert body["face_greet"] == {
        "on": False,
        "settings": {"gate": False, "greet": True, "greet_unknown": False},
        "last_greeted_name": None,
        "last_greeted_age_s": None,
        "unknown_seen": {"count": 0, "last_age_s": None},
    }
