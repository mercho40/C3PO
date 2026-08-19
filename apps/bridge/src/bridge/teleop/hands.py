"""Grip scalar -> hand command topic. The hands are two BrainCo Revo2.

The operator's finger closure arrives as a single number per hand, 0.0 open to
1.0 closed (`protocol.HandSample.grip`). Turning that into a wire message used
to be blocked on an argument the documentation could not close — Dex3-1 or
BrainCo? — and that argument is now **settled by inspection (2026-08-19)**:
somebody looked at the robot. **Two BrainCo hands are fitted**, and one of them
was physically *unplugged* during the earlier DDS probe. That single fact
explains the whole ambiguity: the lone right-hand answer and the silent
`rt/lf/dex3/*` were a missing cable and an absent product, not evidence.
`docs/ROBOT-HARDWARE.md` carries the full story, including the lesson about
reading negative results.

What that settles, and what it does not:

| | BrainCo Revo2 (fitted) | Dex3-1 (not on this robot) |
| --- | --- | --- |
| Topic | `rt/brainco/{side}/cmd` | `rt/dex3/{side}/cmd` |
| Type | `unitree_go MotorCmds_` | `unitree_hg HandCmd_` |
| Motors | 6, `[Thumb, Thumb_aux, Index, Middle, Ring, Pinky]` | 7 |
| **Units** | **[0,1], scaled x1000 on the wire** | radians |
| Firmware deadman | **none** | a timeout bit per motor |

Two consequences carry into this module.

**The units are [0,1], not radians.** The Dex3 driver below is kept for the
product, not for this robot — a Dex3 pose sent to a BrainCo topic would exceed
full scale by 70%, and the reverse sends 29 degrees where 90 was meant. It
stays because the seam costs nothing and a hand swap is a cable, but nothing
here may default to it.

**There is no firmware deadman on a BrainCo.** A Dex3's per-motor mode byte
carries a timeout bit that makes the *firmware* release a held grip after one
second, free, the same free safety `SetVelocity`'s `duration` gives locomotion.
BrainCo has no equivalent: a closed hand stays closed until something tells it
otherwise. So the bound has to be ours, and it is — `server.py` calls
`relax()` on the falling edge of the arm request and again in `_safe_stop`,
which covers the operator letting go, the frames going stale, the session
ending and the process being cancelled. **Do not add a code path that closes a
hand without a matching release; on this hardware nothing else will open it.**

⚠️ **Which end of [0,1] is open is still unknown.** BrainCo never documents it,
Inspire DFX maps 1.0 = open, and `hand_sdk` says positive torque closes. So
`TELEOP_BRAINCO_OPEN_AT` has no default. Getting it backwards turns every
"relax your hand" into "clench".

Enablement: `TELEOP_HAND_ENABLED=1` **and** `TELEOP_HAND_TYPE` in
{`brainco`, `dex3`}, plus `TELEOP_BRAINCO_OPEN_AT` in {0, 1} for BrainCo.
Anything short of that yields a `NullHandDriver` that logs and publishes
nothing — teleop still runs, the fingers just do not move.
"""

from __future__ import annotations

import os
from typing import Any, Literal

import structlog

log = structlog.get_logger(__name__)

Side = Literal["left", "right"]

# Two hands are fitted, but only the right one has ever *answered* — the left
# was found unplugged, and `brainco_hand_server` is launched by hand with an
# explicit `--serial` per hand, so a hand with no server is silent whether or
# not it is connected. Defaulting to the right alone keeps the logs honest:
# widen to "left,right" via TELEOP_HAND_SIDES once a left hand has replied.
DEFAULT_SIDES: tuple[Side, ...] = ("right",)

# --- BrainCo Revo2 -----------------------------------------------------------
# 6 entries, order [Thumb, Thumb_aux, Index, Middle, Ring, Pinky]. Positions and
# speeds normalised [0,1], multiplied by 1000 on the wire by the server.
# Unitree's page confirms the topics, the normalisation, the 6-DoF count, the
# finger order, and the recommendation to run all finger speeds at 1.0.
BRAINCO_MOTORS = 6
BRAINCO_SPEED = 1.0

