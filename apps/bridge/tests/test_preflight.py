"""Preflight's judgements, without a robot, a headset or a tunnel.

Every one of these was previously only checkable by standing in front of the
hardware with the thing already broken — which is the worst moment to discover
that the check itself is wrong.
"""

from __future__ import annotations

import json

from bridge.preflight import (
    BAD,
    OK,
    WARN,
    Finding,
    Section,
    adb_devices,
    camera_findings,
    camera_url_from_env,
    classify_probe,
    estop_finding,
    headset_findings,
    port_of,
    tunnel_finding,
    verdict,
)


def levels(findings):
    return [f.level for f in findings]


def text_of(findings):
    return " | ".join(f.text + " " + " ".join(f.notes) for f in findings)


# --- the tunnel distinction -------------------------------------------------


def test_a_successful_probe_is_alive():
    assert classify_probe(0, "") == "alive"


def test_a_refusal_means_the_tunnel_is_not_up():
    assert classify_probe(7, "curl: (7) Failed to connect to 127.0.0.1 port 8001") == "nothing"


def test_an_empty_reply_means_the_tunnel_works_and_the_robot_does_not():
    """The distinction the whole check exists for.

    `ssh -L` binds its listener at setup time, so connect() succeeds whenever
    ssh is alive — which is how a forgotten run_teleop earns a green tick.
    """
    assert classify_probe(52, "curl: (52) Empty reply from server") == "empty"
    assert classify_probe(56, "Recv failure: Connection reset by peer") == "empty"


def test_a_timeout_is_a_wedged_tunnel_not_a_missing_one():
    assert classify_probe(28, "curl: (28) Operation timed out") == "timeout"


def test_an_http_level_complaint_still_means_something_answered():
    assert classify_probe(22, "curl: (22) The requested URL returned error: 404") == "alive"


def test_an_empty_reply_on_a_fatal_port_fails_and_names_the_starter():
    finding = tunnel_finding(8767, "teleop stream", True, "run_teleop", "empty")
    assert finding.level == BAD
    assert "run_teleop" in " ".join(finding.notes)
    assert "do NOT go looking at the tunnel" in " ".join(finding.notes)


def test_the_same_state_is_only_a_warning_on_a_non_fatal_port():
    assert tunnel_finding(8081, "camera", False, "perception_up", "empty").level == WARN


# --- reading the web app's config -------------------------------------------


def test_the_camera_url_comes_from_the_web_apps_env():
    url, source = camera_url_from_env("PUBLIC_ROBOT_CAM_URL=http://127.0.0.1:8001/camera\n")
    assert url == "http://127.0.0.1:8001/camera"
    assert source == "apps/web/.env"


def test_a_trailing_comment_is_not_part_of_the_url():
    """The shell version's `cut -d= -f2- | tr -d '"' | xargs` kept the comment.

    It would then probe a nonsense URL and report the camera unreachable.
    """
    url, _ = camera_url_from_env("PUBLIC_ROBOT_CAM_URL=http://x:8081   # the vision one\n")
    assert url == "http://x:8081"


def test_the_shell_environment_is_the_fallback_not_the_authority():
    url, source = camera_url_from_env("# nothing here\n", shell_value="http://shell:9")
    assert (url, source) == ("http://shell:9", "the shell environment")


def test_no_url_anywhere_is_reported_as_the_configuration_problem_it_is():
    findings = camera_findings("", "", "alive", "", None)
    assert findings[0].level == BAD
    assert "not set in apps/web/.env" in findings[0].text


def test_the_port_is_taken_from_the_url_not_assumed():
    """Telling somebody to add -L 8081 when their URL says 8001 is the kind of
    confident wrong advice this script exists to end."""
    assert port_of("http://127.0.0.1:8001/camera") == 8001
    assert port_of("http://127.0.0.1:8081") == 8081
    assert port_of("http://example") == 80


# --- the camera verdict -----------------------------------------------------

LIVE = json.dumps({"live": True, "frames": 120})
NEVER = json.dumps({"live": False, "frames": 0})
STOPPED = json.dumps({"live": False, "frames": 900})


def test_live_with_real_frame_data_is_the_whole_chain_working():
    findings = camera_findings("http://x:8081", "env", "alive", LIVE, True)
    assert BAD not in levels(findings)
    assert "whole chain" in text_of(findings)


