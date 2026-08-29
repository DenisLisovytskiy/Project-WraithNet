"""Unit tests for the Mesa World model and jet physics."""

import math

import pytest

from wraithnet import config
from wraithnet.broker import MessageBroker
from wraithnet.scenario_schema import (
    JetSpec,
    ScenarioConfig,
    TargetSpec,
    _default_scenario,
)
from wraithnet.schemas import ActionIntent
from wraithnet.world import WraithNetModel


def test_model_spawns_exact_scenario():
    model = WraithNetModel()
    assert len(model.jets) == 3
    assert len(model.targets) == 3  # Target_A, Target_B, SAM_Site_1
    assert len(model.sam_sites) == 1
    assert len(model.jammers) == 1
    assert "EW_Jammer_1" in model.jammers

    for j in config.SCENARIO["jets"]:
        jet = model.jets[j["agent_id"]]
        assert jet.payload == j["payload"]
        assert jet.pos == j["position"]
        assert jet.hp == config.JET_HP
        assert jet.is_destroyed is False

    for t in config.SCENARIO["targets"]:
        target = model.targets[t["target_id"]]
        assert target.position == t["position"]
        assert target.hp_required == t["hp_required"]
        assert target.hp_remaining == t["hp_required"]
        assert target.target_type == t["target_type"]

    sam = model.targets["SAM_Site_1"]
    assert sam.target_type == "SAM_SITE"
    assert sam.hp_required == 1
    assert "SAM_Site_1" in model.sam_sites


def test_jet1_payload_and_target_a_hp():
    model = WraithNetModel()
    assert model.jets["Jet_1"].payload == {"JDAM": 1, "AIM9X": 0}
    assert model.targets["Target_A"].hp_required == 5
    assert model.targets["Target_A"].hp_remaining == 5
    assert model.targets["Target_A"].target_type == "HARDENED_BUNKER"


def test_build_observation_for_jet1():
    model = WraithNetModel()
    obs = model.build_observation("Jet_1", MessageBroker())
    assert obs.agent_id == "Jet_1"
    assert obs.tick == 0
    assert obs.current_position == (70.0, 70.0)
    assert obs.sensor_radius == config.SENSOR_RADIUS
    assert obs.comms_jammed is False
    assert obs.payload == {"JDAM": 1, "AIM9X": 0}
    by_id = {t.target_id: t for t in obs.known_targets}
    # Jet_1 is within SENSOR_RADIUS of Target_A, Target_B, and SAM_Site_1
    assert set(by_id) == {"Target_A", "Target_B", "SAM_Site_1"}
    assert by_id["Target_A"].hp_remaining == 5
    assert by_id["Target_A"].target_type == "HARDENED_BUNKER"
    assert by_id["Target_B"].hp_remaining == 1
    assert by_id["Target_B"].target_type == "MOBILE_RADAR"
    assert by_id["SAM_Site_1"].target_type == "SAM_SITE"


def test_build_observation_respects_sensor_radius(monkeypatch):
    model = WraithNetModel()
    monkeypatch.setattr(config, "SENSOR_RADIUS", 10.0)
    # Jet_1 is ~7.07 from Target_A (inside) and ~63.64 from Target_B (outside).
    obs = model.build_observation("Jet_1", MessageBroker())
    assert {t.target_id for t in obs.known_targets} == {"Target_A"}


def test_v3_scenario_initial_sensor_visibility():
    """Load-bearing geometry: Jet_1 sees A+B(+SAM); Jet_2/3 start outside A/B range."""
    model = WraithNetModel()
    jet1 = model.jets["Jet_1"]
    jet2 = model.jets["Jet_2"]
    jet3 = model.jets["Jet_3"]
    assert math.dist(jet1.pos, model.targets["Target_A"].position) <= config.SENSOR_RADIUS
    assert math.dist(jet1.pos, model.targets["Target_B"].position) <= config.SENSOR_RADIUS
    assert math.dist(jet2.pos, model.targets["Target_A"].position) > config.SENSOR_RADIUS
    assert math.dist(jet2.pos, model.targets["Target_B"].position) > config.SENSOR_RADIUS
    assert math.dist(jet3.pos, model.targets["Target_A"].position) > config.SENSOR_RADIUS
    assert math.dist(jet3.pos, model.targets["Target_B"].position) > config.SENSOR_RADIUS
    # Jet_2 starts within sensor range of SAM_Site_1 (on its path to Target_A)
    assert math.dist(jet2.pos, model.targets["SAM_Site_1"].position) <= config.SENSOR_RADIUS

    broker = MessageBroker()
    jet1_ids = {t.target_id for t in model.build_observation("Jet_1", broker).known_targets}
    assert {"Target_A", "Target_B", "SAM_Site_1"} <= jet1_ids
    jet2_ids = {t.target_id for t in model.build_observation("Jet_2", broker).known_targets}
    assert "SAM_Site_1" in jet2_ids
    assert "Target_A" not in jet2_ids
    assert "Target_B" not in jet2_ids
    assert model.build_observation("Jet_3", broker).known_targets == []


