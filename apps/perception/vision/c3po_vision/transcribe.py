"""Whisper on the GPU, in the container that already has CUDA.

WHY HERE AND NOT IN THE BRIDGE. The bridge ran faster-whisper on the CPU and it
was slow — 3.5-6.6 s for a short utterance, measured warm. Three reasons, and
only one of them was Whisper's fault:

  * the PyPI ctranslate2 aarch64 wheel is compiled WITHOUT CUDA ("This
    CTranslate2 package was not compiled with CUDA support"), so the GPU was
    never reachable from there at all;
  * the Jetson's 8 cores are shared with the co-tenant's SLAM, and the machine
    was at load 25 during the benchmark;
  * meanwhile this container holds CUDA 11.4 and TensorRT, and the GPU sits at
    roughly 0-5 percent.

Moving the work here also restores D6.2's intent, which the CPU version had
quietly broken: ML dependencies stay OUT of the process that owns
`stop_everything`. The bridge keeps vosk — 39 MB, CPU, streaming, and the stop
phrase depends on it — and loses ctranslate2, onnxruntime and av entirely.

WHY whisper.cpp AND NOT faster-whisper. This image is Python 3.8, because
JetPack 5's TensorRT bindings hard-depend on `python3 (<< 3.9)`. faster-whisper
requires 3.9+, so it cannot be installed here at all. whisper.cpp is C++ with a
CLI, so the interpreter version is irrelevant — the same reason Piper is a
binary rather than a package.

THE MODEL IS NOT IN THE IMAGE, for the same reason the TensorRT plan is not:
it is large, it changes far less often than the code, and a rebuild should not
cost a re-download. It is bind-mounted from ~/.c3po/models like the ONNX.
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import tempfile
import time
from typing import Any

WHISPER_BIN = os.environ.get("WHISPER_BIN", "/opt/c3po/whisper/whisper-cli")
WHISPER_MODEL = os.environ.get(
    "WHISPER_MODEL", "/opt/c3po/models/ggml-base.bin")
# Spanish, pinned. Autodetect on a short noisy clip is how a Spanish utterance
# comes back confidently transcribed as Portuguese.
WHISPER_LANG = os.environ.get("WHISPER_LANG", "es")
# Beam 1: greedy. On short command utterances the beam buys little and costs
# latency, which is the whole reason this moved to the GPU.
WHISPER_BEAM = int(os.environ.get("WHISPER_BEAM", "1"))
WHISPER_TIMEOUT_S = float(os.environ.get("WHISPER_TIMEOUT_S", "30"))

SAMPLE_RATE = 16000
BYTES_PER_SECOND = SAMPLE_RATE * 2


class TranscribeUnavailable(RuntimeError):
    """whisper.cpp or its model is missing. Carries the fix, not just the fault."""


def available() -> tuple[bool, str]:
    if not os.path.isfile(WHISPER_BIN):
        return False, f"whisper.cpp not built at {WHISPER_BIN} — rebuild the vision image"
    if not os.path.isfile(WHISPER_MODEL):
        return False, (
            f"no model at {WHISPER_MODEL} — fetch ggml-base.bin into "
            "~/.c3po/models on the robot (it is bind-mounted, not baked in)")
    return True, ""


def _wav_bytes(pcm: bytes) -> bytes:
    """Wrap raw PCM in a WAV header.

    whisper.cpp's CLI reads files, not stdin, and it wants a real RIFF header —
    handing it headerless PCM produces a confident transcript of noise rather
    than an error, which is the worst failure shape available here.
    """
    n = len(pcm)
    return (
        b"RIFF" + struct.pack("<I", 36 + n) + b"WAVE"
        + b"fmt " + struct.pack("<IHHIIHH", 16, 1, 1, SAMPLE_RATE,
                                SAMPLE_RATE * 2, 2, 16)
        + b"data" + struct.pack("<I", n) + pcm
    )


def transcribe(pcm: bytes) -> dict[str, Any]:
    """16 kHz mono 16-bit PCM -> {text, seconds, ms, device}.

    Returns an empty string rather than raising when nothing was said. Whisper
    is known to invent text on silence, and a confident sentence produced from
    an empty room would be acted on by the agent as if somebody had spoken.
    """
    ok, why = available()
    if not ok:
        raise TranscribeUnavailable(why)
    if not pcm:
        return {"text": "", "seconds": 0.0, "ms": 0, "device": "gpu"}

    started = time.time()
    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as fh:
        fh.write(_wav_bytes(pcm))
        wav_path = fh.name
    try:
        proc = subprocess.run(
            [WHISPER_BIN, "-m", WHISPER_MODEL, "-f", wav_path,
             "-l", WHISPER_LANG, "-bs", str(WHISPER_BEAM),
             "-oj", "-of", wav_path[:-4],      # JSON out, alongside the wav
             "-nt"],                            # no timestamps in the text
            capture_output=True, timeout=WHISPER_TIMEOUT_S, check=False)
        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", "replace")[:300]
            raise TranscribeUnavailable(
                f"whisper.cpp exited {proc.returncode}: {stderr}")

        json_path = wav_path[:-4] + ".json"
        text = ""
        if os.path.isfile(json_path):
            with open(json_path, "r") as jf:
                doc = json.load(jf)
            text = " ".join(
                seg.get("text", "").strip()
                for seg in doc.get("transcription", [])).strip()
            os.unlink(json_path)
        else:
            # Fall back to stdout if -oj did not land. Not an error: some builds
            # print the transcript and write no file.
            text = proc.stdout.decode("utf-8", "replace").strip()

        return {
            "text": text,
            "seconds": round(len(pcm) / BYTES_PER_SECOND, 2),
            "ms": int((time.time() - started) * 1000),
            "device": "gpu",
        }
    finally:
        try:
            os.unlink(wav_path)
        except OSError:
            pass


def status() -> dict[str, Any]:
    ok, why = available()
    return {
        "available": ok,
        "reason": why or None,
        "model": os.path.basename(WHISPER_MODEL),
        "language": WHISPER_LANG,
    }


# --- standalone server ------------------------------------------------------
#
# TRANSCRIPTION MUST NOT REQUIRE THE CAMERA. The `/transcribe` route is served
# by the detector's HTTP server, and the detector opens the RealSense — so
# serving speech-to-text would claim a sensor away from the co-tenant for no
# reason. Speech needs the GPU, not the camera.
#
# This entry point serves ONLY /transcribe and /transcribe/status, opens no
# device, and holds nothing:
#
#     docker run --runtime nvidia ... python3 -m c3po_vision.transcribe
#
# It binds loopback by default. The bridge is the only client, both run
# --network host on the same Jetson, and this endpoint accepts a POST body —
# it has no business being reachable from the school Wi-Fi.

def serve(host: str = "127.0.0.1", port: int = 8082) -> None:
    import json as _json
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.0"
        server_version = "c3po-stt"

        def log_message(self, fmt: str, *args: Any) -> None:
            pass          # one line per utterance is the detector's job, not ours

        def _json(self, code: int, payload: dict) -> None:
            body = _json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self) -> None:
            if self.path.rstrip("/") in ("/transcribe/status", "/status"):
                return self._json(200, status())
            self.send_error(404, "not a c3po stt endpoint")

        def do_POST(self) -> None:
            if self.path.rstrip("/") != "/transcribe":
                self.send_error(404, "not a c3po stt endpoint")
                return
            try:
                length = int(self.headers.get("Content-Length") or 0)
            except ValueError:
                self.send_error(400, "bad Content-Length")
                return
            # 120 s of audio is far past any utterance and still only ~3.8 MB.
            # The body is read into memory, so it needs a bound.
            if length <= 0 or length > 120 * BYTES_PER_SECOND:
                self.send_error(400, "expected 1..120s of 16kHz mono 16-bit PCM")
                return
            pcm = self.rfile.read(length)
            if len(pcm) != length:
                self.send_error(400, "short read")
                return
            try:
                return self._json(200, transcribe(pcm))
            except Exception as exc:  # noqa: BLE001 - reported, never raised at a client
                # 503, not 500: "no model installed" is a state the caller can
                # act on, and the bridge degrades to its segmenter rather than
                # losing the utterance.
                return self._json(503, {"error": str(exc)[:300]})

    ok, why = available()
    # Plain concatenation, not a nested f-string: this image is Python 3.8 and
    # nested quotes inside an f-string are a 3.12 feature.
    state = "ready" if ok else "DEGRADED - " + why
    print("c3po stt: " + state, flush=True)
    print(f"c3po stt: listening on http://{host}:{port}/transcribe", flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    serve(os.environ.get("C3PO_STT_HOST", "127.0.0.1"),
          int(os.environ.get("C3PO_STT_PORT", "8082")))
