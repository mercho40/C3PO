"""The bring-up SEQUENCE, with docker replaced by a recorder.

What gets removed, what gets created, in what order, and what is refused — all
of it on a laptop. This is the half that used to be unobservable: the shell
version could only be checked by running it on the robot and watching what the
other team lost.
"""

from __future__ import annotations

import os
from typing import List, Sequence, Tuple

import pytest

from bringup.cli import bring_up
from bringup.spec import NAV, STT, VISION


class FakeRunner:
    """Records every docker call; answers queries from a scripted world."""

    def __init__(
        self,
        existing=(),
        running=(),
        gemm=(),
        topic_ready=True,
        http_ready=True,
        logs=None,
        script_rc=None,
    ):
        #: Exit code per helper script, by basename. 127 is "not on disk" —
        #: see `Runner.run_script`. Defaults to 0 for anything unlisted.
        self._script_rc = dict(script_rc or {})
        self.calls: List[List[str]] = []
        self.scripts: List[Tuple[str, dict]] = []
        self.slept = 0.0
        self.dirs: List[str] = []
        self._existing = list(existing)
        self._running = list(running)
        self._gemm = list(gemm)
        self._topic_ready = topic_ready
        self._http_ready = http_ready
        self._logs = dict(logs or {})

    def docker(self, args: Sequence[str], capture: bool = True) -> Tuple[int, str]:
        args = list(args)
        self.calls.append(args)
        if args[:1] == ["ps"]:
            name_filter = args[args.index("--filter") + 1] if "--filter" in args else ""
            if "gemm" in name_filter:
                return 0, "\n".join(self._gemm)
            pool = self._existing if "-a" in args else self._running
            return 0, "\n".join(pool)
        if args[:2] == ["image", "inspect"]:
            return 0, "sha256:deadbeef"
        if args[:1] == ["create"]:
            name = args[args.index("--name") + 1]
            self._existing.append(name)
            return 0, ""
        if args[:1] == ["start"]:
            self._running.append(args[1])
            return 0, ""
        if args[:1] == ["rm"]:
            target = args[-1]
            self._existing = [c for c in self._existing if c != target]
            self._running = [c for c in self._running if c != target]
            return 0, ""
        if args[:1] == ["exec"]:
            return (0 if self._topic_ready else 1), ""
        if args[:1] == ["logs"]:
            return 0, self._logs.get(args[-1], "")
        return 0, ""

    def http_ok(self, url: str, timeout: float = 3.0) -> bool:
        return self._http_ready

    def sleep(self, seconds: float) -> None:
        self.slept += seconds

    def makedirs(self, path: str) -> None:
        self.dirs.append(path)

    def run_script(self, path, env=None):
        self.scripts.append((path, dict(env or {})))
        return self._script_rc.get(os.path.basename(path), 0)


ENV = {"HOME": "/home/unitree", "C3PO_DIR": "/home/unitree/c3po"}


def created(runner: FakeRunner) -> List[str]:
    return [c[c.index("--name") + 1] for c in runner.calls if c[:1] == ["create"]]


def removed(runner: FakeRunner) -> List[str]:
    return [c[-1] for c in runner.calls if c[:1] == ["rm"]]


# --- composition ------------------------------------------------------------


def test_starting_stt_does_not_remove_a_running_nav2():
    """The bug: the two stages designed to coexist could not.

    The failure was quiet in the worst direction — the bridge silently fell back
    to CPU whisper, three times slower, and said so only in a log line written
    at that moment.
    """
    runner = FakeRunner(existing=[NAV, VISION], running=[NAV, VISION])
    assert bring_up("stt", runner, env=ENV) == 0
    assert NAV not in removed(runner)
    assert VISION not in removed(runner)
    assert created(runner) == [STT]


def test_starting_nav2_does_not_remove_a_running_stt():
    runner = FakeRunner(existing=[STT], running=[STT])
    assert bring_up("nav2", runner, env=ENV) == 0
    assert STT not in removed(runner)
    assert set(created(runner)) == {NAV, VISION}


def test_a_stage_replaces_its_own_stale_containers():
    runner = FakeRunner(existing=[NAV, VISION], running=[])
    bring_up("nav2", runner, env=ENV)
    assert set(removed(runner)) == {NAV, VISION}


# --- arbitration ------------------------------------------------------------


def test_a_sensor_free_stage_never_touches_gemm():
    """nav2-fake exists to be the sensor-free one; for a while it took both."""
    runner = FakeRunner(gemm=["gemm-bringup"])
    bring_up("nav2-fake", runner, env=ENV, scripts_dir="/s")
    assert runner.scripts == []


def test_stt_never_touches_gemm_either():
    runner = FakeRunner(gemm=["gemm-bringup"])
    bring_up("stt", runner, env=ENV, scripts_dir="/s")
    assert runner.scripts == []


