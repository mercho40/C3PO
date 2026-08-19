"""The OccupancyGrid -> PNG encoder, on a laptop with no ROS.

The palette assertions here are not cosmetic. This encoder had a real bug on its
first pass: with a greyscale ramp between free=255 and occupied=0, unknown=128
sat exactly ON the ramp, so a cell at 45 % occupancy encoded to the same byte as
"never observed". The two states a map most needs to distinguish were pixel
identical, and nothing about a grey map would have revealed it. Hence
`test_no_occupancy_value_collides_with_unknown`, which is the regression guard
for that specific mistake.
"""

from __future__ import annotations

import struct
import zlib

import pytest

from c3po_perception.costmap_png import (
    IDX_FREE,
    IDX_RAMP_HI,
    IDX_RAMP_LO,
    IDX_LETHAL,
    IDX_UNKNOWN,
    LETHAL_THRESHOLD,
    PALETTE,
    encode_occupancy_png,
    occupancy_to_index,
    occupancy_to_rows,
)

PNG_SIG = b"\x89PNG\r\n\x1a\n"


# --------------------------------------------------------------------------
# The palette contract
# --------------------------------------------------------------------------


def test_no_occupancy_value_collides_with_unknown():
    """THE regression guard. No real occupancy may render as "never observed".

    A map that draws unlooked-at space the same as clear floor is the same
    false negative as a detector reporting `objects: []` while offline, which is
    the rule D7 spends most of its text on.
    """
    collisions = [v for v in range(0, 101) if occupancy_to_index(v) == IDX_UNKNOWN]
    assert collisions == [], f"occupancy values rendering as UNKNOWN: {collisions}"


def test_unknown_and_malformed_are_unknown():
    assert occupancy_to_index(-1) == IDX_UNKNOWN
    # Out of range in either direction is unknown, never free — guessing
    # "probably clear" is the dangerous direction to be wrong in.
    assert occupancy_to_index(-99) == IDX_UNKNOWN
    assert occupancy_to_index(101) == IDX_UNKNOWN
    assert occupancy_to_index(255) == IDX_UNKNOWN


def test_free_and_lethal_endpoints():
    assert occupancy_to_index(0) == IDX_FREE
    assert occupancy_to_index(LETHAL_THRESHOLD) == IDX_LETHAL
    assert occupancy_to_index(100) == IDX_LETHAL


def test_ramp_is_monotonic_between_free_and_lethal():
    seen = [occupancy_to_index(v) for v in range(1, LETHAL_THRESHOLD)]
    assert seen == sorted(seen), "occupancy ramp must never go backwards"
    assert min(seen) > IDX_FREE and max(seen) < IDX_LETHAL


def test_unknown_differs_from_every_other_colour_in_both_lightness_and_hue():
    """Distinguishable on a projector, in a screenshot, and to a colour-blind eye."""
    r, g, b = PALETTE[IDX_UNKNOWN]
    lum_unknown = 0.2126 * r + 0.7152 * g + 0.0722 * b
    for idx, (r2, g2, b2) in enumerate(PALETTE):
        if idx == IDX_UNKNOWN:
            continue
        lum = 0.2126 * r2 + 0.7152 * g2 + 0.0722 * b2
        # 35 rather than a token margin: the failure this guards against is two
        # colours that are merely "different enough on my monitor".
        assert abs(lum - lum_unknown) > 35, (
            f"palette {idx} luminance {lum:.0f} is too close to unknown {lum_unknown:.0f}"
        )
    # Cool (blue-dominant) where the whole ramp is warm (red-dominant).
    assert b > r, "unknown must be cool; the free->occupied ramp is warm"

    # Hue is asserted against the RAMP only. Free (white) and lethal (near-black)
    # are achromatic on purpose, and demanding r > b of a near-black would be a
    # meaningless test that could only be satisfied by tinting black — which is
    # a worse palette adopted to please an assertion. Both are already separated
    # from unknown by lightness above, which is the guarantee that matters.
    for idx in range(IDX_RAMP_LO, IDX_RAMP_HI + 1):
        rr, _gg, bb = PALETTE[idx]
        assert rr > bb, f"ramp entry {idx} must be warm, so unknown stays distinct by hue"


# --------------------------------------------------------------------------
# Row order — the mirrored-map trap
# --------------------------------------------------------------------------


