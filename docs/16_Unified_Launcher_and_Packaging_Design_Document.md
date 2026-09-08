# Design Document: Unified Launcher & Packaging Readiness

> This document is the source of truth for turning WraithNet's three separate entry points (`python -m wraithnet.main`, `python -m viewer.app`, `python -m viewer3d.app`) into **one command**, and for restructuring the small amount of code that currently assumes "running from a Python checkout" so the project can later be frozen into a single distributable `.exe` with PyInstaller, without a disruptive rewrite when that day comes. **Nothing in this document is implemented yet.** See `docs/phases/Phase_28_*.md` and `docs/phases/Phase_29_*.md` for the phase-by-phase build plan. This is a tooling/packaging expansion, not a simulation or gameplay feature — it changes zero combat/negotiation/fuel/heading behavior.

## 0. What this expansion is, in one paragraph

Today, using WraithNet means knowing which of three unrelated module paths to run, and once you're in the 3D app, "editor → save → launch → watch it live" already works end-to-end (Phases 14–19, 24) — but getting *to* that app, and later turning this whole thing into something a non-developer can double-click, has never been designed. This expansion adds one thin, dependency-light `launcher/` package that becomes the single command every user (and, later, the packaged `.exe`) runs, and makes the small number of places that currently assume "there's a `python.exe` next to me and a writable folder under the current directory" go through two new, narrow seams (`launcher`'s subprocess-spawning logic, and a small `wraithnet/paths.py` helper) so that swapping "run from source" for "run frozen" is a two-file concern, not a scavenger hunt through the codebase.

## 1. What already exists (do not rebuild this)

The "control it via GUI, edit/save/load scenarios" half of this request is **already built**, across Phases 14, 18, 19, and 24:

