"""The colour frame, as MJPEG over HTTP. The operator console's live view.

    GET /stream.mjpg   multipart/x-mixed-replace   what an <img> renders
    GET /frame.jpg     one JPEG                    for a poll or a screenshot
    GET /status        JSON                        how old the last frame is
    GET /depth_box     JSON                        depth inside a box of a /frame.jpg

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
import socket
import sys
import threading
import time
import urllib.parse
from collections import deque, namedtuple
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

#: Socket timeout on a client connection. THE FIX FOR A STREAM THAT REPORTS
#: HEALTHY AND SENDS NOTHING.
#:
#: Observed on the robot 2026-08-29: `clients` climbed 0 -> 12 -> 14 across an
#: evening of page reloads and never came down, and at 14 the stream accepted
#: connections and delivered ZERO BYTES IN FIVE SECONDS while `/status` still
#: said `live: true`. The headset showed SIN IMAGEN while every check said the
#: camera was fine.
#:
#: The accounting was never wrong. `_stream` increments on entry and
#: decrements in a `finally`, and catches BrokenPipeError — but a browser that
#: navigates away without closing cleanly leaves a HALF-OPEN connection, and a
#: write to one of those does not raise. It BLOCKS, until the OS gives up
#: minutes later. So the `finally` never runs, the thread never exits, and the
#: count only ever goes up.
#:
#: This container is pinned to a single core (`--cpuset-cpus 5` in
#: `perception_up`), so a dozen threads parked in `write()` are not free: they
#: contend with the JPEG encoder on the one CPU the detector also needs.
#:
#: Five seconds is far longer than a loopback write to a live peer and far
#: shorter than the kernel's default. It applies to reads too, which is
#: harmless: HTTP/1.0 means one request per connection, and the only sizeable
#: read is `/transcribe`'s PCM upload on loopback.
CLIENT_TIMEOUT_S = 5.0

#: Concurrent MJPEG clients. Beyond this, refuse and SAY SO.
#:
#: Defence in depth behind the timeout. Even with dead peers now cleaned up, a
#: cap turns "the stream mysteriously stopped serving" into a 503 that names
#: the reason — and this feed has exactly one legitimate consumer at a time
#: (the console, or the headset), so four is already generous.
MAX_STREAM_CLIENTS = 4

# How long a blocked MJPEG writer waits on the condition before re-checking
# whether the server is shutting down. Only affects `docker stop` latency.
_WAKE_S = 0.25

# A colour frame with no numpy: (width, height, packed RGB bytes). This is what
# the synthetic mode produces, so the whole HTTP path — encode, multipart,
# browser — can be exercised on a machine with no camera and no numpy.
RawFrame = namedtuple("RawFrame", ["width", "height", "rgb"])

#: Depth frames kept, newest last, so /depth_box can answer for the frame a client
#: fetched from /frame.jpg after a newer one arrived (5 Hz: ~0.8 s of history).
DEPTH_KEEP = 4

#: At most this many depth pixels are read per side of a box: a 200 px face box is
#: read every 4th pixel, ~2.5k values, which pure Python sorts in about a millisecond.
DEPTH_BOX_SAMPLES = 50


def _log(event: str, **fields: Any) -> None:
    """Same one-line stderr format as detector.log, duplicated on purpose.

    Importing detector.log here would make `import c3po_vision.stream` pull in
    the detector's module-scope env parsing, which is exactly what the Stage 0
    suite must be able to avoid.
    """
    parts = [f"{k}={v}" for k, v in sorted(fields.items())]
    sys.stderr.write("[stream] {} {}\n".format(event, " ".join(parts)))
    sys.stderr.flush()


def depth_box_stats(depth: Any, box: Tuple[int, int, int, int], depth_scale: float) -> Dict[str, Any]:
    """Median depth, relief (p90 - p10) and valid share inside `box` of a depth frame.

    `depth` is rows of raw units (numpy HxW or a list of lists), 0 = no reading;
    `box` is (x0, y0, x1, y1) in its pixels. Pure Python so the laptop suite runs it.
    Raises ValueError on a box with no pixels inside the frame.
    """
    height, width = len(depth), len(depth[0])
    x0, x1 = max(0, box[0]), min(width, box[2])
    y0, y1 = max(0, box[1]), min(height, box[3])
    if x1 <= x0 or y1 <= y0:
        raise ValueError("box is empty or outside the frame")
    step = max(1, max(x1 - x0, y1 - y0) // DEPTH_BOX_SAMPLES)
    raw = [int(v) for row in depth[y0:y1:step] for v in row[x0:x1:step]]
    valid = sorted(v for v in raw if v > 0)
    out = {"valid_frac": round(len(valid) / len(raw), 3), "median_m": None, "relief_m": None}
    if valid:
        def q(p: float) -> float:
            return valid[min(len(valid) - 1, int(p * len(valid)))] * depth_scale

        out["median_m"] = round(q(0.5), 4)
        out["relief_m"] = round(q(0.9) - q(0.1), 4)
    return out


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
        # (stamp, depth, fx, depth_scale), sharing the stamp of the colour frame offered with it.
        self._depths = deque(maxlen=DEPTH_KEEP)  # type: Any
        self.offered = 0
        self.clients = 0
        self.closed = False

    def offer(
        self,
        frame: Any,
        stamp: float,
        depth: Any = None,
        fx: Optional[float] = None,
        depth_scale: float = 0.001,
    ) -> None:
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
            if depth is not None and fx:
                # A copy for the same reason as the colour frame.
                self._depths.append((stamp, depth.copy(), float(fx), depth_scale))
            self.offered += 1
            self._cv.notify_all()

    def close(self) -> None:
        with self._cv:
            self.closed = True
            self._cv.notify_all()

    def snapshot(self) -> Tuple[int, Any, float]:
        with self._cv:
            return self._seq, self._frame, self._stamp

    def depth_at(self, stamp: Optional[float]) -> Optional[Tuple[float, Any, float, float]]:
        """The depth offered with the colour frame of this stamp (None: the newest), or None."""
        with self._cv:
            for entry in reversed(self._depths):
                if stamp is None or entry[0] == stamp:
                    return entry
        return None

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
            # SATURATION IS A STATE THE CONSOLE MUST BE ABLE TO SEE.
            #
            # On 2026-08-29 this endpoint reported `live: true` with 14 clients
            # while serving zero bytes, and there was no field that said so —
            # so every check said the camera was healthy while the headset
            # showed nothing. `clients` alone did not help: nobody knows what
            # number is too many. These two say it outright.
            "clients_max": MAX_STREAM_CLIENTS,
            "at_capacity": clients >= MAX_STREAM_CLIENTS,
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

        def setup(self) -> None:
            # The one line that stops a vanished browser parking a thread in
            # `write()` forever. See CLIENT_TIMEOUT_S.
            super().setup()
            try:
                self.connection.settimeout(CLIENT_TIMEOUT_S)
            except OSError:  # pragma: no cover - a socket that is already gone
                pass

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
            if path == "/depth_box":
                return self._depth_box()
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

        def _json(self, code: int, payload: Dict[str, Any]) -> None:
            body = json.dumps(payload).encode("ascii")
            if code == 200:
                self._headers("application/json", {"Content-Length": str(len(body))})
            else:
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
            self.wfile.write(body)

        def _depth_box(self) -> None:
            """GET /depth_box?x0=&y0=&x1=&y1=[&stamp=] — the bridge's face-liveness check.

            The box is in /frame.jpg's pixels (so C3PO_VISION_STREAM_SCALE is undone
            here) and `fx` is returned in them too. `stamp` is /frame.jpg's
            X-Frame-Stamp: the answer is for THAT frame's depth or a 503, never
            another moment's.
            """
            query = urllib.parse.parse_qs(self.path.partition("?")[2])
            try:
                x0, y0, x1, y1 = (float(query[k][0]) for k in ("x0", "y0", "x1", "y1"))
                stamp = float(query["stamp"][0]) if "stamp" in query else None
            except (KeyError, ValueError):
                return self._json(400, {"error": "need numeric x0, y0, x1, y1 (and optional stamp)"})
            entry = latest.depth_at(stamp)
            if entry is None:
                return self._json(503, {"error": "no depth for that frame"})
            at, depth, fx, depth_scale = entry
            box = (round(x0 / scale), round(y0 / scale), round(x1 / scale), round(y1 / scale))
            try:
                stats = depth_box_stats(depth, box, depth_scale)
            except ValueError as exc:
                return self._json(400, {"error": str(exc)})
            self._json(200, {"v": 1, "stamp": at, "fx": fx * scale, **stats})

        def _frame(self) -> None:
            seq, frame, stamp = latest.snapshot()
            if seq == 0 or frame is None:
                # 503, not 404: the endpoint exists and the camera has simply
                # not produced anything yet. 404 would read as a wrong URL.
                self.send_error(503, "no frame yet")
                return
            data = latest.jpeg(seq, frame, quality, scale)
            # The key /depth_box takes, so a client can ask for this frame's depth.
            self._headers(
                "image/jpeg", {"Content-Length": str(len(data)), "X-Frame-Stamp": repr(stamp)}
            )
            self.wfile.write(data)

        def _stream(self) -> None:
            # CLAIM THE SLOT BEFORE SENDING ANY HEADERS. Sending 200 and the
            # multipart content-type and THEN discovering we cannot serve is
            # the failure this endpoint already had: a socket that opens,
            # announces a picture, and delivers nothing. A 503 before the
            # headers is a refusal the client can read.
            with latest._cv:
                at_capacity = latest.clients >= MAX_STREAM_CLIENTS
                if not at_capacity:
                    latest.clients += 1
            if at_capacity:
                self.send_error(
                    503,
                    f"too many stream clients (max {MAX_STREAM_CLIENTS})",
                )
                return

            # INSIDE the try, not before it. `_headers` ends in `end_headers()`,
            # which flushes to the socket and raises BrokenPipeError /
            # ConnectionResetError if the client went away in the window
            # between claiming the slot above and writing the first byte. With
            # the call outside, that exception escaped before the `finally`
            # below was ever entered: the slot was taken and never given back.
            # Four of those and this endpoint answers 503 forever while
            # `/status` reports four clients that do not exist — which is the
            # same "reports healthy, serves nothing" failure the client cap was
            # added to end, reached by a different route.
            try:
                self._headers("multipart/x-mixed-replace; boundary=" + BOUNDARY)
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
                            f"--{BOUNDARY}\r\nContent-Type: image/jpeg\r\n"
                            f"Content-Length: {len(data)}\r\n\r\n"
                        ).encode("ascii")
                    )
                    self.wfile.write(data)
                    self.wfile.write(b"\r\n")
            except (BrokenPipeError, ConnectionResetError, socket.timeout):
                # The operator closed the tab, or walked away and left a
                # half-open socket. `socket.timeout` is the one that was
                # missing: without the timeout above it never arrived, and
                # without catching it here it would escape as an unhandled
                # error instead of being the ordinary end of a stream.
                pass
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
                url=f"http://{self.host}:{self.port}/stream.mjpg",
                hz=round(1.0 / self._min_interval, 1) if self._min_interval else "tick",
                quality=self.quality,
                scale=self.scale,
                bind="loopback" if self.host in ("127.0.0.1", "localhost") else self.host,
            )
        # BLIND EXCEPT, DELIBERATE: the operator's video is the LEAST important thing this
        # container does. A port already bound, a permission error, a socket
        # the kernel will not give us — none of them justify taking the
        # detector and the world model down with them, and the log says so.
        except Exception as exc:  # noqa: BLE001
            _log(
                "start.failed",
                host=self.host,
                port=self.port,
                error=repr(exc),
                consequence="no operator video; detection and the world model are unaffected",
            )
            self.running = False

    def offer(
        self,
        frame: Any,
        stamp: Optional[float] = None,
        depth: Any = None,
        fx: Optional[float] = None,
        depth_scale: float = 0.001,
    ) -> None:
        """Hand over a colour frame from a tick that SUCCEEDED, and its aligned depth. Never raises."""
        if not self.running or frame is None:
            return
        now = time.time() if stamp is None else stamp
        if now < self._next_at:
            return
        self._next_at = now + self._min_interval
        try:
            self._latest.offer(frame, now, depth, fx, depth_scale)
        except Exception as exc:  # noqa: BLE001 — a video bug must not stop the detector
            _log("offer.failed", error=repr(exc))

    def status(self) -> Dict[str, Any]:
        return self._latest.status(time.time())

    def close(self) -> None:
        if not self.running:
            return
        self.running = False
        self._latest.close()
        # Shutdown path, same reasoning as the detector's RealSense close():
        # the process is going away, and an error raised while tearing down the
        # video server would replace whatever we were actually shutting down
        # for. `stopped` still gets logged below either way.
        try:
            self._server.shutdown()
            self._server.server_close()
        except Exception:  # noqa: BLE001, S110
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


def from_env(getenv: Callable[[str, str], str]) -> Optional[FrameStream]:
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