def test_build_observation_reflects_broker_claims():
    model = WraithNetModel()
    broker = MessageBroker()
    assert broker.try_open_contract("Target_A", "c1", "Jet_1", tick=0) is True
    obs = model.build_observation("Jet_1", broker)
    by_id = {t.target_id: t for t in obs.known_targets}
    assert by_id["Target_A"].claimed is True
    assert by_id["Target_B"].claimed is False


def test_move_clamped_to_jet_speed():
    model = WraithNetModel()
    jet = model.jets["Jet_2"]
    old_pos = jet.pos
    destination = (old_pos[0] + 50.0, old_pos[1])  # farther than JET_SPEED
    intent = ActionIntent(
        agent_id="Jet_2",
        tick=0,
        action_type="MOVE",
        target_position=destination,
    )
    model.step({"Jet_2": intent})
    moved = math.dist(old_pos, jet.pos)
    assert moved == pytest.approx(config.JET_SPEED)
    assert jet.distance_traveled == pytest.approx(config.JET_SPEED)


def test_move_does_not_overshoot_near_destination():
    model = WraithNetModel()
    jet = model.jets["Jet_2"]
    old_pos = jet.pos
    short = 2.0
    destination = (old_pos[0] + short, old_pos[1])
    intent = ActionIntent(
        agent_id="Jet_2",
        tick=0,
        action_type="MOVE",
        target_position=destination,
    )
    model.step({"Jet_2": intent})
    assert jet.pos == destination
    assert jet.distance_traveled == pytest.approx(short)


def test_strike_out_of_range_is_noop():
    model = WraithNetModel()
    jet = model.jets["Jet_1"]
    target = model.targets["Target_A"]
    assert math.dist(jet.pos, target.position) > config.STRIKE_RANGE
    intent = ActionIntent(
        agent_id="Jet_1",
        tick=0,
        action_type="STRIKE",
        target_id="Target_A",
        weapon_type="JDAM",
    )
    model.step({"Jet_1": intent})
    assert jet.payload == {"JDAM": 1, "AIM9X": 0}
    assert target.hp_remaining == 5
    assert model.strike_log == []


def test_strike_in_range_damages_and_logs():
    model = WraithNetModel()
    jet = model.jets["Jet_1"]
    target = model.targets["Target_A"]
    model.space.move_agent(jet, target.position)
    intent = ActionIntent(
        agent_id="Jet_1",
        tick=0,
        action_type="STRIKE",
        target_id="Target_A",
        weapon_type="JDAM",
    )
    model.step({"Jet_1": intent})
    assert jet.payload == {"JDAM": 0, "AIM9X": 0}
    assert target.hp_remaining == 4
    assert model.strike_log == [
        {"tick": 1, "agent_id": "Jet_1", "target_id": "Target_A", "weapon_type": "JDAM"}
    ]


def test_strike_unsuitable_weapon_is_noop():
    """World-layer suitability guard: AIM9X cannot damage HARDENED_BUNKER."""
    model = WraithNetModel()
    jet = model.jets["Jet_3"]
    target = model.targets["Target_A"]
    model.space.move_agent(jet, target.position)
    payload_before = dict(jet.payload)
    hp_before = target.hp_remaining
    intent = ActionIntent(
        agent_id="Jet_3",
        tick=0,
        action_type="STRIKE",
        target_id="Target_A",
        weapon_type="AIM9X",
    )
    model.step({"Jet_3": intent})
    assert jet.payload == payload_before
    assert target.hp_remaining == hp_before
    assert model.strike_log == []