# --- Dex3-1 ------------------------------------------------------------------
# 7 motors, radians. IDL order is `thumb_0, thumb_1, thumb_2, middle_0,
# middle_1, index_0, index_1` for BOTH hands — two official pages agree, and
# the widely-copied contradiction comes from sorting the left hand's URDF link
# names, which run out of numeric order (§4.4).
DEX3_MOTORS = 7

# Full-closure pose, magnitudes only, in radians. Taken from the *spec sheet*
# ranges rather than the vendor example's clamps, because the examples exceed
# the URDF on `thumb_1` on both hands, and then scaled to ~70% so a wrong sign
# shows up as a half-curl rather than as a finger driven into its stop.
DEX3_CLOSED_MAGNITUDE = (0.60, 0.35, 0.30, 1.10, 1.10, 1.10, 1.10)

# Which entries flip sign between hands: joints 3-6 are negative-only on the
# left and positive-only on the right, and thumb_2 likewise. Straight from the
# per-side position limits in the vendor example (§4.4).
DEX3_RIGHT_SIGNS = (1.0, 1.0, -1.0, 1.0, 1.0, 1.0, 1.0)
DEX3_LEFT_SIGNS = (1.0, 1.0, 1.0, -1.0, -1.0, -1.0, -1.0)

# `RIS_Mode_t { id:4, status:3, timeout:1 }`, confirmed verbatim by Unitree.
# status 1 = FOC (driven), timeout 1 = enable the firmware's 1 s deadman on the
# hand motors. Always set timeout, never clear it: it is free safety of exactly
# the same shape as SetVelocity's `duration`.
def dex3_mode_byte(motor_id: int, status: int = 1, timeout: int = 1) -> int:
    return (motor_id & 0x0F) | ((status & 0x07) << 4) | ((timeout & 0x01) << 7)


DEX3_KP = 1.5
DEX3_KD = 0.2


def _env_flag(name: str) -> bool:
    return os.environ.get(name, "").strip() in {"1", "true", "yes", "on"}


def _clamp01(value: float) -> float:
    return max(0.0, min(1.0, value))


class HandDriver:
    """Interface every hand type implements. `send` takes 0.0 open -> 1.0 closed."""

    name = "none"
    #: Which hands this driver actually publishes to. Empty on the base and on
    #: `NullHandDriver`, so `relax()` is a no-op there without a special case.
    sides: tuple[Side, ...] = ()

    def send(self, side: Side, grip: float) -> None:  # pragma: no cover - interface
        raise NotImplementedError

    def relax(self) -> None:
        """Open every driven hand. Called on release and on the dead-man."""
        for side in self.sides:
            self.send(side, 0.0)


class NullHandDriver(HandDriver):
    """Publishes nothing. The default, and the only safe default."""

    name = "none"

    def __init__(self, reason: str) -> None:
        self.reason = reason
        log.info("teleop.hands.disabled", reason=reason)

    def send(self, side: Side, grip: float) -> None:
        return

    def relax(self) -> None:
        return


class BrainCoHandDriver(HandDriver):
    """`rt/brainco/{side}/cmd`, `unitree_go MotorCmds_`, 6 entries in [0,1]."""

    name = "brainco"

    def __init__(self, open_at: float, sides: tuple[Side, ...] = DEFAULT_SIDES) -> None:
        if open_at not in (0.0, 1.0):
            raise ValueError("BrainCo open_at must be exactly 0.0 or 1.0")
        self.open_at = open_at
        self.sides = sides
        self._publishers: dict[Side, Any] = {}

    def _publisher(self, side: Side) -> Any:
        if side not in self._publishers:
            from unitree_sdk2py.core.channel import ChannelPublisher
            from unitree_sdk2py.idl.unitree_go.msg.dds_ import MotorCmds_

            topic = f"rt/brainco/{side}/cmd"
            pub = ChannelPublisher(topic, MotorCmds_)
            pub.Init()
            self._publishers[side] = pub
            log.info("teleop.hands.publisher.ready", hand="brainco", topic=topic)
        return self._publishers[side]

    def send(self, side: Side, grip: float) -> None:
        if side not in self.sides:
            return
        from unitree_sdk2py.idl.default import unitree_go_msg_dds__MotorCmd_
        from unitree_sdk2py.idl.unitree_go.msg.dds_ import MotorCmds_

        # `open_at` is the wire value meaning "open", so closure runs from it
        # toward the other end. Written this way rather than as an `if` so the
        # unknown polarity is one number, not two code paths.
        position = self.open_at + (1.0 - 2.0 * self.open_at) * _clamp01(grip)

        cmds = []
        for _ in range(BRAINCO_MOTORS):
            cmd = unitree_go_msg_dds__MotorCmd_()
            cmd.q = float(position)
            cmd.dq = float(BRAINCO_SPEED)
            cmds.append(cmd)
        # kp/kd/tau exist in the message and the server does not read them.
        self._publisher(side).Write(MotorCmds_(cmds=cmds))


