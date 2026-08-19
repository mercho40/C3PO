"""Pure-maths tests for `c3po_vision.grounding` — the 3D grounding step.

This is the half of the vision container that can be wrong silently. The
TensorRT half fails loudly: a bad engine throws, a missing ONNX exits 1 at the
entrypoint. Grounding never throws. It turns a pixel box plus a depth frame
into a range and a bearing, and every way it can be wrong — a transposed
intrinsic, a metre/millimetre mixup, an averaged-in zero-depth pixel, a flipped
bearing sign — produces a plausible number that reaches the model as fact.

None of it needs a camera, CUDA, TensorRT, DDS or ROS, which is why it is
tested here on the Mac rather than discovered on the robot. `grounding.py`
imports nothing outside the stdlib on purpose; the day that stops being true,
this file stops collecting, and that is the alarm working.

The module ships into the l4t-jetpack:r35.3.1 image, whose interpreter is
python 3.8 (JetPack 5's python3-libnvinfer hard-depends `python3 (>= 3.8),
python3 (<< 3.9)`), and imports here under the harness interpreter. Nothing
below asks it for anything the 3.8 copy would not have.

------------------------------------------------------------------------------
WHAT THIS FILE PINS, AND WHY EACH ONE IS HERE
------------------------------------------------------------------------------
1. The pinhole deprojection, against intrinsics whose answers are computable by
   hand — including one asymmetric set (fx != fy, cx != cy), because a
   transposed intrinsic matrix survives every square-image test.
2. The optical -> base_link relabel and the fixed mount, which is the whole of
   D2.2 option 1: the container that owns the camera resolves the extrinsic
   itself, so there is no TF in this container and none in the bridge.
3. The median-over-an-inner-region depth, with invalid (zero) pixels mixed in.
   0 is the D435i's "I could not solve this pixel", never a distance.
4. An all-invalid box producing NO detection rather than one at range 0.
5. THE BEARING SIGN. See `test_an_object_to_the_robots_LEFT_has_a_POSITIVE_bearing`
   below. That one is not a unit test of a helper; it is the LLM's interface.

Numbers below use a 640x480 stream at fx = fy = 600, principal point
(320, 240) — close enough to a real D435i colour stream to keep the maths
honest, round enough that every expected value is checkable on paper.
"""

from __future__ import annotations

import math

import pytest
from bridge.world_model import Observation
from c3po_vision import grounding
from c3po_vision.grounding import (
    CameraExtrinsic,
    Grounded,
    Intrinsics,
    camera_to_base,
    clamp_box,
    deproject_pixel_to_camera,
    ground_box,
    inner_region,
    median_depth_m,
    norm180,
    range_bearing_from_base,
    sample_depths_m,
    to_observation,
)

INTR = Intrinsics(fx=600.0, fy=600.0, cx=320.0, cy=240.0, width=640, height=480)

# Camera 0.10 m forward of base_link's origin, on the centreline, 1.20 m up,
# looking straight ahead. Every test states its own extrinsic rather than
# leaning on `DEFAULT_CAMERA_EXTRINSIC`: those numbers are an UNMEASURED
# reasoned default — grounding.py says so in capitals — and a test that
# asserted them would start failing on the day somebody finally measures the
# robot with a tape, which is the one day you want this suite green.
LEVEL_CAM = CameraExtrinsic(x_m=0.10, y_m=0.0, z_m=1.20, pitch_deg=0.0)


def frame(width: int = 640, height: int = 480) -> list[list[int]]:
    """An all-invalid depth frame: 0 everywhere, i.e. "the camera solved nothing".

    A list of rows, not a numpy array. `grounding` indexes `depth[v][u]` and
    imports no numpy, so this suite needs none either — which is what keeps it
    runnable on a laptop with nothing installed.
    """
    return [[0] * width for _ in range(height)]


def fill(depth: list[list[int]], box: tuple[int, int, int, int], mm: int) -> None:
    """Write a raw depth value (millimetres, i.e. `depth_scale=0.001`) over a box.

    x1/y1 exclusive, as everywhere else in this module.
    """
    x0, y0, x1, y1 = box
    for v in range(y0, y1):
        for u in range(x0, x1):
            depth[v][u] = mm


# --------------------------------------------------------------------------
# Deprojection — the pinhole maths, against intrinsics with known answers
# --------------------------------------------------------------------------


