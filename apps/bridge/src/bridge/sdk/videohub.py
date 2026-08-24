"""The head camera, without taking it away from anyone.

    from bridge.sdk.videohub import get_camera
    get_camera().start()
    seq, jpeg, stamp = get_camera().snapshot()

WHY THIS EXISTS AT ALL
----------------------
`/dev/video4` — the D435i's colour capture node — is single-owner, and on this
robot the owner is `videohub_pc4`, a Unitree service under `master_service`
that takes the device as argv and holds it open. Our detector opens the same
node through pyrealsense2 and dies on `xioctl(VIDIOC_S_FMT) failed, errno=16
Device or resource busy`, which is how the operator console has had no picture.

The obvious fix is to take the device: stop `master_service`, `pkill -9 -f
videohub_pc4`, open the RealSense ourselves. The other team on this robot does
exactly that (`~/gemm_ai/xr_teleoperate/scripts/start_image_service.sh`) and
documented the catch — their own README says they could not confirm to 100%
that stopping `master_service` leaves arm and balance control alone. On a
shared robot we are about to walk, that is an unconfirmed belief, not a
clearance, and it also takes the official app's camera away from the other team.

So: don't fight for the device. `videohub_pc4` already holds it and already
serves it, and the vendor's own RPC hands us the frames it is decoding anyway.
Measured on the G1, 2026-08-21, while `videohub_pc4` kept the device:

    ok=30 failed=0
    latency  min=6ms  median=20ms  max=33ms
    size     175-176 KB      SOF0: 1920x1080
    throughput back-to-back: 49.69 Hz

This is the same shape as the LiDAR: consume the vendor's republish instead of
claiming the sensor, so every consumer can have it at once.

WHAT THIS IS NOT
----------------
**Colour only. There is no depth here**, and that is why this does not replace
`apps/perception`'s detector. The detector ranges a detection by reading the
aligned depth frame under its box and DROPS a detection whose depth will not
resolve rather than reporting a guessed distance (`detector.py`). A JPEG cannot
do that. So this feeds the operator's eyes — `/live-camera`, the VR layer — and
the world model still needs either the device or the LiDAR for range.

The two routes are mutually exclusive by construction, which is worth stating
plainly because it decides a run before it starts:

  * `videohub_pc4` alive  -> this works, the detector cannot start.
  * `videohub_pc4` killed -> the detector works, this returns nothing.

`GetImageSample` failing is therefore an ordinary, expected state with a known
cause, not a broken camera — `status()` says so rather than reporting a fault.

A FROZEN FRAME IS NOT A LIVE ONE
--------------------------------
Same rule as `c3po_vision.stream` and for the same reason: an `<img>` holding
the last JPEG it received is indistinguishable from a working camera. A failed
sample updates NOTHING — not the frame, not the stamp, not the counter — so the
age keeps climbing and `live` goes false on its own. `frames` is a monotonic
counter the console watches to catch a stall that both ends of an age check
straddle.

IMPORTABLE ON A LAPTOP
----------------------
`unitree_sdk2py` is imported inside `start()`, never at module scope, so the
bridge still imports on a Mac with no CycloneDDS — the same constraint the rest
of `bridge.sdk` observes.
"""

from __future__ import annotations

import os
import struct
import threading
import time
from typing import Any, Dict, Optional, Tuple

import structlog

log = structlog.get_logger(__name__)

# How old the newest frame may be before `live` goes false. Matches
# `c3po_vision.stream.STALE_AFTER_S` so the console's single client applies one
# rule to both feeds.
STALE_AFTER_S = 1.0

# Poll rate. The RPC sustains ~50 Hz back-to-back; we take a fraction of that
# because this is an operator's view of one camera, not a video service, and
# every frame is a 175 KB JPEG crossing an SSH tunnel. `C3PO_CAMERA_HZ` moves it.
DEFAULT_HZ = 10.0

# How long a single GetImageSample may block before we treat it as failed. The
# measured max was 33 ms; a second is far past "slow" and into "gone".
RPC_TIMEOUT_S = 1.0

