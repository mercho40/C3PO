"""Grip scalar -> hand command topic. Four incompatible hands, one unknown.

The operator's finger closure arrives as a single number per hand, 0.0 open to
1.0 closed (`protocol.HandSample.grip`). Turning that into a wire message is
the part nobody can do safely yet, because **which hands are physically fitted
to this robot is unresolved** — `docs/ROBOT-PERIPHERALS.md` §4 lays out the
full argument and it does not conclude:

* A `brainco_hand_server` was found running, holding `/dev/ttyUSB1`, having
  identified **one BrainCo Revo2, medium RIGHT** hand (6 DoF, five fingers).
  No left hand answered — but it was launched with an explicit `--serial` for
  one port, so that silence says as much about the launch as the hardware.
* Yet `g1pilot` ships `g1_29dof_dx3.urdf` for *this* robot and
  `xr_teleoperate` carries `g1_body29_hand14.urdf` (2 x 7 DoF), both of which
  describe a **Dex3-1** pair (7 DoF, three fingers).

The two are not interchangeable in any respect that matters:

| | Dex3-1 | BrainCo Revo2 |
| --- | --- | --- |
| Topic | `rt/dex3/{side}/cmd` | `rt/brainco/{side}/cmd` |
| Type | `unitree_hg HandCmd_` | `unitree_go MotorCmds_` |
| Motors | 7 | 6 |
| **Units** | **radians** | **[0,1], scaled x1000 on the wire** |

A command written for one and sent to the other is not merely ignored. Send
Dex3 radians (1.7) to BrainCo and you exceed its full-scale by 70%; send
BrainCo's 0.5 to a Dex3 and you get 29 degrees where 90 was meant. That is why
this module has **no default hand type** and refuses rather than guesses.

And one thing is unknown even for the hand we have seen: **BrainCo never
documents which end of [0,1] is open.** Inspire DFX maps 1.0 = open, `hand_sdk`
says positive torque closes, BrainCo says nothing. So `TELEOP_BRAINCO_OPEN_AT`
has no default either. Getting it backwards means every "relax your hand"
becomes "clench".

Settle it before enabling anything here
---------------------------------------
`scripts/hand_probe.py` subscribes passively to all the candidate state topics
for a few seconds and writes nothing at all. One message decides the whole
argument. That is the intended first step, and it cannot hurt the robot.

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

# Only a right hand has ever answered on this robot. Defaulting to both would
# publish to a topic with no subscriber on the left, which is harmless — but it
# would also make the logs claim two hands are being driven when one is.
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
        f"TELEOP_HAND_TYPE={hand_type!r} is not one of 'brainco' or 'dex3'. Which hands are "
        "fitted to this robot is unresolved — run scripts/hand_probe.py, which subscribes "
        "passively and writes nothing, before choosing."
    )
