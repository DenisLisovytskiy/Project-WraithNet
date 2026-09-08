# Design Document: Deterministic Random Scenario Generation

> This document is the source of truth for WraithNet's random-scenario expansion. It adds a few-click generator to the existing 3D scenario editor without weakening or replacing the existing manual editor. A generated scenario is an ordinary, fully materialized `ScenarioConfig`: users can immediately inspect it, move entities, change loadouts, save it, load it, preview it, and launch it through the same workflow that already exists. Nothing in this document is implemented yet. See Phases 30–33 for LLM-ready implementation prompts.

## 0. Goal

Let a user produce useful scenarios in seconds:

1. Open WraithNet.
2. Choose a difficulty preset.
3. Optionally adjust a few understandable parameters.
4. Choose automatic friendly forces or a custom roster.
5. Click **Generate**.
6. Preview, edit manually if desired, save, and launch normally.

Generation must be deterministic from a seed, bounded (never hang while searching for positions), schema-valid, and—unless the user explicitly chooses an experimental mode—pass a transparent static feasibility check for weapon coverage, payload capacity, and approximate fuel-to-objective-and-home reach.

## 1. Existing capabilities this expansion builds on

* `ScenarioConfig` already represents the complete concrete scenario.
* The editor already supports manual placement and editing of jets, targets, SAM sites, jammers, and a home base.
* `JET_FRAME_CATALOG` and `WEAPON_CATALOG` are fixed, validated catalogs.
* The simulation already supports fog of war, CNP negotiation, heterogeneous weapons, SAM threats, jamming, fuel, and RTB.
* Save, load, preview, launch, live 3D viewing, and frozen executable paths already exist.

The generator must call these existing contracts rather than introducing a parallel scenario format or a second simulation path.

## 2. Scope clarification: what “enemies” means today

WraithNet currently has three opposing entity categories:

* **Objectives**: `TargetSpec`, either `HARDENED_BUNKER` or `MOBILE_RADAR`.
* **SAM sites**: `SamSiteSpec`; active threats and destroyable mission targets.
* **EW jammers**: `JammerSpec`; active communication hazards, currently not destroyable.

There is no hostile aircraft agent, dogfighting AI, moving enemy formation, or enemy loadout system. This expansion therefore randomizes groups made from the enemy types the simulation actually implements. It must not generate “enemy jets” that are only decorative or pretend the engine can simulate behavior it does not have. Hostile aircraft would require a separate future design.

## 3. Core decisions

| Concern | Decision |
|---|---|
| Randomness | Every generation uses named local `random.Random(derived_seed)` streams for preset, enemy, and friendly decisions. Never call module-global `random.*`, Mesa RNG, wall-clock time, or `secrets` inside the deterministic generator. The GUI may use `secrets.randbits(64)` only to create a new root seed before calling the generator. |
| Output | A normal `ScenarioConfig`, not a lazy/procedural scenario. The simulator remains completely unaware that generation occurred. |
| Presets | Presets expand into explicit parameters before generation. Difficulty contains no hidden runtime buffs or modified global constants. |
| Overrides | A user may choose a preset and override any advanced parameter. The resolved values are shown in the GUI. |
| Map size | Generation uses the current `config.GRID_WIDTH × config.GRID_HEIGHT`. Random map dimensions are deferred until bid distance normalization is scenario-aware instead of using fixed `MAX_GRID_DISTANCE`. |
| Enemy organization | Enemies are produced as spatial **groups** centered on an objective, with optional SAM/jammer escorts selected from group templates. |
| Friendly modes | `AUTO_BALANCED`, `RANDOM_LEGAL`, and `CUSTOM_ROSTER`. Auto-balanced is the default. |
| Solvability | Static feasibility is guaranteed for `AUTO_BALANCED`, reported but not guaranteed for `RANDOM_LEGAL`, and validated/reported for `CUSTOM_ROSTER`. No generator may claim that static feasibility guarantees the CNP agents will win dynamically. |
| Manual editing | Generation replaces the current draft only after successful generation and confirmation when unsaved changes exist. Thereafter the normal editor owns the concrete scenario. |
| Failure | Bounded placement attempts followed by a structured `GenerationError`; never an unbounded retry loop and never silently reducing requested counts. |
| Persistence | Saved scenario JSON includes the concrete entities plus optional generation metadata. Manual edits mark metadata as modified; loading never regenerates implicitly. |

