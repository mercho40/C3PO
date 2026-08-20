"""The resampler, tested as DSP rather than as plumbing.

A resampler that returns roughly the right number of bytes can still sound
wrong, and nobody notices until the robot is talking to a room. The interesting
failures are invisible to a byte count and obvious to an ear:

  * no anti-aliasing -> everything above the output's 8 kHz Nyquist folds back
    into the speech band as a metallic ring on sibilants;
  * missing gain compensation after zero-stuffing -> correct spectrum, 320x too
    quiet, which reads as "the speaker is broken";
  * int16 wrap on overshoot -> +32768 becomes -32768, a full-scale click mid-word.

So these measure the spectrum, not the length.
"""

from __future__ import annotations

import numpy as np
import pytest

from bridge.skills import tts

IN_RATE = tts.PIPER_RATE
OUT_RATE = tts.TARGET_RATE


def tone(freq_hz: float, seconds: float = 0.25, rate: int = IN_RATE,
         amplitude: float = 0.5) -> bytes:
    t = np.arange(int(rate * seconds)) / rate
    return (amplitude * np.sin(2 * np.pi * freq_hz * t) * 32767).astype("<i2").tobytes()


def dominant_freq(pcm: bytes, rate: int = OUT_RATE) -> float:
    x = np.frombuffer(pcm, dtype="<i2").astype(np.float64)
    # Hann window: a hard edge on a finite record smears the peak enough to
    # matter at the tolerances below.
    spec = np.abs(np.fft.rfft(x * np.hanning(len(x))))
    return float(np.fft.rfftfreq(len(x), 1 / rate)[int(np.argmax(spec))])


def rms(pcm: bytes) -> float:
    if not pcm:
        return 0.0
    return float(np.sqrt(np.mean(np.frombuffer(pcm, dtype="<i2").astype(np.float64) ** 2)))


def test_empty_in_empty_out():
    assert tts.resample_to_16k(b"") == b""


def test_output_length_tracks_the_rate_ratio():
    n_out = len(tts.resample_to_16k(tone(1000, seconds=1.0))) // 2
    assert abs(n_out - OUT_RATE) <= 2, f"1 s in should be ~{OUT_RATE} samples out, got {n_out}"


@pytest.mark.parametrize("freq", [200.0, 1000.0, 3000.0])
def test_speech_band_tones_survive_at_the_right_pitch(freq):
    """Pitch must not shift. A wrong UP/DOWN ratio transposes the whole voice."""
    got = dominant_freq(tts.resample_to_16k(tone(freq, seconds=0.5)))
    assert abs(got - freq) < 15.0, f"{freq} Hz came out at {got} Hz"


def test_amplitude_is_preserved_not_divided_by_the_upsampling_factor():
    """Zero-stuffing costs a factor of UP; the filter gain must give it back."""
    src = tone(1000, seconds=0.5, amplitude=0.5)
    ratio = rms(tts.resample_to_16k(src)) / rms(src)
    assert 0.9 < ratio < 1.1, f"level changed by {ratio:.3f}x — check the *UP gain term"


def test_above_nyquist_is_rejected_not_folded_back():
    """10 kHz cannot exist at 16 kHz out; it must be attenuated, NOT aliased.

    Without the anti-alias filter it returns as 16000-10000 = 6000 Hz at full
    strength — a loud tone in the middle of the speech band.
    """
    out = tts.resample_to_16k(tone(10000, seconds=0.5))
    assert rms(out) < 0.05 * rms(tone(1000, seconds=0.5)), "energy above 8 kHz survived"


def test_a_tone_just_below_nyquist_still_passes():
    """The filter must not be so wide it eats the top of the speech band."""
    src = tone(6000, seconds=0.5)
    out = tts.resample_to_16k(src)
    assert abs(dominant_freq(out) - 6000.0) < 30.0
    assert rms(out) > 0.3 * rms(src)


def test_full_scale_input_never_wraps_sign():
    """int16 wraps instead of saturating; overshoot must clip, not invert."""
    src = tone(1000, seconds=0.3, amplitude=1.0)
    out = np.frombuffer(tts.resample_to_16k(src), dtype="<i2").astype(np.float64)
    assert out.max() <= 32767 and out.min() >= -32768
    assert abs(out).max() > 0.8 * 32767, "signal collapsed"


def test_silence_stays_silent():
    assert rms(tts.resample_to_16k(b"\x00\x00" * IN_RATE)) == 0.0


def test_duration_is_computed_from_bytes_not_guessed():
    """PlayStream acks on receipt, so bytes are the only completion signal."""
    assert tts.duration_s(b"\x00\x00" * OUT_RATE) == pytest.approx(1.0)
    assert tts.duration_s(b"") == 0.0


