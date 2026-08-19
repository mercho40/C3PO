"""The MJPEG stream's contract, without Pillow, numpy, a camera or a socket.

What is worth testing here is not "does it produce a JPEG" — Pillow does, and
Pillow is not in this harness (see pyproject.toml: every dependency added here
is a laptop somebody cannot run the suite on). What is worth testing is the
part `stream.py` exists to get right: **a frozen frame must not read as a live
one**. That lives in `_Latest.status()` and `_Latest.wait_for_newer()`, both of
which are pure Python over a Condition and a clock.

The RawFrame path is what makes this possible: it is the numpy-free colour
frame the synthetic mode produces, so the store, the sequence numbers and the
staleness maths can all be exercised with a tuple of bytes.
"""

from __future__ import annotations

import time

from c3po_vision import stream


def _store_with_frame(width: int = 4, height: int = 2):
    latest = stream._Latest()
    latest.offer(stream.test_pattern(width, height, 0), time.time())
    return latest


def test_status_before_any_frame_is_not_live():
    """No frame yet is not the same as a fresh one, and must not report `live`.

    A console that starts up against a detector still opening the D435i would
    otherwise show a live badge over a blank panel for the first second.
    """
    latest = stream._Latest()
    status = latest.status(time.time())
    assert status["live"] is False
    assert status["frame_age_s"] is None
    assert status["frames"] == 0
    assert status["width"] is None


def test_status_reports_dimensions_before_anything_is_encoded():
    """Dimensions come from the offered frame, not from a JPEG nobody asked for.

    They were once recorded at encode time, which meant `/status` answered
    `width: null` until someone opened the stream — a console asking "is there a
    camera" got "no size" from a camera that was producing frames.
    """
    latest = _store_with_frame(width=640, height=480)
    status = latest.status(time.time())
    assert (status["width"], status["height"]) == (640, 480)
    assert status["stream_width"] is None  # nothing encoded yet, and that is honest


def test_status_goes_stale_without_new_frames():
    """The whole point of the endpoint. Age crosses STALE_AFTER_S -> not live."""
    latest = _store_with_frame()
    stamped = time.time()
    latest.offer(stream.test_pattern(4, 2, 1), stamped)

    assert latest.status(stamped)["live"] is True
    assert latest.status(stamped + stream.STALE_AFTER_S - 0.01)["live"] is True
    fresh_no_more = latest.status(stamped + stream.STALE_AFTER_S + 0.01)
    assert fresh_no_more["live"] is False
    # The frame is still THERE — /frame.jpg would still serve it. `live` is a
    # statement about age, not about existence, and the console needs both.
    assert fresh_no_more["frames"] == 2
    assert fresh_no_more["frame_age_s"] > stream.STALE_AFTER_S


def test_wait_for_newer_times_out_into_a_closed_stream():
    """A stalled detector must end the MJPEG response, not block on it forever.

    seq 0 is the sentinel the handler turns into `return`, which closes the
    connection. Anything else here means a viewer holds an open socket onto a
    camera that stopped looking.
    """
    latest = _store_with_frame()
    seq, frame, _stamp = latest.wait_for_newer(latest.snapshot()[0], time.time() + 0.05)
    assert seq == 0 and frame is None


def test_wait_for_newer_returns_the_next_frame():
    latest = _store_with_frame()
    seq_before = latest.snapshot()[0]
    latest.offer(stream.test_pattern(4, 2, 3), time.time())
    seq, frame, _stamp = latest.wait_for_newer(seq_before, time.time() + 1.0)
    assert seq == seq_before + 1
    assert frame is not None


def test_close_releases_a_waiting_client():
    """`docker stop` must not wait out STALE_AFTER_S per connected viewer."""
    latest = _store_with_frame()
    latest.close()
    started = time.time()
    seq, _frame, _stamp = latest.wait_for_newer(latest.snapshot()[0], time.time() + 5.0)
    assert seq == 0
    assert time.time() - started < 1.0


def test_test_pattern_is_a_full_rgb_buffer_and_moves():
    """Pillow reads this as raw RGB: three bytes per pixel, exactly, or it errors.

    And it must differ between phases — a static pattern cannot distinguish
    "frames are arriving" from "one frame is being redisplayed", which is the
    exact confusion this module is built to prevent.
    """
    a = stream.test_pattern(64, 8, 0)
    b = stream.test_pattern(64, 8, 1)
    assert len(a.rgb) == 64 * 8 * 3
    assert (a.width, a.height) == (64, 8)
    assert a.rgb != b.rgb


def test_from_env_is_off_unless_asked():
    """Video is opt-in. A stage that did not ask for it must not open a port."""
    assert stream.from_env({}.get) is None
    assert stream.from_env({"C3PO_VISION_STREAM": "0"}.get) is None


def test_from_env_defaults_to_loopback():
    """The safety default, asserted so a refactor cannot quietly widen it.

    This is an unauthenticated camera feed on a robot that shares a Wi-Fi with
    another team; binding it to 0.0.0.0 has to stay a thing somebody typed.
    """
    env = {"C3PO_VISION_STREAM": "1"}
    fs = stream.from_env(env.get)
    assert fs is not None
    assert fs.host == "127.0.0.1"
    assert fs.port == 8081
    assert fs.running is False  # not until start()


def test_from_env_reads_the_tuning_knobs():
    env = {
        "C3PO_VISION_STREAM": "on",
        "C3PO_VISION_STREAM_HOST": "0.0.0.0",
        "C3PO_VISION_STREAM_PORT": "9099",
        "C3PO_VISION_STREAM_HZ": "2",
        "C3PO_VISION_STREAM_QUALITY": "50",
        "C3PO_VISION_STREAM_SCALE": "0.5",
    }
    fs = stream.from_env(env.get)
    assert (fs.host, fs.port, fs.quality, fs.scale) == ("0.0.0.0", 9099, 50, 0.5)
    assert fs._min_interval == 0.5


def test_offer_decimates_to_the_configured_rate():
    """The detector ticks at 10 Hz; the viewer gets 5. Skipped frames cost nothing.

    Asserted on a stopped FrameStream via the internal store, because starting
    one would bind a port in a unit test.
    """
    fs = stream.FrameStream(host="127.0.0.1", port=0, hz=5.0, quality=75, scale=1.0)
    fs.running = True  # no socket; we are testing the decimation, not the server
    now = time.time()
    fs.offer(stream.test_pattern(4, 2, 0), stamp=now)
    fs.offer(stream.test_pattern(4, 2, 1), stamp=now + 0.1)  # too soon: dropped
    fs.offer(stream.test_pattern(4, 2, 2), stamp=now + 0.25)  # due
    assert fs.status()["frames"] == 2


def test_offer_ignores_a_missing_frame():
    """Synthetic mode hands back no colour frame at all. That is not an error."""
    fs = stream.FrameStream(host="127.0.0.1", port=0, hz=5.0, quality=75, scale=1.0)
    fs.running = True
    fs.offer(None)
    assert fs.status()["frames"] == 0
