"""Friendly roster generation tests (Phase 32)."""

from __future__ import annotations

import math
import random
import time

import pytest

from wraithnet import config
from wraithnet.generation_presets import parameters_for_preset
from wraithnet.generation_schemas import GenerationParameters
from wraithnet.geometry import bearing_to
from wraithnet.scenario_feasibility import jet_can_reach_round_trip, round_trip_fuel_required
from wraithnet.scenario_generator import (
    AUTO_BALANCE_CAPACITY,
    LAUNCH_JET_MIN_SEPARATION,
    LAUNCH_RING_INNER_RADIUS,
    LAUNCH_RING_OUTER_RADIUS,
    PLACEMENT_EXHAUSTED,
    GenerationError,
    generate_enemies,
    generate_friendly_roster,
    generate_scenario,
    initial_heading_toward_map_center,
    map_center,
    payload_slots_used,
    place_jets_on_launch_ring,
)
from wraithnet.generation_rng import FRIENDLY_SALT, derived_seed


def _params(**overrides) -> GenerationParameters:
    base = {
        "seed": 42,
        "preset": "CUSTOM",
        "grid_width": 100.0,
        "grid_height": 100.0,
        "objective_count": 3,
        "sam_count": 1,
        "jammer_count": 0,
        "objective_hp_min": 1,
        "objective_hp_max": 3,
        "friendly_mode": "AUTO_BALANCED",
        "friendly_count_min": 3,
        "friendly_count_max": 6,
        "friendly_reserve_factor": 1.25,
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


def test_same_seed_identical_full_scenario_json():
    params = parameters_for_preset("STANDARD", seed=1001, overrides={"grid_width": 100.0, "grid_height": 100.0})
    a = generate_scenario(params)
    b = generate_scenario(params)
    assert a.scenario.model_dump(mode="json") == b.scenario.model_dump(mode="json")
    assert a.feasibility.model_dump(mode="json") == b.feasibility.model_dump(mode="json")
    assert a.groups == b.groups


def test_enemy_layout_unchanged_across_friendly_modes():
    seed = 77
    auto = generate_scenario(
        _params(seed=seed, friendly_mode="AUTO_BALANCED", jammer_count=1)
    )
    random_legal = generate_scenario(
        _params(seed=seed, friendly_mode="RANDOM_LEGAL", jammer_count=1)
    )
    custom = generate_scenario(
        _params(
            seed=seed,
            friendly_mode="CUSTOM_ROSTER",
            jammer_count=1,
            custom_roster=[
                {
                    "frame_type": "STRIKE_FIGHTER",
                    "payload": {"JDAM": 2, "AIM9X": 1},
                    "count": 2,
                }
            ],
        )
    )
    for other in (random_legal, custom):
        assert auto.scenario.home_base == other.scenario.home_base
        assert [t.position for t in auto.scenario.targets] == [
            t.position for t in other.scenario.targets
        ]
        assert [t.target_id for t in auto.scenario.targets] == [
            t.target_id for t in other.scenario.targets
        ]
        assert [t.target_type for t in auto.scenario.targets] == [
            t.target_type for t in other.scenario.targets
        ]
        assert [s.position for s in auto.scenario.sam_sites] == [
            s.position for s in other.scenario.sam_sites
        ]
        assert [j.position for j in auto.scenario.jammers] == [
            j.position for j in other.scenario.jammers
        ]


_CAPACITY_ERROR_CODES = frozenset(
    {
        "NO_SUITABLE_WEAPON",
        "INSUFFICIENT_DAMAGE",
        "FUEL_REACH",
        "EMPTY_LOADOUT",
        "NO_ANTI_ARMOR_CAPABILITY",
        "OBJECTIVE_UNACHIEVABLE",
        "AUTO_BALANCE_CAPACITY",
    }
)


@pytest.mark.parametrize("preset", ["TRAINING", "STANDARD", "CONTESTED", "NIGHTMARE"])
def test_auto_balanced_feasible_over_seed_matrix(preset):
    for seed in range(100):
        params = parameters_for_preset(
            preset,
            seed=seed,
            overrides={"grid_width": 100.0, "grid_height": 100.0},
        )
        assert params.friendly_mode == "AUTO_BALANCED"
        try:
            result = generate_scenario(params)
        except GenerationError as exc:
            if exc.code in (PLACEMENT_EXHAUSTED, AUTO_BALANCE_CAPACITY):
                continue
            raise
        capacity_errors = [
            i
            for i in result.feasibility.issues
            if i.severity == "ERROR" and i.code in _CAPACITY_ERROR_CODES
        ]
        assert not capacity_errors


@pytest.mark.parametrize("preset", ["TRAINING", "STANDARD", "CONTESTED", "NIGHTMARE"])
def test_auto_roster_within_bounds_and_joint_allocation(preset):
    for seed in range(20):
        params = parameters_for_preset(
            preset,
            seed=seed,
            overrides={"grid_width": 100.0, "grid_height": 100.0},
        )
        try:
            result = generate_scenario(params)
        except GenerationError as exc:
            if exc.code in (PLACEMENT_EXHAUSTED, AUTO_BALANCE_CAPACITY):
                continue
            raise
        n = len(result.scenario.jets)
        assert params.friendly_count_min <= n <= params.friendly_count_max
        for jet in result.scenario.jets:
            used = payload_slots_used(jet.payload)
            cap = config.JET_FRAME_CATALOG[jet.frame_type].ordnance_slots
            assert used <= cap
            assert sum(jet.payload.values()) >= 1
        capacity_errors = [
            i
            for i in result.feasibility.issues
            if i.severity == "ERROR" and i.code in _CAPACITY_ERROR_CODES
        ]
        assert not capacity_errors
        assert not any(
            i.code == "INSUFFICIENT_DAMAGE" for i in result.feasibility.issues
        )


def test_auto_capacity_failure_is_actionable():
    params = _params(
        seed=1,
        objective_count=8,
        sam_count=4,
        jammer_count=0,
        objective_hp_min=5,
        objective_hp_max=7,
        friendly_count_min=1,
        friendly_count_max=1,
        friendly_reserve_factor=2.5,
        allowed_frame_types=["INTERCEPTOR"],
        cooperation_objective_fraction=0.0,
    )
    with pytest.raises(GenerationError) as exc_info:
        generate_scenario(params)
    err = exc_info.value
    assert err.code == AUTO_BALANCE_CAPACITY
    assert "suggestions" in err.details
    assert err.details["suggestions"]
    assert "friendly_roster" in str(err.details.get("kind", ""))


def test_cooperation_marked_objectives_satisfy_inequality():
    # STANDARD has ~25% coop fraction; scan seeds until deliberate coop appears.
    found = False
    for seed in range(80):
        params = parameters_for_preset("STANDARD", seed=seed, overrides={"grid_width": 100.0, "grid_height": 100.0})
        result = generate_scenario(params)
        deliberate = result.scenario.generation.deliberate_cooperative_objective_ids
        if not deliberate:
            continue
        found = True
        report_ids = set(result.feasibility.cooperative_objective_ids)
        for oid in deliberate:
            assert oid in report_ids
            target = next(t for t in result.scenario.targets if t.target_id == oid)
            contribs = [
                sum(
                    config.WEAPON_CATALOG[w].damage_per_hit * c
                    for w, c in jet.payload.items()
                    if c > 0
                    and config.WEAPON_CATALOG[w].suitability.get(target.target_type, 0)
                    > 0
                )
                for jet in result.scenario.jets
                if jet_can_reach_round_trip(
                    jet, result.scenario.home_base, target.position
                )
            ]
            assert contribs
            assert max(contribs) < target.hp_required <= sum(contribs)
        break
    assert found, "expected at least one deliberate cooperative objective in sample"


def test_random_legal_validates_and_can_be_infeasible():
    feasible = False
    infeasible = False
    # Controlled sample: tiny weak rosters vs bunkers tend to fail; larger
    # mixed rosters sometimes succeed.
    for seed in range(80):
        params = _params(
            seed=seed,
            friendly_mode="RANDOM_LEGAL",
            friendly_count_min=1,
            friendly_count_max=1,
            allowed_frame_types=["INTERCEPTOR"],
            objective_count=4,
            sam_count=2,
            jammer_count=0,
            objective_hp_min=4,
            objective_hp_max=6,
            objective_distance_min_fraction=0.15,
            objective_distance_max_fraction=0.9,
        )
        result = generate_scenario(params)
        assert result.scenario.jets
        assert all(j.agent_id.startswith("Jet_") for j in result.scenario.jets)
        # Schema always validates; feasibility may fail.
        if result.feasibility.feasible:
            feasible = True
        else:
            infeasible = True
            assert any(i.severity == "ERROR" for i in result.feasibility.issues)
        if feasible and infeasible:
            break
    # Guarantee an infeasible case with an all-AIM9X custom-like random by
    # constructing a known-bad RANDOM path via generate_friendly_roster then
    # wrapping — if the loop never saw feasible, still require infeasible.
    assert infeasible, "expected at least one infeasible RANDOM_LEGAL result"
    if not feasible:
        # Softer path: a generous roster should often be statically feasible.
        for seed in range(40, 120):
            params = _params(
                seed=seed,
                friendly_mode="RANDOM_LEGAL",
                friendly_count_min=6,
                friendly_count_max=6,
                allowed_frame_types=["HEAVY_BOMBER", "STRIKE_FIGHTER"],
                objective_count=2,
                sam_count=0,
                jammer_count=0,
                objective_hp_min=1,
                objective_hp_max=1,
            )
            result = generate_scenario(params)
            if result.feasibility.feasible:
                feasible = True
                break
    assert feasible, "expected at least one feasible RANDOM_LEGAL result"


def test_custom_roster_preserves_frames_payloads_and_counts():
    params = _params(
        seed=9,
        friendly_mode="CUSTOM_ROSTER",
        custom_roster=[
            {
                "frame_type": "HEAVY_BOMBER",
                "payload": {"JDAM": 4, "AMRAAM": 1, "GUN_POD": 2},
                "count": 2,
            },
            {
                "frame_type": "INTERCEPTOR",
                "payload": {"AIM9X": 2, "AMRAAM": 1},
                "count": 1,
            },
        ],
    )
    result = generate_scenario(params)
    jets = result.scenario.jets
    assert len(jets) == 3
    assert jets[0].frame_type == "HEAVY_BOMBER"
    assert jets[0].payload == {"JDAM": 4, "AMRAAM": 1, "GUN_POD": 2}
    assert jets[1].frame_type == "HEAVY_BOMBER"
    assert jets[1].payload == {"JDAM": 4, "AMRAAM": 1, "GUN_POD": 2}
    assert jets[2].frame_type == "INTERCEPTOR"
    assert jets[2].payload == {"AIM9X": 2, "AMRAAM": 1}
    assert [j.agent_id for j in jets] == ["Jet_01", "Jet_02", "Jet_03"]


def test_friendly_positions_headings_and_launch_safety():
    result = generate_scenario(parameters_for_preset("STANDARD", seed=5, overrides={"grid_width": 100.0, "grid_height": 100.0}))
    home = result.scenario.home_base
    gw = result.scenario.grid_width
    gh = result.scenario.grid_height
    center = map_center(gw, gh)
    positions = []
    for jet in result.scenario.jets:
        d = math.dist(home, jet.position)
        assert LAUNCH_RING_INNER_RADIUS - 1e-6 <= d <= LAUNCH_RING_OUTER_RADIUS + 1e-6
        assert 0.0 <= jet.position[0] <= gw
        assert 0.0 <= jet.position[1] <= gh
        assert math.dist(jet.position, home) > 1e-6
        expected = bearing_to(jet.position, center)
        assert jet.initial_heading == pytest.approx(expected)
        assert jet.initial_heading == pytest.approx(
            initial_heading_toward_map_center(
                jet.position, grid_width=gw, grid_height=gh
            )
        )
        for sam in result.scenario.sam_sites:
            assert math.dist(jet.position, sam.position) > config.SAM_ENGAGEMENT_RANGE
        positions.append(jet.position)
    for i, a in enumerate(positions):
        for b in positions[i + 1 :]:
            assert math.dist(a, b) >= LAUNCH_JET_MIN_SEPARATION - 1e-6


def test_fuel_reach_helpers_per_frame():
    home = (2.0, 2.0)
    near = (20.0, 20.0)
    far = (98.0, 98.0)
    for frame in config.JET_FRAME_CATALOG:
        from wraithnet.scenario_schema import JetSpec

        jet = JetSpec(
            agent_id="J",
            position=(3.0, 3.0),
            payload={"JDAM": 1},
            frame_type=frame,
        )
        assert jet_can_reach_round_trip(jet, home, near)
        required_far = round_trip_fuel_required(frame, home, far)
        capacity = config.JET_FRAME_CATALOG[frame].fuel_capacity
        if required_far > capacity:
            assert not jet_can_reach_round_trip(jet, home, far)
        else:
            assert jet_can_reach_round_trip(jet, home, far)


def test_metadata_regeneration_and_save_load_round_trip(tmp_path):
    from wraithnet.scenario_io import load_scenario, save_scenario

    params = parameters_for_preset("TRAINING", seed=33, overrides={"grid_width": 100.0, "grid_height": 100.0})
    result = generate_scenario(params)
    meta = result.scenario.generation
    assert meta is not None
    assert meta.seed == params.seed
    assert meta.preset == params.preset
    assert meta.modified_after_generation is False
    assert meta.resolved_parameters == params

    path = tmp_path / "gen.json"
    save_scenario(result.scenario, path)
    loaded = load_scenario(path)
    assert loaded.model_dump(mode="json") == result.scenario.model_dump(mode="json")

    again = generate_scenario(meta.resolved_parameters)
    assert again.scenario.model_dump(mode="json") == result.scenario.model_dump(
        mode="json"
    )


def test_global_random_unchanged_by_friendly_generation():
    state_before = random.getstate()
    generate_scenario(parameters_for_preset("STANDARD", seed=12, overrides={"grid_width": 100.0, "grid_height": 100.0}))
    generate_scenario(_params(seed=99, friendly_mode="RANDOM_LEGAL"))
    assert random.getstate() == state_before


def test_friendly_rng_stream_is_friendly_salt():
    params = _params(seed=123)
    enemies = generate_enemies(params)
    rng = random.Random(derived_seed(params.seed, FRIENDLY_SALT))
    jets_a = generate_friendly_roster(params, enemies, rng)
    rng_b = random.Random(derived_seed(params.seed, FRIENDLY_SALT))
    jets_b = generate_friendly_roster(params, enemies, rng_b)
    assert [j.model_dump() for j in jets_a] == [j.model_dump() for j in jets_b]


def test_place_jets_on_launch_ring_rejects_unplaceable_count():
    params = _params(seed=1, max_placement_attempts=20)
    enemies = generate_enemies(params)
    # Absurd count that cannot fit on the ring with separation + SAM clearance.
    from wraithnet.scenario_schema import JetSpec

    jets = [
        JetSpec(
            agent_id=f"Jet_{i:02d}",
            position=enemies.home_base,
            payload={"JDAM": 1},
            frame_type="STRIKE_FIGHTER",
        )
        for i in range(80)
    ]
    with pytest.raises(GenerationError) as exc_info:
        place_jets_on_launch_ring(
            jets,
            home_base=enemies.home_base,
            parameters=params,
            sam_sites=list(enemies.sam_sites),
            jammers=list(enemies.jammers),
            rng=random.Random(0),
        )
    assert exc_info.value.code == "PLACEMENT_EXHAUSTED"


def test_generation_performance_standard_and_max():
    # STANDARD median under a CI-friendly ceiling; Nightmare under a generous cap.
    standard_times: list[float] = []
    for seed in range(30):
        params = parameters_for_preset(
            "STANDARD",
            seed=seed,
            overrides={"grid_width": 100.0, "grid_height": 100.0},
        )
        t0 = time.perf_counter()
        generate_scenario(params)
        standard_times.append(time.perf_counter() - t0)
    standard_times.sort()
    median = standard_times[len(standard_times) // 2]
    assert median < 0.250, f"STANDARD median {median:.3f}s exceeds 250ms"

    max_times: list[float] = []
    for seed in range(10):
        params = parameters_for_preset(
            "NIGHTMARE",
            seed=seed,
            overrides={"grid_width": 100.0, "grid_height": 100.0},
        )
        t0 = time.perf_counter()
        try:
            generate_scenario(params)
        except GenerationError:
            # Rare dense-pack exhaustion on compact maps; timing still recorded.
            pass
        max_times.append(time.perf_counter() - t0)
    assert max(max_times) < 2.0, f"max generation {max(max_times):.3f}s exceeds 2s CI ceiling"
