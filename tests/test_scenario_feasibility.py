"""Static feasibility analyzer and challenge-score tests (Phase 30)."""

from __future__ import annotations

import math
import random

import pytest

from wraithnet import config
from wraithnet.scenario_feasibility import (
    CHALLENGE_COMPONENT_NAMES,
    DISCOVERY_CHAIN_GAP,
    DISCOVERY_MARGIN,
    EMPTY_LOADOUT,
    FUEL_REACH,
    INSUFFICIENT_DAMAGE,
    LAUNCH_RING_OUTER_RADIUS,
    NO_SUITABLE_WEAPON,
    ChallengeScoreInputs,
    analyze_feasibility,
    compute_challenge_components,
    discovery_link_max_distance,
    jet_can_reach_round_trip,
    max_weapon_range,
    round_trip_fuel_required,
    weapon_eligible_damage,
)
from wraithnet.scenario_schema import ScenarioConfig, scenario_from_data


def _scenario(**overrides) -> ScenarioConfig:
    data = {
        "jets": [
            {
                "agent_id": "Jet_1",
                "position": (4.0, 4.0),
                "payload": {"JDAM": 4, "AIM9X": 2},
                "frame_type": "STRIKE_FIGHTER",
            },
            {
                "agent_id": "Jet_2",
                "position": (5.0, 3.0),
                "payload": {"JDAM": 3, "AIM9X": 1},
                "frame_type": "STRIKE_FIGHTER",
            },
        ],
        "targets": [
            {
                "target_id": "Target_Near",
                "position": (30.0, 30.0),
                "hp_required": 3,
                "target_type": "HARDENED_BUNKER",
            },
            {
                "target_id": "Target_Radar",
                "position": (35.0, 28.0),
                "hp_required": 1,
                "target_type": "MOBILE_RADAR",
            },
        ],
        "sam_sites": [],
        "jammers": [],
        "home_base": (2.0, 2.0),
    }
    data.update(overrides)
    return scenario_from_data(data)


def test_feasible_handcrafted_scenario():
    report = analyze_feasibility(_scenario())
    assert report.feasible is True
    assert not any(i.severity == "ERROR" for i in report.issues)
    assert report.required_hp_by_target_type["HARDENED_BUNKER"] == 3
    assert report.required_hp_by_target_type["MOBILE_RADAR"] == 1
    assert report.total_damage_capacity_by_target_type["HARDENED_BUNKER"] >= 3
    assert report.total_damage_capacity_by_target_type["MOBILE_RADAR"] >= 1
    assert 0.0 <= report.challenge_score <= 100.0
    for name in CHALLENGE_COMPONENT_NAMES:
        assert name in report.challenge_components
        assert 0.0 <= report.challenge_components[name] <= 100.0


def test_no_suitable_weapon_error():
    # AIM9X is air-to-air only after Phase 38 — useless vs bunkers when already v4.
    from wraithnet.scenario_schema import new_scenario_data

    scenario = scenario_from_data(
        new_scenario_data(
            jets=[
                {
                    "agent_id": "Jet_1",
                    "position": (4.0, 4.0),
                    "payload": {"AIM9X": 4},
                    "frame_type": "STRIKE_FIGHTER",
                }
            ],
            targets=[
                {
                    "target_id": "Bunker_Only",
                    "position": (25.0, 25.0),
                    "hp_required": 1,
                    "target_type": "HARDENED_BUNKER",
                }
            ],
            sam_sites=[],
            jammers=[],
            home_base=(2.0, 2.0),
        )
    )
    report = analyze_feasibility(scenario)
    assert report.feasible is False
    codes = [i.code for i in report.issues if i.severity == "ERROR"]
    assert NO_SUITABLE_WEAPON in codes


def test_insufficient_damage_joint_allocation():
    # One JDAM cannot cover bunker HP=5; AIM9X (air-to-air) cannot help bunkers.
    from wraithnet.scenario_schema import new_scenario_data

    scenario = scenario_from_data(
        new_scenario_data(
            jets=[
                {
                    "agent_id": "Jet_1",
                    "position": (4.0, 4.0),
                    "payload": {"JDAM": 1, "AIM9X": 4},
                    "frame_type": "STRIKE_FIGHTER",
                }
            ],
            targets=[
                {
                    "target_id": "Hard_Bunker",
                    "position": (25.0, 25.0),
                    "hp_required": 5,
                    "target_type": "HARDENED_BUNKER",
                }
            ],
            sam_sites=[],
            jammers=[],
            home_base=(2.0, 2.0),
        )
    )
    report = analyze_feasibility(scenario)
    assert report.feasible is False
    assert any(i.code == INSUFFICIENT_DAMAGE for i in report.issues)


