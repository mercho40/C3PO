"""MuJoCo twin of the G1 arms (ACTION-PLAN I4): the bridge's kinematics and arm plans checked
against MuJoCo Menagerie's `unitree_g1/g1.xml`, a second description of the same robot with
real meshes. No DDS, no SIM_MODE change, kinematics only (no stepping, so no gains to tune).

    uv sync --extra sim && bash scripts/fetch_menagerie_g1.sh && uv run pytest -m sim

Skips without mujoco or the fetched model, so the default suite stays fast and offline.
C3PO_SIM_REQUIRED=1 (CI's sim step) turns that skip into a failure: a twin that always skips
checks nothing.
"""

from __future__ import annotations

import collections
import math
import os
import random
from functools import cache
from pathlib import Path

import pytest

from bridge import kinematics
from bridge.skills import arm_pose
from bridge.skills import manipulation as m
from bridge.teleop import arm_sdk
from bridge.teleop.retarget import JOINT_NAMES, LIMITS, ArmAngles

pytestmark = pytest.mark.sim

MODEL = (
    Path(
        os.environ.get(
            "C3PO_MENAGERIE_G1", Path(__file__).parents[2] / ".cache" / "menagerie" / "unitree_g1"
        )
    )
    / "g1.xml"
)

if os.environ.get("C3PO_SIM_REQUIRED") == "1":
    import mujoco

    assert MODEL.exists(), f"{MODEL} missing: bash scripts/fetch_menagerie_g1.sh"
else:
    mujoco = pytest.importorskip("mujoco", reason="uv sync --extra sim")
    if not MODEL.exists():
        pytest.skip(f"{MODEL} missing: bash scripts/fetch_menagerie_g1.sh", allow_module_level=True)

import numpy as np  # noqa: E402 - mujoco's own dependency, present whenever it is

SIDES = ("left", "right")
#: The right arm hanging at the side (test_manipulation.HANGING); the left one mirrors it.
HANGING = {
    "right": [0.0, -0.15, 0.0, 1.5, 0.0, 0.0, 0.0],
    "left": [0.0, 0.15, 0.0, 1.5, 0.0, 0.0, 0.0],
}
#: test_manipulation's pick targets (torso_link, right arm): counter height to below the desk.
PICK_TARGETS = [
    (0.30, -0.15, 0.05),
    (0.35, -0.16, -0.03),
    (0.36, -0.25, 0.0),
    (0.28, -0.05, 0.10),
    (0.26, -0.30, -0.05),
    (0.30, -0.15, -0.10),
]


@pytest.fixture(scope="module")
def twin():
    model = mujoco.MjModel.from_xml_path(str(MODEL))
    return model, mujoco.MjData(model)


def _pose(twin, sides_q):
    """Arms at `sides_q`, everything else at the model's qpos0; FK plus collision detection."""
    model, data = twin
    data.qpos[:] = model.qpos0
    for side, q in sides_q.items():
        for joint, v in zip(kinematics.arm_chain(side), q, strict=True):
            data.qpos[model.jnt_qposadr[model.joint(joint.name).id]] = v
    mujoco.mj_forward(model, data)
    return model, data


def _in_torso(data, model, body):
    """Position and rotation of `body` in torso_link, as kinematics.py reports them."""
    t, b = model.body("torso_link").id, model.body(body).id
    rt = data.xmat[t].reshape(3, 3)
    return rt.T @ (data.xpos[b] - data.xpos[t]), rt.T @ data.xmat[b].reshape(3, 3)


def _tcp(twin, side, q):
    model, data = _pose(twin, {side: q})
    p, r = _in_torso(data, model, f"{side}_wrist_yaw_link")
    return p + r[:, 0] * kinematics.GRASP_TCP_M


@cache
def _pick(target):
    return m.plan_pick("right", target, "power", HANGING["right"])  # pure-Python IK: ~1 s each


