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
from collections.abc import Iterator

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

# Argentine Spanish imperatives, plus the English one because it is universally
# understood under stress and costs nothing to add. "alto" also means "tall",
# which will occasionally false-fire — accepted deliberately per the bias above.
# `[unk]` is REQUIRED by Vosk's grammar mode: without it the decoder must map
# every sound onto one of the listed phrases, so unrelated speech is forced into
# a spurious "pará". It is the escape hatch that makes the restriction safe.
DEFAULT_STOP_PHRASES = ["pará", "para", "alto", "frená", "frena", "stop"]

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
        self._rec = KaldiRecognizer(Model(VOSK_MODEL), sample_rate,
                                    _grammar(self.phrases))
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
