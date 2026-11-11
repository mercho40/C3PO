"""Detections are aged from the frame grab, on a laptop with no ROS."""

from __future__ import annotations

import importlib.util
import json
import math
import sys
import types

import pytest
from bridge import world_model
from c3po_perception import object_age
from conftest import WORLD_MODEL_PUBLISHER_PY

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


def _publisher(monkeypatch, clock):
    """The real node's methods on a bare object: just enough ROS stubbed to import it."""
    stub = types.SimpleNamespace
    for name, attrs in {
        "rclpy": {}, "rclpy.node": {"Node": object},
        "rclpy.qos": dict.fromkeys(("DurabilityPolicy", "HistoryPolicy", "QoSProfile",
                                    "ReliabilityPolicy", "qos_profile_sensor_data")),
        "nav_msgs": {}, "nav_msgs.msg": {"Odometry": object},
        "sensor_msgs": {}, "sensor_msgs.msg": {"LaserScan": object},
        "std_msgs": {}, "std_msgs.msg": {"String": stub},
    }.items():
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
        for k, v in attrs.items():
            setattr(sys.modules[name], k, v)
    spec = importlib.util.spec_from_file_location("wmp_under_test", WORLD_MODEL_PUBLISHER_PY)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.setattr(mod, "time", stub(time=lambda: clock[0]))
    node = mod.WorldModelPublisher.__new__(mod.WorldModelPublisher)
    node.__dict__.update(
        _pose=None, _pose_stamp=0.0, _pose_seen=0.0, _objects=[], _objects_omitted=0,
        _objects_seen=0.0, _objects_stamp=0.0, _free_space=None, _scan_seen=0.0,
        _scan_msg=None, _notes=[], published=[],
    )
    node.pub = stub(publish=lambda msg: node.published.append(json.loads(msg.data)))
    return node


def test_the_publisher_ages_objects_from_the_grab_not_the_arrival(monkeypatch):
    clock = [1000.0]
    node = _publisher(monkeypatch, clock)
    # Grabbed 0.6 s before it reached the node, emitted 0.2 s after that.
    node._on_objects(types.SimpleNamespace(data=json.dumps(
        {"v": 1, "stamp_unix": 999.4, "objects": [CUP]})))
    clock[0] += 0.2
    node._emit()
    assert node.published[-1]["objects"] == [dict(CUP, age_s=0.8)]
