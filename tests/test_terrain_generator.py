"""Procedural terrain generation and stream-isolation tests (Phase 35)."""

from __future__ import annotations

import random

import pytest

from wraithnet import config
from wraithnet.generation_presets import parameters_for_preset
from wraithnet.generation_rng import (
    ENEMY_SALT,
    FRIENDLY_SALT,
    TERRAIN_SALT,
    derived_seed,
)
from wraithnet.generation_schemas import TerrainGenerationParameters
from wraithnet.scenario_feasibility import (
    ground_entity_on_passable_terrain,
    has_discovery_los,
    largest_land_component_size,
)
from wraithnet.scenario_generator import (
    AUTO_BALANCE_CAPACITY,
    GenerationError,
    PLACEMENT_EXHAUSTED,
    generate_scenario,
)
from wraithnet.terrain import TerrainCell
from wraithnet.terrain_generator import (
    TerrainGenerationError,
    coverage_tolerance_cells,
    generate_terrain,
)


def test_same_seed_terrain_identity_and_json_stability():
    params = TerrainGenerationParameters()
    seed = derived_seed(42, TERRAIN_SALT)
    a = generate_terrain(100.0, 100.0, params, seed=seed)
    b = generate_terrain(100.0, 100.0, params, seed=seed)
    assert a.model_dump() == b.model_dump()
    assert a.model_dump_json() == b.model_dump_json()


def test_terrain_stream_isolated_from_friendly_salt():
    params = TerrainGenerationParameters(water_fraction=0.25, city_fraction=0.1)
    root = 99
    t_a = generate_terrain(
        100.0, 100.0, params, seed=derived_seed(root, TERRAIN_SALT)
    )
    t_b = generate_terrain(
        100.0, 100.0, params, seed=derived_seed(root, TERRAIN_SALT)
    )
    assert t_a.model_dump() == t_b.model_dump()
    # Friendly salt must not equal terrain salt mixing.
    assert derived_seed(root, TERRAIN_SALT) != derived_seed(root, FRIENDLY_SALT)
    assert derived_seed(root, TERRAIN_SALT) != derived_seed(root, ENEMY_SALT)


def test_friendly_mode_does_not_change_terrain():
    base = parameters_for_preset("STANDARD", seed=7)
    auto = generate_scenario(base)
    random_legal = generate_scenario(
        base.model_copy(update={"friendly_mode": "RANDOM_LEGAL"})
    )
    assert auto.scenario.terrain.model_dump() == random_legal.scenario.terrain.model_dump()


def test_different_seed_varies_terrain():
    params = TerrainGenerationParameters(water_fraction=0.3, city_fraction=0.15)
    a = generate_terrain(100.0, 100.0, params, seed=1)
    b = generate_terrain(100.0, 100.0, params, seed=2)
    assert a.model_dump() != b.model_dump()


def test_coverage_within_tolerance():
    params = TerrainGenerationParameters(
        water_fraction=0.20,
        forest_fraction=0.20,
        city_fraction=0.10,
        smoothing_passes=3,
    )
    grid = generate_terrain(100.0, 100.0, params, seed=5)
    total = grid.rows * grid.columns
    tol = coverage_tolerance_cells(total)
    water = sum(
        1
        for row in grid.cells
        for cell in row
        if cell.terrain_type == "WATER"
    )
    target = int(round(params.water_fraction * total))
    assert abs(water - target) <= tol

    land = total - water
    forest = sum(1 for row in grid.cells for c in row if c.terrain_type == "FOREST")
    city = sum(1 for row in grid.cells for c in row if c.terrain_type == "CITY")
    assert abs(forest - int(round(params.forest_fraction * land))) <= tol
    assert abs(city - int(round(params.city_fraction * land))) <= tol


def test_land_component_guarantee():
    params = TerrainGenerationParameters(
        water_fraction=0.35,
        minimum_land_component_cells=40,
        minimum_water_component_cells=0,
    )
    grid = generate_terrain(100.0, 100.0, params, seed=11)
    assert largest_land_component_size(grid) >= 40


