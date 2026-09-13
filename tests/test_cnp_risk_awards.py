"""Phase 45 Part G/I: deterministic milli-damage award selection."""

from __future__ import annotations

from wraithnet.bid_schemas import (
    AttackPlanSummary,
    AwardContext,
    BidProposal,
    BidRiskBreakdownModel,
    BidUtilityBreakdownModel,
    BidderStateSnapshot,
    ThreatContact,
)
from wraithnet.bid_utility import build_bid_proposals, select_winning_bids


def _proposal(
    *,
    bidder: str,
    weapon: str = "JDAM",
    offered: int = 1,
    util: float = 0.5,
    risk: float = 0.0,
    milli: int | None = None,
    flares: int = 0,
    flare_cap: int = 6,
    context_hash: str = "h",
) -> BidProposal:
    damage_milli = offered * 1000 if milli is None else milli
    return BidProposal(
        bidder_id=bidder,
        weapon_type=weapon,
        payload_offered=offered,
        plan=AttackPlanSummary(
            weapon_type=weapon,
            launch_xy=(0.0, 0.0),
            launch_altitude=0.0,
            estimated_ticks_to_launch=0,
            estimated_ticks_to_impact=1,
        ),
        bidder_state=BidderStateSnapshot(
            agent_id=bidder,
            position=(0.0, 0.0),
            altitude=0.0,
            heading_deg=0.0,
            velocity=0.0,
            hp=3,
            hp_max=3,
            flares_remaining=flares,
            flare_capacity=flare_cap,
        ),
        risk=BidRiskBreakdownModel(
            ir_risk=risk, radar_risk=0.0, gun_risk=0.0, combined=risk
        ),
        utility=BidUtilityBreakdownModel(
            capability=1.0,
            reach=1.0,
            survival=1.0 - risk,
            recovery=1.0,
            total=util,
        ),
        normalized_context_hash=context_hash,
        expected_damage_milli=damage_milli,
        delivery_probability=1.0,
    )


def test_dp_multi_bidder_covers_raw_need_minimally():
    """Three 1-HP bids cover a 2-HP target with an inclusion-minimal pair."""
    props = [
        _proposal(bidder="Jet_C", offered=1, util=0.40),
        _proposal(bidder="Jet_A", offered=1, util=0.55),
        _proposal(bidder="Jet_B", offered=1, util=0.50),
    ]
    award = select_winning_bids(
        props,
        AwardContext(
            target_id="T",
            unreserved_hp=2,
            context_hash="h",
            coverage_reserve_factor=1.0,
        ),
    )
    assert len(award.accepted) == 2
    assert {p.bidder_id for p in award.accepted} == {"Jet_A", "Jet_B"}
    assert award.covered_milli >= 2000
    assert award.low_confidence_coverage is False


def test_dp_prefers_reserve_coverage_when_available():
    # Inclusion-minimal raw coverers: {A=3000} meets reserve; {B=2000} meets raw only.
    props = [
        _proposal(bidder="Jet_A", offered=3, util=0.40, milli=3000),
        _proposal(bidder="Jet_B", offered=2, util=0.95, milli=2000),
    ]
    award = select_winning_bids(
        props,
        AwardContext(
            target_id="T",
            unreserved_hp=2,
            context_hash="h",
            coverage_reserve_factor=1.25,
        ),
    )
    assert [p.bidder_id for p in award.accepted] == ["Jet_A"]
    assert award.low_confidence_coverage is False
    assert award.covered_milli >= 2500

    alone = select_winning_bids(
        [_proposal(bidder="Jet_B", offered=2, util=0.95, milli=2000)],
        AwardContext(
            target_id="T",
            unreserved_hp=2,
            context_hash="h",
            coverage_reserve_factor=1.25,
        ),
    )
    assert alone.accepted and alone.low_confidence_coverage is True


def test_partial_award_when_swarm_cannot_cover_raw_need():
    """Under-armed bids must still be accepted so contractors engage (not idle-bingo)."""
    props = [
        _proposal(bidder="Jet_1", offered=1, util=0.80),
        _proposal(bidder="Jet_2", offered=1, util=0.70),
    ]
    award = select_winning_bids(
        props,
        AwardContext(
            target_id="Tank_1",
            unreserved_hp=6,
            context_hash="h",
        ),
    )
    assert len(award.accepted) == 2
    assert award.covered_milli == 2000
    assert award.low_confidence_coverage is True
    assert set(award.reject_reasons) == set()


