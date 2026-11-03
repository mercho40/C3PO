"""skills/manipulation.py: plan everything before moving, and stop when it must."""

from __future__ import annotations

import math

import pytest

from bridge import kinematics, safety
from bridge.sdk import perception_link
from bridge.skills import manipulation as m
from bridge.teleop import arm_sdk, hands
from bridge.teleop.retarget import ArmAngles

REACHABLE = (0.30, -0.15, 0.05)  # torso_link: counter height, in front of the right shoulder


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
        return {"detector_online": True, "objects": self.objects, "objects_omitted": 0}, self.age


class FakeArm:
    """Instant slew; records every arm command."""

    def __init__(self):
        self.engaged = False
        self.weight = 1.0
        self._t = [0.0] * 14
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
    monkeypatch.setattr(safety, "PERSON_RULE", True)
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
    assert phases(steps) == ["open", "pre_grasp", "grasp", "close", "closing", "lift"]
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
    assert phases(steps) == ["above", "down", "open", "opening", "retreat"]
    assert max_deviation(steps, "down", "above") < 0.01
    assert max_deviation(steps, "retreat", "down") < 0.01


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
    assert [e["phase"] for e in plan] == ["open", "pre_grasp", "grasp", "close", "closing", "lift"]
    assert plan[2]["waypoints"] >= 10  # 10 cm approach at 1 cm


def test_an_unreachable_target_never_produces_a_plan():
    with pytest.raises(m.PlanError):
        m.plan_pick("right", (0.52, -0.2, -0.28), "power", None)


def test_the_detection_round_trips_through_the_camera_mount(monkeypatch):
    see(monkeypatch, cup())
    p, obj = m.detected_target("taza", None)  # Spanish query, English label
    assert obj["label"] == "cup"
    assert p == pytest.approx(REACHABLE, abs=1e-6)


def test_stale_or_depthless_detections_are_not_grasp_targets(monkeypatch):
    see(monkeypatch, cup(), age=2.0)
    with pytest.raises(m.PlanError, match="fresh"):
        m.detected_target("cup", None)
    no_point = cup()
    del no_point["cam_xyz"]
    see(monkeypatch, no_point)
    with pytest.raises(m.PlanError, match="3-D point"):
        m.detected_target("cup", None)


# --- execution ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stub_mode_plans_and_moves_nothing(monkeypatch):
    see(monkeypatch, cup())
    monkeypatch.setattr(m, "SIM_MODE", "stub")
    arm = FakeArm()
    monkeypatch.setattr(arm_sdk, "get_driver", lambda: arm)
    out = await m.pick("cup")
    assert out["status"] == "completed" and out["phase"] == "stub"
    assert len(out["result"]["plan"]) == 6
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
async def test_pick_refuses_when_that_hand_is_not_configured(monkeypatch, real):
    arm, _hand = real
    see(monkeypatch, cup())
    out = await m.pick("cup", side="left")  # FakeHand drives the right hand only
    assert out["phase"] in ("hand_unavailable", "planning")
    assert not arm.engaged


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
