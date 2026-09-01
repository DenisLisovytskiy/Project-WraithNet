# Code Review — Phases 11–13 (Visualization: Trace Recorder + Pygame Replay Viewer)

> Scope: this review covers the three new phases layered on top of the already-reviewed, complete simulation (`docs/06_Code_Review_Full_Project.md`): Phase 11 (`wraithnet/trace_schemas.py` + `wraithnet/recorder.py`), Phase 12 (the `viewer/` package's static rendering), and Phase 13 (playback, speed control, event highlights). It checks the implementation against `docs/07_Visualization_Design_Document.md` and `docs/phases/Phase_11..13_*.md` line by line, and empirically verifies behavior rather than trusting the code by inspection alone.

## Method

* Read every new/changed module fresh: `wraithnet/trace_schemas.py`, `wraithnet/recorder.py`, `wraithnet/bridge.py`, `wraithnet/main.py`, and all four `viewer/` modules (`render.py`, `app.py`, `trace_loader.py`, `playback.py`).
* Read all six new test files (`tests/test_trace_schemas.py`, `tests/test_recorder.py`, `tests/test_viewer_render.py`, `tests/test_viewer_playback.py`, `tests/test_viewer_smoke.py`) — 36 new tests total.
* Ran the full `pytest` suite: **166/166 passing** (130 pre-existing + 36 new), ~203s.
* Ran `python -m wraithnet.main` fresh: identical outcome to every prior run this project has produced (tick 32, 3/3 targets destroyed, 0 jets lost, 7 missiles `{"JDAM": 6, "AIM9X": 1}`, 5 jammed-agent-ticks) — confirms the recorder hook has no observable effect on simulation behavior, consistent with what `test_recorder_has_zero_effect_on_summarize` already asserts in isolation.
* Loaded the **real** `results/trace.jsonl` this run produced and independently verified its contents against the model's own ground truth (33 ticks, correct header, final tick's targets/jets/strike totals matching the console summary exactly) — not just trusting the synthetic fixtures the test suite uses.
* Headlessly rendered every one of that real trace's 33 ticks through the *entire* Phase 12 + Phase 13 draw pipeline (`SDL_VIDEODRIVER=dummy`) — zero exceptions — and cross-checked `viewer.playback.compute_run_summary`'s output against `wraithnet.metrics.summarize`'s output from the same run: **identical** (`3` targets destroyed, `0` jets lost, `7` missiles, `{"JDAM": 6, "AIM9X": 1}`).
* Verified `grep -r pygame wraithnet/ requirements.txt requirements-dev.txt` returns zero matches — the core simulation package and its required dependencies remain completely GUI-free, as designed.

## Summary

**This is a clean, faithful, well-tested implementation of all three phases.** Every acceptance criterion in `docs/phases/Phase_11_Simulation_Trace_Recorder.md`, `Phase_12_Pygame_Viewer_Static_Rendering.md`, and `Phase_13_Pygame_Viewer_Playback_and_Events.md` is met. No bugs were found. The implementation includes several deviations from the phase docs' exact pseudocode — every one of them is a deliberate, documented improvement, not a shortcut, and each is called out below.

## 1. Phase 11 — Simulation Trace Recorder

* **Schema matches spec exactly**, plus the one sanctioned Phase 12 addition (`TraceHeader.target_positions`) applied consistently across `trace_schemas.py`, `recorder.py`, and both phases' tests.
* **The `_is_claimed` placeholder from the phase doc's pseudocode was correctly resolved** to `bridge.broker.is_claimed(tid)` — the doc explicitly flagged this as not-a-real-method and told the implementer to fix it; confirmed done correctly.
* **Import-cycle avoidance done correctly**: both `recorder.py` (importing `WraithNetBridge` for typing) and `bridge.py` (importing `TraceRecorder` for typing) use `from __future__ import annotations` + `TYPE_CHECKING`-guarded imports. Verified empirically — `python -m wraithnet.main` runs without any circular-import error.
* **Zero-behavioral-impact claim is actually tested, not just asserted**: `test_recorder_has_zero_effect_on_summarize` runs the identical scenario with and without a recorder attached and asserts `summarize()` output is byte-identical between the two. This is exactly the kind of test the design doc's §4 claim needs to back it up, and it passes.
* **The cumulative-vs-per-tick counting lesson from `docs/06_Code_Review_Full_Project.md` §1 was explicitly internalized**: `test_strikes_are_per_tick_not_cumulative` is a direct, named regression guard for exactly that class of bug, and it passes — a strike appears in the tick it landed and nowhere else.
* **Destroyed-jet handling matches spec**: destroyed jets are absent from subsequent `TraceTick.jets` entirely (never a lingering `destroyed=True` entry) — `test_destroyed_jet_is_absent_from_subsequent_tick` confirms this directly against a forced destruction.
* **`role` correctly sourced from `bridge.agents`, not `model`**, and confirmed to track at least two distinct roles (`IDLE`, `CONTRACTOR`) across a real multi-tick run in `test_recorded_role_matches_runtime_for_two_roles`.
* **`grid_width`/`grid_height` are read from `model.space.x_max`/`y_max`** rather than hardcoded from `config.GRID_WIDTH`/`GRID_HEIGHT` — a small, sensible improvement (reflects the actual space Mesa constructed, not just the constant that was passed to it); verified these are equal in the current scenario, so no discrepancy exists either way.

