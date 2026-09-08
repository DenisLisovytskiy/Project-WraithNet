# Phase 29 — Packaging Readiness & a Verified PyInstaller Build

> **How to use this document:** This is a complete, standalone prompt. Give an LLM coding agent this entire file as its task. Assume Phase 28 is complete and passing (`launcher/` package exists; `python -m launcher` is the canonical entry point; `viewer3d/launch.py` spawns its child process through the frozen-aware `build_sim_worker_argv`). Read `docs/16_Unified_Launcher_and_Packaging_Design_Document.md` §5–§7 in full before starting. This phase adds `wraithnet/paths.py`, migrates the small number of hardcoded relative-path call sites onto it, commits a PyInstaller build recipe, and — unlike a normal Phase 0–27 acceptance pass — requires **actually running PyInstaller once** and manually exercising the built executable, because "prepared for packaging" that has never been packaged is exactly the kind of unverified claim this project's review discipline does not accept at face value (see `docs/06_Code_Review_Full_Project.md`'s and every subsequent review's insistence on empirical, not asserted, verification).

> **Automated GUI tests:** Follow `docs/14_Automated_GUI_Testing_Design_Document.md`'s three-layer model. `wraithnet/paths.py` is Layer 1 (pure `pathlib`/`os.environ` logic, no panda3d/pygame import). The actual PyInstaller build/run step in Part D is **not** a pytest test — it is a one-time manual verification pass, exactly like Phase 20/21/22's "run `python -m wraithnet.main` and compare the printed summary" acceptance step, just against the frozen binary instead of the interpreter.

## Context

