"""skills/manipulation.py: plan everything before moving, and stop when it must."""

from __future__ import annotations

import asyncio
import math
import threading

import pytest

from bridge import kinematics, safety
from bridge.sdk import perception_link
from bridge.skills.task_runtime import get_registry
from bridge.skills import manipulation as m
from bridge.teleop import arm_sdk, hands
from bridge.teleop.retarget import ArmAngles

#: torso_link, in front of the right shoulder: a level grasp plans here with no table reported.
REACHABLE = (0.38, -0.15, 0.124)
#: Counter height: only a tipped-down grasp reaches it, so only over a reported table.
COUNTER, COUNTER_TABLE = (0.30, -0.15, 0.05), (0.20, -0.07)
#: The right arm hanging at the side, the hand just below desk height (URDF angles).
HANGING = [0.0, -0.15, 0.0, 1.5, 0.0, 0.0, 0.0]
#: The thermos on the desk, 2026-11-05: planned fine, then the hand hit the desk edge.
THERMOS = (0.35, -0.16, -0.03)
PICK_PHASES = [
    *("curl", "curling", "clear", "over", "pre_grasp", "open", "opening"),
    *("grasp", "close", "closing", "lift"),
]
#: The summary's own stamp (FakeLink); a detection's capture is this minus its age_s.
STAMP = 1_000.0


def to_optical(p):
    """Inverse of kinematics.camera_to_torso, so a test can place a detection anywhere."""
    xyz, rot = kinematics._camera_mount()
    d = [p[i] - xyz[i] for i in range(3)]
    link = [sum(rot[r][c] * d[r] for r in range(3)) for c in range(3)]  # Rᵀ d
    return [-link[1], -link[2], link[0]]


class FakeLink:
    def __init__(self, objects, age=0.1):
        self.objects, self.age = objects, age

    def latest_report(self):
        report = {"stamp_unix": STAMP, "detector_online": True, "objects": self.objects}
        return {**report, "objects_omitted": 0}, self.age


class FakeArm:
    """Instant slew; records every arm command."""

    def __init__(self):
        self.engaged = False
        self.weight = 1.0
        # Both arms hanging at the sides, as the robot stands before a pick.
        self._t = [HANGING[0], -HANGING[1], *HANGING[2:], *HANGING]
        self.commands: list[tuple[ArmAngles | None, ArmAngles | None]] = []
        self.release_requested = False
        self.on_command = None

    async def engage(self):
        self.engaged = True

    @property
    def target_angles(self):
        return tuple(self._t)

    @property
    def current_angles(self):
        return tuple(self._t)

    def command(self, left, right):
        self.commands.append((left, right))
        if right is not None:
            self._t[7:14] = list(right.as_tuple())
        if left is not None:
            self._t[0:7] = list(left.as_tuple())
        if self.on_command:
            self.on_command()

    def request_release(self):
        self.release_requested = True

    async def release(self):
        self.engaged = False

    def status(self):
        return {"failed": None}


class FakeHand:
    name = "fake"
    sides = ("right",)

    def __init__(self):
        self.shapes = []

    def send_shape(self, side, closures):
        self.shapes.append((side, closures))


@pytest.fixture
def real(monkeypatch):
    arm, hand = FakeArm(), FakeHand()
    monkeypatch.setattr(m, "SIM_MODE", "real")
    monkeypatch.setattr(m, "CLOSE_SETTLE_S", 0.0)
    monkeypatch.setattr(arm_sdk, "get_driver", lambda: arm)
    monkeypatch.setattr(hands, "get_driver", lambda: hand)
    # The robot measures the arm before planning; here the fake arm is that measurement.
    monkeypatch.setattr(
        m, "_measured_q", lambda side: list(arm._t[0:7] if side == "left" else arm._t[7:14])
    )
    monkeypatch.setattr(safety, "PERSON_RULE", True)
    monkeypatch.setenv(m.ALLOW_PICK_ENV, "1")  # past the gantry fence; its own tests drop it
    safety.reset()
    return arm, hand


def see(monkeypatch, *objects, age=0.1):
    monkeypatch.setattr(perception_link, "_link_singleton", FakeLink(list(objects), age))


def cup(p=REACHABLE, **kw):
    return {
        "label": "cup",
        "range_m": 0.3,
        "bearing_deg": 0,
        "confidence": 0.8,
        "cam_xyz": to_optical(p),
        **kw,
    }


def person(range_m):
    return {"label": "person", "range_m": range_m, "bearing_deg": 0.0, "confidence": 0.9}


# --- planning -----------------------------------------------------------------------------


def test_workspace_is_per_side():
    assert m.in_workspace("right", REACHABLE) is None
    assert "across" in m.in_workspace("right", (0.3, 0.2, 0.0))
    assert m.in_workspace("left", (0.3, 0.2, 0.0)) is None
    assert "x=" in m.in_workspace("right", (0.05, -0.2, 0.0))


def phases(steps):
    """Phase names with a line's sub-waypoints collapsed."""
    out = []
    for s in steps:
        if not out or out[-1] != s[1]:
            out.append(s[1])
    return out


def last_q(steps, phase):
    return [s[2] for s in steps if s[1] == phase][-1]


def test_a_pick_plan_curls_first_opens_at_pre_grasp_and_lifts_last():
    steps = m.plan_pick("right", REACHABLE, "power", None)
    assert phases(steps) == PICK_PHASES
    grasp = kinematics.forward("right", last_q(steps, "grasp")).position
    lift = kinematics.forward("right", last_q(steps, "lift")).position
    assert lift[2] - grasp[2] == pytest.approx(m.LIFT_M, abs=0.02)
    assert math.dist(grasp, REACHABLE) < 0.04  # within the depth offset + IK tolerance


def slewed_tcp(q_from, q_to):
    """Hand positions while arm_sdk slews q_from -> q_to, each joint on its own (its _slew)."""
    step = arm_sdk.MAX_JOINT_RATE_RAD_S * arm_sdk.CONTROL_PERIOD_S
    cur, out = list(q_from), []
    while any(abs(t - c) > 1e-9 for t, c in zip(q_to, cur)):
        cur = [c + max(-step, min(step, t - c)) for c, t in zip(cur, q_to)]
        out.append(kinematics.forward("right", cur).position)
    return out


def off_line(p, a, b):
    ab = [b[i] - a[i] for i in range(3)]
    t = max(0.0, min(1.0, sum((p[i] - a[i]) * ab[i] for i in range(3)) / sum(v * v for v in ab)))
    return math.dist(p, [a[i] + ab[i] * t for i in range(3)])


def max_deviation(steps, line_phase, from_phase):
    qs = [last_q(steps, from_phase)] + [s[2] for s in steps if s[1] == line_phase]
    a = kinematics.forward("right", qs[0]).position
    b = kinematics.forward("right", qs[-1]).position
    return max(off_line(p, a, b) for q0, q1 in zip(qs, qs[1:]) for p in slewed_tcp(q0, q1))


@pytest.mark.parametrize(
    ("target", "table"),
    [
        (REACHABLE, None),
        ((0.36, -0.225, 0.124), None),
        ((0.34, -0.10, 0.20), None),
        (COUNTER, COUNTER_TABLE),  # tipped down
    ],
)
def test_approach_and_lift_follow_a_straight_line(target, table):
    # Audit §4: one joint-space move pre-grasp -> grasp swept the hand up to 24 cm sideways.
    steps = m.plan_pick("right", target, "power", None, table)
    assert max_deviation(steps, "grasp", "pre_grasp") < 0.01
    assert max_deviation(steps, "lift", "grasp") < 0.01


