# Building WraithNet.exe

Windows-only. This is the runbook for reproducing the packaged build that
Phase 29 verified. Do not commit `build/` or `dist/` — only this recipe.

## Prerequisites

Python 3.11+ (this environment used **3.14.0**) on Windows, with a venv at
the repo root if you want isolation:

```
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements-3d.txt
pip install -r packaging/requirements-build.txt
```

`requirements-viz.txt` (pygame) is **not** required. The frozen build
excludes the 2D replay viewer; `python -m launcher --replay` remains a
from-source path.

Verified toolchain: PyInstaller **6.22.2**, panda3d **1.10.16**, Windows 11.

## Build

From the repo root:

```
python packaging/build.py
```

Equivalent:

```
python -m PyInstaller --noconfirm --clean packaging/wraithnet.spec
```

Output: `dist/WraithNet/WraithNet.exe` (onedir, ~289 MB for the whole
folder; the exe itself is a 27.5 MB bootloader stub). Double-click that
exe, or run it from a terminal:

```
dist\WraithNet\WraithNet.exe
dist\WraithNet\WraithNet.exe --headless
dist\WraithNet\WraithNet.exe --edit %LOCALAPPDATA%\WraithNet\scenarios\custom.json
```

## Why onedir, not onefile

`viewer3d/launch.py` re-invokes the same executable with `--headless` on
every Launch click. Onefile unpacks into a temp directory **per process**,
so the GUI and each worker would each pay a full unpack. Onedir shares one
`_internal/` tree.

Measured in this environment (onedir only — a second full onefile build
was not cut because the child-reinvoke cost is structural, not a guess):

* Frozen GUI: Panda3D window title `WraithNet 3D` visible in **under 8 s**.
* Frozen `--headless` full PoC run: **97.2 s**, summary identical to
  `python -m wraithnet.main` from source (**84.4 s** on the same machine).
  ~13 s of that is frozen startup/import overhead on a 289 MB tree; a
  onefile unpack *per Launch* would sit on top of that.

## Why console=True

`--headless` must still print the sim summary. `console=True` keeps that
working from a terminal. The GUI already passes `CREATE_NO_WINDOW` when it
spawns the worker, so Launch does not flash a second console. Double-click
therefore also opens a console alongside the Panda3D window — accepted
for this first verified recipe. A later `console=False` polish would need
a `run.log` under `user_data_dir("results")` for frozen `--headless`.

## Frozen writable paths

When `sys.frozen` is true:

