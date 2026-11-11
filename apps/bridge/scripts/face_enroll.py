"""Enroll the operator's face for the face gate (bridge/face_gate.py). Run ON the robot, as the operator.

    .venv/bin/python scripts/face_enroll.py            # look at the head camera, within 1.5 m
    .venv/bin/python scripts/face_enroll.py --delete   # forget the operator

Needs the vision container serving /frame.jpg and /depth_box (it holds the camera) and the
models from scripts/install_face_models.sh. Every frame must hold exactly one face, and it
must pass the same liveness check the gate uses. Only the SFace feature vectors are
written, to ~/.c3po/face/operator.npy (0600); no image is kept, nothing leaves the robot,
and nothing about the face is printed or logged. Re-running replaces the operator.
"""

from __future__ import annotations

import argparse
import sys
import time

from bridge import face_gate

CONSENT = (
    "Face gate enrollment: this stores ONLY face feature vectors (no images) at {path}, on this "
    "robot, never sent anywhere. Remove them any time with --delete.\n"
    "Registro facial: se guardan SOLO vectores de rasgos (ninguna imagen) en {path}, en este "
    "robot, sin enviarse a ningún lado. Se borran con --delete."
)
#: Pause between accepted frames, so the vectors are not all the same instant.
INTERVAL_S = 0.4


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--frames", type=int, default=5, help="frames to enroll from (default 5)")
    parser.add_argument("--delete", action="store_true", help="delete the enrolled operator")
    parser.add_argument("--yes", action="store_true", help="consent without the prompt")
    args = parser.parse_args(argv)
    path = face_gate.enrollment_path()

    if args.delete:
        try:
            path.unlink()
        except FileNotFoundError:
            print(f"nothing enrolled at {path}")
            return 0
        print(f"deleted {path}")
        return 0

    print(CONSENT.format(path=path))
    if not args.yes and input("¿Continuar? / Continue? [s/N] ").strip().lower() not in ("s", "y"):
        print("not enrolled")
        return 1

    features: list[object] = []
    for attempt in range(1, args.frames * 4 + 1):
        if len(features) == args.frames:
            break
        try:
            img, stamp = face_gate.fresh_frame()
            rows = face_gate.faces(img)
            if len(rows) == 1:
                why = face_gate.liveness(rows[0], stamp)
            else:
                why = f"{len(rows)} faces, need exactly one"
        except (face_gate.Unavailable, OSError) as exc:  # URLError is an OSError
            print(f"cannot enroll: {exc}", file=sys.stderr)
            return 1
        if why is not None:
            print(f"frame {attempt}: skipped, {why}")
            continue
        features.append(face_gate.feature(img, rows[0]))
        print(f"frame {attempt}: ok ({len(features)}/{args.frames})")
        time.sleep(INTERVAL_S)

    if len(features) < args.frames:
        print(
            f"not enrolled: only {len(features)} of {args.frames} frames had one live face",
            file=sys.stderr,
        )
        return 1
    print(f"enrolled: {face_gate.save_enrollment(features)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