def test_place_lowers_and_retreats_in_straight_lines():
    steps = m.plan_place("right", COUNTER, None)
    assert phases(steps) == ["above", "down", "open", "opening", "retreat", "withdraw"]
    assert max_deviation(steps, "down", "above") < 0.01
    assert max_deviation(steps, "retreat", "down") < 0.01
    assert max_deviation(steps, "withdraw", "retreat") < 0.01


def arm_path(steps, q0, until):
    """Every hand position arm_sdk slews through from q0 until the last `until` waypoint."""
    qs = [q0] + [s[2] for s in steps if s[0] == "arm"]
    last = max(i for i, s in enumerate([s for s in steps if s[0] == "arm"]) if s[1] == until)
    return [p for a, b in zip(qs, qs[1 : last + 2]) for p in slewed_tcp(a, b)]


@pytest.mark.parametrize(
    ("target", "table"),
    [(THERMOS, (0.25, -0.13)), (REACHABLE, None), ((0.36, -0.25, 0.0), (0.25, -0.12))],
)
def test_the_hand_never_sweeps_through_the_desk_on_the_way_to_pre_grasp(target, table):
    # Robot, 2026-11-05: one slew hanging -> pre-grasp swung the hand into the desk edge.
    # In front of the body the hand stays above the surface + margin; the only way down
    # is straight down onto pre-grasp, over the object's footprint.
    # The tuck is the closest TUCK_X_M point the arm clears the torso from: the desk incident's
    # thermos still rises at the first. Tipped down (the desk ones) only over a reported table.
    steps = m.plan_pick("right", target, "power", HANGING, table)
    pre = kinematics.forward("right", last_q(steps, "pre_grasp")).position
    edge = kinematics.forward("right", last_q(steps, "clear")).position[0]
    clear_z = target[2] + m.SURFACE_MARGIN_M
    tol = kinematics.POS_TOL_M
    assert min(abs(edge - x) for x in m.TUCK_X_M) < tol
    if target == THERMOS:
        assert edge < m.TUCK_X_M[0] + tol
    for x, y, z in arm_path(steps, HANGING, "pre_grasp"):
        if x > edge + tol:
            descending = math.hypot(x - pre[0], y - pre[1]) < 0.02 and z >= pre[2] - tol
            assert z >= clear_z - tol or descending, (x, y, z)


def test_the_place_retreat_lifts_clear_before_pulling_back():
    steps = m.plan_place("right", COUNTER, None)
    retreat = [kinematics.forward("right", s[2]).position for s in steps if s[1] == "retreat"]
    withdraw = [kinematics.forward("right", s[2]).position for s in steps if s[1] == "withdraw"]
    assert retreat[-1][2] >= COUNTER[2] + m.SURFACE_MARGIN_M - kinematics.POS_TOL_M
    assert all(p[2] >= retreat[-1][2] - kinematics.POS_TOL_M for p in withdraw)
    assert withdraw[-1][0] <= m.TUCK_X_M[-1] + kinematics.POS_TOL_M  # back toward the body


def test_a_hand_already_low_in_front_is_refused_not_swept_back():
    low_front = kinematics.solve("right", (0.30, -0.15, -0.15), seed=HANGING).q
    with pytest.raises(m.PlanError, match="release_arm_control"):
        m.plan_pick("right", THERMOS, "power", list(low_front))


@pytest.mark.asyncio
async def test_a_refusal_caused_by_the_start_pose_never_says_walk(monkeypatch, real):
    # Robot 2026-11-13: refused at the arm's own pose, told to walk 0.14 m; walked, same refusal.
    arm, _hand = real
    low_front = kinematics.solve("right", (0.30, -0.15, -0.15), seed=HANGING).q
    arm._t[7:14] = list(low_front)
    steps = stepper(monkeypatch)
    see(monkeypatch, cup(p=(0.40, -0.20, 0.124)))  # 5 cm off the level zone: a step if it helped
    out = await m.pick("cup", side="right")
    assert out["phase"] == "planning" and not arm.engaged and steps == []
    res = out["result"]
    assert res["advice"]["problem"] == "start_pose", res
    assert not any(k.startswith("step_") for k in res["advice"])
    assert "release_arm_control" in out["error"] and "soltalo" in res["note"]
    assert res["start_q_deg"] == [round(math.degrees(v), 1) for v in low_front]


def test_a_path_through_the_torso_is_refused():
    # Right arm placing just across the midline, close in: withdrawing drives the forearm into
    # the chest. The workspace box allows it.
    with pytest.raises(m.PlanError, match=r"\d+/\d+: the \w+ would pass through the torso"):
        m.plan_place("right", (0.14, 0.05, 0.05), None)


def test_the_hanging_arms_clear_the_torso():
    m._check_torso("right", "hanging", [HANGING])
    m._check_torso("left", "hanging", [[HANGING[0], -HANGING[1], *HANGING[2:]]])


def test_an_upper_arm_folded_along_the_flank_is_refused():
    # The old tuck for (0.26, -0.30, -0.05): every joint point outside the box, but the upper
    # arm and forearm 23-44 mm into the torso's hull in the MuJoCo twin.
    folded = [math.radians(v) for v in (39, 19, 11, -12, -19, -23, -33)]
    with pytest.raises(m.PlanError, match=r"clear 1/1: the \w+ would pass through the torso"):
        m._check_torso("right", "clear", [folded])


@pytest.mark.parametrize("seed", [HANGING, None])
def test_the_tuck_holds_the_upper_arm_out_from_the_body(seed):
    tuck = last_q(m.plan_pick("right", REACHABLE, "power", seed), "clear")
    assert tuck[1] <= -min(m.SHOULDER_OUT_RAD) + 1e-9  # URDF right shoulder roll: - is out


def test_every_planned_pose_is_inside_the_urdf_limits():
    lim = kinematics.limits("right")
    for s in m.plan_pick("right", (0.36, -0.25, 0.0), "power", None, (0.25, -0.12)):
        if s[0] == "arm":
            assert all(lo <= v <= hi for v, (lo, hi) in zip(s[2], lim))


def test_a_pose_outside_the_joint_limits_is_a_plan_error(monkeypatch):
    # Pretend the solver returned an over-limit pose: the planner must refuse, not clamp.
    bad = kinematics.IKResult((0.0, 0.0, 0.0, 3.0, 0.0, 0.0, 0.0), True, 0.0, 0.0, 1)
    monkeypatch.setattr(kinematics, "solve", lambda *a, **k: bad)
    with pytest.raises(m.PlanError, match="outside its limits"):
        m.plan_pick("right", REACHABLE, "power", None)


def test_the_plan_summary_collapses_each_line_into_one_entry():
    plan = m._plan_dict(m.plan_pick("right", REACHABLE, "power", None))
    assert [e["phase"] for e in plan] == PICK_PHASES
    assert plan[PICK_PHASES.index("grasp")]["waypoints"] >= 10  # 10 cm approach at 1 cm


def test_an_unreachable_target_never_produces_a_plan():
    with pytest.raises(m.PlanError):
        m.plan_pick("right", (0.52, -0.2, -0.28), "power", None)