def test_ir_flare_disadvantage_in_award():
    ir = [
        ThreatContact(
            threat_id="ir1",
            kind="IR_MISSILE",
            position=(12.0, 12.0),
            observed_tick=1,
        )
    ]
    rich = build_bid_proposals(
        bidder_id="Jet_Rich",
        position=(10.0, 10.0),
        altitude=8.0,
        heading_deg=90.0,
        velocity=2.0,
        payload={"JDAM": 2},
        target_id="T1",
        target_type="HARDENED_BUNKER",
        target_position=(11.0, 11.0),
        hp_needed=1,
        flares_remaining=6,
        flare_capacity=6,
        flare_cooldown_remaining=0,
        frame_flare_cooldown_ticks=2,
        has_rwr=True,
        notch_hold_progress=0,
        notch_hold_ticks=2,
        hp=3,
        hp_max=3,
        threat_contacts=ir,
        context_hash="ir",
    )
    poor = build_bid_proposals(
        bidder_id="Jet_Poor",
        position=(10.0, 10.0),
        altitude=8.0,
        heading_deg=90.0,
        velocity=2.0,
        payload={"JDAM": 2},
        target_id="T1",
        target_type="HARDENED_BUNKER",
        target_position=(11.0, 11.0),
        hp_needed=1,
        flares_remaining=0,
        flare_capacity=6,
        flare_cooldown_remaining=0,
        frame_flare_cooldown_ticks=2,
        has_rwr=True,
        notch_hold_progress=0,
        notch_hold_ticks=2,
        hp=3,
        hp_max=3,
        threat_contacts=ir,
        context_hash="ir",
    )
    award = select_winning_bids(
        [*poor, *rich],
        AwardContext(target_id="T1", unreserved_hp=1, context_hash="ir"),
    )
    assert len(award.accepted) == 1
    assert award.accepted[0].bidder_id == "Jet_Rich"


def test_zero_ir_flare_neutrality_in_award():
    rich = build_bid_proposals(
        bidder_id="Jet_Rich",
        position=(10.0, 10.0),
        altitude=8.0,
        heading_deg=90.0,
        velocity=2.0,
        payload={"JDAM": 1},
        target_id="T1",
        target_type="HARDENED_BUNKER",
        target_position=(11.0, 11.0),
        hp_needed=1,
        flares_remaining=6,
        flare_capacity=6,
        flare_cooldown_remaining=0,
        frame_flare_cooldown_ticks=2,
        has_rwr=True,
        notch_hold_progress=0,
        notch_hold_ticks=2,
        hp=3,
        hp_max=3,
        threat_contacts=[],
        context_hash="z",
    )
    poor = build_bid_proposals(
        bidder_id="Jet_Poor",
        position=(10.0, 10.0),
        altitude=8.0,
        heading_deg=90.0,
        velocity=2.0,
        payload={"JDAM": 1},
        target_id="T1",
        target_type="HARDENED_BUNKER",
        target_position=(11.0, 11.0),
        hp_needed=1,
        flares_remaining=0,
        flare_capacity=6,
        flare_cooldown_remaining=0,
        frame_flare_cooldown_ticks=2,
        has_rwr=True,
        notch_hold_progress=0,
        notch_hold_ticks=2,
        hp=3,
        hp_max=3,
        threat_contacts=[],
        context_hash="z",
    )
    assert rich and poor
    assert rich[0].utility.total == poor[0].utility.total
    # Equal utility → stable bidder-id tie-break (Jet_Poor before Jet_Rich).
    award = select_winning_bids(
        [*rich, *poor],
        AwardContext(target_id="T1", unreserved_hp=1, context_hash="z"),
    )
    assert award.accepted[0].bidder_id == "Jet_Poor"


def test_insertion_order_determinism():
    props_a = [
        _proposal(bidder="Jet_B", offered=1, util=0.5),
        _proposal(bidder="Jet_A", offered=1, util=0.5),
        _proposal(bidder="Jet_C", offered=1, util=0.4),
    ]
    props_b = list(reversed(props_a))
    ctx = AwardContext(target_id="T", unreserved_hp=1, context_hash="h")
    a = select_winning_bids(props_a, ctx)
    b = select_winning_bids(props_b, ctx)
    assert [p.bidder_id for p in a.accepted] == [p.bidder_id for p in b.accepted]
    assert a.accepted[0].bidder_id == "Jet_A"


