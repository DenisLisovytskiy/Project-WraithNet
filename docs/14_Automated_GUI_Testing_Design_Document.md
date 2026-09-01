# Design Document: Automated GUI Testing (2D Replay Viewer + 3D Live Viewer / Editor)

> This document is the source of truth for how WraithNet tests its two GUI packages (`viewer/` and `viewer3d/`). It does **not** add a new GUI feature. It consolidates the testing discipline already established across Phases 12–19 (and planned for Phases 24–26), names the remaining gaps in the already-implemented GUI, and specifies the one phase that closes those gaps. See `docs/phases/Phase_27_Automated_GUI_Testing.md` for the implementation prompt.

## 0. What this expansion is, in one paragraph

WraithNet has two human-facing GUIs: a Pygame replay viewer (`viewer/`, Phases 12–13) and a Panda3D live viewer + scenario editor (`viewer3d/`, Phases 16–19). Both were designed so that **almost everything a test should care about is a pure function or a headless scene-graph mutation**, not a pixel on a real display. This expansion makes that strategy an explicit, standing rule, inventories what the existing suite already covers, and adds the remaining automated tests for input handling, HUD copy, camera geometry, editor widget commands, and dependency isolation — without introducing screenshot/golden-image tests, OS-level click bots, or any new testing library.

## 1. Can we add automated tests for the GUI?

**Yes.** The visualization design documents already chose this on purpose, twice:

* `docs/07_Visualization_Design_Document.md` §5 put coordinate mapping and drawing primitives in `viewer/render.py` specifically because they are "pure-ish, testable without a live display." Phase 12 then made that concrete: *"Pygame rendering itself can't be meaningfully unit-tested without a display, but most of this phase's logic can and must be tested headlessly."*
* `docs/09_3D_Live_Visualization_and_Scenario_Editor_Design_Document.md` §1 kept the simulation in a separate process with **zero rendering imports**, so `pytest` / `python -m wraithnet.main` keep working on a machine with no Pygame or Panda3D installed. Phase 16 then required a headless Panda3D config (`window-type none`) for the same reason Phase 12 required `SDL_VIDEODRIVER=dummy`.
* `docs/12_Flight_Dynamics_and_Weapon_Effects_Design_Document.md` §2 restated the same split for later visual work: *"separate the 'what should happen' decision from the 'how to draw it' mechanics, so the decision logic is unit-testable without a display."*

So the question is not "can a GUI be tested." For this project the answer was already "yes, as long as we test the right layer." What this document adds is the standing rule, the inventory, and the remaining work.

**What we will never automate:** whether a chevron "looks like a jet" or a HUD panel is aesthetically aligned. `docs/08_Code_Review_Phases_11-13.md` §5 called pixel-correctness "inherently unverifiable by an automated review," and Phase 12 made golden-image comparisons an explicit non-goal. That non-goal is permanent. A human still has to open the window once per visual change. Automated tests prove that the pipeline ran, the right *data* reached the right *node/function* at the right *time*, and the windowing libraries did not raise.

## 2. Decisions (already made by prior design, restated here as standing rules)

| Decision | Choice | Why it matters |
|---|---|---|
| Where GUI tests live | Existing top-level `tests/`, same as every other module | `viewer/` and `viewer3d/` are sibling packages of `wraithnet/`; the test suite already treats them that way (`tests/test_viewer_*.py`, `tests/test_viewer3d_*.py`). Do not create a second test runner. |
| Testing library | **pytest only** — already in `requirements-dev.txt` | No `pytest-qt`, no Playwright, no Selenium, no `pyautogui`, no screenshot-diff library. Those tools target web/desktop widget trees this project does not have. |
| Optional GUI deps | `pytest.importorskip("pygame")` / `pytest.importorskip("panda3d")` | A CI job that only installs `requirements-dev.txt` must still pass: Pygame/Panda3D tests skip, everything else runs. Proven in `docs/08_...md` and `docs/11_...md`. |
| Headless display | Pygame: `SDL_VIDEODRIVER=dummy` **before** `import pygame`. Panda3D: `loadPrcFileData("", "window-type none")` **before** `ShowBase` | Both are already confirmed working in this environment (pygame-ce + Panda3D 1.10.16 / Windows). Do not switch to a different offscreen backend without re-confirming. |
| What a GUI test may assert | State, data, scene-graph, exceptions, subprocess lifecycle — **never pixels** | Matches Phase 12 Part D and every later GUI phase's smoke tests. |
| Simulation isolation | `wraithnet/` never imports `pygame` / `panda3d` / `direct` / `viewer` / `viewer3d` | Already a hard acceptance grep in Phases 12, 16, and 19. This expansion turns those greps into pytest tests so they cannot silently regress. |