def _arm_qs(steps):
    return [q for kind, _, q in steps if kind == "arm"]


def _last(steps, phase):
    return [q for kind, ph, q in steps if kind == "arm" and ph == phase][-1]


# --- the description itself --------------------------------------------------------------


def test_arm_sdk_motor_slots_are_the_models_joint_order(twin):
    # The model's 29 actuators follow Unitree's motor index; arm_sdk writes q by slot number.
    model, _ = twin
    actuators = [model.actuator(i).name for i in range(model.nu)]
    for side, base in (("left", arm_sdk.LEFT_ARM_BASE), ("right", arm_sdk.RIGHT_ARM_BASE)):
        assert actuators[base : base + 7] == [j.name for j in kinematics.arm_chain(side)]
    waist = [actuators[s] for s in arm_sdk.WAIST_SLOTS]
    assert waist == ["waist_yaw_joint", "waist_roll_joint", "waist_pitch_joint"]


@pytest.mark.parametrize("side", SIDES)
def test_urdf_joint_limits_and_axes_match_the_model(twin, side):
    model, _ = twin
    for joint in kinematics.arm_chain(side):
        jid = model.joint(joint.name).id
        assert model.jnt_range[jid] == pytest.approx((joint.lower, joint.upper), abs=1e-4)
        assert model.jnt_axis[jid] == pytest.approx(joint.axis, abs=1e-6)


@pytest.mark.parametrize("side", SIDES)
def test_forward_kinematics_matches_mujoco(twin, side):
    # Frame or sign errors in kinematics.forward would show here as cm and tens of degrees.
    rng = random.Random(4)
    worst_m = worst_deg = 0.0
    for _ in range(100):
        q = [rng.uniform(lo, hi) for lo, hi in kinematics.limits(side)]
        model, data = _pose(twin, {side: q})
        p, r = _in_torso(data, model, f"{side}_wrist_yaw_link")
        hp = kinematics.forward(side, q, tcp_m=0.0)
        worst_m = max(worst_m, float(np.linalg.norm(p - hp.position)))
        cos = float(np.clip(r[:, 0] @ hp.pointing, -1.0, 1.0))
        worst_deg = max(worst_deg, math.degrees(math.acos(cos)))
    assert worst_m < 1e-3, f"wrist position off by {worst_m * 1000:.2f} mm"
    assert worst_deg < 0.1, f"finger axis off by {worst_deg:.3f}°"


# --- what the skills command -------------------------------------------------------------


@pytest.mark.parametrize("side", SIDES)
def test_move_arm_envelope_stays_inside_the_model_limits(twin, side):
    # move_arm clamps to retarget.LIMITS (semantic) and applies JOINT_SIGNS: every corner of
    # that box must land inside the motor's range, or a sign error asks for an impossible q.
    model, _ = twin
    for name, joint in zip(JOINT_NAMES, kinematics.arm_chain(side), strict=True):
        lo, hi = model.jnt_range[model.joint(joint.name).id]
        for semantic in LIMITS[name]:
            wiring = arm_pose.semantic_to_wiring(side, {name: semantic})[name]
            assert lo <= wiring <= hi, f"{side} {name} {math.degrees(wiring):.0f}° out of range"


@pytest.mark.parametrize("target", PICK_TARGETS)
def test_pick_plan_stays_in_limits_and_the_model_hand_reaches_the_grasp(twin, target):
    model, _ = twin
    steps = _pick(target)
    ranges = [model.jnt_range[model.joint(j.name).id] for j in kinematics.arm_chain("right")]
    for q in _arm_qs(steps):
        assert all(lo <= v <= hi for v, (lo, hi) in zip(q, ranges, strict=True))
    # The grasp point is the target pushed GRASP_DEPTH_OFFSET_M along whichever tilt planned.
    grasps = [
        np.add(target, np.multiply(m._approach("right", target, t), m.GRASP_DEPTH_OFFSET_M))
        for t in m.APPROACH_TILTS_DEG
    ]
    at_grasp = _tcp(twin, "right", _last(steps, "grasp"))
    miss = min(float(np.linalg.norm(at_grasp - g)) for g in grasps)
    assert miss < kinematics.POS_TOL_M, f"model hand {miss * 100:.1f} cm from the grasp point"
    grasp = min(grasps, key=lambda g: float(np.linalg.norm(at_grasp - g)))
    lifted = _tcp(twin, "right", _last(steps, "lift"))
    assert np.linalg.norm(lifted - (grasp + (0, 0, m.LIFT_M))) < kinematics.POS_TOL_M


