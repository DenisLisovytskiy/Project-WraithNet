# Phase 11 — Simulation Trace Recorder

> **How to use this document:** This is a complete, standalone prompt. Give an LLM coding agent this entire file as its task. Assume Phases 0–10 are complete and passing (the full V1 + Phase 2 Expansion simulation works end-to-end — `python -m wraithnet.main` solves the fixed scenario deterministically). Read `docs/07_Visualization_Design_Document.md` in full before starting — it explains *why* this is a recorded-trace architecture rather than a live viewer, and defines the exact schema this phase implements. This phase touches `wraithnet/bridge.py` and `wraithnet/main.py` only to add an **optional** hook — it must not change any existing simulation behavior, timing, or test outcomes. It adds two new modules (`wraithnet/trace_schemas.py`, `wraithnet/recorder.py`) and one new test file. **This phase does not touch Pygame, does not add a GUI, and does not render anything** — it only produces the data file that Phase 12 will later read.

## Context

This is Phase 11 of a 3-phase visualization feature (11–13) layered on top of the already-complete WraithNet simulation. Its sole job is to make it possible to record a run to a file that fully describes, tick by tick, everything a later top-down 2D renderer will need — without adding any new dependency to the core `wraithnet` package, and without changing what the simulation actually does.

## Part A — `wraithnet/trace_schemas.py` (new file)

Implement exactly these Pydantic models (see design doc §3 for the full rationale behind each field/omission — do not add fields not listed here, and do not drop any):

```python
"""Pydantic schemas for the simulation trace file consumed by the Pygame
replay viewer (viewer/ package, Phase 12+). This module must import only
pydantic and stdlib — never pygame, never anything from viewer/.

See docs/07_Visualization_Design_Document.md §3 for the full rationale.
"""

from typing import Optional

from pydantic import BaseModel

from wraithnet.config import TargetType, WeaponType


class TraceHeader(BaseModel):
    grid_width: float
    grid_height: float
    sensor_radius: float
    jam_radius: float
    sam_engagement_range: float
    strike_range: float
    jet_ids: list[str]
    initial_payload: dict[str, dict[WeaponType, int]]
    target_ids: list[str]
    target_types: dict[str, TargetType]
    target_hp_required: dict[str, int]
    sam_site_ids: list[str]
    jammer_positions: dict[str, tuple[float, float]]


class JetFrame(BaseModel):
    agent_id: str
    position: tuple[float, float]
    hp: int
    payload: dict[WeaponType, int]
    destroyed: bool
    jammed: bool
    role: Optional[str]


class TargetFrame(BaseModel):
    target_id: str
    hp_remaining: int
    claimed: bool


class StrikeEvent(BaseModel):
    agent_id: str
    target_id: str
    weapon_type: WeaponType


class TraceTick(BaseModel):
    tick: int
    jets: list[JetFrame]
    targets: list[TargetFrame]
    strikes: list[StrikeEvent]
    running: bool
    swarm_wiped_out: bool
```

## Part B — `wraithnet/recorder.py` (new file)

```python
"""Optional trace recorder for the Pygame replay viewer. Attaching a
TraceRecorder to a WraithNetBridge has zero effect on simulation logic,
timing, or determinism -- see docs/07_Visualization_Design_Document.md §4
and this phase's acceptance criteria for the tests that prove it.
"""

import json
from pathlib import Path

from wraithnet.bridge import WraithNetBridge  # for typing only; avoid a hard
                                                # runtime import cycle -- see note below
from wraithnet.trace_schemas import JetFrame, StrikeEvent, TargetFrame, TraceHeader, TraceTick


class TraceRecorder:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self._path.open("w", encoding="utf-8")

    def record_header(self, bridge: "WraithNetBridge") -> None:
        model = bridge.model
        header = TraceHeader(
            grid_width=model.space.x_max,
            grid_height=model.space.y_max,
            sensor_radius=config.SENSOR_RADIUS,
            jam_radius=config.JAM_RADIUS,
            sam_engagement_range=config.SAM_ENGAGEMENT_RANGE,
            strike_range=config.STRIKE_RANGE,
            jet_ids=list(model.jets.keys()),
            initial_payload={aid: dict(j.payload) for aid, j in model.jets.items()},
            target_ids=list(model.targets.keys()),
            target_types={tid: t.target_type for tid, t in model.targets.items()},
            target_hp_required={tid: t.hp_required for tid, t in model.targets.items()},
            sam_site_ids=list(model.sam_sites.keys()),
            jammer_positions={jid: j.position for jid, j in model.jammers.items()},
        )
        self._file.write(header.model_dump_json() + "\n")

    def record_tick(self, bridge: "WraithNetBridge") -> None:
        model = bridge.model
        jets = [
            JetFrame(
                agent_id=aid,
                position=jet.pos,
                hp=jet.hp,
                payload=dict(jet.payload),
                destroyed=False,
                jammed=model._jet_is_jammed(jet),
                role=bridge.agents[aid].role if aid in bridge.agents else None,
            )
            for aid, jet in model.jets.items()
        ]
        targets = [
            TargetFrame(target_id=tid, hp_remaining=t.hp_remaining, claimed=t.is_destroyed is False and self._is_claimed(bridge, tid))
            for tid, t in model.targets.items()
        ]
        strikes = [
            StrikeEvent(agent_id=e["agent_id"], target_id=e["target_id"], weapon_type=e["weapon_type"])
            for e in model.strike_log
            if e["tick"] == model.tick
        ]
        tick = TraceTick(
            tick=model.tick,
            jets=jets,
            targets=targets,
            strikes=strikes,
            running=model.running,
            swarm_wiped_out=model.swarm_wiped_out,
        )
        self._file.write(tick.model_dump_json() + "\n")

    def close(self) -> None:
        self._file.close()
```

