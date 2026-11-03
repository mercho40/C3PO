"""G1 arm kinematics from the vendored URDF: forward kinematics, a damped-least-squares IK, and
the head camera's mount. Pure Python, no I/O.

Why not Pinocchio/CasADi like `xr_teleoperate`: one 7-joint chain and a 6-row task are a few
hundred floating-point operations, and this module runs inside the process that owns the stop.
A compiled solver would add a heavy dependency there for no speed we need.

Frames — everything here is in **`torso_link`**. In the URDF, both arms *and* the D435 hang off
`torso_link`. So a point the head camera sees reaches the arm through two fixed CAD transforms
and nothing else: no waist angle, and none of the vision container's `base_link` extrinsic,
which is unmeasured. `torso_link`: +x forward, +y left, +z up.

Angles are the **URDF = motor convention**. That is what `rt/lowstate` reports and what
`rt/arm_sdk` takes; Unitree's RL deploy and `xr_teleoperate` feed URDF angles straight to the
motors. In `teleop/retarget.py`'s words this is the *wiring* frame, not its semantic frame.

Known disagreement, settle on the robot before trusting a reach (test-day `manipulation.md`):
the URDF says positive `right_shoulder_roll` rotates the right arm *toward* the body, while
`retarget.JOINT_SIGNS` (measured by eye 2026-08-20) has semantic "away" = wiring +1. Both cannot
be right. The `arm_state` tool's FK while the firmware plays `wave` tells which.

What it does not model: self-collision (the workspace box in `skills/manipulation.py` stands in
for it), the hand's own geometry beyond one tool-centre offset, and arm flex under load.
"""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from pathlib import Path
from typing import Literal

Side = Literal["left", "right"]
Vec3 = tuple[float, float, float]
Mat3 = tuple[Vec3, Vec3, Vec3]

URDF_PATH = Path(__file__).parent / "assets" / "g1_29dof_rev_1_0.urdf"
ROOT_LINK = "torso_link"

#: Tool centre point: where a grasp closes, measured from `<side>_wrist_yaw_link` along its +x.
#: Unitree's own IK uses 0.05 m for the rubber hand. The BrainCo Revo2's palm centre sits further
#: out. shortcut: 0.10 m is read off the product drawing, not measured; measure palm-centre to
#: wrist-yaw axis with a ruler and set GRASP_TCP_M here.
GRASP_TCP_M = 0.10

#: Keep IK solutions this far inside the URDF's hard limits.
LIMIT_MARGIN_RAD = 0.05

#: Converged when within these.
POS_TOL_M = 0.01
DIR_TOL_RAD = math.radians(10)

#: Orientation error is weighted so 1 rad of misalignment costs like this many metres.
DIR_WEIGHT_M = 0.15

_SUFFIXES = (
    "shoulder_pitch",
    "shoulder_roll",
    "shoulder_yaw",
    "elbow",
    "wrist_roll",
    "wrist_pitch",
    "wrist_yaw",
)

# --- tiny linear algebra ---------------------------------------------------------------------


def _mm(a: Mat3, b: Mat3) -> Mat3:
    return tuple(  # type: ignore[return-value]
        tuple(sum(a[i][k] * b[k][j] for k in range(3)) for j in range(3)) for i in range(3)
    )


def _mv(a: Mat3, v: Sequence[float]) -> Vec3:
    return (
        a[0][0] * v[0] + a[0][1] * v[1] + a[0][2] * v[2],
        a[1][0] * v[0] + a[1][1] * v[1] + a[1][2] * v[2],
        a[2][0] * v[0] + a[2][1] * v[1] + a[2][2] * v[2],
    )


def _add(a: Sequence[float], b: Sequence[float]) -> Vec3:
    return (a[0] + b[0], a[1] + b[1], a[2] + b[2])


def _sub(a: Sequence[float], b: Sequence[float]) -> Vec3:
    return (a[0] - b[0], a[1] - b[1], a[2] - b[2])


def _cross(a: Sequence[float], b: Sequence[float]) -> Vec3:
    return (a[1] * b[2] - a[2] * b[1], a[2] * b[0] - a[0] * b[2], a[0] * b[1] - a[1] * b[0])


def _norm(a: Sequence[float]) -> float:
    return math.sqrt(a[0] * a[0] + a[1] * a[1] + a[2] * a[2])


def unit(a: Sequence[float]) -> Vec3:
    n = _norm(a)
    if n < 1e-9:
        raise ValueError("zero-length direction")
    return (a[0] / n, a[1] / n, a[2] / n)


def _rpy(r: float, p: float, y: float) -> Mat3:
    """URDF fixed-axis roll-pitch-yaw: R = Rz(y) Ry(p) Rx(r)."""
    cr, sr, cp, sp, cy, sy = (
        math.cos(r),
        math.sin(r),
        math.cos(p),
        math.sin(p),
        math.cos(y),
        math.sin(y),
    )
    return (
        (cy * cp, cy * sp * sr - sy * cr, cy * sp * cr + sy * sr),
        (sy * cp, sy * sp * sr + cy * cr, sy * sp * cr - cy * sr),
        (-sp, cp * sr, cp * cr),
    )


