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

    def test_an_allowlisted_origin_is_echoed_back(self):
        # `http://localhost:3001` is where the Quest browser loads the console
        # from — `quest_setup.sh` forwards 3001 — so this is the headset case.
        headers = camera_relay.camera_headers("http://localhost:3001")
        assert headers["Access-Control-Allow-Origin"] == "http://localhost:3001"

    def test_the_echo_comes_with_vary_origin(self):
        # The answer now depends on the request. A cache that missed that
        # would hand one origin's response to another.
        headers = camera_relay.camera_headers("http://localhost:3001")
        assert headers["Vary"] == "Origin"

    def test_an_unknown_origin_gets_no_cors_header_at_all(self):
        # Not `*`, and not an echo. A page the deployment did not name does
        # not get to read the robot's camera.
        headers = camera_relay.camera_headers("https://evil.example")
        assert "Access-Control-Allow-Origin" not in headers

    def test_no_origin_means_no_header_and_that_is_fine(self):
        # `apps/back`, curl, any native MCP client. CORS is a browser
        # mechanism; its absence restricts nothing else.
        headers = camera_relay.camera_headers(None)
        assert "Access-Control-Allow-Origin" not in headers
        assert headers["Cache-Control"] == "no-store"

    def test_the_default_allowlist_matches_the_deployed_unit(self):
        # `scripts/robot/c3po-bridge.service` sets
        # BRIDGE_CORS_ORIGINS=http://localhost:3001,http://127.0.0.1:3001.
        # A bridge started without the variable must behave like the deployed
        # one, or "works on my machine" means something different here.
        assert "http://localhost:3001" in camera_relay.CORS_ORIGINS
        assert "http://127.0.0.1:3001" in camera_relay.CORS_ORIGINS

    def test_they_still_forbid_caching(self):
        # A cached frame is indistinguishable from a live one, which is the
        # whole reason `/camera/status` exists.
        assert camera_relay.CAMERA_BASE_HEADERS["Cache-Control"] == "no-store"

    def test_relayed_responses_carry_them_too(self):
        # The vision-container relay path is a separate branch from the
        # videohub one, with its own header construction. Both are the camera;
        # both need the header.
        for kind in ("stream", "frame"):
            _media_type, headers = camera_relay.relay_headers(
                kind, "http://localhost:3001"
            )
            assert headers["Access-Control-Allow-Origin"] == "http://localhost:3001", kind
            assert headers["Cache-Control"] == "no-store", kind

    def test_headers_are_fresh_dicts_not_the_shared_base(self):
        # Starlette is free to mutate the mapping it is given. Handing out the
        # module-level constant would let one response's edit reach every
        # later one.
        first = camera_relay.camera_headers("http://localhost:3001")
        first["X-Scribbled-On"] = "1"
        second = camera_relay.camera_headers("http://localhost:3001")
        assert "X-Scribbled-On" not in second
        assert "X-Scribbled-On" not in camera_relay.CAMERA_BASE_HEADERS

    #: The only routes a browser on the console origin may read.
    MAY_GRANT = {"/camera/status", "/camera/frame.jpg", "/camera/stream.mjpg", "/face/seen"}
    #: Never end on their own, so they are checked by preflight only (below).
    STREAMING = {"/camera/stream.mjpg", "/telemetry/voice/events", "/telemetry/voice/audio/input"}

    def test_cors_is_not_sent_anywhere_outside_the_camera_relay(self):
        """The line that keeps `/mcp`, the mic and the voice events off-limits
        to a browser, checked on RESPONSES of the app exactly as served.

        A site-wide `CORSMiddleware` (it was there, switched on by the unit's
        BRIDGE_CORS_ORIGINS) answers every preflight and stamps every reply,
        so a preflight to each route catches it; a GET to each catches a route
        that grows the header itself.
        """
        import logging
        import re

        from starlette.testclient import TestClient

        from bridge import mcp_server

        client = TestClient(mcp_server._http_app(), raise_server_exceptions=False)
        origin = {"Origin": "http://localhost:3001"}
        granted: list[str] = []
        leaks: list[str] = []
        logging.disable(logging.INFO)
        try:
            # Listed from the bare app: a wrapping middleware has no `.routes`.
            for route in mcp_server.mcp.streamable_http_app().routes:
                path = re.sub(r"\{[^}]+\}", "x", route.path)
                pre = client.options(
                    path,
                    headers={**origin, "Access-Control-Request-Method": "POST",
                             "Access-Control-Request-Headers": "content-type"},
                )
                if "access-control-allow-origin" in pre.headers:
                    leaks.append(f"OPTIONS {path}")
                if "GET" not in (getattr(route, "methods", None) or ()):
                    continue
                if path in self.STREAMING or path == "/mcp":
                    continue
                got = client.get(path, headers=origin)
                if "access-control-allow-origin" in got.headers:
                    (granted if path in self.MAY_GRANT else leaks).append(f"GET {path}")
        finally:
            logging.disable(logging.NOTSET)
        assert not leaks, (
            f"CORS granted on {leaks}. This bridge serves /mcp (an unauthenticated "
            "tool surface that can walk the robot), the mic and the voice events "
            "on the same port, so only the camera routes and /face/seen may grant it."
        )
        # And the guard is not vacuous: the camera does get it.
        assert {"GET /camera/status", "GET /face/seen"} <= set(granted), granted

    async def test_the_voice_streams_are_not_readable_cross_origin(self, monkeypatch):
        from bridge import mcp_server
        from bridge.skills import listen as listen_mod

        class Req:
            headers = {"origin": "http://localhost:3001"}

            async def is_disconnected(self):
                return True

        monkeypatch.setattr(listen_mod, "get_mic_listener", lambda: listen_mod.MicListener(
            source=lambda: iter(()), build=lambda: (None, None, None)))
        for route in (mcp_server.voice_events, mcp_server.voice_audio_input):
            response = await route(Req())
            await response.body_iterator.aclose()
            assert "access-control-allow-origin" not in response.headers, route.__name__