def test_weapon_unit_cannot_satisfy_two_target_categories():
    # Two JDAM total, but bunker needs 2 and SAM needs 2 — joint alloc fails.
    scenario = _scenario(
        jets=[
            {
                "agent_id": "Jet_1",
                "position": (4.0, 4.0),
                "payload": {"JDAM": 2},
                "frame_type": "STRIKE_FIGHTER",
            }
        ],
        targets=[
            {
                "target_id": "Bunker",
                "position": (20.0, 20.0),
                "hp_required": 2,
                "target_type": "HARDENED_BUNKER",
            }
        ],
        sam_sites=[
            {
                "target_id": "SAM_1",
                "position": (22.0, 18.0),
                "hp_required": 2,
                "target_type": "SAM_SITE",
            }
        ],
    )
    report = analyze_feasibility(scenario)
    assert report.feasible is False
    assert any(i.code == INSUFFICIENT_DAMAGE for i in report.issues)
    # Theoretical per-type capacity looks fine in isolation (2>=2 each) but joint fails.
    assert report.total_damage_capacity_by_target_type["HARDENED_BUNKER"] == 2
    assert report.total_damage_capacity_by_target_type["SAM_SITE"] == 2


def test_empty_loadout_warning():
    scenario = _scenario(
        jets=[
            {
                "agent_id": "Jet_Armed",
                "position": (4.0, 4.0),
                "payload": {"JDAM": 4, "AIM9X": 2},
                "frame_type": "STRIKE_FIGHTER",
            },
            {
                "agent_id": "Jet_Empty",
                "position": (5.0, 3.0),
                "payload": {"JDAM": 0, "AIM9X": 0},
                "frame_type": "STRIKE_FIGHTER",
            },
        ]
    )
    report = analyze_feasibility(scenario)
    assert any(i.code == EMPTY_LOADOUT for i in report.issues)
    empty = next(i for i in report.issues if i.code == EMPTY_LOADOUT)
    assert empty.severity == "WARNING"
    assert "Jet_Empty" in empty.related_ids


def test_fuel_reach_boundary_interceptor_vs_far_target():
    from wraithnet.scenario_schema import JetSpec

    home = (2.0, 2.0)
    interceptor = JetSpec(
        agent_id="I1",
        position=(3.0, 3.0),
        payload={"JDAM": 2},
        frame_type="INTERCEPTOR",
    )
    assert jet_can_reach_round_trip(interceptor, home, (30.0, 30.0))
    # Beyond INTERCEPTOR round-trip fuel under RTB margin (capacity sized for
    # DEFAULT_GENERATION_GRID; PoC-corner distances are no longer out of reach).
    capacity = config.JET_FRAME_CATALOG["INTERCEPTOR"].fuel_capacity
    # Solve 2*(d/speed)*burn*margin > capacity  =>  d > capacity*speed/(2*burn*margin)
    frame = config.JET_FRAME_CATALOG["INTERCEPTOR"]
    beyond = (
        capacity
        * frame.max_speed
        / (2.0 * frame.fuel_burn_per_tick * config.RTB_FUEL_SAFETY_MARGIN)
    ) + 50.0
    far = (home[0] + beyond / math.sqrt(2.0), home[1] + beyond / math.sqrt(2.0))
    assert not jet_can_reach_round_trip(interceptor, home, far)
    required = round_trip_fuel_required("INTERCEPTOR", home, far)
    assert required > capacity

    scenario = _scenario(
        jets=[
            {
                "agent_id": "I1",
                "position": (3.0, 3.0),
                "payload": {"JDAM": 4},
                "frame_type": "INTERCEPTOR",
            }
        ],
        targets=[
            {
                "target_id": "Far_Bunker",
                "position": far,
                "hp_required": 1,
                "target_type": "HARDENED_BUNKER",
            }
        ],
        grid_width=max(200.0, far[0] + 10.0),
        grid_height=max(200.0, far[1] + 10.0),
    )
    report = analyze_feasibility(scenario)
    assert report.feasible is False
    assert any(i.code == FUEL_REACH for i in report.issues)