def test_available_explains_the_fix_when_piper_is_missing(monkeypatch):
    """An error that does not say what to run is a support ticket."""
    monkeypatch.setattr(tts, "PIPER_BIN", "/nonexistent/piper")
    monkeypatch.setattr(tts, "PIPER_VOICE", "/nonexistent/voice.onnx")
    monkeypatch.setattr(tts.shutil, "which", lambda _: None)
    ok, why = tts.available()
    assert ok is False
    assert "install_piper" in why


# --- PlayStream transport ---------------------------------------------------
#
# The wire rules here are vendor behaviour we cannot discover from the types:
# chunks sharing a stream_id concatenate, a new id interrupts, and PlayStop is
# scoped by app_name. Getting any of them wrong produces audio that is subtly
# wrong (clipped, restarted, or silencing the co-tenant) rather than an error.


class _FakeVoiceClient:
    """Records what would go on the wire, and can fail on demand."""

    def __init__(self, fail_at: int | None = None) -> None:
        self.calls: list[tuple[int, str, bytes]] = []
        self.plain: list[tuple[int, str]] = []
        self._fail_at = fail_at

    def _CallRequestWithParamAndBin(self, api_id, param, binary):  # noqa: N802, ANN001
        self.calls.append((api_id, param, binary))
        if self._fail_at is not None and len(self.calls) > self._fail_at:
            return 100, None          # the service's only declared error
        return 0, None

    def call_raw(self, api_id, param):  # noqa: ANN001
        self.plain.append((api_id, param))
        return 0, None


@pytest.fixture()
def fake_voice(monkeypatch):
    from bridge.sdk import g1_rpc

    client = _FakeVoiceClient()
    monkeypatch.setattr(g1_rpc, "_get_voice_client", lambda: client)
    return client


def test_every_chunk_of_one_utterance_reuses_one_stream_id(fake_voice):
    """Same id = gapless concatenation. A fresh id per chunk would make each
    chunk interrupt the one before it, so a sentence would play as its own last
    fragment — audible, and very confusing to debug."""
    import json

    from bridge.sdk import g1_rpc

    pcm = b"\x01\x02" * (g1_rpc.PLAY_CHUNK_BYTES)   # several chunks' worth
    g1_rpc.play_pcm(pcm, "stream-abc")

    assert len(fake_voice.calls) > 1, "test needs a multi-chunk buffer to be meaningful"
    ids = {json.loads(param)["stream_id"] for _, param, _ in fake_voice.calls}
    assert ids == {"stream-abc"}


def test_chunks_partition_the_audio_exactly_once(fake_voice):
    """No dropped tail, no duplicated bytes — reassembly must equal the input."""
    from bridge.sdk import g1_rpc

    pcm = bytes(range(256)) * 700          # not a multiple of the chunk size
    g1_rpc.play_pcm(pcm, "s")
    assert b"".join(binary for _, _, binary in fake_voice.calls) == pcm


def test_playback_uses_our_own_app_name(fake_voice):
    """app_name is what stops us and gemm-ai silencing each other."""
    import json

    from bridge.sdk import g1_rpc

    g1_rpc.play_pcm(b"\x00" * 100, "s")
    assert json.loads(fake_voice.calls[0][1])["app_name"] == "c3po"


def test_a_rejected_chunk_stops_the_stream(monkeypatch):
    """Format errors reject every chunk identically; sending 30 more is noise."""
    from bridge.sdk import g1_rpc

    client = _FakeVoiceClient(fail_at=1)
    monkeypatch.setattr(g1_rpc, "_get_voice_client", lambda: client)

    code, _ = g1_rpc.play_pcm(b"\x00" * (g1_rpc.PLAY_CHUNK_BYTES * 5), "s")
    assert code == 100
    assert len(client.calls) == 2, "should stop right after the first failure"


def test_empty_audio_is_a_no_op_not_an_rpc(fake_voice):
    from bridge.sdk import g1_rpc

    assert g1_rpc.play_pcm(b"", "s") == (0, None)
    assert fake_voice.calls == []


def test_stop_play_is_scoped_by_app_name_not_stream_id(fake_voice):
    """Three of four sources agree; the on-robot C++ example is wrong."""
    import json

    from bridge.sdk import g1_protocol, g1_rpc

    g1_rpc.stop_play()
    api_id, param = fake_voice.plain[-1]
    assert api_id == g1_protocol.API_ID_VOICE_STOP_PLAY
    assert json.loads(param) == {"app_name": "c3po"}
