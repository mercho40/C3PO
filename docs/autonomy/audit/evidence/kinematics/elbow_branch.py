"""How often do plan_pick waypoints use the elbow past its straight (max-reach) angle ~1.45 rad?"""
import sys
sys.path.insert(0, f"{sys.argv[1]}/apps/bridge/src")
from bridge.skills import manipulation as m
n = past = 0; mx = -9
for x in (0.20, 0.28, 0.36, 0.44, 0.50):
    for y in (-0.40, -0.30, -0.20, -0.10, 0.0, 0.10):
        for z in (-0.25, -0.15, -0.05, 0.05, 0.15, 0.25, 0.35):
            try: st = m.plan_pick("right", (x, y, z), "power", None)
            except m.PlanError: continue
            for s in st:
                if s[0] == "arm":
                    n += 1; e = s[2][3]; mx = max(mx, e); past += e > 1.45
print(f"{past}/{n} waypoints have elbow > 1.45 rad (past straight); max elbow {mx:.2f} rad")
