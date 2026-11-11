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

REACHABLE = (0.30, -0.15, 0.05)  # torso_link: counter height, in front of the right shoulder
#: The right arm hanging at the side, the hand just below desk height (URDF angles).
HANGING = [0.0, -0.15, 0.0, 1.5, 0.0, 0.0, 0.0]
#: The thermos on the desk, 2026-11-05: planned fine, then the hand hit the desk edge.
THERMOS = (0.35, -0.16, -0.03)
PICK_PHASES = ["open", "clear", "over", "pre_grasp", "grasp", "close", "closing", "lift"]
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


def test_a_pick_plan_opens_first_and_lifts_last():
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
    "target", [REACHABLE, (0.36, -0.25, 0.0), (0.28, -0.05, 0.10), (0.26, -0.30, -0.05)]
)
def test_approach_and_lift_follow_a_straight_line(target):
    # Audit §4: one joint-space move pre-grasp -> grasp swept the hand up to 24 cm sideways.
    steps = m.plan_pick("right", target, "power", None)
    assert max_deviation(steps, "grasp", "pre_grasp") < 0.01
    assert max_deviation(steps, "lift", "grasp") < 0.01


def test_place_lowers_and_retreats_in_straight_lines():
    steps = m.plan_place("right", REACHABLE, None)
    assert phases(steps) == ["above", "down", "open", "opening", "retreat", "withdraw"]
    assert max_deviation(steps, "down", "above") < 0.01
    assert max_deviation(steps, "retreat", "down") < 0.01
    assert max_deviation(steps, "withdraw", "retreat") < 0.01


def arm_path(steps, q0, until):
    """Every hand position arm_sdk slews through from q0 until the last `until` waypoint."""
    qs = [q0] + [s[2] for s in steps if s[0] == "arm"]
    last = max(i for i, s in enumerate([s for s in steps if s[0] == "arm"]) if s[1] == until)
    return [p for a, b in zip(qs, qs[1 : last + 2]) for p in slewed_tcp(a, b)]


@pytest.mark.parametrize("target", [THERMOS, REACHABLE, (0.30, -0.15, -0.10), (0.36, -0.25, 0.0)])
def test_the_hand_never_sweeps_through_the_desk_on_the_way_to_pre_grasp(target):
    # Robot, 2026-11-05: one slew hanging -> pre-grasp swung the hand into the desk edge.
    # In front of the body the hand stays above the surface + margin; the only way down
    # is straight down onto pre-grasp, over the object's footprint.
    # The tuck is the closest TUCK_X_M point the arm clears the torso from: the desk incident's
    # thermos still rises at the first; REACHABLE's 0.16 m tuck folds the wrist into the chest.
    steps = m.plan_pick("right", target, "power", HANGING)
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
    steps = m.plan_place("right", REACHABLE, None)
    retreat = [kinematics.forward("right", s[2]).position for s in steps if s[1] == "retreat"]
    withdraw = [kinematics.forward("right", s[2]).position for s in steps if s[1] == "withdraw"]
    assert retreat[-1][2] >= REACHABLE[2] + m.SURFACE_MARGIN_M - kinematics.POS_TOL_M
    assert all(p[2] >= retreat[-1][2] - kinematics.POS_TOL_M for p in withdraw)
    assert withdraw[-1][0] <= m.TUCK_X_M[-1] + kinematics.POS_TOL_M  # back toward the body


def test_a_hand_already_low_in_front_is_refused_not_swept_back():
    low_front = kinematics.solve("right", (0.30, -0.15, -0.15), seed=HANGING).q
    with pytest.raises(m.PlanError, match="release_arm_control"):
        m.plan_pick("right", THERMOS, "power", list(low_front))


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
    for s in m.plan_pick("right", (0.36, -0.25, 0.0), "power", None):
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
    assert [s for _, s in hand.shapes] == [hands.SHAPES["open"], hands.SHAPES["pinch"]]
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
        if len(hand.shapes) == 2:  # the close, after the open
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
    see(monkeypatch, cup(p=(0.35, -0.01, -0.03)))
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
    m.plan_pick("right", (0.30, -0.15, -0.03), "power", None)  # where that walk puts it


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