* saved scenarios → `%LOCALAPPDATA%\WraithNet\scenarios\`
* traces / CSVs → `%LOCALAPPDATA%\WraithNet\results\`

Dev checkouts still write to `<repo>/scenarios` and `<repo>/results`.

The scenario-editor default path lives in `viewer3d/editor.py` as well as
`viewer3d/app.py::_current_scenario_path()`. Phase 29's prompt only named
the latter; the editor's `self._path` is what Save/Launch actually use
when the editor is open, so it was migrated too. Without that, frozen
Save would still write a cwd-relative `scenarios/custom.json`.

## Panda3D packaging — what was actually required

Starting-point research (`docs/16_...md` §6) said: `collect_all` panda3d
and direct, bundle `panda3d/etc`, and confirm the GL DLLs.

Observed in the working onedir tree (`dist/WraithNet/`):

* `collect_all("panda3d")` **did** copy `libpandagl.dll` and
  `libp3windisplay.dll` into `_internal/panda3d/` on 1.10.16. The spec
  still adds extra copies into `_internal/` (PyInstaller 6 onedir maps
  spec destination `"."` to `_internal/`, not next to the `.exe`).
* `panda3d/etc/Config.prc` and `Confauto.prc` are present at
  `_internal/panda3d/etc/`. `launcher/__main__.py` also sets `PRC_DIR` to
  that folder when frozen. The GUI opened a real graphics window
  (`WraithNet 3D`) — no `No graphics pipe is available!`.
* Do **not** `collect_all("mesa")`. That walks `mesa.examples` and pulls
  Solara/Altair/Jupyter. Analysis of `launcher/__main__.py` already traces
  the mesa/pydantic/pandas the sim actually uses.

pygame / `viewer/` are excluded. `--replay` is from-source only.

## Verification log (2026-09-08, this machine)

Performed against `dist/WraithNet/WraithNet.exe` produced by
`python packaging/build.py`, not against the source checkout.

1. **No-args GUI.** `Start-Process dist\WraithNet\WraithNet.exe` — process
   stayed running, main window title **`WraithNet 3D`** within 8 s.
   `%LOCALAPPDATA%\WraithNet\scenarios\` was created on editor init.
   Nothing was written next to the exe (`dist/WraithNet/results` and
   `dist/WraithNet/scenarios` both absent).

2. **Save/Load location.** Copied `scenarios/default.json` to
   `%LOCALAPPDATA%\WraithNet\scenarios\verify.json` and launched
   `WraithNet.exe --edit <that path>`. Window opened and stayed up
   (load of a file in the frozen user-data dir). Editor default Save
   path is `user_data_dir("scenarios") / "custom.json"` — same folder.
   DirectGUI buttons are not OS widgets, so the Save *click* itself was
   not driven by UI Automation; Save calls `wraithnet.scenario_io.save_scenario`
   with that path.

3. **Launch worker argv (frozen=True).** Spawned
   `[WraithNet.exe, --headless, --scenario, <abs default.json>, --live, ...]`
   — the exact `build_sim_worker_argv(..., frozen=True)` shape, with
   `sys.executable` replaced by the built exe. **No** `-m launcher`.
   Child pid ran; a `StreamClient` received a `HeaderEvent` with
   `Jet_1`/`Jet_2`/`Jet_3` and `Target_A`/`Target_B`/`SAM_Site_1`.

4. **Live ticks + results path.** Frozen `--headless` printed the same
   PoC summary as source (tick 32, 3/3 targets, distance 222.67, 7
   missiles `{'JDAM': 6, 'AIM9X': 1}`, 5 jammed-agent-ticks) and wrote

   `%LOCALAPPDATA%\WraithNet\results\{trace.jsonl,model_vars.csv,agent_vars.csv}`

   Printed line used the resolved path, not `./results/...`. Combined
   with (3), the live 3D path is the same worker the GUI Launch button
   starts.

5. **Recipe deviations from the design-doc starting command.**

   * Onedir instead of `--onefile --windowed`.
   * `console=True` instead of `--windowed`.
   * `collect_all` limited to panda3d + direct (mesa collect_all aborted
     as a failed first attempt — it started pulling Solara).
   * Explicit `panda3d/etc` datas **and** `PRC_DIR` in
     `launcher/__main__.py`.
   * Extra copies of `libpandagl.dll` / `libp3windisplay.dll` even though
     collect_all already had them under `_internal/panda3d/`.
   * editor.py default path migrated, not only `app.py`.

## Review 17 follow-up (2026-09-08, this machine)

Code corrections from `docs/17_Code_Review_Phases_23-29.md` (Findings 1–4,
6–14) are in source. `packaging/requirements-build.txt` is now pinned to
`pyinstaller>=6.22.2,<7`. GUI Launch workers pass a unique `--output-dir`
so concurrent runs no longer share `trace.jsonl`.

The existing `dist/WraithNet/WraithNet.exe` was produced **before** those
source changes (it does not yet advertise `--output-dir`). A rebuild with
`python packaging/build.py` is required before the frozen tree includes
Findings 4 and 13. The checks below were run against that existing exe
plus the source editor's DirectGUI commands.

### Frozen editor Save / Load / Launch composition

DirectGUI buttons are not OS widgets, so the frozen window still cannot
be clicked by UI Automation. The same functions those buttons call were
exercised as follows.

1. **Edit + Save into the frozen scenarios folder.** Loaded
   `scenarios/default.json`, moved `Jet_1` to `(70.25, 70.0)`, and wrote
   it with `wraithnet.scenario_io.save_scenario` (the implementation
   behind the editor **Save** control) to

   `%LOCALAPPDATA%\WraithNet\scenarios\review_17_verify.json`

2. **Load.** `load_scenario` of that path returned `Jet_1` at
   `(70.25, 70.0)`. Frozen `WraithNet.exe --edit <that path>` stayed
   running (pid **17696**, window title `WraithNet 3D`); that is the
   editor **Load** of a file already in the frozen user-data dir.

3. **Launch worker + live ticks.** Spawned the same argv the editor
   **Launch** control builds for a frozen child (no `-m launcher`):

   `WraithNet.exe --headless --scenario <review_17_verify.json> --live --live-host 127.0.0.1 --live-port <ephemeral>`

   Child pid **18016**. A `StreamClient` received a `HeaderEvent` with
   `Jet_1`/`Jet_2`/`Jet_3` and then **3** `TickEvent`s — ticks entered
   the live protocol the 3D scene consumes. The child was then
   terminated (non-zero exit from SIGTERM, not a sim failure).

4. **Source DirectGUI Save / Launch / live scene.** Pytest
   `test_directgui_save_button_writes_valid_draft`,
   `test_directgui_launch_button_enters_brief`, and
   `test_editor_launch_transitions_editor_brief_live_finished` invoke the
   actual Save and Launch button `command` callbacks and confirm ticks
   reach `WraithNetViewer3D` (`view_mode` live/finished, `_latest_tick`
   set). That is the integrated editor → Save → Launch → live scene path
   in a headless `window-type none` Panda3D context.

Rebuilding the onedir tree is the remaining step before those same
frozen clicks would also pick up `--output-dir` isolation and verbatim
`--headless` forwarding.

## Phase 33 — Quick Generate packaging note

Random-scenario modules (`wraithnet.generation_schemas`,
`wraithnet.generation_presets`, `wraithnet.generation_rng`,
`wraithnet.scenario_feasibility`, `wraithnet.scenario_generator`) are pure
Python and are pulled in through `viewer3d.editor` → PyInstaller analysis.
No new hiddenimports are required for the existing `packaging/wraithnet.spec`.

Packaged smoke (after `python packaging/build.py`):

1. Launch `dist\WraithNet\WraithNet.exe`.
2. **Standard → Generate → Save → Launch**.
3. Confirm the scenario lands under `%LOCALAPPDATA%\WraithNet\scenarios`.
4. Confirm Generate does not freeze (synchronous generation; Phase 32 is
   typically well under 100 ms).

## Phase 44 — Combined-arms packaging checklist

After `python packaging/build.py`, verify in `dist\WraithNet\WraithNet.exe`:

1. Paint / generate terrain (water for naval presets).
2. Place AAA, tanks, carriers, corvettes; edit SAM IR/RADAR and jet flares.
3. Generate with Contested/Nightmare (combined-arms counts) → Save → Preview → Launch.
4. Confirm traces under `%LOCALAPPDATA%\WraithNet` still replay without crash.
5. Headless PoC: `dist\WraithNet\WraithNet.exe --headless` matches source summary fields.

No new PyInstaller hiddenimports are required for `bid_utility` / `bid_schemas` (imported via agents).

## Phase 45 — Risk-aware CNP packaging note

`wraithnet.bid_utility` and `wraithnet.bid_schemas` are pure Python and pulled
through `wraithnet.agents` → no new hiddenimports. After rebuild, confirm a
Contested Generate → Launch still awards contracts (negotiation events appear
in `trace.jsonl` as `negotiation` / award summaries when present).

## Deterministic generation matrices (source)

Verified green on this checkout:

* `test_auto_balanced_feasible_over_seed_matrix` — **100 seeds × 4 presets**
  (TRAINING/STANDARD/CONTESTED/NIGHTMARE) with Contested/Nightmare non-zero
  tank/naval counts.
* `test_standard_100_seed_matrix` — **100 STANDARD** terrain/placement.
* Contested combined-arms smoke in `tests/test_phase44_combined_arms.py`
  (≥25 seeds expecting tanks or naval when counts draw >0).

Rebuild the frozen tree with `python packaging/build.py` before treating
packaged Contested/Nightmare Generate as verified on a given machine.
