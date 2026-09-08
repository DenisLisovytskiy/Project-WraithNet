"""Layer-1 CLI parsing tests for python -m launcher. No pygame/panda3d required."""

from __future__ import annotations

import subprocess
import sys

import pytest

from launcher.cli import USAGE, LauncherArgs, parse_launcher_argv


def test_empty_argv_opens_gui():
    parsed = parse_launcher_argv([])
    assert parsed == LauncherArgs(role="gui", edit_path=None)


def test_edit_flag_alone_opens_gui():
    parsed = parse_launcher_argv(["--edit"])
    assert parsed.role == "gui"
    assert parsed.edit_path is None


def test_edit_flag_with_path():
    parsed = parse_launcher_argv(["--edit", "path.json"])
    assert parsed.role == "gui"
    assert parsed.edit_path == "path.json"


def test_headless_flag_alone():
    parsed = parse_launcher_argv(["--headless"])
    assert parsed.role == "headless"
    assert parsed.role_argv == ()


def test_headless_with_scenario():
    parsed = parse_launcher_argv(["--headless", "--scenario", "x.json"])
    assert parsed.role == "headless"
    assert parsed.role_argv == ("--scenario", "x.json")


def test_headless_with_all_live_flags():
    parsed = parse_launcher_argv(
        [
            "--headless",
            "--scenario",
            "x.json",
            "--live",
            "--live-host",
            "h",
            "--live-port",
            "9",
        ]
    )
    assert parsed.role == "headless"
    assert parsed.role_argv == (
        "--scenario",
        "x.json",
        "--live",
        "--live-host",
        "h",
        "--live-port",
        "9",
    )


def test_headless_equals_form_and_unknown_tokens_are_forwarded():
    parsed = parse_launcher_argv(
        ["--headless", "--scenario=x.json", "--cool-new-flag", "1"]
    )
    assert parsed.role == "headless"
    assert parsed.role_argv == ("--scenario=x.json", "--cool-new-flag", "1")


def test_headless_help_shows_wraithnet_main_help():
    result = subprocess.run(
        [sys.executable, "-m", "launcher", "--headless", "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0
    combined = result.stdout + result.stderr
    assert "--scenario" in combined
    assert "wraithnet" in combined.lower() or "PoC scenario" in combined


def test_replay_flag_alone_defaults_to_user_data_trace():
    parsed = parse_launcher_argv(["--replay"])
    assert parsed.role == "replay"
    assert parsed.trace_path is not None
    assert parsed.trace_path.endswith("trace.jsonl")
    assert "results" in parsed.trace_path.replace("\\", "/")


def test_replay_flag_with_path():
    parsed = parse_launcher_argv(["--replay", "trace.jsonl"])
    assert parsed.role == "replay"
    assert parsed.trace_path == "trace.jsonl"


def test_frozen_replay_is_rejected(monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    with pytest.raises(SystemExit) as exc_info:
        parse_launcher_argv(["--replay", "trace.jsonl"])
    assert "replay" in str(exc_info.value).lower()


def test_unrecognized_first_token_is_usage_error():
    with pytest.raises(SystemExit) as exc_info:
        parse_launcher_argv(["--bogus"])
    assert USAGE in str(exc_info.value)
