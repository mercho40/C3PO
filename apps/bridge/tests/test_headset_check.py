"""The pure halves of the headset verification script.

The script needs a Quest on somebody's head. These functions do not, and they
are the ones that decide WHAT THE ANSWER MEANS — which is the part worth being
sure about before an operator with their eyes covered is answering questions
about a robot that can walk.

The rule under test throughout: a NO with no data behind it is BLOCKED, never
FAIL. "I cannot see the radar" was reported three times between 2026-08-21 and
2026-08-27 and only ONE of those was a rendering bug. Recording the other two
as rendering bugs is what sent people to read shader source while a port sat
unforwarded.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from headset_check import (  # noqa: E402
    BLOCKED,
    CHECKS,
    FAIL,
    PASS,
    PROBES,
    SKIPPED,
    Check,
    Outcome,
    report,
    run,
    summarize_camera,
    summarize_gate,
    summarize_scan,
    verdict_for,
)


def run_one(check_id: str, answers: list, probe_present: bool):
    """Drive `run` through exactly one check, with the probe answer forced.

    `probe_present` decides what the fake bridge returns: a live camera and a
    filled ring, or nothing at all. Everything else — which verdict the typed
    answer earns — is the code under test.
    """
    check = next(c for c in CHECKS if c.id == check_id)
    replies = list(answers)

    def fake_fetch(base, path, timeout=4.0):
        if not probe_present:
            return None, None
        if path == "/camera/status":
            return {"live": True, "source": "videohub", "frames": 7}, 200
        if path == "/telemetry/scan":
            return {"ranges_cm": [90] + [0] * 119, "frame": "base_footprint"}, 200
        return {"armed": True}, 200

    outcomes = run(
        "http://unused",
        lambda _prompt: replies.pop(0) if replies else "",
        lambda _line: None,
        checks=[check],
        fetcher=fake_fetch,
    )
    return outcomes[0]


class TestVerdictHonoursTheProbe:
    """The one rule this script exists for."""

    def test_no_with_data_present_is_a_real_failure(self):
        # The bridge sent it, the headset did not show it. This is the claim
        # worth acting on, and the only one that should read as FAIL.
        assert verdict_for("n", data_present=True) == FAIL

    def test_no_with_data_absent_is_blocked_not_failed(self):
        # Nothing was sent, so nothing could be drawn. Calling this a
        # rendering failure is the mistake that cost 2026-08-21 and
        # 2026-08-27 a day each.
        assert verdict_for("n", data_present=False) == BLOCKED

    def test_no_with_no_probe_at_all_is_a_failure(self):
        # A check with no data precondition is a pure observation — immersive
        # mode, the panel merging into one. There is nothing to be blocked ON,
        # so a NO means what it says.
        assert verdict_for("n", data_present=None) == FAIL

    def test_yes_is_a_pass_even_when_the_probe_disagreed(self):
        # If the operator can SEE it, the renderer is doing its job and the
        # probe is the thing that is wrong. Downgrading a working display
        # because a telemetry route was unhappy would teach people to distrust
        # this script, which is worse than a wrong probe.
        assert verdict_for("y", data_present=False) == PASS

    @pytest.mark.parametrize("answer", ["y", "Y", "yes", "si", "sí", " yes "])
    def test_the_ways_a_person_says_yes(self, answer):
        # Typed one-handed, in Spanish or English, by somebody wearing a
        # headset. All of them mean yes.
        assert verdict_for(answer, None) == PASS

    @pytest.mark.parametrize("answer", ["n", "N", "no", " NO "])
    def test_the_ways_a_person_says_no(self, answer):
        assert verdict_for(answer, True) == FAIL

    @pytest.mark.parametrize("answer", ["", "s", "skip", "  ", "what?"])
    def test_anything_unclear_is_skipped_never_guessed(self, answer):
        # An ambiguous answer must not become evidence. `works_real` means a
        # human watched it; a shrug recorded as a PASS is exactly the kind of
        # false verification `works_real` exists to prevent.
        assert verdict_for(answer, None) == SKIPPED


class TestCameraProbe:
    def test_a_live_feed_is_data_present(self):
        got = summarize_camera({"live": True, "source": "videohub", "frames": 42}, 200)
        assert got.data_present is True
        assert "videohub" in got.detail

    def test_a_dark_feed_carries_the_bridge_s_own_hint(self):
        # The route reports BOTH sources and the ordinary cause, so the
        # sentence that ends the search is already written — pass it through
        # rather than reducing it to False.
        got = summarize_camera(
            {"live": False, "source": "vision", "hint": "somebody ran take_camera"},
            200,
        )
        assert got.data_present is False
        assert "take_camera" in got.detail

    def test_no_answer_at_all_blames_the_tunnel_not_the_headset(self):
        # This is the 2026-08-27 case exactly: the bridge was fine, the port
        # simply never reached the headset. The operator must not go looking
        # at the renderer.
        got = summarize_camera(None, None)
        assert got.data_present is False
        assert "tunnel" in got.detail or "bridge" in got.detail


class TestScanProbe:
    def test_a_filled_ring_is_data_present(self):
        got = summarize_scan(
            {"ranges_cm": [0, 120, 0, 80] + [0] * 116, "frame": "base_footprint"},
            200,
        )
        assert got.data_present is True
        assert "2/120" in got.detail
        assert "base_footprint" in got.detail

    def test_a_503_reports_the_reason_the_bridge_gave(self):
        # The route puts the cause in the body precisely so nobody has to
        # guess between "nav container down", "lidar unplugged" and "wrong
        # DDS domain". Printing it is the most useful thing this can do.
        got = summarize_scan({"error": "no scan received", "hint": "check ros2 topic hz /scan"}, 503)
        assert got.data_present is False
        assert "ros2 topic hz" in got.detail

    def test_a_ring_of_all_zeroes_is_not_data(self):
        # 120 bearings and nothing in any of them. The dial is correctly
        # empty, so asking the operator whether they can see points would be
        # asking them to judge a renderer given nothing to render.
        got = summarize_scan({"ranges_cm": [0] * 120, "frame": "base_footprint"}, 200)
        assert got.data_present is False
        assert "EMPTY" in got.detail

    def test_a_stale_ring_still_counts_as_present(self):
        # THE IMPORTANT ONE. The headset is supposed to draw an old ring
        # dimmed rather than blank it, so stale is exactly the case where the
        # operator's eyes are the instrument. Blocking here would skip the
        # check that matters most.
        got = summarize_scan(
            {"ranges_cm": [90] + [0] * 119, "frame": "base_footprint", "stale": True},
            200,
        )
        assert got.data_present is True
        assert "STALE" in got.detail


class TestGateProbe:
    def test_a_gate_answer_is_data_present(self):
        got = summarize_gate({"armed": False, "reason": "zero_torque"}, 200)
        assert got.data_present is True
        assert "zero_torque" in got.detail

    def test_no_gate_means_the_banner_has_nothing_to_say(self):
        got = summarize_gate(None, None)
        assert got.data_present is False


class TestTheChecklistItself:
    """Structural rules. A checklist is only as good as its worst entry."""

    def test_every_check_says_what_a_no_would_mean(self):
        # A check whose failure has no stated consequence is a question not
        # worth asking somebody wearing a headset next to a humanoid.
        for check in CHECKS:
            assert check.if_no.strip(), f"{check.id} has no consequence"

    def test_every_check_names_what_it_closes_out(self):
        # The report is meant to be pasted into a commit that flips something
        # from unverified to verified. A check that closes nothing cannot do
        # that.
        for check in CHECKS:
            assert check.closes.strip(), f"{check.id} closes nothing"

    def test_ids_are_unique(self):
        ids = [c.id for c in CHECKS]
        assert len(ids) == len(set(ids))

    def test_every_declared_probe_exists(self):
        # A typo here would silently KeyError in front of somebody wearing a
        # headset, halfway through a supervised run.
        for check in CHECKS:
            if check.probe:
                assert check.probe in PROBES, f"{check.id} names an unknown probe"

    def test_the_data_dependent_checks_actually_have_probes(self):
        # These three are the ones whose "I cannot see it" has repeatedly
        # meant "it was never sent". If any of them loses its probe, this
        # script stops being different from a piece of paper.
        by_id = {c.id: c for c in CHECKS}
        assert by_id["camera_picture"].probe == "camera"
        assert by_id["radar_single"].probe == "scan"
        assert by_id["readiness"].probe == "gate"


class TestInvertedChecks:
    """The two placeholder checks ask what the headset does with NOTHING."""

    def test_they_are_marked_inverted(self):
        by_id = {c.id: c for c in CHECKS}
        assert by_id["no_signal_card"].only_when_absent is True
        assert by_id["radar_says_why"].only_when_absent is True

    def test_an_inverted_check_needs_a_probe_to_invert(self):
        # `only_when_absent` with no probe has nothing to key off and would
        # silently always run.
        for check in CHECKS:
            if check.only_when_absent:
                assert check.probe, f"{check.id} inverts nothing"

    def test_a_live_feed_skips_the_placeholder_check(self):
        out = run_one("no_signal_card", answers=["n"], probe_present=True)
        # Not applicable: there was no reason to draw a SIN IMAGEN card, so a
        # "no" here must not be collected as a rendering failure.
        assert out.verdict == SKIPPED
        assert "not applicable" in out.note

    def test_a_dead_feed_asks_it_and_a_no_is_a_real_failure(self):
        # This is the inversion. Absent data is the PRECONDITION being met —
        # the card is exactly what should be on screen — so failing to draw it
        # is a genuine bug, not something to file as blocked on missing data.
        out = run_one("no_signal_card", answers=["n", ""], probe_present=False)
        assert out.verdict == FAIL

    def test_a_dead_feed_and_a_visible_card_passes(self):
        out = run_one("no_signal_card", answers=["y"], probe_present=False)
        assert out.verdict == PASS

    def test_an_ordinary_check_still_blocks_on_absent_data(self):
        # The inversion must not have leaked into the normal path: "I cannot
        # see the camera" with nothing being sent is still BLOCKED.
        out = run_one("camera_picture", answers=["n", ""], probe_present=False)
        assert out.verdict == BLOCKED


class TestReport:
    def _outcome(self, verdict, **kw):
        check = Check(
            id=kw.get("id", "x"),
            title=kw.get("title", "A thing"),
            look_for="?",
            if_no=kw.get("if_no", "it would mean something"),
            closes=kw.get("closes", "some claim"),
        )
        return Outcome(check, verdict, kw.get("note", ""), kw.get("probe_detail", ""))

    def test_the_counts_line_comes_first(self):
        text = report([self._outcome(PASS), self._outcome(FAIL)])
        assert "1 pass, 1 fail" in text

    def test_failures_are_listed_with_their_consequence(self):
        text = report([self._outcome(FAIL, if_no="the stereo fix did not work")])
        assert "the stereo fix did not work" in text

    def test_blocked_items_show_the_probe_rather_than_blaming_the_headset(self):
        text = report([self._outcome(BLOCKED, probe_detail="bridge has no ring")])
        assert "BLOCKED" in text
        assert "bridge has no ring" in text
        # And it must not be filed under the heading that means "go fix the
        # renderer".
        blocked_section = text.split("BLOCKED —")[1]
        assert "did not show it" not in blocked_section.split("PASSED")[0]

    def test_passes_are_reported_as_claims_that_can_now_be_marked_verified(self):
        text = report([self._outcome(PASS, closes="feat(vr): lidar radar")])
        assert "safe to mark verified" in text
        assert "feat(vr): lidar radar" in text

    def test_the_transcript_lists_every_check_whatever_the_verdict(self):
        outs = [
            self._outcome(PASS, id="a"),
            self._outcome(FAIL, id="b"),
            self._outcome(BLOCKED, id="c"),
            self._outcome(SKIPPED, id="d"),
        ]
        text = report(outs)
        transcript = text.split("FULL TRANSCRIPT")[1]
        for wanted in ("a", "b", "c", "d"):
            assert wanted in transcript

    def test_an_empty_run_still_produces_a_readable_report(self):
        # Ctrl-C on the first prompt, or every check skipped. It must not
        # crash on the way to telling somebody nothing happened.
        text = report([])
        assert "0 pass, 0 fail" in text
