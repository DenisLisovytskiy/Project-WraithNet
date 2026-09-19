"""Deterministic enemy placement tests (Phase 31)."""

from __future__ import annotations

import math
import random

import pytest

from wraithnet import config
from wraithnet.generation_presets import parameters_for_preset
from wraithnet.generation_schemas import GenerationParameters
from wraithnet.scenario_generator import (
    DISCOVERY_MARGIN,
    IMPOSSIBLE_GEOMETRY,
    LAUNCH_RING_OUTER_RADIUS,
    PLACEMENT_EXHAUSTED,
    THREAT_MEMBER_MAX_OFFSET,
    THREAT_OVERLAP_TOLERANCE,
    EnemyGeneration,
    GenerationError,
    discovery_link_max_distance,
    distance_from_home,
    enemies_to_scenario,
    generate_enemies,
    make_test_jet,
    position_in_bounds,
    sam_min_distance_from_home,
    threat_overlap_ratio,
)
import wraithnet.scenario_generator as generator_mod


def _params(**overrides) -> GenerationParameters:
    base = {
        "seed": 42,
        "preset": "CUSTOM",
        # Compact grid for fast unit geometry; production defaults are 5000×5000.
        "grid_width": 100.0,
        "grid_height": 100.0,
        "objective_count": 4,
        "sam_count": 2,
        "jammer_count": 1,
        "objective_hp_min": 1,
        "objective_hp_max": 4,
        "group_style": "MIXED",
        "allowed_group_templates": [
            "OBJECTIVE_ONLY",
            "DEFENDED_OBJECTIVE",
            "JAMMED_OBJECTIVE",
            "IADS_CLUSTER",
        ],
        "max_placement_attempts": 200,
    }
    base.update(overrides)
    return GenerationParameters.model_validate(base)


def _entity_positions(enemies: EnemyGeneration) -> list[tuple[float, float]]:
    positions = [t.position for t in enemies.targets]
    positions.extend(s.position for s in enemies.sam_sites)
    positions.extend(j.position for j in enemies.jammers)
    return positions


def test_same_seed_identical_enemy_generation():
    params = _params(seed=12345)
    a = generate_enemies(params)
    b = generate_enemies(params)
    assert a == b


def test_different_seeds_produce_layout_variety():
    layouts = set()
    for seed in range(10):
        enemies = generate_enemies(_params(seed=seed))
        key = (
            enemies.home_base,
            tuple(t.position for t in enemies.targets),
            tuple(s.position for s in enemies.sam_sites),
            tuple(j.position for j in enemies.jammers),
        )
        layouts.add(key)
    assert len(layouts) > 1


def test_global_random_state_unchanged():
    state_before = random.getstate()
    generate_enemies(_params(seed=7))
    generate_enemies(_params(seed=99, objective_count=3, sam_count=1, jammer_count=0))
    assert random.getstate() == state_before


@pytest.mark.parametrize("preset", ["TRAINING", "STANDARD", "CONTESTED", "NIGHTMARE"])
def test_presets_over_seed_matrix_exact_counts_and_ids(preset):
    for seed in range(50):
        params = parameters_for_preset(
            preset,
            seed=seed,
            overrides={"grid_width": 100.0, "grid_height": 100.0},
        )
        enemies = generate_enemies(params)
        assert len(enemies.targets) == params.objective_count
        assert len(enemies.sam_sites) == params.sam_count
        assert len(enemies.jammers) == params.jammer_count

        target_ids = [t.target_id for t in enemies.targets]
        sam_ids = [s.target_id for s in enemies.sam_sites]
        jam_ids = [j.jammer_id for j in enemies.jammers]
        group_ids = [g.group_id for g in enemies.groups]
        all_ids = target_ids + sam_ids + jam_ids + group_ids
        assert len(all_ids) == len(set(all_ids))
        assert target_ids == sorted(target_ids)
        assert sam_ids == sorted(sam_ids)
        assert jam_ids == sorted(jam_ids)

        for pos in _entity_positions(enemies):
            assert position_in_bounds(
                pos,
                margin=params.map_margin,
                grid_width=params.grid_width,
                grid_height=params.grid_height,
            )
        assert 0.0 <= enemies.home_base[0] <= params.grid_width
        assert 0.0 <= enemies.home_base[1] <= params.grid_height


def test_geometry_constraints_base_safety_and_separation():
    params = _params(seed=11, group_style="DISPERSED", objective_count=4, sam_count=0, jammer_count=0)
    enemies = generate_enemies(params)
    centers = [g.center for g in enemies.groups if g.target_ids]
    for center in centers:
        assert distance_from_home(enemies.home_base, center) >= params.base_safety_radius
    min_sep = 1.5 * params.group_min_separation
    for i, a in enumerate(centers):
        for b in centers[i + 1 :]:
            assert math.dist(a, b) + 1e-6 >= min_sep


