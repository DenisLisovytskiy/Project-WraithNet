"""Relative altitude, obstacle avoidance and altitude tactics."""

from __future__ import annotations

import math

import pytest

from wraithnet import config
from wraithnet.agents import JetAgentRuntime, transit_altitude
from wraithnet.altitude_tactics import climb_rate
from wraithnet.broker import MessageBroker
from wraithnet.missiles import LaunchRequest
from wraithnet.scenario_schema import JetSpec, TargetSpec, new_scenario_data, scenario_from_data
from wraithnet.schemas import ActionIntent, ObservedGunThreat, TargetInfo
from wraithnet.terrain import (
    FOREST_CANOPY_HEIGHT,
    TerrainCell,
    all_plains_terrain,
    lookahead_required_altitude,
)
from wraithnet.weapons_release import attack_altitude, release_attitude_ok
from wraithnet.world import WraithNetModel


def _model(
    *,
    position=(10.0, 50.0),
    heading=90.0,
    city_cells=(),
    forest_cells=(),
    building_height=25.0,
    frame_type="STRIKE_FIGHTER",
    altitude_rules=None,
) -> WraithNetModel:
    terrain = all_plains_terrain(100.0, 100.0)
    for col, row in city_cells:
        terrain.cells[row][col] = TerrainCell(
            terrain_type="CITY", building_height=building_height
        )
    for col, row in forest_cells:
        terrain.cells[row][col] = TerrainCell(terrain_type="FOREST")
    scenario = scenario_from_data(
        new_scenario_data(
            terrain=terrain,
            jets=[
                JetSpec(
                    agent_id="Jet_A",
                    position=position,
                    payload={"JDAM": 2},
                    frame_type=frame_type,
                    initial_heading=heading,
                )
            ],
            targets=[
                TargetSpec(
                    target_id="Bunker",
                    position=(90.0, 10.0),
                    hp_required=1,
                    target_type="HARDENED_BUNKER",
                )
            ],
            **({"altitude_rules": altitude_rules} if altitude_rules else {}),
        )
    )
    return WraithNetModel(scenario=scenario)


def _fly_low(model: WraithNetModel, ticks: int, track=None) -> list[float]:
    """Command 'as low as allowed' due east; return relative altitude per tick.

    ``track`` (optional list) collects ``(altitude_change, pitch_deg)`` per tick.
    """
    jet = model.jets["Jet_A"]
    agl = []
    for tick in range(ticks):
        alt_before = jet.altitude
        model.step(
            {
                "Jet_A": ActionIntent(
                    agent_id="Jet_A",
                    tick=tick,
                    action_type="IDLE",
                    desired_heading=90.0,
                    desired_pitch_deg=0.0,
                    desired_altitude=0.0,
                )
            }
        )
        if "Jet_A" not in model.jets:
            break
        agl.append(model.relative_altitude(jet))
        if track is not None:
            track.append((jet.altitude - alt_before, jet.pitch_deg))
    return agl


def test_relative_altitude_is_height_above_roof_or_treetops():
    model = _model(position=(12.5, 52.5), city_cells=[(2, 10)], building_height=12.0)
    jet = model.jets["Jet_A"]
    assert model.relative_altitude(jet) == pytest.approx(jet.altitude - 12.0)
    forest = _model(position=(12.5, 52.5), forest_cells=[(2, 10)])
    fjet = forest.jets["Jet_A"]
    assert forest.relative_altitude(fjet) == pytest.approx(
        fjet.altitude - FOREST_CANOPY_HEIGHT
    )


def test_level_command_descends_nose_down_at_flown_rate():
    """desired_altitude is flown with the nose: pitch down, dz = speed*sin(pitch)."""
    model = _model()
    jet = model.jets["Jet_A"]
    frame = config.JET_FRAME_CATALOG[jet.frame_type]
    track = []
    _fly_low(model, 12, track)
    assert all(pitch < 0.0 for dz, pitch in track[:3])
    for dz, pitch in track:
        assert abs(dz) <= climb_rate(frame) + 1e-9
        assert dz == pytest.approx(frame.max_speed * math.sin(math.radians(pitch)), abs=1e-6)
    assert jet.altitude < 20.0 - 5.0


