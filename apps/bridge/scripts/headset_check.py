"""Supervised verification of what the headset actually SHOWS.

Run this with the Quest ON somebody's head and /vr-control open in it, against
a bridge already running onboard and reached over the SSH tunnel:

    scripts/quest_setup.sh                                   # forwards the ports
    ssh -N -L 8001:127.0.0.1:8001 -o ControlMaster=no c3po    # another terminal
    uv run python scripts/headset_check.py

WHY THIS EXISTS
---------------
`vr_smoke_test.py` verifies that the VR path MOVES THE ROBOT. Nothing verifies
what the operator SEES, and that is where every unverified claim in this
project currently sits. As of 2026-08-27 the list is: immersive mode, the panel
position, the readiness banner, joystick walking, the 8-second alert band, the
lidar radar, the camera picture, and the per-eye stereo fix — eight changes,
none of them observed, several of them shipped on consecutive days on top of
each other.

Wearing a headset and remembering eight things is how five of them get called
"fine". So they are enumerated here, each with what to look for and what a NO
actually means.

THE PART THAT IS NOT A CHECKLIST, AND IS THE WHOLE POINT
--------------------------------------------------------
Every visual check that depends on data ASKS THE BRIDGE FIRST.

"I cannot see the radar" and "I cannot see the camera" have been reported three
times between 2026-08-21 and 2026-08-27, and the cause was different every
time: a clip-space placement outside the lens cone, a QoS mismatch that meant
no ring was ever published, and a port the headset never had forwarded. Two of
those three were not rendering bugs at all — and from inside the headset they
looked identical to one.

So a check whose data is not arriving is recorded BLOCKED, never FAIL, and the
operator is told which it is before being asked what they see. A FAIL from this
script means "the data was there and the headset did not show it", which is a
claim worth acting on. That distinction is the difference between fixing a
shader and re-running a setup script.

NOTHING HERE COMMANDS THE ROBOT. Every probe is a GET against a read-only
telemetry route. The two checks that involve motion ask the operator to drive,
because a script that walks a humanoid while somebody's eyes are covered is not
a thing this repo is going to grow.
"""

from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Callable, Optional

DEFAULT_BRIDGE = "http://127.0.0.1:8001"

#: Answers the prompt accepts, and what each one means in the report.
PASS = "PASS"
FAIL = "FAIL"
BLOCKED = "BLOCKED"
SKIPPED = "SKIPPED"


@dataclass
class Probe:
    """One read-only GET, and what its answer means for a visual check.

    `summarize` turns the decoded body into a single line an operator can act
    on. It receives `None` when the request failed outright, which is a
    different fact from a 503 with a reason in it and is reported as such.
    """

    name: str
    path: str
    summarize: Callable[[Optional[dict], Optional[int]], "ProbeResult"]


@dataclass
class ProbeResult:
    """Whether the DATA is there, and one line saying how we know."""

    data_present: bool
    detail: str


@dataclass
class Check:
    """One thing the operator has to look at, and what a NO would mean."""

    id: str
    title: str
    #: Exactly what to look for. Written to be read out loud to somebody whose
    #: eyes are covered, so: no jargon, no file paths, one observable thing.
    look_for: str
    #: What a NO means and what it invalidates. Never empty — a check whose
    #: failure has no consequence is a question not worth asking somebody
    #: wearing a headset.
    if_no: str
    #: Which claim this closes out. Goes in the report so the run can be pasted
    #: into a commit that flips something from unverified to verified.
    closes: str
    #: Optional data precondition. When it says the data is absent, the check
    #: is BLOCKED and the operator is not asked to judge a renderer that was
    #: given nothing to render.
    probe: Optional[str] = None
    #: Inverts the probe: this check only makes sense when the data is ABSENT.
    #:
    #: The two placeholder checks — the SIN IMAGEN card, the radar dial saying
    #: why it is empty — are questions about what the headset does with
    #: NOTHING. Asking them while a picture is happily arriving gets a "no"
    #: that means "there was no reason to show one", which is a pass recorded
    #: as a failure. So they are skipped, and the report says why.
    only_when_absent: bool = False


