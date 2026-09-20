"""Dispatch a parsed LauncherArgs to the matching existing entry point.

Imports of wraithnet.main / viewer.app / viewer3d.app are deferred to
inside each branch of run() so this module (and launcher.cli) stay
importable with neither pygame nor panda3d installed.
"""

from __future__ import annotations

from launcher.cli import LauncherArgs, default_replay_trace_path


_PANDA3D_INSTALL_HELP = """Panda3D is required for the WraithNet 3D launcher.

Use the project's virtual environment instead of Debian's system Python:

  python -m venv .venv
  source .venv/bin/activate
  python -m pip install -r requirements-3d.txt
  python -m launcher

If .venv already exists, you can run the launcher directly with:

  .venv/bin/python -m launcher
"""


def run(args: LauncherArgs) -> None:
    if args.role == "headless":
        from wraithnet.main import main as sim_main

        sim_main(list(args.role_argv))
    elif args.role == "replay":
        from viewer.app import main as replay_main

        replay_main(_replay_argv(args))
    else:
        try:
            from viewer3d.app import main as gui_main
        except ModuleNotFoundError as exc:
            if exc.name == "panda3d" or (exc.name or "").startswith("panda3d."):
                raise SystemExit(_PANDA3D_INSTALL_HELP) from None
            raise

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
