"""Unit tests for JetAgentRuntime message loop and Phase 21 decide_intent."""

import asyncio
import math
import uuid

import pytest

from wraithnet import config
from wraithnet.agents import JetAgentRuntime, compute_bid
from wraithnet.broker import MessageBroker
from wraithnet.config import BROADCAST_ID
from wraithnet.geometry import is_in_firing_arc
from wraithnet.scenario_schema import JetSpec, ScenarioConfig, TargetSpec, new_scenario_data, scenario_from_data
from wraithnet.schemas import Message, Observation, TargetInfo
from wraithnet.world import WraithNetModel


def make_bid(sender_id, receiver_id, target_id="Target_A", tick=1):
    return Message(
        message_id=uuid.uuid4().hex,
        contract_id="contract-1",
        tick=tick,
        sender_id=sender_id,
        receiver_id=receiver_id,
        message_type="BID",
        target_id=target_id,
        payload_offered=1,
        bid_utility=0.5,
        weapon_type="JDAM",
    )


def make_cfp(sender_id, target_id="Target_A", tick=1):
    return Message(
        message_id=uuid.uuid4().hex,
        contract_id="contract-1",
        tick=tick,
        sender_id=sender_id,
        receiver_id=BROADCAST_ID,
        message_type="CFP",
        target_id=target_id,
        hp_remaining=2,
    )


async def _wait_until(predicate, timeout: float = 0.5) -> None:
    async def _poll() -> None:
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_poll(), timeout=timeout)


@pytest.mark.asyncio
async def test_broadcast_cfp_received_by_other_runtimes():
    broker = MessageBroker()
    jet1 = JetAgentRuntime("Jet_1", broker)
    jet2 = JetAgentRuntime("Jet_2", broker)
    jet3 = JetAgentRuntime("Jet_3", broker)
    tasks = [jet1.start(), jet2.start(), jet3.start()]

    try:
        await broker.send(make_cfp("Jet_1"))
        await _wait_until(
            lambda: len(jet2.received_messages) == 1 and len(jet3.received_messages) == 1
        )
        assert jet2.received_messages[0].message_type == "CFP"
        assert jet2.received_messages[0].sender_id == "Jet_1"
        assert jet3.received_messages[0].message_type == "CFP"
        assert jet3.received_messages[0].sender_id == "Jet_1"
        assert jet1.received_messages == []
    finally:
        for jet in (jet1, jet2, jet3):
            jet.stop()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_direct_bid_recorded_only_by_receiver_runtime():
    broker = MessageBroker()
    jet1 = JetAgentRuntime("Jet_1", broker)
    jet2 = JetAgentRuntime("Jet_2", broker)
    jet3 = JetAgentRuntime("Jet_3", broker)
    tasks = [jet1.start(), jet2.start(), jet3.start()]

    try:
        await broker.send(make_bid("Jet_2", "Jet_1"))
        await _wait_until(lambda: len(jet1.received_messages) == 1)
        assert jet1.received_messages[0].message_type == "BID"
        assert jet1.received_messages[0].sender_id == "Jet_2"
        assert jet3.received_messages == []
    finally:
        for jet in (jet1, jet2, jet3):
            jet.stop()
        await asyncio.gather(*tasks, return_exceptions=True)


def _bunker(**overrides) -> TargetInfo:
    data = {
        "target_id": "Bunker",
        "position": (50.3, 50.0),
        "target_type": "HARDENED_BUNKER",
        "hp_required": 5,
        "hp_remaining": 5,
        "claimed": False,
    }
    data.update(overrides)
    return TargetInfo(**data)


def _gunner_obs(
    position: tuple[float, float],
    heading: float,
    payload: dict[str, int],
    targets: list[TargetInfo],
) -> Observation:
    return Observation(
        agent_id="Gunner",
        tick=0,
        current_position=position,
        sensor_radius=config.SENSOR_RADIUS,
        payload=payload,
        known_targets=targets,
        current_heading=heading,
    )


def test_compute_bid_jdam_against_bunker_matches_pre_catalog_formula():
    """Formula shape is unchanged; only the suitability lookup moved."""
    target = _bunker(
        position=(75.0, 75.0), target_id="Target_A", hp_required=4, hp_remaining=4
    )
    obs = _gunner_obs(target.position, 0.0, {"JDAM": 4, "AIM9X": 0}, [target])
    result = compute_bid(obs, target)
    assert result is not None
    utility, offered, weapon_type = result
    assert utility == pytest.approx(
        config.UTILITY_W_WEAPON * 1.0 + config.UTILITY_W1 * 1.0
    )
    assert offered == 4
    assert weapon_type == "JDAM"


