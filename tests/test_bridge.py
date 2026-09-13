"""End-to-end tests for the Hybrid Bridge."""

import asyncio

import pytest

from wraithnet import config
from wraithnet.agents import JetAgentRuntime
from wraithnet.bridge import WraithNetBridge
from wraithnet.scenario_schema import JetSpec, ScenarioConfig, TargetSpec, new_scenario_data, scenario_from_data
from wraithnet.schemas import Observation, TargetInfo


def _target(**overrides) -> TargetInfo:
    data = {
        "target_id": "Target_A",
        "position": (75.0, 75.0),
        "target_type": "HARDENED_BUNKER",
        "hp_required": 5,
        "hp_remaining": 5,
        "claimed": False,
    }
    data.update(overrides)
    return TargetInfo(**data)


def _obs(
    agent_id: str,
    position: tuple[float, float],
    payload: dict[str, int],
    targets: list[TargetInfo],
    tick: int = 0,
) -> Observation:
    return Observation(
        agent_id=agent_id,
        tick=tick,
        current_position=position,
        sensor_radius=config.SENSOR_RADIUS,
        payload=payload,
        known_targets=targets,
        comms_jammed=False,
    )


def _set_obs(jet: JetAgentRuntime, obs: Observation) -> None:
    jet.set_observation(obs)
    jet.refresh_world_view()


