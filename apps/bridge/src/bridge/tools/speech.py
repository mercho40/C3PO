"""Speech: say, its one FIFO queue, and ask_user.

Every Spanish utterance (the agent's, the motion warning's, the greeter's, the console's)
goes through `say` and its queue. Importing this module also hands `say` to
bridge.server as the motion warning's speaker (server.set_speaker)."""

from __future__ import annotations

import asyncio
import time
from typing import Annotated, Any, Literal

import structlog
from pydantic import Field

from bridge import config, server
from bridge.server import mcp
from bridge.skill_meta import meta as skill_meta

log = structlog.get_logger(__name__)


# How much longer than the byte-derived estimate `say(wait_for_completion=True)`
# will wait for `play_state` to drop. Playback runs slightly past the estimate
# (measured 1->0 gaps of 0.52/2.10/3.07 s against sends of 0.5/2.0/3.0 s), and
# the firmware is a shared service that can simply not answer — so the wait is
# bounded and a timeout is reported, never raised.
PLAYBACK_WAIT_MARGIN_S = 2.0


# ---------------------------------------------------------------------------
# Tool: say
# ---------------------------------------------------------------------------

# Every Spanish say goes through one FIFO queue, held from synthesis to the end of
# playback: the agent, apps/back's confirm line, the motion warning and the greeter all
# speak in arrival order, and only a stop (stop_play bumps _play_generation) cuts in.
_speech: dict[str, Any] = {"loop": None, "lock": None, "ended_at": -1e9, "tails": set()}


# The greeter's "a conversation is going on": this long after our last utterance ended.
SPEECH_HOLD_S = 3.0


def _speech_lock() -> asyncio.Lock:
    # Per loop: the server runs one; tests run one each, and a Lock binds to its loop.
    loop = asyncio.get_running_loop()
    if _speech["loop"] is not loop:
        _speech.update(loop=loop, lock=asyncio.Lock())
    return _speech["lock"]


def _speech_release(lock: asyncio.Lock) -> None:
    _speech["ended_at"] = time.monotonic()
    lock.release()


def speech_busy() -> bool:
    """Speech playing, queued, or just ended (the gap between a reply's sentences)."""
    lock = _speech["lock"]
    return bool(lock and lock.locked()) or time.monotonic() - _speech["ended_at"] < SPEECH_HOLD_S


def _playback_end(link: Any, timeout_s: float, generation: int) -> bool:
    """True when play_state dropped; False on timeout, or soon after a stop (a stop before
    the firmware reported playing is never followed by an end)."""
    from bridge.sdk import g1_rpc

    deadline = time.monotonic() + timeout_s
    while (left := deadline - time.monotonic()) > 0:
        if link.wait_for_playback_end(min(left, 0.1)):
            return True
        if g1_rpc._play_generation != generation:
            return False
    return False


async def _speech_tail(lock: asyncio.Lock, link: Any, timeout_s: float, generation: int) -> None:
    """A wait_for_completion=False say: the queue stays held until its audio ends."""
    try:
        await asyncio.to_thread(_playback_end, link, timeout_s, generation)
    finally:
        _speech_release(lock)


