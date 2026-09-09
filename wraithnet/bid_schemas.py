"""Phase 45 CNP bid/risk schema stubs (typed contracts ahead of broker wiring).

These models are additive. Legacy CFP/BID messages in ``schemas.py`` remain
authoritative until agents/broker migrate to ``RISK_UPDATE`` + ``BidProposal``.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

ThreatKind = Literal[
    "IR_MISSILE",
    "RADAR_MISSILE",
    "GUN_AAA",
    "GUN_CIWS",
    "SAM_SITE",
    "UNKNOWN",
]
BidRejectReason = Literal[
    "NO_CAPABLE_WEAPON",
    "UNREACHABLE",
    "INSUFFICIENT_FUEL",
    "CONTEXT_HASH_MISMATCH",
    "STALE_CONTACT",
    "MANAGER_REJECTED",
    "MALFORMED",
    "DUPLICATE_PLAN",
    "IMPOSSIBLE_ROUNDS",
    "WORLD_STALE",
    "INCOMPATIBLE_CONTEXT",
]


class ThreatContact(BaseModel):
    threat_id: str
    kind: ThreatKind
    position: tuple[float, float]
    altitude: float = 0.0
    heading_deg: float | None = None
    velocity: float | None = None
    engagement_arc_deg: float | None = None
    altitude_ceiling: float | None = None
    readiness: float = Field(default=1.0, ge=0.0, le=1.0)
    observed_tick: int = Field(ge=0)
    confidence: float = Field(default=1.0, ge=0.0, le=1.0)
    source: Literal["DIRECT", "DATALINK", "CFP"] = "DIRECT"
    observer_id: str | None = None


class TimedRouteSegment(BaseModel):
    start_xy: tuple[float, float]
    end_xy: tuple[float, float]
    start_altitude: float
    end_altitude: float
    ticks: int = Field(ge=0)
    turn_ticks: int = Field(default=0, ge=0)


class AttackPlanSummary(BaseModel):
    weapon_type: str
    launch_xy: tuple[float, float]
    launch_altitude: float
    estimated_ticks_to_launch: int = Field(ge=0)
    estimated_ticks_to_impact: int = Field(ge=0)
    route: list[TimedRouteSegment] = Field(default_factory=list)


class BidderStateSnapshot(BaseModel):
    agent_id: str
    position: tuple[float, float]
    altitude: float
    heading_deg: float
    velocity: float
    hp: int
    hp_max: int
    flares_remaining: int = Field(ge=0)
    flare_capacity: int = Field(ge=0)
    flare_cooldown_remaining: int = Field(default=0, ge=0)
    fuel_remaining: float = Field(default=0.0, ge=0.0)


class BaseForecastSnapshot(BaseModel):
    base_id: str
    position: tuple[float, float]
    available: bool = True
    eta_ticks: int | None = None


class BidRiskBreakdownModel(BaseModel):
    ir_risk: float = Field(ge=0.0, le=1.0)
    radar_risk: float = Field(ge=0.0, le=1.0)
    gun_risk: float = Field(ge=0.0, le=1.0)
    combined: float = Field(ge=0.0, le=1.0)


class BidUtilityBreakdownModel(BaseModel):
    capability: float = Field(ge=0.0)
    reach: float = Field(ge=0.0)
    survival: float = Field(ge=0.0, le=1.0)
    recovery: float = Field(ge=0.0, le=1.0)
    # Legacy CNP scalars are unbounded (often >> 1); risk-aware totals stay in [0,1].
    total: float


class BidUtilityWeights(BaseModel):
    capability: float = Field(default=0.35, ge=0.0, le=1.0)
    reach: float = Field(default=0.25, ge=0.0, le=1.0)
    survival: float = Field(default=0.25, ge=0.0, le=1.0)
    recovery: float = Field(default=0.15, ge=0.0, le=1.0)


class BidContext(BaseModel):
    target_id: str
    raw_hp: int = Field(ge=0)
    reserved_hp: int = Field(ge=0)
    unreserved_hp: int = Field(ge=0)
    objective_priority: float = Field(default=1.0, ge=0.0)
    threat_contacts: list[ThreatContact] = Field(default_factory=list)
    context_hash: str = ""


class BidProposal(BaseModel):
    bidder_id: str
    weapon_type: str
    payload_offered: int = Field(ge=1)
    plan: AttackPlanSummary
    bidder_state: BidderStateSnapshot
    primary_base: BaseForecastSnapshot | None = None
    alternate_base: BaseForecastSnapshot | None = None
    risk: BidRiskBreakdownModel
    utility: BidUtilityBreakdownModel
    normalized_context_hash: str
    threat_contacts_used: list[ThreatContact] = Field(default_factory=list)
    # Phase 45 Part E/G: deterministic coverage currency for award DP.
    delivery_probability: float = Field(default=1.0, ge=0.0, le=1.0)
    expected_damage_milli: int = Field(default=0, ge=0)


class AwardContext(BaseModel):
    target_id: str
    raw_hp: int = Field(default=0, ge=0)
    reserved_hp: int = Field(default=0, ge=0)
    unreserved_hp: int = Field(default=0, ge=0)
    accepted_bidder_ids: list[str] = Field(default_factory=list)
    reject_reasons: dict[str, BidRejectReason] = Field(default_factory=dict)
    context_hash: str = ""
    coverage_reserve_factor: float = Field(default=1.25, ge=1.0, le=3.0)
    overkill_weight: float = Field(default=0.20, ge=0.0)
    team_size_weight: float = Field(default=0.05, ge=0.0)
    current_tick: int = Field(default=0, ge=0)