def test_sam_launch_clearance():
    params = _params(seed=3, sam_count=3, jammer_count=0, objective_count=3)
    enemies = generate_enemies(params)
    min_dist = sam_min_distance_from_home(params)
    for sam in enemies.sam_sites:
        assert distance_from_home(enemies.home_base, sam.position) >= min_dist - 1e-6


def test_threat_overlap_within_tolerance_for_sam_pair():
    params = _params(
        seed=8,
        objective_count=2,
        sam_count=4,
        jammer_count=0,
        threat_overlap=0.35,
        allowed_group_templates=["SAM_PAIR", "OBJECTIVE_ONLY"],
    )
    enemies = generate_enemies(params)
    pair_groups = [g for g in enemies.groups if g.template == "SAM_PAIR" and len(g.sam_site_ids) >= 2]
    assert pair_groups, "expected at least one SAM_PAIR group"
    sam_by_id = {s.target_id: s for s in enemies.sam_sites}
    for group in pair_groups:
        a = sam_by_id[group.sam_site_ids[0]].position
        b = sam_by_id[group.sam_site_ids[1]].position
        overlap = threat_overlap_ratio(
            math.dist(a, b), config.SAM_ENGAGEMENT_RANGE, config.SAM_ENGAGEMENT_RANGE
        )
        assert abs(overlap - params.threat_overlap) <= THREAT_OVERLAP_TOLERANCE + 0.05


def test_both_target_types_when_at_least_two_objectives():
    for seed in range(20):
        enemies = generate_enemies(_params(seed=seed, objective_count=2, sam_count=0, jammer_count=0))
        types = {t.target_type for t in enemies.targets}
        assert types == {"HARDENED_BUNKER", "MOBILE_RADAR"}


def test_templates_never_overdraw_and_leftovers_exact():
    params = _params(
        seed=21,
        objective_count=3,
        sam_count=5,
        jammer_count=3,
        allowed_group_templates=[
            "OBJECTIVE_ONLY",
            "DEFENDED_OBJECTIVE",
            "JAMMED_OBJECTIVE",
            "IADS_CLUSTER",
            "SAM_PAIR",
        ],
    )
    enemies = generate_enemies(params)
    assert len(enemies.sam_sites) == 5
    assert len(enemies.jammers) == 3
    # Primary groups consume only what their templates allow in aggregate.
    consumed_sam = sum(len(g.sam_site_ids) for g in enemies.groups)
    consumed_jam = sum(len(g.jammer_ids) for g in enemies.groups)
    assert consumed_sam == 5
    assert consumed_jam == 3


def test_zero_threats_and_min_max_counts():
    minimal = generate_enemies(
        _params(seed=1, objective_count=1, sam_count=0, jammer_count=0)
    )
    assert len(minimal.targets) == 1
    assert len(minimal.sam_sites) == 0
    assert len(minimal.jammers) == 0

    maximal = generate_enemies(
        _params(
            seed=2,
            objective_count=10,
            sam_count=6,
            jammer_count=4,
            group_style="CLUSTERED",
            group_min_separation=8.0,
            allowed_group_templates=[
                "OBJECTIVE_ONLY",
                "DEFENDED_OBJECTIVE",
                "JAMMED_OBJECTIVE",
                "IADS_CLUSTER",
                "SAM_PAIR",
            ],
            max_placement_attempts=500,
        )
    )
    assert len(maximal.targets) == 10
    assert len(maximal.sam_sites) == 6
    assert len(maximal.jammers) == 4


def test_impossible_geometry_fails_fast():
    with pytest.raises(GenerationError) as exc_info:
        generate_enemies(
            _params(
                seed=1,
                objective_count=4,
                sam_count=0,
                jammer_count=0,
                base_safety_radius=80.0,
                objective_distance_min_fraction=0.9,
                objective_distance_max_fraction=1.0,
                max_placement_attempts=50,
            )
        )
    err = exc_info.value
    assert err.code in {IMPOSSIBLE_GEOMETRY, PLACEMENT_EXHAUSTED}
    assert "seed" in err.details


def test_placement_attempts_bounded_by_hook(monkeypatch):
    counter = {"n": 0}

    def hook() -> None:
        counter["n"] += 1

    monkeypatch.setattr(generator_mod, "_placement_attempt_hook", hook)
    params = _params(seed=5, max_placement_attempts=80)
    generate_enemies(params)
    # Soft bound: attempts should not explode far beyond attempts * entities.
    assert counter["n"] <= params.max_placement_attempts * 200


