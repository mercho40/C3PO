"""2D box + aligned depth -> egocentric range/bearing. Pure functions, nothing else.

THIS MODULE IMPORTS NOTHING THAT NEEDS A ROBOT. No ROS, no DDS, no
pyrealsense2, no CUDA, no TensorRT, not even numpy — only the stdlib. That is
deliberate and it is the point: the maths that decides *where the robot thinks
things are* is the part most likely to carry a silent sign error, so it must be
exercisable by `pytest` on a Mac with no hardware attached. Everything that
touches a device lives in detector.py and calls into here.

FRAMES, IN ORDER, BECAUSE EVERY ONE OF THEM IS A CHANCE TO FLIP A SIGN
--------------------------------------------------------------------
1. Pixel + depth. (u, v) in the COLOR image, with the depth frame already
   aligned to color (rs.align(rs.stream.color)), so one (u, v) indexes both.
   Depth is a raw integer in `depth_scale` units; 0 means INVALID, never
   "zero metres" — the D435i reports 0 for every pixel it could not solve
   (no texture, out of range, occluded, inside the min-Z blind zone).

2. Camera optical frame (REP-145): +x RIGHT, +y DOWN, +z FORWARD (out of the
   lens). This is what the pinhole intrinsics deproject into. The y-down is
   the trap: it is upside down relative to every robot frame in this repo.

3. base_link (REP-103): +x FORWARD, +y LEFT, +z UP. Right-handed. Same frame
   Nav2, the costmaps and the world model all speak.

4. D7's egocentric pair: `range_m` and `bearing_deg`.

D7 BEARING CONVENTION — READ THIS BEFORE TOUCHING A MINUS SIGN
--------------------------------------------------------------
    bearing_deg is DEGREES, 0 = STRAIGHT AHEAD, POSITIVE = LEFT (CCW),
    range (-180, 180].

It is `math.degrees(atan2(y_base, x_base))` and nothing else, because in
base_link +y already IS left. There is no negation anywhere in this file's
bearing path, and there must never be one: this is the same sign convention as
`turn`'s `delta_yaw_radians`, as `geometry_msgs/Twist.angular.z`, and as
`world_model.Observation.bearing_deg`. A flip here is not a bug in a detector,
it is a bug baked into the LLM's interface — the model would be told "chair on
your left", would turn left, and would walk into it. So:

    object on the robot's LEFT   -> bearing_deg > 0   -> u < cx  (LEFT half of the image)
    object on the robot's RIGHT  -> bearing_deg < 0   -> u > cx  (RIGHT half of the image)

The image-column relation is the one worth memorising, because it is the one
you can check by eye from a single frame: something in the left half of the
picture must come out with a POSITIVE bearing. `test_grounding.py` asserts
exactly that, and if you ever find yourself "fixing" that test, stop.

`range_m` is the HORIZONTAL (ground-plane) distance sqrt(x^2 + y^2), not the
3-D slant distance. Two reasons: it is what "how far away is it" means when
you are deciding whether to walk there, and it is the same quantity the
LaserScan-derived `free_space` sectors report, so the model is never comparing
two different definitions of distance in one snapshot. The slant distance and
the elevation are still returned, separately and explicitly named.

DEPTH: MEDIAN OVER AN INNER REGION, ZEROS DROPPED
-------------------------------------------------
The depth at the box CENTRE pixel is the obvious thing to use and it is wrong
often enough to matter: on a person it can land between an arm and the
background, on a chair it lands in a gap in the backrest, and on anything
shiny it lands on a hole. So we take the median over the middle of the box
(default: the central 50% in each dimension, i.e. a quarter of its area),
which stays on the object even when the box is loose, and which the box edges
— where the background bleeds in — cannot reach.

Median, not mean: one background pixel at 8 m mixed into a 1 m object drags a
mean by metres and a median not at all.

Zeros are dropped BEFORE the median, not clamped, not treated as near. If too
few valid samples survive, the function returns None and the caller must drop
the detection rather than invent a range. "I saw a thing but cannot say where"
is not an observation the world model has a slot for, and guessing would put a
confident wrong number in front of the model — the exact failure D7 exists to
prevent.

The inner region is SUBSAMPLED on a stride so the cost is bounded (see
`max_samples`). This module has no numpy on purpose, and a full 200x200 inner
box would be 40,000 Python-level lookups per detection per frame. A median
over ~1000 stratified samples is statistically indistinguishable here and is
~40x cheaper.

THE EXTRINSIC IS A CONSTANT, AND THAT IS THE WHOLE OF D2.2 OPTION 1
-------------------------------------------------------------------
The camera is bolted to the robot. base_link <- camera_link never changes at
runtime, so resolving it here — in the container that owns the camera — means
no TF in the vision container, no TF in the bridge, and no ROS in either. The
nav container's `g1_odom_tf.py` carries the same mount note; the number below
must agree with the physical robot, and NOTHING will tell you if it does not.

    VERIFY IT. `DEFAULT_CAMERA_EXTRINSIC` is the URDF's `d435_joint` (CAD),
    not a tape measurement, and it is the same mount the bridge's arm IK uses
    (`bridge.kinematics.camera_to_torso`; test_grounding.py pins the two
    together). A 5 cm error in `z_m` is harmless; a 10 degree error in
    `pitch_deg` puts a 4 m detection about 0.7 m off in range. If the robot
    disagrees, the measured numbers go in `C3PO_CAMERA_*`
    (~/.c3po/perception.env), not here.
"""
from __future__ import annotations

