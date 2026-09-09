"""Phase 45 design §13 bid-utility / risk cases + compact calibration smoke."""

from __future__ import annotations

import math

import pytest

from wraithnet.bid_schemas import AwardContext, ThreatContact
from wraithnet.bid_utility import (
    build_bid_proposals,
    estimate_threat_exposure,
    flare_readiness,
    gun_risk,
    ir_risk,
    notch_readiness,
    radar_risk,
    score_bid,
    select_winning_bids,
)


def _finite(*values: float) -> None:
    for v in values:
        assert math.isfinite(v)
        assert not math.isnan(v)


def _proposals(
    *,
    bidder: str,
    flares: int,
    contacts: list[ThreatContact],
    context_hash: str = "ctx",
    payload: dict[str, int] | None = None,
):
    return build_bid_proposals(
        bidder_id=bidder,
        position=(10.0, 10.0),
        altitude=8.0,
        heading_deg=90.0,
        velocity=2.0,
        payload=payload or {"JDAM": 2},
        target_id="T1",
        target_type="HARDENED_BUNKER",
        target_position=(11.0, 11.0),
        hp_needed=1,
        flares_remaining=flares,
        flare_capacity=6,
        flare_cooldown_remaining=0,
        frame_flare_cooldown_ticks=2,
        has_rwr=True,
        notch_hold_progress=0,
        notch_hold_ticks=2,
        hp=3,
        hp_max=3,
        threat_contacts=contacts,
        context_hash=context_hash,
    )


def test_fewer_flares_lower_bid_under_equal_ir():
    ir = [
        ThreatContact(
            threat_id="ir1", kind="IR_MISSILE", position=(12.0, 12.0), observed_tick=1
        )
    ]
    rich = _proposals(bidder="Jet_Rich", flares=6, contacts=ir)
    poor = _proposals(bidder="Jet_Poor", flares=1, contacts=ir)
    assert rich and poor
    assert rich[0].utility.total > poor[0].utility.total
    _finite(rich[0].utility.total, poor[0].utility.total, rich[0].risk.ir_risk)


def test_zero_flares_max_ir_penalty():
    ready = flare_readiness(
        flares_remaining=0,
        flare_capacity=6,
        flare_cooldown_remaining=0,
        ticks_to_first_ir_encounter=1.0,
        frame_flare_cooldown_ticks=2,
    )
    assert ready == 0.0
    assert ir_risk(known_ir_exposure=1.0, flare_ready=ready) == pytest.approx(1.0)


def test_zero_ir_flare_count_no_utility_effect():
    a = _proposals(bidder="Jet_A", flares=6, contacts=[])
    b = _proposals(bidder="Jet_B", flares=0, contacts=[])
    assert a and b
    assert a[0].utility.total == b[0].utility.total


def test_flare_has_no_radar_or_gun_effect():
    with_flares = estimate_threat_exposure(
        known_ir_exposure=0.0,
        known_radar_exposure=0.8,
        known_gun_exposure=0.6,
        flare_ready=1.0,
        notch_ready=0.0,
        hp_fraction=1.0,
        evasion_bonus=0.0,
    )
    no_flares = estimate_threat_exposure(
        known_ir_exposure=0.0,
        known_radar_exposure=0.8,
        known_gun_exposure=0.6,
        flare_ready=0.0,
        notch_ready=0.0,
        hp_fraction=1.0,
        evasion_bonus=0.0,
    )
    assert with_flares.radar_risk == no_flares.radar_risk
    assert with_flares.gun_risk == no_flares.gun_risk
    assert with_flares.combined == no_flares.combined


def test_notch_affects_radar_only():
    hi = notch_readiness(has_rwr=True, notch_hold_progress=2, notch_hold_ticks=2)
    lo = notch_readiness(has_rwr=True, notch_hold_progress=0, notch_hold_ticks=2)
    assert radar_risk(known_radar_exposure=1.0, notch_ready=hi) < radar_risk(
        known_radar_exposure=1.0, notch_ready=lo
    )
    assert gun_risk(
        known_gun_exposure=1.0, hp_fraction=1.0, evasion_bonus=0.0
    ) == gun_risk(known_gun_exposure=1.0, hp_fraction=1.0, evasion_bonus=0.0)
    # Flare readiness must not change radar.
    assert radar_risk(known_radar_exposure=1.0, notch_ready=0.5) == radar_risk(
        known_radar_exposure=1.0, notch_ready=0.5
    )


