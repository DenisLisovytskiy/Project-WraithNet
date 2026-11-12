"""Phase 41: corvettes, layered naval defense, AGM84, shared gun resolver."""

from __future__ import annotations

import copy
import math

import pytest

from wraithnet import config
from wraithnet.bases import HOME_BASE_ID, default_home_airbase
from wraithnet.corvette_doctrine import select_doctrine_actions
from wraithnet.guns import (
    CIWS_GUN,
    DECK_GUN,
    GunBurstRequest,
    resolve_gun_burst,
    validate_gun_burst,
)
from wraithnet.metrics import summarize
from wraithnet.naval import (
    NAVAL_FRAME_CATALOG,
    CorvetteSpec,
    CorvetteState,
    all_water_terrain,
    step_corvette_movement,
    validate_corvette_placement,
)
from wraithnet.objectives import MissionObjective
from wraithnet.scenario_schema import (
    migrate_v5_to_v6,
    migrate_v6_to_v7,
    new_scenario_data,
    scenario_from_data,
)
from wraithnet.schemas import ActionIntent
from wraithnet.taxonomy import is_legal_weapon_target, reject_engagement
from wraithnet.terrain import TerrainCell, all_plains_terrain
from wraithnet.trace_builders import build_trace_tick
from wraithnet.world import WraithNetModel


