"""Dispatch tests for launcher.run. Headless cases need no GUI libs.

GUI/replay branches are importorskip-guarded and spy on the target main()
so this file never opens a window.
"""

from __future__ import annotations

import builtins

import pytest

from launcher.cli import LauncherArgs, default_replay_trace_path, parse_launcher_argv
from launcher.dispatch import run


def test_run_headless_forwards_full_argv(monkeypatch):
    recorded: list[list[str] | None] = []
    import wraithnet.main as sim_main

    monkeypatch.setattr(sim_main, "main", lambda argv=None: recorded.append(argv))
    run(
        LauncherArgs(
            role="headless",
            role_argv=(
                "--scenario",
                "x.json",
                "--live",
                "--live-host",
                "h",
                "--live-port",
                "9",
            ),
        )
    )
    assert recorded == [
        ["--scenario", "x.json", "--live", "--live-host", "h", "--live-port", "9"]
    ]


def test_run_headless_with_no_flags_forwards_empty_argv(monkeypatch):
    recorded: list[list[str] | None] = []
    import wraithnet.main as sim_main

    monkeypatch.setattr(sim_main, "main", lambda argv=None: recorded.append(argv))
    run(LauncherArgs(role="headless"))
    assert recorded == [[]]


def test_run_headless_forwards_equals_form_and_future_tokens(monkeypatch):
    recorded: list[list[str] | None] = []
    import wraithnet.main as sim_main

    monkeypatch.setattr(sim_main, "main", lambda argv=None: recorded.append(argv))
    parsed = parse_launcher_argv(
        ["--headless", "--scenario=x.json", "--cool-new-flag"]
    )
    run(parsed)
    assert recorded == [["--scenario=x.json", "--cool-new-flag"]]


def test_run_gui_forwards_empty_argv(monkeypatch):
    pytest.importorskip("panda3d")
    from panda3d.core import loadPrcFileData

    loadPrcFileData("", "window-type none")
    loadPrcFileData("", "audio-library-name null")
    import viewer3d.app as gui_app

    recorded: list[list[str] | None] = []
    monkeypatch.setattr(gui_app, "main", lambda argv=None: recorded.append(argv))
    run(LauncherArgs(role="gui"))
    assert recorded == [[]]


def test_run_gui_forwards_edit_path(monkeypatch):
    pytest.importorskip("panda3d")
    from panda3d.core import loadPrcFileData

    loadPrcFileData("", "window-type none")
    loadPrcFileData("", "audio-library-name null")
    import viewer3d.app as gui_app

    recorded: list[list[str] | None] = []
    monkeypatch.setattr(gui_app, "main", lambda argv=None: recorded.append(argv))
    run(LauncherArgs(role="gui", edit_path="path.json"))
    assert recorded == [["--edit", "path.json"]]


def test_run_gui_missing_panda3d_shows_virtualenv_help(monkeypatch):
    real_import = builtins.__import__

    def _import_without_panda3d(name, *args, **kwargs):
        if name == "viewer3d.app":
            raise ModuleNotFoundError("No module named 'panda3d'", name="panda3d")
        return real_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", _import_without_panda3d)
    with pytest.raises(SystemExit) as exc_info:
        run(LauncherArgs(role="gui"))

    message = str(exc_info.value)
    assert "Panda3D" in message
    assert "source .venv/bin/activate" in message
    assert "requirements-3d.txt" in message
    assert ".venv/bin/python -m launcher" in message


def test_run_replay_without_path_forwards_default_trace(monkeypatch):
    pytest.importorskip("pygame")
    import viewer.app as replay_app

    recorded: list[list[str] | None] = []
    monkeypatch.setattr(replay_app, "main", lambda argv=None: recorded.append(argv))
    run(LauncherArgs(role="replay"))
    assert recorded == [[default_replay_trace_path()]]


def test_run_replay_forwards_trace_path(monkeypatch):
    pytest.importorskip("pygame")
    import viewer.app as replay_app

    recorded: list[list[str] | None] = []
    monkeypatch.setattr(replay_app, "main", lambda argv=None: recorded.append(argv))
    run(LauncherArgs(role="replay", trace_path="trace.jsonl"))
    assert recorded == [["trace.jsonl"]]
