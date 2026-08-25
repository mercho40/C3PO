#!/usr/bin/env bash
# The compute-budget harness.  measure.sh <label> [seconds]
#
# A shim. The parsing and the verdicts live in apps/perception/bringup/measure.py,
# with tests, because a wrong tegrastats parser is hardest to notice in exactly
# the condition it is used in — a robot under sustained load — and the number it
# produces is the answer to "does this stack fit", which decides whether the
# failure mode takes stop_everything with it.
#
# It changes nothing: starts no container, stops no container, claims no sensor.
# Run it alongside whatever stage is already up.

set -euo pipefail

C3PO_DIR="${C3PO_DIR:-$HOME/c3po}"
export C3PO_DIR
export PYTHONPATH="$C3PO_DIR/apps/perception${PYTHONPATH:+:$PYTHONPATH}"

exec python3 -m bringup.measure "$@"