def test_compute_bid_agm65_still_required_for_mobile_radar():
    target = _bunker(
        target_id="Radar",
        position=(25.0, 25.0),
        target_type="MOBILE_RADAR",
        hp_required=1,
        hp_remaining=1,
    )
    jdam_only = _gunner_obs(target.position, 0.0, {"JDAM": 4, "AGM65": 0}, [target])
    mixed = _gunner_obs(target.position, 0.0, {"JDAM": 3, "AGM65": 2}, [target])
    assert compute_bid(jdam_only, target) is None
    result = compute_bid(mixed, target)
    assert result is not None
    assert result[2] == "AGM65"


def test_decide_intent_rotates_to_bear_when_in_range_but_outside_arc():
    broker = MessageBroker()
    jet = JetAgentRuntime("Gunner", broker)
    target = _bunker()
    # Heading 0° (+y); target is due +x — 90° off, GUN_POD cone is ±30°.
    jet.set_observation(_gunner_obs((50.0, 50.0), 0.0, {"GUN_POD": 1}, [target]))
    jet.refresh_world_view()
    jet._commit("Bunker", 1, "GUN_POD")
    payload_before = jet.committed_payload
    intent = jet.decide_intent()
    assert intent.action_type == "IDLE"
    assert intent.desired_heading == pytest.approx(90.0)
    assert jet.committed_payload == payload_before
    assert intent.weapon_type is None


def test_decide_intent_strikes_when_narrow_arc_weapon_is_in_arc():
    broker = MessageBroker()
    jet = JetAgentRuntime("Gunner", broker)
    target = _bunker()
    # GUN_POD releases at or below max_release_altitude
    obs = _gunner_obs((50.0, 50.0), 90.0, {"GUN_POD": 1}, [target])
    jet.set_observation(obs.model_copy(update={"current_altitude": 8.0}))
    jet.refresh_world_view()
    jet._commit("Bunker", 2, "GUN_POD")
    intent = jet.decide_intent()
    assert intent.action_type == "STRIKE"
    assert intent.target_id == "Bunker"
    assert intent.weapon_type == "GUN_POD"
    assert jet.committed_payload == 1


def test_decide_intent_move_sets_desired_heading_while_closing():
    broker = MessageBroker()
    jet = JetAgentRuntime("Gunner", broker)
    target = _bunker(position=(75.0, 75.0), target_id="Target_A")
    jet.set_observation(
        _gunner_obs((70.0, 70.0), 0.0, {"JDAM": 1, "AIM9X": 0}, [target])
    )
    jet.refresh_world_view()
    jet._commit("Target_A", 1, "JDAM")
    intent = jet.decide_intent()
    assert intent.action_type == "MOVE"
    # Closing aims at a release gate just inside weapon range.
    weapon = config.WEAPON_CATALOG["JDAM"]
    dist = math.dist((70.0, 70.0), target.position)
    ux = (70.0 - target.position[0]) / dist
    uy = (70.0 - target.position[1]) / dist
    standoff = max(weapon.range * 0.5, 1e-3)
    expected = (
        target.position[0] + ux * standoff,
        target.position[1] + uy * standoff,
    )
    assert intent.target_position == pytest.approx(expected)
    assert intent.desired_heading == pytest.approx(45.0)


def test_agents_and_world_agree_on_firing_arc_gate():
    """Helper prediction vs _apply_strike actually landing, including edges."""
    dy = 10.0
    edge_dx = dy * math.tan(math.radians(30.0))
    out_dx = dy * math.tan(math.radians(31.0))
    model = WraithNetModel(
        scenario=scenario_from_data(new_scenario_data(
            jets=[
                JetSpec(
                    agent_id="Gunner",
                    position=(50.0, 50.0),
                    payload={"GUN_POD": 1},
                    initial_heading=0.0,
                )
            ],
            targets=[
                TargetSpec(
                    target_id="Bunker",
                    position=(50.0 + edge_dx, 50.0 + dy),
                    hp_required=20,
                    target_type="HARDENED_BUNKER",
                )
            ],
        ))
    )
    jet = model.jets["Gunner"]
    target = model.targets["Bunker"]
    weapon = config.WEAPON_CATALOG["GUN_POD"]
    jet.altitude = 8.0  # GUN_POD releases at or below max_release_altitude
    cases = [
        ((50.0 + edge_dx, 50.0 + dy), 0.0, True),
        ((50.0 + out_dx, 50.0 + dy), 0.0, False),
        ((50.0, 60.0), 0.0, True),
        ((50.0, 40.0), 0.0, False),
        ((50.3, 50.0), 90.0, True),
        ((50.3, 50.0), 0.0, False),
    ]
    for tpos, heading, should_hit in cases:
        model.space.move_agent(jet, (50.0, 50.0))
        jet.heading = heading
        target.position = tpos
        jet.payload["GUN_POD"] = 1
        hp_before = target.hp_remaining
        helper = is_in_firing_arc(heading, jet.pos, tpos, weapon.firing_arc_deg)
        assert helper is should_hit, (tpos, heading)
        world_gate = model._target_in_firing_arc(jet, target, weapon)
        assert world_gate is helper
        in_range = math.dist(jet.pos, tpos) <= weapon.range
        if not in_range:
            continue
        model._apply_strike(jet, "Bunker", "GUN_POD")
        if should_hit:
            assert jet.payload["GUN_POD"] == 0
            assert target.hp_remaining == hp_before - weapon.damage_per_hit
            target.hp_remaining = hp_before
        else:
            assert jet.payload["GUN_POD"] == 1
            assert target.hp_remaining == hp_before


