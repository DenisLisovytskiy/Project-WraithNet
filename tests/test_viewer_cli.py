"""Layer-1 CLI parsing tests for viewer.app. No pygame required for these cases."""

from __future__ import annotations

import pytest

from viewer.app import USAGE, parse_viewer_argv


def test_parse_viewer_argv_returns_path():
    assert parse_viewer_argv(["trace.jsonl"]) == "trace.jsonl"


def test_parse_viewer_argv_empty_raises():
    with pytest.raises(SystemExit) as exc_info:
        parse_viewer_argv([])
    assert USAGE in str(exc_info.value)


def test_parse_viewer_argv_too_many_raises():
    with pytest.raises(SystemExit):
        parse_viewer_argv(["a", "b"])
