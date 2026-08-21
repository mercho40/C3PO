"""The spoken stop, and the audio sources that feed it.

D6.2's split is a SAFETY property, not a layout preference: the stop phrase runs
**in the bridge**, in the same process that owns `stop_everything`, and nothing
else about voice does. VAD, wake word and transcription belong to a separate
voice process. If that process dies, hangs, or is mid-rebuild, the spoken stop
must still work — so it never crosses a process boundary to reach the thing it
triggers. Multicast is what makes this free: the mic is a group, not a device,
so two processes can join it independently and neither can starve the other.

D6.3 replaced the trained openWakeWord model with **Vosk plus a restricted
grammar**, which is what removed the H100 from the critical path — changing the
stop word becomes editing a list, not retraining. The wheel is 2.4 MB and the
model 39 MB, CPU-only, no torch: heavier than D6.2's "~1 MB ONNX" but still not
an ML stack, so the split survives the substitution.

FALSE POSITIVES ARE CHEAP AND FALSE NEGATIVES ARE NOT. A stop that fires when
nobody asked costs a halted robot and an apology. A stop that misses costs
whatever the robot was about to do to the person in front of it, and D6.1 is
blunt about it: a spoken stop that works in demos and not in panic is worse than
none, because people rely on it. Everything here is therefore biased toward
firing — several phrases, partial results accepted, no confidence gate.

PARTIAL RESULTS ARE THE POINT. Vosk only emits a final result at an utterance
boundary, which is silence — so waiting for one means waiting for the person to
stop shouting before the robot reacts. `feed()` checks partials too, which is
what makes it fire mid-word instead of after.

THE MICROPHONE IS NOT PROVEN. `239.168.123.161:5555` does not stream at rest and
has no software trigger (ROBOT-HARDWARE.md §8.2). This module is written so that
is a SOURCE problem, not a design problem: `detect_in_pcm()` takes bytes from
anywhere, and the test suite drives it with Piper-synthesised speech, so the
recogniser is verifiable today and the mic is a swap when it unblocks.
"""

from __future__ import annotations

import json
import os
import socket
import threading
import time
import struct
from collections import deque
from collections.abc import Callable, Iterator
from typing import Any

import structlog

log = structlog.get_logger(__name__)

MIC_GROUP = os.environ.get("C3PO_MIC_GROUP", "239.168.123.161")
MIC_PORT = int(os.environ.get("C3PO_MIC_PORT", "5555"))
# The feed lives on the robot's internal wired LAN. Binding INADDR_ANY lets the
# kernel pick wlan0 or docker0 and yields zero packets with NO error — the
# single most expensive mistake available here, because silence looks like a
# gated microphone rather than a wrong interface.
MIC_IFACE_PREFIX = "192.168.123."
MIC_SAMPLE_RATE = 16000
# 16 kHz mono 16-bit. Used to turn a buffer length into seconds.
TARGET_BYTES_PER_S = MIC_SAMPLE_RATE * 2

VOSK_MODEL = os.environ.get(
    "VOSK_MODEL", os.path.expanduser("~/.local/share/vosk/vosk-model-small-es-0.42")
)

# CHOSEN FROM MEASURED DECODES, not from what reads well. Every obvious
# candidate failed on this model (measured 2026-08-21, synthesised es_AR):
#
#     "pará"     -> "para"      the commonest preposition in Spanish
#     "alto"     -> "alta"
#     "stop"     -> "sí"
#     "basta ya" -> "las tasas"
#     "frená"    -> not in the model's vocabulary at all
#
# So a single "pará" cannot be told from "para ayudarte", and D6.3's own warning
# — decide the phrase for separability, not charm — rules it out. What survived:
# "emergencia" decodes exactly, and the doubled form "pará pará" -> "para para"
# is stable and does not occur in ordinary speech.
#
# THIS LIST IS NOT VALIDATED FOR SAFETY USE. It is separable on synthesised
# speech; the case that matters is a stressed human, and that needs recordings.
DEFAULT_STOP_PHRASES = ["emergencia", "para para", "detener"]

# 30 s of 16 kHz mono 16-bit — Whisper's own window. Past this a continuous
# talker would grow the buffer unboundedly AND get nothing back until they
# paused, so the loop cuts and transcribes instead.
MAX_UTTERANCE_BYTES = 30 * 16000 * 2

# 16-bit mono at 16 kHz: 0.125 s. Small enough that stop latency is dominated by
# the decoder rather than by buffering.
FRAME_BYTES = 4000