def test_narrow_arc_weapon_rotates_into_arc_before_first_strike():
    """Acceptance: a GUN_POD jet starting 90° off its target turns over
    several ticks and only then lands a strike — not immediately, not never.
    The jet cannot hover while turning, so it may re-close with MOVE."""
    jet_pos = (50.0, 50.0)
    target_pos = (50.3, 50.0)
    model = WraithNetModel(
        scenario=scenario_from_data(new_scenario_data(
            jets=[
                JetSpec(
                    agent_id="Gunner",
                    position=jet_pos,
                    payload={"GUN_POD": 1},
                    initial_heading=0.0,
                )
            ],
            targets=[
                TargetSpec(
                    target_id="Bunker",
                    position=target_pos,
                    hp_required=5,
                    target_type="HARDENED_BUNKER",
                )
            ],
        ))
    )
    runtime = JetAgentRuntime("Gunner", MessageBroker())
    runtime._commit("Bunker", 1, "GUN_POD")
    idle_ticks = 0
    struck = False
    for _ in range(60):
        world_jet = model.jets["Gunner"]
        obs = model.build_observation("Gunner", runtime.broker)
        runtime.set_observation(obs)
        runtime.refresh_world_view()
        intent = runtime.decide_intent()
        if intent.action_type == "STRIKE":
            model.step({"Gunner": intent})
            struck = True
            break
        assert intent.action_type in ("IDLE", "MOVE")
        assert intent.desired_heading is not None
        idle_ticks += 1
        model.step({"Gunner": intent})
        assert world_jet.payload["GUN_POD"] == 1
    assert idle_ticks >= 1
    assert struck
    assert model.jets["Gunner"].payload["GUN_POD"] == 0
    assert model.targets["Bunker"].hp_remaining == 3
    assert model.strike_log[-1]["weapon_type"] == "GUN_POD"


def test_bingo_fuel_reached_uses_leq_including_exact_equality():
    broker = MessageBroker()
    jet = JetAgentRuntime("Gunner", broker)
    frame = config.JET_FRAME_CATALOG["STRIKE_FIGHTER"]
    home = (0.0, 0.0)
    position = (0.0, 10.0)  # exactly 10 units north of home
    ticks_home = 10.0 / frame.max_speed
    bingo = ticks_home * frame.fuel_burn_per_tick * config.RTB_FUEL_SAFETY_MARGIN
    target = _bunker()
    jet.set_observation(
        _gunner_obs(
            position,
            0.0,
            {"JDAM": 1, "AIM9X": 0},
            [target],
        )
    )
    jet.latest_observation = jet.latest_observation.model_copy(
        update={"home_base": home, "fuel": bingo, "frame_type": "STRIKE_FIGHTER"}
    )
    assert jet.bingo_fuel_reached() is True
    jet.latest_observation = jet.latest_observation.model_copy(
        update={"fuel": bingo + 1e-9}
    )
    assert jet.bingo_fuel_reached() is False
    jet.role = "RTB"
    jet.latest_observation = jet.latest_observation.model_copy(update={"fuel": 0.0})
    assert jet.bingo_fuel_reached() is False  # already RTBing


