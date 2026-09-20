"""Pydantic schemas for the simulation trace file consumed by the
replay viewer (viewer/ package, Phase 12+). This module must import only
pydantic and stdlib — never a GUI library, never anything from viewer/.

See docs/07_Visualization_Design_Document.md §3 for the full rationale.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field

from wraithnet.config import DEFAULT_HOME_BASE, JetFrameType, TargetType, WeaponType
from wraithnet.objectives import MissionObjective
from wraithnet.terrain import TerrainGrid


class TraceHeader(BaseModel):
    # Phase 36: bump when terrain is written; legacy traces omit terrain.
    # Phase 37: optional objectives / fixed bases; altitude lives on JetFrame.
    # Phase 38: missile frames + launch/impact events (v3).
    # Phase 54: JetFrame.pitch_deg / roll_deg are additive with defaults — keep
    # trace_version=3 so legacy JSONL loads without a wire break.
    trace_version: int = 3
    grid_width: float
    grid_height: float
    sensor_radius: float
    jam_radius: float
    sam_engagement_range: float
    strike_range: float
    jet_ids: list[str]
    initial_payload: dict[str, dict[WeaponType, int]]
    target_ids: list[str]
    target_types: dict[str, TargetType]
    target_hp_required: dict[str, int]
    sam_site_ids: list[str]
    aaa_site_ids: list[str] = Field(default_factory=list)
    jammer_positions: dict[str, tuple[float, float]]
    # Additive Phase 12 field: targets never move, so positions live in the
    # header (Phase 11's schema omitted this; the viewer cannot draw targets
    # without it). See docs/phases/Phase_12_Pygame_Viewer_Static_Rendering.md
    # Part B.
    target_positions: dict[str, tuple[float, float]]
    # Phase 24: airframe / weapon overlay data so viewers never have to
    # import wraithnet.config (viewer3d isolation) or guess capacities.
    home_base: tuple[float, float] = DEFAULT_HOME_BASE
    jet_frame_types: dict[str, JetFrameType] = Field(default_factory=dict)
    weapon_ranges: dict[WeaponType, float] = Field(default_factory=dict)
    weapon_firing_arcs_deg: dict[WeaponType, float] = Field(default_factory=dict)
    jet_frame_stats: dict[str, dict[str, float]] = Field(default_factory=dict)
    # Phase 36: authoritative terrain. None/omitted → viewer synthesizes plains.
    terrain: TerrainGrid | None = None
    # Phase 37 (optional): mission objectives + fixed airbase registry.
    mission_objectives: list[MissionObjective] = Field(default_factory=list)
    fixed_airbases: dict[str, tuple[float, float]] = Field(default_factory=dict)
    combat_seed: int = 0


class JetFrame(BaseModel):
    agent_id: str
    position: tuple[float, float]
    hp: int
    payload: dict[WeaponType, int]
    destroyed: bool
    jammed: bool
    role: Optional[str]
    heading: float = 0.0
    fuel: float = 0.0
    # Phase 37: authoritative altitude when present; None → legacy viewer cosmetics.
    altitude: Optional[float] = None
    # Phase 54: authoritative attitude (sim conventions). Defaults keep legacy
    # traces loadable as wings-level / pitch-0 without a version bump.
    pitch_deg: float = 0.0
    roll_deg: float = 0.0
    # Phase 39: countermeasure / defensive HUD state.
    flares_remaining: Optional[int] = None
    flare_capacity: Optional[int] = None
    defensive_state: Optional[Literal["NONE", "NOTCH", "FLARE"]] = None
    defensive_threat_id: Optional[str] = None
    inbound_threat_count: int = 0
    notch_heading_cue: Optional[float] = None


class CountermeasureEvent(BaseModel):
    """Flare / notch audit event for viewer particles and HUD cues."""

    event: Literal["flare", "notch"]
    agent_id: str
    threat_id: Optional[str] = None
    accepted: Optional[bool] = None
    result: Optional[str] = None
    flares_remaining: Optional[int] = None
    notch_heading_cue: Optional[float] = None


class NegotiationEvent(BaseModel):
    """Phase 45 CNP negotiation audit (inspector / metrics export)."""

    event: Literal["cfp", "bid", "award", "decline", "reservation_release"]
    tick: int = Field(ge=0)
    context_hash: str = ""
    contract_id: Optional[str] = None
    target_id: Optional[str] = None
    bidder_id: Optional[str] = None
    reason: Optional[str] = None
    detail: dict = Field(default_factory=dict)


class TargetFrame(BaseModel):
    target_id: str
    hp_remaining: int
    claimed: bool


class StrikeEvent(BaseModel):
    agent_id: str
    target_id: str
    weapon_type: WeaponType


class WeaponLaunchEvent(BaseModel):
    """Authoritative guided-weapon launch (Phase 38)."""

    missile_id: str
    shooter_id: str
    target_id: str
    weapon_type: WeaponType
    guidance: Literal["NONE", "INFRARED", "RADAR"] = "NONE"


class WeaponImpactEvent(BaseModel):
    """Terminal missile outcome (Phase 38)."""

    missile_id: str
    shooter_id: str
    target_id: str
    weapon_type: WeaponType
    outcome: Literal[
        "HIT",
        "MISS",
        "LOST_LOCK",
        "FUEL_EXPIRED",
        "INTERCEPTED",
        "DECOYED",
    ]


class MissileFrame(BaseModel):
    """Per-tick in-flight missile state for authoritative viewer tracks."""

    missile_id: str
    shooter_id: str
    target_id: str
    weapon_type: WeaponType
    guidance: Literal["NONE", "INFRARED", "RADAR"] = "NONE"
    position: tuple[float, float, float]
    status: Literal[
        "IN_FLIGHT",
        "HIT",
        "MISS",
        "LOST_LOCK",
        "FUEL_EXPIRED",
        "INTERCEPTED",
        "DECOYED",
    ] = "IN_FLIGHT"
    remaining_fuel_ticks: int = 0


class JetOutcomeEvent(BaseModel):
    """Emitted on the tick a jet leaves play — not a ghost frame, not cumulative."""

    agent_id: str
    tick: int
    outcome: Literal["destroyed", "returned_to_base"]


class CarrierFrame(BaseModel):
    """Per-tick carrier state for viewer glyphs and recovery rings (Phase 40)."""

    unit_id: str
    position: tuple[float, float]
    heading: float = 0.0
    hp: int = 0
    hp_max: int = 0
    destroyed: bool = False
    hold_position: bool = True
    route: list[tuple[float, float]] = Field(default_factory=list)
    frame_type: str = "ESCORT_CARRIER"


class CorvetteFrame(BaseModel):
    """Per-tick corvette state for viewer glyphs and threat rings (Phase 41)."""

    unit_id: str
    position: tuple[float, float]
    heading: float = 0.0
    hp: int = 0
    hp_max: int = 0
    destroyed: bool = False
    hold_position: bool = True
    route: list[tuple[float, float]] = Field(default_factory=list)
    frame_type: str = "CORVETTE_STANDARD"
    loadout: dict[str, int] = Field(default_factory=dict)
    sam_range: float = 15.0
    ciws_range: float = 4.0


class AaaSiteFrame(BaseModel):
    """Per-tick AAA state for viewer glyphs and gun rings (Phase 42)."""

    target_id: str
    position: tuple[float, float]
    heading: float = 0.0
    hp: int = 0
    hp_max: int = 0
    destroyed: bool = False
    faction: str = "ENEMY"
    frame_type: str = "AAA_LIGHT"
    ammo: int = 0
    gun_range: float = 5.0
    altitude_ceiling: float | None = 18.0


class TankFrame(BaseModel):
    """Per-tick tank state for viewer glyphs (Phase 43)."""

    unit_id: str
    position: tuple[float, float]
    heading: float = 0.0
    hp: int = 0
    hp_max: int = 0
    destroyed: bool = False
    faction: str = "ENEMY"
    frame_type: str = "MBT_STANDARD"
    order: str = "HOLD"
    ammo: int = 0
    current_target_id: str | None = None
    gun_range: float = 8.0


class TraceTick(BaseModel):
    tick: int
    jets: list[JetFrame]
    targets: list[TargetFrame]
    strikes: list[StrikeEvent]
    running: bool
    swarm_wiped_out: bool
    mission_aborted: bool = False
    jet_outcomes: list[JetOutcomeEvent] = Field(default_factory=list)
    # Phase 38 (optional on legacy traces — default empty).
    weapon_launches: list[WeaponLaunchEvent] = Field(default_factory=list)
    weapon_impacts: list[WeaponImpactEvent] = Field(default_factory=list)
    missiles: list[MissileFrame] = Field(default_factory=list)
    # Phase 39 (optional on legacy traces — default empty).
    countermeasures: list[CountermeasureEvent] = Field(default_factory=list)
    # Phase 40 (optional on legacy traces — default empty).
    carriers: list[CarrierFrame] = Field(default_factory=list)
    # Phase 41 (optional on legacy traces — default empty).
    corvettes: list[CorvetteFrame] = Field(default_factory=list)
    # Phase 42 (optional on legacy traces — default empty).
    aaa_sites: list[AaaSiteFrame] = Field(default_factory=list)
    # Phase 43 (optional on legacy traces — default empty).
    tanks: list[TankFrame] = Field(default_factory=list)
    # Phase 45 (optional on legacy traces — default empty).
    negotiation: list[NegotiationEvent] = Field(default_factory=list)
