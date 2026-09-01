# Phase 23 — Evasion, Metrics & Termination

> **How to use this document:** This is a complete, standalone prompt. Give an LLM coding agent this entire file as its task. Assume Phases 0–22 are complete and passing (jets have fuel, autonomously RTB, land safely, or are lost en route). Read `docs/10_Airframes_Weapons_and_Fuel_Design_Document.md` §1, §4, §6, and §8 in full before starting. This phase does two things: (1) makes `evasion_bonus` (defined since Phase 20, unused until now) reduce SAM damage deterministically, and (2) fixes `wraithnet/metrics.py` and `wraithnet/world.py`'s termination condition to correctly account for a jet that leaves `model.jets` by landing safely rather than being destroyed — including a **pre-existing latent bug**, found while designing this phase, that predates this entire expansion (see Part B).

> **Automated GUI tests:** Follow `docs/14_Automated_GUI_Testing_Design_Document.md`. This phase is simulation-core, but any new viewer-facing state must be covered by automated `viewer3d` tests in the phase that visualizes it (Phase 24) — and if you touch `viewer3d/` here, add those tests in *this* phase. Do not leave 3D GUI coverage as a later follow-up or as manual-only. Pixel/screenshot tests remain out of scope; headless Layer-1 / Layer-2 tests are required for GUI changes.

## Context

Phase 22 gave jets a real reason to leave `model.jets` mid-run for a *good* reason (landing safely) rather than only a bad one (destruction). Nothing downstream was ever designed to tell the two apart — not the termination check (`swarm_wiped_out`), and not `metrics.py`. This phase fixes both, and along the way fixes a genuine pre-existing bug: **any jet ever removed from `model.jets` — even before this expansion, e.g. a SAM kill — has always had its `distance_traveled`/final payload silently dropped from every totals/per-jet metric**, because `metrics.py::summarize` only ever iterates `model.jets.values()`, which no longer contains a removed jet at all. This was harmless-ish for a wipeout-only outcome (the run is a clear failure either way) but is a real correctness problem for RTB, where "how far did the returning jets fly and how much ordnance did they bring home" is exactly the kind of number a user would expect to see correctly reported.

## Part A — `wraithnet/world.py`: evasion-reduced SAM damage

```python
def _apply_sam_damage(self) -> None:
    for sam_target_id, sam in self.sam_sites.items():
        if self.targets[sam_target_id].is_destroyed:
            continue
        for jet in self.jets.values():
            if math.dist(jet.pos, sam.position) <= config.SAM_ENGAGEMENT_RANGE:
                evasion = config.JET_FRAME_CATALOG[jet.frame_type].evasion_bonus
                damage = max(0, config.SAM_DAMAGE_PER_TICK - evasion)
                jet.hp -= damage
    ...  # reaping loop unchanged in shape (still just checks jet.is_destroyed here;
         # Phase 22 already generalized the OTHER reap call site in step() to also
         # check fuel -- do not duplicate that here, this method's own local reap
         # loop, if Phase 22 left one, should be consolidated into the single
         # generalized reap call in step() rather than having two separate sweeps.
         # Check Phase 22's actual final structure before assuming there are two.
```

Still fully deterministic — `evasion_bonus` is a flat integer reduction, never a probability roll, preserving `SAM_DAMAGE_PER_TICK`'s existing `# deterministic, no RNG` invariant (design doc §3). A `HEAVY_BOMBER` (evasion `0`, per Phase 20's illustrative catalog) takes full damage; an `INTERCEPTOR` (evasion `2`) takes less or none.

## Part B — `wraithnet/world.py`: persistent per-jet outcome snapshots (fixes the pre-existing bug)

Add a small record type and a persistent store that survives jet removal:

```python
@dataclass(frozen=True)
class JetOutcome:
    agent_id: str
    frame_type: "JetFrameType"
    outcome: Literal["destroyed", "returned_to_base"]
    distance_traveled: float
    final_payload_remaining: dict[str, int]
```