No issues found in Phase 11.

## 2. Phase 12 — Pygame Viewer: Static Rendering

* **`requirements-viz.txt` deviation, well-justified and clearly documented:** the phase doc specified `pygame>=2.5,<3.0`; the implementation instead installs `pygame-ce>=2.5.6,<3.0` (the Community Edition fork), with an inline comment explaining that upstream `pygame` 2.6.x has no Python 3.14 wheels and is unmaintained, while `pygame-ce` is a drop-in replacement (`import pygame` unchanged) with 3.14 support. This is exactly the right way to handle a dependency-availability problem the original phase doc couldn't have anticipated — documented, not silently substituted. Confirmed `pygame-ce 2.5.8` is what's actually installed and everything (`import pygame`, all key constants used in `app.py`) works identically to upstream Pygame's API.
* **Lazy `pygame` import in `render.py`/`playback.py`/`trace_loader.py` is a stronger implementation of the headless-testability requirement than the phase doc's own pseudocode showed.** The doc's Part B code sample imports `pygame` at module level in `render.py`; the shipped code instead imports it only inside a `_require_pygame()` helper (called from every `draw_*`/`_font` function), with only a `TYPE_CHECKING`-guarded `from pygame import Surface` at module level for type hints. This means `viewer.render`, `viewer.playback`, and `viewer.trace_loader` — and therefore `tests/test_viewer_render.py`/`tests/test_viewer_playback.py` — genuinely never require Pygame to be importable, not just "don't happen to call a Pygame function in these specific tests." Confirmed by grep: neither test file references `pygame` at all, and both are unaffected by whether `requirements-viz.txt` is installed.
* **Coordinate mapping is correct and explicitly tested for the y-flip**, the exact failure mode the phase doc called out as the thing to catch: `test_world_to_screen_corners_and_y_flip` asserts world `(0, 0)` (bottom-left) maps to the screen's bottom-left, not top-left.
* **All required drawing primitives are present and match the design's per-entity-type rules**: jets (palette color + role ring + jam ring + HP bar), targets (shape-by-type, destroyed → outline-only, HP label, SAM ring only while alive), the jammer (always drawn, dashed jam-radius ring), the sensor-radius toggle. Verified all of this renders without exception across a real 33-tick trace from an actual full run, not just the synthetic fixtures the test suite ships with.
* **`viewer/trace_loader.py` matches spec exactly**, including both required error cases (empty file, header-only file), both tested directly.

No issues found in Phase 12.

## 3. Phase 13 — Pygame Viewer: Playback, Speed Control & Event Highlights