## 3. The three-layer model (standing rule for every GUI phase)

Every GUI concern in this project is tested at exactly one of three layers. New GUI code (including Phases 24–26, when they land) must pick a layer *before* it is written, the same way Phase 13 extracted `viewer/playback.py` *so that* auto-play could be tested without a display.

```
Layer 1 — Pure logic, zero rendering import
  viewer/trace_loader.py, viewer/playback.py, viewer/render.py::world_to_screen
  viewer3d/stream_client.py, viewer3d/highlights.py, viewer3d/launch.py
  viewer3d/editor.py::scenario_to_header / _humanize_validation_error
  (planned) viewer3d/flight_dynamics.py          — Phase 25
  Tests: tests/test_viewer_render.py, test_viewer_playback.py,
         test_viewer3d_stream_client.py, test_viewer3d_highlights.py,
         test_viewer3d_launch.py, and the Panda3D-free cases in
         test_viewer3d_editor.py
  CI: always run (no pygame / panda3d required)

Layer 2 — Headless rendering smoke (dummy / window-type none)
  viewer/render.py::draw_* against a pygame.Surface
  viewer3d/scene.py, cameras.py, hud.py, editor.py, app.py against ShowBase
  Tests: tests/test_viewer_smoke.py, test_viewer3d_smoke.py,
         Panda3D-dependent cases in test_viewer3d_editor.py
  CI: run when requirements-viz.txt / requirements-3d.txt are installed;
      skip cleanly otherwise

Layer 3 — Manual visual check (not automated, never will be)
  "does the glyph look like a chevron", "is the HUD legible", "is the
  orbit camera a pleasant framing"
  Owner: a human running python -m viewer.app / python -m viewer3d.app
  Documented as acceptance criteria in every GUI phase since Phase 12
```

**Rule:** if a behavior can be expressed as Layer 1, it must be Layer 1. Do not bury playback math, highlight diffs, coordinate mapping, validation, or launch argv construction inside a `ShowBase` / `pygame.display` loop. Phase 13 already did this for `advance_playback` / `compute_highlights` / `compute_run_summary`. Phase 27 does the same for the remaining blobs that are still stuck in `viewer/app.py` and `viewer/render.py`'s HUD copy.

**Rule:** Layer 2 asserts "the call completed and the *state* is what we asked for" — `view_mode` flipped, a node was attached/removed, a HUD `OnscreenText` string changed, a subprocess died, `save()` refused to write a file. It does not sample pixel colors.

## 4. What already exists (inventory, Phases 12–19)

This is not a green-field test suite. As of the Phase 17–19 review (`docs/13_Code_Review_Phases_17-19.md`) plus the follow-up `cancel_launch` / `exit_preview` tests, the implemented GUI is already covered as follows.

### 4.1 2D Pygame replay viewer (`viewer/`)

| Concern | Tests | Layer |
|---|---|---|
| World→screen y-flip, corners, center | `test_viewer_render.py::test_world_to_screen_corners_and_y_flip` | 1 |
| Distance scaling | `test_world_length_to_screen_scales_sensor_radius` | 1 |
| Window layout constants | `test_window_is_1200_by_900` | 1 |
| Trace load round-trip / empty / header-only | `test_load_trace_*` | 1 |
| Highlight transitions (strike, destroy, jam) | `test_viewer_playback.py` | 1 |
| Auto-play accumulator + auto-pause at end | `test_advance_playback_*`, `test_viewer_smoke.py::test_autoplay_accumulator_*` | 1 |
| Full draw pipeline does not raise (tiny + rich fixture) | `test_viewer_smoke.py` | 2 |
| End-of-run overlay numbers vs. `compute_run_summary` | `test_compute_run_summary_from_trace` | 1 |

