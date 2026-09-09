"""Phase 38: keyed combat RNG, missiles, v3→v4 migration, SAM launch path."""

from __future__ import annotations

import copy

import pytest

from wraithnet import config
from wraithnet.combat_rng import keyed_unit_float
from wraithnet.missiles import (
    LaunchRequest,
    MissileState,
    advance_missile,
    closest_approach_distance,
    create_missile,
    resolve_impact,
    terminal_contact,
    validate_launch,
)
from wraithnet.scenario_schema import (
    migrate_v2_to_v3,
    migrate_v3_to_v4,
    scenario_from_data,
)
from wraithnet.schemas import ActionIntent
from wraithnet.world import WraithNetModel


def test_keyed_unit_float_reproducible_and_order_independent_parts():
    a = keyed_unit_float(7, "impact", "M-1", "Jet_1", "T1")
    b = keyed_unit_float(7, "impact", "M-1", "Jet_1", "T1")
    assert a == b
    assert 0.0 <= a < 1.0
    # Different part order → different stream (caller must stabilize order).
    c = keyed_unit_float(7, "impact", "Jet_1", "M-1", "T1")
    assert c != a
    # Same parts, different seed.
    assert keyed_unit_float(8, "impact", "M-1", "Jet_1", "T1") != a


def test_missile_flyout_fuel_and_hit():
    req = LaunchRequest(
        shooter_id="Jet_X",
        target_id="T",
        weapon_type="AGM65",
        shooter_position=(0.0, 0.0, 20.0),
        target_position=(0.5, 0.0, 1.8),
        shooter_faction="FRIENDLY",
        target_faction="ENEMY",
        target_class="INSTALLATION",
        target_signature=config.TARGET_SIGNATURE_DEFAULTS["MOBILE_RADAR"],
        target_type="MOBILE_RADAR",
        has_los=True,
        ammo_available=1,
    )
    assert validate_launch(req) is None
    m = create_missile(missile_id="M-test", req=req, launch_tick=1)
    assert m.status == "IN_FLIGHT"
    old = advance_missile(m, req.target_position)
    assert terminal_contact(m, old, req.target_position)
    outcome = resolve_impact(m, combat_seed=0)
    assert outcome == "HIT"  # AGM65 base_hit_probability=1.0


def test_missile_fuel_expires_without_contact():
    m = MissileState(
        missile_id="M-fuel",
        shooter_id="S",
        target_id="T",
        weapon_type="AGM65",
        guidance="INFRARED",
        position=(0.0, 0.0, 20.0),
        speed=0.1,
        remaining_fuel_ticks=1,
        support_source_id=None,
    )
    # Far target; slow missile burns fuel before arriving.
    far = (50.0, 0.0, 1.8)
    advance_missile(m, far)
    from wraithnet.missiles import apply_fuel_expiry

    apply_fuel_expiry(m)
    assert m.status == "FUEL_EXPIRED"


def test_closest_approach_prevents_tunneling():
    # Segment passes within 0.1 of target even though endpoints are farther.
    d = closest_approach_distance(
        (-1.0, 0.0, 0.0),
        (1.0, 0.0, 0.0),
        (0.0, 0.1, 0.0),
    )
    assert d == pytest.approx(0.1)


def test_migrate_v3_to_v4_aim9x_becomes_agm65():
    raw = copy.deepcopy(config.SCENARIO)
    from wraithnet.scenario_schema import migrate_v1_to_v2

    v3 = migrate_v2_to_v3(migrate_v1_to_v2(dict(raw)))
    assert v3["scenario_version"] == 3
    assert v3["jets"][2]["payload"].get("AIM9X") == 2

    v4 = migrate_v3_to_v4(copy.deepcopy(v3))
    assert v4["scenario_version"] == 4
    jet3 = v4["jets"][2]["payload"]
    assert jet3.get("AIM9X", 0) == 0
    assert jet3.get("AGM65") == 2
    assert jet3.get("JDAM") == 3
    sam = v4["sam_sites"][0]
    assert "SAM_RADAR" in sam["missile_loadout"]
    assert sam["missile_loadout"]["SAM_RADAR"] > 0


