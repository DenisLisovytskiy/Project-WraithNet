# Phase 35 — Procedural Terrain & Terrain-Aware Scenario Generation

> **LLM implementation prompt:** Assume Phase 34 and the Phase 30–33 random-scenario generator are complete. Read `docs/19_Terrain_Naval_and_Combined_Arms_Design_Document.md` §§4, 15–17. Add deterministic terrain generation and make random scenario placement/feasibility terrain-aware. Do not implement editor painting/rendering or naval/tank units yet.

## Deliverables

### A. Generation schemas

Add:

```python
class TerrainGenerationParameters(BaseModel):
    water_fraction: float = Field(default=0.20, ge=0.0, le=0.75)
    forest_fraction: float = Field(default=0.20, ge=0.0, le=0.80)
    city_fraction: float = Field(default=0.10, ge=0.0, le=0.50)
    smoothing_passes: int = Field(default=3, ge=0, le=8)
    city_cluster_radius: int = Field(default=2, ge=1, le=6)
    building_height_min: float = Field(default=4.0, ge=4.0, le=25.0)
    building_height_max: float = Field(default=18.0, ge=4.0, le=25.0)
    minimum_land_component_cells: int = Field(default=40, ge=1)
    minimum_water_component_cells: int = Field(default=0, ge=0)
```

Embed this typed model in `GenerationParameters`. Presets resolve explicit terrain values. Use a new named `TERRAIN_SALT`; changing friendly mode/loadouts must not alter terrain.

`water_fraction` is a fraction of all cells. `forest_fraction` and `city_fraction` are fractions of the land cells remaining after water generation; validate `forest_fraction + city_fraction <= 1.0` and height min/max ordering.

### B. `wraithnet/terrain_generator.py`

Implement pure deterministic:

```python
def generate_terrain(
    grid_width: float,
    grid_height: float,
    parameters: TerrainGenerationParameters,
    *,
    seed: int,
) -> TerrainGrid: ...
```

Algorithm requirements:

* stdlib only;
* bounded operations;
* coherent water regions via low-resolution seeded values/cellular seeding and fixed smoothing;
* land connectivity repair preserving coverage within documented tolerance;
* forest/city assigned only on land;
* city cells clustered;
* each city height generated once and stored;
* exact same input gives exact JSON;
* global random state unchanged.

Define coverage tolerance, e.g. maximum of two cells or 3% of map cells. If constraints conflict, raise structured `TerrainGenerationError`; never silently erase all requested water/city.

### C. Terrain-aware entity placement

Update `scenario_generator.py`:

* generate terrain before positions;
* home base, objectives, SAMs, and jammers select land-passable cells;
* continuous positions use safe cell centers/jitter that remains inside the chosen cell;
* jets launch around fixed land base but remain AIR-valid;
* discoverability chain must use actual city LOS, not distance only;
* placement search uses precomputed candidate cell lists/spatial buckets;
* bounded failure includes terrain coverage/connectivity suggestions.

Do not allow a generated fixed base or land entity on WATER.

### D. Feasibility

Extend `scenario_feasibility.py`:

* `NO_LAND_SPAWN_COMPONENT`;
* `ENTITY_ON_IMPASSABLE_TERRAIN`;
* `LOS_DISCOVERY_CHAIN_GAP`;
* enough legal cells for requested entities/separations;
* terrain-aware direct discovery chain.

Forest does not affect any feasibility result besides being land.

Expose reusable terrain-supercover and legal-LOS firing-point helpers for Phase 45 bid planning. Do not add negotiation scoring in this phase.

### E. Metadata

Terrain changes generated output and resolved parameters, so this phase increments `GenerationMetadata.generator_version` from 1 to 2 and provides a raw metadata migration/union for loading v1 provenance. Resolved terrain parameters and terrain seed derivation are preserved through metadata. The concrete terrain remains authoritative after manual edits; v1 recipes may be regenerated only by a supported v1 implementation.

### F. Tests/calibration

Create `tests/test_terrain_generator.py` and extend generation integration:

* same-seed identity and stream isolation;
* different-seed variation;
* coverage tolerance;
* land/water connected-component guarantees;
* city heights and clustering;
* no forest/city on water;
* 100 seeds per preset produce valid terrain-aware scenarios;
* all land entities are passable;
* city LOS discovery remains possible;
* impossible coverage/component requests fail quickly;
* normal generation performance under the Phase 32 budget or a documented revised budget.

## Acceptance criteria

* Full pytest passes.
* Every Auto-balanced preset seed matrix is schema-valid and terrain-feasible.
* Terrain generation is deterministic and does not perturb enemy/friendly RNG streams.
* No editor/viewer imports in terrain generator.