def test_begin_rtb_aborts_open_management_and_is_idempotent():
    broker = MessageBroker()
    manager = JetAgentRuntime("Manager", broker)
    target = _bunker()
    manager.set_observation(
        _gunner_obs((50.0, 50.0), 0.0, {"JDAM": 1, "AIM9X": 0}, [target])
    )
    assert broker.try_open_contract("Bunker", "c-rtb", "Manager", tick=0) is True
    manager.role = "MANAGER"
    manager.managed_target_id = "Bunker"
    manager.open_contract_id = "c-rtb"
    manager.committed_target_id = "Bunker"
    manager.committed_weapon_type = "JDAM"
    manager.begin_rtb()
    assert manager.role == "RTB"
    assert manager.committed_target_id is None
    assert manager.committed_weapon_type is None
    assert manager.managed_target_id is None
    assert broker.is_claimed("Bunker") is False
    manager.begin_rtb()  # idempotent
    assert manager.role == "RTB"
    assert broker.is_claimed("Bunker") is False


def test_decide_intent_rtb_always_moves_home():
    broker = MessageBroker()
    jet = JetAgentRuntime("Gunner", broker)
    home = (2.0, 2.0)
    position = (10.0, 10.0)
    target = _bunker()
    jet.set_observation(
        _gunner_obs(position, 0.0, {"JDAM": 1, "AIM9X": 0}, [target])
    )
    jet.latest_observation = jet.latest_observation.model_copy(
        update={"home_base": home}
    )
    jet.refresh_world_view()
    jet._commit("Bunker", 1, "JDAM")
    jet.begin_rtb()
    intent = jet.decide_intent()
    assert intent.action_type == "MOVE"
    assert intent.target_position == home
    assert intent.desired_heading == pytest.approx(
        math.degrees(math.atan2(home[0] - position[0], home[1] - position[1])) % 360.0
    )


def test_decide_intent_idle_seeks_briefed_destroy_waypoint():
    """Uncommitted jets transit toward mission-brief DESTROY positions."""
    broker = MessageBroker()
    jet = JetAgentRuntime("Gunner", broker)
    position = (10.0, 10.0)
    waypoint = (80.0, 80.0)
    obs = _gunner_obs(position, 0.0, {"JDAM": 1, "AIM9X": 0}, [])
    jet.set_observation(
        obs.model_copy(update={"briefed_seek_positions": [waypoint]})
    )
    intent = jet.decide_intent()
    assert intent.action_type == "MOVE"
    assert intent.target_position == waypoint
    assert intent.desired_heading == pytest.approx(
        math.degrees(math.atan2(waypoint[0] - position[0], waypoint[1] - position[1]))
        % 360.0
    )


def test_out_of_sensor_range_jet_moves_toward_briefed_bunker():
    """Regression: spawn beyond SENSOR_RADIUS must not hover IDLE forever."""
    jet_pos = (10.0, 10.0)
    bunker_pos = (80.0, 80.0)
    assert math.dist(jet_pos, bunker_pos) > config.SENSOR_RADIUS
    model = WraithNetModel(
        scenario=scenario_from_data(
            new_scenario_data(
                jets=[
                    JetSpec(
                        agent_id="Jet_1",
                        position=jet_pos,
                        payload={"JDAM": 2},
                    )
                ],
                targets=[
                    TargetSpec(
                        target_id="Target_1",
                        position=bunker_pos,
                        hp_required=2,
                        target_type="HARDENED_BUNKER",
                    )
                ],
            )
        )
    )
    broker = MessageBroker()
    runtime = JetAgentRuntime("Jet_1", broker)
    obs = model.build_observation("Jet_1", broker)
    assert obs.briefed_seek_positions == [bunker_pos]
    assert not any(t.target_id == "Target_1" for t in obs.known_targets)
    runtime.set_observation(obs)
    intent = runtime.decide_intent()
    assert intent.action_type == "MOVE"
    assert intent.target_position == bunker_pos
    start = tuple(model.jets["Jet_1"].pos)
    model.step({"Jet_1": intent})
    assert math.dist(start, tuple(model.jets["Jet_1"].pos)) > 0.5


def test_untasked_jet_on_station_flies_wide_slow_orbit():
    """Regression: re-diving at the waypoint looped jets at full turn rate."""
    waypoint = (50.0, 50.0)
    model = WraithNetModel(
        scenario=scenario_from_data(
            new_scenario_data(
                jets=[JetSpec(agent_id="Jet_1", position=(50.0, 25.0), payload={"JDAM": 2})],
                targets=[
                    TargetSpec(
                        target_id="Target_1",
                        position=(95.0, 95.0),
                        hp_required=2,
                        target_type="HARDENED_BUNKER",
                    )
                ],
            )
        )
    )
    jet = model.jets["Jet_1"]
    frame = config.JET_FRAME_CATALOG[jet.frame_type]
    orbit_r = config.LOITER_RADIUS_TURN_RADII * frame.max_speed / math.radians(
        frame.turn_rate_deg_per_tick
    )
    broker = MessageBroker()
    runtime = JetAgentRuntime("Jet_1", broker)
    turns, radii, steps = [], [], []
    for tick in range(120):
        obs = model.build_observation("Jet_1", broker)
        runtime.set_observation(obs.model_copy(update={"briefed_seek_positions": [waypoint]}))
        before_h, before_p = jet.heading, tuple(jet.pos)
        model.step({"Jet_1": runtime.decide_intent()})
        if tick >= 30:
            turns.append(abs((jet.heading - before_h + 180.0) % 360.0 - 180.0))
            radii.append(math.dist(tuple(jet.pos), waypoint))
            steps.append(math.dist(tuple(jet.pos), before_p))
    assert max(turns) < frame.turn_rate_deg_per_tick / 2
    assert 0.7 * orbit_r < min(radii) and max(radii) < 1.3 * orbit_r
    assert max(steps) == pytest.approx(config.LOITER_SPEED_FRACTION * frame.max_speed, rel=0.05)