#: The right arm at rest with the shoulder rolled in (~1°): released, the stock controller hangs
#: it like this. The capsules are conservative, so its shoulder is already inside their margin.
RESTING_IN = [0.291, -0.02, 0.035, 0.984, -0.133, 0.04, 0.049]


def test_the_measured_start_pose_inside_the_torso_margin_does_not_refuse_the_pick():
    # Robot 2026-11-13: every approach refused at 'clear 1/N: the shoulder ... 0-1 mm'.
    with pytest.raises(m.PlanError, match="shoulder would pass through the torso"):
        m._check_torso("right", "rest", [RESTING_IN])
    m.plan_pick("right", REACHABLE, "power", RESTING_IN)
    # Leaving it, the arm may stay that far in but never go deeper.
    deeper = [RESTING_IN[0], RESTING_IN[1] + 0.05, *RESTING_IN[2:]]
    m._check_torso("right", "clear", [RESTING_IN, RESTING_IN], start=RESTING_IN)
    with pytest.raises(m.PlanError, match="clear 2/2: the shoulder"):
        m._check_torso("right", "clear", [RESTING_IN, deeper], start=RESTING_IN)
    # Once out, back in is refused as before.
    out = [RESTING_IN[0], -0.3, *RESTING_IN[2:]]
    with pytest.raises(m.PlanError, match="clear 2/2"):
        m._check_torso("right", "clear", [out, RESTING_IN], start=RESTING_IN)


def thumb(side, q):
    """kinematics.THUMB_AXIS (hand frame) in torso_link at pose q."""
    hp = kinematics.forward(side, q)
    x, z = hp.pointing, hp.joint_axes[6]
    y = kinematics._cross(z, x)
    return [sum(a[k] * w for a, w in zip((x, y, z), kinematics.THUMB_AXIS)) for k in range(3)]


@pytest.mark.parametrize("side", ["right", "left"])
def test_a_level_grasp_holds_the_thumb_up_and_the_palm_toward_the_object_side(side):
    # Robot 2026-11-13: roll was free, the hand reached the bottle rolled palm-down.
    p = REACHABLE if side == "right" else (REACHABLE[0], -REACHABLE[1], REACHABLE[2])
    steps = m.plan_pick(side, p, "power", None)
    for _kind, phase, q in steps:
        if phase in ("pre_grasp", "grasp", "lift"):
            assert thumb(side, q)[2] >= math.cos(kinematics.DIR_TOL_RAD), (phase, thumb(side, q))


def test_from_above_the_thumb_points_level_and_inward():
    steps = m.plan_pick("right", REACHABLE, "power", HANGING, (0.20, -0.07))  # only 60° plans
    assert m.approach_tilt_deg("right", steps) > 45
    t = thumb("right", last_q(steps, "grasp"))
    assert abs(t[2]) < 0.05 and t[1] > 0.95  # +y: toward the body's midline for the right arm


def test_without_a_reported_table_only_a_level_grasp_is_planned():
    # Robot 2026-11-13: table None, 60° down, the open fingertips 13 cm under the target, unchecked.
    with pytest.raises(m.PlanError, match="tilt 0°") as err:
        m.plan_pick("right", COUNTER, "power", HANGING)
    assert "tilt 30°" not in str(err.value) and "tilt 60°" not in str(err.value)
    tipped = m.plan_pick("right", COUNTER, "power", HANGING, COUNTER_TABLE)
    assert m.approach_tilt_deg("right", tipped) > 20


def test_the_detection_round_trips_through_the_camera_mount(monkeypatch):
    see(monkeypatch, cup())
    p, obj = m.detected_target("taza", None)  # Spanish query, English label
    assert obj["label"] == "cup"
    assert p == pytest.approx(REACHABLE, abs=1e-6)


def test_a_different_label_is_never_a_match_and_the_miss_lists_what_is_graspable(monkeypatch):
    # Robot: "Dixie cup" resolved the detection "cup" (track 4300), a different object.
    see(monkeypatch, cup(track_id=4300, range_m=0.42, bearing_deg=-12.0))
    with pytest.raises(m.PlanError) as err:
        m.detected_target("Dixie cup", None)
    assert "'cup' track_id=4300 0.42 m -12°" in str(err.value)
    with pytest.raises(m.PlanError, match="'cup' track_id=4300"):
        m.detected_target("botella de agua", None)


def test_the_exact_label_beats_a_translation_and_case_is_ignored(monkeypatch):
    see(
        monkeypatch,
        cup(track_id=1, confidence=0.9),
        cup(label="Dixie Cup", track_id=2, confidence=0.5),
    )
    assert m.detected_target("dixie cup", None)[1]["track_id"] == 2
    assert m.detected_target("cups", None)[1]["track_id"] == 1


def test_a_track_id_wins_over_the_label(monkeypatch):
    see(monkeypatch, cup(track_id=7), cup(label="bottle", track_id=8, confidence=0.99))
    assert m.detected_target("bottle", 7)[1]["label"] == "cup"
    with pytest.raises(m.PlanError, match="track 9 .*'bottle' track_id=8"):
        m.detected_target("cup", 9)


def test_stale_or_depthless_detections_are_not_grasp_targets(monkeypatch):
    see(monkeypatch, cup(), age=2.0)
    with pytest.raises(m.PlanError, match="fresh"):
        m.detected_target("cup", None)
    no_point = cup()
    del no_point["cam_xyz"]
    see(monkeypatch, no_point)
    with pytest.raises(m.PlanError, match="3-D point"):
        m.detected_target("cup", None)


def test_a_fresh_report_repeating_an_old_capture_is_not_a_grasp_target(monkeypatch):
    # The summary re-emits the last frame at 4 Hz: report age 0.1 s, capture 1.5 s old.
    see(monkeypatch, cup(age_s=1.5))
    with pytest.raises(m.PlanError):
        m.detected_target("cup", None)
    see(monkeypatch, cup(age_s=0.2))
    assert m.detected_target("cup", None)[1]["age_s"] == 0.2


# --- execution ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stub_mode_plans_and_moves_nothing(monkeypatch):
    see(monkeypatch, cup())
    monkeypatch.setattr(m, "SIM_MODE", "stub")
    arm = FakeArm()
    monkeypatch.setattr(arm_sdk, "get_driver", lambda: arm)
    out = await m.pick("cup")
    assert out["status"] == "completed" and out["phase"] == "stub"
    assert len(out["result"]["plan"]) == len(PICK_PHASES)
    assert not arm.engaged and arm.commands == []


@pytest.mark.asyncio
async def test_pick_runs_the_plan_in_order_and_holds(monkeypatch, real):
    arm, hand = real
    see(monkeypatch, cup())
    out = await m.pick("cup", shape="pinch")
    assert out["status"] == "completed", out
    assert out["phase"] == "holding" and out["result"]["hand_closed"] is True
    shapes = [s for _, s in hand.shapes]
    assert shapes == [hands.SHAPES[m.TRANSIT_SHAPE], hands.SHAPES["open"], hands.SHAPES["pinch"]]
    assert all(left is None for left, _ in arm.commands)  # only the right arm moved
    assert not arm.release_requested


@pytest.mark.asyncio
async def test_a_person_stepping_into_reach_aborts_mid_motion(monkeypatch, real):
    arm, _hand = real
    see(monkeypatch, cup())
    arm.on_command = lambda: see(monkeypatch, cup(), person(0.4))
    out = await m.pick("cup")
    assert out["status"] == "failed"
    assert out["phase"] == "aborted:person_in_reach"
    assert arm.release_requested
    assert len(arm.commands) == 1  # stopped at the first waypoint