def test_discovery_chain_gap():
    # Over-horizon island beyond SENSOR_RADIUS: mission brief covers transit, so
    # it is treated as a reachable root (no DISCOVERY_CHAIN_GAP ERROR).
    link = discovery_link_max_distance()
    assert link == pytest.approx(
        config.SENSOR_RADIUS - max_weapon_range() - DISCOVERY_MARGIN
    )
    island_x = max(25.0 + link + 5.0, config.SENSOR_RADIUS + LAUNCH_RING_OUTER_RADIUS + 10.0)
    scenario = _scenario(
        targets=[
            {
                "target_id": "Near",
                "position": (25.0, 25.0),
                "hp_required": 1,
                "target_type": "HARDENED_BUNKER",
            },
            {
                "target_id": "Island",
                "position": (island_x, 25.0),
                "hp_required": 1,
                "target_type": "HARDENED_BUNKER",
            },
        ],
        jets=[
            {
                "agent_id": "Jet_1",
                "position": (4.0, 4.0),
                "payload": {"JDAM": 6},
                "frame_type": "HEAVY_BOMBER",
            }
        ],
        grid_width=max(100.0, island_x + 10.0),
        grid_height=100.0,
    )
    report = analyze_feasibility(scenario)
    assert not any(i.code == DISCOVERY_CHAIN_GAP for i in report.issues)


def test_cooperative_objective_detection():
    scenario = _scenario(
        jets=[
            {
                "agent_id": "Jet_1",
                "position": (4.0, 4.0),
                "payload": {"JDAM": 3},
                "frame_type": "STRIKE_FIGHTER",
            },
            {
                "agent_id": "Jet_2",
                "position": (5.0, 3.0),
                "payload": {"JDAM": 3},
                "frame_type": "STRIKE_FIGHTER",
            },
        ],
        targets=[
            {
                "target_id": "Coop",
                "position": (30.0, 30.0),
                "hp_required": 5,
                "target_type": "HARDENED_BUNKER",
            }
        ],
    )
    report = analyze_feasibility(scenario)
    assert report.feasible is True
    assert "Coop" in report.cooperative_objective_ids


def test_score_and_components_remain_in_bounds():
    report = analyze_feasibility(_scenario())
    assert 0.0 <= report.challenge_score <= 100.0
    components = compute_challenge_components(
        _scenario(),
        ChallengeScoreInputs(
            required_hp_by_target_type=report.required_hp_by_target_type,
            total_damage_capacity_by_target_type=report.total_damage_capacity_by_target_type,
            objective_count=2,
            sam_count=10,
            jammer_count=10,
            mean_objective_distance_fraction=1.5,
            cooperative_objective_count=50,
            destroyable_count=2,
            friendly_reserve_ratio=0.1,
            min_fuel_margin_ratio=0.1,
        ),
    )
    for name in CHALLENGE_COMPONENT_NAMES:
        assert 0.0 <= components[name] <= 100.0


def test_weapon_eligible_damage_gate():
    assert weapon_eligible_damage("JDAM", "HARDENED_BUNKER") == 1
    assert weapon_eligible_damage("JDAM", "MOBILE_RADAR") == 0
    assert weapon_eligible_damage("AGM65", "MOBILE_RADAR") == 1
    assert weapon_eligible_damage("AIM9X", "MOBILE_RADAR") == 0
    assert weapon_eligible_damage("GUN_POD", "HARDENED_BUNKER") == 2
    assert weapon_eligible_damage("AMRAAM", "SAM_SITE") == 0
    assert weapon_eligible_damage("AGM88", "SAM_SITE") == 1


def test_analyze_feasibility_does_not_mutate_global_random_or_catalog():
    state_before = random.getstate()
    weapons_before = {
        k: (v.slot_cost, v.damage_per_hit, dict(v.suitability))
        for k, v in config.WEAPON_CATALOG.items()
    }
    analyze_feasibility(_scenario())
    analyze_feasibility(
        _scenario(
            jets=[
                {
                    "agent_id": "Jet_1",
                    "position": (4.0, 4.0),
                    "payload": {"AMRAAM": 2, "GUN_POD": 1},
                    "frame_type": "STRIKE_FIGHTER",
                }
            ],
            targets=[
                {
                    "target_id": "T1",
                    "position": (20.0, 20.0),
                    "hp_required": 1,
                    "target_type": "SAM_SITE",
                }
            ],
            sam_sites=[
                {
                    "target_id": "SAM_1",
                    "position": (22.0, 20.0),
                    "hp_required": 1,
                    "target_type": "SAM_SITE",
                }
            ],
        )
    )
    assert random.getstate() == state_before
    weapons_after = {
        k: (v.slot_cost, v.damage_per_hit, dict(v.suitability))
        for k, v in config.WEAPON_CATALOG.items()
    }
    assert weapons_after == weapons_before


