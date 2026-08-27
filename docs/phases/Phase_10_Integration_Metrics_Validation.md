# Phase 10 — Integration, Metrics & V2 Scenario Validation

> **How to use this document:** This is a complete, standalone prompt. Give an LLM coding agent this entire file as its task. Assume Phases 0–9 are complete and passing: weapon typing (6), Fog of War (7), SAM sites/jet destruction/Manager Fallback (8), and jamming (9) are all implemented and individually tested. This is the **final phase of the Phase 2 Expansion** (mirrors what Phase 5 was for the original 6-phase V1 build). Read `docs/04_6th-Gen_Warfare_Sim_Design_Document_v3_Phase2.md` §2 and §9 before starting. This phase extends `wraithnet/world.py` (DataCollector + a small shared jamming-check helper), `wraithnet/metrics.py`, `wraithnet/main.py`, and adds `tests/test_scenario_v2.py`. Do not change negotiation logic, physics rules, or the Broker/Bridge integration points established in Phases 6–9 — if a validation assertion in this phase fails, the fix belongs in whichever earlier phase actually owns the broken behavior, not in loosening this phase's assertions to paper over it.

## Context

Phases 6–9 each shipped with their own focused, isolated tests. What's never been checked yet is: does the **entire** v3 scenario (`docs/04_..._v3_Phase2.md` §2 — weapon-typed targets, fog of war, a SAM site, a jammer, all four mechanics live simultaneously) actually resolve correctly end-to-end, with all the cross-feature interactions the design doc's scenario rationale specifically engineered (`Jet_2` likely passing near `SAM_Site_1`, `Jet_3` likely passing through `EW_Jammer_1`'s radius)? This phase is that final proof, plus the metrics needed to observe it.

**A methodological note on numeric bounds (read before writing `test_total_distance_traveled_is_reasonable`):** V1's Phase 5 could hand-derive an exact optimal-distance range from the scenario's geometry alone (two targets, no hazards, perfect information). This scenario has four interacting stochastic-in-timing (though *not* random — everything is still deterministic, just harder to hand-compute) mechanics: contact-propagation delay, possible SAM-driven re-tasking, possible jamming-driven negotiation stalls. Do **not** invent a precise-sounding numeric bound by guesswork. Instead: run the completed implementation once (e.g. via `python -m wraithnet.main` or a throwaway script), record the actual `total_distance_traveled` and `ticks_elapsed` it produces, and set the test's bounds generously around that *observed* value (document the observed value in a comment, the same way V1's Phase 5 test documented its hand-derived geometric optimum). This is a legitimate, honest way to write a regression-sanity bound for a system too complex to hand-derive exactly — it is not a workaround, as long as the bound is wide enough to tolerate legitimate run-to-run variation in *this specific deterministic scenario* (there should be none, since nothing here is randomized — see the determinism acceptance criterion) while remaining tight enough to catch a genuinely broken assignment (e.g. a jet looping forever, or all three jets converging on one target).

## Part A — `wraithnet/world.py`: shared jamming helper + extended `DataCollector`

### 1. Factor out a shared "is this jet currently jammed" helper (avoids duplicating the Phase 9 distance check)

```python
def _jet_is_jammed(self, jet: JetAgent) -> bool:
    return any(
        math.dist(jet.pos, jammer.position) <= config.JAM_RADIUS
        for jammer in self.jammers.values()
    )
```

Update `build_observation` (Phase 9) to call `self._jet_is_jammed(jet)` instead of inlining the comprehension, so there is exactly one place this computation lives.

### 2. Track cumulative jammed-agent-ticks and initial jet roster

In `__init__`, after `_spawn_scenario()`:
```python
self._initial_jet_ids: set[str] = set(self.jets.keys())   # NEW — jets are popped from self.jets on destruction (Phase 8); this preserves the original roster for "how many were lost" metrics
self.total_jammed_agent_ticks: int = 0                     # NEW
```

At the end of `step()`, before `self.datacollector.collect(self)`:
```python
self.total_jammed_agent_ticks += sum(1 for jet in self.jets.values() if self._jet_is_jammed(jet))
```

### 3. Extend the `DataCollector`

