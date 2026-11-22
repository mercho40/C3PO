"""apps/perception/.env.example documents exactly what ~/.c3po/perception.env may set.

perception_up keeps only `PERSISTENT_ENV_KEYS` from that file and warns about the
rest, so the example lists those names, no more and no fewer, and copying it to
the robot as it is changes nothing.
"""
from __future__ import annotations

import re
from pathlib import Path

from bringup.cli import read_env_file
from bringup.spec import PERSISTENT_ENV_KEYS

EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"


def test_the_example_names_every_persistent_key_and_nothing_else() -> None:
    # A whole `# KEY=` or `# KEY=default` line; prose that mentions KEY=… is not one.
    names = re.findall(r"^#\s*([A-Z][A-Z0-9_]*)=\S*\s*$", EXAMPLE.read_text(), re.MULTILINE)
    assert sorted(names) == sorted(PERSISTENT_ENV_KEYS)


def test_the_example_parses_and_sets_nothing_as_shipped() -> None:
    assert read_env_file(str(EXAMPLE)) == {}
