# Phase 30 — Generation Schemas, Presets & Feasibility Contracts

> **LLM implementation prompt:** Assume Phases 0–29 are complete. Read `docs/18_Random_Scenario_Generation_Design_Document.md` §§0–5, 9–10, 12–14 before coding. This phase defines and tests the data contracts for deterministic generation, presets, metadata, feasibility reports, and challenge scoring. It does **not** place entities, generate a friendly roster, or change the GUI.

## Goal

Create a strict, human-readable input/output contract so later phases do not pass loose dictionaries between generator, analyzer, and editor. Preserve every existing `ScenarioConfig` call site by making generation metadata optional.

## Part A — `wraithnet/generation_schemas.py`

Add:

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
    allowed_frame_types: list[JetFrameType] = Field(default_factory=...)
    custom_roster: list[FriendlyTemplate] = Field(default_factory=list)
    friendly_reserve_factor: float = Field(default=1.25, ge=1.0, le=3.0)
    max_placement_attempts: int = Field(default=200, ge=20, le=5000)
```

Use model validators for:

* HP and friendly min/max ordering.
* Objective distance min/max ordering and a non-empty, duplicate-free group-template list.
* Non-empty, duplicate-free `allowed_frame_types`.
* Custom roster required only for `CUSTOM_ROSTER`; choose and document whether other modes reject or ignore it.
* Every template's nonnegative counts and ordnance-slot total.
* Margins/safety distances that leave some usable map area.
* A human-readable multi-error style consistent with `ScenarioConfig`.

Add:

```python
class GenerationMetadata(BaseModel):
    generator_version: int = 1
    seed: int
    preset: DifficultyPreset
    resolved_parameters: GenerationParameters
    deliberate_cooperative_objective_ids: list[str] = Field(default_factory=list)
    modified_after_generation: bool = False

class FeasibilityIssue(BaseModel):
    severity: Literal["ERROR", "WARNING", "INFO"]
    code: str
    message: str
    related_ids: list[str] = Field(default_factory=list)

class FeasibilityReport(BaseModel):
    feasible: bool
    issues: list[FeasibilityIssue] = Field(default_factory=list)
    total_damage_capacity_by_target_type: dict[TargetType, int]
    required_hp_by_target_type: dict[TargetType, int]
    unreachable_group_ids: list[str] = Field(default_factory=list)
    cooperative_objective_ids: list[str] = Field(default_factory=list)
    challenge_score: float = Field(ge=0.0, le=100.0)
    challenge_components: dict[str, float] = Field(default_factory=dict)

```

`generation_schemas.py` must not import `ScenarioConfig`; this keeps it dependency-neutral so `scenario_schema.py` can safely import `GenerationMetadata` without a cycle. Phase 32 defines `GenerationResult` in `scenario_generator.py`, where importing both schema modules is safe. Do not weaken metadata to `dict`.

## Part B — optional metadata in `ScenarioConfig`

Add:

```python
generation: GenerationMetadata | None = None
```

Requirements:

* Existing JSON without this field loads identically.
* Existing default scenario's `model_dump()` changes only by the optional `generation=None` field according to normal Pydantic serialization; update exact fixtures intentionally.
* `WraithNetModel`, bridge, trace builders, and simulation behavior ignore metadata.
* Save/load round-trips full typed metadata.

## Part C — `wraithnet/generation_presets.py`

Define a frozen preset record for `TRAINING`, `STANDARD`, `CONTESTED`, and `NIGHTMARE`, matching the ranges and intent in the design document. A preset record may contain integer ranges even though resolved `GenerationParameters` contains scalar counts.

Also define documented 64-bit seed derivation and stable salts here (or in a tiny `generation_rng.py` imported by all generator phases):

```python
PRESET_SALT = ...
ENEMY_SALT = ...
FRIENDLY_SALT = ...

