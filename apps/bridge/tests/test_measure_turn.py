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

from measure_turn import (  # noqa: E402
    Aborted,
    settle_yaw,
    settled,
    verdict,
    wrap_pi,
    yaw_of,
)


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
        assert "0 converged" in verdict([{"residual_deg": -9.0}], 3.0)


class TestItCallsToolsThatActuallyExist:
    """The script's tool names and argument names, against the server's own.

    Verified live once, against a stub bridge on 2026-08-25: `turn` declares
    delta_yaw_radians / timeout_s / tolerance_degrees, and the exact call this
    script makes returns status ok. This keeps that true.

    Worth pinning because of WHERE the failure would land. A renamed argument
    is an MCP error at the moment the operator has already confirmed, is
    standing next to the robot, and is watching it instead of the terminal —
    the one place in this project where a typo costs more than a rerun.
    """

    @staticmethod
    def _tool_calls():
        """(tool_name, {arg names}) for every `call(session, "x", {...})`."""
        import ast

        src = (
            Path(__file__).resolve().parents[1] / "scripts" / "measure_turn.py"
        ).read_text()
        out = []
        for node in ast.walk(ast.parse(src)):
            if not isinstance(node, ast.Call):
                continue
            fn = node.func
            if not (isinstance(fn, ast.Name) and fn.id == "call"):
                continue
            if len(node.args) < 3 or not isinstance(node.args[1], ast.Constant):
                continue
            name = node.args[1].value
            keys = set()
            if isinstance(node.args[2], ast.Dict):
                keys = {
                    k.value
                    for k in node.args[2].keys
                    if isinstance(k, ast.Constant)
                }
            out.append((name, keys))
        return out

    @staticmethod
    def _declared_params(tool: str):
        """The parameter names of an `async def <tool>(...)` in mcp_server."""
        import ast

        src = (
            Path(__file__).resolve().parents[1]
            / "src"
            / "bridge"
            / "mcp_server.py"
        ).read_text()
        for node in ast.walk(ast.parse(src)):
            if isinstance(node, (ast.AsyncFunctionDef, ast.FunctionDef)) and node.name == tool:
                a = node.args
                return {
                    arg.arg
                    for arg in list(a.args) + list(a.kwonlyargs)
                    if arg.arg not in ("ctx", "self")
                }
        return None

    def test_the_scan_found_the_calls(self):
        calls = self._tool_calls()
        assert calls, "no call(session, ...) found — the AST scan has drifted"
        assert any(name == "turn" for name, _ in calls)
        assert any(name == "get_state" for name, _ in calls)

    def test_every_tool_it_calls_exists_on_the_server(self):
        for name, _ in self._tool_calls():
            assert self._declared_params(name) is not None, (
                "measure_turn calls the tool {!r}, which mcp_server does not "
                "define. This fails at the moment the operator has already "
                "confirmed and is watching the robot.".format(name)
            )

    def test_every_argument_it_sends_is_one_the_tool_accepts(self):
        for name, keys in self._tool_calls():
            declared = self._declared_params(name)
            assert declared is not None
            unknown = sorted(keys - declared)
            assert not unknown, (
                "measure_turn sends {} to {!r}, which accepts {}.".format(
                    unknown, name, sorted(declared)
                )
            )


class TestSettling:
    """Measured on the robot 2026-08-26, and it changed the number.

    `turn` gave up at a 3.76 deg residual; the body then coasted ~3.7 deg after
    the commanding stopped and landed within 0.002 deg of the target. The old
    fixed 1.5 s sleep sampled mid-coast and recorded the skill as missing a
    target it had actually hit.
    """

    def test_a_settle_across_pi_is_not_read_as_a_full_rotation(self):
        # The same trap wrap_pi exists for. Unwrapped, this pair reads as
        # ~360 deg of movement and the settle would never converge — it would
        # burn the whole cap on a robot standing perfectly still.
        assert settled(math.radians(179.95), math.radians(-179.95)) is True

    def test_movement_is_not_mistaken_for_stillness(self):
        assert settled(0.0, math.radians(2.0)) is False

    @staticmethod
    def _run(readings):
        """Drive settle_yaw off a scripted list of yaw samples."""
        import asyncio

        seq = list(readings)

        async def read_yaw():
            return seq.pop(0) if len(seq) > 1 else seq[0]

        async def sleep(_seconds):
            return None

        return asyncio.run(settle_yaw(read_yaw, sleep))

    def test_it_waits_for_the_coast_to_finish(self):
        # Still moving for three samples, then stopped.
        yaw, waited = self._run(
            [math.radians(d) for d in (0.0, 2.0, 3.5, 4.0, 4.0, 4.0)]
        )
        assert math.degrees(yaw) == pytest.approx(4.0, abs=0.01)
        assert waited > 0

    def test_it_gives_up_rather_than_hanging_in_front_of_an_operator(self):
        """A robot that never stops must not block the measurement forever.

        Somebody is standing next to it holding an e-stop.
        """
        from measure_turn import SETTLE_MAX_S

        forever = [math.radians(i * 5.0) for i in range(200)]
        _yaw, waited = self._run(forever)
        assert waited >= SETTLE_MAX_S


class TestVerdictUsesTheSkillsOwnAnswer:
    """A small residual is not the same claim as a loop that converged."""

    def test_a_timeout_that_drifts_onto_target_is_not_convergence(self):
        # Exactly the 2026-08-26 run: residual well inside tolerance, and the
        # skill itself said reached=false because it ran out of time. Counting
        # it would flip works_real on the strength of momentum.
        out = verdict([{"residual_deg": 1.0, "reached": False}], 3.0)
        assert "flip it" not in out
        assert "reached=false" in out

    def test_it_says_what_to_change(self):
        out = verdict([{"residual_deg": 1.0, "reached": False}], 3.0)
        assert "--timeout-s" in out

    def test_clean_runs_still_license_the_flag(self):
        out = verdict(
            [{"residual_deg": 1.0, "reached": True}, {"residual_deg": -2.0, "reached": True}],
            3.0,
        )
        assert "flip it" in out

    def test_a_run_with_no_reached_field_is_judged_on_residual_alone(self):
        """Older records, and stub runs, carry no `reached`. Don't fail them."""
        out = verdict([{"residual_deg": 1.0}, {"residual_deg": 0.5}], 3.0)
        assert "flip it" in out
