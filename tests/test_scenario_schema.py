"""Validation and round-trip tests for the scenario Pydantic schema."""

import pytest
from pydantic import ValidationError

from wraithnet import config
from wraithnet.scenario_schema import ScenarioConfig, _default_scenario


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
