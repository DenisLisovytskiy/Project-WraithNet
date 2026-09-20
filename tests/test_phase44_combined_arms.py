"""Phase 44 acceptance smokes: presets, SAM mix, naval, metrics keys."""

from __future__ import annotations

import copy

from wraithnet import config
from wraithnet.generation_presets import parameters_for_preset
from wraithnet.generation_rng import GENERATOR_VERSION
from wraithnet.generation_schemas import GenerationParameters
from wraithnet.metrics import summarize
from wraithnet.scenario_feasibility import (
    NO_ANTI_ARMOR_CAPABILITY,
    NO_ANTI_SHIP_CAPABILITY,
    analyze_feasibility,
    weapon_eligible_damage,
)
from wraithnet.scenario_generator import generate_scenario
from wraithnet.scenario_schema import SUPPORTED_SCENARIO_VERSION, scenario_from_data
from wraithnet.world import WraithNetModel


def test_generator_version_is_v4():
    # v4: placement keeps the sampled position instead of snapping it into a
    # terrain cell, and the CLUSTERED band moved outside group_min_separation.
    assert GENERATOR_VERSION == 4


def test_legacy_poc_migrates_to_latest():
    scenario = scenario_from_data(copy.deepcopy(config.SCENARIO))
    assert scenario.scenario_version == SUPPORTED_SCENARIO_VERSION
    assert scenario.aaa_sites == []
    assert scenario.tanks == []
    assert scenario.corvettes == []
    report = analyze_feasibility(scenario)
    codes = {i.code for i in report.issues}
    assert "INVALID_DOMAIN_SPAWN" not in codes
    assert "GROUND_DISCOVERY_DEADLOCK" not in codes
    assert "NO_VALID_LAND_COMPONENT" not in codes
    model = WraithNetModel(seed=0, scenario=scenario)
    model.step({})
    assert model.tick >= 1


def test_standard_preset_resolves_combined_arms_fields():
    params = parameters_for_preset("STANDARD", seed=7)
    assert 0.0 <= params.sam_ir_fraction <= 1.0
    assert 0.0 <= params.jet_flare_fraction <= 1.0
    assert params.aaa_count >= 0
    assert params.friendly_tank_count >= 0


def test_sam_ir_mix_and_flare_fraction_applied():
    params = GenerationParameters(
        seed=21,
        objective_count=2,
        sam_count=4,
        jammer_count=0,
        friendly_count_min=3,
        friendly_count_max=4,
        sam_ir_fraction=0.5,
        jet_flare_fraction=0.5,
        aaa_count=0,
        friendly_tank_count=0,
        enemy_tank_count=0,
        max_placement_attempts=400,
    )
    result = generate_scenario(params)
    ir = sum(
        1
        for s in result.scenario.sam_sites
        if int((s.missile_loadout or {}).get("SAM_IR", 0) or 0) > 0
    )
    assert ir == 2
    assert all(
        j.flares_remaining == max(0, int(round(j.flare_capacity * 0.5)))
        for j in result.scenario.jets
    )


def test_generate_scenario_with_tanks_uses_generator_v3():
    params = GenerationParameters(
        seed=11,
        objective_count=2,
        sam_count=1,
        jammer_count=0,
        friendly_count_min=2,
        friendly_count_max=3,
        friendly_tank_count=1,
        enemy_tank_count=1,
        aaa_count=0,
        max_placement_attempts=400,
    )
    result = generate_scenario(params)
    assert result.scenario.generation is not None
    assert result.scenario.generation.generator_version == GENERATOR_VERSION
    assert len([t for t in result.scenario.tanks if t.faction == "FRIENDLY"]) == 1
    assert len([t for t in result.scenario.tanks if t.faction == "ENEMY"]) == 1
    assert any(t.order == "ADVANCE" for t in result.scenario.tanks)
    model = WraithNetModel(seed=0, scenario=result.scenario)
    model.step({})
    summary = summarize(model)
    assert "tank_rounds" in summary
    assert "aaa_bursts" in summary
    assert "ciws_attempts" in summary
    assert "carrier_recoveries" in summary


def test_training_preset_matrix_smoke():
    """Small deterministic matrix: Training seeds are schema-valid."""
    for seed in range(8):
        params = parameters_for_preset("TRAINING", seed=seed)
        result = generate_scenario(params)
        assert result.scenario.generation.generator_version == GENERATOR_VERSION
        assert result.feasibility is not None


def test_contested_25_seed_combined_arms_smoke():
    """Contested can emit non-zero tanks/naval across a 25-seed matrix."""
    saw_enemy_tank = False
    saw_friendly_tank = False
    saw_corvette = False
    for seed in range(25):
        params = parameters_for_preset("CONTESTED", seed=seed)
        result = generate_scenario(params)
        assert result.feasibility.feasible is True
        assert result.scenario.generation.generator_version == GENERATOR_VERSION
        assert len(result.scenario.tanks) == (
            params.friendly_tank_count + params.enemy_tank_count
        )
        assert len(result.scenario.corvettes) == params.enemy_corvette_count
        if params.enemy_tank_count > 0:
            saw_enemy_tank = True
            enemy_ids = {
                t.unit_id for t in result.scenario.tanks if t.faction == "ENEMY"
            }
            subjects = {
                sid
                for obj in result.scenario.mission_objectives
                if obj.objective_type == "DESTROY"
                for sid in obj.subject_ids
            }
            assert enemy_ids <= subjects
        if params.friendly_tank_count > 0:
            saw_friendly_tank = True
        if params.enemy_corvette_count > 0:
            saw_corvette = True
            corvette_ids = {c.unit_id for c in result.scenario.corvettes}
            subjects = {
                sid
                for obj in result.scenario.mission_objectives
                if obj.objective_type == "DESTROY"
                for sid in obj.subject_ids
            }
            assert corvette_ids <= subjects
            assert any(
                sum(
                    weapon_eligible_damage(w, "SURFACE_VESSEL") * c
                    for w, c in j.payload.items()
                )
                > 0
                for j in result.scenario.jets
            )
    assert saw_enemy_tank
    assert saw_friendly_tank
    assert saw_corvette