def test_strike_suitable_jdam_on_hardened_bunker():
    model = WraithNetModel()
    jet = model.jets["Jet_2"]
    target = model.targets["Target_A"]
    model.space.move_agent(jet, target.position)
    jdam_before = jet.payload["JDAM"]
    intent = ActionIntent(
        agent_id="Jet_2",
        tick=0,
        action_type="STRIKE",
        target_id="Target_A",
        weapon_type="JDAM",
    )
    model.step({"Jet_2": intent})
    assert jet.payload["JDAM"] == jdam_before - 1
    assert target.hp_remaining == 4
    assert model.strike_log[-1]["weapon_type"] == "JDAM"


def test_strike_out_of_range_does_not_append_strike_log():
    model = WraithNetModel()
    intent = ActionIntent(
        agent_id="Jet_1",
        tick=0,
        action_type="STRIKE",
        target_id="Target_A",
        weapon_type="JDAM",
    )
    model.step({"Jet_1": intent})
    assert model.strike_log == []


def test_strike_with_zero_payload_in_range_is_noop():
    model = WraithNetModel()
    jet = model.jets["Jet_1"]
    target = model.targets["Target_A"]
    model.space.move_agent(jet, target.position)
    jet.payload = {"JDAM": 0, "AIM9X": 0}
    intent = ActionIntent(
        agent_id="Jet_1",
        tick=0,
        action_type="STRIKE",
        target_id="Target_A",
        weapon_type="JDAM",
    )
    model.step({"Jet_1": intent})
    assert target.hp_remaining == 5
    assert model.strike_log == []


def test_strike_on_destroyed_target_clamps_hp_but_consumes_payload():
    """Documented semantics: in-range STRIKE always consumes payload;
    hp_remaining stays clamped at 0 even if the target is already dead."""
    model = WraithNetModel()
    jet = model.jets["Jet_3"]
    target = model.targets["Target_B"]  # MOBILE_RADAR — needs AIM9X
    model.space.move_agent(jet, target.position)
    target.hp_remaining = 0
    aim9x_before = jet.payload["AIM9X"]
    intent = ActionIntent(
        agent_id="Jet_3",
        tick=0,
        action_type="STRIKE",
        target_id="Target_B",
        weapon_type="AIM9X",
    )
    model.step({"Jet_3": intent})
    assert target.hp_remaining == 0
    assert jet.payload["AIM9X"] == aim9x_before - 1
    assert len(model.strike_log) == 1
    assert model.strike_log[0]["weapon_type"] == "AIM9X"


def test_idle_intent_changes_nothing():
    model = WraithNetModel()
    jet = model.jets["Jet_1"]
    pos_before = jet.pos
    payload_before = dict(jet.payload)
    hp_before = {tid: t.hp_remaining for tid, t in model.targets.items()}
    intent = ActionIntent(agent_id="Jet_1", tick=0, action_type="IDLE")
    model.step({"Jet_1": intent})
    assert jet.pos == pos_before
    assert jet.payload == payload_before
    assert jet.distance_traveled == 0.0
    assert {tid: t.hp_remaining for tid, t in model.targets.items()} == hp_before
    assert model.strike_log == []


def test_empty_step_increments_tick_only():
    model = WraithNetModel()
    jet_state = {
        aid: (j.pos, dict(j.payload), j.distance_traveled)
        for aid, j in model.jets.items()
    }
    hp_before = {tid: t.hp_remaining for tid, t in model.targets.items()}
    model.step({})
    assert model.tick == 1
    for aid, j in model.jets.items():
        assert (j.pos, dict(j.payload), j.distance_traveled) == jet_state[aid]
    assert {tid: t.hp_remaining for tid, t in model.targets.items()} == hp_before


def test_datacollector_total_payload_remaining_column():
    from wraithnet.world import JetAgent

    model = WraithNetModel()
    model.step({})
    model.step({})
    model.datacollector.collect(model)
    agent_df = model.datacollector.get_agenttype_vars_dataframe(JetAgent)
    assert "TotalPayloadRemaining" in agent_df.columns


def test_running_true_after_construction():
    model = WraithNetModel()
    assert model.running is True


def test_running_false_when_all_targets_destroyed():
    model = WraithNetModel()
    for t in model.targets.values():
        t.hp_remaining = 0
    model.step({})
    assert model.running is False
    assert model.swarm_wiped_out is False


