"""IK behaviour and plan_pick geometry, pure Python against the repo modules (read-only)."""
import math, random, statistics, sys, time

REPO = sys.argv[1]
sys.path.insert(0, f"{REPO}/apps/bridge/src")
from bridge import kinematics as k  # noqa: E402
from bridge.skills import manipulation as m  # noqa: E402

rng = random.Random(3)
LIM = {s: k.limits(s) for s in ("left", "right")}

def dist(a, b):
    return math.dist(a, b)

# ---- 1. IK on FK-generated targets over the FULL limit range -------------------------------
for side in ("right",):
    ok = okp = viol = 0
    times = []
    fails = []
    for _ in range(200):
        q = [rng.uniform(lo, hi) for lo, hi in LIM[side]]
        hp = k.forward(side, q)
        t = time.perf_counter()
        r = k.solve(side, hp.position, hp.pointing)
        times.append(time.perf_counter() - t)
        ok += r.reached
        if not r.reached:
            fails.append(round(r.position_error_m, 3))
        for v, (lo, hi) in zip(r.q, LIM[side]):
            if not lo - 1e-9 <= v <= hi + 1e-9:
                viol += 1
        r2 = k.solve(side, hp.position)
        okp += r2.reached
    print(f"[IK full-range] pos+dir reached {ok}/200, pos-only {okp}/200, limit violations {viol}")
    print(f"   solve time median {statistics.median(times)*1e3:.0f} ms, max {max(times)*1e3:.0f} ms; "
          f"failed pos errs (m) {sorted(fails)[-8:]}")

# ---- 2. reach: elbow-straight singularity and workspace edge ---------------------------------
sh = k.forward("right", [0.0] * 7).joint_origins[0]
print("[shoulder] right shoulder_pitch origin in torso", [round(v, 3) for v in sh])
best = max((k.forward("right", [0, 0, 0, e, 0, 0, 0]), e) for e in [i / 100 for i in range(-104, 209)]) \
    if False else None
rmax, emax = 0, 0
for e10 in range(-104, 210):
    e = e10 / 100
    hp = k.forward("right", [0, 0, 0, e, 0, 0, 0])
    d = dist(hp.position, sh)
    if d > rmax:
        rmax, emax = d, e
print(f"[reach] max shoulder->TCP {rmax:.3f} m at elbow={emax:.2f} rad (lower limit {k.arm_chain('right')[3].lower})")
# targets straight ahead of the shoulder at increasing distance, fingers forward
for d in (0.30, 0.38, 0.42, 0.45, 0.48, 0.50, 0.53):
    p = (sh[0] + d, sh[1], sh[2] - 0.15)
    r = k.solve("right", p, (1, 0, 0))
    rp = k.solve("right", p)
    print(f"   d={d:.2f} reached(dir)={r.reached} err={r.position_error_m*100:.1f}cm it={r.iterations} "
          f"elbow={r.q[3]:.2f} | pos-only reached={rp.reached}")

# ---- 3. plan_pick over the workspace box ------------------------------------------------------
SEEDS = {"hanging": [0.25, -0.15, 0.0, 1.0, 0.0, 0.0, 0.0], "none": None}
STEP = 0.6 / 50  # arm_sdk per-tick slew

def slew_path(q0, q1):
    """arm_sdk._slew: each joint independently at 0.012 rad/tick (not synchronised)."""
    cur = list(q0)
    path = [k.forward("right", cur).position]
    while any(abs(a - b) > 1e-9 for a, b in zip(cur, q1)):
        cur = [c + max(-STEP, min(STEP, t - c)) for c, t in zip(cur, q1)]
        path.append(k.forward("right", cur).position)
    return path

def seg_dev(path, a, b):
    ab = [b[i] - a[i] for i in range(3)]
    L2 = sum(v * v for v in ab) or 1e-12
    worst = 0
    for p in path:
        t = max(0, min(1, sum((p[i] - a[i]) * ab[i] for i in range(3)) / L2))
        c = [a[i] + t * ab[i] for i in range(3)]
        worst = max(worst, dist(p, c))
    return worst

