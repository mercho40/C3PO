"""Tests for the landmark store (`bridge.skills.landmarks`).

Landmarks persist to disk so they survive a bridge restart — which is safe
because the odometry frame their coordinates live in is produced by the
robot's control board, not by us, and outlives our process.

A *robot* reboot is the dangerous case: the estimator re-origins and every
saved coordinate silently starts pointing somewhere else. The frame-guard
tests below cover that, and they matter more than the persistence ones —
a landmark that navigates the robot to the wrong place is worse than one
that was forgotten.
"""

from __future__ import annotations

from bridge.skills.landmarks import LandmarkStore, frame_is_stale


def test_remember_then_recall_round_trips_pose():
    store = LandmarkStore()
    pose = {"x_meters_world": 1.5, "y_meters_world": -2.0, "yaw_radians_world": 0.3}

    store.remember("kitchen", pose)
    landmark = store.recall("kitchen")

    assert landmark is not None
    assert landmark.x_meters_world == 1.5
    assert landmark.y_meters_world == -2.0
    assert landmark.yaw_radians_world == 0.3


def test_recall_unknown_name_returns_none():
    store = LandmarkStore()
    assert store.recall("nope") is None


def test_remember_overwrites_existing_name():
    store = LandmarkStore()
    store.remember("dock", {"x_meters_world": 0.0, "y_meters_world": 0.0, "yaw_radians_world": 0.0})
    store.remember("dock", {"x_meters_world": 5.0, "y_meters_world": 5.0, "yaw_radians_world": 1.0})

    landmark = store.recall("dock")
    assert landmark.x_meters_world == 5.0


def test_list_all_orders_most_recent_first():
    store = LandmarkStore()
    pose = {"x_meters_world": 0.0, "y_meters_world": 0.0, "yaw_radians_world": 0.0}
    store.remember("first", pose)
    store.remember("second", pose)

    names = [lm.name for lm in store.list_all()]

    assert names == ["second", "first"]


def test_forget_removes_landmark_and_reports_whether_it_existed():
    store = LandmarkStore()
    store.remember("temp", {"x_meters_world": 0.0, "y_meters_world": 0.0, "yaw_radians_world": 0.0})

    assert store.forget("temp") is True
    assert store.recall("temp") is None
    assert store.forget("temp") is False


def test_ordering_survives_identical_timestamps():
    """Regression: ordering must not depend on wall-clock resolution.

    `saved_at` comes from `time.time()`, and two landmarks saved microseconds
    apart can share a value. Because `sorted` is stable, a tie there silently
    reversed the intended "most recent first" into insertion order — which made
    this suite flaky rather than failing outright. Ordering now uses a
    monotonic insertion counter, so a frozen clock changes nothing.
    """
    import time as time_mod

    from bridge.skills import landmarks as landmarks_mod

    store = LandmarkStore()
    pose = {"x_meters_world": 0.0, "y_meters_world": 0.0, "yaw_radians_world": 0.0}

    original = time_mod.time
    landmarks_mod.time.time = lambda: 1_000_000.0  # every save gets the same stamp
    try:
        store.remember("first", pose)
        store.remember("second", pose)
        store.remember("third", pose)
    finally:
        landmarks_mod.time.time = original

    assert [lm.name for lm in store.list_all()] == ["third", "second", "first"]


# --------------------------------------------------------------------------
# Persistence
# --------------------------------------------------------------------------


def test_landmarks_survive_a_restart(tmp_path):
    """A new store at the same path sees what the old one saved.

    This is the whole point: the bridge is restarted constantly during
    development, and the odometry frame these coordinates live in belongs to
    the robot's control board, not to us — so it outlives our process.
    """
    path = tmp_path / "landmarks.json"
    pose = {"x_meters_world": 1.5, "y_meters_world": -2.0, "yaw_radians_world": 0.5}

    first = LandmarkStore(path=path)
    first.remember("kitchen", pose, frame_tick=1000)

    reopened = LandmarkStore(path=path)
    recalled = reopened.recall("kitchen")

    assert recalled is not None
    assert recalled.x_meters_world == 1.5
    assert recalled.yaw_radians_world == 0.5
    assert recalled.frame_tick == 1000


def test_forget_is_persisted_too(tmp_path):
    path = tmp_path / "landmarks.json"
    pose = {"x_meters_world": 0.0, "y_meters_world": 0.0, "yaw_radians_world": 0.0}

    store = LandmarkStore(path=path)
    store.remember("temp", pose)
    store.forget("temp")

    assert LandmarkStore(path=path).recall("temp") is None


def test_ordering_survives_a_restart(tmp_path):
    # `seq` is persisted, so "most recent first" holds across a reload rather
    # than reverting to whatever order the file happened to serialise in.
    path = tmp_path / "landmarks.json"
    pose = {"x_meters_world": 0.0, "y_meters_world": 0.0, "yaw_radians_world": 0.0}

    store = LandmarkStore(path=path)
    for name in ("first", "second", "third"):
        store.remember(name, pose)

    reopened = LandmarkStore(path=path)
    assert [lm.name for lm in reopened.list_all()] == ["third", "second", "first"]

    # And a landmark saved after the reload must sort ahead of the old ones,
    # which only works if the counter resumed rather than restarting at 1.
    reopened.remember("fourth", pose)
    assert reopened.list_all()[0].name == "fourth"


def test_no_path_means_no_file_is_written(tmp_path, monkeypatch):
    # Persistence is opt-in: a throwaway store must never touch the real file.
    monkeypatch.setenv("LANDMARKS_PATH", str(tmp_path / "should-not-exist.json"))
    store = LandmarkStore()
    store.remember(
        "ghost",
        {"x_meters_world": 0.0, "y_meters_world": 0.0, "yaw_radians_world": 0.0},
    )
    assert not (tmp_path / "should-not-exist.json").exists()


def test_a_corrupt_file_does_not_stop_the_bridge_starting(tmp_path):
    # Landmarks are a convenience; refusing to boot over a bad file would take
    # the robot offline for a cosmetic feature.
    path = tmp_path / "landmarks.json"
    path.write_text("{not json at all")

    store = LandmarkStore(path=path)

    assert store.list_all() == []
    # And it must recover — a later save should produce a readable file.
    store.remember(
        "fresh",
        {"x_meters_world": 1.0, "y_meters_world": 1.0, "yaw_radians_world": 0.0},
    )
    assert LandmarkStore(path=path).recall("fresh") is not None


# --------------------------------------------------------------------------
# Frame guard — persisted coordinates are only valid within one boot
# --------------------------------------------------------------------------


def test_frame_is_stale_after_the_control_board_reboots():
    # `tick` counts up within a boot, so a lower current value means the board
    # restarted and odometry re-origined: every saved coordinate now points
    # somewhere else entirely.
    assert frame_is_stale(saved_tick=500_000, current_tick=1_200) is True


def test_frame_is_fresh_within_the_same_boot():
    assert frame_is_stale(saved_tick=1_000, current_tick=500_000) is False


def test_unknown_ticks_do_not_cry_wolf():
    # Flagging every landmark as suspect when we simply can't tell would train
    # operators to ignore the warning — which is how the guard stops working.
    assert frame_is_stale(None, 1000) is False
    assert frame_is_stale(1000, None) is False
    assert frame_is_stale(None, None) is False