def test_a_camera_stage_stops_gemm_and_then_takes_the_camera():
    """stop_gemm frees gemm's holders and deliberately not Unitree's.

    A stage that announced a camera claim and stopped there is how this
    reported success with the device still held.
    """
    runner = FakeRunner(gemm=["gemm-bringup"])
    bring_up("nav2", runner, env=ENV, scripts_dir="/s")
    ran = [path for path, _ in runner.scripts]
    assert ran == ["/s/stop_gemm", "/s/take_camera"]


def test_taking_the_camera_is_confirmed_because_the_stage_was_chosen():
    runner = FakeRunner()
    bring_up("nav2", runner, env=ENV, scripts_dir="/s")
    _, env = next(s for s in runner.scripts if s[0].endswith("take_camera"))
    assert env["C3PO_TAKE_CAMERA_YES"] == "1"


def test_the_lidar_driver_override_makes_an_otherwise_free_stage_claim():
    runner = FakeRunner(gemm=["gemm-bringup"])
    bring_up("odometry", runner, env=dict(ENV, C3PO_LIDAR_SOURCE="driver"), scripts_dir="/s")
    assert "/s/stop_gemm" in [path for path, _ in runner.scripts]


# --- readiness --------------------------------------------------------------


def test_stt_readiness_is_its_own_endpoint_not_a_ros_topic():
    runner = FakeRunner(http_ready=True)
    assert bring_up("stt", runner, env=ENV) == 0
    assert not any(c[:1] == ["exec"] for c in runner.calls)


def test_a_stt_container_that_never_answers_is_left_up_to_inspect():
    """Rolling it back would delete the logs that say why."""
    runner = FakeRunner(http_ready=False)
    assert bring_up("stt", runner, env=ENV) == 1
    assert removed(runner) == []


def test_a_nav_stack_that_never_publishes_is_rolled_back():
    runner = FakeRunner(topic_ready=False)
    assert bring_up("nav2", runner, env=ENV) == 1
    assert set(removed(runner)) >= {NAV, VISION}


def test_the_logs_are_printed_before_the_rollback_destroys_them(capsys):
    """Release the sensors, but keep the evidence.

    On 2026-08-26 `nav2-fake` reported "no world summary" and rolled back while
    every synthetic publisher was at message #200+ and world_model_publisher,
    planner_server and the lifecycle manager were all alive. `docker logs` had
    already been deleted by the time anyone looked, so the first failure said
    nothing at all and the run had to be repeated inside a polling loop just to
    catch the output.
    """
    runner = FakeRunner(topic_ready=False, logs={NAV: "boom: could not start\nsecond line"})
    assert bring_up("nav2", runner, env=ENV) == 1
    captured = capsys.readouterr()  # ONE call: a second returns only new output
    printed = captured.out + captured.err
    assert "boom: could not start" in printed, "the tail must reach the operator"
    # And it must still be removed: nav2 holds the Livox and the RealSense away
    # from gemm, so a failed bring-up that leaves containers up takes the other
    # team's sensors hostage.
    assert NAV in removed(runner)


def test_the_logs_are_read_before_the_container_is_removed(capsys):
    """Order matters, and it is not observable from the output alone."""
    runner = FakeRunner(topic_ready=False, logs={NAV: "something"})
    bring_up("nav2", runner, env=ENV)
    kinds = [c[0] for c in runner.calls if c[:1] in (["logs"], ["rm"])]
    assert "logs" in kinds and "rm" in kinds
    assert kinds.index("logs") < kinds.index("rm"), (
        "logs were read after the container was destroyed, which is the bug"
    )


def test_the_probe_timeout_tolerates_a_loaded_box():
    """The measured number, pinned.

    2.25 s at load 10 against a 3 s ceiling; over 3 s at load 15 — which is
    just what the box reads while the other team runs SLAM. The ceiling has to
    clear that by enough that a busy robot is not a failed bring-up.
    """
    from bringup.cli import PROBE_TIMEOUT_S

    assert PROBE_TIMEOUT_S >= 10


def test_the_failure_message_states_the_real_worst_case():
    """It used to say "after 90s" for a loop that could take six minutes.

    Somebody timing the failure against that number concludes the machine is
    wedged and starts killing things.
    """
    from bringup.cli import PROBE_INTERVAL_S, PROBE_TIMEOUT_S, READY_ATTEMPTS

    worst = READY_ATTEMPTS * (PROBE_TIMEOUT_S + PROBE_INTERVAL_S)
    assert worst <= 360, "a bring-up must not be able to hang longer than the old one"


def test_a_dead_camera_does_not_fail_a_nav_bring_up():
    """Somebody running the nav stack does not care about 8081.

    They are told, and the bring-up still succeeds.
    """
    runner = FakeRunner(topic_ready=True, http_ready=False)
    assert bring_up("nav2", runner, env=ENV) == 0


# --- refusals and dry runs --------------------------------------------------


def test_an_unknown_stage_is_refused_before_anything_runs():
    runner = FakeRunner()
    assert bring_up("nonsense", runner, env=ENV) == 2
    assert runner.calls == []


