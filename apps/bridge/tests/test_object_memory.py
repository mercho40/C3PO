"""Tests for the object memory (`bridge.object_memory`, PLAN Phase 4.1).

The memory answers "where did I last see X?" after X has left the frame, so the
failures that matter are: a point placed on the wrong side of the robot (a
bearing-sign bug), one object counted as two (or two as one), an object that is
never forgotten after it was taken away, and an object forgotten while it was
merely out of view.
"""

from __future__ import annotations

import json
import math

import pytest

from bridge import object_memory as om
from bridge.object_memory import ObjectMemory, egocentric, label_matches, to_world

ORIGIN = {"x_m": 0.0, "y_m": 0.0, "yaw_deg": 0.0}


def det(label: str, range_m: float, bearing_deg: float, confidence: float = 0.8) -> dict:
    return {"label": label, "range_m": range_m, "bearing_deg": bearing_deg, "confidence": confidence}


class Clock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t


def memory(path=None) -> tuple[ObjectMemory, Clock]:
    clock = Clock()
    return ObjectMemory(path=path, clock=clock), clock


def look(mem: ObjectMemory, clock: Clock, pose: dict, objects: list, frames: int = 1) -> None:
    """`frames` still reports, 0.25 s apart (4 Hz), at one pose."""
    for _ in range(frames):
        clock.t += 0.25
        mem.observe(pose, objects)


# -- geometry ------------------------------------------------------------------


def test_bearing_positive_is_left():
    # Robot at the origin facing +x: something 2 m away at +90 deg is at +y.
    x, y = to_world(ORIGIN, 2.0, 90.0)
    assert x == pytest.approx(0.0, abs=1e-9)
    assert y == pytest.approx(2.0)


def test_to_world_uses_the_robots_heading_and_position():
    pose = {"x_m": 1.0, "y_m": 1.0, "yaw_deg": 90.0}  # facing +y
    x, y = to_world(pose, 1.0, 0.0)
    assert (x, y) == (pytest.approx(1.0), pytest.approx(2.0))
    x, y = to_world(pose, 1.0, -90.0)  # to its right = +x
    assert (x, y) == (pytest.approx(2.0), pytest.approx(1.0))


def test_egocentric_inverts_to_world():
    pose = {"x_m": -0.7, "y_m": 2.3, "yaw_deg": 135.0}
    for rng, bearing in [(1.0, 0.0), (2.5, 40.0), (0.8, -170.0), (3.0, 179.0)]:
        x, y = to_world(pose, rng, bearing)
        r2, b2 = egocentric(pose, x, y)
        assert r2 == pytest.approx(rng)
        assert math.isclose(math.cos(math.radians(b2 - bearing)), 1.0, abs_tol=1e-9)
        assert -180.0 <= b2 <= 180.0


# -- merge ---------------------------------------------------------------------


def test_same_label_within_half_a_metre_is_one_object():
    mem, clock = memory()
    look(mem, clock, ORIGIN, [det("cup", 2.0, 0.0, 0.5)])
    look(mem, clock, ORIGIN, [det("cup", 2.3, 0.0, 0.9)])

    [entry] = mem.list_all()
    assert entry.seen_count == 2
    assert entry.best_confidence == 0.9
    assert entry.last_range_m == 2.3
    assert entry.x_m == pytest.approx(2.15)  # running mean
    assert entry.frame == "odom"


def test_far_apart_or_different_labels_stay_separate():
    mem, clock = memory()
    look(mem, clock, ORIGIN, [det("cup", 2.0, 0.0), det("cup", 2.0, 30.0), det("bottle", 2.0, 0.0)])
    labels = sorted(e.label for e in mem.list_all())
    assert labels == ["bottle", "cup", "cup"]


def test_the_same_object_seen_from_two_poses_merges():
    mem, clock = memory()
    look(mem, clock, ORIGIN, [det("chair", 2.0, 0.0)])
    # Walked to (1, 1) and turned to face -y: the chair at (2, 0) is now
    # sqrt(2) away at 45 deg to the left.
    pose = {"x_m": 1.0, "y_m": 1.0, "yaw_deg": -90.0}
    clock.t += 5.0  # walked there; not a fast turn
    look(mem, clock, pose, [det("chair", math.sqrt(2), 45.0)])
    [entry] = mem.list_all()
    assert entry.seen_count == 2
    assert (entry.x_m, entry.y_m) == (pytest.approx(2.0), pytest.approx(0.0, abs=1e-9))


def test_malformed_detections_are_skipped_and_extra_fields_ignored():
    mem, clock = memory()
    look(
        mem,
        clock,
        ORIGIN,
        [
            {"label": "cup"},
            {"range_m": 1.0, "bearing_deg": 0.0},
            "nonsense",
            {"label": "cup", "range_m": "far", "bearing_deg": 0},
            {"label": "tv", "range_m": 2.0, "bearing_deg": 5.0, "track_id": 7, "depth_source": "plane"},
        ],
    )
    assert [e.label for e in mem.list_all()] == ["tv"]