def test_rejects_context_hash_mismatch():
    props = [
        _proposal(bidder="Jet_A", context_hash="wrong"),
        _proposal(bidder="Jet_B", context_hash="h"),
    ]
    award = select_winning_bids(
        props,
        AwardContext(target_id="T", unreserved_hp=1, context_hash="h"),
    )
    assert award.reject_reasons.get("Jet_A") == "CONTEXT_HASH_MISMATCH"
    assert [p.bidder_id for p in award.accepted] == ["Jet_B"]


def test_at_most_one_proposal_per_bidder():
    props = [
        _proposal(bidder="Jet_A", weapon="JDAM", offered=1, util=0.9),
        _proposal(bidder="Jet_A", weapon="AGM65", offered=1, util=0.8),
        _proposal(bidder="Jet_B", weapon="JDAM", offered=1, util=0.7),
    ]
    award = select_winning_bids(
        props,
        AwardContext(
            target_id="T",
            unreserved_hp=2,
            context_hash="h",
            coverage_reserve_factor=1.0,
        ),
    )
    winners = [p.bidder_id for p in award.accepted]
    assert len(winners) == len(set(winners))
    assert set(winners) == {"Jet_A", "Jet_B"}


def test_dp_covers_where_single_best_greedy_misses():
    """Need 3 HP; each top jet offers only 2 — single-winner greedy under-covers."""
    props = [
        _proposal(bidder="Jet_A", offered=2, util=0.99),
        _proposal(bidder="Jet_B", offered=2, util=0.50),
    ]
    # Naive single-best greedy would take only Jet_A (2000 < 3000).
    best_alone = max(props, key=lambda p: (p.utility.total, p.bidder_id))
    assert best_alone.expected_damage_milli < 3000

    award = select_winning_bids(
        props,
        AwardContext(
            target_id="T",
            unreserved_hp=3,
            context_hash="h",
            coverage_reserve_factor=1.0,
        ),
    )
    assert len(award.accepted) == 2
    assert {p.bidder_id for p in award.accepted} == {"Jet_A", "Jet_B"}
    assert award.covered_milli >= 3000


def test_award_and_proposal_json_order_independent():
    props = [
        _proposal(bidder="Jet_C", offered=1, util=0.4),
        _proposal(bidder="Jet_A", offered=2, util=0.6),
        _proposal(bidder="Jet_B", offered=2, util=0.55),
    ]
    ctx = AwardContext(
        target_id="T", unreserved_hp=3, context_hash="h", coverage_reserve_factor=1.0
    )
    forward = select_winning_bids(props, ctx)
    backward = select_winning_bids(list(reversed(props)), ctx)
    assert [p.bidder_id for p in forward.accepted] == [
        p.bidder_id for p in backward.accepted
    ]
    assert [p.model_dump_json() for p in forward.accepted] == [
        p.model_dump_json() for p in backward.accepted
    ]


def test_zero_flare_still_wins_unique_safe_task():
    """Design §13.5: zero flares may win when no IR and unique capability."""
    only = build_bid_proposals(
        bidder_id="Jet_NoFlare",
        position=(10.0, 10.0),
        altitude=8.0,
        heading_deg=90.0,
        velocity=2.0,
        payload={"JDAM": 1},
        target_id="T1",
        target_type="HARDENED_BUNKER",
        target_position=(11.0, 11.0),
        hp_needed=1,
        flares_remaining=0,
        flare_capacity=6,
        flare_cooldown_remaining=0,
        frame_flare_cooldown_ticks=2,
        has_rwr=True,
        notch_hold_progress=0,
        notch_hold_ticks=2,
        hp=3,
        hp_max=3,
        threat_contacts=[],
        context_hash="safe",
    )
    award = select_winning_bids(
        only,
        AwardContext(target_id="T1", unreserved_hp=1, context_hash="safe"),
    )
    assert len(award.accepted) == 1
    assert award.accepted[0].bidder_id == "Jet_NoFlare"