class Dex3HandDriver(HandDriver):
    """`rt/dex3/{side}/cmd`, `unitree_hg HandCmd_`, 7 motors in radians."""

    name = "dex3"

    def __init__(self, sides: tuple[Side, ...] = DEFAULT_SIDES) -> None:
        self.sides = sides
        self._publishers: dict[Side, Any] = {}

    def _publisher(self, side: Side) -> Any:
        if side not in self._publishers:
            from unitree_sdk2py.core.channel import ChannelPublisher
            from unitree_sdk2py.idl.unitree_hg.msg.dds_ import HandCmd_

            topic = f"rt/dex3/{side}/cmd"
            pub = ChannelPublisher(topic, HandCmd_)
            pub.Init()
            self._publishers[side] = pub
            log.info("teleop.hands.publisher.ready", hand="dex3", topic=topic)
        return self._publishers[side]

    def target_pose(self, side: Side, grip: float) -> tuple[float, ...]:
        """Joint angles in radians for a given closure. Pure — tested directly."""
        signs = DEX3_RIGHT_SIGNS if side == "right" else DEX3_LEFT_SIGNS
        fraction = _clamp01(grip)
        return tuple(m * s * fraction for m, s in zip(DEX3_CLOSED_MAGNITUDE, signs, strict=True))

    def send(self, side: Side, grip: float) -> None:
        if side not in self.sides:
            return
        from unitree_sdk2py.idl.default import unitree_hg_msg_dds__HandCmd_

        cmd = unitree_hg_msg_dds__HandCmd_()
        for i, q in enumerate(self.target_pose(side, grip)):
            motor = cmd.motor_cmd[i]
            motor.mode = dex3_mode_byte(i)
            motor.q = float(q)
            motor.dq = 0.0
            motor.tau = 0.0
            motor.kp = DEX3_KP
            motor.kd = DEX3_KD
        self._publisher(side).Write(cmd)


def build_driver() -> HandDriver:
    """Construct the configured hand driver, or a `NullHandDriver` explaining why not.

    Never raises. A misconfigured hand must not stop the arms and the
    locomotion channel from working — the fingers are the least important
    third of this feature and the least verified.
    """
    if not _env_flag("TELEOP_HAND_ENABLED"):
        return NullHandDriver("TELEOP_HAND_ENABLED is not set")

    hand_type = os.environ.get("TELEOP_HAND_TYPE", "").strip().lower()
    sides_raw = os.environ.get("TELEOP_HAND_SIDES", ",".join(DEFAULT_SIDES))
    sides = tuple(s.strip() for s in sides_raw.split(",") if s.strip() in ("left", "right"))
    if not sides:
        return NullHandDriver(f"TELEOP_HAND_SIDES={sides_raw!r} names no valid side")

    if hand_type == "brainco":
        raw = os.environ.get("TELEOP_BRAINCO_OPEN_AT", "").strip()
        if raw not in ("0", "1"):
            return NullHandDriver(
                "TELEOP_BRAINCO_OPEN_AT must be 0 or 1 — BrainCo never documents which end of "
                "[0,1] is an open hand, and guessing inverts every grip command"
            )
        return BrainCoHandDriver(open_at=float(raw), sides=sides)  # type: ignore[arg-type]

    if hand_type == "dex3":
        return Dex3HandDriver(sides=sides)  # type: ignore[arg-type]

    return NullHandDriver(
        f"TELEOP_HAND_TYPE={hand_type!r} is not one of 'brainco' or 'dex3'. This robot has "
        "two BrainCo Revo2 hands (settled by inspection 2026-08-19), so 'brainco' is the "
        "answer here — but it stays explicit, because the two types disagree on units and "
        "a silent default is how that mistake would ship."
    )