### 4.2 3D live viewer + editor (`viewer3d/`)

| Concern | Tests | Layer |
|---|---|---|
| Stream client: order, retry, exhausted-retry | `test_viewer3d_stream_client.py` | 1 |
| Highlight diffs (same semantics as 2D) | `test_viewer3d_highlights.py` | 1 |
| Launch subprocess: connectable, bad path, distinct ports | `test_viewer3d_launch.py` | 1 |
| Scene: destroyed target greyed, destroyed jet removed | `test_viewer3d_smoke.py::test_build_static_scene_and_update_tick_*` | 2 |
| Live drain of a real `LiveStreamServer` | `test_viewer_app_drains_live_stream_without_raising` | 2 |
| `connecting → brief → live → finished` | `test_view_mode_connecting_brief_live_finished` | 2 |
| Orbit/chase switch invokes chase with a real jet pose | `test_camera_orbit_chase_switching_invokes_chase_with_jet_position` | 2 |
| HUD four panels present; disconnect reason surfaced | `test_hud_update_across_modes_panels_and_disconnect_reason` | 2 |
| Editor: `screen_to_world`, place all four kinds, save/load, validation banner, preview | `test_viewer3d_editor.py` | 1 + 2 |
| Editor → Launch → brief → live/finished (real subprocess) | `test_editor_launch_transitions_editor_brief_live_finished` | 2 |
| Malformed JSON → `launch_failed` | `test_malformed_scenario_launch_sets_launch_failed` | 2 |
| Cancel launch terminates the child and returns to editor | `test_cancel_launch_terminates_sim_and_returns_to_editor` | 2 |
| Esc during preview routes through `cancel_launch` | `test_escape_during_preview_cancels_launch_via_on_escape` | 2 |
| Preview-without-sim `exit_preview` restores editor | `test_preview_without_sim_exits_back_to_editor` | 2 |

That last block of three tests closes the only test gap `docs/13_Code_Review_Phases_17-19.md` §2 flagged as non-blocking. They are already in `tests/test_viewer3d_smoke.py`.

### 4.3 What is *not* yet tested (the gaps this expansion fills)

These are all Layer-1 or Layer-2 gaps in the **already-implemented** GUI. They are not new features.

1. **2D replay keyboard/state machine lives only in `viewer/app.py`'s main loop.** `advance_playback` is tested; the keybindings that *drive* it (`Space`, arrows, `S`, `R`, `+`/`-`, Esc) are not. Pressing Right while playing is specified to pause (Phase 13 Part B.1) and that pause is implemented, but no test constructs a `KEYDOWN` (or an extracted handler) and asserts the resulting state.
2. **2D HUD / end-of-run overlay copy is not asserted.** The smoke tests only check that `draw_hud` / `draw_end_of_run_overlay` do not raise. The strings themselves ("Tick N", "Targets X/Y destroyed", "PLAYING"/"PAUSED", overlay headline numbers) are assembled inline inside Pygame blit calls, so they are not Layer-1-testable today.
3. **3D camera geometry is only "did not raise."** Chase is asserted to be *called* with a jet position; nothing asserts the camera node ends up *behind* the jet at `follow_distance` / `follow_height`. Mission-brief is not asserted to actually change azimuth over `dt`. Orbit is not asserted to look at `center`.
4. **3D keybindings beyond Esc.** `O`/`C` (orbit/chase) and `Tab`/`N` (cycle selected jet) are methods on `WraithNetViewer3D` with no dedicated test. The existing chase test forces `camera_mode` rather than going through `_on_orbit` / `_on_chase` / `_cycle_selected_jet`.
5. **Editor DirectGUI commands are not the test entry point.** Placement/save/load/preview are called as Python methods. The actual button `command=` callbacks (`Add Jet`, `Save`, `Preview`, `Launch`, `Back`) are un-invoked. A regression that wires the wrong method to a button would not be caught.
6. **Isolation greps are review-time only.** Phases 12/16/19 require `grep -rn pygame wraithnet/` and `grep -rn panda3d wraithnet/` to return nothing. Those are not pytest tests, so a later phase can re-introduce a rendering import and CI will not notice until a human re-runs the grep.
7. **CLI parsing is inside `main()` functions that call `app.run()` / the Pygame loop.** `viewer.app:main` and `viewer3d.app:main` cannot be unit-tested without opening a real loop. A tiny `parse_*_argv` helper (Layer 1) is missing.

