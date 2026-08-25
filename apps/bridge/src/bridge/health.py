"""Is the stack healthy? Assembled from injected probes, so it is testable.

`c3po_health` was a shell script that decided whether the MOTION GATE WAS ARMED
by substring-matching JSON:

    case "$gate" in *'"enabled":true'*) ... ;; *) note "closed (default)" ;; esac

That is a safety-relevant reading of a safety-relevant field, and it is wrong
the moment anything puts a space after the colon. `"enabled": true` falls
through to the default branch and the script reports **closed** for an ARMED
gate — quietly, in green, to somebody checking whether it is safe to be near the
robot. Nothing in the bridge promises compact JSON; that it happens to emit it
is an implementation detail of the response class.

So the parsing is `json.loads` and the assessment is a pure function over
probe results, which means the armed case can be tested — including with the
spacing that broke the original.

PYTHON 3.8 AND STDLIB ONLY, unlike the rest of this package. It runs on the
robot's SYSTEM interpreter, because the whole point of a health check is to
work when the thing it checks does not: a venv that failed to sync is exactly
when somebody types `c3po_health`. It imports nothing from `bridge` beyond the
package's own docstring-only `__init__`.
"""

from __future__ import annotations

import json
from typing import Any, Callable, Dict, List, Optional, Sequence

__all__ = ["Check", "Report", "assess", "render"]

class Check:
    """One line of the report."""

    def __init__(self, name: str, detail: str, problem: bool = False) -> None:
        self.name = name
        self.detail = detail
        self.problem = problem

    def __repr__(self) -> str:  # pragma: no cover - debugging aid
        return "Check({!r}, {!r}, problem={})".format(self.name, self.detail, self.problem)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Check):
            return NotImplemented
        return (self.name, self.detail, self.problem) == (other.name, other.detail, other.problem)


class Report:
    def __init__(self, checks: Sequence[Check]) -> None:
        self.checks = list(checks)

    @property
    def problems(self) -> int:
        return sum(1 for c in self.checks if c.problem)

    @property
    def healthy(self) -> bool:
        return self.problems == 0


def _parse(raw: Optional[str]) -> Optional[Dict[str, Any]]:
    """JSON or None. Never raises — a malformed body is 'cannot tell', not a crash."""
    if not raw:
        return None
    try:
        value = json.loads(raw)
    except (ValueError, TypeError):
        return None
    return value if isinstance(value, dict) else None


def assess(
    *,
    bridge_pid: Optional[int],
    gate_json: Optional[str],
    perception_containers: Sequence[str],
    gemm_containers: Sequence[str],
    port: int = 8001,
) -> Report:
    """Pure. Probe results in, a report out."""
    checks: List[Check] = []

    if bridge_pid:
        checks.append(Check("bridge", "running (pid {})".format(bridge_pid)))
    else:
        checks.append(Check("bridge", "DOWN", problem=True))

    gate = _parse(gate_json)
    if gate is None:
        checks.append(Check("bridge http", "NOT ANSWERING on :{}".format(port), problem=True))
    else:
        # `is True`, not truthiness: a string "false" is truthy, and this field
        # decides whether the robot can be driven.
        armed = gate.get("enabled") is True
        if armed:
            checks.append(
                Check("cmd_vel gate", "ARMED — the robot can be driven by Nav2")
            )
        else:
            reason = gate.get("disabled_reason") or "default"
            checks.append(Check("cmd_vel gate", "closed ({})".format(reason)))

        link = gate.get("link") or {}
        if isinstance(link, dict) and link.get("started") is True:
            detail = "joined"
            domain = link.get("domain_id")
            if domain is not None:
                detail = "joined (domain {})".format(domain)
            checks.append(Check("domain 42 link", detail))
        else:
            checks.append(
                Check(
                    "domain 42 link",
                    "NOT started — perception cannot reach the bridge",
                    problem=True,
                )
            )

    running = [c for c in perception_containers if c]
    if running:
        # Perception has no systemd lifecycle: every stage is an explicit
        # foreground operator window, and STT may compose with another stage.
        checks.append(Check("perception", "running: {}".format(" ".join(running))))
    else:
        checks.append(Check("perception", "off (no sensor is claimed by C3PO)"))

    gemm = [c for c in gemm_containers if c]
    checks.append(
        Check("gemm (co-tenant)", "running: {}".format(" ".join(gemm)) if gemm else "not running")
    )

    return Report(checks)


def render(report: Report, colour: bool = False) -> str:
    """The terminal form. Same shape the shell version printed."""
    green, red, reset = ("\033[32m", "\033[31m", "\033[0m") if colour else ("", "", "")
    lines = ["c3po health"]
    for check in report.checks:
        lines.append("  {:<28} {}".format(check.name, check.detail))
    lines.append("")
    if report.healthy:
        lines.append("  {}OK{} healthy".format(green, reset))
    else:
        lines.append("  {}X{} {} problem(s) above".format(red, reset, report.problems))
    return "\n".join(lines)


def probe_and_assess(
    *,
    read_pid: Callable[[], Optional[int]],
    http_get: Callable[[str], Optional[str]],
    docker_ps: Callable[[str], Sequence[str]],
    port: int = 8001,
) -> Report:
    """Run the probes, then assess. The only place the two are joined."""
    return assess(
        bridge_pid=read_pid(),
        gate_json=http_get("http://127.0.0.1:{}/telemetry/gate".format(port)),
        perception_containers=docker_ps("^c3po-perception"),
        gemm_containers=docker_ps("^gemm"),
        port=port,
    )


# --- the command ------------------------------------------------------------


def _service_main_pid(unit: str) -> Optional[int]:
    """The systemd-owned main pid, or None when the unit is inactive/unknown.

    The bridge has one lifecycle owner now. Reading MainPID avoids recreating a
    second source of truth in ~/.c3po/run and cannot go stale across a restart.
    """
    raw = _run(["systemctl", "show", "--property=MainPID", "--value", unit]).strip()
    try:
        pid = int(raw)
    except ValueError:
        return None
    return pid if pid > 0 else None


def _http_get(url: str, timeout: float = 5.0) -> Optional[str]:
    import urllib.request

    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            return str(resp.read().decode("utf-8"))
    except Exception:
        return None


def _run(argv: Sequence[str]) -> str:
    import subprocess

    try:
        out = subprocess.run(
            list(argv), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False
        )
    except OSError:
        return ""
    return out.stdout.decode("utf-8", "replace") if out.stdout else ""


def main(argv: Optional[Sequence[str]] = None) -> int:
    import os
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        print("usage: c3po status", file=sys.stderr)
        return 2
    port = int(os.environ.get("BRIDGE_PORT", "8001"))

    report = probe_and_assess(
        read_pid=lambda: _service_main_pid("c3po-bridge.service"),
        http_get=_http_get,
        docker_ps=lambda prefix: [
            name
            for name in _run(
                ["docker", "ps", "--filter", "name=" + prefix, "--format", "{{.Names}}"]
            ).splitlines()
            if name.strip()
        ],
        port=port,
    )

    print(render(report, colour=sys.stdout.isatty()))

    return 0 if report.healthy else 1


if __name__ == "__main__":
    import sys

    sys.exit(main())
