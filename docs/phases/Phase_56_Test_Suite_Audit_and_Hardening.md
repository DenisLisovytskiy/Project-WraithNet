# Phase 56 — Test Suite Audit & Hardening

> **LLM implementation prompt:** Assume Phases 0–55 are complete. Read `docs/23_Project_State_Audit_and_Next_Phases_Roadmap.md` §2 before touching a test; it holds the measurements this phase starts from. The job is to make the suite tell the truth about the product, not to raise the test count. Do not delete a test without recording what it covered and what covers that now. Do not change production code in this phase except where Part E says so. Every claim in the final report must come from a command you ran.

## Goal

Find out which tests would fail if the product broke, fix or replace the ones that would not, and split the suite so the fast part runs in under a minute.

## Starting point (measured)

* **1,012 tests** collected; 1,008 passed, 1 failed, 3 skipped, **7 min 20 s** on the development machine. The README badge says 775.
* The failing test is `tests/test_viewer3d_smoke.py::test_live_stream_strike_triggers_spawn_strike_effect`. It polls a real socket and a Panda3D task manager against a 12-second wall-clock deadline.
* One test takes 107 s (`test_dynamic_calibration_sample_no_exceptions`) and asserts only that 20 missions run without raising. Its own docstring records NIGHTMARE solving 2 of 10.
* Unit-level logic is well guarded. A hand mutation check broke 18 behaviours in the agent backbone one at a time and ran the 17 core test files: **16 were caught, 2 survived** (listed in Part B).
* System-level outcomes are not guarded at all. Of 32 generated missions only 14 are solved, the rest stall at the tick cap, and the suite is green (see Phase 59). `summarize()["objective_outcomes"]` is always `{}` because it reads a model attribute that does not exist, and no test notices.
* Static scan of the 961 `test_` functions: 3 have no assertion, 3 assert only "is not None", 3 assert on source text, 138 have exactly one assertion, 192 reach into private attributes (706 references; 564 of them in five `viewer3d` test files), 58 use `monkeypatch`, 26 depend on wall-clock time.
* There is no `conftest.py`, no `pytest.ini`/`pyproject.toml`, no markers, no coverage tool installed, and no CI workflow. A `fast_cnp` fixture is copy-pasted into 7 files; 26 files define their own scenario/observation builder helpers.

So the suspicion "the tests are weak" is half right. They are strong where a single function is checked and absent where the whole mission is checked.

## Part A — inventory and classification

Produce `docs/24_Test_Suite_Audit_Report.md` with one row per test file: test count, runtime, what layer it exercises (pure function / world step / agent / bridge end-to-end / generator / viewer logic / viewer with window), and a verdict from this fixed list:

* **KEEP** — fails when the behaviour breaks.
* **STRENGTHEN** — runs the right code but asserts too little.
* **REWRITE** — coupled to private state or to implementation text; breaks on refactor, not on bugs.
* **MOVE** — correct but slow; belongs in the slow lane.
* **DELETE** — duplicates another test or asserts nothing meaningful.

Collect per-test runtimes with `pytest --durations=0`. Install `pytest-cov` (dev requirement only) and record line and branch coverage per package. Coverage is a map of what is *not* run; do not set a percentage target.

Known entries to start the list:

| Test | Problem |
|---|---|
| `test_scenario_generation_integration.py::test_dynamic_calibration_sample_no_exceptions` | 107 s, "no exceptions" only; a soft ordering check that `pytest.skip`s itself when the sample is noisy |
| `test_bid_utility.py::test_score_bid_no_nan_inf_at_boundaries` | no assertion |
| `test_viewer_smoke.py::test_end_of_run_overlay_mission_aborted_and_fuel_bars_do_not_raise` | no assertion |
| `test_viewer3d_ui_chrome.py::test_no_filled_chrome_widget_is_built_with_relief_none` | no assertion |
| `test_viewer3d_editor.py::test_sync_generation_decision_documented` | asserts that a source file contains a phrase |
| `test_viewer3d_hud_style.py::test_chrome_colors_come_from_ui_chrome_but_tactical_colors_stay_local` | asserts on source text |
| `test_viewer3d_textures.py::test_repo_checkout_has_the_gemini_jpegs` | asserts only that asset loads return something |
| `test_viewer3d_smoke.py::test_live_stream_strike_triggers_spawn_strike_effect` | wall-clock deadline; currently failing |
| `test_phase44_combined_arms.py::test_contested_25_seed_combined_arms_smoke` | checks 25 scenarios are *generated* feasible; never runs one |

## Part B — mutation testing as the judge

