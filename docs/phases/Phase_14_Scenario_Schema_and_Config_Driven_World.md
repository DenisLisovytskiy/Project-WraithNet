# Phase 14 — Scenario Schema, IO & Config-Driven World

> **How to use this document:** This is a complete, standalone prompt. Give an LLM coding agent this entire file as its task. Assume Phases 0–13 are complete and passing (`python -m wraithnet.main` solves the fixed scenario deterministically; `python -m viewer.app results/trace.jsonl` replays it). Read `docs/09_3D_Live_Visualization_and_Scenario_Editor_Design_Document.md` §4 in full before starting. This phase touches `wraithnet/world.py`, `wraithnet/bridge.py`, and `wraithnet/main.py` **only to add optional parameters with backward-compatible defaults** — every existing test that calls `WraithNetModel()`/`WraithNetBridge()` with no arguments must keep passing, completely unmodified, with identical behavior. This phase adds no 3D, no GUI, no networking — it is pure data modeling and plumbing.

## Context

Today, `wraithnet/config.py::SCENARIO` is a hardcoded Python dict — the only scenario that has ever existed. Later phases (18–19) build a visual editor that lets a user construct a *different* scenario and hand it to the simulation. For that to be possible, the scenario needs to become a validated, loadable/saveable **data format**, not a Python literal. This phase does exactly that, and nothing else — no editor, no 3D, no live streaming yet.

## Part A — `wraithnet/scenario_schema.py` (new file)

Define Pydantic models that capture exactly the shape of today's `config.SCENARIO`, with validation added on top:

```python
"""Pydantic schema for a WraithNet scenario: the full set of jets, targets,
SAM sites, and jammers that make up one runnable scenario. This is the
data-driven replacement for the hardcoded wraithnet.config.SCENARIO dict —
see docs/09_..._Design_Document.md §4.
"""

from pydantic import BaseModel, model_validator

from wraithnet import config
from wraithnet.config import TargetType, WeaponType


class JetSpec(BaseModel):
    agent_id: str
    position: tuple[float, float]
    payload: dict[WeaponType, int]


class TargetSpec(BaseModel):
    target_id: str
    position: tuple[float, float]
    hp_required: int
    target_type: TargetType


class SamSiteSpec(BaseModel):
    target_id: str
    position: tuple[float, float]
    hp_required: int
    target_type: TargetType = "SAM_SITE"


class JammerSpec(BaseModel):
    jammer_id: str
    position: tuple[float, float]


class ScenarioConfig(BaseModel):
    grid_width: float = config.GRID_WIDTH
    grid_height: float = config.GRID_HEIGHT
    jets: list[JetSpec]
    targets: list[TargetSpec]
    sam_sites: list[SamSiteSpec] = []
    jammers: list[JammerSpec] = []

    @model_validator(mode="after")
    def _validate(self) -> "ScenarioConfig":
        # 1. at least one jet
        # 2. all IDs (jet agent_id, target/sam target_id, jammer jammer_id)
        #    unique across the WHOLE scenario, not just within their own list
        #    (targets and sam_sites share the same broker "target" namespace
        #    at simulation time -- see wraithnet/world.py's self.targets dict)
        # 3. every position's x in [0, grid_width], y in [0, grid_height]
        # 4. every hp_required >= 1
        # 5. every payload count >= 0
        # raise ValueError(...) with a specific, human-readable message per
        # violation -- Phase 18's editor will surface these messages directly
        # in its UI, so word them for a scenario-builder, not a developer.
        ...
        return self
```

Fill in the validator body; do not skip any of the five checks listed. Use a single `ValueError` per violation (Pydantic wraps it in a `ValidationError` automatically) — if you want to report multiple problems at once, collect messages into a list and join them in one `ValueError`, but do not silently ignore any check.

## Part B — `wraithnet/scenario_io.py` (new file)

```python
"""Load/save a ScenarioConfig to/from a JSON file. JSON, not YAML -- see
docs/09_..._Design_Document.md §4 for why (no YAML dependency exists
anywhere else in this project)."""

from pathlib import Path

from wraithnet.scenario_schema import ScenarioConfig


def load_scenario(path: str | Path) -> ScenarioConfig:
    return ScenarioConfig.model_validate_json(Path(path).read_text(encoding="utf-8"))


def save_scenario(config: ScenarioConfig, path: str | Path) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(config.model_dump_json(indent=2), encoding="utf-8")
```