for seed_name, seed in SEEDS.items():
    n = ok = 0
    jumps, devs, dips, through, tilts, rolls = [], [], [], [], [], []
    for x in [0.15 + 0.05 * i for i in range(9)]:
        for y in [-0.40 + 0.05 * i for i in range(10)]:
            for z in [-0.25 + 0.05 * i for i in range(13)]:
                tgt = (x, y, z)
                if m.in_workspace("right", tgt):
                    continue
                n += 1
                try:
                    steps = m.plan_pick("right", tgt, "power", seed)
                except m.PlanError:
                    continue
                ok += 1
                qs = [s[2] for s in steps if s[0] == "arm"]
                q_pre, q_grasp, q_lift = qs
                jumps.append(max(max(abs(a - b) for a, b in zip(q_pre, q_grasp)),
                                 max(abs(a - b) for a, b in zip(q_grasp, q_lift))))
                pre, gr = k.forward("right", q_pre).position, k.forward("right", q_grasp).position
                path = slew_path(q_pre, q_grasp)
                devs.append(seg_dev(path, pre, gr))
                d_appr = [(gr[i] - pre[i]) for i in range(3)]
                tilts.append(round(math.degrees(math.asin(max(-1, min(1, -d_appr[2] / (math.hypot(*d_appr) or 1))))) / 30) * 30)
                if seed is not None:
                    p0 = slew_path(seed, q_pre)
                    dips.append(min(p[2] for p in p0) - tgt[2])  # <0: hand went below object point
                    # does start->pre pass within 5 cm of the object point?
                    through.append(min(dist(p, tgt) for p in p0))
                # palm roll: angle of wrist_yaw +y about pointing, vs torso up
                rot_y = None
                hp = k.forward("right", q_grasp, tcp_m=0.0)
                hp1 = k.forward("right", q_grasp[:6] + (q_grasp[6],), tcp_m=0.0)
                rolls.append(q_grasp[4])
    print(f"[plan_pick seed={seed_name}] planned {ok}/{n} workspace grid points ({ok/n:.0%})")
    if jumps:
        print(f"   max |dq| between consecutive waypoints: median {statistics.median(jumps):.2f} rad, max {max(jumps):.2f} rad; "
              f">0.5 rad in {sum(j > 0.5 for j in jumps)} plans")
        print(f"   pre->grasp TCP deviation from straight line under per-joint slew: median {statistics.median(devs)*100:.1f} cm, "
              f"max {max(devs)*100:.1f} cm, >2 cm in {sum(d > 0.02 for d in devs)}/{len(devs)}")
        print(f"   approach tilt chosen (deg, rounded): {dict((t, tilts.count(t)) for t in sorted(set(tilts)))}")
    if dips:
        print(f"   start->pre: hand passes within 5 cm of the object point in {sum(t < 0.05 for t in through)}/{len(through)} plans; "
              f"dips >5 cm below object point in {sum(d < -0.05 for d in dips)}/{len(dips)}")

# ---- 4. free roll about the finger axis --------------------------------------------------------
# Same target + pointing, different seeds -> how different is the palm orientation?
def palm_axis(side, q):
    # wrist_yaw_link +y and +z in torso: rebuild rotation via FK of two TCP offsets is not
    # possible, so recompute with the module internals.
    rot = k._I3
    for j, a in zip(k.arm_chain(side), q):
        rot = k._mm(rot, j.rot)
        rot = k._mm(rot, k._axis_angle(j.axis, a))
    return tuple(rot[i][2] for i in range(3)), tuple(rot[i][1] for i in range(3))

tgt, d = (0.32, -0.18, 0.0), (1.0, 0.0, 0.0)
zs = []
for _ in range(25):
    s = [rng.uniform(lo, hi) * 0.5 for lo, hi in LIM["right"]]
    r = k.solve("right", tgt, d, seed=s)
    if r.reached:
        z, y = palm_axis("right", r.q)
        zs.append(math.degrees(math.atan2(z[1], z[2])))  # roll of link +z about torso x
print(f"[free roll] {len(zs)} solutions for one target+pointing; wrist_yaw_link +z roll about the finger axis "
      f"spans {min(zs):.0f}..{max(zs):.0f} deg")
# the default 3-seed path for a pick: what roll does plan_pick produce across targets?
rs = []
for y in (-0.30, -0.20, -0.10, 0.0):
    for z in (-0.15, 0.0, 0.15):
        try:
            st = m.plan_pick("right", (0.30, y, z), "power", None)
        except m.PlanError:
            continue
        zz, _ = palm_axis("right", st[2][2])
        rs.append(round(math.degrees(math.atan2(zz[1], zz[2]))))
print(f"   plan_pick (seed=None) grasp-pose link +z roll across 12 targets: {rs}")

# ---- 5. GRASP_TCP_M sensitivity --------------------------------------------------------------
q = m.plan_pick("right", (0.30, -0.15, 0.05), "power", None)[2][2]
for tcp in (0.05, 0.08, 0.10, 0.12):
    print(f"[tcp] if the true palm centre is {tcp:.2f} m, grasp pose puts it at "
          f"{[round(v, 3) for v in k.forward('right', q, tcp_m=tcp).position]} (target (0.32ish))")
