"""Makes the declared `preconditions` agree with the FSM they describe.

WHY THIS IS WORTH A TEST WHEN NOTHING READS `preconditions`

Nothing does — today. `skill_meta(preconditions=[...])` is declared on twenty
skills, ships in `_meta.c3po.preconditions` to every MCP client including
Claude Code, and is read by no code anywhere in this repo. It is documentation
that looks like a contract.

`g1_protocol.py` is explicit about why the matching transition tables are NOT
wired to a guard: the rules come from partly-unverified sources, and a
client-side gate encoding a wrong rule would refuse a transition the firmware
would have accepted, "turning a robot problem into a bridge problem, precisely
when we are trying to tell those apart". That reasoning is right and this test
does not challenge it — it does not make preconditions enforce anything.

What it does is make them TRUE. The same file says: "Documenting a safety check
that does not run is worse than having neither." A precondition naming a state
the robot can never report is exactly that, and it is invisible, because free
text in a metadata dict is checked by nothing. `test_mcp_catalogue.py` asserts
that `preconditions` EXISTS. Nothing asserted it means anything.

This is the same shape as the test that forces the telemetry routes to match
between the bridge and apps/back: two places have to agree, and when they
drift the symptom is silent.
"""

from __future__ import annotations

import os
import re

import pytest

os.environ.setdefault("SIM_MODE", "stub")


@pytest.fixture(scope="module")
def tools() -> dict:
    import asyncio

    from bridge.mcp_server import mcp

    listed = asyncio.run(mcp.list_tools())
    return {t.name: t for t in listed}


def preconditions_of(tool) -> list:
    return list(tool.meta["c3po"]["preconditions"])


#: `fsm_state_is_X` and `fsm_state_in_{A,B,C}` — the only two shapes in use.
_IS = re.compile(r"^fsm_state_is_([a-z_0-9]+)$")
_IN = re.compile(r"^fsm_state_in_\{([a-z_0-9,]+)\}$")


def fsm_states_named(precondition: str) -> list:
    """The FSM state labels a precondition string refers to, if any.

    Returns [] for the non-FSM preconditions — `robot_upright`,
    `battery_pct_gt_15`, `operator_present`, `real_hardware_only`,
    `pose_available`, `no_active_walk_task`. Those describe things that are not
    FSM states and are out of scope here.
    """
    m = _IS.match(precondition)
    if m:
        return [m.group(1)]
    m = _IN.match(precondition)
    if m:
        return [s for s in m.group(1).split(",") if s]
    return []


def posture_label_for(skill: str):
    """The FSM label the robot will report AFTER this posture skill succeeds.

    None for anything that is not a full-body posture request: arm gestures go
    to a different api_id entirely, and `balance_stand` is a balance-controller
    call (7102), not an FSM transition, so it has no resulting FSM label.
    """
    from bridge.sdk import g1_protocol

    req = g1_protocol.SKILL_REQUESTS.get(skill)
    if req is None or req.api_id != g1_protocol.API_ID_G1_STATE:
        return None
    return g1_protocol.MODE_LABEL.get(req.data)


class TestTheVocabularyIsReal:
    def test_every_named_fsm_state_is_one_the_robot_can_report(self, tools):
        """A precondition naming a label `MODE_LABEL` cannot produce is fiction.

        `get_state` derives `posture` from `mode_label(fsm_id)`, so these
        strings are the only ones any consumer will ever see. A typo here — or
        a name that was renamed on one side only — produces a precondition that
        can never be satisfied, and nothing would say so.
        """
        from bridge.sdk import g1_protocol

        reportable = set(g1_protocol.MODE_LABEL.values())
        for name, tool in tools.items():
            for precondition in preconditions_of(tool):
                for state in fsm_states_named(precondition):
                    assert state in reportable, (
                        f"{name}: precondition names FSM state {state!r}, which "
                        f"mode_label() can never produce. Reportable: "
                        f"{sorted(reportable)}"
                    )

    def test_the_precondition_shapes_are_the_two_we_parse(self, tools):
        """A third spelling would be silently ignored by everything, including
        this test. Catch it here rather than let it look checked."""
        known_non_fsm = {
            "robot_upright",
            "battery_pct_gt_15",
            "no_active_walk_task",
            "no_active_turn_task",
            "operator_present",
            "real_hardware_only",
            "pose_available",
        }
        for name, tool in tools.items():
            for precondition in preconditions_of(tool):
                if precondition in known_non_fsm:
                    continue
                assert fsm_states_named(precondition), (
                    f"{name}: precondition {precondition!r} is neither a known "
                    f"non-FSM condition nor a parseable fsm_state_* form. "
                    f"Anything that reads preconditions will skip it silently."
                )


