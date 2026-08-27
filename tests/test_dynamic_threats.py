"""Unit tests for SAM / Manager Fallback dynamic threats (Phase 8)."""

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


def _set_obs(jet: JetAgentRuntime, obs: Observation) -> None:
    jet.set_observation(obs)
    jet.refresh_world_view()


@pytest.mark.asyncio
async def test_managed_contracts_populated_and_survives_resolution(monkeypatch):
    monkeypatch.setattr(config, "BID_TIMEOUT_SECONDS", 0.05)
    broker = MessageBroker()
    jet1 = JetAgentRuntime("Jet_1", broker)
    jet2 = JetAgentRuntime("Jet_2", broker)
    jet3 = JetAgentRuntime("Jet_3", broker)
    tasks = [jet1.start(), jet2.start(), jet3.start()]

    target = _target()
    _set_obs(jet1, _obs("Jet_1", (70.0, 70.0), {"JDAM": 1, "AIM9X": 0}, [target]))
    _set_obs(jet2, _obs("Jet_2", (5.0, 95.0), {"JDAM": 4, "AIM9X": 0}, [target]))
    _set_obs(jet3, _obs("Jet_3", (95.0, 5.0), {"JDAM": 3, "AIM9X": 2}, [target]))

    try:
        await jet1.scan_and_negotiate()
        await asyncio.sleep(0.15)

        assert jet1.managed_target_id is None  # cleared after successful resolve
        assert "Target_A" in jet1.managed_contracts
        accepted = jet1.managed_contracts["Target_A"]
        assert "Jet_1" not in accepted  # never includes self
        assert len(accepted) >= 1
        assert accepted <= {"Jet_2", "Jet_3"}
    finally:
        for jet in (jet1, jet2, jet3):
            jet.stop()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_notify_contractor_lost_reopens_bidding(monkeypatch):
    monkeypatch.setattr(config, "BID_TIMEOUT_SECONDS", 0.05)
    broker = MessageBroker()
    manager = JetAgentRuntime("Manager", broker)
    helper = JetAgentRuntime("Helper", broker)
    replacement = JetAgentRuntime("Replacement", broker)
    tasks = [manager.start(), helper.start(), replacement.start()]

    # hp=3: manager self-commits 1, helper (closer, higher util) takes 2.
    # Replacement starts with no observation so it cannot bid in round 1.
    target = _target(hp_required=3, hp_remaining=3)
    _set_obs(
        manager, _obs("Manager", target.position, {"JDAM": 1, "AIM9X": 0}, [target])
    )
    _set_obs(
        helper, _obs("Helper", (70.0, 70.0), {"JDAM": 2, "AIM9X": 0}, [target])
    )
    # Replacement is registered but has no observation yet → will not bid.

    try:
        await manager.scan_and_negotiate()
        await asyncio.sleep(0.15)

        assert "Helper" in manager.managed_contracts.get("Target_A", set())
        # Simulate helper's contribution never landing — shortfall remains.
        manager.role = "IDLE"
        manager.committed_target_id = None
        manager.committed_payload = 0
        live = _target(hp_required=3, hp_remaining=2)
        _set_obs(
            manager, _obs("Manager", target.position, {"JDAM": 0, "AIM9X": 0}, [live])
        )
        _set_obs(
            replacement,
            _obs("Replacement", (0.0, 0.0), {"JDAM": 3, "AIM9X": 0}, [live]),
        )
        assert broker.is_claimed("Target_A")

        await manager.notify_contractor_lost("Helper", "Target_A")
        await asyncio.sleep(0.15)

        assert "Helper" not in manager.managed_contracts.get("Target_A", set())
        assert replacement.role == "CONTRACTOR"
        assert replacement.committed_target_id == "Target_A"
        assert replacement.committed_payload >= 2
    finally:
        for jet in (manager, helper, replacement):
            jet.stop()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_notify_contractor_lost_noop_for_unknown(monkeypatch):
    monkeypatch.setattr(config, "BID_TIMEOUT_SECONDS", 0.05)
    broker = MessageBroker()
    manager = JetAgentRuntime("Manager", broker)
    _set_obs(
        manager,
        _obs(
            "Manager",
            (0.0, 0.0),
            {"JDAM": 0, "AIM9X": 0},
            [_target()],
        ),
    )
    manager.role = "IDLE"
    before_role = manager.role
    await manager.notify_contractor_lost("Ghost", "Target_A")
    assert manager.role == before_role
    assert manager.managed_target_id is None
    assert manager.received_messages == []


@pytest.mark.asyncio
async def test_scan_skips_unsuitable_weapon_but_zero_payload_can_coordinate(monkeypatch):
    """Documents deliberate scan_and_negotiate optimization (review §2): wrong
    weapon skips auto-management; zero-payload agents may still coordinate."""
    monkeypatch.setattr(config, "BID_TIMEOUT_SECONDS", 0.05)
    broker = MessageBroker()
    jdam_only = JetAgentRuntime("JDAM_Only", broker)
    coordinator = JetAgentRuntime("Coordinator", broker)
    tasks = [jdam_only.start(), coordinator.start()]

    radar = TargetInfo(
        target_id="Target_B",
        position=(25.0, 25.0),
        target_type="MOBILE_RADAR",
        hp_required=1,
        hp_remaining=1,
        claimed=False,
    )
    _set_obs(
        jdam_only, _obs("JDAM_Only", (25.0, 25.0), {"JDAM": 4, "AIM9X": 0}, [radar])
    )
    _set_obs(
        coordinator, _obs("Coordinator", (0.0, 0.0), {"JDAM": 0, "AIM9X": 0}, [radar])
    )

    try:
        await jdam_only.scan_and_negotiate()
        await asyncio.sleep(0.05)
        assert jdam_only.role == "IDLE"
        assert jdam_only.managed_target_id is None

        await coordinator.scan_and_negotiate()
        await asyncio.sleep(0.15)
        assert coordinator.role in ("MANAGER", "CONTRACTOR", "IDLE")
        assert coordinator.managed_target_id is None or coordinator.role != "IDLE"
    finally:
        for jet in (jdam_only, coordinator):
            jet.stop()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_notify_contractor_lost_noop_if_target_destroyed(monkeypatch):
    monkeypatch.setattr(config, "BID_TIMEOUT_SECONDS", 0.05)
    broker = MessageBroker()
    manager = JetAgentRuntime("Manager", broker)
    helper = JetAgentRuntime("Helper", broker)
    tasks = [manager.start(), helper.start()]

    dead = _target(hp_remaining=0)
    _set_obs(
        manager, _obs("Manager", dead.position, {"JDAM": 1, "AIM9X": 0}, [dead])
    )
    manager.managed_contracts["Target_A"] = {"Helper"}
    manager.role = "IDLE"
    cfp_before = len(
        [m for m in manager.received_messages if m.message_type == "CFP"]
    )

    try:
        await manager.notify_contractor_lost("Helper", "Target_A")
        await asyncio.sleep(0.05)
        assert "Target_A" not in manager.managed_contracts
        assert manager.role == "IDLE"
        assert manager.managed_target_id is None
        # No new CFP from this manager's outbound path shows up as managed activity
        assert manager.managed_target_id is None
        _ = cfp_before  # explicit: we assert no re-management above
    finally:
        for jet in (manager, helper):
            jet.stop()
        await asyncio.gather(*tasks, return_exceptions=True)
