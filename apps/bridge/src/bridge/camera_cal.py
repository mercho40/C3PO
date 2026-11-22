"""Fit the head camera's mount to tape measurements. Pure: no I/O, no DDS.

One sample is a detection's colour-optical point (`cam_xyz` in the perception report) and the
operator's tape measurement of the same point (the middle of the object's near face: cam_xyz is
a depth reading of the surface the camera sees), in the **tape frame**: origin on the
floor midway between the two ankle joints, +x where the feet point, +y the robot's left, +z up.
That point goes into torso_link (`tape_to_torso`), and `fit` finds the torso_link mount that maps
the camera points onto it: pitch and z always, x too when the samples span enough range, y and yaw
held. The result is printed as the C3PO_CAMERA_* five (pelvis frame, kinematics.CAMERA_ENV),
which both the vision container and the bridge read.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from bridge import kinematics as k

Vec3 = k.Vec3

#: ankle_roll_link origin down to the sole: the URDF's foot contact spheres sit at z -0.03 with
#: radius 0.005.
SOLE_M = 0.035

#: x is fitted only when the samples' camera ranges span this much. Points at one range cannot
#: tell a pitch error from a forward offset.
FIT_X_MIN_SPREAD_M = 0.30

#: G1JointIndex 0-14, the order rt/lowstate reports them: the legs and the waist.
LEG_WAIST_JOINTS = tuple(
    f"{s}_{j}_joint"
    for s in ("left", "right")
    for j in ("hip_pitch", "hip_roll", "hip_yaw", "knee", "ankle_pitch", "ankle_roll")
) + ("waist_yaw_joint", "waist_roll_joint", "waist_pitch_joint")


def _dot(a: Sequence[float], b: Sequence[float]) -> float:
    return sum(u * v for u, v in zip(a, b, strict=True))


def _col(m: k.Mat3, i: int) -> Vec3:
    return (m[0][i], m[1][i], m[2][i])


def _tmv(m: k.Mat3, v: Sequence[float]) -> Vec3:
    return (_dot(_col(m, 0), v), _dot(_col(m, 1), v), _dot(_col(m, 2), v))


def tape_frame(q: Mapping[str, float]) -> tuple[Vec3, k.Mat3]:
    """The tape frame's origin and axes (columns) in torso_link, from the leg and waist angles.
    Assumes both feet flat on a level floor."""
    soles, zs, xs = [], [], []
    for side in ("left", "right"):
        p, r = k.link_pose(f"{side}_ankle_roll_link", "pelvis", q)
        soles.append(k._add(p, k._mv(r, (0.0, 0.0, -SOLE_M))))
        xs.append(_col(r, 0))
        zs.append(_col(r, 2))
    z = k.unit(k._add(zs[0], zs[1]))
    fx = k._add(xs[0], xs[1])
    x = k.unit(k._sub(fx, tuple(_dot(fx, z) * c for c in z)))
    y = k._cross(z, x)
    origin = tuple((a + b) / 2 for a, b in zip(soles[0], soles[1], strict=True))
    pos_t, rot_t = k.link_pose(k.ROOT_LINK, "pelvis", q)
    axes = tuple(_tmv(rot_t, c) for c in (x, y, z))
    rot: k.Mat3 = tuple(zip(*axes))  # type: ignore[assignment]
    return _tmv(rot_t, k._sub(origin, pos_t)), rot


def torso_in_tape(q: Mapping[str, float]) -> Vec3:
    """torso_link's origin in the tape frame: z is the torso's height above the floor."""
    o, rot = tape_frame(q)
    return _tmv(rot, (-o[0], -o[1], -o[2]))


def tape_to_torso(
    tape: Sequence[float],
    *,
    q: Mapping[str, float] | None = None,
    torso_height_m: float | None = None,
) -> Vec3:
    """A taped point (ahead, left, height above the floor) -> torso_link.

    With `q` (the 15 leg + waist angles, radians) through the URDF. Without it, `torso_height_m`
    stands in: torso upright and straight above the tape origin (a few cm off when it leans).
    """
    if q is not None:
        o, rot = tape_frame(q)
        return k._add(o, k._mv(rot, tape))
    if torso_height_m is None:
        raise ValueError("tape_to_torso needs q or torso_height_m")
    return (float(tape[0]), float(tape[1]), float(tape[2]) - torso_height_m)


@dataclass(frozen=True)
class Fit:
    xyz: Vec3  # camera mount in torso_link
    pitch_deg: float
    yaw_deg: float
    fitted: tuple[str, ...]  # which of x, z, pitch were solved for
    residuals_m: tuple[Vec3, ...]  # measured - predicted, torso_link, per sample

    def env(self) -> tuple[float, float, float, float, float]:
        """The C3PO_CAMERA_* five (pelvis frame), kinematics.CAMERA_ENV order."""
        rot = k._rpy(0.0, math.radians(self.pitch_deg), math.radians(self.yaw_deg))
        return k.mount_in_pelvis(self.xyz, rot)


def predict(cam: Sequence[float], xyz: Sequence[float], pitch_deg: float, yaw_deg: float) -> Vec3:
    """camera_to_torso for a mount given as torso_link xyz + pitch/yaw (degrees)."""
    rot = k._rpy(0.0, math.radians(pitch_deg), math.radians(yaw_deg))
    return k._add(xyz, k._mv(rot, (cam[2], -cam[0], -cam[1])))


def residuals(
    samples: Sequence[tuple[Sequence[float], Sequence[float]]],
    xyz: Sequence[float],
    pitch_deg: float,
    yaw_deg: float,
) -> tuple[Vec3, ...]:
    return tuple(k._sub(m, predict(c, xyz, pitch_deg, yaw_deg)) for c, m in samples)


def current_mount() -> tuple[Vec3, float, float]:
    """The mount camera_to_torso uses now (URDF, or CAMERA_ENV): torso_link xyz, pitch, yaw."""
    xyz, rot = k._camera_mount()
    return (xyz, *k.pitch_yaw_deg(rot))


def fit(
    samples: Sequence[tuple[Sequence[float], Sequence[float]]],
    *,
    start: tuple[Vec3, float, float] | None = None,
    fit_x: bool | None = None,
) -> Fit:
    """Least-squares mount for (cam_xyz, torso_link point) pairs, by Gauss-Newton.

    Solves pitch and z; also x when `fit_x` (default: 2+ samples whose camera ranges span
    FIT_X_MIN_SPREAD_M). y and yaw stay at `start` (default: the current mount).
    """
    if not samples:
        raise ValueError("fit needs at least one sample")
    xyz, pitch, yaw = start or current_mount()
    if fit_x is None:
        ranges = [math.dist((0, 0, 0), c) for c, _ in samples]
        fit_x = len(samples) >= 2 and max(ranges) - min(ranges) >= FIT_X_MIN_SPREAD_M
    x, y, z, p = xyz[0], xyz[1], xyz[2], math.radians(pitch)
    rz = k._rpy(0.0, 0.0, math.radians(yaw))
    for _iteration in range(50):
        cols: list[list[float]] = [[] for _ in range(3 if fit_x else 2)]
        res: list[float] = []
        for c, m in samples:
            r = predict(c, (x, y, z), math.degrees(p), yaw)
            link = (c[2], -c[0], -c[1])
            # d/dp of Ry(p) link
            d = (
                -link[0] * math.sin(p) + link[2] * math.cos(p),
                0.0,
                -link[0] * math.cos(p) - link[2] * math.sin(p),
            )
            dp = k._mv(rz, d)
            for i in range(3):
                res.append(m[i] - r[i])
                cols[0].append(dp[i])
                cols[1].append(1.0 if i == 2 else 0.0)
                if fit_x:
                    cols[2].append(1.0 if i == 0 else 0.0)
        jtj = [[_dot(a, b) for b in cols] for a in cols]
        step = k._solve(jtj, [_dot(a, res) for a in cols])
        p, z = p + step[0], z + step[1]
        if fit_x:
            x += step[2]
        if max(abs(s) for s in step) < 1e-10:
            break
    pitch_deg = math.degrees(p)
    return Fit(
        xyz=(x, y, z),
        pitch_deg=pitch_deg,
        yaw_deg=yaw,
        fitted=("pitch", "z") + (("x",) if fit_x else ()),
        residuals_m=residuals(samples, (x, y, z), pitch_deg, yaw),
    )