Phase 28 made "run the sim as a subprocess" frozen-safe in principle (`build_sim_worker_argv`'s `frozen=True` branch is unit-tested). This phase is where that principle either holds up against a real Panda3D/PyInstaller build or doesn't — and where the two remaining unaddressed assumptions (relative-path I/O, and Panda3D's own non-obvious packaging requirements) get resolved. Nothing in this phase changes simulation or GUI behavior when run from source (`frozen=False` continues to mean exactly what it does today, everywhere) — it only adds a second, frozen code path alongside the existing one.

## Part A — `wraithnet/paths.py`

```python
from __future__ import annotations
import os
import sys
from pathlib import Path


def is_frozen() -> bool:
    return getattr(sys, "frozen", False)


def app_root() -> Path:
    """Directory the .exe lives in (frozen) or the repo root (dev)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


def _platform_data_root() -> Path:
    local_appdata = os.environ.get("LOCALAPPDATA")
    if local_appdata:
        return Path(local_appdata)
    return Path.home() / ".local" / "share"  # non-Windows fallback


def user_data_dir(subdir: str) -> Path:
    """Writable location for anything WraithNet creates at runtime.

    Dev mode: app_root() / subdir -- identical to today's './results',
    './scenarios' relative paths, unchanged. Frozen: a per-user writable
    folder under %LOCALAPPDATA%, because the .exe may be installed
    somewhere (e.g. Program Files) a standard user cannot write into.
    """
    base = app_root() if not is_frozen() else _platform_data_root() / "WraithNet"
    path = base / subdir
    path.mkdir(parents=True, exist_ok=True)
    return path
```

There is deliberately no `resource_path()` function in this module — see `docs/16_...md` §5 for why (no runtime-loaded binary assets exist anywhere in this repo; the fixed PoC scenario is a Python literal, not a file read at runtime). Do not add one speculatively.

## Part B — Call-site migration

* `wraithnet/main.py`:
  ```python
  from wraithnet.paths import user_data_dir
  ...
  results_dir = user_data_dir("results")
  recorder = TraceRecorder(str(results_dir / "trace.jsonl"))
  ...
  export_results(bridge.model, output_dir=str(results_dir))
  print(f"Results exported to {results_dir / 'model_vars.csv'} and {results_dir / 'agent_vars.csv'}")
  ```
  The printed message must reflect the *actual* resolved path (which differs between dev and frozen), not a hardcoded `"./results/..."` string that would silently lie once frozen.
* `viewer3d/app.py::_current_scenario_path()`:
  ```python
  from wraithnet.paths import user_data_dir
  ...
  def _current_scenario_path(self) -> str:
      if self._editor is None:
          return str(user_data_dir("scenarios") / "custom.json")
      return self._editor.current_path()
  ```
* No other call site needs to change. `wraithnet/scenario_io.py::load_scenario`/`save_scenario`, `wraithnet/recorder.py::TraceRecorder`, and `wraithnet/metrics.py::export_results` already accept an explicit path/`output_dir` — they were never the problem; only their two callers' hardcoded defaults were. Do not add `paths` imports to those three modules.

## Part C — PyInstaller build recipe

Create `packaging/wraithnet.spec` (or a `packaging/build.py` wrapper around a `pyinstaller` invocation, your call — but it must be a single committed, re-runnable artifact, not a one-off command typed into a terminal and lost) targeting `launcher/__main__.py`, incorporating everything `docs/16_...md` §6 already researched:

* `collect_all("panda3d")`, `collect_all("direct")` (or the equivalent `--collect-all` CLI flags if you choose a plain command over a `.spec`).
* Explicit inclusion of the installed Panda3D wheel's `etc/` directory (`Config.prc`, `Confauto.prc`) as data files, at the destination path Panda3D expects relative to the executable (confirm the exact expected relative path empirically in Part D — do not guess from a blog post and skip verification).
* Confirm (in Part D) whether the display-driver DLLs (`libpandagl.dll`, `libp3windisplay.dll` on Windows) are already pulled in by `collect_all("panda3d")` on the Panda3D version this project uses, or need an explicit `--add-binary`/`binaries=[...]` entry — record whichever is actually true for this environment in a comment in the spec file, not as an assumption.
* Decide and record, in a comment at the top of the spec file, **onedir vs onefile** (`docs/16_...md` §6.2) — measure both if time allows, but at minimum justify whichever one ships, the same way this project has always required a recorded, observed justification rather than an assumed default (c.f. Phase 5/10's distance-traveled bound, Phase 20's fuel-capacity comment).
* Add `build/` and `dist/` (PyInstaller's own output directories) to `.gitignore`. Do not commit built binaries.

Write a short `packaging/BUILDING.md` documenting the exact command to reproduce the build from a clean checkout, including the `requirements-3d.txt` (and, if included per your onedir/onefile decision, `requirements-viz.txt`) install step. This is the "how to build the exe when this project is done" runbook the original request asked for — it must be accurate enough that someone who has never built it before can follow it verbatim.

## Part D — Mandatory manual verification (not optional, not deferred)

Run the actual build once, using the committed recipe from Part C, and manually verify, against the real built executable (not the source checkout):

1. Double-clicking (or running with no arguments) the built executable opens the scenario editor — the default (`role="gui"`) path.
2. Editing a scenario, saving it, and reloading it round-trips correctly, and the saved file lands under the frozen `user_data_dir("scenarios")` location (inspect the actual file on disk at the resolved `%LOCALAPPDATA%\WraithNet\scenarios\` path — confirm it is *not* silently written next to the executable or failing with a permissions error).
3. Clicking **Launch** successfully spawns the simulation as a child process using `build_sim_worker_argv(..., frozen=True)`'s argv shape (Phase 28) — confirm via Task Manager / Process Explorer (or equivalent) that the spawned child is the *same* `WraithNet.exe`, invoked with `--headless`, not a crash or a "file not found" for a nonexistent `python.exe`.
4. The live 3D view populates with ticks exactly as it does from source, and results land under the frozen `user_data_dir("results")` location.
5. Record the actual observed behavior (what worked, what needed an extra `--add-data`/`--add-binary` beyond Part C's starting recipe, actual cold-start latency for onedir vs onefile if both were tried) in `packaging/BUILDING.md` — this document must describe what was *actually* required for a working build in this environment, not merely repeat §6's starting-point research unchanged if reality required more.

If any step fails, fix the spec/recipe and re-verify — do not mark this phase complete on an assumption that the recipe from Part C "should" work. This is the one phase in this project where "the design doc's suggested command" and "a working executable" are explicitly two different claims, and only the second one satisfies this phase.

## Part E — Tests

### `tests/test_paths.py` (new)

* `is_frozen()` is `False` in the normal test environment (pytest never runs frozen).
* `app_root()` in dev mode resolves to the actual repo root (assert it contains, e.g., `pyproject.toml`/`requirements.txt`/`.git`, whichever markers this repo actually has at its root).
* `user_data_dir("results")` in dev mode equals `app_root() / "results"` exactly, and creates the directory if it doesn't already exist (use `tmp_path`-style isolation or clean up after the test — do not leave a stray directory behind or, worse, collide with the real `./results` a concurrent `pytest` run of `test_main.py` might be writing to).
* Monkeypatch `wraithnet.paths.is_frozen` to return `True` (and `sys.executable`/`LOCALAPPDATA` as needed) and confirm `user_data_dir(...)` resolves under the mocked platform-data root, not `app_root()`.

### `tests/test_main.py` / `tests/test_viewer3d_smoke.py` additions

* `wraithnet.main.main([...])` writes its trace/CSV output under `paths.user_data_dir("results")` rather than a hardcoded `"results"` string — a direct regression test for Part B's `main.py` change (monkeypatch `paths.user_data_dir` to point at `tmp_path` and assert the recorder/exporter received that path).
* `viewer3d.app`'s `_current_scenario_path()` default resolves through `user_data_dir("scenarios")` — same monkeypatch-and-assert pattern, guarded by `pytest.importorskip("panda3d")`.

### Full-suite regression

* Run the entire existing `pytest` suite and confirm zero failures — this phase's `pytest`-visible changes are two call-site redirects and one new pure-logic module; every existing test's assumptions about `./results`/`./scenarios` in dev mode must continue to hold exactly, since `user_data_dir()` is defined to equal `app_root() / subdir` when not frozen.

## Explicit non-goals for this phase

* No installer, no code-signing, no auto-update — `docs/16_...md` §7.
* No macOS/Linux build — Windows only, per this project's environment.
* No bundling the 2D Pygame viewer into the build unless it turns out to be effectively free to include — this phase's job is proving the 3D/editor/sim path works frozen, not maximizing what's bundled.
* No committing `build/`/`dist/` output, and no committing the actual `WraithNet.exe` produced during Part D's verification — the spec/recipe is the deliverable; the binary is a local, reproducible verification artifact.
* No CI job that runs PyInstaller automatically — this phase is a manual, one-time proof; automating packaged-build CI is a future concern, not required here.

## Acceptance criteria

1. `pytest` passes in full, including every new test in Part E.
2. `wraithnet/paths.py::user_data_dir` behaves identically to today's hardcoded relative paths when `is_frozen()` is `False` — verified both by unit test and by the fact that every pre-existing test involving `results/`/`scenarios/` still passes unmodified.
3. A committed, re-runnable build recipe (`packaging/wraithnet.spec` or `packaging/build.py`) exists, along with `packaging/BUILDING.md` documenting the exact reproduction steps and whatever was actually discovered/required beyond the design doc's starting-point recipe.
4. Part D's five manual verification steps were actually performed against a real built executable in this environment, and the outcome (including any deviations from the starting recipe) is recorded in `packaging/BUILDING.md`, not merely asserted in a commit message.
5. `grep -rn "results/\|scenarios/custom.json" wraithnet/main.py viewer3d/app.py` returns no matches — both hardcoded relative-path defaults were fully migrated to `wraithnet.paths.user_data_dir`.