@dataclass
class Outcome:
    check: Check
    verdict: str
    note: str = ""
    probe_detail: str = ""


# --------------------------------------------------------------------------
# Probes. Read-only, and each one answers "is the data actually arriving?"
# --------------------------------------------------------------------------


def summarize_camera(body: Optional[dict], status: Optional[int]) -> ProbeResult:
    """Is a picture being served, and by which of the two possible servers?

    `/camera/status` reports BOTH sources on purpose, so a dark feed says which
    server was asked and what it answered. Reproduced here rather than reduced
    to a boolean, because "videohub is dark and the vision container is not
    running" is the sentence that ends the search.
    """
    if body is None:
        return ProbeResult(
            False,
            f"no answer from the bridge's /camera/status (HTTP {status})"
            " — the tunnel or the bridge is down, not the headset",
        )
    live = bool(body.get("live"))
    source = body.get("source") or body.get("serving") or "unknown"
    frames = body.get("frames")
    if live:
        return ProbeResult(True, f"live from {source}, frames={frames}")
    hint = body.get("hint") or "no hint given"
    return ProbeResult(False, f"NOT live (source={source}): {hint}")


def summarize_scan(body: Optional[dict], status: Optional[int]) -> ProbeResult:
    """Is there a lidar ring, and is it fresh?

    A stale ring is DATA PRESENT. The headset is supposed to draw an old ring
    dimmed rather than blank it, so a stale ring is exactly the case where the
    operator's eyes are the instrument — blocking here would skip the check
    that matters most.
    """
    if body is None:
        return ProbeResult(False, f"no answer from /telemetry/scan (HTTP {status})")
    if status == 503:
        hint = body.get("hint") or body.get("error") or "no reason given"
        return ProbeResult(False, f"bridge has no ring: {hint}")
    # `r_cm` IS THE FIELD, AND THIS LOOKED FOR TWO NAMES THAT DO NOT EXIST.
    #
    # The first version read `ranges_cm` / `ranges`, invented from reading
    # `scan_ring.py` rather than from a reply. `/telemetry/scan` sends `r_cm`,
    # which `apps/web`'s `ScanRing` type has always had right. So a healthy
    # 105-bearing ring came back as "present but EMPTY", and the radar check
    # was recorded BLOCKED — this script committing, about itself, the exact
    # confusion it was written to end.
    #
    # It survived because the payloads captured from the robot on 2026-08-28
    # were the 503 "no scan received" case; the success case was never looked
    # at until the lidar was actually running on 2026-08-29.
    ranges = body.get("r_cm") or body.get("ranges_cm") or body.get("ranges") or []
    filled = sum(1 for r in ranges if r)
    frame = body.get("frame") or "?"
    stale = " STALE" if body.get("stale") else ""
    if not filled:
        return ProbeResult(
            False,
            f"ring present but EMPTY ({len(ranges)} bearings, none filled)"
            " — an empty dial is not a rendering question",
        )
    return ProbeResult(True, f"{filled}/{len(ranges)} bearings in {frame}{stale}")


def summarize_gate(body: Optional[dict], status: Optional[int]) -> ProbeResult:
    """Can the bridge tell us why the robot will or will not move?

    The readiness banner is derived from this. If the bridge cannot say, the
    banner has nothing to show and its blankness is honest.
    """
    if body is None:
        return ProbeResult(False, f"no answer from /telemetry/gate (HTTP {status})")
    return ProbeResult(True, json.dumps(body, sort_keys=True)[:160])


PROBES = {
    "camera": Probe("camera", "/camera/status", summarize_camera),
    "scan": Probe("scan", "/telemetry/scan", summarize_scan),
    "gate": Probe("gate", "/telemetry/gate", summarize_gate),
}


# --------------------------------------------------------------------------
# The checks. Order matters: it is the order somebody can actually do them in
# without taking the headset off between two of them.
# --------------------------------------------------------------------------