Phases 24–26 already specify their *own* Layer-1/Layer-2 tests for heading/fuel/RTB visuals, flight-dynamics math, and weapon-effect node lifetime. Those tests belong in those phases when they are implemented. This expansion does not preempt them and does not require Phases 20–26 to have landed.

## 5. What this expansion adds

### 5.1 Extract remaining untestable blobs (so Layer 1 can see them)

Same move Phase 13 already made for playback. Two small, Pygame-free helpers in `viewer/`:

* **`viewer/controls.py`** (new): a frozen-ish `ReplayViewState` dataclass (`tick_index`, `playing`, `ticks_per_second`, `show_sensor_radius`, `playback_accumulator_ms`) plus `apply_keydown(state, key, last_index) -> ReplayViewState`. `viewer/app.py`'s event loop becomes a thin translator from `pygame.K_*` to that function. This is the testable form of Phase 13's keyboard table.
* **`viewer/hud_text.py`** (new): pure functions that return the HUD / overlay *strings* `draw_hud` / `draw_end_of_run_overlay` currently compose inline (`tick_line`, `status_line`, `targets_line`, `jets_line`, overlay title + counts). `draw_*` blits those strings; it does not invent them. No pygame import.

No equivalent extraction is needed in `viewer3d/` for HUD copy: `Hud` already writes into named `OnscreenText` nodes (`mission_info_text`, etc.) that Layer 2 tests can read. Camera math is already in `viewer3d/cameras.py` and is testable against the dummy camera node Phase 17 introduced for `window-type none`.

A tiny **`parse_viewer_argv` / `parse_viewer3d_argv`** (or equivalent) in each package's `app.py` — or a 10-line helper next to `main` — so usage/flag/host-port/`--edit` handling can be tested without calling `app.run()`.

### 5.2 New tests (Phase 27)

All new tests follow the existing file split (`test_viewer_*` vs `test_viewer3d_*`, `importorskip` for Layer 2). Do not create a parallel `tests/gui/` tree.

**2D, Layer 1**

* Every Phase 13 key: Right/Left step and pause; Space toggles `playing`; `+`/`-` clamp `ticks_per_second` to `[0.5, 20]`; `S` toggles sensor rings; `R` resets to tick 0 and pauses; Esc is a "quit requested" flag (the helper returns state, it does not call `sys.exit`).
* HUD copy: a synthetic tick produces the expected `Tick … (i/N)`, `Targets a/b destroyed`, `Jets c/d alive`, `Sensor rings: ON/OFF`, `PLAYING`/`PAUSED` strings. Overlay copy from a known `RunSummary` matches `compute_run_summary` (do not re-derive the numbers in the test).
* `parse_viewer_argv`: missing path → usage/error; one path → that path.

**3D, Layer 1 where possible / Layer 2 otherwise**

* `ChaseCamera.update` against the dummy camera: given jet at `(50, 50, 8)` heading `0` (facing +y), camera `y` is strictly less than `50` (behind) and `z` is `8 + follow_height`; `look_at` target is the jet. A second heading (`90`) moves the camera along a different axis. Tolerance: `1e-4`.
* `MissionBriefCamera.update(dt=1.0)` twice: `_angle_deg` advances by `angular_speed_deg_s` each call (modulo 360) and the dummy camera's `xy` position actually changes.
* `OrbitCamera._apply` (or `update` with no mouse): camera looks at `center`; distance from camera pos to center equals `radius` within a small tolerance.
* `_on_orbit` / `_on_chase` / `_cycle_selected_jet` on a headless `WraithNetViewer3D` in `view_mode="live"` with a two-jet header: `O` sets `camera_mode="orbit"`; `C` sets `"chase"` and selects a jet if none selected; `Tab` twice cycles through both ids and wraps.
* Editor: invoking the DirectGUI `command` of `Add Jet` / `Save` / `Preview` / `Launch` / `Back` (whatever the actual button attributes are named) has the same effect as the corresponding method the existing tests already call. If a button is created conditionally, skip that one case with a reason — do not start asserting on pixel positions of the widgets.
* `parse_viewer3d_argv`: `[]` and `["--edit"]` → editor mode; `["--edit", "path.json"]` → editor + path; `["127.0.0.1", "8765"]` → live host/port; any other arity → usage/error.