def test_the_principal_point_deprojects_onto_the_optical_axis():
    # The one pixel where the maths cannot hide a swapped fx/fy or a stray sign.
    assert deproject_pixel_to_camera(320.0, 240.0, 2.0, INTR) == pytest.approx((0.0, 0.0, 2.0))


def test_deprojection_is_linear_in_depth_and_uses_the_matching_focal_length():
    # 60 px right of centre at 2 m: 60 * 2 / 600 = 0.2 m right. +x is RIGHT in
    # the optical frame (REP-145).
    x, y, z = deproject_pixel_to_camera(380.0, 240.0, 2.0, INTR)
    assert x == pytest.approx(0.2)
    assert y == pytest.approx(0.0)
    assert z == pytest.approx(2.0)

    # 60 px BELOW centre at 1.5 m: 60 * 1.5 / 600 = 0.15 m, and +y is DOWN.
    # That sign never shows up in a bearing; it shows up as objects at the
    # wrong height, i.e. as a height filter that drops the wrong things.
    x, y, _ = deproject_pixel_to_camera(320.0, 300.0, 1.5, INTR)
    assert x == pytest.approx(0.0)
    assert y == pytest.approx(0.15)

    # Same pixel, twice the depth, twice the offset. `z_m` is depth ALONG THE
    # OPTICAL AXIS, not slant range to the pixel, which is why this is a
    # multiply and the ray never needs normalising.
    x_far, _, _ = deproject_pixel_to_camera(380.0, 240.0, 4.0, INTR)
    assert x_far == pytest.approx(0.4)


def test_asymmetric_intrinsics_do_not_cross_axes():
    # fx != fy and cx != cy: a transposed intrinsic matrix passes every
    # square-image test above and dies here.
    intr = Intrinsics(fx=600.0, fy=300.0, cx=320.0, cy=200.0, width=640, height=400)
    x, y, _ = deproject_pixel_to_camera(320.0 + 60.0, 200.0 + 60.0, 2.0, intr)
    assert x == pytest.approx(60.0 * 2.0 / 600.0)
    assert y == pytest.approx(60.0 * 2.0 / 300.0)


# --------------------------------------------------------------------------
# Optical -> base_link: the axis relabel and the fixed mount
# --------------------------------------------------------------------------


def test_camera_to_base_is_the_axis_relabel_then_the_mount_offset():
    # Optical (right, down, forward) -> base_link (forward, LEFT, up):
    #   x_base =  z_opt    y_base = -x_opt    z_base = -y_opt
    # So (0.2 right, 0.15 down, 2.0 ahead) is 2.0 forward, 0.2 to the RIGHT
    # (y = -0.2) and 0.15 below the lens, before the mount offset is added.
    # Those two minus signs are the only negations in the whole path and they
    # are relabels, not corrections.
    x, y, z = camera_to_base((0.2, 0.15, 2.0), LEVEL_CAM)
    assert x == pytest.approx(2.0 + 0.10)
    assert y == pytest.approx(-0.2)
    assert z == pytest.approx(1.20 - 0.15)


def test_positive_pitch_is_nose_down_and_moves_nothing_sideways():
    # A camera pitched 30 deg DOWN sees a point 2 m along ITS optical axis as
    # 1.73 m ahead and 1.0 m below the mount. Pitch is the rotation most likely
    # to be applied about the wrong axis, and doing it about x or z instead
    # would move the point sideways — so `y == 0` is the real content here, and
    # `z < z_m` is the nose-down sign check grounding.py's docstring asks for.
    pitched = CameraExtrinsic(x_m=0.0, y_m=0.0, z_m=1.20, pitch_deg=30.0)
    x, y, z = camera_to_base((0.0, 0.0, 2.0), pitched)
    assert x == pytest.approx(2.0 * math.cos(math.radians(30.0)))
    assert y == pytest.approx(0.0)
    assert z == pytest.approx(1.20 - 2.0 * math.sin(math.radians(30.0)))
    assert z < pitched.z_m


def test_pitch_never_moves_an_off_axis_point_across_the_centreline():
    # The failure a single on-axis test cannot see: a rotation applied about
    # the wrong axis leaves an on-axis point alone and swings an off-axis one.
    # An object 0.4 m to the robot's LEFT is still on the left after the camera
    # is pitched at the floor — pitch changes range and height, never side.
    pitched = CameraExtrinsic(x_m=0.10, y_m=0.0, z_m=1.20, pitch_deg=25.0)
    _, y, _ = camera_to_base((-0.4, 0.0, 2.0), pitched)   # -0.4 optical x = LEFT
    assert y == pytest.approx(0.4)