class _EndlessUpstream:
    """The vision server's stream: it never ends on its own."""

    closed = False

    def read(self, _n: int) -> bytes:
        return b"--c3poframe\r\n"

    def close(self) -> None:
        self.closed = True


class _ViewerLeavesAfter:
    def __init__(self, chunks: int) -> None:
        self.left_after = chunks

    async def is_disconnected(self) -> bool:
        self.left_after -= 1
        return self.left_after < 0


def test_a_viewer_leaving_releases_the_vision_slot(monkeypatch):
    """Four abandoned relays fill the vision server (max 4) and it 503s everyone."""
    import asyncio
    import urllib.request

    from bridge import mcp_server

    upstream = _EndlessUpstream()
    monkeypatch.setattr(urllib.request, "urlopen", lambda *_a, **_k: upstream)

    async def drain() -> int:
        n = 0
        async for _ in mcp_server._relay_stream("http://vision/stream.mjpg", _ViewerLeavesAfter(2)):
            n += 1
        return n

    assert asyncio.run(asyncio.wait_for(drain(), 5)) == 2
    assert upstream.closed, "the upstream connection outlived the viewer"


def test_a_slow_vision_probe_does_not_stall_the_event_loop(monkeypatch):
    """The probe blocks up to 1.5 s; the same loop serves stop_everything."""
    import asyncio
    import time

    from bridge import mcp_server
    from bridge.sdk import camera_relay, videohub

    class _Camera:
        def status(self) -> dict:
            return {"live": False}

    def slow_probe(now=None):  # noqa: ANN001, ANN202
        time.sleep(0.5)
        return None

    monkeypatch.setattr(mcp_server, "SIM_MODE", "real")
    monkeypatch.setattr(camera_relay, "upstream_status", slow_probe)
    monkeypatch.setattr(videohub, "get_camera", lambda: _Camera())

    class _Request:
        headers: dict = {}

    async def race() -> float:
        route = asyncio.ensure_future(mcp_server.camera_status(_Request()))
        started = time.monotonic()
        await asyncio.sleep(0.05)  # a PARAR arriving while the probe runs
        waited = time.monotonic() - started
        await route
        return waited

    assert asyncio.run(race()) < 0.3, "the camera probe held the event loop"