This is intentionally tiny — do not add extra features (no merging, no partial updates, no schema versioning) beyond straightforward whole-file load/save.

## Part C — `wraithnet/world.py` changes

`WraithNetModel.__init__` currently reads `config.SCENARIO["targets"]`/`config.SCENARIO.get("sam_sites", [])`/`config.SCENARIO.get("jammers", [])`/`config.SCENARIO["jets"]` directly (lines ~130–151 today). Change the signature to:

```python
def __init__(self, seed: int | None = None, scenario: "ScenarioConfig | None" = None) -> None:
    ...
    self._scenario = scenario or _default_scenario()
    for t in self._scenario.targets:
        ...  # same spawning logic as today, reading from self._scenario.targets instead of config.SCENARIO["targets"]
    for s in self._scenario.sam_sites:
        ...
    for jam in self._scenario.jammers:
        ...
    for j in self._scenario.jets:
        ...
```

* `_default_scenario()` is a small module-level function (in `world.py`, or in `scenario_schema.py` if you prefer — pick one and be consistent) that builds a `ScenarioConfig` from today's `config.SCENARIO` dict **exactly**, so `WraithNetModel()` with no `scenario` argument produces the identical spawn as before this phase. Do not hand-retype the scenario numbers — construct it from `config.SCENARIO` so there is exactly one source of truth for the default scenario's values, or, if you judge it cleaner, keep `config.SCENARIO` as the literal and have `_default_scenario()` be a thin `ScenarioConfig(**config.SCENARIO)`-style wrapper. Either is fine; document which you picked in a one-line comment.
* Every field the spawning loop reads today (`t["target_id"]`, `t["position"]`, etc. from dict access) becomes an attribute access (`t.target_id`, `t.position`) since these are now Pydantic model instances, not dicts. Update all call sites accordingly — do not leave any `t["..."]`-style dict access against a `TargetSpec`/`JetSpec`/etc. instance.
* Also read `self._scenario.grid_width`/`grid_height` when constructing `mesa.space.ContinuousSpace` (currently hardcoded to `config.GRID_WIDTH`/`GRID_HEIGHT`) so a scenario with a custom grid size actually takes effect. Store the scenario's grid dims somewhere reachable (e.g. keep using `self.space.x_max`/`y_max` as the source of truth downstream, as `wraithnet/recorder.py::record_header` already does — Phase 11 deliberately read from `model.space`, not `config`, for exactly this future-proofing reason).

## Part D — `wraithnet/bridge.py` changes

```python
def __init__(
    self,
    recorder: TraceRecorder | None = None,
    scenario: ScenarioConfig | None = None,
) -> None:
    self.broker = MessageBroker()
    self.model = WraithNetModel(scenario=scenario)
    ...
```

Nothing else in `bridge.py` changes — `_tick()`, `run()`, `_notify_managers_of_losses()` are untouched; they operate on `self.model`/`self.agents` and have no scenario-specific logic themselves.

## Part E — `wraithnet/main.py` changes

Add an `argparse`-based `--scenario PATH` flag (stdlib `argparse`, no new dependency):

```python
import argparse
...

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the WraithNet PoC scenario.")
    parser.add_argument(
        "--scenario",
        type=str,
        default=None,
        help="Path to a scenario JSON file (wraithnet.scenario_schema.ScenarioConfig). "
             "Defaults to the built-in fixed PoC scenario if omitted.",
    )
    return parser.parse_args()


async def run_scenario(scenario: "ScenarioConfig | None" = None) -> WraithNetBridge:
    recorder = TraceRecorder("results/trace.jsonl")
    bridge = WraithNetBridge(recorder=recorder, scenario=scenario)
    try:
        await bridge.run()
    finally:
        recorder.close()
    return bridge


def main() -> None:
    args = parse_args()
    scenario = load_scenario(args.scenario) if args.scenario else None
    bridge = asyncio.run(run_scenario(scenario))
    ...  # rest unchanged
```

## Part F — `scenarios/default.json` (new file, new directory)

At the end of `Part C`'s implementation, write a tiny one-off script (or a `tests/`-adjacent helper you delete after running, your choice) that constructs `_default_scenario()` and calls `save_scenario(..., "scenarios/default.json")` once, so this file exists and is committed. It should round-trip exactly back to the built-in default scenario via `load_scenario`. This file is both a usage example and the fixture Part G's regression test uses.

