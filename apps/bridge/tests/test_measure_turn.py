"""The pure halves of the `turn` measurement script.

The script itself needs a robot. These two functions do not, and both of them
decide what the measurement MEANS — which is the part worth getting right
before anyone stands next to a humanoid holding an e-stop.
"""

from __future__ import annotations

import math
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from measure_turn import Aborted, verdict, wrap_pi, yaw_of  # noqa: E402


class TestWrapPi:
    """The wrap is not cosmetic: without it a 10-degree turn reads as -350."""

    def test_a_turn_across_the_pi_boundary_is_the_short_way(self):
        # +175 turning 10 degrees left lands at -175. `after - before` is
        # -350: most of a full rotation, the wrong way. That looks exactly
        # like the sign bug that was already ruled out on 2026-08-20, and
        # would send someone chasing it a second time.
        before, after = math.radians(175), math.radians(-175)
        assert math.degrees(wrap_pi(after - before)) == pytest.approx(10.0, abs=1e-9)

    def test_it_works_the_other_way_round_too(self):
        before, after = math.radians(-175), math.radians(175)
        assert math.degrees(wrap_pi(after - before)) == pytest.approx(-10.0, abs=1e-9)

    def test_an_ordinary_turn_is_unchanged(self):
        assert math.degrees(wrap_pi(math.radians(40))) == pytest.approx(40.0)

    def test_half_a_turn_does_not_flip_sign_arbitrarily(self):
        assert abs(math.degrees(wrap_pi(math.radians(180)))) == pytest.approx(180.0)


class TestYawOf:
    """A missing pose must stop the measurement, never default to zero."""

    def test_a_good_pose_comes_through(self):
        assert yaw_of({"pose": {"yaw_radians_world": 1.25}}) == 1.25

    def test_no_pose_aborts(self):
        # `turn` cannot close its loop without a pose either, so measuring it
        # against a substituted zero would produce a number describing nothing.
        with pytest.raises(Aborted):
            yaw_of({"pose": None})
        with pytest.raises(Aborted):
            yaw_of({})

    def test_a_null_or_nonfinite_yaw_aborts(self):
        with pytest.raises(Aborted):
            yaw_of({"pose": {"yaw_radians_world": None}})
        with pytest.raises(Aborted):
            yaw_of({"pose": {"yaw_radians_world": float("nan")}})
        with pytest.raises(Aborted):
            yaw_of({"pose": {"yaw_radians_world": "0.0"}})


class TestVerdict:
    """What the numbers license, and — mostly — what they do not.

    `works_real` asserts one thing: a human watched this skill run. For `turn`
    the untested part is the CLOSED LOOP, so the property is not "it rotated",
    it is "it stopped where it said it would".
    """

    def test_nothing_measured_never_licenses_the_flag(self):
        assert "stays False" in verdict([], 3.0)

    def test_converging_every_time_licenses_the_flag(self):
        out = verdict([{"residual_deg": 1.0}, {"residual_deg": -2.0}], 3.0)
        assert "flip it" in out

    def test_one_good_run_is_not_enough(self):
        # The yaw sign was verified three times before anyone believed it.
        # A single convergence is one sample of a stopping condition.
        out = verdict([{"residual_deg": 1.0}], 3.0)
        assert "flip it" not in out

    def test_intermittent_convergence_is_the_case_the_flag_exists_for(self):
        out = verdict([{"residual_deg": 1.0}, {"residual_deg": 22.0}], 3.0)
        assert "STAYS FALSE" in out

    def test_never_converging_suggests_tuning_rather_than_a_broken_loop(self):
        out = verdict([{"residual_deg": 22.0}, {"residual_deg": 19.0}], 3.0)
        assert "stays False" in out
        assert "under-travel" in out

    def test_a_run_outside_tolerance_is_counted_by_magnitude_not_sign(self):
        # Overshoot and undershoot are both failures to stop where promised.
        assert "0 inside" in verdict([{"residual_deg": -9.0}], 3.0)