**Isolation, Layer 1, no GUI import required**

* A small `tests/test_gui_isolation.py` that reads source files as text (not by importing them) and asserts:
  * `wraithnet/` `.py` files contain no `pygame` / `panda3d` / `direct` / `viewer3d` / `from viewer` imports.
  * `requirements.txt`, `requirements-dev.txt` contain neither `pygame` nor `panda3d`.
  * `viewer3d/stream_client.py`, `viewer3d/highlights.py`, `viewer3d/launch.py` contain no `panda3d` / `direct` imports.
  * `viewer/trace_loader.py`, `viewer/playback.py`, and the new `viewer/controls.py` / `viewer/hud_text.py` contain no `pygame` import.
* This replaces the "run this grep during review" acceptance criteria of Phases 12/16/19 with something CI actually executes.

### 5.3 What does *not* change

* No new dependency. No change to `requirements.txt` / `requirements-dev.txt` / `requirements-viz.txt` / `requirements-3d.txt`.
* No change to simulation behavior, trace schema, or live-stream protocol.
* No change to how the windows look. Extracting HUD strings / key handling is a move of existing logic, not a visual redesign.
* Existing tests keep passing unmodified. New tests are additive. The one sanctioned exception is `viewer/app.py` / `viewer/render.py` calling the new helpers instead of inlining the same logic — behavior-preserving refactor, covered by the old smoke tests plus the new Layer-1 tests.

## 6. Explicit non-goals

* **No pixel / screenshot / golden-image tests.** Reaffirmed from Phase 12 Part D and `docs/08_...md` §5. Comparing a `pygame.Surface` or a Panda3D framebuffer to a checked-in PNG is brittle across pygame-ce vs pygame, GPU drivers, font hinting, and `window-type none` (which has no framebuffer). Out of scope forever for this PoC.
* **No OS-level GUI automation** (`pyautogui`, `pywinauto`, sending real mouse events to a visible window). Fragile, display-dependent, and slower than calling the same methods the widgets already call.
* **No new test framework or browser stack.** There is no web UI (`docs/09_...md` §1 explicitly rejected a browser). pytest is sufficient.
* **No requirement to run Layer 2 in every CI job.** The skip-if-uninstalled contract stays. A job that wants Layer 2 installs `requirements-viz.txt` and/or `requirements-3d.txt`.
* **No tests that open a real on-screen window.** `SDL_VIDEODRIVER=dummy` and `window-type none` remain mandatory in test files that import the rendering libraries.
* **No implementation of Phase 24/25/26's own GUI tests here.** Those phases already specify heading/fuel/RTB visuals, `flight_dynamics.py` unit tests, and weapon-effect node lifetime tests. When those phases are built, they follow §3 of *this* document; they are not in Phase 27's scope.
* **No merging of `viewer/` and `viewer3d/` test helpers, and no `viewer3d` import of `viewer`.** The isolation rule (`docs/09_...md` §3) is unchanged; duplicated highlight-test fixtures are accepted, as they already are today.

## 7. Ordering / dependency note

Phase 27 depends only on the already-implemented GUI (Phases 12–13 and 16–19). It does not depend on Phases 20–26. It can land before or after those phases; if 24–26 land first, their new modules simply get picked up by the isolation test's file walk (Layer 1 modules must stay rendering-free; Layer 2 modules are free to import Panda3D).

## 8. Implementation phase for the LLM

* **Phase 27 — Automated GUI Testing** (`docs/phases/Phase_27_Automated_GUI_Testing.md`): extract `viewer/controls.py` and `viewer/hud_text.py`; add argv parsers; add the Layer-1/Layer-2 tests listed in §5.2; add `tests/test_gui_isolation.py`. No new GUI feature, no visual change, no `wraithnet/` change.
