#!/usr/bin/env bash
# Verifies that `uv sync` produced a COMPLETE unitree_sdk2py install.
# Idempotent, read-only, safe to run anytime.
#
# This used to patch `unitree_sdk2py/__init__.py` to strip its `import b2`,
# because b2 was not shipped. That was treating a symptom. The real cause:
# upstream's setup.py uses `find_packages()`, and several package directories
# had no `__init__.py`, so pip silently installed NONE of them — b2, comm, g1,
# h1, h2. One packaging bug, four consequences:
#
#   - `import unitree_sdk2py` raised ModuleNotFoundError on b2 (hence the patch)
#   - no `comm.motion_switcher` — we hand-rolled CheckMode against the raw RPC client
#   - no `g1.audio` — which is why `say` was a stub
#   - no `g1.loco` — we rebuilt LocoClient by hand from the vendor's C++ header
#
# Upstream added the missing `__init__.py` files. Pinning past that commit fixes
# all four at once and made the patch unnecessary, so it is gone.
#
# What remains is the check the patch should always have been: fail loudly if
# the install is incomplete again. A silently-missing subpackage costs hours —
# it presents as "the SDK doesn't support that", not as a broken install.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BRIDGE_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SITE="$BRIDGE_DIR/.venv/lib/python3.12/site-packages/unitree_sdk2py"

if [[ ! -d "$SITE" ]]; then
    echo "postsync: $SITE not found — did you run 'uv sync'?" >&2
    exit 1
fi

# Subpackages the bridge relies on, or intends to. `core`/`rpc`/`idl` are the
# transport; `comm` carries motion_switcher, which is the first thing to call
# when the robot accepts commands and does nothing; `g1` carries the loco, arm
# and audio clients.
REQUIRED=(core rpc idl utils go2 comm g1)
missing=()
for pkg in "${REQUIRED[@]}"; do
    [[ -d "$SITE/$pkg" ]] || missing+=("$pkg")
done

if [[ ${#missing[@]} -gt 0 ]]; then
    echo "postsync: INCOMPLETE unitree_sdk2py install — missing: ${missing[*]}" >&2
    echo "postsync: upstream setup.py uses find_packages(); a directory without" >&2
    echo "postsync: an __init__.py is skipped silently. Check whether the pinned" >&2
    echo "postsync: commit regressed, and prefer a commit at or after 65691c8." >&2
    exit 1
fi

# The OTHER half of the same packaging bug, still unfixed upstream: setup.py has
# no package_data and there is no MANIFEST.in, so utils/lib/crc_{amd64,aarch64}.so
# are omitted from every non-editable install. crc.py loads them with
# ctypes.CDLL() on Linux and falls back to pure Python everywhere else — so this
# is invisible on a Mac and raises OSError on the Jetson, the moment anything
# constructs CRC(). That is the rt/lowcmd and rt/arm_sdk path.
#
# The file IS in the source uv checked out — it is only the *install* that drops
# it. So recover it from uv's own cache rather than warning and moving on:
# same git checkout, same commit, same provenance as the install itself.
# Verified on the Jetson 2026-08-20, where the missing .so was what stood
# between us and the arm path.
#
# Only warn if no source can be found. The RPC clients we use for locomotion
# and gestures never touch CRC, so this must never block a bridge start — it
# only has to stop being a surprise the first time someone drives an arm.
if [[ "$(uname -s)" == "Linux" ]]; then
    arch="$(uname -m)"
    case "$arch" in
        x86_64) so="crc_amd64.so" ;;
        aarch64) so="crc_aarch64.so" ;;
        *) so="" ;;
    esac
    if [[ -n "$so" && ! -f "$SITE/utils/lib/$so" ]]; then
        # -newer ordering is not worth it: this is a handful of bytes of CRC32
        # that has not changed upstream in years. Any copy from the cache beats
        # no copy at all.
        src="$(find "${UV_CACHE_DIR:-$HOME/.cache/uv}" -path "*/unitree_sdk2py/utils/lib/$so" \
               -type f 2>/dev/null | head -1)"
        if [[ -n "$src" ]]; then
            mkdir -p "$SITE/utils/lib"
            cp "$src" "$SITE/utils/lib/$so"
            echo "postsync: restored utils/lib/$so from $src"
        else
            echo "postsync: WARNING — $SITE/utils/lib/$so is missing." >&2
            echo "postsync: upstream ships no package_data, so the CRC library is not" >&2
            echo "postsync: installed, and no copy was found in uv's cache to restore." >&2
            echo "postsync: Harmless for the RPC clients, but CRC() will raise OSError —" >&2
            echo "postsync: which blocks any rt/lowcmd or rt/arm_sdk work." >&2
        fi
    fi
fi

# The import itself is the real test — a directory can exist and still fail.
if ! "$BRIDGE_DIR/.venv/bin/python" -c "import unitree_sdk2py" 2>/dev/null; then
    echo "postsync: unitree_sdk2py present but does not import." >&2
    "$BRIDGE_DIR/.venv/bin/python" -c "import unitree_sdk2py" 2>&1 | tail -3 >&2
    exit 1
fi

echo "postsync: unitree_sdk2py install complete (${REQUIRED[*]})"