@pytest.mark.parametrize("side", SIDES)
def test_handover_and_place_reach_their_points_in_the_model(twin, side):
    p = m.handover_point(side)
    q = m._solve(side, "handover", p, (1.0, 0.0, 0.0), HANGING[side])
    assert np.linalg.norm(_tcp(twin, side, q) - p) < kinematics.POS_TOL_M
    place = (0.30, 0.15 if side == "left" else -0.15, 0.05)
    steps = m.plan_place(side, place, HANGING[side])
    assert np.linalg.norm(_tcp(twin, side, _last(steps, "down")) - place) < kinematics.POS_TOL_M


# --- what the box does not see --------------------------------------------------------------


def _body_contacts(twin, side, q):
    """(arm body, other body, depth m) for every mesh contact between this arm and the rest of
    the robot, the other arm hanging. MuJoCo collides mesh convex hulls."""
    other_side = "left" if side == "right" else "right"
    model, data = _pose(twin, {side: q, other_side: HANGING[other_side]})
    arm = {model.body(j.name.replace("_joint", "_link")).id for j in kinematics.arm_chain(side)}
    out = []
    for c in data.contact[: data.ncon]:
        b1, b2 = (int(model.geom_bodyid[g]) for g in (c.geom1, c.geom2))
        if (b1 in arm) != (b2 in arm):
            mine, other = (b1, b2) if b1 in arm else (b2, b1)
            out.append((model.body(mine).name, model.body(other).name, -float(c.dist)))
    return out


def _vertices(twin, body, skip=()):
    """Every collision-geom vertex of `body` in torso_link at the current pose, but for the
    meshes in `skip` (a cylinder by its two rim circles, which span its hull)."""
    model, data = twin
    t = model.body("torso_link").id
    rt, pt = data.xmat[t].reshape(3, 3), data.xpos[t]
    out = []
    for g in range(model.ngeom):
        if model.geom_bodyid[g] != model.body(body).id or not model.geom_contype[g]:
            continue
        if model.geom_type[g] == mujoco.mjtGeom.mjGEOM_MESH:
            mid = model.geom_dataid[g]
            if model.mesh(mid).name in skip:
                continue
            a, n = model.mesh_vertadr[mid], model.mesh_vertnum[mid]
            v = model.mesh_vert[a : a + n]
        else:
            assert model.geom_type[g] == mujoco.mjtGeom.mjGEOM_CYLINDER
            r, h = model.geom_size[g][:2]
            th = np.linspace(0, 2 * np.pi, 256, endpoint=False)
            ring = np.stack([r * np.cos(th), r * np.sin(th), np.zeros_like(th)], 1)
            v = np.concatenate([ring + (0, 0, -h), ring + (0, 0, h)])
        world = v @ data.geom_xmat[g].reshape(3, 3).T + data.geom_xpos[g]
        out.append((world - pt) @ rt)
    return np.concatenate(out)


def test_the_torso_model_contains_the_torso_meshes(twin):
    # TORSO_BOX and TORSO_FLANKS must hold every point MuJoCo collides as the torso, or a
    # clear plan can still touch. The head sits above the arm's workspace and is left out.
    _pose(twin, {})
    outside = [p for p in _vertices(twin, "torso_link", skip=("head_link",)) if m._torso_gap(p)]
    assert not outside, f"{len(outside)} torso vertices outside the model, e.g. {outside[0]}"


