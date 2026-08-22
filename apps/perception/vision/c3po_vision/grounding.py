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

    MEASURE IT. `DEFAULT_CAMERA_EXTRINSIC` is a reasoned default, not a
    measurement (apps/perception/README.md, decisions list). A 5 cm error in
    `z_m` is harmless; a 10
    degree error in `pitch_deg` puts a 4 m detection about 0.7 m off in range.
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median
from typing import Any, Sequence

__all__ = [
    "DEFAULT_CAMERA_EXTRINSIC",
    "MAX_VALID_DEPTH_M",
    "MIN_VALID_DEPTH_M",
    "CameraExtrinsic",
    "Grounded",
    "Intrinsics",
    "camera_to_base",
    "clamp_box",
    "deproject_pixel_to_camera",
    "ground_box",
    "inner_region",
    "median_depth_m",
    "norm180",
    "range_bearing_from_base",
    "sample_depths_m",
    "to_observation",
]

# Depth outside this band is not believed. The D435i's min-Z is ~0.2 m even in
# its most permissive preset (below it the projector's pattern does not resolve
# and the pixel comes back 0 anyway), and beyond ~10 m the stereo disparity is
# down in the noise — a "12 m" reading there is a coin flip, and a coin flip
# handed to a planner is worse than a gap.
MIN_VALID_DEPTH_M = 0.20
MAX_VALID_DEPTH_M = 10.0


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


# UNMEASURED. See the module docstring and apps/perception/README.md (decisions
# list): the nav container's
# g1_odom_tf.py holds the LiDAR mount numbers, this holds the camera's, and both
# are reasoned defaults that a human with a tape measure must replace before any
# detection is trusted for navigation. Nominal here: D435i on the G1's head,
# roughly a hand's width forward of base_link's origin, level.
DEFAULT_CAMERA_EXTRINSIC = CameraExtrinsic(
    x_m=0.10,
    y_m=0.0,
    z_m=0.55,
    pitch_deg=0.0,
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
) -> list[float]:
    """Valid depths, in METRES, over `region`, subsampled on a stride.

    `depth` is anything indexable as depth[row][col] — a numpy 2-D array, a
    list of lists, a memoryview. This module never imports numpy, so it never
    assumes one.

    `depth_scale` converts the raw units to metres (the D435i's is nominally
    0.001; read it from `sensor.get_depth_scale()`, do not assume). Pass 1.0 if
    the array is already metres.

    ZEROS ARE DROPPED, not clamped and not counted. 0 is the D435i's "I could
    not solve this pixel", and a hole in the middle of an object is common. So
    is a hole covering most of it, which is why the caller must handle an empty
    return rather than an average of nothing.
    """
    x0, y0, x1, y1 = region
    side = max(1, math.isqrt(max(1, int(max_samples))))
    sx = _stride_for(x1 - x0, side)
    sy = _stride_for(y1 - y0, side)

    out: list[float] = []
    for v in range(y0, y1, sy):
        row = depth[v]
        for u in range(x0, x1, sx):
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
) -> tuple[float | None, int]:
    """Robust depth for one 2-D box. Returns (metres | None, sample count).

    None means "this detection has no usable range". Propagate that as a
    dropped detection; do NOT substitute a default, a previous frame's value or
    the box-centre pixel. An object reported at the wrong distance is more
    dangerous than an object not reported at all, because the model will plan
    around the number it was given.
    """
    clipped = clamp_box(box, width, height)
    if clipped is None:
        return None, 0
    region = inner_region(clipped, inner_fraction)
    samples = sample_depths_m(
        depth,
        region,
        depth_scale=depth_scale,
        min_depth_m=min_depth_m,
        max_depth_m=max_depth_m,
        max_samples=max_samples,
    )
    if len(samples) < max(1, int(min_valid_samples)):
        return None, len(samples)
    return float(median(samples)), len(samples)


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
) -> Grounded | None:
    """The whole 2-D box -> egocentric pipeline, in one pure call.

    Returns None when the box has no usable depth. That is a normal outcome
    (glass, a hole, something past the stereo range) and the caller drops the
    detection — it does NOT make the scene empty, because the detector's
    heartbeat still goes out on that tick saying "online, and here is what I
    could ground".
    """
    width = intr.width or (len(depth[0]) if len(depth) else 0)
    height = intr.height or len(depth)

    z_m, n = median_depth_m(
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
    )
    if z_m is None:
        return None

    clipped = clamp_box(box, width, height)
    if clipped is None:
        return None
    x0, y0, x1, y1 = clipped
    # Deproject the box CENTRE with the box's MEDIAN depth. The centre is the
    # best single bearing estimate for the object; the median is the best
    # single range estimate for it. Mixing them is intentional — the centre
    # pixel's own depth is exactly the value we refused to trust above.
    u = (x0 + x1) / 2.0
    v = (y0 + y1) / 2.0

    p_opt = deproject_pixel_to_camera(u, v, z_m, intr)
    xb, yb, zb = camera_to_base(p_opt, extr)
    rng, bearing, elevation, slant = range_bearing_from_base(xb, yb, zb)

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