class ListenUnavailable(RuntimeError):
    """Vosk or its model is missing. Carries the fix."""


def available() -> tuple[bool, str]:
    try:
        import vosk  # noqa: F401
    except ImportError:
        return False, ("vosk is not installed — add it to apps/bridge and `uv sync` on the robot")
    if not os.path.isdir(VOSK_MODEL):
        return False, (
            f"vosk model not found at {VOSK_MODEL} — see apps/bridge/scripts/install_vosk.sh"
        )
    return True, ""


def _grammar(phrases: list[str]) -> str:
    """Vosk wants a JSON array of allowed phrases, `[unk]` included."""
    return json.dumps([*phrases, "[unk]"], ensure_ascii=False)


def match_phrase(text: str, phrases: list[str]) -> str | None:
    """Does `text` contain any stop phrase? Returns which one, or None.

    SUBSTRING, NOT EQUALITY, and that is a safety choice rather than laziness.
    Partial results arrive as fragments, and Vosk returns space-separated tokens,
    so a real shout produces "eh pará", "pará pará", or "para" mid-decode. Exact
    matching would reject all three. Over-matching costs a robot that stopped
    when it did not strictly need to; under-matching costs the thing D6.1 says is
    worse than having no spoken stop at all.

    Module-level so it is testable without vosk installed — the Mac has no wheel.
    """
    low = (text or "").lower()
    for phrase in phrases:
        if phrase and phrase in low:
            return phrase
    return None


class StopPhraseDetector:
    """Restricted-grammar recogniser that answers one question: stop or not.

    Deliberately not a general transcriber. The search space is a handful of
    phrases, which is why published open-vocabulary WER (16 % for this model)
    says nothing about accuracy here — that number is for transcribing anything,
    and this decodes among six options plus "something else".
    """

    def __init__(
        self, phrases: list[str] | None = None, sample_rate: int = MIC_SAMPLE_RATE
    ) -> None:
        ok, why = available()
        if not ok:
            raise ListenUnavailable(why)
        from vosk import KaldiRecognizer, Model

        self.phrases = [p.lower() for p in (phrases or DEFAULT_STOP_PHRASES)]
        # FULL VOCABULARY, not a restricted grammar, and this reverses D6.3's
        # assumption on measured evidence. A grammar of allowed phrases must map
        # every acoustic segment onto one of them, and `[unk]` does not reliably
        # absorb the rest: a friendly sentence about helping with the project
        # decoded as "pará | stop stop para alto" — the robot would halt
        # whenever anyone spoke near it, which is not a cheap false positive but
        # an unusable one. Full decoding renders that same sentence as ordinary
        # words, so the phrase match sees what was actually said.
        self._rec = KaldiRecognizer(Model(VOSK_MODEL), sample_rate)
        self._rec.SetWords(False)

    def _hit(self, text: str) -> str | None:
        return match_phrase(text, self.phrases)

    def feed(self, pcm: bytes) -> str | None:
        """Feed one frame. Returns the phrase heard, or None.

        Both result kinds are checked, and partials first: a final result only
        appears at an utterance boundary, i.e. after the speaker has stopped, and
        a stop that waits for silence is a stop that arrives too late.
        """
        if self._rec.AcceptWaveform(pcm):
            hit = self._hit(json.loads(self._rec.Result()).get("text", ""))
            if hit:
                return hit
        partial = json.loads(self._rec.PartialResult()).get("partial", "")
        return self._hit(partial)

    def reset(self) -> None:
        self._rec.Reset()


def detect_in_pcm(
    pcm: bytes, phrases: list[str] | None = None, frame_bytes: int = FRAME_BYTES
) -> str | None:
    """Run the detector over a whole buffer. The offline entry point.

    This is what makes the stop phrase testable with no microphone: feed it
    Piper-synthesised speech, or a recording of a real person, and assert the
    phrase fires. Every regression test for the safety-critical path goes
    through here.
    """
    det = StopPhraseDetector(phrases=phrases)
    for off in range(0, len(pcm), frame_bytes):
        hit = det.feed(pcm[off : off + frame_bytes])
        if hit:
            return hit
    return None


