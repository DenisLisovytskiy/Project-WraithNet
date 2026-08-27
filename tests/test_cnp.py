"""Unit tests for CNP negotiation logic (Phase 3 / Phase 6 weapon updates)."""

import asyncio
import math

import pytest

from wraithnet import config
from wraithnet.agents import JetAgentRuntime, compute_bid
from wraithnet.broker import MessageBroker
from wraithnet.schemas import Observation, TargetInfo


def _target_a(**overrides) -> TargetInfo:
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


def _observation(
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
    """set_observation + refresh_world_view so effective_known_targets is populated."""
    jet.set_observation(obs)
    jet.refresh_world_view()
    jet.refresh_world_view()


# --- Utility function ---


def test_compute_bid_full_payload_zero_distance():
    target = _target_a(hp_required=4, hp_remaining=4)
    obs = _observation(
        "Jet_X", target.position, payload={"JDAM": 4, "AIM9X": 0}, targets=[target]
    )
    result = compute_bid(obs, target)
    assert result is not None
    utility, offered, weapon_type = result
    # W_WEAPON*1.0 + W1*1.0 - 0 = 11.0
    assert utility == pytest.approx(
        config.UTILITY_W_WEAPON * 1.0 + config.UTILITY_W1 * 1.0
    )
    assert offered == 4
    assert weapon_type == "JDAM"


def test_compute_bid_half_payload_score():
    target = _target_a(hp_required=4, hp_remaining=4)
    obs = _observation(
        "Jet_X", target.position, payload={"JDAM": 2, "AIM9X": 0}, targets=[target]
    )
    result = compute_bid(obs, target)
    assert result is not None
    utility, _, weapon_type = result
    assert utility == pytest.approx(
        config.UTILITY_W_WEAPON * 1.0 + config.UTILITY_W1 * 0.5
    )
    assert weapon_type == "JDAM"


def test_compute_bid_payload_score_capped_at_one():
    target = _target_a(hp_required=2, hp_remaining=2)
    exact = _observation(
        "Jet_Exact", target.position, payload={"JDAM": 2, "AIM9X": 0}, targets=[target]
    )
    double = _observation(
        "Jet_Double", target.position, payload={"JDAM": 4, "AIM9X": 0}, targets=[target]
    )
    u_exact, _, _ = compute_bid(exact, target)
    u_double, _, _ = compute_bid(double, target)
    assert u_exact == pytest.approx(u_double)
    assert u_exact == pytest.approx(
        config.UTILITY_W_WEAPON * 1.0 + config.UTILITY_W1 * 1.0
    )


def test_compute_bid_distance_strictly_decreases_utility():
    target = _target_a(hp_required=4, hp_remaining=4)
    near = _observation(
        "Jet_Near", (75.0, 75.0), payload={"JDAM": 4, "AIM9X": 0}, targets=[target]
    )
    far = _observation(
        "Jet_Far", (0.0, 0.0), payload={"JDAM": 4, "AIM9X": 0}, targets=[target]
    )
    u_near, _, _ = compute_bid(near, target)
    u_far, _, _ = compute_bid(far, target)
    assert u_far < u_near


def test_compute_bid_payload_offered_never_exceeds_limits():
    target = _target_a(hp_required=5, hp_remaining=5)
    obs = _observation(
        "Jet_1", (70.0, 70.0), payload={"JDAM": 1, "AIM9X": 0}, targets=[target]
    )
    result = compute_bid(obs, target)
    assert result is not None
    _, offered, weapon_type = result
    assert offered <= obs.payload[weapon_type]
    assert offered <= target.hp_remaining
    assert offered == 1
    assert weapon_type == "JDAM"


def test_compute_bid_respects_explicit_hp_needed_ask():
    target = _target_a(hp_required=10, hp_remaining=10)
    obs = _observation(
        "Jet_X", target.position, payload={"JDAM": 4, "AIM9X": 0}, targets=[target]
    )
    _, offered_full, _ = compute_bid(obs, target)
    assert offered_full == 4
    utility_ask, offered_ask, _ = compute_bid(obs, target, hp_needed=2)
    assert offered_ask == 2
    assert utility_ask == pytest.approx(
        config.UTILITY_W_WEAPON * 1.0 + config.UTILITY_W1 * 1.0
    )


def test_compute_bid_returns_none_for_unsuitable_weapon():
    """AIM9X-only jet cannot bid on HARDENED_BUNKER (hard disqualification)."""
    target = _target_a()
    obs = _observation(
        "Jet_X", target.position, payload={"JDAM": 0, "AIM9X": 4}, targets=[target]
    )
    assert compute_bid(obs, target) is None


def test_compute_bid_selects_only_suitable_weapon_from_mixed_loadout():
    """Jet with both weapons must bid the suitable one, not the unsuitable count."""
    target = _target_a()  # HARDENED_BUNKER — JDAM only
    obs = _observation(
        "Jet_3",
        target.position,
        payload={"JDAM": 3, "AIM9X": 10},  # AIM9X count is higher but unsuitable
        targets=[target],
    )
    result = compute_bid(obs, target)
    assert result is not None
    _, offered, weapon_type = result
    assert weapon_type == "JDAM"
    assert offered == 3  # not 10 from AIM9X


def test_compute_bid_mobile_radar_requires_aim9x():
    target = TargetInfo(
        target_id="Target_B",
        position=(25.0, 25.0),
        target_type="MOBILE_RADAR",
        hp_required=1,
        hp_remaining=1,
        claimed=False,
    )
    jdam_only = _observation(
        "Jet_2", target.position, payload={"JDAM": 4, "AIM9X": 0}, targets=[target]
    )
    assert compute_bid(jdam_only, target) is None
    mixed = _observation(
        "Jet_3", target.position, payload={"JDAM": 3, "AIM9X": 2}, targets=[target]
    )
    result = compute_bid(mixed, target)
    assert result is not None
    _, offered, weapon_type = result
    assert weapon_type == "AIM9X"
    assert offered == 1


# --- Manager election ---


@pytest.mark.asyncio
async def test_manager_election_no_duplicate_contracts(monkeypatch):
    monkeypatch.setattr(config, "BID_TIMEOUT_SECONDS", 0.05)
    broker = MessageBroker()
    jet_a = JetAgentRuntime("Jet_A", broker)
    jet_b = JetAgentRuntime("Jet_B", broker)
    tasks = [jet_a.start(), jet_b.start()]
    target = _target_a(hp_required=1, hp_remaining=1)
    _set_obs(jet_a, 
        _observation("Jet_A", (75.0, 75.0), {"JDAM": 1, "AIM9X": 0}, [target])
    )
    _set_obs(jet_b, 
        _observation("Jet_B", (74.0, 74.0), {"JDAM": 1, "AIM9X": 0}, [target])
    )

    open_results: list[bool] = []
    original = broker.try_open_contract

    def tracking_open(*args, **kwargs):
        result = original(*args, **kwargs)
        open_results.append(result)
        return result

    broker.try_open_contract = tracking_open  # type: ignore[method-assign]

    try:
        await asyncio.gather(jet_a.scan_and_negotiate(), jet_b.scan_and_negotiate())
        assert open_results.count(True) == 1
        # The loser may either call try_open_contract and get False, or skip via
        # the live broker.is_claimed() check in scan_and_negotiate — both are OK.
        assert open_results.count(False) <= 1
        # Loser of the race is either still IDLE (if they never bid) or a
        # contractor / pending contractor — never a second manager.
        assert sum(1 for j in (jet_a, jet_b) if j.role == "MANAGER") == 0
        assert not (
            jet_a.open_contract_id is not None and jet_b.open_contract_id is not None
        )
    finally:
        for jet in (jet_a, jet_b):
            jet.stop()
        await asyncio.gather(*tasks, return_exceptions=True)


# --- Multi-winner ---


@pytest.mark.asyncio
async def test_multi_winner_partial_fulfillment_for_target_a(monkeypatch):
    monkeypatch.setattr(config, "BID_TIMEOUT_SECONDS", 0.05)
    broker = MessageBroker()
    jet1 = JetAgentRuntime("Jet_1", broker)
    jet2 = JetAgentRuntime("Jet_2", broker)
    jet3 = JetAgentRuntime("Jet_3", broker)
    tasks = [jet1.start(), jet2.start(), jet3.start()]

    target = _target_a()  # HARDENED_BUNKER; Target_B omitted on purpose
    _set_obs(jet1, 
        _observation("Jet_1", (70.0, 70.0), {"JDAM": 1, "AIM9X": 0}, [target])
    )
    _set_obs(jet2, 
        _observation("Jet_2", (5.0, 95.0), {"JDAM": 4, "AIM9X": 0}, [target])
    )
    _set_obs(jet3, 
        _observation("Jet_3", (95.0, 5.0), {"JDAM": 3, "AIM9X": 2}, [target])
    )

    try:
        await jet1.scan_and_negotiate()
        await asyncio.sleep(0.15)  # let ACCEPT/REJECT delivery settle

        contractors = [
            j
            for j in (jet1, jet2, jet3)
            if j.role == "CONTRACTOR" and j.committed_target_id == "Target_A"
        ]
        assert len(contractors) >= 2
        assert sum(j.committed_payload for j in contractors) >= 5
        assert all(j.committed_weapon_type == "JDAM" for j in contractors)

        contractor_ids = {j.agent_id for j in contractors}
        for j in (jet1, jet2, jet3):
            if j.agent_id not in contractor_ids:
                assert j.role == "IDLE"
    finally:
        for jet in (jet1, jet2, jet3):
            jet.stop()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_multi_round_renegotiation_accepts_late_bidder(monkeypatch):
    """Force a round-1 shortfall: helper only gets an Observation after the
    first CFP window closes, so round 2 must pick them up. Regression for
    docs/02_Code_Review_Phases_3-5.md §1."""
    monkeypatch.setattr(config, "BID_TIMEOUT_SECONDS", 0.05)
    broker = MessageBroker()
    manager = JetAgentRuntime("Manager", broker)
    helper = JetAgentRuntime("Helper", broker)
    tasks = [manager.start(), helper.start()]

    target = TargetInfo(
        target_id="T",
        position=(0.0, 0.0),
        target_type="HARDENED_BUNKER",
        hp_required=10,
        hp_remaining=10,
        claimed=False,
    )
    _set_obs(manager, 
        _observation("Manager", (0.0, 0.0), {"JDAM": 1, "AIM9X": 0}, [target])
    )

    async def late_helper_observation() -> None:
        await asyncio.sleep(0.02)  # mid round-1 bid window
        _set_obs(helper, 
            _observation("Helper", (0.0, 0.0), {"JDAM": 9, "AIM9X": 0}, [target])
        )

    try:
        await asyncio.gather(manager.scan_and_negotiate(), late_helper_observation())
        await asyncio.sleep(0.1)

        assert manager.role == "CONTRACTOR"
        assert manager.committed_target_id == "T"
        assert manager.committed_payload == 1
        assert manager.committed_weapon_type == "JDAM"
        assert helper.role == "CONTRACTOR"
        assert helper.committed_target_id == "T"
        assert helper.committed_payload == 9
        assert helper.committed_weapon_type == "JDAM"
        assert manager.committed_payload + helper.committed_payload >= 10
        assert helper.role != "CONTRACTOR_PENDING"
        assert manager.managed_target_id is None
        assert broker.is_claimed("T") is True
        assert manager._cfp_round >= 2
    finally:
        for jet in (manager, helper):
            jet.stop()
        await asyncio.gather(*tasks, return_exceptions=True)


# --- Tie-break ---


@pytest.mark.asyncio
async def test_tie_break_prefers_lexicographically_smaller_agent_id(monkeypatch):
    monkeypatch.setattr(config, "BID_TIMEOUT_SECONDS", 0.05)
    for _ in range(3):  # deterministic across repeated runs
        broker = MessageBroker()
        manager = JetAgentRuntime("Manager", broker)
        jet_b = JetAgentRuntime("Jet_B", broker)
        jet_c = JetAgentRuntime("Jet_C", broker)
        tasks = [manager.start(), jet_b.start(), jet_c.start()]

        target = TargetInfo(
            target_id="Target_Tie",
            position=(50.0, 50.0),
            target_type="HARDENED_BUNKER",
            hp_required=1,
            hp_remaining=1,
            claimed=False,
        )
        # Manager has zero payload so it does not self-bid / self-win.
        _set_obs(manager, 
            _observation("Manager", (0.0, 0.0), {"JDAM": 0, "AIM9X": 0}, [target])
        )
        pos = (50.0, 60.0)
        _set_obs(jet_b, 
            _observation("Jet_B", pos, {"JDAM": 1, "AIM9X": 0}, [target])
        )
        _set_obs(jet_c, 
            _observation("Jet_C", pos, {"JDAM": 1, "AIM9X": 0}, [target])
        )

        u_b, _, _ = compute_bid(jet_b.latest_observation, target)
        u_c, _, _ = compute_bid(jet_c.latest_observation, target)
        assert u_b == pytest.approx(u_c)

        try:
            await manager.scan_and_negotiate()
            await asyncio.sleep(0.1)
            assert jet_b.role == "CONTRACTOR"
            assert jet_b.committed_target_id == "Target_Tie"
            assert jet_b.committed_weapon_type == "JDAM"
            assert jet_c.role == "IDLE"
        finally:
            for jet in (manager, jet_b, jet_c):
                jet.stop()
            await asyncio.gather(*tasks, return_exceptions=True)


# --- decide_intent ---


def test_decide_intent_idle_without_commitment():
    broker = MessageBroker()
    jet = JetAgentRuntime("Jet_1", broker)
    target = _target_a()
    _set_obs(jet, 
        _observation("Jet_1", (70.0, 70.0), {"JDAM": 1, "AIM9X": 0}, [target])
    )
    intent = jet.decide_intent()
    assert intent.action_type == "IDLE"


def test_decide_intent_move_when_far_from_committed_target():
    broker = MessageBroker()
    jet = JetAgentRuntime("Jet_1", broker)
    target = _target_a()
    _set_obs(jet, 
        _observation("Jet_1", (70.0, 70.0), {"JDAM": 1, "AIM9X": 0}, [target])
    )
    jet._commit("Target_A", 1, "JDAM")
    intent = jet.decide_intent()
    assert intent.action_type == "MOVE"
    assert intent.target_position == target.position
    assert math.dist((70.0, 70.0), target.position) > config.STRIKE_RANGE


def test_decide_intent_strike_when_in_range_decrements_committed_payload():
    broker = MessageBroker()
    jet = JetAgentRuntime("Jet_1", broker)
    target = _target_a()
    _set_obs(jet, 
        _observation("Jet_1", target.position, {"JDAM": 1, "AIM9X": 0}, [target])
    )
    jet._commit("Target_A", 2, "JDAM")
    intent = jet.decide_intent()
    assert intent.action_type == "STRIKE"
    assert intent.target_id == "Target_A"
    assert intent.weapon_type == "JDAM"
    assert jet.committed_payload == 1


def test_decide_intent_resets_when_target_destroyed_or_payload_exhausted():
    broker = MessageBroker()
    jet = JetAgentRuntime("Jet_1", broker)
    dead = _target_a(hp_remaining=0)
    _set_obs(jet, 
        _observation("Jet_1", dead.position, {"JDAM": 1, "AIM9X": 0}, [dead])
    )
    jet._commit("Target_A", 1, "JDAM")
    intent = jet.decide_intent()
    assert intent.action_type == "IDLE"
    assert jet.role == "IDLE"
    assert jet.committed_target_id is None

    live = _target_a()
    _set_obs(jet, 
        _observation("Jet_1", live.position, {"JDAM": 1, "AIM9X": 0}, [live])
    )
    jet._commit("Target_A", 0, "JDAM")
    intent = jet.decide_intent()
    assert intent.action_type == "IDLE"
    assert jet.role == "IDLE"