def test_positive_yaw_is_LEFT_like_every_other_angle_in_this_repo():
    # A squint mount is modelled, so its sign has to be pinned too. Rotate the
    # camera 90 deg CCW and a point straight down its optical axis must land on
    # the robot's LEFT (+y), not its right. Same convention as bearing_deg, as
    # `turn`'s delta_yaw_radians, and as REP-103.
    yawed = CameraExtrinsic(x_m=0.0, y_m=0.0, z_m=0.0, yaw_deg=90.0)
    x, y, z = camera_to_base((0.0, 0.0, 2.0), yawed)
    assert x == pytest.approx(0.0, abs=1e-12)
    assert y == pytest.approx(2.0)
    assert z == pytest.approx(0.0, abs=1e-12)


def test_range_is_horizontal_and_slant_is_never_shorter():
    # `range_m` is the GROUND-PLANE distance, deliberately: it is what "how far
    # away is it" means when you are deciding whether to walk there, and it is
    # the same quantity the LaserScan-derived free_space sectors report — so
    # the model never compares two definitions of distance inside one snapshot.
    rng, bearing, elevation, slant = range_bearing_from_base(3.0, 4.0, 12.0)
    assert rng == pytest.approx(5.0)                       # 3-4-5
    assert slant == pytest.approx(13.0)                    # 5-12-13
    assert slant > rng
    assert bearing == pytest.approx(math.degrees(math.atan2(4.0, 3.0)))
    assert elevation == pytest.approx(math.degrees(math.atan2(12.0, 5.0)))
    assert elevation > 0.0                                 # +z is UP


def test_norm180_wraps_without_moving_a_bearing_already_in_range():
    assert norm180(0.0) == pytest.approx(0.0)
    assert norm180(45.0) == pytest.approx(45.0)
    assert norm180(-45.0) == pytest.approx(-45.0)
    assert norm180(190.0) == pytest.approx(-170.0)         # just past the left rear
    assert norm180(-190.0) == pytest.approx(170.0)
    assert norm180(360.0) == pytest.approx(0.0)
    # Whatever goes in, what comes out is a bearing the contract can carry, and
    # wrapping twice is a no-op.
    #
    # NOTE, not asserted either way: the contract is written (-180, 180] and
    # this helper puts dead astern on -180 rather than +180. They are the same
    # bearing and a forward-facing mount cannot reach either — every bearing
    # `ground_box` can produce with this extrinsic has |x_base| >= x_m > 0 — so
    # it is a documentation mismatch rather than a behaviour one. If a rear
    # camera or a yawed mount ever lands here, pin the endpoint then.
    for deg in (-540.0, -181.0, -1.0, 0.0, 1.0, 181.0, 540.0, 1e6):
        wrapped = norm180(deg)
        assert -180.0 <= wrapped <= 180.0
        assert norm180(wrapped) == pytest.approx(wrapped)


# --------------------------------------------------------------------------
# Boxes: clipping, and the inner region the depth is actually taken over
# --------------------------------------------------------------------------


def test_a_box_hanging_off_the_image_is_clipped_not_wrapped():
    # A detector run on a letterboxed input routinely emits boxes a few pixels
    # over the edge. Indexing with those is an IndexError at best and, in
    # Python, a SILENT wrap to the far side of the image at worst — an object
    # on the left reported from the right, at a plausible range.
    assert clamp_box((-20.0, -10.0, 100.0, 100.0), 640, 480) == (0, 0, 100, 100)
    assert clamp_box((600.0, 440.0, 900.0, 900.0), 640, 480) == (600, 440, 640, 480)
    # Inverted corners are a box, not an error.
    assert clamp_box((100.0, 100.0, 40.0, 40.0), 640, 480) == (40, 40, 100, 100)
    # Entirely outside the image is not a box at all.
    assert clamp_box((700.0, 500.0, 800.0, 600.0), 640, 480) is None