def test_migrate_amraam_prefers_agm88_when_emitters_present():
    data = {
        "scenario_version": 3,
        "jets": [
            {
                "agent_id": "Jet_1",
                "position": (10.0, 10.0),
                "payload": {"AMRAAM": 2},
                "frame_type": "STRIKE_FIGHTER",
            }
        ],
        "targets": [
            {
                "target_id": "Radar",
                "position": (20.0, 20.0),
                "hp_required": 1,
                "target_type": "MOBILE_RADAR",
            }
        ],
        "sam_sites": [
            {
                "target_id": "SAM_1",
                "position": (30.0, 30.0),
                "hp_required": 1,
                "target_type": "SAM_SITE",
            }
        ],
    }
    v4 = migrate_v3_to_v4(copy.deepcopy(data))
    payload = v4["jets"][0]["payload"]
    # Emitter HP demand = 2 → both AMRAAM become AGM88.
    assert payload.get("AGM88") == 2
    assert payload.get("AGM65", 0) == 0
    assert "AMRAAM" not in payload


def test_default_scenario_loads_as_v4_with_agm65():
    scenario = scenario_from_data(config.SCENARIO)
    assert scenario.scenario_version == 9
    jet3 = next(j for j in scenario.jets if j.agent_id == "Jet_3")
    assert jet3.payload.get("AGM65") == 2
    assert jet3.payload.get("AIM9X", 0) == 0
    sam = scenario.sam_sites[0]
    assert sam.missile_loadout.get("SAM_RADAR", 0) > 0


def test_no_passive_sam_damage_without_missile_event():
    model = WraithNetModel()
    jet = model.jets["Jet_2"]
    # Place jet inside SAM envelope with clear LOS.
    sam = model.sam_sites["SAM_Site_1"]
    model.space.move_agent(jet, sam.position)
    hp_before = jet.hp
    model._apply_sam_damage()  # deprecated no-op
    assert jet.hp == hp_before
    assert not any(
        e.get("event") == "launch" and e.get("shooter_id") == "SAM_Site_1"
        for e in model.missile_events
    )


def test_sam_launch_creates_missile_event_not_passive_hp_drain():
    model = WraithNetModel()
    jet = model.jets["Jet_2"]
    sam = model.sam_sites["SAM_Site_1"]
    model.space.move_agent(jet, (sam.position[0] + 1.0, sam.position[1]))
    ammo_before = sam.missile_loadout.get("SAM_RADAR", 0)
    assert ammo_before > 0
    # Run a full tick with IDLE so SAM register_launches fires.
    model.step({"Jet_2": ActionIntent(agent_id="Jet_2", tick=0, action_type="IDLE")})
    assert any(
        e.get("event") == "launch" and e.get("shooter_id") == "SAM_Site_1"
        for e in model.missile_events
    )
    assert sam.missile_loadout.get("SAM_RADAR", 0) == ammo_before - 1
    # Strike fighter evasion zeroes SAM_RADAR damage_per_hit=1 → HP unchanged.
    assert jet.hp == config.JET_FRAME_CATALOG["STRIKE_FIGHTER"].hp


def test_launch_ack_ammo_only_after_accept_and_reservation():
    model = WraithNetModel()
    jet = model.jets["Jet_3"]
    target = model.targets["Target_B"]
    model.space.move_agent(jet, target.position)
    ammo_before = jet.payload["AGM65"]
    assert model.reserved_damage_for("Target_B") == 0

    jet.payload["AGM65"] = 0
    ack_fail = model.try_launch_from_jet(jet, "Target_B", "AGM65")
    assert ack_fail.accepted is False
    assert ack_fail.reason == "no_ammo"
    assert model.reserved_damage_for("Target_B") == 0

    jet.payload["AGM65"] = ammo_before
    ack = model.try_launch_from_jet(jet, "Target_B", "AGM65")
    assert ack.accepted is True
    assert ack.missile_id is not None
    assert jet.payload["AGM65"] == ammo_before - 1
    assert model.reserved_damage_for("Target_B") == 1
    assert model.uncovered_hp("Target_B") == 0

    ack2 = model.try_launch_from_jet(jet, "Target_B", "AGM65")
    assert ack2.accepted is False
    assert ack2.reason == "target_hp_covered"
    assert jet.payload["AGM65"] == ammo_before - 1


