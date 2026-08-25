"""Operator wrist pose -> G1 arm joint angles. Pure geometry, no I/O.

Why this exists rather than an IK solver
----------------------------------------
`xr_teleoperate` — the stack our colleague already has driving this robot's
arms from a Quest (`docs/ROBOT-HARDWARE.md`) — solves full inverse
kinematics: casadi + pinocchio over `g1_body29_hand14.urdf`, retargeting the
wrist *pose* to 14 joint angles. That is the better answer when you have the
URDF. **We do not have it**: it lives on the Jetson inside their tree, and
nothing in this repo carries G1 link lengths or joint axes.

Guessing link lengths to fake an IK solve would produce a solver that is
confidently wrong — it converges, it reports success, and it puts the elbow
somewhere else. So this module does something weaker and honest instead: a
**direction-and-extension mapping**. It uses only quantities that survive not
knowing the robot's dimensions —

  * the *direction* from the operator's shoulder to their wrist  -> shoulder angles
  * the *fraction of their own reach* they have extended         -> elbow angle
  * the hand's rotation relative to their forearm                -> wrist angles

— all of which are scale-free. The operator's arm length is measured once at
calibration (`calibrate_arm_length`), so a 1.55 m and a 1.95 m operator both
map "fully extended" to "robot fully extended". The robot's own link lengths
never enter the calculation, because no target *position* is ever claimed.

What you lose: the robot's hand does not end up where the operator's hand is
in metres. What you keep: it points where they point, extends when they
extend, and rotates as they rotate — which is what "the robot copies my arms"
means to the person wearing the headset, and it is achievable today.

SIGN AND AXIS CONVENTIONS — SHOULDERS AND ELBOW NOW MEASURED
------------------------------------------------------------
Settled on the robot 2026-08-20 for the **right** arm's shoulder and elbow:
`shoulder_pitch` is **-1** (the original assumption was backwards),
`shoulder_roll`, `shoulder_yaw` and `elbow` are **+1**. See `JOINT_SIGNS`.

Still unverified: the three **wrist** joints, and the whole **left** arm.
Wrist mapping is off by default (`include_wrist=False`), so those cost nothing
today; the left column is inferred from bilateral symmetry, which is sound for
the mirroring but says nothing about absolute polarity.

`docs/ROBOT-API.md` gives the joint *order* (15-21 left, 22-28 right:
shoulder pitch/roll/yaw, elbow, wrist roll/pitch/yaw) and that is solid. It
does **not** give the positive direction of any of them, and no source we have
does. Every sign below is therefore a stated assumption, collected in
`JOINT_SIGNS` so there is exactly one place to correct them, and the whole arm
path ships disabled (`arm_sdk.py`). `scripts/arm_sign_check.py` walks one
joint at a time by a few degrees so an operator standing next to the robot can
settle each sign in about two minutes. Do that before trusting any of this.

Frames
------
Input is WebXR: metres, Y-up, right-handed, **-Z forward**, quaternions
`[x, y, z, w]`. Internally everything is first rotated into the operator's own
*yaw frame* (undoing head yaw about Y), so turning on the spot does not sweep
the arms — head yaw is a locomotion input in this system, not an arm input.
In that frame the axes read: **+x right, +y up, -z forward**, arm-hanging-down
is the direction `(0, -1, 0)`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from bridge.teleop.protocol import HandSample

Side = Literal["left", "right"]

# --- Operator body model -----------------------------------------------------
# Where the shoulder sits relative to the headset, in the operator's yaw frame.
# The headset reports the *eyes*; the shoulder is below and lateral. These are
# population averages, and being a couple of centimetres off costs almost
# nothing: the mapping consumes the shoulder->wrist *direction*, and a 3 cm
# error in a ~60 cm vector is under 3 degrees of angle.
SHOULDER_HALF_WIDTH_M = 0.18  # lateral, +x for the right arm
SHOULDER_DROP_M = 0.22  # below the headset

# Fallback operator arm length (shoulder to wrist) if calibration never ran.
# Deliberately on the long side: over-estimating reach makes the robot under-
# extend, which is the safe direction to be wrong in.
DEFAULT_ARM_LENGTH_M = 0.62
MIN_ARM_LENGTH_M = 0.35
MAX_ARM_LENGTH_M = 0.90

# Below this, shoulder->wrist direction is numerically meaningless (the
# operator's hand is essentially at their own shoulder), so the previous
# direction is held instead of being recomputed from noise.
MIN_DIRECTION_M = 0.08


@dataclass(frozen=True)
class ArmAngles:
    """Seven joint angles for one arm, radians, in G1 joint order.

    Order matches `G1JointIndex` 15-21 / 22-28 exactly, so `as_tuple()` can be
    written straight into `motor_cmd[base + i]` without a lookup table.
    """

    shoulder_pitch: float
    shoulder_roll: float
    shoulder_yaw: float
    elbow: float
    wrist_roll: float
    wrist_pitch: float
    wrist_yaw: float

    def as_tuple(self) -> tuple[float, float, float, float, float, float, float]:
        return (
            self.shoulder_pitch,
            self.shoulder_roll,
            self.shoulder_yaw,
            self.elbow,
            self.wrist_roll,
            self.wrist_pitch,
            self.wrist_yaw,
        )


#: The pose the arms rest in. All zeros = arms hanging straight down, which is
#: also the pose `arm_sdk` blends *from* on engage and back *to* on release.
NEUTRAL = ArmAngles(0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0)

#: Joint names in `G1JointIndex` order. `ArmAngles.as_tuple()`, `LIMITS` and
#: `JOINT_SIGNS` are all keyed by this, and `_finish` asserts they agree — the
#: alternative is a silent off-by-one that swaps two joints' limits.
JOINT_NAMES: tuple[str, ...] = (
    "shoulder_pitch",
    "shoulder_roll",
    "shoulder_yaw",
    "elbow",
    "wrist_roll",
    "wrist_pitch",
    "wrist_yaw",
)

# --- Software joint envelope -------------------------------------------------
# NOT the hardware limits: those are in a URDF we do not have. These are a
# deliberately tight box chosen so that a sign error found on the robot
# produces a slow, small, obviously-wrong motion instead of the arm slamming
# to a mechanical stop. Widen them only after `scripts/arm_sign_check.py` has
# confirmed the signs, and even then only up to measured URDF values.
LIMITS: dict[str, tuple[float, float]] = {
    "shoulder_pitch": (math.radians(-90), math.radians(90)),
    "shoulder_roll": (math.radians(-10), math.radians(90)),
    "shoulder_yaw": (math.radians(-45), math.radians(45)),
    "elbow": (math.radians(0), math.radians(110)),
    "wrist_roll": (math.radians(-45), math.radians(45)),
    "wrist_pitch": (math.radians(-30), math.radians(30)),
    "wrist_yaw": (math.radians(-30), math.radians(30)),
}

# Per-joint direction, applied **after** clamping. Two different things are
# being kept apart here, and conflating them is how sign bugs hide:
#
#   * `LIMITS` is a *semantic* envelope, in the mapping's own convention where
#     positive means the same physical thing on both arms (roll positive =
#     abduction, away from the body). Those numbers are meaningful to a human
#     and are identical left and right.
#   * `JOINT_SIGNS` is *wiring* — how this robot happens to have numbered that
#     physical direction. Roll and yaw are mirrored between arms because their
#     axes are shared body-frame axes, so one physical motion has opposite
#     signs on the two sides. That mirroring follows from bilateral symmetry
#     rather than from any vendor claim; the absolute polarity of every row,
#     including which side gets the minus, does not, and is unverified.
#
# MEASURED ON THE ROBOT, 2026-08-20, by scripts/arm_sign_check.py at FSM 4:
#
#     shoulder_pitch  -1.0   <- the assumption was WRONG
#     shoulder_roll   +1.0
#     shoulder_yaw    +1.0
#     elbow           +1.0
#     wrist_*                 still unverified — see below
#
# `shoulder_pitch` is the one that matters most: it is the joint the arm mirror
# uses hardest, and at +1 every "reach forward" would have swung the arm
# BACKWARD. From inside a headset that reads as the whole feature being broken,
# with nothing to point at.
#
# The left column is still inferred, not measured. Roll and yaw are mirrored
# because their axes are shared body-frame axes, which follows from bilateral
# symmetry; pitch and elbow are copied across because they do not depend on it.
# Run `arm_sign_check.py --side left` to replace inference with measurement.
JOINT_SIGNS: dict[Side, dict[str, float]] = {
    "right": {
        "shoulder_pitch": -1.0,
        "shoulder_roll": 1.0,
        "shoulder_yaw": 1.0,
        "elbow": 1.0,
        "wrist_roll": 1.0,
        "wrist_pitch": 1.0,
        "wrist_yaw": 1.0,
    },
    "left": {
        "shoulder_pitch": -1.0,
        "shoulder_roll": -1.0,
        "shoulder_yaw": -1.0,
        "elbow": 1.0,
        "wrist_roll": -1.0,
        "wrist_pitch": 1.0,
        "wrist_yaw": -1.0,
    },
}


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _rotate_y(v: tuple[float, float, float], angle: float) -> tuple[float, float, float]:
    """Rotate a vector about +Y (the WebXR up axis) by `angle` radians."""
    c, s = math.cos(angle), math.sin(angle)
    x, y, z = v
    return (c * x + s * z, y, -s * x + c * z)


def quat_to_matrix(
    q: tuple[float, float, float, float],
) -> tuple[tuple[float, float, float], tuple[float, float, float], tuple[float, float, float]]:
    """`[x, y, z, w]` -> a 3x3 rotation matrix, as rows.

    Note the input order. WebXR's `DOMPointReadOnly` exposes `.x .y .z .w`, and
    the whole robotics half of this codebase writes quaternions `[w, x, y, z]`
    (see `state._yaw_from_quaternion`). Converting in exactly one place is why
    this function exists rather than being inlined.
    """
    x, y, z, w = q
    xx, yy, zz = x * x, y * y, z * z
    xy, xz, yz = x * y, x * z, y * z
    wx, wy, wz = w * x, w * y, w * z
    return (
        (1 - 2 * (yy + zz), 2 * (xy - wz), 2 * (xz + wy)),
        (2 * (xy + wz), 1 - 2 * (xx + zz), 2 * (yz - wx)),
        (2 * (xz - wy), 2 * (yz + wx), 1 - 2 * (xx + yy)),
    )


def shoulder_origin(
    head_position: tuple[float, float, float], head_yaw: float, side: Side
) -> tuple[float, float, float]:
    """Estimate a shoulder position from the headset pose."""
    lateral = SHOULDER_HALF_WIDTH_M if side == "right" else -SHOULDER_HALF_WIDTH_M
    offset = _rotate_y((lateral, -SHOULDER_DROP_M, 0.0), head_yaw)
    return (
        head_position[0] + offset[0],
        head_position[1] + offset[1],
        head_position[2] + offset[2],
    )


def calibrate_arm_length(
    head_position: tuple[float, float, float],
    head_yaw: float,
    hand: HandSample,
    side: Side,
) -> float:
    """Measure the operator's shoulder-to-wrist reach from one extended pose.

    Called once, with the operator's arm held straight out. Clamped to a human
    range so a bad sample (hand tracked at the wrong depth for one frame)
    cannot scale every subsequent frame's elbow angle.
    """
    origin = shoulder_origin(head_position, head_yaw, side)
    return clamp(math.dist(hand.position, origin), MIN_ARM_LENGTH_M, MAX_ARM_LENGTH_M)


def _shoulder_pitch(forward: float, down: float) -> float:
    """Sagittal shoulder angle: 0 is the arm hanging, +pi/2 is straight ahead.

    `atan2(forward, down)` alone is correct everywhere the joint can actually
    go, and catastrophic just outside it.

    The joint's limit is +/-90 degrees, so the arm cannot reach overhead at
    all. But `atan2` does not know that: it keeps going, and the hand raised
    straight up sits exactly on its branch cut. One degree of wobble there —
    the hand a hair in front of vertical versus a hair behind — swings the
    result from +179 to -179 degrees. Clamping to the joint limit turns that
    into a jump from +90 to -90, and the driver's rate limiter, doing its job,
    faithfully sweeps the arm through the full 180 degrees at 0.6 rad/s. Five
    seconds of a humanoid arm travelling from straight ahead to straight back,
    because the operator's raised hand shook.

    Above shoulder height the arm is out of reach whatever we do, so the only
    question is which unreachable answer to give. This gives the forward limit,
    because that is the continuous one: a hand raised the way hands are raised
    — up the front — passes through forward-horizontal at exactly +90 and stays
    there as it rises. No jump anywhere along that path.

    The discontinuity does not vanish; it cannot, for a joint that spans half
    the circle. It moves to "behind the shoulder AND above it", which is a
    place a human arm essentially cannot go without the shoulder rolling out
    first — and where the old code was already producing nonsense.
    """
    if down <= 0.0:
        # At or above shoulder height. `forward >= 0` covers the overhead case
        # (forward == 0) on purpose: the arm is going up, not backwards.
        return math.pi / 2 if forward >= 0.0 else -math.pi / 2
    return math.atan2(forward, down)


def _elbow_angle(reach_m: float, arm_length_m: float) -> float:
    """Elbow flexion from how far the operator has extended, radians.

    Two equal links of length `L = arm_length / 2` put the wrist at
    `|v| = 2L cos(e/2)` where `e` is flexion from straight, so
    `e = 2 acos(|v| / arm_length)`. Exact, and needs no robot dimensions —
    the *fraction* of reach is what carries over, not the metres.
    """
    ratio = clamp(reach_m / arm_length_m, 0.0, 1.0)
    return 2.0 * math.acos(ratio)


def _wrist_angles(
    direction: tuple[float, float, float],
    orientation: tuple[float, float, float, float],
) -> tuple[float, float, float]:
    """Hand rotation expressed relative to the forearm, as roll/pitch/yaw.

    Builds a frame whose +Z is along the forearm (shoulder -> wrist), then
    reads the hand's own axes in it. Degenerate when the forearm points
    straight up or down, because the reference used to complete the basis is
    world up — and note that "straight down" is the *resting* pose, so this
    branch is taken often rather than rarely. Falling back to zero there is
    both the numerically safe answer and the right one: an arm hanging at rest
    should have a neutral wrist, not whatever an ill-conditioned cross product
    produces.
    """
    fz = direction
    world_up = (0.0, 1.0, 0.0)
    # fx = up x forearm, normalised. Near-parallel means no usable basis.
    fx = (
        world_up[1] * fz[2] - world_up[2] * fz[1],
        world_up[2] * fz[0] - world_up[0] * fz[2],
        world_up[0] * fz[1] - world_up[1] * fz[0],
    )
    fx_norm = math.sqrt(fx[0] ** 2 + fx[1] ** 2 + fx[2] ** 2)
    if fx_norm < 1e-3:
        return (0.0, 0.0, 0.0)
    fx = (fx[0] / fx_norm, fx[1] / fx_norm, fx[2] / fx_norm)
    fy = (
        fz[1] * fx[2] - fz[2] * fx[1],
        fz[2] * fx[0] - fz[0] * fx[2],
        fz[0] * fx[1] - fz[1] * fx[0],
    )

    m = quat_to_matrix(orientation)
    # The hand's local +Z and +X axes are the matrix columns.
    hand_z = (m[0][2], m[1][2], m[2][2])
    hand_x = (m[0][0], m[1][0], m[2][0])

    def dot(a: tuple[float, float, float], b: tuple[float, float, float]) -> float:
        return a[0] * b[0] + a[1] * b[1] + a[2] * b[2]

    # Pitch/yaw: where the hand points relative to straight-along-the-forearm.
    pitch = math.atan2(dot(hand_z, fy), dot(hand_z, fz))
    yaw = math.atan2(dot(hand_z, fx), dot(hand_z, fz))
    # Roll: rotation of the hand about the forearm axis.
    roll = math.atan2(dot(hand_x, fy), dot(hand_x, fx))
    return (roll, pitch, yaw)


def retarget_arm(
    side: Side,
    head_position: tuple[float, float, float],
    head_yaw: float,
    hand: HandSample,
    arm_length_m: float = DEFAULT_ARM_LENGTH_M,
    include_wrist: bool = False,
) -> ArmAngles:
    """Map one tracked hand to seven joint angles, clamped to `LIMITS`.

    `include_wrist` is off by default. The three wrist joints are the least
    verifiable part of this mapping — they depend on the WebXR hand-joint
    orientation convention *and* on three unverified robot joint signs at once
    — and a wrong wrist adds nothing an operator would miss while adding three
    more ways to bend a joint the wrong way. Turn it on after the sign check.
    """
    origin = shoulder_origin(head_position, head_yaw, side)
    v_world = (
        hand.position[0] - origin[0],
        hand.position[1] - origin[1],
        hand.position[2] - origin[2],
    )
    # Undo head yaw: arms should follow the operator's *body*, and head yaw is
    # already spoken for as the locomotion input.
    v = _rotate_y(v_world, -head_yaw)
    reach = math.sqrt(v[0] ** 2 + v[1] ** 2 + v[2] ** 2)

    if reach < MIN_DIRECTION_M:
        # Hand at the shoulder: direction is noise. Fold the arm and keep the
        # shoulder where a folded arm belongs rather than amplifying jitter.
        return _finish(
            side, ArmAngles(0.0, 0.0, 0.0, _elbow_angle(reach, arm_length_m), 0.0, 0.0, 0.0)
        )

    d = (v[0] / reach, v[1] / reach, v[2] / reach)

    # Roll first (abduction away from the body), then pitch in the remaining
    # sagittal plane. Ill-conditioned only when the arm is straight out to the
    # side, where pitch genuinely stops being distinguishable from yaw.
    lateral = d[0] if side == "right" else -d[0]
    shoulder_roll = math.asin(clamp(lateral, -1.0, 1.0))
    shoulder_pitch = _shoulder_pitch(forward=-d[2], down=-d[1])

    elbow = _elbow_angle(reach, arm_length_m)

    wrist_roll = wrist_pitch = wrist_yaw = 0.0
    if include_wrist:
        wrist_roll, wrist_pitch, wrist_yaw = _wrist_angles(d, hand.orientation)

    return _finish(
        side,
        ArmAngles(
            shoulder_pitch=shoulder_pitch,
            shoulder_roll=shoulder_roll,
            # Held at zero on purpose: shoulder yaw is the redundant degree of
            # freedom (the "swivel" of the elbow about the shoulder-wrist
            # line), and wrist position alone does not determine it. Zero is
            # the natural elbow-down posture. Driving it from hand rotation
            # instead would fight the wrist joints for the same measurement.
            shoulder_yaw=0.0,
            elbow=elbow,
            wrist_roll=wrist_roll,
            wrist_pitch=wrist_pitch,
            wrist_yaw=wrist_yaw,
        ),
    )


def _finish(side: Side, angles: ArmAngles) -> ArmAngles:
    """Clamp in the semantic convention, then convert to robot joint signs.

    Order matters. Clamping *after* the sign flip would apply an envelope
    written as "abduct up to 90 degrees, adduct at most 10" to the mirrored
    arm as "adduct up to 90, abduct at most 10" — a limit table that silently
    means the opposite thing on the left side. Clamp where the numbers carry
    meaning, then multiply by the wiring.
    """
    signs = JOINT_SIGNS[side]
    values: dict[str, float] = {}
    for name, raw in zip(JOINT_NAMES, angles.as_tuple(), strict=True):
        low, high = LIMITS[name]
        values[name] = clamp(raw, low, high) * signs[name]
    return ArmAngles(**values)
