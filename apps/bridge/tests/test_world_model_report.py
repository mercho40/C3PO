"""Tests for `world_model.from_report` — the container's JSON → the D7 contract.

`from_report` is the ONLY place the perception container's wire shape is
interpreted, which is the whole reason it lives in `world_model.py` and not in
`perception_link.py`: this module imports no DDS, so the interpretation is
testable with no robot, no containers and no perception stack, exactly like the
rest of the contract.

Two rules get the coverage here, because both fail silently and plausibly:

* **Truncation is always declared.** The container caps its own list before
  publishing, and `build()` caps again. Those two counts must be SUMMED. A
  version that reports only one of them still emits a non-zero, believable
  number — which is precisely why it would survive review.
* **Absent is not empty.** A missing report, a stale one, and a rejected one all
  have to degrade to explicit `offline` sources, never to `objects: []`.
"""

from __future__ import annotations

from bridge.world_model import (
    MAX_OBJECTS,
    Observation,
    build,
    from_report,
)


def report(**overrides):
    """A minimal well-formed report, in the publisher's own key set."""
    base = {
        "report_version": 1,
        "stamp_unix": 1_000_000.0,
        "pose": {"x_m": 1.0, "y_m": 2.0, "yaw_deg": 30.0},
        "pose_age_s": 0.1,
        "detector_online": True,
        "objects": [],
        "objects_omitted": 0,
        "lidar_online": True,
        "free_space": {"ahead_m": 3.0, "left_m": 1.5},
        "notes": [],
    }
    base.update(overrides)
    return base


def wire(label: str, range_m: float, bearing_deg: float = 0.0, **extra):
    d = {"label": label, "range_m": range_m, "bearing_deg": bearing_deg}
    d.update(extra)
    return d


# --------------------------------------------------------------------------
# The happy path — the container's kwargs really are build()'s kwargs
# --------------------------------------------------------------------------


def test_a_well_formed_report_becomes_a_normal_snapshot():
    wm = from_report(report(objects=[wire("person", 2.0, 45.0, confidence=0.9, age_s=0.2)]))
    d = wm.to_dict()

    assert d["sources"] == {"pose": "ok", "detector": "ok", "lidar": "ok"}
    assert d["objects"] == [
        {"label": "person", "range_m": 2.0, "bearing_deg": 45.0, "confidence": 0.9, "age_s": 0.2}
    ]
    assert d["free_space"] == {"ahead_m": 3.0, "left_m": 1.5}
    assert d["pose"] == {"x_m": 1.0, "y_m": 2.0, "yaw_deg": 30.0}
    assert d.get("notes", []) == []


def test_bearing_sign_survives_the_wire_unchanged():
    # D7's convention is 0 ahead, POSITIVE LEFT (CCW). If the crossing ever
    # flipped it, every "turn toward it" would go the wrong way, and nothing
    # about the snapshot would look wrong.
    wm = from_report(report(objects=[wire("door", 3.0, 45.0)]))
    assert wm.objects[0].bearing_deg == 45.0


def test_container_notes_reach_the_model():
    wm = from_report(report(notes=["Detector payload rejected: bad schema"]))
    assert any("Detector payload rejected" in n for n in wm.notes)


# --------------------------------------------------------------------------
# Truncation is summed, never replaced
# --------------------------------------------------------------------------


def test_both_omitted_counts_are_summed():
    # The container saw 40, published 32, and declared 8 omitted. We then keep
    # MAX_OBJECTS of the 32. The snapshot must claim 8 + (32 - MAX_OBJECTS),
    # not 8 and not (32 - MAX_OBJECTS).
    on_wire = [wire(f"box{i}", float(i + 1)) for i in range(32)]
    wm = from_report(report(objects=on_wire, objects_omitted=8))

    assert len(wm.objects) == MAX_OBJECTS
    assert wm.objects_omitted == 8 + (32 - MAX_OBJECTS)
    assert any("not listed" in n for n in wm.notes)


def test_perception_side_omissions_are_declared_even_when_we_truncate_nothing():
    # We dropped none of them; the container dropped 5. Reporting zero here is
    # the silent violation this argument exists to prevent.
    wm = from_report(report(objects=[wire("chair", 1.0)], objects_omitted=5))

    assert wm.objects_omitted == 5
    assert "objects_omitted" in wm.to_dict()


