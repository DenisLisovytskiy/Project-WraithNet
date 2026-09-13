"""Validation and round-trip tests for the scenario Pydantic schema."""

import copy

import pytest
from pydantic import ValidationError

from wraithnet import config
from wraithnet.bases import HOME_BASE_ID
from wraithnet.scenario_schema import (
    JetSpec,
    ScenarioConfig,
    _default_scenario,
    migrate_v1_to_v2,
    migrate_v2_to_v3,
    migrate_v4_to_v5,
    new_scenario_data,
    scenario_from_data,
)
from wraithnet.terrain import all_plains_terrain


def _valid_kwargs(**overrides) -> dict:
    data = {
        "jets": [
            {
                "agent_id": "Jet_1",
                "position": (10.0, 10.0),
                "payload": {"JDAM": 1, "AIM9X": 0},
            }
        ],
        "targets": [
            {
                "target_id": "Target_A",
                "position": (50.0, 50.0),
                "hp_required": 1,
                "target_type": "HARDENED_BUNKER",
            }
        ],
    }
    data.update(overrides)
    return new_scenario_data(**data)


def test_scenario_config_accepts_builtin_poc_scenario():
    scenario = scenario_from_data(config.SCENARIO)
    assert scenario == _default_scenario()
    assert [j.agent_id for j in scenario.jets] == ["Jet_1", "Jet_2", "Jet_3"]
    assert [t.target_id for t in scenario.targets] == ["Target_A", "Target_B"]
    assert [s.target_id for s in scenario.sam_sites] == ["SAM_Site_1"]
    assert [j.jammer_id for j in scenario.jammers] == ["EW_Jammer_1"]
    assert scenario.scenario_version == 9
    assert scenario.combat_seed == 0
    assert scenario.terrain.world_width == scenario.grid_width
    assert all(
        cell.terrain_type == "PLAINS"
        for row in scenario.terrain.cells
        for cell in row
    )
    assert any(b.base_id == HOME_BASE_ID for b in scenario.fixed_airbases)
    assert any(o.required for o in scenario.mission_objectives)
    destroy = next(o for o in scenario.mission_objectives if o.required)
    assert destroy.objective_type == "DESTROY"
    assert set(destroy.subject_ids) == {"Target_A", "Target_B", "SAM_Site_1"}


def test_direct_validate_rejects_legacy_without_migration():
    with pytest.raises(ValidationError):
        ScenarioConfig.model_validate(config.SCENARIO)


def test_allows_jetless_scenario_with_required_objective():
    scenario = ScenarioConfig(**_valid_kwargs(jets=[]))
    assert scenario.jets == []
    assert any(o.required for o in scenario.mission_objectives)


def test_rejects_scenario_without_required_objective():
    with pytest.raises(ValidationError) as exc_info:
        ScenarioConfig(**_valid_kwargs(mission_objectives=[]))
    assert "required mission objective" in str(exc_info.value).lower()


def test_rejects_duplicate_id_across_entity_lists():
    with pytest.raises(ValidationError) as exc_info:
        ScenarioConfig(
            **_valid_kwargs(
                jets=[
                    {
                        "agent_id": "Shared_ID",
                        "position": (10.0, 10.0),
                        "payload": {"JDAM": 1, "AIM9X": 0},
                    }
                ],
                targets=[
                    {
                        "target_id": "Shared_ID",
                        "position": (50.0, 50.0),
                        "hp_required": 1,
                        "target_type": "HARDENED_BUNKER",
                    }
                ],
            )
        )
    message = str(exc_info.value)
    assert "Shared_ID" in message
    assert "unique" in message.lower()


def test_rejects_position_outside_grid():
    with pytest.raises(ValidationError) as exc_info:
        ScenarioConfig(
            **_valid_kwargs(
                jets=[
                    {
                        "agent_id": "Jet_1",
                        "position": (150.0, 10.0),
                        "payload": {"JDAM": 1, "AIM9X": 0},
                    }
                ]
            )
        )
    message = str(exc_info.value).lower()
    assert "outside" in message
    assert "jet_1" in message or "150" in message


def test_rejects_hp_required_below_one():
    with pytest.raises(ValidationError) as exc_info:
        ScenarioConfig(
            **_valid_kwargs(
                targets=[
                    {
                        "target_id": "Target_A",
                        "position": (50.0, 50.0),
                        "hp_required": 0,
                        "target_type": "HARDENED_BUNKER",
                    }
                ]
            )
        )
    message = str(exc_info.value).lower()
    assert "hp_required" in message
    assert "at least 1" in message


