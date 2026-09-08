"""Import-isolation checks for the 3D viewer (no Panda3D required).

Turns the Phase 16/19 review greps into pytest so a later change cannot
quietly re-introduce a rendering import into wraithnet/ or the headless
viewer3d modules.
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

_RENDER_IMPORT = re.compile(
    r"^\s*(?:from|import)\s+(?:pygame|panda3d|direct)\b",
    re.MULTILINE,
)
_VIEWER3D_IMPORT = re.compile(
    r"^\s*(?:from|import)\s+viewer3d\b",
    re.MULTILINE,
)


def _read(rel: str) -> str:
    return (ROOT / rel).read_text(encoding="utf-8")


def _py_files(rel_dir: str) -> list[Path]:
    return sorted((ROOT / rel_dir).rglob("*.py"))


def test_wraithnet_does_not_import_rendering_or_viewer3d():
    offenders = []
    for path in _py_files("wraithnet"):
        text = path.read_text(encoding="utf-8")
        if _RENDER_IMPORT.search(text) or _VIEWER3D_IMPORT.search(text):
            offenders.append(str(path.relative_to(ROOT)))
    assert offenders == []


def test_core_requirements_do_not_declare_panda3d_or_pygame():
    for name in ("requirements.txt", "requirements-dev.txt"):
        text = _read(name)
        assert "panda3d" not in text.lower()
        assert "pygame" not in text.lower()


def test_headless_viewer3d_modules_do_not_import_panda3d():
    for rel in (
        "viewer3d/stream_client.py",
        "viewer3d/highlights.py",
        "viewer3d/launch.py",
        "viewer3d/argv.py",
    ):
        text = _read(rel)
        assert not _RENDER_IMPORT.search(text), rel


def _top_level_source(text: str) -> str:
    return "\n".join(line for line in text.splitlines() if line[:1].strip())


def test_launcher_modules_do_not_import_rendering_at_module_level():
    for path in _py_files("launcher"):
        top = _top_level_source(path.read_text(encoding="utf-8"))
        assert not _RENDER_IMPORT.search(top), path.relative_to(ROOT)
