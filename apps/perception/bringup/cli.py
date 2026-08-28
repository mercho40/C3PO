"""`perception_up <stage>` — the orchestration, with docker behind a seam.

Everything that touches the machine goes through `Runner`, so the sequencing —
what gets removed, what gets created, in what order, and what is refused — is
testable on a laptop. That sequencing is where the bugs were: a stage tearing
down a container it was designed to coexist with, a readiness gate that proved
the wrong container was alive, a claim announced and then not made.

The parts that genuinely need the robot are the docker calls themselves and the
readiness probes. `--dry-run` prints the exact argv without running any of it,
which is how a change here gets checked before it is trusted.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from bringup.spec import NAV, STAGES, STT, VISION, Stage, containers_for, containers_to_replace

PREFIX = "c3po-perception"


#: How long ONE readiness probe may take, in seconds.
#:
#: Each attempt spawns a fresh `ros2 topic echo`, which must complete full DDS
#: discovery before it can report anything. Measured on the robot 2026-08-26
#: with `nav2-fake` up: 2.25 s at load 10, against a 3 s ceiling — and at load
#: 15, which is simply what the box reads while the other team runs SLAM, it
#: exceeded 3 s on EVERY attempt. A completely healthy stack tore itself down,
#: twenty participants having made discovery slower than the timeout allowed.
#:
#: The old value was not a little tight, it was tight in the one condition that
#: matters: a shared robot with somebody else working on it.
PROBE_TIMEOUT_S = 12

#: Gap between attempts. Small on purpose — the probe's own timeout is the
#: thing that dominates.
PROBE_INTERVAL_S = 1

#: Attempts before rolling back. Worst case READY_ATTEMPTS * (PROBE_TIMEOUT_S +
#: PROBE_INTERVAL_S) = 260 s, which is SHORTER than the 360 s the old loop could
#: take while being far more tolerant of a loaded box. A genuinely absent
#: publisher is still reported in about four minutes.
READY_ATTEMPTS = 20


class Runner:
    """Everything with a side effect. Replaced wholesale in tests."""

    def docker(self, args: Sequence[str], capture: bool = True) -> Tuple[int, str]:
        try:
            return self._docker(args, capture)
        except OSError as exc:
            # No docker on this machine. A missing binary is a message, not a
            # traceback: this same module is imported by the test suite on
            # laptops that have never had docker installed.
            return 127, f"docker is not available: {exc}"

    def _docker(self, args: Sequence[str], capture: bool = True) -> Tuple[int, str]:
        # check=False: the returncode IS the answer here — callers branch on it
        # rather than catching, because "that container does not exist" is an
        # ordinary reply to a query, not an exception.
        proc = subprocess.run(
            ["docker"] + list(args),
            check=False,
            stdout=subprocess.PIPE if capture else None,
            stderr=subprocess.STDOUT if capture else None,
        )
        out = proc.stdout.decode("utf-8", "replace") if proc.stdout else ""
        return proc.returncode, out

    def http_ok(self, url: str, timeout: float = 3.0) -> bool:
        import urllib.request

        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return 200 <= resp.status < 300
        except Exception:  # noqa: BLE001 - any failure means "not answering yet"
            return False

    def sleep(self, seconds: float) -> None:
        time.sleep(seconds)

    def makedirs(self, path: str) -> None:
        os.makedirs(path, exist_ok=True)

    def shell(self, argv: Sequence[str]) -> Tuple[int, str]:
        """A non-docker command. Same shape as `docker`, same reason."""
        try:
            proc = subprocess.run(
                list(argv), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False
            )
        except OSError as exc:
            return 127, str(exc)
        return proc.returncode, proc.stdout.decode("utf-8", "replace") if proc.stdout else ""

    def run_script(self, path: str, env: Optional[Dict[str, str]] = None) -> int:
        merged = dict(os.environ)
        merged.update(env or {})
        # Flush before handing the terminal to a child: our buffered lines would
        # otherwise land after everything the child writes.
        sys.stdout.flush()
        sys.stderr.flush()
        return subprocess.call([path], env=merged)


# --- output -----------------------------------------------------------------

# LINE-BUFFERED, because this is a progress report and the order is the message.
# Python block-buffers stdout when it is a pipe — which is every `ssh c3po
# 'perception_up ...'` — while stderr stays unbuffered. The first live run
# printed take_camera's four-line refusal ABOVE the header explaining what stage
# was even starting, so the reason arrived before the thing it was a reason for.
try:
    # `sys.stdout` is typed `TextIO`, which has no `reconfigure`; the concrete
    # `TextIOWrapper` does. The `except AttributeError` below IS the check, and
    # it is there because this runs on the robot's system interpreter where the
    # stream may be something else entirely.
    sys.stdout.reconfigure(line_buffering=True)  # type: ignore[union-attr]
except AttributeError:  # pragma: no cover - python < 3.7
    pass

_BOLD, _DIM, _RED, _GREEN, _YELLOW, _RESET = "", "", "", "", "", ""
if sys.stdout.isatty():
    _BOLD, _DIM = "\033[1m", "\033[2m"
    _RED, _GREEN, _YELLOW, _RESET = "\033[31m", "\033[32m", "\033[33m", "\033[0m"


def say(msg: str) -> None:
    print(f"{_BOLD}{msg}{_RESET}")


def ok(msg: str) -> None:
    print(f"  {_GREEN}✓{_RESET} {msg}")


def warn(msg: str) -> None:
    print(f"  {_YELLOW}!{_RESET} {msg}")


def err(msg: str) -> None:
    print(f"  {_RED}✗{_RESET} {msg}", file=sys.stderr)


def info(msg: str) -> None:
    print(f"  {_DIM}{msg}{_RESET}")


# --- helpers ----------------------------------------------------------------


def existing_containers(runner: Runner) -> List[str]:
    code, out = runner.docker(
        ["ps", "-a", "--filter", "name=^" + PREFIX, "--format", "{{.Names}}"]
    )
    if code != 0:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def running_containers(runner: Runner) -> List[str]:
    code, out = runner.docker(["ps", "--filter", "name=^" + PREFIX, "--format", "{{.Names}}"])
    if code != 0:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def gemm_running(runner: Runner, prefix: str = "gemm") -> List[str]:
    code, out = runner.docker(["ps", "--filter", "name=^" + prefix, "--format", "{{.Names}}"])
    if code != 0:
        return []
    return [line.strip() for line in out.splitlines() if line.strip()]


def nav_image_digest(runner: Runner, image: str) -> str:
    code, out = runner.docker(
        [
            "image",
            "inspect",
            image,
            "--format",
            "{{if .RepoDigests}}{{index .RepoDigests 0}}{{else}}{{.Id}}{{end}}",
        ]
    )
    return out.strip() if code == 0 else ""


# --- the bring-up -----------------------------------------------------------


def bring_up(
    stage_name: str,
    runner: Runner,
    *,
    env: Optional[Dict[str, str]] = None,
    scripts_dir: str = "",
    dry_run: bool = False,
    log: Callable[[str], None] = info,
) -> int:
    env = dict(env if env is not None else os.environ)
    stage = STAGES.get(stage_name)
    if stage is None:
        err("usage: perception_up {" + "|".join(STAGES) + "}")
        return 2

    home = env.get("HOME", "/home/unitree")
    c3po_dir = env.get("C3PO_DIR", os.path.join(home, "c3po"))
    log_dir = env.get("PERCEPTION_LOG_DIR", os.path.join(home, ".c3po", "logs", "perception"))
    lidar_source = env.get("C3PO_LIDAR_SOURCE", "republish")

    # SAY WHAT THIS STAGE TAKES BEFORE TAKING IT. "claims shared sensors" is not
    # specific enough to act on: what the other team loses for the length of the
    # window is the sentence they should be able to read back to you.
    say(f"perception_up: stage '{stage_name}'")
    for line in stage.summary:
        (warn if line.startswith("sensors claimed: RealSense") else info)(line)
    if lidar_source == "driver":
        warn("C3PO_LIDAR_SOURCE=driver — claiming the Livox device itself")

    if not _arbitrate(stage, runner, scripts_dir, dry_run, lidar_source):
        return 1

    digest = "" if dry_run else nav_image_digest(runner, "c3po/perception-nav:humble")
    existing = [] if dry_run else existing_containers(runner)
    wanted = containers_for(
        stage,
        c3po_dir=c3po_dir,
        home=home,
        log_dir=log_dir,
        env=env,
        nav_digest=digest,
        lidar_source=lidar_source,
    )

    for name in containers_to_replace(stage, existing):
        log(f"removing existing {name}")
        if not dry_run:
            runner.docker(["rm", "-f", name])

    if not dry_run:
        # Through the runner like everything else with a side effect. Calling
        # os.makedirs directly here made the suite try to create /home/unitree
        # on a laptop — the seam is only worth having if nothing routes around it.
        runner.makedirs(log_dir)
        runner.makedirs(os.path.join(home, ".c3po", "models"))

    for container in wanted:
        args = container.create_args()
        if dry_run:
            print("docker " + " ".join(args))
            continue
        code, out = runner.docker(args)
        if code != 0:
            err(f"could not create {container.name}: {out.strip()}")
            return 1
        ok(f"created {container.name}")

    if dry_run:
        for container in wanted:
            print(f"docker start {container.name}")
        return 0

    for container in wanted:
        code, out = runner.docker(["start", container.name])
        if code != 0:
            err(f"could not start {container.name}: {out.strip()}")
            return 1
        ok(f"started {container.name}")

    return _await_ready(stage, runner, env)


def _arbitrate(
    stage: Stage,
    runner: Runner,
    scripts_dir: str,
    dry_run: bool,
    lidar_source: str,
) -> bool:
    """Stop what has to be stopped, and only that."""
    claims_device = stage.claims_camera or lidar_source == "driver"
    if not claims_device:
        info(f"stage '{stage.name}' claims no device — gemm is left alone")
        return True

    if dry_run:
        # A dry run asks nothing and stops nothing. Querying docker here made
        # `--dry-run` fail outright on a machine with no docker, which is
        # exactly the machine it is most useful on.
        info("would stop gemm if running, then take the camera")
        return True

    if gemm_running(runner):
        warn(f"stage '{stage.name}' claims a shared sensor")
        info("stopping gemm to release it")
        if scripts_dir:
            runner.run_script(os.path.join(scripts_dir, "stop_gemm"))

    # stop_gemm frees GEMM's holders and deliberately leaves `videohub_pc4`
    # alone — that one is Unitree's, not gemm's to stop. But a stage that needs
    # the D435i needs it from whoever has it, and this has already said out loud
    # that it claims the device. Announcing a claim and then leaving the device
    # held is precisely how this reported success with a dead camera.
    if stage.claims_camera and scripts_dir:
        rc = runner.run_script(
            os.path.join(scripts_dir, "take_camera"), env={"C3PO_TAKE_CAMERA_YES": "1"}
        )
        if rc == 2:
            # take_camera distinguishes "not allowed" from "would not help".
            warn("the camera could not be released without a terminal — detector stays offline")
        elif rc != 0:
            warn("could not release the camera — the detector will stay offline")
    return True


def _await_ready(stage: Stage, runner: Runner, env: Dict[str, str]) -> int:
    if stage.readiness == "none":
        return 0

    if stage.readiness == "http":
        # `stt` publishes no ROS topic at all. Gating it on /c3po/world_summary
        # waited 90 s for something that would never arrive and then rolled back
        # a container that was working.
        url = "http://127.0.0.1:{}/transcribe/status".format(env.get("C3PO_STT_PORT", "8082"))
        info(f"waiting for the STT endpoint on {url}")
        # Generous: the entrypoint builds or loads the TensorRT plan before
        # exec'ing the server, a one-off couple of minutes on a cold volume.
        for _ in range(60):
            if runner.http_ok(url):
                ok("speech-to-text up (GPU, no sensors claimed)")
                return 0
            runner.sleep(3)
        err("the STT endpoint never answered — leaving the container up to inspect:")
        err(f"  docker logs {STT}")
        return 1

    info("waiting for /c3po/world_summary on domain 42")
    for _ in range(READY_ATTEMPTS):
        if not running_containers(runner):
            break
        code, _ = runner.docker(
            [
                "exec",
                NAV,
                "bash",
                "-lc",
                (
                    "source /opt/ros/humble/setup.bash;"
                    " source /opt/c3po/ws/install/setup.bash;"
                    f" timeout {PROBE_TIMEOUT_S} ros2 topic echo --once /c3po/world_summary"
                ),
            ]
        )
        if code == 0:
            ok(f"perception up on DDS domain 42 (stage={stage.name})")
            _check_vision(stage, runner, env)
            warn("/cmd_vel forwarding in the bridge stays OFF until arm_navigation is called")
            return 0
        runner.sleep(PROBE_INTERVAL_S)

    # The number is the WORST CASE and says so. The old message read
    # "after 90s" while the loop was 90 attempts of a 3 s timeout plus a 1 s
    # sleep — up to six minutes, reported as ninety seconds. Somebody timing
    # the failure against that number concludes the machine is wedged.
    err(
        f"no world summary after {READY_ATTEMPTS} attempts (up to {READY_ATTEMPTS * (PROBE_TIMEOUT_S + PROBE_INTERVAL_S)}s) — rolling back"
    )
    _dump_logs_before_rollback(runner)
    for name in existing_containers(runner):
        runner.docker(["rm", "-f", name])
    return 1


def _dump_logs_before_rollback(runner: Runner) -> None:
    """Print each container's tail BEFORE it is destroyed.

    THE ROLLBACK ITSELF IS CORRECT and must stay: `nav2` and `perception` claim
    the Livox and the RealSense away from gemm, so a failed bring-up that left
    its containers running would hold the other team's sensors hostage. That is
    exactly why the `stt` branch above does the opposite — it claims nothing,
    so leaving it up costs nobody anything.

    What was wrong is that the rollback destroyed the only evidence. On
    2026-08-26 `nav2-fake` reported "no world summary" and rolled back while
    every synthetic publisher was at message #200+ and world_model_publisher,
    planner_server and the lifecycle manager were all alive — and `docker logs`
    had already been deleted by the time anyone looked. The second run had to
    be wrapped in a polling loop just to catch the output.

    So: release the sensors, keep the evidence. The tail goes to the terminal
    the operator is already looking at rather than a file they must be told
    about.
    """
    for name in existing_containers(runner):
        code, out = runner.docker(["logs", "--tail", "40", name])
        if code != 0 or not out.strip():
            continue
        err(f"  --- last lines of {name} (the container is about to be removed) ---")
        for line in out.strip().splitlines():
            err(f"  {line}")


def _check_vision(stage: Stage, runner: Runner, env: Dict[str, str]) -> None:
    """Never fatal, and separate from the topic gate on purpose.

    The gate above proves the NAV container is publishing and says nothing about
    the vision container — which is a different container, owns the D435i, and
    serves the camera. That is how "perception up" was printed with the camera
    dead through two headset sessions.
    """
    if VISION not in stage.containers:
        return
    if VISION not in running_containers(runner):
        warn("the VISION container is not running — there will be no camera")
        warn("  (the check above only proves the NAV container is publishing)")
        return
    port = env.get("C3PO_VISION_STREAM_PORT", "8081")
    for _ in range(20):
        if runner.http_ok(f"http://127.0.0.1:{port}/status", timeout=2.0):
            ok(f"camera stream answering on {port}")
            return
        runner.sleep(1)
    warn(f"the vision container is Up but {port} is not answering after 20s")
    warn("  usually the one-off TensorRT engine build (2-5 min). Re-run to check.")


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    dry_run = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]
    if not args:
        err("usage: perception_up {" + "|".join(STAGES) + "} [--dry-run]")
        return 2
    scripts_dir = os.environ.get("C3PO_SCRIPTS_DIR", "")
    return bring_up(args[0], Runner(), scripts_dir=scripts_dir, dry_run=dry_run)


if __name__ == "__main__":
    sys.exit(main())