```python
self.datacollector = DataCollector(
    model_reporters={
        "Tick": lambda m: m.tick,
        "TotalDistanceTraveled": lambda m: sum(j.distance_traveled for j in m.jets.values()),
        "TotalMissilesFired": lambda m: sum(len(m.strike_log) for _ in [None]),  # see note below
        "TargetsDestroyed": lambda m: sum(1 for t in m.targets.values() if t.is_destroyed),
        "JetsDestroyed": lambda m: len(m._initial_jet_ids) - len(m.jets),          # NEW
        "SamSitesDestroyed": lambda m: sum(1 for tid in m.sam_sites if m.targets[tid].is_destroyed),  # NEW
        "TotalJammedAgentTicks": lambda m: m.total_jammed_agent_ticks,             # NEW
    },
    # agenttype_reporters (NOT flat agent_reporters): by this phase model.agents
    # also contains SamSiteAgent/EwJammerAgent (Phase 8/9), which lack
    # total_payload_remaining/distance_traveled — a flat agent_reporters dict
    # raises AttributeError the first time collect() walks a non-JetAgent. Scope
    # these reporters to JetAgent only (see docs/05_Code_Review_Phases_6-8.md §3).
    agenttype_reporters={
        JetAgent: {
            "TotalPayloadRemaining": "total_payload_remaining",
            "DistanceTraveled": "distance_traveled",
        },
    },
)
```

**Important — Mesa's own `self.time`/`self.steps` must actually advance:** `agent_reporters`/`agenttype_reporters` key their per-tick storage internally by `model.time` (not `model.tick`). Phase 1's `self.step = self._user_step` workaround (needed so callers can call `model.step(intents)` directly) bypasses Mesa's own step scheduler, which is what normally advances `self.time`/`self.steps` — left alone, both stay frozen at their `__init__` values (`0`/`0.0`) for the entire run, so every tick's `collect()` call silently overwrites the *same* `_agenttype_records[0.0]` entry instead of adding a new one, and `get_agenttype_vars_dataframe()` ends up returning only the last tick's snapshot instead of a full time series (`model_reporters` are unaffected — those append to a plain list, not a time-keyed dict). Fix this in `WraithNetModel.step()`, alongside the existing `self.tick += 1`:

```python
def step(self, intents: dict[str, ActionIntent] | None = None) -> None:
    intents = intents or {}
    self.tick += 1
    self.steps = self.tick     # NEW — keep Mesa's own counter in sync
    self.time = float(self.tick)  # NEW — DataCollector keys agent-level records by this
    ...
```

Add a regression test asserting `get_agenttype_vars_dataframe(JetAgent)`'s index contains one distinct `Step` value per collected tick (not just that the expected columns exist) — a test that only checks column presence after 1–2 `step()` calls will pass even with this bug present, since a single collapsed row still has the right columns.

**Note on `TotalMissilesFired`:** Phase 5's original reporter computed this from payload deltas (`_initial_total_payload - sum(current payload)`), which silently breaks once jets can be destroyed mid-run with unfired payload still in their inventory (that unfired payload simply vanishes from the "remaining" side of the subtraction along with the jet, overcounting "fired"). Replace it with a direct count from `len(model.strike_log)` (each entry is one successful strike, already only appended by `_apply_strike` on an actual hit — see Phase 6 Part C.4) — this is correct regardless of jets being destroyed with leftover payload. Remove the old `_initial_total_payload`-based computation from both the `DataCollector` lambda and `metrics.py` (Part B) — `strike_log` is a strictly more reliable source of truth for "how many missiles actually landed."

## Part B — `wraithnet/metrics.py`: full rewrite of `summarize()`

```python
"""Post-run metrics summary and CSV export for the WraithNet PoC."""

from collections import Counter
from pathlib import Path


def summarize(model) -> dict:
    """Human/test-friendly summary of a completed (or in-progress) run."""
    total_distance = sum(j.distance_traveled for j in model.jets.values())
    contributors_by_target: dict[str, set[str]] = {}
    missiles_by_weapon_type: Counter[str] = Counter()
    for entry in model.strike_log:
        contributors_by_target.setdefault(entry["target_id"], set()).add(entry["agent_id"])
        missiles_by_weapon_type[entry["weapon_type"]] += 1

    return {
        "ticks_elapsed": model.tick,
        "solved": all(t.is_destroyed for t in model.targets.values()),
        "swarm_wiped_out": model.swarm_wiped_out,                                   # NEW
        "targets_destroyed": sum(1 for t in model.targets.values() if t.is_destroyed),
        "total_targets": len(model.targets),
        "jets_destroyed": len(model._initial_jet_ids) - len(model.jets),            # NEW
        "jets_survived": len(model.jets),                                           # NEW
        "total_distance_traveled": total_distance,
        "total_missiles_fired": len(model.strike_log),                              # CHANGED: strike_log-based, see Part A note
        "per_weapon_type_missiles_fired": dict(missiles_by_weapon_type),            # NEW
        "total_jammed_agent_ticks": model.total_jammed_agent_ticks,                 # NEW
        "contributors_by_target": {k: sorted(v) for k, v in contributors_by_target.items()},
        "per_jet_distance_traveled": {aid: j.distance_traveled for aid, j in model.jets.items()},
        "per_jet_total_payload_remaining": {aid: j.total_payload_remaining for aid, j in model.jets.items()},
    }


def export_results(model, output_dir: str = "results") -> None:
    """Write Mesa's collected time series to CSV for offline inspection."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    model.datacollector.get_model_vars_dataframe().to_csv(out / "model_vars.csv", index_label="step")
    model.datacollector.get_agent_vars_dataframe().to_csv(out / "agent_vars.csv")
```

