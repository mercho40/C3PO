"""`rt/audio_msg` — the robot's ear and its mouth's completion signal, one topic.

This is the LISTENING side's first working piece. Two unrelated payload shapes
ride the same topic (docs/ROBOT-API.md §7.1), and conflating them is the obvious
bug this module exists to prevent:

    {"index":1,"text":"hola","angle":90,"speaker_id":0,"language":"es-ES",...}
    {"play_state":1}

The first is firmware ASR output — recognised speech, plus `angle`, a
direction-of-arrival in degrees that costs us nothing and would otherwise need a
mic array we cannot access per-element. The second is playback state.

WHAT IS PROVEN AND WHAT IS NOT, measured 2026-08-21:

  * `play_state` WORKS, and fires for OUR `PlayStream`, not just the vendor
    assistant's. Sent 0.5/2.0/3.0 s of audio, measured 1->0 gaps of
    0.52/2.10/3.07 s. That is true completion detection, and it settles open
    question 23. It also independently confirms the byte-derived estimate in
    `skills/tts.py::duration_s` to within ~100 ms.
  * `text` HAS NEVER ARRIVED. Eight seconds of idle listening produced nothing,
    which is consistent with the firmware ASR being gated on the remote's
    wake-up mode exactly as the raw mic feed is (ROBOT-HARDWARE.md §8.2). So the
    ASR half here is PLUMBING WITHOUT A PROVEN SOURCE — wired, parsed and
    tested, but do not report it as a working microphone.

That asymmetry is the whole reason both halves live in one module with one
subscription: the topic is live and we demonstrably receive from it, so if ASR
ever starts publishing, nothing needs to be built to catch it.
"""

from __future__ import annotations

import json
import threading
import time
from collections.abc import Callable
from typing import Any

import structlog

log = structlog.get_logger(__name__)

AUDIO_MSG_TOPIC = "rt/audio_msg"

# An ASR result older than this is history, not a thing being said now. Speech
# arrives in bursts and a stale line is worse than none: an agent that acts on a
# minute-old "pará" is acting on a command already obeyed or abandoned.
ASR_STALE_AFTER_S = 10.0


def parse_audio_msg(raw: str) -> dict[str, Any] | None:
    """One payload -> a tagged dict, or None if it is neither shape.

    Pure and DDS-free so the dispatch can be tested without a robot. Returns
    `{"kind": "play_state", "playing": bool}` or
    `{"kind": "asr", "text": str, ...}`.

    Note the ordering: `play_state` is checked FIRST and a payload carrying it is
    never treated as ASR. A `{"play_state":0}` message has no `text`, so a
    text-first reader would silently drop it and playback tracking would just
    stop working — with no error, and only under conditions where speech happens
    to be playing.
    """
    try:
        payload = json.loads(raw)
    except (ValueError, TypeError):
        return None
    if not isinstance(payload, dict):
        return None

    if "play_state" in payload:
        state = payload["play_state"]
        # Documented as 0/1; accept anything truthy rather than assuming ints,
        # since this field is [web]-sourced and one firmware already surprised
        # us by returning a bare array where docs promised an envelope.
        return {"kind": "play_state", "playing": bool(state)}

    text = payload.get("text")
    if isinstance(text, str) and text:
        return {
            "kind": "asr",
            "text": text,
            # 0-180. Free direction-of-arrival: the beamforming happens on the
            # control board and this is the only per-utterance spatial cue we
            # get, since the four mic elements are pre-mixed to one channel.
            "angle": payload.get("angle"),
            "language": payload.get("language"),
            "confidence": payload.get("confidence"),
            # Streaming mode is off by default, so this is normally absent and
            # every result is final. Do not require it.
            "is_final": payload.get("is_final", True),
            "speaker_id": payload.get("speaker_id"),
        }
    return None