def test_reservation_releases_on_non_hit(monkeypatch):
    import dataclasses

    monkeypatch.setitem(
        config.WEAPON_CATALOG,
        "AGM65",
        dataclasses.replace(
            config.WEAPON_CATALOG["AGM65"],
            speed_per_tick=100.0,
            base_hit_probability=0.0,
            fuel_ticks=5,
            terminal_range=5.0,
        ),
    )
    model = WraithNetModel()
    jet = model.jets["Jet_3"]
    target = model.targets["Target_B"]
    model.space.move_agent(jet, target.position)
    ack = model.try_launch_from_jet(jet, "Target_B", "AGM65")
    assert ack.accepted
    assert model.reserved_damage_for("Target_B") == 1
    model._advance_and_resolve_missiles()
    assert model.reserved_damage_for("Target_B") == 0
    assert target.hp_remaining == 1
    assert any(
        e.get("outcome") == "MISS"
        for e in model.missile_events
        if e.get("event") == "impact"
    )


def test_decide_intent_emits_launch_for_guided_strike_for_direct():
    from wraithnet.agents import JetAgentRuntime
    from wraithnet.broker import MessageBroker
    from wraithnet.schemas import Observation, TargetInfo

    broker = MessageBroker()
    runtime = JetAgentRuntime("Jet_3", broker)
    target = TargetInfo(
        target_id="Target_B",
        position=(25.0, 25.0),
        target_type="MOBILE_RADAR",
        hp_required=1,
        hp_remaining=1,
        claimed=False,
    )
    obs = Observation(
        agent_id="Jet_3",
        tick=1,
        current_position=target.position,
        sensor_radius=config.SENSOR_RADIUS,
        payload={"AGM65": 2, "JDAM": 3},
        known_targets=[target],
        current_heading=0.0,
    )
    runtime.set_observation(obs)
    runtime.refresh_world_view()
    runtime._commit("Target_B", 1, "AGM65")
    intent = runtime.decide_intent()
    assert intent.action_type == "LAUNCH"
    assert intent.weapon_type == "AGM65"

    bunker = TargetInfo(
        target_id="Target_A",
        position=(75.0, 75.0),
        target_type="HARDENED_BUNKER",
        hp_required=5,
        hp_remaining=5,
        claimed=False,
    )
    obs2 = Observation(
        agent_id="Jet_3",
        tick=2,
        current_position=bunker.position,
        sensor_radius=config.SENSOR_RADIUS,
        payload={"JDAM": 3},
        known_targets=[bunker],
        current_heading=0.0,
    )
    runtime.set_observation(obs2)
    runtime.refresh_world_view()
    runtime._commit("Target_A", 1, "JDAM")
    intent2 = runtime.decide_intent()
    assert intent2.action_type == "STRIKE"
    assert intent2.weapon_type == "JDAM"


def test_trace_round_trip_includes_missile_events():
    from wraithnet.bridge import WraithNetBridge
    from wraithnet.trace_builders import build_trace_header, build_trace_tick
    from wraithnet.trace_schemas import TraceHeader, TraceTick

    bridge = WraithNetBridge()
    model = bridge.model
    jet = model.jets["Jet_3"]
    target = model.targets["Target_B"]
    model.space.move_agent(jet, target.position)
    model.step(
        {
            "Jet_3": ActionIntent(
                agent_id="Jet_3",
                tick=0,
                action_type="LAUNCH",
                target_id="Target_B",
                weapon_type="AGM65",
            )
        }
    )
    header = build_trace_header(bridge)
    tick = build_trace_tick(bridge)
    assert header.trace_version == 3
    assert len(tick.weapon_launches) >= 1
    assert tick.weapon_launches[0].weapon_type == "AGM65"
    assert len(tick.weapon_impacts) >= 1 or len(tick.missiles) >= 1

    restored_h = TraceHeader.model_validate_json(header.model_dump_json())
    restored_t = TraceTick.model_validate_json(tick.model_dump_json())
    assert restored_h.trace_version == 3
    assert restored_t.weapon_launches[0].missile_id == tick.weapon_launches[0].missile_id


def test_strike_compat_guided_still_goes_through_ack():
    model = WraithNetModel()
    jet = model.jets["Jet_3"]
    target = model.targets["Target_B"]
    model.space.move_agent(jet, target.position)
    ammo = jet.payload["AGM65"]
    model.step(
        {
            "Jet_3": ActionIntent(
                agent_id="Jet_3",
                tick=0,
                action_type="STRIKE",
                target_id="Target_B",
                weapon_type="AGM65",
            )
        }
    )
    assert jet.payload["AGM65"] == ammo - 1
    assert any(a.accepted for a in model.launch_acks)
    assert target.hp_remaining == 0