def _axis_angle(axis: Vec3, angle: float) -> Mat3:
    x, y, z = axis
    c, s = math.cos(angle), math.sin(angle)
    t = 1.0 - c
    return (
        (t * x * x + c, t * x * y - s * z, t * x * z + s * y),
        (t * x * y + s * z, t * y * y + c, t * y * z - s * x),
        (t * x * z - s * y, t * y * z + s * x, t * z * z + c),
    )


def _solve(a: list[list[float]], b: list[float]) -> list[float]:
    """Gaussian elimination with partial pivoting; `a` is small and SPD (J Jᵀ + λ²I)."""
    n = len(b)
    m = [row[:] + [b[i]] for i, row in enumerate(a)]
    for col in range(n):
        piv = max(range(col, n), key=lambda r: abs(m[r][col]))
        m[col], m[piv] = m[piv], m[col]
        for r in range(col + 1, n):
            f = m[r][col] / m[col][col]
            for c in range(col, n + 1):
                m[r][c] -= f * m[col][c]
    x = [0.0] * n
    for r in range(n - 1, -1, -1):
        x[r] = (m[r][n] - sum(m[r][c] * x[c] for c in range(r + 1, n))) / m[r][r]
    return x


# --- URDF ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Joint:
    name: str
    xyz: Vec3
    rot: Mat3
    axis: Vec3
    lower: float
    upper: float


def _floats(text: str | None, default: str) -> Vec3:
    a, b, c = (float(v) for v in (text or default).split())
    return (a, b, c)


@cache
def _joints_by_child(path: Path = URDF_PATH) -> dict[str, tuple[str, Joint]]:
    out: dict[str, tuple[str, Joint]] = {}
    for j in ET.parse(path).getroot().iter("joint"):
        origin, axis, limit = j.find("origin"), j.find("axis"), j.find("limit")
        child = j.find("child")
        parent = j.find("parent")
        assert child is not None and parent is not None
        revolute = j.get("type") == "revolute"
        out[child.get("link", "")] = (
            parent.get("link", ""),
            Joint(
                name=j.get("name", ""),
                xyz=_floats(origin.get("xyz") if origin is not None else None, "0 0 0"),
                rot=_rpy(*_floats(origin.get("rpy") if origin is not None else None, "0 0 0")),
                axis=unit(_floats(axis.get("xyz"), "1 0 0"))
                if revolute and axis is not None
                else (0.0, 0.0, 0.0),
                lower=float(limit.get("lower", 0)) if revolute and limit is not None else 0.0,
                upper=float(limit.get("upper", 0)) if revolute and limit is not None else 0.0,
            ),
        )
    return out


def _path(tip: str, root: str = ROOT_LINK) -> list[Joint]:
    joints = _joints_by_child()
    chain: list[Joint] = []
    link = tip
    while link != root:
        if link not in joints:
            raise KeyError(f"{tip} is not below {root} in the URDF")
        link, joint = joints[link]
        chain.append(joint)
    return chain[::-1]


@cache
def arm_chain(side: Side) -> tuple[Joint, ...]:
    """The 7 revolute joints torso_link -> <side>_wrist_yaw_link, in G1JointIndex order."""
    chain = tuple(_path(f"{side}_wrist_yaw_link"))
    names = tuple(j.name for j in chain)
    expected = tuple(f"{side}_{s}_joint" for s in _SUFFIXES)
    assert names == expected, f"URDF arm chain {names} != {expected}"
    return chain


def limits(side: Side) -> list[tuple[float, float]]:
    """URDF joint limits shrunk by LIMIT_MARGIN_RAD."""
    return [(j.lower + LIMIT_MARGIN_RAD, j.upper - LIMIT_MARGIN_RAD) for j in arm_chain(side)]


# --- forward kinematics ------------------------------------------------------------------------

_I3: Mat3 = ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


@dataclass(frozen=True)
class HandPose:
    position: Vec3  # tool centre point, torso_link, metres
    pointing: Vec3  # unit +x of the wrist-yaw link: the way the fingers point
    joint_origins: tuple[Vec3, ...]
    joint_axes: tuple[Vec3, ...]


def forward(side: Side, q: Sequence[float], tcp_m: float = GRASP_TCP_M) -> HandPose:
    """Hand pose in torso_link for 7 arm angles (URDF convention, radians)."""
    if len(q) != 7:
        raise ValueError("forward() takes 7 arm joint angles")
    pos: Vec3 = (0.0, 0.0, 0.0)
    rot = _I3
    origins: list[Vec3] = []
    axes: list[Vec3] = []
    for joint, angle in zip(arm_chain(side), q, strict=True):
        pos = _add(pos, _mv(rot, joint.xyz))
        rot = _mm(rot, joint.rot)
        origins.append(pos)
        axes.append(_mv(rot, joint.axis))
        rot = _mm(rot, _axis_angle(joint.axis, float(angle)))
    pointing = _mv(rot, (1.0, 0.0, 0.0))
    return HandPose(
        position=_add(pos, (pointing[0] * tcp_m, pointing[1] * tcp_m, pointing[2] * tcp_m)),
        pointing=pointing,
        joint_origins=tuple(origins),
        joint_axes=tuple(axes),
    )


