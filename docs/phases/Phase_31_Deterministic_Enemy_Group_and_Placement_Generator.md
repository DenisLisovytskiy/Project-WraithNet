# Phase 31 — Deterministic Enemy Group & Placement Generator

> **LLM implementation prompt:** Assume Phase 30 is complete and passing. Read `docs/18_Random_Scenario_Generation_Design_Document.md` §§2–7 and 12–14. Implement deterministic home-base, objective, SAM, jammer, and group placement. Do not generate friendly loadouts beyond accepting an explicit temporary roster supplied by tests/callers; Phase 32 owns friendly-force generation and the final public orchestration API. Do not touch the GUI.

## Goal

Given validated `GenerationParameters`, a local RNG, and a concrete friendly roster (possibly empty during intermediate tests), produce deterministic enemy entities and legal positions with exact requested counts. Never hang, silently drop entities, or alter requested parameters.

## Part A — generation-time group structures

Use `GroupTemplateName` from Phase 30's generation schemas. In `wraithnet/scenario_generator.py`, add dependency-neutral internal/public types and fixed geometry constants:

```python
LAUNCH_RING_INNER_RADIUS = 2.0
LAUNCH_RING_OUTER_RADIUS = 6.0
THREAT_MEMBER_MAX_OFFSET = 24.0
DISCOVERY_MARGIN = 2.0

@dataclass(frozen=True)
class EnemyGroup:
    group_id: str
    template: GroupTemplateName
    center: tuple[float, float]
    target_ids: tuple[str, ...]
    sam_site_ids: tuple[str, ...]
    jammer_ids: tuple[str, ...]

@dataclass(frozen=True)
class EnemyGeneration:
    home_base: tuple[float, float]
    targets: tuple[TargetSpec, ...]
    sam_sites: tuple[SamSiteSpec, ...]
    jammers: tuple[JammerSpec, ...]
    groups: tuple[EnemyGroup, ...]
    cooperation_candidate_ids: tuple[str, ...]

class GenerationError(ValueError):
    code: str
    details: dict[str, object]
```

`GenerationError.__str__` must be human-readable because Phase 33 surfaces it directly in the editor. Stable codes should include at least `PLACEMENT_EXHAUSTED`, `IMPOSSIBLE_GEOMETRY`, and `COUNT_BUDGET_ERROR`.

## Part B — deterministic RNG discipline

Public entry:

```python
def generate_enemies(parameters: GenerationParameters) -> EnemyGeneration:
    rng = random.Random(derived_seed(parameters.seed, ENEMY_SALT))
    ...
```

`derived_seed` and `ENEMY_SALT` come from Phase 30. Establish stream isolation now; Phase 32 must not retrofit and change Phase 31 output. Every random helper takes `rng: random.Random` explicitly. Prohibited:

* module-global `random.choice`, `random.uniform`, etc.;
* `time.time()`, `time.monotonic()`, UUIDs, hash-randomized set iteration, or Mesa RNG;
* relying on dictionary/set iteration when it affects generated order without sorting first.

Same parameters must yield byte-equivalent Pydantic/dataclass-to-JSON output in repeated calls in the supported Python version.

## Part C — home base and launch region

Choose one map corner from a deterministic shuffled list and place home base at `(margin, margin)`-style coordinates. Keep enough room for the friendly launch ring inside bounds.

Expose helpers:

```python
def distance_from_home(...): ...
def position_in_bounds(...): ...
def position_respects_separation(...): ...
```

Tests and Phase 32 may reuse these; do not hide geometry in nested functions.

Requirements:

* no generated enemy center within `base_safety_radius`;
* normalized center distance from base lies within the resolved `objective_distance_min_fraction..objective_distance_max_fraction`;
* every SAM satisfies `distance(home_base, sam) >= SAM_ENGAGEMENT_RANGE + LAUNCH_RING_OUTER_RADIUS + parameters.launch_threat_clearance`;
* presets express easier/harder launch safety only by resolving a larger/smaller `launch_threat_clearance`; placement never branches on preset names;
* home base itself remains an ordinary `ScenarioConfig.home_base`.

## Part D — group centers

Place exactly `objective_count` primary group centers.

Style:

* `DISPERSED`: pairwise center separation is at least `1.5 × group_min_separation`.
* `CLUSTERED`: choose 1–3 deterministic macro-cluster anchors; assigned centers lie `0.5–1.0 × group_min_separation` from their anchor while still preserving the ordinary pairwise minimum.
* `MIXED`: deterministically assign roughly half of centers to each measurable rule.

Every candidate must pass:

* map margin;
* base safety;
* `group_min_separation`;
* optional threat-overlap policy.

Each center gets at least one objective. Alternate/randomize bunker and radar types while guaranteeing both appear whenever `objective_count >= 2`. HP comes from the validated range. Select and preserve the requested fraction in `cooperation_candidate_ids`, but do not raise HP based on an unknown roster here. Phase 32 jointly finalizes candidate HP and Auto-balanced payload packing. Random/custom modes leave this HP unchanged and only report actual cooperation.

### Discoverability chain

Current uncommitted agents idle when they know no target, so placement must create a rooted discovery spanning tree:

1. Every group has one canonical destroyable entity exactly at its center (primary objective, or SAM for a SAM-only leftover).
2. At least one canonical entity is within `config.SENSOR_RADIUS` from every point on the fixed launch ring; enforce `home_to_first_center + LAUNCH_RING_OUTER_RADIUS <= SENSOR_RADIUS`.
3. Each remaining group containing a destroyable entity has a previously placed parent; jammer-only leftovers do not need discovery edges.
4. Parent/child center distance is at most `config.SENSOR_RADIUS - max(spec.range for spec in WEAPON_CATALOG.values()) - DISCOVERY_MARGIN`.

Store parent group IDs in generation summaries or make them derivable/testable. If requested separation, counts, and this hop limit conflict, fail with `IMPOSSIBLE_GEOMETRY`; do not emit undiscoverable islands.

## Part E — exact threat budgets and templates

Select group templates using preset-resolved parameters and remaining exact budgets:

* `OBJECTIVE_ONLY`: objective only.
* `DEFENDED_OBJECTIVE`: consume 1 SAM.
* `JAMMED_OBJECTIVE`: consume 1 jammer.
* `IADS_CLUSTER`: consume 1 SAM + 1 jammer.
* `SAM_PAIR`: consume 2 SAMs; only when budget and higher-difficulty resolved parameters permit it.

Rules:

1. Total emitted SAMs equals `sam_count`.
2. Total emitted jammers equals `jammer_count`.
3. Select only from explicit `parameters.allowed_group_templates`; do not inspect the preset name. Templates may not overdraw a budget.
4. Remaining threats after objective groups are assigned become deterministic standalone groups.
5. A `SamSiteSpec` is not also emitted as a `TargetSpec`; `WraithNetModel` already merges SAM sites into its target state.
6. Jammers remain non-destroyable and therefore do not count toward objective HP.

Place non-canonical group members around their center using bounded angular/radial offsets no greater than `THREAT_MEMBER_MAX_OFFSET`. For threat pairs in the same group, compute `max(0, 1 - center_distance / (radius_a + radius_b))`; target the requested `threat_overlap` within a documented numeric tolerance. Inter-group pairs are excluded from this target. Never modify actual runtime radii.

## Part F — IDs and ordering

IDs:

* `Group_01`, ...
* `Target_01`, ...
* `SAM_01`, ...
* `Jammer_01`, ...

Use zero-padded monotonic numbering based on output order. Final lists are sorted by numeric ID. Do not use UUIDs or random suffixes.

## Part G — bounded search

Every candidate loop is capped by `max_placement_attempts`.

On failure:

```python
raise GenerationError(
    code="PLACEMENT_EXHAUSTED",
    details={
        "kind": ...,
        "seed": parameters.seed,
        "requested_counts": ...,
        "grid": ...,
        "active_constraints": ...,
        "suggestions": ["reduce counts", "reduce separation"],
    },
)
```

Never:

* continue forever;
* return fewer entities;
* silently reduce separation;
* generate an out-of-bounds position and rely on `ScenarioConfig` to reject it later.

A deterministic grid fallback is optional. If used, it must satisfy all active constraints and have focused tests.

## Part H — tests

Create `tests/test_scenario_generator_enemies.py`.

Required cases:

1. Same seed/parameters produces identical `EnemyGeneration`.
2. At least 10 different seeds produce more than one layout/composition.
3. Global `random.getstate()` is unchanged.
4. Every preset over at least 50 seeds: exact objective/SAM/jammer counts, unique deterministic IDs, all positions in bounds.
5. Group centers and entities satisfy the measurable style, separation, base-safety, and threat-overlap constraints within documented floating tolerances.
6. With at least two objectives, bunker and radar both appear.
7. Group templates never overdraw budgets; leftovers still produce exact totals.
8. `sam_count=0`, `jammer_count=0`, minimum map/count, and maximum documented counts.
9. Deliberately impossible geometry raises the expected structured error before the attempt budget is exceeded.
10. Monkeypatch/wrap candidate generation to prove no placement loop performs more than `max_placement_attempts`.
11. Every layout has a valid discovery spanning chain from the launch area.
12. `ScenarioConfig` constructed from generated enemies plus one legal test jet validates.

Avoid snapshotting one large exact random map as the only correctness test. Assert invariants; use exact equality only for same-seed reproducibility.

## Performance

Measure generation over maximum documented counts. Target:

* normal preset under 100 ms median;
* documented maximum under 500 ms on the development machine.

If the implementation exceeds this, improve spatial checks (for example, a simple bucket grid) before moving generation onto a background GUI thread. Do not prematurely add threads in `wraithnet/`.

## Non-goals

* Friendly loadout/count selection.
* Feasibility completion and challenge calibration.
* GUI.
* Hostile jets.
* Runtime enemy group objects.

## Acceptance criteria

1. Full `pytest` passes.
2. Seed determinism and global RNG isolation are proven.
3. Exact budgets and all geometry constraints hold over the seed matrix.
4. Impossible requests fail with actionable `GenerationError`, never partial output or a hang.
5. `wraithnet/scenario_generator.py` imports no viewer/rendering/filesystem/network module.