class Transcriber:
    """Continuous Spanish speech -> text. Full vocabulary, streaming.

    Separate from StopPhraseDetector on purpose, even though both wrap the same
    model: the stop detector is safety-critical and must stay in the bridge
    (D6.2), while general transcription is what the voice process will do with
    the same multicast group. Two objects, two recognisers, one model on disk.
    """

    def __init__(self, sample_rate: int = MIC_SAMPLE_RATE) -> None:
        ok, why = available()
        if not ok:
            raise ListenUnavailable(why)
        from vosk import KaldiRecognizer, Model

        self._rec = KaldiRecognizer(Model(VOSK_MODEL), sample_rate)

    def feed(self, pcm: bytes) -> str | None:
        """One frame in; a FINAL utterance out, or None while still listening.

        Only finals are returned. Partials matter for the stop phrase, where
        latency is the whole point, and are noise here — an agent should act on
        what someone finished saying, not on the first syllable of it.
        """
        if self._rec.AcceptWaveform(pcm):
            text = json.loads(self._rec.Result()).get("text", "").strip()
            return text or None
        return None

    def flush(self) -> str | None:
        """Whatever is still buffered, at end of stream. Needed for finite
        sources (a file, a synthesised clip) whose last utterance never gets the
        trailing silence that would close it."""
        text = json.loads(self._rec.FinalResult()).get("text", "").strip()
        return text or None


def transcribe_pcm(pcm: bytes, frame_bytes: int = FRAME_BYTES) -> list[str]:
    """Transcribe a complete buffer. The offline entry point, mirroring
    `detect_in_pcm` — this is what makes the loop testable with no microphone."""
    t = Transcriber()
    out = [
        text
        for off in range(0, len(pcm), frame_bytes)
        if (text := t.feed(pcm[off : off + frame_bytes]))
    ]
    if tail := t.flush():
        out.append(tail)
    return out


# WHISPER IS NOT A BLANKET UPGRADE, AND THE MEASUREMENTS SAY SO.
# Benchmarked on this Jetson, warm (model already loaded), synthesised es_AR:
#
#     clip                       vosk            whisper
#     "Para."            0.66s   ~370 ms  cara   3.58 s  "!Bien!"      <- wrong
#     "Camina hasta      1.35s   ~370 ms  ok     4.00 s  "caminaste
#      la puerta."                                        la puerta."   <- wrong
#     long sentence      3.53s   ~370 ms  ok     6.64 s  ok
#
# Two things fall out. Whisper is 10-18x SLOWER here, and it is WORSE on short
# utterances -- it is trained on 30 s windows and mangles a one-word clip. Short
# utterances are exactly what commands to a robot look like, so routing
# everything through it would make the common case both slower and less correct.
#
# So: SHORT UTTERANCES KEEP VOSK'S TEXT, long ones go to Whisper, where its
# better language modelling and punctuation actually pay for the latency.
#
# What this benchmark CANNOT settle is the case that motivated the swap: Vosk
# rendered live far-field mic audio as "guapa dias" for "buenos dias". Piper's
# audio is clean, level and unhurried, so it does not test noise robustness at
# all. If Whisper wins there, this threshold is the thing to lower.
WHISPER_MIN_SECONDS = float(os.environ.get("WHISPER_MIN_SECONDS", "2.0"))

WHISPER_MODEL = os.environ.get("WHISPER_MODEL", "base")
# INT8 halves memory for well under a point of WER, and the Orin NX has no spare
# RAM to spend: 15 GB shared between us, the detector and another team's SLAM.
WHISPER_COMPUTE = os.environ.get("WHISPER_COMPUTE", "int8")
# CPU, not CUDA. ctranslate2 on GPU needs cuDNN, which is not in this venv and
# would drag the voice path into the ~10 GB vision container to transcribe
# five-second utterances (D6.3). The GPU is free; this is not why it is free.
WHISPER_DEVICE = os.environ.get("WHISPER_DEVICE", "cpu")


# The vision container's HTTP surface. Same host — both run --network host on
# the Jetson — so this is a loopback call, not a network hop.
VISION_HOST = os.environ.get("C3PO_VISION_STREAM_HOST", "127.0.0.1")
VISION_PORT = int(os.environ.get("C3PO_VISION_STREAM_PORT", "8081"))
# Generous: it covers a cold CUDA context on the first call after the container
# starts. A tight timeout here turns a slow first utterance into a lost one.
VISION_TIMEOUT_S = float(os.environ.get("C3PO_VISION_STT_TIMEOUT_S", "30"))


