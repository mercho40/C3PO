"""Building the perception images, with the checks as functions.

The images build ON the robot — aarch64, JetPack 5, no buildx, no compose — and
each build is tens of minutes on eight cores shared with another team. That
combination is why this script is mostly guardrails: refusing early, loudly, and
for a stated reason is worth far more here than anywhere else in the stack,
because the alternative is finding out forty minutes in.

Every guardrail is a pure function over text, so the refusals can be tested
without a Jetson, an ONNX file, or a co-tenant to annoy.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Sequence, Tuple

from bringup.spec import NAV_IMAGE, VISION_IMAGE

TARGETS = ("all", "vision", "engine", "nav", "bench")

USAGE = [
    "usage: build_perception {all|vision|engine|nav|bench}",
    "  vision  the L4T/TensorRT image      (~25 min after the 5.3 GB base pull)",
    "  engine  the .plan, from your ONNX   (needs the GPU, no camera)",
    "  nav     the Humble/Nav2 image       (~35 min, -j3 on 8 shared cores)",
    "  bench   trtexec on the built engine (the number that decides the budget)",
]

#: Below this, the two images plus their intermediate layers do not fit and the
#: build dies most of the way through with a disk error rather than a reason.
MIN_FREE_GB = 40

#: What `ros2 pkg list` must contain for the nav image to be worth starting.
REQUIRED_NAV_PACKAGES = (
    "fast_lio",
    "livox_ros_driver2",
    "c3po_perception",
    "nav2_controller",
    "nav2_bt_navigator",
    "pointcloud_to_laserscan",
)


# --- the Dockerfile must be classic-builder-only ----------------------------


def dockerfile_problems(text: str) -> List[str]:
    """BuildKit-only syntax this daemon cannot run, or silently ignores.

    The distinction matters and is why each is checked separately: `RUN --mount`
    is a hard parse error, while `FROM --platform` is accepted and IGNORED —
    which is worse, because the build succeeds and produces an image for the
    wrong architecture with nothing in the log to say so.
    """
    problems = []
    if re.search(r"^[ \t]*RUN[ \t]+--mount", text, re.MULTILINE):
        problems.append("uses RUN --mount — BuildKit only, and this daemon has no buildx")
    if re.search(r"^[ \t]*FROM[ \t]+--platform", text, re.MULTILINE | re.IGNORECASE):
        problems.append("uses FROM --platform — BuildKit only, and it is silently ignored here")
    if re.search(r"^[ \t]*#[ \t]*syntax=", text, re.MULTILINE | re.IGNORECASE):
        problems.append("declares a # syntax= frontend — BuildKit only")
    return problems


# --- refusing before forty minutes are wasted -------------------------------


def precheck(
    *,
    arch: str,
    free_gb: Optional[int],
    gemm_running: Sequence[str],
    ack_shared: bool,
    interactive: bool,
    target: str,
) -> Tuple[bool, List[str]]:
    """(may proceed, what to say). Refusals come with the fix attached."""
    if target not in TARGETS:
        return False, list(USAGE)

    if arch != "aarch64":
        return False, [
            f"this is {arch}, not aarch64 — these images are L4T r35.3.1 / arm64 only",
            "there is no buildx on the robot's docker, so there is nothing to cross-build with",
            f"run this on the Jetson:  ssh c3po 'build_perception {target}'",
        ]

    if free_gb is not None and free_gb < MIN_FREE_GB:
        return False, [
            (
                f"need >{MIN_FREE_GB}G free on / for both images and the "
                f"intermediate layers (have {free_gb}G)"
            )
        ]

    if gemm_running:
        # Not a sensor claim — a build opens no device. It is a courtesy check
        # about CPU: tens of minutes of eight shared cores while somebody else
        # is trying to work.
        if ack_shared:
            return True, ["C3PO_BUILD_ACK_SHARED=1 — assuming that conversation already happened"]
        if not interactive:
            return False, [
                "not a terminal, and the other team's stack is up",
                "ask them, then re-run with C3PO_BUILD_ACK_SHARED=1",
            ]
        return True, ["gemm is running — this build takes most of 8 shared cores for tens of minutes"]

    return True, []


# --- reading the machine's answers ------------------------------------------


def free_gb_from_df(output: str) -> Optional[int]:
    """Parse `df -BG --output=avail /`. None when it cannot be read.

    None means "do not know", and the caller proceeds — refusing a build
    because a parser could not read a number would be worse than the disk
    problem it is guarding against.
    """
    digits = re.findall(r"\d+", output or "")
    return int(digits[-1]) if digits else None


def power_mode_finding(raw: str) -> Tuple[str, str]:
    """(level, message) for the Jetson's power mode.

    Benchmark numbers are meaningless without it: a median latency measured at
    a reduced power mode, quoted later as "the engine does 6 ms", becomes a
    compute budget built on a number nobody can reproduce.
    """
    text = (raw or "").strip()
    if not text:
        return "warn", "could not read the power mode — treat the latencies below as unattributed"
    if "pmode:0000" in text or "MAXN" in text:
        return "ok", "MAXN"
    return "warn", "NOT MAXN — every latency below is for this power mode, not for MAXN"


def missing_packages(listing: str, required: Sequence[str] = REQUIRED_NAV_PACKAGES) -> List[str]:
    """Which required ROS packages are absent from `ros2 pkg list` output.

    Whole-line matching, not substring: `nav2_controller` appears inside
    `nav2_controller_something`, and a build that silently satisfied the check
    with a neighbouring package would be found later, on the robot, as a
    launch file that cannot find a node.
    """
    present = {line.strip() for line in (listing or "").splitlines() if line.strip()}
    return [pkg for pkg in required if pkg not in present]


# --- what each target does --------------------------------------------------


def images_for(target: str) -> Dict[str, str]:
    """Which image each build step produces. Data, so the shim cannot drift."""
    if target == "vision":
        return {"vision": VISION_IMAGE}
    if target == "nav":
        return {"nav": NAV_IMAGE}
    if target == "all":
        return {"vision": VISION_IMAGE, "nav": NAV_IMAGE}
    return {}


def steps_for(target: str) -> List[str]:
    """The ordered steps a target runs.

    `all` deliberately builds the engine after vision and does NOT bench: the
    benchmark is a number somebody reads and acts on, so it should be asked for
    rather than produced as a side effect of a build.
    """
    if target == "all":
        return ["vision", "engine", "nav"]
    return [target]


# --- the command ------------------------------------------------------------


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as fh:
            return fh.read()
    except OSError:
        return None


def _free_gb(runner) -> Optional[int]:
    code, out = runner.shell(["df", "-BG", "--output=avail", "/"])
    return free_gb_from_df(out) if code == 0 else None


def _gemm(runner) -> List[str]:
    code, out = runner.docker(["ps", "--filter", "name=^gemm", "--format", "{{.Names}}"])
    return [n for n in out.splitlines() if n.strip()] if code == 0 else []


def _power_mode(runner) -> str:
    text = _read("/var/lib/nvpmodel/status") or ""
    return text if text.strip() else runner.shell(["nvpmodel", "-q"])[1]


def run_build(
    target: str,
    runner,
    *,
    env: Optional[Dict[str, str]] = None,
    dry_run: bool = False,
    say=print,
) -> int:
    """Build what `target` names. Returns a process exit code."""
    import os

    from bringup.cli import err, info, ok, warn

    env = dict(env if env is not None else os.environ)
    home = env.get("HOME", "/home/unitree")
    c3po_dir = env.get("C3PO_DIR", os.path.join(home, "c3po"))
    perception_dir = os.path.join(c3po_dir, "apps", "perception")
    models_dir = env.get("C3PO_MODELS_DIR", os.path.join(home, ".c3po", "models"))
    engine_volume = env.get("C3PO_ENGINE_VOLUME", "c3po-trt-engines")

    allowed, lines = precheck(
        arch=env.get("C3PO_FAKE_ARCH") or os.uname().machine,
        free_gb=None if dry_run else _free_gb(runner),
        gemm_running=[] if dry_run else _gemm(runner),
        ack_shared=env.get("C3PO_BUILD_ACK_SHARED") == "1",
        interactive=os.isatty(0),
        target=target,
    )
    for line in lines:
        (info if allowed else err)(line)
    if not allowed:
        return 2

    for step in steps_for(target):
        if step in ("vision", "nav"):
            context = os.path.join(perception_dir, step)
            problems = dockerfile_problems(_read(os.path.join(context, "Dockerfile")) or "")
            if problems:
                for problem in problems:
                    err(f"{context}/Dockerfile {problem}")
                return 1
            image = images_for(step)[step]
            if dry_run:
                say(f"docker build -t {image} {context}")
                continue
            code, _ = runner.docker(["build", "-t", image, context], capture=False)
            if code != 0:
                err("build failed — the reason is usually one of the Dockerfile's own")
                err("assertions, not the last line. Search the output for ASSERT/Error.")
                return 1
            ok(f"built {image}")

        elif step == "engine":
            if not dry_run:
                for required in ("yolo11n.onnx", "labels.txt"):
                    if not os.path.exists(os.path.join(models_dir, required)):
                        err(f"no {required} in {models_dir}")
                        err("export it off-robot with opset<=17 — ultralytics defaults to 20,")
                        err("which TensorRT 8.5's parser REJECTS — then scp both files across.")
                        err("labels.txt must come from model.names of the SAME checkpoint, or")
                        err("a fine-tune silently reports 'person' for a traffic cone.")
                        return 1
            args = [
                "run", "--rm", "--runtime", "nvidia",
                "-v", f"{models_dir}:/opt/c3po/models:ro",
                "-v", f"{engine_volume}:/opt/c3po/engines",
                VISION_IMAGE, "true",
            ]
            if dry_run:
                say("docker " + " ".join(args))
                continue
            info("one-off, 2-5 min; --runtime nvidia is REQUIRED and is not the default here")
            runner.docker(args, capture=False)
            ok("engine step finished — check the number with: build_perception bench")

        elif step == "bench":
            args = [
                "run", "--rm", "--runtime", "nvidia",
                "-v", f"{engine_volume}:/opt/c3po/engines",
                VISION_IMAGE,
                "/usr/src/tensorrt/bin/trtexec",
                "--loadEngine=/opt/c3po/engines/yolo11n.fp16.plan",
                "--iterations=200", "--avgRuns=100", "--noDataTransfers",
            ]
            if dry_run:
                say("docker " + " ".join(args))
                continue
            level, message = power_mode_finding(_power_mode(runner))
            (ok if level == "ok" else warn)(message)
            info("expect a median GPU latency of 5-8 ms at 640; far off is the")
            info("compute-budget conversation, not a tuning detail")
            runner.docker(args, capture=False)

    if target in ("nav", "all") and not dry_run:
        code, listing = runner.docker(["run", "--rm", NAV_IMAGE, "bash", "-lc", "ros2 pkg list"])
        missing = missing_packages(listing if code == 0 else "")
        if missing:
            err("the nav image is missing: {}".format(" ".join(missing)))
            return 1
        ok("the workspace contains every package the launch files name")

    return 0


def main(argv: Optional[Sequence[str]] = None) -> int:
    import sys

    from bringup.cli import Runner

    args = list(sys.argv[1:] if argv is None else argv)
    dry_run = "--dry-run" in args
    args = [a for a in args if a != "--dry-run"]
    return run_build(args[0] if args else "all", Runner(), dry_run=dry_run)


if __name__ == "__main__":
    import sys

    sys.exit(main())
