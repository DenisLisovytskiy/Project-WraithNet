"""Validation and round-trip tests for generation schemas (Phase 30)."""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError

from wraithnet import config
from wraithnet.generation_schemas import (
    FeasibilityIssue,
    FeasibilityReport,
    FriendlyTemplate,
    GenerationMetadata,
    GenerationParameters,
)
from wraithnet.scenario_schema import ScenarioConfig, _default_scenario, scenario_from_data


def _valid_params(**overrides) -> dict:
    data = {
        "seed": 42,
        "preset": "STANDARD",
        "objective_count": 4,
        "sam_count": 2,
        "jammer_count": 1,
    }
    data.update(overrides)
    return data


def test_default_generation_parameters_validate():
    params = GenerationParameters(seed=1)
    assert params.preset == "STANDARD"
    assert params.friendly_mode == "AUTO_BALANCED"
    assert params.objective_hp_min <= params.objective_hp_max
    assert "SAM_PAIR" not in params.allowed_group_templates
    assert set(params.allowed_frame_types) == set(config.JET_FRAME_CATALOG)


def test_rejects_hp_min_greater_than_max():
    with pytest.raises(ValidationError) as exc_info:
        GenerationParameters(**_valid_params(objective_hp_min=5, objective_hp_max=2))
    assert "objective_hp_min" in str(exc_info.value)


def test_rejects_friendly_count_min_greater_than_max():
    with pytest.raises(ValidationError) as exc_info:
        GenerationParameters(
            **_valid_params(friendly_count_min=8, friendly_count_max=3)
        )
    assert "friendly_count_min" in str(exc_info.value)


def test_rejects_distance_fraction_ordering():
    with pytest.raises(ValidationError) as exc_info:
        GenerationParameters(
            **_valid_params(
                objective_distance_min_fraction=0.9,
                objective_distance_max_fraction=0.2,
            )
        )
    assert "objective_distance_min_fraction" in str(exc_info.value)


def test_rejects_empty_and_duplicate_group_templates():
    with pytest.raises(ValidationError) as exc_info:
        GenerationParameters(**_valid_params(allowed_group_templates=[]))
    assert "allowed_group_templates" in str(exc_info.value)

    with pytest.raises(ValidationError) as exc_info:
        GenerationParameters(
            **_valid_params(
                allowed_group_templates=["OBJECTIVE_ONLY", "OBJECTIVE_ONLY"]
            )
        )
    assert "duplicate" in str(exc_info.value).lower()


def test_rejects_empty_and_duplicate_frame_types():
    with pytest.raises(ValidationError) as exc_info:
        GenerationParameters(**_valid_params(allowed_frame_types=[]))
    assert "allowed_frame_types" in str(exc_info.value)

    with pytest.raises(ValidationError) as exc_info:
        GenerationParameters(
            **_valid_params(
                allowed_frame_types=["STRIKE_FIGHTER", "STRIKE_FIGHTER"]
            )
        )
    assert "duplicate" in str(exc_info.value).lower()


def test_custom_roster_required_only_for_custom_mode():
    with pytest.raises(ValidationError) as exc_info:
        GenerationParameters(
            **_valid_params(friendly_mode="CUSTOM_ROSTER", custom_roster=[])
        )
    assert "CUSTOM_ROSTER" in str(exc_info.value)

    template = FriendlyTemplate(
        frame_type="STRIKE_FIGHTER",
        payload={"JDAM": 2, "AIM9X": 1},
        count=2,
    )
    with pytest.raises(ValidationError) as exc_info:
        GenerationParameters(
            **_valid_params(friendly_mode="AUTO_BALANCED", custom_roster=[template])
        )
    assert "rejects a non-empty custom_roster" in str(exc_info.value)

    params = GenerationParameters(
        **_valid_params(friendly_mode="CUSTOM_ROSTER", custom_roster=[template])
    )
    assert len(params.custom_roster) == 1


