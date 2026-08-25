"""Spanish speech for a robot whose firmware has no Spanish voice.

D6.1 established that external synthesis is MANDATORY here, not preferable: the
firmware's `speaker_id` is 0=Chinese / 1=English, there is no third voice, and
passing Spanish to the TTS api returns **rpc_code 0 while emitting unusable
audio** — a false success, which is worse than an error because nothing in the
logs marks it. D6.3 then removed the cloud from the path entirely.

So the shape is: Piper synthesises locally, and the PCM goes to the robot over
`PlayStream` (`voice`/1003) instead of asking the firmware to read text.

TWO SAMPLE RATES, AND THE MISMATCH IS THE WHOLE JOB HERE. `es_AR/daniela` is a
22 050 Hz voice; `PlayStream` hard-rejects anything but **16 kHz mono 16-bit**,
and both vendor examples enforce it. Neither ffmpeg nor sox exists on the robot,
so the resampler is ours. D6.2 took that trade deliberately — the alternative
was `es_ES/carlfm`, native 16 kHz but a Spain accent, and the robot speaks to
Argentine students every day.

WHY NOT scipy.signal.resample_poly: scipy is not in this venv and would be a
~40 MB wheel on the robot for one function. numpy is already here (the SDK pulls
it), and the polyphase decomposition below is the same algorithm.
"""

from __future__ import annotations

import os
import shutil
import subprocess

import numpy as np
import structlog

log = structlog.get_logger(__name__)

# PlayStream's contract, not a preference. 16 kHz mono 16-bit LE.
TARGET_RATE = 16000
PIPER_RATE = 22050

# 22050 -> 16000 reduces by gcd 50 to 320/441. Kept as named constants because
# every magic number below is derived from this pair.
UP = 320
DOWN = 441

# scipy's resample_poly default. Filter length 2*10*max(up,down)+1 = 8821 taps,
# which sounds enormous until the polyphase split: each output sample touches
# ceil(8821/320) = 28 of them, so a 5 s utterance is ~2M MACs.
HALF_LEN_FACTOR = 10
KAISER_BETA = 5.0

PIPER_BIN = os.environ.get("PIPER_BIN", os.path.expanduser("~/.local/share/piper/piper"))
PIPER_VOICE = os.environ.get(
    "PIPER_VOICE", os.path.expanduser("~/.local/share/piper/es_AR-daniela-high.onnx")
)

# Long enough for a paragraph on an Orin NX, short enough that a wedged binary
# does not hold the tool call open forever.
SYNTH_TIMEOUT_S = 30.0


class TtsUnavailable(RuntimeError):
    """Piper or its voice is not installed. Carries the fix, not just the fault."""


def _design_lowpass() -> np.ndarray:
    """Windowed-sinc anti-alias filter, at the 22050*320 intermediate rate.

    Cutoff is the LOWER of the two Nyquists — 8 kHz, the output's — because this
    filter does two jobs at once: it removes the images that zero-stuffing by UP
    creates, and it band-limits before dropping every DOWNth sample. Designing at
    the input's Nyquist instead would alias everything between 8 and 11 kHz back
    down into the speech band, which sounds like a metallic ring on sibilants.
    """
    n_taps = 2 * HALF_LEN_FACTOR * max(UP, DOWN) + 1
    # Cutoff in cycles per intermediate sample: half the lower rate.
    fc = 0.5 / max(UP, DOWN)
    n = np.arange(n_taps)
    h = 2 * fc * np.sinc(2 * fc * (n - (n_taps - 1) / 2))
    h *= np.kaiser(n_taps, KAISER_BETA)
    # Zero-stuffing divides amplitude by UP; give it back or the robot whispers.
    return h * UP


def _polyphase_bank(h: np.ndarray) -> np.ndarray:
    """Split the filter into UP phases: bank[p] is the filter for output phase p."""
    taps = int(np.ceil(len(h) / UP))
    bank = np.zeros((UP, taps), dtype=np.float64)
    for phase in range(UP):
        seg = h[phase::UP]
        bank[phase, : len(seg)] = seg
    return bank


_BANK: np.ndarray | None = None


def _bank() -> np.ndarray:
    """Built once. 8821 taps of sinc is not something to recompute per utterance."""
    global _BANK
    if _BANK is None:
        _BANK = _polyphase_bank(_design_lowpass())
    return _BANK