def test_same_seed_reproducible_terrain_and_ids():
    params = GenerationParameters(
        seed=99,
        objective_count=2,
        sam_count=1,
        jammer_count=0,
        friendly_count_min=2,
        friendly_count_max=3,
        enemy_tank_count=1,
        friendly_tank_count=1,
        max_placement_attempts=400,
    )
    a = generate_scenario(params)
    b = generate_scenario(params)
    assert [t.unit_id for t in a.scenario.tanks] == [t.unit_id for t in b.scenario.tanks]
    assert [t.position for t in a.scenario.tanks] == [t.position for t in b.scenario.tanks]
    assert [j.agent_id for j in a.scenario.jets] == [j.agent_id for j in b.scenario.jets]


def test_jetless_tank_duel_is_statically_feasible():
    from wraithnet.bases import default_home_airbase
    from wraithnet.objectives import MissionObjective
    from wraithnet.scenario_schema import new_scenario_data
    from wraithnet.tanks import TankSpec

    home = (2.0, 2.0)
    data = new_scenario_data(
        grid_width=40.0,
        grid_height=40.0,
        home_base=home,
        fixed_airbases=[default_home_airbase(home)],
        jets=[],
        targets=[],
        tanks=[
            TankSpec(
                unit_id="Blue_1",
                faction="FRIENDLY",
                position=(10.0, 10.0),
                order="ADVANCE",
                waypoints=[(18.0, 10.0)],
            ),
            TankSpec(
                unit_id="Red_1",
                faction="ENEMY",
                position=(20.0, 10.0),
                order="ADVANCE",
                waypoints=[(12.0, 10.0)],
            ),
        ],
        mission_objectives=[
            MissionObjective(
                objective_id="OBJ_T",
                objective_type="DESTROY",
                subject_ids=["Red_1"],
                required=True,
            ).model_dump()
        ],
    )
    scenario = scenario_from_data(data)
    report = analyze_feasibility(scenario)
    codes = {i.code for i in report.issues if i.severity == "ERROR"}
    assert "INSUFFICIENT_DAMAGE" not in codes
    assert "NO_SUITABLE_WEAPON" not in codes
    assert "OBJECTIVE_UNACHIEVABLE" not in codes
    assert report.feasible


def test_no_anti_armor_when_jets_lack_weapons_and_no_friendly_tanks():
    from wraithnet.bases import default_home_airbase
    from wraithnet.objectives import MissionObjective
    from wraithnet.scenario_schema import new_scenario_data
    from wraithnet.tanks import TankSpec

    home = (2.0, 2.0)
    data = new_scenario_data(
        grid_width=40.0,
        grid_height=40.0,
        home_base=home,
        fixed_airbases=[default_home_airbase(home)],
        jets=[
            {
                "agent_id": "Jet_1",
                "position": (5.0, 5.0),
                "payload": {"AIM9X": 2},
            }
        ],
        targets=[],
        tanks=[
            TankSpec(
                unit_id="Red_1",
                faction="ENEMY",
                position=(20.0, 20.0),
                order="HOLD",
            )
        ],
        mission_objectives=[
            MissionObjective(
                objective_id="OBJ_T",
                objective_type="DESTROY",
                subject_ids=["Red_1"],
                required=True,
            ).model_dump()
        ],
    )
    report = analyze_feasibility(scenario_from_data(data))
    assert any(i.code == NO_ANTI_ARMOR_CAPABILITY for i in report.issues)


def test_anti_ship_warning_without_agm84():
    from wraithnet.bases import default_home_airbase
    from wraithnet.naval import CorvetteSpec, all_water_terrain
    from wraithnet.objectives import MissionObjective
    from wraithnet.scenario_schema import new_scenario_data
    from wraithnet.terrain import TerrainCell

    home = (2.0, 2.0)
    terrain = all_water_terrain(40.0, 40.0)
    for r in range(5):
        for c in range(5):
            terrain.cells[r][c] = TerrainCell(terrain_type="PLAINS")
    data = new_scenario_data(
        grid_width=40.0,
        grid_height=40.0,
        home_base=home,
        fixed_airbases=[default_home_airbase(home)],
        terrain=terrain,
        jets=[{"agent_id": "Jet_1", "position": (3.0, 3.0), "payload": {"AIM9X": 2}}],
        targets=[
            {
                "target_id": "T1",
                "position": (3.5, 3.5),
                "hp_required": 1,
                "target_type": "HARDENED_BUNKER",
            }
        ],
        corvettes=[
            CorvetteSpec(unit_id="CV_1", position=(30.0, 30.0), hold_position=True)
        ],
        mission_objectives=[
            MissionObjective(
                objective_id="OBJ_T",
                objective_type="DESTROY",
                subject_ids=["T1"],
                required=True,
            ).model_dump()
        ],
    )
    report = analyze_feasibility(scenario_from_data(data))
    assert any(i.code == NO_ANTI_SHIP_CAPABILITY for i in report.issues)
