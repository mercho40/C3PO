"""The LaserScan -> bearing-ring decimation, on a laptop with no ROS.

`scan_ring` is stdlib-only for the same reason `costmap_png` is: the decisions
in it are the kind that produce a plausible-looking picture and a wrong one, and
those are exactly the decisions that should not need a robot to check.

The one that matters most is minimum-per-bucket. Every other combining rule
produces a ring that looks fine and hides the thing you would walk into.
"""

from __future__ import annotations

import math

from c3po_perception import scan_ring


def _uniform(n: int, value: float) -> list[float]:
    return [value] * n


class TestMinimumPerBucket:
    """The safety decision: nearest return wins, never an average."""

    def test_a_thin_obstacle_survives_decimation(self):
        # A table leg at 0.6 m among wall returns at 5 m, all inside one bucket.
        # A mean would report ~4.6 m of clear space and the operator walks into
        # the table; the minimum reports the leg.
        ranges = _uniform(20, 5.0)
        ranges[7] = 0.6
        out = scan_ring.decimate(
            ranges, angle_min=0.0, angle_increment=math.radians(1.0),
            range_min=0.05, range_max=70.0, buckets=4,
        )
        # 20 samples over 20 degrees all land in the first 3-degree buckets of a
        # 4-bucket (90-degree) ring, so bucket 0 holds the leg.
        assert out[0] == 60, out

    def test_the_nearest_of_several_wins(self):
        ranges = [3.0, 1.25, 9.0, 2.0]
        out = scan_ring.decimate(
            ranges, angle_min=0.0, angle_increment=math.radians(0.5),
            range_min=0.05, range_max=70.0, buckets=8,
        )
        assert min(v for v in out if v is not None) == 125


class TestAbsenceIsNull:
    """No return must never encode as an obstacle, nor as a wall."""

    def test_infinite_returns_are_none_not_zero(self):
        out = scan_ring.decimate(
            [float("inf")] * 8, 0.0, math.radians(45.0), 0.05, 70.0, buckets=8
        )
        assert out == [None] * 8
        # 0 would read as an obstacle touching the robot: the most alarming
        # reading of the safest state.
        assert 0 not in out

    def test_nan_is_none(self):
        out = scan_ring.decimate(
            [float("nan")] * 4, 0.0, math.radians(90.0), 0.05, 70.0, buckets=4
        )
        assert out == [None] * 4

    def test_a_room_with_an_open_door_keeps_the_gap(self):
        # The failure this guards: encoding "nothing" as range_max draws a solid
        # ring, so the one direction the operator could walk looks blocked.
        ranges = [2.0, 2.0, float("inf"), 2.0]
        out = scan_ring.decimate(
            ranges, 0.0, math.radians(90.0), 0.05, 70.0, buckets=4
        )
        assert out[2] is None
        assert out[0] == 200 and out[1] == 200 and out[3] == 200


class TestRangeGating:
    def test_below_range_min_is_dropped(self):
        # Returns closer than the sensor's minimum are the sensor seeing itself.
        out = scan_ring.decimate(
            [0.01, 1.0], 0.0, math.radians(180.0), 0.05, 70.0, buckets=2
        )
        assert out[0] is None
        assert out[1] == 100

    def test_beyond_max_m_is_dropped_even_when_the_sensor_reports_it(self):
        # The Mid-360 reaches 70 m. A dot ring around an operator is useless
        # past room scale, and keeping far returns makes every bearing occupied.
        out = scan_ring.decimate(
            [40.0, 3.0], 0.0, math.radians(180.0), 0.05, 70.0,
            buckets=2, max_m=12.0,
        )
        assert out[0] is None
        assert out[1] == 300

    def test_max_m_can_be_raised_above_the_default(self):
        out = scan_ring.decimate(
            [40.0], 0.0, math.radians(180.0), 0.05, 70.0, buckets=2, max_m=50.0
        )
        assert out[0] == 4000