def test_frames_arriving_is_no_longer_reported_as_proof_the_rest_is_fine():
    """The hint this replaced pointed at the renderer, and was wrong.

    It used to say "if the headset still shows nothing after this, it is the
    renderer". On 2026-08-28 the chain worked all the way to the Mac —
    videohub live at 1920x1080, frames flowing — and the headset would still
    have been black, because the bridge sent no CORS header. Two of these have
    now cost a day each by confidently pointing at the renderer.
    """
    text = text_of(camera_findings("http://x:8001/camera", "env", "alive", LIVE, True))
    assert "it is the renderer" not in text


def test_frames_arriving_without_cors_is_a_failure_and_names_the_deploy():
    # The exact 2026-08-28 state: a live feed the console cannot read.
    findings = camera_findings(
        "http://x:8001/camera", "env", "alive", LIVE, True, cors_ok=False
    )
    text = text_of(findings)
    assert BAD in levels(findings)
    # It must say WHY the on-page panel looks fine while the headset does not,
    # because "but the camera works on the page" is what stops the search.
    assert "crossOrigin" in text
    assert "DEPLOY problem" in text
    assert "systemctl restart c3po-bridge" in text


def test_frames_plus_cors_is_the_clean_pass():
    findings = camera_findings(
        "http://x:8001/camera", "env", "alive", LIVE, True, cors_ok=True
    )
    assert BAD not in levels(findings)
    assert "the headset can load it" in text_of(findings)


def test_an_unaskable_cors_probe_neither_passes_nor_fails_it():
    # `None` means curl could not run or the request failed outright. That is
    # not evidence either way, and inventing a verdict from it would be the
    # same mistake as calling a missing ring a rendering bug.
    findings = camera_findings(
        "http://x:8001/camera", "env", "alive", LIVE, True, cors_ok=None
    )
    text = text_of(findings)
    assert BAD not in levels(findings)
    assert "Access-Control-Allow-Origin" not in text


def test_live_without_frame_data_is_a_failure_not_a_pass():
    """A 200 proves the socket opened. The multipart headers are sent before
    any frame exists, so only body content proves a picture."""
    findings = camera_findings("http://x:8081", "env", "alive", LIVE, False)
    assert BAD in levels(findings)


def test_never_produced_and_stopped_producing_are_different_diagnoses():
    never = text_of(camera_findings("http://x:8081", "env", "alive", NEVER, None))
    stopped = text_of(camera_findings("http://x:8081", "env", "alive", STOPPED, None))
    assert "NOTHING since it started" in never
    assert "arrived and then stopped" in stopped
    assert never != stopped


def test_the_servers_own_hint_beats_a_guess_from_outside():
    """The bridge's relay knows which feed is dark and why."""
    body = json.dumps({"live": False, "frames": 0, "hint": "videohub_pc4 was killed"})
    assert "videohub_pc4 was killed" in text_of(
        camera_findings("http://x:8001/camera", "env", "alive", body, None)
    )
    # ...and without a hint it still says something useful.
    assert "Check the cable" in text_of(
        camera_findings("http://x:8081", "env", "alive", NEVER, None)
    )


def test_a_non_json_reply_is_flagged_rather_than_parsed_hopefully():
    findings = camera_findings("http://x:8081", "env", "alive", "<html>502</html>", None)
    assert WARN in levels(findings)


def test_nothing_listening_names_the_right_owner_for_each_port():
    bridge_side = text_of(camera_findings("http://x:8001/camera", "env", "empty", "", None))
    vision_side = text_of(camera_findings("http://x:8081", "env", "empty", "", None))
    assert "BRIDGE is not up" in bridge_side
    assert "vision container is not up" in vision_side


# --- the headset ------------------------------------------------------------

ADB_AUTHORISED = "List of devices attached\n1WMHH815A00123\tdevice\n"
ADB_UNAUTHORISED = "List of devices attached\n1WMHH815A00123\tunauthorized\n"


def test_an_unauthorised_headset_points_inside_the_headset():
    """The single easiest thing to miss, and it looks exactly like a bad cable."""
    findings = headset_findings(True, ADB_UNAUTHORISED, "")
    assert findings[0].level == BAD
    assert "INSIDE the headset" in " ".join(findings[0].notes)


def test_no_headset_is_a_warning_not_a_failure():
    assert headset_findings(True, "List of devices attached\n", "")[0].level == WARN


def test_missing_adb_is_fatal_and_says_how_to_install_it():
    findings = headset_findings(False, "", "")
    assert findings[0].level == BAD
    assert "brew install" in " ".join(findings[0].notes)


def test_each_missing_reverse_forward_is_reported():
    findings = headset_findings(True, ADB_AUTHORISED, "tcp:3000 tcp:3001")
    missing = [f for f in findings if f.level == WARN]
    assert len(missing) == 2  # 8081 and 8767
    assert "8081" in text_of(missing)


