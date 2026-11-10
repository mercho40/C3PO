#!/usr/bin/env bash
# Install Piper + the es_AR voice on the robot. Run ON the Jetson.
#
#     ssh c3po 'bash ~/c3po/apps/bridge/scripts/install_piper.sh'
#
# CI runs it too (.github/workflows/ci.yml, the `voice` job), on an x86_64
# ubuntu runner, so the spoken-stop tests decode real synthesised speech there.
# Same release, same voice, same paths; only the binary's architecture differs.
#
# Why a binary and not `pip install piper-tts`: the pip package pulls
# onnxruntime, which on aarch64/JetPack means either a wheel that does not exist
# for this Python or a CUDA build that drags the bridge venv toward the vision
# container. The prebuilt binary bundles its own runtime and espeak-ng data,
# needs no Python at all, and is what D6.2 chose.
#
# Idempotent: re-running verifies and re-fetches only what is missing.

set -euo pipefail

DEST="${PIPER_DEST:-$HOME/.local/share/piper}"
# es_AR/daniela, high. The accent is the point — D6.2 took the 22050->16000
# resampler deliberately rather than ship a Spain accent to Argentine students.
VOICE="es_AR-daniela-high"
# Pinned: a commit of rhasspy/piper-voices (the voice files last changed in
# 375a0fe, 2025-06-25), and each file's sha256.
VOICE_REV="e7229f7d33a10e145e109b886d661984201ac3c4"
VOICE_URL="https://huggingface.co/rhasspy/piper-voices/resolve/$VOICE_REV/es/es_AR/daniela/high"
VOICE_ONNX_SHA256=7ceb1fc0dab349418c5b54a639ae9ee595212d7c9ea422220d8419163d5cc985
VOICE_JSON_SHA256=aedbf69647e1d754c62ecf8e0366ca5f16af3e768e3c6b5329af6eb6bde3852b
RELEASE="https://github.com/rhasspy/piper/releases/download/2023.11.14-2"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mFATAL:\033[0m %s\n' "$*" >&2; exit 1; }

# The Jetson is aarch64; the CI runner is x86_64. Release 2023.11.14-2 ships a
# Linux build for both and nothing else this script has been run on.
case "$(uname -s)-$(uname -m)" in
    Linux-aarch64) TARBALL="piper_linux_aarch64.tar.gz" ;;
    Linux-x86_64) TARBALL="piper_linux_x86_64.tar.gz" ;;
    *) die "no piper build chosen for $(uname -s) $(uname -m) — aarch64 (robot) and x86_64 (CI) Linux only" ;;
esac

mkdir -p "$DEST"

if [ -x "$DEST/piper" ]; then
    say "piper binary already present at $DEST/piper"
else
    say "fetching $TARBALL"
    tmp="$(mktemp -d)"
    trap 'rm -rf "$tmp"' EXIT
    curl -fL --retry 3 --connect-timeout 20 -o "$tmp/$TARBALL" "$RELEASE/$TARBALL" \
        || die "download failed — the robot is on the campus LAN; check egress to github.com"
    # The tarball's top-level directory is itself named `piper`, so extracting
    # into DEST's PARENT lands the binary at $DEST/piper alongside its libs.
    tar -xzf "$tmp/$TARBALL" -C "$(dirname "$DEST")"
    [ -x "$DEST/piper" ] || die "extracted, but $DEST/piper is missing or not executable"
fi

# A TRUNCATED MODEL IS THE FAILURE MODE THAT ACTUALLY HAPPENS HERE. The model
# is ~114 MB over a slow campus link, and an interrupted ssh session takes the
# curl down with it — this went wrong once already, leaving 72 MB of a 114 MB
# file. piper then dies at synthesis with `Model config doesn't exist` or an
# opaque onnxruntime parse error, neither of which points at the download.
#
# So each file is checked against its pinned sha256, not its size: `curl -C -`
# resumes onto whatever is there, and a resume onto another revision's file
# ends at the right size with the wrong bytes. A file that fails the hash after
# a resume is fetched once more from scratch.
sha256_of() { sha256sum "$1" | cut -d' ' -f1; }

for pin in "onnx $VOICE_ONNX_SHA256" "onnx.json $VOICE_JSON_SHA256"; do
    ext="${pin% *}" want="${pin#* }"
    target="$DEST/$VOICE.$ext"
    url="$VOICE_URL/$VOICE.$ext"

    if [ -s "$target" ] && [ "$(sha256_of "$target")" = "$want" ]; then
        say "voice file verified: $VOICE.$ext"
        continue
    fi

    if [ -s "$target" ]; then say "resuming $VOICE.$ext"; else say "fetching $VOICE.$ext"; fi
    rc=0
    curl -fL -C - --retry 5 --retry-delay 2 --connect-timeout 20 -o "$target" "$url" || rc=$?
    # 22 or 33: the server refused the range, so what is there is not a prefix of this file.
    case "$rc" in 0 | 22 | 33) ;; *) die "voice download failed — re-run this script, it resumes" ;; esac

    if [ "$(sha256_of "$target")" != "$want" ]; then
        say "$VOICE.$ext does not match its sha256 — fetching it again from scratch"
        rm -f "$target"
        curl -fL --retry 5 --retry-delay 2 --connect-timeout 20 -o "$target" "$url" \
            || die "voice download failed — re-run this script, it resumes"
        [ "$(sha256_of "$target")" = "$want" ] \
            || die "$VOICE.$ext does not match its pinned sha256 even fetched whole — check $url"
    fi
done

say "verifying end to end (synthesising to /dev/null, nothing is played)"
printf 'Hola, soy C3PO.' | "$DEST/piper" --model "$DEST/$VOICE.onnx" --output-raw > /tmp/piper-check.raw 2>/tmp/piper-check.err \
    || { cat /tmp/piper-check.err >&2; die "piper ran but exited non-zero"; }

raw_size="$(stat -c%s /tmp/piper-check.raw)"
[ "$raw_size" -gt 1000 ] || die "piper produced only $raw_size bytes of audio"
rm -f /tmp/piper-check.raw /tmp/piper-check.err

say "OK — piper at $DEST/piper, voice $VOICE, produced ${raw_size} bytes of 22050 Hz PCM"
say "the bridge finds these by default; override with PIPER_BIN / PIPER_VOICE"
say "NOTE: nothing was played here. The synthesis path itself is proven — Spanish was"
say "      heard through the robot's speaker on 2026-08-21."