@mcp.tool(
    meta=skill_meta(
        classification="speech",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=3.0,
        works_sim=False,
        works_real=True,
        typical_failure_modes=["voice_service_no_answer", "bridge_disconnected"],
    )
)
async def say(
    text: Annotated[
        str,
        Field(
            min_length=1,
            max_length=500,
            description="What the robot should say, spoken aloud through its own speaker.",
        ),
    ],
    language: Annotated[
        Literal["spanish", "english", "chinese"],
        Field(
            description=(
                "Language of `text`. **spanish** is this deployment's operating "
                "language and the default; it is synthesised on the robot and "
                "played as audio, so it does not use the firmware's voices at "
                "all. english/chinese use the firmware's own two voices. Match "
                "this to the text you actually wrote — labelling Spanish as "
                "english does not fail, it produces an English voice attempting "
                "Spanish phonemes. One language per utterance; send separate "
                "calls to switch."
            )
        ),
    ] = "spanish",
    wait_for_completion: Annotated[
        bool,
        Field(
            description=(
                "Return only once the robot has actually STOPPED talking, "
                "rather than as soon as the audio has been handed to the "
                "firmware. Off by default, which keeps this tool fast. Turn it "
                "on when the next thing you do would talk over this — chaining "
                "two utterances, or listening for a reply, since the robot's "
                "own microphone hears its own speaker."
            )
        ),
    ] = False,
) -> dict:
    """Speak text aloud through the robot's own speaker (voice service, TTS).

    TWO DIFFERENT MECHANISMS LIVE BEHIND ONE TOOL, and the split is forced by
    the firmware rather than chosen:

      spanish  -> synthesised locally by Piper (`es_AR/daniela`), resampled to
                  16 kHz, and pushed as PCM through `PlayStream` (1003).
      en / zh  -> the firmware's own TTS (1001), `speaker_id` 1 / 0.

    The firmware has exactly two voices and no third. It was verified on this
    robot that neither reads Spanish intelligibly, and — the part that matters —
    passing Spanish to firmware TTS returns **rpc_code 0** while emitting
    unusable audio. A false success, which is why Spanish never touches that
    path here and why this tool refuses loudly instead of falling back to it
    when local synthesis is unavailable. Silence with an explanation beats
    confident noise. See `docs/DECISIONS.md` D6.1/D6.3.

    Everything is local and offline: no cloud, no API key, and Spanish still
    works with the network down — which is exactly when "estoy atascado" is
    worth saying. Logs only on stub and sim, which have no speaker.

    SPANISH SPEECH QUEUES. Every Spanish `say` (yours, the motion warning, the
    greeter, the console's lines) waits for the one before it to finish playing,
    in arrival order, so nothing talks over anything. Only stop_everything cuts
    speech, and it drops what is still queued (status 'stopped'). Firmware TTS
    has no such queue and no documented behaviour for overlapping calls.

    The robot's speaker is SHARED with the co-tenant `gemm` stack and there is
    no arbitration. We publish under our own `app_name` ("c3po"), so their
    assistant cannot stop our speech and we cannot stop theirs — expect
    occasional talking over each other; it is not a bug to fix here.

    Worth reaching for more than it sounds. Speech is not gated by the
    locomotion FSM, so it is a channel the robot still has when motion is being
    refused — which is a situation this robot gets into. Saying what you are
    about to do, or that you are stuck, is usually better than silence when a
    person is standing next to a humanoid.

    Measured rather than assumed, 2026-08-21: the voice service answered
    GET_VOLUME with rpc_code 0 while fsm_id was 0 (ZeroTorque) — the robot limp,
    with motion categorically unavailable and another stack owning it. What that
    establishes is that the SERVICE is reachable in that state; it is not proof
    that audio reached the speaker, which cannot be checked without making noise
    in a shared room. That confirmation has since happened: Spanish played audibly
    through the speaker on 2026-08-21, so the whole path is proven, not merely
    accepted by the RPC.

    One utterance at a time, and one language per utterance: the firmware has
    no mixed Chinese/English voice. There is also no documented behaviour for
    calling it while speech is already playing, so this is not the tool to use
    for anything that must be interruptible — `PlayStream` is, and its
    `stream_id` is the interrupt model (same id concatenates, new id barges in).
    """
    log.info("say.called", text=text, language=language, sim_mode=config.SIM_MODE)

    if config.SIM_MODE != "real":
        return {
            "status": "ok",
            "spoken": text,
            "language": language,
            "note": f"SIM_MODE={config.SIM_MODE} has no speaker — logged, not spoken.",
            "env": config.SIM_MODE,
            "stub": True,
        }

    import asyncio
    import time

    from bridge.sdk import g1_protocol, g1_rpc

    if language == "spanish":
        from bridge.skills import tts

        ok, why = tts.available()
        if not ok:
            # Refuse rather than degrade. The available fallback is the English
            # voice reading Spanish, which is precisely the rpc_code-0-and-noise
            # failure this path exists to avoid — it would look like success in
            # the logs and be unusable in the room.
            return {
                "status": "failed",
                "spoken": None,
                "language": language,
                "error": "spanish_tts_unavailable",
                "detail": why,
                "note": (
                    "Refusing to fall back to the English voice: it reads Spanish "
                    "unintelligibly while reporting success. Install Piper, or "
                    "call again with language='english' and English text."
                ),
                "env": config.SIM_MODE,
                "stub": False,
            }

        # Before queueing and before synthesis (hundreds of ms): a PARAR while this waits
        # its turn, or while Piper works, must cancel it too.
        generation = g1_rpc._play_generation
        lock = _speech_lock()
        await lock.acquire()
        held = True
        try:
            if g1_rpc._play_generation != generation:
                return {
                    "status": "stopped",
                    "spoken": None,
                    "language": language,
                    "note": "A stop came while this waited its turn: not spoken.",
                    "env": config.SIM_MODE,
                    "stub": False,
                }
            # One stream_id per utterance (a new id would barge in; the queue makes that
            # moot). Milliseconds is the vendor's own convention.
            stream_id = f"c3po-{int(time.time() * 1000)}"
            try:
                from bridge.sdk.audio_msg import get_audio_msg_link

                link = get_audio_msg_link()
            except Exception:
                log.exception("say.audio_link_failed")
                link = None

            def _synth_and_play() -> tuple[int, str | None, float]:
                pcm = tts.synthesize(text)
                if link is not None:
                    # The last utterance's end is still set: a wait must not return on it.
                    link.mark_play_pending()
                code, data = g1_rpc.play_pcm(pcm, stream_id, generation=generation)
                return code, data, tts.duration_s(pcm)

            try:
                # Both halves off the event loop. Synthesis is CPU-bound and
                # PlayStream is a blocking RPC per chunk; on the loop, a long
                # sentence would stall every other tool call — stop_everything
                # included, which is the one that must never wait behind speech.
                code, data, seconds = await asyncio.to_thread(_synth_and_play)
            except tts.TtsUnavailable as exc:
                return {
                    "status": "failed",
                    "spoken": None,
                    "language": language,
                    "error": "spanish_tts_failed",
                    "detail": str(exc),
                    "env": config.SIM_MODE,
                    "stub": False,
                }

            # PlayStream acks on RECEIPT, not on completion. `rt/audio_msg`'s
            # `play_state`, measured on this robot to track our own PlayStream to
            # within ~100 ms, is the real completion signal (see main()).
            #
            # The timeout is the byte estimate plus a margin, and not a constant: a
            # long sentence must be allowed to finish, and a firmware that never
            # drops play_state must not wedge the queue forever. `False` back from
            # the wait means "it did not end in time", which is reported rather than
            # raised — the speech was still sent. Without wait_for_completion the
            # queue still waits, in the background, so the next say cannot cut this.
            playback_confirmed: bool | None = None
            if code == 0 and link is not None:
                timeout_s = seconds + PLAYBACK_WAIT_MARGIN_S
                if wait_for_completion:
                    playback_confirmed = await asyncio.to_thread(
                        _playback_end, link, timeout_s, generation
                    )
                else:
                    tail = asyncio.create_task(_speech_tail(lock, link, timeout_s, generation))
                    _speech["tails"].add(tail)
                    tail.add_done_callback(_speech["tails"].discard)
                    held = False
            cut = g1_rpc._play_generation != generation
        finally:
            if held:
                _speech_release(lock)

        return {
            # stopped: a stop cut it or it never started, so it was not heard in full.
            "status": "failed" if code != 0 else "stopped" if cut else "ok",
            "spoken": text if code == 0 else None,
            "language": language,
            "rpc_code": code,
            "rpc_data": data,
            "speech_seconds": round(seconds, 2),
            # None when we did not wait, True when playback was observed to
            # end, False when it did not within the timeout. Three states,
            # because "we didn't look" and "it didn't stop" are different facts.
            "playback_confirmed": playback_confirmed,
            "stream_id": stream_id,
            "via": "piper+playstream",
            "error": None if code == 0 else f"rpc_error_code_{code}",
            "env": config.SIM_MODE,
            "stub": False,
        }

    speaker_id = (
        g1_protocol.Speaker.CHINESE if language == "chinese" else g1_protocol.Speaker.ENGLISH
    )
    # Off the event loop: TTS acks on completion of synthesis, so a long
    # sentence would otherwise stall every other tool call — including
    # stop_everything.
    code, data = await asyncio.to_thread(g1_rpc.speak, text, speaker_id)

    return {
        "status": "ok" if code == 0 else "failed",
        "spoken": text if code == 0 else None,
        "language": language,
        "rpc_code": code,
        "rpc_data": data,
        "via": "firmware_tts",
        "error": None if code == 0 else f"rpc_error_code_{code}",
        "env": config.SIM_MODE,
        "stub": False,
    }