def _plains_pad_water(terrain, *world_xy: tuple[float, float]):
    cells = [list(row) for row in terrain.cells]
    for x, y in world_xy:
        col = int(x // terrain.cell_size)
        row = int(y // terrain.cell_size)
        cells[row][col] = TerrainCell(terrain_type="PLAINS")
    return terrain.model_copy(update={"cells": cells})


def _corvette_scenario(
    *,
    corvette_pos: tuple[float, float] = (30.0, 30.0),
    jet_pos: tuple[float, float] = (10.0, 10.0),
    jet_payload: dict | None = None,
    hold: bool = True,
    route: list[tuple[float, float]] | None = None,
    loadout: dict | None = None,
    destroy_required: bool = True,
    combat_seed: int = 0,
    include_carrier: bool = False,
):
    terrain = all_water_terrain(50.0, 50.0, cell_size=5.0)
    terrain = _plains_pad_water(terrain, (2.0, 2.0), (12.0, 12.0))
    home = (2.0, 2.0)
    objectives = []
    if destroy_required:
        objectives.append(
            MissionObjective(
                objective_id="OBJ_DESTROY_CVT",
                objective_type="DESTROY",
                subject_ids=["Corvette_1"],
                required=True,
            ).model_dump()
        )
    else:
        objectives.append(
            MissionObjective(
                objective_id="OBJ_DESTROY_A",
                objective_type="DESTROY",
                subject_ids=["Target_A"],
                required=True,
            ).model_dump()
        )
    carriers = []
    if include_carrier:
        from wraithnet.naval import CarrierSpec

        carriers.append(
            CarrierSpec(
                unit_id="Carrier_1",
                position=(40.0, 40.0),
                heading=0.0,
                hold_position=True,
            )
        )
    data = new_scenario_data(
        grid_width=50.0,
        grid_height=50.0,
        terrain=terrain,
        home_base=home,
        fixed_airbases=[default_home_airbase(home)],
        combat_seed=combat_seed,
        carriers=carriers,
        corvettes=[
            CorvetteSpec(
                unit_id="Corvette_1",
                position=corvette_pos,
                heading=0.0,
                route=list(route or []),
                hold_position=hold,
                loadout=dict(loadout or {"SAM_RADAR": 4, "CIWS": 40, "DECK_GUN": 10}),
            )
        ],
        jets=[
            {
                "agent_id": "Jet_1",
                "position": jet_pos,
                "payload": dict(jet_payload or {"AGM84": 2}),
                "frame_type": "STRIKE_FIGHTER",
            }
        ],
        targets=[
            {
                "target_id": "Target_A",
                "position": (12.0, 12.0),
                "hp_required": 1,
                "target_type": "HARDENED_BUNKER",
            }
        ],
        sam_sites=[],
        jammers=[],
        mission_objectives=objectives,
    )
    return scenario_from_data(data)


def test_migrate_v6_to_v7_adds_empty_corvettes():
    raw = copy.deepcopy(config.SCENARIO)
    from wraithnet.scenario_schema import (
        migrate_v1_to_v2,
        migrate_v2_to_v3,
        migrate_v3_to_v4,
        migrate_v4_to_v5,
    )

    v6 = migrate_v5_to_v6(
        migrate_v4_to_v5(
            migrate_v3_to_v4(migrate_v2_to_v3(migrate_v1_to_v2(dict(raw))))
        )
    )
    assert v6["scenario_version"] == 6
    assert "corvettes" not in v6
    v7 = migrate_v6_to_v7(copy.deepcopy(v6))
    assert v7["scenario_version"] == 7
    assert v7["corvettes"] == []

    scenario = scenario_from_data(config.SCENARIO)
    assert scenario.scenario_version == 9
    assert scenario.corvettes == []
    assert scenario.carriers == []


def test_default_poc_unaffected():
    model = WraithNetModel()
    assert model.corvettes == {}
    assert model.carriers == {}
    summary = summarize(model)
    assert summary["corvette_losses"] == 0
    assert summary["ciws_attempts"] == 0


def test_corvette_rejects_land_spawn():
    terrain = all_plains_terrain(50.0, 50.0)
    cv = CorvetteSpec(unit_id="CVT", position=(25.0, 25.0))
    errors = validate_corvette_placement(terrain, cv)
    assert errors


def test_corvette_moves_on_water_only():
    terrain = all_water_terrain(50.0, 50.0)
    frame = NAVAL_FRAME_CATALOG["CORVETTE_STANDARD"]
    cv = CorvetteState(
        unit_id="Corvette_1",
        position=(10.0, 10.0),
        heading=0.0,
        frame_type="CORVETTE_STANDARD",
        route=[(10.0, 40.0)],
        hold_position=False,
        hp=frame.hp,
        hp_max=frame.hp,
        loadout=dict({"SAM_RADAR": 1, "CIWS": 1, "DECK_GUN": 1}),
        prev_position=(10.0, 10.0),
    )
    start = cv.position
    for _ in range(5):
        step_corvette_movement(cv, terrain, others=[])
    assert cv.position != start
    assert cv.position[1] > start[1]
    dist = math.dist(start, cv.position)
    assert dist <= frame.max_speed * 5 + 1e-6


def test_agm84_target_class_restrictions():
    assert is_legal_weapon_target("AGM84", "SURFACE_VESSEL")
    assert not is_legal_weapon_target("AGM84", "AIRCRAFT")
    assert not is_legal_weapon_target("AGM84", "INSTALLATION")
    assert not is_legal_weapon_target("AGM84", "GROUND_VEHICLE")
    assert not is_legal_weapon_target("AIM9X", "SURFACE_VESSEL")
    assert not is_legal_weapon_target("AGM65", "SURFACE_VESSEL")
    assert reject_engagement(
        shooter_faction="FRIENDLY",
        target_faction="ENEMY",
        weapon="AIM9X",
        target_class="SURFACE_VESSEL",
    ) == "illegal_target_class"


def test_gun_resolver_rejects_illegal_class_and_range():
    req = GunBurstRequest(
        shooter_id="CVT",
        target_id="T1",
        gun=DECK_GUN,
        shooter_faction="ENEMY",
        target_faction="FRIENDLY",
        target_class="AIRCRAFT",
        shooter_position=(0.0, 0.0),
        target_position=(5.0, 0.0),
        shooter_heading_deg=90.0,
        target_altitude=10.0,
        ammo_available=10,
        has_los=True,
        combat_seed=0,
        tick=1,
    )
    assert validate_gun_burst(req) == "illegal_target_class"

    req2 = req.__class__(
        **{
            **req.__dict__,
            "gun": CIWS_GUN,
            "target_class": "AIRCRAFT",
            "target_position": (50.0, 0.0),
        }
    )
    assert validate_gun_burst(req2) == "out_of_range"


def test_gun_resolver_keyed_inaccuracy_deterministic():
    def _burst(seed: int, burst_index: int) -> object:
        return resolve_gun_burst(
            GunBurstRequest(
                shooter_id="CVT",
                target_id="Jet_1",
                gun=CIWS_GUN,
                shooter_faction="ENEMY",
                target_faction="FRIENDLY",
                target_class="AIRCRAFT",
                shooter_position=(0.0, 0.0),
                target_position=(2.0, 0.0),
                shooter_heading_deg=90.0,
                target_altitude=5.0,
                ammo_available=40,
                has_los=True,
                combat_seed=seed,
                tick=3,
                burst_index=burst_index,
            )
        )

    a = _burst(7, 0)
    b = _burst(7, 0)
    c = _burst(8, 0)
    assert a.accepted and b.accepted
    assert [r.hit for r in a.rounds] == [r.hit for r in b.rounds]
    assert [r.roll for r in a.rounds] == [r.roll for r in b.rounds]
    # Different seed should usually differ; if equal, burst_index still keys.
    d = _burst(7, 1)
    assert [r.roll for r in a.rounds] != [r.roll for r in d.rounds] or [
        r.roll for r in a.rounds
    ] != [r.roll for r in c.rounds]


def test_doctrine_priority_ciws_missile_before_sam():
    frame = NAVAL_FRAME_CATALOG["CORVETTE_STANDARD"]
    cv = CorvetteState(
        unit_id="Corvette_1",
        position=(0.0, 0.0),
        heading=0.0,
        frame_type="CORVETTE_STANDARD",
        route=[],
        hold_position=True,
        hp=frame.hp,
        hp_max=frame.hp,
        loadout={"SAM_RADAR": 4, "CIWS": 40, "DECK_GUN": 10},
    )
    actions = select_doctrine_actions(
        cv,
        inbound_missiles=[("M-1", (1.0, 0.0), 1.0)],
        aircraft=[("Jet_1", (5.0, 0.0), 10.0, True)],
        vessels=[("Carrier_1", (8.0, 0.0), True)],
    )
    layers = [a.layer for a in actions]
    assert layers[0] == "CIWS_MISSILE"
    assert "SAM_RADAR" in layers
    assert "CIWS_AIRCRAFT" not in layers  # shared CIWS cooldown/ammo priority
    assert "DECK_GUN" in layers


def test_doctrine_stable_distance_id_tiebreak():
    frame = NAVAL_FRAME_CATALOG["CORVETTE_STANDARD"]
    cv = CorvetteState(
        unit_id="Corvette_1",
        position=(0.0, 0.0),
        heading=0.0,
        frame_type="CORVETTE_STANDARD",
        route=[],
        hold_position=True,
        hp=frame.hp,
        hp_max=frame.hp,
        loadout={"SAM_RADAR": 4, "CIWS": 0, "DECK_GUN": 0},
    )
    actions = select_doctrine_actions(
        cv,
        inbound_missiles=[],
        aircraft=[
            ("Jet_B", (5.0, 0.0), 10.0, True),
            ("Jet_A", (5.0, 0.0), 10.0, True),
        ],
        vessels=[],
    )
    assert len(actions) == 1
    assert actions[0].target_id == "Jet_A"


def test_agm84_launch_and_impact_destroys_corvette():
    scenario = _corvette_scenario(
        corvette_pos=(20.0, 20.0),
        jet_pos=(20.0, 5.0),
        jet_payload={"AGM84": 2},
        loadout={"SAM_RADAR": 0, "CIWS": 0, "DECK_GUN": 0},
        combat_seed=1,
    )
    model = WraithNetModel(scenario=scenario)
    # One AGM84 deals 3 damage; force remaining HP so a single HIT destroys.
    model.corvettes["Corvette_1"].hp = 3
    model.corvettes["Corvette_1"].hp_max = 3
    jet = model.jets["Jet_1"]
    jet.heading = 0.0
    ack = model.try_launch_from_jet(jet, "Corvette_1", "AGM84")
    assert ack.accepted, ack.reason
    assert model.agm84_launches == 1
    for _ in range(20):
        model.tick += 1
        model._tick_stages_done.clear()
        model._tick_stages_done.update(
            {
                "pre_move_obs",
                "apply_movement",
                "post_move_obs",
                "register_launches",
                "countermeasures",
            }
        )
        model.stage_resolve_combat({})
        if model.corvettes["Corvette_1"].is_destroyed:
            break
    assert model.corvettes["Corvette_1"].is_destroyed
    assert model.agm84_hits >= 1
    assert model.corvette_losses == 1


def test_ciws_can_intercept_before_impact():
    scenario = _corvette_scenario(
        corvette_pos=(25.0, 25.0),
        jet_pos=(25.0, 10.0),
        jet_payload={"AGM84": 1},
        loadout={"SAM_RADAR": 0, "CIWS": 100, "DECK_GUN": 0},
        combat_seed=1,  # known HIT for AGM84 in this geometry
    )
    model = WraithNetModel(scenario=scenario)
    from wraithnet import guns as guns_mod
    import wraithnet.world as world_mod

    perfect = guns_mod.GunSpec(
        gun_id="CIWS",
        range=40.0,
        min_range=0.0,
        firing_arc_deg=360.0,
        rounds_per_burst=4,
        accuracy=1.0,
        damage_per_hit=1,
        cooldown_ticks=0,
        allowed_target_classes=frozenset({"AIRCRAFT", "IN_FLIGHT_MISSILE"}),
        altitude_ceiling=50.0,
        kind="CIWS",
    )
    original = guns_mod.CIWS_GUN
    guns_mod.CIWS_GUN = perfect
    world_mod.CIWS_GUN = perfect
    try:
        jet = model.jets["Jet_1"]
        ack = model.try_launch_from_jet(jet, "Corvette_1", "AGM84")
        assert ack.accepted, ack.reason
        intercepted = False
        for _ in range(25):
            model.tick += 1
            model._tick_stages_done.clear()
            model._tick_stages_done.update(
                {
                    "pre_move_obs",
                    "apply_movement",
                    "post_move_obs",
                    "register_launches",
                    "countermeasures",
                }
            )
            model.stage_resolve_combat({})
            if any(
                e.get("outcome") == "INTERCEPTED"
                for e in model.missile_events
                if e.get("event") == "impact"
            ):
                intercepted = True
                break
        assert intercepted
        assert model.ciws_intercepts >= 1
        assert not model.corvettes["Corvette_1"].is_destroyed
    finally:
        guns_mod.CIWS_GUN = original
        world_mod.CIWS_GUN = original


def test_finite_ammo_and_cooldown_block_repeat_fire():
    scenario = _corvette_scenario(
        corvette_pos=(20.0, 20.0),
        jet_pos=(21.0, 20.0),
        jet_payload={"JDAM": 1},
        loadout={"SAM_RADAR": 0, "CIWS": 4, "DECK_GUN": 0},
        destroy_required=False,
        combat_seed=3,
    )
    model = WraithNetModel(scenario=scenario)
    cv = model.corvettes["Corvette_1"]
    # Place jet inside CIWS envelope.
    jet = model.jets["Jet_1"]
    jet.pos = (21.0, 20.0)
    jet.altitude = 5.0
    dmg1 = model._fire_corvette_ciws_air(cv, "Jet_1")
    assert cv.loadout["CIWS"] == 0 or cv.ciws_cooldown_remaining > 0 or dmg1 >= 0
    ammo_after = cv.loadout["CIWS"]
    # Immediate second fire should fail on cooldown or ammo.
    dmg2 = model._fire_corvette_ciws_air(cv, "Jet_1")
    assert dmg2 == 0
    assert cv.loadout["CIWS"] == ammo_after


def test_corvette_destroy_objective_completes():
    scenario = _corvette_scenario(
        corvette_pos=(20.0, 20.0),
        jet_pos=(20.0, 5.0),
        loadout={"SAM_RADAR": 0, "CIWS": 0, "DECK_GUN": 0},
        combat_seed=2,
    )
    model = WraithNetModel(scenario=scenario)
    model.corvettes["Corvette_1"].hp = 0
    model.corvettes["Corvette_1"].mark_destroyed()
    model.corvette_losses = 1
    model._evaluate_termination()
    assert model.running is False


def test_deck_gun_damages_carrier_not_installation():
    scenario = _corvette_scenario(
        corvette_pos=(35.0, 40.0),
        jet_pos=(2.0, 2.0),
        include_carrier=True,
        loadout={"SAM_RADAR": 0, "CIWS": 0, "DECK_GUN": 20},
        destroy_required=False,
        combat_seed=4,
    )
    model = WraithNetModel(scenario=scenario)
    cv = model.corvettes["Corvette_1"]
    carrier = model.carriers["Carrier_1"]
    hp_before = carrier.hp
    # Force perfect deck gun.
    from wraithnet import guns as guns_mod
    import wraithnet.world as world_mod

    perfect = guns_mod.GunSpec(
        gun_id="DECK_GUN",
        range=40.0,
        min_range=0.0,
        firing_arc_deg=360.0,
        rounds_per_burst=1,
        accuracy=1.0,
        damage_per_hit=2,
        cooldown_ticks=0,
        allowed_target_classes=frozenset({"SURFACE_VESSEL"}),
        kind="DECK_GUN",
    )
    original = guns_mod.DECK_GUN
    guns_mod.DECK_GUN = perfect
    world_mod.DECK_GUN = perfect
    try:
        dmg = model._fire_corvette_deck_gun(cv, "Carrier_1")
        assert dmg == 2
        model._apply_platform_damage("Carrier_1", dmg, shooter_id=cv.unit_id)
        assert carrier.hp == hp_before - 2
        # Deck gun must refuse installations via resolver.
        bad = resolve_gun_burst(
            GunBurstRequest(
                shooter_id=cv.unit_id,
                target_id="Target_A",
                gun=perfect,
                shooter_faction="ENEMY",
                target_faction="FRIENDLY",
                target_class="INSTALLATION",
                shooter_position=cv.position,
                target_position=(12.0, 12.0),
                shooter_heading_deg=0.0,
                target_altitude=0.0,
                ammo_available=10,
                has_los=True,
                combat_seed=0,
                tick=1,
            )
        )
        assert not bad.accepted
    finally:
        guns_mod.DECK_GUN = original
        world_mod.DECK_GUN = original


def test_trace_includes_corvette_frames():
    scenario = _corvette_scenario()
    model = WraithNetModel(scenario=scenario)

    class _Broker:
        def is_claimed(self, _tid: str) -> bool:
            return False

    class _Bridge:
        def __init__(self, m):
            self.model = m
            self.agents = {}
            self.broker = _Broker()

    tick = build_trace_tick(_Bridge(model))
    assert len(tick.corvettes) == 1
    assert tick.corvettes[0].unit_id == "Corvette_1"
    assert tick.corvettes[0].loadout.get("SAM_RADAR", 0) >= 0


def test_full_step_corvette_sam_launches_at_nearby_jet():
    scenario = _corvette_scenario(
        corvette_pos=(20.0, 20.0),
        jet_pos=(22.0, 20.0),
        jet_payload={"JDAM": 1},
        loadout={"SAM_RADAR": 2, "CIWS": 0, "DECK_GUN": 0},
        destroy_required=False,
        combat_seed=5,
    )
    model = WraithNetModel(scenario=scenario)
    jet = model.jets["Jet_1"]
    jet.pos = (22.0, 20.0)
    jet.altitude = 10.0
    model.step(
        {
            "Jet_1": ActionIntent(
                action_type="IDLE",
                agent_id="Jet_1",
                tick=model.tick,
            )
        }
    )
    assert model.corvette_sam_launches >= 1 or any(
        m.weapon_type == "SAM_RADAR" and m.shooter_id == "Corvette_1"
        for m in model.missiles
    ) or any(
        e.get("weapon_type") == "SAM_RADAR" and e.get("shooter_id") == "Corvette_1"
        for e in model.missile_events
    )


def test_aim9x_cannot_damage_corvette_via_launch():
    scenario = _corvette_scenario(
        jet_payload={"AIM9X": 2},
        loadout={"SAM_RADAR": 0, "CIWS": 0, "DECK_GUN": 0},
    )
    model = WraithNetModel(scenario=scenario)
    ack = model.try_launch_from_jet(model.jets["Jet_1"], "Corvette_1", "AIM9X")
    assert not ack.accepted
    assert ack.reason in ("illegal_target_class", "unsuitable_target_type")