import math
import random
from dataclasses import dataclass
from statistics import median, quantiles
from typing import Any, Sequence

__all__ = [
    "DEFAULT_CAMERA_EXTRINSIC",
    "MAX_VALID_DEPTH_M",
    "MIN_VALID_DEPTH_M",
    "CameraExtrinsic",
    "Grounded",
    "Intrinsics",
    "Support",
    "base_to_camera",
    "camera_to_base",
    "clamp_box",
    "deproject_pixel_to_camera",
    "fit_plane",
    "fit_support",
    "ground_box",
    "inner_region",
    "median_depth_m",
    "norm180",
    "object_depths_m",
    "range_bearing_from_base",
    "ray_bearing_deg",
    "ray_plane_depth_m",
    "sample_depths_m",
    "support_points",
    "to_observation",
]

# Depth outside this band is not believed. The D435i's min-Z is ~0.2 m even in
# its most permissive preset (below it the projector's pattern does not resolve
# and the pixel comes back 0 anyway), and beyond ~10 m the stereo disparity is
# down in the noise — a "12 m" reading there is a coin flip, and a coin flip
# handed to a planner is worse than a gap.
MIN_VALID_DEPTH_M = 0.20
MAX_VALID_DEPTH_M = 10.0
# near_m's percentile: the object's near face, not its middle, and still past a stray near pixel.
NEAR_PERCENTILE = 10


@dataclass(frozen=True)
class Intrinsics:
    """Pinhole intrinsics of the COLOR stream (depth is aligned to it).

    Taken at runtime from
    `profile.get_stream(rs.stream.color).as_video_stream_profile().intrinsics`,
    never hardcoded: they differ per unit, per resolution and per firmware. The
    D435i's color stream is Brown-Conrady, and we ignore the distortion
    coefficients — at the box scales we deproject (whole-object centroids, not
    features) the residual is well under the depth noise.
    """

    fx: float
    fy: float
    cx: float
    cy: float
    width: int = 0
    height: int = 0


@dataclass(frozen=True)
class CameraExtrinsic:
    """base_link <- camera_link, as a fixed mount. Angles are REP-103.

    base_link is the URDF root `pelvis` (what the nav container's base_link
    stands for); camera_link is the URDF `d435_link`.

    x_m/y_m/z_m: where the camera sits IN base_link. +x forward, +y left,
    +z up. So z_m is "how far above the robot's base frame the lens is".

    pitch_deg: rotation about base_link's +y (LEFT) axis. By the right-hand
    rule that means POSITIVE PITCH = CAMERA NOSE DOWN, which is both the
    REP-103 sign and the intuitive one for a camera aimed at the floor ahead.
    (Sanity check baked into `camera_to_base`: with pitch > 0, a point straight
    down the optical axis comes out with z_base < 0 — below the mount.)

    yaw_deg: rotation about +z (UP), POSITIVE = LEFT (CCW), same as every other
    angle in this repo.

    Roll is deliberately not modelled. A roll about the optical axis would tilt
    the horizon in the image, and if the camera is mounted rolled, the fix is a
    bracket, not a constant here.
    """

    x_m: float
    y_m: float
    z_m: float
    pitch_deg: float = 0.0
    yaw_deg: float = 0.0


# CAD, not a tape measure: pelvis -> d435_link in
# apps/bridge/src/bridge/assets/g1_29dof_rev_1_0.urdf, waist joints at zero:
#   pelvis -> waist_yaw_joint(0) -> waist_roll_joint xyz(-0.0039635, 0, 0.044)
#   -> waist_pitch_joint(0) -> torso_link
#   -> d435_joint xyz(0.0576235, 0.01753, 0.42987) rpy(0, 0.8307767, 0) = 47.6 deg nose-down.
# C3PO_CAMERA_* still override it (detector.camera_extrinsic_from_env).
# shortcut: the D435 rides torso_link above the three waist joints, so this is exact only at
# waist (0,0,0): waist yaw adds straight onto every bearing, waist pitch adds onto the pitch and
# moves the lens ~4 cm per 0.1 rad, and gait pitch/roll of the pelvis is not modelled. Upgrade:
# feed waist_q from rt/lowstate if the robot shows bearing drift with the waist off zero.
DEFAULT_CAMERA_EXTRINSIC = CameraExtrinsic(
    x_m=0.05366,
    y_m=0.01753,
    z_m=0.47387,
    pitch_deg=47.6,  # the URDF's rpy pitch 0.8307767239493009 rad is radians(47.6) exactly
    yaw_deg=0.0,
)


