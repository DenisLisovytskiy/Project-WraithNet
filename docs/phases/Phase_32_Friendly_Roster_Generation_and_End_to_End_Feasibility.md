# Phase 32 — Friendly Roster Generation & End-to-End Feasibility

> **LLM implementation prompt:** Assume Phases 30–31 are complete. Read `docs/18_Random_Scenario_Generation_Design_Document.md` §§8–10 and 12–14. Implement all three friendly modes, complete static feasibility analysis, challenge scoring, generation metadata, and the public `generate_scenario()` API. Calibrate presets with deterministic seed matrices. Do not touch Panda3D/editor code; Phase 33 owns GUI integration.

## Goal

Turn Phase 31's enemy layout into a complete, concrete `ScenarioConfig`. The default `AUTO_BALANCED` mode must be schema-valid and statically feasible by construction. `RANDOM_LEGAL` and `CUSTOM_ROSTER` may be statically infeasible, but the result must say so clearly and never mislabel them as guaranteed winnable.

## Part A — use Phase 30's stable RNG streams

Enemy composition must not change merely because friendly-generation internals consume one extra random number later.

Phase 30 defined stable seed derivation and Phase 31 already uses the enemy-derived stream. Friendly generation must use `random.Random(derived_seed(seed, FRIENDLY_SALT))`; do not redefine or retrofit enemy seeding here. Tests must prove changing friendly mode does not move enemies for otherwise-identical parameters.

## Part B — friendly roster types and helpers

Add to `wraithnet/scenario_generator.py`:

```python
class GeneratedGroupSummary(BaseModel):
    group_id: str
    template: GroupTemplateName
    center: tuple[float, float]
    entity_ids: list[str]
    discovery_parent_group_id: str | None = None

class GenerationResult(BaseModel):
    scenario: ScenarioConfig
    parameters: GenerationParameters
    feasibility: FeasibilityReport
    groups: list[GeneratedGroupSummary]

def generate_scenario(parameters: GenerationParameters) -> GenerationResult:
    ...

def generate_friendly_roster(
    parameters: GenerationParameters,
    enemies: EnemyGeneration,
    rng: random.Random,
) -> list[JetSpec]:
    ...
```

Internal helpers should expose/test:

* weapon eligible damage against a target type;
* payload slot use;
* frame fuel range under the existing flat-burn model;
* deterministic payload packing;
* launch-ring position generation;
* initial heading toward map center.

## Part C — `AUTO_BALANCED`

Algorithm:

1. Build per-objective HP demand for all `TargetSpec` and `SamSiteSpec`, including reserve demand from `friendly_reserve_factor`.
2. Select a candidate varied set of allowed airframes within `friendly_count_min..friendly_count_max`, considering which frames have approximate round-trip reach to which objectives.
3. Pack an initial payload into those concrete candidate carriers without exceeding each frame's `ordnance_slots`.
4. Run joint allocation: suitability `<= 0` is ineligible, positive suitability does not scale damage, each carrier-owned unit may be assigned once, and only to an objective that carrier can reach and return from.
5. Deterministically repair failed allocation by swapping payload units or adding another allowed frame up to `friendly_count_max`; bound repair attempts and fail with `AUTO_BALANCE_CAPACITY` if no valid allocation exists.
6. Guarantee positive jointly allocatable damage for every objective, not merely every target type.
7. During selection/packing, prefer tactically sensible catalog choices with deterministic weighted tie-breaks:
   * bunker: JDAM primary, GUN_POD possible;
   * radar: AIM9X/AMRAAM;
   * SAM: AMRAAM/JDAM, avoiding a strict all-AMRAAM monoculture;
   * preserve at least two weapon types when scenario demand permits.
8. The accepted joint allocation must supply every objective from jets that can approximately reach it and return under frame capacity, burn rate, and `RTB_FUEL_SAFETY_MARGIN`; do not substitute an independent “some reachable jet exists” check.
9. If demand cannot fit within max jet count/allowed frames, raise `GenerationError("AUTO_BALANCE_CAPACITY", ...)` with required versus available slots/damage and suggestions.

Do not optimize by running the simulation. This is deterministic bin packing with small bounded inputs. Use a clear greedy algorithm and stable tie-breakers rather than an opaque optimizer.

### Cooperative objectives

Phase 31 preserved explicit `cooperation_candidate_ids`. Finalize Auto-balanced candidates and payload packing together:

1. Choose candidate HP within configured bounds.
2. Pack eligible damage across at least two jets while constraining every single jet's contribution to that candidate below its HP.
3. Jointly allocate the remaining finite ammunition across all other objectives.
4. If a candidate cannot satisfy these constraints within roster/count/HP bounds, leave it ordinary and emit an `INFO`; never falsely label it cooperative.

Random/custom modes do not alter enemy HP after seeing their roster. The analyzer only reports whether an objective happens to meet the cooperation inequality.

This is the generated equivalent of the original fixed PoC's deliberate `Target_A.hp_required=5` invariant.

## Part D — `RANDOM_LEGAL`

* Draw count within user bounds.
* Draw only from `allowed_frame_types`.
* Generate nonnegative payloads that always fit slots.
* Avoid all-empty roster unless explicitly permitted (default: every jet carries at least one weapon).
* Randomize legal composition; do not repair missing target coverage.
* Analyze and return warnings/errors without rejecting a schema-valid result.
* Metadata/panel language must call this **Experimental / may be unwinnable**.

