"""CLI parsing for viewer3d.app. No panda3d/direct import.

Kept in its own module so tests/test_viewer3d_cli.py can exercise the
supported invocations without constructing a ShowBase.
"""

from __future__ import annotations

from dataclasses import dataclass

USAGE = (
    "Usage: python -m viewer3d.app\n"
    "       python -m viewer3d.app --edit [scenario.json]\n"
    "       python -m viewer3d.app <host> <port>"
)


@dataclass(frozen=True)
class Viewer3DArgs:
    editor_mode: bool
    edit_path: str | None = None
    host: str | None = None
    port: int | None = None


def parse_viewer3d_argv(argv: list[str]) -> Viewer3DArgs:
    """Parse argv *after* the script name has been stripped.

    Matches the existing viewer3d.app:main invocations:
    [] or ['--edit'] -> editor, no path
    ['--edit', 'path.json'] -> editor + path
    ['127.0.0.1', '8765'] -> live host/port
    any other arity -> SystemExit with USAGE
    """
    if not argv:
        return Viewer3DArgs(editor_mode=True, edit_path=None)
    if argv[0] == "--edit":
        if len(argv) == 1:
            return Viewer3DArgs(editor_mode=True, edit_path=None)
        if len(argv) == 2:
            return Viewer3DArgs(editor_mode=True, edit_path=argv[1])
        raise SystemExit(USAGE)
    if len(argv) != 2:
        raise SystemExit(USAGE)
    return Viewer3DArgs(
        editor_mode=False,
        host=argv[0],
        port=int(argv[1]),
    )
