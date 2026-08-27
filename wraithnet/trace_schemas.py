"""Pydantic schemas for the simulation trace file consumed by the
replay viewer (viewer/ package, Phase 12+). This module must import only
pydantic and stdlib — never a GUI library, never anything from viewer/.

See docs/07_Visualization_Design_Document.md §3 for the full rationale.
"""

from typing import Optional

from pydantic import BaseModel

from wraithnet.config import TargetType, WeaponType


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


class JetFrame(BaseModel):
    agent_id: str
    position: tuple[float, float]
    hp: int
    payload: dict[WeaponType, int]
    destroyed: bool
    jammed: bool
    role: Optional[str]


class TargetFrame(BaseModel):
    target_id: str
    hp_remaining: int
    claimed: bool


class StrikeEvent(BaseModel):
    agent_id: str
    target_id: str
    weapon_type: WeaponType


class TraceTick(BaseModel):
    tick: int
    jets: list[JetFrame]
    targets: list[TargetFrame]
    strikes: list[StrikeEvent]
    running: bool
    swarm_wiped_out: bool