Assertion counts do not show whether a test protects anything. Mutation testing does: change the code, and a good suite goes red.

1. Add `mutmut` (or `cosmic-ray`) as a dev-only tool. Do not add it to the default test run.
2. Scope it to the logic that matters and is fast to test: `agents.py`, `broker.py`, `bridge.py`, `bid_utility.py`, `altitude_tactics.py`, `weapons_release.py`, `objectives.py`, `countermeasures.py`, `missiles.py`.
3. Record the mutation score per module in the audit report. Every surviving mutant gets one of: a new test, or a written reason why it is equivalent.

Two survivors already known from the hand check; write tests for both first:

* **Contractor-loss wiring.** Replacing the `_notify_managers_of_losses` call in `WraithNetBridge._tick` with a no-op leaves all 228 core tests green. The existing tests call the notify path directly; none proves that `_tick` calls it. Needed: a bridge-level test where a contractor is shot down mid-contract and a different jet is awarded the remainder within N ticks.
* **Notch geometry.** Changing the notch heading from "threat bearing + 90°" to "threat bearing" (flying straight at the missile) leaves the tests green. Needed: a test asserting the commanded heading is perpendicular to the threat bearing within the notch window.

Note the limit of the hand check: it ran 17 core files, not the full suite.

## Part C — outcome tests the suite is missing

Add a small set of end-to-end tests that assert on *results*, each on a fixed seed so a failure is reproducible:

* Mission terminates before the tick cap with an explicit outcome (shared with Phase 59; while Phase 59 is open, mark known-bad seeds `xfail(strict=True)` so they flip to failures the moment they start passing).
* Invariants checked every tick on several generated missions through one shared helper: no target HP below zero; no jet payload below zero; no weapon released outside its range, arc, altitude or attitude envelope; no strike on a target the jet is not directly sensing; no jet both `RTB` and committed; every open claim names a live manager; fuel never increases in flight.
* Determinism: same scenario twice gives the same summary and the same trace hash (Phase 57 Part D owns the subprocess version).
* Golden summary for `scenarios/default.json`: the full `summarize()` dict against a stored JSON file, so a change in behaviour shows up as a reviewed diff.
* Every key of `summarize()` is non-default for at least one fixture run. This is the test that would have caught the dead `objective_outcomes` key.

## Part D — structure

* `tests/conftest.py` with the shared fixtures (`fast_cnp` once, common scenario and observation builders). Remove the copies.
* `pyproject.toml` pytest section: register markers `slow`, `gui`, `e2e`; `asyncio_mode`; `filterwarnings = error` for warnings raised from `wraithnet` itself (the unseeded-RNG warning from Mesa is a real reproducibility smell).
* Default run excludes `slow` and `gui`. Target: default lane under 60 s. Full lane stays available with `-m ""`.
* Replace wall-clock polling in viewer tests with stepping the task manager a fixed number of frames against a fake clock. A test that can fail because the machine was busy is a broken test.
* Viewer tests that reach into private attributes: where the private attribute is the only way to observe a behaviour, add a small read-only accessor on the viewer class instead of asserting on internals (this is the one allowed production change, see Part E).
* Rename `test_phaseNN_*.py` files after the subsystem they test (`test_missiles.py`, `test_carriers.py`, …). Phase numbers say when something was written, not what it protects.
* A GitHub Actions workflow: default lane on every push, full lane nightly, on Windows (the packaging target) and Linux.

## Part E — allowed production changes

Only these, each in its own commit:

1. Read-only accessors needed by Part D.
2. Fix `summarize()["objective_outcomes"]` (reads `model.objective_states`, which is never set) and replace the `getattr(model, "x", default)` pattern in `metrics.py` with direct attribute access, so a renamed field raises instead of silently reporting zero.
3. Update the README test count, or better, remove the hard-coded number.

Anything else found is written into the audit report and handed to the phase that owns it.

## Acceptance criteria

1. `docs/24_Test_Suite_Audit_Report.md` exists with a verdict for every test file, coverage numbers, and mutation scores for the scoped modules.
2. Default lane under 60 s, green on three consecutive runs on Windows.
3. No test without an assertion; no test asserting on source text; no test with a wall-clock deadline.
4. Both known surviving mutants are killed.
5. Mutation score for `agents.py`, `broker.py` and `bridge.py` is recorded, with every survivor explained or tested.
6. The Part C invariant helper runs on at least 8 generated seeds in the slow lane.

## Non-goals

* No coverage percentage gate.
* No rewrite of tests that are verdict KEEP.
* No behaviour fixes beyond Part E; mission stalls belong to Phase 59.
