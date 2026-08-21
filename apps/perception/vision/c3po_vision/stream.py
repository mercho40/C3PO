"""The colour frame, as MJPEG over HTTP. The operator console's live view.

    GET /stream.mjpg   multipart/x-mixed-replace   what an <img> renders
    GET /frame.jpg     one JPEG                    for a poll or a screenshot
    GET /status        JSON                        how old the last frame is

This exists because `detector.py` already owns the RealSense and `/dev/videoN`
is single-owner: whoever wants to show the operator a picture has to be the
process that is already holding the camera, or take it away from the process
that is. So the detector hands every colour frame it successfully grabs to this
module, and this module is the only thing in the container that speaks HTTP.

WHY MJPEG AND NOT WEBRTC
------------------------
The sim's cameras are aiortc servers and `apps/web/src/lib/webrtc/sim-camera.ts`
speaks that protocol. Reusing it here was the obvious move and is the wrong one:
aiortc + a TLS cert + ICE would go into a container pinned to ONE core
(`--cpuset-cpus 5` in scripts/robot/perception_up) that is already running a TRT
engine, and WebRTC's media path is UDP, which is the transport this project has
twice found blocked between the robot/sim and the operator's machine. MJPEG is
a `Content-Type` and a boundary string: it costs one JPEG encode per frame, it
is plain TCP so it goes through the same SSH tunnel as the bridge, and the
browser renders it with `<img src>` — no cert to accept per port, no signalling.

The cost is honest and worth stating: no inter-frame compression, so this is
~30-60 KB per frame where H.264 would be ~3. At the default 5 Hz that is
~1.5 Mbit/s for one viewer. It is a view of one camera for one operator, not a
video service.

LOOPBACK BY DEFAULT, AND THAT IS A SAFETY DEFAULT
-------------------------------------------------
An unauthenticated video feed of a lab, on a school Wi-Fi that this robot shares
with another team, is not something to bind to `0.0.0.0` because it was
convenient. The bridge is on loopback for the same reason (docs/OPERATIONS.md
§2) and the console already reaches it through an SSH tunnel — add
`-L 8081:127.0.0.1:8081` to the same `ssh` and the console sees this too.
`C3PO_VISION_STREAM_HOST=0.0.0.0` is there for a demo on a trusted LAN and
should be a decision somebody makes out loud.

A FROZEN FRAME IS NOT A LIVE ONE
--------------------------------
`detector.py`'s rule is that a failed tick publishes nothing, because "I did not
look" and "I looked and the room is clear" are different facts. The same rule
applies to a picture, and it is easier to get wrong: an `<img>` holding the last
JPEG it received looks exactly like a camera that is working. So the detector
offers a frame ONLY on a tick that succeeded, `/status` reports the age of the
newest frame, and the MJPEG connection is CLOSED once no new frame has arrived
for `STALE_AFTER_S`. The console's job is to say so; this module's job is to
make it possible to know.

PYTHON 3.8, AND IMPORTABLE ON A LAPTOP
--------------------------------------
Same constraints as `detector.py`: the container's interpreter is 3.8, and
`import c3po_vision.stream` must work on a Mac with no Pillow and no numpy so
the Stage 0 suite can import the module and check its pure parts. Pillow and
numpy are therefore imported inside the encoder, never at module scope.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from collections import namedtuple
from typing import Any, Callable, Dict, Optional, Tuple

# The multipart boundary. Arbitrary, but it must not appear in JPEG payloads —
# it does not, since every part carries an explicit Content-Length and the
# reader never has to scan for it.
BOUNDARY = "c3poframe"

# No new frame for this long and the feed is not live any more. 1.0 s is ten
# ticks at the detector's default 10 Hz: long enough that a single slow frame
# does not drop the viewer, short enough that a wedged camera stops looking
# alive within a breath. `detector.py` gives up on the camera after 20
# consecutive failures, so a truly dead D435i ends the connection well before
# the container exits.
STALE_AFTER_S = 1.0

# How long a blocked MJPEG writer waits on the condition before re-checking
# whether the server is shutting down. Only affects `docker stop` latency.
_WAKE_S = 0.25

# A colour frame with no numpy: (width, height, packed RGB bytes). This is what
# the synthetic mode produces, so the whole HTTP path — encode, multipart,
# browser — can be exercised on a machine with no camera and no numpy.
RawFrame = namedtuple("RawFrame", ["width", "height", "rgb"])


def _log(event: str, **fields: Any) -> None:
    """Same one-line stderr format as detector.log, duplicated on purpose.

    Importing detector.log here would make `import c3po_vision.stream` pull in
    the detector's module-scope env parsing, which is exactly what the Stage 0
    suite must be able to avoid.
    """
    parts = [f"{k}={v}" for k, v in sorted(fields.items())]
    sys.stderr.write("[stream] {} {}\n".format(event, " ".join(parts)))
    sys.stderr.flush()


def encode_jpeg(frame: Any, quality: int, scale: float) -> Tuple[bytes, int, int]:
    """A colour frame -> JPEG bytes, (width, height) of what was encoded.

    Accepts either the detector's numpy BGR array (HxWx3 uint8, the pyrealsense2
    layout) or a `RawFrame` of packed RGB. Pillow only: the container has no
    OpenCV and adding one for `imencode` would be ~100 MB of image to encode a
    picture Pillow encodes in single-digit milliseconds.

    BGR -> RGB is a copy, not a view, because Pillow needs C-contiguous bytes
    and a negative-stride slice is not. That copy is ~0.9 MB at 640x480; it runs
    on the HTTP thread, never on the detector's tick.
    """
    from PIL import Image  # lazy: see the module docstring

    if isinstance(frame, RawFrame):
        img = Image.frombytes("RGB", (frame.width, frame.height), frame.rgb)
    else:
        import numpy as np

        rgb = np.ascontiguousarray(frame[:, :, ::-1])
        img = Image.fromarray(rgb, "RGB")

    if scale != 1.0:
        img = img.resize(
            (max(1, int(img.width * scale)), max(1, int(img.height * scale))),
            Image.BILINEAR,
        )

    from io import BytesIO

    buf = BytesIO()
    # optimize=False deliberately: it re-runs Huffman table selection for a few
    # percent of size at several ms of CPU, on the one core this container has.
    img.save(buf, format="JPEG", quality=quality, optimize=False)
    return buf.getvalue(), img.width, img.height


class _Latest:
    """The newest frame, and the JPEG of it, behind one lock.

    Two things share this: the detector's tick (writes a frame) and N HTTP
    threads (read it). The frame is stored as a COPY — what pyrealsense2 hands
    back is a view into a buffer librealsense recycles as soon as the frame
    object is released, so handing that array to another thread is a race whose
    symptom is a torn or wrong picture, not a crash.

    The JPEG is encoded lazily and cached against the sequence number, so ten
    viewers of the same frame cost one encode, and a frame nobody is watching
    costs none.
    """

    def __init__(self) -> None:
        self._cv = threading.Condition()
        self._seq = 0
        self._frame = None  # type: Any
        self._stamp = 0.0
        self._jpeg_seq = -1
        self._jpeg = b""
        self._dims = (0, 0)
        self._encoded_dims = (0, 0)
        self.offered = 0
        self.clients = 0
        self.closed = False

    def offer(self, frame: Any, stamp: float) -> None:
        if isinstance(frame, RawFrame):
            copied, dims = frame, (frame.width, frame.height)
        else:
            copied = frame.copy()  # numpy: see the class docstring
            dims = (int(frame.shape[1]), int(frame.shape[0]))
        with self._cv:
            self._seq += 1
            self._frame = copied
            self._stamp = stamp
            # Recorded here, not at encode time: `/status` has to be able to say
            # what the camera is producing before anyone has asked for a JPEG.
            self._dims = dims
            self.offered += 1
            self._cv.notify_all()

    def close(self) -> None:
        with self._cv:
            self.closed = True
            self._cv.notify_all()

    def snapshot(self) -> Tuple[int, Any, float]:
        with self._cv:
            return self._seq, self._frame, self._stamp

    def wait_for_newer(self, seq: int, deadline: float) -> Tuple[int, Any, float]:
        """Block until a frame newer than `seq` exists, or the deadline passes.

        Returns the frame with seq 0 on timeout, which the caller reads as "the
        detector stopped feeding me" and turns into a closed connection.
        """
        with self._cv:
            while self._seq <= seq and not self.closed:
                remaining = deadline - time.time()
                if remaining <= 0:
                    return 0, None, 0.0
                self._cv.wait(min(_WAKE_S, remaining))
            if self.closed:
                return 0, None, 0.0
            return self._seq, self._frame, self._stamp

    def jpeg(self, seq: int, frame: Any, quality: int, scale: float) -> bytes:
        with self._cv:
            if self._jpeg_seq == seq:
                return self._jpeg
        data, w, h = encode_jpeg(frame, quality, scale)
        with self._cv:
            # Last writer wins, and that is fine: both encoded a real frame, and
            # the cache is only ever consulted for the seq it was stored under.
            self._jpeg_seq = seq
            self._jpeg = data
            self._encoded_dims = (w, h)
        return data

    def status(self, now: float) -> Dict[str, Any]:
        with self._cv:
            seq, stamp = self._seq, self._stamp
            dims, encoded = self._dims, self._encoded_dims
            offered, clients = self.offered, self.clients
        age = None if seq == 0 else round(max(0.0, now - stamp), 3)
        return {
            "v": 1,
            # `live` is the whole point of this endpoint. An <img> cannot tell
            # the console the difference between a feed and a photograph of one.
            "live": seq > 0 and age is not None and age < STALE_AFTER_S,
            "frame_age_s": age,
            "frames": offered,
            "clients": clients,
            # What the camera produces, and what actually goes down the wire —
            # they differ whenever C3PO_VISION_STREAM_SCALE is not 1.0, and a
            # console that shows the first while sending the second is lying
            # about the picture's resolution.
            "width": dims[0] or None,
            "height": dims[1] or None,
            "stream_width": encoded[0] or None,
            "stream_height": encoded[1] or None,
            "stale_after_s": STALE_AFTER_S,
        }


def _handler_class(latest: _Latest, quality: int, scale: float) -> Any:
    from http.server import BaseHTTPRequestHandler

    class Handler(BaseHTTPRequestHandler):
        # HTTP/1.0 keeps this simple: no chunked encoding, no keep-alive state
        # machine. The MJPEG response is one connection held open until the feed
        # goes stale, which is what a browser <img> wants anyway.
        protocol_version = "HTTP/1.0"
        server_version = "c3po-vision"

        def log_message(self, fmt: str, *args: Any) -> None:
            # BaseHTTPRequestHandler logs every request to stderr in Apache
            # format. At 5 Hz for /status that would bury the detector's own
            # lines; failures still surface through the handlers below.
            pass

        def _headers(self, ctype: str, extra: Optional[Dict[str, str]] = None) -> None:
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Cache-Control", "no-store, no-cache, must-revalidate")
            # The console is served from another origin (:3001) and fetches
            # /status with JS. `<img>` needs no CORS; this does. The endpoint is
            # read-only, carries no secret, and is on loopback by default.
            self.send_header("Access-Control-Allow-Origin", "*")
            for k, v in (extra or {}).items():
                self.send_header(k, v)
            self.end_headers()

        def do_GET(self) -> None:  # BaseHTTPRequestHandler names the verb, not us
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            if path == "/status":
                return self._status()
            if path == "/frame.jpg":
                return self._frame()
            if path == "/transcribe/status":
                return self._transcribe_status()
            if path in ("/stream.mjpg", "/"):
                return self._stream()
            self.send_error(404, "not a c3po vision endpoint")

        def do_POST(self) -> None:
            """POST /transcribe — raw 16 kHz mono 16-bit PCM in, Spanish out.

            The ONLY endpoint here that is not read-only, and it is still inert:
            it consumes audio and returns text. It cannot move the robot, cannot
            touch the camera, and holds nothing.

            The bridge calls this instead of running Whisper itself, so the
            process that owns stop_everything keeps no ML stack (D6.2) and the
            inference lands on a GPU that is otherwise idle.
            """
            path = self.path.split("?", 1)[0].rstrip("/") or "/"
            if path != "/transcribe":
                self.send_error(404, "not a c3po vision endpoint")
                return

            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self.send_error(400, "bad Content-Length")
                return
            # A cap, because this is a socket anyone on loopback can post to and
            # the buffer is read into memory. 120 s of audio is far past any
            # utterance and still only ~3.8 MB.
            if length <= 0 or length > 120 * 16000 * 2:
                self.send_error(400, "expected 1..120s of 16kHz mono 16-bit PCM")
                return

            pcm = self.rfile.read(length)
            if len(pcm) != length:
                self.send_error(400, "short read")
                return

            try:
                from c3po_vision import transcribe as tr

                result = tr.transcribe(pcm)
            except Exception as exc:  # noqa: BLE001 - reported, never raised at a client
                # 503 rather than 500: "the model is not installed" is a state
                # the caller can act on, and the bridge degrades to its own
                # segmenter rather than losing the utterance.
                body = json.dumps({"error": str(exc)[:300]}).encode("utf-8")
                self.send_response(503)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
                return

            body = json.dumps(result, ensure_ascii=False).encode("utf-8")
            self._headers("application/json", {"Content-Length": str(len(body))})
            self.wfile.write(body)

        def _transcribe_status(self) -> None:
            from c3po_vision import transcribe as tr

            body = json.dumps(tr.status()).encode("utf-8")
            self._headers("application/json", {"Content-Length": str(len(body))})
            self.wfile.write(body)

        def _status(self) -> None:
            body = json.dumps(latest.status(time.time())).encode("ascii")
            self._headers("application/json", {"Content-Length": str(len(body))})
            self.wfile.write(body)

        def _frame(self) -> None:
            seq, frame, _stamp = latest.snapshot()
            if seq == 0 or frame is None:
                # 503, not 404: the endpoint exists and the camera has simply
                # not produced anything yet. 404 would read as a wrong URL.
                self.send_error(503, "no frame yet")
                return
            data = latest.jpeg(seq, frame, quality, scale)
            self._headers("image/jpeg", {"Content-Length": str(len(data))})
            self.wfile.write(data)

        def _stream(self) -> None:
            self._headers("multipart/x-mixed-replace; boundary=" + BOUNDARY)
            with latest._cv:
                latest.clients += 1
            try:
                seq = 0
                while True:
                    seq, frame, _stamp = latest.wait_for_newer(
                        seq, time.time() + STALE_AFTER_S
                    )
                    if seq == 0 or frame is None:
                        # Stale or shutting down. Ending the response is the
                        # only in-band way MJPEG has to say "this is no longer
                        # live" — see the module docstring.
                        return
                    data = latest.jpeg(seq, frame, quality, scale)
                    self.wfile.write(
                        (
                            "--{}\r\nContent-Type: image/jpeg\r\n"
                            "Content-Length: {}\r\n\r\n"
                        ).format(BOUNDARY, len(data)).encode("ascii")
                    )
                    self.wfile.write(data)
                    self.wfile.write(b"\r\n")
            except (BrokenPipeError, ConnectionResetError):
                pass  # the operator closed the tab; not an error
            finally:
                with latest._cv:
                    latest.clients -= 1

    return Handler


class FrameStream:
    """Owns the HTTP server and the latest frame. One per process.

    `offer()` is what the detector calls; it is cheap (a memcpy and a notify)
    and it never raises, because a broken video feed must not be able to stop a
    robot's obstacle reporting.
    """

    def __init__(
        self,
        host: str,
        port: int,
        hz: float,
        quality: int,
        scale: float,
    ) -> None:
        self.host = host
        self.port = port
        self.quality = quality
        self.scale = scale
        # The detector ticks at 10 Hz; the viewer does not need 10 Hz of JPEG.
        # Decimating here rather than at the encoder means the skipped frames
        # cost nothing at all, not just no encode.
        self._min_interval = 1.0 / hz if hz > 0 else 0.0
        self._next_at = 0.0
        self._latest = _Latest()
        self._server = None  # type: Any
        self._thread = None  # type: Optional[threading.Thread]
        self.running = False

    def start(self) -> None:
        """Bind and serve. A failure here is logged, not raised.

        The camera and the `/c3po/objects` heartbeat are the container's job;
        the picture is a convenience on top. A port already in use must not be
        able to take the detector down with it.
        """
        try:
            from http.server import ThreadingHTTPServer

            handler = _handler_class(self._latest, self.quality, self.scale)
            self._server = ThreadingHTTPServer((self.host, self.port), handler)
            # Otherwise `docker stop` waits on live MJPEG connections and turns
            # into a 10 s SIGKILL.
            self._server.daemon_threads = True
            self._thread = threading.Thread(
                target=self._server.serve_forever,
                kwargs={"poll_interval": _WAKE_S},
                daemon=True,
                name="c3po-vision-stream",
            )
            self._thread.start()
            self.running = True
            _log(
                "ready",
                url="http://{}:{}/stream.mjpg".format(self.host, self.port),
                hz=round(1.0 / self._min_interval, 1) if self._min_interval else "tick",
                quality=self.quality,
                scale=self.scale,
                bind="loopback" if self.host in ("127.0.0.1", "localhost") else self.host,
            )
        except Exception as exc:
            _log(
                "start.failed",
                host=self.host,
                port=self.port,
                error=repr(exc),
                consequence="no operator video; detection and the world model are unaffected",
            )
            self.running = False

    def offer(self, frame: Any, stamp: Optional[float] = None) -> None:
        """Hand over a colour frame from a tick that SUCCEEDED. Never raises."""
        if not self.running or frame is None:
            return
        now = time.time() if stamp is None else stamp
        if now < self._next_at:
            return
        self._next_at = now + self._min_interval
        try:
            self._latest.offer(frame, now)
        except Exception as exc:  # a video bug must not stop the detector
            _log("offer.failed", error=repr(exc))

    def status(self) -> Dict[str, Any]:
        return self._latest.status(time.time())

    def close(self) -> None:
        if not self.running:
            return
        self.running = False
        self._latest.close()
        try:
            self._server.shutdown()
            self._server.server_close()
        except Exception:
            pass
        _log("stopped", frames=self._latest.offered)


def test_pattern(width: int, height: int, phase: int) -> RawFrame:
    """A moving synthetic colour frame, with no numpy and no camera.

    `detector.py`'s synthetic mode exists so the DDS crossing can be brought up
    with no sensors; this is the same idea for the video path, and it is what
    makes the console's live view testable on a laptop. The pattern MOVES —
    a static image cannot tell you whether frames are arriving or one frame is
    being redisplayed, which is precisely the failure this whole module is
    careful about.

    Built once per size and then rolled by a byte offset: composing 900 KB of
    pixels in a Python loop every tick would cost more than the JPEG encode.
    """
    row = bytearray()
    for x in range(width):
        # A horizontal ramp, so a shifted copy is visibly shifted.
        row += bytes((x * 255 // max(1, width - 1), 64, 255 - (x * 255 // max(1, width - 1))))
    stripe = bytes(row)
    shift = (phase * 12 % width) * 3
    rolled = stripe[shift:] + stripe[:shift]
    return RawFrame(width=width, height=height, rgb=rolled * height)


def from_env(getenv: Callable[[str, str], str]) -> Optional["FrameStream"]:
    """Build a FrameStream from C3PO_VISION_STREAM* env, or None if disabled.

    Takes `getenv` rather than reading os.environ so the Stage 0 suite can drive
    it without mutating the process environment.
    """
    if getenv("C3PO_VISION_STREAM", "0").strip().lower() not in ("1", "true", "yes", "on"):
        return None
    return FrameStream(
        host=getenv("C3PO_VISION_STREAM_HOST", "127.0.0.1"),
        port=int(getenv("C3PO_VISION_STREAM_PORT", "8081")),
        hz=float(getenv("C3PO_VISION_STREAM_HZ", "5")),
        quality=int(getenv("C3PO_VISION_STREAM_QUALITY", "75")),
        scale=float(getenv("C3PO_VISION_STREAM_SCALE", "1.0")),
    )
