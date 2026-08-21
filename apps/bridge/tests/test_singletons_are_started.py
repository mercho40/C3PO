"""Every background singleton is actually started by somebody.

WHY THIS TEST EXISTS. The most expensive class of bug in this project is not a
wrong algorithm — it is a correct, tested component that nothing ever calls.
Five instances so far, and every one of them read from the outside as "not
built yet" rather than as a defect:

  * `PerceptionLink` was written and never `start()`ed, so the bridge had no
    participant on DDS domain 42. It produced three unrelated-looking symptoms:
    a 503 from the costmap route, `Subscription count: 0` on the cmd_vel gate,
    and `describe_surroundings` reporting perception offline. None of them
    pointed at the missing call.
  * `stop_everything` cancelled tasks but never closed the gate — "stop" meant
    "pause".
  * `describe_surroundings` hardcoded `detector_online=False` while the
    function that fills it in was never called.
  * `VoiceLoop` in `apps/back` was referenced by nothing but its own test.
  * `AudioMsgLink` — this one was FOUND BY WRITING THIS TEST. It parses
    `rt/audio_msg`, its `play_state` half was measured working to within ~100 ms
    and settles a documented open question, and `get_audio_msg_link()` had zero
    call sites anywhere in the source tree.

A unit test of the module cannot catch this, because the module is fine. Only
something that looks at the wiring can, so this reads the source rather than
importing it — no CycloneDDS, no robot, no network.

THE RULE. A `get_*()` singleton whose class defines `start()` is a background
worker: it does nothing until started, and code that fetches it later will get
an object that silently reports nothing. Such a factory must be satisfied by
one of three things, and anything else is a finding:

  1. `main()` starts it, or
  2. the factory starts it itself (`get_sampler` does this), or
  3. it is in EXEMPT below, with a reason someone wrote down.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "bridge"
SERVER = SRC / "mcp_server.py"

# Singletons that deliberately are not started at boot. Keep the reason with
# the entry: an exemption without one is how a real finding gets silenced.
#
# Empty on purpose. The first draft of this file pre-emptively exempted the
# teleop `get_driver` singletons on the theory that they are claimed per
# session — and the staleness check below rejected it on the first run, because
# those classes define no `start()` and so were never in scope at all. An
# exemption for a problem that does not exist is exactly the kind of entry that
# later hides one that does.
EXEMPT: dict[str, str] = {}


def _factories() -> list[tuple[str, str, Path, bool]]:
    """(factory, class, file, starts_itself) for every startable singleton."""
    found = []
    for path in sorted(SRC.rglob("*.py")):
        if "__pycache__" in str(path):
            continue
        src = path.read_text()
        for match in re.finditer(r"^def (get_\w+)\(\) -> (\w+)", src, re.M):
            factory, cls = match.group(1), match.group(2)
            body = re.search(rf"class {cls}\b.*?(?=\nclass |\Z)", src, re.S)
            if body is None or "def start(self" not in body.group(0):
                continue
            fn_body = re.search(
                rf"^def {factory}\(\).*?(?=\n(?:def |class )|\Z)", src, re.S | re.M
            )
            starts_itself = bool(fn_body and ".start()" in fn_body.group(0))
            found.append((factory, cls, path, starts_itself))
    return found


def _main_body() -> str:
    src = SERVER.read_text()
    return src[src.index("def main() -> None:") :]


def test_the_scan_finds_something():
    """Guards the test: a regex that matched nothing would pass everything."""
    assert len(_factories()) >= 3


@pytest.mark.parametrize(
    "factory,cls,path,starts_itself",
    _factories(),
    ids=lambda v: v if isinstance(v, str) else "",
)
def test_startable_singleton_is_wired(factory, cls, path, starts_itself):
    if factory in EXEMPT:
        pytest.skip(f"exempt: {EXEMPT[factory]}")
    if starts_itself:
        return  # the factory guarantees it; nothing to wire

    main = _main_body()
    assert f"{factory}()" in main and ".start()" in main, (
        f"{cls} defines start() but {factory}() is never started in main().\n"
        f"  defined in: {path.relative_to(SRC.parent.parent)}\n"
        "  A background singleton that is never started returns an object that\n"
        "  reports nothing, forever, with no error — see this file's header for\n"
        "  the five times that has already happened here.\n"
        "  Fix it by starting it in main(), starting it in the factory, or\n"
        "  adding it to EXEMPT with a reason."
    )


def test_every_exemption_still_refers_to_something():
    """A stale exemption is a hole nobody can see."""
    live = {f for f, _, _, _ in _factories()}
    for factory in EXEMPT:
        assert factory in live, (
            f"EXEMPT names {factory}, which no longer exists as a startable "
            "singleton. Remove the entry so the exemption list stays readable."
        )