def test_group_ids_by_entity_affects_unreachable_reporting():
    scenario = _scenario()
    mapping = {
        "Target_Near": "Group_A",
        "Target_Radar": "Group_A",
    }
    report = analyze_feasibility(scenario, group_ids_by_entity=mapping)
    assert report.feasible is True
    assert report.unreachable_group_ids == []


def test_canonical_group_centers_override_mean_drift():
    # Escort offset pulls a mean-center beyond link_max from Root. Over-horizon
    # groups are mission-brief reachable, so mean drift no longer opens a
    # DISCOVERY_CHAIN_GAP; canonical centers still keep local link geometry.
    link = discovery_link_max_distance()
    home = (2.0, 2.0)
    root = (10.0, 50.0)
    child = (10.0 + link - 1.0, 50.0)
    escort = (min(98.0, child[0] + link + 5.0), 50.0)
    mean_x = 0.5 * (child[0] + escort[0])
    assert mean_x - root[0] > link
    scenario = _scenario(
        home_base=home,
        targets=[
            {
                "target_id": "Root",
                "position": root,
                "hp_required": 1,
                "target_type": "HARDENED_BUNKER",
            },
            {
                "target_id": "Child",
                "position": child,
                "hp_required": 1,
                "target_type": "HARDENED_BUNKER",
            },
        ],
        sam_sites=[
            {
                "target_id": "Escort",
                "position": escort,
                "hp_required": 1,
                "target_type": "SAM_SITE",
            }
        ],
        jets=[
            {
                "agent_id": "Jet_1",
                "position": (4.0, 4.0),
                "payload": {"JDAM": 6},
                "frame_type": "HEAVY_BOMBER",
            }
        ],
    )
    mapping = {"Root": "G1", "Child": "G2", "Escort": "G2"}
    without = analyze_feasibility(scenario, group_ids_by_entity=mapping)
    assert not any(i.code == DISCOVERY_CHAIN_GAP for i in without.issues)
    with_centers = analyze_feasibility(
        scenario,
        group_ids_by_entity=mapping,
        group_centers={"G1": root, "G2": child},
    )
    assert not any(i.code == DISCOVERY_CHAIN_GAP for i in with_centers.issues)


# --- Discovery observer altitude (low flight near SAM sites) ---------------

_OBSERVER = (12.5, 52.5)
_HIDDEN = (42.5, 52.5)


def _city_wall_terrain():
    """Plains 100×100 (5-unit cells) with one 8-tall city block between
    ``_OBSERVER`` and ``_HIDDEN``: clear from cruise, blocking from low level."""
    from wraithnet.terrain import TerrainCell, TerrainGrid

    cells = [[TerrainCell() for _ in range(20)] for _ in range(20)]
    cells[10][5] = TerrainCell(terrain_type="CITY", building_height=8.0)
    return TerrainGrid(
        world_width=100.0,
        world_height=100.0,
        cell_size=5.0,
        columns=20,
        rows=20,
        cells=cells,
    )


def _wall_scenario(sam_positions=(), **overrides) -> ScenarioConfig:
    return _scenario(
        terrain=_city_wall_terrain(),
        grid_width=100.0,
        grid_height=100.0,
        home_base=(6.5, 52.5),
        jets=[
            {
                "agent_id": "Jet_1",
                "position": (8.0, 52.5),
                "payload": {"JDAM": 6},
                "frame_type": "STRIKE_FIGHTER",
            }
        ],
        targets=[
            {
                "target_id": "Hidden",
                "position": _HIDDEN,
                "hp_required": 1,
                "target_type": "HARDENED_BUNKER",
            }
        ],
        sam_sites=[
            {
                "target_id": f"SAM_{i}",
                "position": pos,
                "hp_required": 1,
                "target_type": "SAM_SITE",
            }
            for i, pos in enumerate(sam_positions)
        ],
        **overrides,
    )


