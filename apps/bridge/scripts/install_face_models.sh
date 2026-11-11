#!/usr/bin/env bash
# Fetch the face gate's two models (bridge/face_gate.py) onto the robot. Run ON the Jetson.
#
#     ssh c3po 'bash ~/c3po/apps/bridge/scripts/install_face_models.sh'
#
# YuNet (detection, 0.2 MB) and SFace (recognition, 37 MB) from opencv/opencv_zoo, pinned
# to one commit and checked by sha256. They run on cv2.FaceDetectorYN / FaceRecognizerSF,
# which the bridge venv's OpenCV already has: no new Python dependency. Outside git.
#
# Idempotent: re-running verifies and re-fetches only what is missing or wrong.

set -euo pipefail

DEST="${C3PO_FACE_DIR:-$HOME/.c3po/face}/models"
# opencv_zoo main on 2026-10-20; YuNet last changed in f12e127, SFace in ba91a3b.
ZOO_REV="48b10fa505aeb51fbd127fc60a5c660040e823b7"
ZOO_URL="https://github.com/opencv/opencv_zoo/raw/$ZOO_REV/models"
PINS=(
    "face_detection_yunet/face_detection_yunet_2023mar.onnx 8f2383e4dd3cfbb4553ea8718107fc0423210dc964f9f4280604804ed2552fa4"
    "face_recognition_sface/face_recognition_sface_2021dec.onnx 0ba9fbfa01b5270c96627c4ef784da859931e02f04419c829e83484087c34e79"
)

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mFATAL:\033[0m %s\n' "$*" >&2; exit 1; }
sha256_of() { sha256sum "$1" | cut -d' ' -f1; }

# The face directory also holds the operator's enrollment: owner-only.
mkdir -p "$DEST"
chmod 700 "$(dirname "$DEST")" "$DEST"

for pin in "${PINS[@]}"; do
    rel="${pin% *}" want="${pin#* }"
    target="$DEST/$(basename "$rel")"

    if [ -s "$target" ] && [ "$(sha256_of "$target")" = "$want" ]; then
        say "verified: $(basename "$rel")"
        continue
    fi

    say "fetching $(basename "$rel")"
    # Into a .part first: a cut connection must not leave a half model under the real name.
    curl -fL --retry 5 --retry-delay 2 --connect-timeout 20 -o "$target.part" "$ZOO_URL/$rel" \
        || die "download failed — check egress to github.com, then re-run"
    [ "$(sha256_of "$target.part")" = "$want" ] \
        || { rm -f "$target.part"; die "$(basename "$rel") does not match its pinned sha256 — check $ZOO_URL/$rel"; }
    mv "$target.part" "$target"
done

PY="${PYTHON:-$(dirname "$0")/../.venv/bin/python}"
if [ -x "$PY" ]; then
    say "loading both with the bridge's OpenCV"
    C3PO_FACE_DIR="$(dirname "$DEST")" "$PY" -c 'from bridge import face_gate; face_gate.models()' \
        || die "the models are on disk but the bridge's OpenCV could not load them"
fi

say "OK — models in $DEST"
say "next: enroll the operator with scripts/face_enroll.py, then set C3PO_FACE_GATE"