@pytest.mark.asyncio
async def test_perception_going_stale_aborts_mid_motion(monkeypatch, real):
    arm, _hand = real
    see(monkeypatch, cup())
    arm.on_command = lambda: see(monkeypatch, cup(), age=5.0)
    out = await m.pick("cup")
    assert out["phase"] == "aborted:perception_stale"
    assert arm.release_requested and len(arm.commands) == 1


@pytest.mark.asyncio
async def test_a_person_arriving_during_the_grasp_close_aborts(monkeypatch, real):
    arm, hand = real
    see(monkeypatch, cup())
    real_send = hand.send_shape

    def send_then_person(side, closures):
        real_send(side, closures)
        if len(hand.shapes) == 3:  # the close, after the curl and the open
            see(monkeypatch, cup(), person(0.4))

    hand.send_shape = send_then_person
    out = await m.pick("cup")
    assert out["phase"] == "aborted:person_in_reach"
    assert arm.release_requested and out["result"]["hand_closed"] is True


@pytest.mark.asyncio
async def test_handover_is_allowed_with_a_person_close(monkeypatch, real):
    arm, _hand = real
    see(monkeypatch, person(0.4))
    out = await m.handover("right")
    assert out["status"] == "completed", out
    assert not arm.release_requested


@pytest.mark.asyncio
async def test_nothing_moves_while_the_base_walks(monkeypatch, real):
    arm, _hand = real
    see(monkeypatch, cup())
    monkeypatch.setattr(safety, "base_moving", lambda: True)
    out = await m.pick("cup")
    assert out["phase"] == "refused:base_moving"
    assert not arm.engaged


@pytest.mark.asyncio
async def test_a_simulated_hand_runs_the_pick_and_says_nothing_was_gripped(monkeypatch, real):
    _arm, hand = real
    hand.name = "simulated"
    see(monkeypatch, cup())
    out = await m.pick("cup")
    assert out["status"] == "completed", out
    assert "nothing was gripped" in out["result"]["hand"]


@pytest.mark.asyncio
async def test_a_real_hand_result_carries_no_simulation_note(monkeypatch, real):
    see(monkeypatch, cup())
    out = await m.pick("cup")
    assert "hand" not in out["result"]


@pytest.mark.asyncio
async def test_pick_refuses_when_that_hand_is_not_configured(monkeypatch, real):
    arm, _hand = real
    see(monkeypatch, cup())
    out = await m.pick("cup", side="left")  # FakeHand drives the right hand only
    assert out["phase"] in ("hand_unavailable", "planning")
    assert not arm.engaged


@pytest.mark.asyncio
async def test_an_out_of_reach_pick_says_how_to_walk(monkeypatch, real):
    # Robot: the centred thermos (y -0.01) failed every tilt; at y -0.16 it planned fine.
    arm, _hand = real
    monkeypatch.setattr(m, "MAX_REPOSITIONS", 0)  # the advice; pick's own stepping: below
    see(monkeypatch, cup(p=(0.41, -0.01, 0.124)))
    out = await m.pick("cup", side="right")
    assert out["phase"] == "planning" and not arm.engaged
    assert "0.05 m forward" in out["error"] and "0.14 m to the LEFT" in out["error"]
    # The same walk, for the agent to act on and the console to show.
    res = out["result"]
    assert res["reason"] in m.PLAN_REASONS and not res["moved"]
    assert {k: res["advice"][k] for k in ("step_forward_m", "step_left_m")} == {
        "step_forward_m": 0.05,
        "step_left_m": 0.14,
    }
    assert res["note"].startswith("Acercate ~0.05 m y correte ~0.14 m a la izquierda")
    m.plan_pick("right", (0.36, -0.15, 0.124), "power", HANGING)  # where that walk puts it


def test_a_height_no_walk_can_fix_says_so():
    with pytest.raises(m.PlanError):
        m.plan_pick("right", (0.30, -0.15, -0.28), "power", None)
    text, advice, hint = m.reach_advice("right", (0.30, -0.15, -0.28))
    assert "walking will not fix" in text and "too low" in text
    assert advice == {"problem": "too_low", "object_z_m": -0.28}
    assert "muy bajo" in hint and "-0.28" in hint


# --- the table perception reports under the object ------------------------------------------


def support(z_m=0.03, near_edge_m=0.25, stamp=STAMP - 0.2, **kw):
    """A detection's `support`: base_link (pelvis) height and range, like range_m."""
    return {"z_m": z_m, "near_edge_m": near_edge_m, "inliers": 900, "rms_m": 0.004,
            "stamp_unix": stamp, **kw}  # fmt: skip


def test_a_reported_support_becomes_the_table_in_torso_link(monkeypatch):
    # The edge 0.25 m out along the object's 20° bearing, the top 3 cm above the pelvis: through
    # the URDF's pelvis -> torso_link (waist at zero) like the camera point.
    see(monkeypatch, cup(bearing_deg=20.0, age_s=0.2, support=support()))
    p, obj = m.detected_target("cup", None)
    edge_x, top_z = m.support_table(obj, p)
    torso = kinematics.base_to_torso((0.0, 0.0, 0.0))
    assert torso == pytest.approx((0.0039635, 0.0, -0.044))
    assert (edge_x, top_z) == pytest.approx((0.25 * math.cos(math.radians(20)) + 0.0039635, -0.014))
    # No edge: the old desk-edge proxy. An edge beyond the object: no further than the object.
    assert m.support_table({"support": support(near_edge_m=None)}, p)[0] == m.TUCK_X_M[0]
    assert m.support_table({"support": support(near_edge_m=2.0)}, p)[0] == p[0]
    for bad in ({"z_m": float("nan")}, {"z_m": "high"}, {"near_edge_m": -1.0}, {"z_m": None}):
        assert m.support_table({"support": {**support(), **bad}}, p) is None


def test_the_supports_camera_points_put_the_table_in_the_objects_frame(monkeypatch):
    # The vision side's C3PO_CAMERA_* override moved z_m / near_edge_m (its base_link); the
    # cam_* points are measurements and go through camera_to_torso like the cup's cam_xyz.
    top, near = (0.32, -0.20, -0.02), (0.22, -0.18, 0.01)  # torso_link: the near end higher
    see(monkeypatch, cup(age_s=0.2, support=support(
        z_m=0.30, near_edge_m=0.60, cam_top_xyz=to_optical(top), cam_near_xyz=to_optical(near))))
    p, obj = m.detected_target("cup", None)
    assert p == pytest.approx(REACHABLE)
    assert m.support_table(obj, p) == pytest.approx((0.22, 0.01))
    # Malformed points: the base_link fields, as from a perception without them.
    for bad in ({"cam_near_xyz": None}, {"cam_top_xyz": [0.1, 0.2]}, {"cam_top_xyz": "up"}):
        s = {**support(), "cam_top_xyz": to_optical(top), "cam_near_xyz": to_optical(near), **bad}
        assert m.support_table({"support": s}, p) == m.support_table({"support": support()}, p)


def test_a_support_from_another_frame_is_dropped(monkeypatch):
    # Same capture = the support's stamp is the summary's stamp minus the object's age_s.
    see(monkeypatch, cup(age_s=0.2, support=support(stamp=STAMP - 0.7)))
    _p, obj = m.detected_target("cup", None)
    assert "support" not in obj and m.support_table(obj, REACHABLE) is None
    see(monkeypatch, cup(age_s=0.2, support=support()))
    assert "support" in m.detected_target("cup", None)[1]


