"""Compare bridge.kinematics FK / camera mount against pinocchio on the same URDF."""
import math, random, sys, time
import numpy as np
import pinocchio as pin

REPO = sys.argv[1]
sys.path.insert(0, f"{REPO}/apps/bridge/src")
from bridge import kinematics as k  # noqa: E402

model = pin.buildModelFromUrdf(str(k.URDF_PATH))
data = model.createData()
fid = lambda n: model.getFrameId(n)
TORSO = fid("torso_link")

def jidx(name):
    return model.joints[model.getJointId(name)].idx_q

print("pin nq", model.nq, "njoints", model.njoints)

def pose_in_torso(q_full, frame):
    pin.framesForwardKinematics(model, data, q_full)
    return data.oMf[TORSO].inverse() * data.oMf[fid(frame)]

rng = random.Random(1)
worst = {}
for side in ("left", "right"):
    chain = k.arm_chain(side)
    # limits check
    for j in chain:
        pj = model.getJointId(j.name)
        lo, hi = model.lowerPositionLimit[jidx(j.name)], model.upperPositionLimit[jidx(j.name)]
        assert abs(lo - j.lower) < 1e-9 and abs(hi - j.upper) < 1e-9, (j.name, lo, hi, j.lower, j.upper)
    pe = de = re_ = 0.0
    for trial in range(100):
        q7 = [rng.uniform(j.lower, j.upper) for j in chain]
        qf = pin.neutral(model)
        # random waist + other arm too, to prove torso frame independence
        for n in ("waist_yaw_joint", "waist_roll_joint", "waist_pitch_joint"):
            qf[jidx(n)] = rng.uniform(-0.3, 0.3)
        for j, v in zip(chain, q7):
            qf[jidx(j.name)] = v
        M = pose_in_torso(qf, f"{side}_wrist_yaw_link")
        tcp = M.translation + M.rotation[:, 0] * k.GRASP_TCP_M
        hp = k.forward(side, q7)
        pe = max(pe, np.linalg.norm(tcp - np.array(hp.position)))
        de = max(de, np.linalg.norm(M.rotation[:, 0] - np.array(hp.pointing)))
        # also joint origins vs pin joint placements
        pin.forwardKinematics(model, data, qf)
        for j, o in zip(chain, hp.joint_origins):
            oj = (data.oMf[TORSO].inverse() * data.oMi[model.getJointId(j.name)]).translation
            re_ = max(re_, np.linalg.norm(oj - np.array(o)))
    worst[side] = (pe, de, re_)
    print(f"{side}: max TCP pos err {pe:.2e} m, max pointing err {de:.2e}, max joint-origin err {re_:.2e} m")

# camera
qf = pin.neutral(model)
for n in ("waist_yaw_joint", "waist_roll_joint", "waist_pitch_joint"):
    qf[jidx(n)] = 0.2
M = pose_in_torso(qf, "d435_link")
print("d435_link in torso:", np.round(M.translation, 5), "pitch deg", round(math.degrees(pin.rpy.matrixToRpy(M.rotation)[1]), 3))
cerr = 0.0
# optical -> link (REP-103): link = R_lo * opt with R_lo columns
R_lo = np.array([[0, 0, 1], [-1, 0, 0], [0, -1, 0]], float)
for _ in range(100):
    p = np.array([rng.uniform(-0.5, 0.5), rng.uniform(-0.5, 0.5), rng.uniform(0.2, 2.0)])
    ref = M.translation + M.rotation @ (R_lo @ p)
    got = np.array(k.camera_to_torso(p))
    cerr = max(cerr, np.linalg.norm(ref - got))
print(f"camera_to_torso max err vs pin d435_link+REP103 optical: {cerr:.2e} m")
# sanity: optical +z is forward-down, +x is robot right, +y down
for name, v in (("+z", (0, 0, 1)), ("+x", (1, 0, 0)), ("+y", (0, 1, 0))):
    a = np.array(k.camera_to_torso(v)) - np.array(k.camera_to_torso((0, 0, 0)))
    print("optical", name, "-> torso", np.round(a, 3))
print("lens height in torso", np.round(k.camera_to_torso((0, 0, 0)), 4))

# FK speed (pure python), relevant for IK cost on Jetson
t = time.perf_counter()
for _ in range(2000):
    k.forward("right", [0.1] * 7)
print(f"pure-python forward(): {(time.perf_counter()-t)/2000*1e6:.0f} us/call on this Mac")