class RemoteWhisper:
    """Transcription over HTTP, on the GPU, in the vision container.

    WHY THE WORK MOVED OUT OF THIS PROCESS. faster-whisper here ran on the CPU
    and could not do otherwise: the PyPI ctranslate2 aarch64 wheel is compiled
    WITHOUT CUDA, so the GPU was unreachable from Python on this machine. It
    took 3.5-6.6 s for a short utterance on eight cores shared with the
    co-tenant's SLAM, while the GPU idled at 0-5 percent.

    Moving it also restores something D6.2 asked for and the CPU version had
    quietly broken: ML dependencies stay OUT of the process that owns
    `stop_everything`. The bridge keeps vosk — small, CPU, streaming, and the
    stop phrase needs it — and shed ctranslate2, onnxruntime and av.

    DEGRADES, NEVER RAISES INTO THE LOOP. If the container is down or has no
    model, this returns "" and the caller keeps the segmenter's text. A missing
    GPU transcriber must cost transcript QUALITY, never the utterance.
    """

    def __init__(self, host: str = VISION_HOST, port: int = VISION_PORT) -> None:
        self._url = f"http://{host}:{port}/transcribe"

    def transcribe(self, pcm: bytes) -> str:
        if not pcm:
            return ""
        import json as _json
        import urllib.error
        import urllib.request

        req = urllib.request.Request(
            self._url, data=pcm, method="POST",
            headers={"Content-Type": "application/octet-stream"})
        try:
            with urllib.request.urlopen(req, timeout=VISION_TIMEOUT_S) as resp:
                payload = _json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            # 503 is the container saying "no model" — expected, not a fault.
            log.warning("listen.remote_whisper_unavailable",
                        code=exc.code, url=self._url)
            return ""
        except Exception as exc:
            log.warning("listen.remote_whisper_failed",
                        error=str(exc)[:200], url=self._url)
            return ""

        text = (payload.get("text") or "").strip()
        if text:
            log.info("listen.remote_whisper", ms=payload.get("ms"),
                     seconds=payload.get("seconds"))
        return text


def remote_whisper_status() -> dict[str, Any]:
    """Ask the vision container whether GPU transcription is ready."""
    import json as _json
    import urllib.request

    url = f"http://{VISION_HOST}:{VISION_PORT}/transcribe/status"
    try:
        with urllib.request.urlopen(url, timeout=3) as resp:
            return dict(_json.loads(resp.read().decode("utf-8")))
    except Exception as exc:
        return {"available": False, "reason": f"vision container unreachable: {exc}"}


class WhisperTranscriber:
    """Accurate Spanish transcription. Batch, not streaming — and that matters.

    Vosk decodes frame by frame and can answer mid-utterance; Whisper needs a
    whole utterance and returns nothing useful until it has one. They are not
    interchangeable, which is why both are here: Vosk keeps the stop phrase fast,
    Whisper makes the transcript worth acting on.

    The quality gap is the reason for the swap. `vosk-model-small-es` is 39 MB
    and built for keyword spotting; asked to transcribe, it rendered "buenos
    días" as "guapa días" on this robot. Whisper is the model D6.3 always named
    for STT.
    """

    def __init__(self, model_name: str | None = None) -> None:
        try:
            from faster_whisper import WhisperModel
        except ImportError as exc:
            raise ListenUnavailable(
                "faster-whisper is not installed — `uv sync --extra stt` on the robot"
            ) from exc

        self._model = WhisperModel(
            model_name or WHISPER_MODEL, device=WHISPER_DEVICE, compute_type=WHISPER_COMPUTE
        )

    def transcribe(self, pcm: bytes) -> str:
        """16 kHz mono 16-bit PCM -> Spanish text. Empty string if nothing said."""
        import numpy as np

        if not pcm:
            return ""
        # Whisper wants float32 in [-1, 1]; 32768 rather than 32767 so that
        # full-scale negative maps to exactly -1.0 and cannot overshoot.
        audio = np.frombuffer(pcm, dtype="<i2").astype(np.float32) / 32768.0

        # language pinned rather than detected: this deployment is Spanish, and
        # letting Whisper guess on a short noisy clip is how a Spanish utterance
        # comes back confidently transcribed as Portuguese.
        segments, _info = self._model.transcribe(audio, language="es", beam_size=1, vad_filter=True)
        return " ".join(seg.text.strip() for seg in segments).strip()


def transcribe_pcm_whisper(pcm: bytes, model_name: str | None = None) -> str:
    """One-shot Whisper transcription. The offline entry point."""
    return WhisperTranscriber(model_name).transcribe(pcm)


