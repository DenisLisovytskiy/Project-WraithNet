# Phase 5 — Metrics, Data Collection & Final Scenario Validation

> **How to use this document:** This is a complete, standalone prompt. Give an LLM coding agent this entire file as its task. Assume Phases 0–4 are complete and the full scenario already runs end-to-end via `wraithnet/bridge.py` / `wraithnet/main.py` (Phase 4 acceptance criteria are met). This phase adds **metrics collection** and a **rigorous final validation** that the PoC actually demonstrates what `docs/6th-Gen_Warfare_Sim_Design_Document_v2.md` set out to prove. Edit `wraithnet/world.py` (small, additive change), `wraithnet/metrics.py`, `wraithnet/main.py`, and add `tests/test_scenario.py`. Do not change `schemas.py`, `broker.py`, `agents.py`, or `bridge.py` — if a validation assertion fails, the fix belongs in this phase's own understanding/assertions, not in silently changing earlier phases' negotiation logic to force a specific outcome.

## Context

WraithNet is a Proof-of-Concept multi-agent simulator: autonomous jets negotiate over radio (Contract Net Protocol) to decide who strikes which target. This is **Phase 5 of 6, the final phase**. Termination detection (`model.running`) and strike bookkeeping (`model.strike_log`) already exist from Phase 1. What's missing is (a) a proper Mesa `DataCollector` for time-series metrics, and (b) an actual validation run that checks the PoC's stated objective was met — not just "it didn't crash."

Recall the fixed scenario (`config.SCENARIO`, all in `wraithnet/config.py`):
* `Target_A`: `hp_required=5` — deliberately more than any single jet's max payload (`4`), so destroying it **structurally requires at least 2 jets to cooperate** (see `docs/00_Design_Review_Findings.md` item F1). This is the property this phase must explicitly verify.
* `Target_B`: `hp_required=1` — solo-able by any one jet.
* `Jet_1` (payload 1, near `Target_A`), `Jet_2` and `Jet_3` (payload 4 each, both distant, exactly equidistant — ≈72.80 units — from both targets).

## Part A — Extend `wraithnet/world.py`: add a `DataCollector`

Add to `WraithNetModel.__init__`, after `_spawn_scenario()`:

```python
from mesa.datacollection import DataCollector

# in __init__, after self._spawn_scenario():
self._initial_total_payload = sum(j.payload_remaining for j in self.jets.values())
self.datacollector = DataCollector(
    model_reporters={
        "Tick": lambda m: m.tick,
        "TotalDistanceTraveled": lambda m: sum(j.distance_traveled for j in m.jets.values()),
        "TotalMissilesFired": lambda m: m._initial_total_payload
        - sum(j.payload_remaining for j in m.jets.values()),
        "TargetsDestroyed": lambda m: sum(1 for t in m.targets.values() if t.is_destroyed),
    },
    agent_reporters={
        "PayloadRemaining": "payload_remaining",
        "DistanceTraveled": "distance_traveled",
    },
)
self.datacollector.collect(self)  # capture the tick-0 baseline before any steps
```

And at the very end of `step()` (after the existing `self.running` update from Phase 1), add:

```python
        self.datacollector.collect(self)
```

Add `pandas>=2.0,<3.0` to `requirements.txt` explicitly (Mesa's `DataCollector` depends on it transitively, but pin it directly since this phase's code imports it too).

## Part B — Implement `wraithnet/metrics.py`

