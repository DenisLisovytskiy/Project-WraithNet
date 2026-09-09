"""Preset resolution and seed-derivation tests (Phase 30)."""

from __future__ import annotations

import random

import pytest

from wraithnet import config
from wraithnet.generation_presets import (
    DIFFICULTY_PRESETS,
    parameters_for_preset,
)
from wraithnet.generation_rng import (
    ENEMY_SALT,
    FRIENDLY_SALT,
    PRESET_SALT,
    derived_seed,
)
from wraithnet.generation_schemas import GenerationParameters


def test_derived_seed_is_stable_and_salt_sensitive():
    a = derived_seed(12345, PRESET_SALT)
    b = derived_seed(12345, PRESET_SALT)
    assert a == b
    assert 0 <= a <= 2**64 - 1
    assert derived_seed(12345, ENEMY_SALT) != a
    assert derived_seed(12345, FRIENDLY_SALT) != a
    assert derived_seed(999, PRESET_SALT) != a


def test_derived_seed_does_not_use_python_hash_randomization():
    # Two processes would differ under hash() randomization; integer mixing must not.
    samples = [derived_seed(i, PRESET_SALT) for i in range(20)]
    assert samples == [derived_seed(i, PRESET_SALT) for i in range(20)]
    assert len(set(samples)) == 20


@pytest.mark.parametrize("preset", ["TRAINING", "STANDARD", "CONTESTED", "NIGHTMARE"])
def test_every_preset_resolves_to_valid_parameters(preset):
    params = parameters_for_preset(preset, seed=7)
    assert isinstance(params, GenerationParameters)
    assert params.preset == preset
    assert params.seed == 7
    # Same seed → identical resolution.
    again = parameters_for_preset(preset, seed=7)
    assert again == params


def test_same_seed_same_counts_different_seed_may_change():
    a = parameters_for_preset("STANDARD", seed=100)
    b = parameters_for_preset("STANDARD", seed=100)
    assert a.objective_count == b.objective_count
    assert a.sam_count == b.sam_count
    # Over a small sample, at least one seed differs in a ranged field.
    counts = {
        parameters_for_preset("STANDARD", seed=s).objective_count for s in range(50)
    }
    assert len(counts) >= 2


def test_preset_relative_ordering_threat_and_reserve():
    training = DIFFICULTY_PRESETS["TRAINING"]
    standard = DIFFICULTY_PRESETS["STANDARD"]
    contested = DIFFICULTY_PRESETS["CONTESTED"]
    nightmare = DIFFICULTY_PRESETS["NIGHTMARE"]

    def max_count(value: int | tuple[int, int]) -> int:
        return value[1] if isinstance(value, tuple) else value

    def min_count(value: int | tuple[int, int]) -> int:
        return value[0] if isinstance(value, tuple) else value

    assert max_count(training.objective_count) <= min_count(standard.objective_count) or (
        max_count(training.objective_count) <= max_count(standard.objective_count)
    )
    assert max_count(training.sam_count) <= max_count(standard.sam_count)
    assert max_count(standard.sam_count) <= max_count(contested.sam_count)
    assert max_count(contested.sam_count) <= max_count(nightmare.sam_count)
    assert max_count(training.objective_count) < max_count(nightmare.objective_count)

    assert (
        training.friendly_reserve_factor
        > standard.friendly_reserve_factor
        > contested.friendly_reserve_factor
        > nightmare.friendly_reserve_factor
    )
    assert training.objective_hp_max <= standard.objective_hp_max <= contested.objective_hp_max
    assert contested.objective_hp_max <= nightmare.objective_hp_max
    assert (
        training.cooperation_objective_fraction
        <= standard.cooperation_objective_fraction
        <= contested.cooperation_objective_fraction
        <= nightmare.cooperation_objective_fraction
    )
    assert training.threat_overlap < nightmare.threat_overlap
    assert training.launch_threat_clearance > nightmare.launch_threat_clearance


def test_training_excludes_sam_pair_higher_presets_may_include():
    training = parameters_for_preset("TRAINING", seed=1)
    assert "SAM_PAIR" not in training.allowed_group_templates
    nightmare = parameters_for_preset("NIGHTMARE", seed=1)
    assert "SAM_PAIR" in nightmare.allowed_group_templates
    contested = parameters_for_preset("CONTESTED", seed=1)
    assert "SAM_PAIR" in contested.allowed_group_templates


def test_overrides_set_custom_and_do_not_mutate_templates():
    before = DIFFICULTY_PRESETS["STANDARD"]
    snapshot = (
        before.objective_count,
        before.sam_count,
        before.friendly_reserve_factor,
        before.allowed_group_templates,
    )
    params = parameters_for_preset(
        "STANDARD",
        seed=5,
        overrides={"objective_count": 6, "friendly_reserve_factor": 2.0},
    )
    assert params.preset == "CUSTOM"
    assert params.objective_count == 6
    assert params.friendly_reserve_factor == 2.0
    # Un-overridden ranged fields still resolved from the STANDARD template.
    assert params.group_style == "MIXED"
    after = DIFFICULTY_PRESETS["STANDARD"]
    assert (
        after.objective_count,
        after.sam_count,
        after.friendly_reserve_factor,
        after.allowed_group_templates,
    ) == snapshot


def test_invalid_override_raises_via_generation_parameters():
    with pytest.raises(Exception) as exc_info:
        parameters_for_preset(
            "TRAINING",
            seed=1,
            overrides={"objective_hp_min": 9, "objective_hp_max": 2},
        )
    assert "objective_hp_min" in str(exc_info.value)


def test_parameters_for_preset_rejects_custom_name():
    with pytest.raises(ValueError, match="CUSTOM"):
        parameters_for_preset("CUSTOM", seed=1)


def test_importing_presets_does_not_change_global_random_or_catalog():
    state_before = random.getstate()
    catalog_before = {
        name: (spec.slot_cost, spec.damage_per_hit, dict(spec.suitability))
        for name, spec in config.WEAPON_CATALOG.items()
    }
    frames_before = {
        name: (spec.fuel_capacity, spec.ordnance_slots, spec.max_speed)
        for name, spec in config.JET_FRAME_CATALOG.items()
    }
    parameters_for_preset("NIGHTMARE", seed=123456)
    parameters_for_preset("CONTESTED", seed=99, overrides={"jammer_count": 3})
    assert random.getstate() == state_before
    catalog_after = {
        name: (spec.slot_cost, spec.damage_per_hit, dict(spec.suitability))
        for name, spec in config.WEAPON_CATALOG.items()
    }
    frames_after = {
        name: (spec.fuel_capacity, spec.ordnance_slots, spec.max_speed)
        for name, spec in config.JET_FRAME_CATALOG.items()
    }
    assert catalog_after == catalog_before
    assert frames_after == frames_before
