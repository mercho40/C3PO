#!/usr/bin/env bash
# Put the stack controls on PATH as run_c3po / stop_c3po / run_gemm / stop_gemm.
#
# Symlinks rather than copies, so `git pull` in the checkout updates the
# commands with no reinstall step. Run this on the robot.

set -euo pipefail

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
target="${BIN_DIR:-$HOME/.local/bin}"

mkdir -p "$target"

for cmd in run_c3po stop_c3po run_gemm stop_gemm; do
    chmod +x "$here/$cmd"
    ln -sf "$here/$cmd" "$target/$cmd"
    echo "  linked $target/$cmd -> $here/$cmd"
done
chmod +x "$here/_common.sh"

case ":$PATH:" in
    *":$target:"*)
        echo
        echo "Ready. Try: run_c3po"
        ;;
    *)
        echo
        echo "$target is not on your PATH. Add it:"
        echo "  echo 'export PATH=\"$target:\$PATH\"' >> ~/.bashrc && source ~/.bashrc"
        ;;
esac