def test_running_stays_true_if_only_one_target_destroyed():
    model = WraithNetModel()
    model.targets["Target_B"].hp_remaining = 0
    model.step({})
    assert model.running is True


def test_step_is_not_shadowed_by_mesas_internal_wrapped_step():
    model = WraithNetModel()
    assert model.step.__func__ is WraithNetModel.step


def test_step_advances_mesa_time_so_agenttype_records_are_not_collapsed():
    """Regression: self.step = self._user_step (Phase 1) bypasses Mesa's own
    scheduler, which is what normally advances self.steps/self.time. DataCollector
    keys per-tick agent_reporters/agenttype_reporters storage by model.time
    internally — if it never advances, every tick's snapshot overwrites the same
    entry and get_agenttype_vars_dataframe() silently returns only the last tick
    instead of a full time series (model_reporters are unaffected: they append to
    a plain list, not a time-keyed dict)."""
    from wraithnet.world import JetAgent

    model = WraithNetModel()
    assert model.time == 0.0
    assert model.steps == 0
    model.step({})
    assert model.time == 1.0
    assert model.steps == 1
    model.step({})
    assert model.time == 2.0
    assert model.steps == 2

    agent_df = model.datacollector.get_agenttype_vars_dataframe(JetAgent)
    steps_seen = {step for step, _agent_id in agent_df.index}
    assert steps_seen == {0.0, 1.0, 2.0}, (
        f"expected one distinct row-group per collected tick, got {steps_seen} — "
        "agenttype_reporters records are being collapsed onto a single tick"
    )


def test_sam_damage_in_range_and_out_of_range():
    model = WraithNetModel()
    jet = model.jets["Jet_1"]
    sam = model.sam_sites["SAM_Site_1"]
    hp_before = jet.hp
    # Place jet inside engagement range
    model.space.move_agent(jet, sam.position)
    model._apply_sam_damage()
    assert jet.hp == hp_before - config.SAM_DAMAGE_PER_TICK

    # Outside range — unaffected
    far = model.jets["Jet_3"]
    far_hp = far.hp
    assert math.dist(far.pos, sam.position) > config.SAM_ENGAGEMENT_RANGE
    model._apply_sam_damage()
    assert far.hp == far_hp


def test_destroyed_sam_deals_no_damage():
    model = WraithNetModel()
    jet = model.jets["Jet_1"]
    sam = model.sam_sites["SAM_Site_1"]
    model.space.move_agent(jet, sam.position)
    model.targets["SAM_Site_1"].hp_remaining = 0
    hp_before = jet.hp
    model._apply_sam_damage()
    assert jet.hp == hp_before


def test_jet_destroyed_removed_from_jets_and_space():
    model = WraithNetModel()
    jet = model.jets["Jet_1"]
    sam = model.sam_sites["SAM_Site_1"]
    model.space.move_agent(jet, sam.position)
    # Drain HP via repeated SAM damage
    while "Jet_1" in model.jets:
        model._apply_sam_damage()
    assert "Jet_1" not in model.jets
    assert jet not in model.space.agents


def test_swarm_wipeout_terminates_as_failure():
    model = WraithNetModel()
    for jet in list(model.jets.values()):
        jet.hp = 0
    model.step({})
    assert model.running is False
    assert model.swarm_wiped_out is True
    assert len(model.jets) == 0


def test_ew_jammer_spawns_from_scenario():
    model = WraithNetModel()
    assert "EW_Jammer_1" in model.jammers
    jammer = model.jammers["EW_Jammer_1"]
    assert jammer.position == (46.0, 19.0)
    assert jammer.jammer_id == "EW_Jammer_1"


def test_comms_jammed_true_inside_jam_radius_false_outside():
    model = WraithNetModel()
    broker = MessageBroker()
    jet = model.jets["Jet_1"]
    jammer = model.jammers["EW_Jammer_1"]

    model.space.move_agent(jet, jammer.position)
    obs_in = model.build_observation("Jet_1", broker)
    assert obs_in.comms_jammed is True

    far = (0.0, 0.0)
    assert math.dist(far, jammer.position) > config.JAM_RADIUS
    model.space.move_agent(jet, far)
    obs_out = model.build_observation("Jet_1", broker)
    assert obs_out.comms_jammed is False


