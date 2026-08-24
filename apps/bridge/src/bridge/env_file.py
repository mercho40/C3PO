"""Load `.env` the way a shell does, so the launcher does not have to.

WHY THE APP OWNS THIS. `run_c3po` exists largely to `set -a; . ./.env` before
starting the bridge, and that single responsibility is what forces the launcher
to be a shell script at all. Move it in here and the unit file can name the
interpreter directly, with no wrapper, no pidfile and no `nohup` — see
`docs/OPERATIONS.md`.

WHY NOT systemd's `EnvironmentFile=`, WHICH IS THE OBVIOUS ANSWER. Because it
does not parse this file the way `.` does, and the difference is silent. Our
own `.env.example` ships:

    SIM_MODE=stub                  # 'stub' | 'isaac' | 'mujoco_local' | 'real'

`EnvironmentFile` keeps everything after the `=`, so `SIM_MODE` would become
`stub                  # 'stub' | ...`, which is not `stub`, not `real`, and
not an error either. The bridge would decide it was on real hardware and try to
open DDS. A config loader that turns a comment into a mode is exactly the class
of failure this project keeps paying for, so the parsing lives here where it can
be tested against that exact line.

WHAT IT DOES NOT DO. It never overwrites a variable that is already set. The
environment a service is started with — systemd `Environment=`, a `SIM_MODE=x`
prefix on the command, whatever CI injects — must win over a file on disk, or
overriding anything means editing the robot's `.env` and remembering to put it
back. `.env` is the default, not the authority.
"""

from __future__ import annotations

import os
from pathlib import Path

__all__ = ["parse_env_file", "load_env_file"]


def _strip_inline_comment(value: str) -> str:
    """Drop a trailing ` # comment`, but only outside quotes.

    A `#` is only a comment when whitespace precedes it, which is the rule a
    shell follows and the reason `PASSWORD=a#b` keeps its hash.
    """
    out: list[str] = []
    quote: str | None = None
    prev_space = False
    for ch in value:
        if quote:
            out.append(ch)
            if ch == quote:
                quote = None
            prev_space = False
            continue
        if ch in ("'", '"'):
            quote = ch
            out.append(ch)
            prev_space = False
            continue
        if ch == "#" and (prev_space or not out):
            break
        out.append(ch)
        prev_space = ch.isspace()
    return "".join(out)


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        return value[1:-1]
    return value


def parse_env_file(text: str) -> dict[str, str]:
    """Pure: `.env` text in, name→value out. No I/O, no environment touched."""
    env: dict[str, str] = {}
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        # `export FOO=bar` is valid in a file meant to be sourced, and people
        # write it out of habit.
        if line.startswith("export "):
            line = line[len("export ") :].lstrip()
        name, sep, value = line.partition("=")
        if not sep:
            continue  # not an assignment; a shell would ignore it too
        name = name.strip()
        if not name or not (name[0].isalpha() or name[0] == "_"):
            continue
        env[name] = _unquote(_strip_inline_comment(value))
    return env


def load_env_file(path: str | os.PathLike[str], *, override: bool = False) -> dict[str, str]:
    """Apply `path` to `os.environ`. Returns what was actually set.

    Missing file is not an error: the bridge runs from a checkout without one in
    stub mode, and the caller that genuinely requires configuration should say
    so about the setting it needs, not about the file.
    """
    file = Path(path)
    try:
        text = file.read_text()
    except OSError:
        return {}

    applied: dict[str, str] = {}
    for name, value in parse_env_file(text).items():
        if not override and name in os.environ:
            continue
        os.environ[name] = value
        applied[name] = value
    return applied
