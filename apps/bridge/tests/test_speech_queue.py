"""One queue for the robot's Spanish speech: every `say` (agent, confirm line, motion warning,
greeter) plays after the one before it ends; only a stop cuts in, and it drops the queue."""

from __future__ import annotations

import asyncio
import threading

import pytest

from bridge import mcp_server
from bridge.sdk import audio_msg, g1_rpc
from bridge.skills import tts


class _Link:
    """rt/audio_msg's end-of-playback: set by the test when the speaker goes quiet."""

    def __init__(self) -> None:
        self.end = threading.Event()

    def mark_play_pending(self) -> None:
        self.end.clear()

    def wait_for_playback_end(self, timeout_s: float) -> bool:
        return self.end.wait(timeout_s)


@pytest.fixture
def robot(monkeypatch):
    played: list[str] = []
    link = _Link()
    monkeypatch.setattr(mcp_server, "SIM_MODE", "real")
    monkeypatch.setattr(tts, "available", lambda: (True, ""))
    monkeypatch.setattr(tts, "synthesize", lambda text: text.encode())
    monkeypatch.setattr(tts, "duration_s", lambda pcm: 5.0)
    monkeypatch.setattr(audio_msg, "get_audio_msg_link", lambda: link)

    def play_pcm(pcm, stream_id, generation=None):
        if generation is not None and generation != g1_rpc._play_generation:
            return 0, "stopped"
        played.append(pcm.decode())
        return 0, None

    monkeypatch.setattr(g1_rpc, "play_pcm", play_pcm)
    yield played, link
    link.end.set()  # let any background tail finish


async def _until(cond, timeout_s: float = 2.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout_s
    while not cond():
        assert asyncio.get_running_loop().time() < deadline, "timed out"
        await asyncio.sleep(0.01)


async def test_a_second_say_waits_for_the_first_to_finish_playing(robot):
    played, link = robot
    first = asyncio.create_task(mcp_server.say("uno", wait_for_completion=True))
    await _until(lambda: played == ["uno"])
    second = asyncio.create_task(mcp_server.say("dos", wait_for_completion=True))
    await asyncio.sleep(0.3)
    assert played == ["uno"]  # «uno» still plays: «dos» waits its turn, no barge-in
    link.end.set()
    assert (await first)["status"] == "ok"
    await _until(lambda: played == ["uno", "dos"])
    link.end.set()
    assert (await second)["status"] == "ok"


async def test_a_say_that_does_not_wait_still_holds_the_queue_until_it_ends(robot):
    played, link = robot
    assert (await mcp_server.say("Confirmado: ejecuté pick."))["status"] == "ok"  # returns at once
    nxt = asyncio.create_task(mcp_server.say("¿Algo más?", wait_for_completion=True))
    await asyncio.sleep(0.3)
    assert played == ["Confirmado: ejecuté pick."]
    link.end.set()
    await _until(lambda: len(played) == 2)
    link.end.set()
    await nxt


async def test_a_stop_cuts_the_speech_and_drops_what_is_queued(robot):
    played, link = robot
    first = asyncio.create_task(mcp_server.say("uno", wait_for_completion=True))
    await _until(lambda: played == ["uno"])
    queued = asyncio.create_task(mcp_server.say("dos", wait_for_completion=True))
    await asyncio.sleep(0.05)
    g1_rpc._play_generation += 1  # PARAR: stop_play
    assert (await first)["status"] == "stopped"  # cut: not heard in full
    assert (await queued)["status"] == "stopped"
    assert played == ["uno"]


async def test_the_last_utterances_end_does_not_end_the_wait_for_this_one(robot):
    played, link = robot
    link.end.set()  # still set from the previous utterance
    said = asyncio.create_task(mcp_server.say("uno", wait_for_completion=True))
    await _until(lambda: played == ["uno"])
    await asyncio.sleep(0.3)
    assert not said.done()  # still playing
    link.end.set()
    assert (await said)["playback_confirmed"] is True


async def test_the_queue_counts_as_busy_while_speech_waits_or_just_ended(robot):
    played, link = robot
    said = asyncio.create_task(mcp_server.say("uno", wait_for_completion=True))
    await _until(lambda: played == ["uno"])
    assert mcp_server._speech_busy()
    link.end.set()
    await said
    assert mcp_server._speech_busy()  # the gap before a reply's next sentence