def test_low_flier_climbs_over_tallest_building_without_crashing():
    # A wall of 25-tall buildings across the jet's eastbound track.
    wall = [(12, row) for row in range(20)]
    model = _model(city_cells=wall)
    frame = config.JET_FRAME_CATALOG["STRIKE_FIGHTER"]
    track = []
    agl = _fly_low(model, 30, track)
    # The climb is flown: nose up, never faster than the pitch allows.
    assert max(pitch for _, pitch in track) > 10.0
    for dz, pitch in track:
        assert dz == pytest.approx(frame.max_speed * math.sin(math.radians(pitch)), abs=1e-6)
    assert model.crash_log == []
    assert "Jet_A" in model.jets
    assert min(agl) >= config.TERRAIN_CLEARANCE - 1e-9
    # It went low over open ground, then rose to clear the 25-tall roofs.
    assert min(agl) < 10.0
    assert model.jets["Jet_A"].pos[0] > 65.0  # past the wall


def test_treetops_respected_at_low_level():
    model = _model(forest_cells=[(c, 10) for c in range(20)])
    agl = _fly_low(model, 15)
    assert model.crash_log == []
    assert min(agl) >= config.TERRAIN_CLEARANCE - 1e-9


def test_lookahead_ignores_far_buildings_so_jets_can_fly_low():
    model = _model(city_cells=[(19, 19)])
    frame = config.JET_FRAME_CATALOG["STRIKE_FIGHTER"]
    need = lookahead_required_altitude(
        model.terrain,
        (5.0, 5.0),
        reach_per_tick=frame.max_speed,
        climb_per_tick=frame.max_climb_per_tick,
        clearance=config.TERRAIN_CLEARANCE,
    )
    assert need <= 0.0


def test_crash_safety_net_destroys_jet_below_obstacle():
    model = _model(position=(12.5, 52.5), city_cells=[(2, 10)], building_height=12.0)
    jet = model.jets["Jet_A"]
    jet.altitude = 5.0  # forced inside the building (bypassing the floor)
    jet.desired_altitude = None
    model.step({})
    assert model.crash_log and model.crash_log[0]["agent_id"] == "Jet_A"
    assert jet.is_destroyed


def test_radar_sam_cannot_engage_below_clutter_floor():
    model = _model()
    jet = model.jets["Jet_A"]
    req = LaunchRequest(
        shooter_id="SAM_X",
        target_id="Jet_A",
        weapon_type="SAM_RADAR",
        shooter_position=(50.0, 50.0, 0.0),
        target_position=(10.0, 50.0, 4.0),
        shooter_faction="ENEMY",
        target_faction="FRIENDLY",
        target_class="AIRCRAFT",
        target_signature=config.JET_FRAME_CATALOG["STRIKE_FIGHTER"].signature,
        ammo_available=1,
    )
    jet.altitude = config.SAM_RADAR_MIN_ENGAGEMENT_AGL - 1.0
    assert model._radar_clutter_reason(req) == "below_radar_clutter_floor"
    jet.altitude = config.SAM_RADAR_MIN_ENGAGEMENT_AGL + 1.0
    assert model._radar_clutter_reason(req) is None
    ir = LaunchRequest(**{**req.__dict__, "weapon_type": "SAM_IR"})
    jet.altitude = 4.0
    assert model._radar_clutter_reason(ir) is None


def test_unguided_release_requires_descent():
    jdam = config.WEAPON_CATALOG["JDAM"]
    assert release_attitude_ok(jdam, pitch_deg=0.0, roll_deg=0.0, altitude=20.0) == (
        False,
        "above_max_release_altitude",
    )
    assert release_attitude_ok(jdam, pitch_deg=0.0, roll_deg=0.0, altitude=13.0)[0]
    assert attack_altitude(jdam, 23.0) == pytest.approx(13.0)
    assert attack_altitude(config.WEAPON_CATALOG["AGM65"], 23.0) == 23.0


def _obs(model: WraithNetModel):
    return model.build_observation("Jet_A", MessageBroker())


def test_transit_altitude_follows_threat_picture():
    model = _model()
    obs = _obs(model)
    cruise = config.JET_FRAME_CATALOG["STRIKE_FIGHTER"].cruise_altitude
    assert transit_altitude(obs, []) == cruise
    sam = TargetInfo(
        target_id="SAM_1",
        position=(30.0, 50.0),
        hp_required=2,
        hp_remaining=2,
        target_type="SAM_SITE",
    )
    assert transit_altitude(obs, [sam]) == 0.0  # low: under surface radar
    far_sam = sam.model_copy(update={"position": (95.0, 95.0)})
    assert transit_altitude(obs, [far_sam]) == cruise  # high: sensors see further
    gun = ObservedGunThreat(
        threat_id="AAA_1", position=(30.0, 30.0), engagement_range=5.0, altitude_ceiling=18.0
    )
    guns = obs.model_copy(update={"observed_gun_threats": [gun]})
    assert transit_altitude(guns, [sam]) == pytest.approx(
        max(cruise, 18.0 + config.GUN_CEILING_MARGIN)
    )