def test_dry_run_touches_nothing(capsys):
    runner = FakeRunner(existing=[NAV], gemm=["gemm-bringup"])
    assert bring_up("nav2", runner, env=ENV, scripts_dir="/s", dry_run=True) == 0
    assert not any(c[:1] in (["create"], ["start"], ["rm"]) for c in runner.calls)
    assert runner.scripts == []
    out = capsys.readouterr().out
    assert "docker create --name " + NAV in out


@pytest.mark.parametrize("stage", ["fake", "nav2-fake", "stt", "odometry", "perception", "nav2"])
def test_every_stage_dry_runs_without_touching_the_machine(stage):
    """Not a single docker call, including the read-only ones.

    Asserting only that nothing is created/started/removed was too weak: the
    first version still ran `docker ps` to see whether gemm was up, so
    `--dry-run` blew up with a traceback on any machine without docker — which
    is precisely the machine it exists to be useful on.
    """
    runner = FakeRunner()
    assert bring_up(stage, runner, env=ENV, scripts_dir="/s", dry_run=True) == 0
    assert runner.calls == []
    assert runner.scripts == []
    assert runner.dirs == []


# --- a helper script that is not on disk -------------------------------------
#
# On 2026-08-29 `scripts/robot/stop_gemm`, `take_camera` and `run_c3po` were all
# missing from the robot's working tree — tracked in git, gone from disk, cause
# never established. Every camera-claiming stage then died on a raw
# FileNotFoundError traceback out of `subprocess.call`, with the real problem on
# the last line. The two cases below are the same accident wanting opposite
# answers, which is the whole reason 127 is told apart from every other rc.


def test_a_missing_stop_gemm_refuses_rather_than_starting_anyway():
    """gemm is running and holding the sensor. Carrying on is the worst option.

    Starting the containers with gemm still on the device is the "one commander"
    failure `_common.sh` exists to prevent, and it presents as a detector that
    comes up clean and sees nothing — a far longer debugging session than a
    refusal that names the missing file.
    """
    runner = FakeRunner(gemm=["gemm-ai"], script_rc={"stop_gemm": 127})
    assert bring_up("perception", runner, env=ENV, scripts_dir="/s") == 1
    assert created(runner) == [], "nothing may start while gemm holds the device"


def test_the_refusal_names_the_containers_it_actually_found(capsys):
    """The manual fallback has to act on what was detected, which is containers.

    It first read `sudo systemctl stop gemm-ai`, which is wrong in a way that
    costs an operator real time: OPERATIONS is explicit that `stop_gemm` does
    NOT stop `gemm-ai.service` — it filters running *containers*, and that is
    equally what `gemm_running()` here sees. Naming the unit sends somebody to
    stop a voice assistant while the containers keep the camera.
    """
    runner = FakeRunner(gemm=["gemm-bringup", "gemm-slam"], script_rc={"stop_gemm": 127})
    bring_up("perception", runner, env=ENV, scripts_dir="/s")
    printed = capsys.readouterr()
    text = printed.out + printed.err
    assert "docker stop gemm-bringup gemm-slam" in text
    assert "systemctl" not in text


def test_a_missing_stop_gemm_is_not_fatal_when_gemm_is_not_running():
    """Nothing to release, so its absence costs this stage nothing."""
    runner = FakeRunner(script_rc={"stop_gemm": 127})
    assert bring_up("perception", runner, env=ENV, scripts_dir="/s") == 0


def test_a_missing_take_camera_warns_but_still_brings_the_stage_up():
    """Different problem, opposite answer.

    Nothing is announced to be holding the device, so the detector may simply
    find it — and if it does not, the stage reports it offline honestly.
    Refusing here would cost the lidar ring too, for a camera that might have
    worked.
    """
    runner = FakeRunner(script_rc={"take_camera": 127})
    assert bring_up("perception", runner, env=ENV, scripts_dir="/s") == 0
    assert VISION in created(runner)


def test_the_absent_and_the_refused_camera_do_not_share_a_message(capsys):
    """"could not release the camera" sends somebody into udev and permissions.

    For a file that is simply not on disk that is the wrong hour to spend, so
    rc 127 and rc 2 must not print the same sentence.
    """
    absent = FakeRunner(script_rc={"take_camera": 127})
    bring_up("perception", absent, env=ENV, scripts_dir="/s")
    absent_out = capsys.readouterr()  # ONE call: a second returns only new output

    refused = FakeRunner(script_rc={"take_camera": 2})
    bring_up("perception", refused, env=ENV, scripts_dir="/s")
    refused_out = capsys.readouterr()

    assert "missing" in absent_out.out + absent_out.err
    assert "missing" not in refused_out.out + refused_out.err


def test_run_script_reports_127_instead_of_raising():
    """The crash itself, at its source: a return code, not a traceback."""
    from bringup.cli import Runner

    assert Runner().run_script("/nonexistent/definitely-not-a-real-helper") == 127
