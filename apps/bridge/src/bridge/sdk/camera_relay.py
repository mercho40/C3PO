"""One camera URL, whichever source is actually alive.

There are two feeds of the same physical camera and they are mutually exclusive
by construction (`docs/ROBOT-HARDWARE.md` §6.6):

    videohub   the vendor RPC, read by `bridge/sdk/videohub.py`. Works only
               while `videohub_pc4` holds /dev/video4. 1920x1080, colour only.
    vision     apps/perception's container on :8081, which serves MJPEG only
               while IT holds the device. 640x480, and the detector's depth
               comes with it.

Whoever owns the device decides which one is live, and that changes whenever
somebody runs `take_camera` or hands the sensors back. Making the operator
notice the swap and edit `PUBLIC_ROBOT_CAM_URL` is a manual step that will be
forgotten, and its failure mode is a black rectangle — the exact symptom this
project has already chased through two headset sessions, twice concluding the
camera hardware was broken when it was merely somewhere else.

So the bridge picks. `:8001/camera` serves videohub when videohub is live and
relays :8081 when it is not, and `-L 8081` stops being something to remember.

WHY THE BRIDGE AND NOT THE CONSOLE. Both feeds are on the Jetson's loopback and
the bridge is already there; a browser would need a second forward and a CORS
story to try them itself. The bridge also already knows one of the two answers
without asking anybody.
"""

from __future__ import annotations

import json
import threading
import time
import urllib.request
from typing import Any, Callable, Dict, Optional, Tuple

import structlog

log = structlog.get_logger(__name__)

#: The vision container's MJPEG server. Same host and port `perception_up`
#: passes it, so the two cannot drift apart silently.
VISION_HOST = "127.0.0.1"
VISION_PORT = 8081

#: How long an upstream probe is reused. The console polls /status twice a
#: second; without this, each poll would add an HTTP round trip to :8081. One
#: second is under the staleness threshold both servers use, so a feed that
#: dies is still noticed within the window the console already tolerates.
PROBE_TTL_S = 1.0

#: Short on purpose. This runs inside a request the operator is waiting on, and
#: a vision container mid-TensorRT-build will simply not answer — which is a
#: "not live" answer, not something worth blocking a page render for.
PROBE_TIMEOUT_S = 1.5


def vision_url(path: str) -> str:
    return "http://{}:{}/{}".format(VISION_HOST, VISION_PORT, path.lstrip("/"))


def _fetch_status() -> Optional[Dict[str, Any]]:
    try:
        with urllib.request.urlopen(vision_url("status"), timeout=PROBE_TIMEOUT_S) as resp:
            return dict(json.loads(resp.read().decode("utf-8")))
    except Exception:
        # Refused, timed out, or serving something that is not our JSON. All of
        # them mean the same thing to a caller: no picture from here.
        return None


class _Probe:
    """The upstream's status, cached for `PROBE_TTL_S`."""

    def __init__(self, fetch: Callable[[], Optional[Dict[str, Any]]] = _fetch_status) -> None:
        self._fetch = fetch
        self._lock = threading.Lock()
        self._at = 0.0
        self._value: Optional[Dict[str, Any]] = None

    def status(self, now: Optional[float] = None) -> Optional[Dict[str, Any]]:
        now = time.monotonic() if now is None else now
        with self._lock:
            if self._at and now - self._at < PROBE_TTL_S:
                return self._value
        try:
            value = self._fetch()
        except Exception:
            # Never raise into a request. The production fetch already catches,
            # but the cache is the last line: a probe that throws would turn a
            # dark camera into a 500 on a page the operator is watching.
            log.debug("camera_relay.probe_failed", exc_info=True)
            value = None
        with self._lock:
            self._at, self._value = now, value
        return value


_probe = _Probe()


def upstream_status(now: Optional[float] = None) -> Optional[Dict[str, Any]]:
    return _probe.status(now)


def is_live(status: Optional[Dict[str, Any]]) -> bool:
    if status is None:
        return False
    return bool(status.get("live"))


def choose_source(
    videohub: Optional[Dict[str, Any]], vision: Optional[Dict[str, Any]]
) -> Optional[str]:
    """Which feed to serve: "videohub", "vision", or None.

    VIDEOHUB FIRST WHEN BOTH ARE LIVE, which should never happen — one device,
    one owner — but a tie needs a rule rather than an accident. It is the higher
    resolution of the two, and it is the one that costs nobody the device.
    """
    if is_live(videohub):
        return "videohub"
    if is_live(vision):
        return "vision"
    return None


class _Monotonic:
    """Keeps `frames` non-decreasing across a source switch.

    The console watches this counter to catch a stall that both ends of an age
    comparison straddle (`apps/web/src/lib/robot/mjpeg-camera.ts`). The two
    servers count their own frames independently, so switching from a videohub
    feed at 3025 to a vision feed at 677 would hand the client a counter that
    went backwards — which is not a state its stall logic has a name for.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._source: Optional[str] = None
        self._base = 0
        self._last = 0

    def total(self, source: Optional[str], frames: int) -> int:
        with self._lock:
            if source != self._source:
                self._base = self._last
                self._source = source
            self._last = self._base + int(frames or 0)
            return self._last


_frames = _Monotonic()


def merged_status(
    videohub: Dict[str, Any],
    vision: Optional[Dict[str, Any]],
    source: Optional[str],
) -> Dict[str, Any]:
    """The console's contract, filled in from whichever source is serving.

    Field names match `c3po_vision.stream.status()` and `VideohubCamera.status()`
    exactly, because the point of this module is that the browser never learns
    there are two servers.
    """
    chosen: Dict[str, Any] = {}
    if source == "videohub":
        chosen = videohub
    elif source == "vision" and vision is not None:
        chosen = vision

    frames = _frames.total(source, chosen.get("frames", 0))
    status = {
        "v": 1,
        "source": source or "none",
        "live": bool(chosen.get("live", False)),
        "frame_age_s": chosen.get("frame_age_s"),
        "frames": frames,
        "width": chosen.get("width"),
        "height": chosen.get("height"),
        "stream_width": chosen.get("stream_width"),
        "stream_height": chosen.get("stream_height"),
        "stale_after_s": chosen.get("stale_after_s", 1.0),
        # Both sides, always — an operator debugging a dark feed needs to know
        # that the OTHER one was also asked and what it said.
        "sources": {
            "videohub": {
                "live": bool(videohub.get("live", False)),
                "hint": videohub.get("hint"),
            },
            "vision": (
                {"live": bool(vision.get("live", False))}
                if vision is not None
                else {"live": False, "hint": "not answering on :{}".format(VISION_PORT)}
            ),
        },
    }
    if source is None:
        status["hint"] = (
            "neither camera feed is live. The vendor feed needs videohub_pc4 to hold "
            "/dev/video4; the vision feed needs `perception_up perception` (or nav2) "
            "with the device taken by `take_camera`. They cannot both work at once."
        )
    return status


def relay_headers(kind: str) -> Tuple[str, Dict[str, str]]:
    """Content type and headers for a relayed response."""
    if kind == "stream":
        return "multipart/x-mixed-replace; boundary=c3poframe", {"Cache-Control": "no-store"}
    return "image/jpeg", {"Cache-Control": "no-store"}