@pytest.mark.parametrize("side", SIDES)
def test_each_arm_capsule_contains_its_links_collision_geometry(twin, side):
    rng = random.Random(7)
    chain = kinematics.arm_chain(side)
    for _ in range(5):
        q = [rng.uniform(lo, hi) for lo, hi in kinematics.limits(side)]
        _pose(twin, {side: q})
        hp = kinematics.forward(side, q)
        pts = (*hp.joint_origins, hp.position)
        for part, a, b, t0, t1, r in m.ARM_CAPSULES:
            link = chain[a].name.replace("_joint", "_link")
            p0, p1 = (np.add(pts[a], np.subtract(pts[b], pts[a]) * t) for t in (t0, t1))
            ab = p1 - p0
            v = _vertices(twin, link)
            t = np.clip((v - p0) @ ab / (ab @ ab), 0.0, 1.0)
            reach = float(np.linalg.norm(v - (p0 + np.outer(t, ab)), axis=1).max())
            assert reach <= r, f"{link} ({part}) reaches {reach * 1000:.1f} mm > {r * 1000:.1f}"


def test_hanging_and_handover_poses_touch_nothing(twin):
    # Baseline: if these touched, every contact below would be noise from the hulls.
    for side in SIDES:
        assert _body_contacts(twin, side, HANGING[side]) == []
        q = m._solve(side, "handover", m.handover_point(side), (1.0, 0.0, 0.0), HANGING[side])
        assert _body_contacts(twin, side, q) == []


@pytest.mark.parametrize("target", PICK_TARGETS)
def test_pick_plan_keeps_the_arm_out_of_the_body(twin, target):
    # Every commanded pose, against every mesh hull MuJoCo collides (both arms, legs, head).
    # Before the capsule check every one of these put an arm link 6-51 mm into the torso.
    steps = _pick(target)
    hits = {}
    for phase, q in ((ph, q) for kind, ph, q in steps if kind == "arm"):
        for mine, other, depth in _body_contacts(twin, "right", q):
            key = f"{phase}: {mine} in {other}"
            hits[key] = max(hits.get(key, 0.0), depth)
    assert not hits, {k: f"{v * 1000:.0f} mm" for k, v in sorted(hits.items())}


# --- the driver in the loop (A39) ------------------------------------------------------------


class _Wire:
    """The fake rt/arm_sdk publisher: each arm's q out of every LowCmd_ written."""

    def __init__(self):
        self.right, self.left = [], []

    def Write(self, cmd):  # noqa: N802 - the SDK's name
        self.right.append([cmd.motor_cmd[arm_sdk.RIGHT_ARM_BASE + i].q for i in range(7)])
        self.left.append([cmd.motor_cmd[arm_sdk.LEFT_ARM_BASE + i].q for i in range(7)])


class _SteppedDriver(arm_sdk.ArmSdkDriver):
    """The real driver with its 50 Hz tick (weight ramp, slew, LowCmd_ build, publish) stepped
    by hand after each command until it settles, instead of by its asyncio loop and the clock.
    engage() skips the lowstate/FSM gates, which have their own tests."""

    def __init__(self, start):
        super().__init__()
        self._publisher = _Wire()
        self._current, self._target = list(start), list(start)
        self._waist_hold = [0.0, 0.0, 0.0]

    async def engage(self):
        self._engaged = True

    def command(self, left, right):
        super().command(left, right)
        while self._weight < 1.0 or any(
            abs(t - c) > 1e-9 for t, c in zip(self._target, self._current, strict=True)
        ):
            self._weight = min(1.0, self._weight + arm_sdk.CONTROL_PERIOD_S / arm_sdk.RAMP_S)
            self._slew()
            self._publish()