def derived_seed(seed: int, salt: int) -> int: ...
```

Use integer mixing only, never Python's randomized `hash()`.

Provide:

```python
def parameters_for_preset(
    preset: DifficultyPreset,
    *,
    seed: int,
    overrides: Mapping[str, object] | None = None,
) -> GenerationParameters:
    ...
```

Rules:

* Presets expand to explicit values; generation never branches on preset names later.
* `parameters_for_preset()` uses `random.Random(derived_seed(seed, PRESET_SALT))` to draw scalar counts from each inclusive preset range. Same seed resolves the same counts; changing the seed may change counts as well as layout.
* Placement bias and template availability are also explicit resolved fields: for example Training resolves a middle-distance band and excludes `SAM_PAIR`, while higher presets may include it.
* Overrides are validated by `GenerationParameters`.
* Any override changes the returned `preset` to `"CUSTOM"` while retaining all resolved values.
* The input preset template is never mutated.
* No preset modifies runtime config/catalog constants.

## Part D — feasibility and score contract stubs

Create `wraithnet/scenario_feasibility.py` with public signatures:

```python
def analyze_feasibility(
    scenario: ScenarioConfig,
    *,
    group_ids_by_entity: Mapping[str, str] | None = None,
) -> FeasibilityReport:
    ...

def compute_challenge_components(
    scenario: ScenarioConfig,
    report_inputs: ...,
) -> dict[str, float]:
    ...
```

Implement catalog-driven checks that do not depend on random placement:

* required HP by target type, including SAM sites;
* joint integer allocation of finite weapon units across all destroyable targets using positive suitability and `damage_per_hit`; one weapon unit cannot count toward multiple target categories, and allocation eligibility includes its carrier's approximate round-trip fuel reach;
* no-suitable-weapon errors;
* insufficient-total-damage errors;
* empty/zero-damage loadout warnings;
* approximate round-trip fuel reach from home base to each target/group and home;
* conservative fog-of-war discovery-chain validation with stable `DISCOVERY_CHAIN_GAP` issues;
* challenge score clamped to `[0, 100]`, with named components.

Document limitations: static analysis does not guarantee CNP victory and does not simulate attrition, jamming, arc turn time, or assignment.

## Part E — tests

Create:

* `tests/test_generation_schemas.py`
* `tests/test_generation_presets.py`
* `tests/test_scenario_feasibility.py`

Cover:

1. Every boundary and cross-field validation rule.
2. Slot-cost validation for every catalog weapon, including AMRAAM/GUN_POD.
3. Generation-parameter, metadata, and feasibility-report JSON round-trips.
4. Existing scenario JSON loads with `generation is None`.
5. Every preset resolves to valid parameters with expected relative ordering: threat/objective burden increases and reserve factor decreases from Training to Nightmare.
6. Overrides do not mutate templates and set preset to Custom.
7. Feasible and infeasible handcrafted scenarios with exact issue codes.
8. Fuel reach and discovery-chain warning/error boundaries.
9. Score and every component remain in bounds.
10. Importing these modules changes neither global `random` state nor catalog contents.

Issue codes must be stable constants (for example `NO_SUITABLE_WEAPON`, `INSUFFICIENT_DAMAGE`, `FUEL_REACH`, `DISCOVERY_CHAIN_GAP`, `EMPTY_LOADOUT`) so GUI/tests never parse human prose.

## Non-goals

* No random placement.
* No GUI.
* No generator CLI.
* No simulation runs to compute difficulty.
* No hostile-aircraft schema.

## Acceptance criteria

1. Full `pytest` passes.
2. Existing saved scenarios remain loadable and behaviorally identical.
3. Preset resolution and feasibility analysis are deterministic and rendering-free.
4. `grep -rn "panda3d\\|pygame\\|from direct" wraithnet/generation_*.py wraithnet/scenario_feasibility.py` finds no imports.
5. No untyped metadata/request dictionaries are used at public boundaries.