def test_rejects_negative_payload_count():
    with pytest.raises(ValidationError) as exc_info:
        ScenarioConfig(
            **_valid_kwargs(
                jets=[
                    {
                        "agent_id": "Jet_1",
                        "position": (10.0, 10.0),
                        "payload": {"JDAM": -1, "AIM9X": 0},
                    }
                ]
            )
        )
    message = str(exc_info.value).lower()
    assert "negative" in message
    assert "jdam" in message


def test_scenario_config_json_round_trip():
    original = _default_scenario()
    restored = ScenarioConfig.model_validate_json(original.model_dump_json())
    assert restored == original
    assert restored.model_dump() == original.model_dump()


def test_jet_spec_defaults_to_strike_fighter_facing_plus_y():
    jet = JetSpec(
        agent_id="Jet_1",
        position=(10.0, 10.0),
        payload={"JDAM": 1, "AIM9X": 0},
    )
    assert jet.frame_type == "STRIKE_FIGHTER"
    assert jet.initial_heading == 0.0


def test_default_scenario_jets_get_strike_fighter_without_explicit_frame():
    scenario = _default_scenario()
    assert all(j.frame_type == "STRIKE_FIGHTER" for j in scenario.jets)
    assert all(j.initial_heading == 0.0 for j in scenario.jets)


def test_rejects_invalid_frame_type():
    with pytest.raises(ValidationError):
        JetSpec(
            agent_id="Jet_1",
            position=(10.0, 10.0),
            payload={"JDAM": 1, "AIM9X": 0},
            frame_type="BIPLANE",
        )


def test_default_scenario_payloads_fit_strike_fighter_ordnance_slots():
    scenario = _default_scenario()
    slots_max = config.JET_FRAME_CATALOG["STRIKE_FIGHTER"].ordnance_slots
    for jet in scenario.jets:
        used = sum(
            config.WEAPON_CATALOG[wt].slot_cost * count
            for wt, count in jet.payload.items()
        )
        assert used <= slots_max, (jet.agent_id, used, slots_max)


def test_rejects_payload_exceeding_ordnance_slots():
    """STRIKE_FIGHTER has 7 slots; 8 JDAM at slot_cost 1 overflows."""
    with pytest.raises(ValidationError) as exc_info:
        ScenarioConfig(
            **_valid_kwargs(
                jets=[
                    {
                        "agent_id": "Jet_2",
                        "position": (10.0, 10.0),
                        "payload": {"JDAM": 8, "AIM9X": 0},
                    }
                ]
            )
        )
    message = str(exc_info.value)
    assert "Jet 'Jet_2'" in message
    assert "8 ordnance slots" in message
    assert "STRIKE_FIGHTER" in message
    assert "7" in message


def test_default_scenario_home_base_is_sw_corner():
    scenario = _default_scenario()
    assert scenario.home_base == config.DEFAULT_HOME_BASE == (2.0, 2.0)


def test_rejects_home_base_outside_grid():
    with pytest.raises(ValidationError) as exc_info:
        ScenarioConfig(
            **_valid_kwargs(
                home_base=(150.0, 10.0),
                fixed_airbases=[{"base_id": HOME_BASE_ID, "position": (150.0, 10.0)}],
            )
        )
    message = str(exc_info.value).lower()
    assert "outside" in message
    assert "home base" in message


def test_migrate_v1_to_v3_via_scenario_from_data():
    scenario = scenario_from_data(dict(config.SCENARIO))
    assert scenario.scenario_version == 9
    assert scenario.combat_seed == 0
    assert [b.base_id for b in scenario.fixed_airbases] == [HOME_BASE_ID]
    assert scenario.fixed_airbases[0].position == scenario.home_base
    assert all(
        j.base_assignment.primary_base_id == HOME_BASE_ID for j in scenario.jets
    )
    assert len(scenario.mission_objectives) == 2
    destroy = next(o for o in scenario.mission_objectives if o.objective_type == "DESTROY")
    ret = next(o for o in scenario.mission_objectives if o.objective_type == "RETURN")
    assert destroy.required is True
    assert set(destroy.subject_ids) == {"Target_A", "Target_B", "SAM_Site_1"}
    assert ret.required is True
    assert set(ret.subject_ids) == {"Jet_1", "Jet_2", "Jet_3"}