@pytest.mark.parametrize("target", PICK_TARGETS)
async def test_the_driver_carries_the_pick_plan_into_the_model_untouched(twin, target, monkeypatch):
    # execute(plan_pick) through the real driver: every frame it would put on the wire, the
    # per-joint slew between waypoints included, is a pose of the twin's arm.
    class Hand:
        name, sides = "fake", ("right",)

        def send_shape(self, side, closures):
            pass

    steps = [(k, ph, 0.0 if k == "wait" else v) for k, ph, v in _pick(target)]
    driver = _SteppedDriver([*HANGING["left"], *HANGING["right"]])
    monkeypatch.setattr(m, "SIM_MODE", "real")
    monkeypatch.setattr(m, "_abort_reason", lambda allow_person: None)  # own tests
    monkeypatch.setattr(arm_sdk, "get_driver", lambda: driver)
    monkeypatch.setattr(m.hands, "get_driver", Hand)
    out = await m.execute(m.get_registry().create("pick"), "right", steps, summary={})
    assert out["status"] == "completed", out

    # The frames follow the plan: each waypoint reached, in order, every joint moving straight
    # from one waypoint to the next (the slew never overshoots or reorders).
    frames, i, prev = driver._publisher.right, 0, HANGING["right"]
    for n, q in enumerate(_arm_qs(steps)):
        while max(abs(f - t) for f, t in zip(frames[i], q, strict=True)) > 1e-6:
            box = zip(frames[i], prev, q, strict=True)
            assert all(min(a, b) - 1e-9 <= f <= max(a, b) + 1e-9 for f, a, b in box), n
            i += 1
        prev = q
    assert i == len(frames) - 1

    hits = {}
    for f in frames:
        for mine, other, depth in _body_contacts(twin, "right", f):
            hits[f"{mine} in {other}"] = max(hits.get(f"{mine} in {other}", 0.0), depth)
    assert not hits, {k: f"{v * 1000:.1f} mm" for k, v in sorted(hits.items())}


# --- randomized tables (the support perception reports) -----------------------------------------

#: Seeded scenes per run: CI's sweep. C3PO_SIM_SCENES=200 (or more) for the full one.
SCENES = int(os.environ.get("C3PO_SIM_SCENES", "20"))  # ~1.5 s each
#: Every planning refusal's advice says how to walk, or (problem) why walking will not help.
WALKS = {"step_forward_m", "step_back_m", "step_left_m", "step_right_m", "problem"}


@pytest.fixture(scope="module")
def table_twin():
    """The twin with a box table (moved per scene) and, on each wrist_yaw_link, the planner's
    open hand (TABLE_CAPSULES): out past the TCP to the fingertips, palm-wide. The model's hand
    has no collision geometry of its own."""
    spec = mujoco.MjSpec.from_file(str(MODEL))
    # A mocap body, not a world geom: MuJoCo's broadphase never re-bounds a moved world geom.
    table = spec.worldbody.add_body(name="table", mocap=True, pos=[0, 0, -10])
    table.add_geom(type=mujoco.mjtGeom.mjGEOM_BOX, size=[1.0, 1.5, 0.5])
    (_part, _a, _b, t0, t1, radius) = m.TABLE_CAPSULES[-1]
    for side in SIDES:
        spec.body(f"{side}_wrist_yaw_link").add_geom(
            type=mujoco.mjtGeom.mjGEOM_CAPSULE,
            fromto=[t0 * kinematics.GRASP_TCP_M, 0, 0, t1 * kinematics.GRASP_TCP_M, 0, 0],
            size=[radius, 0, 0],
        )
    model = spec.compile()
    return model, mujoco.MjData(model)