## 4. Generation schemas

Create `wraithnet/generation_schemas.py`, containing only Pydantic/stdlib imports plus catalog literal types from `wraithnet.config`.

```python
DifficultyPreset = Literal["TRAINING", "STANDARD", "CONTESTED", "NIGHTMARE", "CUSTOM"]
FriendlyMode = Literal["AUTO_BALANCED", "RANDOM_LEGAL", "CUSTOM_ROSTER"]
GroupStyle = Literal["DISPERSED", "CLUSTERED", "MIXED"]
GroupTemplateName = Literal[
    "OBJECTIVE_ONLY", "DEFENDED_OBJECTIVE", "JAMMED_OBJECTIVE",
    "IADS_CLUSTER", "SAM_PAIR",
]

class FriendlyTemplate(BaseModel):
    frame_type: JetFrameType
    payload: dict[WeaponType, int]
    count: int = Field(ge=1, le=20)

class GenerationParameters(BaseModel):
    seed: int = Field(ge=0, le=2**64 - 1)
    preset: DifficultyPreset = "STANDARD"

    objective_count: int = Field(default=4, ge=1, le=20)
    sam_count: int = Field(default=2, ge=0, le=12)
    jammer_count: int = Field(default=1, ge=0, le=8)
    objective_hp_min: int = Field(default=1, ge=1, le=10)
    objective_hp_max: int = Field(default=4, ge=1, le=12)
    cooperation_objective_fraction: float = Field(default=0.25, ge=0.0, le=1.0)
    group_style: GroupStyle = "MIXED"
    allowed_group_templates: list[GroupTemplateName] = Field(default_factory=...)
    objective_distance_min_fraction: float = Field(default=0.20, ge=0.0, le=1.0)
    objective_distance_max_fraction: float = Field(default=0.85, ge=0.0, le=1.0)

    map_margin: float = Field(default=5.0, ge=1.0)
    base_safety_radius: float = Field(default=18.0, ge=5.0)
    group_min_separation: float = Field(default=16.0, ge=3.0)
    entity_min_separation: float = Field(default=3.0, ge=0.5)
    threat_overlap: float = Field(default=0.35, ge=0.0, le=1.0)
    launch_threat_clearance: float = Field(default=5.0, ge=0.0, le=30.0)

    friendly_mode: FriendlyMode = "AUTO_BALANCED"
    friendly_count_min: int = Field(default=3, ge=1, le=20)
    friendly_count_max: int = Field(default=6, ge=1, le=20)
    allowed_frame_types: list[JetFrameType] = Field(
        default_factory=lambda: list(JET_FRAME_CATALOG)
    )
    custom_roster: list[FriendlyTemplate] = Field(default_factory=list)
    friendly_reserve_factor: float = Field(default=1.25, ge=1.0, le=3.0)

    max_placement_attempts: int = Field(default=200, ge=20, le=5000)
```

Validation rules:

* Every `*_min <= *_max`.
* `allowed_group_templates` is non-empty and duplicate-free; generation never branches on a preset name to decide whether `SAM_PAIR` is allowed.
* Counts are geometrically plausible for the fixed `config.GRID_WIDTH × config.GRID_HEIGHT` map and requested margins/separation. Do not promise exact packing in validation; reject only obvious impossibilities and let bounded placement return a detailed failure for the rest.
* `CUSTOM_ROSTER` requires at least one template; other modes reject or ignore a non-empty roster consistently (choose one and test it).
* `allowed_frame_types` must be non-empty for generated-friendly modes.
* Every friendly template must pass the same slot-cost arithmetic as `ScenarioConfig`.
* `sam_count + objective_count >= 1`.
* Jammers are not counted as destroyable objectives.