@mcp.tool(
    meta=skill_meta(
        classification="speech",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=4.0,
        works_sim=False,
        works_real=False,
        typical_failure_modes=["voice_service_no_answer", "spanish_tts_unavailable"],
    )
)
async def ask_user(
    question: Annotated[
        str,
        Field(min_length=1, max_length=300, description="The clarifying question, spoken aloud."),
    ],
    language: Annotated[
        Literal["spanish", "english", "chinese"],
        Field(description="Language of `question`; spanish is the default, as for `say`."),
    ] = "spanish",
) -> dict:
    """Ask the people near the robot a clarifying question, out loud, and return.

    Use it when the request is ambiguous ("which cup?") rather than guessing. It
    speaks through `say` and waits for the speech to finish; it does NOT wait
    for the answer. Then stop and let the operator reply — in the console they
    type it; on the robot, call `listen` (push-to-talk).
    """
    spoken = await say(text=question, language=language, wait_for_completion=True)
    return {
        "asked": question,
        "speech": spoken,
        "next": "Wait for the reply: the operator types it, or call listen on the robot.",
    }


async def speak_and_wait(text: str) -> Any:
    """`say`, returning at the end of playback: what the greeter and the motion warning speak
    through, so PARAR's stop_play cuts them like any speech. The greeter waits because
    apps/back's listen window opens on the greeting's record."""
    return await say(text, wait_for_completion=True)


# The motion warning in server.GovernedMCP.call_tool speaks through it too (injected:
# server.py cannot import `say`, which registers on server.mcp).
server.set_speaker(speak_and_wait)