def test_the_inner_region_is_the_middle_of_the_box_and_never_empty():
    # Default: the central 50% in each dimension, a quarter of the area, far
    # enough inside that background bleeding in at the box edge cannot reach
    # the sample set.
    assert inner_region((0, 0, 8, 8)) == (2, 2, 6, 6)
    assert inner_region((0, 0, 8, 8), 1.0) == (0, 0, 8, 8)
    # A 1 px box has no middle to take. It must still come back indexable —
    # an empty range would read downstream as "no depth here", which is the
    # one answer that is not true.
    x0, y0, x1, y1 = inner_region((10, 10, 11, 11))
    assert x1 > x0 and y1 > y0


# --------------------------------------------------------------------------
# Median depth — where a zero-depth pixel becomes a phantom obstacle
# --------------------------------------------------------------------------


def median_of(depth, box, **kw):
    """`median_depth_m` with the image size taken from the array itself.

    `min_valid_samples` drops to 1 here because these fixtures are 3x3 and 8x8
    boxes chosen so the expected median is computable by eye. The default (12)
    is exercised on its own in `test_too_few_valid_samples_is_also_no_reading`.
    """
    kw.setdefault("min_valid_samples", 1)
    return median_depth_m(depth, box, width=len(depth[0]), height=len(depth), **kw)


def test_the_median_ignores_invalid_zero_depth_pixels():
    # A 3x3 box, six of nine pixels invalid. The D435i writes 0 for "I could
    # not solve this" (no texture, occluded, out of range, inside the min-Z
    # blind zone), and 0 is NOT 0 metres.
    #
    #   valid pixels are 1000, 2000, 3000 mm            -> median 2.0 m
    #   zeros counted in: [0,0,0,0,0,0,1000,2000,3000]  -> median 0.0 m
    #
    # The bug is not that 0.0 looks wrong. It is that the MEAN of the same nine
    # pixels is 0.67 m, which looks entirely plausible and puts a phantom
    # obstacle inside the robot's next step.
    depth = frame(3, 3)
    depth[0][1] = 1000
    depth[1][0] = 2000
    depth[1][2] = 3000

    metres, samples = median_of(depth, (0, 0, 3, 3), inner_fraction=1.0)
    assert metres == pytest.approx(2.0)
    # The count comes back with the value because it is the caller's only
    # evidence of how much of the object was actually seen.
    assert samples == 3


def test_the_median_rejects_depths_outside_the_sensors_usable_band():
    # Below the min-Z a D435i emits noise; past ~10 m the stereo disparity is
    # down in the sensor noise and the reading is a coin flip. Both are
    # INVALID, not "very near" and "very far" — clamping either in would drag
    # the median toward whichever artefact happens to be present.
    too_near_mm = max(1, int(grounding.MIN_VALID_DEPTH_M * 1000.0) - 1)
    too_far_mm = int(grounding.MAX_VALID_DEPTH_M * 1000.0) + 1

    depth = frame(3, 3)
    depth[0][0] = too_near_mm
    depth[0][1] = 1000
    depth[0][2] = 2000
    depth[1][0] = 3000
    depth[1][1] = too_far_mm

    metres, samples = median_of(depth, (0, 0, 3, 3), inner_fraction=1.0)
    assert metres == pytest.approx(2.0)
    assert samples == 3


def test_the_median_honours_the_depth_scale():
    # pyrealsense2 hands over raw uint16 units and the scale is a QUERIED
    # device property (`sensor.get_depth_scale()`) — nominally 0.001 on a
    # D435i, not guaranteed. Baking millimetres in is a metre/millimetre error
    # that reads as "everything is 1000x too close".
    depth = frame(3, 3)
    depth[0][0] = 4000
    depth[0][1] = 4000
    depth[0][2] = 4000

    metres, _ = median_of(depth, (0, 0, 3, 3), depth_scale=0.00025, inner_fraction=1.0)
    assert metres == pytest.approx(1.0)


def test_the_default_inner_region_samples_the_object_not_the_wall_behind_it():
    # A detector box is a rectangle around a non-rectangular object, so its
    # border pixels are mostly BACKGROUND. Sampling the whole box reports the
    # wall behind the person; sampling the middle reports the person.
    #
    #   8x8 box, border 1000 mm (the wall), central 4x4 3000 mm (the object).
    #   inner_fraction=0.5 -> the central 4x4 only      -> 3.0 m
    #   inner_fraction=1.0 -> 48 wall px vs 16 object   -> 1.0 m
    depth = frame(8, 8)
    fill(depth, (0, 0, 8, 8), 1000)
    fill(depth, (2, 2, 6, 6), 3000)

    metres, samples = median_of(depth, (0, 0, 8, 8))
    assert metres == pytest.approx(3.0)
    assert samples == 16

    metres, samples = median_of(depth, (0, 0, 8, 8), inner_fraction=1.0)
    assert metres == pytest.approx(1.0)
    assert samples == 64


