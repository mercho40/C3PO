"""listen: what the robot's microphone heard, transcribed on the robot."""

from __future__ import annotations

import asyncio
from typing import Annotated

import structlog
from pydantic import Field

from bridge import config
from bridge.server import mcp
from bridge.skill_meta import meta as skill_meta

log = structlog.get_logger(__name__)


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
    if config.SIM_MODE != "real":
        return {
            "status": "ok",
            "heard": [],
            "transcript": "",
            "stop_heard": None,
            "mic_ever_open": False,
            "note": f"SIM_MODE={config.SIM_MODE} has no microphone.",
            "env": config.SIM_MODE,
            "stub": True,
        }


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
                "env": config.SIM_MODE,
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
    source = await asyncio.to_thread(listen_skill.describe_audio_source)  # runs `arecord -l`
    speech = [i for i in items if i["kind"] == "speech"]
    stops = [i for i in items if i["kind"] == "stop"]

    return {
        "status": "ok",
        "heard": [{"text": i["text"], "age_s": i["age_s"]} for i in speech],
        "transcript": " ".join(i["text"] for i in speech).strip(),
        # Reported here. The bridge acts on it by itself only while a voice
        # session (realtime or legacy) is connected; see MicListener.
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
        "env": config.SIM_MODE,
        "stub": False,
    }
