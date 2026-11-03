"""Mask depth, the support-plane fallback, track ids and the additive wire fields.

Same rules as test_grounding.py: depth frames are lists of rows, millimetres,
no numpy, so this runs on a laptop with nothing installed.

The scene for the plane tests is the one that motivated it: a camera looking
level across a table whose top is 0.30 m below the lens, and a glass standing
on it 1.0 m away. Active-IR stereo reads THROUGH the glass to whatever is
behind it, so the glass's own pixels say "3 m" — or nothing at all.
"""

from __future__ import annotations

from c3po_vision import detector
from c3po_vision.grounding import (
    CameraExtrinsic,
    Intrinsics,
    fit_plane,
    ground_box,
    median_depth_m,
    ray_bearing_deg,
)
from c3po_vision.tracking import IouTracker, iou

INTR = Intrinsics(fx=600.0, fy=600.0, cx=320.0, cy=240.0, width=640, height=480)
LEVEL_CAM = CameraExtrinsic(x_m=0.10, y_m=0.0, z_m=1.20, pitch_deg=0.0)

TABLE_DROP_M = 0.30          # table top, below the lens
GLASS = (300, 340, 340, 420)  # its bottom edge, row 420, is where the table is 1.0 m away


def table_scene() -> list[list[int]]:
    """Wall at 3 m above the horizon; below it, the table, z = 0.3 * fy / (v - cy)."""
    rows = []
    for v in range(480):
        if v <= 240:
            mm = 3000
        else:
            mm = min(3000, round(TABLE_DROP_M * 600.0 / (v - 240) * 1000))
        rows.append([mm] * 640)
    return rows


def fill(depth, box, mm):
    x0, y0, x1, y1 = box
    for v in range(y0, y1):
        for u in range(x0, x1):
            depth[v][u] = mm


# --- mask-median depth --------------------------------------------------------


def test_the_mask_keeps_the_wall_between_the_legs_out_of_the_depth():
    # A box whose left half is the object (1.0 m) and right half the wall
    # (3.0 m). The inner region straddles both; the mask says which is which.
    depth = [[3000] * 640 for _ in range(480)]
    fill(depth, (100, 100, 150, 200), 1000)
    box = (100, 100, 200, 200)
    mask = [[u < 50 for u in range(100)] for _ in range(100)]
    z, _n = median_depth_m(depth, box, width=640, height=480, mask=mask)
    assert z == 1.0


def test_a_mask_over_nothing_but_holes_falls_back_to_the_inner_region():
    depth = [[0] * 640 for _ in range(480)]
    fill(depth, (125, 125, 175, 175), 2000)  # only the inner region has depth
    mask = [[u < 10 and v < 10 for u in range(100)] for v in range(100)]
    z, _n = median_depth_m(depth, (100, 100, 200, 200), width=640, height=480, mask=mask)
    assert z == 2.0


# --- the support plane ----------------------------------------------------------


def test_a_glass_read_through_to_the_wall_is_placed_on_the_table_instead():
    depth = table_scene()
    fill(depth, GLASS, 3000)
    g = ground_box(GLASS, depth, INTR, extr=LEVEL_CAM)
    assert g is not None
    assert g.depth_source == "plane"
    assert abs(g.depth_m - 1.0) < 0.03


def test_a_glass_with_no_depth_at_all_is_placed_on_the_table():
    depth = table_scene()
    fill(depth, GLASS, 0)
    g = ground_box(GLASS, depth, INTR, extr=LEVEL_CAM)
    assert g is not None and g.depth_source == "plane"
    assert abs(g.depth_m - 1.0) < 0.03


def test_an_opaque_cup_on_the_same_table_keeps_its_own_depth():
    depth = table_scene()
    fill(depth, GLASS, 1020)
    g = ground_box(GLASS, depth, INTR, extr=LEVEL_CAM)
    assert g is not None and g.depth_source == "direct"
    assert g.depth_m == 1.02


def test_a_vertical_surface_under_the_box_is_not_a_table():
    # A TV on the wall above a sofa: the sofa back under it is nearer, but it is
    # not something the TV stands on. Keep the direct reading.
    depth = [[3000] * 640 for _ in range(480)]
    fill(depth, (0, 420, 640, 480), 1000)  # fronto-parallel, 1 m
    g = ground_box(GLASS, depth, INTR, extr=LEVEL_CAM)
    assert g is not None and g.depth_source == "direct"
    assert g.depth_m == 3.0


def test_the_fallback_can_be_switched_off():
    depth = table_scene()
    fill(depth, GLASS, 0)
    assert ground_box(GLASS, depth, INTR, extr=LEVEL_CAM, behind_m=None) is None


