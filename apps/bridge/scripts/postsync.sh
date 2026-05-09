#!/usr/bin/env bash
# Re-applies local patches to dependencies after `uv sync`.
# Idempotent — safe to run anytime.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRIDGE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
INIT_PY="$BRIDGE_DIR/.venv/lib/python3.12/site-packages/unitree_sdk2py/__init__.py"

if [[ ! -f "$INIT_PY" ]]; then
    echo "postsync: $INIT_PY not found — did you run 'uv sync'?" >&2
    exit 1
fi

# Patch: upstream __init__.py imports `b2` which isn't shipped. Strip the reference.
if grep -q "from . import idl, utils, core, rpc, go2, b2" "$INIT_PY"; then
    cat > "$INIT_PY" <<'EOF'
# Patched by apps/bridge/scripts/postsync.sh — upstream __init__.py imports `b2`,
# but the b2 submodule isn't shipped. Strip it locally. Track upstream fix as
# bridge tech debt (long-term: fork unitree_sdk2_python).
from . import idl, utils, core, rpc, go2

__all__ = [
    "idl",
    "utils",
    "core",
    "rpc",
    "go2",
]
EOF
    echo "postsync: patched unitree_sdk2py/__init__.py (removed b2 import)"
else
    echo "postsync: unitree_sdk2py/__init__.py already patched (or upstream changed)"
fi
