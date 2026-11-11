"""RealSenseSource.start's one-shot recovery from a wedged D435 (VIDIOC_S_FMT errno=5),
and run()'s reopen when the D435 drops off USB mid-run.

`pyrealsense2` is a stand-in module put in sys.modules, the same move as
test_nav2_launch_flags.py. What is checked is OUR policy (which errors reset,
how often, what is raised); whether a real D435 re-enumerates inside the
container is checked on the robot, in `docker logs c3po-perception-vision`.
"""

from __future__ import annotations

import sys
import types
from types import SimpleNamespace as NS

import pytest
from c3po_vision import detector

WEDGED = "xioctl(VIDIOC_S_FMT) failed, errno=5 Last Error: Input/output error"
BUSY = "xioctl(VIDIOC_S_FMT) failed, errno=16 Last Error: Device or resource busy"
INTR = NS(fx=615.0, fy=615.0, ppx=320.0, ppy=240.0, width=640, height=480)
PROFILE = NS(
    get_device=lambda: NS(first_depth_sensor=lambda: NS(get_depth_scale=lambda: 0.001)),
    get_stream=lambda _s: NS(as_video_stream_profile=lambda: NS(intrinsics=INTR)),
)


def fake_rs(monkeypatch, *errors, listed=True, comes_back=True, frames=()):
    """Each pipeline.start() raises the next of `errors`, then succeeds.

    wait_for_frames() walks `frames`: "ok" is a frameset, a string is raised, a
    callable is called with the state and what it returns is raised.
    `state.absent` empty listings are served before the device is listed again.
    """
    state = NS(errors=list(errors), pipelines=0, starts=0, resets=0,
               frames=list(frames), absent=0)

    class Pipeline:
        def __init__(self):
            state.pipelines += 1

        def start(self, _config):
            state.starts += 1
            if state.errors:
                raise RuntimeError(state.errors.pop(0))
            return PROFILE

        def wait_for_frames(self):
            item = state.frames.pop(0) if state.frames else "ok"
            if item == "ok":
                return NS(get_color_frame=lambda: NS(get_data=lambda: "bgr"),
                          get_depth_frame=lambda: NS(get_data=lambda: "z16"))
            raise RuntimeError(item(state) if callable(item) else item)

        def stop(self):
            pass

    class Device:
        def get_info(self, _key):
            return "SN123"

        def hardware_reset(self):
            state.resets += 1

    def query_devices():
        if state.absent:
            state.absent -= 1
            return []
        gone = state.resets and not comes_back
        return [Device()] if listed and not gone else []

    attrs = {
        "config": lambda: NS(enable_stream=lambda *_a: None),
        "pipeline": Pipeline,
        "align": lambda _s: NS(process=lambda f: f),
        "context": lambda: NS(query_devices=query_devices),
        "stream": NS(color="color", depth="depth"),
        "format": NS(bgr8="bgr8", z16="z16"),
        "camera_info": NS(serial_number="serial_number"),
    }
    rs = types.ModuleType("pyrealsense2")
    for k, v in attrs.items():
        setattr(rs, k, v)
    monkeypatch.setitem(sys.modules, "pyrealsense2", rs)
    monkeypatch.setattr(detector.time, "sleep", lambda _s: None)
    return state


def test_wedged_once_resets_once_and_starts_a_fresh_pipeline(monkeypatch):
    state = fake_rs(monkeypatch, WEDGED)
    src = detector.RealSenseSource()
    src.start()
    assert (state.resets, state.pipelines, state.starts) == (1, 2, 2)
    assert src.intrinsics is not None and src.intrinsics.fx == 615.0


def test_busy_never_resets(monkeypatch):
    state = fake_rs(monkeypatch, BUSY)
    with pytest.raises(RuntimeError, match="errno=16"):
        detector.RealSenseSource().start()
    assert state.resets == 0


def test_wedged_twice_resets_once_then_raises(monkeypatch):
    state = fake_rs(monkeypatch, WEDGED, WEDGED)
    with pytest.raises(RuntimeError, match="errno=5"):
        detector.RealSenseSource().start()
    assert (state.resets, state.starts) == (1, 2)


def test_device_that_never_comes_back_raises_naming_its_serial(monkeypatch):
    state = fake_rs(monkeypatch, WEDGED, comes_back=False)
    with pytest.raises(RuntimeError, match="SN123"):
        detector.RealSenseSource().start()
    assert (state.resets, state.starts) == (1, 1)