def test_adb_output_parsing_ignores_the_header_and_blank_lines():
    authorised, unauthorised = adb_devices(ADB_AUTHORISED + "\n")
    assert authorised == ["1WMHH815A00123"] and unauthorised == []


# --- a standing stop --------------------------------------------------------


def test_an_outstanding_stop_is_a_warning_with_an_explanation():
    """It is working as designed. Reading it as a fault sends somebody looking
    for a broken robot."""
    finding = estop_finding(stop_at=200.0, ack_at=100.0, when="11:40:33 on 21 Aug")
    assert finding.level == WARN
    assert "outlives the session" in " ".join(finding.notes)
    assert "11:40:33" in " ".join(finding.notes)


def test_an_acknowledged_stop_is_clear():
    assert estop_finding(stop_at=100.0, ack_at=200.0).level == OK


def test_no_stop_file_is_clear():
    assert estop_finding(stop_at=None, ack_at=None).level == OK


# --- whose stop, though -----------------------------------------------------
#
# The sentinel is per-machine: estop.py puts it at $HOME/.c3po/run on whatever
# host is running. For real hardware the bridge runs ONBOARD, so the sentinel
# that latches teleop is the robot's — and preflight runs on the operator's Mac
# and reads the Mac's. Both directions of that mislead, and the second is the
# dangerous one.


def test_a_standing_stop_says_which_machine_it_read():
    """Otherwise a months-old local sim stop reads as a live safety state on the
    robot. This Mac carried one from 2026-08-20 for days."""
    finding = estop_finding(
        stop_at=200.0, ack_at=100.0, when="16:50:57 on 20 Aug", run_dir="/Users/x/.c3po/run"
    )
    assert finding.level == WARN
    assert "THIS MACHINE" in finding.text
    notes = " ".join(finding.notes)
    assert "/Users/x/.c3po/run" in notes
    # And it must point at where the authoritative one lives.
    assert "onboard" in notes.lower()
    assert "ssh c3po" in notes


def test_a_clear_result_admits_it_cannot_see_the_robots():
    """The dangerous direction: a REAL standing stop onboard is invisible from
    here, and a bare 'no stop outstanding' would be read as covering it."""
    finding = estop_finding(stop_at=None, ack_at=None)
    assert finding.level == OK
    assert "this machine" in finding.text
    assert "cannot see" in " ".join(finding.notes).lower()


def test_run_dir_is_optional_so_existing_callers_still_work():
    finding = estop_finding(stop_at=200.0, ack_at=100.0)
    assert finding.level == WARN
    assert "THIS MACHINE" in finding.text


# --- the verdict ------------------------------------------------------------


def test_all_clear_says_put_the_headset_on():
    sections = [Section("x", [Finding(OK, "fine")])]
    failed, warned, closing = verdict(sections)
    assert (failed, warned) == (0, 0)
    assert "Put the headset on" in closing


def test_warnings_alone_do_not_stop_you():
    sections = [Section("x", [Finding(WARN, "hmm")])]
    failed, warned, closing = verdict(sections)
    assert (failed, warned) == (0, 1)
    assert "nothing fatal" in closing


def test_failures_are_counted_and_the_reason_is_stated():
    sections = [Section("x", [Finding(BAD, "no"), Finding(BAD, "also no"), Finding(WARN, "eh")])]
    failed, warned, closing = verdict(sections)
    assert (failed, warned) == (2, 1)
    assert "2 thing(s) will stop you" in closing
    assert "cannot debug from in there" in closing


# --- probing a port that may be bound on one family only --------------------


def test_a_server_listening_on_only_one_address_family_is_found():
    """vite binds [::1]:3001 and nothing on 127.0.0.1.

    Probing the IPv4 literal alone reported "not listening" and told the
    operator to start a dev server that was already running — the exact false
    alarm this file exists to prevent. The original shell script did this.
    """
    from bridge.preflight import best_state

    assert best_state(["nothing", "alive"]) == "alive"
    assert best_state(["alive", "nothing"]) == "alive"


def test_when_nothing_answers_the_first_diagnosis_is_kept():
    """refused / reset / timeout are different problems; flattening them would
    lose the distinction the tunnel section is built on."""
    from bridge.preflight import best_state

    assert best_state(["empty", "nothing"]) == "empty"
    assert best_state(["timeout", "nothing"]) == "timeout"
    assert best_state([]) == "nothing"
