"""GUI dependency isolation: what each layer actually loads when imported.

A headless run must work without Panda3D or pygame installed. Each case
imports a set of modules in a fresh interpreter and lists every forbidden
package that ended up in ``sys.modules``, so a transitive import is caught
too. See docs/14_Automated_GUI_Testing_Design_Document.md §5.2.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
RENDERING = ["pygame", "panda3d", "direct"]

_PROBE = r"""
import importlib, json, pkgutil, sys
modules, forbidden = json.loads(sys.argv[1])
names = []
for name in modules:
    if name.endswith(".*"):
        package = importlib.import_module(name[:-2])
        names += [m.name for m in pkgutil.walk_packages(package.__path__, package.__name__ + ".")]
    else:
        names.append(name)
for name in names:
    importlib.import_module(name)
loaded = sorted({m.split(".")[0] for m in sys.modules} & set(forbidden))
print(json.dumps(loaded))
"""

CASES = {
    "wraithnet": (["wraithnet.*"], RENDERING + ["viewer", "viewer3d"]),
    "launcher": (["launcher.cli", "launcher.dispatch"], RENDERING),
    "viewer3d-headless": (
        [
            "viewer3d.stream_client",
            "viewer3d.highlights",
            "viewer3d.launch",
            "viewer3d.argv",
            "viewer3d.playback",
            "viewer3d.motion",
            "viewer3d.flight_dynamics",
            "viewer3d.trace_io",
        ],
        RENDERING,
    ),
    "viewer-headless": (
        ["viewer.trace_loader", "viewer.playback", "viewer.controls", "viewer.hud_text"],
        RENDERING,
    ),
    # The 3D HUD keeps its own tactical palette so viewer3d never needs the
    # pygame viewer to agree with the 2D map.
    "viewer3d-hud": (["viewer3d.hud"], ["pygame", "viewer"]),
}


@pytest.mark.parametrize("case", sorted(CASES))
def test_layer_does_not_load_forbidden_packages(case):
    if case == "viewer3d-hud":
        pytest.importorskip("panda3d")
    modules, forbidden = CASES[case]
    out = subprocess.run(
        [sys.executable, "-c", _PROBE, json.dumps([modules, forbidden])],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=True,
    )
    assert json.loads(out.stdout) == []


def test_core_requirements_do_not_declare_panda3d_or_pygame():
    for name in ("requirements.txt", "requirements-dev.txt"):
        text = (ROOT / name).read_text(encoding="utf-8").lower()
        assert "panda3d" not in text
        assert "pygame" not in text