### 4.1 Optional scenario metadata

Add a small optional field to `ScenarioConfig`:

```python
class GenerationMetadata(BaseModel):
    generator_version: int = 1
    seed: int
    preset: DifficultyPreset
    resolved_parameters: GenerationParameters
    deliberate_cooperative_objective_ids: list[str] = Field(default_factory=list)
    modified_after_generation: bool = False

class ScenarioConfig(BaseModel):
    ...
    generation: GenerationMetadata | None = None
```

This metadata is informational and reproducibility-oriented. The simulation must ignore it. The generated entity lists remain authoritative. Once a user manually changes an entity, the editor sets `modified_after_generation=True`; it does not regenerate or discard the metadata.

## 5. Difficulty presets

Create `wraithnet/generation_presets.py` with immutable preset records. Preset records may contain count ranges; `parameters_for_preset()` uses a named preset RNG stream derived from the root seed to resolve each range to scalar values in `GenerationParameters`. The seed therefore controls both counts and layout. Presets only choose generator parameters; they never alter `config.SAM_DAMAGE_PER_TICK`, sensor range, weapon stats, airframe stats, or agent intelligence.

Suggested starting presets:

### TRAINING

* 2 objectives
* 0 SAMs
* 0–1 jammer
* objective HP 1–2
* dispersed groups
* 3–4 auto-balanced jets
* reserve factor 1.6
* resolved objective distance fractions of approximately 0.25–0.65 from base

Purpose: quick demonstration of negotiation, sensors, and heterogeneous target types with little threat pressure.

### STANDARD

* 3–4 objectives
* 1–2 SAMs
* 1 jammer
* objective HP 1–4
* mixed grouping
* 3–6 auto-balanced jets
* reserve factor 1.35
* approximately 25% of objectives deliberately require multi-jet payload cooperation

Purpose: the default experience and closest relative of the fixed PoC scenario.

### CONTESTED

* 5–7 objectives
* 2–4 SAMs
* 1–2 jammers
* objective HP 2–5
* clustered/mixed groups with more overlapping threat rings
* 4–8 auto-balanced jets
* reserve factor 1.2
* approximately 40% cooperative objectives

Purpose: sustained reassignment, threat avoidance pressure, and fuel/RTB risk.

### NIGHTMARE

* 7–10 objectives
* 4–6 SAMs
* 2–4 jammers
* objective HP 3–7
* clustered groups and high threat overlap
* 5–10 auto-balanced jets
* reserve factor 1.05
* approximately 60% cooperative objectives

Purpose: a statically feasible but low-margin scenario. It is not guaranteed to be dynamically winnable.

The concrete defaults must be calibrated by deterministic batch tests in Phase 32. If observed completion rates show these numbers are mislabeled, change the preset numbers—not simulation mechanics.

## 6. Enemy group model

An enemy group is a generation-time concept only. It is flattened into existing `targets`, `sam_sites`, and `jammers` lists before returning `ScenarioConfig`.

Templates:

* `OBJECTIVE_ONLY`: one bunker or radar.
* `DEFENDED_OBJECTIVE`: one objective plus one nearby SAM.
* `JAMMED_OBJECTIVE`: one objective plus one nearby jammer.
* `IADS_CLUSTER`: one objective, one SAM, and one jammer, with intentional but bounded ring overlap.
* `SAM_PAIR`: one objective plus two separated SAMs; available only to higher presets.

The requested global `sam_count` and `jammer_count` are exact. Template selection must consume from those budgets. Any threats left after group construction are placed as standalone groups while still respecting constraints. IDs are deterministic and human-readable: `Target_01`, `SAM_01`, `Jammer_01`, `Jet_01`.