def test_score_bid_no_nan_inf_at_boundaries():
    risk = estimate_threat_exposure(
        known_ir_exposure=1.0,
        known_radar_exposure=1.0,
        known_gun_exposure=1.0,
        flare_ready=0.0,
        notch_ready=0.0,
        hp_fraction=0.0,
        evasion_bonus=0.0,
    )
    util = score_bid(capability=0.0, reach=0.0, risk=risk, recovery=0.0)
    _finite(util.total, util.survival, risk.combined, risk.ir_risk)


def test_proposal_json_insertion_order_determinism():
    ir = [
        ThreatContact(
            threat_id="b", kind="IR_MISSILE", position=(1.0, 1.0), observed_tick=1
        ),
        ThreatContact(
            threat_id="a", kind="GUN_AAA", position=(2.0, 2.0), observed_tick=1
        ),
    ]
    first = _proposals(
        bidder="Jet_1",
        flares=3,
        contacts=ir,
        payload={"AGM65": 1, "JDAM": 2},
    )
    second = _proposals(
        bidder="Jet_1",
        flares=3,
        contacts=list(reversed(ir)),
        payload={"JDAM": 2, "AGM65": 1},
    )
    assert [p.model_dump_json() for p in first] == [p.model_dump_json() for p in second]


def test_calibration_smoke_threat_flavors_no_nan():
    """Compact Part J smoke: a few seeds × threat flavors produce finite awards."""
    flavors = {
        "no_threat": [],
        "ir_heavy": [
            ThreatContact(
                threat_id=f"ir_{i}",
                kind="IR_MISSILE",
                position=(10.0 + i, 10.0),
                observed_tick=1,
                confidence=0.9,
            )
            for i in range(2)
        ],
        "aaa": [
            ThreatContact(
                threat_id="aaa1",
                kind="GUN_AAA",
                position=(12.0, 12.0),
                observed_tick=1,
                readiness=1.0,
            )
        ],
    }
    seeds = (0, 1, 2)
    for flavor, contacts in flavors.items():
        for seed in seeds:
            props = []
            for i, flares in enumerate((6, 2, 0)):
                bidder = f"Jet_{flavor}_{seed}_{i}"
                # Slight position jitter by seed — must not introduce NaN.
                offset = 0.1 * ((seed + i) % 3)
                built = build_bid_proposals(
                    bidder_id=bidder,
                    position=(10.0 + offset, 10.0),
                    altitude=8.0,
                    heading_deg=90.0,
                    velocity=2.0,
                    payload={"JDAM": 2},
                    target_id=f"T_{flavor}",
                    target_type="HARDENED_BUNKER",
                    target_position=(14.0, 14.0),
                    hp_needed=2,
                    flares_remaining=flares,
                    flare_capacity=6,
                    flare_cooldown_remaining=0,
                    frame_flare_cooldown_ticks=2,
                    has_rwr=True,
                    notch_hold_progress=0,
                    notch_hold_ticks=2,
                    hp=3,
                    hp_max=3,
                    threat_contacts=contacts,
                    context_hash=f"{flavor}-{seed}",
                )
                assert built, f"expected proposals for {flavor}/{seed}/{bidder}"
                for p in built:
                    _finite(p.utility.total, p.risk.combined, p.risk.ir_risk)
                props.extend(built)
            award = select_winning_bids(
                props,
                AwardContext(
                    target_id=f"T_{flavor}",
                    unreserved_hp=2,
                    context_hash=f"{flavor}-{seed}",
                    coverage_reserve_factor=1.0,
                ),
            )
            assert award.accepted, f"expected an award for {flavor} seed={seed}"
            _finite(award.award_score)
            covered = sum(int(p.expected_damage_milli) for p in award.accepted)
            assert covered >= 2000
