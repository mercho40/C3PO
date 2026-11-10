"""C3PO_VISION_CLASS_CONF: per-class thresholds, set on the robot without a re-export.

Same rules as test_depth_and_tracking.py: depth frames are lists of rows, no
numpy, so this runs on a laptop with nothing installed.
"""

from __future__ import annotations

import math

import pytest
from c3po_vision import detector
from c3po_vision.grounding import CameraExtrinsic, Intrinsics

from bringup.spec import vision_env_passthrough

INTR = Intrinsics(fx=600.0, fy=600.0, cx=320.0, cy=240.0, width=640, height=480)
LEVEL_CAM = CameraExtrinsic(x_m=0.10, y_m=0.0, z_m=1.20, pitch_deg=0.0)
LABELS = ["person", "cooker", "cup"]


def parse(raw: str):
    return detector.parse_class_conf(raw, 0.35)


# --- the parser ---------------------------------------------------------------


def test_unset_is_no_overrides():
    assert parse("") == ({}, [])


def test_spaces_are_trimmed_but_a_label_keeps_its_own():
    conf, ignored = parse(" cooker = 0.6 , drinking glass=0.5,")
    assert conf == {"cooker": 0.6, "drinking glass": 0.5}
    assert ignored == []


def test_off_drops_the_class_entirely():
    conf, _ = parse("thumbtack=off,toaster=OFF")
    assert conf == {"thumbtack": math.inf, "toaster": math.inf}


@pytest.mark.parametrize(
    "entry",
    [
        "cooker=high",  # not a number
        "cooker",  # no '='
        "=0.6",  # no label
        "cooker=nan",
        "cooker=60",  # meant 0.60; confidences stop at 1
        "cooker=0.2",  # below C3PO_VISION_CONF, which the decoder already applies
    ],
)
def test_a_bad_entry_is_ignored_and_reported_never_fatal(entry):
    conf, ignored = parse(entry + ",cup=0.5")
    assert conf == {"cup": 0.5}
    assert len(ignored) == 1 and ignored[0].startswith(entry)


@pytest.mark.parametrize("value", ["0.9", "off", "0.2"])
def test_person_cannot_be_overridden(value):
    # bridge.safety's person rule reads label == "person"; raising its bar would
    # hide people from the stop and slow distances.
    conf, ignored = parse("person=" + value)
    assert conf == {}
    assert len(ignored) == 1 and "person" in ignored[0]


def test_a_label_the_model_does_not_have_is_kept():
    # The labels load later, in run(); a typo is logged there, not here.
    assert parse("toastr=0.6")[0] == {"toastr": 0.6}


def test_the_variable_reaches_the_vision_container():
    assert vision_env_passthrough({"C3PO_VISION_CLASS_CONF": "cooker=0.6"}) == {
        "C3PO_VISION_CLASS_CONF": "cooker=0.6"
    }


# --- the filter ---------------------------------------------------------------


def _scene():
    depth = [[2000] * 640 for _ in range(480)]
    dets = [
        (0, 0.40, (10, 100, 60, 200)),  # person, under every override below
        (1, 0.55, (110, 100, 160, 200)),  # cooker at 0.55
        (1, 0.70, (210, 100, 260, 200)),  # cooker at 0.70
        (2, 0.36, (310, 100, 360, 200)),  # cup, no override
    ]
    return dets, depth


def _labels_and_conf(objects):
    return sorted((o["label"], o["confidence"]) for o in objects)


def test_a_named_class_is_held_to_its_own_bar_and_the_rest_are_untouched(monkeypatch):
    monkeypatch.setattr(detector, "CLASS_CONF", {"cooker": 0.6})
    dets, depth = _scene()
    objects, omitted, _ = detector.ground_all(dets, depth, INTR, LEVEL_CAM, LABELS, 0.001)
    assert omitted == 0
    assert _labels_and_conf(objects) == [("cooker", 0.7), ("cup", 0.36), ("person", 0.4)]


def test_off_removes_a_class_from_unranged_too(monkeypatch):
    monkeypatch.setattr(detector, "CLASS_CONF", {"cooker": math.inf})
    dets, _ = _scene()
    no_depth = [[0] * 640 for _ in range(480)]
    _objects, _omitted, unranged = detector.ground_all(
        dets, no_depth, INTR, LEVEL_CAM, LABELS, 0.001
    )
    assert [u["label"] for u in unranged] == ["person", "cup"]


def test_unset_reports_exactly_what_it_did_before(monkeypatch):
    monkeypatch.setattr(detector, "CLASS_CONF", {})
    dets, depth = _scene()
    # A detection under the global bar still passes: the decoder owns that bar,
    # and an unset variable must not add a second one here.
    dets.append((2, 0.10, (410, 100, 460, 200)))
    objects, _, _ = detector.ground_all(dets, depth, INTR, LEVEL_CAM, LABELS, 0.001)
    assert len(objects) == len(dets)
