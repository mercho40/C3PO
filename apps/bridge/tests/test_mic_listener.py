"""The background listener's logic, tested without vosk, whisper or a robot.

None of the default path runs on a laptop — vosk has no macOS wheel and the mic
is a multicast group on hardware we do not always have. But the models are not
where the bugs are. The bugs are in the thread: whether an utterance is buffered
and handed over exactly once, whether `poll()` consumes and `recent()` does not,
whether a never-polling agent can grow a queue without bound, and whether a
missing recogniser fails loudly or silently.

All of that is plain logic, so `MicListener` takes an injectable audio source and
recogniser factory and all of it is covered here — leaving only the models
themselves needing the robot.
"""

from __future__ import annotations

import time


from bridge.skills.listen import ListenUnavailable, MicListener


class FakeDetector:
    """Fires on any frame containing the sentinel byte."""

    def __init__(self, trigger: bytes = b"\xff") -> None:
        self.trigger = trigger
        self.resets = 0

    def feed(self, frame: bytes) -> str | None:
        return "emergencia" if self.trigger in frame else None

    def reset(self) -> None:
        self.resets += 1


class FakeTranscriber:
    """Closes an utterance on a frame of b'.' — stands in for Vosk's boundary."""

    def __init__(self) -> None:
        self.fed = 0

    def feed(self, frame: bytes) -> str | None:
        self.fed += 1
        return "vosk text" if frame == b"." else None

    def flush(self) -> str | None:
        return None


class FakeWhisper:
    def __init__(self) -> None:
        self.calls: list[int] = []

    def transcribe(self, pcm: bytes) -> str:
        self.calls.append(len(pcm))
        return f"whisper text ({len(pcm)}B)"


def make(frames, whisper=True, min_seconds=0.0, monkeypatch=None, **kw):
    """`min_seconds=0` by default so the buffering tests are not silently
    skipping Whisper because these fake frames are a few bytes long. The routing
    threshold gets its own test below, where it is the subject rather than a
    hidden precondition."""
    import bridge.skills.listen as mod
    mod.WHISPER_MIN_SECONDS = min_seconds
    det, trans, whis = FakeDetector(), FakeTranscriber(), FakeWhisper() if whisper else None
    lis = MicListener(source=lambda: iter(frames), build=lambda: (det, trans, whis), **kw)
    return lis, det, trans, whis


def drain(listener, timeout=3.0):
    """Wait for the source to be exhausted, then read. Threads need a join."""
    deadline = time.time() + timeout
    while listener.is_running() and time.time() < deadline:
        time.sleep(0.01)
    return listener.poll()


def test_utterances_are_transcribed_and_available_without_blocking():
    lis, *_ = make([b"aa", b"bb", b"."])
    lis.start()
    items = drain(lis)
    assert [i["kind"] for i in items] == ["speech"]
    assert "whisper text" in items[0]["text"]


def test_whisper_text_replaces_vosk_text():
    """Vosk is the segmenter here; its transcript must not be what surfaces."""
    lis, *_ = make([b"aa", b"."])
    lis.start()
    items = drain(lis)
    assert "vosk text" not in items[0]["text"]


def test_whisper_receives_the_whole_utterance_not_one_frame():
    """The buffer is the point — transcribing frame by frame would destroy the
    context Whisper needs and produce worse text than the segmenter it replaced."""
    lis, _, _, whis = make([b"aaaa", b"bbbb", b"."])
    lis.start()
    drain(lis)
    assert whis.calls and whis.calls[0] == 9, f"expected the buffered 9 bytes, got {whis.calls}"


def test_the_buffer_is_cleared_between_utterances():
    """Otherwise every utterance carries all previous audio and grows forever."""
    lis, _, _, whis = make([b"aa", b".", b"bbbb", b"."])
    lis.start()
    drain(lis)
    assert len(whis.calls) == 2
    assert whis.calls[1] < whis.calls[0] + 100, "second utterance kept the first's audio"


def test_poll_consumes_and_a_second_poll_is_empty():
    """Each utterance must be acted on once. Re-returning it would make an agent
    answer the same question twice."""
    lis, *_ = make([b"aa", b"."])
    lis.start()
    assert drain(lis)
    assert lis.poll() == []


def test_recent_does_not_consume():
    lis, *_ = make([b"aa", b"."])
    lis.start()
    drain(lis)
    assert lis.recent(60) and lis.recent(60), "recent() must be re-readable"


def test_a_never_polling_agent_cannot_grow_the_queue_without_bound():
    frames = [b"x", b"."] * 50
    lis, *_ = make(frames, max_keep=8)
    lis.start()
    time.sleep(0.6)
    assert len(lis.poll()) <= 8


