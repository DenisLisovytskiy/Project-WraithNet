"""Pydantic message/data schemas for WraithNet.



Implemented in Phase 1 (Schemas & World Scaffolding).

See docs/phases/Phase_1_Schemas_and_World.md.

Phase 6 adds weapon/target typing and Observation sensor/jam fields.

Phase 7: DATALINK_CONTACT message type; contract_id optional for contacts.

Phase 20: ActionIntent.desired_heading; Observation.current_heading.

Phase 22: Observation.frame_type / fuel / home_base.

Phase 39: inbound threats, flares, notch defense fields on Observation/ActionIntent.

Phase 50: Observation.current_pitch_deg / current_roll_deg;

ActionIntent.desired_pitch_deg / desired_roll_deg (optional hold-current).

"""



from typing import Literal, Optional



from pydantic import BaseModel, ConfigDict, Field, model_validator



from wraithnet import config as _config
from wraithnet.config import (

    BROADCAST_ID,

    DEFAULT_HOME_BASE,

    JET_FRAME_CATALOG,

    MAX_GRID_DISTANCE,

    GuidanceType,

    JetFrameType,

    TargetType,

    WeaponType,

)





class TargetInfo(BaseModel):

    target_id: str

    position: tuple[float, float]

    target_type: TargetType

    hp_required: int = Field(ge=1)

    hp_remaining: int = Field(ge=0)

    claimed: bool = False

    # Lowest altitude the observer's airframe can safely hold over the target
    # (terrain look-ahead); unguided weapons capped below it cannot release.
    min_overflight_altitude: float = Field(default=0.0, ge=0.0)



    @model_validator(mode="after")

    def _check_hp_remaining_not_above_required(self) -> "TargetInfo":

        if self.hp_remaining > self.hp_required:

            raise ValueError("hp_remaining cannot exceed hp_required")

        return self





class InboundThreat(BaseModel):

    """Detectable inbound missile (Phase 39). Radar threats require RWR."""



    missile_id: str

    guidance: GuidanceType

    estimated_bearing: float = Field(ge=0.0, lt=360.0)

    estimated_time_to_impact: float = Field(ge=0.0)

    support_emitter_id: Optional[str] = None





class ObservedGunThreat(BaseModel):

    """Typed ground gun-envelope contact (Phase 42 AAA; Phase 45 bid input)."""



    threat_id: str

    threat_kind: Literal["AAA_GUN"] = "AAA_GUN"

    position: tuple[float, float]

    engagement_range: float = Field(gt=0.0)

    altitude_ceiling: Optional[float] = None

    requires_los: bool = True

    confidence: float = Field(default=1.0, ge=0.0, le=1.0)

    faction: Literal["FRIENDLY", "ENEMY"] = "ENEMY"





class AltitudeRules(BaseModel):
    """Per-scenario altitude safety and tactics knobs (defaults in config)."""

    model_config = ConfigDict(extra="forbid")

    # Minimum height above the roof/treetop/ground under a jet.
    terrain_clearance: float = Field(default=_config.TERRAIN_CLEARANCE, gt=0.0)
    # Surface radar missiles cannot engage a jet below this relative altitude.
    sam_radar_min_engagement_agl: float = Field(
        default=_config.SAM_RADAR_MIN_ENGAGEMENT_AGL, ge=0.0
    )
    # Transit this far above the highest known gun ceiling.
    gun_ceiling_margin: float = Field(default=_config.GUN_CEILING_MARGIN, ge=0.0)


class Observation(BaseModel):

    agent_id: str

    tick: int = Field(ge=0)

    current_position: tuple[float, float]

    sensor_radius: float = Field(gt=0)

    payload: dict[WeaponType, int]

    known_targets: list[TargetInfo]

    comms_jammed: bool = False

    current_heading: float = Field(default=0.0, ge=0.0, lt=360.0)

    # Phase 50: wings-level defaults; see JetAgent attitude conventions.

    current_pitch_deg: float = Field(default=0.0)

    current_roll_deg: float = Field(default=0.0)

    frame_type: JetFrameType = "STRIKE_FIGHTER"

    fuel: float = Field(

        default=JET_FRAME_CATALOG["STRIKE_FIGHTER"].fuel_capacity, ge=0.0

    )

    home_base: tuple[float, float] = DEFAULT_HOME_BASE

    # Phase 37: authoritative combat altitude (world units).

    current_altitude: float = Field(

        default=JET_FRAME_CATALOG["STRIKE_FIGHTER"].cruise_altitude, ge=0.0

    )

    # Phase 39: countermeasure / RWR state (Phase 45 bid inputs).

    inbound_threats: list[InboundThreat] = Field(default_factory=list)

    flares_remaining: int = Field(default=0, ge=0)

    flare_capacity: int = Field(default=0, ge=0)

    flare_cooldown_remaining: int = Field(default=0, ge=0)

    radar_warning_receiver: bool = True

    notch_angle_window_deg: float = Field(default=30.0, ge=0.0)

    notch_min_speed: float = Field(default=2.0, ge=0.0)

    notch_hold_ticks: int = Field(default=2, ge=1)

    current_speed: float = Field(default=0.0, ge=0.0)

    defensive_state: Literal["NONE", "NOTCH", "FLARE"] = "NONE"

    defensive_threat_id: Optional[str] = None

    # Phase 40: dynamic base / carrier recovery (Phase 45 bid inputs).

    assigned_base_id: Optional[str] = None

    recovery_phase: Optional[Literal["RTB", "APPROACH", "RECOVERED"]] = None

    recovery_base_hp: Optional[int] = None

    recovery_base_available: Optional[bool] = None

    alternate_base_available: Optional[bool] = None

    recovery_route_forecast: list[tuple[float, float]] = Field(default_factory=list)

    # Phase 42: observed AAA gun envelopes (typed threat contacts).

    observed_gun_threats: list[ObservedGunThreat] = Field(default_factory=list)

    # Mission-brief waypoints for incomplete required DESTROY subjects.
    # Not sensor contacts — jets may MOVE toward these under fog of war until
    # a live TargetInfo appears in known_targets / world_view.
    briefed_seek_positions: list[tuple[float, float]] = Field(default_factory=list)

    # Battlespace diagonal for CNP distance normalization (scenario-aware).
    map_diagonal: float = Field(default=MAX_GRID_DISTANCE, gt=0.0)
    altitude_rules: AltitudeRules = Field(default_factory=AltitudeRules)



    @property

    def total_payload_remaining(self) -> int:

        return sum(self.payload.values())