CHECKS = [
    Check(
        id="immersive",
        title="Immersive mode — everything except the picture is black",
        look_for=(
            "With the session running: is the area around the camera picture"
            " solid BLACK, rather than showing your room or the browser?"
        ),
        if_no=(
            "The session fell back to passthrough (immersive-ar) or the DOM"
            " overlay is still painting its background. Environment mode was"
            " asked for on 2026-08-24 and has never been observed."
        ),
        closes="feat(vr): real environment mode — 2026-08-24, unverified",
    ),
    Check(
        id="camera_picture",
        title="The camera picture is there",
        look_for="Do you see the robot's camera view, roughly centred?",
        if_no=(
            "If the probe above said the feed IS live, this is the headset's"
            " problem and the port fix did not land. If it said NOT live, the"
            " picture was never sent and this is not a rendering fault."
        ),
        closes="fix(quest): camera port 8001 forwarded — 2026-08-27, unverified",
        probe="camera",
    ),
    Check(
        id="camera_single",
        title="ONE camera picture, not two",
        look_for=(
            "Close one eye, then the other. Then open both: does the picture"
            " merge into a SINGLE image, or do you see two side by side?"
        ),
        if_no=(
            "The per-eye stereo projection is still wrong. The camera quad had"
            " the same clip-space bug as the panel and the radar; it was not"
            " named in the 2026-08-27 report only because it was not visible."
        ),
        closes="fix(vr): one clip-space point for two eyes — 2026-08-27, unverified",
        probe="camera",
    ),
    Check(
        id="no_signal_card",
        title="With no camera, the headset SAYS why",
        look_for=(
            "Do you see a red SIN IMAGEN card with a reason written under it,"
            " rather than a black field?"
        ),
        if_no=(
            "The placeholder is not drawing. A black field is an assertion"
            " that everything is fine, and it has been wrong every time it"
            " appeared."
        ),
        closes="fix(vr): SIN IMAGEN card — 2026-08-27, unverified",
        probe="camera",
        only_when_absent=True,
    ),
    Check(
        id="panel_single",
        title="ONE command panel, not two",
        look_for=(
            "Look at the command panel above centre. With both eyes open, is"
            " there exactly ONE panel? On 2026-08-27 there were two, one per"
            " eye, and they would not merge however long you looked."
        ),
        if_no=(
            "The stereo fix did not work. Two images ~10 degrees apart"
            " OUTWARD cannot be fused by any vergence angle, so this is not"
            " something the operator can adapt to."
        ),
        closes="fix(vr): one clip-space point for two eyes — 2026-08-27, unverified",
    ),
    Check(
        id="panel_readable",
        title="The panel is readable where it sits",
        look_for=(
            "Without moving your head: can you read the third item in the"
            " list? Say which item it is."
        ),
        if_no=(
            "It is back outside the comfortable lens cone. This is its third"
            " position; the previous two were unreadable at the edge of the"
            " render target."
        ),
        closes="fix(vr): HUD inside the lens cone — 2026-08-27, partially confirmed",
    ),
    Check(
        id="readiness",
        title="The readiness banner says WHY the robot will not move",
        look_for=(
            "Top-right of the panel: is there a line about the robot's"
            " posture — and does it tell you what to DO, not just what is"
            " wrong?"
        ),
        if_no=(
            "The banner is the fix for the 2026-08-21 session where gestures,"
            " walk buttons and head-yaw all failed for ONE reason (the robot"
            " was limp) and nothing in the headset said so."
        ),
        closes="feat(vr): readiness banner — 2026-08-24, unverified",
        probe="gate",
    ),
    Check(
        id="radar_single",
        title="ONE radar, not two, with points on it",
        look_for=(
            "Lower-left of centre: exactly ONE radar dial, and does it have"
            " points on it matching what is actually around the robot?"
        ),
        if_no=(
            "If the probe found a filled ring, the headset is not drawing it"
            " or is drawing two. If the probe found none, the dial is"
            " correctly empty and should SAY why it is empty."
        ),
        closes="feat(vr): lidar radar — 2026-08-25, unverified",
        probe="scan",
    ),
    Check(
        id="radar_says_why",
        title="An empty radar explains itself",
        look_for=(
            "Does the empty dial carry a short reason, rather than being"
            " blank?"
        ),
        if_no=(
            "An empty dial that does not say why is the one thing this display"
            " must never do by accident — it reads as 'nothing is around me'."
        ),
        closes="feat(vr): the dial always says why it is empty — 2026-08-25",
        probe="scan",
        only_when_absent=True,
    ),
    Check(
        id="head_yaw",
        title="Head yaw turns the robot",
        look_for=(
            "Turn your head slowly left, then right. Does the robot follow,"
            " and in the SAME direction?"
        ),
        if_no=(
            "Either no teleop session registered (the 2026-08-24 failure: port"
            " 8767 not forwarded, list_active_tasks empty) or the yaw sign is"
            " inverted, which is a different and much worse bug."
        ),
        closes="head yaw end-to-end from a headset — never observed",
    ),
    Check(
        id="joystick_walk",
        title="Walking with the thumbstick",
        look_for="Push the thumbstick forward and HOLD it. Does the robot walk?",
        if_no=(
            "The 2026-08-24 finding was that a HELD stick hit the 8-second"
            " limit every time and a TAPPED button never did, which read as"
            " 'walking half works'."
        ),
        closes="feat(vr): joystick walking — 2026-08-24, unverified",
    ),
    Check(
        id="deadman_band",
        title="The 8-second limit announces itself",
        look_for=(
            "Keep holding the stick past eight seconds. Does a band appear"
            " saying LIMITE DE 8 s, telling you to let go and push again?"
        ),
        if_no=(
            "The latch still fires silently. That is the state where walking"
            " stops dead and the operator has no way to know a"
            " release-and-push is all it wants."
        ),
        closes="feat(vr): 8 s alert band — 2026-08-24, unverified",
    ),
]