## 7. Placement algorithm

Create `wraithnet/scenario_generator.py`. It must be pure simulation-core code: no Panda3D, DirectGUI, Pygame, filesystem, sockets, or wall clock.

High-level sequence:

1. Resolve preset plus overrides into one `GenerationParameters`.
2. Derive stable preset/enemy/friendly sub-seeds from the root seed using documented 64-bit integer mixing (never Python's randomized `hash()`), then construct local RNG streams.
3. Choose or preserve the home base.
4. Place group centers.
5. Materialize objective/SAM/jammer members around each center.
6. Generate or materialize friendly roster.
7. Place jets in a launch area around the base and aim initial headings toward the map center.
8. Build `ScenarioConfig` (the existing schema remains the final validator).
9. Run `analyze_feasibility()`.
10. Return `GenerationResult`.

### 7.1 Constraints

* All positions are in bounds with `map_margin`.
* No enemy group center lies within `base_safety_radius` of home base.
* `DISPERSED` group centers have pairwise separation of at least `1.5 × group_min_separation`.
* `CLUSTERED` centers belong to 1–3 macro-cluster anchors and lie between `0.5 ×` and `1.0 × group_min_separation` from their anchor while still respecting the ordinary pairwise minimum.
* `MIXED` deterministically assigns roughly half of centers to each rule.
* Each group has one canonical destroyable entity exactly at its center (primary objective, or SAM for a SAM-only leftover). Other members respect `entity_min_separation` and have offsets no greater than `THREAT_MEMBER_MAX_OFFSET = 24.0`.
* Friendly jets spawn between `LAUNCH_RING_INNER_RADIUS = 2.0` and `LAUNCH_RING_OUTER_RADIUS = 6.0` from home base, in bounds and outside immediate SAM engagement.
* Every SAM ring edge remains at least resolved `launch_threat_clearance` beyond the launch ring; presets reduce this explicit margin as difficulty rises.
* For two threat rings, overlap ratio is `max(0, 1 - center_distance / (radius_a + radius_b))`. `threat_overlap` is the target mean overlap ratio for threat pairs within the same group, accepted within a documented tolerance. Inter-group pairs need not match it. This changes placement only; runtime radii remain unchanged.
* No entity is placed directly on home base.

### 7.2 Discoverability under current fog of war

Current uncommitted jets idle when no target is known; there is no search-pattern behavior. Generation must therefore build a conservative discovery chain instead of scattering objectives into disconnected sensor islands:

* at least one destroyable entity is within direct sensor range from every point on the launch ring;
* every other group containing a destroyable entity has a parent in a rooted spanning tree;
* parent-to-child canonical-entity/center distance is no greater than `SENSOR_RADIUS - max_weapon_range - DISCOVERY_MARGIN`.

This ensures a jet approaching a known parent can directly detect its child even if it stops at the longest weapon's firing range. `analyze_feasibility()` reports `DISCOVERY_CHAIN_GAP` when a concrete scenario has no such chain. This remains a conservative static check, not a victory guarantee.

### 7.3 Bounded search

Each placement uses at most `max_placement_attempts`. On exhaustion:

* Do not silently drop entities.
* Do not relax constraints invisibly.
* Raise `GenerationError` containing the entity/group kind, requested counts, fixed map dimensions, active separations, seed, and practical suggestions such as “reduce enemy count or group separation.”

Optionally, a deterministic grid fallback may be used, but it must obey the same constraints and be covered by tests. It must not turn failure into an unreported parameter change.

## 8. Friendly-force generation

### 8.1 `AUTO_BALANCED` (default)

Generate a legal roster after enemies are known:

1. Compute per-objective destroyable demand, including SAM sites.
2. Select candidate airframes with approximate round-trip reach to the demands.
3. Pack initial legal payloads using only positively suitable weapons and `damage_per_hit`, including the reserve factor.
4. Jointly assign each carrier-owned weapon unit to at most one reachable objective.
5. Deterministically repair payload/frame selection within count bounds until allocation succeeds or return a structured capacity error.
6. Ensure every objective—not merely every target type—has jointly allocatable damage.
7. Prefer mixed airframes; do not always emit one optimal frame.
8. Keep jet count within user bounds. If demand cannot fit under `friendly_count_max`, return a structured feasibility/generation error instead of emitting an impossible “balanced” roster.
9. Check approximate round-trip fuel reach for at least one suitable jet per enemy group.

The packing algorithm must use deterministic tie-breakers. Same seed and parameters must produce byte-equivalent `ScenarioConfig.model_dump(mode="json")`.

Phase 31 records explicit `cooperation_candidate_ids`. In Auto-balanced mode, the packer chooses final HP and payload together: candidate HP is raised only when at least two generated jets can jointly satisfy it, and each participating jet's eligible contribution is constrained below that HP. Random/custom modes do not rewrite enemy HP after seeing the roster; the analyzer merely reports which objectives happen to require cooperation. This avoids generating a provisional roster, raising HP afterward, and invalidating capacity.

### 8.2 `RANDOM_LEGAL`

Randomize count, frame types, and loadouts while always respecting catalog names, nonnegative counts, and slot capacity. Run feasibility analysis and show warnings, but allow generation even if objective coverage or approximate fuel reach fails. The GUI labels this mode **Experimental / may be unwinnable**, not simply “Random.”

### 8.3 `CUSTOM_ROSTER`

The user chooses counts, frame types, and complete loadouts in the generator panel. The generator:

* expands templates into concrete jets;
* randomizes only positions and headings near home base;
* never changes the selected frame or payload;
* runs feasibility analysis;
* blocks only schema-invalid rosters;
* requires explicit confirmation to accept a valid-but-statically-infeasible roster.

The existing deep manual editor remains available after generation for per-jet changes.

## 9. Static feasibility analysis

Create `wraithnet/scenario_feasibility.py`.

```python
class FeasibilityIssue(BaseModel):
    severity: Literal["ERROR", "WARNING", "INFO"]
    code: str
    message: str
    related_ids: list[str] = []

class FeasibilityReport(BaseModel):
    feasible: bool
    issues: list[FeasibilityIssue]
    total_damage_capacity_by_target_type: dict[TargetType, int]
    required_hp_by_target_type: dict[TargetType, int]
    unreachable_group_ids: list[str]
    cooperative_objective_ids: list[str]
```

Checks:

* `ScenarioConfig` validity.
* Joint integer ammunition allocation across all destroyable targets. A weapon unit may be assigned to only one target, and its carrier must have approximate round-trip fuel reach to that target; the same JDAM cannot independently satisfy multiple demands or pass a separate reach check on another jet.
* At least one suitable weapon for every destroyable entity.
* Per-frame slot capacity (also enforced by `ScenarioConfig`; report friendly text here).
* Approximate fuel reach: straight-line base → group → base using frame speed/burn and RTB safety margin.
* Empty or zero-damage loadouts.
* Threat concentration near the launch area.
* Cooperative objectives are actually cooperative: their HP exceeds the largest single suitable jet contribution but does not exceed total suitable contribution.

This is an analyzer, not a solver. It must say “statically feasible,” never “guaranteed winnable.” It does not simulate CNP, SAM attrition, jamming message loss, firing-arc turn time, or dynamic assignment.

## 10. Difficulty score

The GUI displays an explainable estimated challenge score from 0–100, derived from the concrete generated scenario:

* objective HP/damage demand;
* number of objectives;
* SAM coverage near routes/objectives;
* jammer coverage near routes/objectives;
* objective distance from base;
* friendly damage reserve;
* friendly fuel margin;
* number of deliberately cooperative objectives.

Keep each component in the report so the score is explainable. Do not use the score as a simulation mechanic and do not claim equal scores imply equal win probability.

## 11. Editor workflow

Add a collapsible **Quick Generate** panel without removing or hiding the current manual controls.

Compact mode:

* Difficulty: Training / Standard / Contested / Nightmare
* Seed field plus 🎲/New Seed button
* Friendly forces: Auto-balanced / Experimental random / Custom roster
* **Generate**

Advanced mode:

* objective, SAM, and jammer counts;
* HP range;
* group style and threat overlap;
* cooperation fraction;
* friendly min/max or custom roster rows;
* placement distances;
* reserve factor.

Workflow:

1. Selecting a preset fills explicit fields.
2. Editing a field changes the preset label to `CUSTOM`.
3. Generate runs synchronously only if bounded generation is fast; otherwise run it off the Panda3D task thread and return through a queue. Target budget: under 100 ms for normal presets, under 500 ms at documented maximum counts.
4. On success, show the generated scene immediately and a concise report: seed, challenge score, entity counts, feasible/warnings.
5. Keep one-level **Undo Generation** restoring the exact previous draft and selection.
6. Manual editing continues normally. Any manual change marks generation metadata modified.
7. Generate with unsaved/manual changes asks for confirmation before replacing the draft.
8. Save/Load/Preview/Launch remain the existing buttons and code paths.

The generator must not automatically launch. The user sees and can edit the result first.

## 12. Testing strategy

### Pure Layer 1

* Same seed + resolved parameters → identical JSON.
* Different seeds produce at least one positional/composition difference over a sample, without brittle exact-random-number assertions.
* Global `random` state is unchanged.
* Every preset across a seed matrix generates in bounds, with unique IDs and exact requested counts.
* Constraints and bounded-failure errors.
* Group-budget consumption.
* All friendly modes and slot limits.
* Feasibility issue codes and difficulty-score components.
* Metadata round-trip.

### Headless Layer 2

* Quick Generate controls invoke the generator and replace the scene/draft.
* Preset fields, custom roster rows, seed controls, warning banner, confirmation, and undo.
* Generated draft can be manually edited afterward.
* Generated scenario saves, loads, previews, and reaches the existing Launch callback.
* No pixel assertions.

### Batch calibration

For each preset, run at least 100 deterministic seeds through static generation tests. For a smaller documented sample, run the actual simulation with shortened CNP timing and record completion/abort/wipeout rates. These rates calibrate labels; they are not tests that every random scenario must be won.

## 13. Packaging and isolation

* Generator modules are pure Python and automatically included by PyInstaller analysis through imports.
* No new dependency.
* Frozen and source GUI use identical generation code.
* Saving still uses `user_data_dir("scenarios")`.
* `wraithnet/` continues to import no viewer or rendering package.

## 14. Explicit non-goals

* Hostile aircraft or dogfight AI.
* Procedural terrain, weather, or moving ground forces.
* Modifying weapon/airframe catalogs through the generator.
* Guaranteed dynamic victory.
* A genetic/optimization algorithm that repeatedly runs simulations to find a “perfectly balanced” map.
* Replacing the manual editor.
* Networked scenario sharing or an online seed leaderboard.

## 15. Implementation phases

* **Phase 30 — Generation Schemas, Presets & Feasibility Contracts**: parameter/metadata/report schemas, immutable presets, validation, and challenge-score contract.
* **Phase 31 — Deterministic Enemy Group & Placement Generator**: seeded placement, group templates, exact budgets, IDs, bounded failure, home base and objective/threat generation.
* **Phase 32 — Friendly Roster Generation & End-to-End Feasibility**: auto-balanced, random-legal, and custom rosters; fuel/damage checks; complete `GenerationResult`; seed-matrix calibration.
* **Phase 33 — Quick Generate GUI & Manual-Editor Handoff**: compact/advanced panel, custom roster controls, generate/undo/confirmation/report, metadata dirtying, save/load/preview/launch integration, and packaged smoke verification.
