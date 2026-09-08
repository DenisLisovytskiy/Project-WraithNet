"""Layer-1 tests for wraithnet.paths. No pygame/panda3d required."""

from __future__ import annotations

import sys
from pathlib import Path

from wraithnet.paths import app_root, is_frozen, user_data_dir

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_is_frozen_is_false_under_pytest():
    assert is_frozen() is False


def test_app_root_is_the_repo_root():
    root = app_root()
    assert root == REPO_ROOT
    assert (root / "requirements.txt").is_file()
    assert (root / ".git").exists()


def test_user_data_dir_dev_equals_app_root_subdir(tmp_path, monkeypatch):
    monkeypatch.setattr("wraithnet.paths.app_root", lambda: tmp_path)
    monkeypatch.setattr("wraithnet.paths.is_frozen", lambda: False)
    target = tmp_path / "results"
    assert not target.exists()
    resolved = user_data_dir("results")
    assert resolved == target
    assert resolved.is_dir()


def test_user_data_dir_frozen_uses_platform_data_root(tmp_path, monkeypatch):
    fake_local = tmp_path / "LocalAppData"
    fake_exe_dir = tmp_path / "Program Files" / "WraithNet"
    fake_exe_dir.mkdir(parents=True)
    fake_exe = fake_exe_dir / "WraithNet.exe"
    fake_exe.write_bytes(b"")

    monkeypatch.setattr("wraithnet.paths.is_frozen", lambda: True)
    monkeypatch.setenv("LOCALAPPDATA", str(fake_local))
    monkeypatch.setattr(sys, "executable", str(fake_exe))

    resolved = user_data_dir("results")
    assert resolved == fake_local / "WraithNet" / "results"
    assert resolved.is_dir()
    assert resolved != app_root() / "results"
    assert app_root() == fake_exe_dir