@dataclass(frozen=True)
class Grounded:
    """Where one detection is, in base_link and in D7's egocentric pair."""

    # D7's pair. bearing_deg: 0 ahead, POSITIVE LEFT (CCW).
    range_m: float          # HORIZONTAL distance, sqrt(x^2 + y^2)
    bearing_deg: float
    # The rest, so a caller can filter (e.g. drop things above head height)
    # without re-deriving anything.
    x_m: float
    y_m: float
    z_m: float
    elevation_deg: float    # positive = above base_link's xy plane
    slant_m: float          # 3-D distance, always >= range_m
    depth_m: float          # the median optical-axis depth this came from
    depth_samples: int      # how many valid pixels the median was taken over
    # "direct": the median of the object's own pixels. "plane": the sensor read
    # THROUGH the object (glass) or not at all, so depth came from the support
    # surface under it — see `ground_box`. Published, so the planner knows.
    depth_source: str = "direct"
    # HORIZONTAL range of the object's NEAR face (NEAR_PERCENTILE of its depth samples); range_m is
    # its middle. None unless depth_source is "direct" — the plane fallback has no face to measure.
    near_m: float | None = None
    # The deprojected point in the colour-optical frame (x right, y down, z forward), before any
    # extrinsic. The bridge's arm IK maps it through the URDF's CAD mount instead of ours.
    p_opt: tuple[float, float, float] | None = None


def norm180(deg: float) -> float:
    """Wrap to (-180, 180]. Same helper, same convention as the nav container."""
    return (deg + 180.0) % 360.0 - 180.0


def clamp_box(
    box: Sequence[float], width: int, height: int
) -> tuple[int, int, int, int] | None:
    """Clip a (x0, y0, x1, y1) box to the image; None if nothing is left.

    A detector run on a letterboxed input routinely emits boxes that hang off
    the edge by a few pixels. Indexing with those is either an IndexError or,
    worse in Python, a silent wrap to the far side of the image.
    """
    x0, y0, x1, y1 = (float(v) for v in box)
    if x1 < x0:
        x0, x1 = x1, x0
    if y1 < y0:
        y0, y1 = y1, y0
    ix0 = max(0, min(math.floor(x0), width))
    iy0 = max(0, min(math.floor(y0), height))
    ix1 = max(0, min(math.ceil(x1), width))
    iy1 = max(0, min(math.ceil(y1), height))
    if ix1 <= ix0 or iy1 <= iy0:
        return None
    return ix0, iy0, ix1, iy1


def inner_region(
    box: tuple[int, int, int, int], fraction: float = 0.5
) -> tuple[int, int, int, int]:
    """The central `fraction` of the box in each dimension, min 1 px each way.

    fraction=0.5 keeps the middle 50% of the width and of the height — a
    quarter of the area — which is far enough inside that background bleeding
    in at the box edge cannot reach the sample set.
    """
    x0, y0, x1, y1 = box
    f = max(0.05, min(1.0, float(fraction)))
    w = x1 - x0
    h = y1 - y0
    dx = round(w * (1.0 - f) / 2.0)
    dy = round(h * (1.0 - f) / 2.0)
    ix0, ix1 = x0 + dx, x1 - dx
    iy0, iy1 = y0 + dy, y1 - dy
    if ix1 <= ix0:
        ix0, ix1 = x0, min(x0 + 1, x1)
    if iy1 <= iy0:
        iy0, iy1 = y0, min(y0 + 1, y1)
    return ix0, iy0, ix1, iy1


def _stride_for(span: int, target: int) -> int:
    """Stride that keeps at most ~`target` samples along one axis."""
    if span <= target or target <= 0:
        return 1
    return math.ceil(span / float(target))


def sample_depths_m(
    depth: Any,
    region: tuple[int, int, int, int],
    *,
    depth_scale: float = 0.001,
    min_depth_m: float = MIN_VALID_DEPTH_M,
    max_depth_m: float = MAX_VALID_DEPTH_M,
    max_samples: int = 1024,
    mask: Any = None,
    mask_origin: tuple[int, int] = (0, 0),
) -> list[float]:
    """Valid depths, in METRES, over `region`, subsampled on a stride.

    `depth` is anything indexable as depth[row][col] — a numpy 2-D array, a
    list of lists, a memoryview. This module never imports numpy, so it never
    assumes one.

    `depth_scale` converts the raw units to metres (the D435i's is nominally
    0.001; read it from `sensor.get_depth_scale()`, do not assume). Pass 1.0 if
    the array is already metres.

    `mask`, if given, is indexable as mask[v - oy][u - ox] with (ox, oy) =
    `mask_origin`; pixels where it is falsy are skipped. That is how an
    instance mask keeps the background between a chair's legs out of its depth.

    ZEROS ARE DROPPED, not clamped and not counted. 0 is the D435i's "I could
    not solve this pixel", and a hole in the middle of an object is common. So
    is a hole covering most of it, which is why the caller must handle an empty
    return rather than an average of nothing.
    """
    x0, y0, x1, y1 = region
    ox, oy = mask_origin
    side = max(1, math.isqrt(max(1, int(max_samples))))
    sx = _stride_for(x1 - x0, side)
    sy = _stride_for(y1 - y0, side)

    out: list[float] = []
    for v in range(y0, y1, sy):
        row = depth[v]
        mrow = None if mask is None else mask[v - oy]
        for u in range(x0, x1, sx):
            if mrow is not None and not mrow[u - ox]:
                continue
            raw = row[u]
            if not raw:                     # 0 == INVALID, never "at the lens"
                continue
            z = float(raw) * depth_scale
            if z < min_depth_m or z > max_depth_m:
                continue
            out.append(z)
    return out


