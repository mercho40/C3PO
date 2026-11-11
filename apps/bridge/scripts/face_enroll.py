"""Enroll a person's face for the face gate (bridge/face_gate.py). Run ON the robot, as them.

    .venv/bin/python scripts/face_enroll.py --name Oliver --role superadmin
    .venv/bin/python scripts/face_enroll.py --name Ana --role operator
    .venv/bin/python scripts/face_enroll.py --list
    .venv/bin/python scripts/face_enroll.py --delete Ana
    .venv/bin/python scripts/face_enroll.py --delete-all

Look at the head camera, within 1.2 m. Needs the vision container serving /frame.jpg and
/depth_points (it holds the camera) and the models from scripts/install_face_models.sh.
Every frame must hold exactly one face, it must pass the same liveness check the gate uses,
and all frames must be the same person. Both roles pass the gate; the greeter says the
name. Enrolling a name that exists (any case) replaces that person. Writes
~/.c3po/face/people/<id>/ (0700): features.npy, person.json and photo.jpg, one small crop
of the face for the console (0600). Nothing leaves the robot but that photo, to the signed
console, and nothing about the face is printed or logged.
"""

from __future__ import annotations

import argparse
import sys

from bridge import face_gate

CONSENT = (
    "Face enrollment: this stores face feature vectors and ONE small photo of your face at "
    "{path}, on this robot. The photo is shown only in the C3PO console; nothing else leaves "
    "the robot. Remove both any time with --delete NAME.\n"
    "Registro facial: se guardan vectores de rasgos y UNA foto chica de tu cara en {path}, "
    "en este robot. La foto solo se muestra en la consola de C3PO; nada más sale del robot. "
    "Se borran con --delete NOMBRE."
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--frames", type=int, default=5, help="frames to enroll from (default 5)")
    parser.add_argument("--name", type=_name, help="the person's display name, for the greeting")
    parser.add_argument("--role", choices=face_gate.ROLES, default="operator")
    parser.add_argument("--list", action="store_true", help="list everyone enrolled")
    parser.add_argument("--delete", metavar="NAME", type=_name, help="forget this person")
    parser.add_argument("--delete-all", action="store_true", help="forget everyone")
    parser.add_argument("--yes", action="store_true", help="consent without the prompt")
    args = parser.parse_args(argv)

    if args.list:
        for p in face_gate.people():
            print(f"{p['name']}\t{p['role']}\t{p['id']}")
        return 0
    if args.delete_all:
        face_gate.delete_all()
        print(f"deleted everyone under {face_gate.FACE_DIR}")
        return 0
    if args.delete:
        face_gate.people()  # migrates the old layout first, so it can be deleted by name
        wanted = args.delete.casefold()
        ids = [p["id"] for p in face_gate.people_listing() if p["name"].casefold() == wanted]
        if not ids or not face_gate.delete_person(ids[0]):
            print(f"nobody enrolled as {args.delete}")
            return 0
        print(f"deleted {args.delete}")
        return 0
    if not args.name:
        parser.error("--name is required to enroll")

    print(CONSENT.format(path=face_gate.people_dir()))
    if not args.yes and input("¿Continuar? / Continue? [s/N] ").strip().lower() not in ("s", "y"):
        print("not enrolled")
        return 1
    try:
        face_gate.people()  # migrate first: enrolling the operator's name then replaces them
        p = face_gate.enroll(args.name, args.role, args.frames, say=print)
    except face_gate.Unavailable as exc:
        print(f"not enrolled: {exc}", file=sys.stderr)
        return 1
    print(f"enrolled: {p['name']} ({p['role']}) at {face_gate.people_dir() / p['id']}")
    return 0


def _name(raw: str) -> str:
    try:
        return face_gate.clean_name(raw)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(str(exc)) from None


if __name__ == "__main__":
    sys.exit(main())