Notes for the implementer (do not deviate from these without a good reason, and if you do, document it the way earlier phases document deliberate deviations):

* **Destroyed jets never appear in a `TraceTick`'s `jets` list at all** (once removed from `model.jets`, per Phase 8), not as an entry with `destroyed=True` — this mirrors exactly how `model.jets`/`bridge.agents` already work, and keeps the recorder a thin read-only reflection of existing state rather than inventing new "tombstone" bookkeeping. (The `destroyed: bool` field on `JetFrame` is reserved for a possible future need but will always be `False` as currently wired — leave it as specified in the schema for forward-compatibility rather than removing it, but do not add tombstone-tracking logic in this phase.)
* **`claimed`** should reflect `bridge.broker.is_claimed(target_id)` — read it directly from the Broker (`bridge.broker`), do not reimplement the logic. Fix the pseudocode above accordingly (the `self._is_claimed` placeholder shown is not a real method — replace that whole expression with `bridge.broker.is_claimed(tid)`).
* **Avoid a real import cycle**: `wraithnet/bridge.py` will need to reference `TraceRecorder` for its type hint (Part C). Use `from __future__ import annotations` plus a `TYPE_CHECKING`-guarded import in `recorder.py` for the `WraithNetBridge` type hint (or a forward-reference string), so `recorder.py` and `bridge.py` don't import each other eagerly at module load time. Get this right — a naive circular import will break `python -m wraithnet.main`.
* **`config` must be imported** in `recorder.py` (`from wraithnet import config`) — it's used for `SENSOR_RADIUS`/`JAM_RADIUS`/`SAM_ENGAGEMENT_RANGE`/`STRIKE_RANGE` in `record_header`. The shown snippet omits this import; add it.

## Part C — `wraithnet/bridge.py` changes

```python
class WraithNetBridge:
    def __init__(self, recorder: "TraceRecorder | None" = None) -> None:
        self.broker = MessageBroker()
        self.model = WraithNetModel()
        self.agents: dict[str, JetAgentRuntime] = {
            agent_id: JetAgentRuntime(agent_id, self.broker)
            for agent_id in self.model.jets
        }
        self._tasks: list[asyncio.Task] = []
        self.recorder = recorder
        if self.recorder is not None:
            self.recorder.record_header(self)
            self.recorder.record_tick(self)   # tick-0 baseline, mirrors DataCollector's own tick-0 collect() call
```

And in `_tick()`, add exactly one new line at the very end (after the existing contract-cleanup step, so the recorded state reflects post-cleanup reality):

```python
async def _tick(self) -> None:
    ... # everything existing, unchanged
    self.broker.expire_stale_contracts(self.model.tick)
    if self.recorder is not None:   # NEW
        self.recorder.record_tick(self)
```

Do not add a `recorder` parameter anywhere else, and do not make the default anything other than `None`.

## Part D — `wraithnet/main.py` changes

Wire up a real recorder for actual runs only, writing to `results/trace.jsonl` (alongside the existing `model_vars.csv`/`agent_vars.csv`):

```python
async def run_scenario() -> WraithNetBridge:
    recorder = TraceRecorder("results/trace.jsonl")
    bridge = WraithNetBridge(recorder=recorder)
    try:
        await bridge.run()
    finally:
        recorder.close()
    return bridge
```

## Part E — Tests

### `tests/test_trace_schemas.py` (new file)

