"""Wire format for the teleop stream, and the parser that refuses bad frames.

One JSON object per WebSocket text message, browser -> bridge, at whatever
rate the headset's `requestAnimationFrame` runs (72-120 Hz on a Quest 3; the
client is expected to decimate to ~30-50 Hz before sending, see
`apps/web/src/lib/webxr/xr-teleop.ts`).

    {
      "v": 1,
      "seq": 1234,
      "t": 6042.5,                       // client performance.now(), ms
      "enabled": true,                   // dead-man held on the client
      "walk": 0.0,                       // -1 back .. +1 forward, from the buttons
      "arms": true,                      // operator wants the arms mirrored
      "head": { "yaw": 0.21, "pos": [0.0, 1.62, 0.0] },
      "hands": {
        "left":  { "tracked": true, "pos": [...], "quat": [x,y,z,w], "grip": 0.0 },
        "right": { "tracked": false }
      }
    }

Everything is in the WebXR reference space: metres, Y-up, right-handed, -Z
forward, origin at the `local-floor` calibrated floor point. Quaternions are
`[x, y, z, w]` — the DOMPointReadOnly field order, *not* the `[w, x, y, z]`
that most robotics code uses. That mismatch is a classic silent-wrong-rotation
bug, so it is stated here and converted exactly once, in `retarget.py`.

Parsing posture: **strict, and hostile.** This socket carries setpoints for a
1.3 m humanoid's arms, so a malformed frame must be rejected rather than
coerced. In particular every float is checked for NaN/Inf — `float("nan")` is
a valid JSON-ish value that survives arithmetic silently, compares false to
every bound (so it passes `if x > MAX` clamps untouched), and lands in a
`float32` motor command as a NaN the firmware then has to interpret. A NaN
that reaches `q` is not a rounding error, it is an undefined arm.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from typing import Any

PROTOCOL_VERSION = 1

# A frame is one control tick. Anything appreciably larger than a headset's
# frame budget is either a buggy client or someone hand-crafting messages.
MAX_FRAME_BYTES = 4096

# Operator hands more than this far from the headset are a tracking artefact,
# not a reachable pose — Quest hand tracking emits wild positions for a frame
# or two as a hand leaves the tracking volume.
MAX_HAND_DISTANCE_M = 1.5


class FrameError(ValueError):
    """A frame that must not be acted on. Carries a short reason for the log."""


def clamp_unit(value: float) -> float:
    """Clamp to [-1, 1]. Clamped rather than rejected: an out-of-range walk
    axis is a client bug, not an attack, and dropping the whole frame would
    also drop the head yaw and both arms riding in it."""
    return max(-1.0, min(1.0, value))


def _finite(value: Any, field: str) -> float:
    """Coerce to float and reject NaN/Inf. See the module docstring."""
    try:
        out = float(value)
    except (TypeError, ValueError) as exc:
        raise FrameError(f"{field}: not a number ({value!r})") from exc
    if not math.isfinite(out):
        raise FrameError(f"{field}: not finite ({out!r})")
    return out


def _vec3(value: Any, field: str) -> tuple[float, float, float]:
    if not isinstance(value, (list, tuple)) or len(value) != 3:
        raise FrameError(f"{field}: expected 3 numbers, got {value!r}")
    return (
        _finite(value[0], f"{field}[0]"),
        _finite(value[1], f"{field}[1]"),
        _finite(value[2], f"{field}[2]"),
    )


def _quat(value: Any, field: str) -> tuple[float, float, float, float]:
    """Parse an `[x, y, z, w]` quaternion and require it to be near-unit.

    A non-unit quaternion is not merely untidy: the rotation matrix built from
    it in `retarget` scales as well as rotates, so a 0.5-norm quaternion
    halves every mapped direction and the arm reads as "operator's hand is
    near their shoulder" — a large, plausible-looking, entirely wrong pose.
    """
    if not isinstance(value, (list, tuple)) or len(value) != 4:
        raise FrameError(f"{field}: expected 4 numbers, got {value!r}")
    x = _finite(value[0], f"{field}[0]")
    y = _finite(value[1], f"{field}[1]")
    z = _finite(value[2], f"{field}[2]")
    w = _finite(value[3], f"{field}[3]")
    norm = math.sqrt(x * x + y * y + z * z + w * w)
    if not (0.9 <= norm <= 1.1):
        raise FrameError(f"{field}: not a unit quaternion (|q| = {norm:.3f})")
    return (x / norm, y / norm, z / norm, w / norm)


@dataclass(frozen=True)
class HandSample:
    """One wrist, as the headset sees it.

    `grip` is the client's already-reduced finger closure: 0.0 fully open,
    1.0 fully closed. Reducing 25 hand joints to one scalar happens on the
    client because that is where the joint data already lives — sending 25
    poses per hand per frame would be ~40x the bytes for information no
    current hand driver can use (`hands.py`: the fitted hand is 6-DoF at
    most, and which hand is fitted is still unresolved).
    """

    position: tuple[float, float, float]
    orientation: tuple[float, float, float, float]
    grip: float


@dataclass(frozen=True)
class TeleopFrame:
    seq: int
    client_time_ms: float
    enabled: bool
    #: Forward/back intent, -1..1. Carried in the same frame as head yaw on
    #: purpose: both are locomotion, and splitting them across two transports
    #: (buttons over MCP, yaw over this socket) would put two independent
    #: commanders on `SetVelocity`, where the last writer silently wins and
    #: neither knows about the other. One frame, one velocity, one commander.
    walk: float
    #: Whether the operator wants the arms mirrored this frame. Falling edge is
    #: what triggers the `rt/arm_sdk` weight ramp back down.
    arms: bool
    head_yaw: float
    head_position: tuple[float, float, float]
    left: HandSample | None
    right: HandSample | None

    @property
    def any_hand_tracked(self) -> bool:
        return self.left is not None or self.right is not None


def _parse_hand(
    raw: Any, side: str, head_position: tuple[float, float, float]
) -> HandSample | None:
    """Return None for an untracked hand rather than raising.

    A hand leaving the tracking volume is normal operation, not a protocol
    error: the operator dropped an arm, or reached behind themselves. The
    caller holds that arm's last commanded pose; it must not kill the session,
    which would also drop the *other* arm and the locomotion channel with it.
    """
    if raw is None:
        return None
    if not isinstance(raw, dict):
        raise FrameError(f"hands.{side}: expected an object, got {type(raw).__name__}")
    if not raw.get("tracked"):
        return None

    position = _vec3(raw.get("pos"), f"hands.{side}.pos")
    orientation = _quat(raw.get("quat"), f"hands.{side}.quat")
    grip = _finite(raw.get("grip", 0.0), f"hands.{side}.grip")
    if not (0.0 <= grip <= 1.0):
        raise FrameError(f"hands.{side}.grip: out of range [0,1] ({grip})")

    distance = math.dist(position, head_position)
    if distance > MAX_HAND_DISTANCE_M:
        # Treated as untracked, not as an error: this is the tracking-artefact
        # case above, and the arm should hold rather than the session drop.
        return None

    return HandSample(position=position, orientation=orientation, grip=grip)


def parse_frame(raw: str | bytes) -> TeleopFrame:
    """Parse and validate one wire frame. Raises `FrameError` on anything off."""
    # Measured in bytes, as the name says. `len()` on a `str` counts
    # characters, so a frame of 4096 multi-byte characters passed a 4096-BYTE
    # cap while being up to four times that on the wire.
    size = len(raw) if isinstance(raw, bytes) else len(raw.encode("utf-8", "surrogatepass"))
    if size > MAX_FRAME_BYTES:
        raise FrameError(f"frame too large ({size} > {MAX_FRAME_BYTES} bytes)")
    try:
        payload = json.loads(raw)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise FrameError(f"not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise FrameError(f"expected a JSON object, got {type(payload).__name__}")

    version = payload.get("v")
    if version != PROTOCOL_VERSION:
        # Refusing an unknown version is the point of having one. A future
        # client that changes the meaning of a field must not be interpreted
        # under today's meanings by an older bridge.
        raise FrameError(
            f"unsupported protocol version {version!r} (this bridge speaks {PROTOCOL_VERSION})"
        )

    raw_seq = payload.get("seq")
    # `int()` would accept 3.7 and quietly return 3. A client sending floats
    # for a sequence number is a client whose frames will start colliding, and
    # this is the one place that can say so.
    if isinstance(raw_seq, bool) or not isinstance(raw_seq, int):
        raise FrameError(f"seq: missing or not an integer ({raw_seq!r})")
    seq = raw_seq
    if seq < 0:
        raise FrameError(f"seq: negative ({seq})")

    head = payload.get("head")
    if not isinstance(head, dict):
        raise FrameError("head: missing")
    head_yaw = _finite(head.get("yaw"), "head.yaw")
    if abs(head_yaw) > math.pi:
        raise FrameError(f"head.yaw: outside [-pi, pi] ({head_yaw})")
    head_position = _vec3(head.get("pos"), "head.pos")

    hands = payload.get("hands") or {}
    if not isinstance(hands, dict):
        raise FrameError(f"hands: expected an object, got {type(hands).__name__}")

    return TeleopFrame(
        seq=seq,
        client_time_ms=_finite(payload.get("t", 0.0), "t"),
        # The dead-man fails closed: anything that is not literally `true`
        # reads as released. `bool()` would not do — `bool("false")` is True,
        # so a client with a stringly-typed bug, or a hand-written frame
        # carrying "false", would have held the dead-man down while saying the
        # opposite. There is no value in this field worth guessing at.
        enabled=payload.get("enabled") is True,
        walk=clamp_unit(_finite(payload.get("walk", 0.0), "walk")),
        # Same fail-closed rule as `enabled`, for the same reason.
        arms=payload.get("arms") is True,
        head_yaw=head_yaw,
        head_position=head_position,
        left=_parse_hand(hands.get("left"), "left", head_position),
        right=_parse_hand(hands.get("right"), "right", head_position),
    )