`WraithNetModel.__init__` gains `self.jet_outcomes: dict[str, JetOutcome] = {}`. **Every place `world.py` currently removes a jet from `self.jets`** (the landing removal and the generalized destroyed/fuel-exhausted reap, both from Phase 22 — re-read that phase's Part C for the exact two call sites before editing) must, immediately before removal, snapshot it:

```python
self.jet_outcomes[jet.agent_id] = JetOutcome(
    agent_id=jet.agent_id, frame_type=jet.frame_type,
    outcome="returned_to_base",  # or "destroyed", matching which removal path this is
    distance_traveled=jet.distance_traveled,
    final_payload_remaining=dict(jet.payload),
)
```

Add an invariant helper (used by tests, and genuinely useful for any future debugging):

```python
def all_jet_ids(self) -> set[str]:
    """Every jet that ever existed this run, alive or not. Must always equal
    self._initial_jet_ids -- if it doesn't, a jet was removed from self.jets
    without a corresponding JetOutcome being recorded, which is exactly the
    class of bug this phase exists to fix. See tests/test_world.py."""
    return set(self.jets) | set(self.jet_outcomes)
```

## Part C — `wraithnet/world.py`: termination condition fix

Today: `elif len(self.jets) == 0: self.running = False; self.swarm_wiped_out = True` — this is wrong once a jet can leave `self.jets` by landing safely. Replace with:

```python
if all(t.is_destroyed for t in self.targets.values()):
    self.running = False  # success, unchanged
elif len(self.jets) == 0:
    self.running = False
    if self.jets_returned_to_base:
        self.mission_aborted = True   # NEW -- at least one jet made it home; not a wipeout
    else:
        self.swarm_wiped_out = True   # every jet that left play was destroyed -- a true wipeout
```

Add `self.mission_aborted: bool = False` to `__init__` alongside the existing `self.swarm_wiped_out: bool = False`. These two flags are mutually exclusive by construction (the `if self.jets_returned_to_base` branch above never sets both) — add a test asserting they're never both `True` at once for any reachable game state.

**Naming note**: "mission aborted" here specifically means "the swarm ran out of fuel and had to go home before finishing the objective" — not a general-purpose abort flag for any other reason. If a future phase needs a different abort cause, it should get its own flag, not overload this one (mirrors this project's existing discipline of one flag per specific cause — e.g. `swarm_wiped_out` is not reused for "ran out of time" either).

## Part D — `wraithnet/metrics.py`: correctness overhaul

```python
def summarize(model) -> dict:
    live_stats = {
        aid: (jet.distance_traveled, dict(jet.payload)) for aid, jet in model.jets.items()
    }
    past_stats = {
        aid: (o.distance_traveled, dict(o.final_payload_remaining))
        for aid, o in model.jet_outcomes.items()
    }
    all_stats = {**past_stats, **live_stats}  # union over every jet that ever existed

    total_distance = sum(d for d, _ in all_stats.values())
    contributors_by_target: dict[str, set[str]] = {}
    missiles_by_weapon_type: Counter[str] = Counter()
    for entry in model.strike_log:
        contributors_by_target.setdefault(entry["target_id"], set()).add(entry["agent_id"])
        missiles_by_weapon_type[entry["weapon_type"]] += 1

    initial_jet_ids = getattr(model, "_initial_jet_ids", set(model.jets.keys()))
    jets_destroyed = sum(1 for o in model.jet_outcomes.values() if o.outcome == "destroyed")
    jets_returned_to_base = sum(1 for o in model.jet_outcomes.values() if o.outcome == "returned_to_base")

    return {
        "ticks_elapsed": model.tick,
        "solved": all(t.is_destroyed for t in model.targets.values()),
        "swarm_wiped_out": model.swarm_wiped_out,
        "mission_aborted": getattr(model, "mission_aborted", False),  # NEW
        "targets_destroyed": sum(1 for t in model.targets.values() if t.is_destroyed),
        "total_targets": len(model.targets),
        "jets_destroyed": jets_destroyed,                      # CHANGED -- was len(initial) - len(current)
        "jets_returned_to_base": jets_returned_to_base,         # NEW
        "jets_still_flying": len(model.jets),                  # RENAMED from "jets_survived" -- see note below
        "total_distance_traveled": total_distance,              # FIXED -- now includes removed jets
        "total_missiles_fired": len(model.strike_log),
        "per_weapon_type_missiles_fired": dict(missiles_by_weapon_type),
        "total_jammed_agent_ticks": getattr(model, "total_jammed_agent_ticks", 0),
        "contributors_by_target": {k: sorted(v) for k, v in contributors_by_target.items()},
        "per_jet_distance_traveled": {aid: d for aid, (d, _) in all_stats.items()},               # FIXED
        "per_jet_total_payload_remaining": {aid: sum(p.values()) for aid, (_, p) in all_stats.items()},  # FIXED
    }
```

**`jets_survived` is renamed to `jets_still_flying`** — "survived" is ambiguous once landing safely is also a form of surviving; `jets_still_flying` unambiguously means "still active in the simulation right now," while `jets_returned_to_base` and `jets_destroyed` cover the other two ways a jet can end a run. **Update every call site of the old key** (`wraithnet/main.py`'s printed summary, any test asserting on `summary["jets_survived"]`) to the new three-way split — do not leave a stale `jets_survived` key for backward compatibility; this is a deliberate, clean break, matching how Phase 6 already renamed `payload_remaining` → `total_payload_remaining` without a compatibility shim.

`wraithnet/main.py`'s printed summary needs a small update to report all three buckets and the new `mission_aborted` outcome distinctly from `swarm_wiped_out` (e.g. `"OUTCOME: MISSION ABORTED — swarm returned to base before completing the objective."` alongside the existing `"OUTCOME: FAILURE — entire swarm destroyed..."` and the success path).

## Part E — Tests

### `tests/test_world.py` additions

* `_apply_sam_damage` with a nonzero `evasion_bonus` frame takes proportionally less damage than a zero-evasion frame under otherwise identical SAM exposure — a direct before/after comparison, and a boundary case where `evasion_bonus >= SAM_DAMAGE_PER_TICK` results in exactly `0` damage (never negative HP-delta, never healing).
* `all_jet_ids()` equals `_initial_jet_ids` at every point after any jet removal (destroyed, fuel-exhausted, or landed) across a full run — this is the direct regression test for the pre-existing bug this phase fixes (Part B); construct a scenario exercising all three removal paths in one run if practical, or three separate small scenarios if not.
* `mission_aborted` and `swarm_wiped_out` are never both `True` simultaneously (construct or search for a state where this could plausibly happen and assert it doesn't).
* A scenario where every jet lands safely (`jets_returned_to_base` non-empty, `model.jets` empty) before all targets are destroyed sets `mission_aborted=True`, `swarm_wiped_out=False`, `running=False`.
* A scenario where every jet is destroyed (`jets_returned_to_base` empty, `model.jets` empty) sets `swarm_wiped_out=True`, `mission_aborted=False`, exactly as before this phase.

### `tests/test_metrics.py` (new file, or extend if one already exists — check first)

* `summarize()`'s `total_distance_traveled` and `per_jet_distance_traveled` correctly include a jet that was destroyed or landed partway through the run — construct a short scenario forcing exactly this, and assert the removed jet's distance is present and correct, not silently dropped (checked against a `summarize()` call made both before and after that jet's removal tick, to prove it isn't just coincidentally still in `model.jets`).
* `jets_destroyed + jets_returned_to_base + jets_still_flying == len(initial_jet_ids)` holds for every tick of a full run across at least two different scenarios (one with an RTB, one with a SAM kill, one with neither) — this is the three-way-split's core correctness invariant.
* `grep -rn "jets_survived" wraithnet/ tests/` returns no matches — the rename (Part D) is complete everywhere, including `wraithnet/main.py`.

### Full-suite regression

* `python -m wraithnet.main` (no flags) — since the default scenario has no SAM kills or RTBs in its normal deterministic run (verify this is still true after Phases 20–22's changes; if it isn't, that's worth flagging explicitly rather than silently accepting a changed baseline), `jets_destroyed` should be `0`, `jets_returned_to_base` should be `0`, `jets_still_flying` should equal the jet count, and every other printed number should match the pre-this-phase baseline exactly.

## Explicit non-goals for this phase

* No editor/viewer changes for the new metrics fields or termination outcomes — Phase 24.
* No further termination-outcome categories beyond `solved` / `swarm_wiped_out` / `mission_aborted` — if a future scenario needs a fourth, that's a future phase's problem, not this one's to anticipate.
* No change to how `evasion_bonus` interacts with anything other than `_apply_sam_damage` — e.g. it does not reduce fuel burn, does not affect movement, does not affect firing-arc turn rate. One stat, exactly the effects the design doc §1/§6 specify, nothing implied beyond that.

## Acceptance criteria

1. `pytest` passes in full, including all new/extended tests, and the new `tests/test_metrics.py` (or extended existing file).
2. `python -m wraithnet.main` (no flags) produces the identical printed summary it did before this phase, modulo the renamed/added keys described in Part D (which should read as `0`/empty for every new bucket in this specific run, per the Full-suite regression note above).
3. A hand-crafted scenario forcing at least one jet to land safely and at least one to be destroyed in the same run produces a `summarize()` output where `total_distance_traveled` and `per_jet_distance_traveled` correctly account for both removed jets' final distances — not just the jets still in `model.jets` at the end.
4. `grep -rn "jets_survived" wraithnet/ tests/` returns no matches.
5. `model.all_jet_ids() == model._initial_jet_ids` holds at the end of every existing test in the suite that runs a full or partial simulation — add this as a shared assertion helper if convenient, rather than duplicating the check by hand in every test.
