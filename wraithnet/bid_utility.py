"""Phase 45: risk-aware bid utility (pure, no asyncio/Mesa/viewer).

Scoring primitives + proposal builders + deterministic award selection.
When known exposure is zero, utilities match the historical weapon/payload/
distance formula so the default PoC assignment stays stable.

Default ``BidUtilityWeights`` / ``score_bid`` tuple ``(0.35, 0.25, 0.25, 0.15)``
(capability, reach, survival, recovery):

* capability 0.35 — weapon/target fit and payload coverage dominate; a uniquely
  capable jet must still outrank a safer but useless loadout.
* reach 0.25 — travel/geometry matter but must not swamp survivability.
* survival 0.25 — IR/radar/gun risk (flare/notch/HP mitigation) is first-class
  without letting threat fear veto every bid.
* recovery 0.15 — fuel/base forecast is a soft preference; hard no-bid handles
  impossible recovery separately.

Mitigation caps (IR 0.80, radar 0.75, gun 0.50) keep readiness from erasing
exposure. Award defaults: ``coverage_reserve_factor=1.25``,
``overkill_weight=0.20``, ``team_size_weight=0.05``.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from itertools import product

from wraithnet import config
from wraithnet.bid_schemas import (
    AttackPlanSummary,
    AwardContext,
    BidProposal,
    BidRejectReason,
    BidRiskBreakdownModel,
    BidUtilityBreakdownModel,
    BidderStateSnapshot,
    ThreatContact,
)

# Bounded enumeration limits (Part G): keep award selection deterministic and finite.
MAX_AWARD_BIDDERS = 12
MAX_PROPOSALS_PER_BIDDER = 6


def clamp01(x: float) -> float:
    return 0.0 if x < 0.0 else 1.0 if x > 1.0 else float(x)


def flare_readiness(
    *,
    flares_remaining: int,
    flare_capacity: int,
    flare_cooldown_remaining: int,
    ticks_to_first_ir_encounter: float | None,
    frame_flare_cooldown_ticks: int,
) -> float:
    if flare_capacity <= 0:
        return 0.0
    inventory = clamp01(flares_remaining / max(1, flare_capacity))
    if ticks_to_first_ir_encounter is None:
        cooldown_term = 1.0 if flare_cooldown_remaining <= 0 else 0.0
    else:
        if flare_cooldown_remaining <= ticks_to_first_ir_encounter:
            cooldown_term = 1.0
        else:
            denom = max(1, int(frame_flare_cooldown_ticks))
            cooldown_term = clamp01(
                1.0
                - (flare_cooldown_remaining - ticks_to_first_ir_encounter) / denom
            )
    return clamp01(inventory * cooldown_term)


def ir_risk(
    *,
    known_ir_exposure: float,
    flare_ready: float,
    ir_mitigation_effectiveness: float = 0.80,
) -> float:
    mitigation = min(ir_mitigation_effectiveness, 0.80)
    return clamp01(known_ir_exposure * (1.0 - mitigation * flare_ready))


def notch_readiness(
    *,
    has_rwr: bool,
    notch_hold_progress: int,
    notch_hold_ticks: int,
) -> float:
    if not has_rwr:
        return 0.0
    if notch_hold_ticks <= 0:
        return 1.0
    return clamp01(notch_hold_progress / max(1, notch_hold_ticks))


def radar_risk(
    *,
    known_radar_exposure: float,
    notch_ready: float,
    radar_mitigation_effectiveness: float = 0.75,
) -> float:
    mitigation = min(radar_mitigation_effectiveness, 0.75)
    return clamp01(known_radar_exposure * (1.0 - mitigation * notch_ready))


def gun_risk(
    *,
    known_gun_exposure: float,
    hp_fraction: float,
    evasion_bonus: float,
    gun_mitigation_effectiveness: float = 0.50,
) -> float:
    mitigation = min(gun_mitigation_effectiveness, 0.50)
    survivability = clamp01(0.5 * hp_fraction + 0.5 * clamp01(evasion_bonus))
    return clamp01(known_gun_exposure * (1.0 - mitigation * survivability))


@dataclass(frozen=True)
class BidRiskBreakdown:
    ir_risk: float
    radar_risk: float
    gun_risk: float

    @property
    def combined(self) -> float:
        return clamp01(
            1.0
            - (1.0 - self.ir_risk) * (1.0 - self.radar_risk) * (1.0 - self.gun_risk)
        )


def estimate_threat_exposure(
    *,
    known_ir_exposure: float,
    known_radar_exposure: float,
    known_gun_exposure: float,
    flare_ready: float,
    notch_ready: float,
    hp_fraction: float,
    evasion_bonus: float,
) -> BidRiskBreakdown:
    return BidRiskBreakdown(
        ir_risk=ir_risk(
            known_ir_exposure=known_ir_exposure, flare_ready=flare_ready
        ),
        radar_risk=radar_risk(
            known_radar_exposure=known_radar_exposure, notch_ready=notch_ready
        ),
        gun_risk=gun_risk(
            known_gun_exposure=known_gun_exposure,
            hp_fraction=hp_fraction,
            evasion_bonus=evasion_bonus,
        ),
    )


@dataclass(frozen=True)
class BidUtilityBreakdown:
    capability: float
    reach: float
    survival: float
    recovery: float
    total: float


def score_bid(
    *,
    capability: float,
    reach: float,
    risk: BidRiskBreakdown,
    recovery: float,
    weights: tuple[float, float, float, float] = (0.35, 0.25, 0.25, 0.15),
) -> BidUtilityBreakdown:
    w_cap, w_reach, w_surv, w_rec = weights
    survival = clamp01(1.0 - risk.combined)
    total = clamp01(
        w_cap * clamp01(capability)
        + w_reach * clamp01(reach)
        + w_surv * survival
        + w_rec * clamp01(recovery)
    )
    return BidUtilityBreakdown(
        capability=clamp01(capability),
        reach=clamp01(reach),
        survival=survival,
        recovery=clamp01(recovery),
        total=total,
    )


def legacy_compatible_utility(
    *,
    suitability: float,
    payload_score: float,
    distance_score: float,
) -> float:
    return (
        config.UTILITY_W_WEAPON * suitability
        + config.UTILITY_W1 * payload_score
        - config.UTILITY_W2 * distance_score
    )


def exposure_from_threat_contacts(
    contacts: Sequence[ThreatContact] | Sequence[object],
) -> tuple[float, float, float]:
    ir = 0.0
    radar = 0.0
    gun = 0.0
    for c in contacts:
        kind = str(getattr(c, "kind", "") or "")
        readiness = float(getattr(c, "readiness", 1.0) or 1.0)
        conf = float(getattr(c, "confidence", 1.0) or 1.0)
        weight = clamp01(readiness * conf)
        if kind in ("IR_MISSILE",) or "IR" in kind:
            ir = max(ir, weight)
        elif kind in ("RADAR_MISSILE", "SAM_SITE") or "RADAR" in kind:
            radar = max(radar, weight)
        elif kind in ("GUN_AAA", "GUN_CIWS") or "GUN" in kind:
            gun = max(gun, weight)
    return ir, radar, gun


def merge_threat_contacts(contacts: Sequence[ThreatContact]) -> list[ThreatContact]:
    def _rank(c: ThreatContact) -> tuple:
        source_rank = 2 if c.source == "DIRECT" else 1 if c.source == "DATALINK" else 0
        return (
            int(c.observed_tick),
            source_rank,
            float(c.confidence),
            str(c.observer_id or ""),
        )

    best: dict[str, ThreatContact] = {}
    for c in contacts:
        prev = best.get(c.threat_id)
        if prev is None or _rank(c) > _rank(prev):
            best[c.threat_id] = c
    return [best[k] for k in sorted(best.keys())]


def estimate_attack_plan(
    *,
    bidder_id: str,
    weapon_type: str,
    position: tuple[float, float],
    altitude: float,
    target_position: tuple[float, float],
    target_altitude: float = 0.0,
) -> AttackPlanSummary | None:
    _ = bidder_id
    _ = target_altitude
    weapon = config.WEAPON_CATALOG.get(weapon_type)  # type: ignore[arg-type]
    if weapon is None:
        return None
    dist = math.dist(position, target_position)
    launch_dist = max(0.0, dist - float(weapon.range))
    speed = 3.0
    ticks_to_launch = int(math.ceil(launch_dist / speed)) if speed > 0 else 0
    return AttackPlanSummary(
        weapon_type=weapon_type,
        launch_xy=position if launch_dist <= 1e-9 else target_position,
        launch_altitude=altitude,
        estimated_ticks_to_launch=ticks_to_launch,
        estimated_ticks_to_impact=ticks_to_launch + 1,
        route=[],
    )


def build_bid_proposals(
    *,
    bidder_id: str,
    position: tuple[float, float],
    altitude: float,
    heading_deg: float,
    velocity: float,
    payload: dict[str, int],
    target_id: str,
    target_type: str,
    target_position: tuple[float, float],
    hp_needed: int,
    flares_remaining: int,
    flare_capacity: int,
    flare_cooldown_remaining: int,
    frame_flare_cooldown_ticks: int,
    has_rwr: bool,
    notch_hold_progress: int,
    notch_hold_ticks: int,
    hp: int,
    hp_max: int,
    threat_contacts: Sequence[ThreatContact],
    context_hash: str,
    committed_payload: int = 0,
    map_diagonal: float | None = None,
) -> list[BidProposal]:
    _ = target_id
    _ = committed_payload
    diagonal = (
        float(map_diagonal)
        if map_diagonal is not None and float(map_diagonal) > 0.0
        else config.MAX_GRID_DISTANCE
    )
    ask = max(1, int(hp_needed))
    ir_exp, radar_exp, gun_exp = exposure_from_threat_contacts(threat_contacts)
    flare_ready = flare_readiness(
        flares_remaining=flares_remaining,
        flare_capacity=flare_capacity,
        flare_cooldown_remaining=flare_cooldown_remaining,
        ticks_to_first_ir_encounter=2.0 if ir_exp > 0 else None,
        frame_flare_cooldown_ticks=frame_flare_cooldown_ticks,
    )
    notch_ready = notch_readiness(
        has_rwr=has_rwr,
        notch_hold_progress=notch_hold_progress,
        notch_hold_ticks=notch_hold_ticks,
    )
    hp_frac = clamp01(hp / max(1, hp_max))
    risk = estimate_threat_exposure(
        known_ir_exposure=ir_exp,
        known_radar_exposure=radar_exp,
        known_gun_exposure=gun_exp,
        flare_ready=flare_ready,
        notch_ready=notch_ready,
        hp_fraction=hp_frac,
        evasion_bonus=0.0,
    )
    zero_threat = ir_exp <= 0.0 and radar_exp <= 0.0 and gun_exp <= 0.0
    # Stable sorted contact set for identical proposal JSON across insertion orders.
    stable_contacts = merge_threat_contacts(list(threat_contacts))

    proposals: list[BidProposal] = []
    for weapon_type, count in sorted(payload.items()):
        if count <= 0:
            continue
        catalog = config.WEAPON_CATALOG.get(weapon_type)  # type: ignore[arg-type]
        if catalog is None:
            continue
        suitability = float(catalog.suitability.get(target_type, 0.0))  # type: ignore[arg-type]
        if suitability <= 0.0 and target_type == "SURFACE_VESSEL":
            from wraithnet.taxonomy import is_legal_weapon_target

            suitability = (
                1.0 if is_legal_weapon_target(weapon_type, "SURFACE_VESSEL") else 0.0
            )
        if suitability <= 0.0:
            continue
        payload_offered = min(int(count), ask)
        payload_score = min(1.0, int(count) / ask)
        distance = math.dist(position, target_position)
        distance_score = distance / diagonal
        if zero_threat:
            total = legacy_compatible_utility(
                suitability=suitability,
                payload_score=payload_score,
                distance_score=distance_score,
            )
            util = BidUtilityBreakdown(
                capability=suitability,
                reach=clamp01(1.0 - distance_score),
                survival=1.0,
                recovery=1.0,
                total=float(total),
            )
        else:
            util = score_bid(
                capability=suitability * payload_score,
                reach=clamp01(1.0 - distance_score),
                risk=risk,
                recovery=1.0,
            )
        plan = estimate_attack_plan(
            bidder_id=bidder_id,
            weapon_type=weapon_type,
            position=position,
            altitude=altitude,
            target_position=target_position,
        )
        if plan is None:
            continue
        delivery_probability = clamp01(float(catalog.base_hit_probability))
        expected_damage_milli = int(
            round(
                payload_offered
                * int(catalog.damage_per_hit)
                * delivery_probability
                * 1000.0
            )
        )
        # Prefer most ammo then alpha among equal totals (legacy max key).
        proposals.append(
            BidProposal(
                bidder_id=bidder_id,
                weapon_type=weapon_type,
                payload_offered=payload_offered,
                plan=plan,
                bidder_state=BidderStateSnapshot(
                    agent_id=bidder_id,
                    position=position,
                    altitude=altitude,
                    heading_deg=heading_deg,
                    velocity=velocity,
                    hp=hp,
                    hp_max=hp_max,
                    flares_remaining=flares_remaining,
                    flare_capacity=flare_capacity,
                    flare_cooldown_remaining=flare_cooldown_remaining,
                ),
                risk=BidRiskBreakdownModel(
                    ir_risk=risk.ir_risk,
                    radar_risk=risk.radar_risk,
                    gun_risk=risk.gun_risk,
                    combined=risk.combined,
                ),
                utility=BidUtilityBreakdownModel(
                    capability=util.capability,
                    reach=util.reach,
                    survival=util.survival,
                    recovery=util.recovery,
                    total=float(util.total),
                ),
                normalized_context_hash=context_hash,
                threat_contacts_used=stable_contacts,
                delivery_probability=delivery_probability,
                expected_damage_milli=expected_damage_milli,
            )
        )

    # Legacy preferred weapon with most ammo; among proposals with same total
    # keep ammo-desc then weapon alpha via secondary sort keys on selection.
    proposals.sort(
        key=lambda p: (
            -p.utility.total,
            -p.payload_offered,
            p.weapon_type,
            p.bidder_id,
        )
    )
    return proposals


@dataclass(frozen=True)
class AwardPlan:
    accepted: list[BidProposal]
    reject_reasons: dict[str, BidRejectReason]
    low_confidence_coverage: bool = False
    overkill_milli: int = 0
    award_score: float = 0.0
    covered_milli: int = 0
    raw_need_milli: int = 0
    reserve_need_milli: int = 0


def proposal_damage_milli(proposal: BidProposal) -> int:
    """Integer milli-damage used by award DP (legacy proposals may omit the field)."""
    if int(proposal.expected_damage_milli) > 0:
        return int(proposal.expected_damage_milli)
    weapon = config.WEAPON_CATALOG.get(proposal.weapon_type)  # type: ignore[arg-type]
    damage = int(weapon.damage_per_hit) if weapon is not None else 1
    p_hit = (
        clamp01(float(proposal.delivery_probability))
        if proposal.delivery_probability is not None
        else 1.0
    )
    return int(round(int(proposal.payload_offered) * damage * p_hit * 1000.0))


def _proposal_sort_key(p: BidProposal) -> tuple:
    return (
        -float(p.utility.total),
        float(p.risk.combined),
        -proposal_damage_milli(p),
        str(p.bidder_id),
        str(p.weapon_type),
    )


def _validate_proposals(
    proposals: Sequence[BidProposal],
    context: AwardContext,
) -> tuple[list[BidProposal], dict[str, BidRejectReason]]:
    """Reject malformed / incompatible proposals with stable per-bidder reasons."""
    reject: dict[str, BidRejectReason] = {}
    valid: list[BidProposal] = []
    seen_plan: set[tuple[str, str]] = set()

    ordered = sorted(proposals, key=_proposal_sort_key)
    for p in ordered:
        bidder = str(p.bidder_id or "")
        weapon = str(p.weapon_type or "")
        key = f"{bidder}:{weapon}"
        if not bidder or not weapon:
            reject[key or "malformed"] = "MALFORMED"
            continue
        if int(p.payload_offered) < 1:
            reject[bidder] = "MALFORMED"
            continue
        if proposal_damage_milli(p) <= 0:
            reject[bidder] = "IMPOSSIBLE_ROUNDS"
            continue
        if context.context_hash and p.normalized_context_hash:
            if p.normalized_context_hash != context.context_hash:
                reject[bidder] = "CONTEXT_HASH_MISMATCH"
                continue
        plan_key = (bidder, weapon)
        if plan_key in seen_plan:
            reject[f"{bidder}:{weapon}"] = "DUPLICATE_PLAN"
            continue
        seen_plan.add(plan_key)
        valid.append(p)
    return valid, reject


def _is_inclusion_minimal(subset: Sequence[BidProposal], raw_need: int) -> bool:
    total = sum(proposal_damage_milli(p) for p in subset)
    if total < raw_need:
        return False
    if not subset:
        return raw_need <= 0
    for i in range(len(subset)):
        without = sum(
            proposal_damage_milli(p) for j, p in enumerate(subset) if j != i
        )
        if without >= raw_need:
            return False
    return True


def _award_score(
    subset: Sequence[BidProposal],
    *,
    raw_need: int,
    overkill_weight: float,
    team_size_weight: float,
    n_bidders: int,
) -> tuple[float, float, float, int, tuple[tuple[str, str], ...]]:
    """Return sortable components for award ranking."""
    if not subset:
        return (float("-inf"), 0.0, 0.0, 0, ())
    mean_util = sum(float(p.utility.total) for p in subset) / len(subset)
    covered = sum(proposal_damage_milli(p) for p in subset)
    overkill = max(0, covered - raw_need)
    norm_overkill = overkill / max(1.0, float(raw_need)) if raw_need > 0 else 0.0
    norm_team = len(subset) / max(1.0, float(n_bidders))
    score = mean_util - overkill_weight * norm_overkill - team_size_weight * norm_team
    mean_risk = sum(float(p.risk.combined) for p in subset) / len(subset)
    mean_recovery_risk = sum(1.0 - float(p.utility.recovery) for p in subset) / len(
        subset
    )
    weapon_diversity = len({p.weapon_type for p in subset})
    id_tie = tuple(sorted((p.bidder_id, p.weapon_type) for p in subset))
    return (score, -mean_risk, -mean_recovery_risk, weapon_diversity, id_tie)


def select_winning_bids(
    proposals: Sequence[BidProposal],
    context: AwardContext,
) -> AwardPlan:
    """Deterministic milli-damage subset award (Part G).

    Enumerates inclusion-minimal covering teams (≤1 proposal per bidder),
    prefers reserve coverage when available, else marks LOW_CONFIDENCE_COVERAGE.
    """
    valid, reject = _validate_proposals(proposals, context)
    raw_need = max(0, int(context.unreserved_hp)) * 1000
    reserve_need = int(
        math.ceil(raw_need * float(context.coverage_reserve_factor))
    )
    if raw_need <= 0:
        for p in valid:
            reject.setdefault(p.bidder_id, "MANAGER_REJECTED")
        return AwardPlan(
            accepted=[],
            reject_reasons=reject,
            low_confidence_coverage=False,
            raw_need_milli=0,
            reserve_need_milli=reserve_need,
        )

    by_bidder: dict[str, list[BidProposal]] = {}
    for p in sorted(valid, key=_proposal_sort_key):
        by_bidder.setdefault(p.bidder_id, []).append(p)

    bidder_ids = sorted(by_bidder.keys())[:MAX_AWARD_BIDDERS]
    for dropped in sorted(by_bidder.keys())[MAX_AWARD_BIDDERS:]:
        reject.setdefault(dropped, "MANAGER_REJECTED")

    options: list[list[BidProposal | None]] = []
    for bid in bidder_ids:
        props = by_bidder[bid][:MAX_PROPOSALS_PER_BIDDER]
        options.append([None, *props])

    raw_coverers: list[list[BidProposal]] = []
    reserve_coverers: list[list[BidProposal]] = []

    if options:
        for combo in product(*options):
            subset = [p for p in combo if p is not None]
            if not subset:
                continue
            # Enforce at most one proposal per bidder (product already does).
            covered = sum(proposal_damage_milli(p) for p in subset)
            if covered < raw_need:
                continue
            if not _is_inclusion_minimal(subset, raw_need):
                continue
            raw_coverers.append(subset)
            if covered >= reserve_need:
                reserve_coverers.append(subset)

    candidates = reserve_coverers if reserve_coverers else raw_coverers
    low_confidence = bool(raw_coverers) and not bool(reserve_coverers)

    if not candidates:
        # No team can cover raw_need in one award. Accept the best partial
        # subset so multi-strike / multi-round CNP can chip HP down — empty
        # awards leave contractors idle until bingo-fuel abort (tanks/ships).
        partials: list[list[BidProposal]] = []
        if options:
            for combo in product(*options):
                subset = [p for p in combo if p is not None]
                if subset:
                    partials.append(subset)
        if not partials:
            for bid in bidder_ids:
                reject.setdefault(bid, "MANAGER_REJECTED")
            return AwardPlan(
                accepted=[],
                reject_reasons=reject,
                low_confidence_coverage=True if valid else False,
                raw_need_milli=raw_need,
                reserve_need_milli=reserve_need,
            )

        def _partial_cover(subset: list[BidProposal]) -> int:
            return sum(proposal_damage_milli(p) for p in subset)

        best_cover = max(_partial_cover(s) for s in partials)
        partials = [s for s in partials if _partial_cover(s) == best_cover]

        def _partial_rank(subset: list[BidProposal]) -> tuple:
            score, neg_risk, neg_rec, diversity, id_tie = _award_score(
                subset,
                raw_need=raw_need,
                overkill_weight=float(context.overkill_weight),
                team_size_weight=float(context.team_size_weight),
                n_bidders=max(1, len(bidder_ids)),
            )
            return (-score, -neg_risk, -neg_rec, -diversity, id_tie)

        best = min(partials, key=_partial_rank)
        best_rank = _award_score(
            best,
            raw_need=raw_need,
            overkill_weight=float(context.overkill_weight),
            team_size_weight=float(context.team_size_weight),
            n_bidders=max(1, len(bidder_ids)),
        )
        covered_milli = sum(proposal_damage_milli(p) for p in best)
        accepted = sorted(best, key=lambda p: (p.bidder_id, p.weapon_type))
        accepted_ids = {p.bidder_id for p in accepted}
        for bid in bidder_ids:
            if bid not in accepted_ids:
                reject.setdefault(bid, "MANAGER_REJECTED")
        return AwardPlan(
            accepted=accepted,
            reject_reasons=reject,
            low_confidence_coverage=True,
            overkill_milli=0,
            award_score=float(best_rank[0]),
            covered_milli=covered_milli,
            raw_need_milli=raw_need,
            reserve_need_milli=reserve_need,
        )

    def _rank(subset: list[BidProposal]) -> tuple:
        score, neg_risk, neg_rec, diversity, id_tie = _award_score(
            subset,
            raw_need=raw_need,
            overkill_weight=float(context.overkill_weight),
            team_size_weight=float(context.team_size_weight),
            n_bidders=max(1, len(bidder_ids)),
        )
        # Sort ascending: negate score/diversity so larger wins; keep id_tie ascending.
        return (-score, -neg_risk, -neg_rec, -diversity, id_tie)

    best = min(candidates, key=_rank)
    best_rank = _award_score(
        best,
        raw_need=raw_need,
        overkill_weight=float(context.overkill_weight),
        team_size_weight=float(context.team_size_weight),
        n_bidders=max(1, len(bidder_ids)),
    )
    covered_milli = sum(proposal_damage_milli(p) for p in best)
    overkill_milli = max(0, covered_milli - raw_need)
    accepted = sorted(best, key=lambda p: (p.bidder_id, p.weapon_type))
    accepted_ids = {p.bidder_id for p in accepted}
    for bid in bidder_ids:
        if bid not in accepted_ids:
            reject.setdefault(bid, "MANAGER_REJECTED")

    return AwardPlan(
        accepted=accepted,
        reject_reasons=reject,
        low_confidence_coverage=low_confidence,
        overkill_milli=overkill_milli,
        award_score=float(best_rank[0]),
        covered_milli=covered_milli,
        raw_need_milli=raw_need,
        reserve_need_milli=reserve_need,
    )