# --- inverse kinematics ------------------------------------------------------------------------


@dataclass(frozen=True)
class IKResult:
    q: tuple[float, ...]
    reached: bool
    position_error_m: float
    direction_error_rad: float
    iterations: int

    def to_dict(self) -> dict[str, object]:
        return {
            "reached": self.reached,
            "position_error_m": round(self.position_error_m, 4),
            "direction_error_deg": round(math.degrees(self.direction_error_rad), 1),
            "q_rad": [round(v, 4) for v in self.q],
        }


def _clip(q: Sequence[float], lim: Sequence[tuple[float, float]]) -> list[float]:
    return [min(max(v, lo), hi) for v, (lo, hi) in zip(q, lim, strict=True)]


def _solve_from(
    side: Side,
    target: Vec3,
    pointing: Vec3 | None,
    seed: Sequence[float],
    lim: Sequence[tuple[float, float]],
    tcp_m: float,
    max_iter: int,
) -> IKResult:
    q = _clip(seed, lim)
    damping2 = 0.03**2
    pos_err = dir_err = math.inf
    for it in range(1, max_iter + 1):
        hp = forward(side, q, tcp_m)
        e_pos = _sub(target, hp.position)
        pos_err = _norm(e_pos)
        rows: list[list[float]] = [
            [
                _cross(a, _sub(hp.position, o))[k]
                for a, o in zip(hp.joint_axes, hp.joint_origins, strict=True)
            ]
            for k in range(3)
        ]
        err = list(e_pos)
        if pointing is not None:
            # Rotation that takes the hand's pointing axis onto the requested one. Roll about
            # that axis is left free, so the task is rank 5 and DLS spends the spare joints.
            w = _cross(hp.pointing, pointing)
            dir_err = math.atan2(
                _norm(w), sum(a * b for a, b in zip(hp.pointing, pointing, strict=True))
            )
            rows += [[DIR_WEIGHT_M * a[k] for a in hp.joint_axes] for k in range(3)]
            err += [DIR_WEIGHT_M * v for v in w]
        else:
            dir_err = 0.0
        if pos_err < POS_TOL_M and dir_err < DIR_TOL_RAD:
            return IKResult(tuple(q), True, pos_err, dir_err, it)
        n = len(rows)
        jjt = [
            [
                sum(rows[i][k] * rows[j][k] for k in range(7)) + (damping2 if i == j else 0.0)
                for j in range(n)
            ]
            for i in range(n)
        ]
        y = _solve(jjt, err)
        dq = [sum(rows[i][k] * y[i] for i in range(n)) for k in range(7)]
        step = max(abs(v) for v in dq)
        if step > 0.2:
            dq = [v * 0.2 / step for v in dq]
        q = _clip([a + b for a, b in zip(q, dq, strict=True)], lim)
    return IKResult(tuple(q), False, pos_err, dir_err, max_iter)


def solve(
    side: Side,
    target: Sequence[float],
    pointing: Sequence[float] | None = None,
    seed: Sequence[float] | None = None,
    tcp_m: float = GRASP_TCP_M,
    joint_limits: Sequence[tuple[float, float]] | None = None,
    max_iter: int = 150,
) -> IKResult:
    """Joint angles that put the tool centre at `target` (torso_link, m).

    `pointing`, if given, is the direction the fingers should point (unit vector, torso_link);
    roll about it is free. `seed` is where to start — pass the measured arm pose so the answer
    is the nearby one, not a mirror-image elbow. Never raises for an unreachable target:
    returns the closest pose found with `reached=False`, and the caller must not send that.
    """
    lim = list(joint_limits) if joint_limits is not None else limits(side)
    goal = (float(target[0]), float(target[1]), float(target[2]))
    want = unit(pointing) if pointing is not None else None
    seeds = [list(seed)] if seed is not None else []
    # Elbow bent, forearm forward: the URDF zero pose. Then a reach-forward pose.
    seeds += [[0.0] * 7, [-0.8, 0.0, 0.0, 0.6, 0.0, 0.0, 0.0]]
    best: IKResult | None = None
    for s in seeds:
        r = _solve_from(side, goal, want, s, lim, tcp_m, max_iter)
        if r.reached:
            return r
        if best is None or r.position_error_m < best.position_error_m:
            best = r
    assert best is not None
    return best


# --- the head camera -----------------------------------------------------------------------------


@cache
def _camera_mount() -> tuple[Vec3, Mat3]:
    (joint,) = _path("d435_link")
    return joint.xyz, joint.rot


def camera_to_torso(p_optical: Sequence[float]) -> Vec3:
    """D435 colour-optical point (x right, y down, z forward, metres) -> torso_link.

    Uses the URDF's CAD mount, which tilts the camera ~47.6° nose-down.
    """
    x, y, z = (float(v) for v in p_optical)
    link = (z, -x, -y)  # REP-103 optical -> body: forward = z, left = -x, up = -y
    xyz, rot = _camera_mount()
    return _add(xyz, _mv(rot, link))