@pytest.mark.asyncio
async def test_rtb_jet_never_bids_on_cfp_or_becomes_manager():
    broker = MessageBroker()
    manager = JetAgentRuntime("Manager", broker)
    rtb = JetAgentRuntime("RTB_Jet", broker)
    tasks = [manager.start(), rtb.start()]
    target = _bunker()
    try:
        rtb.set_observation(
            _gunner_obs((50.0, 50.0), 0.0, {"JDAM": 4, "AIM9X": 0}, [target])
        )
        rtb.refresh_world_view()
        rtb.role = "RTB"
        await rtb.scan_and_negotiate()
        assert rtb.role == "RTB"
        assert rtb.managed_target_id is None
        assert broker.is_claimed("Bunker") is False

        manager.set_observation(
            _gunner_obs((10.0, 10.0), 0.0, {"JDAM": 1, "AIM9X": 0}, [target])
        )
        manager.refresh_world_view()
        cfp = Message(
            message_id="cfp-rtb",
            contract_id="c-rtb",
            tick=0,
            sender_id="Manager",
            receiver_id=BROADCAST_ID,
            message_type="CFP",
            target_id="Bunker",
            hp_remaining=5,
        )
        await broker.send(cfp)
        await asyncio.sleep(0.05)
        bids_from_rtb = [
            m
            for m in manager.received_messages
            if m.message_type == "BID" and m.sender_id == "RTB_Jet"
        ]
        assert bids_from_rtb == []
        assert rtb.role == "RTB"
        assert rtb.committed_target_id is None
    finally:
        for jet in (manager, rtb):
            jet.stop()
        await asyncio.gather(*tasks, return_exceptions=True)

def test_stable_id_same_inputs_same_output():
    from wraithnet.agents import stable_id

    a = stable_id("Jet_1", "Target_A", 3, "CONTRACT", 1)
    b = stable_id("Jet_1", "Target_A", 3, "CONTRACT", 1)
    c = stable_id("Jet_1", "Target_A", 3, "CONTRACT", 2)
    assert a == b
    assert a != c
    assert len(a) == 32
    assert all(ch in "0123456789abcdef" for ch in a)


@pytest.mark.asyncio
async def test_contract_ids_deterministic_for_same_seq(monkeypatch):
    monkeypatch.setattr(config, "BID_TIMEOUT_SECONDS", 0.05)
    target = TargetInfo(
        target_id="Target_A",
        position=(75.0, 75.0),
        target_type="HARDENED_BUNKER",
        hp_required=1,
        hp_remaining=1,
        claimed=False,
    )

    async def _open_once(agent_id: str) -> str:
        broker = MessageBroker()
        jet = JetAgentRuntime(agent_id, broker)
        jet._msg_seq = 0
        obs = Observation(
            agent_id=agent_id,
            tick=7,
            current_position=(70.0, 70.0),
            sensor_radius=config.SENSOR_RADIUS,
            payload={"JDAM": 1, "AIM9X": 0},
            known_targets=[target],
            comms_jammed=False,
        )
        jet.set_observation(obs)
        jet.refresh_world_view()
        task = jet.start()
        try:
            original = jet.broker.try_open_contract
            captured: list[str] = []

            def tracking(tid, cid, mid, tick):
                captured.append(cid)
                return original(tid, cid, mid, tick)

            jet.broker.try_open_contract = tracking  # type: ignore[method-assign]
            assert await jet.try_become_manager(target) is True
            assert captured
            return captured[0]
        finally:
            jet.stop()
            await asyncio.gather(task, return_exceptions=True)

    c1 = await _open_once("Jet_1")
    c2 = await _open_once("Jet_1")
    assert c1 == c2
    c3 = await _open_once("Jet_2")
    assert c3 != c1
