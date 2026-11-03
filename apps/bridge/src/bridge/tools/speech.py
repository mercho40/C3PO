"""Speech tools: `say` (TTS through the voice service) and `listen` (onboard STT)."""

from __future__ import annotations

from typing import Annotated, Literal

import structlog
from pydantic import Field

from bridge.config import SIM_MODE
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

    INTERRUPTION IS FREE AND IMPLICIT for Spanish. `PlayStream`'s `stream_id` is
    the interrupt model — a new id interrupts whatever is playing — and each
    call here mints a new one. So a second `say` barges in over the first rather
    than queueing behind it, with no stop call needed. Firmware TTS has no such
    model and no documented behaviour for overlapping calls.

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
    log.info("say.called", text=text, language=language, sim_mode=SIM_MODE)

    if SIM_MODE != "real":
        return {
            "status": "ok",
            "spoken": text,
            "language": language,
            "note": f"SIM_MODE={SIM_MODE} has no speaker — logged, not spoken.",
            "env": SIM_MODE,
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
                "env": SIM_MODE,
                "stub": False,
            }

        # One stream_id per utterance, minted fresh so this call interrupts
        # whatever is currently playing. Milliseconds is the vendor's own
        # convention; it only has to be different from the last one.
        stream_id = f"c3po-{int(time.time() * 1000)}"

        def _synth_and_play() -> tuple[int, str | None, float]:
            pcm = tts.synthesize(text)
            code, data = g1_rpc.play_pcm(pcm, stream_id)
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
                "env": SIM_MODE,
                "stub": False,
            }

        # PlayStream acks on RECEIPT, not on completion — the vendor's own
        # example fires it and sleeps a fixed 3 s. `speech_seconds` below is
        # computed from the bytes sent, which used to be the only answer
        # available.
        #
        # It is no longer. `rt/audio_msg` carries `play_state`, measured on this
        # robot to track our own PlayStream to within ~100 ms, and it is the
        # real completion signal. The module that reads it existed the whole
        # time and was subscribed by nobody (see main()).
        #
        # The timeout is the byte estimate plus a margin, and not a constant: a
        # long sentence must be allowed to finish, and a firmware that never
        # drops play_state must not wedge this call forever. `False` back from
        # the wait means "it did not end in time", which is reported rather than
        # raised — the speech was still sent.
        playback_confirmed: bool | None = None
        if wait_for_completion and code == 0:
            try:
                from bridge.sdk.audio_msg import get_audio_msg_link

                link = get_audio_msg_link()
                playback_confirmed = await asyncio.to_thread(
                    link.wait_for_playback_end, seconds + PLAYBACK_WAIT_MARGIN_S
                )
            except Exception:
                log.exception("say.wait_for_completion_failed")
                playback_confirmed = None

        return {
            "status": "ok" if code == 0 else "failed",
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
            "env": SIM_MODE,
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
        "env": SIM_MODE,
        "stub": False,
    }


# ---------------------------------------------------------------------------
# Tool: listen
# ---------------------------------------------------------------------------


