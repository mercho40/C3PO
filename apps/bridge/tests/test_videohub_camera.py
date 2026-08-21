"""The head camera read over the vendor RPC, without the device.

The properties worth pinning are the honesty ones. A camera feed that lies is
worse than no feed, because an `<img>` holding its last JPEG is indistinguishable
from a working camera, and the operator acts on what they see.
"""

from __future__ import annotations

import struct

import pytest

from bridge.sdk.videohub import STALE_AFTER_S, VideohubCamera, jpeg_dimensions


def _jpeg(width: int, height: int, *, decoy: bool = False) -> bytes:
    """A byte string with a real JPEG segment chain, optionally booby-trapped.

    `decoy=True` buries an `FF C0` inside an APP0 payload — which is exactly what
    entropy-coded image data does in practice. A parser that scans for the marker
    instead of walking the length chain reads its geometry out of that and is
    confidently wrong.
    """
    app0_payload = b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
    if decoy:
        app0_payload += b"\xff\xc0\x00\x11\x08" + struct.pack(">HH", 9999, 9999)
    app0 = b"\xff\xe0" + struct.pack(">H", len(app0_payload) + 2) + app0_payload
    sof0 = b"\xff\xc0\x00\x11\x08" + struct.pack(">HH", height, width) + b"\x03\x01\x22\x00"
    return b"\xff\xd8" + app0 + sof0 + b"\xff\xda\x00\x08\x01\x01\x00\x00\x3f\x00"


class _FakeClient:
    """Stands in for the SDK's VideoClient. Scripted, so a test can fail a call."""

    def __init__(self, script):
        self._script = list(script)
        self.calls = 0

    def GetImageSample(self):  # noqa: N802 - the SDK names it
        self.calls += 1
        if not self._script:
            return (3102, b"")
        return self._script.pop(0)


def _camera(script) -> VideohubCamera:
    """A camera wired to a fake client, driven one poll at a time.

    The thread is never started: `_run` is a loop around the two recorders, and
    stepping them directly is what makes the timing assertions deterministic.
    """
    cam = VideohubCamera(hz=10.0)
    cam._client_for_test = _FakeClient(script)  # noqa: SLF001 - test seam
    return cam


def _poll(cam: VideohubCamera) -> None:
    """One real poll. Calls production code, not a copy of it."""
    cam.poll_once(cam._client_for_test)  # noqa: SLF001


# --- geometry ---------------------------------------------------------------


def test_dimensions_come_from_the_segment_chain():
    assert jpeg_dimensions(_jpeg(1920, 1080)) == (1920, 1080)


def test_a_marker_inside_a_payload_is_not_a_frame_header():
    # The whole reason the parser walks lengths instead of scanning for 0xFFC0.
    assert jpeg_dimensions(_jpeg(1920, 1080, decoy=True)) == (1920, 1080)


@pytest.mark.parametrize("blob", [b"", b"not a jpeg", b"\xff\xd8", b"\xff\xd8\xff"])
def test_unreadable_input_returns_no_guess(blob):
    # (None, None) and not a default: a wrong resolution in the console is worse
    # than an absent one, because it looks like an answer.
    assert jpeg_dimensions(blob) == (None, None)


# --- the honesty properties -------------------------------------------------


def test_a_failed_sample_updates_nothing():
    """The core rule. A failure must not refresh the frame, the stamp or the count.

    If it did, `frame_age_s` would reset on every failed poll and `live` would
    stay true forever while the camera was gone — the console would show a
    photograph and call it a feed.
    """
    cam = _camera([(0, _jpeg(640, 480)), (3102, b""), (3102, b"")])

    _poll(cam)
    seq_after_frame, jpeg_after_frame, stamp_after_frame = cam.snapshot()

    _poll(cam)
    _poll(cam)
    seq, jpeg, stamp = cam.snapshot()

    assert seq == seq_after_frame == 1
    assert jpeg == jpeg_after_frame
    assert stamp == stamp_after_frame
    assert cam.status()["consecutive_failures"] == 2


def test_live_goes_false_on_age_alone():
    cam = _camera([(0, _jpeg(640, 480))])
    _poll(cam)

    _, _, stamp = cam.snapshot()
    assert cam.status(now=stamp)["live"] is True
    assert cam.status(now=stamp + STALE_AFTER_S + 0.01)["live"] is False


def test_frames_is_monotonic_so_a_stall_is_visible():
    # The console watches this counter to catch a gap that both ends of an age
    # comparison straddle. It must count frames, not polls.
    cam = _camera([(0, _jpeg(640, 480)), (3102, b""), (0, _jpeg(640, 480))])
    for _ in range(3):
        _poll(cam)
    assert cam.status()["frames"] == 2


def test_nothing_ever_received_is_not_live_and_has_no_age():
    cam = _camera([(3102, b"")])
    _poll(cam)
    status = cam.status()
    assert status["live"] is False
    assert status["frame_age_s"] is None
    assert status["frames"] == 0


# --- the hint ---------------------------------------------------------------


def test_never_framed_and_stopped_are_different_diagnoses():
    """Both are "no picture"; only one of them means somebody took the device."""
    never = _camera([(3102, b"")])
    _poll(never)
    assert "may not be running" in never.status()["hint"]

    stopped = _camera([(0, _jpeg(640, 480)), (3102, b"")])
    _poll(stopped)
    _poll(stopped)
    assert "frames stopped" in stopped.status()["hint"]


def test_a_healthy_camera_offers_no_hint():
    cam = _camera([(0, _jpeg(1920, 1080))])
    _poll(cam)
    status = cam.status()
    assert status["hint"] is None
    assert (status["width"], status["height"]) == (1920, 1080)


def test_status_field_names_match_the_vision_containers():
    """One client in the console, two possible servers, no branch in the browser.

    `apps/web/src/lib/robot/mjpeg-camera.ts` reads exactly these; drifting from
    `c3po_vision.stream.status()` would break the feed it is meant to stand in for.
    """
    cam = _camera([(0, _jpeg(640, 480))])
    _poll(cam)
    for field in ("live", "frame_age_s", "frames", "width", "height", "stale_after_s"):
        assert field in cam.status(), field


def test_an_rpc_that_raises_is_a_failure_not_a_crash():
    """The thread must survive a throwing client — DDS goes away at reboot.

    A camera poller that dies on the first exception leaves `status()` frozen at
    whatever it last saw, which is the frozen-frame failure by another route.
    """

    class Exploding:
        def GetImageSample(self):  # noqa: N802 - the SDK names it
            raise RuntimeError("dds is gone")

    cam = VideohubCamera(hz=10.0)
    assert cam.poll_once(Exploding()) is False
    status = cam.status()
    assert status["consecutive_failures"] == 1
    assert "RuntimeError" in status["last_error"]
    assert status["live"] is False