def build_whisperer() -> Any | None:
    """Pick a transcriber: GPU in the vision container first, CPU here second.

    PREFERRING REMOTE IS THE POINT. The local path cannot use the GPU on this
    machine at all — the ctranslate2 aarch64 wheel has no CUDA — so it is the
    fallback, not the default. It stays because a bridge running without the
    vision container should still transcribe, just slowly.

    Returns None if neither is available, and the caller then keeps the
    segmenter's text. Losing Whisper must cost transcript quality, never the
    utterance.
    """
    status = remote_whisper_status()
    if status.get("available"):
        log.info("listen.whisper_backend", backend="vision-container-gpu",
                 model=status.get("model"))
        return RemoteWhisper()

    try:
        whisperer = WhisperTranscriber()
        log.warning("listen.whisper_backend", backend="local-cpu",
                    reason=status.get("reason"),
                    note="the GPU path is in the vision container; is it running?")
        return whisperer
    except ListenUnavailable as exc:
        log.warning("listen.whisper_unavailable", reason=str(exc))
        return None


def listen_loop(
    frames: Iterator[bytes],
    on_text: Callable[[str], None] | None = None,
    on_stop: Callable[[str], None] | None = None,
    phrases: list[str] | None = None,
    whisper: bool = True,
) -> None:
    """Run both recognisers over one stream of PCM frames.

    `frames` is any iterator of 16 kHz mono 16-bit chunks — `iter_mic_frames()`
    in production, a file or synthesised audio in tests. That indirection is the
    point: the microphone is currently gated (ROBOT-HARDWARE.md §8.2), and
    keeping the source abstract means everything downstream of it is finished
    and exercised rather than waiting on hardware.

    THE STOP DETECTOR IS FED FIRST, and if it fires, `on_stop` runs before
    `on_text` is even considered for that frame. Transcription is best-effort;
    stopping is not, and it must never queue behind a slower consumer. With
    `whisper=True` that ordering stops being a formality — Whisper takes about a
    second per utterance, and the stop must not wait behind it.

    SEGMENTATION COMES FROM VOSK, FOR FREE. Whisper needs whole utterances and
    Vosk already finds their boundaries: it returns a final result exactly when
    speech stops. So the loop buffers audio, and when Vosk closes an utterance it
    hands that buffer to Whisper. No Silero, no energy threshold, no extra
    dependency, and one fewer thing to tune.

    `whisper=False` falls back to Vosk's own text — useful when faster-whisper is
    not installed, and noticeably worse: "buenos días" comes back as "guapa
    días", because a 39 MB keyword spotter is being asked to transcribe.
    """
    detector = StopPhraseDetector(phrases=phrases)
    transcriber = Transcriber()

    whisperer = build_whisperer() if whisper else None

    utterance = bytearray()

    for frame in frames:
        hit = detector.feed(frame)
        if hit:
            log.warning("listen.stop_phrase", phrase=hit)
            detector.reset()
            if on_stop is not None:
                on_stop(hit)

        if whisperer is not None:
            utterance.extend(frame)

        text = transcriber.feed(frame)
        if text:
            # Vosk closed an utterance: hand the buffered audio to Whisper and
            # emit ITS text instead. Vosk's own result is discarded here — it was
            # only ever the segmenter and the stop detector.
            if whisperer is not None:
                # Route by LENGTH. Below the threshold Whisper is both slower
                # and less accurate than the segmenter that already produced
                # `text`, so calling it would cost seconds to get a worse
                # answer. See WHISPER_MIN_SECONDS.
                if len(utterance) >= WHISPER_MIN_SECONDS * TARGET_BYTES_PER_S:
                    text = whisperer.transcribe(bytes(utterance)) or text
                utterance.clear()
            if on_text is not None:
                on_text(text)
        elif whisperer is not None and len(utterance) > MAX_UTTERANCE_BYTES:
            # Someone talking continuously never gives Vosk a boundary, so the
            # buffer would grow without limit and the first transcript would
            # arrive only when they stopped. Cut it, transcribe, carry on.
            better = whisperer.transcribe(bytes(utterance))
            utterance.clear()
            if better and on_text is not None:
                on_text(better)

    # FLUSH, and it is not an edge case. Vosk emits a final result only at an
    # utterance boundary — i.e. trailing silence — so a finite source (a file, a
    # synthesised clip, a mic stream that ends) leaves its LAST utterance, and
    # often its only one, sitting in the decoder. Without this the loop returns
    # an empty transcript for a perfectly good 2.5 s clip and looks like the
    # recogniser is broken. A live mic never reaches here, which is exactly why
    # this is easy to omit and hard to notice.
    tail = transcriber.flush()
    if whisperer is not None and utterance:
        tail = whisperer.transcribe(bytes(utterance)) or tail
    if tail and on_text is not None:
        on_text(tail)


