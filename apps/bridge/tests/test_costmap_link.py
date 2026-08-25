"""The costmap half of the domain-42 link. No DDS, no robot, no ROS.

This is read-only telemetry: it feeds the operator console's map and reaches
nothing that can actuate. The tests below are mostly about DEGRADATION, because
that is where a map display goes wrong in a way nobody notices — a stale map
shown as current, or a good map thrown away by one bad frame.
"""

from __future__ import annotations

import base64
import json

import pytest

from bridge.sdk.perception_link import COSTMAP_STALE_AFTER_S, PerceptionLink

# A 2x2 indexed PNG. Content is irrelevant here — the bridge never decodes it,
# it only checks the field is present and passes the bytes through.
FAKE_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32


def _payload(**over) -> str:
    base = {
        "v": 1,
        "stamp_unix": 1_755_530_000.0,
        "frame_id": "odom",
        "width": 240,
        "height": 240,
        "resolution_m": 0.1,
        "origin_x_m": -12.0,
        "origin_y_m": -12.0,
        "png_base64": base64.b64encode(FAKE_PNG).decode("ascii"),
    }
    base.update(over)
    return json.dumps(base)


@pytest.fixture()
def link():
    """A link with a controllable clock and no DDS anywhere near it."""
    now = {"t": 1000.0}
    lk = PerceptionLink(clock=lambda: now["t"])
    lk._advance = lambda dt: now.__setitem__("t", now["t"] + dt)  # type: ignore[attr-defined]
    return lk


def test_a_good_costmap_is_accepted_and_passed_through_untouched(link):
    assert link._ingest_costmap(_payload()) is True
    payload, age = link.latest_costmap()
    assert payload is not None
    # The bridge is a pass-through: the bytes it hands out must be the bytes it
    # was given. Decoding here would put an image library in the actuation
    # process for no reason.
    assert base64.b64decode(payload["png_base64"]) == FAKE_PNG
    assert age == 0.0
    assert link.costmap_status()["present"] is True
    assert link.costmap_status()["width"] == 240


@pytest.mark.parametrize(
    "bad, why",
    [
        (_payload(v=9), "wrong schema version"),
        (json.dumps({"v": 1}), "no png_base64"),
        ("not json at all", "unparseable"),
        (json.dumps([1, 2, 3]), "not an object"),
    ],
)
def test_malformed_costmaps_are_refused(link, bad, why):
    assert link._ingest_costmap(bad) is False, why
    assert link.latest_costmap() == (None, None)
    assert link.costmap_status()["rejected"] >= 1


def test_a_bad_frame_never_discards_a_good_map(link):
    """One corrupt message must not blank the operator's display.

    The alternative — clearing on any parse failure — turns a single dropped or
    truncated sample into "no map", which is both wrong and alarming. Keeping
    the last good map and letting AGE tell the truth is the honest behaviour.
    """
    assert link._ingest_costmap(_payload()) is True
    for bad in (_payload(v=99), "garbage", json.dumps({"v": 1})):
        link._ingest_costmap(bad)
    payload, _age = link.latest_costmap()
    assert payload is not None, "a good map was thrown away by a bad frame"
    assert link.costmap_status()["received"] == 1
    assert link.costmap_status()["rejected"] == 3


def test_an_old_map_is_still_served_but_marked_stale(link):
    """Showing an old map is fine. Showing it AS CURRENT is not.

    Deliberately different from `latest_report`, which drops a stale world
    summary outright — a stale summary could mislead the model into acting,
    while a stale map only misleads a human who is told how old it is.
    """
    link._ingest_costmap(_payload())
    link._advance(COSTMAP_STALE_AFTER_S + 1.0)

    payload, age = link.latest_costmap()
    assert payload is not None, "an old map is still the best picture available"
    assert age > COSTMAP_STALE_AFTER_S
    status = link.costmap_status()
    assert status["stale"] is True
    assert status["age_s"] == pytest.approx(COSTMAP_STALE_AFTER_S + 1.0, abs=0.01)


def test_status_reports_nothing_rather_than_zeroes_when_no_map_has_arrived(link):
    """ "No map yet" must be distinguishable from "a 0x0 map".

    Same rule as the world model's: absent is not empty. The HTTP route turns
    `present: False` into a 503 rather than a blank image for exactly this
    reason.
    """
    status = link.costmap_status()
    assert status["present"] is False
    assert status["age_s"] is None
    assert status["stale"] is False
    assert status["width"] is None and status["height"] is None


def test_costmap_traffic_cannot_touch_the_cmd_vel_gate(link):
    """Telemetry must not be able to arm anything. Structural, not incidental."""
    assert link.is_enabled() is False
    for _ in range(5):
        link._ingest_costmap(_payload())
    assert link.is_enabled() is False
    assert link.status()["last_sent"] is None
    assert link.status()["cmd_vel_received"] == 0