def test_the_stop_phrase_is_reported_and_resets_the_detector():
    """Without the reset the phrase re-fires on every subsequent frame, so one
    shout becomes a stop event per 0.125 s for as long as the person talks."""
    lis, det, _, _ = make([b"aa", b"\xff", b"."])
    lis.start()
    items = drain(lis)
    assert any(i["kind"] == "stop" for i in items)
    assert det.resets == 1


def test_the_stop_callback_fires_and_a_raising_one_does_not_kill_the_thread():
    got: list[str] = []
    lis, *_ = make([b"\xff", b"aa", b"."])
    lis.start(on_stop=got.append)
    items = drain(lis)
    assert got == ["emergencia"]
    assert any(i["kind"] == "speech" for i in items), "thread died after the callback"


def test_a_raising_stop_callback_is_survived():
    lis, *_ = make([b"\xff", b"aa", b"."])
    lis.start(on_stop=lambda _: (_ for _ in ()).throw(RuntimeError("boom")))
    assert any(i["kind"] == "speech" for i in drain(lis))


def test_a_missing_recogniser_is_recorded_not_swallowed():
    """This runs in a daemon thread: an exception would vanish into stderr while
    poll() kept returning an innocent empty list forever."""
    lis = MicListener(
        source=lambda: iter([b"aa"]),
        build=lambda: (_ for _ in ()).throw(ListenUnavailable("no vosk")),
    )
    lis.start()
    time.sleep(0.3)
    diag = lis.diagnostics()
    assert diag["error"] and "no vosk" in diag["error"]


def test_diagnostics_separate_a_shut_mic_from_a_quiet_room():
    """The distinction the agent needs: with no audio EVER, an empty transcript
    means nobody held the button — not that nobody spoke."""
    lis = MicListener(
        source=lambda: iter(()), build=lambda: (FakeDetector(), FakeTranscriber(), None)
    )
    lis.start()
    time.sleep(0.2)
    assert lis.diagnostics()["mic_ever_open"] is False

    lis2, *_ = make([b"aa", b"."])
    lis2.start()
    drain(lis2)
    assert lis2.diagnostics()["mic_ever_open"] is True


def test_whisper_disabled_falls_back_to_the_segmenter_text():
    lis, *_ = make([b"aa", b"."], whisper=False)
    lis.start()
    items = drain(lis)
    assert items[0]["text"] == "vosk text"


# --- audio source selection -------------------------------------------------
#
# "Always listening" is a source question, not a code question: the robot's own
# mic is push-to-talk and cannot be opened from software. These pin the
# selection logic, which decides whether the robot can hear continuously.

import subprocess  # noqa: E402

from bridge.skills import listen as listen_mod  # noqa: E402

ARECORD_JETSON_BARE = """**** List of CAPTURE Hardware Devices ****
card 1: APE [NVIDIA Jetson Orin NX APE], device 0: tegra-dlink-0 XBAR-ADMAIF1-0 []
  Subdevices: 1/1
card 1: APE [NVIDIA Jetson Orin NX APE], device 1: tegra-dlink-1 XBAR-ADMAIF2-1 []
  Subdevices: 1/1
"""

ARECORD_WITH_USB_MIC = (
    ARECORD_JETSON_BARE
    + """card 2: Device [USB Audio Device], device 0: USB Audio [USB Audio]
  Subdevices: 1/1
"""
)


def _fake_arecord(monkeypatch, output: str):
    monkeypatch.setattr(
        subprocess,
        "run",
        lambda *a, **k: subprocess.CompletedProcess(a, 0, stdout=output, stderr=""),
    )


def test_tegra_routing_devices_are_not_mistaken_for_microphones(monkeypatch):
    """THE TRAP. Every Jetson lists tegra-dlink/ADMAIF capture devices whether
    or not a mic exists, and opening one yields SILENCE rather than an error —
    indistinguishable from a room where nobody is talking. Treating them as a
    microphone would make the robot look deaf with no diagnosable cause."""
    _fake_arecord(monkeypatch, ARECORD_JETSON_BARE)
    assert listen_mod.alsa_capture_devices() == []


def test_a_usb_microphone_is_found_alongside_the_tegra_noise(monkeypatch):
    _fake_arecord(monkeypatch, ARECORD_WITH_USB_MIC)
    assert listen_mod.alsa_capture_devices() == ["plughw:2,0"]


def test_without_a_usb_mic_the_robot_cannot_listen_continuously(monkeypatch):
    """The honest report: the built-in mic is push-to-talk, so always_on is
    false and the note says what hardware would change that."""
    _fake_arecord(monkeypatch, ARECORD_JETSON_BARE)
    monkeypatch.setattr(listen_mod, "AUDIO_SOURCE", "auto")
    chosen = listen_mod.describe_audio_source()
    assert chosen["source"] == "multicast"
    assert chosen["always_on"] is False
    assert "USB mic" in chosen["note"]


