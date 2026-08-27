"""No process lookup in the robot scripts may match the shell that runs it.

`pgrep -f` matches whole command lines, so it finds the process asking the
question. Over SSH that is a `bash -c ...` whose arguments contain the very
pattern being searched for, and the match looks exactly like a real find.

It has now fired in both directions on this robot:

  * 2026-08-26, `run_c3po` REFUSED TO START, reporting "a bridge process exists
    outside c3po-bridge.service: 15861". 15861 was the ssh command line running
    the check. A safety guard blocking a legitimate start.
  * `stop_c3po` reports the same match as a "leftover" bridge — the same false
    positive pointed the other way, inviting somebody to kill their own shell.

`_common.sh` already knew: `_is_self_or_ancestor` exists and has a comment
saying the false positive "cost real debugging time". It was simply not applied
to the bridge and teleop lookups, which are the ones every run_/stop_ script
uses. So this is a wiring test, not a logic one — the cure was written and left
unconnected, which is the failure mode this repo keeps meeting.

Text, not execution: these are shell functions, and the point is that no NEW
call site can appear without the filter.
"""

from __future__ import annotations

import re

from conftest import REPO_ROOT

COMMON_SH = REPO_ROOT / "scripts" / "robot" / "_common.sh"

#: The one function allowed to call pgrep, because it does the filtering.
FILTER_FN = "_pids_matching"


def _code_lines() -> list[tuple[int, str]]:
    """Numbered lines with comments and blanks removed."""
    out = []
    for n, raw in enumerate(COMMON_SH.read_text().splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if line:
            out.append((n, line))
    return out


def test_the_file_is_there_and_looks_like_shell():
    """Guards the test: a moved file would make everything below vacuous."""
    assert COMMON_SH.exists(), f"{COMMON_SH} is gone — this test is now blind"
    assert "pgrep" in COMMON_SH.read_text(), "no pgrep at all; has this been rewritten?"


def test_only_the_filter_calls_pgrep_dash_f():
    """Every COMMAND-LINE lookup goes through the self/ancestor filter.

    Scoped to `pgrep -f` deliberately. `pgrep -P <pid>` selects the children of
    a given process and cannot match the caller by its arguments, so the
    process-tree walk in `_kill_tree` is not a hazard and must not be flagged —
    a test that cried wolf about it would get the whole file disabled.
    """
    offenders = [
        (n, line) for n, line in _code_lines() if re.search(r"\bpgrep\s+-f\b", line)
    ]
    # The filter's own call is the single legitimate one.
    unfiltered = [
        (n, line)
        for n, line in offenders
        if not re.search(r'pgrep -f "\$1"', line)
    ]
    assert not unfiltered, (
        "these lines call pgrep directly instead of going through "
        f"{FILTER_FN}():\n"
        + "\n".join(f"  {COMMON_SH.name}:{n}  {line}" for n, line in unfiltered)
        + "\nA bare `pgrep -f` matches the shell asking the question. Route it "
        f"through {FILTER_FN} so self and ancestors are dropped."
    )


def test_the_filter_and_its_helper_still_exist():
    """Renaming either without updating the other would pass the test above
    while removing the protection entirely."""
    src = COMMON_SH.read_text()
    assert f"{FILTER_FN}()" in src, f"{FILTER_FN} is gone; the lookups are unguarded"
    assert "_is_self_or_ancestor()" in src, "the ancestor walk is gone"
    assert f"_is_self_or_ancestor" in src.split(f"{FILTER_FN}()", 1)[1][:400], (
        f"{FILTER_FN} no longer calls _is_self_or_ancestor — it is a plain "
        "pgrep wrapper now, which is the bug with extra steps."
    )


def test_every_lookup_helper_routes_through_the_filter():
    """The four the run_/stop_ scripts actually call."""
    src = COMMON_SH.read_text()
    for fn in (
        "running_bridge_pids",
        "stray_bridge_pids",
        "stray_teleop_pids",
        "other_commander_pids",
    ):
        body = src.split(f"{fn}() {{", 1)
        assert len(body) == 2, f"{fn} no longer exists"
        assert FILTER_FN in body[1][:300], (
            f"{fn}() does not use {FILTER_FN}. Every run_/stop_ script depends "
            "on it, and a false positive there either blocks a legitimate start "
            "or names somebody's own shell as a process to kill."
        )