def test_an_offline_detectors_omission_count_is_not_believed():
    # If the detector is offline its object list is dropped entirely, so its
    # claim about what it omitted is not evidence of anything either.
    wm = from_report(report(detector_online=False, objects=[wire("chair", 1.0)], objects_omitted=7))

    assert wm.objects == []
    assert wm.objects_omitted == 0
    assert wm.sources["detector"] == "offline"


def test_build_sums_extra_omitted_directly():
    many = [Observation(label=f"o{i}", range_m=float(i + 1), bearing_deg=0.0) for i in range(12)]
    wm = build(detector_online=True, objects=many, extra_omitted=3)
    assert wm.objects_omitted == (12 - MAX_OBJECTS) + 3


def test_a_malformed_detection_is_counted_as_omitted_not_dropped():
    # A fragment we cannot read is still something the detector saw. Silently
    # discarding it would shrink the scene without saying so.
    wm = from_report(report(objects=[wire("person", 1.0), {"label": "ghost"}, "not-a-dict"]))

    assert [o.label for o in wm.objects] == ["person"]
    assert wm.objects_omitted == 2
    assert any("malformed" in n for n in wm.notes)


# --------------------------------------------------------------------------
# Absent is not empty, across the crossing
# --------------------------------------------------------------------------


def test_no_report_at_all_is_every_source_offline_not_an_empty_scene():
    # This is what the link hands us when the container is gone, when its last
    # report aged out, and when its report_version was refused.
    wm = from_report(None)
    d = wm.to_dict()

    assert d["sources"] == {"pose": "offline", "detector": "offline", "lidar": "offline"}
    assert "objects" not in d
    assert any("OFFLINE" in n for n in d["notes"])
    assert any("not assume the path is clear" in n.lower() for n in d["notes"])


def test_an_online_detector_reporting_nothing_is_not_degraded():
    wm = from_report(report(objects=[]))
    assert wm.sources["detector"] == "ok"
    assert wm.objects == []
    assert not any("OFFLINE" in n for n in wm.notes)


def test_offline_lidar_is_reported_as_unavailable_not_as_clear():
    wm = from_report(report(lidar_online=False, free_space=None))
    d = wm.to_dict()

    assert d["sources"]["lidar"] == "offline"
    assert "free_space" not in d
    assert any("not infinite" in n for n in d["notes"])


def test_missing_pose_from_the_container_is_stated():
    wm = from_report(report(pose=None, pose_age_s=None))
    assert wm.sources["pose"] == "offline"
    assert any("Pose is unavailable" in n for n in wm.notes)


def test_stale_pose_age_from_the_container_is_honoured():
    wm = from_report(report(pose_age_s=30.0))
    assert wm.sources["pose"] == "stale"


def test_landmarks_are_merged_bridge_side_not_taken_from_the_wire():
    # Landmarks live in skills/landmarks.py; the container knows nothing about
    # them and must not be able to inject any.
    kitchen = Observation(label="kitchen", range_m=4.0, bearing_deg=10.0)
    wm = from_report(report(landmarks=[wire("spoofed", 1.0)]), landmarks=[kitchen])

    assert [lm.label for lm in wm.landmarks] == ["kitchen"]


def test_an_empty_free_space_dict_does_not_read_as_four_clear_sectors():
    # {} would serialise to nothing and leave `lidar: ok` claiming an estimate
    # it does not have.
    wm = from_report(report(free_space={}))
    assert wm.sources["lidar"] == "offline"
    assert wm.free_space is None


def test_the_snapshot_still_fits_the_token_budget_from_a_full_report():
    busy = [wire(f"person{i}", 1.0 + i, -90 + i * 9, confidence=0.5) for i in range(20)]
    wm = from_report(
        report(
            objects=busy,
            objects_omitted=11,
            free_space={"ahead_m": 3.4, "left_m": 1.1, "right_m": 2.8, "behind_m": 5.0},
            notes=["Scan arrives in 'base_link', not 'base_footprint'."],
        )
    )
    assert wm.approx_tokens() < 300
