"""Phase 45: risk-weighted bidding vs baseline + award selection."""

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
from wraithnet.bid_utility import (
    build_bid_proposals,
    estimate_threat_exposure,
    flare_readiness,
    ir_risk,
    score_bid,
    select_winning_bids,
)
from wraithnet.schemas import Observation, TargetInfo


def _obs(**overrides) -> Observation:
    base = dict(
        agent_id="Jet_1",
        tick=1,
        current_position=(10.0, 10.0),
        sensor_radius=65.0,
        payload={"JDAM": 2},
        known_targets=[],
        flares_remaining=6,
        flare_capacity=6,
        flare_cooldown_remaining=0,
    )
    base.update(overrides)
    return Observation(**base)


def test_fewer_flares_raises_ir_risk():
    hi = flare_readiness(
        flares_remaining=6,
        flare_capacity=6,
        flare_cooldown_remaining=0,
        ticks_to_first_ir_encounter=2.0,
        frame_flare_cooldown_ticks=2,
    )
    lo = flare_readiness(
        flares_remaining=1,
        flare_capacity=6,
        flare_cooldown_remaining=0,
        ticks_to_first_ir_encounter=2.0,
        frame_flare_cooldown_ticks=2,
    )
    assert hi > lo
    assert ir_risk(known_ir_exposure=1.0, flare_ready=lo) > ir_risk(
        known_ir_exposure=1.0, flare_ready=hi
    )


def test_zero_flares_max_ir_penalty():
    ready = flare_readiness(
        flares_remaining=0,
        flare_capacity=6,
        flare_cooldown_remaining=0,
        ticks_to_first_ir_encounter=1.0,
        frame_flare_cooldown_ticks=2,
    )
    assert ready == 0.0
    assert ir_risk(known_ir_exposure=1.0, flare_ready=ready) == 1.0


def test_zero_capacity_no_division_error():
    ready = flare_readiness(
        flares_remaining=0,
        flare_capacity=0,
        flare_cooldown_remaining=0,
        ticks_to_first_ir_encounter=None,
        frame_flare_cooldown_ticks=2,
    )
    assert ready == 0.0


def test_flare_count_irrelevant_without_ir_exposure():
    a = estimate_threat_exposure(
        known_ir_exposure=0.0,
        known_radar_exposure=0.2,
        known_gun_exposure=0.0,
        flare_ready=1.0,
        notch_ready=0.5,
        hp_fraction=1.0,
        evasion_bonus=0.0,
    )
    b = estimate_threat_exposure(
        known_ir_exposure=0.0,
        known_radar_exposure=0.2,
        known_gun_exposure=0.0,
        flare_ready=0.0,
        notch_ready=0.5,
        hp_fraction=1.0,
        evasion_bonus=0.0,
    )
    assert a.ir_risk == 0.0 and b.ir_risk == 0.0
    assert a.combined == b.combined


def test_score_bid_penalizes_ir_when_flares_low():
    risk_hi = estimate_threat_exposure(
        known_ir_exposure=1.0,
        known_radar_exposure=0.0,
        known_gun_exposure=0.0,
        flare_ready=1.0,
        notch_ready=0.0,
        hp_fraction=1.0,
        evasion_bonus=0.0,
    )
    risk_lo = estimate_threat_exposure(
        known_ir_exposure=1.0,
        known_radar_exposure=0.0,
        known_gun_exposure=0.0,
        flare_ready=0.0,
        notch_ready=0.0,
        hp_fraction=1.0,
        evasion_bonus=0.0,
    )
    util_hi = score_bid(capability=1.0, reach=1.0, risk=risk_hi, recovery=1.0)
    util_lo = score_bid(capability=1.0, reach=1.0, risk=risk_lo, recovery=1.0)
    assert util_hi.total > util_lo.total


def test_build_proposals_flare_scarce_loses_under_ir():
    ir = [
        ThreatContact(
            threat_id="m1",
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
        context_hash="h",
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
        context_hash="h",
    )
    assert rich and poor
    assert rich[0].utility.total > poor[0].utility.total


def test_zero_threat_matches_legacy_compute_bid():
    from wraithnet.agents import compute_bid

    obs = _obs(payload={"JDAM": 3, "AGM65": 1})
    target = TargetInfo(
        target_id="T_A",
        position=(30.0, 30.0),
        hp_remaining=2,
        hp_required=2,
        target_type="HARDENED_BUNKER",
    )
    bid = compute_bid(obs, target, hp_needed=2, threat_contacts=[], context_hash="")
    assert bid is not None
    utility, offered, weapon = bid
    assert weapon == "JDAM"
    assert offered == 2
    # Sanity: utility is finite and uses legacy path (no clamp to [0,1] only).
    assert utility != 0.0


def test_select_winning_bids_tie_break_by_sender():
    def _p(bidder: str, util: float, offered: int = 1) -> BidProposal:
        return BidProposal(
            bidder_id=bidder,
            weapon_type="JDAM",
            payload_offered=offered,
            plan=AttackPlanSummary(
                weapon_type="JDAM",
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
                flares_remaining=0,
                flare_capacity=0,
            ),
            risk=BidRiskBreakdownModel(
                ir_risk=0.0, radar_risk=0.0, gun_risk=0.0, combined=0.0
            ),
            utility=BidUtilityBreakdownModel(
                capability=1.0,
                reach=1.0,
                survival=1.0,
                recovery=1.0,
                total=util,
            ),
            normalized_context_hash="",
        )

    award = select_winning_bids(
        [_p("Jet_B", 0.5), _p("Jet_A", 0.5)],
        AwardContext(target_id="T", unreserved_hp=1, context_hash=""),
    )
    assert len(award.accepted) == 1
    assert award.accepted[0].bidder_id == "Jet_A"


def test_bid_proposal_schema_validates():
    proposal = BidProposal(
        bidder_id="Jet_1",
        weapon_type="JDAM",
        payload_offered=1,
        plan=AttackPlanSummary(
            weapon_type="JDAM",
            launch_xy=(10.0, 10.0),
            launch_altitude=8.0,
            estimated_ticks_to_launch=5,
            estimated_ticks_to_impact=6,
        ),
        bidder_state=BidderStateSnapshot(
            agent_id="Jet_1",
            position=(10.0, 10.0),
            altitude=8.0,
            heading_deg=90.0,
            velocity=2.0,
            hp=3,
            hp_max=3,
            flares_remaining=2,
            flare_capacity=6,
        ),
        risk=BidRiskBreakdownModel(
            ir_risk=0.2, radar_risk=0.0, gun_risk=0.1, combined=0.28
        ),
        utility=BidUtilityBreakdownModel(
            capability=1.0, reach=0.8, survival=0.7, recovery=0.9, total=0.85
        ),
        normalized_context_hash="abc",
    )
    assert proposal.payload_offered == 1
