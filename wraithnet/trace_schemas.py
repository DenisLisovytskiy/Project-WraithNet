"""Pydantic schemas for the simulation trace file consumed by the
replay viewer (viewer/ package, Phase 12+). This module must import only
pydantic and stdlib — never a GUI library, never anything from viewer/.

See docs/07_Visualization_Design_Document.md §3 for the full rationale.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field

from wraithnet.config import DEFAULT_HOME_BASE, JetFrameType, TargetType, WeaponType


class TraceHeader(BaseModel):
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


class TargetFrame(BaseModel):
    target_id: str
    hp_remaining: int
    claimed: bool


class StrikeEvent(BaseModel):
    agent_id: str
    target_id: str
    weapon_type: WeaponType


class JetOutcomeEvent(BaseModel):
    """Emitted on the tick a jet leaves play — not a ghost frame, not cumulative."""

    agent_id: str
    tick: int
    outcome: Literal["destroyed", "returned_to_base"]


class TraceTick(BaseModel):
    tick: int
    jets: list[JetFrame]
    targets: list[TargetFrame]
    strikes: list[StrikeEvent]
    running: bool
    swarm_wiped_out: bool
    mission_aborted: bool = False
    jet_outcomes: list[JetOutcomeEvent] = Field(default_factory=list)