# --- audio sources ----------------------------------------------------------
#
# ALWAYS-ON LISTENING IS A SOURCE PROBLEM, NOT A SOFTWARE ONE. The G1's built-in
# mic array lives on the control board and is published as a multicast group
# that only streams while somebody holds L1+L2 (§8.2). There is no RPC to open
# it: `vui_service` exposes playback, volume and LEDs and no capture function at
# all. Nothing in this process can press that button.
#
# So the listener takes its frames from whichever of these is available, and
# every one of them feeds the identical recogniser:
#
#   alsa       a USB microphone plugged into the Jetson. ALWAYS ON, no gating,
#              no button. This is the answer if you want the robot listening
#              continuously — the Jetson has USB ports and currently exposes no
#              real capture device, so it needs hardware, not code.
#   multicast  the robot's own mic array. Best audio, on the robot's body, but
#              PUSH-TO-TALK and that is not negotiable from here.
#   stdin      audio piped in from anywhere (see scripts/listen_stdin.py).
#
# `C3PO_AUDIO_SOURCE` forces one; otherwise a real capture device wins and the
# multicast group is the fallback.

AUDIO_SOURCE = os.environ.get("C3PO_AUDIO_SOURCE", "auto")
ALSA_DEVICE = os.environ.get("C3PO_ALSA_DEVICE", "")


def alsa_capture_devices() -> list[str]:
    """Real capture devices, i.e. plausible microphones.

    Filters out the Tegra XBAR/ADMAIF `dlink` entries. Those are the SoC's
    internal audio routing fabric, they appear on every Jetson whether or not a
    microphone exists, and opening one yields silence rather than an error —
    which would look exactly like a room where nobody is talking.
    """
    import subprocess

    try:
        out = subprocess.run(["arecord", "-l"], capture_output=True, text=True, timeout=5).stdout
    except (OSError, subprocess.SubprocessError):
        return []

    devices = []
    for line in out.splitlines():
        if not line.startswith("card "):
            continue
        if "dlink" in line or "ADMAIF" in line:
            continue
        try:
            card = line.split("card ", 1)[1].split(":", 1)[0].strip()
            dev = line.split("device ", 1)[1].split(":", 1)[0].strip()
        except IndexError:
            continue
        devices.append(f"plughw:{card},{dev}")
    return devices


