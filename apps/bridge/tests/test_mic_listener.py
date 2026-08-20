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


def make(frames, whisper=True, **kw):
    det, trans, whis = FakeDetector(), FakeTranscriber(), FakeWhisper() if whisper else None
    lis = MicListener(source=lambda: iter(frames),
                      build=lambda: (det, trans, whis), **kw)
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
    lis = MicListener(source=lambda: iter([b"aa"]),
                      build=lambda: (_ for _ in ()).throw(ListenUnavailable("no vosk")))
    lis.start()
    time.sleep(0.3)
    diag = lis.diagnostics()
    assert diag["error"] and "no vosk" in diag["error"]


def test_diagnostics_separate_a_shut_mic_from_a_quiet_room():
    """The distinction the agent needs: with no audio EVER, an empty transcript
    means nobody held the button — not that nobody spoke."""
    lis = MicListener(source=lambda: iter(()), build=lambda: (FakeDetector(), FakeTranscriber(), None))
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