def _scene(rng):
    """side, target, table (torso_link) and the table's height above the floor."""
    side = rng.choice(SIDES)
    floor_to_torso = 0.837  # torso_link above the feet's floor at qpos0 (asserted below)
    height = rng.uniform(0.70, 0.95)
    top = height - floor_to_torso
    edge = rng.uniform(0.10, 0.35)
    own = rng.uniform(0.0, 0.40)  # out from the midline on the arm's own side
    target = (
        rng.uniform(edge + 0.02, edge + 0.30),  # on the table, within reach and beyond it
        own if side == "left" else -own,
        top + rng.uniform(0.01, 0.12),  # an object's centre above the top: a phone to a bottle
    )
    return side, target, (edge, top), height


def _place_table(twin, table):
    model, data = twin
    _pose(twin, {})
    t = data.xpos[model.body("torso_link").id]
    edge, top = table
    data.mocap_pos[model.body("table").mocapid[0]] = (t[0] + edge + 1.0, t[1], t[2] + top - 0.5)


def test_random_tables_every_plan_clears_torso_and_table(table_twin, monkeypatch):
    # Every plan the planner RETURNS, through the real driver's slew: no frame puts the arm or
    # the hand into the torso or the table. Every refusal: a known reason, and advice.
    model, data = table_twin
    _pose(table_twin, {})
    feet = [
        g for g in range(model.ngeom) if model.geom_bodyid[g] and model.geom_size[g][0] == 0.005
    ]
    floor = min(data.geom_xpos[g][2] - 0.005 for g in feet)
    assert data.xpos[model.body("torso_link").id][2] - floor == pytest.approx(0.837, abs=2e-3)

    monkeypatch.setattr(m, "_measured_q", lambda side: list(HANGING[side]))
    rng = random.Random(20261111)
    plans, refused, hits = 0, collections.Counter(), {}
    for n in range(SCENES):
        side, target, table, height = _scene(rng)
        try:
            _arm, steps = m._plan_pick(side, side, target, "power", table)
        except m.PlanError as exc:
            assert exc.reason in m.PLAN_REASONS and exc.reason != "not_planned", (n, exc)
            assert exc.hint and set(exc.advice) & WALKS, (n, exc.reason, exc.advice)
            refused[exc.reason] += 1
            continue
        plans += 1
        _place_table(table_twin, table)
        driver = _SteppedDriver([*HANGING["left"], *HANGING["right"]])
        for q in _arm_qs(steps):
            angles = ArmAngles(*q)
            driver.command(*((angles, None) if side == "left" else (None, angles)))
        for f in getattr(driver._publisher, side):
            for mine, body, depth in _body_contacts(table_twin, side, f):
                key = f"scene {n} ({side}, table {height:.2f} m): {mine} in {body}"
                hits[key] = max(hits.get(key, 0.0), depth)
    print(f"\n{SCENES} table scenes: {plans} planned, refused {dict(refused)}")
    assert not hits, {k: f"{v * 1000:.1f} mm" for k, v in sorted(hits.items())}
    # The open hand (TABLE_CAPSULES) needs ~5 cm under the TCP: most of these low objects and
    # near edges refuse (1/20, 2/200 plan), so the floor is "some", not a tenth.
    assert plans >= max(1, SCENES // 100), "a sweep that plans nothing checks no trajectory"


def test_the_twin_table_catches_a_plan_that_ignores_it(table_twin):
    # The planner's view before supports: the cup's own z is the surface. With the real top 1 cm
    # under the cup's centre the hand goes into the twin's table; the table-aware plan refuses.
    target, table = PICK_TARGETS[0], (0.20, PICK_TARGETS[0][2] - 0.01)
    _place_table(table_twin, table)
    into = {
        f"{phase}: {mine}"
        for kind, phase, q in _pick(target)
        if kind == "arm"
        for mine, other, _ in _body_contacts(table_twin, "right", q)
        if other == "table"
    }
    assert any("wrist_yaw" in k for k in into), into
    with pytest.raises(m.PlanError) as err:
        m.plan_pick("right", target, "power", HANGING["right"], table)
    assert err.value.reason == "table_clearance"