def test_a_box_with_no_valid_depth_yields_no_reading_at_all():
    # Not 0.0, not inf, not the previous frame's value. None.
    metres, samples = median_of(frame(8, 8), (0, 0, 8, 8))
    assert metres is None
    assert samples == 0


def test_too_few_valid_samples_is_also_no_reading():
    # A handful of surviving pixels in a large box is a hole, not a
    # measurement. `min_valid_samples` is where that line sits, and crossing it
    # must produce None rather than a confident median over three pixels.
    depth = frame(64, 64)
    depth[30][30] = 2000
    depth[31][31] = 2000
    depth[32][32] = 2000

    metres, samples = median_depth_m(
        depth, (0, 0, 64, 64), width=64, height=64, min_valid_samples=12
    )
    assert metres is None
    # It still reports what it found, so a caller can log "3 of 1024" rather
    # than an indistinguishable "nothing at all".
    assert samples == 3


def test_depth_sampling_is_bounded_so_a_big_box_cannot_cost_a_frame():
    # This module has no numpy on purpose, so a full 400x400 region would be
    # 160,000 python-level lookups per detection per frame. The stride bounds
    # it; a median over ~1000 stratified samples is statistically
    # indistinguishable here and roughly 40x cheaper.
    depth = [[1500] * 400 for _ in range(400)]

    assert len(sample_depths_m(depth, (0, 0, 400, 400), max_samples=100)) == 100
    assert len(sample_depths_m(depth, (0, 0, 400, 400))) <= 1024
    # Small regions are not thinned at all — a 20x20 box keeps its 400 pixels.
    assert len(sample_depths_m(depth, (0, 0, 20, 20))) == 400


# --------------------------------------------------------------------------
# The whole step — and the one assertion that is baked into the LLM interface
# --------------------------------------------------------------------------


def detect(box: tuple[int, int, int, int], mm: int = 2000, **kw) -> Grounded | None:
    """Ground one box against a depth frame valid ONLY inside that box."""
    depth = frame()
    fill(depth, box, mm)
    return ground_box(box, depth, INTR, extr=LEVEL_CAM, **kw)


def test_an_object_at_the_image_centre_is_dead_ahead():
    g = detect((300, 220, 340, 260))
    assert g is not None
    # Zero, not "near zero in some convention": the box centre IS the principal
    # point, so there is nothing left for a sign to act on.
    assert g.bearing_deg == pytest.approx(0.0, abs=1e-9)
    # Horizontal range from base_link, so the 0.10 m mount offset is in it and
    # the 1.20 m mount height is not. The model reasons about distance to walk,
    # not distance to fly — the height is still there, named, as z_m/slant_m.
    assert g.range_m == pytest.approx(2.10)
    assert g.x_m == pytest.approx(2.10)
    assert g.y_m == pytest.approx(0.0, abs=1e-9)
    assert g.z_m == pytest.approx(1.20)
    assert g.slant_m == pytest.approx(math.hypot(2.10, 1.20))
    assert g.slant_m > g.range_m
    assert g.depth_m == pytest.approx(2.0)
    assert g.depth_samples == 400          # the central 20x20 of a 40x40 box


