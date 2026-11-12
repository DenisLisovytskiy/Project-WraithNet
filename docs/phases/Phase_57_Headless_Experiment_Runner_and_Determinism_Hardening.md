# Phase 57 — Headless Experiment Runner & Determinism Hardening

> **Status: DONE (2026-11-12).** `python -m wraithnet.main` takes `--generate/--seed`, `--max-ticks`, `--no-trace` and `--summary-json`; `python -m wraithnet.experiment` runs a batch of seeds into `runs.jsonl`. The tick has no wall-clock wait left (`BID_TIMEOUT_SECONDS` and `MAX_TICK_WAIT_SECONDS` are removed; the guard is `MAX_TICK_NEGOTIATION_YIELDS`). A 32-mission sweep (4 presets, seeds 0–7) gave identical rows before and after that change, and it reproduces the doc 23 §3 table. GUI run folders are pruned to the newest 20.
>
> **Found while doing it, handed to Phase 62 Part C:** the outcome of a mission depends on how many scheduling rounds pass between the end of negotiation and the collection of intents. A message still in a mailbox at that moment (for example an `ACCEPT`) takes effect one tick later. It is deterministic, so it is not a reproducibility bug, but the delivery point is an accident of scheduling, not a rule. The per-tick delivery queue in Phase 62 Part C should make it explicit.

> **LLM implementation prompt:** Assume Phases 0–56 are complete. Read `docs/23_Project_State_Audit_and_Next_Phases_Roadmap.md` §4 (headless findings) before coding. A single headless run already exists (`python -m wraithnet.main`, `python -m launcher --headless`) and the GUI already launches the simulation as exactly that worker subprocess. Do **not** build a second simulation mode. Build the missing layer on top: generate-from-seed on the command line, a batch runner over seeds, and a machine-readable result per run. Reuse `generate_scenario`, `parameters_for_preset`, `WraithNetBridge`, and `metrics.summarize`; add no new dependency.

## Goal

Make "run N seeded missions without a window and get a table of outcomes" a one-line command, and make the result of a seed independent of how fast the machine is.

## Why (measured, see doc 23 §4)

* GUI and simulation are already separate processes. The viewer only consumes a TCP tick stream, so there is no "GUI mode" inside the simulation to switch off.
* Headless today is single-run only. It needs a scenario file that only the editor GUI can generate, always writes `trace.jsonl` plus two CSVs, prints the summary as text, and has a fixed 1000-tick cap.
* Trace writing costs about 16% of run time. The simulation itself is the cost (Phase 60).
* Repeated in-process runs of the same scenario gave identical summaries (16 of 16 checked), but the tick loop still contains a wall-clock timeout (`MAX_TICK_WAIT_SECONDS` around the negotiation `gather`). On a slow or loaded machine that timeout can cut a negotiation short and change the outcome. A batch of parallel workers is exactly the situation that triggers it.

## Part A — single-run flags on `wraithnet.main`

Extend the existing parser (it stays the single source of truth for simulation flags):

* `--generate PRESET --seed N` — build the scenario with `parameters_for_preset` + `generate_scenario` instead of loading a file. Mutually exclusive with `--scenario`.
* `--max-ticks N` — passed to `bridge.run`. Default unchanged (1000).
* `--no-trace` — do not attach `TraceRecorder`. CSV export is skipped too.
* `--summary-json PATH` — write `summarize(model)` plus run metadata as one JSON object.

Run metadata: preset, generation seed, combat seed, generator version, scenario hash, tick cap, whether the cap was hit, wall time, git commit if available, feasibility verdict.

Add an explicit outcome field with exactly one of `SOLVED`, `ABORTED`, `WIPED`, `TICK_CAP`. Today a run that hits the cap prints `Solved: False` and is indistinguishable from a real failure.

## Part B — batch runner

New module `wraithnet/experiment.py`, entry `python -m wraithnet.experiment`:

```text
python -m wraithnet.experiment --preset STANDARD --seeds 0-199 --workers 8 --out results/exp-standard
```

* One row per run in `runs.jsonl` (the Part A summary object). Append and flush per finished run so a killed batch keeps its rows.
* `--resume` skips seeds already present in `runs.jsonl`.
* `--workers` uses `concurrent.futures.ProcessPoolExecutor`. One process per run; no shared state.
* `--keep-traces {none,failed,all}`, default `failed`, so a stalled or lost mission can be replayed in the 3D viewer afterwards.
* `--param key=value` (repeatable) forwards to the `overrides` argument of `parameters_for_preset`.
* `--config-override NAME=value` (repeatable) sets a `wraithnet.config` constant in the worker before the run, and records it in the row. This is what makes weight and timeout sweeps possible without editing code.
* A generation failure or an exception is a row with `outcome: ERROR` and the message, not a crashed batch.
* On exit print a short table: counts per outcome, median ticks, jets lost, targets destroyed.

Do not add plotting, a database, or a config-file format. `runs.jsonl` loads with `pandas.read_json(lines=True)`.

## Part C — remove wall-clock from the tick

The tick must be a pure function of state.

1. Replace the `asyncio.wait_for(..., timeout=config.MAX_TICK_WAIT_SECONDS)` guard with a bounded number of cooperative scheduling rounds. The bid window already counts yields (`_await_bid_window`); give the tick-level guard the same treatment.
2. Remove the `BID_TIMEOUT_SECONDS` sleep path from production code. Seven test files monkeypatch it through a copy-pasted `fast_cnp` fixture; move those tests to the tick clock (`_tick_clock` already exists for this) and delete the fixture copies. Phase 58 then consolidates the remaining shared test helpers.
3. Fix every unseeded RNG warning. The test run prints Mesa's "no random number generator specified… can make models non-reproducible" warning from `mesa/space.py`.

## Part D — determinism test

One test that runs the same generated scenario in two fresh subprocesses and compares the full summary object and a hash of the trace. In-process repetition is not enough: it shares hash seed, import order and warm caches.

Run it with `PYTHONHASHSEED` set to two different values to catch set/dict-order dependence.

## Part E — results retention

`results/` in the working tree holds 561 run folders and 106 MB, one per GUI launch, never cleaned. Keep the newest 20 GUI run folders and delete older ones when a new run is allocated (`viewer3d/launch.py::allocate_run_output_dir`). Experiment output directories are never auto-deleted.

## Tests

* Parser: `--generate` and `--scenario` are mutually exclusive; `--seed` required with `--generate`.
* `--no-trace` writes no `trace.jsonl`; `--summary-json` round-trips through `json.load`.
* Outcome classification for all four cases using tiny hand-built scenarios.
* Batch of 4 seeds with 2 workers produces 4 rows; rerun with `--resume` runs nothing; a deliberately invalid override yields an `ERROR` row.
* Same batch with 1 worker and with 4 workers produces identical rows apart from wall time.
* Part D subprocess determinism test.

## Acceptance criteria

1. `python -m wraithnet.experiment --preset STANDARD --seeds 0-99 --workers N` finishes without a display, with one row per seed.
2. Identical rows (excluding wall time) for any worker count and any `PYTHONHASHSEED`.
3. No `time`, `sleep(>0)` or wall-clock timeout remains on the tick path in `wraithnet/`.
4. The GUI launch path is unchanged and still passes its existing launch tests.
5. README "Headless run" section documents both commands.

## Non-goals

* No distributed execution, no experiment-tracking service, no notebook.
* No changes to simulation rules. If a determinism fix changes an outcome, record it in the PR.