def test_the_plane_fit_rejects_a_cloud_with_no_plane_in_it():
    pts = [(float(i % 7), float(i % 5) * 0.3, float(i % 3) * 0.7 + i * 0.01) for i in range(40)]
    assert fit_plane(pts) is None
    flat = [(x * 0.1, 0.3, 1.0 + z * 0.1) for x in range(6) for z in range(6)]
    plane = fit_plane(flat)
    assert plane is not None and abs(abs(plane[0][1]) - 1.0) < 1e-6


def test_a_ray_bearing_keeps_the_sign_convention():
    assert ray_bearing_deg(100, 240, INTR, LEVEL_CAM) > 0   # left half of the image -> LEFT
    assert ray_bearing_deg(540, 240, INTR, LEVEL_CAM) < 0


# --- the tracker ------------------------------------------------------------------


def test_iou_of_identical_and_disjoint_boxes():
    assert iou((0, 0, 10, 10), (0, 0, 10, 10)) == 1.0
    assert iou((0, 0, 10, 10), (20, 20, 30, 30)) == 0.0


def test_an_object_that_barely_moves_keeps_its_id():
    t = IouTracker()
    a = t.update([("mug", (100, 100, 150, 150)), ("chair", (300, 200, 400, 400))])
    b = t.update([("chair", (305, 200, 405, 400)), ("mug", (102, 101, 152, 151))])
    assert b == [a[1], a[0]]


def test_a_new_object_gets_a_new_id_and_ids_are_never_reused():
    t = IouTracker(max_missed=1)
    (first,) = t.update([("mug", (100, 100, 150, 150))])
    (second,) = t.update([("mug", (400, 100, 450, 150))])
    assert second != first
    t.update([])
    t.update([])
    (third,) = t.update([("mug", (100, 100, 150, 150))])
    assert third not in (first, second)


def test_a_short_gap_does_not_lose_the_track():
    t = IouTracker(max_missed=3)
    (first,) = t.update([("wallet", (10, 10, 60, 40))])
    t.update([])
    t.update([])
    assert t.update([("wallet", (11, 10, 61, 40))]) == [first]


def test_a_label_flicker_keeps_the_id_only_when_the_box_is_the_same():
    t = IouTracker()
    (first,) = t.update([("cup", (100, 100, 150, 150))])
    assert t.update([("mug", (101, 100, 151, 150))]) == [first]
    assert t.update([("person", (120, 120, 170, 170))]) != [first]


# --- the wire ---------------------------------------------------------------------


def test_every_ranged_object_carries_a_track_id_and_a_depth_source():
    depth = table_scene()
    fill(depth, GLASS, 3000)
    fill(depth, (500, 300, 560, 380), 1500)
    dets = [(0, 0.9, GLASS), (1, 0.8, (500, 300, 560, 380))]
    objects, omitted, unranged = detector.ground_all(
        dets, depth, INTR, LEVEL_CAM, ["drinking glass", "backpack"], 0.001, tracker=IouTracker()
    )
    assert omitted == 0 and unranged == []
    by_label = {o["label"]: o for o in objects}
    assert by_label["drinking glass"]["depth_source"] == "plane"
    assert by_label["backpack"]["depth_source"] == "direct"
    assert {o["track_id"] for o in objects} == {1, 2}
    # The optical-frame point the bridge's arm IK uses: z is the depth that was ranged.
    bag = by_label["backpack"]["cam_xyz"]
    assert len(bag) == 3 and abs(bag[2] - 1.5) < 1e-6 and bag[0] > 0


def test_a_detection_with_no_depth_is_reported_unranged_never_at_range_zero():
    depth = [[0] * 640 for _ in range(480)]
    objects, _omitted, unranged = detector.ground_all(
        [(0, 0.7, (100, 100, 200, 200))], depth, INTR, LEVEL_CAM, ["wallet"], 0.001,
        tracker=IouTracker(),
    )
    assert objects == []
    assert unranged == [{"label": "wallet", "bearing_deg": unranged[0]["bearing_deg"],
                         "confidence": 0.7, "depth_source": "invalid", "track_id": 1}]
    assert unranged[0]["bearing_deg"] > 0  # left half of the image


def test_the_cap_is_a_parameter_and_still_counts_what_it_drops():
    depth = [[2000] * 640 for _ in range(480)]
    dets = [(0, 0.9, (10 + 60 * i, 100, 50 + 60 * i, 200)) for i in range(5)]
    objects, omitted, _ = detector.ground_all(dets, depth, INTR, LEVEL_CAM, ["box"], 0.001,
                                              max_objects=3)
    assert len(objects) == 3 and omitted == 2
    assert "track_id" not in objects[0]  # no tracker, no invented id


def test_the_report_only_grows_and_keeps_its_version():
    old = detector.build_report([], 0, 1.0)
    new = detector.build_report([], 0, 1.0, unranged=[])
    assert new["v"] == old["v"] == 1
    assert set(new) - set(old) == {"unranged"}
