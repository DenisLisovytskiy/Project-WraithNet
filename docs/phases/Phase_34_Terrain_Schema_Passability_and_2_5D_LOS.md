# Phase 34 — Terrain Schema, Passability & 2.5D LOS

> **LLM implementation prompt:** Assume Phases 0–33 are complete. Read `docs/19_Terrain_Naval_and_Combined_Arms_Design_Document.md` §§1–3, 16–17. Implement authoritative terrain storage, coordinate/passability queries, and city-building LOS. Integrate LOS with current sensing, strikes, and legacy SAM engagement. Do not implement procedural generation, painting, ships, tanks, missile flyout, or terrain rendering.

## Deliverables

### A. `wraithnet/terrain.py`

Implement:

* `TerrainType = Literal["WATER", "PLAINS", "FOREST", "CITY"]`
* `MovementDomain = Literal["AIR", "GROUND", "SURFACE"]`
* `TerrainCell`
* `TerrainGrid`, including persisted `terrain_edit_seed: int = 0` for deterministic editor-created city heights;
* `all_plains_terrain(width, height, cell_size=5.0)`
* `world_to_cell()`
* `cell_center()`
* `is_passable()`
* `segment_supercover_cells()`
* `has_line_of_sight()`

Validation:

* positive cell size;
* rectangular nonempty rows;
* `rows/columns` equal the ceiling of world dimensions divided by cell size; maximum-edge cells are clipped, supporting arbitrary legacy dimensions;
* city heights `4.0..25.0`;
* city requires height; non-city forbids it;
* no shared mutable defaults.

Coordinate rules:

* max-edge points clamp to final cell;
* out-of-map points raise a typed error;
* boundary traversal is deterministic and symmetric;
* LOS ignores start/end cells and tests intermediate city columns using linearly interpolated Z.

Forest is passable and transparent. Water blocks GROUND but permits SURFACE. AIR ignores passability.

### B. `ScenarioConfig`

Add required latest-version schema fields:

```python
scenario_version: Literal[2]
terrain: TerrainGrid
```

Create one public `scenario_from_data(raw)` adapter used by scenario I/O, editor construction, tests, default-scenario wrapping, and launcher paths. It inspects raw data before validation: missing version means legacy v1, then pure `migrate_v1_to_v2()` adds all-plains terrain/version. Reject versions greater than supported. Use `extra="forbid"` and keep latest `scenario_version`/`terrain` required so a direct `ScenarioConfig.model_validate(legacy_dict)` fails instead of silently bypassing migration. Provide `new_scenario_data()` for new programmatic drafts. Reject a grid whose declared world dimensions differ from the scenario.

Keep terrain data independent of optional generation metadata.

### C. Current-world LOS integration

Add explicit temporary sight heights in config:

* `JET_COMBAT_ALTITUDE = 20.0`
* `GROUND_TARGET_HEIGHT = 1.8`
* `SAM_SENSOR_HEIGHT = 3.0`

Use one world helper for LOS:

* direct target inclusion in `build_observation`;
* `_apply_strike`;
* legacy `_apply_sam_damage`.

Do not apply city LOS to jamming in this phase; radio propagation remains radius-only unless a later EW design says otherwise.

The agent's remembered datalink contact may persist without current LOS, but a direct strike still requires LOS at adjudication.

### D. Scenario validation helpers

Add reusable:

```python
def validate_position_for_domain(
    terrain: TerrainGrid,
    position: tuple[float, float],
    domain: MovementDomain,
) -> None: ...
```

Apply GROUND validation to current targets, SAMs, jammers, and fixed home base. Legacy all-plains scenarios pass. Jets are AIR and may start over any tile.

### E. Tests

Create `tests/test_terrain.py` and extend scenario/world tests:

1. schema and legacy all-plains migration;
2. world/cell boundary mapping including `(width,height)`;
3. passability matrix for every terrain/domain pair;
4. supercover symmetry and corner cases;
5. low ground LOS blocked by city;
6. high LOS passes over shorter building;
7. start/end city cell is ignored;
8. forests do not block LOS;
9. observations omit blocked targets;
10. injected strike through building is rejected without consuming ammunition;
11. legacy SAM does not damage through blocked LOS;
12. land entities reject water; jets do not;
13. serialization round-trip preserves exact cell types/heights.

## Compatibility

* Existing scenarios and default scenario behavior stay identical on all-plains terrain.
* Existing viewer may ignore terrain until Phase 36, but trace changes must not be added prematurely unless required for schema tests.
* Do not import Panda3D/Pygame in `wraithnet/terrain.py`.

## Acceptance criteria

* Full pytest passes.
* LOS is symmetric and deterministic.
* Water restrictions and city blocking are enforced in world adjudication, not only UI.
* No unbounded grid traversal or per-query full-map scan.