def test_discovery_blocked_by_city_only_when_flying_low_near_sam():
    from wraithnet.scenario_feasibility import (
        LOS_DISCOVERY_CHAIN_GAP,
        has_discovery_los,
        scenario_observer_altitude_at,
    )

    cruise = _wall_scenario()
    z = scenario_observer_altitude_at(cruise)(_OBSERVER)
    assert z == config.JET_FRAME_CATALOG["STRIKE_FIGHTER"].cruise_altitude
    assert has_discovery_los(cruise.terrain, _OBSERVER, _HIDDEN, observer_z=z)

    # SAM out of discovery-link reach of Hidden, but its low-level radius
    # covers the launch approach → jets fly low there.
    defended = _wall_scenario(sam_positions=[(12.5, 7.5)])
    z_low = scenario_observer_altitude_at(defended)(_OBSERVER)
    assert z_low == defended.altitude_rules.terrain_clearance  # plains floor
    assert not has_discovery_los(
        defended.terrain, _OBSERVER, _HIDDEN, observer_z=z_low
    )

    def gaps(sc):
        return {
            i.code for i in analyze_feasibility(sc).issues if "Hidden" in i.related_ids
        }

    assert LOS_DISCOVERY_CHAIN_GAP not in gaps(cruise)
    assert LOS_DISCOVERY_CHAIN_GAP in gaps(defended)


def test_terrain_clearance_sets_low_observer_altitude():
    from wraithnet.scenario_feasibility import (
        has_discovery_los,
        scenario_observer_altitude_at,
    )

    sam = [(12.5, 7.5)]
    low = _wall_scenario(sam, altitude_rules={"terrain_clearance": 3.0})
    high = _wall_scenario(sam, altitude_rules={"terrain_clearance": 18.0})
    assert scenario_observer_altitude_at(low)(_OBSERVER) == pytest.approx(3.0)
    z_high = scenario_observer_altitude_at(high)(_OBSERVER)
    assert z_high == pytest.approx(18.0)
    assert has_discovery_los(high.terrain, _OBSERVER, _HIDDEN, observer_z=z_high)
    # The floor sits on the obstacle top: over the 8-tall block, 8 + clearance.
    assert scenario_observer_altitude_at(high)((27.5, 52.5)) == pytest.approx(26.0)


def test_unknown_sam_layout_assumes_lowest_altitude():
    # Generation checks discovery before SAMs exist: floor, capped at cruise.
    from wraithnet.scenario_feasibility import discovery_observer_altitude
    from wraithnet.schemas import AltitudeRules

    terrain = _city_wall_terrain()
    rules = AltitudeRules(terrain_clearance=3.0)
    unknown = dict(sam_positions=None, rules=rules)
    assert discovery_observer_altitude(terrain, _OBSERVER, **unknown) == 3.0
    assert discovery_observer_altitude(terrain, (27.5, 52.5), **unknown) == 11.0
    rules = AltitudeRules(terrain_clearance=30.0)
    assert discovery_observer_altitude(
        terrain, _OBSERVER, sam_positions=None, rules=rules
    ) == config.JET_COMBAT_ALTITUDE


def test_observer_altitude_defaults_to_legacy_combat_altitude():
    from wraithnet.scenario_feasibility import (
        discovery_link_clear,
        discovery_observer_altitude,
        has_discovery_los,
        scenario_observer_altitude_at,
    )

    terrain = _city_wall_terrain()
    # No SAMs, no jets, and a SAM well outside every frame's low-level radius.
    assert discovery_observer_altitude(terrain, _OBSERVER) == config.JET_COMBAT_ALTITUDE
    far_sam = _wall_scenario(sam_positions=[(97.5, 97.5)])
    for sc in (_scenario(), _wall_scenario(), far_sam):
        z_at = scenario_observer_altitude_at(sc)
        for xy in (_OBSERVER, (50.0, 50.0), (2.0, 2.0)):
            assert z_at(xy) == config.JET_COMBAT_ALTITUDE
            assert has_discovery_los(sc.terrain, xy, _HIDDEN) == has_discovery_los(
                sc.terrain, xy, _HIDDEN, observer_z=z_at(xy)
            )
            assert discovery_link_clear(sc.terrain, xy, _HIDDEN) == discovery_link_clear(
                sc.terrain, xy, _HIDDEN, observer_z_at=z_at
            )