@pytest.mark.asyncio
async def test_the_pick_says_why_it_has_no_table(monkeypatch):
    # Robot 2026-11-13: pick.requested logged table_torso_m=None and nothing more.
    for obj, why in (
        (cup(age_s=0.2, support=support(stamp=STAMP - 0.7)), "other_capture"),
        (cup(), "absent"),
        (cup(age_s=0.2, support=support(z_m=float("nan"))), "unusable"),
        (cup(age_s=0.2, support=support(z_m=-0.10 + 0.044)), "used"),
    ):
        see(monkeypatch, obj)
        out = await m.pick("cup", side="right")
        assert out["result"]["support"] == why, out


@pytest.mark.asyncio
async def test_pick_plans_against_the_reported_table(monkeypatch):
    see(monkeypatch, cup(p=COUNTER, age_s=0.2, support=support(z_m=-0.10 + 0.044)))  # top -0.10
    out = await m.pick("cup", side="right")
    assert out["phase"] == "stub", out
    assert out["result"]["table_torso_m"] == pytest.approx([0.254, -0.10], abs=1e-3)
    # The object only 2 cm above the table top: the hand would hit it, so nothing plans.
    see(monkeypatch, cup(p=COUNTER, age_s=0.2, support=support(z_m=0.03 + 0.044)))
    out = await m.pick("cup", side="right")
    assert out["phase"] == "planning" and out["result"]["reason"] == "table_clearance"
    assert out["result"]["advice"]["part"] == "hand" and "tocaría la mesa" in out["result"]["note"]


def test_a_lower_reported_surface_never_lowers_the_hand():
    # The object's own z (+ margin) stays the floor of the over-the-surface path.
    over = last_q(m.plan_pick("right", COUNTER, "power", HANGING, (0.20, -0.10)), "over")
    assert kinematics.forward("right", over).position[2] == pytest.approx(
        COUNTER[2] + m.SURFACE_MARGIN_M, abs=kinematics.POS_TOL_M
    )


def test_every_arm_capsule_keeps_clear_of_the_table_box():
    table = COUNTER_TABLE  # the object's centre 12 cm over it: room for the open hand
    for kind, _phase, q in m.plan_pick("right", COUNTER, "power", HANGING, table):
        if kind == "arm":
            m._check_torso("right", "replay", [q], table)  # raises if any capsule is inside
    with pytest.raises(m.PlanError, match="would touch the table") as err:
        m.plan_pick("right", COUNTER, "power", HANGING, (0.20, 0.03))
    assert err.value.reason == "table_clearance" and err.value.part == "hand"


#: The object 7 cm over the top: room for the open hand at the object, not on the way there.
LOW_ON_TABLE, LOW_TABLE = (0.31, -0.16, 0.12), (0.24, 0.05)


def test_a_transit_that_clears_only_with_the_hand_curled_plans():
    # The hand opened first: rising to the tuck, its open fingers met the table.
    steps = m.plan_pick("right", LOW_ON_TABLE, "power", HANGING, LOW_TABLE)
    transit = [q for kind, ph, q in steps if kind == "arm" and ph in ("clear", "over", "pre_grasp")]
    with pytest.raises(m.PlanError, match="would touch the table"):
        m._check_torso("right", "open hand", transit, LOW_TABLE)
    m._check_torso("right", "curled", transit, LOW_TABLE, hand_open=False)
    m._check_torso("right", "opening", transit[-1:], LOW_TABLE)  # opens clear of the table


def test_a_hand_that_would_open_into_the_table_at_pre_grasp_refuses():
    # Curled, the arm gets there; the fingers sweeping open at pre-grasp would hit the top.
    with pytest.raises(m.PlanError, match="open 1/1: the hand would touch the table") as err:
        m.plan_pick("right", (0.40, -0.15, -0.09), "power", HANGING, (0.21, -0.12))
    assert err.value.reason == "table_clearance" and err.value.part == "hand"


@pytest.mark.asyncio
async def test_the_hand_opens_at_pre_grasp_not_before(monkeypatch, real):
    arm, hand = real
    see(monkeypatch, cup())
    at = []  # (shape, arm commands so far, the arm's pose) as each shape is sent
    hand.send_shape = lambda side, c: at.append((c, len(arm.commands), arm._t[7:14]))
    out = await m.pick("cup")
    assert out["status"] == "completed", out
    (curl, n_curl, _), (opened, n_open, q_open), _close = at
    assert curl == hands.SHAPES[m.TRANSIT_SHAPE] and n_curl == 0  # before the arm moves
    pre = next(e for e in out["result"]["plan"] if e["phase"] == "pre_grasp")
    assert opened == hands.SHAPES["open"] and n_open > 0
    assert [math.degrees(v) for v in q_open] == pytest.approx(pre["q_deg"], abs=0.06)


@pytest.mark.asyncio
async def test_a_stop_before_pre_grasp_never_opens_the_hand(monkeypatch, real):
    arm, hand = real
    see(monkeypatch, cup())
    registry = get_registry()
    before = {t.task_id for t in registry.list_active()}

    def stop_at_the_third_waypoint():
        if len(arm.commands) == 3:
            for t in registry.list_active():
                if t.task_id not in before:
                    registry.cancel(t.task_id)

    arm.on_command = stop_at_the_third_waypoint
    out = await m.pick("cup")
    assert out["status"] == "cancelled" and out["result"]["failed_at"] == "clear", out
    sent = [s for _, s in hand.shapes]
    assert hands.SHAPES["open"] not in sent and sent == [hands.SHAPES[m.TRANSIT_SHAPE]]
    assert out["result"]["hand_closed"] is True and "open_hands" in out["result"]["note"]


def test_an_object_beyond_the_table_edge_says_walking_stops_at_the_edge():
    text, advice, hint = m.reach_advice("right", (0.60, -0.15, 0.05), (0.20, -0.07))
    assert advice == {"problem": "behind_table_edge", "behind_edge_m": 0.4}
    assert "0.40 m behind the table edge" in text and "detrás del borde" in hint


def test_a_robot_hugging_the_table_is_told_to_step_back():
    # The edge 20 cm from the torso: closer than walking's standoff, so back off to it.
    text, advice, hint = m.reach_advice("right", REACHABLE, (0.20, -0.07))
    assert advice == {"step_back_m": 0.05}
    assert "0.05 m back" in text and hint.startswith("Alejate ~0.05 m")


def test_walking_to_a_table_stops_with_its_edge_a_standoff_ahead():
    # The hips reach x ~0.10 at desk height: walking leaves the edge 0.25 m out, not 0.16.
    _text, advice, _hint = m.reach_advice("right", (0.60, -0.15, 0.05), (0.45, -0.07))
    assert advice == {"step_forward_m": round(0.45 - m.WALK_EDGE_STANDOFF_M, 2)}
    assert m.WALK_EDGE_STANDOFF_M == 0.25
    # 0.26 m behind an edge 0.36 m out: walking up to the standoff leaves it out of reach.
    _text, advice, _hint = m.reach_advice("right", (0.62, -0.15, 0.05), (0.36, -0.07))
    assert advice == {"problem": "behind_table_edge", "behind_edge_m": 0.26}


