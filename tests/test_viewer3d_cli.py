"""Panda3D-free CLI parsing tests for viewer3d.app.

This file must not import panda3d or direct.
"""

from __future__ import annotations

import pytest

from viewer3d.argv import USAGE, Viewer3DArgs, parse_viewer3d_argv


def test_empty_argv_opens_editor():
    parsed = parse_viewer3d_argv([])
    assert parsed == Viewer3DArgs(editor_mode=True, edit_path=None)


def test_edit_flag_alone_opens_editor():
    parsed = parse_viewer3d_argv(["--edit"])
    assert parsed.editor_mode is True
    assert parsed.edit_path is None
    assert parsed.host is None
    assert parsed.port is None


def test_edit_flag_with_path():
    parsed = parse_viewer3d_argv(["--edit", "scenarios/custom.json"])
    assert parsed.editor_mode is True
    assert parsed.edit_path == "scenarios/custom.json"


def test_host_port_opens_live_viewer():
    parsed = parse_viewer3d_argv(["127.0.0.1", "8765"])
    assert parsed.editor_mode is False
    assert parsed.host == "127.0.0.1"
    assert parsed.port == 8765
    assert parsed.edit_path is None


def test_single_token_is_usage_error():
    with pytest.raises(SystemExit) as exc_info:
        parse_viewer3d_argv(["only-one-token"])
    assert USAGE in str(exc_info.value)


def test_three_live_tokens_is_usage_error():
    with pytest.raises(SystemExit):
        parse_viewer3d_argv(["127.0.0.1", "8765", "extra"])