def test_jet3_path_to_target_b_crosses_jammer():
    """Geometric property from v3 §2: Jet_3 → Target_B passes through jam radius."""
    model = WraithNetModel()
    start = model.jets["Jet_3"].pos
    end = model.targets["Target_B"].position
    jammer = model.jammers["EW_Jammer_1"].position
    broker = MessageBroker()
    jammed_at_some_point = False
    for i in range(21):
        t = i / 20.0
        pos = (start[0] + t * (end[0] - start[0]), start[1] + t * (end[1] - start[1]))
        model.space.move_agent(model.jets["Jet_3"], pos)
        if model.build_observation("Jet_3", broker).comms_jammed:
            jammed_at_some_point = True
            break
    assert jammed_at_some_point, (
        f"Jet_3 path from {start} to {end} never entered JAM_RADIUS of {jammer}"
    )


def test_bare_model_still_spawns_the_builtin_poc_scenario():
    """Phase 14: omitting `scenario` must spawn the same world as before this phase."""
    model = WraithNetModel()
    default = _default_scenario()

    assert set(model.jets) == {j.agent_id for j in default.jets}
    assert set(model.targets) == (
        {t.target_id for t in default.targets} | {s.target_id for s in default.sam_sites}
    )
    assert set(model.sam_sites) == {s.target_id for s in default.sam_sites}
    assert set(model.jammers) == {j.jammer_id for j in default.jammers}

    for spec in default.jets:
        jet = model.jets[spec.agent_id]
        assert jet.pos == spec.position
        assert jet.payload == dict(spec.payload)
        assert jet.hp == config.JET_HP

    for spec in default.targets:
        target = model.targets[spec.target_id]
        assert target.position == spec.position
        assert target.hp_required == spec.hp_required
        assert target.hp_remaining == spec.hp_required
        assert target.target_type == spec.target_type

    for spec in default.sam_sites:
        target = model.targets[spec.target_id]
        assert target.position == spec.position
        assert target.hp_required == spec.hp_required
        assert target.target_type == spec.target_type
        assert spec.target_id in model.sam_sites
        assert model.sam_sites[spec.target_id].position == spec.position

    for spec in default.jammers:
        jammer = model.jammers[spec.jammer_id]
        assert jammer.position == spec.position

    assert model.space.x_max == default.grid_width
    assert model.space.y_max == default.grid_height


def test_model_spawns_custom_scenario_not_default():
    custom = ScenarioConfig(
        jets=[
            JetSpec(
                agent_id="Solo_Jet",
                position=(5.0, 5.0),
                payload={"JDAM": 2, "AIM9X": 0},
            )
        ],
        targets=[
            TargetSpec(
                target_id="Custom_Bunker",
                position=(8.0, 8.0),
                hp_required=1,
                target_type="HARDENED_BUNKER",
            )
        ],
    )
    model = WraithNetModel(scenario=custom)

    assert set(model.jets) == {"Solo_Jet"}
    assert set(model.targets) == {"Custom_Bunker"}
    assert model.sam_sites == {}
    assert model.jammers == {}
    assert "Jet_1" not in model.jets
    assert "Target_A" not in model.targets
    assert "SAM_Site_1" not in model.targets
    assert "EW_Jammer_1" not in model.jammers

    jet = model.jets["Solo_Jet"]
    assert jet.pos == (5.0, 5.0)
    assert jet.payload == {"JDAM": 2, "AIM9X": 0}
    target = model.targets["Custom_Bunker"]
    assert target.position == (8.0, 8.0)
    assert target.hp_required == 1
    assert target.target_type == "HARDENED_BUNKER"


def test_model_uses_scenario_grid_size_not_config_constants():
    custom = ScenarioConfig(
        grid_width=40.0,
        grid_height=60.0,
        jets=[
            JetSpec(
                agent_id="Solo_Jet",
                position=(1.0, 1.0),
                payload={"JDAM": 1, "AIM9X": 0},
            )
        ],
        targets=[
            TargetSpec(
                target_id="Custom_Bunker",
                position=(2.0, 2.0),
                hp_required=1,
                target_type="HARDENED_BUNKER",
            )
        ],
    )
    model = WraithNetModel(scenario=custom)
    assert model.space.x_max == 40.0
    assert model.space.y_max == 60.0
    assert model.space.x_max != config.GRID_WIDTH
    assert model.space.y_max != config.GRID_HEIGHT