def median_depth_m(
    depth: Any,
    box: Sequence[float],
    *,
    width: int,
    height: int,
    depth_scale: float = 0.001,
    inner_fraction: float = 0.5,
    min_valid_samples: int = 12,
    min_depth_m: float = MIN_VALID_DEPTH_M,
    max_depth_m: float = MAX_VALID_DEPTH_M,
    max_samples: int = 1024,
    mask: Any = None,
) -> tuple[float | None, int]:
    """Robust depth for one 2-D box. Returns (metres | None, sample count).

    None means "this detection has no usable range". Propagate that as a
    dropped detection; do NOT substitute a default, a previous frame's value or
    the box-centre pixel. An object reported at the wrong distance is more
    dangerous than an object not reported at all, because the model will plan
    around the number it was given.

    With an instance `mask` (indexed relative to the CLIPPED box's top-left)
    the median is over the WHOLE box's masked pixels — the mask does the inner
    region's job, better. A mask with too few valid pixels under it (tiny, or
    all holes) falls back to the inner region rather than to None.
    """
    samples = object_depths_m(
        depth,
        box,
        width=width,
        height=height,
        depth_scale=depth_scale,
        inner_fraction=inner_fraction,
        min_valid_samples=min_valid_samples,
        min_depth_m=min_depth_m,
        max_depth_m=max_depth_m,
        max_samples=max_samples,
        mask=mask,
    )
    if len(samples) < max(1, int(min_valid_samples)):
        return None, len(samples)
    return float(median(samples)), len(samples)


def object_depths_m(
    depth: Any,
    box: Sequence[float],
    *,
    width: int,
    height: int,
    depth_scale: float = 0.001,
    inner_fraction: float = 0.5,
    min_valid_samples: int = 12,
    min_depth_m: float = MIN_VALID_DEPTH_M,
    max_depth_m: float = MAX_VALID_DEPTH_M,
    max_samples: int = 1024,
    mask: Any = None,
) -> list[float]:
    """The valid (non-zero, in-band) depths `median_depth_m` takes its median over.

    Fewer than `min_valid_samples` means "no usable range" — the caller checks.
    """
    clipped = clamp_box(box, width, height)
    if clipped is None:
        return []
    if mask is not None:
        masked = sample_depths_m(
            depth, clipped, depth_scale=depth_scale, min_depth_m=min_depth_m,
            max_depth_m=max_depth_m, max_samples=max_samples,
            mask=mask, mask_origin=(clipped[0], clipped[1]),
        )
        if len(masked) >= max(1, int(min_valid_samples)):
            return masked
    return sample_depths_m(
        depth,
        inner_region(clipped, inner_fraction),
        depth_scale=depth_scale,
        min_depth_m=min_depth_m,
        max_depth_m=max_depth_m,
        max_samples=max_samples,
    )


def deproject_pixel_to_camera(
    u: float, v: float, z_m: float, intr: Intrinsics
) -> tuple[float, float, float]:
    """Pinhole deprojection into the CAMERA OPTICAL frame (+x right, +y DOWN, +z fwd).

    z_m is depth along the optical axis (what the depth image stores), not the
    slant range to the pixel — which is why this is a multiply and not a
    normalisation.

    Distortion is ignored on purpose; see Intrinsics.
    """
    x = (float(u) - intr.cx) * z_m / intr.fx
    y = (float(v) - intr.cy) * z_m / intr.fy
    return x, y, float(z_m)


def camera_to_base(
    point_optical: tuple[float, float, float],
    extr: CameraExtrinsic = DEFAULT_CAMERA_EXTRINSIC,
) -> tuple[float, float, float]:
    """Camera optical frame -> base_link. The fixed mount, applied as a constant.

    Two steps, kept separate so each is readable:

    1. Axis relabel, optical (x right, y DOWN, z forward) -> robot-style
       (x forward, y LEFT, z up):

           x_r =  z_o        forward is the optical axis
           y_r = -x_o        LEFT is minus RIGHT
           z_r = -y_o        UP   is minus DOWN

       Those two minus signs are the ONLY negations in the whole camera->base
       path, and they are axis relabels, not corrections. If you are adding a
       third minus sign somewhere to make an output look right, the bug is
       upstream of here.

    2. Mount rotation Rz(yaw) * Ry(pitch), then the mount translation.
       Positive pitch is nose-down, so a point straight down the optical axis
       (x_r = d, y_r = z_r = 0) comes out at z = -d*sin(pitch) + z_m: below the
       lens, as it should be.
    """
    xo, yo, zo = point_optical
    # 1. axis relabel
    xr, yr, zr = zo, -xo, -yo

    # 2. mount orientation
    p = math.radians(extr.pitch_deg)
    cp, sp = math.cos(p), math.sin(p)
    # Ry(p): x' = x cos p + z sin p ; z' = -x sin p + z cos p
    xp = xr * cp + zr * sp
    yp = yr
    zp = -xr * sp + zr * cp

    a = math.radians(extr.yaw_deg)
    ca, sa = math.cos(a), math.sin(a)
    # Rz(a): x' = x cos a - y sin a ; y' = x sin a + y cos a
    xb = xp * ca - yp * sa
    yb = xp * sa + yp * ca
    zb = zp

    return xb + extr.x_m, yb + extr.y_m, zb + extr.z_m