@pytest.fixture
def fast_cnp(monkeypatch):
    monkeypatch.setattr(config, "BID_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(config, "MAX_TICK_WAIT_SECONDS", 1.0)


@pytest.mark.asyncio
async def test_bridge_runs_scenario_to_completion(fast_cnp):
    bridge = WraithNetBridge()
    await asyncio.wait_for(bridge.run(max_ticks=500), timeout=60.0)

    assert bridge.model.running is False
    assert bridge.model.swarm_wiped_out is False
    assert bridge.model.tick < 500
    assert bridge.model.targets["Target_A"].hp_remaining == 0
    assert bridge.model.targets["Target_B"].hp_remaining == 0
    assert bridge.model.targets["SAM_Site_1"].hp_remaining == 0

    assert len(bridge.model.strike_log) >= 7

    strikers_a = {
        entry["agent_id"]
        for entry in bridge.model.strike_log
        if entry["target_id"] == "Target_A"
    }
    assert len(strikers_a) >= 2
    assert all(
        entry["weapon_type"] == "JDAM"
        for entry in bridge.model.strike_log
        if entry["target_id"] == "Target_A"
    )
    assert all(
        entry["weapon_type"] == "AGM65"
        for entry in bridge.model.strike_log
        if entry["target_id"] == "Target_B"
    )

    for task in bridge._tasks:
        if task.cancelled():
            continue
        exc = task.exception()
        assert exc is None or isinstance(exc, asyncio.CancelledError)


@pytest.mark.asyncio
async def test_bridge_removes_destroyed_jet_and_notifies(fast_cnp):
    """Force-destroy a committed contractor: runtime cancelled, agent removed,
    and Manager Fallback notified (contractor dropped from managed_contracts)."""
    bridge = WraithNetBridge()
    bridge.start_agent_loops()
    try:
        for _ in range(8):
            await bridge._tick()
            if not bridge.model.running:
                break

        victim_id = None
        for aid, agent in bridge.agents.items():
            if agent.role == "CONTRACTOR" and agent.committed_target_id is not None:
                victim_id = aid
                break
        if victim_id is None:
            victim_id = next(iter(bridge.model.jets))

        # Snapshot who had accepted this victim before destruction
        managers_with_victim = [
            mid
            for mid, mgr in bridge.agents.items()
            for cids in mgr.managed_contracts.values()
            if victim_id in cids
        ]

        victim_task = bridge.agents[victim_id]._task
        bridge.model.jets[victim_id].hp = 0
        await bridge._tick()

        assert victim_id not in bridge.agents
        assert victim_id not in bridge.model.jets
        assert victim_task is not None
        # Give the cancelled run() coroutine a turn to finish unwinding.
        await asyncio.sleep(0)
        assert victim_task.cancelled() or victim_task.done()
        assert bridge.model.swarm_wiped_out is False
        assert bridge.model.running is True  # only one jet died

        for mid in managers_with_victim:
            if mid not in bridge.agents:
                continue
            for cids in bridge.agents[mid].managed_contracts.values():
                assert victim_id not in cids
    finally:
        await bridge.stop_agent_loops()


@pytest.mark.asyncio
async def test_notify_contractor_lost_skips_destroyed_manager(fast_cnp):
    """Regression for docs/05_Code_Review_Phases_6-8.md §1: a manager and its
    accepted contractor destroyed in the same tick must not let the dead
    manager broadcast a zombie CFP that strands a surviving bidder."""
    bridge = WraithNetBridge()
    bridge.start_agent_loops()
    try:
        jet1 = bridge.agents["Jet_1"]
        jet2 = bridge.agents["Jet_2"]
        jet3 = bridge.agents["Jet_3"]
        target = _target()

        # Normal post-resolution state: Jet_2 managed Target_A, Jet_3 contracted.
        jet2.role = "IDLE"
        jet2.managed_contracts["Target_A"] = {"Jet_3"}
        bridge.broker.try_open_contract("Target_A", "c-dead", "Jet_2", tick=0)

        _set_obs(jet1, _obs("Jet_1", (70.0, 70.0), {"JDAM": 1, "AIM9X": 0}, [target]))
        jet2_snapshot = jet2
        role_before = jet2.role
        committed_before = jet2.committed_target_id

        destroyed = {"Jet_2", "Jet_3"}
        bridge.model.jets["Jet_2"].hp = 0
        bridge.model.jets["Jet_3"].hp = 0
        bridge.model._apply_sam_damage()
        for aid in destroyed:
            bridge.agents[aid].stop()
        await bridge._notify_managers_of_losses(destroyed)
        for aid in destroyed:
            del bridge.agents[aid]

        # Dead manager must not re-enter CNP or broadcast.
        assert jet2_snapshot.role == role_before
        assert jet2_snapshot.committed_target_id == committed_before
        assert jet2_snapshot.managed_target_id is None
        cfps_from_dead = [
            m
            for m in jet1.received_messages
            if m.message_type == "CFP" and m.sender_id == "Jet_2"
        ]
        assert cfps_from_dead == []

        # Surviving Jet_1 must not be stranded in CONTRACTOR_PENDING.
        await jet1.scan_and_negotiate()
        await asyncio.sleep(0.15)
        assert jet1.role != "CONTRACTOR_PENDING"
    finally:
        await bridge.stop_agent_loops()


@pytest.mark.asyncio
async def test_bridge_swarm_wipeout_terminates(fast_cnp):
    bridge = WraithNetBridge()
    bridge.start_agent_loops()
    try:
        await bridge._tick()
        for jet in list(bridge.model.jets.values()):
            jet.hp = 0
        await bridge._tick()
        assert bridge.model.running is False
        assert bridge.model.swarm_wiped_out is True
        assert len(bridge.agents) == 0
    finally:
        await bridge.stop_agent_loops()


@pytest.mark.asyncio
async def test_bridge_runs_custom_scenario_to_completion(fast_cnp):
    """Phase 14: a custom ScenarioConfig actually reaches the running simulation."""
    scenario = scenario_from_data(new_scenario_data(
        jets=[
            JetSpec(
                agent_id="Solo_Jet",
                position=(20.0, 20.0),
                payload={"JDAM": 1, "AIM9X": 0},
            )
        ],
        targets=[
            TargetSpec(
                target_id="Near_Bunker",
                position=(20.5, 20.0),
                hp_required=1,
                target_type="HARDENED_BUNKER",
            )
        ],
    ))
    bridge = WraithNetBridge(scenario=scenario)
    await asyncio.wait_for(bridge.run(max_ticks=50), timeout=15.0)

    assert bridge.model.running is False
    assert bridge.model.swarm_wiped_out is False
    assert bridge.model.targets["Near_Bunker"].hp_remaining == 0
    assert "Target_A" not in bridge.model.targets
    assert bridge.model.mission_solved is True
    assert "Solo_Jet" in bridge.model.jets_returned_to_base
    assert set(bridge.model.jets) == set()
    assert any(entry["target_id"] == "Near_Bunker" for entry in bridge.model.strike_log)


@pytest.mark.asyncio
async def test_rtb_contractor_notifies_manager_like_a_combat_loss(fast_cnp):
    """A contractor that hits bingo is handed off the same way a SAM kill is."""
    bridge = WraithNetBridge()
    bridge.start_agent_loops()
    try:
        for _ in range(8):
            await bridge._tick()
            if not bridge.model.running:
                break

        victim_id = None
        for aid, agent in bridge.agents.items():
            if agent.role == "CONTRACTOR" and agent.committed_target_id is not None:
                victim_id = aid
                break
        if victim_id is None:
            victim_id = next(iter(bridge.model.jets))

        managers_with_victim = [
            mid
            for mid, mgr in bridge.agents.items()
            for cids in mgr.managed_contracts.values()
            if victim_id in cids
        ]

        # Park far from SAMs and outside the landing radius so this tick's
        # only loss-of-availability is bingo RTB, not a SAM kill or a landing.
        jet = bridge.model.jets[victim_id]
        bridge.model.space.move_agent(jet, (10.0, 10.0))
        jet.hp = config.JET_FRAME_CATALOG[jet.frame_type].hp
        # (10,10)→(2,2) is ~11.3 units: bingo ≈ 2.7 for a STRIKE_FIGHTER, so
        # fuel=2.0 triggers RTB but survives this tick's burn and does not land.
        jet.fuel = 2.0
        await bridge._tick()

        assert victim_id in bridge.agents
        assert bridge.agents[victim_id].role == "RTB"
        assert bridge.model.jets[victim_id].is_rtb is True
        assert victim_id not in bridge.model.jets_returned_to_base

        for mid in managers_with_victim:
            if mid not in bridge.agents:
                continue
            for cids in bridge.agents[mid].managed_contracts.values():
                assert victim_id not in cids
    finally:
        await bridge.stop_agent_loops()


@pytest.mark.asyncio
async def test_landed_jet_runtime_is_removed_from_bridge_agents(fast_cnp):
    scenario = scenario_from_data(new_scenario_data(
        home_base=(20.0, 20.0),
        jets=[
            JetSpec(
                agent_id="Homebody",
                position=(20.0, 20.0),
                payload={"JDAM": 1, "AIM9X": 0},
            )
        ],
        targets=[
            TargetSpec(
                target_id="Far_Bunker",
                position=(80.0, 80.0),
                hp_required=1,
                target_type="HARDENED_BUNKER",
            )
        ],
    ))
    bridge = WraithNetBridge(scenario=scenario)
    bridge.start_agent_loops()
    try:
        runtime = bridge.agents["Homebody"]
        task = runtime._task
        bridge.model.jets["Homebody"].fuel = 0.0
        await bridge._tick()
        assert "Homebody" not in bridge.agents
        assert "Homebody" not in bridge.model.jets
        assert "Homebody" in bridge.model.jets_returned_to_base
        await asyncio.sleep(0)
        assert task is not None
        assert task.cancelled() or task.done()
    finally:
        await bridge.stop_agent_loops()


@pytest.mark.asyncio
async def test_fuel_exhausted_en_route_runtime_is_removed_not_landed(fast_cnp):
    scenario = scenario_from_data(new_scenario_data(
        home_base=(2.0, 2.0),
        jets=[
            JetSpec(
                agent_id="Dry",
                position=(80.0, 80.0),
                payload={"JDAM": 1, "AIM9X": 0},
            )
        ],
        targets=[
            TargetSpec(
                target_id="Far_Bunker",
                position=(90.0, 90.0),
                hp_required=1,
                target_type="HARDENED_BUNKER",
            )
        ],
    ))
    bridge = WraithNetBridge(scenario=scenario)
    bridge.start_agent_loops()
    try:
        bridge.model.jets["Dry"].fuel = 0.0
        await bridge._tick()
        assert "Dry" not in bridge.agents
        assert "Dry" not in bridge.model.jets
        assert "Dry" not in bridge.model.jets_returned_to_base
    finally:
        await bridge.stop_agent_loops()

@pytest.mark.asyncio
async def test_jetless_bridge_ticks_world_with_zero_cnp_runtimes(fast_cnp):
    scenario = scenario_from_data(new_scenario_data(
        jets=[],
        targets=[
            TargetSpec(
                target_id="Bunker",
                position=(50.0, 50.0),
                hp_required=1,
                target_type="HARDENED_BUNKER",
            )
        ],
        combat_seed=42,
    ))
    bridge = WraithNetBridge(scenario=scenario)
    assert bridge.agents == {}
    assert bridge.model.combat_seed == 42
    tick_before = bridge.model.tick
    await bridge._tick()
    assert bridge.model.tick == tick_before + 1
    assert bridge.agents == {}


@pytest.mark.asyncio
async def test_bridge_idle_scan_order_is_agent_id_sorted(fast_cnp, monkeypatch):
    """CNP scan runs in stable agent-id order (not dict insertion order)."""
    scenario = scenario_from_data(new_scenario_data(
        jets=[
            JetSpec(agent_id="Jet_B", position=(10.0, 10.0), payload={"JDAM": 1, "AIM9X": 0}),
            JetSpec(agent_id="Jet_A", position=(12.0, 12.0), payload={"JDAM": 1, "AIM9X": 0}),
        ],
        targets=[
            TargetSpec(
                target_id="Bunker",
                position=(11.0, 11.0),
                hp_required=1,
                target_type="HARDENED_BUNKER",
            )
        ],
    ))
    bridge = WraithNetBridge(scenario=scenario)
    order: list[str] = []
    original = JetAgentRuntime.scan_and_negotiate

    async def tracking(self):
        order.append(self.agent_id)
        return await original(self)

    monkeypatch.setattr(JetAgentRuntime, "scan_and_negotiate", tracking)
    bridge.start_agent_loops()
    try:
        await bridge._tick()
    finally:
        await bridge.stop_agent_loops()
    assert order == sorted(order)
    assert order[0] == "Jet_A"
