"""Greet enrolled people by name, and optionally a live stranger, as their face comes into view.

Live settings (face_gate.settings): `greet` (default C3PO_FACE_GREET=1) and `greet_unknown`
(default off). With both off the loop idles without looking. Every TICK_S it runs
face_gate's own live check on face_gate's own worker, as a background check: when a gated
call's check is running the tick is skipped, and a gated call waits a greeter check out
instead of being refused by it. A person is greeted on the step to recognising them, past
THEIR cooldown. A live face that matches nobody gets GREETING_UNKNOWN once per visit (a
tick with no face at all ends the visit), past UNKNOWN_COOLDOWN_S; of it only a count and a
time are kept. It speaks through `say` (so PARAR's stop_play cuts it), never while a PARAR
stands or our speech is playing. It never moves anything, and shares no lock with
stop_everything. Logs carry no features, images or measurements.
"""

from __future__ import annotations

import asyncio
import os
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

import structlog

from bridge import estop, face_gate
from bridge.safety import _env_float

log = structlog.get_logger(__name__)

GREETING = os.environ.get("C3PO_FACE_GREETING") or "Hola {name}, ¿qué querés que haga?"
GREETING_UNKNOWN = (
    os.environ.get("C3PO_FACE_GREETING_UNKNOWN") or "Hola, no te conozco. ¿Cómo te llamás?"
)
COOLDOWN_S = _env_float("C3PO_FACE_GREET_COOLDOWN_S", 300.0)
UNKNOWN_COOLDOWN_S = _env_float("C3PO_FACE_GREET_UNKNOWN_COOLDOWN_S", 120.0)
TICK_S = 1.0

_running = False
#: Person id -> when they were last greeted (monotonic).
_last_at: dict[str, float] = {}
_last_greeted_at: float | None = None
#: Strangers: visits seen (live, matching nobody), and the last greeting's time. Nothing else.
_unknown_count = 0
_unknown_seen_at: float | None = None
_unknown_greeted_at: float | None = None


def greeting(name: str | None) -> str:
    """GREETING with the name in, or with the name (and the space before it) dropped."""
    if name:
        return GREETING.replace("{name}", name)
    return re.sub(r"\s*\{name\}", "", GREETING)


def _speaking() -> bool:
    try:
        from bridge.sdk.audio_msg import get_audio_msg_link

        return get_audio_msg_link().is_playing()
    except Exception:  # noqa: BLE001 - no audio_msg link (sim, laptop): nothing detectable
        return False


def _quiet() -> bool:
    return estop.stop_is_standing() or _speaking()


def _past(at: float | None, cooldown_s: float) -> bool:
    return at is None or time.monotonic() - at >= cooldown_s


async def run(speak: Callable[[str], Awaitable[Any]], tick_s: float = TICK_S) -> None:
    """The greeter loop, for as long as it is not cancelled. Idles while both settings are off."""
    global _running, _last_greeted_at, _unknown_count, _unknown_seen_at
    global _unknown_greeted_at
    _running, prev, visit, visit_greeted = True, None, False, False
    log.info("face_greet.on", cooldown_s=COOLDOWN_S, unknown_cooldown_s=UNKNOWN_COOLDOWN_S)
    try:
        while True:
            await asyncio.sleep(tick_s)
            try:
                s = face_gate.settings()
                if not (s["greet"] or s["greet_unknown"]):
                    prev = None
                    continue
                code, detail = await face_gate.live_check(
                    background=True, unknown=s["greet_unknown"]
                )
                if code == face_gate.BUSY:
                    continue  # a gated call's check is running: never compete with it
                who = {face_gate.PASSED: detail, face_gate.UNKNOWN: ""}.get(code)
                was, prev = prev, who
                if code == face_gate.NO_FACE:
                    visit = visit_greeted = False
                if who == "":
                    if not visit:
                        visit, _unknown_count = True, _unknown_count + 1
                        _unknown_seen_at = time.monotonic()
                    if visit_greeted or not _past(_unknown_greeted_at, UNKNOWN_COOLDOWN_S):
                        continue
                    if _quiet():
                        continue
                    await speak(GREETING_UNKNOWN)
                    visit_greeted, _unknown_greeted_at = True, time.monotonic()
                    log.info("face_greet.greeted_unknown")
                    continue
                if not who or who == was or not s["greet"]:
                    continue
                if not _past(_last_at.get(who), COOLDOWN_S) or _quiet():
                    continue
                p = face_gate.person(who)
                name = p["name"] if p else face_gate.enrolled_name()
                await speak(greeting(name))
                _last_at[who] = _last_greeted_at = time.monotonic()
                log.info("face_greet.greeted", person=who)
            except Exception:
                log.exception("face_greet.tick_failed")
    finally:
        _running = False


@asynccontextmanager
async def greeting_operator(speak: Callable[[str], Awaitable[Any]]) -> AsyncIterator[None]:
    """`run` for as long as the server serves, like the foreign-stop watcher. Off in stub."""
    if os.environ.get("SIM_MODE", "stub") == "stub":
        yield
        return
    task = asyncio.create_task(run(speak))
    try:
        yield
    finally:
        task.cancel()


def _age(at: float | None) -> float | None:
    return None if at is None else round(time.monotonic() - at, 1)


def unknown_seen() -> dict[str, Any]:
    return {"count": _unknown_count, "last_age_s": _age(_unknown_seen_at)}


def status() -> dict[str, Any]:
    """For /safety/status, which is open: no names, no biometrics; ages and the settings."""
    return {
        "on": _running,
        "settings": face_gate.settings(),
        "last_greeted_age_s": _age(_last_greeted_at),
        "unknown_seen": unknown_seen(),
    }