def test_frames_are_never_mixed():
    mem, clock = memory()
    clock.t += 0.25
    mem.observe(ORIGIN, [det("cup", 2.0, 0.0)], frame="odom")
    clock.t += 0.25
    mem.observe(ORIGIN, [det("cup", 2.0, 0.0)], frame="map")
    assert sorted(e.frame for e in mem.list_all()) == ["map", "odom"]


def test_frames_taken_while_turning_fast_are_ignored():
    mem, clock = memory()
    look(mem, clock, ORIGIN, [])
    # 20 deg in 0.25 s = 80 deg/s: the detection is paired with the wrong pose.
    look(mem, clock, {"x_m": 0.0, "y_m": 0.0, "yaw_deg": 20.0}, [det("cup", 2.0, 0.0)])
    assert mem.list_all() == []
    # Still again -> accepted.
    look(mem, clock, {"x_m": 0.0, "y_m": 0.0, "yaw_deg": 20.0}, [det("cup", 2.0, 0.0)])
    assert len(mem.list_all()) == 1


# -- forget --------------------------------------------------------------------


def seeded(label: str = "bottle", rng: float = 2.0, bearing: float = 0.0):
    mem, clock = memory()
    look(mem, clock, ORIGIN, [det(label, rng, bearing)])
    return mem, clock


def test_looking_at_an_empty_spot_forgets_it():
    mem, clock = seeded()
    look(mem, clock, ORIGIN, [], frames=om.MISSES_TO_FORGET - 1)
    assert len(mem.list_all()) == 1, "one glance is not enough to forget"
    look(mem, clock, ORIGIN, [])
    assert mem.list_all() == []


def test_a_sighting_resets_the_miss_count():
    mem, clock = seeded()
    look(mem, clock, ORIGIN, [], frames=om.MISSES_TO_FORGET - 1)
    look(mem, clock, ORIGIN, [det("bottle", 2.0, 0.0)])
    look(mem, clock, ORIGIN, [], frames=om.MISSES_TO_FORGET - 1)
    assert len(mem.list_all()) == 1


@pytest.mark.parametrize(
    "pose",
    [
        {"x_m": 0.0, "y_m": 0.0, "yaw_deg": 90.0},  # looking away: outside the FOV
        {"x_m": -2.0, "y_m": 0.0, "yaw_deg": 0.0},  # 4 m away: beyond depth range
        {"x_m": 1.8, "y_m": 0.0, "yaw_deg": 0.0},  # 0.2 m: under the camera
    ],
)
def test_not_in_view_is_not_a_miss(pose):
    mem, clock = seeded()
    look(mem, clock, pose, [], frames=om.MISSES_TO_FORGET * 2)
    assert len(mem.list_all()) == 1


def test_something_nearer_on_the_same_bearing_hides_it():
    mem, clock = seeded()
    look(mem, clock, ORIGIN, [det("person", 1.0, 3.0)], frames=om.MISSES_TO_FORGET * 2)
    assert {e.label for e in mem.list_all()} == {"bottle", "person"}


def test_a_report_without_a_working_detector_counts_nothing():
    mem, clock = seeded()
    report = {"pose": ORIGIN, "detector_online": False, "objects": []}
    for _ in range(om.MISSES_TO_FORGET * 2):
        clock.t += 0.25
        mem.observe_report(report)
    assert len(mem.list_all()) == 1


def test_observe_report_reads_the_wire_shape_and_pose_frame():
    mem, clock = memory()
    clock.t += 0.25
    mem.observe_report(
        {"pose": ORIGIN, "detector_online": True, "objects": [det("tv", 3.0, 10.0)]}
    )
    clock.t += 0.25
    mem.observe_report(
        {
            "pose": ORIGIN,
            "pose_frame": "map",
            "detector_online": True,
            "objects": [det("tv", 3.0, 10.0)],
        }
    )
    mem.observe_report({"pose": None, "detector_online": True, "objects": [det("cup", 1, 0)]})
    assert sorted((e.label, e.frame) for e in mem.list_all()) == [("tv", "map"), ("tv", "odom")]


def test_forget_by_id_and_all():
    mem, clock = memory()
    look(mem, clock, ORIGIN, [det("cup", 2.0, 0.0), det("tv", 3.0, 20.0)])
    cup = next(e for e in mem.list_all() if e.label == "cup")
    assert mem.forget(cup.id) == 1
    assert mem.forget(cup.id) == 0
    assert mem.forget("all") == 1
    assert mem.list_all() == []