```python
"""Post-run metrics summary and CSV export for the WraithNet PoC."""

from pathlib import Path


def summarize(model) -> dict:
    """Human/test-friendly summary of a completed (or in-progress) run."""
    total_distance = sum(j.distance_traveled for j in model.jets.values())
    total_fired = model._initial_total_payload - sum(
        j.payload_remaining for j in model.jets.values()
    )
    contributors_by_target: dict[str, set[str]] = {}
    for entry in model.strike_log:
        contributors_by_target.setdefault(entry["target_id"], set()).add(entry["agent_id"])
    return {
        "ticks_elapsed": model.tick,
        "solved": all(t.is_destroyed for t in model.targets.values()),
        "targets_destroyed": sum(1 for t in model.targets.values() if t.is_destroyed),
        "total_targets": len(model.targets),
        "total_distance_traveled": total_distance,
        "total_missiles_fired": total_fired,
        "contributors_by_target": {k: sorted(v) for k, v in contributors_by_target.items()},
        "per_jet_distance_traveled": {aid: j.distance_traveled for aid, j in model.jets.items()},
        "per_jet_payload_remaining": {aid: j.payload_remaining for aid, j in model.jets.items()},
    }


def export_results(model, output_dir: str = "results") -> None:
    """Write Mesa's collected time series to CSV for offline inspection."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    model.datacollector.get_model_vars_dataframe().to_csv(out / "model_vars.csv", index_label="step")
    model.datacollector.get_agent_vars_dataframe().to_csv(out / "agent_vars.csv")
```

## Part C — Update `wraithnet/main.py`

```python
"""Entrypoint: run the fixed WraithNet PoC scenario end-to-end and report results."""

import asyncio

from wraithnet.bridge import WraithNetBridge
from wraithnet.metrics import export_results, summarize


async def run_scenario() -> WraithNetBridge:
    bridge = WraithNetBridge()
    await bridge.run()
    return bridge


def main() -> None:
    bridge = asyncio.run(run_scenario())
    summary = summarize(bridge.model)

    print(f"Simulation finished at tick {summary['ticks_elapsed']}")
    print(f"Solved: {summary['solved']} "
          f"({summary['targets_destroyed']}/{summary['total_targets']} targets destroyed)")
    print(f"Total distance traveled: {summary['total_distance_traveled']:.2f}")
    print(f"Total missiles fired: {summary['total_missiles_fired']}")
    for target_id, contributors in summary["contributors_by_target"].items():
        print(f"  {target_id}: struck by {contributors}")
    for agent_id in bridge.model.jets:
        print(f"  {agent_id}: distance_traveled="
              f"{summary['per_jet_distance_traveled'][agent_id]:.2f}, "
              f"payload_remaining={summary['per_jet_payload_remaining'][agent_id]}")

    export_results(bridge.model)
    print("Results exported to ./results/model_vars.csv and ./results/agent_vars.csv")


if __name__ == "__main__":
    main()
```

## Part D — The validation test suite: `tests/test_scenario.py`

This is the most important test file in the whole project — it is the one that proves the PoC actually demonstrates decentralized multi-agent cooperation, not just "the code runs." Use `@pytest.mark.asyncio` and a generous timeout (e.g. wrap in `asyncio.wait_for(..., timeout=30)`).

