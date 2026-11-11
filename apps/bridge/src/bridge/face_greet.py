"""Greet the enrolled operator by name when their live face comes into view. Opt-in: C3PO_FACE_GREET=1.

Every TICK_S it runs face_gate's own live check on face_gate's own worker, as a background
check: when a gated call's check is running the tick is skipped, and a gated call waits a
greeter check out instead of being refused by it. On the step from not recognised to
recognised, past the cooldown, it speaks through `say` (so PARAR's stop_play cuts it), never
while a PARAR stands or our speech is playing. It never moves anything, and shares no lock
with stop_everything. Logs carry no features, images or measurements.
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

ON = os.environ.get("C3PO_FACE_GREET", "") == "1"
GREETING = os.environ.get("C3PO_FACE_GREETING") or "Hola {name}, ¿qué querés que haga?"
COOLDOWN_S = _env_float("C3PO_FACE_GREET_COOLDOWN_S", 300.0)
TICK_S = 1.0

_running = False
_last_at: float | None = None


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


async def run(speak: Callable[[str], Awaitable[Any]], tick_s: float = TICK_S) -> None:
    """The greeter loop. Returns at once, logged, when there is nobody or nothing to recognise."""
    global _running, _last_at
    try:
        if face_gate.enrolled() is None:
            log.info("face_greet.off", reason="no operator enrolled")
            return
        face_gate.models()
    except Exception as exc:  # noqa: BLE001 - a greeter that cannot run just does not
        log.warning("face_greet.off", reason=str(exc))
        return
    _running, seen = True, False
    log.info("face_greet.on", cooldown_s=COOLDOWN_S)
    try:
        while True:
            await asyncio.sleep(tick_s)
            try:
                outcome = await face_gate.live_check(background=True)
                if outcome is not None and outcome[0] == face_gate.BUSY:
                    continue  # a gated call's check is running: never compete with it
                now, was = outcome is None, seen
                seen = now
                if not now or was:
                    continue
                if _last_at is not None and time.monotonic() - _last_at < COOLDOWN_S:
                    continue
                if estop.stop_is_standing() or _speaking():
                    continue
                name = face_gate.enrolled_name()
                await speak(greeting(name))
                _last_at = time.monotonic()
                log.info("face_greet.greeted", named=name is not None)
            except Exception:
                log.exception("face_greet.tick_failed")
    finally:
        _running = False


@asynccontextmanager
async def greeting_operator(speak: Callable[[str], Awaitable[Any]]) -> AsyncIterator[None]:
    """`run` for as long as the server serves, like the foreign-stop watcher. Off in stub."""
    if not ON or os.environ.get("SIM_MODE", "stub") == "stub":
        yield
        return
    task = asyncio.create_task(run(speak))
    try:
        yield
    finally:
        task.cancel()


def status() -> dict[str, Any]:
    """For /safety/status. No biometrics."""
    return {
        "on": _running,
        "enrolled_name": face_gate.enrolled_name(),
        "last_greeted_age_s": None if _last_at is None else round(time.monotonic() - _last_at, 1),
    }
