"""Say what the robot is about to do before it moves: «Atención, todos los humanos
córranse: voy a agarrar la botella».

Called from `mcp_server.GovernedMCP.call_tool`, after every gate passed and the arguments
validated (a refused call says nothing), and before the tool runs. The tools in SELF plan
or check a lot before they move: for them call_tool only `defer`s, and the skill calls
`before_motion()` right before its first motion command, so a plan that fails says nothing.
It waits for the end of playback so a person has time to step away; a PARAR (say's
stop_play) or a soft_stop during the speech means the motion does not start.
"""

from __future__ import annotations

import math
import os
import time
from collections.abc import Awaitable, Callable
from contextvars import ContextVar, Token
from typing import Any

import structlog

from bridge import estop

log = structlog.get_logger(__name__)

PREFIX_DEFAULT = "Atención, todos los humanos córranse: voy a"
# The same phrase again this soon is one plan's next step, not news.
REPEAT_S = 5.0

# T1 tools that are not "about to move": stops and releases (never delay them), the
# headset's ~1 Hz walk_velocity renewals, and the navigation gate switches.
SKIP = frozenset(
    {
        "walk_velocity",
        "damp",
        "open_hands",
        "release_arm",
        "release_arm_control",
        "cancel_navigation",
        "disarm_navigation",
        "arm_navigation",
    }
)

# Tools that announce themselves via before_motion(), after planning: every path of theirs
# that moves calls it first (manipulation.execute, pick's step, walk_to.run, go_to_object's
# Nav2 start).
SELF = frozenset({"pick", "place", "reach", "handover", "walk_to", "go_to_object"})

LABELS = {
    "bottle": "la botella",
    "cup": "el vaso",
    "chair": "la silla",
    "table": "la mesa",
    "desk": "el escritorio",
    "person": "la persona",
    "door": "la puerta",
    "remote": "el control remoto",
    "wallet": "la billetera",
    "phone": "el teléfono",
    "cell phone": "el teléfono",
    "book": "el libro",
    "box": "la caja",
    "backpack": "la mochila",
    "ball": "la pelota",
}

SIMPLE = {
    "wave": "saludar con la mano",
    "clap": "aplaudir",
    "hug": "dar un abrazo",
    "shake_hand": "dar la mano",
    "dance": "bailar",
    "squat": "agacharme",
    "sit_g1": "sentarme",
    "lie_up": "levantarme",
    "prepare": "pararme",
    "balance_stand": "pararme en equilibrio",
    "start_walking": "activar la marcha",
    "start_walking_waist": "activar la marcha",
    "zero_torque": "soltar los motores",
    "point_at": "señalar",
    "look_around": "girar para mirar alrededor",
    "handover": "darte lo que tengo en la mano",
    "walk_to": "caminar",
    "navigate_to": "caminar hasta el punto marcado",
}

_last: tuple[str, float] = ("", 0.0)
#: When soft_stop last ran (monotonic): it latches nothing, so announce looks for it itself.
_soft_stop_at = 0.0
#: The SELF call in progress whose warning is still due: (tool, validated args, speak).
_pending: ContextVar[tuple[str, dict[str, Any], Callable[[str], Awaitable[Any]]] | None] = (
    ContextVar("motion_announce_pending", default=None)
)


def note_soft_stop() -> None:
    global _soft_stop_at
    _soft_stop_at = time.monotonic()


def defer(
    name: str, args: dict[str, Any], speak: Callable[[str], Awaitable[Any]]
) -> Token[Any]:
    """call_tool, for a SELF tool: its warning is due at its first motion command."""
    return _pending.set((name, args, speak))


def reset(token: Token[Any]) -> None:
    _pending.reset(token)


async def before_motion() -> bool:
    """Skills call this right before their first motion command. Speaks the deferred warning
    once per call (later calls, and calls not from call_tool, return True at once). False:
    a stop came during the warning, so don't move."""
    pending = _pending.get()
    if pending is None:
        return True
    _pending.set(None)
    return await announce(*pending)


def enabled() -> bool:
    return os.environ.get("C3PO_ANNOUNCE_MOTION", "1") != "0"


def applies(name: str, meta: dict[str, Any] | None) -> bool:
    tier = ((meta or {}).get("c3po") or {}).get("tier")
    return enabled() and tier in ("T1", "T2") and name not in SKIP


def _thing(label: Any) -> str:
    text = str(label or "").strip()
    if not text:
        return "el objeto"
    return LABELS.get(text.lower(), text)


def _side(args: dict[str, Any], feminine: bool) -> str | None:
    """« derecho» / « izquierda», or None for both sides."""
    side = args.get("side")
    if side == "both":
        return None
    if side == "left":
        return " izquierda" if feminine else " izquierdo"
    if side in ("right", None):
        return " derecha" if feminine else " derecho"
    return ""  # "auto": the planner picks


def action(name: str, args: dict[str, Any] | None) -> str:
    """What the robot is about to do, as the end of «voy a …»."""
    a = args or {}
    if name == "pick":
        return f"agarrar {_thing(a.get('label'))}"
    if name == "place":
        onto = a.get("onto")
        return f"dejar lo que tengo sobre {_thing(onto)}" if onto else "dejar lo que tengo en la mano"
    if name == "go_to_object":
        return f"ir hasta {_thing(a.get('query'))}"
    if name == "navigate_to_landmark":
        return f"ir hasta {a.get('name') or 'el lugar marcado'}"
    if name == "turn":
        deg = round(math.degrees(float(a.get("delta_yaw_radians") or 0.0)))
        way = "izquierda" if deg >= 0 else "derecha"
        return f"girar {abs(deg)} grados a la {way}"
    if name in ("move_arm", "reach"):
        verb = "mover" if name == "move_arm" else "estirar"
        side = _side(a, feminine=False)
        return f"{verb} los dos brazos" if side is None else f"{verb} el brazo{side}"
    if name == "set_hand":
        side = _side(a, feminine=True)
        return "mover las dos manos" if side is None else f"mover la mano{side}"
    if name == "gesture":
        return "hacer un gesto"
    return SIMPLE.get(name, "moverme")


def phrase(name: str, args: dict[str, Any] | None) -> str:
    prefix = os.environ.get("C3PO_ANNOUNCE_MOTION_TEXT") or PREFIX_DEFAULT
    return f"{prefix} {action(name, args)}."


async def announce(
    name: str,
    args: dict[str, Any] | None,
    speak: Callable[[str], Awaitable[Any]],
    now: Callable[[], float] = time.monotonic,
) -> bool:
    """Speak the warning and wait for it to end. False when a stop (PARAR, soft_stop, or
    anything that cut the warning) came meanwhile: don't move."""
    global _last
    text = phrase(name, args)
    t = now()
    if _last[0] == text and t - _last[1] < REPEAT_S:
        return True
    stopped_before, soft_before = estop.last_stop_at(), _soft_stop_at
    status: Any = "error"
    try:
        result = await speak(text)
        status = result.get("status") if isinstance(result, dict) else None
        if status not in (None, "ok", "stopped"):
            log.warning("motion.announce_failed", tool=name, result=status)
    except Exception as exc:  # noqa: BLE001 - a broken speaker must not hide the motion's own result
        log.warning("motion.announce_failed", tool=name, error=str(exc))
    if estop.last_stop_at() > stopped_before or _soft_stop_at != soft_before or status == "stopped":
        log.warning("motion.announce_stopped", tool=name)
        return False
    if status in (None, "ok"):
        _last = (text, t)  # only a warning heard in full spares the next identical one
    log.info("motion.announced", tool=name, text=text)
    return True
