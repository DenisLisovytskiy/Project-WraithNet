# Phase 58 — Simulation Hot-Path Performance

> **LLM implementation prompt:** Assume Phases 0–57 are complete. Read `docs/23_Project_State_Audit_and_Next_Phases_Roadmap.md` §4. Profile first with the Phase 57 runner and `cProfile`; change only what the profile names. Outcomes must stay byte-identical: every seed in the regression set must produce the same summary before and after. No new dependency, no numpy rewrite, no multithreading inside a run.

## Goal

Make one mission cheap enough that a 200-seed sweep is a coffee break, not an afternoon.

## Why (measured, see doc 23 §4)

Sixteen generated missions (8 STANDARD, 8 CONTESTED, 200×200 map) took 120 s for 11,999 ticks: about **100 ticks per second**. A 6-jet CONTESTED mission that runs to the 1000-tick cap takes about 18 s.

A `cProfile` of one 1000-tick CONTESTED run:

| Share of run | Where |
|---|---|
| ~80% | `terrain.lookahead_required_altitude` (18,490 calls, 18.7 M `cell_obstacle_height` calls) |
| of which ~64% | reached through `world._min_overflight_altitude`, called from `build_observation` once per jet per known target per tick |
| ~18% | reached through `world._autopilot_pitch` → `_terrain_required_altitude` (once per jet per tick) |
| ~4% | all negotiation code (`scan_and_negotiate` and below) |
| ~3% | line-of-sight (`has_line_of_sight`, supercover) |

The agent system is not the bottleneck. One terrain query is.

## Part A — cache the static overflight floor

`_min_overflight_altitude(jet, position)` depends only on the airframe type, the position, the terrain and the scenario altitude rules. Terrain and rules never change during a run, and most targets never move.

Memoise on `(frame_type, position)` in a dict owned by the model. Moving units (tanks, ships) simply miss the cache when their position changes; bound the dict size or key moving units by cell.

Expected effect: the 64% share drops to a few lookups per target per airframe.

## Part B — make the look-ahead cheaper per call

Only after Part A, re-profile. If `lookahead_required_altitude` is still dominant:

* precompute once per terrain a coarse "max obstacle height within radius r" grid for the radii the three airframes actually use, so a query is a table lookup instead of a scan over cells;
* or cache `cell_obstacle_height` / `_cell_bounds` per cell (both are pure functions of a cell index).

Pick one. Do not do both speculatively.

## Part C — observation building

`build_observation` runs once per jet per tick and rebuilds every `TargetInfo` (Pydantic validation included). After Parts A–B, re-profile; if model construction shows up, build the per-target static part once per tick and share it across jets instead of once per jet.

## Part D — benchmark guard

Add `tests/test_perf_budget.py` with one fixed generated scenario and a generous tick-rate floor (for example: must exceed 3× the pre-phase rate on the same machine class, asserted as ticks per second with wide margin). Mark it `slow` (Phase 56 introduces the marker) so it does not run in the default quick lane.

Record before/after numbers for the 16-mission set in the PR description.

## Tests

* Equivalence: for a fixed list of at least 20 seeds across all four presets, the full `summarize` object is identical before and after. Generate the expected values on the commit before this phase and store them as a JSON fixture.
* Cache correctness: a tank that moves gets a recomputed floor; two airframes over the same point get different floors where their climb rates differ.
* Benchmark guard above.

## Acceptance criteria

1. At least 4× ticks per second on the 16-mission set, with identical outcomes.
2. No behaviour change: equivalence fixture green.
3. Profile after the phase shows no single function above 35% of run time.

## Non-goals

* No vectorisation, Cython, numba or process-level parallelism inside one run. Parallelism across runs is Phase 57.
* No viewer performance work.