def test_migrate_v2_to_v3_synthesizes_legacy_destroy_and_defaults():
    v2 = migrate_v1_to_v2(dict(config.SCENARIO))
    assert v2["scenario_version"] == 2
    assert "combat_seed" not in v2
    assert "mission_objectives" not in v2
    assert "fixed_airbases" not in v2

    v3 = migrate_v2_to_v3(copy.deepcopy(v2))
    assert v3["scenario_version"] == 3
    assert v3["combat_seed"] == 0
    assert v3["fixed_airbases"][0]["base_id"] == HOME_BASE_ID
    assert v3["mission_objectives"][0]["objective_type"] == "DESTROY"
    assert set(v3["mission_objectives"][0]["subject_ids"]) == {
        "Target_A",
        "Target_B",
        "SAM_Site_1",
    }
    for jet in v3["jets"]:
        assert jet["base_assignment"]["primary_base_id"] == HOME_BASE_ID

    scenario = scenario_from_data(v2)
    assert scenario.scenario_version == 9
    assert scenario.combat_seed == 0


def test_new_scenario_data_defaults_combat_seed_zero():
    data = new_scenario_data(
        jets=[
            {
                "agent_id": "Jet_1",
                "position": (10.0, 10.0),
                "payload": {"JDAM": 1, "AIM9X": 0},
            }
        ],
        targets=[
            {
                "target_id": "Target_A",
                "position": (50.0, 50.0),
                "hp_required": 1,
                "target_type": "HARDENED_BUNKER",
            }
        ],
    )
    assert data["combat_seed"] == 0
    assert data["scenario_version"] == 9
    scenario = scenario_from_data(data)
    assert scenario.combat_seed == 0


def test_legacy_destroy_objective_covers_targets_and_sams():
    data = new_scenario_data(
        jets=[
            {
                "agent_id": "Jet_1",
                "position": (10.0, 10.0),
                "payload": {"JDAM": 1, "AIM9X": 0},
            }
        ],
        targets=[
            {
                "target_id": "Target_A",
                "position": (50.0, 50.0),
                "hp_required": 1,
                "target_type": "HARDENED_BUNKER",
            }
        ],
        sam_sites=[
            {
                "target_id": "SAM_1",
                "position": (60.0, 60.0),
                "hp_required": 1,
                "target_type": "SAM_SITE",
            }
        ],
    )
    objs = data["mission_objectives"]
    assert len(objs) == 2
    destroy = next(
        o for o in objs
        if (o.objective_type if hasattr(o, "objective_type") else o["objective_type"])
        == "DESTROY"
    )
    subject_ids = destroy.subject_ids if hasattr(destroy, "subject_ids") else destroy["subject_ids"]
    assert set(subject_ids) == {"Target_A", "SAM_1"}
    ret = next(
        o for o in objs
        if (o.objective_type if hasattr(o, "objective_type") else o["objective_type"])
        == "RETURN"
    )
    ret_ids = ret.subject_ids if hasattr(ret, "subject_ids") else ret["subject_ids"]
    assert set(ret_ids) == {"Jet_1"}


def test_rejects_unknown_objective_subject_id():
    with pytest.raises(ValidationError) as exc_info:
        ScenarioConfig(
            **_valid_kwargs(
                mission_objectives=[
                    {
                        "objective_id": "OBJ_BAD",
                        "objective_type": "DESTROY",
                        "subject_ids": ["Missing_Entity"],
                        "required": True,
                    }
                ]
            )
        )
    assert "Missing_Entity" in str(exc_info.value)


def test_rejects_base_home_position_mismatch():
    with pytest.raises(ValidationError) as exc_info:
        ScenarioConfig(
            **_valid_kwargs(
                home_base=(2.0, 2.0),
                fixed_airbases=[{"base_id": HOME_BASE_ID, "position": (5.0, 5.0)}],
            )
        )
    message = str(exc_info.value).lower()
    assert "base_home" in message or HOME_BASE_ID.lower() in message
    assert "home_base" in message


def test_jet_spec_defaults_base_assignment_to_home():
    jet = JetSpec(
        agent_id="Jet_1",
        position=(10.0, 10.0),
        payload={"JDAM": 1, "AIM9X": 0},
    )
    assert jet.base_assignment.primary_base_id == HOME_BASE_ID
    assert jet.base_assignment.alternate_base_id is None


def test_direct_validate_rejects_v2_without_migration():
    v2 = {
        "scenario_version": 2,
        "jets": [
            {
                "agent_id": "Jet_1",
                "position": (10.0, 10.0),
                "payload": {"JDAM": 1, "AIM9X": 0},
            }
        ],
        "targets": [
            {
                "target_id": "Target_A",
                "position": (50.0, 50.0),
                "hp_required": 1,
                "target_type": "HARDENED_BUNKER",
            }
        ],
        "terrain": all_plains_terrain(100.0, 100.0).model_dump(),
    }
    with pytest.raises(ValidationError):
        ScenarioConfig.model_validate(v2)
