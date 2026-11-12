"""Phase 39: flares (IR), notching (radar), inventory/cooldown, seeker specificity."""

from __future__ import annotations

import copy
import dataclasses
import math

import pytest

from wraithnet import config
from wraithnet.combat_rng import keyed_unit_float
from wraithnet.countermeasures import (
    flare_decoy_probability,
    notch_geometry_ok,
    roll_flare_decoy,
)
from wraithnet.missiles import MissileState
from wraithnet.scenario_schema import (
    migrate_v3_to_v4,
    migrate_v4_to_v5,
    scenario_from_data,
)
from wraithnet.schemas import ActionIntent
from wraithnet.world import WraithNetModel


def _place_jet_near(model: WraithNetModel, agent_id: str, xy: tuple[float, float]) -> None:
    jet = model.jets[agent_id]
    model.space.move_agent(jet, xy)


def _inject_missile(
    model: WraithNetModel,
    *,
    missile_id: str,
    weapon_type: str,
    target_id: str,
    shooter_id: str = "SAM_Site_1",
    position: tuple[float, float, float] | None = None,
    support_source_id: str | None = None,
) -> MissileState:
    weapon = config.WEAPON_CATALOG[weapon_type]  # type: ignore[arg-type]
    jet = model.jets[target_id]
    pos = position or (jet.pos[0] + 3.0, jet.pos[1], jet.altitude)
    m = MissileState(
        missile_id=missile_id,
        shooter_id=shooter_id,
        target_id=target_id,
        weapon_type=weapon_type,  # type: ignore[arg-type]
        guidance=weapon.guidance,
        position=pos,
        speed=weapon.speed_per_tick,
        remaining_fuel_ticks=weapon.fuel_ticks,
        support_source_id=support_source_id if support_source_id is not None else shooter_id,
        status="IN_FLIGHT",
        heading_xy_rad=math.atan2(jet.pos[1] - pos[1], jet.pos[0] - pos[0]),
        launch_tick=model.tick,
    )
    model.missiles.append(m)
    model._reserve_damage(missile_id, target_id, weapon.damage_per_hit)
    return m


def test_migrate_v4_to_v5_supplies_frame_countermeasure_defaults():
    raw = copy.deepcopy(config.SCENARIO)
    from wraithnet.scenario_schema import migrate_v1_to_v2, migrate_v2_to_v3

    v4 = migrate_v3_to_v4(migrate_v2_to_v3(migrate_v1_to_v2(dict(raw))))
    assert v4["scenario_version"] == 4
    assert "flare_capacity" not in v4["jets"][0]

    v5 = migrate_v4_to_v5(copy.deepcopy(v4))
    assert v5["scenario_version"] == 5
    jet = v5["jets"][0]
    frame = config.JET_FRAME_CATALOG[jet.get("frame_type", "STRIKE_FIGHTER")]
    assert jet["flare_capacity"] == frame.flare_capacity
    assert jet["flares_remaining"] == frame.flare_capacity
    assert jet["notch_hold_ticks"] == frame.notch_hold_ticks

    scenario = scenario_from_data(config.SCENARIO)
    assert scenario.scenario_version == 9
    assert scenario.jets[0].flare_capacity == frame.flare_capacity


def test_flare_inventory_and_cooldown():
    model = WraithNetModel()
    jet = model.jets["Jet_1"]
    jet.flares_remaining = 2
    jet.flare_cooldown_ticks = 2
    jet.flare_cooldown_remaining = 0
    _inject_missile(model, missile_id="M-ir-1", weapon_type="SAM_IR", target_id="Jet_1")

    model.step(
        {
            "Jet_1": ActionIntent(
                agent_id="Jet_1",
                tick=0,
                action_type="IDLE",
                countermeasure_action="FLARE",
                defensive_threat_id="M-ir-1",
            )
        }
    )
    assert jet.flares_remaining == 1
    assert jet.flare_cooldown_remaining >= 1
    assert any(e.get("event") == "flare" and e.get("accepted") for e in model.missile_events)

    # Cooldown blocks spam on the next tick even with inventory.
    _inject_missile(model, missile_id="M-ir-2", weapon_type="SAM_IR", target_id="Jet_1")
    before = jet.flares_remaining
    model.step(
        {
            "Jet_1": ActionIntent(
                agent_id="Jet_1",
                tick=1,
                action_type="IDLE",
                countermeasure_action="FLARE",
                defensive_threat_id="M-ir-2",
            )
        }
    )
    assert jet.flares_remaining == before  # still on cooldown after tick decrement+fire deny
    assert any(
        e.get("event") == "flare" and e.get("accepted") is False and e.get("reason") == "cooldown"
        for e in model.missile_events
        if e.get("tick") == model.tick
    )


