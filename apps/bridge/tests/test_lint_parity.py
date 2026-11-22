"""CI's ruff and mypy check exactly what `bun run lint` / `check-types` check.

The bridge job has uv and no bun, so it cannot call the package scripts; it
spells the same commands out. They had drifted: package.json linted
`src tests`, CI linted `src tests scripts` plus two root scripts, so a local
`lint` passed what CI then refused. This keeps the two spellings one list.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

BRIDGE = Path(__file__).resolve().parents[1]
CI = BRIDGE.parents[1] / ".github" / "workflows" / "ci.yml"


def _package_args(script: str) -> list[str]:
    cmd = json.loads((BRIDGE / "package.json").read_text())["scripts"][script]
    prefix = "uv run --project . "
    assert cmd.startswith(prefix), cmd
    return cmd[len(prefix) :].split()


def _ci_args(step: str) -> list[str]:
    """The folded `run: >` of the bridge job's step named `step`, after `uv run`."""
    text = CI.read_text()
    m = re.search(
        rf"- name: {step}\n\s+working-directory: apps/bridge\n\s+run: >\n((?:\s{{10}}\S.*\n)+)",
        text,
    )
    assert m, f"no `{step}` step with working-directory apps/bridge in ci.yml"
    words = m.group(1).split()
    assert words[:2] == ["uv", "run"], words
    return words[2:]


def test_ci_ruff_is_the_package_lint():
    assert _ci_args("ruff") == _package_args("lint")


def test_ci_mypy_is_the_package_check_types():
    assert _ci_args("mypy") == _package_args("check-types")