def test_open_fingertips_never_go_below_the_table_top():
    # Review sweep scene 253: tilts 0 and 30 met the table, the planner fell through to 60
    # nose-down with the TCP 4.6 cm over the top and open fingertips 2.3 cm under it.
    target, (edge, top) = (0.374, -0.257, 0.087), (0.197, 0.024)
    try:
        steps = m.plan_pick("right", target, "power", HANGING, (edge, top))
    except m.PlanError as exc:  # refusing is fine (the roll-held hand meets the torso first)
        assert exc.reason in ("table_clearance", "torso_clearance")
        return
    for kind, phase, q in steps:
        if kind == "arm":
            hp = kinematics.forward("right", q)
            tip = [p + m.FINGER_M * d for p, d in zip(hp.position, hp.pointing, strict=True)]
            assert tip[0] < edge or tip[2] > top, (phase, tip)


def test_a_place_withdraws_the_palm_behind_a_near_table_edge_or_refuses():
    # Releasing lowers the arm from where the withdraw ends: never with the palm over the top.
    target = (0.30, -0.25, 0.05)
    steps = m.plan_place("right", target, HANGING, (0.20, -0.05))
    x = kinematics.forward("right", last_q(steps, "withdraw")).position[0]
    assert x <= 0.20 - m.HAND_OPEN_RADIUS_M - m.TABLE_CLEARANCE_M + kinematics.POS_TOL_M
    with pytest.raises(m.PlanError, match="behind the table edge") as err:
        m.plan_place("right", target, HANGING, (0.12, -0.05))
    assert err.value.reason == "table_clearance" and err.value.part == "hand"


@pytest.mark.asyncio
async def test_auto_side_tries_the_other_arm_before_giving_up(monkeypatch, real):
    _arm, hand = real
    hand.sides = ("left", "right")
    # right's side (y <= 0), the left arm reaches.
    see(monkeypatch, cup(p=(0.36, 0.0, 0.124), label="remote"))
    plan = m.plan_pick

    def right_fails(side, *a):
        if side == "right":
            raise m.PlanError("right: out of reach")
        return plan(side, *a)

    monkeypatch.setattr(m, "plan_pick", right_fails)
    out = await m.pick("remote")
    assert out["status"] == "completed", out
    assert out["result"]["side"] == "left"


def test_brainco_shapes_respect_the_thumb_cap():
    sent = []

    class Pub:
        def Write(self, msg):  # noqa: N802 — SDK name
            sent.append([c.q for c in msg.cmds])

    d = hands.BrainCoHandDriver(open_at=0.0, sides=("right",))
    d._publishers["right"] = Pub()
    pytest.importorskip("unitree_sdk2py")
    d.send_shape("right", (1.0,) * 6)
    assert sent[-1][1] == pytest.approx(hands.BRAINCO_CLOSURE_CAPS[1])
    d.send("right", 0.5)
    assert sent[-1] == pytest.approx([0.5] * 6)


@pytest.mark.asyncio
async def test_a_pick_without_the_measured_arm_pose_refuses_on_the_robot(monkeypatch, real):
    # No start pose means no checked rise: the one-slew path that hit the desk.
    arm, _hand = real
    monkeypatch.setattr(m, "_measured_q", lambda side: None)
    see(monkeypatch, cup())
    out = await m.pick("cup", side="right")
    assert out["phase"] == "planning" and "measured arm pose" in out["error"]
    assert not arm.engaged and not arm.commands


# --- the gantry fence (no pick until the planner's torso clearance is checked on the robot) ---


@pytest.mark.asyncio
@pytest.mark.parametrize("skill", ["pick", "place"])
async def test_pick_and_place_refuse_on_the_robot_until_the_fence_is_lifted(
    monkeypatch, real, skill
):
    arm, _hand = real
    monkeypatch.delenv(m.ALLOW_PICK_ENV)
    planned = []
    monkeypatch.setattr(m, "plan_pick", lambda *a: planned.append(a))
    monkeypatch.setattr(m, "plan_place", lambda *a: planned.append(a))
    see(monkeypatch, cup())
    out = await (m.pick("cup") if skill == "pick" else m.place("right", label="cup"))
    assert out["status"] == "failed" and out["error"] == "pick_disabled_until_gantry_test"
    assert out["result"]["moved"] is False and m.ALLOW_PICK_ENV in out["result"]["note"]
    assert planned == [] and not arm.engaged and arm.commands == []


@pytest.mark.asyncio
async def test_the_lifted_fence_lets_pick_and_place_run(monkeypatch, real):
    arm, _hand = real
    see(monkeypatch, cup())
    assert (await m.pick("cup"))["status"] == "completed"
    picked = len(arm.commands)
    out = await m.place("right")  # set it down below the hand
    assert out["status"] == "completed", out
    assert len(arm.commands) > picked


@pytest.mark.asyncio
async def test_the_fence_leaves_stub_planning_alone(monkeypatch):
    monkeypatch.delenv(m.ALLOW_PICK_ENV, raising=False)
    see(monkeypatch, cup())
    out = await m.pick("cup")
    assert out["phase"] == "stub" and out["result"]["plan"]


# --- the headset wins (A4): no second rt/arm_sdk commander from this process ------------------


@pytest.mark.asyncio
@pytest.mark.parametrize("skill", ["pick", "reach", "handover"])
async def test_arm_skills_refuse_while_the_headset_drives(monkeypatch, real, skill):
    from bridge import estop

    arm, _hand = real
    see(monkeypatch, cup())
    estop.renew_teleop_lease()
    out = await {
        "pick": lambda: m.pick("cup"),
        "reach": lambda: m.reach("right", (0.30, -0.15, 0.10)),
        "handover": lambda: m.handover("right"),
    }[skill]()
    assert out["phase"] == "refused:teleop_active" and out["error"] == "teleop_active"
    assert not arm.engaged and arm.commands == []


@pytest.mark.asyncio
async def test_the_headset_taking_over_mid_pick_aborts_it(monkeypatch, real):
    from bridge import estop

    arm, _hand = real
    see(monkeypatch, cup())
    arm.on_command = estop.renew_teleop_lease
    out = await m.pick("cup")
    assert out["phase"] == "aborted:teleop_active"
    assert arm.release_requested and len(arm.commands) == 1


# --- planning off the event loop (A11) ------------------------------------------------------


async def _stop_while_planning(monkeypatch, skill, start):
    """Run `start()` with planning held open in its worker thread; cancel the task the skill
    registered meanwhile, as stop_everything does, then let planning finish."""
    release = threading.Event()

    def held(real):
        def wrapper(*a, **k):
            assert release.wait(2.0), "planning ran on the event loop"
            return real(*a, **k)

        return wrapper

    for name in ("plan_pick", "plan_place", "_solve"):
        monkeypatch.setattr(m, name, held(getattr(m, name)))
    registry = get_registry()
    before = {t.task_id for t in registry.list_active()}
    running = asyncio.ensure_future(start())
    mine = []
    for _ in range(100):
        await asyncio.sleep(0.01)
        mine = [t for t in registry.list_active() if t.task_id not in before]
        if mine:
            break
    release.set()
    assert mine and mine[0].skill_name == skill, "no task to cancel while the plan is computed"
    assert registry.cancel(mine[0].task_id)
    out = await asyncio.wait_for(running, 5.0)
    assert out["task_id"] == mine[0].task_id
    return out


