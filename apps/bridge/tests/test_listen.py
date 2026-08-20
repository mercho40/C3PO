"""The spoken stop. Safety-critical, and currently sourceless.

The microphone does not stream at rest (ROBOT-HARDWARE.md §8.2), so the obvious
move would be to leave this untested until it does. That is backwards: the
recogniser is the part that has to be right, the mic is a byte source, and D6.1
is explicit that a stop which works in demos and not in panic is worse than
none. So the phrases are driven through the decoder from SYNTHESISED SPEECH —
the TTS half built earlier is the test signal for the STT half.

Two tiers:
  * matching logic and grammar — pure, run everywhere, including the Mac where
    vosk has no wheel;
  * decode-real-audio — needs vosk AND piper, so robot-only, and skipped with a
    reason rather than silently passing.

What synthesised speech CANNOT establish: whether this fires for a stressed
human, which is the case that actually matters. Piper is clean, level and
unhurried. Treat green here as "the wiring decodes Spanish", not as "the stop
phrase is validated" — that needs recordings of real people, ideally shouting.
"""

from __future__ import annotations

import pytest

from bridge.skills import listen

vosk_ok, vosk_why = listen.available()


def _tts_ready() -> tuple[bool, str]:
    try:
        from bridge.skills import tts
    except ImportError as exc:                      # pragma: no cover
        return False, str(exc)
    return tts.available()


tts_ok, tts_why = _tts_ready()
needs_audio = pytest.mark.skipif(
    not (vosk_ok and tts_ok),
    reason=f"needs vosk and piper on the robot — vosk: {vosk_why or 'ok'}; piper: {tts_why or 'ok'}",
)


# --- matching logic (no vosk needed) ---------------------------------------

@pytest.mark.parametrize("heard", ["pará", "PARÁ", "eh pará", "pará pará", "para ya"])
def test_fragments_and_repeats_all_count_as_a_stop(heard):
    """What a real shout decodes to, mid-partial. Exact matching rejects all of these."""
    assert listen.match_phrase(heard, listen.DEFAULT_STOP_PHRASES) is not None


@pytest.mark.parametrize("heard", ["", "hola", "seguí adelante", "girá a la derecha"])
def test_ordinary_speech_is_not_a_stop(heard):
    assert listen.match_phrase(heard, listen.DEFAULT_STOP_PHRASES) is None


def test_the_grammar_carries_the_unknown_escape_hatch():
    """Without `[unk]` the decoder must map ALL speech onto the listed phrases,
    so unrelated talking is forced into a spurious "pará" — a restricted grammar
    with no escape hatch is a stop button that presses itself."""
    import json
    grammar = json.loads(listen._grammar(["pará"]))
    assert "[unk]" in grammar, "grammar must allow the decoder to say 'none of these'"
    assert "pará" in grammar


def test_the_english_stop_is_included():
    """Understood under stress by speakers who freeze in their second language."""
    assert "stop" in listen.DEFAULT_STOP_PHRASES


def test_mic_constants_match_the_documented_feed():
    assert listen.MIC_GROUP == "239.168.123.161"
    assert listen.MIC_PORT == 5555
    assert listen.MIC_SAMPLE_RATE == 16000
    assert listen.MIC_IFACE_PREFIX == "192.168.123.", (
        "binding any other interface yields zero packets with no error")


# --- real decoding (robot only) --------------------------------------------

@needs_audio
@pytest.mark.parametrize("spoken", ["pará", "pará, pará", "alto", "stop"])
def test_synthesised_stop_phrases_are_actually_heard(spoken):
    """End to end through the decoder: Spanish audio in, stop phrase out."""
    from bridge.skills import tts

    hit = listen.detect_in_pcm(tts.synthesize(spoken))
    assert hit is not None, f"the decoder did not hear a stop in {spoken!r}"


@needs_audio
def test_unrelated_spanish_does_not_trigger_a_stop():
    """False positives are cheap but not free — a robot that halts whenever
    anyone speaks is one nobody will keep switched on."""
    from bridge.skills import tts

    benign = "Hola, me llamo C3PO y estoy aquí para ayudarte con el proyecto."
    assert listen.detect_in_pcm(tts.synthesize(benign)) is None
