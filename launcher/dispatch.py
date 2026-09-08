"""Dispatch a parsed LauncherArgs to the matching existing entry point.

Imports of wraithnet.main / viewer.app / viewer3d.app are deferred to
inside each branch of run() so this module (and launcher.cli) stay
importable with neither pygame nor panda3d installed.
"""

from __future__ import annotations

from launcher.cli import LauncherArgs, default_replay_trace_path


def run(args: LauncherArgs) -> None:
    if args.role == "headless":
        from wraithnet.main import main as sim_main

        sim_main(list(args.role_argv))
    elif args.role == "replay":
        from viewer.app import main as replay_main

        replay_main(_replay_argv(args))
    else:
        from viewer3d.app import main as gui_main

        gui_main(_gui_argv(args))


def _headless_argv(args: LauncherArgs) -> list[str]:
    """Headless tokens are stored untouched; never reconstructed."""
    return list(args.role_argv)


def _replay_argv(args: LauncherArgs) -> list[str]:
    path = args.trace_path if args.trace_path is not None else default_replay_trace_path()
    return [path]


def _gui_argv(args: LauncherArgs) -> list[str]:
    if args.edit_path is not None:
        return ["--edit", args.edit_path]
    return []
