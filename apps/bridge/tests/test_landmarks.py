"""Tests for the in-memory landmark store (`bridge.skills.landmarks`)."""

from __future__ import annotations

from bridge.skills.landmarks import LandmarkStore


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