def iter_alsa_frames(device: str = "", frame_bytes: int = FRAME_BYTES) -> Iterator[bytes]:
    """Frames from a USB microphone. Always on — no button, no gating.

    Shells out to `arecord` rather than adding pyaudio/sounddevice: both need
    portaudio headers to build, neither has a wheel that installs cleanly on
    JetPack, and alsa-utils is already present. `plughw:` rather than `hw:` so
    ALSA resamples and reformats for us — a USB mic that only does 44.1 kHz
    stereo then still arrives here as the 16 kHz mono the recogniser needs.
    """
    import subprocess

    if not device:
        found = alsa_capture_devices()
        if not found:
            raise ListenUnavailable(
                "no ALSA capture device — the Jetson has no built-in microphone, "
                "so continuous listening needs a USB mic plugged in. Without one, "
                "use the robot's own mic (push-to-talk, hold L1+L2)."
            )
        device = found[0]

    proc = subprocess.Popen(
        [
            "arecord",
            "-D",
            device,
            "-f",
            "S16_LE",
            "-r",
            str(MIC_SAMPLE_RATE),
            "-c",
            "1",
            "-t",
            "raw",
            "-q",
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    if proc.stdout is None:
        proc.terminate()
        raise ListenUnavailable(f"arecord gave no stdout for {device}")

    log.info("listen.alsa_opened", device=device)
    try:
        while True:
            # Exact reads: a short read at a frame boundary desynchronises the
            # 16-bit samples and everything after it decodes as noise.
            chunk = proc.stdout.read(frame_bytes)
            if not chunk:
                break
            yield chunk
    finally:
        proc.terminate()
        proc.wait(timeout=5)


def describe_audio_source() -> dict[str, Any]:
    """Which source will be used, and whether it can listen continuously."""
    devices = alsa_capture_devices()
    if AUDIO_SOURCE == "alsa" or (AUDIO_SOURCE == "auto" and devices):
        return {
            "source": "alsa",
            "device": ALSA_DEVICE or (devices[0] if devices else None),
            "always_on": True,
            "devices": devices,
        }
    return {
        "source": "multicast",
        "device": f"{MIC_GROUP}:{MIC_PORT}",
        "always_on": False,
        "devices": devices,
        "note": (
            "the robot's own mic is push-to-talk: it streams only while "
            "somebody holds L1+L2. Plug a USB mic into the Jetson for "
            "continuous listening."
        ),
    }


def default_source(frame_bytes: int = FRAME_BYTES) -> Iterator[bytes]:
    """The configured source, resolved at call time."""
    chosen = describe_audio_source()
    if chosen["source"] == "alsa":
        return iter_alsa_frames(chosen["device"] or "", frame_bytes)
    return iter_mic_frames(timeout_s=0.5)


class MicListener:
    """Always-on background listener. The agent reads; it never waits.

    WHY BACKGROUND AND NOT A BLOCKING CALL. The microphone is push-to-talk: it
    streams only while somebody holds L1+L2. That button press IS the "I am
    talking to you" signal, and it happens on the human's schedule, not the
    agent's. A blocking `listen(seconds)` forces the agent to GUESS when to
    listen, and anyone who starts speaking outside that window is simply not
    heard. Consuming continuously inverts it: by the time the agent thinks to
    ask, the speech is already transcribed and waiting.

    It also lets the robot do two things at once. A blocking listen means no
    walking while hearing, and — because tool calls hold the transport — it
    stalls every other call for the duration, `stop_everything` included.

    COSTS ALMOST NOTHING WHILE IDLE. A closed mic delivers no packets, so the
    thread sits in a socket timeout loop. There is no audio to decode and no
    model work to do until somebody presses the button.

    The stop phrase is detected here and REPORTED, not acted on. D6.2 had it
    bypass the agent straight into stop_everything; that made sense when the
    microphone was assumed always-on, and does not now — whoever is holding the
    remote to make the robot hear at all already has a physical e-stop under
    their thumb, which is faster and cannot mis-hear. Auto-triggering on a
    mis-decode would halt the robot mid-task on a word nobody said. Pass
    `on_stop=` to opt into acting on it.
    """

    def __init__(
        self,
        whisper: bool = True,
        phrases: list[str] | None = None,
        max_keep: int = 32,
        source: Callable[[], Iterator[bytes]] | None = None,
        build: Callable[[], tuple[Any, Any, Any]] | None = None,
    ) -> None:
        """`source` and `build` exist so this is testable off the robot.

        vosk ships no macOS wheel and the mic is a multicast group on hardware
        we do not always have, so the default path cannot run on a laptop. The
        thread's LOGIC — buffering, utterance boundaries, the pending/history
        split, bounded queues — is where the bugs live, and none of it needs a
        real recogniser. Injecting both means that logic is covered by tests
        that run everywhere, and only the models themselves need the robot.
        """
        self._whisper = whisper
        self._phrases = phrases
        self._max_keep = max_keep
        self._source = source or (lambda: default_source())
        self._build = build
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._stop_evt = threading.Event()

        self._pending: list[dict[str, Any]] = []
        self._history: deque[dict[str, Any]] = deque(maxlen=max_keep)
        self._on_stop: Callable[[str], None] | None = None

        self._frames = 0
        self._utterances = 0
        self._last_audio_at: float | None = None
        self._error: str | None = None

    # -- lifecycle -----------------------------------------------------------

    def start(self, on_stop: Callable[[str], None] | None = None) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._on_stop = on_stop
        self._stop_evt.clear()
        self._thread = threading.Thread(target=self._run, name="mic-listener", daemon=True)
        self._thread.start()
        log.info("mic_listener.started", whisper=self._whisper)

    def stop(self) -> None:
        self._stop_evt.set()

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    # -- the thread ----------------------------------------------------------

    def _run(self) -> None:
        try:
            if self._build is not None:
                detector, transcriber, whisperer = self._build()
            else:
                detector = StopPhraseDetector(phrases=self._phrases)
                transcriber = Transcriber()
                whisperer = build_whisperer() if self._whisper else None
        except ListenUnavailable as exc:
            # Recorded rather than raised: this is a daemon thread, and an
            # exception here would vanish into stderr while `poll()` kept
            # returning an innocent empty list forever.
            with self._lock:
                self._error = str(exc)
            log.error("mic_listener.unavailable", reason=str(exc))
            return

        utterance = bytearray()
        for frame in self._source():
            if self._stop_evt.is_set():
                break

            with self._lock:
                self._frames += 1
                self._last_audio_at = time.monotonic()

            if hit := detector.feed(frame):
                detector.reset()
                log.warning("mic_listener.stop_phrase", phrase=hit)
                self._record(hit, kind="stop")
                if self._on_stop is not None:
                    try:
                        self._on_stop(hit)
                    except Exception:
                        log.exception("mic_listener.on_stop_failed")

            if whisperer is not None:
                utterance.extend(frame)

            text = transcriber.feed(frame)
            if text:
                if whisperer is not None:
                    if len(utterance) >= WHISPER_MIN_SECONDS * TARGET_BYTES_PER_S:
                        text = whisperer.transcribe(bytes(utterance)) or text
                    utterance.clear()
                self._record(text, kind="speech")
            elif whisperer is not None and len(utterance) > MAX_UTTERANCE_BYTES:
                if better := whisperer.transcribe(bytes(utterance)):
                    self._record(better, kind="speech")
                utterance.clear()

    def _record(self, text: str, kind: str) -> None:
        item = {"text": text, "kind": kind, "at": time.monotonic()}
        with self._lock:
            if kind == "speech":
                self._utterances += 1
            self._pending.append(item)
            self._history.append(item)
            # Bound the pending queue too: an agent that never polls must not be
            # able to grow this without limit while somebody talks at the robot.
            if len(self._pending) > self._max_keep:
                del self._pending[: -self._max_keep]

    # -- reading -------------------------------------------------------------

    def poll(self) -> list[dict[str, Any]]:
        """Everything heard since the last poll. Returns immediately, always."""
        now = time.monotonic()
        with self._lock:
            items, self._pending = self._pending, []
        return [{**i, "age_s": round(now - i["at"], 2)} for i in items]

    def recent(self, seconds: float = 30.0) -> list[dict[str, Any]]:
        """Recent history WITHOUT consuming it — for a second look at context."""
        now = time.monotonic()
        with self._lock:
            items = [i for i in self._history if now - i["at"] <= seconds]
        return [{**i, "age_s": round(now - i["at"], 2)} for i in items]

    def diagnostics(self) -> dict[str, Any]:
        now = time.monotonic()
        with self._lock:
            return {
                "running": self.is_running(),
                "error": self._error,
                "frames": self._frames,
                "utterances": self._utterances,
                # The one number that separates "nobody is talking" from "the
                # microphone is shut". None means no audio has EVER arrived,
                # which on this robot means nobody has held L1+L2.
                "seconds_since_audio": (
                    None if self._last_audio_at is None else round(now - self._last_audio_at, 1)
                ),
                "mic_ever_open": self._last_audio_at is not None,
                "pending": len(self._pending),
            }


_mic_listener: MicListener | None = None
_mic_lock = threading.Lock()


def get_mic_listener() -> MicListener:
    global _mic_listener
    with _mic_lock:
        if _mic_listener is None:
            _mic_listener = MicListener()
        return _mic_listener


def _iface_addr() -> str:
    import subprocess

    out = subprocess.run(["ip", "-4", "-o", "addr", "show"], capture_output=True, text=True).stdout
    for line in out.splitlines():
        for tok in line.split():
            if tok.startswith(MIC_IFACE_PREFIX):
                return tok.split("/")[0]
    raise ListenUnavailable(
        f"no interface on {MIC_IFACE_PREFIX}0/24 — the mic feed is on the robot's "
        "internal wired LAN, so this must run onboard, not on a laptop"
    )


def iter_mic_frames(timeout_s: float = 1.0) -> Iterator[bytes]:
    """Yield PCM frames from the mic multicast group. Onboard only.

    Joins with `imr_interface` pinned to the eth0 address, for the reason in this
    module's header. Yields nothing at all today: the feed does not stream at
    rest. That is a property of the robot, not of this function, and it is why
    every caller must treat "no frames" as normal rather than as an error.
    """
    addr = _iface_addr()
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM, socket.IPPROTO_UDP)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind(("", MIC_PORT))
    sock.setsockopt(
        socket.IPPROTO_IP,
        socket.IP_ADD_MEMBERSHIP,
        struct.pack("4s4s", socket.inet_aton(MIC_GROUP), socket.inet_aton(addr)),
    )
    sock.settimeout(timeout_s)
    log.info("listen.mic_joined", group=MIC_GROUP, port=MIC_PORT, iface=addr)
    try:
        while True:
            try:
                yield sock.recv(65535)
            except socket.timeout:
                continue
    finally:
        sock.close()
