"""End-to-end tests for the Hybrid Bridge."""

import asyncio

import pytest

from wraithnet import config
from wraithnet.agents import JetAgentRuntime
from wraithnet.bridge import WraithNetBridge
from wraithnet.scenario_schema import JetSpec, ScenarioConfig, TargetSpec
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
        entry["weapon_type"] == "AIM9X"
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
    scenario = ScenarioConfig(
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
    )
    bridge = WraithNetBridge(scenario=scenario)
    await asyncio.wait_for(bridge.run(max_ticks=50), timeout=15.0)

    assert bridge.model.running is False
    assert bridge.model.swarm_wiped_out is False
    assert bridge.model.targets["Near_Bunker"].hp_remaining == 0
    assert "Target_A" not in bridge.model.targets
    assert set(bridge.model.jets) == {"Solo_Jet"}
    assert any(entry["target_id"] == "Near_Bunker" for entry in bridge.model.strike_log)
