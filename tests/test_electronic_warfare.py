"""Unit tests for electronic warfare / jamming (Phase 9)."""

import asyncio

import pytest

from wraithnet import config
from wraithnet.agents import JetAgentRuntime, MAX_CFP_ROUNDS
from wraithnet.broker import MessageBroker
from wraithnet.schemas import Observation, TargetInfo
from wraithnet.world import WraithNetModel


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
    comms_jammed: bool = False,
) -> Observation:
    return Observation(
        agent_id=agent_id,
        tick=tick,
        current_position=position,
        sensor_radius=config.SENSOR_RADIUS,
        payload=payload,
        known_targets=targets,
        comms_jammed=comms_jammed,
    )


def _set_obs(jet: JetAgentRuntime, obs: Observation) -> None:
    jet.set_observation(obs)
    jet.refresh_world_view()


@pytest.mark.asyncio
async def test_jammed_manager_eventually_releases_claim(monkeypatch):
    """Existing MAX_CFP_ROUNDS / BID_TIMEOUT machinery frees a jammed Manager —
    no jamming-specific branching in negotiation methods required."""
    monkeypatch.setattr(config, "BID_TIMEOUT_SECONDS", 0.02)
    monkeypatch.setattr(config, "MAX_TICK_WAIT_SECONDS", 2.0)
    broker = MessageBroker()
    manager = JetAgentRuntime("Manager", broker)
    bidder = JetAgentRuntime("Bidder", broker)
    tasks = [manager.start(), bidder.start()]

    target = _target(hp_required=1, hp_remaining=1)
    # Zero payload so Manager cannot self-cover; CFPs never leave while jammed.
    _set_obs(
        manager, _obs("Manager", target.position, {"JDAM": 0, "AIM9X": 0}, [target])
    )
    _set_obs(
        bidder, _obs("Bidder", (0.0, 0.0), {"JDAM": 1, "AIM9X": 0}, [target])
    )
    broker.set_jammed("Manager", True)

    try:
        await manager.scan_and_negotiate()
        # Allow any remaining timeout rounds to settle.
        await asyncio.sleep(MAX_CFP_ROUNDS * 0.02 + 0.1)
        assert manager.role == "IDLE"
        assert broker.is_claimed("Target_A") is False
        assert bidder.role == "IDLE"
        assert bidder.committed_target_id is None
    finally:
        for jet in (manager, bidder):
            jet.stop()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_bidder_self_heals_via_pending_ttl_when_accept_jammed(monkeypatch):
    """ACCEPT dropped because bidder is jammed → CONTRACTOR_PENDING TTL reverts to IDLE."""
    monkeypatch.setattr(config, "BID_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(config, "CONTRACTOR_PENDING_TTL_TICKS", 3)
    broker = MessageBroker()
    manager = JetAgentRuntime("Manager", broker)
    bidder = JetAgentRuntime("Bidder", broker)
    tasks = [manager.start(), bidder.start()]

    target = _target(hp_required=1, hp_remaining=1)
    # Manager has zero payload so it does not self-win.
    _set_obs(
        manager, _obs("Manager", (0.0, 0.0), {"JDAM": 0, "AIM9X": 0}, [target], tick=0)
    )
    _set_obs(
        bidder, _obs("Bidder", target.position, {"JDAM": 1, "AIM9X": 0}, [target], tick=0)
    )

    original_send = broker.send

    async def jam_bidder_after_bid(message):
        await original_send(message)
        if message.message_type == "BID" and message.sender_id == "Bidder":
            broker.set_jammed("Bidder", True)

    broker.send = jam_bidder_after_bid  # type: ignore[method-assign]

    try:
        await manager.scan_and_negotiate()
        await asyncio.sleep(0.15)
        assert bidder.role == "CONTRACTOR_PENDING"
        assert bidder.pending_bid_since_tick is not None

        # Advance ticks past TTL without a real ACCEPT arriving.
        for tick in range(1, config.CONTRACTOR_PENDING_TTL_TICKS + 3):
            _set_obs(
                bidder,
                _obs(
                    "Bidder",
                    target.position,
                    {"JDAM": 1, "AIM9X": 0},
                    [target],
                    tick=tick,
                ),
            )
            bidder.check_pending_bid_timeout()
            if bidder.role == "IDLE":
                break

        assert bidder.role == "IDLE"
        assert bidder.pending_bid_since_tick is None
    finally:
        broker.send = original_send  # type: ignore[method-assign]
        for jet in (manager, bidder):
            jet.stop()
        await asyncio.gather(*tasks, return_exceptions=True)


def test_check_pending_bid_timeout_negative_cases():
    broker = MessageBroker()
    jet = JetAgentRuntime("Jet_1", broker)
    target = _target()
    _set_obs(jet, _obs("Jet_1", (70.0, 70.0), {"JDAM": 1, "AIM9X": 0}, [target], tick=0))

    # Not CONTRACTOR_PENDING → no-op
    jet.role = "IDLE"
    jet.pending_bid_since_tick = 0
    jet.check_pending_bid_timeout()
    assert jet.role == "IDLE"

    # CONTRACTOR_PENDING but within TTL → no-op
    jet.role = "CONTRACTOR_PENDING"
    jet.pending_bid_since_tick = 0
    _set_obs(
        jet,
        _obs(
            "Jet_1",
            (70.0, 70.0),
            {"JDAM": 1, "AIM9X": 0},
            [target],
            tick=config.CONTRACTOR_PENDING_TTL_TICKS,
        ),
    )
    jet.check_pending_bid_timeout()
    assert jet.role == "CONTRACTOR_PENDING"
    assert jet.pending_bid_since_tick == 0


def test_jamming_never_filters_observation_contents():
    """Finding G4: jamming is informational on Observation; sensors still work."""
    model = WraithNetModel()
    broker = MessageBroker()
    jet = model.jets["Jet_3"]
    jammer = model.jammers["EW_Jammer_1"]
    model.space.move_agent(jet, jammer.position)
    obs = model.build_observation("Jet_3", broker)
    assert obs.comms_jammed is True
    assert obs.current_position == jammer.position
    assert obs.payload == dict(jet.payload)
    # known_targets still populated from sensors (not gated by jamming)
    assert isinstance(obs.known_targets, list)