Note `per_jet_total_payload_remaining` only reports **surviving** jets (`model.jets` no longer contains destroyed ones, per Phase 8) — this is intentional; a destroyed jet's leftover payload is irrelevant to reporting since it never fires again. Update any code (`main.py`, tests) that read the old `per_jet_payload_remaining` key name.

## Part C — `wraithnet/main.py`: report the new metrics

```python
def main() -> None:
    bridge = asyncio.run(run_scenario())
    summary = summarize(bridge.model)

    print(f"Simulation finished at tick {summary['ticks_elapsed']}")
    if summary["swarm_wiped_out"]:
        print("OUTCOME: FAILURE — entire swarm destroyed before objective complete.")
    else:
        print(f"Solved: {summary['solved']} "
              f"({summary['targets_destroyed']}/{summary['total_targets']} targets destroyed)")
    print(f"Jets destroyed: {summary['jets_destroyed']} / survived: {summary['jets_survived']}")
    print(f"Total distance traveled: {summary['total_distance_traveled']:.2f}")
    print(f"Total missiles fired: {summary['total_missiles_fired']} {summary['per_weapon_type_missiles_fired']}")
    print(f"Total jammed-agent-ticks: {summary['total_jammed_agent_ticks']}")
    for target_id, contributors in summary["contributors_by_target"].items():
        print(f"  {target_id}: struck by {contributors}")
    for agent_id, distance in summary["per_jet_distance_traveled"].items():
        print(f"  {agent_id}: distance_traveled={distance:.2f}, "
              f"payload_remaining={summary['per_jet_total_payload_remaining'][agent_id]}")

    export_results(bridge.model)
    print("Results exported to ./results/model_vars.csv and ./results/agent_vars.csv")
```

## Part D — `tests/test_scenario_v2.py` (new file — the full cross-feature validation suite)

Use `@pytest.mark.asyncio` and a generous `asyncio.wait_for(..., timeout=30)` around every run, same pattern as `tests/test_scenario.py`.

