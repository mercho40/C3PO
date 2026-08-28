"""Everything that has to be true before the headset goes on.

From inside a Quest, five different causes all look like "it does not work",
and you cannot debug from in there. So this runs on the operator's machine, in
a terminal, and separates the causes BEFORE anyone puts a headset on.

WHY IT IS NOT A SHELL SCRIPT ANY MORE. The 344-line original made the same
class of mistake twice, in the two places it mattered most:

  * It read `apps/web/.env` with `grep | cut -d= -f2- | tr -d '"' | xargs`,
    which truncates any value containing a space and eats inline comments as
    part of the URL. `bridge.env_file` already parses that file correctly and
    is tested against the exact line that breaks naive parsers.
  * It read the camera's `/status` by substring — the same technique that had
    `c3po_health` reporting a closed motion gate for an armed one.

The messages are kept verbatim wherever they were already good. They are the
actual product here: `bad "headset plugged in but NOT authorised"` followed by
"there is a prompt INSIDE the headset" is the difference between a five-second
fix and half an hour of swapping cables.

STDLIB ONLY AND 3.8-VALID, like `bridge.health`, so it runs from a checkout with
no venv — which is a state a preflight check should survive rather than trip on.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional, Sequence, Tuple

from bridge.env_file import parse_env_file

__all__ = ["Finding", "Section", "verdict", "classify_probe", "best_state"]

OK, WARN, BAD = "ok", "warn", "bad"


class Finding:
    def __init__(self, level: str, text: str, notes: Sequence[str] = ()) -> None:
        self.level = level
        self.text = text
        self.notes = list(notes)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Finding):
            return NotImplemented
        return (self.level, self.text, self.notes) == (other.level, other.text, other.notes)

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "Finding({!r}, {!r})".format(self.level, self.text)


class Section:
    def __init__(self, title: str, findings: Sequence[Finding]) -> None:
        self.title = title
        self.findings = list(findings)


# --- the distinction this whole file exists for -----------------------------


def classify_probe(rc: int, stderr: str) -> str:
    """What a `curl` to a forwarded port actually means.

    `ssh -L` binds its local listener at SETUP time, before it knows anything
    about the far end. So a plain connect() SUCCEEDS whenever the ssh process is
    alive, whatever is or is not running on the robot — which makes `nc -z`
    useless here, and actively harmful: it is how a forgotten `run_teleop` earns
    a green tick.

    Forcing a byte through the channel is what tells them apart. ssh opens the
    channel, finds nothing listening, and drops the already-accepted local
    socket — so the client sees a reset or an empty reply, never a refusal.

        nothing  no listener at all — the tunnel is not up
        empty    tunnel is up, nothing is listening on the robot
        timeout  the tunnel is wedged
        alive    something answered
    """
    if rc == 0:
        return "alive"
    text = stderr or ""
    for needle in ("Connection refused", "Couldn't connect to server", "Failed to connect"):
        if needle in text:
            return "nothing"
    for needle in ("reset by peer", "Empty reply", "Recv failure", "closed connection"):
        if needle in text:
            return "empty"
    for needle in ("timed out", "Operation timed out"):
        if needle in text:
            return "timeout"
    # Any HTTP-level complaint means something spoke to us.
    return "alive"


def best_state(states: Sequence[str]) -> str:
    """The kindest true answer across several addresses for one port.

    A dev server may be listening on ONE address family only — vite binds
    `[::1]:3001` and nothing on 127.0.0.1 — so probing a single literal address
    reports "not listening" for a server that is serving. The original shell
    script probed 127.0.0.1 and told the operator to start a `bun run dev` that
    was already running, which is the false alarm this whole file exists to
    prevent.

    Any address answering means the port is up. Otherwise keep the first
    diagnosis, because refused/reset/timeout are different problems and
    flattening them loses the distinction section 2 is built on.
    """
    if not states:
        return "nothing"
    return "alive" if "alive" in states else states[0]


TUNNEL_HINT = [
    "ssh -N -o ControlMaster=no \\",
    "    -L 8001:127.0.0.1:8001 -L 8081:127.0.0.1:8081 \\",
    "    -L 8767:127.0.0.1:8767 c3po",
    "ControlMaster=no matters: a forward on a shared master evaporates",
    "when the master idles out, mid-session, with no obvious cause.",
]


def tunnel_finding(port: int, label: str, fatal: bool, starter: str, state: str) -> Finding:
    if state == "alive":
        return Finding(OK, "{}  {} — answering".format(port, label))
    if state == "empty":
        level = BAD if fatal else WARN
        return Finding(
            level,
            "{}  {} — the tunnel works, nothing is running on the robot".format(port, label),
            [
                "on the robot:  {}".format(starter),
                "the forward is fine — do NOT go looking at the tunnel for this one",
            ],
        )
    if state == "timeout":
        return Finding(
            BAD,
            "{}  {} — timed out (the tunnel is wedged)".format(port, label),
            ["kill the ssh process and open it again"],
        )
    return Finding(
        BAD if fatal else WARN,
        "{}  {} — not forwarded".format(port, label),
        list(TUNNEL_HINT),
    )


# --- the camera -------------------------------------------------------------


def camera_url_from_env(env_text: Optional[str], shell_value: str = "") -> Tuple[str, str]:
    """(url, where it came from). Reads the WEB APP's config, not this shell.

    Testing 127.0.0.1:8081 while `apps/web/.env` points somewhere else gives the
    camera a full green while the page renders "PUBLIC_ROBOT_CAM_URL no está
    configurado" — the exact mismatch reading the real file is meant to avoid.
    """
    if env_text is not None:
        value = parse_env_file(env_text).get("PUBLIC_ROBOT_CAM_URL", "").strip()
        if value:
            return value, "apps/web/.env"
    if shell_value.strip():
        return shell_value.strip(), "the shell environment"
    return "", ""


def port_of(url: str) -> int:
    """The port a camera URL uses. There are two possible feeds on two ports.

    Telling somebody to add `-L 8081` when their URL says 8001 is the kind of
    confident wrong advice this script exists to end.
    """
    rest = url.split("://", 1)[-1]
    host = rest.split("/", 1)[0]
    if ":" in host:
        try:
            return int(host.rsplit(":", 1)[1])
        except ValueError:
            return 80
    return 80


def camera_findings(
    url: str,
    source: str,
    status_state: str,
    status_body: str,
    stream_has_frames: Optional[bool],
    cors_ok: Optional[bool] = None,
) -> List[Finding]:
    """What /status, the stream body and the CORS header mean, together.

    `cors_ok` is the question this section used to be missing, and its absence
    is why the last line below used to be wrong. None means we could not ask.
    """
    if not url:
        return [
            Finding(
                BAD,
                "PUBLIC_ROBOT_CAM_URL is not set in apps/web/.env",
                [
                    "the page will not open the stream at all without it. Add:",
                    "  PUBLIC_ROBOT_CAM_URL=http://127.0.0.1:8001/camera",
                    "(the bridge picks whichever feed is live and relays it, so this",
                    " one URL works whether or not the detector owns the camera)",
                ],
            )
        ]

    port = port_of(url)
    out = [Finding(OK, "camera URL from {}: {}".format(source, url))]

    if status_state == "nothing":
        out.append(
            Finding(
                BAD,
                "nothing is forwarding {}".format(url),
                [
                    "the SSH tunnel is missing -L {}. A tunnel problem, not a robot one.".format(
                        port
                    )
                ],
            )
        )
        return out
    if status_state == "empty":
        who = (
            "the BRIDGE is not up — this feed is served by it, not by perception."
            if port == 8001
            else "the vision container is not up. On the robot:  perception_up perception"
        )
        out.append(
            Finding(
                BAD,
                "the tunnel reaches the robot, and nothing is listening on {} there".format(port),
                [who, "This is the failure that looked like a camera fault last time. It is not."],
            )
        )
        return out
    if status_state == "timeout":
        out.append(
            Finding(BAD, "{} timed out".format(url), ["the tunnel is wedged. Reopen it."])
        )
        return out

    status = _parse_json(status_body)
    if status is None:
        out.append(
            Finding(WARN, "the reply does not look like the expected /status shape", [status_body])
        )
        return out

    if status.get("live") is True:
        out.append(Finding(OK, "and it says it is LIVE — there are frames to show"))
        # NOT the status code. The server sends 200 and the multipart headers
        # unconditionally, before it has waited for any frame — so a 200 proves
        # the socket opened and nothing else. A boundary marker in the body is
        # what proves a picture.
        if stream_has_frames:
            out.append(
                Finding(
                    OK,
                    "real frame data is arriving — the whole chain to this Mac works",
                    # THIS HINT USED TO SAY "if the headset still shows nothing
                    # after this, it is the renderer", AND THAT WAS WRONG. On
                    # 2026-08-28 the chain worked to the Mac — videohub live at
                    # 1920x1080, frames flowing — and the headset would still
                    # have been black, because the bridge sent no CORS header
                    # and the WebGL layer's `crossOrigin="anonymous"` image
                    # cannot load without one. A confident wrong pointer at the
                    # renderer is how the last two of these cost a day each.
                    ["frames are not the whole story — see the CORS line below"],
                )
            )
            if cors_ok is False:
                out.append(
                    Finding(
                        BAD,
                        "frames arrive, but the reply is not readable by the console",
                        [
                            "no Access-Control-Allow-Origin for " + CONSOLE_ORIGIN + ".",
                            "The on-page panel will still show a picture — a plain <img>",
                            "needs no CORS. THE HEADSET WILL NOT: its WebGL layer sets",
                            "crossOrigin=anonymous, because WebGL refuses to sample a",
                            "texture the page cannot read back, and without the header",
                            "the image never loads at all.",
                            "",
                            "This is a DEPLOY problem, not a camera or renderer one:",
                            "the bridge on the robot is running a build from before",
                            "BRIDGE_CORS_ORIGINS was implemented. Update and restart it:",
                            "  ssh c3po 'cd ~/c3po && git pull && sudo systemctl restart c3po-bridge'",
                        ],
                    )
                )
            elif cors_ok:
                out.append(
                    Finding(
                        OK,
                        "and the console is allowed to read it — the headset can load it",
                    )
                )
        else:
            out.append(
                Finding(
                    BAD,
                    "/status says live, but no frame data came out of the stream",
                    [
                        "the server is answering and not sending pictures. Restart it:",
                        "  perception_up perception",
                    ],
                )
            )
        return out

    # `frames` separates two different faults that both read as live:false.
    frames = status.get("frames")
    if frames == 0:
        notes = []
        hint = status.get("hint")
        if hint:
            # The bridge's relay knows WHY, and its answer beats anything this
            # script can guess from outside.
            notes.append(str(hint))
        else:
            notes += [
                "the server is up and the D435i is not delivering — the camera",
                "itself, not the tunnel and not the web app. Check the cable and",
                "that nothing else has the device open.",
            ]
        out.append(Finding(WARN, "live: false, and it has produced NOTHING since it started", notes))
    else:
        out.append(
            Finding(
                WARN,
                "live: false, but frames HAVE arrived and then stopped",
                [
                    "so the camera works — its ticks are failing or running slower",
                    "than the 1 s staleness threshold. Restarting is the quick answer:",
                    "  perception_up perception",
                ],
            )
        )
    return out


def _parse_json(raw: str) -> Optional[Dict[str, Any]]:
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


# --- the headset ------------------------------------------------------------


def adb_devices(listing: str) -> Tuple[List[str], List[str]]:
    """(authorised, unauthorised) serials from `adb devices` output."""
    authorised, unauthorised = [], []
    for line in listing.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        if parts[1] == "device":
            authorised.append(parts[0])
        elif parts[1] == "unauthorized":
            unauthorised.append(parts[0])
    return authorised, unauthorised


HEADSET_PORTS = (3000, 3001, 8081, 8767)


def headset_findings(
    adb_present: bool, listing: str, reverse_listing: str
) -> List[Finding]:
    if not adb_present:
        return [
            Finding(BAD, "adb is not installed", ["brew install --cask android-platform-tools"])
        ]

    authorised, unauthorised = adb_devices(listing)
    if authorised:
        out = [Finding(OK, "headset connected and authorised")]
        for port in HEADSET_PORTS:
            if "tcp:{}".format(port) in reverse_listing:
                out.append(Finding(OK, "  quest localhost:{} is forwarded".format(port)))
            else:
                out.append(
                    Finding(
                        WARN,
                        "  quest localhost:{} is NOT forwarded".format(port),
                        ["run ./scripts/quest_setup.sh"],
                    )
                )
        return out
    if unauthorised:
        return [
            Finding(
                BAD,
                "headset plugged in but NOT authorised",
                [
                    "there is an 'Allow USB debugging?' prompt INSIDE the headset.",
                    "Put it on and accept it.",
                    "This is the single easiest thing to miss, and it looks exactly",
                    "like a bad cable.",
                ],
            )
        ]
    return [Finding(WARN, "no headset on USB (fine if you have not plugged it in yet)")]


# --- a standing stop --------------------------------------------------------


def estop_finding(
    stop_at: Optional[float],
    ack_at: Optional[float],
    when: str = "",
    run_dir: str = "",
) -> Finding:
    """A stop deliberately outlives the session it was pressed in.

    So an outstanding one is a WARNING with an explanation, never a failure:
    it is working as designed, and somebody who reads it as a fault will go
    looking for a broken robot.

    WHOSE STOP, THOUGH. The sentinel is per-machine by construction — `estop.py`
    puts it at `$HOME/.c3po/run` on whatever host is running. For real hardware
    the bridge runs ONBOARD, because DDS only exists on the robot's internal
    LAN, so the sentinel that actually latches teleop is the ROBOT's. Preflight
    runs on the operator's Mac and reads the Mac's.

    Both directions of that are wrong, and the second is the dangerous one:

      * a months-old local sim stop reads as a live safety state on the robot
        (this Mac carried one from 2026-08-20 for days);
      * a real standing stop ONBOARD is invisible from here and reports as
        "no stop outstanding".

    So both branches name the machine. Reading the robot's over SSH was
    considered and rejected: a check that can hang on the network is not what
    you want in the thing you run before a headset goes on. It points at the
    one-line command instead.
    """
    where = " ({})".format(run_dir) if run_dir else ""
    if not stop_at or (ack_at or 0) >= stop_at:
        return Finding(
            OK,
            "no stop outstanding on this machine",
            ["the onboard bridge keeps its own — this check cannot see it"],
        )
    notes = [
        "this is not broken — a stop deliberately outlives the session it was pressed in.",
        "It clears itself once you connect: hold the dead-man RELEASED for one full second.",
    ]
    if when:
        notes.append("Pressed at: {}".format(when))
    notes.append("Read from THIS machine{}".format(where))
    notes.append("The onboard bridge keeps its OWN sentinel, and that one is what")
    notes.append("latches teleop on real hardware. Check it with:")
    notes.append("    ssh c3po 'ls -l ~/.c3po/run/'")
    return Finding(
        WARN,
        "an emergency stop is recorded on THIS MACHINE and has not been cleared",
        notes,
    )


# --- the verdict ------------------------------------------------------------


def verdict(sections: Sequence[Section]) -> Tuple[int, int, str]:
    """(failed, warned, the closing sentence)."""
    failed = sum(1 for s in sections for f in s.findings if f.level == BAD)
    warned = sum(1 for s in sections for f in s.findings if f.level == WARN)
    if not failed and not warned:
        return failed, warned, "everything checked out. Put the headset on."
    if not failed:
        return (
            failed,
            warned,
            "{} warning(s), nothing fatal. You can drive; read them first.".format(warned),
        )
    return (
        failed,
        warned,
        "{} thing(s) will stop you. Fix those before the headset goes on — "
        "from inside it they all look the same, and you cannot debug from in there.".format(
            failed
        ),
    )


def render(sections: Sequence[Section], colour: bool = False) -> str:
    b, g, r, y, d, z = (
        ("\033[1m", "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m")
        if colour
        else ("", "", "", "", "", "")
    )
    mark = {OK: "{}✓{}".format(g, z), WARN: "{}!{}".format(y, z), BAD: "{}✗{}".format(r, z)}
    lines: List[str] = []
    for section in sections:
        lines.append("\n{}{}{}".format(b, section.title, z))
        for finding in section.findings:
            lines.append("  {} {}".format(mark[finding.level], finding.text))
            for note in finding.notes:
                lines.append("      {}{}{}".format(d, note, z))
    failed, warned, closing = verdict(sections)
    colour_for = g if not failed and not warned else (y if not failed else r)
    lines.append("\n{}Verdict{}".format(b, z))
    lines.append("  {}{}{}\n".format(colour_for, closing, z))
    return "\n".join(lines)


# --- the command ------------------------------------------------------------
#
# curl rather than urllib, deliberately: `classify_probe` reads curl's own
# messages, and those messages ARE the diagnosis. Reimplementing the
# refused/reset/timeout distinction on top of urllib's exception hierarchy would
# be re-deriving something curl already gets right, in the one place this script
# has to be exactly right.


def _curl(url: str, timeout: int = 5) -> Tuple[int, str, str]:
    """(rc, body, stderr)."""
    import subprocess

    try:
        proc = subprocess.run(
            ["curl", "-sS", "--max-time", str(timeout), url],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
    except OSError as exc:
        return 1, "", str(exc)
    return (
        proc.returncode,
        proc.stdout.decode("utf-8", "replace"),
        proc.stderr.decode("utf-8", "replace"),
    )


def _probe(url: str, timeout: int = 5) -> Tuple[str, str]:
    rc, body, stderr = _curl(url, timeout)
    return classify_probe(rc, stderr), body


#: The origin the console is served from, and therefore the one the headset
#: uses: `quest_setup.sh` forwards 3001 and the Quest browser loads
#: `http://localhost:3001/vr-control`.
CONSOLE_ORIGIN = "http://localhost:3001"


def _camera_allows_console(url: str, origin: str = CONSOLE_ORIGIN) -> Optional[bool]:
    """Does `/status` come back readable by the console? None if we could not ask.

    Sends a real `Origin` and looks for `Access-Control-Allow-Origin` coming
    back, because that is exactly what the browser will do and exactly what it
    will refuse over. See `camera_findings` for why this is a separate question
    from whether frames are arriving.
    """
    import subprocess

    try:
        proc = subprocess.run(
            [
                "curl", "-sS", "--max-time", "6",
                "-o", "/dev/null", "-D", "-",
                "-H", "Origin: " + origin,
                url,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return None
    if proc.returncode != 0:
        return None
    return b"access-control-allow-origin" in (proc.stdout or b"").lower()


def _stream_has_frames(url: str, boundary: str = "c3poframe") -> bool:
    """A boundary marker in the body. See `camera_findings`."""
    import subprocess

    try:
        proc = subprocess.run(
            ["curl", "-sS", "--max-time", "6", url],
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            check=False,
        )
    except OSError:
        return False
    return boundary.encode() in (proc.stdout or b"")[:4096]


def _run(argv: Sequence[str]) -> Tuple[bool, str]:
    """(the command exists, its stdout)."""
    import subprocess

    try:
        proc = subprocess.run(
            list(argv), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False
        )
    except OSError:
        return False, ""
    return True, proc.stdout.decode("utf-8", "replace") if proc.stdout else ""


def _local_section() -> Section:
    findings: List[Finding] = []
    for port, label in ((3000, "apps/back"), (3001, "apps/web")):
        state = best_state([_probe("http://{}:{}/".format(h, port), timeout=3)[0]
                            for h in ("localhost", "127.0.0.1")])
        if state == "nothing":
            findings.append(
                Finding(
                    BAD,
                    "{}  {} — not listening".format(port, label),
                    ["bun run dev   (starts both 3000 and 3001)"],
                )
            )
        else:
            findings.append(Finding(OK, "{}  {}".format(port, label)))

    state = best_state([_probe("http://{}:3000/health".format(h), timeout=3)[0]
                        for h in ("localhost", "127.0.0.1")])
    if state == "alive":
        findings.append(Finding(OK, "the API answers /health"))
    else:
        findings.append(
            Finding(
                BAD,
                "3000 is open but /health does not answer",
                ["it bound the port and then failed — look at what it printed at boot"],
            )
        )
    return Section("1. This machine", findings)


def _tunnel_section() -> Section:
    spec = (
        (8767, "teleop stream", True, "run_teleop"),
        (8001, "bridge MCP", True, "run_c3po"),
        (8081, "camera MJPEG (only if PUBLIC_ROBOT_CAM_URL says 8081)", False, "perception_up perception"),
    )
    findings = []
    for port, label, fatal, starter in spec:
        state, _ = _probe("http://127.0.0.1:{}/".format(port), timeout=4)
        findings.append(tunnel_finding(port, label, fatal, starter, state))
    return Section("2. The tunnel to the robot", findings)


def _camera_section(repo_root: str) -> Section:
    import os

    env_path = os.path.join(repo_root, "apps", "web", ".env")
    env_text: Optional[str] = None
    try:
        with open(env_path) as fh:
            env_text = fh.read()
    except OSError:
        env_text = None

    url, source = camera_url_from_env(env_text, os.environ.get("PUBLIC_ROBOT_CAM_URL", ""))
    if not url:
        return Section("3. The camera, end to end", camera_findings("", "", "alive", "", None))

    state, body = _probe(url.rstrip("/") + "/status", timeout=5)
    has_frames: Optional[bool] = None
    cors_ok: Optional[bool] = None
    status = _parse_json(body) if state == "alive" else None
    if status is not None and status.get("live") is True:
        has_frames = _stream_has_frames(url.rstrip("/") + "/stream.mjpg")
        # Asked only when there is a picture to be readable. A CORS complaint
        # about a feed that is not running would be noise on top of the real
        # finding.
        cors_ok = _camera_allows_console(url.rstrip("/") + "/status")
    return Section(
        "3. The camera, end to end",
        camera_findings(url, source, state, body, has_frames, cors_ok),
    )


def _headset_section() -> Section:
    present, listing = _run(["adb", "devices"])
    _, reverse = _run(["adb", "reverse", "--list"]) if present else (False, "")
    return Section("4. The headset", headset_findings(present, listing, reverse))


def _estop_section() -> Section:
    import os
    import time

    run_dir = os.environ.get("C3PO_RUN_DIR", os.path.expanduser("~/.c3po/run"))

    def mtime(name: str) -> Optional[float]:
        try:
            return os.path.getmtime(os.path.join(run_dir, name))
        except OSError:
            return None

    stop_at = mtime("stop_everything")
    when = (
        time.strftime("%H:%M:%S on %d %b", time.localtime(stop_at)) if stop_at else ""
    )
    return Section(
        "5. Is a stop still standing?",
        [estop_finding(stop_at, mtime("stop_acknowledged"), when, run_dir)],
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    import os
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    quick = "--quick" in args
    repo_root = os.environ.get("C3PO_DIR") or os.getcwd()

    sections = [_local_section()]
    if not quick:
        sections.append(_tunnel_section())
        sections.append(_camera_section(repo_root))
    sections.append(_headset_section())
    sections.append(_estop_section())

    print(render(sections, colour=sys.stdout.isatty()))
    # Always 0: this is a briefing, not a gate. Somebody who wants to fly with
    # two warnings is allowed to, and a non-zero exit here would only teach
    # people to append `|| true`.
    return 0


if __name__ == "__main__":
    import sys

    sys.exit(main())