def test_no_device_listed_raises_the_original_error(monkeypatch):
    state = fake_rs(monkeypatch, WEDGED, listed=False)
    with pytest.raises(RuntimeError) as err:
        detector.RealSenseSource().start()
    assert str(err.value) == WEDGED and state.resets == 0


# --- mid-run USB drop -------------------------------------------------------

LOST = ("Device disconnected. Failed to reconnect: xioctl(VIDIOC_S_FMT) failed, "
        "errno=5 Last Error: Input/output error")
TIMEOUT = "Frame didn't arrive within 5000"


def drop(absent=0, start_errors=()):
    """A frames item: the D435 leaves for `absent` listings and fails `start_errors` on return."""
    def item(state):
        state.absent = absent
        state.errors = list(start_errors)
        return LOST
    return item


def run_detector(monkeypatch, state, publishes):
    """run() on the fake D435 until `publishes` reports went out. Returns (code, events, published, clock)."""
    clock = NS(t=1000.0)

    def sleep(s):
        clock.t += s

    monkeypatch.setattr(detector, "time", NS(time=lambda: clock.t, monotonic=lambda: clock.t, sleep=sleep))
    monkeypatch.setitem(sys.modules, "numpy", NS(asanyarray=lambda x: x))
    monkeypatch.setenv("C3PO_VISION_DRY_RUN", "1")
    monkeypatch.delenv("C3PO_VISION_FAKE", raising=False)
    monkeypatch.delenv("C3PO_VISION_STREAM", raising=False)
    monkeypatch.setattr(detector, "TensorRTDetector", lambda: NS(infer=lambda _c: []))
    monkeypatch.setattr(detector, "load_labels", lambda _p: ["person"])

    class Stop:
        stop = False

    published = []

    def publish(_self, payload):
        published.append(payload)
        Stop.stop = len(published) >= publishes

    monkeypatch.setattr(detector, "_Stopper", lambda: Stop)
    monkeypatch.setattr(detector.StdoutPublisher, "publish", publish)
    events = []
    monkeypatch.setattr(detector, "log", lambda event, **_f: events.append(event))
    start = clock.t
    code = detector.run()
    return code, events, published, clock.t - start


@pytest.mark.parametrize("absent, resets", [(0, 1), (3, 0)], ids=["still-listed", "unplugged"])
def test_disconnect_mid_run_reopens_and_resumes_publishing(monkeypatch, absent, resets):
    state = fake_rs(monkeypatch, frames=["ok", drop(absent=absent), "ok", "ok"])
    code, events, published, _ = run_detector(monkeypatch, state, publishes=3)
    assert code == 0 and len(published) == 3
    assert (state.starts, state.resets) == (2, resets)
    assert events.index("camera.lost") < events.index("camera.reopened")
    assert ("camera.reset" in events) == bool(resets)
    assert "giving.up" not in events


@pytest.mark.parametrize("item", [drop(start_errors=[BUSY] * 50), drop(absent=10**6)],
                         ids=["reopen-keeps-failing", "never-comes-back"])
def test_a_camera_that_does_not_come_back_gives_up_within_the_bound(monkeypatch, item):
    state = fake_rs(monkeypatch, frames=["ok", item])
    code, events, published, elapsed = run_detector(monkeypatch, state, publishes=99)
    assert code == 1 and len(published) == 1 and "giving.up" in events
    assert state.starts <= 1 + detector.RECOVER_ATTEMPTS
    assert elapsed <= detector.RECOVER_S + detector.RESET_WAIT_S + 10
    assert "camera.lost" in events and "camera.reopened" not in events


def test_a_slow_frame_is_not_a_lost_camera(monkeypatch):
    state = fake_rs(monkeypatch, frames=["ok", TIMEOUT, TIMEOUT, "ok"])
    code, events, published, _ = run_detector(monkeypatch, state, publishes=2)
    assert code == 0 and len(published) == 2
    assert (state.starts, state.resets) == (1, 0) and "camera.lost" not in events


@pytest.mark.parametrize("msg, lost", [
    (LOST, True),
    ("wait_for_frames cannot be called before start()", True),
    (TIMEOUT, False),
    ("incomplete frameset from the D435i", False),
])
def test_what_counts_as_a_lost_camera(msg, lost):
    assert detector.camera_lost(RuntimeError(msg)) is lost