def test_a_support_from_another_frame_is_dropped(monkeypatch):
    # Same capture = the support's stamp is the summary's stamp minus the object's age_s.
    see(monkeypatch, cup(age_s=0.2, support=support(stamp=STAMP - 0.7)))
    _p, obj = m.detected_target("cup", None)
    assert "support" not in obj and m.support_table(obj, REACHABLE) is None
    see(monkeypatch, cup(age_s=0.2, support=support()))
    assert "support" in m.detected_target("cup", None)[1]


@pytest.mark.asyncio
async def test_pick_plans_against_the_reported_table(monkeypatch):
    see(monkeypatch, cup(age_s=0.2, support=support(z_m=-0.10 + 0.044)))  # top z=-0.10
    out = await m.pick("cup", side="right")
    assert out["phase"] == "stub", out
    assert out["result"]["table_torso_m"] == pytest.approx([0.254, -0.10], abs=1e-3)
    # The object only 2 cm above the table top: the hand would hit it, so nothing plans.
    see(monkeypatch, cup(age_s=0.2, support=support(z_m=0.03 + 0.044)))
    out = await m.pick("cup", side="right")
    assert out["phase"] == "planning" and out["result"]["reason"] == "table_clearance"
    assert out["result"]["advice"]["part"] == "hand" and "tocaría la mesa" in out["result"]["note"]


def test_a_lower_reported_surface_never_lowers_the_hand():
    # The object's own z (+ margin) stays the floor of the over-the-surface path.
    over = last_q(m.plan_pick("right", REACHABLE, "power", HANGING, (0.20, -0.10)), "over")
    assert kinematics.forward("right", over).position[2] == pytest.approx(
        REACHABLE[2] + m.SURFACE_MARGIN_M, abs=kinematics.POS_TOL_M
    )


def test_every_arm_capsule_keeps_clear_of_the_table_box():
    table = (0.20, -0.03)
    for kind, _phase, q in m.plan_pick("right", REACHABLE, "power", HANGING, table):
        if kind == "arm":
            m._check_torso("right", "replay", [q], table)  # raises if any capsule is inside
    with pytest.raises(m.PlanError, match="would touch the table") as err:
        m.plan_pick("right", REACHABLE, "power", HANGING, (0.20, 0.03))
    assert err.value.reason == "table_clearance" and err.value.part == "hand"


def test_an_object_beyond_the_table_edge_says_walking_stops_at_the_edge():
    text, advice, hint = m.reach_advice("right", (0.60, -0.15, 0.05), (0.20, -0.05))
    assert advice == {"problem": "behind_table_edge", "behind_edge_m": 0.4}
    assert "0.40 m behind the table edge" in text and "detrás del borde" in hint


def test_a_robot_hugging_the_table_is_told_to_step_back():
    # The edge 8 cm from the torso: closer than the nearest tuck point, so back off to it.
    text, advice, hint = m.reach_advice("right", REACHABLE, (0.08, -0.05))
    assert advice == {"step_back_m": 0.08}
    assert "0.08 m back" in text and hint.startswith("Alejate ~0.08 m")


@pytest.mark.asyncio
async def test_auto_side_tries_the_other_arm_before_giving_up(monkeypatch, real):
    _arm, hand = real
    hand.sides = ("left", "right")
    see(monkeypatch, cup(p=(0.30, 0.0, 0.05)))  # right's side (y <= 0), the left arm reaches
    plan = m.plan_pick

    def right_fails(side, *a):
        if side == "right":
            raise m.PlanError("right: out of reach")
        return plan(side, *a)

    monkeypatch.setattr(m, "plan_pick", right_fails)
    out = await m.pick("cup")
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