@pytest.mark.parametrize("weapon", list(config.WEAPON_CATALOG))
def test_slot_cost_validation_for_every_catalog_weapon(weapon):
    slot_cost = config.WEAPON_CATALOG[weapon].slot_cost
    slots_max = config.JET_FRAME_CATALOG["STRIKE_FIGHTER"].ordnance_slots
    overflow_count = (slots_max // slot_cost) + 1
    template = FriendlyTemplate(
        frame_type="STRIKE_FIGHTER",
        payload={weapon: overflow_count},
        count=1,
    )
    with pytest.raises(ValidationError) as exc_info:
        GenerationParameters(
            **_valid_params(friendly_mode="CUSTOM_ROSTER", custom_roster=[template])
        )
    message = str(exc_info.value)
    assert "ordnance slots" in message

    legal_count = slots_max // slot_cost
    legal = FriendlyTemplate(
        frame_type="STRIKE_FIGHTER",
        payload={weapon: legal_count},
        count=1,
    )
    GenerationParameters(
        **_valid_params(friendly_mode="CUSTOM_ROSTER", custom_roster=[legal])
    )


def test_rejects_negative_weapon_counts_in_roster():
    template = FriendlyTemplate(
        frame_type="STRIKE_FIGHTER",
        payload={"JDAM": -1},
        count=1,
    )
    with pytest.raises(ValidationError) as exc_info:
        GenerationParameters(
            **_valid_params(friendly_mode="CUSTOM_ROSTER", custom_roster=[template])
        )
    assert "negative" in str(exc_info.value).lower()


def test_rejects_impossible_map_margin_and_base_safety():
    with pytest.raises(ValidationError) as exc_info:
        GenerationParameters(
            **_valid_params(grid_width=100.0, grid_height=100.0, map_margin=48.0)
        )
    assert "map_margin" in str(exc_info.value)

    with pytest.raises(ValidationError) as exc_info:
        GenerationParameters(
            **_valid_params(
                grid_width=100.0,
                grid_height=100.0,
                map_margin=5.0,
                base_safety_radius=95.0,
            )
        )
    assert "base_safety_radius" in str(exc_info.value)


def test_seed_boundaries():
    GenerationParameters(seed=0)
    GenerationParameters(seed=2**64 - 1)
    with pytest.raises(ValidationError):
        GenerationParameters(seed=-1)
    with pytest.raises(ValidationError):
        GenerationParameters(seed=2**64)


def test_generation_parameters_and_metadata_json_round_trip():
    params = GenerationParameters(
        **_valid_params(
            friendly_mode="CUSTOM_ROSTER",
            custom_roster=[
                FriendlyTemplate(
                    frame_type="HEAVY_BOMBER",
                    payload={"JDAM": 4, "AMRAAM": 1, "GUN_POD": 1},
                    count=2,
                )
            ],
        )
    )
    restored = GenerationParameters.model_validate_json(params.model_dump_json())
    assert restored == params

    meta = GenerationMetadata(
        seed=params.seed,
        preset=params.preset,
        resolved_parameters=params,
        deliberate_cooperative_objective_ids=["Target_01"],
        modified_after_generation=True,
    )
    meta_restored = GenerationMetadata.model_validate_json(meta.model_dump_json())
    assert meta_restored == meta


def test_feasibility_report_json_round_trip():
    report = FeasibilityReport(
        feasible=False,
        issues=[
            FeasibilityIssue(
                severity="ERROR",
                code="INSUFFICIENT_DAMAGE",
                message="not enough",
                related_ids=["Target_A"],
            )
        ],
        total_damage_capacity_by_target_type={
            "HARDENED_BUNKER": 2,
            "MOBILE_RADAR": 0,
            "SAM_SITE": 1,
        },
        required_hp_by_target_type={
            "HARDENED_BUNKER": 5,
            "MOBILE_RADAR": 0,
            "SAM_SITE": 1,
        },
        unreachable_group_ids=["G1"],
        cooperative_objective_ids=["Target_A"],
        challenge_score=55.5,
        challenge_components={"objective_burden": 40.0},
    )
    restored = FeasibilityReport.model_validate_json(report.model_dump_json())
    assert restored == report


def test_existing_scenario_json_loads_with_generation_none():
    scenario = scenario_from_data(config.SCENARIO)
    assert scenario.generation is None
    default = _default_scenario()
    assert default.generation is None
    assert "generation" in default.model_dump()
    assert default.model_dump()["generation"] is None


def test_scenario_save_load_round_trips_typed_metadata(tmp_path):
    params = GenerationParameters(seed=99, preset="TRAINING", objective_count=2, sam_count=0)
    meta = GenerationMetadata(
        seed=99,
        preset="TRAINING",
        resolved_parameters=params,
        deliberate_cooperative_objective_ids=["Target_A"],
    )
    scenario = scenario_from_data(config.SCENARIO)
    scenario = scenario.model_copy(update={"generation": meta})
    path = tmp_path / "with_meta.json"
    path.write_text(scenario.model_dump_json(indent=2), encoding="utf-8")
    loaded = ScenarioConfig.model_validate_json(path.read_text(encoding="utf-8"))
    assert loaded.generation is not None
    assert loaded.generation.seed == 99
    assert loaded.generation.resolved_parameters.preset == "TRAINING"
    assert loaded.generation.deliberate_cooperative_objective_ids == ["Target_A"]
    assert loaded.jets == scenario.jets


def test_multi_error_messages_accumulate():
    with pytest.raises(ValidationError) as exc_info:
        GenerationParameters(
            **_valid_params(
                objective_hp_min=8,
                objective_hp_max=2,
                friendly_count_min=9,
                friendly_count_max=2,
                allowed_group_templates=[],
            )
        )
    message = str(exc_info.value)
    assert "objective_hp_min" in message
    assert "friendly_count_min" in message
    assert "allowed_group_templates" in message


def test_validators_do_not_mutate_input_defaults():
    first = GenerationParameters(seed=1)
    second = GenerationParameters(seed=2)
    first.allowed_group_templates.append("SAM_PAIR")
    assert "SAM_PAIR" not in second.allowed_group_templates
    # Deep-copy safety for callers holding template lists.
    templates = copy.deepcopy(first.allowed_group_templates)
    assert templates[-1] == "SAM_PAIR"