* Round-trip each schema: construct a `TraceHeader`/`TraceTick` with representative data, call `.model_dump_json()`, then `TraceHeader.model_validate_json(...)`/`TraceTick.model_validate_json(...)` on the result, and assert equality with the original.
* `TargetFrame`/`JetFrame`/`StrikeEvent` accept valid `WeaponType`/`TargetType` literals and reject invalid ones (`pytest.raises(ValidationError)`), consistent with how `tests/test_schemas.py` already tests `wraithnet/schemas.py`.

### `tests/test_recorder.py` (new file)

* Build a `WraithNetBridge(recorder=TraceRecorder(tmp_path / "trace.jsonl"))`, run it for a handful of ticks (`await bridge._tick()` a few times, or a short `bridge.run(max_ticks=5)` with `fast_cnp`-style monkeypatched timeouts as other test files already do), close the recorder, then read the file back line by line:
  * Line 1 parses as a valid `TraceHeader` whose `jet_ids`/`target_ids`/`sam_site_ids`/`jammer_positions` match `bridge.model`'s actual scenario data.
  * Every subsequent line parses as a valid `TraceTick`; `tick` values are non-decreasing and start at `0`.
  * A tick on which a `_apply_strike` actually landed (force one deterministically, the same way `tests/test_world.py::test_strike_in_range_damages_and_logs` does, by moving a jet into range and stepping directly with a `STRIKE` intent through the Bridge/model) shows up in that tick's `TraceTick.strikes`, and does **not** also appear in the next tick's `strikes` (i.e., strikes are per-tick, not cumulative — regression-guard this explicitly, mirroring the `docs/06_Code_Review_Full_Project.md` §1 lesson about cumulative-vs-per-tick counting bugs).
  * A jet placed within `JAM_RADIUS` of the jammer shows `jammed=True` in that tick's `JetFrame`; one that isn't shows `False`.
  * A destroyed jet (force `jet.hp = 0` then advance a tick, same pattern `tests/test_bridge.py` already uses) is **absent** from the following tick's `jets` list, not present with `destroyed=True`.
  * `role` matches `bridge.agents[agent_id].role` at the moment `record_tick` was called, for a jet in each of at least two different roles across the recorded run (e.g. `IDLE` and `CONTRACTOR`).
* **Regression test proving zero behavioral impact:** run the same fixed-seed scenario twice — once via `WraithNetBridge()` (no recorder) and once via `WraithNetBridge(recorder=TraceRecorder(tmp_path / "trace.jsonl"))` — and assert `summarize(bridge.model)` is identical between the two (same pattern as `tests/test_scenario_v2.py::test_determinism_across_repeated_runs`). This is the test that actually proves the "zero effect on simulation logic" claim in the design doc, not just an assertion in a docstring.
* A trace recorded from a full real run (`WraithNetBridge(recorder=...)`, `await bridge.run()`) results in a valid, fully-parseable JSONL file whose final `TraceTick.tick` equals `bridge.model.tick`, and whose `running`/`swarm_wiped_out` on that final line match `bridge.model.running`/`bridge.model.swarm_wiped_out`.

## Explicit non-goals for this phase

* No Pygame import anywhere in `wraithnet/` (verify with a quick `grep -r pygame wraithnet/` returning nothing) — this phase is pure data recording.
* No `viewer/` package yet — that's Phase 12.
* No per-agent `world_view`/Fog-of-War-belief recording, no message-bus/negotiation-message recording (design doc §7 — explicit scope cuts, not oversights).
* No change to `wraithnet/world.py`, `wraithnet/agents.py`, or `wraithnet/broker.py` — this phase only reads existing state through `wraithnet/bridge.py`; it must not need to add any new attribute to those modules. If you find yourself needing to add one, stop and re-read design doc §3 — everything needed (`_jet_is_jammed`, `is_claimed`, `strike_log`, `role`) already exists.

## Acceptance criteria

1. `pytest` passes, including the two new test files, with **no new dependency** beyond what `requirements-dev.txt` already has (Pydantic + pytest + pytest-asyncio — no Pygame).
2. `python -m wraithnet.main` still runs the full v3 scenario to completion, printing the exact same summary as before this phase, and additionally produces `results/trace.jsonl`.
3. The regression test proving `WraithNetBridge()` vs. `WraithNetBridge(recorder=...)` produce identical `summarize()` output passes.
4. `grep -r pygame wraithnet/` returns no matches.
5. Every line of a real `results/trace.jsonl` (from a full run of `python -m wraithnet.main`) round-trips through `TraceHeader.model_validate_json`/`TraceTick.model_validate_json` without error.