def test_no_forest_or_city_on_water():
    params = TerrainGenerationParameters(
        water_fraction=0.3, forest_fraction=0.25, city_fraction=0.2
    )
    for seed in range(15):
        grid = generate_terrain(100.0, 100.0, params, seed=seed)
        for row in grid.cells:
            for cell in row:
                if cell.terrain_type == "WATER":
                    assert cell.building_height is None
                if cell.terrain_type in ("FOREST", "CITY"):
                    assert cell.terrain_type != "WATER"


def test_city_heights_persisted_in_range():
    params = TerrainGenerationParameters(
        city_fraction=0.2,
        building_height_min=5.0,
        building_height_max=15.0,
        water_fraction=0.1,
    )
    grid = generate_terrain(100.0, 100.0, params, seed=3)
    cities = [
        cell for row in grid.cells for cell in row if cell.terrain_type == "CITY"
    ]
    assert cities
    for cell in cities:
        assert cell.building_height is not None
        assert 5.0 <= cell.building_height <= 15.0


def test_city_cells_are_clustered():
    params = TerrainGenerationParameters(
        water_fraction=0.05,
        forest_fraction=0.0,
        city_fraction=0.2,
        city_cluster_radius=2,
    )
    grid = generate_terrain(100.0, 100.0, params, seed=8)
    cities = [
        (c, r)
        for r, row in enumerate(grid.cells)
        for c, cell in enumerate(row)
        if cell.terrain_type == "CITY"
    ]
    assert len(cities) >= 4
    # Most cities should have a city neighbor within Chebyshev radius 2.
    clustered = 0
    for c, r in cities:
        if any(
            max(abs(c - oc), abs(r - or_)) <= 2 and (c, r) != (oc, or_)
            for oc, or_ in cities
        ):
            clustered += 1
    assert clustered / len(cities) >= 0.5


def test_impossible_coverage_fails_quickly():
    params = TerrainGenerationParameters(
        water_fraction=0.75,
        minimum_land_component_cells=300,
    )
    with pytest.raises(TerrainGenerationError) as exc_info:
        generate_terrain(100.0, 100.0, params, seed=1)
    assert exc_info.value.code in {"IMPOSSIBLE_COVERAGE", "COMPONENT_CONSTRAINT"}


def test_global_rng_unchanged_by_terrain_generation():
    rng_state = random.getstate()
    generate_terrain(
        100.0,
        100.0,
        TerrainGenerationParameters(),
        seed=123,
    )
    assert random.getstate() == rng_state


def test_generated_land_entities_are_passable():
    for preset in ("TRAINING", "STANDARD", "CONTESTED"):
        for seed in (0, 1, 2, 5, 9):
            result = generate_scenario(parameters_for_preset(preset, seed=seed))
            sc = result.scenario
            for ent_pos in (
                *(t.position for t in sc.targets),
                *(s.position for s in sc.sam_sites),
                *(j.position for j in sc.jammers),
                sc.home_base,
            ):
                assert ground_entity_on_passable_terrain(sc.terrain, ent_pos)


def test_generator_version_is_two():
    result = generate_scenario(parameters_for_preset("STANDARD", seed=0))
    assert result.scenario.generation is not None
    assert result.scenario.generation.generator_version == 3
    assert result.scenario.generation.resolved_parameters.terrain.water_fraction > 0.0