# --------------------------------------------------------------------------
# Pure logic — the part worth being sure about before anybody is wearing this
# --------------------------------------------------------------------------


def verdict_for(answer: str, data_present: Optional[bool]) -> str:
    """Turn one typed answer into a verdict, honouring the probe.

    THE ONE RULE THIS SCRIPT EXISTS FOR: a NO with no data behind it is
    BLOCKED, not FAIL. "I cannot see the radar" when the bridge published no
    ring is a fact about the nav container, and recording it as a rendering
    failure is how 2026-08-21 and 2026-08-27 each cost a day.

    A YES is still a PASS when the data is absent — if the operator can see
    the thing, whatever the probe thought, the renderer is doing its job and
    the probe is the thing that is wrong.
    """
    normalized = answer.strip().lower()
    if normalized in ("s", "skip", ""):
        return SKIPPED
    if normalized in ("y", "yes", "si", "sí"):
        return PASS
    if normalized in ("n", "no"):
        return BLOCKED if data_present is False else FAIL
    return SKIPPED


def report(outcomes: list) -> str:
    """The run, as something that can be pasted into a commit message.

    Grouped by verdict rather than listed in order: somebody reading this wants
    "what is still broken" first and the full transcript second.
    """
    lines = ["HEADSET CHECK", "=" * 13, ""]
    counts = {PASS: 0, FAIL: 0, BLOCKED: 0, SKIPPED: 0}
    for out in outcomes:
        counts[out.verdict] = counts.get(out.verdict, 0) + 1
    lines.append(
        f"{counts[PASS]} pass, {counts[FAIL]} fail, "
        f"{counts[BLOCKED]} blocked, {counts[SKIPPED]} skipped"
    )
    lines.append("")

    if counts[FAIL]:
        lines.append("FAILED — the data was there and the headset did not show it:")
        for out in outcomes:
            if out.verdict == FAIL:
                lines.append(f"  * {out.check.title}")
                lines.append(f"      {out.check.if_no}")
                if out.note:
                    lines.append(f"      operator: {out.note}")
        lines.append("")

    if counts[BLOCKED]:
        lines.append("BLOCKED — nothing was sent, so nothing could be drawn:")
        for out in outcomes:
            if out.verdict == BLOCKED:
                lines.append(f"  * {out.check.title}")
                lines.append(f"      probe: {out.probe_detail}")
        lines.append("")

    if counts[PASS]:
        lines.append("PASSED — safe to mark verified:")
        for out in outcomes:
            if out.verdict == PASS:
                lines.append(f"  * {out.check.closes}")
                if out.note:
                    lines.append(f"      operator: {out.note}")
        lines.append("")

    lines.append("FULL TRANSCRIPT")
    for out in outcomes:
        lines.append(f"  [{out.verdict:<7}] {out.check.id:<16} {out.check.title}")
        if out.probe_detail:
            lines.append(f"              probe: {out.probe_detail}")
        if out.note:
            lines.append(f"              note:  {out.note}")
    return "\n".join(lines)