class ActionIntent(BaseModel):

    agent_id: str

    tick: int = Field(ge=0)

    # Phase 38: LAUNCH for guided weapons; STRIKE remains for direct-fire

    # (JDAM/GUN_POD). STRIKE with a guided weapon is still accepted by the

    # world as a compatibility path that goes through LaunchAcknowledgement.

    action_type: Literal["MOVE", "STRIKE", "LAUNCH", "IDLE"]

    target_position: Optional[tuple[float, float]] = None

    target_id: Optional[str] = None

    weapon_type: Optional[WeaponType] = None

    desired_heading: Optional[float] = Field(default=None, ge=0.0, lt=360.0)

    # Phase 37: optional altitude intent; climb/descent clamped per frame.

    desired_altitude: Optional[float] = Field(default=None, ge=0.0)

    # Phase 50: optional attitude intents; None = hold current (Phase 51 applies).

    desired_pitch_deg: Optional[float] = None

    desired_roll_deg: Optional[float] = None

    # Fraction of max_speed to fly at (holding orbit slows down); NOTCH forces 1.
    speed_fraction: float = Field(default=1.0, gt=0.0, le=1.0)

    # Phase 39 — MobilityIntent / FastDefenseIntent fields (alongside primary).

    maneuver_defense: Literal["NONE", "NOTCH"] = "NONE"

    countermeasure_action: Literal["NONE", "FLARE"] = "NONE"

    defensive_threat_id: Optional[str] = None



    @model_validator(mode="after")

    def _check_required_fields_for_action(self) -> "ActionIntent":

        if self.action_type == "MOVE" and self.target_position is None:

            raise ValueError("MOVE requires target_position")

        if self.action_type in ("STRIKE", "LAUNCH"):

            if self.target_id is None:

                raise ValueError(f"{self.action_type} requires target_id")

            if self.weapon_type is None:

                raise ValueError(f"{self.action_type} requires weapon_type")

        if self.maneuver_defense == "NOTCH" and self.defensive_threat_id is None:

            raise ValueError("NOTCH requires defensive_threat_id")

        if self.countermeasure_action == "FLARE" and self.defensive_threat_id is None:

            raise ValueError("FLARE requires defensive_threat_id")

        return self





class Message(BaseModel):

    message_id: str

    contract_id: Optional[str] = None  # None only for DATALINK_CONTACT

    tick: int = Field(ge=0)

    sender_id: str

    receiver_id: str  # BROADCAST_ID sentinel, or a specific agent_id

    message_type: Literal[
        "CFP", "BID", "ACCEPT", "REJECT", "DATALINK_CONTACT", "RISK_UPDATE"
    ]

    target_id: str

    hp_remaining: Optional[int] = None

    payload_offered: Optional[int] = None

    weapon_type: Optional[WeaponType] = None

    bid_utility: Optional[float] = None

    target_info: Optional[TargetInfo] = None  # required on DATALINK_CONTACT

    # Phase 45: risk-aware CNP fields (optional for legacy BIDs).
    raw_hp: Optional[int] = None
    reserved_hp: Optional[int] = None
    objective_priority: Optional[float] = None
    context_hash: Optional[str] = None
    threat_contacts: list[dict] = Field(default_factory=list)
    proposals: list[dict] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_required_fields_for_message_type(self) -> "Message":
        if self.message_type == "DATALINK_CONTACT":
            if self.target_info is None:
                raise ValueError("DATALINK_CONTACT requires target_info")
            if self.contract_id is not None:
                raise ValueError("DATALINK_CONTACT must not carry a contract_id")
            if self.receiver_id != BROADCAST_ID:
                raise ValueError("DATALINK_CONTACT must be broadcast")
            return self

        if self.message_type == "RISK_UPDATE":
            if self.contract_id is None:
                raise ValueError("RISK_UPDATE requires contract_id")
            if self.receiver_id != BROADCAST_ID:
                raise ValueError("RISK_UPDATE must be broadcast")
            if self.context_hash is None:
                raise ValueError("RISK_UPDATE requires context_hash")
            return self

        if self.contract_id is None:
            raise ValueError(f"{self.message_type} requires contract_id")

        if self.message_type == "CFP":
            if self.hp_remaining is None:
                raise ValueError("CFP requires hp_remaining")
            if self.receiver_id != BROADCAST_ID:
                raise ValueError("CFP must be broadcast")

        if self.message_type == "BID":
            if self.payload_offered is None or self.bid_utility is None or self.weapon_type is None:
                raise ValueError("BID requires payload_offered, bid_utility, and weapon_type")

        if self.message_type in ("BID", "ACCEPT", "REJECT"):
            if self.receiver_id == BROADCAST_ID:
                raise ValueError(f"{self.message_type} must not be broadcast")

        return self