## Part G — Tests

### `tests/test_scenario_schema.py` (new file)

* Valid `ScenarioConfig` construction with data matching the current `config.SCENARIO` passes with no error.
* Each of the five validation rules in Part A has at least one dedicated test that constructs an invalid `ScenarioConfig` and asserts `pytest.raises(ValidationError)` (or `ValueError`, whichever Pydantic surfaces — check `tests/test_schemas.py` for the existing convention in this codebase and match it): duplicate ID across two different entity lists (e.g. a jet and a target sharing an `agent_id`/`target_id` string), a position outside `[0, grid_width]×[0, grid_height]`, `hp_required=0`, a negative payload count, and a scenario with zero jets.
* Round-trip: `ScenarioConfig(...).model_dump_json()` then `ScenarioConfig.model_validate_json(...)` produces an equal object.

### `tests/test_scenario_io.py` (new file)

* `save_scenario` then `load_scenario` on a `tmp_path` file round-trips losslessly.
* `load_scenario` on `scenarios/default.json` (the real, committed file from Part F) succeeds and matches `_default_scenario()`'s output field-for-field.

### `tests/test_world.py` additions

* `WraithNetModel()` (no scenario argument) still spawns the exact same jets/targets/sam_sites/jammers as before this phase — assert on IDs, positions, payloads, hp_required, matching what already-existing tests assert today. **This is the single most important test in this phase** — every other Phase 0–13 test that constructs a bare `WraithNetModel()`/`WraithNetBridge()` is implicitly relying on this being true; do not skip it.
* `WraithNetModel(scenario=some_custom_scenario)` spawns exactly the entities described in `some_custom_scenario`, none of the default ones.
* A scenario with a non-default `grid_width`/`grid_height` results in `model.space.x_max`/`y_max` reflecting those custom values, not `config.GRID_WIDTH`/`GRID_HEIGHT`.

### `tests/test_bridge.py` / `tests/test_scenario_v2.py` additions (or a new dedicated test)

* `WraithNetBridge(scenario=some_custom_scenario)` runs to completion (`await bridge.run()`) against a small custom scenario (e.g. one jet, one one-hp target within immediate range) and solves it — proving the scenario plumbing actually reaches the running simulation, not just `__init__`.
* `python -m wraithnet.main --scenario scenarios/default.json` (invoke via `subprocess` in the test, or refactor `main()`'s core into a testable function if that's cleaner — match this codebase's existing convention for testing `main.py`, if any exists; if none exists, a `subprocess.run([...], capture_output=True)` integration test is acceptable) produces the exact same printed summary as `python -m wraithnet.main` with no `--scenario` flag.

## Explicit non-goals for this phase

* No 3D, no GUI, no `viewer3d/` package, no live streaming — those are Phases 15–19.
* No schema versioning/migration story for `ScenarioConfig` — if the schema needs to change later, that is a future concern, not this phase's.
* No change to `wraithnet/agents.py`, `wraithnet/broker.py`, or any negotiation/combat logic — this phase only changes *where entity data comes from at spawn time*.
* Do not remove `config.SCENARIO` from `wraithnet/config.py` — keep it as-is; it remains the single literal source that `_default_scenario()` is built from (or validated against), so nothing that currently reads `config.SCENARIO` directly elsewhere breaks.

## Acceptance criteria

1. `pytest` passes in full, including all new test files, with **zero behavior change** for any test that constructs `WraithNetModel()`/`WraithNetBridge()` with no scenario argument.
2. `python -m wraithnet.main` (no flags) produces the identical printed summary it did before this phase.
3. `python -m wraithnet.main --scenario scenarios/default.json` produces the identical printed summary as `python -m wraithnet.main` with no flags.
4. A hand-crafted custom scenario JSON file (different jet/target counts/positions) loads via `load_scenario` and runs to a sensible completion via `python -m wraithnet.main --scenario <that file>` without crashing.
5. `grep -r "config.SCENARIO\[" wraithnet/world.py` (or equivalent dict-subscript access against the old literal) returns no matches outside `_default_scenario()`'s own construction — proving the spawn loop itself is fully scenario-object-driven, not still secretly reading the old dict directly.
