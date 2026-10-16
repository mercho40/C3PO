"""The lidar-ring half of the domain-42 link. No DDS, no robot, no ROS.

Read-only telemetry, same footing as the costmap — but with one difference
these tests exist to pin down: this ring is drawn AROUND an operator wearing a
headset, so it degrades differently. A stale map is a picture of somewhere the
robot has left, which a human recognises. A stale ring is an obstacle that
stopped moving when the robot did not, and looks exactly like a correct one.
"""

from __future__ import annotations

import json

import pytest

from bridge.sdk.perception_link import SCAN_STALE_AFTER_S, PerceptionLink

BUCKETS = 120


def _ring(**over) -> str:
    base = {
        "v": 1,
        "frame": "base_footprint",
        "stamp_s": 1_755_530_000.0,
        "a0_deg": -180.0,
        "step_deg": 3.0,
        "max_cm": 1200,
        # A wall 2 m to one side, nothing anywhere else. `None` is the whole
        # point of the encoding — see scan_ring's docstring.
        "r_cm": [None] * 30 + [200] + [None] * (BUCKETS - 31),
    }
    base.update(over)
    return json.dumps(base)


@pytest.fixture()
def link():
    now = {"t": 1000.0}
    lk = PerceptionLink(clock=lambda: now["t"])
    lk._advance = lambda dt: now.__setitem__("t", now["t"] + dt)  # type: ignore[attr-defined]
    return lk


def test_a_good_ring_is_accepted_and_passed_through_untouched(link):
    """The bridge must not re-bucket, re-project or fill in the ring.

    `scan_ring.decimate` already made the safety-relevant call (minimum per
    bucket, not mean). Doing any part of it a second time here would give the
    operator and the agent two different pictures of one room.
    """
    assert link._ingest_scan(_ring()) is True
    payload, age = link.latest_scan()
    assert payload is not None
    assert payload["r_cm"] == json.loads(_ring())["r_cm"]
    assert payload["frame"] == "base_footprint"
    assert age == 0.0
    status = link.scan_status()
    assert status["present"] is True
    assert status["buckets"] == BUCKETS
    assert status["frame"] == "base_footprint"


def test_empty_bearings_survive_as_null(link):
    """`None` must not become 0 or max_cm on the way through.

    Both are actively dangerous: 0 draws an obstacle touching the robot, and
    max_cm draws a wall across the one direction that is actually open.
    """
    payload, _ = link.latest_scan() if link._ingest_scan(_ring()) else (None, None)
    assert payload is not None
    assert payload["r_cm"][0] is None
    assert payload["r_cm"][30] == 200
    assert 0 not in payload["r_cm"]


@pytest.mark.parametrize(
    "bad, why",
    [
        (_ring(v=9), "wrong schema version"),
        (json.dumps({"v": 1}), "no r_cm"),
        (json.dumps({"v": 1, "r_cm": "120 bearings"}), "r_cm is not a list"),
        ("not json at all", "unparseable"),
        (json.dumps([1, 2, 3]), "not an object"),
    ],
)
def test_malformed_rings_are_refused(link, bad, why):
    assert link._ingest_scan(bad) is False, why
    assert link.latest_scan() == (None, None)
    assert link.scan_status()["rejected"] >= 1


def test_a_bad_frame_never_discards_a_good_ring(link):
    """One corrupt sample must not blank the surround mid-walk.

    Clearing on any parse failure turns a dropped sample into "nothing around
    you", which is the most dangerous thing this display can say.
    """
    assert link._ingest_scan(_ring()) is True
    for bad in (_ring(v=99), "garbage", json.dumps({"v": 1})):
        link._ingest_scan(bad)
    payload, _age = link.latest_scan()
    assert payload is not None, "a good ring was thrown away by a bad frame"
    assert link.scan_status()["received"] == 1
    assert link.scan_status()["rejected"] == 3


def test_an_old_ring_is_still_served_but_marked_stale(link):
    """The age always comes back with the payload — never one without the other.

    Whether to grey it out or hide it belongs to whoever draws it, but they
    cannot decide without the age, so `latest_scan` never hands out a ring
    alone.
    """
    link._ingest_scan(_ring())
    link._advance(SCAN_STALE_AFTER_S + 0.5)

    payload, age = link.latest_scan()
    assert payload is not None
    assert age > SCAN_STALE_AFTER_S
    status = link.scan_status()
    assert status["stale"] is True
    assert status["age_s"] == pytest.approx(SCAN_STALE_AFTER_S + 0.5, abs=0.01)


def test_the_ring_goes_stale_sooner_than_the_costmap(link):
    """Two windows, two files, one reason — asserted so a later edit can't drift.

    The map is a 1 Hz picture of a room. The ring is a 4 Hz picture of what is
    about to be walked into, and it is worth less, sooner.
    """
    from bridge.sdk.perception_link import COSTMAP_STALE_AFTER_S

    assert SCAN_STALE_AFTER_S < COSTMAP_STALE_AFTER_S


def test_status_reports_nothing_rather_than_an_empty_ring(link):
    """ "No scan yet" must be distinguishable from "120 clear bearings".

    Absent is not empty. The HTTP route turns `present: False` into a 503 with
    a hint, rather than a circle of nothing that reads as a clear room.
    """
    status = link.scan_status()
    assert status["present"] is False
    assert status["age_s"] is None
    assert status["stale"] is False
    assert status["buckets"] is None


def test_scan_traffic_cannot_touch_the_cmd_vel_gate(link):
    """Telemetry must not be able to arm anything. Structural, not incidental."""
    assert link.is_enabled() is False
    for _ in range(5):
        link._ingest_scan(_ring())
    assert link.is_enabled() is False
    assert link.status()["last_sent"] is None
    assert link.status()["cmd_vel_received"] == 0