def test_a_usb_mic_switches_the_robot_to_always_on(monkeypatch):
    _fake_arecord(monkeypatch, ARECORD_WITH_USB_MIC)
    monkeypatch.setattr(listen_mod, "AUDIO_SOURCE", "auto")
    chosen = listen_mod.describe_audio_source()
    assert chosen["source"] == "alsa"
    assert chosen["always_on"] is True
    assert chosen["device"] == "plughw:2,0"


def test_the_source_can_be_forced(monkeypatch):
    """So the robot's own mic stays reachable even with a USB mic attached —
    its array is on the body and better placed for someone standing in front."""
    _fake_arecord(monkeypatch, ARECORD_WITH_USB_MIC)
    monkeypatch.setattr(listen_mod, "AUDIO_SOURCE", "multicast")
    assert listen_mod.describe_audio_source()["source"] == "multicast"


def test_missing_arecord_is_not_a_crash(monkeypatch):
    """A dev machine has no alsa-utils; selection must fall back, not explode."""

    def boom(*a, **k):
        raise FileNotFoundError("arecord")

    monkeypatch.setattr(subprocess, "run", boom)
    assert listen_mod.alsa_capture_devices() == []


# --- whisper routing by utterance length ------------------------------------
#
# Measured on the robot, warm: Whisper is 10-18x slower than the segmenter AND
# worse on short clips -- "Para." came back as "!Bien!". Short utterances are
# what commands to a robot look like, so routing everything through it makes the
# common case both slower and less correct.

def test_short_utterances_keep_the_segmenter_text():
    """Below the threshold, Whisper must not even be called: it would cost
    seconds to return a worse answer."""
    lis, _, _, whis = make([b"aa", b"."], min_seconds=10.0)
    lis.start()
    items = drain(lis)
    assert items[0]["text"] == "vosk text"
    assert whis.calls == [], "whisper was called on a short utterance"


def test_long_utterances_go_to_whisper():
    lis, _, _, whis = make([b"a" * 40000, b"."], min_seconds=1.0)
    lis.start()
    items = drain(lis)
    assert whis.calls, "whisper was not called on a long utterance"
    assert "whisper text" in items[0]["text"]


def test_the_buffer_is_cleared_even_when_whisper_is_skipped():
    """The skip must not leak audio into the NEXT utterance -- otherwise a run
    of short utterances silently accumulates until it crosses the threshold and
    one of them is transcribed with all the others' audio in front of it."""
    lis, _, _, whis = make([b"aa", b".", b"bb", b"."], min_seconds=10.0)
    lis.start()
    drain(lis)
    assert whis.calls == []
    assert lis.diagnostics()["utterances"] == 2


# --- which whisper backend gets used ----------------------------------------
#
# The local path CANNOT use the GPU on this machine: the ctranslate2 aarch64
# wheel is compiled without CUDA. So remote-first is not a preference, it is the
# only way the GPU is reachable at all — and a regression to local-first would
# silently cost 10x latency with everything still "working".

def test_the_gpu_container_is_preferred_when_available(monkeypatch):
    import bridge.skills.listen as mod
    monkeypatch.setattr(mod, "remote_whisper_status",
                        lambda: {"available": True, "model": "ggml-base.bin"})
    assert isinstance(mod.build_whisperer(), mod.RemoteWhisper)


def test_it_falls_back_to_local_cpu_when_the_container_is_down(monkeypatch):
    import bridge.skills.listen as mod

    class FakeLocal:
        pass

    monkeypatch.setattr(mod, "remote_whisper_status",
                        lambda: {"available": False, "reason": "unreachable"})
    monkeypatch.setattr(mod, "WhisperTranscriber", lambda: FakeLocal())
    assert isinstance(mod.build_whisperer(), FakeLocal)


def test_neither_available_returns_none_rather_than_raising(monkeypatch):
    """The caller then keeps the segmenter's text. Losing Whisper must cost
    transcript QUALITY, never the utterance itself."""
    import bridge.skills.listen as mod
    monkeypatch.setattr(mod, "remote_whisper_status", lambda: {"available": False})

    def boom():
        raise mod.ListenUnavailable("no model")

    monkeypatch.setattr(mod, "WhisperTranscriber", boom)
    assert mod.build_whisperer() is None


def test_a_dead_container_yields_empty_text_not_an_exception(monkeypatch):
    """RemoteWhisper must never raise into the listening loop — an unreachable
    container would otherwise kill the thread that also carries the stop phrase."""
    import bridge.skills.listen as mod
    r = mod.RemoteWhisper(host="127.0.0.1", port=1)   # nothing listens there
    assert r.transcribe(b"\x00\x00" * 1000) == ""
    assert r.transcribe(b"") == ""