```python
import asyncio
import pytest

from wraithnet.bridge import WraithNetBridge
from wraithnet.metrics import summarize


@pytest.mark.asyncio
async def test_scenario_fully_resolves():
    bridge = WraithNetBridge()
    await asyncio.wait_for(bridge.run(max_ticks=500), timeout=30)
    summary = summarize(bridge.model)

    assert summary["solved"] is True
    assert summary["targets_destroyed"] == 2
    assert bridge.model.tick < 500  # terminated naturally, not via the safety cap


@pytest.mark.asyncio
async def test_target_a_required_genuine_cooperation():
    """The core property this whole design exists to demonstrate: Target_A
    cannot be destroyed by any single jet (hp_required=5 > max payload=4),
    so its strike_log must show contributions from more than one agent."""
    bridge = WraithNetBridge()
    await asyncio.wait_for(bridge.run(max_ticks=500), timeout=30)
    summary = summarize(bridge.model)

    contributors_a = summary["contributors_by_target"].get("Target_A", set())
    assert len(contributors_a) >= 2, (
        f"Target_A was destroyed by only {contributors_a} — this means a single "
        "jet soloed a 5-HP target with a 4-missile payload, which should be "
        "physically impossible. Check world.py's strike/payload accounting."
    )


@pytest.mark.asyncio
async def test_target_b_does_not_require_unnecessary_cooperation():
    """Target_B (hp_required=1) should be solo-able; the protocol shouldn't
    force needless multi-jet involvement when one jet suffices."""
    bridge = WraithNetBridge()
    await asyncio.wait_for(bridge.run(max_ticks=500), timeout=30)
    summary = summarize(bridge.model)

    contributors_b = summary["contributors_by_target"].get("Target_B", set())
    assert len(contributors_b) == 1


@pytest.mark.asyncio
async def test_total_missiles_fired_matches_requirement_exactly():
    bridge = WraithNetBridge()
    await asyncio.wait_for(bridge.run(max_ticks=500), timeout=30)
    summary = summarize(bridge.model)

    # 5 for Target_A + 1 for Target_B = 6. No wasted/extra strikes.
    assert summary["total_missiles_fired"] == 6


@pytest.mark.asyncio
async def test_total_distance_traveled_is_near_optimal():
    """Sanity bound on the stated objective (minimize total distance).
    Given the scenario's exact coordinates, the geometrically optimal plan is:
    Jet_1 travels ~7.07 to Target_A, one distant jet travels ~72.80 to help
    finish Target_A, and the other distant jet travels ~72.80 to solo
    Target_B — total ~152.67. This bound is deliberately a bit generous
    (rather than an exact float equality) to tolerate legitimate movement
    quantization, while still being tight enough to catch a genuinely worse
    assignment (e.g. both distant jets converging on the same target while
    Jet_1 has to trek all the way to the other one, which would push the
    total well past 200)."""
    bridge = WraithNetBridge()
    await asyncio.wait_for(bridge.run(max_ticks=500), timeout=30)
    summary = summarize(bridge.model)

    assert 140.0 <= summary["total_distance_traveled"] <= 165.0, (
        f"Total distance traveled was {summary['total_distance_traveled']:.2f}, "
        "outside the expected near-optimal range — inspect "
        "summary['contributors_by_target'] to see which jets were assigned "
        "where."
    )


def test_export_results_writes_csv(tmp_path):
    """metrics.export_results should not require a full async run to be unit-testable
    in isolation — construct a model, step it a couple of times, and export."""
    from wraithnet.world import WraithNetModel
    from wraithnet.metrics import export_results

    model = WraithNetModel()
    model.step({})
    model.step({})
    export_results(model, output_dir=str(tmp_path))

    assert (tmp_path / "model_vars.csv").exists()
    assert (tmp_path / "agent_vars.csv").exists()
```

## Explicit non-goals for this phase

* No visualization/UI (e.g. Mesa's `SolaraViz`) — out of scope for this PoC per the original design document; CSV export is sufficient for offline inspection.
* No performance/scale testing (e.g. hundreds of jets) — this PoC is fixed at exactly 3 jets and 2 targets by design.
* Do not weaken or special-case `test_target_a_required_genuine_cooperation` to pass by hardcoding which jets must contribute — if this test fails, the bug is in the negotiation logic (Phase 3) or the Bridge (Phase 4), and should be diagnosed there, not patched around here.

## Acceptance criteria

1. `pytest` passes, including every test in `tests/test_scenario.py`.
2. Running `python -m wraithnet.main` prints a clear summary showing both targets destroyed, which jets struck `Target_A` (must be >= 2 distinct agents) and which struck `Target_B` (must be exactly 1), total distance traveled, and total missiles fired — and writes `results/model_vars.csv` and `results/agent_vars.csv`.
3. `results/model_vars.csv` has one row per tick with columns for `TotalDistanceTraveled`, `TotalMissilesFired`, and `TargetsDestroyed` that monotonically move toward the final state (distance and missiles-fired are non-decreasing tick over tick; targets-destroyed ends at `2`).
4. This completes the PoC: at this point, `docs/6th-Gen_Warfare_Sim_Design_Document_v2.md`'s stated objective (§2) is mechanically verified by an automated test, not just asserted in prose.
