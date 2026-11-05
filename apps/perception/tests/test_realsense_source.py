"""RealSenseSource.start's one-shot recovery from a wedged D435 (VIDIOC_S_FMT errno=5).

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


def fake_rs(monkeypatch, *errors, listed=True, comes_back=True):
    """Each pipeline.start() raises the next of `errors`, then succeeds."""
    state = NS(errors=list(errors), pipelines=0, starts=0, resets=0)

    class Pipeline:
        def __init__(self):
            state.pipelines += 1

        def start(self, _config):
            state.starts += 1
            if state.errors:
                raise RuntimeError(state.errors.pop(0))
            return PROFILE

    class Device:
        def get_info(self, _key):
            return "SN123"

        def hardware_reset(self):
            state.resets += 1

    def query_devices():
        gone = state.resets and not comes_back
        return [Device()] if listed and not gone else []

    attrs = {
        "config": lambda: NS(enable_stream=lambda *_a: None),
        "pipeline": Pipeline,
        "align": lambda _s: object(),
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