class TestTheSafetyExitCoversEveryPostureWeCanReach:
    """`damp` is the way out. Its accepted-from set had three holes.

    Every posture skill puts the robot into a specific FSM state. `damp` is the
    documented safety transition out of all of them — the vendor rules in
    `g1_protocol.py` say "In Squat -> only Damp accepted", and being reachable
    from everywhere is the entire point of a safety state.

    So the set of states `damp` declares it is legal from should cover every
    state a posture skill can command. When this was first written it did not:
    `sit_g1` reaches `seating` and `lie_up` reaches `lie_up`, neither of which
    appeared, and the `squat` skill sends SQUAT_UP (706) — deliberately, see
    its docstring — which reports as `squat_up`, while the precondition said
    `squat` (mode 2, an index this skill never sends).
    """

    #: Postures whose exit to Damp NOBODY HAS TRIED, listed rather than assumed.
    #:
    #: The firmware's real accepted-from set for these is unknown. This project
    #: does not encode unverified transition rules as guards, and it should not
    #: encode them as documentation either — so they are named here, with the
    #: reason, instead of being quietly added to `damp`'s precondition list or
    #: quietly skipped by this test.
    #:
    #: Resolve by watching `fsm_id` during a supervised run: put the robot in
    #: one of these, call `damp`, and record what happens. That is one line in
    #: `docs/ROBOT-API.md` and one edit here.
    UNVERIFIED_EXITS = {
        "seating": "sit_g1 has never run on hardware (works_real=False)",
        "lie_up": "lie_up has never run on hardware (works_real=False)",
    }

    def test_damp_lists_every_posture_a_skill_can_command(self, tools):
        accepted: set = set()
        for precondition in preconditions_of(tools["damp"]):
            accepted.update(fsm_states_named(precondition))

        reachable = {}
        for name, tool in tools.items():
            if tool.meta["c3po"]["classification"] != "posture":
                continue
            label = posture_label_for(name)
            if label and label != "damp":
                reachable[label] = name

        missing = {
            label: skill
            for label, skill in reachable.items()
            if label not in accepted and label not in self.UNVERIFIED_EXITS
        }
        assert not missing, (
            "`damp` is the safety exit and does not list these postures it "
            f"must be reachable from: {missing}. Either add them to damp's "
            "preconditions, or record them in UNVERIFIED_EXITS with the reason "
            "nobody has tried."
        )

    def test_the_squat_skill_s_actual_state_is_the_one_declared(self, tools):
        """The mismatch that motivated this file.

        `squat` sends SQUAT_UP (706), not SQUAT (2) — its own docstring
        explains why, at length, citing the reference implementation. So the
        robot reports `squat_up` afterwards, and a precondition that says
        `squat` describes a state this skill never produces.
        """
        assert posture_label_for("squat") == "squat_up"
        accepted: set = set()
        for precondition in preconditions_of(tools["damp"]):
            accepted.update(fsm_states_named(precondition))
        assert "squat_up" in accepted, (
            "damp declares it is legal from `squat` (mode 2), but the squat "
            "skill sends SQUAT_UP (706) and the robot reports `squat_up`."
        )

    def test_unverified_exits_are_actually_unverified(self, tools):
        """Stops this list becoming a place to hide a real gap.

        Once a skill is marked `works_real`, somebody has watched it run — and
        at that point its exit to Damp is answerable, so it does not get to sit
        in the unverified list any more.
        """
        for label, reason in self.UNVERIFIED_EXITS.items():
            skill = next(
                (
                    name
                    for name, tool in tools.items()
                    if posture_label_for(name) == label
                ),
                None,
            )
            assert skill, f"UNVERIFIED_EXITS names {label!r}, which no skill reaches"
            works_real = tools[skill].meta["c3po"]["works"]["real"]
            assert not works_real, (
                f"{skill} is now marked works_real, so its exit to damp is "
                f"answerable — verify it and remove {label!r} from "
                f"UNVERIFIED_EXITS. Recorded reason was: {reason}"
            )


class TestArmGesturesAgreeWithTheProtocolRule:
    """`g1_protocol.py` states the rule; the catalogue repeats it 6 times."""

    LOCOMOTION_ACTIVE = {"walk", "walk_waist", "run"}

    def test_every_arm_gesture_requires_a_locomotion_state(self, tools):
        """The rule is "arm gestures require Walk / WalkWaist / Run".

        Written out per-skill in the catalogue, which means six chances to get
        it wrong and no check that they agree. `wave` and `point_at` are both
        works_real=False, so if one of them named a state the arms cannot be
        commanded from, nobody would find out until an operator was standing
        there.

        A SUBSET, not equality — and that distinction is a real finding rather
        than a loosened assertion. `hug` declares only {walk, walk_waist}, and
        its docstring says why: it is one of four gestures the firmware hides
        while in Run mode. Demanding all three would have forced a correct,
        deliberately narrower declaration to be made wrong. What must never
        happen is a gesture naming a state OUTSIDE this set, or naming none.
        """
        from bridge.sdk import g1_protocol

        seen = 0
        for name, tool in tools.items():
            req = g1_protocol.SKILL_REQUESTS.get(name)
            if req is None or req.api_id != g1_protocol.API_ID_G1_UPPER_LIMBS:
                continue
            seen += 1
            states: set = set()
            for precondition in preconditions_of(tool):
                states.update(fsm_states_named(precondition))
            assert states, f"{name} is an arm gesture and declares no FSM state"
            assert states <= self.LOCOMOTION_ACTIVE, (
                f"{name} is an arm gesture and may only require locomotion-active "
                f"states {sorted(self.LOCOMOTION_ACTIVE)}; it names "
                f"{sorted(states - self.LOCOMOTION_ACTIVE)}"
            )
        # If the api_id ever changes, the loop above would quietly check
        # nothing and pass. There are six arm gestures.
        assert seen >= 6, f"expected the arm gestures, matched {seen}"
