"""(1) Wall time of plan_pick (it runs synchronously inside async pick()). (2) Straight-line
deviation of pre->grasp under per-joint slew (what arm_sdk does) vs synchronised joint interp."""
import math, statistics, sys, time
REPO = sys.argv[1]
sys.path.insert(0, f"{REPO}/apps/bridge/src")
from bridge import kinematics as k  # noqa: E402
from bridge.skills import manipulation as m  # noqa: E402

for tgt in [(0.30, -0.15, 0.05), (0.50, -0.30, -0.25), (0.45, -0.05, 0.30), (0.20, -0.40, -0.25)]:
    t = time.perf_counter()
    try:
        m.plan_pick("right", tgt, "power", [0.25, -0.15, 0.0, 1.0, 0.0, 0.0, 0.0])
        res = "planned"
    except m.PlanError:
        res = "PlanError"
    print(f"plan_pick{tgt}: {res} in {(time.perf_counter()-t)*1e3:.0f} ms (Mac, blocking the event loop)")

STEP = 0.012
def fk(q): return k.forward("right", q).position
def dev(path, a, b):
    ab = [b[i]-a[i] for i in range(3)]; L2 = sum(v*v for v in ab) or 1e-12
    return max(math.dist(p, [a[i]+max(0, min(1, sum((p[j]-a[j])*ab[j] for j in range(3))/L2))*ab[i] for i in range(3)]) for p in path)
per, syn, cart = [], [], []
for x in (0.25, 0.32, 0.40):
    for y in (-0.30, -0.20, -0.10, 0.0):
        for z in (-0.15, -0.05, 0.05, 0.15):
            try:
                st = m.plan_pick("right", (x, y, z), "power", None)
            except m.PlanError:
                continue
            q0, q1 = st[1][2], st[2][2]
            a, b = fk(q0), fk(q1)
            cur, path = list(q0), [a]
            while any(abs(c-t) > 1e-9 for c, t in zip(cur, q1)):
                cur = [c + max(-STEP, min(STEP, t-c)) for c, t in zip(cur, q1)]; path.append(fk(cur))
            per.append(dev(path, a, b))
            syn.append(dev([fk([u+(v-u)*s/50 for u, v in zip(q0, q1)]) for s in range(51)], a, b))
            # Cartesian sub-waypoints every 1 cm, IK seeded from the previous one, then sync interp
            d = [b[i]-a[i] for i in range(3)]; n = max(1, int(math.dist(a, b)/0.01))
            qs, q = [q0], list(q0)
            for s in range(1, n+1):
                r = k.solve("right", [a[i]+d[i]*s/n for i in range(3)], k.forward("right", q1).pointing, seed=q)
                q = list(r.q); qs.append(q)
            pts = []
            for qa, qb in zip(qs, qs[1:]):
                pts += [fk([u+(v-u)*s/10 for u, v in zip(qa, qb)]) for s in range(11)]
            cart.append(dev(pts, a, b))
print(f"pre->grasp max deviation from the straight line over {len(per)} plans:")
for name, v in (("per-joint slew (current)", per), ("synchronised joint interp", syn), ("1 cm Cartesian sub-waypoints", cart)):
    print(f"   {name:30s} median {statistics.median(v)*100:.1f} cm, max {max(v)*100:.1f} cm")
