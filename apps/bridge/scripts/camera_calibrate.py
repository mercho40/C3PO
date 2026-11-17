"""Measure the head camera's mount against a tape, and print it for both the vision container and
the bridge. Run ON the robot, standing still in its normal stance, with perception up:

    .venv/bin/python scripts/camera_calibrate.py --reset
    .venv/bin/python scripts/camera_calibrate.py --label bottle --ahead-m 0.45 --left-m -0.15 --height-m 0.82
    .venv/bin/python scripts/camera_calibrate.py --label bottle --ahead-m 0.90 --left-m 0.00 --height-m 0.05
    .venv/bin/python scripts/camera_calibrate.py --solve

Offline (cam_xyz from a log, torso height taped by hand), the same file and the same fit:

    uv run python scripts/camera_calibrate.py --cam 0.05 0.12 0.71 --torso-height-m 0.80 \\
        --ahead-m 0.45 --left-m -0.15 --height-m 0.82

THE TAPE. cam_xyz is a depth reading of the surface the camera sees, so tape to the middle of
the object's NEAR FACE (a box square to the robot is best; a bottle's front, not its axis), from
the floor point midway between the robot's two ankle joints: --ahead-m along the way the feet
point, --left-m to the ROBOT'S OWN left (positive; its right, which is your left facing it, is
negative), --height-m above the floor (half the object's height if it stands on the floor). Each run adds one sample to --samples and refits all of them. Take 3+
at different distances (0.4-1.2 m) and heights: one sample fits pitch and z exactly and so shows
no error; two at ranges 0.3 m apart or more also fit x.

TORSO HEIGHT. Live, from rt/lowstate's 12 leg + 3 waist angles through the URDF (pelvis -> both
feet, feet assumed flat on a level floor); that also knows how far the torso leans ahead of the
ankles. --torso-height-m (torso_link origin above the floor, about the waist motor) skips lowstate
and assumes the torso upright above the ankles' midpoint. It prints which it used.

Nothing here moves the robot: it reads two DDS topics and writes one file.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
from pathlib import Path
from typing import Any

from bridge import camera_cal
from bridge.env_file import load_env_file

DEFAULT_SAMPLES = Path.home() / ".c3po" / "camera_cal.jsonl"

#: A detection whose frames spread more than this is two objects or bad depth: refuse it.
MAX_SPREAD_M = 0.03

#: Past these from the CAD mount the tape, the object or the label is more likely wrong than the
#: bracket.
SANE_PITCH_DEG = 15.0
SANE_Z_M = 0.10


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    src = parser.add_mutually_exclusive_group()
    src.add_argument("--label", help="live: the detection label to measure, e.g. bottle")
    src.add_argument(
        "--cam",
        nargs=3,
        type=float,
        metavar=("X", "Y", "Z"),
        help="offline: the detection's cam_xyz (optical: x right, y down, z ahead)",
    )
    src.add_argument("--solve", action="store_true", help="refit the samples file, add nothing")
    src.add_argument("--reset", action="store_true", help="delete the samples file")
    parser.add_argument("--ahead-m", type=float, help="tape: forward from between the ankles")
    parser.add_argument("--left-m", type=float, help="tape: to the robot's left (+) / right (-)")
    parser.add_argument("--height-m", type=float, help="tape: object centre above the floor")
    parser.add_argument(
        "--torso-height-m",
        type=float,
        help="torso_link above the floor, instead of reading the legs",
    )
    parser.add_argument("--frames", type=int, default=20, help="live frames to average (20)")
    parser.add_argument(
        "--samples",
        type=Path,
        default=DEFAULT_SAMPLES,
        help=f"the samples file (default {DEFAULT_SAMPLES})",
    )
    args = parser.parse_args(argv)

    # The same mount the bridge would use, and its DDS settings.
    load_env_file(Path(__file__).resolve().parents[1] / ".env")

    if args.reset:
        args.samples.unlink(missing_ok=True)
        print(f"deleted {args.samples}")
        return 0
    if not args.solve:
        if args.label is None and args.cam is None:
            parser.error("give --label (live), --cam (offline), --solve or --reset")
        if None in (args.ahead_m, args.left_m, args.height_m):
            parser.error("a sample needs --ahead-m, --left-m and --height-m")
        if args.cam is not None and args.torso_height_m is None:
            parser.error("offline (--cam) needs --torso-height-m")
        try:
            sample = _sample(args)
        except RuntimeError as exc:
            print(f"refused: {exc}", file=sys.stderr)
            return 1
        args.samples.parent.mkdir(parents=True, exist_ok=True)
        with args.samples.open("a") as f:
            f.write(json.dumps(sample) + "\n")
        print(f"sample {_count(args.samples)} saved to {args.samples}")
    if not args.samples.exists():
        print(f"no samples in {args.samples}", file=sys.stderr)
        return 1
    samples = [json.loads(line) for line in args.samples.read_text().splitlines() if line]
    print(report(samples))
    return 0


def _count(path: Path) -> int:
    return sum(1 for line in path.read_text().splitlines() if line)


def _sample(args: argparse.Namespace) -> dict[str, Any]:
    tape = [args.ahead_m, args.left_m, args.height_m]
    out: dict[str, Any] = {"at": time.strftime("%Y-%m-%dT%H:%M:%S"), "tape": tape}
    if args.cam is not None:
        out["cam"] = list(args.cam)
    else:
        out.update(capture_cam(args.label, args.frames))
    if args.torso_height_m is not None:
        out["torso_height_m"] = args.torso_height_m
    else:
        out["q"] = capture_q()
    return out


def report(samples: list[dict[str, Any]]) -> str:
    """The fit over all samples, as the operator reads it."""
    pairs = []
    lines = []
    for i, s in enumerate(samples, 1):
        q = s.get("q")
        m = camera_cal.tape_to_torso(s["tape"], q=q, torso_height_m=s.get("torso_height_m"))
        pairs.append((s["cam"], m))
        if q is not None:
            t = camera_cal.torso_in_tape(q)
            how = f"legs: torso {t[2]:.3f} m up, {t[0]:+.3f} m ahead of the ankles"
        else:
            how = f"--torso-height-m {s['torso_height_m']:.3f}"
        lines.append(f"  #{i} tape {_v(s['tape'])} -> torso_link {_v(m)}  ({how})")
    xyz0, pitch0, yaw0 = camera_cal.current_mount()
    fit = camera_cal.fit(pairs)
    before = camera_cal.residuals(pairs, xyz0, pitch0, yaw0)
    x, y, z, pitch, yaw = fit.env()
    lines.append("")
    lines.append("  residual, measured - camera (m):   now (current mount)        fitted")
    for i, (b, a) in enumerate(zip(before, fit.residuals_m, strict=True), 1):
        lines.append(f"  #{i}  {_v(b)} |{_n(b):.3f}|   {_v(a)} |{_n(a):.3f}|")
    lines.append("")
    held = [n for n in ("x", "y", "yaw") if n not in fit.fitted]
    lines.append(
        f"  fitted {', '.join(fit.fitted)}; held {', '.join(held)}. pitch {fit.pitch_deg:.2f} deg "
        f"(now {pitch0:.2f}), torso_link xyz {_v(fit.xyz)} (now {_v(xyz0)})"
    )
    if len(samples) == 1:
        lines.append(
            "  ONE sample: pitch and z fit its x and z exactly, so it shows no error. Take 3+."
        )
    if abs(fit.pitch_deg - pitch0) > SANE_PITCH_DEG or abs(fit.xyz[2] - xyz0[2]) > SANE_Z_M:
        lines.append(
            f"  !! more than {SANE_PITCH_DEG:.0f} deg or {SANE_Z_M:.2f} m from the current mount: "
            "check the tape, the label and that you taped the near face before applying."
        )
    lines.append("")
    lines.append("  Put these five lines in BOTH ~/.c3po/perception.env (vision) and")
    lines.append("  ~/c3po/apps/bridge/.env (arm), replacing any C3PO_CAMERA_* there:")
    lines.append("")
    lines.append(f"C3PO_CAMERA_X_M={x:.5f}")
    lines.append(f"C3PO_CAMERA_Y_M={y:.5f}")
    lines.append(f"C3PO_CAMERA_Z_M={z:.5f}")
    lines.append(f"C3PO_CAMERA_PITCH_DEG={pitch:.2f}")
    lines.append(f"C3PO_CAMERA_YAW_DEG={yaw:.2f}")
    lines.append("")
    lines.append(
        "  and C3PO_PICK_Z_OFFSET_M=0 in apps/bridge/.env: the measured mount replaces it."
    )
    lines.append("  Apply: `c3po up` (vision) and `c3po restart` (bridge). Undo: delete the lines,")
    lines.append("  same two commands. (pelvis frame, waist at zero; URDF d435_joint equivalent:")
    lines.append(
        f'  xyz="{fit.xyz[0]:.5f} {fit.xyz[1]:.5f} {fit.xyz[2]:.5f}" '
        f'rpy="0 {math.radians(fit.pitch_deg):.6f} {math.radians(fit.yaw_deg):.6f}")'
    )
    return "\n".join(lines)


def _v(v: Any) -> str:
    return "(" + ", ".join(f"{float(c):+.3f}" for c in v) + ")"


def _n(v: Any) -> float:
    return math.sqrt(sum(float(c) ** 2 for c in v))


# --- live: the robot's DDS. Not unit-tested: it needs the robot. ---------------------------------


def capture_cam(label: str, frames: int, timeout_s: float = 15.0) -> dict[str, Any]:
    """Mean cam_xyz of `label` over `frames` distinct captures (the most confident per report)."""
    from bridge.sdk.perception_link import PerceptionLink

    # Read-only: never armed, and its velocity forward is a no-op besides.
    link = PerceptionLink(forward=lambda *_: None)
    link.start()
    seen: dict[float, list[float]] = {}
    labels: set[str] = set()
    deadline = time.monotonic() + timeout_s
    while len(seen) < frames and time.monotonic() < deadline:
        report, _ = link.latest_report()
        objs = [o for o in (report or {}).get("objects") or [] if isinstance(o, dict)]
        labels.update(str(o.get("label")) for o in objs)
        match = [o for o in objs if o.get("label") == label and isinstance(o.get("cam_xyz"), list)]
        if report and match:
            best = max(match, key=lambda o: float(o.get("confidence") or 0))
            at = round(float(report["stamp_unix"]) - float(best.get("age_s") or 0), 3)
            seen[at] = [float(v) for v in best["cam_xyz"]]
        time.sleep(0.05)
    if not seen:
        raise RuntimeError(
            f"no {label!r} with a cam_xyz in {timeout_s:.0f} s; seen: {sorted(labels) or 'nothing'}"
        )
    pts = list(seen.values())
    mean = [statistics.fmean(p[i] for p in pts) for i in range(3)]
    spread = max(statistics.pstdev(p[i] for p in pts) for i in range(3))
    print(f"{label}: {len(pts)} frames, cam_xyz {_v(mean)}, spread {spread:.3f} m")
    if spread > MAX_SPREAD_M:
        raise RuntimeError(
            f"spread {spread:.3f} m > {MAX_SPREAD_M} m: two {label}s, or depth unstable. "
            "Leave one, on a matte background, and retry."
        )
    return {"cam": mean, "label": label, "frames": len(pts), "spread_m": spread}


def capture_q(frames: int = 50, timeout_s: float = 5.0) -> dict[str, float]:
    """Mean leg + waist angles (radians, by URDF joint name) from rt/lowstate."""
    from bridge.sdk.connection import init_dds

    init_dds(
        robot_host=os.environ.get("ROBOT_HOST", "127.0.0.1"),
        domain_id=int(os.environ.get("DDS_DOMAIN_ID", "0")),
        interface=os.environ.get("DDS_INTERFACE") or None,
    )
    from unitree_sdk2py.core.channel import ChannelSubscriber
    from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowState_

    from bridge.sdk.state import LOWSTATE_TOPIC

    got: list[list[float]] = []
    modes: set[int] = set()

    def on(msg: Any) -> None:
        modes.add(int(msg.mode_pr))
        got.append([float(msg.motor_state[i].q) for i in range(15)])

    sub = ChannelSubscriber(LOWSTATE_TOPIC, LowState_)
    sub.Init(on, 10)
    deadline = time.monotonic() + timeout_s
    while len(got) < frames and time.monotonic() < deadline:
        time.sleep(0.02)
    if not got:
        raise RuntimeError("no rt/lowstate in 5 s: is the bridge's DDS up? Or use --torso-height-m")
    if modes != {0}:
        # shortcut: AB mode reports the two ankle motors, not pitch/roll; no conversion here.
        raise RuntimeError(f"lowstate mode_pr {sorted(modes)}, not PR (0): use --torso-height-m")
    names = camera_cal.LEG_WAIST_JOINTS
    q = {n: statistics.fmean(row[i] for row in got) for i, n in enumerate(names)}
    t = camera_cal.torso_in_tape(q)
    print(f"legs: {len(got)} lowstate frames, torso {t[2]:.3f} m above the floor")
    return q


if __name__ == "__main__":
    sys.exit(main())
