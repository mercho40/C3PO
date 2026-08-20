#!/usr/bin/env bash
# Install the Vosk Spanish model on the robot. Run ON the Jetson.
#
#     ssh c3po 'bash ~/c3po/apps/bridge/scripts/install_vosk.sh'
#
# The vosk PACKAGE comes from `uv sync --extra voice` (run_c3po does this) —
# this script only places the MODEL, which is not on PyPI.
#
# FETCHING IT FROM THE ROBOT IS THE SLOW PATH. Egress here measured ~7 KB/s to
# model hosts, which is over an hour for 39 MB; the same download from a laptop
# on the same campus runs ~500x faster. So if the zip is absent, push it from
# your machine instead of waiting:
#
#     curl -fLO https://alphacephei.com/vosk/models/vosk-model-small-es-0.42.zip
#     # then chunked-append it over — a single scp of this size stalls silently
#
# Idempotent: re-running verifies and skips work already done.

set -euo pipefail

DEST="${VOSK_DEST:-$HOME/.local/share/vosk}"
NAME="vosk-model-small-es-0.42"
ZIP="$DEST/vosk-model-small-es.zip"
URL="https://alphacephei.com/vosk/models/$NAME.zip"
EXPECT_BYTES=39817833

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mFATAL:\033[0m %s\n' "$*" >&2; exit 1; }

mkdir -p "$DEST"

if [ -d "$DEST/$NAME" ] && [ -f "$DEST/$NAME/am/final.mdl" ]; then
    say "model already installed at $DEST/$NAME"
else
    if [ ! -s "$ZIP" ] || [ "$(stat -c%s "$ZIP")" != "$EXPECT_BYTES" ]; then
        have=$([ -s "$ZIP" ] && stat -c%s "$ZIP" || echo 0)
        say "zip missing or incomplete ($have of $EXPECT_BYTES) — fetching (SLOW from here)"
        curl -fL -C - --retry 5 --connect-timeout 20 -o "$ZIP" "$URL" \
            || die "download failed — push the zip from a laptop instead, see the header"
        [ "$(stat -c%s "$ZIP")" = "$EXPECT_BYTES" ] \
            || die "zip is $(stat -c%s "$ZIP") bytes, expected $EXPECT_BYTES — re-run to resume"
    fi
    say "unpacking"
    command -v unzip >/dev/null || die "unzip is not installed"
    unzip -q -o "$ZIP" -d "$DEST"
    # The acoustic model is the file that actually has to be there; a partial
    # unzip leaves a directory that looks right and fails inside Vosk's C layer
    # with a message that names no path.
    [ -f "$DEST/$NAME/am/final.mdl" ] || die "unpacked, but $NAME/am/final.mdl is missing"
fi

# The BRIDGE's interpreter, not the system one. vosk is installed into the venv
# by `uv sync --extra voice`, so verifying with /usr/bin/python3 reports
# ModuleNotFoundError on a perfectly good install and sends you debugging the
# wrong thing.
PY_BIN="$(dirname "$(dirname "$(readlink -f "$0")")")/.venv/bin/python"
[ -x "$PY_BIN" ] || PY_BIN=python3

say "verifying the decoder loads and the grammar restricts ($PY_BIN)"
"$PY_BIN" - "$DEST/$NAME" <<'PY' || die "vosk could not load the model — run: uv sync --extra voice"
import json, sys
from vosk import KaldiRecognizer, Model
rec = KaldiRecognizer(Model(sys.argv[1]), 16000, json.dumps(["pará", "alto", "[unk]"]))
rec.AcceptWaveform(b"\x00\x00" * 8000)          # 0.5 s of silence
print("   decoder OK, silence ->", json.loads(rec.Result()).get("text", "") or "(nothing)")
PY

say "OK — model at $DEST/$NAME"
say "the bridge finds it by default; override with VOSK_MODEL"
say "NOTE: the mic multicast does not stream at rest, so this decodes nothing"
say "      live yet. Verify it with synthesised speech: pytest tests/test_listen.py"