def test_the_table_is_bounded(monkeypatch):
    monkeypatch.setattr(om, "MAX_ENTRIES", 3)
    mem, clock = memory()
    for i in range(5):
        look(mem, clock, ORIGIN, [det("cup", 1.0 + i, 0.0)])
    assert len(mem.list_all()) == 3
    assert min(e.last_range_m for e in mem.list_all()) == 3.0, "oldest went first"


# -- describe / recall ---------------------------------------------------------


def test_describe_gives_distance_and_bearing_from_where_the_robot_is_now():
    mem, clock = seeded("tv", 2.0, 0.0)
    clock.t += 10
    [entry] = mem.list_all()
    here = {"x_m": 2.0, "y_m": -2.0, "yaw_deg": 90.0}  # tv is 2 m straight ahead
    d = entry.describe(here, "odom", clock())
    assert d["distance_m"] == 2.0
    assert d["bearing_deg"] == 0.0
    assert d["last_seen_s_ago"] == 10.0
    assert "distance_m" not in entry.describe(here, "map", clock()), "other frame"
    assert "distance_m" not in entry.describe(None, None, clock()), "no pose"


@pytest.mark.parametrize(
    "label,query",
    [
        ("wallet", "billetera"),
        ("wallet", "la billetera"),
        ("handbag", "mi cartera"),
        ("clock", "reloj"),
        ("tv", "tele"),
        ("tv", "Televisión"),
        ("cell phone", "celular"),
        ("cell phone", "teléfono"),
        ("remote", "el control remoto"),
        ("wine glass", "vasos"),
        ("wine glass", "glass"),
        ("keys", "llaves"),
        ("dining table", "mesa"),
        ("cup", "CUP"),
        ("potted_plant", "planta"),
    ],
)
def test_spanish_and_english_queries_find_the_detector_label(label, query):
    assert label_matches(label, query)


@pytest.mark.parametrize("label,query", [("cup", "botella"), ("cupboard", "cup"), ("tv", "")])
def test_queries_do_not_overmatch(label, query):
    assert not label_matches(label, query)


def test_recall_returns_matches_most_recent_first():
    mem, clock = memory()
    look(mem, clock, ORIGIN, [det("cup", 2.0, 0.0)])
    look(mem, clock, ORIGIN, [det("cup", 2.0, 60.0), det("tv", 3.0, -20.0)])
    found = mem.recall("taza")
    assert [e.label for e in found] == ["cup", "cup"]
    assert found[0].last_seen >= found[1].last_seen
    assert mem.labels() == ["cup", "tv"]


# -- persistence ---------------------------------------------------------------


def test_memory_survives_a_restart(tmp_path):
    path = tmp_path / "objects.json"
    mem, clock = memory(path)
    look(mem, clock, ORIGIN, [det("wallet", 1.5, 20.0)])
    assert path.exists(), "a new entry is written at once"

    again = ObjectMemory(path=path)
    [entry] = again.list_all()
    assert entry.label == "wallet"
    assert entry.frame == "odom"
    # New ids continue after the loaded ones instead of colliding.
    look(again, Clock(2000.0), ORIGIN, [det("tv", 3.0, -60.0)])
    assert len({e.id for e in again.list_all()}) == 2


def test_count_updates_are_throttled_but_flushed(tmp_path):
    path = tmp_path / "objects.json"
    mem, clock = memory(path)
    look(mem, clock, ORIGIN, [det("cup", 2.0, 0.0)])
    look(mem, clock, ORIGIN, [det("cup", 2.0, 0.0)])
    on_disk = json.loads(path.read_text())["objects"][0]
    assert on_disk["seen_count"] == 1, "a merge alone does not rewrite the file every frame"
    mem.flush()
    assert json.loads(path.read_text())["objects"][0]["seen_count"] == 2


def test_a_corrupt_file_does_not_stop_the_bridge(tmp_path):
    path = tmp_path / "objects.json"
    path.write_text("{not json")
    assert ObjectMemory(path=path).list_all() == []


def test_forget_is_persisted(tmp_path):
    path = tmp_path / "objects.json"
    mem, clock = memory(path)
    look(mem, clock, ORIGIN, [det("cup", 2.0, 0.0)])
    mem.forget("all")
    assert ObjectMemory(path=path).list_all() == []


def test_default_path_sits_next_to_landmarks(monkeypatch):
    from bridge.skills import landmarks

    monkeypatch.delenv("OBJECT_MEMORY_PATH", raising=False)
    monkeypatch.delenv("LANDMARKS_PATH", raising=False)
    assert om.default_path().parent == landmarks.default_path().parent
    monkeypatch.setenv("OBJECT_MEMORY_PATH", "/tmp/x.json")
    assert str(om.default_path()) == "/tmp/x.json"
