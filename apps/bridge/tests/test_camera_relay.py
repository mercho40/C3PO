"""Picking a camera feed, so the operator never has to.

Two feeds of one physical camera, mutually exclusive by construction, and which
one is live depends on who currently owns /dev/video4. That changes whenever
somebody runs `take_camera`. Getting this wrong shows up as a black rectangle,
which this project has twice mistaken for broken hardware.
"""

from __future__ import annotations

from bridge.sdk import camera_relay
from bridge.sdk.camera_relay import _Monotonic, _Probe, choose_source, merged_status

LIVE_VIDEOHUB = {"live": True, "frames": 3025, "frame_age_s": 0.02, "width": 1920, "height": 1080}
DARK_VIDEOHUB = {"live": False, "frames": 3025, "frame_age_s": 320.0, "hint": "frames stopped"}
LIVE_VISION = {"live": True, "frames": 677, "frame_age_s": 0.1, "width": 640, "height": 480}
DARK_VISION = {"live": False, "frames": 0, "frame_age_s": None}


# --- choosing ---------------------------------------------------------------


def test_the_vendor_feed_is_used_when_it_is_live():
    assert choose_source(LIVE_VIDEOHUB, None) == "videohub"


def test_the_vision_container_takes_over_when_the_vendor_feed_goes_dark():
    """Exactly what happens the moment `take_camera` runs."""
    assert choose_source(DARK_VIDEOHUB, LIVE_VISION) == "vision"


def test_neither_live_is_a_real_answer_not_a_guess():
    assert choose_source(DARK_VIDEOHUB, DARK_VISION) is None
    assert choose_source(DARK_VIDEOHUB, None) is None


def test_a_tie_has_a_rule_rather_than_an_accident():
    """One device, one owner — so this should not happen. It still needs a rule."""
    assert choose_source(LIVE_VIDEOHUB, LIVE_VISION) == "videohub"


# --- the console's contract -------------------------------------------------


def test_the_reported_geometry_follows_the_serving_source():
    status = merged_status(DARK_VIDEOHUB, LIVE_VISION, "vision")
    assert (status["width"], status["height"]) == (640, 480)
    assert status["live"] is True
    assert status["source"] == "vision"


def test_both_sides_are_always_reported():
    """An operator debugging a dark feed needs to know the other was asked."""
    status = merged_status(DARK_VIDEOHUB, DARK_VISION, None)
    assert status["sources"]["videohub"]["live"] is False
    assert status["sources"]["vision"]["live"] is False
    assert "hint" in status


def test_a_missing_upstream_is_described_rather_than_omitted():
    status = merged_status(DARK_VIDEOHUB, None, None)
    assert "not answering" in status["sources"]["vision"]["hint"]


def test_the_field_names_match_what_the_console_reads():
    status = merged_status(LIVE_VIDEOHUB, None, "videohub")
    for field in ("live", "frame_age_s", "frames", "width", "height", "stale_after_s"):
        assert field in status, field


# --- the frame counter ------------------------------------------------------


def test_frames_never_go_backwards_across_a_source_switch():
    """The console's stall check compares this poll's counter to the last one.

    Switching from a videohub feed at 3025 to a vision feed at 677 would hand it
    a counter that went backwards, which its stall logic has no name for.
    """
    counter = _Monotonic()
    assert counter.total("videohub", 3025) == 3025
    after_switch = counter.total("vision", 677)
    assert after_switch >= 3025
    assert counter.total("vision", 678) > after_switch


def test_a_stalled_source_still_reports_a_still_counter():
    """Monotonic must not mean "always increasing" — a stall has to stay visible."""
    counter = _Monotonic()
    counter.total("vision", 10)
    assert counter.total("vision", 10) == counter.total("vision", 10)


# --- the probe cache --------------------------------------------------------


def test_the_upstream_is_not_re_probed_on_every_poll():
    """The console polls /status twice a second; each poll would be a round trip."""
    calls = []

    probe = _Probe(fetch=lambda: calls.append(1) or {"live": True})
    probe.status(now=100.0)
    probe.status(now=100.2)
    probe.status(now=100.5)
    assert len(calls) == 1


def test_the_cache_expires_so_a_dead_feed_is_noticed():
    calls = []
    probe = _Probe(fetch=lambda: calls.append(1) or {"live": True})
    probe.status(now=100.0)
    probe.status(now=100.0 + camera_relay.PROBE_TTL_S + 0.01)
    assert len(calls) == 2


def test_an_unreachable_upstream_is_none_not_an_exception():
    """Refused, timed out, or serving something else all mean "no picture here".

    The cache is the last line, not the first: a probe that throws would turn a
    dark camera into a 500 on a page the operator is watching.
    """

    def boom():
        raise OSError("connection refused")

    assert _Probe(fetch=boom).status(now=1.0) is None


def test_the_real_fetch_returns_none_for_a_closed_port(monkeypatch):
    """Against a port nothing is listening on — not a tautology.

    The first version of this asserted `is None or isinstance(dict)`, which is
    true of every possible value, and passed against the live tunnel on the
    developer's machine while proving nothing.
    """
    monkeypatch.setattr(camera_relay, "VISION_PORT", 9)  # discard
    monkeypatch.setattr(camera_relay, "PROBE_TIMEOUT_S", 0.25)
    assert camera_relay._fetch_status() is None
