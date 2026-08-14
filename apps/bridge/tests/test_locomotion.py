"""Tests for _locomotion.py -- shared helpers (send_velocity, stop_motion,
stop_motion_sync, maybe_report_progress) used by walk_to, turn, and
stop_everything. Direct tests here catch regressions that would otherwise
break all three silently, since none of their own tests exercise this
module's actual publish/timing logic directly.

`_get_publisher()` lazily creates a real DDS ChannelPublisher on first use,
which crashes outside a running DDS participant (same issue test_stop_
everything.py works around) -- monkeypatch it to a fake that just records
what would have been sent.
"""

from __future__ import annotations

import time

import pytest

from bridge.skills import _locomotion
from bridge.skills.task_runtime import TaskRegistry


class _FakePublisher:
    def __init__(self) -> None:
        self.writes: list[str] = []

    def Write(self, msg: object) -> None:
        self.writes.append(msg.data)  # type: ignore[attr-defined]


@pytest.fixture
def fake_publisher(monkeypatch):
    pub = _FakePublisher()
    monkeypatch.setattr(_locomotion, "_publisher", None)
    monkeypatch.setattr(_locomotion, "_get_publisher", lambda: pub)
    return pub


def test_send_velocity_publishes_expected_payload_shape(fake_publisher):
    _locomotion.send_velocity(0.3, -0.1, 0.5, 0.78)

    assert fake_publisher.writes == ["[0.3, -0.1, 0.5, 0.78]"]


@pytest.mark.asyncio
async def test_stop_motion_sends_zero_velocity_for_full_duration(fake_publisher):
    start = time.time()
    await _locomotion.stop_motion(height=0.78, duration_s=0.06)
    elapsed = time.time() - start

    assert elapsed >= 0.06
    assert len(fake_publisher.writes) >= 2
    assert all(w == "[0.0, 0.0, 0.0, 0.78]" for w in fake_publisher.writes)


def test_stop_motion_sync_sends_zero_velocity_for_full_duration(fake_publisher):
    start = time.time()
    _locomotion.stop_motion_sync(height=0.78, duration_s=0.06)
    elapsed = time.time() - start

    assert elapsed >= 0.06
    assert len(fake_publisher.writes) >= 2
    assert all(w == "[0.0, 0.0, 0.0, 0.78]" for w in fake_publisher.writes)


@pytest.mark.asyncio
async def test_maybe_report_progress_skips_below_delta_threshold():
    registry = TaskRegistry()
    task = registry.create("walk_to")
    task.progress = 0.02  # below PROGRESS_NOTIFY_DELTA (0.05) from a 0.0 watermark

    reported = []

    class _Ctx:
        async def report_progress(self, progress, total, message):
            reported.append((progress, total, message))

    last_reported = await _locomotion.maybe_report_progress(_Ctx(), task, "test", 0.0)

    assert reported == []
    assert last_reported == 0.0


@pytest.mark.asyncio
async def test_maybe_report_progress_emits_above_delta_threshold():
    registry = TaskRegistry()
    task = registry.create("walk_to")
    task.progress = 0.2

    reported = []

    class _Ctx:
        async def report_progress(self, progress, total, message):
            reported.append((progress, total, message))

    last_reported = await _locomotion.maybe_report_progress(_Ctx(), task, "test message", 0.0)

    assert reported == [(0.2, 1.0, "test message")]
    assert last_reported == 0.2


@pytest.mark.asyncio
async def test_maybe_report_progress_none_ctx_is_noop():
    registry = TaskRegistry()
    task = registry.create("walk_to")
    task.progress = 0.9

    last_reported = await _locomotion.maybe_report_progress(None, task, "test", 0.0)

    assert last_reported == 0.0


@pytest.mark.asyncio
async def test_maybe_report_progress_swallows_ctx_exceptions():
    registry = TaskRegistry()
    task = registry.create("walk_to")
    task.progress = 0.5

    class _BrokenCtx:
        async def report_progress(self, progress, total, message):
            raise RuntimeError("boom")

    # Should not raise -- progress reporting is decorative, never load-bearing.
    last_reported = await _locomotion.maybe_report_progress(_BrokenCtx(), task, "test", 0.0)

    assert last_reported == 0.5