def test_agent_descends_for_jdam_run_in_and_stays_high_far_out():
    model = _model(position=(80.0, 20.0))
    runtime = JetAgentRuntime("Jet_A", MessageBroker())
    runtime._commit("Bunker", 1, "JDAM")
    runtime.set_observation(_obs(model))
    runtime.refresh_world_view()
    near = runtime.decide_intent()
    assert near.desired_altitude == pytest.approx(13.0)

    far_model = _model(position=(5.0, 95.0))
    far = JetAgentRuntime("Jet_A", MessageBroker())
    far._commit("Bunker", 1, "JDAM")
    far.set_observation(_obs(far_model))
    far.refresh_world_view()
    assert far.decide_intent().desired_altitude == pytest.approx(
        config.JET_FRAME_CATALOG["STRIKE_FIGHTER"].cruise_altitude
    )


@pytest.mark.parametrize("frame_type", ["INTERCEPTOR", "STRIKE_FIGHTER", "HEAVY_BOMBER"])
def test_every_frame_flies_over_a_25_tall_wall(frame_type):
    """Slowest climber (bomber, ~1/tick) still clears the tallest roofs."""
    wall = [(14, row) for row in range(20)]
    model = _model(city_cells=wall, frame_type=frame_type)
    frame = config.JET_FRAME_CATALOG[frame_type]
    track = []
    agl = _fly_low(model, 40, track)
    assert model.crash_log == []
    assert min(agl) >= config.TERRAIN_CLEARANCE - 1e-9
    for dz, pitch in track:
        assert dz == pytest.approx(frame.max_speed * math.sin(math.radians(pitch)), abs=1e-6)


def test_altitude_rules_are_per_scenario():
    rules = {
        "terrain_clearance": 6.0,
        "sam_radar_min_engagement_agl": 9.0,
        "gun_ceiling_margin": 5.0,
    }
    model = _model(forest_cells=[(c, 10) for c in range(20)], altitude_rules=rules)
    agl = _fly_low(model, 15)
    assert min(agl) >= 6.0 - 1e-9
    obs = _obs(model)
    assert obs.altitude_rules.gun_ceiling_margin == 5.0
    gun = ObservedGunThreat(
        threat_id="AAA_1", position=(30.0, 30.0), engagement_range=5.0, altitude_ceiling=18.0
    )
    guns = obs.model_copy(update={"observed_gun_threats": [gun]})
    assert transit_altitude(guns, []) == pytest.approx(23.0)
    jet = model.jets["Jet_A"]
    jet.altitude = FOREST_CANOPY_HEIGHT + 8.0  # 8 AGL: above default floor, below 9
    req = LaunchRequest(
        shooter_id="SAM_X",
        target_id="Jet_A",
        weapon_type="SAM_RADAR",
        shooter_position=(50.0, 50.0, 0.0),
        target_position=(jet.pos[0], jet.pos[1], jet.altitude),
        shooter_faction="ENEMY",
        target_faction="FRIENDLY",
        target_class="AIRCRAFT",
        target_signature=config.JET_FRAME_CATALOG["STRIKE_FIGHTER"].signature,
        ammo_available=1,
    )
    assert model._radar_clutter_reason(req) == "below_radar_clutter_floor"


def test_unguided_weapon_not_offered_where_terrain_keeps_jet_too_high():
    """Gun pod (release <= 10) can't be flown low enough beside 25-tall roofs."""
    from wraithnet.agents import compute_bid_proposals
    from wraithnet.weapons_release import release_reachable

    model = _model(position=(70.0, 20.0), city_cells=[(17, 1)])
    obs = _obs(model)
    near_city = model._min_overflight_altitude(model.jets["Jet_A"], (90.0, 10.0))
    assert near_city > config.WEAPON_CATALOG["GUN_POD"].max_release_altitude
    assert not release_reachable(config.WEAPON_CATALOG["GUN_POD"], near_city)
    target = TargetInfo(
        target_id="Bunker",
        position=(90.0, 10.0),
        target_type="HARDENED_BUNKER",
        hp_required=1,
        hp_remaining=1,
        min_overflight_altitude=near_city,
    )
    gun_only = obs.model_copy(update={"payload": {"GUN_POD": 1}})
    assert compute_bid_proposals(gun_only, target) == []
    open_ground = target.model_copy(update={"min_overflight_altitude": 6.0})
    assert [p.weapon_type for p in compute_bid_proposals(gun_only, open_ground)] == ["GUN_POD"]
