#!/usr/bin/env bash
# Everything that has to be true before the headset goes on.
#
#   ./scripts/preflight.sh [--quick]
#
# A shim. The checks live in apps/bridge/src/bridge/preflight.py, with tests,
# because the 344 lines this replaced read apps/web/.env with
# `cut -d= -f2- | tr -d '"' | xargs` (which truncates any value with a space in
# it and keeps inline comments as part of the URL) and read the camera's /status
# by substring — the same technique that had c3po_health reporting a closed
# motion gate for an armed one.
#
# SYSTEM python3 and stdlib only: a preflight check should survive a checkout
# with no venv rather than trip on it.

set -uo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/.." && pwd)"

export C3PO_DIR="${C3PO_DIR:-$repo}"
export PYTHONPATH="$repo/apps/bridge/src${PYTHONPATH:+:$PYTHONPATH}"

exec python3 -m bridge.preflight "$@"
