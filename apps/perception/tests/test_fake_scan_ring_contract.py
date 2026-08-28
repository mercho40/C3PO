"""Stage 3 must actually exercise the lidar ring, not merely look like it does.

The headset's radar reads `/c3po/scan`, which `world_model_publisher` derives
from `/scan`. Under `sources:=fake` that `/scan` is a `ros2 topic pub` with
eight hand-written ranges — and whether those eight produce a DRAWABLE ring is
not obvious from reading either file.

Two ways it could quietly not:

  * every sample lands in one bucket, so the radar shows a single dot and looks
    broken while both halves are behaving;
  * a range falls outside `range_min`/`range_max` and is dropped, so the ring
    is sparser than the fake claims and the missing bearing reads as an
    obstacle-free direction that was never measured.

Either way Stage 3 would pass its own checks — the summary is fine, the
crossing is fine — while proving nothing about the chain it was chosen to
prove. So this runs the REAL constants out of `fake.launch.py` through the REAL
encoder, off the robot, before anyone drives to the lab.

Text-parsed rather than imported: `fake.launch.py` imports `launch` and
`launch_ros`, which exist only inside the nav container.
"""

from __future__ import annotations

import ast
import re

from c3po_perception import scan_ring
from conftest import NAV_PKG

FAKE_LAUNCH = NAV_PKG / "launch" / "fake.launch.py"


def _fake_scan() -> dict:
    """The LaserScan fields `sources:=fake` really publishes."""
    src = FAKE_LAUNCH.read_text()

    ranges_match = re.search(r"^FAKE_SCAN_RANGES = (\[[^\]]*\])", src, re.MULTILINE)
    assert ranges_match, "FAKE_SCAN_RANGES is no longer a list literal"
    ranges = ast.literal_eval(ranges_match.group(1))

    def field(name: str) -> float:
        m = re.search(rf"{name}:\s*(-?[\d.]+)", src)
        assert m, f"{name} missing from FAKE_SCAN"
        return float(m.group(1))

    frame = re.search(r"frame_id:\s*(\w+)\}?,?\s*\"", src)
    return {
        "ranges": ranges,
        "angle_min": field("angle_min"),
        "angle_increment": field("angle_increment"),
        "range_min": field("range_min"),
        "range_max": field("range_max"),
        "frame_id": "base_footprint" if frame is None else frame.group(1),
    }


def _ring() -> dict:
    f = _fake_scan()
    return scan_ring.encode(
        f["ranges"],
        f["angle_min"],
        f["angle_increment"],
        f["range_min"],
        f["range_max"],
        f["frame_id"],
        1000.0,
    )


def test_the_parse_found_a_real_scan():
    """Guards the regexes: a parse that found nothing would pass everything."""
    f = _fake_scan()
    assert len(f["ranges"]) >= 4, f"parsed ranges look wrong: {f['ranges']}"
    assert f["range_max"] > f["range_min"] > 0
    assert f["angle_increment"] > 0


def test_every_fake_range_survives_into_the_ring():
    """Not one of the eight may be silently dropped.

    A range outside range_min/range_max vanishes, and a vanished bearing is
    indistinguishable from a direction with nothing in it — which on this
    display means "you may walk that way".
    """
    f = _fake_scan()
    ring = _ring()
    got = sorted(v for v in ring["r_cm"] if v is not None)
    want = sorted(round(r * 100) for r in f["ranges"])
    assert got == want, (
        "the fake's ranges do not all reach the ring. Dropped bearings read as "
        "clear space that was never measured."
    )


def test_the_bearings_do_not_collide_in_one_bucket():
    """Eight samples, eight buckets — otherwise the radar shows one dot.

    Nothing warns about this. `decimate` takes the MINIMUM per bucket, so a
    collision silently discards the other samples and Stage 3 still passes.
    """
    f = _fake_scan()
    filled = [i for i, v in enumerate(_ring()["r_cm"]) if v is not None]
    assert len(filled) == len(f["ranges"]), (
        f"{len(f['ranges'])} fake bearings collapsed into {len(filled)} buckets"
    )


def test_the_ring_is_mostly_empty_and_that_is_correct():
    """112 of 120 bearings are `null`, and they must stay `null`.

    This is the property the radar is built around and the one a well-meaning
    change is most likely to break — filling the gaps with 0 or with max_cm
    both look tidier and are both dangerous. Stage 3 is the cheapest place to
    catch it, because the fake scan is deliberately sparse.
    """
    r_cm = _ring()["r_cm"]
    assert None in r_cm, "a sparse scan must leave empty bearings empty"
    assert 0 not in r_cm, "0 draws an obstacle touching the robot"
    max_cm = _ring()["max_cm"]
    assert r_cm.count(max_cm) == 0, "max_cm draws a wall where nothing was seen"


def test_the_ring_carries_the_frame_the_radar_will_accept():
    """`base_footprint`, or the headset refuses to draw it.

    scan-layer.ts refuses any frame it cannot draw heading-up rather than
    rotating the room around the operator silently. A fake in `livox_frame`
    would therefore render as MARCO DESCONOCIDO and Stage 3 would look like a
    renderer bug.
    """
    assert _ring()["frame"] in ("base_footprint", "base_link")


def test_the_bearings_are_spread_around_the_whole_circle():
    """The point of the ring is what is BEHIND you.

    D7's fake puts one sample in each of the four sectors on purpose. If they
    all landed forward, Stage 3 would confirm the chain and still tell the
    operator nothing about the direction the camera cannot see.
    """
    ring = _ring()
    filled = [i for i, v in enumerate(ring["r_cm"]) if v is not None]
    buckets = len(ring["r_cm"])
    behind = [i for i in filled if i < buckets * 0.25 or i > buckets * 0.75]
    assert behind, "no fake bearing lands behind the robot"
    assert max(filled) - min(filled) > buckets * 0.5, (
        f"the fake bearings only span {max(filled) - min(filled)} of {buckets} "
        "buckets — Stage 3 would not exercise the rear arc at all"
    )