def test_flare_decoys_ir_deterministically(monkeypatch):
    model = WraithNetModel(scenario=scenario_from_data({**config.SCENARIO, "combat_seed": 1}))
    jet = model.jets["Jet_1"]
    jet.flare_quality = 1.0
    monkeypatch.setitem(
        config.WEAPON_CATALOG,
        "SAM_IR",
        dataclasses.replace(
            config.WEAPON_CATALOG["SAM_IR"],
            countermeasure_resistance=0.0,
            speed_per_tick=1.0,
            fuel_ticks=20,
        ),
    )
    m = _inject_missile(
        model,
        missile_id="M-ir-decoy",
        weapon_type="SAM_IR",
        target_id="Jet_1",
        position=(jet.pos[0] + 1.0, jet.pos[1], jet.altitude),
    )
    # Force a success path: high quality + resistance 0 + close range.
    p = flare_decoy_probability(
        flare_quality=1.0,
        countermeasure_resistance=0.0,
        range_to_missile=1.0,
        closing_aspect=1.0,
    )
    assert p > 0.5
    # Find a combat_seed/tick that succeeds with keyed roll, or just run and check.
    model.combat_seed = 0
    # Monkeypatch roll to succeed.
    monkeypatch.setattr(
        "wraithnet.world.roll_flare_decoy",
        lambda *a, **k: True,
    )
    model.step(
        {
            "Jet_1": ActionIntent(
                agent_id="Jet_1",
                tick=0,
                action_type="IDLE",
                countermeasure_action="FLARE",
                defensive_threat_id=m.missile_id,
            )
        }
    )
    assert any(
        e.get("event") == "impact" and e.get("outcome") == "DECOYED"
        for e in model.missile_events
    )
    assert model.flare_successes >= 1
    assert all(x.missile_id != m.missile_id for x in model.active_missiles)


def test_flare_has_zero_radar_effect(monkeypatch):
    model = WraithNetModel()
    jet = model.jets["Jet_1"]
    monkeypatch.setattr("wraithnet.world.roll_flare_decoy", lambda *a, **k: True)
    m = _inject_missile(
        model,
        missile_id="M-radar-1",
        weapon_type="SAM_RADAR",
        target_id="Jet_1",
        position=(jet.pos[0] + 1.0, jet.pos[1], jet.altitude),
    )
    model.step(
        {
            "Jet_1": ActionIntent(
                agent_id="Jet_1",
                tick=0,
                action_type="IDLE",
                countermeasure_action="FLARE",
                defensive_threat_id=m.missile_id,
            )
        }
    )
    assert jet.flares_remaining == jet.flare_capacity - 1
    assert m.status == "IN_FLIGHT" or any(
        e.get("missile_id") == m.missile_id and e.get("outcome") != "DECOYED"
        for e in model.missile_events
        if e.get("event") == "impact"
    )
    assert not any(
        e.get("outcome") == "DECOYED" and e.get("missile_id") == m.missile_id
        for e in model.missile_events
    )


def test_notch_has_zero_ir_effect(monkeypatch):
    model = WraithNetModel()
    jet = model.jets["Jet_1"]
    monkeypatch.setattr("wraithnet.world.roll_notch_break", lambda *a, **k: True)
    m = _inject_missile(
        model,
        missile_id="M-ir-n",
        weapon_type="SAM_IR",
        target_id="Jet_1",
        position=(jet.pos[0] + 5.0, jet.pos[1], jet.altitude),
    )
    # Force perpendicular motion toward a notch heading vs support.
    model.step(
        {
            "Jet_1": ActionIntent(
                agent_id="Jet_1",
                tick=0,
                action_type="IDLE",
                maneuver_defense="NOTCH",
                defensive_threat_id=m.missile_id,
            )
        }
    )
    assert not any(
        e.get("outcome") == "LOST_LOCK" and e.get("missile_id") == m.missile_id
        for e in model.missile_events
    )
    assert any(
        e.get("event") == "notch" and e.get("result") == "wrong_guidance"
        for e in model.missile_events
    )


