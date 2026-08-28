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


class TestTheCorsHeaderAndItsBoundary:
    """`Access-Control-Allow-Origin` on the camera, and NOWHERE ELSE.

    THE HEADER IS LOAD-BEARING. The console is served from `localhost:3001`
    and this bridge answers on `127.0.0.1:8001` — a different origin — so both
    of the console's uses of the camera are cross-origin:

      * `mjpeg-camera.ts` fetches `/camera/status` and READS the body.
      * `webxr/camera-layer.ts` sets `img.crossOrigin = "anonymous"`, because
        WebGL will not sample a texture the page cannot read back.

    Without the header the image never loads, `#ready` stays false, and the
    headset draws nothing. `camera-layer.ts` asserts this works because "the
    vision server sets Access-Control-Allow-Origin: * on every response" —
    true until the feed moved to this process on port 8001, at which point the
    obligation moved with it and nobody noticed. Fixing the forwarded port on
    2026-08-27 restored REACHABILITY and would have left the headset black.

    THE BOUNDARY IS ALSO LOAD-BEARING, and is why this is a constant rather
    than a middleware. This same port serves `/mcp` — the tool surface that can
    walk the robot — with no authentication of its own.
    `apps/back/src/routes/telemetry.ts` says of it: "Never hand a browser a
    route to that port." A blanket CORS middleware would do exactly that: let
    any page the operator opens in the headset browser POST tool calls to a
    humanoid. Scoped to the read-only camera routes, it cannot.
    """

    def test_the_camera_headers_allow_cross_origin_reads(self):
        assert camera_relay.CAMERA_HEADERS["Access-Control-Allow-Origin"] == "*"

    def test_they_still_forbid_caching(self):
        # A cached frame is indistinguishable from a live one, which is the
        # whole reason `/camera/status` exists.
        assert camera_relay.CAMERA_HEADERS["Cache-Control"] == "no-store"

    def test_relayed_responses_carry_them_too(self):
        # The vision-container relay path is a separate branch from the
        # videohub one, with its own header construction. Both are the camera;
        # both need the header.
        for kind in ("stream", "frame"):
            _media_type, headers = camera_relay.relay_headers(kind)
            assert headers["Access-Control-Allow-Origin"] == "*", kind
            assert headers["Cache-Control"] == "no-store", kind

    def test_relay_headers_hands_out_copies_not_the_shared_dict(self):
        # Starlette is free to mutate the mapping it is given. Handing out the
        # module-level constant would let one response's edit reach every
        # later one.
        _mt, first = camera_relay.relay_headers("stream")
        first["X-Scribbled-On"] = "1"
        _mt, second = camera_relay.relay_headers("stream")
        assert "X-Scribbled-On" not in second
        assert "X-Scribbled-On" not in camera_relay.CAMERA_HEADERS

    def test_cors_is_not_applied_anywhere_outside_the_camera_relay(self):
        """The line that keeps `/mcp` off-limits to a browser.

        Read as source rather than by exercising routes: the claim is that no
        OTHER response construction in this package can grow this header, and
        counting where it can come from at all is the cheapest way to say so.
        """
        import pathlib

        package = pathlib.Path(camera_relay.__file__).parent.parent
        offenders = []
        for path in sorted(package.rglob("*.py")):
            if path.name == "camera_relay.py":
                continue  # the one definition
            for number, line in enumerate(path.read_text().splitlines(), 1):
                if "Access-Control-Allow-Origin" not in line:
                    continue
                if line.lstrip().startswith("#"):
                    continue  # prose explaining the rule is not the rule
                offenders.append(f"{path.name}:{number}")
        assert not offenders, (
            "Access-Control-Allow-Origin appears outside camera_relay.py: "
            f"{offenders}. This bridge serves /mcp — an unauthenticated tool "
            "surface that can walk the robot — on the same port, so CORS must "
            "stay scoped to the read-only camera routes."
        )
