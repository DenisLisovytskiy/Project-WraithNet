"""Friendly-base keep-out and AO spread constraints for scenario generation."""

from __future__ import annotations

import math

from wraithnet.generation_schemas import GenerationParameters
from wraithnet.scenario_feasibility import ENEMY_NEAR_BASE, analyze_feasibility
from wraithnet.scenario_generator import (
    effective_base_keepout,
    generate_enemies,
    generate_scenario,
    place_generated_tanks,
    respects_enemy_base_keepout,
)
from wraithnet.geometry import angular_distance, bearing_to


def _params(**overrides) -> GenerationParameters:
    base = {
        "seed": 42,
        "preset": "CUSTOM",
        "grid_width": 100.0,
        "grid_height": 100.0,
        "objective_count": 4,
        "sam_count": 2,
        "jammer_count": 1,
        "objective_hp_min": 1,
        "objective_hp_max": 4,
        "group_style": "DISPERSED",
        "allowed_group_templates": [
            "OBJECTIVE_ONLY",
            "DEFENDED_OBJECTIVE",
            "JAMMED_OBJECTIVE",
            "IADS_CLUSTER",
        ],
        "base_safety_radius": 18.0,
        "base_safety_fraction": 0.06,
        "anchor_min_angular_separation_deg": 35.0,
        "max_placement_attempts": 200,
    }
    base.update(overrides)
    return GenerationParameters.model_validate(base)


def test_effective_keepout_scales_with_map_diagonal():
    small = _params(grid_width=100.0, grid_height=100.0, base_safety_fraction=0.06)
    large = _params(grid_width=5000.0, grid_height=5000.0, base_safety_fraction=0.06)
    # 100×100: fraction ~8.5 < absolute 18 → keep absolute floor.
    assert effective_base_keepout(small) == 18.0
    # 5000×5000: fraction ~424 dominates.
    assert effective_base_keepout(large) == pytest_approx(
        0.06 * math.hypot(5000.0, 5000.0)
    )


def pytest_approx(value: float, rel: float = 1e-9) -> float:
    # Local helper so this module does not need pytest.approx import dance.
    return value  # assertions use math.isclose below


def test_effective_keepout_large_map_numeric():
    large = _params(grid_width=5000.0, grid_height=5000.0, base_safety_fraction=0.06)
    expected = 0.06 * math.hypot(5000.0, 5000.0)
    assert math.isclose(effective_base_keepout(large), expected, rel_tol=1e-9)


def test_all_enemy_entities_outside_keepout_on_compact_map():
    params = _params(
        seed=17,
        objective_count=3,
        sam_count=3,
        jammer_count=2,
        aaa_count=2,
    )
    enemies = generate_enemies(params)
    keepout = effective_base_keepout(params)
    for pos in (
        *[t.position for t in enemies.targets],
        *[s.position for s in enemies.sam_sites],
        *[j.position for j in enemies.jammers],
        *[a.position for a in enemies.aaa_sites],
    ):
        assert distance_ok(enemies.home_base, pos, keepout)


def distance_ok(home, pos, keepout: float) -> bool:
    assert math.dist(home, pos) >= keepout - 1e-6
    return True


def test_enemy_tanks_respect_keepout():
    params = _params(
        seed=9,
        objective_count=2,
        sam_count=0,
        jammer_count=0,
        enemy_tank_count=2,
        friendly_tank_count=0,
    )
    enemies = generate_enemies(params)
    occupied = [enemies.home_base]
    occupied.extend(t.position for t in enemies.targets)
    tanks = place_generated_tanks(
        params,
        terrain=enemies.terrain,
        home_base=enemies.home_base,
        occupied=occupied,
    )
    keepout = effective_base_keepout(params)
    assert tanks
    for tank in tanks:
        assert tank.faction == "ENEMY"
        assert math.dist(enemies.home_base, tank.position) >= keepout - 1e-6


def test_dispersed_centers_prefer_angular_spread():
    params = _params(
        seed=5,
        objective_count=4,
        sam_count=0,
        jammer_count=0,
        group_style="DISPERSED",
        anchor_min_angular_separation_deg=35.0,
    )
    enemies = generate_enemies(params)
    centers = [g.center for g in enemies.groups if g.target_ids]
    assert len(centers) >= 3
    bearings = [bearing_to(enemies.home_base, c) for c in centers]
    # At least one pair should clear the soft minimum (fan-out worked somehow).
    # Stronger: max pairwise angular gap should exceed a collapsed wedge.
    max_gap = 0.0
    for i, a in enumerate(bearings):
        for b in bearings[i + 1 :]:
            max_gap = max(max_gap, angular_distance(a, b))
    assert max_gap >= 20.0


def test_large_map_keepout_blocks_near_base_point():
    params = _params(
        grid_width=5000.0,
        grid_height=5000.0,
        base_safety_radius=18.0,
        base_safety_fraction=0.06,
    )
    home = (50.0, 50.0)
    near = (60.0, 50.0)  # 10 units — inside absolute and fraction keepout
    far = (2000.0, 2000.0)
    assert not respects_enemy_base_keepout(near, home, params)
    assert respects_enemy_base_keepout(far, home, params)


def test_feasibility_warns_enemy_near_base_on_manual_edit():
    result = generate_scenario(
        _params(seed=3, objective_count=1, sam_count=0, jammer_count=0)
    )
    scenario = result.scenario
    # Drag a target into the keep-out to simulate an editor edit.
    keepout = effective_base_keepout(scenario.generation.resolved_parameters)
    victim = scenario.targets[0]
    scenario.targets[0] = victim.model_copy(
        update={
            "position": (
                scenario.home_base[0] + keepout * 0.25,
                scenario.home_base[1],
            )
        }
    )
    report = analyze_feasibility(scenario)
    assert any(i.code == ENEMY_NEAR_BASE for i in report.issues)