@pytest.mark.asyncio
@pytest.mark.parametrize("skill", ["pick", "place", "reach", "handover"])
async def test_a_stop_during_planning_finds_the_task_and_nothing_moves(monkeypatch, real, skill):
    arm, _hand = real
    see(monkeypatch, cup())
    if skill == "place":  # from where a pick leaves the hand
        await m.pick("cup")
        arm.commands.clear()
        arm.engaged = False
    start = {
        "pick": lambda: m.pick("cup"),
        "place": lambda: m.place("right"),
        "reach": lambda: m.reach("right", (0.30, -0.15, 0.10)),
        "handover": lambda: m.handover("right"),
    }[skill]
    out = await _stop_while_planning(monkeypatch, skill, start)
    assert out["status"] == "cancelled", out
    assert not arm.engaged and arm.commands == []


@pytest.mark.asyncio
async def test_a_pick_cancelled_while_planning_does_not_stay_running(monkeypatch, real):
    # FastMCP cancels the coroutine (client gone, timeout); the worker thread finishes alone.
    see(monkeypatch, cup())
    release = threading.Event()
    plan = m.plan_pick
    monkeypatch.setattr(m, "plan_pick", lambda *a: release.wait(2.0) and plan(*a))
    before = {t.task_id for t in get_registry().list_active()}
    running = asyncio.ensure_future(m.pick("cup"))
    await asyncio.sleep(0.05)
    (task,) = [t for t in get_registry().list_active() if t.task_id not in before]
    running.cancel()
    with pytest.raises(asyncio.CancelledError):
        await running
    release.set()
    assert task.status == "cancelled" and task.ended_at is not None


# --- pick steps the robot itself (2026-11-13) ------------------------------------------------

#: The bottle of 2026-11-13: only a 60° plan, and the real hand came down on it from above.
CLOSE_BOTTLE = (0.258, -0.096, 0.124)


class LaterLink(FakeLink):
    """A report captured `after` seconds later than STAMP: the frames after the robot stepped."""

    def __init__(self, objects, after=5.0):
        super().__init__(objects)
        self.after = after

    def latest_report(self):
        report, age = super().latest_report()
        return {**report, "stamp_unix": STAMP + self.after}, age


def bottle(p, **kw):
    return cup(p=p, label="bottle", track_id=7, **kw)


def stepper(monkeypatch, moved_to=None, status="completed", phase="done", on_step=None):
    """Fake _step_base: records each (forward, left); the scene then shows the bottle where a
    perfect step leaves it (or `moved_to(target, fwd, left)`), in a later capture."""
    steps = []

    async def step(fwd, left):
        steps.append((fwd, left))
        if on_step:
            on_step()
        where = moved_to or (lambda t, f, lft: bottle((t[0] - f, t[1] - lft, t[2])))
        objects = where(steps_target[0], fwd, left)
        objects = objects if isinstance(objects, list) else [objects]
        if objects and "cam_xyz" in objects[0] and moved_to is None:
            steps_target[0] = kinematics.camera_to_torso(objects[0]["cam_xyz"])
        monkeypatch.setattr(perception_link, "_link_singleton", LaterLink(objects, after=5.0 * len(steps)))
        return {"status": status, "phase": phase, "error": None}

    steps_target = [CLOSE_BOTTLE]
    monkeypatch.setattr(m, "_step_base", step)
    return steps


def test_the_walk_advice_aims_where_a_level_grasp_plans():
    # With no table only level plans; at COMFORT_X_M 0.30 it said "walking will not fix that".
    _text, advice, _hint = m.reach_advice("right", CLOSE_BOTTLE)
    assert advice == {"step_back_m": 0.10, "step_left_m": 0.05}


def test_upright_labels_and_the_level_zone_step():
    assert m.upright("bottle") and m.upright("Water Bottle") and m.upright("wine glass")
    assert not m.upright("remote") and not m.upright(None)
    # Back to the zone's middle and toward the arm's side; never forward without a table.
    assert m.reposition_step("right", CLOSE_BOTTLE, None) == pytest.approx((-0.122, 0.054))
    assert m.reposition_step("right", (0.50, -0.15, 0.1), None) == pytest.approx((0.0, 0.0))
    # A reported edge 0.30 m out: forward only until it is WALK_EDGE_STANDOFF_M away.
    assert m.reposition_step("right", (0.50, -0.15, 0.1), (0.30, 0.0))[0] == pytest.approx(0.05)
    assert m.reposition_step("left", (0.38, 0.15, 0.1), None) == pytest.approx((0.0, 0.0))


def test_an_upright_object_never_gets_the_steep_grasp():
    near = (0.20, -0.07)  # a table edge this close: only 60° plans at REACHABLE
    steep = m.plan_pick("right", REACHABLE, "power", HANGING, near)
    assert m.approach_tilt_deg("right", steep) > 45
    with pytest.raises(m.PlanError):
        m.plan_pick("right", REACHABLE, "power", HANGING, near, m.LEVEL_TILT_DEG)
    level = m.plan_pick("right", REACHABLE, "power", HANGING, (0.25, -0.07), m.LEVEL_TILT_DEG)
    assert m.approach_tilt_deg("right", level) <= 31


@pytest.mark.asyncio
async def test_a_level_plan_from_here_means_no_step(monkeypatch, real):
    steps = stepper(monkeypatch)
    see(monkeypatch, bottle(REACHABLE))
    out = await m.pick("bottle")
    assert out["status"] == "completed", out
    assert steps == [] and "moves" not in out["result"]
    assert out["result"]["approach_tilt_deg"] <= 31


@pytest.mark.asyncio
async def test_a_bottle_too_close_for_a_level_grasp_makes_the_robot_step_back_and_aside(
    monkeypatch, real
):
    arm, _hand = real
    steps = stepper(monkeypatch)
    see(monkeypatch, bottle(CLOSE_BOTTLE))
    out = await m.pick("bottle")
    assert out["status"] == "completed", out
    assert steps == [pytest.approx((-0.122, 0.054))]  # back to x 0.38, right arm's side y -0.15
    (move,) = out["result"]["moves"]
    assert move["why"] == "no level approach from here"
    assert out["result"]["approach_tilt_deg"] <= 31  # from the side, not from above
    assert out["result"]["target_torso_m"] == pytest.approx([0.38, -0.15, 0.124], abs=2e-3)
    assert arm.engaged  # stepped first, the arm only after


@pytest.mark.asyncio
async def test_after_the_step_the_same_label_near_where_it_should_be_counts(monkeypatch, real):
    # The tracker gave it a new id after the step: same label, 4 cm from the prediction.
    stepper(monkeypatch, moved_to=lambda t, f, lft: [
        cup(p=(0.30, -0.15, 0.124), label="bottle", track_id=99),  # 8 cm off: the farther one
        cup(p=(t[0] - f + 0.04, t[1] - lft, t[2]), label="bottle", track_id=42),
    ])  # fmt: skip
    see(monkeypatch, bottle(CLOSE_BOTTLE))
    out = await m.pick("bottle")
    assert out["result"]["object"]["track_id"] == 42, out
    # Nothing of that label within REFIND_RADIUS_M: lost, refused, nothing on the arm.
    real[0]._t[7:14] = list(HANGING)  # the first pick left it out in front: no step from there
    monkeypatch.setattr(m, "REFIND_WAIT_S", 0.0)
    stepper(monkeypatch, moved_to=lambda t, f, lft: cup(p=(0.30, -0.30, 0.124), label="bottle"))
    see(monkeypatch, bottle(CLOSE_BOTTLE))
    out = await m.pick("bottle")
    assert out["phase"] == "planning" and "lost 'bottle' after stepping" in out["error"]
    assert out["result"]["base_moved"] is True and out["result"]["moved"] is False