class AudioMsgLink:
    """Subscribes once to `rt/audio_msg`; serves playback state and ASR text."""

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._sub: Any = None
        self._started = False

        self._playing = False
        self._play_changed_at: float | None = None
        # Fires on every 1->0 transition; how wait_for_playback_end() blocks
        # without polling.
        self._play_end = threading.Event()

        self._asr: dict[str, Any] | None = None
        self._asr_at: float | None = None
        self._asr_count = 0
        self._play_events = 0
        self._on_asr: Callable[[dict[str, Any]], None] | None = None

    # -- ingest --------------------------------------------------------------

    def handle(self, raw: str) -> dict[str, Any] | None:
        """Process one payload. Separated from DDS so tests can drive it."""
        parsed = parse_audio_msg(raw)
        if parsed is None:
            return None

        if parsed["kind"] == "play_state":
            with self._lock:
                self._play_events += 1
                was, self._playing = self._playing, parsed["playing"]
                self._play_changed_at = self._clock()
                ended = was and not self._playing
            if ended:
                self._play_end.set()
            elif parsed["playing"]:
                self._play_end.clear()
            return parsed

        with self._lock:
            self._asr = parsed
            self._asr_at = self._clock()
            self._asr_count += 1
            callback = self._on_asr
        log.info("audio_msg.asr", text=parsed["text"], angle=parsed.get("angle"))
        if callback is not None:
            # Outside the lock: a callback that speaks or plans must not be able
            # to deadlock the subscriber thread against its own reader.
            try:
                callback(parsed)
            except Exception:
                log.exception("audio_msg.asr_callback_failed")
        return parsed

    def on_asr(self, callback: Callable[[dict[str, Any]], None] | None) -> None:
        """Register a listener for recognised speech. None clears it."""
        with self._lock:
            self._on_asr = callback

    # -- lifecycle -----------------------------------------------------------

    def start(self) -> None:
        if self._started:
            return
        from unitree_sdk2py.core.channel import ChannelSubscriber
        from unitree_sdk2py.idl.std_msgs.msg.dds_ import String_

        self._sub = ChannelSubscriber(AUDIO_MSG_TOPIC, String_)
        self._sub.Init(lambda msg: self.handle(msg.data), 10)
        self._started = True
        log.info("audio_msg.started", topic=AUDIO_MSG_TOPIC)

    # -- playback ------------------------------------------------------------

    def is_playing(self) -> bool:
        with self._lock:
            return self._playing

    def wait_for_playback_end(self, timeout_s: float) -> bool:
        """Block until playback stops. True if it ended, False on timeout.

        The honest completion signal for `say`, replacing "sleep for as long as
        we think the audio was". Callers must still pass a timeout: `play_state`
        is a firmware signal on a shared service, and waiting forever for a robot
        to stop talking is how a tool call wedges the transport.
        """
        return self._play_end.wait(timeout_s)

    # -- listening -----------------------------------------------------------

    def latest_asr(self) -> tuple[dict[str, Any] | None, float | None]:
        """Most recent recognised utterance and its age, or (None, None).

        Returns (None, None) once stale rather than a stale line, on the same
        reasoning as the perception link's report handling: absent is honest,
        old-and-presented-as-current is not.
        """
        with self._lock:
            if self._asr is None or self._asr_at is None:
                return None, None
            age = self._clock() - self._asr_at
            if age > ASR_STALE_AFTER_S:
                return None, None
            return dict(self._asr), round(age, 3)

    def diagnostics(self) -> dict[str, Any]:
        with self._lock:
            return {
                "started": self._started,
                "topic": AUDIO_MSG_TOPIC,
                "playing": self._playing,
                "play_state_events": self._play_events,
                "asr_messages": self._asr_count,
                # Distinguishes "the topic is dead" from "the topic is alive and
                # the microphone is gated" — the difference between a broken
                # subscription and a robot that simply is not listening yet.
                "asr_source_proven": self._asr_count > 0,
            }


_link: AudioMsgLink | None = None
_link_lock = threading.Lock()


def get_audio_msg_link() -> AudioMsgLink:
    global _link
    with _link_lock:
        if _link is None:
            _link = AudioMsgLink()
        return _link
