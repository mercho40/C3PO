"""Detections are aged from the frame grab, on a laptop with no ROS."""

from __future__ import annotations

import math

import pytest
from bridge import world_model
from c3po_perception import object_age

CUP = {"label": "cup", "range_m": 0.4, "bearing_deg": 5.0, "confidence": 0.8}


def test_age_is_now_minus_the_capture_stamp():
    assert object_age.aged([CUP], 100.0, 100.73) == [dict(CUP, age_s=0.7)]


def test_a_future_stamp_clamps_to_zero():
    assert object_age.aged([CUP], 100.2, 100.0)[0]["age_s"] == 0.0


def test_the_input_objects_are_not_mutated():
    objects = [dict(CUP)]
    object_age.aged(objects, 100.0, 101.0)
    assert objects == [CUP]


def test_the_age_reaches_the_bridge_observation():
    objs = object_age.aged([CUP], 100.0, 101.2)
    wm = world_model.from_report({"detector_online": True, "objects": objs})
    assert wm.objects[0].age_s == pytest.approx(1.2)


def test_a_plausible_stamp_is_kept_and_a_missing_one_is_now():
    assert object_age.capture_stamp(99.9, 100.0) == 99.9
    assert object_age.capture_stamp(None, 100.0) == 100.0


@pytest.mark.parametrize("raw", [130.0, 90.0, math.nan, math.inf, "soon"])
def test_a_broken_stamp_is_refused_rather_than_read_as_fresh(raw):
    with pytest.raises(ValueError):
        object_age.capture_stamp(raw, 100.0)