* `python -m viewer3d.app` with no arguments already opens straight into the scenario editor (`viewer3d/editor.py`) — add/edit/delete jets (with frame-type picker and live ordnance-slot feedback), targets, SAM sites, jammers, and the home base; **Save**/**Load** round-trip through `wraithnet/scenario_io.py`; **Preview** shows the mission-brief camera against the drafted scenario with no sim running; **Launch** saves, spawns the simulation as a child process (`viewer3d/launch.py`), and transitions straight into the live 3D view (Phases 16–17) as ticks arrive.
* This is one continuous GUI session — you do not leave the process to go run a separate CLI command for the simulation. The "launch" button *is* the thing spawning it, today, already.

**What this expansion adds is not a new GUI feature.** It is: (a) collapsing "which of three commands do I run to get to that GUI" into one, and (b) making the one place that currently spawns a subprocess (`launch.py`) and the handful of places that currently write to a relative `./results`/`./scenarios` folder do so in a way that survives being frozen into a single `.exe` where there is no `python.exe` to shell out to and no guarantee the current directory is writable.

## 2. Decisions

| Decision | Choice | Why it matters |
|---|---|---|
| Canonical entry point | A new top-level package, `launcher/`, run as `python -m launcher` (no args = GUI) | Matches this project's existing convention of one sibling package per concern (`wraithnet/`, `viewer/`, `viewer3d/`); it is also the **one** script PyInstaller will target later — see §5. |
| What "no args" does | Boots straight into `viewer3d.app`'s existing editor mode | That mode already *is* "edit / save / load / launch / watch live" end-to-end (§1). No reason to insert a menu screen in front of a screen that already does the job. |
| Existing entry points | `python -m wraithnet.main`, `python -m viewer.app`, `python -m viewer3d.app` all keep working, unchanged, forever | Power users, CI, and every existing test invoke these directly. `launcher` is a thin dispatcher *in front of* them, not a replacement that breaks scripts depending on the old paths. |
| How the GUI spawns the simulation, post-freeze | `launcher`'s own `--headless` role, re-invoking **the same executable** (frozen) or `python -m launcher --headless` (dev) — never a bare `python -m wraithnet.main` subprocess call once frozen | A frozen `.exe` has no separate `python.exe` next to it for `sys.executable -m wraithnet.main` to work. Re-invoking the running executable with a flag is the standard PyInstaller pattern for "one exe, multiple roles" (the same idea `multiprocessing.freeze_support()` uses). |
| Packaging tool | **PyInstaller**, one-file build, targeting `launcher/__main__.py` | Most widely documented tool for this exact stack (stdlib asyncio + Mesa + Pydantic + Panda3D on Windows); confirmed workable for Panda3D specifically (§6) with `--collect-all panda3d --collect-all direct` plus explicit `etc/` PRC data and display-driver DLLs. Panda3D's own `build_apps`/`bdist_apps` (§6.3) is noted as a fallback if PyInstaller proves troublesome for Panda3D specifically when Phase 29 actually builds it — not chosen now because it requires adopting `setuptools`/`pyproject.toml` this project doesn't otherwise need. |
| Writable-data vs. bundled-resource split | New `wraithnet/paths.py`: `user_data_dir()` for anything written at runtback (`results/`, saved scenarios) and left untouched for anything the app itself is shipped with | A frozen `.exe` is often installed somewhere the OS won't let a normal user write (e.g. `C:\Program Files`). Separating "what we write" from "what we ship" now avoids a silent `PermissionError` the first time someone actually installs the built `.exe` outside their own user folder. |
| Dev-mode behavior | **Byte-for-byte unchanged.** `paths.user_data_dir("results")` resolves to exactly today's `./results` when not frozen; `scenarios/default.json`, `config.SCENARIO`, and every existing test's relative-path assumption are untouched | Same discipline this project has applied to every prior refactor (Phase 20's `STRIKE_FIGHTER` must equal `JET_SPEED`, Phase 21's `JDAM`/`AIM9X` must keep their exact numbers): a packaging-readiness refactor must not be the thing that finally changes the fixed PoC's observable behavior. |
| 2D Pygame replay viewer | Kept reachable via `python -m launcher --replay [trace.jsonl]`, but **not** part of the default "no args" path and not required to be bundled into the first packaged build | The 3D live viewer superseded it as the primary GUI (per this project's own prior direction — "2d is just not enough i want 3d"). It stays alive and testable, just demoted to an explicit flag rather than a first-class launch mode. |

## 3. `launcher/` package

```
launcher/
  __init__.py
  __main__.py      # `python -m launcher` entry point
  cli.py           # pure argv parser -> LauncherArgs, no panda3d/pygame import (Layer 1, same
                    # discipline as viewer3d/argv.py)
  dispatch.py       # given LauncherArgs, calls into wraithnet.main / viewer3d.app / viewer.app
```

### 3.1 `launcher/cli.py`

```python
@dataclass(frozen=True)
class LauncherArgs:
    role: Literal["gui", "headless", "replay"]
    # "headless" fields (forwarded to wraithnet.main.main(argv)):
    scenario: str | None = None
    live: bool = False
    live_host: str = "127.0.0.1"
    live_port: int = 8765
    # "replay" fields (forwarded to viewer.app.main(argv)):
    trace_path: str | None = None
    # "gui" fields (forwarded to viewer3d.app.main(argv)):
    edit_path: str | None = None

def parse_launcher_argv(argv: list[str]) -> LauncherArgs: ...
```

* `[]` → `role="gui"`. This is what a double-clicked frozen `.exe` (or `python -m launcher` with no arguments) does.
* `["--headless", ...]` → forwards everything after it to `wraithnet.main`'s existing argument shape (`--scenario`, `--live`, `--live-host`, `--live-port`) unchanged — this role is used both by power users/CI who want today's exact `python -m wraithnet.main` behavior under the new umbrella command, **and** internally by `viewer3d/launch.py` when it spawns the simulation subprocess (§4). There is exactly one implementation of "run the sim headlessly," not two.
* `["--replay", ...]` → optional trace path, forwarded to `viewer.app`.
* Anything else → `SystemExit` with a usage string, mirroring `viewer3d/argv.py`'s existing convention exactly.

### 3.2 `launcher/dispatch.py`

```python
def run(args: LauncherArgs) -> None:
    if args.role == "headless":
        from wraithnet.main import main as sim_main
        sim_main(_headless_argv(args))          # see §3.3 — no sys.argv mutation
    elif args.role == "replay":
        from viewer.app import main as replay_main
        replay_main(_replay_argv(args))
    else:
        from viewer3d.app import main as gui_main
        gui_main(_gui_argv(args))
```

Imports are deferred inside each branch — this is not an optimization nicety, it is load-bearing: the `headless` role must never import `panda3d`/`direct` (a machine running only the sim, or a `--headless` CI job, should not need `requirements-3d.txt` installed at all, exactly like today's `wraithnet.main` doesn't), and the isolation greps/tests Phase 27 already established (`tests/test_viewer3d_isolation.py`) must keep passing unmodified.

### 3.3 A small, backward-compatible change to `wraithnet/main.py`

`parse_args()` and `main()` currently read `sys.argv` implicitly (via `argparse`'s default). Both gain an optional `argv: list[str] | None = None` parameter, defaulting to `None` (→ `sys.argv[1:]`, today's exact behavior, zero change for `python -m wraithnet.main` or any existing test). `launcher/dispatch.py`'s headless branch calls `wraithnet.main.main(headless_argv)` directly — reusing 100% of the existing, already-tested CLI/print/CSV-export logic rather than re-implementing or shelling out to itself a second time. This is the same "no two sources of truth" discipline this project already applied to `WEAPON_CATALOG` (Phase 21) and `wraithnet/geometry.py` (Phase 20/21) — a `--headless` sim run must have exactly one implementation, not a launcher-side copy that could drift from `wraithnet.main`'s.

## 4. Frozen-aware subprocess spawning (`viewer3d/launch.py`)

This is the one genuinely new piece of runtime logic, and the one most worth getting right *before* anyone tries to actually freeze the app, because it's the part today's code gets structurally wrong for a frozen build: `launch_scenario()` currently spawns `[sys.executable, "-m", "wraithnet.main", ...]`. When frozen, `sys.executable` is the WraithNet `.exe` itself — it has no `-m` flag support for arbitrary modules, because there is no separate Python installation backing it.

The fix is a small, pure, directly-testable function:

```python
def build_sim_worker_argv(
    scenario_path: str, host: str, port: int, *, frozen: bool
) -> list[str]:
    """The argv used to spawn the simulation as a child process, in either
    a dev checkout (a real interpreter, invoked with -m launcher) or a
    frozen build (the same running executable, re-invoked with --headless
    directly -- there is no separate python.exe to shell out to)."""
    prefix = [sys.executable] if frozen else [sys.executable, "-m", "launcher"]
    return prefix + [
        "--headless", "--scenario", scenario_path,
        "--live", "--live-host", host, "--live-port", str(port),
    ]
```

`launch_scenario()` calls this with `frozen=getattr(sys, "frozen", False)` — the one, standard, PyInstaller-set attribute every frozen build carries automatically (no new build-time flag or environment variable to introduce and keep in sync). Everything else in `launch.py` (ephemeral port allocation, `SimLaunchHandle`, stderr draining, `CREATE_NO_WINDOW`) is untouched — this is a one-function, one-call-site change.

**Why this must land as its own reviewable unit (Phase 28) before any packaging work (Phase 29):** it is fully testable today, from a normal dev checkout, without ever running PyInstaller — `build_sim_worker_argv(..., frozen=True)` vs. `frozen=False` is a pure function returning a list of strings, and a test can assert the exact shape of both without a real frozen build existing yet. Proving this logic branch correct now, cheaply, is strictly better than discovering it's wrong the first time someone actually double-clicks the packaged `.exe`'s Launch button months from now.

## 5. `wraithnet/paths.py` — resource vs. writable-data split

Two questions, two functions, both frozen-aware but **identical to today's behavior when not frozen**:

```python
def is_frozen() -> bool:
    return getattr(sys, "frozen", False)

def app_root() -> Path:
    """Directory the .exe lives in (frozen) or the repo root (dev)."""
    if is_frozen():
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent

def user_data_dir(subdir: str) -> Path:
    """Writable location for anything WraithNet *creates* at runtime
    (trace/CSV output, user-saved scenarios). Dev mode: exactly
    app_root() / subdir -- today's './results', './scenarios', unchanged.
    Frozen: an OS-appropriate per-user writable folder, because the .exe
    may be installed somewhere Program-Files-like that a normal user
    cannot write into."""
    base = app_root() if not is_frozen() else _platform_data_root() / "WraithNet"
    path = base / subdir
    path.mkdir(parents=True, exist_ok=True)
    return path
```

`_platform_data_root()` is `Path(os.environ["LOCALAPPDATA"])` on Windows (this project's target platform — see `<user_info>`), with `Path.home() / ".local" / "share"` as the non-Windows fallback so the function doesn't hard-fail if someone runs the frozen build elsewhere later.

There is **no `resource_path()` function**, and this is a deliberate finding, not an oversight: WraithNet has zero runtime-loaded binary assets. Every 3D visual (`viewer3d/scene.py`, `effects.py`) is procedurally generated Panda3D geometry — no `.egg`/`.bam`/font/texture files exist anywhere in the repo (confirmed by grep — `loadModel`/`loadFont`/`Filename(` do not appear in `viewer3d/`). The fixed PoC scenario is a Python literal (`config.SCENARIO`), not a file read at runtime — `scenarios/default.json` is a checked-in *reference copy* kept in sync by `test_load_committed_default_json_matches_builtin_scenario`, not something the running app opens. So PyInstaller's bundling of the Python bytecode itself is sufficient for "the app's own content" — the only genuinely runtime-*written* things are `results/*` and whatever scenario JSON files a user saves, both covered by `user_data_dir()`. (If a future phase ever adds real asset files — e.g. a custom font or model — `resource_path()` using `sys._MEIPASS` is the well-known follow-up; it's simply not needed yet, so it is not being built speculatively.)

**Call sites to update (Phase 29):**
* `wraithnet/main.py`: `TraceRecorder("results/trace.jsonl")` → `TraceRecorder(str(paths.user_data_dir("results") / "trace.jsonl"))`; `export_results(bridge.model)` → `export_results(bridge.model, output_dir=str(paths.user_data_dir("results")))`; the final printed message changes from a hardcoded `"./results/..."` string to the actually-resolved path, so it's honest in both modes rather than lying about where frozen output went.
* `viewer3d/app.py::_current_scenario_path()`: default `"scenarios/custom.json"` → `str(paths.user_data_dir("scenarios") / "custom.json")`.
* Nowhere else needs to change — `wraithnet/scenario_io.py`/`wraithnet/recorder.py`/`wraithnet/metrics.py` already accept an explicit path/`output_dir` parameter; they were never the problem, their *callers'* hardcoded defaults were.

## 6. Packaging notes for Phase 29 (researched, not guessed)

PyInstaller can package a Panda3D application, but Panda3D's runtime data is not auto-detected the way pure-Python packages are — three concrete, previously-confirmed requirements Phase 29 must follow exactly, not rediscover by trial and error:

1. **`--collect-all panda3d --collect-all direct`** on the `pyinstaller` command (or the equivalent `collect_all(...)` calls in a `.spec` file) — without this, DirectGUI/ShowBase submodules and Panda3D's own data files are silently dropped.
2. **Panda3D's `etc/` directory (`Config.prc`, `Confauto.prc`) must be bundled explicitly** as data files (e.g. `--add-data "<venv>/Lib/site-packages/panda3d/etc;panda3d/etc"` in a `.spec`'s `datas=[...]`, matching the *destination* directory structure Panda3D expects relative to the executable) — omitting this produces the exact `Exception: No graphics pipe is available!` failure documented by multiple independent PyInstaller+Panda3D users, because Panda3D can't find `load-display`/`aux-display` directives without it.
3. **The Windows display-driver DLL (`libpandagl.dll`) and `libp3windisplay.dll`** must also be present next to the executable (or bundled as binaries) for the same reason — `--collect-all panda3d` is usually sufficient for this on a recent Panda3D wheel, but Phase 29's acceptance criteria must include *actually launching the built `.exe`* rather than trusting the collect flags blindly, precisely because multiple real-world reports show this failing silently until verified end-to-end.

### 6.1 Recommended baseline command (starting point for Phase 29's `.spec`, not necessarily final)

```
pyinstaller --name WraithNet --onefile --windowed ^
    --collect-all panda3d --collect-all direct ^
    launcher/__main__.py
```

`--windowed` suppresses the console window for the default (GUI) role — but the `--headless` role also needs to print progress/results to *something*. Phase 29 must explicitly decide and test this (e.g. `--console` build variant kept alongside the `--windowed` one for debugging, or `--headless` runs writing a log file via `paths.user_data_dir("results") / "run.log"` in addition to `print()`, since a `--windowed` frozen process may have no visible stdout at all on Windows). This is exactly the kind of "assumed to work, never verified" gap the design doc for this phase must call out rather than paper over.

### 6.2 Onefile vs. onedir

PyInstaller's `--onefile` unpacks itself into a temp directory on every launch (slower cold start, and each of the GUI process and every spawned `--headless` child process would separately unpack). Given `launch.py` spawns a *new* process per scenario launch (§4), **onedir (`-D`, the default) is very plausibly the better choice for the actual shipped build** — a single unpacked folder that both the GUI process and its own re-invocation share, no per-process unpack cost, no numbered-temp-folder cleanup concerns. Phase 29 must measure both and record the actual decision with its reasoning (the launch-latency difference is exactly the kind of thing this project has always measured rather than assumed — see Phase 5/10's "record the actual observed value, don't ship the placeholder bound").

### 6.3 Fallback: Panda3D's own `build_apps`

If PyInstaller proves too fragile for Panda3D specifically once Phase 29 is actually underway, Panda3D ships a purpose-built alternative (`setup.py`'s `build_apps`/`bdist_apps` command) that handles PRC data and asset conversion natively, at the cost of adopting a `setuptools`/`pyproject.toml`-based build this project doesn't otherwise use. This is documented here as a named fallback, not chosen now, so Phase 29 doesn't have to re-research it from zero if the PyInstaller path hits a wall.

## 7. Explicit non-goals

* **No installer, no code-signing, no auto-update mechanism.** "Prepared to be turned into a `.exe`" means the code and build recipe are ready; actually producing a signed, distributable installer (e.g. Inno Setup/MSIX) is a separate, later concern this document does not scope.
* **No macOS/Linux packaging.** This project's target environment is Windows (see `<user_info>`); the `_platform_data_root()` fallback (§5) exists so the code doesn't hard-crash elsewhere, but cross-platform `.exe`-equivalent builds are out of scope.
* **No new scenario-editor features.** Every "edit/save/load" capability the request asks for already exists (§1); this document does not add jet/target/SAM/jammer forms, validation, or camera behavior beyond what Phases 14–19/24 already built.
* **No requirement to bundle the 2D Pygame viewer into the first packaged build.** It stays reachable via `--replay` from source; whether to include `pygame-ce` in the frozen build is Phase 29's call to make once it's actually building, not a decision this document forces now.
* **No change to any simulation/negotiation/combat/fuel/heading behavior.** This is purely an entry-point and packaging-readiness change; every existing test's assertions about scenario outcomes remain exactly as-is.
* **No actual "produce and ship `WraithNet.exe`" deliverable from this document.** Phase 29 proves the packaging *works* with a one-time local build-and-verify pass; cutting an official, versioned release build is left for "when this project is done," per the request.

## 8. Implementation phases for the LLM

* **Phase 28 — Unified Launcher & Frozen-Aware Sim-Worker Dispatch** (`docs/phases/Phase_28_Unified_Launcher_and_Sim_Worker_Dispatch.md`): the new `launcher/` package (§3), the small backward-compatible `argv` parameter added to `wraithnet/main.py` (§3.3), and `viewer3d/launch.py`'s `build_sim_worker_argv` (§4). Fully testable today with no PyInstaller involved — every acceptance criterion is a `pytest` assertion or a manual `python -m launcher` run.
* **Phase 29 — Packaging Readiness & a Verified PyInstaller Build** (`docs/phases/Phase_29_Packaging_Readiness_and_PyInstaller_Build.md`): `wraithnet/paths.py` (§5) and its call-site updates; a committed `.spec`/build script encoding §6's researched requirements; `.gitignore` entries for `build/`/`dist/`; and a one-time, manually-verified onefile-or-onedir build (Phase 29 must record which and why, §6.2) that actually boots to the editor, saves/loads a scenario, launches a simulation, and shows it live — proving the packaging readiness work is real, not just plausible-sounding.
