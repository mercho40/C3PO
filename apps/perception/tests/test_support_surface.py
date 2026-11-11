"""The surface a detection stands on: its height and its near edge, for the arm.

Scenes are ray-cast by hand at the real mount (DEFAULT_CAMERA_EXTRINSIC, 47.6 deg
nose-down), like test_depth_and_tracking.pitched_scene, but with a desk that ENDS:
a top at base_link z `top`, a front face at base_link x `edge`, the floor under it.
Lists of rows, millimetres, no numpy.
"""

from __future__ import annotations

import json
import math
import random
import types

import pytest
from c3po_vision import detector
from c3po_vision.grounding import DEFAULT_CAMERA_EXTRINSIC as EXTR
from test_depth_and_tracking import INTR, fill, pitched_scene
from test_object_age import _publisher

from bringup.spec import vision_env_passthrough

FLOOR = -0.78   # base_link (pelvis) z of the floor, the G1 standing
CUP_H, CUP_HALF_W = 0.10, 0.04


def _ray(u: float, v: float) -> tuple[float, float, float]:
    """Pixel ray at optical depth 1, in base_link axes (pitch only, no yaw)."""
    p = math.radians(EXTR.pitch_deg)
    xo, yo = (u - INTR.cx) / INTR.fx, (v - INTR.cy) / INTR.fy
    return math.cos(p) - yo * math.sin(p), -xo, -math.sin(p) - yo * math.cos(p)


def _project(x: float, y: float, z: float) -> tuple[float, float]:
    """base_link point -> pixel (u, v), the inverse of _ray."""
    p = math.radians(EXTR.pitch_deg)
    dx, dy, dz = x - EXTR.x_m, y - EXTR.y_m, z - EXTR.z_m
    xr, zr = dx * math.cos(p) - dz * math.sin(p), dx * math.sin(p) + dz * math.cos(p)
    return INTR.cx + INTR.fx * -dy / xr, INTR.cy + INTR.fy * -zr / xr


def desk(top: float, edge: float, slope_deg: float = 0.0, noise_mm: float = 0.0,
         holes: float = 0.0) -> list[list[int]]:
    """A desk top (rising `slope_deg` away from the robot) beyond x = `edge`, its front face, the floor."""
    k = math.tan(math.radians(slope_deg))
    rng = random.Random(7)
    rows = []
    for v in range(INTR.height):
        dx, _dy, dz = _ray(INTR.cx, v)
        t = (top - EXTR.z_m + (EXTR.x_m - edge) * k) / (dz - dx * k)
        if t <= 0 or EXTR.x_m + t * dx < edge:   # not on the top: the face, else the floor
            t = (edge - EXTR.x_m) / dx
            if not FLOOR <= EXTR.z_m + t * dz <= top:
                t = (FLOOR - EXTR.z_m) / dz
        if noise_mm or holes:
            rows.append([0 if rng.random() < holes else round(t * 1000 + rng.gauss(0, noise_mm))
                         for _ in range(INTR.width)])
        else:
            rows.append([round(t * 1000)] * INTR.width)
    return rows


def put_cup(depth: list[list[int]], x: float, z: float, y: float = 0.0,
            tall: float = CUP_H) -> tuple[int, int, int, int]:
    """A cup's front face (a vertical plane at base x) standing at height z; returns its box."""
    u0, v1 = _project(x, y + CUP_HALF_W, z)
    u1, v0 = _project(x, y - CUP_HALF_W, z + tall)
    box = (math.floor(u0), math.floor(v0), math.ceil(u1), math.ceil(v1))
    for v in range(max(0, box[1]), min(box[3], INTR.height)):
        dx, _dy, _dz = _ray(INTR.cx, v)
        depth[v][box[0]:box[2]] = [round((x - EXTR.x_m) / dx * 1000)] * (box[2] - box[0])
    return box


def objects(depth, box, label="cup", stamp=1000.25):
    found, _omitted, _unranged = detector.ground_all(
        [(0, 0.9, box)], depth, INTR, EXTR, [label], 0.001, stamp=stamp)
    assert len(found) == 1
    return found[0]


# --- the table ------------------------------------------------------------------


@pytest.mark.parametrize("top", [-0.10, 0.0, 0.08])   # tables 0.68, 0.78, 0.86 m high
@pytest.mark.parametrize("edge", [0.32, 0.40])
@pytest.mark.parametrize("y", [0.0, 0.22, -0.22])   # ahead, and ~20 deg off to either side
def test_the_table_top_and_its_near_edge_are_measured(top, edge, y):
    depth = desk(top, edge)
    obj = objects(depth, put_cup(depth, edge + 0.20, top, y))
    s = obj["support"]
    assert abs(s["z_m"] - top) < 0.01
    along = edge / math.cos(math.radians(obj["bearing_deg"]))  # the edge, along the bearing
    assert s["near_edge_m"] is not None and abs(s["near_edge_m"] - along) < 0.01
    assert s["near_edge_m"] < obj["range_m"]
    assert s["inliers"] >= 30 and s["rms_m"] < 0.005
    assert s["stamp_unix"] == 1000.25


