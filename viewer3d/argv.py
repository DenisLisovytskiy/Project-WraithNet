"""CLI parsing for viewer3d.app. No panda3d/direct import.

Kept in its own module so tests/test_viewer3d_cli.py can exercise the
supported invocations without constructing a ShowBase.
"""

from __future__ import annotations

from dataclasses import dataclass

USAGE = (
    "Usage: python -m viewer3d.app\n"
    "       python -m viewer3d.app --edit [scenario.json]\n"
    "       python -m viewer3d.app --replay <trace.jsonl> [--rate N]\n"
    "       python -m viewer3d.app <host> <port>"
)


@dataclass(frozen=True)
class Viewer3DArgs:
    editor_mode: bool
    edit_path: str | None = None
    host: str | None = None
    port: int | None = None
    replay_path: str | None = None
    playback_rate: float = 1.0


def parse_viewer3d_argv(argv: list[str]) -> Viewer3DArgs:
    """Parse argv *after* the script name has been stripped.

    Matches viewer3d.app:main invocations:
    [] or ['--edit'] -> editor, no path
    ['--edit', 'path.json'] -> editor + path
    ['--replay', 'trace.jsonl'] -> offline TickBuffer playback
    ['--replay', 'trace.jsonl', '--rate', '2'] -> playback at 2×
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
    if argv[0] == "--replay":
        if len(argv) < 2:
            raise SystemExit(USAGE)
        path = argv[1]
        rate = 1.0
        rest = argv[2:]
        if rest:
            if len(rest) == 2 and rest[0] == "--rate":
                rate = float(rest[1])
                if rate <= 0.0:
                    raise SystemExit(USAGE)
            else:
                raise SystemExit(USAGE)
        return Viewer3DArgs(
            editor_mode=False,
            replay_path=path,
            playback_rate=rate,
        )
    if len(argv) != 2:
        raise SystemExit(USAGE)
    return Viewer3DArgs(
        editor_mode=False,
        host=argv[0],
        port=int(argv[1]),
    )
