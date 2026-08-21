"""The build guardrails, without a Jetson.

Each build is tens of minutes on cores shared with another team, so refusing
early and for a stated reason is worth more here than anywhere else. These are
the refusals.
"""

from __future__ import annotations

import pytest

from bringup.build import (
    MIN_FREE_GB,
    dockerfile_problems,
    free_gb_from_df,
    images_for,
    missing_packages,
    power_mode_finding,
    precheck,
    steps_for,
)

OK_ARGS = {
    "arch": "aarch64",
    "free_gb": 100,
    "gemm_running": [],
    "ack_shared": False,
    "interactive": True,
    "target": "vision",
}


# --- classic builder only ---------------------------------------------------


def test_a_plain_dockerfile_has_no_problems():
    assert dockerfile_problems("FROM ubuntu:20.04\nRUN apt-get update\n") == []


def test_run_mount_is_rejected():
    assert dockerfile_problems("RUN --mount=type=cache apt-get update\n")


def test_from_platform_is_rejected_even_though_it_would_not_error():
    """The worse of the two: it is ACCEPTED and ignored.

    The build then succeeds and produces an image for the wrong architecture,
    with nothing in the log saying so.
    """
    assert dockerfile_problems("FROM --platform=linux/arm64 ubuntu:20.04\n")


def test_a_syntax_frontend_is_rejected():
    assert dockerfile_problems("# syntax=docker/dockerfile:1.4\nFROM x\n")


def test_the_word_mount_in_a_comment_is_not_a_problem():
    """Anchored to a RUN at the start of a line, so prose is safe."""
    assert dockerfile_problems("# we do not use RUN --mount here\nFROM x\n") == []


def test_each_problem_is_reported_separately():
    text = "# syntax=docker/dockerfile:1\nFROM --platform=linux/arm64 x\nRUN --mount=type=cache y\n"
    assert len(dockerfile_problems(text)) == 3


# --- refusing early ---------------------------------------------------------


def test_the_wrong_architecture_refuses_and_says_where_to_run_it():
    ok, lines = precheck(**dict(OK_ARGS, arch="x86_64"))
    assert not ok
    assert any("ssh c3po" in line for line in lines)


def test_too_little_disk_refuses_with_both_numbers():
    ok, lines = precheck(**dict(OK_ARGS, free_gb=10))
    assert not ok
    assert "10G" in " ".join(lines) and str(MIN_FREE_GB) in " ".join(lines)


def test_an_unreadable_disk_figure_does_not_block_the_build():
    """"Do not know" must not be treated as "not enough".

    Refusing a build because a parser could not read a number would be worse
    than the disk problem it guards against.
    """
    ok, _ = precheck(**dict(OK_ARGS, free_gb=None))
    assert ok


def test_an_unknown_target_prints_the_usage():
    ok, lines = precheck(**dict(OK_ARGS, target="nonsense"))
    assert not ok
    assert any("usage:" in line for line in lines)


# --- the co-tenant ----------------------------------------------------------


def test_a_running_co_tenant_blocks_an_unattended_build():
    """A build opens no device, so this is about CPU, not sensors — but tens of
    minutes of eight shared cores still deserves a conversation."""
    ok, lines = precheck(
        **dict(OK_ARGS, gemm_running=["gemm-bringup"], interactive=False, ack_shared=False)
    )
    assert not ok
    assert "C3PO_BUILD_ACK_SHARED=1" in " ".join(lines)


def test_the_acknowledgement_lets_it_through_unattended():
    ok, _ = precheck(
        **dict(OK_ARGS, gemm_running=["gemm-bringup"], interactive=False, ack_shared=True)
    )
    assert ok


def test_an_idle_co_tenant_needs_no_ceremony():
    ok, lines = precheck(**dict(OK_ARGS, gemm_running=[], interactive=False))
    assert ok and lines == []


# --- reading the machine ----------------------------------------------------


def test_the_disk_figure_is_the_number_not_the_header():
    assert free_gb_from_df("Avail\n123G\n") == 123


def test_unreadable_df_output_is_none_rather_than_zero():
    """Zero would mean "full" and refuse the build; None means "cannot tell"."""
    assert free_gb_from_df("") is None
    assert free_gb_from_df("Avail\n") is None


@pytest.mark.parametrize("raw", ["pmode:0000 MAXN", "NV Power Mode: MAXN"])
def test_maxn_is_recognised(raw):
    assert power_mode_finding(raw)[0] == "ok"


def test_a_reduced_power_mode_marks_the_numbers_as_not_maxn():
    level, message = power_mode_finding("pmode:0003 15W")
    assert level == "warn"
    assert "NOT MAXN" in message


def test_an_unreadable_power_mode_says_the_numbers_are_unattributed():
    """A latency quoted later as "the engine does 6 ms" becomes a compute budget
    nobody can reproduce."""
    level, message = power_mode_finding("")
    assert level == "warn"
    assert "unattributed" in message


# --- the workspace actually contains what it should -------------------------


def test_a_complete_workspace_is_missing_nothing():
    listing = (
        "fast_lio\nlivox_ros_driver2\nc3po_perception\n"
        "nav2_controller\nnav2_bt_navigator\npointcloud_to_laserscan\nsomething_else\n"
    )
    assert missing_packages(listing) == []


def test_a_missing_package_is_named():
    assert "fast_lio" in missing_packages("c3po_perception\nnav2_controller\n")


def test_a_neighbouring_package_does_not_satisfy_the_check():
    """Substring matching would pass on `nav2_controller_something`.

    The build would then look fine and fail later, on the robot, as a launch
    file that cannot find a node.
    """
    assert "nav2_controller" in missing_packages("nav2_controller_extras\n")


# --- what each target does --------------------------------------------------


def test_all_builds_both_images():
    assert set(images_for("all")) == {"vision", "nav"}


def test_all_builds_the_engine_but_does_not_bench():
    """A benchmark is a number somebody reads and acts on, so it should be
    asked for rather than produced as a side effect."""
    assert steps_for("all") == ["vision", "engine", "nav"]
    assert "bench" not in steps_for("all")


def test_a_single_target_runs_only_itself():
    assert steps_for("nav") == ["nav"]
    assert images_for("bench") == {}
