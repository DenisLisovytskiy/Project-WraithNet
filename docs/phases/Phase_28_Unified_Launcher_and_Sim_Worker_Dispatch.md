# Phase 28 — Unified Launcher & Frozen-Aware Sim-Worker Dispatch

> **How to use this document:** This is a complete, standalone prompt. Give an LLM coding agent this entire file as its task. Assume Phases 0–27 are complete and passing (full simulation core; 2D replay viewer; 3D live viewer + scenario editor with Save/Load/Launch; airframes/weapons/fuel; automated GUI test infrastructure). Read `docs/16_Unified_Launcher_and_Packaging_Design_Document.md` §1–§4 in full before starting. This phase adds **one new top-level package, `launcher/`**, that becomes the single command for running WraithNet, and fixes the one piece of runtime logic (`viewer3d/launch.py`'s subprocess spawn) that is structurally incompatible with ever being frozen into a single executable. **It does not touch PyInstaller, packaging, or `wraithnet/paths.py` at all** — that is Phase 29's job, deliberately kept separate so this phase's acceptance criteria are 100% verifiable with plain `pytest` and manual `python` invocations, no build tool required.

> **Automated GUI tests:** Follow `docs/14_Automated_GUI_Testing_Design_Document.md`'s three-layer model. `launcher/cli.py` is Layer 1 (pure, no panda3d/pygame import — same discipline as `viewer3d/argv.py`). `launcher/dispatch.py`'s branches that import `viewer3d.app`/`viewer.app` are exercised behind `pytest.importorskip("panda3d")` / `pytest.importorskip("pygame")`, exactly like every existing GUI-adjacent test in this project.

## Context

Today there are three unrelated ways to start WraithNet: `python -m wraithnet.main` (headless sim), `python -m viewer.app` (2D replay), `python -m viewer3d.app` (3D live viewer + scenario editor — already the de facto "main app," since it already does edit/save/load/launch/watch-live end to end, per Phases 14/18/19/24). Nobody who isn't already deep in this codebase can guess which one to run. Separately, `viewer3d/launch.py::launch_scenario()` spawns the simulation as a child process via `[sys.executable, "-m", "wraithnet.main", ...]` — a pattern that only works because there is currently a real `python.exe` behind `sys.executable`. Once this project is ever frozen into a single `.exe` (Phase 29), `sys.executable` *is* that `.exe`, and `-m wraithnet.main` means nothing to it. This phase fixes both problems together, because the fix for the second one is easiest to reason about once the first one exists (`launcher --headless` becomes the one thing both a human and a spawned child process invoke).

## Part A — `launcher/` package

```
launcher/
  __init__.py
  __main__.py
  cli.py
  dispatch.py
```

### `launcher/cli.py`

```python
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal

USAGE = (
    "Usage: python -m launcher\n"
    "       python -m launcher --headless [--scenario PATH] [--live] [--live-host HOST] [--live-port PORT]\n"
    "       python -m launcher --replay [trace.jsonl]\n"
    "       python -m launcher --edit [scenario.json]"
)

@dataclass(frozen=True)
class LauncherArgs:
    role: Literal["gui", "headless", "replay"]
    scenario: str | None = None
    live: bool = False
    live_host: str = "127.0.0.1"
    live_port: int = 8765
    trace_path: str | None = None
    edit_path: str | None = None


def parse_launcher_argv(argv: list[str]) -> LauncherArgs:
    """[] or ['--edit', ...]        -> role="gui" (viewer3d.app editor mode; --edit is accepted
                                        for symmetry with viewer3d.app's own flag and optionally
                                        takes a scenario path to preload, same semantics as
                                        viewer3d.argv.parse_viewer3d_argv)
    ['--headless', ...]             -> role="headless"; remaining tokens are exactly
                                        wraithnet.main's existing --scenario/--live/--live-host/
                                        --live-port flags, in any order argparse already accepts
    ['--replay'] / ['--replay', P]  -> role="replay"; P is an optional trace file path
    anything else                   -> raise SystemExit(USAGE)
    """
```

Implementation notes:
* Do **not** re-implement `wraithnet.main`'s `--scenario`/`--live`/`--live-host`/`--live-port` parsing rules here (e.g. type coercion for `--live-port`) — this function's only job for the `headless` role is to recognize `--headless` and capture everything after it as a flat token list to forward verbatim (Part C). Splitting "recognize the role" from "parse that role's own flags" avoids a second, potentially-drifting copy of `wraithnet.main.parse_args()`'s argument shape.
* Model this file directly on `viewer3d/argv.py`'s existing structure and docstring style (same project, same convention, same reason: `tests/test_launcher_cli.py` must be able to exercise every supported invocation without constructing a `ShowBase` or a `pygame.display`).

### `launcher/dispatch.py`

```python
from __future__ import annotations
from launcher.cli import LauncherArgs


def run(args: LauncherArgs) -> None:
    if args.role == "headless":
        from wraithnet.main import main as sim_main
        sim_main(_headless_argv(args))
    elif args.role == "replay":
        from viewer.app import main as replay_main
        replay_main(_replay_argv(args))
    else:
        from viewer3d.app import main as gui_main
        gui_main(_gui_argv(args))


def _headless_argv(args: LauncherArgs) -> list[str]:
    argv: list[str] = []
    if args.scenario is not None:
        argv += ["--scenario", args.scenario]
    if args.live:
        argv += ["--live", "--live-host", args.live_host, "--live-port", str(args.live_port)]
    return argv
```

`_replay_argv`/`_gui_argv` are the equivalent thin adapters onto `viewer.app.main(argv)`/`viewer3d.app.main(argv)`'s existing argv shapes — reuse whatever those two already accept (`viewer.app`'s existing single optional trace-path argument; `viewer3d.app`'s existing `parse_viewer3d_argv` shape for `--edit [path]`) rather than inventing new ones.

**Import placement is load-bearing, not stylistic.** Every import of `wraithnet.main`, `viewer.app`, or `viewer3d.app` inside `dispatch.py` must be deferred to inside the matching branch of `run()`, never at module top level. `launcher/cli.py` and `launcher/dispatch.py`'s module-level code must remain importable with neither `pygame` nor `panda3d` installed — this is exactly the isolation contract `tests/test_viewer3d_isolation.py` already enforces elsewhere in this project (Phase 27) and it must extend cleanly to `launcher/` without modification to that test's file-walk logic (it should just start seeing `launcher/` as another source file to check — verify this by running it, don't just assume the walk is generic enough).

### `launcher/__main__.py`

```python
import sys
from launcher.cli import USAGE, parse_launcher_argv
from launcher.dispatch import run


def main() -> None:
    try:
        args = parse_launcher_argv(sys.argv[1:])
    except SystemExit as exc:
        print(exc.code if isinstance(exc.code, str) else USAGE)
        sys.exit(1)
    run(args)


if __name__ == "__main__":
    main()
```

## Part B — `wraithnet/main.py`: accept an explicit `argv`

```python
def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the WraithNet PoC scenario.")
    ...  # unchanged
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    ...  # everything else in main() is completely unchanged
```

`argv=None` must continue to mean "read `sys.argv[1:]`" exactly as `argparse` already does by default — this is a pure additive signature change. Every existing call site (`if __name__ == "__main__": main()`, every test that calls `wraithnet.main.main()` with no arguments) continues to work with zero modification. `launcher/dispatch.py`'s headless branch is the **only** new caller that passes an explicit `argv` list.

## Part C — `viewer3d/launch.py`: frozen-aware subprocess argv

Extract the argv-construction logic already inside `launch_scenario()` into its own pure function:

```python
def build_sim_worker_argv(
    scenario_path: str, host: str, port: int, *, frozen: bool
) -> list[str]:
    """argv to spawn the simulation as a child process. In a dev checkout
    (frozen=False) this is a real interpreter invoked with -m launcher; in
    a frozen build (frozen=True) there is no separate python.exe, so the
    same running executable is re-invoked directly with --headless."""
    prefix = [sys.executable] if frozen else [sys.executable, "-m", "launcher"]
    return prefix + [
        "--headless",
        "--scenario", scenario_path,
        "--live", "--live-host", host, "--live-port", str(port),
    ]
```

`launch_scenario()` changes its one `args=[...]` line to `args=build_sim_worker_argv(scenario_path, host, port, frozen=getattr(sys, "frozen", False))`. Nothing else in `launch_scenario()`/`SimLaunchHandle` changes — port allocation, `cwd`, `env["PYTHONPATH"]`, `CREATE_NO_WINDOW`, and stderr draining are all untouched.

**Do not read `sys.frozen` inside `build_sim_worker_argv` itself.** Taking `frozen` as an explicit keyword-only parameter (rather than the function reaching for `sys.frozen` internally) is what makes this trivially unit-testable today, in a normal dev checkout, without ever running PyInstaller — see Part D.

## Part D — Tests

### `tests/test_launcher_cli.py` (new)

* `[]` and `["--edit"]` → `role="gui"`, `edit_path is None`.
* `["--edit", "path.json"]` → `role="gui"`, `edit_path == "path.json"`.
* `["--headless"]` → `role="headless"`, `scenario is None`, `live is False`.
* `["--headless", "--scenario", "x.json"]` → `role="headless"`, `scenario == "x.json"`.
* `["--headless", "--scenario", "x.json", "--live", "--live-host", "h", "--live-port", "9"]` → all four headless fields set correctly.
* `["--replay"]` → `role="replay"`, `trace_path is None`. `["--replay", "trace.jsonl"]` → `trace_path == "trace.jsonl"`.
* An unrecognized first token (e.g. `["--bogus"]`) raises `SystemExit` with `USAGE` in its message.

### `tests/test_launcher_dispatch.py` (new)

* `run(LauncherArgs(role="headless", scenario="x.json", live=True, live_host="h", live_port=9))` calls `wraithnet.main.main` with exactly `["--scenario", "x.json", "--live", "--live-host", "h", "--live-port", "9"]` — monkeypatch/spy on `wraithnet.main.main`, do not run a real simulation in this test (that's what `tests/test_main.py` already covers).
* `run(LauncherArgs(role="headless"))` (no scenario, no live) calls `wraithnet.main.main` with `[]`.
* GUI/replay dispatch tests are guarded with `pytest.importorskip("panda3d")` / `pytest.importorskip("pygame")` respectively, and assert only that the right `*.app.main` function is called with the right forwarded argv (spy/monkeypatch again — do not actually open a window in this test file; `tests/test_viewer3d_smoke.py`/`tests/test_viewer_smoke.py` already own that).

### `tests/test_viewer3d_launch.py` additions

* `build_sim_worker_argv("s.json", "127.0.0.1", 9000, frozen=False)` == `[sys.executable, "-m", "launcher", "--headless", "--scenario", "s.json", "--live", "--live-host", "127.0.0.1", "--live-port", "9000"]`.
* `build_sim_worker_argv(..., frozen=True)` omits `"-m", "launcher"` — asserts the exact two-element difference, not just "it's different somehow."
* `launch_scenario(...)`'s existing tests (connectable, bad path, distinct ports) continue to pass unmodified — this proves the refactor didn't change dev-mode (`frozen=False`) observable behavior, only extracted it into a named, separately-testable function.

### `tests/test_main.py` additions

* `wraithnet.main.main(["--scenario", str(some_path)])` behaves identically to setting `sys.argv` and calling `main()` with no arguments — a direct regression test for Part B's signature change, proving `argv=None` still means "read `sys.argv`."

### Isolation

* Re-run (or extend, if it isn't already fully generic) `tests/test_viewer3d_isolation.py`'s source-file walk to confirm `launcher/cli.py` and `launcher/dispatch.py`'s **module-level** code contains no `pygame`/`panda3d`/`direct` import — the deferred imports inside `dispatch.py`'s branch bodies are fine and expected; only top-level imports are the isolation violation this test is designed to catch.

### Full-suite regression

* Run the entire existing `pytest` suite and confirm zero failures — this phase adds a new package and one optional parameter; it must not change any existing test's outcome.
* Manually run `python -m launcher` (no args) and confirm it opens the exact same editor screen `python -m viewer3d.app` does today.
* Manually run `python -m launcher --headless` and confirm it prints the exact same summary `python -m wraithnet.main` does today (tick 32, 3/3 targets, etc. — the same baseline every prior review has recorded).
* From inside the GUI, click **Launch** on a saved scenario and confirm the live view still populates exactly as it does today — this proves `build_sim_worker_argv(..., frozen=False)`'s `-m launcher` prefix genuinely resolves and runs the simulation, not just that the unit test's string comparison is correct.

## Explicit non-goals for this phase

* No PyInstaller, no `.spec` file, no actual frozen build — Phase 29.
* No `wraithnet/paths.py`, no change to where `results/`/saved scenarios are written — Phase 29. This phase's `--headless` role writes to exactly the same `./results` location `python -m wraithnet.main` always has.
* No new scenario-editor features or GUI screens. `launcher --edit` opens the *existing* editor; this phase does not add a splash/menu screen in front of it.
* No change to `wraithnet.main`'s printed output, CSV export location, or CLI flag names/types — Part B is a pure signature addition.

## Acceptance criteria

1. `pytest` passes in full, including every new test in Part D.
2. `python -m launcher` opens the same editor `python -m viewer3d.app` does, with no observable difference.
3. `python -m launcher --headless` produces output identical to `python -m wraithnet.main` for the fixed PoC scenario.
4. `grep -rn "panda3d\|import direct" launcher/cli.py launcher/dispatch.py` returns no matches at module level (deferred imports inside function bodies are fine — confirm by reading, not just grepping blindly, since a naive grep would also flag the intentionally-deferred imports inside `run()`'s branches).
5. `viewer3d/launch.py`'s Launch button still works end-to-end from a normal dev checkout (`frozen=False` path), confirming the refactor in Part C is behavior-preserving for everyone who isn't yet running a frozen build.