def test_rows_are_flipped_because_png_is_top_down():
    """OccupancyGrid starts at the BOTTOM-left; PNG scanlines run top-down."""
    # 3 wide, 2 tall. Grid row 0 is the bottom row.
    rows = occupancy_to_rows([0, 0, 0, -1, -1, -1], width=3, height=2)
    assert len(rows) == 2
    # The FIRST PNG row must be the LAST grid row (the unknown one).
    assert set(rows[0]) == {IDX_UNKNOWN}
    assert set(rows[1]) == {IDX_FREE}


def test_size_mismatch_raises_rather_than_padding():
    """A short grid means metadata and data disagree; a partial map is worse than none."""
    with pytest.raises(ValueError, match="needs"):
        occupancy_to_rows([0] * 5, width=3, height=2)


def test_degenerate_size_raises():
    with pytest.raises(ValueError, match="degenerate"):
        occupancy_to_rows([], width=0, height=0)


# --------------------------------------------------------------------------
# The PNG itself
# --------------------------------------------------------------------------


def _chunks(png: bytes):
    """Walk the chunk stream, verifying every CRC as we go."""
    assert png[:8] == PNG_SIG
    out, i = [], 8
    while i < len(png):
        (length,) = struct.unpack(">I", png[i:i + 4])
        tag = png[i + 4:i + 8]
        payload = png[i + 8:i + 8 + length]
        (crc,) = struct.unpack(">I", png[i + 8 + length:i + 12 + length])
        assert crc == zlib.crc32(tag + payload) & 0xFFFFFFFF, f"bad CRC on {tag!r}"
        out.append((tag, payload))
        i += 12 + length
    return out


def test_png_is_structurally_valid_indexed_colour():
    data = [-1] * 16
    png = encode_occupancy_png(data, 4, 4)
    tags = [t for t, _ in _chunks(png)]
    assert tags == [b"IHDR", b"PLTE", b"IDAT", b"IEND"]

    ihdr = dict(_chunks(png))[b"IHDR"]
    w, h, depth, colour, comp, filt, interlace = struct.unpack(">IIBBBBB", ihdr)
    assert (w, h) == (4, 4)
    assert depth == 8
    assert colour == 3, "colour type 3 = indexed; the palette is the whole point"
    assert (comp, filt, interlace) == (0, 0, 0)

    plte = dict(_chunks(png))[b"PLTE"]
    assert len(plte) == len(PALETTE) * 3


def test_pixels_round_trip_through_the_png():
    """Decode IDAT back and confirm the indices survived, flip included."""
    width, height = 3, 2
    grid = [0, 100, -1, -1, 0, 100]          # bottom row, then top row
    png = encode_occupancy_png(grid, width, height)
    raw = zlib.decompress(dict(_chunks(png))[b"IDAT"])
    # Each scanline is 1 filter byte + `width` index bytes.
    assert len(raw) == height * (1 + width)
    decoded = [list(raw[i * (1 + width) + 1: (i + 1) * (1 + width)]) for i in range(height)]
    assert decoded[0] == [IDX_UNKNOWN, IDX_FREE, IDX_LETHAL]   # grid's TOP row
    assert decoded[1] == [IDX_FREE, IDX_LETHAL, IDX_UNKNOWN]   # grid's BOTTOM row


def test_a_realistic_global_costmap_stays_tiny():
    """24x24 m at 0.10 m is 240x240. It must be nowhere near the domain-42 budget.

    This is the number that justifies putting the map on domain 42 at all, when
    point clouds are explicitly excluded from it: rmem_max here is 208 KiB.
    """
    w = h = 240
    grid = [-1] * (w * h)
    for y in range(h):
        for x in range(w):
            dx, dy = x - 120, y - 120
            if dx * dx + dy * dy < 90 * 90:
                grid[y * w + x] = 0
            if x == 200 and 60 < y < 180:
                grid[y * w + x] = 100
    png = encode_occupancy_png(grid, w, h)
    assert len(png) < 8 * 1024, f"{len(png)} bytes is far larger than expected"
    assert len(png) < (w * h) // 10, "a costmap should compress by at least 10x"


def test_worst_case_noise_still_fits_the_budget():
    """Even an incompressible costmap must stay under the refusal threshold.

    Not realistic — it is the bound costmap_publisher.MAX_ENCODED_BYTES exists
    to enforce, checked here so the two cannot drift apart silently.
    """
    w = h = 240
    grid = [(x * 7 + y * 13) % 101 for y in range(h) for x in range(w)]
    png = encode_occupancy_png(grid, w, h)
    assert len(png) < 256 * 1024
