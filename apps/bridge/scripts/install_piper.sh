#!/usr/bin/env bash
# Install Piper + the es_AR voice on the robot. Run ON the Jetson.
#
#     ssh c3po 'bash ~/c3po/apps/bridge/scripts/install_piper.sh'
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
VOICE_URL="https://huggingface.co/rhasspy/piper-voices/resolve/main/es/es_AR/daniela/high"
RELEASE="https://github.com/rhasspy/piper/releases/download/2023.11.14-2"
TARBALL="piper_linux_aarch64.tar.gz"

say() { printf '\033[1;36m==>\033[0m %s\n' "$*"; }
die() { printf '\033[1;31mFATAL:\033[0m %s\n' "$*" >&2; exit 1; }

[ "$(uname -m)" = "aarch64" ] || die "this fetches an aarch64 build; uname -m says $(uname -m)"

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

# A TRUNCATED MODEL IS THE FAILURE MODE THAT ACTUALLY HAPPENS HERE, so size is
# checked against the SERVER's Content-Length rather than a magic minimum. The
# model is ~109 MB over a slow campus link, and an interrupted ssh session takes
# the curl down with it — this went wrong once already, leaving 72 MB of a
# 114 MB file. Any "is it bigger than 10 MB" check calls that healthy; piper
# then dies at synthesis with `Model config doesn't exist` or an opaque
# onnxruntime parse error, neither of which points at the download.
#
# `curl -C -` resumes, so a re-run after an interruption continues rather than
# restarting the whole transfer.
for ext in onnx onnx.json; do
    target="$DEST/$VOICE.$ext"
    url="$VOICE_URL/$VOICE.$ext"

    remote="$(curl -sIL --max-time 30 "$url" \
        | awk 'tolower($1)=="content-length:"{n=$2} END{gsub(/\r/,"",n); print n}')"
    [ -n "$remote" ] || die "could not read Content-Length for $VOICE.$ext — check egress to huggingface.co"

    if [ -s "$target" ] && [ "$(stat -c%s "$target")" = "$remote" ]; then
        say "voice file complete: $VOICE.$ext ($remote bytes)"
        continue
    fi

    if [ -s "$target" ]; then
        say "resuming $VOICE.$ext ($(stat -c%s "$target") of $remote bytes)"
    else
        say "fetching $VOICE.$ext ($remote bytes)"
    fi
    curl -fL -C - --retry 5 --retry-delay 2 --connect-timeout 20 -o "$target" "$url" \
        || die "voice download failed — re-run this script, it resumes"

    got="$(stat -c%s "$target")"
    [ "$got" = "$remote" ] || die "$VOICE.$ext is $got bytes, expected $remote — re-run to resume"
done

say "verifying end to end (synthesising to /dev/null, nothing is played)"
printf 'Hola, soy C3PO.' | "$DEST/piper" --model "$DEST/$VOICE.onnx" --output-raw > /tmp/piper-check.raw 2>/tmp/piper-check.err \
    || { cat /tmp/piper-check.err >&2; die "piper ran but exited non-zero"; }

raw_size="$(stat -c%s /tmp/piper-check.raw)"
[ "$raw_size" -gt 1000 ] || die "piper produced only $raw_size bytes of audio"
rm -f /tmp/piper-check.raw /tmp/piper-check.err

say "OK — piper at $DEST/piper, voice $VOICE, produced ${raw_size} bytes of 22050 Hz PCM"
say "the bridge finds these by default; override with PIPER_BIN / PIPER_VOICE"
say "NOTE: nothing was played. Audibility is only confirmed by a person in the room."
