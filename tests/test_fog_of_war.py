"""Unit tests for Fog of War / sensor fusion (Phase 7)."""

import asyncio

import pytest

from wraithnet import config
from wraithnet.agents import JetAgentRuntime
from wraithnet.broker import MessageBroker
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


async def _wait_until(predicate, timeout: float = 0.5) -> None:
    async def _poll() -> None:
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_poll(), timeout=timeout)


def test_refresh_world_view_populates_and_detects_changes():
    broker = MessageBroker()
    jet = JetAgentRuntime("Jet_1", broker)
    target = _target()

    jet.set_observation(_obs("Jet_1", (70.0, 70.0), {"JDAM": 1, "AIM9X": 0}, [target], tick=0))
    changed = jet.refresh_world_view()
    assert len(changed) == 1
    assert changed[0].target_id == "Target_A"
    assert "Target_A" in jet.world_view

    jet.set_observation(_obs("Jet_1", (70.0, 70.0), {"JDAM": 1, "AIM9X": 0}, [target], tick=1))
    assert jet.refresh_world_view() == []

    updated = _target(hp_remaining=4)
    jet.set_observation(
        _obs("Jet_1", (70.0, 70.0), {"JDAM": 1, "AIM9X": 0}, [updated], tick=2)
    )
    changed = jet.refresh_world_view()
    assert len(changed) == 1
    assert changed[0].hp_remaining == 4


def test_refresh_world_view_expires_stale_entries():
    broker = MessageBroker()
    jet = JetAgentRuntime("Jet_1", broker)
    target = _target()

    jet.set_observation(_obs("Jet_1", (70.0, 70.0), {"JDAM": 1, "AIM9X": 0}, [target], tick=0))
    jet.refresh_world_view()
    assert "Target_A" in jet.world_view

    # Out of sensor range again, past TTL.
    later = config.DATALINK_CONTACT_TTL_TICKS + 1
    jet.set_observation(
        _obs("Jet_1", (70.0, 70.0), {"JDAM": 1, "AIM9X": 0}, [], tick=later)
    )
    jet.refresh_world_view()
    assert "Target_A" not in jet.world_view
    assert jet.effective_known_targets() == []


@pytest.mark.asyncio
async def test_share_new_contacts_broadcasts_only_changed():
    broker = MessageBroker()
    jet1 = JetAgentRuntime("Jet_1", broker)
    jet2 = JetAgentRuntime("Jet_2", broker)
    tasks = [jet1.start(), jet2.start()]
    target = _target()

    try:
        jet1.set_observation(
            _obs("Jet_1", (70.0, 70.0), {"JDAM": 1, "AIM9X": 0}, [target], tick=0)
        )
        await jet1.share_new_contacts()
        await _wait_until(
            lambda: any(m.message_type == "DATALINK_CONTACT" for m in jet2.received_messages)
        )
        contacts = [m for m in jet2.received_messages if m.message_type == "DATALINK_CONTACT"]
        assert len(contacts) == 1
        assert contacts[0].target_info is not None
        assert contacts[0].target_info.target_id == "Target_A"

        before = len(jet2.received_messages)
        jet1.set_observation(
            _obs("Jet_1", (70.0, 70.0), {"JDAM": 1, "AIM9X": 0}, [target], tick=1)
        )
        await jet1.share_new_contacts()
        await asyncio.sleep(0.05)
        assert len(jet2.received_messages) == before
    finally:
        for jet in (jet1, jet2):
            jet.stop()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_helper_bids_from_datalink_contact_only(monkeypatch):
    """Helper never directly senses the target; bids purely from Jet_1's contact."""
    monkeypatch.setattr(config, "BID_TIMEOUT_SECONDS", 0.05)
    broker = MessageBroker()
    manager = JetAgentRuntime("Manager", broker)
    helper = JetAgentRuntime("Helper", broker)
    tasks = [manager.start(), helper.start()]

    target = _target(hp_required=2, hp_remaining=2)
    # Manager sees the target; Helper's Observation.known_targets is empty.
    manager.set_observation(
        _obs("Manager", target.position, {"JDAM": 1, "AIM9X": 0}, [target], tick=0)
    )
    helper.set_observation(
        _obs("Helper", (0.0, 0.0), {"JDAM": 2, "AIM9X": 0}, [], tick=0)
    )
    helper.refresh_world_view()  # empty direct sense — world_view stays empty for now

    try:
        await manager.share_new_contacts()
        await _wait_until(lambda: "Target_A" in helper.world_view)
        assert helper.latest_observation is not None
        assert helper.latest_observation.known_targets == []
        assert "Target_A" in helper.world_view

        await manager.scan_and_negotiate()
        await asyncio.sleep(0.15)

        assert helper.role == "CONTRACTOR"
        assert helper.committed_target_id == "Target_A"
        assert helper.committed_weapon_type == "JDAM"
        # Helper never had the target in direct Observation.known_targets.
        assert helper.latest_observation.known_targets == []
    finally:
        for jet in (manager, helper):
            jet.stop()
        await asyncio.gather(*tasks, return_exceptions=True)


def test_decide_intent_refuses_strike_on_memory_only_target():
    """In strike range of a remembered target, but not directly sensing it → MOVE."""
    import math

    broker = MessageBroker()
    jet = JetAgentRuntime("Jet_1", broker)
    target = _target()

    # Populate world_view via a prior direct sighting...
    jet.set_observation(
        _obs("Jet_1", target.position, {"JDAM": 1, "AIM9X": 0}, [target], tick=0)
    )
    jet.refresh_world_view()
    jet._commit("Target_A", 1, "JDAM")

    # ...then lose direct sensing while still within STRIKE_RANGE of memory coords.
    jet.set_observation(
        _obs("Jet_1", target.position, {"JDAM": 1, "AIM9X": 0}, [], tick=1)
    )
    # Do NOT refresh — keep the remembered entry; Observation.known_targets is empty.
    assert jet.latest_observation is not None
    assert math.dist(jet.latest_observation.current_position, target.position) <= config.STRIKE_RANGE
    assert any(t.target_id == "Target_A" for t in jet.effective_known_targets())
    assert jet.latest_observation.known_targets == []

    intent = jet.decide_intent()
    assert intent.action_type == "MOVE"
    assert intent.target_position == target.position