Tests must find at least one feasible and one infeasible result across a controlled seed sample, proving the analyzer is active rather than the mode accidentally auto-balancing.

## Part E — `CUSTOM_ROSTER`

Expand each `FriendlyTemplate.count` into deterministic IDs and exact payload copies.

Requirements:

* Never change frame or payload.
* Validate slots through both generation schema and final `ScenarioConfig`.
* Place jets around home base with unique, in-bounds positions.
* Initial heading points toward map center.
* Report feasibility; do not reject valid-but-infeasible rosters in the core API.
* The result must let Phase 33 require explicit confirmation before replacing the draft with an infeasible custom scenario.

## Part F — launch-area safety

Place friendlies inside a bounded ring around home base:

* in map bounds;
* separated from each other;
* outside all SAM engagement rings at tick 0;
* outside jammer rings when possible; if impossible in experimental/custom mode, report `LAUNCH_AREA_THREATENED`;
* never exactly on home base.

If the requested friendly count cannot fit, raise a bounded structured error rather than stacking jets at one point.

## Part G — complete feasibility analyzer

Finish `analyze_feasibility()`:

* joint integer allocation of each carrier-owned weapon unit to at most one reachable destroyable objective, plus summarized HP/capacity by target type for display;
* no-suitable-weapon and insufficient-damage errors;
* approximate per-group round-trip fuel reach;
* conservative rooted discovery-chain validation from the launch area (`DISCOVERY_CHAIN_GAP`);
* launch-area threat warning;
* true cooperative-objective checks;
* slot/loadout issues with related jet IDs;
* no claim of guaranteed victory.

`feasible` is `False` iff at least one `ERROR` exists. Warnings do not flip it.

Populate `FeasibilityReport.cooperative_objective_ids` from the concrete scenario and roster using the actual inequality. `GenerationMetadata.deliberate_cooperative_objective_ids` separately records which Auto-balanced candidates the generator deliberately realized. The challenge component uses the concrete report; after manual edits the deliberate metadata remains provenance, not assumed truth.

Challenge components must be named and individually bounded, for example:

* `objective_burden`
* `sam_pressure`
* `jamming_pressure`
* `route_distance`
* `cooperation_pressure`
* `friendly_reserve_penalty`
* `fuel_margin_penalty`

The final score is a documented weighted sum clamped to `[0, 100]`.

## Part H — metadata

Attach:

```python
GenerationMetadata(
    generator_version=1,
    seed=parameters.seed,
    preset=parameters.preset,
    resolved_parameters=parameters,
    deliberate_cooperative_objective_ids=realized_cooperative_ids,
    modified_after_generation=False,
)
```

The concrete entities remain authoritative. Generating twice from metadata's resolved parameters must reproduce the original scenario before manual edits.

## Part I — tests

Create:

* `tests/test_scenario_generator_friendlies.py`
* `tests/test_scenario_generation_integration.py`
* extend `tests/test_scenario_feasibility.py`

Required coverage:

1. Same seed/parameters → identical full scenario JSON/report.
2. Enemy layout unchanged across friendly modes.
3. Auto-balanced scenarios pass feasibility over at least 100 seeds per preset.
4. Every auto roster stays within count and slot bounds and has a valid joint ammunition allocation across every generated destroyable entity.
5. Auto capacity failure is bounded/actionable.
6. Cooperation-marked objectives satisfy the true single-jet versus total-roster inequality.
7. Random-legal always validates but can produce feasibility warnings/errors.
8. Custom roster preserves every frame, payload key (including AMRAAM/GUN_POD), and count exactly.
9. Friendly positions/headings/launch safety.
10. Fuel reach boundary cases for each frame.
11. Metadata regeneration and save/load round-trip.
12. `GenerationResult.scenario` runs through `WraithNetBridge` without generator-specific branching.

### Dynamic calibration sample

With CNP timing monkeypatched to the existing fast-test values, run a documented sample (for example 20 seeds per preset) through the real bridge. Record solved/mission-aborted/swarm-wiped-out counts in a test comment or `docs/18_...md` calibration appendix.

Do not assert every scenario wins. Assert broad ordering/guardrails after observing real values, such as Training not having a lower completion rate than Nightmare. If results contradict preset labels, tune preset generation values—not runtime mechanics and not a widened meaningless assertion.

## Performance

Benchmark complete generation and analysis. Keep ordinary presets under 100 ms median and maximum documented input under 500 ms on the development machine. Add a generous regression ceiling that catches accidental exponential behavior without being flaky in CI.

## Non-goals

* GUI.
* Runtime adaptive difficulty.
* Simulation-based optimization.
* Guaranteed victory.
* New weapons, airframes, or enemy types.

## Acceptance criteria

1. Full `pytest` passes.
2. Auto-balanced output is valid and statically feasible over the full seed matrix.
3. Random/custom modes preserve their intended freedom and clearly report infeasibility.
4. Same seed and resolved parameters reproduce identical output; friendly choices do not perturb enemy layout.
5. Generated scenarios run through the unchanged bridge/world APIs.