class TestBucketing:
    def test_bucket_zero_is_centred_on_angle_min(self):
        out = scan_ring.decimate(
            [1.0], angle_min=math.radians(-180.0),
            angle_increment=math.radians(1.0),
            range_min=0.05, range_max=70.0, buckets=4,
        )
        assert out[0] == 100
        assert out[1] is None and out[2] is None and out[3] is None

    def test_a_scan_spanning_more_than_a_turn_still_lands_everywhere(self):
        # Wrapping, not clamping: a driver reporting 400 degrees of samples must
        # not pile the overflow into the last bucket.
        n = 400
        out = scan_ring.decimate(
            _uniform(n, 2.0), 0.0, math.radians(1.0), 0.05, 70.0, buckets=8
        )
        assert all(v == 200 for v in out), out

    def test_quarter_turn_scan_leaves_the_rest_empty(self):
        out = scan_ring.decimate(
            _uniform(90, 1.5), 0.0, math.radians(1.0), 0.05, 70.0, buckets=4
        )
        assert out[0] == 150
        assert out[1] is None and out[2] is None and out[3] is None


class TestDegenerateInput:
    """None of these may raise: this runs in a publisher callback."""

    def test_empty_ranges(self):
        assert scan_ring.decimate([], 0.0, 0.01, 0.05, 70.0, buckets=6) == [
            None
        ] * 6

    def test_zero_increment_does_not_divide_by_zero(self):
        assert scan_ring.decimate([1.0], 0.0, 0.0, 0.05, 70.0, buckets=4) == [
            None
        ] * 4

    def test_zero_buckets_is_empty_not_a_crash(self):
        assert scan_ring.decimate([1.0], 0.0, 0.01, 0.05, 70.0, buckets=0) == []

    def test_non_finite_geometry_is_survived(self):
        out = scan_ring.decimate(
            [1.0], 0.0, float("nan"), 0.05, 70.0, buckets=4
        )
        assert out == [None] * 4


class TestEncode:
    def test_shape_and_units(self):
        payload = scan_ring.encode(
            [1.0, 2.0], angle_min=math.radians(-180.0),
            angle_increment=math.radians(180.0),
            range_min=0.05, range_max=70.0,
            frame_id="base_footprint", stamp_s=12.5, buckets=4,
        )
        assert payload["v"] == scan_ring.SCHEMA_VERSION
        assert payload["frame"] == "base_footprint"
        assert payload["stamp_s"] == 12.5
        assert payload["a0_deg"] == -180.0
        assert payload["step_deg"] == 90.0
        assert payload["max_cm"] == 1200  # clipped to max_m, not range_max
        assert len(payload["r_cm"]) == 4

    def test_frame_travels_with_the_data(self):
        # A scan in livox_frame drawn as base_footprint rotates the world around
        # the operator with nothing looking wrong. The consumer must be able to
        # tell.
        payload = scan_ring.encode(
            [1.0], 0.0, 0.1, 0.05, 70.0, frame_id="livox_frame", stamp_s=0.0
        )
        assert payload["frame"] == "livox_frame"

    def test_a_missing_frame_is_empty_string_not_absent(self):
        payload = scan_ring.encode([1.0], 0.0, 0.1, 0.05, 70.0, "", 0.0)
        assert payload["frame"] == ""

    def test_payload_stays_small(self):
        import json

        # 120 bearings of plausible indoor returns. The costmap PNG this sits
        # beside is ~540 bytes at 1 Hz; this must be the same order or it is not
        # shippable through the tunnel at 4 Hz.
        ranges = [1.0 + (i % 700) / 100.0 for i in range(1800)]
        payload = scan_ring.encode(
            ranges, math.radians(-180.0), math.radians(0.2),
            0.05, 70.0, "base_footprint", 1.0,
        )
        wire = json.dumps(payload, separators=(",", ":"))
        assert len(wire) < 1200, f"{len(wire)} bytes is too fat for 4 Hz"
