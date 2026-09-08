"""CLI parsing for `python -m launcher`. No panda3d/pygame import.

Kept in its own module so tests/test_launcher_cli.py can exercise every
supported invocation without constructing a ShowBase or a pygame.display.

The launcher only recognizes the *role* selector (`--headless`, `--replay`,
`--edit`, or no flag for GUI). Everything after `--headless` is stored
verbatim as ``role_argv`` and forwarded to ``wraithnet.main`` — that module
is the single parser/source of truth for simulation flags.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Literal

from wraithnet.paths import user_data_dir

_SOURCE_USAGE = (
    "Usage: python -m launcher\n"
    "       python -m launcher --headless [wraithnet.main flags...]\n"
    "       python -m launcher --replay [trace.jsonl]\n"
    "       python -m launcher --edit [scenario.json]"
)

_FROZEN_USAGE = (
    "Usage: WraithNet.exe\n"
    "       WraithNet.exe --headless [wraithnet.main flags...]\n"
    "       WraithNet.exe --edit [scenario.json]\n"
    "The frozen build does not include the 2D replay viewer; "
    "use a source checkout for --replay."
)


def launcher_usage() -> str:
    if getattr(sys, "frozen", False):
        return _FROZEN_USAGE
    return _SOURCE_USAGE


USAGE = _SOURCE_USAGE


@dataclass(frozen=True)
class LauncherArgs:
    role: Literal["gui", "headless", "replay"]
    scenario: str | None = None
    live: bool = False
    live_host: str = "127.0.0.1"
    live_port: int = 8765
    trace_path: str | None = None
    edit_path: str | None = None
    role_argv: tuple[str, ...] = ()


def default_replay_trace_path() -> str:
    return str(user_data_dir("results") / "trace.jsonl")


def parse_launcher_argv(argv: list[str]) -> LauncherArgs:
    """Parse argv *after* the script name has been stripped.

    [] or ['--edit', ...]        -> role="gui" (viewer3d.app editor mode; --edit is accepted
                                    for symmetry with viewer3d.app's own flag and optionally
                                    takes a scenario path to preload, same semantics as
                                    viewer3d.argv.parse_viewer3d_argv)
    ['--headless', ...]             -> role="headless"; remaining tokens are forwarded
                                    verbatim to wraithnet.main (including --help and
                                    --option=value forms argparse already accepts)
    ['--replay'] / ['--replay', P]  -> role="replay"; omitted P defaults to the
                                    user-data results/trace.jsonl
    anything else                   -> raise SystemExit(USAGE)
    """
    if not argv:
        return LauncherArgs(role="gui")
    if argv[0] == "--edit":
        return _parse_edit(argv)
    if argv[0] == "--headless":
        return LauncherArgs(role="headless", role_argv=tuple(argv[1:]))
    if argv[0] == "--replay":
        return _parse_replay(argv)
    raise SystemExit(launcher_usage())


def _parse_edit(argv: list[str]) -> LauncherArgs:
    if len(argv) == 1:
        return LauncherArgs(role="gui")
    if len(argv) == 2:
        return LauncherArgs(role="gui", edit_path=argv[1])
    raise SystemExit(launcher_usage())


def _parse_replay(argv: list[str]) -> LauncherArgs:
    if getattr(sys, "frozen", False):
        raise SystemExit(launcher_usage())
    if len(argv) == 1:
        return LauncherArgs(role="replay", trace_path=default_replay_trace_path())
    if len(argv) == 2:
        return LauncherArgs(role="replay", trace_path=argv[1])
    raise SystemExit(launcher_usage())