def resample_to_16k(pcm: bytes) -> bytes:
    """22 050 Hz -> 16 000 Hz, signed 16-bit mono in and out.

    Never materialises the 320x-upsampled signal: at 5 s that is 35 M samples,
    ~280 MB, on a board with 15 GB shared between us, the detector and another
    team's SLAM. The polyphase identity does the same arithmetic with the zeros
    left out — for output m at position p = m*DOWN in the upsampled signal, only
    taps congruent to p mod UP land on a non-zero input sample.
    """
    if not pcm:
        return b""
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float64)

    bank = _bank()
    taps = bank.shape[1]

    n_out = int(np.ceil(len(x) * UP / DOWN))
    m = np.arange(n_out)
    p = m * DOWN
    phase = p % UP
    base = p // UP

    # Front-pad so base-j never runs off the start; the filter's own group delay
    # (~0.6 ms) is left in rather than trimmed — it is inaudible and trimming it
    # is the kind of off-by-one that shifts every utterance by a sample forever.
    xp = np.concatenate([np.zeros(taps - 1), x])
    idx = base[:, None] + (taps - 1) - np.arange(taps)[None, :]
    np.clip(idx, 0, len(xp) - 1, out=idx)
    y = np.einsum("ij,ij->i", xp[idx], bank[phase])

    # Clip before casting: a resampler can overshoot past full scale on
    # transients, and int16 wraps rather than saturates — +32768 becomes -32768,
    # a full-scale click in the middle of a word.
    return np.clip(np.rint(y), -32768, 32767).astype("<i2").tobytes()


def available() -> tuple[bool, str]:
    """Is local synthesis usable? Returns (ok, reason-if-not)."""
    binary = PIPER_BIN if os.path.isfile(PIPER_BIN) else shutil.which("piper")
    if not binary:
        return False, (
            f"piper binary not found at {PIPER_BIN} nor on PATH — "
            "run apps/bridge/scripts/install_piper.sh on the robot"
        )
    if not os.path.isfile(PIPER_VOICE):
        return False, (
            f"piper voice not found at {PIPER_VOICE} — "
            "run apps/bridge/scripts/install_piper.sh on the robot"
        )
    return True, ""


def synthesize(text: str) -> bytes:
    """Spanish text -> 16 kHz mono 16-bit PCM, ready for PlayStream.

    `--output-raw` streams headerless PCM at the voice's native rate, so there is
    no WAV header to strip and no temp file to clean up. Piper's own rate is
    22 050; the resample is not optional.
    """
    ok, why = available()
    if not ok:
        raise TtsUnavailable(why)

    binary = PIPER_BIN if os.path.isfile(PIPER_BIN) else shutil.which("piper")
    if binary is None:
        # available() checked a moment ago, so reaching here means the binary
        # was removed mid-call. Rare, but it must raise the same actionable
        # error rather than a TypeError from deep inside subprocess.
        raise TtsUnavailable(f"piper vanished between the check and the call ({PIPER_BIN})")

    proc = subprocess.run(
        [binary, "--model", PIPER_VOICE, "--output-raw"],
        input=text.encode("utf-8"),
        capture_output=True,
        timeout=SYNTH_TIMEOUT_S,
    )
    if proc.returncode != 0:
        raise TtsUnavailable(
            f"piper exited {proc.returncode}: {proc.stderr.decode('utf-8', 'replace')[:300]}"
        )
    if not proc.stdout:
        raise TtsUnavailable("piper produced no audio (empty stdout)")

    pcm16k = resample_to_16k(proc.stdout)
    log.info(
        "tts.synthesized",
        chars=len(text),
        piper_bytes=len(proc.stdout),
        pcm_bytes=len(pcm16k),
        seconds=round(len(pcm16k) / (TARGET_RATE * 2), 2),
    )
    return pcm16k


def duration_s(pcm: bytes) -> float:
    """Seconds of audio in a 16 kHz mono 16-bit buffer.

    The vendor's example fires PlayStream and immediately Sleep(3) — it does not
    wait for playback, and PlayStream acks on receipt. So the only honest way to
    know when the robot has finished talking is to compute it from the bytes we
    sent. §7 calls this out: this service needs its own duration model.
    """
    return len(pcm) / (TARGET_RATE * 2)
