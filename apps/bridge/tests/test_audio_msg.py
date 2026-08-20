"""Two payload shapes share one topic; nearly every bug here is a mix-up.

`rt/audio_msg` carries firmware ASR results AND playback state. They have no
field in common, arrive interleaved, and one of them is currently unreachable on
this robot — so the failure mode is a reader that quietly handles one shape and
drops the other, with no error and only under conditions where speech happens to
be playing.

Driven through `handle()` rather than DDS: the parsing and the state machine are
the parts that can be wrong, and neither needs a robot.
"""

from __future__ import annotations

import json

import pytest

from bridge.sdk.audio_msg import ASR_STALE_AFTER_S, AudioMsgLink, parse_audio_msg

ASR_PAYLOAD = json.dumps({
    "index": 1, "timestamp": 29319303490, "text": "pará", "angle": 90,
    "speaker_id": 0, "sense": "unknown", "confidence": 0.95,
    "language": "es-ES", "is_final": True,
})


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


@pytest.fixture()
def link():
    clock = Clock()
    lk = AudioMsgLink(clock=clock)
    lk.clock = clock
    return lk


# --- parsing ---------------------------------------------------------------

def test_play_state_and_asr_are_told_apart():
    assert parse_audio_msg('{"play_state":1}')["kind"] == "play_state"
    assert parse_audio_msg(ASR_PAYLOAD)["kind"] == "asr"


def test_play_state_zero_is_still_a_play_state_message():
    """The regression this ordering exists for.

    A text-first reader drops {"play_state":0} — it has no `text` — so playback
    would appear to start and never stop. Nothing errors; speech just seems to
    hang forever.
    """
    parsed = parse_audio_msg('{"play_state":0}')
    assert parsed is not None and parsed["kind"] == "play_state"
    assert parsed["playing"] is False


def test_direction_of_arrival_survives_parsing():
    """`angle` is the only spatial cue we get — four mics arrive pre-mixed."""
    assert parse_audio_msg(ASR_PAYLOAD)["angle"] == 90


def test_missing_is_final_defaults_to_final():
    """Streaming mode is off by default, so the field is normally absent."""
    assert parse_audio_msg(json.dumps({"text": "hola"}))["is_final"] is True


@pytest.mark.parametrize("raw", ["", "not json", "[]", "null", "{}", '{"text":""}'])
def test_junk_is_ignored_not_guessed_at(raw):
    assert parse_audio_msg(raw) is None


# --- playback state --------------------------------------------------------

def test_playback_tracks_start_and_stop(link):
    assert link.is_playing() is False
    link.handle('{"play_state":1}')
    assert link.is_playing() is True
    link.handle('{"play_state":0}')
    assert link.is_playing() is False


def test_wait_returns_true_only_after_playback_actually_ends(link):
    link.handle('{"play_state":1}')
    assert link.wait_for_playback_end(0.01) is False, "must not report an end mid-utterance"
    link.handle('{"play_state":0}')
    assert link.wait_for_playback_end(0.01) is True


def test_a_new_utterance_rearms_the_wait(link):
    """Barge-in: a second say starts playback again, and a waiter must block."""
    link.handle('{"play_state":1}')
    link.handle('{"play_state":0}')
    assert link.wait_for_playback_end(0.01) is True
    link.handle('{"play_state":1}')
    assert link.wait_for_playback_end(0.01) is False


# --- ASR -------------------------------------------------------------------

def test_recognised_speech_is_exposed_with_its_age(link):
    link.handle(ASR_PAYLOAD)
    asr, age = link.latest_asr()
    assert asr["text"] == "pará"
    assert age == 0.0


def test_stale_speech_is_withheld_rather_than_served_as_current(link):
    """An agent acting on a minute-old 'pará' acts on a command already obeyed."""
    link.handle(ASR_PAYLOAD)
    link.clock.t += ASR_STALE_AFTER_S + 1
    assert link.latest_asr() == (None, None)


def test_asr_callback_fires_and_a_raising_one_cannot_kill_the_reader(link):
    """The subscriber thread must survive a bad listener — it also carries
    play_state, so an exception here would silently stop completion detection."""
    got = []
    link.on_asr(lambda a: got.append(a["text"]))
    link.handle(ASR_PAYLOAD)
    assert got == ["pará"]

    link.on_asr(lambda a: (_ for _ in ()).throw(RuntimeError("boom")))
    link.handle(ASR_PAYLOAD)          # must not raise
    link.handle('{"play_state":1}')
    assert link.is_playing() is True


def test_diagnostics_separate_a_dead_topic_from_a_gated_microphone(link):
    """The distinction that matters right now: play_state proves we receive from
    this topic, while asr_messages staying 0 is the mic being gated — not a
    broken subscription."""
    link.handle('{"play_state":1}')
    diag = link.diagnostics()
    assert diag["play_state_events"] == 1
    assert diag["asr_messages"] == 0
    assert diag["asr_source_proven"] is False

    link.handle(ASR_PAYLOAD)
    assert link.diagnostics()["asr_source_proven"] is True
