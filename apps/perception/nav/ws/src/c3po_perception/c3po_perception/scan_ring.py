"""sensor_msgs/LaserScan -> a small ring of bearings. Pure: no ROS, no numpy.

WHAT THIS IS FOR
----------------
The headset shows the robot's camera, which is a 69-degree window on a machine
that can be walked into things from any direction. `/scan` already exists —
`pointcloud_to_laserscan` reduces the Mid-360's cloud to a LaserScan in
`base_footprint` — and it answers the question the camera cannot: what is
around the operator, including behind them.

`world_model_publisher` already consumes that scan, but only to fill four
coarse sectors (ahead / left / right / behind) for the world summary. Four
numbers is the right size for an LLM reading a scene description and far too
coarse to draw. This produces the other view of the same message: a fixed ring
of bearings, cheap enough to ship at 4 Hz through an SSH tunnel and detailed
enough to be worth looking at.

WHY MINIMUM PER BUCKET, NOT MEAN
--------------------------------
Decimating means many raw returns land in one output bearing, and the choice of
how to combine them is a safety decision rather than a signal-processing one.

A mean smears a thin obstacle away: a table leg 0.6 m ahead, averaged with the
5 m wall behind it across a 3-degree bucket, reports about 4 m of clear space
and the operator walks into the table. Minimum keeps the nearest thing in that
direction, which is the only reading that can hurt anyone. It biases toward
reporting obstacles that are smaller than they look, and that is the correct
direction to be wrong in.

WHY CENTIMETRES AS INTEGERS
---------------------------
`[1.2340000000000002, 5.678999999999999]` is what float ranges serialise to,
and at 120 bearings that is most of the payload. Centimetres as integers are
1 cm resolution — far finer than a Mid-360's actual accuracy, and finer than
anything a dot on a ring can express — for about a quarter of the bytes.

NO RETURN IS `null`, NEVER 0 OR range_max
-----------------------------------------
A bearing with nothing in it is a different fact from one with an obstacle, and
both possible encodings of "nothing" are actively dangerous:

  * 0 reads as an obstacle touching the robot, which is the most alarming
    possible reading of the safest possible state;
  * `range_max` reads as a wall at the sensor's limit, so a room with an open
    door renders a solid ring and the one direction the operator could walk is
    the one that looks blocked.

`null` is the only encoding that says what is true, and the renderer draws
nothing there.
"""

from __future__ import annotations

import math

#: Bearings on the wire. 120 gives 3-degree resolution, which is finer than the
#: dots are drawn and keeps the payload near the costmap's ~540 bytes. Not a
#: divisor of anything in the scan: the bucket walk below tolerates any ratio.
DEFAULT_BUCKETS = 120

#: Beyond this, a return is treated as "nothing there" regardless of what the
#: sensor claims. The Mid-360 reports out to 70 m; a dot ring drawn around an
#: operator is useless past the size of a room, and keeping far returns only
#: makes every bearing look occupied.
DEFAULT_MAX_M = 12.0

SCHEMA_VERSION = 1


def _finite(x: float) -> bool:
    return isinstance(x, (int, float)) and math.isfinite(x)


def decimate(
    ranges: list[float],
    angle_min: float,
    angle_increment: float,
    range_min: float,
    range_max: float,
    buckets: int = DEFAULT_BUCKETS,
    max_m: float = DEFAULT_MAX_M,
) -> list[int | None]:
    """Fold a scan into `buckets` bearings of centimetres, or None.

    Bucket 0 is centred on the scan's own `angle_min`, and bearings advance by
    `2*pi/buckets` from there. The output is therefore in whatever frame the
    input was — `pointcloud_to_laserscan` publishes in `base_footprint`, and
    rotating here would be a second place for a frame bug to hide, exactly as
    `world_model_publisher._on_scan` says about the sector maths.
    """
    if buckets <= 0:
        return []
    out: list[int | None] = [None] * buckets
    if not ranges or not _finite(angle_increment) or angle_increment == 0:
        return out

    ceiling = min(range_max, max_m) if _finite(range_max) else max_m
    step = 2.0 * math.pi / buckets

    for i, r in enumerate(ranges):
        # `inf` is how a LaserScan says "no return", and NaN is how a driver
        # says it declines to answer. Both are the absence of an obstacle, not
        # an obstacle at an unknown distance.
        if not _finite(r):
            continue
        if r < range_min or r > ceiling:
            continue
        bearing = angle_min + i * angle_increment
        # Relative to angle_min, wrapped, so a scan spanning more or less than
        # a full turn still lands every sample in exactly one bucket.
        offset = (bearing - angle_min) % (2.0 * math.pi)
        b = int(offset / step) % buckets
        cm = round(r * 100.0)
        cur = out[b]
        if cur is None or cm < cur:
            out[b] = cm
    return out


def encode(
    ranges: list[float],
    angle_min: float,
    angle_increment: float,
    range_min: float,
    range_max: float,
    frame_id: str,
    stamp_s: float,
    buckets: int = DEFAULT_BUCKETS,
    max_m: float = DEFAULT_MAX_M,
) -> dict:
    """The wire payload for `/c3po/scan`.

    `frame_id` travels with it rather than being assumed. The consumer cannot
    tell a scan in `base_footprint` from one in `livox_frame` by looking at the
    numbers, and drawing the second as if it were the first rotates the whole
    world around the operator without anything looking wrong.
    """
    return {
        "v": SCHEMA_VERSION,
        "frame": frame_id or "",
        "stamp_s": round(float(stamp_s), 3),
        # Degrees on the wire: the renderer works in degrees, and a reader
        # debugging this by eye should not have to convert radians in their
        # head to know which way a bearing points.
        "a0_deg": round(math.degrees(angle_min), 2),
        "step_deg": round(360.0 / buckets, 4),
        "max_cm": (
            round(min(range_max, max_m) * 100.0)
            if _finite(range_max)
            else round(max_m * 100.0)
        ),
        "r_cm": decimate(
            ranges,
            angle_min,
            angle_increment,
            range_min,
            range_max,
            buckets,
            max_m,
        ),
    }