def base_to_camera(
    point_base: tuple[float, float, float],
    extr: CameraExtrinsic = DEFAULT_CAMERA_EXTRINSIC,
) -> tuple[float, float, float]:
    """base_link -> camera optical frame: `camera_to_base` undone, step by step."""
    x, y, z = point_base[0] - extr.x_m, point_base[1] - extr.y_m, point_base[2] - extr.z_m
    a = math.radians(extr.yaw_deg)
    xp, yp = x * math.cos(a) + y * math.sin(a), -x * math.sin(a) + y * math.cos(a)
    p = math.radians(extr.pitch_deg)
    xr, zr = xp * math.cos(p) - z * math.sin(p), xp * math.sin(p) + z * math.cos(p)
    return -yp, -zr, xr


def range_bearing_from_base(
    x_m: float, y_m: float, z_m: float
) -> tuple[float, float, float, float]:
    """base_link xyz -> (range_m, bearing_deg, elevation_deg, slant_m).

    bearing_deg = degrees(atan2(y, x)). NO NEGATION: in base_link +y is already
    LEFT, and D7 wants positive = left. See the module docstring.
    """
    horiz = math.hypot(x_m, y_m)
    bearing = norm180(math.degrees(math.atan2(y_m, x_m)))
    slant = math.sqrt(horiz * horiz + z_m * z_m)
    elevation = math.degrees(math.atan2(z_m, horiz)) if horiz > 0.0 else (
        90.0 if z_m > 0 else -90.0 if z_m < 0 else 0.0
    )
    return horiz, bearing, elevation, slant


def ray_bearing_deg(u: float, v: float, intr: Intrinsics, extr: CameraExtrinsic) -> float:
    """Bearing of the pixel's RAY, mount rotation only — for a box with no depth.

    Exact for an object far away compared with the mount offset (x_m, y_m);
    a few degrees off up close. Used only where the alternative is no bearing.
    """
    ray = deproject_pixel_to_camera(u, v, 1.0, intr)
    x, y, _z = camera_to_base(ray, CameraExtrinsic(0.0, 0.0, 0.0, extr.pitch_deg, extr.yaw_deg))
    return norm180(math.degrees(math.atan2(y, x)))


# --- the support plane: depth for what the sensor cannot see -----------------
#
# Active-IR stereo reads THROUGH glass (the pattern lands on the wall behind)
# and returns holes on shiny or dark things. The table the object stands on is
# usually fine. So: fit a plane to the valid depth in a strip just BELOW the
# box, cast the ray through the box's bottom-centre pixel, and intersect — that
# is the contact point (docs/autonomy/research/02-perception.md §3.2.1).


def support_points(
    depth: Any,
    box: tuple[int, int, int, int],
    intr: Intrinsics,
    *,
    depth_scale: float = 0.001,
    min_depth_m: float = MIN_VALID_DEPTH_M,
    max_depth_m: float = MAX_VALID_DEPTH_M,
    strip_fraction: float = 0.25,
    max_points: int = 192,
) -> list[tuple[float, float, float]]:
    """Valid depth in the strip under a CLIPPED box, deprojected (camera optical)."""
    x0, _y0, x1, y1 = box
    height = intr.height or len(depth)
    strip = max(4, round((y1 - box[1]) * strip_fraction))
    ys0, ys1 = y1, min(height, y1 + strip)
    if ys1 <= ys0:
        return []
    side = max(1, math.isqrt(max(1, max_points)))
    sx, sy = _stride_for(x1 - x0, side), _stride_for(ys1 - ys0, side)
    out: list[tuple[float, float, float]] = []
    for v in range(ys0, ys1, sy):
        row = depth[v]
        for u in range(x0, x1, sx):
            raw = row[u]
            if not raw:
                continue
            z = float(raw) * depth_scale
            if min_depth_m <= z <= max_depth_m:
                out.append(deproject_pixel_to_camera(u, v, z, intr))
    return out


def fit_plane(
    points: Sequence[tuple[float, float, float]],
    *,
    iterations: int = 32,
    tol_m: float = 0.015,
    min_inlier_fraction: float = 0.5,
    seed: int = 0,
    max_tilt_deg: float | None = None,
) -> tuple[tuple[float, float, float], float] | None:
    """RANSAC plane n.p = d (|n| = 1) or None. Seeded, so a frame replays exactly.

    `max_tilt_deg` skips every hypothesis whose normal is further than that from
    the points' own +z — only meaningful for points in a z-up frame (base_link).

    shortcut: the best 3-point hypothesis, no least-squares refit — the 1.5 cm
    inlier band is already below the D435i's noise at a metre; refit if the
    test set says plane depths are biased.
    """
    if len(points) < 12:
        return None
    rng = random.Random(seed)
    min_nz = -1.0 if max_tilt_deg is None else math.cos(math.radians(max_tilt_deg))
    best: tuple[tuple[float, float, float], float] | None = None
    best_count = 0
    for _ in range(iterations):
        p1, p2, p3 = rng.sample(points, 3)
        ax, ay, az = p2[0] - p1[0], p2[1] - p1[1], p2[2] - p1[2]
        bx, by, bz = p3[0] - p1[0], p3[1] - p1[1], p3[2] - p1[2]
        nx, ny, nz = ay * bz - az * by, az * bx - ax * bz, ax * by - ay * bx
        norm = math.sqrt(nx * nx + ny * ny + nz * nz)
        if norm < 1e-9 or abs(nz) < min_nz * norm:
            continue
        nx, ny, nz = nx / norm, ny / norm, nz / norm
        d = nx * p1[0] + ny * p1[1] + nz * p1[2]
        count = sum(1 for p in points if abs(nx * p[0] + ny * p[1] + nz * p[2] - d) < tol_m)
        if count > best_count:
            best, best_count = ((nx, ny, nz), d), count
    if best is None or best_count < min_inlier_fraction * len(points):
        return None
    return best