@pytest.mark.asyncio
async def test_a_detection_from_before_the_step_is_not_the_object(monkeypatch, real):
    monkeypatch.setattr(m, "REFIND_WAIT_S", 0.0)
    steps = stepper(monkeypatch)
    see(monkeypatch, bottle(CLOSE_BOTTLE))

    async def step_without_new_frames(fwd, left):
        steps.append((fwd, left))
        return {"status": "completed", "phase": "done", "error": None}

    monkeypatch.setattr(m, "_step_base", step_without_new_frames)
    out = await m.pick("bottle")
    assert "lost" in out["error"] and len(steps) == 1


@pytest.mark.asyncio
async def test_stepping_stops_at_two_moves(monkeypatch, real):
    arm, _hand = real
    # The legs did not go anywhere: the bottle stays put (a later frame) and stays too close.
    steps = stepper(monkeypatch, moved_to=lambda t, f, lft: bottle(CLOSE_BOTTLE))
    see(monkeypatch, bottle(CLOSE_BOTTLE))
    out = await m.pick("bottle")
    assert len(steps) == m.MAX_REPOSITIONS == 2
    assert out["phase"] == "planning" and not arm.engaged
    assert len(out["result"]["moves"]) == 2 and out["result"]["base_moved"] is True
    travel = sum(math.hypot(*s) for s in steps)
    assert travel <= m.MAX_REPOSITION_TRAVEL_M


@pytest.mark.asyncio
async def test_a_step_beyond_the_travel_budget_is_not_taken(monkeypatch, real):
    steps = stepper(monkeypatch)
    monkeypatch.setattr(m, "MAX_REPOSITION_TRAVEL_M", 0.10)  # the step needs 0.134 m
    see(monkeypatch, bottle(CLOSE_BOTTLE))
    out = await m.pick("bottle")
    assert steps == [] and out["phase"] == "planning" and "moves" not in out["result"]


@pytest.mark.asyncio
async def test_no_forward_step_without_a_reported_table(monkeypatch, real):
    # Too far for any plan, and nothing says where the desk edge is: no step at all.
    steps = stepper(monkeypatch)
    see(monkeypatch, bottle((0.60, -0.15, 0.05)))
    out = await m.pick("bottle")
    assert steps == [] and out["phase"] == "planning"


@pytest.mark.asyncio
async def test_parar_during_the_step_ends_the_pick(monkeypatch, real):
    arm, _hand = real

    def stop():
        for t in get_registry().list_active():
            get_registry().cancel(t.task_id)

    stepper(monkeypatch, on_step=stop)
    see(monkeypatch, bottle(CLOSE_BOTTLE))
    out = await m.pick("bottle")
    assert out["status"] == "cancelled", out
    assert out["result"]["base_moved"] is True
    assert not arm.engaged and arm.commands == []


@pytest.mark.asyncio
async def test_a_refused_step_says_why_and_nothing_moves(monkeypatch, real):
    arm, _hand = real
    steps = stepper(monkeypatch, status="failed", phase="fsm_not_walkable")
    see(monkeypatch, bottle(CLOSE_BOTTLE))
    out = await m.pick("bottle")
    assert len(steps) == 1 and out["result"]["reason"] == "reposition_fsm_not_walkable"
    assert out["result"]["base_moved"] is False and not arm.engaged


@pytest.mark.asyncio
async def test_stub_mode_never_steps(monkeypatch):
    monkeypatch.setattr(m, "SIM_MODE", "stub")
    steps = stepper(monkeypatch)
    see(monkeypatch, bottle(CLOSE_BOTTLE))
    out = await m.pick("bottle")
    assert steps == [] and out["phase"] == "planning"


@pytest.mark.asyncio
async def test_the_step_goes_through_walk_velocity_as_an_autonomous_walk(monkeypatch):
    from bridge.skills import walk_velocity

    calls = []

    async def run(vx, vy, vyaw, duration_s, autonomous=False):
        calls.append((vx, vy, vyaw, duration_s, autonomous))
        return {"status": "completed"}

    monkeypatch.setattr(walk_velocity, "run", run)
    await m._step_base(-0.12, 0.09)
    ((vx, vy, vyaw, dur, autonomous),) = calls
    assert autonomous is True and vyaw == 0.0
    assert (vx * dur, vy * dur) == pytest.approx((-0.12, 0.09))
    assert math.hypot(vx, vy) == pytest.approx(m.REPOSITION_SPEED_MPS)


@pytest.mark.asyncio
async def test_a_stop_while_planning_never_steps(monkeypatch, real):
    steps = stepper(monkeypatch)
    plan = m.plan_pick

    def stop_then_plan(*a):
        for t in get_registry().list_active():
            get_registry().cancel(t.task_id)
        return plan(*a)

    monkeypatch.setattr(m, "plan_pick", stop_then_plan)
    see(monkeypatch, bottle(CLOSE_BOTTLE))
    out = await m.pick("bottle")
    assert out["status"] == "cancelled" and steps == []


# --- the spoken warning: after the plan, before the first motion ----------------


def _warning(monkeypatch, arm, steps=None, on_speak=None):
    """Defer pick's warning as GovernedMCP.call_tool does; records what had moved when it played."""
    from bridge import motion_announce

    monkeypatch.setattr(motion_announce, "_last", ("", 0.0))
    said = []

    async def speak(text):
        said.append((arm.engaged, len(steps) if steps is not None else 0))
        if on_speak:
            on_speak()
        return {"status": "ok"}

    motion_announce.defer("pick", {"label": "bottle"}, speak)
    return said


async def test_a_pick_its_planner_refuses_is_never_announced(monkeypatch, real):
    arm, _hand = real
    monkeypatch.setattr(m, "MAX_REPOSITIONS", 0)
    said = _warning(monkeypatch, arm)
    see(monkeypatch, cup(p=(0.35, -0.01, -0.03)))  # out of reach: refused in planning
    out = await m.pick("cup", side="right")
    assert out["phase"] == "planning" and said == []


async def test_a_planned_pick_is_announced_once_before_the_arm_engages(monkeypatch, real):
    arm, _hand = real
    said = _warning(monkeypatch, arm)
    see(monkeypatch, cup())
    out = await m.pick("cup", shape="pinch")
    assert out["status"] == "completed", out
    assert said == [(False, 0)]


async def test_a_stop_during_the_pick_warning_moves_nothing(monkeypatch, real):
    from bridge import estop

    arm, _hand = real
    _warning(
        monkeypatch, arm, on_speak=lambda: monkeypatch.setattr(estop, "last_stop_at", lambda: 9e9)
    )
    see(monkeypatch, cup())
    out = await m.pick("cup", shape="pinch")
    assert out["status"] == "cancelled" and not arm.engaged and arm.commands == []


async def test_the_warning_goes_before_the_step_and_not_again_for_the_arm(monkeypatch, real):
    arm, _hand = real
    steps = stepper(monkeypatch)
    said = _warning(monkeypatch, arm, steps)
    see(monkeypatch, bottle(CLOSE_BOTTLE))
    out = await m.pick("bottle")
    assert out["status"] == "completed", out
    assert said == [(False, 0)]  # before the step, once
