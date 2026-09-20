"""Map size parameter and fuel reach on large generated battlespaces."""

from __future__ import annotations

import math

from wraithnet import config
from wraithnet.generation_presets import parameters_for_preset
from wraithnet.generation_schemas import GenerationParameters
from wraithnet.scenario_feasibility import approximate_fuel_range, round_trip_fuel_required
from wraithnet.scenario_generator import generate_scenario, terrain_cell_size_for_grid


def test_generation_defaults_to_twenty_x_poc_map():
    params = GenerationParameters(seed=1)
    assert params.grid_width == config.DEFAULT_GENERATION_GRID_WIDTH
    assert params.grid_height == config.DEFAULT_GENERATION_GRID_HEIGHT
    assert params.grid_width == config.GRID_WIDTH * 20.0


def test_preset_carries_generation_grid_size():
    params = parameters_for_preset("STANDARD", seed=3)
    assert params.grid_width == config.DEFAULT_GENERATION_GRID_WIDTH
    assert params.grid_height == config.DEFAULT_GENERATION_GRID_HEIGHT


def test_generate_scenario_honors_custom_grid_size():
    params = GenerationParameters(seed=11, grid_width=400.0, grid_height=300.0)
    result = generate_scenario(params)
    assert result.scenario.grid_width == 400.0
    assert result.scenario.grid_height == 300.0
    assert result.scenario.terrain.world_width == 400.0
    assert result.scenario.terrain.world_height == 300.0
    assert result.scenario.terrain.cell_size == terrain_cell_size_for_grid(400.0, 300.0)


def test_default_generated_map_places_ao_beyond_sensor():
    """Large default maps must not clamp the AO into SENSOR_RADIUS of home."""
    result = generate_scenario(parameters_for_preset("TRAINING", seed=19))
    home = result.scenario.home_base
    assert result.scenario.grid_width == config.DEFAULT_GENERATION_GRID_WIDTH
    farthest = max(
        math.dist(home, t.position) for t in result.scenario.targets
    )
    assert farthest > config.SENSOR_RADIUS


def test_strike_fuel_covers_far_ao_round_trip_on_default_map():
    result = generate_scenario(parameters_for_preset("STANDARD", seed=23))
    home = result.scenario.home_base
    capacity = config.JET_FRAME_CATALOG["STRIKE_FIGHTER"].fuel_capacity
    for target in result.scenario.targets:
        required = round_trip_fuel_required(
            "STRIKE_FIGHTER", home, target.position
        )
        assert required <= capacity
    assert approximate_fuel_range("STRIKE_FIGHTER") >= config.DEFAULT_GENERATION_GRID_WIDTH
