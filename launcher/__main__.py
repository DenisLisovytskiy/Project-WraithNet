import os
import sys
from pathlib import Path

from launcher.cli import launcher_usage, parse_launcher_argv
from launcher.dispatch import run


def _prepare_frozen_runtime() -> None:
    """Point Panda3D at bundled PRC files when running as a frozen .exe.

    PyInstaller onedir puts collected datas under sys._MEIPASS (the
    `_internal` folder). Without PRC_DIR, Panda3D cannot find load-display
    and raises 'No graphics pipe is available!'.
    """
    if not getattr(sys, "frozen", False):
        return
    meipass = getattr(sys, "_MEIPASS", None)
    if not meipass:
        return
    prc = Path(meipass) / "panda3d" / "etc"
    if prc.is_dir():
        os.environ.setdefault("PRC_DIR", str(prc))


def main() -> None:
    _prepare_frozen_runtime()
    try:
        args = parse_launcher_argv(sys.argv[1:])
    except SystemExit as exc:
        print(exc.code if isinstance(exc.code, str) else launcher_usage())
        sys.exit(1)
    run(args)


if __name__ == "__main__":
    main()
