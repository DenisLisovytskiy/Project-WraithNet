"""Rebuild dist/WraithNet from packaging/wraithnet.spec."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SPEC = Path(__file__).resolve().parent / "wraithnet.spec"


def main() -> None:
    cmd = [
        sys.executable,
        "-m",
        "PyInstaller",
        "--noconfirm",
        "--clean",
        str(SPEC),
    ]
    print("Running:", " ".join(cmd))
    subprocess.check_call(cmd, cwd=REPO_ROOT)


if __name__ == "__main__":
    main()