def test_valid_perpendicular_notch_breaks_radar_lock(monkeypatch):
    model = WraithNetModel()
    jet = model.jets["Jet_1"]
    jet.notch_hold_ticks = 1
    jet.notch_angle_window_deg = 45.0
    jet.notch_min_speed = 1.0
    sam = model.sam_sites["SAM_Site_1"]
    # Place jet east of SAM; LOS bearing ≈ 270 or 90 depending on convention.
    _place_jet_near(model, "Jet_1", (sam.position[0] + 10.0, sam.position[1]))
    m = _inject_missile(
        model,
        missile_id="M-rad-notch",
        weapon_type="SAM_RADAR",
        target_id="Jet_1",
        shooter_id="SAM_Site_1",
        position=(sam.position[0], sam.position[1], config.SAM_SENSOR_HEIGHT),
        support_source_id="SAM_Site_1",
    )
    # Keep missile far from terminal so pre-terminal support geometry applies.
    monkeypatch.setitem(
        config.WEAPON_CATALOG,
        "SAM_RADAR",
        dataclasses.replace(
            config.WEAPON_CATALOG["SAM_RADAR"],
            terminal_range=0.5,
            countermeasure_resistance=0.0,
            speed_per_tick=0.1,
            fuel_ticks=50,
        ),
    )
    monkeypatch.setattr("wraithnet.world.roll_notch_break", lambda *a, **k: True)

    model.step(
        {
            "Jet_1": ActionIntent(
                agent_id="Jet_1",
                tick=0,
                action_type="IDLE",
                maneuver_defense="NOTCH",
                defensive_threat_id=m.missile_id,
            )
        }
    )
    assert model.notch_completions >= 1
    assert model.notch_lock_breaks >= 1
    assert any(
        e.get("event") == "impact" and e.get("outcome") == "LOST_LOCK"
        for e in model.missile_events
    )


def test_wrong_angle_low_speed_insufficient_hold_fails(monkeypatch):
    model = WraithNetModel()
    jet = model.jets["Jet_1"]
    jet.notch_hold_ticks = 3
    jet.notch_min_speed = 4.0
    sam = model.sam_sites["SAM_Site_1"]
    _place_jet_near(model, "Jet_1", (sam.position[0] + 8.0, sam.position[1]))
    m = _inject_missile(
        model,
        missile_id="M-rad-fail",
        weapon_type="SAM_RADAR",
        target_id="Jet_1",
        position=(sam.position[0], sam.position[1], config.SAM_SENSOR_HEIGHT),
    )
    monkeypatch.setattr("wraithnet.world.roll_notch_break", lambda *a, **k: True)

    # IDLE without NOTCH motion → zero speed → geometry_fail.
    model.stage_pre_move_obs(
        {
            "Jet_1": ActionIntent(
                agent_id="Jet_1",
                tick=0,
                action_type="IDLE",
                maneuver_defense="NONE",
            )
        }
    )
    model.stage_apply_movement()
    model.stage_post_move_obs()
    model.stage_register_launches()
    # Manually evaluate notch with zero velocity.
    model._stage_intents = {
        "Jet_1": ActionIntent(
            agent_id="Jet_1",
            tick=model.tick,
            action_type="IDLE",
            maneuver_defense="NOTCH",
            defensive_threat_id=m.missile_id,
        )
    }
    model.stage_countermeasures()
    assert any(
        e.get("event") == "notch" and e.get("result") == "geometry_fail"
        for e in model.missile_events
    )
    assert m.status == "IN_FLIGHT"


def test_notch_geometry_helper_requires_perpendicular_speed():
    assert not notch_geometry_ok(
        velocity_xy=(0.0, 0.0),
        los_bearing_deg=0.0,
        angle_window_deg=30.0,
        min_speed=2.0,
    )
    # Velocity +x (heading 90) vs LOS +y (0) → 90° notch.
    assert notch_geometry_ok(
        velocity_xy=(5.0, 0.0),
        los_bearing_deg=0.0,
        angle_window_deg=30.0,
        min_speed=2.0,
    )
    # Parallel to LOS fails.
    assert not notch_geometry_ok(
        velocity_xy=(0.0, 5.0),
        los_bearing_deg=0.0,
        angle_window_deg=30.0,
        min_speed=2.0,
    )


def test_inbound_threats_in_observation():
    from wraithnet.broker import MessageBroker

    model = WraithNetModel()
    broker = MessageBroker()
    _inject_missile(
        model, missile_id="M-obs", weapon_type="SAM_RADAR", target_id="Jet_2"
    )
    obs = model.build_observation("Jet_2", broker)
    assert any(t.missile_id == "M-obs" for t in obs.inbound_threats)
    assert obs.flares_remaining == model.jets["Jet_2"].flares_remaining
    assert obs.radar_warning_receiver is True