def test_an_object_to_the_robots_LEFT_has_a_POSITIVE_bearing():
    """D7's sign convention, and `turn`'s. A flip here is not a local bug.

    `bridge.world_model` states the contract: bearing is DEGREES, 0 straight
    ahead, POSITIVE to the LEFT (counter-clockwise), matching `turn`'s
    `delta_yaw_radians`, `geometry_msgs/Twist.angular.z`, and REP-103's
    +y-is-left / +yaw-is-CCW.

    The agent's prompt describes that contract in words. The model plans "the
    chair is at +30, turn +30" against it. Every skill downstream inherits it.
    So a sign flip here does not surface as a crash or an obviously wrong
    number — it surfaces as a robot that confidently turns AWAY from everything
    it is asked to approach, and because the convention is written into the
    prompt, THE FIX IS NOT LOCAL: it is baked into the LLM's interface.

    It is one of the three silent frame errors on this project's risk list (the
    others being the Rx(180) LiDAR mount correction and body- vs world-frame
    twist), and none of the three raises. Its live gate is this sentence, run
    on the real robot: stand to the robot's LEFT and confirm `bearing_deg` is
    POSITIVE.

    Left in the image (u < cx) is left of the robot, because the camera looks
    forward. 120 px left of the principal point at 2 m is 0.4 m left of the
    optical axis and 2.1 m ahead of base_link: atan2(0.4, 2.1) = +10.77 deg.

    If you find yourself relaxing this test, stop.
    """
    left = detect((180, 220, 220, 260))
    assert left is not None

    assert left.bearing_deg > 0.0, (
        "an object to the robot's LEFT must have a POSITIVE bearing_deg — "
        "D7's convention and `turn`'s; flipping it inverts the LLM interface"
    )
    assert left.y_m > 0.0, "+y is LEFT in base_link (REP-103)"
    assert left.bearing_deg == pytest.approx(10.77, abs=0.02)
    assert left.range_m == pytest.approx(math.hypot(2.10, 0.40), abs=1e-9)


def test_an_object_to_the_robots_right_has_a_negative_bearing():
    right = detect((420, 220, 460, 260))
    assert right is not None
    assert right.bearing_deg < 0.0
    assert right.y_m < 0.0
    assert right.bearing_deg == pytest.approx(-10.77, abs=0.02)


def test_left_and_right_are_exact_mirrors_about_the_optical_axis():
    # Catches an off-by-one in the box-centre calculation, which would appear
    # as a small constant bias that no single-sided test would notice.
    left = detect((180, 220, 220, 260))
    right = detect((420, 220, 460, 260))
    assert left is not None and right is not None
    assert left.bearing_deg == pytest.approx(-right.bearing_deg, abs=1e-9)
    assert left.range_m == pytest.approx(right.range_m, abs=1e-9)


def test_bearing_stays_inside_the_contracts_range():
    for box in ((0, 220, 40, 260), (300, 220, 340, 260), (600, 220, 640, 260)):
        g = detect(box)
        assert g is not None
        assert -180.0 < g.bearing_deg <= 180.0


def test_the_bearing_comes_from_the_box_centre_and_the_range_from_its_median():
    # Deliberately mixed: the centre is the best single BEARING estimate for
    # the object, the median is the best single RANGE estimate for it, and the
    # centre pixel's own depth is exactly the value we refused to trust.
    #
    # 40x40 box at the image centre, 2.5 m, with a four-pixel hole punched
    # through the middle. A centre-pixel implementation returns None here.
    box = (300, 220, 340, 260)
    depth = frame()
    fill(depth, box, 2500)
    for v in (239, 240):
        for u in (319, 320):
            depth[v][u] = 0

    g = ground_box(box, depth, INTR, extr=LEVEL_CAM)
    assert g is not None
    assert g.depth_m == pytest.approx(2.5)
    assert g.depth_samples == 396          # 400 minus the four-pixel hole
    assert g.bearing_deg == pytest.approx(0.0, abs=1e-9)
    assert g.range_m == pytest.approx(2.60)


def test_a_loose_box_reports_the_object_not_the_wall_behind_it():
    # The background-bleed case end to end: 3 m of wall around a 1.5 m object.
    # Sampling the whole box would put the object a metre and a half further
    # away than it is, and the robot would walk into it while still believing
    # it had room.
    box = (300, 200, 380, 280)
    depth = frame()
    fill(depth, box, 3000)
    fill(depth, (320, 220, 360, 260), 1500)

    g = ground_box(box, depth, INTR, extr=LEVEL_CAM)
    assert g is not None
    assert g.depth_m == pytest.approx(1.5)
    assert g.range_m == pytest.approx(1.60, abs=0.01)


def test_an_unresolvable_box_yields_no_detection_not_a_zero_range_one():
    # The failure this forbids: a box the depth camera could not resolve
    # (glass, backlight, closer than min-Z) becoming an object at range 0.0 m
    # and bearing 0 — an obstacle materialising INSIDE the robot.
    #
    # "Absent is not empty" applied to one object rather than one source.
    # Dropping it is correct; the detector's heartbeat still goes out on that
    # tick saying "online, and here is what I could ground", so the scene does
    # not become empty and the model is never told the way is clear.
    assert ground_box((300, 220, 340, 260), frame(), INTR, extr=LEVEL_CAM) is None