@mcp.tool(
    meta=skill_meta(
        classification="perception",
        danger_level="low",
        status="real",
        cancellable=False,
        expected_duration_s=0.1,
        works_sim=False,
        works_real=True,
        typical_failure_modes=["mic_never_opened", "stt_not_installed"],
    )
)
async def listen(
    wait_s: Annotated[
        float,
        Field(
            ge=0.0,
            le=30.0,
            description=(
                "0 (the default) returns instantly with whatever has already "
                "been heard — use this while doing something else. Give it a "
                "few seconds ONLY when you have just asked a question and are "
                "waiting for the answer; the call blocks for that long."
            ),
        ),
    ] = 0.0,
) -> dict:
    """What has been said to the robot. Returns instantly by default.

    THE ROBOT IS ALWAYS LISTENING WHEN IT CAN BE — a background thread consumes
    the microphone continuously, so speech is transcribed before you ask for it.
    This tool reads that buffer; it does not start listening, and calling it more
    often does not make the robot hear more.

    Each call CONSUMES what it returns. Two calls in a row will not show the same
    utterance twice, so act on what you get.

    WHETHER THE ROBOT HEARS CONTINUOUSLY DEPENDS ON ITS AUDIO SOURCE, and
    `always_listening` in the result says which you have:

        true   a USB microphone is attached to the Jetson. Continuous, no
               button, and an empty result genuinely means nobody spoke.
        false  the robot's own mic array is in use. It is PUSH-TO-TALK: it
               streams only while a person holds L1+L2 on the remote
               (`docs/ROBOT-HARDWARE.md` §8.2), and no RPC can open it —
               `vui_service` has no capture function. An empty result then
               usually means nobody is holding the remote, NOT that the room is
               silent.

    In the push-to-talk case the two are still distinguishable:

        mic_ever_open: false   nobody has held the button; the robot has never
                               had the chance to hear anything
        mic_ever_open: true    audio has arrived before; empty now means quiet

    Never conclude "nobody answered" from an empty result without checking that
    flag. If it is false, say out loud that you cannot hear unless they hold the
    button — the person may not know.

    Use `wait_s=0` while walking or working: it costs nothing and cannot stall
    anything. Reach for a few seconds only right after asking a question, and
    remember that the whole bridge — including `stop_everything` — waits with
    you for that long.
    """
    if SIM_MODE != "real":
        return {
            "status": "ok",
            "heard": [],
            "transcript": "",
            "stop_heard": None,
            "mic_ever_open": False,
            "note": f"SIM_MODE={SIM_MODE} has no microphone.",
            "env": SIM_MODE,
            "stub": True,
        }

    import asyncio

    from bridge.skills import listen as listen_skill

    listener = listen_skill.get_mic_listener()
    if not listener.is_running():
        ok, why = listen_skill.available()
        if not ok:
            return {
                "status": "failed",
                "heard": [],
                "transcript": "",
                "error": "stt_not_installed",
                "detail": why,
                "env": SIM_MODE,
                "stub": False,
            }
        listener.start()

    items = listener.poll()
    if not items and wait_s > 0:
        # Poll rather than block on a condition: the listener is a plain thread
        # and this keeps the wait cancellable and bounded, with the event loop
        # free the whole time.
        deadline = asyncio.get_running_loop().time() + wait_s
        while not items and asyncio.get_running_loop().time() < deadline:
            await asyncio.sleep(0.25)
            items = listener.poll()

    diag = listener.diagnostics()
    source = listen_skill.describe_audio_source()
    speech = [i for i in items if i["kind"] == "speech"]
    stops = [i for i in items if i["kind"] == "stop"]

    return {
        "status": "ok",
        "heard": [{"text": i["text"], "age_s": i["age_s"]} for i in speech],
        "transcript": " ".join(i["text"] for i in speech).strip(),
        # Reported, not acted on. Whoever is holding the remote to make the robot
        # hear at all already has a physical e-stop under their thumb.
        "stop_heard": stops[0]["text"] if stops else None,
        "mic_ever_open": diag["mic_ever_open"],
        "seconds_since_audio": diag["seconds_since_audio"],
        "listener_error": diag["error"],
        # Whether the robot is listening continuously or only while a button is
        # held. The agent needs this to interpret silence: with always_on true,
        # an empty result really does mean nobody spoke.
        # CONFIGURED vs OBSERVED, and the agent needs the second one.
        # `audio_flowing` is measured — audio arrived in the last few seconds.
        # `always_listening` only says the SOURCE is a type that cannot gate,
        # and the robot's own multicast feed has been seen both gated and
        # free-running on the same day, so it is not a reliable guide.
        "audio_flowing": diag.get("audio_flowing", False),
        "always_listening": source["always_on"],
        "audio_source": source["source"],
        "note": (
            None
            if diag.get("audio_flowing") or diag["mic_ever_open"] or source["always_on"]
            else "No audio has arrived. The robot's own microphone has been seen "
            "BOTH gated on the remote's L1+L2 wake-up mode and free-running with "
            "no remote at all, so silence here is not proof of either. It does "
            "mean nothing was recorded — do not read it as a quiet room. Holding "
            "L1+L2 is the known way to open the feed."
        ),
        "env": SIM_MODE,
        "stub": False,
    }