# --------------------------------------------------------------------------
# I/O
# --------------------------------------------------------------------------


def fetch(base: str, path: str, timeout: float = 4.0):
    """GET and decode JSON. Returns (body_or_None, status_or_None).

    A 503 body is RETURNED, not discarded — these routes put the reason for
    their own emptiness in the body, and that reason is the most useful thing
    this script can print.
    """
    url = base.rstrip("/") + path
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:  # noqa: S310
            return json.loads(resp.read().decode("utf-8")), resp.status
    except urllib.error.HTTPError as exc:
        try:
            return json.loads(exc.read().decode("utf-8")), exc.code
        except Exception:  # noqa: BLE001 — a non-JSON error body is still just "no data"
            return None, exc.code
    except Exception:  # noqa: BLE001 — DNS, refused, timeout: all "no answer"
        return None, None


def run(
    base: str,
    ask: Callable[[str], str],
    out: Callable[[str], None],
    checks: Optional[list] = None,
    fetcher: Optional[Callable] = None,
) -> list:
    """Walk the checklist. `checks` and `fetcher` are injection points.

    They exist so the decision logic — which verdict a given answer earns
    against a given probe result — is testable without a bridge and without a
    Quest. That logic is the whole reason this is a script rather than a page
    of markdown, so it is the part that must not be taken on trust.
    """
    outcomes = []
    cache: dict = {}
    get = fetcher or fetch
    for check in checks if checks is not None else CHECKS:
        out("")
        out("-" * 72)
        out(check.title)
        out("")

        data_present: Optional[bool] = None
        detail = ""
        if check.probe:
            if check.probe not in cache:
                probe = PROBES[check.probe]
                body, status = get(base, probe.path)
                cache[check.probe] = probe.summarize(body, status)
            result = cache[check.probe]
            data_present = result.data_present
            detail = result.detail
            state = "IS arriving" if data_present else "is NOT arriving"
            out(f"  bridge says the data {state}: {detail}")
            out("")

            # A "what does it do with nothing?" question, asked while there is
            # something. Not applicable — and asking anyway collects a "no"
            # that means "there was no reason to show one".
            if check.only_when_absent and data_present:
                out("  SKIPPED — not applicable: the data is arriving, so this")
                out("  placeholder is correctly not being shown.")
                outcomes.append(
                    Outcome(check, SKIPPED, "not applicable — data present", detail)
                )
                continue

        out(f"  {check.look_for}")
        answer = ask("  [y]es / [n]o / [s]kip > ")
        # For an inverted check, absent data is the precondition being MET —
        # the placeholder is exactly what should be on screen — so a NO here
        # is a genuine rendering failure, not something to file as blocked.
        gating = None if check.only_when_absent else data_present
        verdict = verdict_for(answer, gating)
        note = ""
        if verdict in (FAIL, BLOCKED):
            out(f"  -> {check.if_no}")
            note = ask("  anything you noticed (enter to skip) > ").strip()
        outcomes.append(Outcome(check, verdict, note, detail))
    return outcomes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--bridge", default=DEFAULT_BRIDGE, help="bridge base URL")
    args = parser.parse_args()

    print(__doc__.split("WHY THIS EXISTS")[0].strip())
    print()
    print(f"bridge: {args.bridge}")

    outcomes = run(args.bridge, input, print)
    print()
    print(report(outcomes))
    failures = sum(1 for o in outcomes if o.verdict == FAIL)
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