def plane_tilt_deg(normal: tuple[float, float, float], extr: CameraExtrinsic) -> float:
    """Angle between the plane and base_link's horizontal. 0 = a table or floor."""
    nx, ny, nz = camera_to_base(normal, CameraExtrinsic(0.0, 0.0, 0.0, extr.pitch_deg, extr.yaw_deg))
    return math.degrees(math.acos(min(1.0, abs(nz) / max(1e-9, math.sqrt(nx * nx + ny * ny + nz * nz)))))


def ray_plane_depth_m(
    u: float, v: float, intr: Intrinsics, plane: tuple[tuple[float, float, float], float]
) -> float | None:
    """Optical-axis depth where pixel (u, v)'s ray meets the plane; None if never."""
    (nx, ny, nz), d = plane
    rx, ry = (float(u) - intr.cx) / intr.fx, (float(v) - intr.cy) / intr.fy
    denom = nx * rx + ny * ry + nz
    if abs(denom) < 1e-6:
        return None
    t = d / denom
    return t if t > 0 else None


def ground_box(
    box: Sequence[float],
    depth: Any,
    intr: Intrinsics,
    *,
    extr: CameraExtrinsic = DEFAULT_CAMERA_EXTRINSIC,
    depth_scale: float = 0.001,
    inner_fraction: float = 0.5,
    min_valid_samples: int = 12,
    min_depth_m: float = MIN_VALID_DEPTH_M,
    max_depth_m: float = MAX_VALID_DEPTH_M,
    max_samples: int = 1024,
    mask: Any = None,
    behind_m: float | None = 0.30,
    max_plane_tilt_deg: float = 45.0,
) -> Grounded | None:
    """The whole 2-D box -> egocentric pipeline, in one pure call.

    Returns None when the box has no usable depth. That is a normal outcome
    (glass, a hole, something past the stereo range) and the caller drops the
    detection — it does NOT make the scene empty, because the detector's
    heartbeat still goes out on that tick saying "online, and here is what I
    could ground".

    THE SUPPORT-PLANE FALLBACK (`depth_source="plane"`). Tried when the
    object's own depth is missing, or reads more than `behind_m` FARTHER than
    the surface just below it — the signature of a glass on a table read
    through to the wall. It is taken only if that surface fits a plane within
    `max_plane_tilt_deg` of horizontal (a table, a floor: never the sofa back
    under a wall-mounted TV) and the contact point is NEARER than the direct
    reading. Anything else keeps the direct depth or returns None — the plane
    is a better guess than a hole, never a licence to invent one.
    `behind_m=None` turns the fallback off.
    """
    width = intr.width or (len(depth[0]) if len(depth) else 0)
    height = intr.height or len(depth)

    samples = object_depths_m(
        depth,
        box,
        width=width,
        height=height,
        depth_scale=depth_scale,
        inner_fraction=inner_fraction,
        min_valid_samples=min_valid_samples,
        min_depth_m=min_depth_m,
        max_depth_m=max_depth_m,
        max_samples=max_samples,
        mask=mask,
    )
    n = len(samples)
    z_m = float(median(samples)) if n >= max(1, int(min_valid_samples)) else None
    clipped = clamp_box(box, width, height)
    if clipped is None:
        return None
    x0, y0, x1, y1 = clipped

    source = "direct"
    if behind_m is not None:
        pts = support_points(depth, clipped, intr, depth_scale=depth_scale,
                             min_depth_m=min_depth_m, max_depth_m=max_depth_m)
        if pts and (z_m is None or z_m > median(p[2] for p in pts) + behind_m):
            plane = fit_plane(pts)
            if plane is not None and plane_tilt_deg(plane[0], extr) <= max_plane_tilt_deg:
                z_plane = ray_plane_depth_m((x0 + x1) / 2.0, float(y1), intr, plane)
                if (z_plane is not None and min_depth_m <= z_plane <= max_depth_m
                        and (z_m is None or z_plane < z_m)):
                    z_m, n, source = z_plane, len(pts), "plane"
    if z_m is None:
        return None

    # Deproject the box CENTRE with the box's MEDIAN depth. The centre is the
    # best single bearing estimate for the object; the median is the best
    # single range estimate for it. Mixing them is intentional — the centre
    # pixel's own depth is exactly the value we refused to trust above.
    u = (x0 + x1) / 2.0
    v = (y0 + y1) / 2.0

    p_opt = deproject_pixel_to_camera(u, v, z_m, intr)
    xb, yb, zb = camera_to_base(p_opt, extr)
    rng, bearing, elevation, slant = range_bearing_from_base(xb, yb, zb)
    near = None
    if source == "direct" and n >= 2:  # quantiles needs two points; min_valid_samples may be 1
        # The near face: a low percentile of the same samples, along the same centre ray.
        z_near = quantiles(samples, n=100, method="inclusive")[NEAR_PERCENTILE - 1]
        near = range_bearing_from_base(
            *camera_to_base(deproject_pixel_to_camera(u, v, z_near, intr), extr)
        )[0]

    return Grounded(
        range_m=rng,
        bearing_deg=bearing,
        x_m=xb,
        y_m=yb,
        z_m=zb,
        elevation_deg=elevation,
        slant_m=slant,
        depth_m=z_m,
        depth_samples=n,
        depth_source=source,
        near_m=near,
        p_opt=(float(p_opt[0]), float(p_opt[1]), float(p_opt[2])),
    )


