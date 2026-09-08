"""nav_msgs/OccupancyGrid -> indexed-colour PNG. Pure: no ROS, no numpy, no PIL.

Why PNG: a browser renders it natively in an <img>, so the operator console
needs zero decoding code. Why indexed colour: a costmap has about six
meaningful values, and a PLTE chunk turns that into one byte per cell with the
colours defined once. A 24 x 24 m global costmap at 0.10 m is 240 x 240 =
57,600 cells and compresses to well under a kilobyte, because a costmap is
mostly large uniform regions and that is exactly what DEFLATE is good at.

Why not PIL: nothing else in the nav image needs it, and a PNG writer is zlib
plus five chunk headers. Adding an imaging library to a container that already
takes 35 minutes to build, for 50 lines of work, is a bad trade.

THE PALETTE IS A SAFETY DECISION, NOT A STYLE ONE
-------------------------------------------------
OccupancyGrid uses -1 for UNKNOWN and 0..100 for probability of occupancy.
Unknown and free are completely different facts and must never render alike: a
map where "nobody has looked here" reads as "this is clear floor" is the same
false negative as an offline detector reporting `objects: []`, which D7 spends
most of its rules preventing.

This started as greyscale and that was WRONG, in a way worth recording. With
free=255, occupied=0 and unknown=128, the linear ramp between free and occupied
passes straight THROUGH 128 — so a cell at 45 % occupancy rendered as the exact
byte used for "never observed". The two most important states to distinguish
were pixel-identical, and on a grey map nobody would ever have noticed.

Greyscale cannot fix this: lightness is one-dimensional, so any mid-grey for
unknown collides with some point on the ramp. Hue can. Unknown is a dark, cool
slate that appears nowhere in the warm free->occupied ramp, and it is placed in
the LIGHTNESS GAP between the deepest ramp colour and lethal — so the
distinction survives a screenshot, a projector, and a colour-blind reader. It
differs from every other entry in lightness AND in temperature, and the tests
assert both.

ROW ORDER IS A REAL TRAP
------------------------
OccupancyGrid.data is row-major from the ORIGIN, the bottom-left corner with +y
up (REP-103). PNG scanlines go top-down. So rows are emitted in reverse, or the
map renders vertically mirrored — which looks plausible, stays plausible while
you drive, and only becomes obvious when the robot turns a corner the wrong way
on screen.
"""

from __future__ import annotations

import struct
import zlib
from typing import Iterable, Sequence

# Palette indices. Order is the wire format — the PLTE chunk below is written in
# exactly this order, and the browser only ever sees indices.
IDX_UNKNOWN = 0
IDX_FREE = 1
IDX_RAMP_LO = 2      # first (least occupied) ramp entry
IDX_RAMP_HI = 6      # last  (most  occupied) ramp entry
IDX_LETHAL = 7

# RGB triples, one per index above.
#   unknown : cool blue-grey — absent from the ramp in BOTH lightness and hue
#   free    : white
#   ramp    : pale yellow -> orange -> red, the usual costmap gradient
#   lethal  : near-black, so the impassable core reads as solid
# Luminance (Rec. 709) is given for each, because the SEPARATION is the
# contract — every entry must sit well clear of unknown in lightness, or the
# palette fails the moment someone screenshots it in greyscale. An earlier
# version put unknown at (100,116,139), luminance 114, which landed 10 away from
# the deepest ramp red at 104. Two of the most important states to tell apart
# were, in effect, the same colour.
PALETTE = [
    (51, 65, 85),      # 0 unknown       lum  63   cool, dark, and in NO ramp gap
    (255, 255, 255),   # 1 free          lum 255
    (254, 240, 138),   # 2 ramp          lum 236
    (253, 205, 96),    # 3               lum 207
    (251, 160, 76),    # 4               lum 173
    (249, 133, 71),    # 5               lum 153
    (244, 105, 65),    # 6 most occupied lum 132
    (24, 24, 27),      # 7 lethal        lum  24
]

# At or above this occupancy probability a cell is impassable rather than
# shaded. Nav2's inflation layer emits a gradient around obstacles; the gradient
# is worth seeing, but the lethal core should not be mistaken for it.
LETHAL_THRESHOLD = 90


def occupancy_to_index(value: int) -> int:
    """One OccupancyGrid cell (-1, or 0..100) -> one palette index.

    Anything outside the documented range becomes UNKNOWN rather than being
    clamped into free space. A malformed cell is exactly the case where
    guessing "probably clear" is the dangerous direction to be wrong in.
    """
    if value < 0 or value > 100:
        return IDX_UNKNOWN
    if value == 0:
        return IDX_FREE
    if value >= LETHAL_THRESHOLD:
        return IDX_LETHAL
    span = IDX_RAMP_HI - IDX_RAMP_LO
    step = (value - 1) * (span + 1) // (LETHAL_THRESHOLD - 1)
    return IDX_RAMP_LO + min(step, span)


def occupancy_to_rows(data: Sequence[int], width: int, height: int) -> list[bytes]:
    """Grid cells -> PNG scanlines, TOP-DOWN (see the module docstring).

    Raises on a length mismatch rather than padding: a short `data` means the
    message and its metadata disagree, and rendering 80 % of a map as though it
    were the whole map is worse than rendering nothing at all.
    """
    if width <= 0 or height <= 0:
        raise ValueError(f"degenerate costmap size {width}x{height}")
    if len(data) != width * height:
        raise ValueError(
            f"costmap data is {len(data)} cells, but {width}x{height} needs {width * height}"
        )
    rows: list[bytes] = []
    for y in range(height - 1, -1, -1):          # bottom-up source -> top-down PNG
        start = y * width
        rows.append(bytes(occupancy_to_index(v) for v in data[start:start + width]))
    return rows


def _chunk(tag: bytes, payload: bytes) -> bytes:
    return (
        struct.pack(">I", len(payload))
        + tag
        + payload
        + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF)
    )


def encode_png_indexed(rows: Iterable[bytes], width: int, height: int) -> bytes:
    """Minimal 8-bit indexed PNG (colour type 3). No interlacing, filter 0."""
    raw = b"".join(b"\x00" + row for row in rows)     # 0 = no per-row filter
    plte = b"".join(bytes(rgb) for rgb in PALETTE)
    return (
        b"\x89PNG\r\n\x1a\n"
        + _chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 3, 0, 0, 0))
        + _chunk(b"PLTE", plte)
        + _chunk(b"IDAT", zlib.compress(raw, 9))
        + _chunk(b"IEND", b"")
    )


def encode_occupancy_png(data: Sequence[int], width: int, height: int) -> bytes:
    """The whole path: OccupancyGrid.data -> PNG bytes."""
    return encode_png_indexed(occupancy_to_rows(data, width, height), width, height)