def test_agent_defensive_priority_and_flare(monkeypatch):
    from wraithnet.agents import JetAgentRuntime
    from wraithnet.broker import MessageBroker
    from wraithnet.schemas import InboundThreat, Observation

    broker = MessageBroker()
    runtime = JetAgentRuntime("Jet_1", broker)
    threats = [
        InboundThreat(
            missile_id="M-far",
            guidance="INFRARED",
            estimated_bearing=10.0,
            estimated_time_to_impact=9.0,
        ),
        InboundThreat(
            missile_id="M-near",
            guidance="INFRARED",
            estimated_bearing=20.0,
            estimated_time_to_impact=1.5,
        ),
    ]
    obs = Observation(
        agent_id="Jet_1",
        tick=1,
        current_position=(10.0, 10.0),
        sensor_radius=config.SENSOR_RADIUS,
        payload={"JDAM": 1},
        known_targets=[],
        inbound_threats=threats,
        flares_remaining=4,
        flare_capacity=6,
        flare_cooldown_remaining=0,
    )
    runtime.set_observation(obs)
    intent = runtime.decide_intent()
    assert intent.countermeasure_action == "FLARE"
    assert intent.defensive_threat_id == "M-near"


def test_keyed_decoy_roll_order_independent_of_dict_walk():
    a = keyed_unit_float(3, "decoy", "M-1", "Jet_1", 2)
    b = keyed_unit_float(3, "decoy", "M-1", "Jet_1", 2)
    assert a == b
    assert roll_flare_decoy(3, missile_id="M-1", jet_id="Jet_1", tick=2, probability=1.0)
    assert not roll_flare_decoy(
        3, missile_id="M-1", jet_id="Jet_1", tick=2, probability=0.0
    )


def test_trace_includes_countermeasure_events(monkeypatch):
    from wraithnet.bridge import WraithNetBridge
    from wraithnet.trace_builders import build_trace_tick

    bridge = WraithNetBridge()
    model = bridge.model
    jet = model.jets["Jet_1"]
    monkeypatch.setattr("wraithnet.world.roll_flare_decoy", lambda *a, **k: False)
    m = _inject_missile(
        model,
        missile_id="M-tr",
        weapon_type="SAM_IR",
        target_id="Jet_1",
        position=(jet.pos[0] + 1.0, jet.pos[1], jet.altitude),
    )
    model.step(
        {
            "Jet_1": ActionIntent(
                agent_id="Jet_1",
                tick=0,
                action_type="IDLE",
                countermeasure_action="FLARE",
                defensive_threat_id=m.missile_id,
            )
        }
    )
    tick = build_trace_tick(bridge)
    assert any(c.event == "flare" for c in tick.countermeasures)
    jf = next(j for j in tick.jets if j.agent_id == "Jet_1")
    assert jf.flares_remaining is not None
    assert jf.flares_remaining == jet.flares_remaining


def test_active_terminal_notch_uses_missile_target_geometry(monkeypatch):
    """After terminal activation, notch LOS is missile→target, not support."""
    from wraithnet.countermeasures import is_active_terminal, notch_reference_bearing_deg
    from wraithnet.geometry import bearing_to

    model = WraithNetModel()
    jet = model.jets["Jet_1"]
    _place_jet_near(model, "Jet_1", (40.0, 40.0))
    m = _inject_missile(
        model,
        missile_id="M-term",
        weapon_type="SAM_RADAR",
        target_id="Jet_1",
        position=(42.0, 40.0, jet.altitude),
        support_source_id="SAM_Site_1",
    )
    monkeypatch.setitem(
        config.WEAPON_CATALOG,
        "SAM_RADAR",
        dataclasses.replace(
            config.WEAPON_CATALOG["SAM_RADAR"],
            terminal_range=5.0,
            requires_support=True,
        ),
    )
    tgt = (float(jet.pos[0]), float(jet.pos[1]), float(jet.altitude))
    assert is_active_terminal(
        guidance="RADAR",
        requires_support=True,
        terminal_range=5.0,
        missile_pos=m.position,
        target_pos=tgt,
    )
    # Active-terminal reference is missile→jet, not jet→SAM.
    active_los = notch_reference_bearing_deg(
        jet_pos=(float(jet.pos[0]), float(jet.pos[1])),
        jet_velocity_xy=(0.0, 5.0),
        support_pos=model.sam_sites["SAM_Site_1"].position,
        missile_pos=m.position,
        active_terminal=True,
    )
    support_los = bearing_to(
        (float(jet.pos[0]), float(jet.pos[1])),
        model.sam_sites["SAM_Site_1"].position,
    )
    missile_to_jet = bearing_to(
        (m.position[0], m.position[1]),
        (float(jet.pos[0]), float(jet.pos[1])),
    )
    assert active_los == pytest.approx(missile_to_jet)
    assert active_los != pytest.approx(support_los)


def test_poc_still_solves_with_countermeasures():
    from wraithnet.bridge import WraithNetBridge
    from wraithnet.metrics import summarize
    import asyncio

    bridge = WraithNetBridge()
    asyncio.run(bridge.run(max_ticks=200))
    summary = summarize(bridge.model)
    assert summary["solved"] is True
    assert summary["swarm_wiped_out"] is False