```python
import asyncio
import pytest

from wraithnet.bridge import WraithNetBridge
from wraithnet.metrics import summarize


@pytest.mark.asyncio
async def test_v2_scenario_fully_resolves():
    """The v3 scenario's numbers are deliberately chosen with supply slack
    (docs/04_..._v3_Phase2.md §2) specifically so that infeasibility is never
    the failure mode — it must fully resolve, not merely 'terminate'."""
    bridge = WraithNetBridge()
    await asyncio.wait_for(bridge.run(max_ticks=1000), timeout=30)
    summary = summarize(bridge.model)

    assert summary["swarm_wiped_out"] is False, "unexpected total swarm loss — check SAM_DAMAGE_PER_TICK/JET_HP balance"
    assert summary["solved"] is True
    assert summary["targets_destroyed"] == 3  # Target_A, Target_B, SAM_Site_1
    assert bridge.model.tick < 1000


@pytest.mark.asyncio
async def test_target_a_requires_multi_jet_jdam_cooperation():
    bridge = WraithNetBridge()
    await asyncio.wait_for(bridge.run(max_ticks=1000), timeout=30)
    summary = summarize(bridge.model)

    contributors_a = summary["contributors_by_target"].get("Target_A", set())
    assert len(contributors_a) >= 2, (
        f"Target_A destroyed by only {contributors_a} — hp_required=5 exceeds any "
        "single jet's max JDAM count (4), so this should be structurally impossible."
    )


@pytest.mark.asyncio
async def test_target_b_solo_with_aim9x():
    bridge = WraithNetBridge()
    await asyncio.wait_for(bridge.run(max_ticks=1000), timeout=30)
    summary = summarize(bridge.model)

    contributors_b = summary["contributors_by_target"].get("Target_B", set())
    assert len(contributors_b) == 1
    b_strikes = [e for e in bridge.model.strike_log if e["target_id"] == "Target_B"]
    assert all(e["weapon_type"] == "AIM9X" for e in b_strikes)


@pytest.mark.asyncio
async def test_sam_site_destroyed_with_jdam():
    bridge = WraithNetBridge()
    await asyncio.wait_for(bridge.run(max_ticks=1000), timeout=30)
    summary = summarize(bridge.model)

    assert bridge.model.targets["SAM_Site_1"].is_destroyed
    sam_strikes = [e for e in bridge.model.strike_log if e["target_id"] == "SAM_Site_1"]
    assert all(e["weapon_type"] == "JDAM" for e in sam_strikes)


@pytest.mark.asyncio
async def test_no_wasted_or_misassigned_strikes():
    bridge = WraithNetBridge()
    await asyncio.wait_for(bridge.run(max_ticks=1000), timeout=30)
    summary = summarize(bridge.model)

    # 5 (Target_A) + 1 (Target_B) + 1 (SAM_Site_1) = 7, using exactly the
    # suitable weapon type for each target type (docs/04_...§2 WEAPON_SUITABILITY).
    assert summary["total_missiles_fired"] == 7
    assert summary["per_weapon_type_missiles_fired"] == {"JDAM": 6, "AIM9X": 1}


@pytest.mark.asyncio
async def test_fog_of_war_and_jamming_were_both_actually_exercised():
    """Prove the two comms-dependent mechanics were genuinely exercised by this
    real run, not just bypassed by coincidence (same principle used in Phases
    7 and 9's own tests, re-checked here at full-integration scale)."""
    bridge = WraithNetBridge()
    await asyncio.wait_for(bridge.run(max_ticks=1000), timeout=30)

    datalink_messages = [
        m for agent in bridge.agents.values() for m in agent.received_messages
        if m.message_type == "DATALINK_CONTACT"
    ]
    assert len(datalink_messages) > 0, "no DATALINK_CONTACT was ever received — Fog of War wasn't exercised"
    assert bridge.model.total_jammed_agent_ticks > 0, "no jet was ever jammed — check EW_Jammer_1 placement/JAM_RADIUS"


@pytest.mark.asyncio
async def test_no_agent_left_in_a_zombie_state():
    """End-of-run sanity check across every mechanic added in Phases 6-9:
    every surviving agent must have settled into a terminal, non-stuck role."""
    bridge = WraithNetBridge()
    await asyncio.wait_for(bridge.run(max_ticks=1000), timeout=30)

    for agent_id, agent in bridge.agents.items():
        assert agent.role in ("IDLE", "CONTRACTOR"), (
            f"{agent_id} ended in role={agent.role!r} — a surviving agent should "
            "never end a fully-resolved run still MANAGER-ing or CONTRACTOR_PENDING."
        )
        assert agent.pending_bid_since_tick is None
    for agent_id, agent in bridge.agents.items():
        assert agent.role == "CONTRACTOR" or not agent.managed_contracts.get(bridge.model.targets and "", set())
    # No live task raised an unexpected exception (mirrors test_bridge.py's Phase 4 check).
    for task in bridge._tasks:
        if task.cancelled():
            continue
        exc = task.exception()
        assert exc is None or isinstance(exc, asyncio.CancelledError)


@pytest.mark.asyncio
async def test_total_distance_traveled_is_reasonable():
    """See this phase doc's Context section for how this bound was derived —
    it is an empirically-observed-then-generously-bounded sanity check, not a
    hand-derived geometric optimum (unlike V1 Phase 5's simpler scenario).
    Replace <OBSERVED> below with the actual value from a real run before
    treating this test as final; do not leave the placeholder in committed code."""
    bridge = WraithNetBridge()
    await asyncio.wait_for(bridge.run(max_ticks=1000), timeout=30)
    summary = summarize(bridge.model)

    observed = summary["total_distance_traveled"]  # record this from your own real run
    assert 0.0 < observed < 600.0, (  # placeholder outer bound — tighten using the real observed value
        f"Total distance traveled ({observed:.2f}) is outside a sane range; if this is a "
        "legitimate, better-optimized outcome, tighten this bound to match, don't just widen it."
    )


@pytest.mark.asyncio
async def test_determinism_across_repeated_runs():
    """Nothing in this scenario is randomized (SAM/jamming are purely
    positional and deterministic — see design doc §2/§7/§8) — repeated runs
    from a fresh WraithNetBridge must produce identical outcomes."""
    results = []
    for _ in range(2):
        bridge = WraithNetBridge()
        await asyncio.wait_for(bridge.run(max_ticks=1000), timeout=30)
        summary = summarize(bridge.model)
        results.append((
            summary["targets_destroyed"], summary["jets_destroyed"],
            summary["total_missiles_fired"], summary["per_weapon_type_missiles_fired"],
            round(summary["total_distance_traveled"], 2),
        ))
    assert results[0] == results[1], f"non-deterministic outcome across runs: {results}"


def test_export_results_includes_new_columns(tmp_path):
    from wraithnet.world import WraithNetModel
    from wraithnet.metrics import export_results

    model = WraithNetModel()
    model.step({})
    model.step({})
    export_results(model, output_dir=str(tmp_path))

    import pandas as pd
    model_vars = pd.read_csv(tmp_path / "model_vars.csv")
    for column in ("JetsDestroyed", "SamSitesDestroyed", "TotalJammedAgentTicks"):
        assert column in model_vars.columns
```

