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
import struct
from collections.abc import Callable, Iterator

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

VOSK_MODEL = os.environ.get(
    "VOSK_MODEL", os.path.expanduser("~/.local/share/vosk/vosk-model-small-es-0.42"))

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

# 16-bit mono at 16 kHz: 0.125 s. Small enough that stop latency is dominated by
# the decoder rather than by buffering.
FRAME_BYTES = 4000


class ListenUnavailable(RuntimeError):
    """Vosk or its model is missing. Carries the fix."""


def available() -> tuple[bool, str]:
    try:
        import vosk  # noqa: F401
    except ImportError:
        return False, (
            "vosk is not installed — add it to apps/bridge and `uv sync` on the robot")
    if not os.path.isdir(VOSK_MODEL):
        return False, (
            f"vosk model not found at {VOSK_MODEL} — "
            "see apps/bridge/scripts/install_vosk.sh")
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

    def __init__(self, phrases: list[str] | None = None,
                 sample_rate: int = MIC_SAMPLE_RATE) -> None:
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


def detect_in_pcm(pcm: bytes, phrases: list[str] | None = None,
                  frame_bytes: int = FRAME_BYTES) -> str | None:
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
    out = [text for off in range(0, len(pcm), frame_bytes)
           if (text := t.feed(pcm[off : off + frame_bytes]))]
    if (tail := t.flush()):
        out.append(tail)
    return out


def listen_loop(
    frames: Iterator[bytes],
    on_text: Callable[[str], None] | None = None,
    on_stop: Callable[[str], None] | None = None,
    phrases: list[str] | None = None,
) -> None:
    """Run both recognisers over one stream of PCM frames.

    `frames` is any iterator of 16 kHz mono 16-bit chunks — `iter_mic_frames()`
    in production, a file or synthesised audio in tests. That indirection is the
    point: the microphone is currently gated (ROBOT-HARDWARE.md §8.2), and
    keeping the source abstract means everything downstream of it is finished
    and exercised rather than waiting on hardware.

    THE STOP DETECTOR IS FED FIRST, and if it fires, `on_stop` runs before
    `on_text` is even considered for that frame. Transcription is best-effort;
    stopping is not, and it must never queue behind a slower consumer.
    """
    detector = StopPhraseDetector(phrases=phrases)
    transcriber = Transcriber()

    for frame in frames:
        hit = detector.feed(frame)
        if hit:
            log.warning("listen.stop_phrase", phrase=hit)
            detector.reset()
            if on_stop is not None:
                on_stop(hit)

        text = transcriber.feed(frame)
        if text and on_text is not None:
            on_text(text)

    # FLUSH, and it is not an edge case. Vosk emits a final result only at an
    # utterance boundary — i.e. trailing silence — so a finite source (a file, a
    # synthesised clip, a mic stream that ends) leaves its LAST utterance, and
    # often its only one, sitting in the decoder. Without this the loop returns
    # an empty transcript for a perfectly good 2.5 s clip and looks like the
    # recogniser is broken. A live mic never reaches here, which is exactly why
    # this is easy to omit and hard to notice.
    tail = transcriber.flush()
    if tail and on_text is not None:
        on_text(tail)


def _iface_addr() -> str:
    import subprocess
    out = subprocess.run(["ip", "-4", "-o", "addr", "show"],
                         capture_output=True, text=True).stdout
    for line in out.splitlines():
        for tok in line.split():
            if tok.startswith(MIC_IFACE_PREFIX):
                return tok.split("/")[0]
    raise ListenUnavailable(
        f"no interface on {MIC_IFACE_PREFIX}0/24 — the mic feed is on the robot's "
        "internal wired LAN, so this must run onboard, not on a laptop")


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
    sock.setsockopt(socket.IPPROTO_IP, socket.IP_ADD_MEMBERSHIP,
                    struct.pack("4s4s", socket.inet_aton(MIC_GROUP),
                                socket.inet_aton(addr)))
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
