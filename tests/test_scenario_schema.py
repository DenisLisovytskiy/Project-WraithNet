"""Validation and round-trip tests for the scenario Pydantic schema."""

import pytest
from pydantic import ValidationError

from wraithnet import config
from wraithnet.scenario_schema import JetSpec, ScenarioConfig, _default_scenario


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
    return data


def test_scenario_config_accepts_builtin_poc_scenario():
    scenario = ScenarioConfig.model_validate(config.SCENARIO)
    assert scenario == _default_scenario()
    assert [j.agent_id for j in scenario.jets] == ["Jet_1", "Jet_2", "Jet_3"]
    assert [t.target_id for t in scenario.targets] == ["Target_A", "Target_B"]
    assert [s.target_id for s in scenario.sam_sites] == ["SAM_Site_1"]
    assert [j.jammer_id for j in scenario.jammers] == ["EW_Jammer_1"]


def test_rejects_scenario_with_zero_jets():
    with pytest.raises(ValidationError) as exc_info:
        ScenarioConfig(**_valid_kwargs(jets=[]))
    assert "at least one jet" in str(exc_info.value).lower()


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
        ScenarioConfig(**_valid_kwargs(home_base=(150.0, 10.0)))
    message = str(exc_info.value).lower()
    assert "outside" in message
    assert "home base" in message