# --- the surface an object stands on, for the arm ------------------------------
#
# Not the glass fallback above (that wants a DEPTH); this reports the SURFACE: how
# high its top is and where its near edge is, so the arm planner stops treating the
# object's own height and a constant as the desk (desk-edge hit, robot 2026-11-05).
SUPPORT_MAX_TILT_DEG = 12.0   # a table or a floor; a ramp or a sofa back is not one
SUPPORT_BELOW_M = 0.10        # the top may sit this far under the object's bottom pixel...
SUPPORT_ABOVE_M = 0.03        # ...or this far over it (a loose box, depth noise)
SUPPORT_MIN_INLIERS = 30
SUPPORT_MAX_RMS_M = 0.0075   # a flat top at D435 noise is ~1-5 mm; rougher is not a surface to trust


@dataclass(frozen=True)
class Support:
    """The near-horizontal plane under one detection, in base_link (the Grounded frame)."""

    z_m: float                  # the plane's HIGHEST along the bearing, from its near end to
    #                             the object's (x_m, y_m): a tilted top is higher at one end
    near_edge_m: float | None   # HORIZONTAL range, along bearing_deg, of its edge nearest the
    #                             robot (range_m's convention); None if the edge is not in view
    inliers: int
    rms_m: float
    # The same plane in the camera OPTICAL frame (ground_box's p_opt / cam_xyz convention), so a
    # consumer with its own camera mount puts it in the frame it puts the object in:
    cam_top_xyz: tuple[float, float, float]   # under the object
    cam_near_xyz: tuple[float, float, float]  # at the near end along the
    #                             bearing: the edge if near_edge_m is set, else the nearest seen


