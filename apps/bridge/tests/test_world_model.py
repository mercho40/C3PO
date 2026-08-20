"""Tests for the world-model contract (`bridge.world_model`).

The rule these mostly defend is "absent is not empty". A model handed
`objects: []` by an offline detector will reason as though it looked and saw
nothing — and walk into the thing it never saw. That failure is silent, which
is why it gets the most coverage here.

No DDS, no perception stack, no robot: the builder composes whatever it is
given and degrades explicitly for the rest, which is exactly what makes the
contract testable before any of it exists.
"""

from __future__ import annotations

import json

from bridge.world_model import (
    MAX_OBJECTS,
    WORLD_MODEL_VERSION,
    FreeSpace,
    Observation,
    build,
    offline,
)


def obs(label: str, range_m: float, bearing_deg: float = 0.0, age_s: float = 0.0):
    return Observation(label=label, range_m=range_m, bearing_deg=bearing_deg, age_s=age_s)


# --------------------------------------------------------------------------
# Absent is not empty — the rule that keeps the robot from walking into things
# --------------------------------------------------------------------------


def test_offline_detector_is_not_reported_as_an_empty_scene():
    wm = offline()
    d = wm.to_dict()

    assert d["sources"]["detector"] == "offline"
    # The absence of an `objects` key must not be readable as "nothing there".
    assert "objects" not in d
    assert any("OFFLINE" in n for n in d["notes"])
    assert any("not assume the path is clear" in n.lower() for n in d["notes"])


def test_online_detector_with_nothing_found_is_a_real_observation():
    # Distinct from the case above: an online detector that sees nothing is
    # useful information, and must NOT be warned about. Every other source is
    # supplied so the only thing under test is the detector's own note.
    wm = build(
        pose={"x_m": 0.0},
        pose_age_s=0.0,
        detector_online=True,
        objects=[],
        lidar_online=True,
        free_space=FreeSpace(ahead_m=4.0),
    )
    d = wm.to_dict()

    assert d["sources"]["detector"] == "ok"
    assert "objects" not in d
    # A clear scene should produce no warnings at all.
    assert d.get("notes", []) == []


def test_objects_handed_in_without_a_working_detector_are_dropped():
    # Stale data from a dead detector is worse than no data — it looks current.
    wm = build(objects=[obs("person", 1.0)], detector_online=False)

    assert wm.objects == []
    assert wm.sources["detector"] == "offline"


def test_missing_lidar_reports_offline_not_infinite_clearance():
    wm = build(lidar_online=False)
    d = wm.to_dict()

    assert d["sources"]["lidar"] == "offline"
    assert "free_space" not in d
    assert any("not infinite" in n for n in d["notes"])


def test_missing_pose_is_stated():
    wm = build(pose=None)
    assert wm.sources["pose"] == "offline"
    assert any("Pose is unavailable" in n for n in wm.notes)


def test_stale_pose_is_distinguished_from_fresh_and_missing():
    fresh = build(pose={"x_m": 0.0}, pose_age_s=0.1)
    stale = build(pose={"x_m": 0.0}, pose_age_s=30.0)

    assert fresh.sources["pose"] == "ok"
    assert stale.sources["pose"] == "stale"


def test_stale_detections_are_flagged_not_silently_trusted():
    wm = build(detector_online=True, objects=[obs("person", 2.0, age_s=9.0)])

    assert wm.sources["detector"] == "stale"
    assert any("stale" in n.lower() for n in wm.notes)


# --------------------------------------------------------------------------
# Truncation is declared, never silent
# --------------------------------------------------------------------------


def test_truncation_is_counted_and_announced():
    many = [obs(f"box{i}", float(i + 1)) for i in range(20)]
    wm = build(detector_online=True, objects=many)
    d = wm.to_dict()

    assert len(d["objects"]) == MAX_OBJECTS
    assert d["objects_omitted"] == 20 - MAX_OBJECTS
    assert any("not listed" in n for n in d["notes"])


def test_nearest_objects_are_the_ones_kept():
    # If we must drop some, drop the far ones — proximity is what matters.
    far = [obs(f"far{i}", 50.0 + i) for i in range(10)]
    near = obs("person", 0.8)
    wm = build(detector_online=True, objects=[*far, near])

    assert wm.objects[0].label == "person"
    assert all(o.range_m <= wm.objects[-1].range_m for o in wm.objects)


def test_no_omission_key_when_nothing_was_dropped():
    wm = build(detector_online=True, objects=[obs("chair", 1.5)])
    assert "objects_omitted" not in wm.to_dict()


# --------------------------------------------------------------------------
# Shape and conventions
# --------------------------------------------------------------------------


def test_bearing_sign_matches_the_turn_skill():
    # `turn` documents positive delta_yaw as counter-clockwise / left. A
    # bearing that meant the opposite would bake a sign flip into the contract
    # itself, and every "turn toward it" the model attempts would go wrong way.
    left = obs("door", 3.0, bearing_deg=45.0).to_dict()
    right = obs("door", 3.0, bearing_deg=-45.0).to_dict()

    assert left["bearing_deg"] > 0, "positive bearing must mean left (CCW)"
    assert right["bearing_deg"] < 0


def test_snapshot_is_versioned():
    assert offline().to_dict()["version"] == WORLD_MODEL_VERSION


def test_every_source_reports_even_when_nothing_is_connected():
    # A missing key would let a consumer mistake "not reported" for "fine".
    sources = offline().to_dict()["sources"]
    assert set(sources) >= {"pose", "detector", "lidar"}
    assert all(v == "offline" for v in sources.values())


def test_free_space_omits_unknown_sectors_rather_than_zeroing_them():
    # 0.0 m would read as "obstacle touching the robot" — the opposite of unknown.
    wm = build(lidar_online=True, free_space=FreeSpace(ahead_m=2.5, left_m=1.0))
    fs = wm.to_dict()["free_space"]

    assert fs == {"ahead_m": 2.5, "left_m": 1.0}
    assert "right_m" not in fs and "behind_m" not in fs


# --------------------------------------------------------------------------
# Budget — the contract only works if it stays small
# --------------------------------------------------------------------------


def test_a_busy_snapshot_still_fits_a_small_budget():
    wm = build(
        pose={"x_m": 1.2, "y_m": -0.4, "yaw_deg": 33.8},
        pose_age_s=0.05,
        detector_online=True,
        objects=[obs(f"person{i}", 1.0 + i, bearing_deg=-90 + i * 20) for i in range(20)],
        lidar_online=True,
        free_space=FreeSpace(ahead_m=3.4, left_m=1.1, right_m=2.8, behind_m=5.0),
        landmarks=[obs("kitchen", 4.2, 30.0), obs("charger", 9.0, -120.0)],
    )

    # Perception must not crowd out the conversation it is meant to inform.
    assert wm.approx_tokens() < 300, json.dumps(wm.to_dict())