* **`viewer/playback.py` correctly separates pure logic from Pygame drawing**, exactly as the phase doc's Part A intended — `compute_highlights`, `advance_playback`, and `compute_run_summary` (the latter not explicitly named in the phase doc, but a sensible, well-scoped extension of the same "keep it pure and testable" principle, used by `draw_end_of_run_overlay`) are all pure functions with dedicated unit tests, none of which need a display.
* **`compute_highlights(None, current)` produces no false-positive transitions** — verified directly (`test_compute_highlights_none_previous_has_no_false_positives`), matching the phase doc's explicit warning about this edge case.
* **`advance_playback`'s auto-pause-at-end behavior is correct and tested for the exact scenario the doc worried about**: stepping past the last tick doesn't wrap or go out of bounds, and further elapsed time with `playing=True` re-passed in doesn't un-pause or advance further — `test_advance_playback_steps_then_auto_pauses_at_end` covers both the normal stepping path and the "already at the end, more time arrives anyway" edge case.
* **Manual stepping correctly pauses auto-play** (`viewer/app.py`'s `K_RIGHT`/`K_LEFT` handlers set `playing = False`), matching the design note that manual stepping while auto-playing would otherwise fight the clock.
* **End-of-run overlay correctly excludes `distance_traveled`**, exactly the deliberate scope cut the phase doc called for (Part B.4) — confirmed absent from `RunSummary` and from the overlay's rendered lines.
* **No looping after the end** — `advance_playback` sets `playing = False` at `last_index` and never resets `tick_index`; a user who wants to rewatch presses `R`, matching the explicit non-goal.
* **The overlay's numbers were independently cross-checked against `wraithnet.metrics.summarize()` from the same run**, not just against the trace file's own internal consistency: `compute_run_summary` on the real trace produced `targets_destroyed=3`, `jets_destroyed=0`, `total_missiles_fired=7`, `per_weapon_type_missiles_fired={"JDAM": 6, "AIM9X": 1}` — an exact match with the console output from the same run. This directly satisfies acceptance criterion #3, which specifically asked for this cross-check to be performed, not merely asserted in a test against synthetic data.
* **The rich smoke-test fixture (`tests/test_viewer_smoke.py::_rich_trace`) deliberately engineers one of each transition type** (a strike, a jammed-state transition, a target destruction, and a jet destruction across 5 ticks) and asserts `compute_highlights` actually detects each one at the expected tick *before* rendering — this is a stronger test than the phase doc strictly required (it only asked for "no exception," not "the right highlights fire at the right tick"), and it passes.

No issues found in Phase 13.

## 4. Cross-cutting checks

* **Dependency isolation held completely**: `grep -r pygame wraithnet/ requirements.txt requirements-dev.txt` → zero matches. The core simulation package, its test suite (`test_viewer_render.py`, `test_viewer_playback.py`), and its required dependencies never need Pygame installed. Only `test_viewer_smoke.py` needs it, and it skips cleanly via `pytest.importorskip("pygame")` (a standard, reliable pytest idiom) when it isn't present.
* **`viewer/app.py` is the one place that imports `pygame` eagerly at module level** — correct and expected, since it *is* the Pygame entrypoint; verified it imports and its key-constant usage (`K_PLUS`, `K_EQUALS`, `K_KP_PLUS`, `K_MINUS`, `K_KP_MINUS`, etc.) all resolve correctly against the installed `pygame-ce`.
* **Full pytest suite**: 166/166 passing, no warnings, no skips observed with `pygame-ce` installed (the `test_viewer_smoke.py` tests ran for real, not skipped).
* **Full real-run verification loop closed**: `python -m wraithnet.main` → `results/trace.jsonl` → loaded via `viewer.trace_loader.load_trace` → rendered through every Phase 12 + Phase 13 drawing function across all 33 ticks with zero exceptions → `compute_run_summary` matches the console summary exactly. This is the complete, real (not synthetic) end-to-end path a user would actually exercise, and it works.

## 5. Minor observations (no action required)

* **Visual/pixel-correctness is inherently unverifiable by an automated review** (and was an explicit non-goal in both phase docs) — everything checked above proves the pipeline runs correctly and produces the right *data* at the right *time*, but only a human looking at the actual window can confirm the shapes/colors/layout look good and are legible. Since I can't open a display in this environment, I'd recommend you run `python -m viewer.app results/trace.jsonl` yourself once and skim through a full playback to eyeball it — if anything looks visually off (overlapping HUD text, a ring that's too small/large, etc.), that's a one-off polish pass, not a correctness bug, and easy to fix after a quick look.
* **`STRIKE_RANGE_UNUSED_HINT = None` in `render.py`** is a slightly unusual way to leave a "we deliberately didn't draw this" breadcrumb (a bare `None` constant, referenced nowhere else) — harmless, and it does faithfully carry forward the phase doc's own suggested name/comment for this exact purpose, but a plain comment would have communicated the same intent without an unused module-level binding. Not worth changing.

## 6. Final verdict

Phases 11–13 are **complete, correct, and faithful to their design documents**, with several small, well-justified improvements over the phase docs' own pseudocode (lazy Pygame imports for stronger headless testability, `pygame-ce` for Python 3.14 compatibility, extracted pure `compute_run_summary`). No bugs were found. All three phases' acceptance criteria were independently re-verified against a real simulation run rather than taken on faith from the test suite alone.

**Final state after this review:**
* `pytest`: **166/166 passing** (130 from Phases 0–10 + 36 new across Phases 11–13).
* `python -m wraithnet.main` → `python -m viewer.app results/trace.jsonl` round-trip verified end-to-end against real data, headlessly, with results matching the console summary exactly.
* Zero Pygame footprint in the core `wraithnet` package or its required dependency files.
