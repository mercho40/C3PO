"""What range_m does the vision container report for a person, given its 0-deg default pitch,
when the camera is really pitched 47.6 deg down (URDF)? Uses the repo's own grounding math."""
import math, sys
REPO = sys.argv[1]
sys.path.insert(0, f"{REPO}/apps/perception/vision")
from c3po_vision import grounding as g  # noqa: E402

CAM_H = 1.25  # lens height above floor, m (estimate: pelvis ~0.79 + torso->d435 0.43)
PITCH = 47.6
VFOV = 42.0  # D435 colour, deg
for H in (0.3, 0.5, 0.6, 0.7, 0.8):
    # visible slice of a person standing H m in front (vertical body at horizontal H)
    lo_ang, hi_ang = PITCH - VFOV / 2, PITCH + VFOV / 2  # deg below horizontal
    top = max(0.0, min(1.75, CAM_H - H * math.tan(math.radians(lo_ang))))
    bot = max(0.0, CAM_H - H * math.tan(math.radians(min(hi_ang, 89))))
    zc = (top + bot) / 2  # box centre height
    ang = math.atan2(CAM_H - zc, H) - math.radians(PITCH)  # below optical axis, rad
    slant = math.hypot(H, CAM_H - zc)
    p_opt = (0.0, slant * math.sin(ang), slant * math.cos(ang))
    r, *_ = g.range_bearing_from_base(*g.camera_to_base(p_opt))
    print(f"person {H:.1f} m away (visible {bot:.2f}-{top:.2f} m tall): reported range_m={r:.2f} "
          f"-> {'ABORT' if r < 0.8 else 'no abort'} at PERSON_REACH_M=0.8")
