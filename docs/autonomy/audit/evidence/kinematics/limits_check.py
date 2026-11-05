"""Do manipulation's IK solutions stay inside retarget.LIMITS (the tight software envelope that
move_arm clamps to)? manipulation sends IK q straight to the driver without that clamp."""
import math, sys
REPO = sys.argv[1]
sys.path.insert(0, f"{REPO}/apps/bridge/src")
from bridge import kinematics as k  # noqa: E402
from bridge.skills import manipulation as m  # noqa: E402
from bridge.teleop.retarget import JOINT_NAMES, JOINT_SIGNS, LIMITS  # noqa: E402

print("URDF limits (deg):", {j.name.replace('right_', ''): (round(math.degrees(j.lower)), round(math.degrees(j.upper))) for j in k.arm_chain('right')})
print("retarget.LIMITS (deg, semantic):", {n: tuple(round(math.degrees(v)) for v in LIMITS[n]) for n in JOINT_NAMES})
n = out = 0
worst = {}
for x in (0.20, 0.28, 0.36, 0.44):
    for y in (-0.35, -0.25, -0.15, -0.05, 0.05):
        for z in (-0.20, -0.10, 0.0, 0.10, 0.20):
            try:
                steps = m.plan_pick("right", (x, y, z), "power", None)
            except m.PlanError:
                continue
            for s in steps:
                if s[0] != "arm":
                    continue
                n += 1
                bad = False
                for name, q in zip(JOINT_NAMES, s[2]):
                    sem = q * JOINT_SIGNS["right"][name]  # wiring -> semantic (signs are +-1)
                    lo, hi = LIMITS[name]
                    if not lo - 1e-6 <= sem <= hi + 1e-6:
                        bad = True
                        over = max(lo - sem, sem - hi)
                        worst[name] = max(worst.get(name, 0), math.degrees(over))
                out += bad
print(f"{out}/{n} planned arm waypoints fall outside retarget.LIMITS; worst excess per joint (deg): "
      f"{ {kk: round(v) for kk, v in worst.items()} }")