# After this many consecutive failures we stop logging each one. The expected
# cause (somebody took the device) does not stop being true, and a log line per
# poll at 10 Hz would bury everything else the bridge says.
QUIET_AFTER_FAILURES = 3


def jpeg_dimensions(data: bytes) -> Tuple[Optional[int], Optional[int]]:
    """(width, height) from a JPEG's SOF marker, or (None, None).

    Done by hand rather than with Pillow: this runs in the bridge's venv, which
    has no imaging stack and should not grow one to read two integers. Walks the
    segment chain instead of scanning for the marker bytes, because 0xFFC0 can
    occur inside entropy-coded data.
    """
    if len(data) < 4 or data[0] != 0xFF or data[1] != 0xD8:
        return (None, None)
    # Every SOF variant carries the same geometry in the same place. SOF4 (0xC4)
    # is DHT and SOF8/12 (0xC8/0xCC) are JPG/DAC — not frame headers.
    sof = {0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF}
    i = 2
    while i + 3 < len(data):
        if data[i] != 0xFF:
            return (None, None)  # desynchronised; do not guess
        marker = data[i + 1]
        if marker in sof:
            if i + 9 > len(data):
                return (None, None)
            height, width = struct.unpack(">HH", data[i + 5 : i + 9])
            return (width, height)
        if marker == 0xD8 or 0xD0 <= marker <= 0xD9:
            i += 2  # standalone marker, no length field
            continue
        length = struct.unpack(">H", data[i + 2 : i + 4])[0]
        if length < 2:
            return (None, None)
        i += 2 + length
    return (None, None)