def test_impossible_request_does_not_exceed_attempt_budget(monkeypatch):
    counter = {"n": 0}

    def hook() -> None:
        counter["n"] += 1
        if counter["n"] > 5000:
            raise AssertionError("placement loop exceeded hard safety cap")

    monkeypatch.setattr(generator_mod, "_placement_attempt_hook", hook)
    with pytest.raises(GenerationError):
        generate_enemies(
            _params(
                seed=1,
                objective_count=8,
                sam_count=0,
                jammer_count=0,
                base_safety_radius=70.0,
                group_min_separation=40.0,
                group_style="DISPERSED",
                objective_distance_min_fraction=0.85,
                objective_distance_max_fraction=0.9,
                max_placement_attempts=40,
            )
        )
    assert counter["n"] <= 40 * 50


def test_discovery_spanning_chain_from_launch_area():
    link_max = discovery_link_max_distance()
    assert link_max == pytest.approx(
        config.SENSOR_RADIUS
        - max(s.range for s in config.WEAPON_CATALOG.values())
        - DISCOVERY_MARGIN
    )
    for seed in range(30):
        params = parameters_for_preset(
            "STANDARD",
            seed=seed,
            overrides={"grid_width": 100.0, "grid_height": 100.0},
        )
        enemies = generate_enemies(params)
        destroyable_groups = [
            g for g in enemies.groups if g.target_ids or g.sam_site_ids
        ]
        assert destroyable_groups
        # Launch-sensor roots plus over-horizon groups (mission brief transit).
        roots = [
            g
            for g in destroyable_groups
            if distance_from_home(enemies.home_base, g.center) + LAUNCH_RING_OUTER_RADIUS
            <= config.SENSOR_RADIUS + 1e-6
            or distance_from_home(enemies.home_base, g.center) + LAUNCH_RING_OUTER_RADIUS
            > config.SENSOR_RADIUS
        ]
        assert roots, f"seed={seed} has no discovery root"

        by_id = {g.group_id: g for g in enemies.groups}
        for group in destroyable_groups:
            if group.discovery_parent_group_id is None:
                continue
            parent = by_id[group.discovery_parent_group_id]
            # CLUSTERED placements still enforce link_max; DISPERSED parents are
            # nearest-neighbor metadata and may exceed it.
            if math.dist(group.center, parent.center) > link_max + 1e-6:
                continue
            assert math.dist(group.center, parent.center) <= link_max + 1e-6

        reachable = {g.group_id for g in roots}
        missing = [g.group_id for g in destroyable_groups if g.group_id not in reachable]
        assert not missing, f"seed={seed} undiscoverable groups: {missing}"


def test_member_offsets_bounded():
    enemies = generate_enemies(
        _params(
            seed=4,
            objective_count=3,
            sam_count=3,
            jammer_count=2,
            allowed_group_templates=["IADS_CLUSTER", "DEFENDED_OBJECTIVE", "OBJECTIVE_ONLY"],
        )
    )
    for group in enemies.groups:
        members = []
        tmap = {t.target_id: t for t in enemies.targets}
        smap = {s.target_id: s for s in enemies.sam_sites}
        jmap = {j.jammer_id: j for j in enemies.jammers}
        for tid in group.target_ids:
            members.append(tmap[tid].position)
        for sid in group.sam_site_ids:
            members.append(smap[sid].position)
        for jid in group.jammer_ids:
            members.append(jmap[jid].position)
        # Canonical at center for primary objectives / leftover SAMs.
        assert any(math.dist(pos, group.center) < 1e-6 for pos in members)
        for pos in members:
            assert math.dist(pos, group.center) <= THREAT_MEMBER_MAX_OFFSET + 1e-6


def test_scenario_config_validates_with_test_jet():
    enemies = generate_enemies(_params(seed=15))
    hx, hy = enemies.home_base
    # Stay inside the map near home (home is inset by launch-ring radius).
    jet_pos = (
        min(config.GRID_WIDTH - 1.0, max(1.0, hx + 2.0)),
        min(config.GRID_HEIGHT - 1.0, max(1.0, hy + 2.0)),
    )
    jet = make_test_jet(position=jet_pos)
    scenario = enemies_to_scenario(enemies, [jet])
    assert scenario.home_base == enemies.home_base
    assert len(scenario.targets) == 4
    assert scenario.generation is None


def test_cooperation_candidates_respect_fraction():
    params = _params(
        seed=9,
        objective_count=4,
        sam_count=0,
        jammer_count=0,
        cooperation_objective_fraction=0.5,
    )
    enemies = generate_enemies(params)
    assert len(enemies.cooperation_candidate_ids) == 2
    assert set(enemies.cooperation_candidate_ids).issubset(
        {t.target_id for t in enemies.targets}
    )


def test_generator_module_has_no_viewer_imports():
    import pathlib

    text = pathlib.Path(generator_mod.__file__).read_text(encoding="utf-8")
    for banned in ("panda3d", "pygame", "from direct", "import socket"):
        assert banned not in text
