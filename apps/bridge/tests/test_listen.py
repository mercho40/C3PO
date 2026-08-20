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

@pytest.mark.parametrize("heard", [
    "emergencia", "EMERGENCIA", "eh emergencia", "para para", "detener ya"])
def test_fragments_and_repeats_all_count_as_a_stop(heard):
    """What a real shout decodes to mid-partial: fragments, repeats, case.
    Exact matching rejects all of these."""
    assert listen.match_phrase(heard, listen.DEFAULT_STOP_PHRASES) is not None


@pytest.mark.parametrize("heard", [
    "", "hola", "seguí adelante", "girá a la derecha",
    # THE ONE THAT DROVE THE PHRASE LIST. "para" is the commonest preposition in
    # Spanish, and this model decodes a shouted "pará" as "para" — so a bare
    # "para" cannot be a stop word without halting the robot mid-sentence
    # whenever anyone explains what it is for.
    "estoy aquí para ayudarte con el proyecto",
    "voy a caminar hasta la puerta para buscar la caja"])
def test_ordinary_speech_is_not_a_stop(heard):
    assert listen.match_phrase(heard, listen.DEFAULT_STOP_PHRASES) is None


def test_stop_phrases_are_ones_this_model_can_actually_decode():
    """Measured, not chosen for how they read. On this model, synthesised
    es_AR: "pará"->"para", "alto"->"alta", "stop"->"sí", "basta ya"->"las
    tasas", and "frená" is not in the vocabulary at all. Keeping any of them
    would be a stop word that never fires, or one that fires constantly."""
    banned = {"pará", "para", "alto", "stop", "frená", "basta"}
    assert not (banned & set(listen.DEFAULT_STOP_PHRASES)), (
        "a phrase this model mis-decodes was re-added to the stop list")
    assert "emergencia" in listen.DEFAULT_STOP_PHRASES


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


# --- the loop (robot only) --------------------------------------------------

def _frames(pcm: bytes, size: int = listen.FRAME_BYTES):
    return (pcm[i : i + size] for i in range(0, len(pcm), size))


@needs_audio
def test_the_loop_transcribes_a_command_and_does_not_stop_on_it():
    """The everyday case: someone asks for something and the robot hears it.

    Asserted on CONTENT WORDS rather than a full string. The recogniser is a
    39 MB small model — "C3PO" comes out as "cual hace tres por", which is fine
    and not worth pinning; what matters is that the actionable part survives.
    """
    from bridge.skills import tts

    heard, stops = [], []
    clip = tts.synthesize("Hola C3PO. Caminá hasta la puerta y buscá la caja azul.")
    listen.listen_loop(_frames(clip), on_text=heard.append, on_stop=stops.append)

    text = " ".join(heard)
    for word in ("puerta", "caja", "azul"):
        assert word in text, f"{word!r} missing from transcript {text!r}"
    assert stops == [], "an ordinary command must not read as a stop"


@needs_audio
def test_the_loop_fires_the_stop_and_still_transcribes_it():
    from bridge.skills import tts

    heard, stops = [], []
    clip = tts.synthesize("emergencia")
    listen.listen_loop(_frames(clip), on_text=heard.append, on_stop=stops.append)

    assert stops, "the stop phrase did not fire"
    assert "emergencia" in " ".join(heard)


@needs_audio
def test_a_clip_with_no_trailing_silence_is_not_swallowed():
    """Regression: the loop once returned an empty transcript for a good clip.

    Vosk only emits a final result at an utterance boundary, so a finite source
    leaves its last — often only — utterance inside the decoder. Without the
    flush this returns [] and reads as a broken recogniser. A live mic never
    reaches the end of its stream, which is why this needed a test to catch.
    """
    from bridge.skills import tts

    heard = []
    clip = tts.synthesize("la caja azul")
    listen.listen_loop(_frames(clip), on_text=heard.append)
    assert heard, "final utterance was swallowed — flush() is missing"


@needs_audio
def test_transcribe_pcm_is_the_offline_entry_point():
    from bridge.skills import tts

    assert any("puerta" in t for t in
               listen.transcribe_pcm(tts.synthesize("abrí la puerta")))