def test_a_tall_bottle_near_the_edge_still_finds_its_table():
    # Its depth grows down its face, so the bottom pixel at the median DEPTH lands 11 cm
    # above the table; at the object's RANGE it lands on it.
    depth = desk(0.0, 0.35)
    s = objects(depth, put_cup(depth, 0.40, 0.0, tall=0.30))["support"]
    assert abs(s["z_m"]) < 0.01 and abs(s["near_edge_m"] - 0.35) < 0.005


def test_noisy_depth_with_holes_still_finds_the_table():
    depth = desk(0.0, 0.35, noise_mm=3.0, holes=0.25)
    s = objects(depth, put_cup(depth, 0.55, 0.0))["support"]
    assert abs(s["z_m"]) < 0.01
    assert s["near_edge_m"] is not None and abs(s["near_edge_m"] - 0.35) < 0.02
    assert s["rms_m"] < 0.0045   # the 3 mm noise, not front-face points the fit swallowed


def test_an_object_on_the_floor_gets_the_floor_never_the_desk_behind_it():
    depth = desk(0.0, 1.40)   # the desk is behind the cup, higher up the picture
    s = objects(depth, put_cup(depth, 1.0, FLOOR)).get("support")
    assert s is not None and abs(s["z_m"] - FLOOR) < 0.02
    assert s["near_edge_m"] is None   # the floor runs off the bottom of the image


@pytest.mark.parametrize("slope_deg", [20.0, 30.0])
def test_a_slanted_surface_is_not_a_support(slope_deg):
    depth = desk(0.0, 0.30, slope_deg=slope_deg)
    x = 0.50
    assert "support" not in objects(depth, put_cup(depth, x, (x - 0.30) * math.tan(
        math.radians(slope_deg))))


def test_a_table_that_runs_off_the_image_has_no_edge():
    depth = desk(0.0, 0.15)   # the edge is under the camera's lowest row
    s = objects(depth, put_cup(depth, 0.45, 0.0))["support"]
    assert abs(s["z_m"]) < 0.01 and s["near_edge_m"] is None


def test_a_box_cut_off_by_the_bottom_border_has_no_edge():
    depth = desk(0.0, 0.15)
    box = put_cup(depth, 0.22, 0.0)
    assert box[3] > INTR.height   # the cup's bottom is below the picture
    s = objects(depth, box)["support"]   # the table beside it still counts
    assert abs(s["z_m"]) < 0.01 and s["near_edge_m"] is None


def test_no_horizontal_surface_means_no_field():
    depth = pitched_scene("wall")
    fill(depth, (300, 200, 340, 260), 800)
    assert "support" not in objects(depth, (300, 200, 340, 260))
    for holes in (1.0, 0.85):   # nothing around the cup, or too little to call a plane
        depth = desk(0.0, 0.35, holes=holes)
        assert "support" not in objects(depth, put_cup(depth, 0.55, 0.0))


def test_a_surface_rougher_than_depth_noise_is_not_trusted():
    depth = desk(0.0, 0.35, noise_mm=15.0)
    assert "support" not in objects(depth, put_cup(depth, 0.55, 0.0))


def test_a_person_gets_no_support():
    depth = desk(0.0, 0.35)
    assert "support" not in objects(depth, put_cup(depth, 0.55, 0.0), label="person")


def test_only_the_nearest_few_pay_for_a_fit():
    depth = desk(0.0, 0.35)
    spots = [(0.50 + 0.05 * i, 0.24 - 0.12 * i) for i in range(detector.SUPPORT_PER_FRAME + 1)]
    dets = [(0, 0.9, put_cup(depth, x, 0.0, y)) for x, y in spots]
    found, _omitted, _unranged = detector.ground_all(dets, depth, INTR, EXTR, ["cup"], 0.001)
    assert ["support" in o for o in found] == [True] * detector.SUPPORT_PER_FRAME + [False]


def test_the_kill_switch_turns_it_off_and_reaches_the_container(monkeypatch):
    depth = desk(0.0, 0.35)
    box = put_cup(depth, 0.55, 0.0)
    monkeypatch.setenv("C3PO_VISION_SUPPORT", "0")
    assert "support" not in objects(depth, box)
    monkeypatch.setenv("C3PO_VISION_SUPPORT", "1")
    assert "support" in objects(depth, box)
    assert vision_env_passthrough({"C3PO_VISION_SUPPORT": "0"}) == {"C3PO_VISION_SUPPORT": "0"}


# --- the wire -------------------------------------------------------------------


def test_the_world_model_publisher_passes_support_through(monkeypatch):
    clock = [1000.5]
    node = _publisher(monkeypatch, clock)
    support = {"z_m": 0.01, "near_edge_m": 0.33, "inliers": 120, "rms_m": 0.002,
               "stamp_unix": 1000.25}
    cup = {"label": "cup", "range_m": 0.55, "bearing_deg": 1.0, "support": support}
    node._on_objects(types.SimpleNamespace(data=json.dumps(
        {"v": 1, "stamp_unix": 1000.25, "objects": [cup]})))
    node._emit()
    assert node.published[-1]["objects"][0]["support"] == support