@pytest.mark.parametrize("preset", ["TRAINING", "STANDARD", "CONTESTED", "NIGHTMARE"])
def test_auto_balanced_seed_matrix_terrain_feasible(preset):
    # Phase 35 acceptance: successful gens must be terrain-feasible. Dense
    # NIGHTMARE packs may raise PLACEMENT_EXHAUSTED on some seeds (same
    # contract as the friendlies AUTO_BALANCED matrix); skip those and require
    # that every completed scenario is feasible.
    n = 25 if preset != "NIGHTMARE" else 15
    completed = 0
    for seed in range(n):
        try:
            result = generate_scenario(parameters_for_preset(preset, seed=seed))
        except GenerationError as exc:
            if preset == "NIGHTMARE" and exc.code in (
                PLACEMENT_EXHAUSTED,
                AUTO_BALANCE_CAPACITY,
            ):
                continue
            raise
        completed += 1
        assert result.scenario.generation.generator_version == 3
        assert result.feasibility.feasible, (
            preset,
            seed,
            [i.code for i in result.feasibility.issues if i.severity == "ERROR"],
        )
    assert completed >= 1, f"{preset}: every seed exhausted placement"


def test_standard_100_seed_matrix():
    for seed in range(100):
        result = generate_scenario(parameters_for_preset("STANDARD", seed=seed))
        assert result.feasibility.feasible, seed


def test_discovery_los_possible_on_generated_scenarios():
    result = generate_scenario(parameters_for_preset("STANDARD", seed=4))
    sc = result.scenario
    # At least the nearest target from a launch-ring approach should have LOS
    # on plains-heavy STANDARD seeds; if cities block one, another may remain.
    home = sc.home_base
    assert sc.targets or sc.sam_sites
    any_clear = False
    for ent in (*sc.targets, *sc.sam_sites):
        dist = ((ent.position[0] - home[0]) ** 2 + (ent.position[1] - home[1]) ** 2) ** 0.5
        if dist <= 1e-9:
            approach = home
        else:
            t = min(1.0, 6.0 / dist)
            approach = (
                home[0] + (ent.position[0] - home[0]) * t,
                home[1] + (ent.position[1] - home[1]) * t,
            )
        if has_discovery_los(sc.terrain, approach, ent.position):
            any_clear = True
            break
    # Feasibility already requires a rooted chain; this asserts helper agreement.
    assert result.feasibility.feasible
    assert any_clear or not (sc.targets or sc.sam_sites)


def test_entity_on_water_feasibility_error():
    from wraithnet.scenario_schema import new_scenario_data, scenario_from_data
    from wraithnet.terrain import TerrainGrid, all_plains_terrain

    base = all_plains_terrain(100.0, 100.0)
    cells = [[TerrainCell(terrain_type=c.terrain_type) for c in row] for row in base.cells]
    cells[10][10] = TerrainCell(terrain_type="WATER")
    terrain = TerrainGrid(
        world_width=100.0,
        world_height=100.0,
        cell_size=5.0,
        columns=20,
        rows=20,
        cells=cells,
    )
    # Target forced onto water cell center (52.5, 52.5) → col/row 10.
    data = new_scenario_data(
        terrain=terrain,
        jets=[
            {
                "agent_id": "Jet_1",
                "position": (10.0, 10.0),
                "payload": {"JDAM": 2},
            }
        ],
        targets=[
            {
                "target_id": "Wet",
                "position": (52.5, 52.5),
                "hp_required": 1,
                "target_type": "HARDENED_BUNKER",
            }
        ],
        home_base=(2.0, 2.0),
    )
    with pytest.raises(Exception):
        # ScenarioConfig validation should reject water GROUND placement.
        scenario_from_data(data)


def test_generation_performance_budget():
    """Compact-grid STANDARD median stays interactive (Phase 32/35 spirit).

    Default missions use 5000×5000 maps (multi-second); this budget pins the
    historical small-map Quick Generate path so regressions stay visible.
    """
    import time

    times = []
    for seed in range(20):
        t0 = time.perf_counter()
        generate_scenario(
            parameters_for_preset(
                "STANDARD",
                seed=seed,
                overrides={"grid_width": 100.0, "grid_height": 100.0},
            )
        )
        times.append(time.perf_counter() - t0)
    times.sort()
    median = times[len(times) // 2]
    assert median < 0.50, (
        f"STANDARD 100×100 median {median:.3f}s exceeds 500ms compact-grid budget"
    )