class VideohubCamera:
    """Polls the vendor's videohub RPC and holds the newest JPEG.

    One per process. `start()` is idempotent and never raises: a bridge that
    refuses to boot has no `stop_everything`, and a missing camera is nowhere
    near bad enough to trade that away.
    """

    def __init__(self, hz: float = DEFAULT_HZ) -> None:
        self._hz = max(0.5, hz)
        self._lock = threading.Lock()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()

        # Guarded by _lock.
        self._seq = 0
        self._jpeg: Optional[bytes] = None
        self._stamp = 0.0
        self._dims: Tuple[Optional[int], Optional[int]] = (None, None)
        self._failures = 0
        self._last_error: Optional[str] = None
        self._ever_framed = False
        self._started = False

    # -- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        with self._lock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop.clear()
            self._started = True
            self._thread = threading.Thread(
                target=self._run, name="videohub-camera", daemon=True
            )
            self._thread.start()
        log.info("videohub.camera.started", hz=self._hz)

    def stop(self) -> None:
        self._stop.set()
        thread = self._thread
        if thread is not None:
            thread.join(timeout=2.0)

    # -- reading -----------------------------------------------------------

    def snapshot(self) -> Tuple[int, Optional[bytes], float]:
        """(seq, jpeg, stamp). seq 0 means nothing has ever arrived."""
        with self._lock:
            return self._seq, self._jpeg, self._stamp

    def status(self, now: Optional[float] = None) -> Dict[str, Any]:
        """The console's contract: `live`, `frame_age_s`, `frames`, geometry.

        Deliberately the same field names `c3po_vision.stream.status()` emits,
        so `apps/web`'s one MJPEG client works against either feed unchanged.
        """
        now = time.monotonic() if now is None else now
        with self._lock:
            seq, stamp, dims = self._seq, self._stamp, self._dims
            failures, last_error = self._failures, self._last_error
            ever, started = self._ever_framed, self._started
        age = None if seq == 0 else round(max(0.0, now - stamp), 3)
        return {
            "v": 1,
            "source": "videohub",
            "live": seq > 0 and age is not None and age < STALE_AFTER_S,
            "frame_age_s": age,
            "frames": seq,
            "width": dims[0],
            "height": dims[1],
            # No re-encode happens here: what the vendor produced is exactly
            # what goes down the wire, so these cannot disagree.
            "stream_width": dims[0],
            "stream_height": dims[1],
            "stale_after_s": STALE_AFTER_S,
            "started": started,
            "consecutive_failures": failures,
            "last_error": last_error,
            # The one thing that makes a dark feed readable. Failing here has a
            # known, ordinary cause, and an operator who is told it will not go
            # looking for a broken camera.
            "hint": self._hint(ever, failures),
        }

    @staticmethod
    def _hint(ever_framed: bool, failures: int) -> Optional[str]:
        if failures == 0:
            return None
        if not ever_framed:
            return (
                "no frame has ever arrived — videohub_pc4 may not be running. It is "
                "the vendor service that owns /dev/video4; if somebody stopped "
                "master_service to give the RealSense to the detector, this feed is "
                "off by design. `sudo systemctl start master_service` brings it back."
            )
        return (
            "frames stopped — most likely videohub_pc4 was killed to free /dev/video4 "
            "for the detector. The two cannot run at once."
        )

    # -- the thread --------------------------------------------------------

    def _run(self) -> None:
        client = self._make_client()
        if client is None:
            return

        period = 1.0 / self._hz
        while not self._stop.is_set():
            started = time.monotonic()
            self.poll_once(client)
            # Pace from the START of the call, not the end, so a slow RPC eats
            # its own slack instead of adding to the period.
            self._stop.wait(max(0.0, period - (time.monotonic() - started)))

    def poll_once(self, client: Any) -> bool:
        """One RPC and its recording. True if a frame landed.

        A method rather than four lines inline in `_run` so the tests can drive
        the REAL branch. Reimplementing "code != 0 counts as a failure" in a test
        helper would let an inverted condition here pass a green suite, and this
        particular condition is the one that decides whether a dead camera
        reports itself as live.
        """
        try:
            code, data = client.GetImageSample()
        except Exception as exc:  # noqa: BLE001 - recorded, never raised at a caller
            self._record_failure(f"{type(exc).__name__}: {exc}")
            return False
        if code != 0 or not data:
            self._record_failure(f"rpc code {code}")
            return False
        self._record_frame(bytes(data))
        return True

    def _make_client(self) -> Any:
        """The vendor RPC client, or None with the reason logged.

        `VideoClient` lives under `go2` in the SDK and there is no G1 variant,
        but the G1's `videohub` service answers it — verified live on this
        robot. The DDS factory is already initialized at import time by
        `bridge.sdk.connection.init_dds`, so this must NOT initialize it again.
        """
        try:
            from unitree_sdk2py.go2.video.video_client import VideoClient

            client = VideoClient()
            client.SetTimeout(RPC_TIMEOUT_S)
            client.Init()
            return client
        except Exception:
            log.exception("videohub.camera.client_failed")
            with self._lock:
                self._last_error = "could not create the videohub RPC client"
            return None

    def _record_frame(self, data: bytes) -> None:
        dims = jpeg_dimensions(data)
        with self._lock:
            first = not self._ever_framed
            self._seq += 1
            self._jpeg = data
            self._stamp = time.monotonic()
            if dims != (None, None):
                self._dims = dims
            self._failures = 0
            self._last_error = None
            self._ever_framed = True
        if first:
            log.info("videohub.camera.first_frame", width=dims[0], height=dims[1])

    def _record_failure(self, reason: str) -> None:
        with self._lock:
            self._failures += 1
            self._last_error = reason
            count = self._failures
        if count <= QUIET_AFTER_FAILURES:
            log.warning("videohub.camera.sample_failed", reason=reason, consecutive=count)
        elif count == QUIET_AFTER_FAILURES + 1:
            log.warning(
                "videohub.camera.sample_failed_quieting",
                reason=reason,
                note="further failures logged only on recovery",
            )


_camera: Optional[VideohubCamera] = None
_camera_lock = threading.Lock()


def get_camera() -> VideohubCamera:
    """The process-wide camera. Created on first use, started by `main()`."""
    global _camera
    with _camera_lock:
        if _camera is None:
            hz = float(os.environ.get("C3PO_CAMERA_HZ", "") or DEFAULT_HZ)
            _camera = VideohubCamera(hz=hz)
        return _camera