`test_no_agent_left_in_a_zombie_state`'s middle loop is intentionally a light, defensive placeholder check — the meaningful assertions are the role check and the `pending_bid_since_tick is None` check above it; feel free to simplify it to just those two if the extra line doesn't add value in your implementation. Adjust `test_total_distance_traveled_is_reasonable`'s bound to a real observed value plus a sensible margin (e.g. ±15–25%) before considering this phase done — do not ship the wide placeholder bound as the final version; the Context section explains why a wide bound alone isn't sufficient (it wouldn't catch a real regression).

## Part E — Final consistency check across the whole expansion

Before declaring Phase 2 Expansion complete, verify (these are process checks, not new code):

1. `grep -r payload_remaining` (excluding `total_payload_remaining`/`per_jet_total_payload_remaining`) across `wraithnet/` and `tests/` returns zero matches — the old scalar field must be fully gone (re-check of Phase 6's own acceptance criterion, since Phases 7–10 also touch files that could have reintroduced a stray reference).
2. `python -m wraithnet.main` runs the full v3 scenario to completion and its printed summary matches what `tests/test_scenario_v2.py` asserts (3 targets destroyed, 7 total missiles, per-weapon breakdown `{"JDAM": 6, "AIM9X": 1}`).
3. The full `pytest` suite (Phases 0–10 combined) passes together, not just each phase's tests in isolation — run the whole suite once at the end, since some interactions (e.g. Phase 6's weapon typing combined with Phase 8's destruction combined with Phase 9's jamming) are only exercised together in `test_scenario_v2.py`.
4. `docs/6th-Gen_Warfare_Sim_Design_Document_v2.md` (V1) and `docs/04_..._v3_Phase2.md` (this expansion) both remain accurate descriptions of the shipped code — if any implementation detail had to deviate from either document while building Phases 6–10, update the relevant document rather than letting the docs drift out of sync with reality (same discipline the V1 build phases followed).

## Explicit non-goals for this phase

* No visualization/UI — CSV export remains sufficient, same as V1 Phase 5.
* No additional new gameplay mechanics — this phase is integration and validation only.
* No performance/scale testing beyond the fixed v3 scenario's exact entity counts (3 jets, 2 targets, 1 SAM site, 1 jammer).
* Do not weaken any of Phases 6–9's own test suites to make this phase's full-integration tests pass — if a Phase 6–9 unit test and this phase's end-to-end test disagree, the bug is real and must be fixed at its source, not hidden by loosening either test.

## Acceptance criteria

1. `pytest` passes for the entire suite (Phases 0–10), including every test in `tests/test_scenario_v2.py`.
2. `python -m wraithnet.main` runs the v3 scenario to completion, reporting all three CNP targets destroyed, correct per-weapon-type missile counts, and non-zero `total_jammed_agent_ticks`, and exports `results/model_vars.csv`/`results/agent_vars.csv` including the three new `JetsDestroyed`/`SamSitesDestroyed`/`TotalJammedAgentTicks` model-reporter columns.
3. `test_total_distance_traveled_is_reasonable`'s bound has been tightened from the placeholder to a real, observed-then-justified range (Part D note) — a submission that leaves the wide placeholder bound in place has not actually completed this phase's validation goal.
4. `test_determinism_across_repeated_runs` passes — the entire expansion remains fully deterministic end-to-end, exactly like V1.
5. The Part E consistency checks have all been performed and any resulting doc/code corrections applied.
