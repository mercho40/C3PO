"""`.env` parsing, including the line that makes systemd's own loader wrong.

This exists so the bridge can be started directly by a unit file instead of
through the old shell launcher. Everything here is about
matching what `set -a; . ./.env` does closely enough that moving the
responsibility changes no behaviour.
"""

from __future__ import annotations

import os

from bridge.env_file import load_env_file, parse_env_file


def test_the_trailing_comment_that_systemd_gets_wrong():
    """The exact line from apps/bridge/.env.example.

    `EnvironmentFile=` keeps everything after the `=`, so SIM_MODE becomes
    "stub                  # 'stub' | ..." — not stub, not real, and not an
    error. The bridge would conclude it was on real hardware.
    """
    env = parse_env_file("SIM_MODE=stub                  # 'stub' | 'isaac' | 'real'\n")
    assert env["SIM_MODE"] == "stub"


def test_a_hash_without_leading_space_is_part_of_the_value():
    # A shell only treats `#` as a comment when whitespace precedes it, and
    # secrets contain hashes.
    assert parse_env_file("PASSWORD=a#b\n")["PASSWORD"] == "a#b"


def test_a_hash_inside_quotes_is_not_a_comment():
    assert parse_env_file('NOTE="a # b"\n')["NOTE"] == "a # b"


def test_quotes_are_stripped_like_a_shell_strips_them():
    env = parse_env_file('A="one two"\nB=\'three\'\nC=four\n')
    assert (env["A"], env["B"], env["C"]) == ("one two", "three", "four")


def test_export_prefix_is_accepted():
    # Valid in a file meant to be sourced, and people write it from habit.
    assert parse_env_file("export ROBOT_HOST=1.2.3.4\n")["ROBOT_HOST"] == "1.2.3.4"


def test_comments_and_blank_lines_are_ignored():
    assert parse_env_file("\n# a comment\n\n  # indented\nA=1\n") == {"A": "1"}


def test_an_empty_value_stays_empty_rather_than_becoming_a_comment():
    # DDS_INTERFACE= ships blank on purpose; it must not vanish or gain text.
    assert parse_env_file("DDS_INTERFACE=\nB=2\n")["DDS_INTERFACE"] == ""


def test_a_line_that_is_not_an_assignment_is_skipped():
    assert parse_env_file("this is prose\nA=1\n") == {"A": "1"}


def test_urls_keep_their_scheme_and_query():
    value = "postgresql://u:p@host/db?sslmode=require&channel_binding=require"
    assert parse_env_file(f"DATABASE_URL={value}\n")["DATABASE_URL"] == value


# --- applying it ------------------------------------------------------------


def test_the_existing_environment_wins_by_default(tmp_path, monkeypatch):
    """A `SIM_MODE=x` prefix on the command must beat the file.

    Otherwise overriding anything on the robot means editing .env and
    remembering to change it back — and forgetting is how a stub-mode session
    becomes a real-hardware one.
    """
    env_file = tmp_path / ".env"
    env_file.write_text("SIM_MODE=stub\n")
    monkeypatch.setenv("SIM_MODE", "real")

    applied = load_env_file(env_file)

    assert os.environ["SIM_MODE"] == "real"
    assert "SIM_MODE" not in applied


def test_override_is_available_but_not_the_default(tmp_path, monkeypatch):
    env_file = tmp_path / ".env"
    env_file.write_text("SIM_MODE=stub\n")
    monkeypatch.setenv("SIM_MODE", "real")

    load_env_file(env_file, override=True)

    assert os.environ["SIM_MODE"] == "stub"


def test_a_missing_file_is_not_an_error(tmp_path):
    # The bridge runs from a checkout with no .env in stub mode.
    assert load_env_file(tmp_path / "nope.env") == {}


def test_the_real_example_file_parses(tmp_path):
    """Parse the repo's own .env.example, which is the documented authority."""
    from pathlib import Path

    example = Path(__file__).resolve().parents[1] / ".env.example"
    env = parse_env_file(example.read_text())
    assert env["SIM_MODE"] == "stub"
    assert env["BRIDGE_HOST"] == "127.0.0.1"
    assert env["BRIDGE_PORT"] == "8001"
    # Every value must be free of the comment text that follows it on the line.
    for name, value in env.items():
        assert "#" not in value or "'" in value, f"{name} kept its inline comment: {value!r}"