def fit_support(
    box: Sequence[float],
    depth: Any,
    g: Grounded,
    intr: Intrinsics,
    *,
    extr: CameraExtrinsic = DEFAULT_CAMERA_EXTRINSIC,
    depth_scale: float = 0.001,
    min_depth_m: float = MIN_VALID_DEPTH_M,
    max_depth_m: float = MAX_VALID_DEPTH_M,
    max_points: int = 256,
    tol_m: float = 0.015,
    seed: int = 0,
) -> Support | None:
    """The table (or floor) top under `box`, or None. Stdlib, seeded, ~max_points lookups.

    Samples a stride grid from the box's lower half down to the image bottom, half a box
    wider each side, skipping the box itself; keeps points within the height window under
    the object's bottom pixel; RANSACs a plane within SUPPORT_MAX_TILT_DEG of level and
    least-squares refits it. The near edge is the lowest plane pixel on the bearing line
    (refined to the exact row), and only if the first thing below it is a DROP under the
    plane (its front face, the floor) — a surface that runs off the image bottom, or whose
    near part is hidden by something over it (a hand, an arm), has no edge in view.
    """
    width = intr.width or (len(depth[0]) if len(depth) else 0)
    height = intr.height or len(depth)
    clipped = clamp_box(box, width, height)
    if clipped is None:
        return None
    x0, y0, x1, y1 = clipped
    w, h = x1 - x0, y1 - y0
    uc = min(width - 1, (x0 + x1) // 2)
    # The object's bottom: where the bottom-centre pixel's ray reaches the object's range.
    # Range, not optical depth: a pitched camera sees a vertical face at one range but
    # at a depth that grows down the face.
    cb, sb = math.cos(math.radians(g.bearing_deg)), math.sin(math.radians(g.bearing_deg))
    rx, ry, rz = camera_to_base(deproject_pixel_to_camera(uc, y1, 1.0, intr),
                                CameraExtrinsic(0.0, 0.0, 0.0, extr.pitch_deg, extr.yaw_deg))
    if rx * cb + ry * sb < 1e-6:
        return None  # the bottom pixel looks away from the object: nothing below to fit
    t = (g.range_m - extr.x_m * cb - extr.y_m * sb) / (rx * cb + ry * sb)
    z_bot = extr.z_m + t * rz
    lo, hi = z_bot - SUPPORT_BELOW_M, z_bot + SUPPORT_ABOVE_M

    def point(u: int, v: int) -> tuple[float, float, float] | None:
        raw = depth[v][u]
        z = float(raw) * depth_scale if raw else 0.0
        if not min_depth_m <= z <= max_depth_m:
            return None
        return camera_to_base(deproject_pixel_to_camera(u, v, z, intr), extr)

    rx0, rx1, ry0 = max(0, x0 - w // 2), min(width, x1 + w // 2), y0 + h // 2
    side = max(1, math.isqrt(max(1, max_points)))
    sx, sy = _stride_for(rx1 - rx0, side), _stride_for(height - ry0, side)
    seen: list[tuple[int, int, tuple[float, float, float]]] = []  # (u, v, p), all valid
    cand: list[tuple[float, float, float]] = []
    for v in range(ry0, height, sy):
        for u in range(rx0, rx1, sx):
            if x0 <= u < x1 and v < y1:
                continue  # the object itself
            p = point(u, v)
            if p is None:
                continue
            seen.append((u, v, p))
            if lo - tol_m <= p[2] <= hi + tol_m:
                cand.append(p)
    if len(cand) < SUPPORT_MIN_INLIERS:
        return None
    plane = fit_plane(cand, tol_m=tol_m, seed=seed, max_tilt_deg=SUPPORT_MAX_TILT_DEG)
    if plane is None:
        return None
    (nx, ny, nz), d = plane
    a, b, c = -nx / nz, -ny / nz, d / nz  # as z = a x + b y + c; nz is near 1 (level)

    def on(p: tuple[float, float, float]) -> bool:
        return abs(p[2] - a * p[0] - b * p[1] - c) < tol_m

    # Least squares on the hypothesis' inliers, then once more on the refit's own:
    # a noisy 3-point hypothesis is tilted enough to swap table points for front-face ones.
    for _ in range(2):
        inl = [p for p in cand if on(p)]
        n = len(inl)
        if n < SUPPORT_MIN_INLIERS:
            return None
        mx, my, mz = (sum(p[i] for p in inl) / n for i in range(3))
        sxx = sum((p[0] - mx) ** 2 for p in inl)
        syy = sum((p[1] - my) ** 2 for p in inl)
        sxy = sum((p[0] - mx) * (p[1] - my) for p in inl)
        sxz = sum((p[0] - mx) * (p[2] - mz) for p in inl)
        syz = sum((p[1] - my) * (p[2] - mz) for p in inl)
        det = sxx * syy - sxy * sxy
        if abs(det) < 1e-12:
            return None  # inliers on a line: no plane to speak of
        a, b = (sxz * syy - syz * sxy) / det, (syz * sxx - sxz * sxy) / det
        c = mz - a * mx - b * my
    rms = math.sqrt(sum((p[2] - a * p[0] - b * p[1] - c) ** 2 for p in inl) / n)
    z_obj = a * g.x_m + b * g.y_m + c
    if (rms > SUPPORT_MAX_RMS_M or not lo <= z_obj <= hi
            or math.degrees(math.atan(math.hypot(a, b))) > SUPPORT_MAX_TILT_DEG):
        return None

    # The edge: the lowest plane point in the sampled column whose edge lies on the bearing
    # line (image columns fan out from it), if that column sees something else below it.
    edge = None
    near = g.range_m
    lowest: dict[int, tuple[int, tuple[float, float, float]]] = {}
    for u, v, p in seen:
        if on(p) and v > lowest.get(u, (-1, p))[0]:
            lowest[u] = (v, p)
    if lowest:
        u_e = min(lowest, key=lambda u: abs(lowest[u][1][1] * cb - lowest[u][1][0] * sb))
        v_e, p_e = lowest[u_e]
        below = [(v, p) for u, v, p in seen if u == u_e and v > v_e and not on(p)]
        nxt = min(below, key=lambda vp: vp[0])[1] if below else None
        # A drop under the plane, not an occluder over it: a hand over the near part would
        # put the "edge" where the hand starts, 12-20 cm too far out.
        if nxt is not None and nxt[2] < a * nxt[0] + b * nxt[1] + c:
            for v in range(v_e + 1, min(height, v_e + sy)):  # the rows the stride skipped
                p = point(u_e, v)
                if p is not None:
                    if not on(p):
                        break
                    p_e = p
            edge = p_e[0] * cb + p_e[1] * sb
        near = min(near, p_e[0] * cb + p_e[1] * sb)
    # Linear along the bearing, so its highest from the near end to the object is at one end.
    top_obj = (g.x_m, g.y_m, z_obj)
    xn, yn = near * cb, near * sb
    top_near = (xn, yn, a * xn + b * yn + c)
    return Support(
        z_m=max(z_obj, top_near[2]), near_edge_m=edge, inliers=n, rms_m=rms,
        cam_top_xyz=base_to_camera(top_obj, extr), cam_near_xyz=base_to_camera(top_near, extr),
    )


def to_observation(
    label: str, g: Grounded, confidence: float | None = None, age_s: float = 0.0
) -> dict:
    """One wire object, shaped EXACTLY like `world_model.Observation.to_dict()`.

    label / range_m / bearing_deg / confidence / age_s, with the same rounding,
    so the bridge can build an `Observation` from it without a translation
    layer — and so a field renamed on either side shows up as a KeyError in
    tests rather than as a quietly missing confidence in front of the model.

    Rounding is part of the contract, not cosmetic: it is what keeps the
    JSON small enough that 32 objects at 4 Hz is a rounding error on a
    loopback socket, and it is at the precision the sensor can actually
    support (1 cm, 0.1 degrees).
    """
    d = {
        "label": label,
        "range_m": round(g.range_m, 2),
        "bearing_deg": round(g.bearing_deg, 1),
    }
    if confidence is not None:
        d["confidence"] = round(float(confidence), 2)
    if age_s:
        d["age_s"] = round(float(age_s), 1)
    return d