def test_a_box_entirely_outside_the_image_yields_no_detection():
    depth = frame()
    fill(depth, (0, 0, 640, 480), 2000)
    assert ground_box((700.0, 500.0, 760.0, 560.0), depth, INTR, extr=LEVEL_CAM) is None


def test_the_frame_size_falls_back_to_the_depth_array_when_the_intrinsics_omit_it():
    # `rs2_intrinsics` always carries width/height, but a hand-built Intrinsics
    # (a bag replay, a calibration file, this test) need not. Falling back to
    # the array's own dimensions keeps a hand-built one correct instead of
    # clamping every box to a 0x0 image and reporting nothing at all.
    sizeless = Intrinsics(fx=600.0, fy=600.0, cx=320.0, cy=240.0)
    depth = frame()
    fill(depth, (300, 220, 340, 260), 2000)

    g = ground_box((300, 220, 340, 260), depth, sizeless, extr=LEVEL_CAM)
    assert g is not None
    assert g.range_m == pytest.approx(2.10)


# --------------------------------------------------------------------------
# The wire shape: one grounded detection, as the bridge will read it
# --------------------------------------------------------------------------


def test_a_grounded_detection_is_directly_constructible_as_an_observation():
    # The two ends of the wire, checked in one process. `to_observation()`'s
    # dict crosses domain 42 as JSON inside /c3po/objects, is folded into
    # /c3po/world_summary, and is handed to `Observation(**obj)` on the bridge
    # side. An extra or renamed key there is a TypeError on the robot at 4 Hz,
    # and this is the only place the two modules can be compared without DDS.
    g = detect((180, 220, 220, 260))
    assert g is not None

    payload = to_observation("person", g, confidence=0.87)
    obs = Observation(**payload)

    assert obs.label == "person"
    # The sign survives the hop. The assertion above, restated at the boundary,
    # because the boundary is where a "helpful" normalisation gets added.
    assert obs.bearing_deg > 0.0
    assert obs.to_dict()["bearing_deg"] == pytest.approx(10.8, abs=0.05)


def test_to_observation_rounds_exactly_like_observation_does():
    # Rounding is part of the contract, not cosmetic: it is at the precision
    # the sensor can actually support (1 cm, 0.1 deg), and it is what keeps 32
    # objects at 4 Hz a rounding error on a loopback socket. If the two sides
    # ever round differently, one detection reads as two different numbers
    # depending on which log you are looking at.
    g = detect((180, 220, 220, 260))
    assert g is not None

    mirror = Observation(
        label="person", range_m=g.range_m, bearing_deg=g.bearing_deg, confidence=0.87
    )
    assert to_observation("person", g, confidence=0.87) == mirror.to_dict()


def test_only_the_wire_fields_cross_and_an_absent_confidence_is_absent():
    # The diagnostic half of a Grounded (base_link xyz, elevation, slant, depth
    # provenance) exists so the detector can filter without re-deriving
    # anything. None of it is the model's business and none of it is worth the
    # tokens, so none of it may leak onto the wire.
    #
    # `confidence` and `age_s` are OMITTED when there is nothing to say, not
    # sent as null and not as 0.0: an absent key is unambiguous, a null is a
    # value the bridge has to special-case, and a 0.0 age would claim a
    # freshness the detection has not earned.
    g = detect((300, 220, 340, 260))
    assert g is not None
    assert g.z_m == pytest.approx(1.20)         # kept on the Grounded...

    bare = to_observation("chair", g)           # ...and not on the wire
    assert set(bare) == {"label", "range_m", "bearing_deg"}
    assert bare["range_m"] == pytest.approx(2.10)

    aged = to_observation("chair", g, confidence=0.5, age_s=0.44)
    assert aged["confidence"] == pytest.approx(0.5)
    assert aged["age_s"] == pytest.approx(0.4)
    assert set(aged) == {"label", "range_m", "bearing_deg", "confidence", "age_s"}


def test_every_wire_key_is_a_field_the_bridge_can_construct_an_observation_from():
    # Stated as a subset comparison rather than by constructing one object,
    # because the failure mode is a NEW key added on this side that
    # `Observation(**obj)` would reject with a TypeError on the robot.
    g = detect((300, 220, 340, 260))
    assert g is not None
    assert set(to_observation("chair", g, confidence=0.5, age_s=1.0)) <= set(
        Observation.__dataclass_fields__
    )
