"""In-flight guided missile state and kinematics (Phase 38+).

Missiles are world entities, not Mesa agents or CNP participants. Launch
consumes ammunition immediately; hit/miss uses keyed combat rolls from
``combat_seed``. Phase 39 consumes DECOYED / LOST_LOCK defense outcomes.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from wraithnet import config
from wraithnet.combat_rng import keyed_unit_float
from wraithnet.config import GuidanceType, SensorSignature, WeaponType
from wraithnet.taxonomy import (
    TargetClass,
    is_legal_weapon_target,
    reject_engagement,
)

MissileStatus = Literal[
    "IN_FLIGHT",
    "HIT",
    "MISS",
    "LOST_LOCK",
    "FUEL_EXPIRED",
    "INTERCEPTED",  # Phase 41+ CIWS
    "DECOYED",
]


@dataclass
class MissileState:
    missile_id: str
    shooter_id: str
    target_id: str
    weapon_type: WeaponType
    guidance: GuidanceType
    position: tuple[float, float, float]
    speed: float
    remaining_fuel_ticks: int
    support_source_id: str | None
    status: MissileStatus = "IN_FLIGHT"
    # Unit heading in XY (atan2 dy, dx style); Z chase is separate.
    heading_xy_rad: float = 0.0
    damage_applied: bool = False
    launch_tick: int = 0


@dataclass(frozen=True)
class LaunchAcknowledgement:
    """Typed world response for a launch request (Phase 38 F)."""

    accepted: bool
    missile_id: str | None
    reason: str | None = None


@dataclass(frozen=True)
class LaunchRequest:
    shooter_id: str
    target_id: str
    weapon_type: WeaponType
    shooter_position: tuple[float, float, float]
    target_position: tuple[float, float, float]
    shooter_faction: str
    target_faction: str
    target_class: TargetClass
    target_signature: SensorSignature
    target_type: str | None = None  # TargetType when ground installation
    has_los: bool = True
    support_source_id: str | None = None
    support_alive: bool = True
    firing_heading_deg: float | None = None  # jet heading; None = skip arc
    ammo_available: int = 0


def validate_launch(req: LaunchRequest) -> str | None:
    """Return a rejection reason, or None if the launch is allowed."""
    if req.ammo_available <= 0:
        return "no_ammo"
    weapon = config.WEAPON_CATALOG.get(req.weapon_type)
    if weapon is None:
        return "unknown_weapon"
    if weapon.guidance == "NONE":
        return "not_guided"

    rejection = reject_engagement(
        shooter_faction=req.shooter_faction,  # type: ignore[arg-type]
        target_faction=req.target_faction,  # type: ignore[arg-type]
        weapon=req.weapon_type,
        target_class=req.target_class,
    )
    if rejection is not None:
        return rejection

    if not is_legal_weapon_target(req.weapon_type, req.target_class):
        return "illegal_target_class"

    # Ground TargetType suitability (installations); air targets skip this table.
    if req.target_type is not None:
        if weapon.suitability.get(req.target_type, 0.0) <= 0.0:  # type: ignore[arg-type]
            return "unsuitable_target_type"

    launch_range = weapon.seeker_range if weapon.seeker_range > 0 else weapon.range
    dx = req.target_position[0] - req.shooter_position[0]
    dy = req.target_position[1] - req.shooter_position[1]
    # Match legacy strike/SAM envelope: range is horizontal (XY), not slant.
    dist_xy = math.sqrt(dx * dx + dy * dy)
    if dist_xy > launch_range:
        return "out_of_range"

    if weapon.firing_arc_deg < 360.0 and req.firing_heading_deg is not None:
        # Horizontal bearing from shooter to target; 0 = +y, clockwise (world).
        bearing = (math.degrees(math.atan2(dx, dy)) + 360.0) % 360.0
        delta = (bearing - req.firing_heading_deg + 180.0) % 360.0 - 180.0
        if abs(delta) > weapon.firing_arc_deg / 2.0:
            return "outside_firing_arc"

    if not req.has_los:
        return "no_los"

    sig = req.target_signature
    if weapon.guidance == "INFRARED":
        if sig.heat < weapon.min_heat:
            return "insufficient_heat"
    elif weapon.guidance == "RADAR":
        if weapon.requires_radar_emitter and not sig.radar_emitter:
            return "requires_radar_emitter"
        if sig.radar_cross_section < weapon.min_rcs:
            return "insufficient_rcs"
        if weapon.requires_support and not req.support_alive:
            return "no_support"

    return None


def create_missile(
    *,
    missile_id: str,
    req: LaunchRequest,
    launch_tick: int,
) -> MissileState:
    weapon = config.WEAPON_CATALOG[req.weapon_type]
    dx = req.target_position[0] - req.shooter_position[0]
    dy = req.target_position[1] - req.shooter_position[1]
    heading = math.atan2(dy, dx) if (dx or dy) else 0.0
    support = req.support_source_id
    if weapon.requires_support and support is None:
        support = req.shooter_id
    return MissileState(
        missile_id=missile_id,
        shooter_id=req.shooter_id,
        target_id=req.target_id,
        weapon_type=req.weapon_type,
        guidance=weapon.guidance,
        position=req.shooter_position,
        speed=weapon.speed_per_tick,
        remaining_fuel_ticks=weapon.fuel_ticks,
        support_source_id=support,
        status="IN_FLIGHT",
        heading_xy_rad=heading,
        launch_tick=launch_tick,
    )


def closest_approach_distance(
    old_pos: tuple[float, float, float],
    new_pos: tuple[float, float, float],
    target_pos: tuple[float, float, float],
) -> float:
    """Minimum distance from ``target_pos`` to the segment old→new (no tunneling)."""
    ox, oy, oz = old_pos
    nx, ny, nz = new_pos
    tx, ty, tz = target_pos
    vx, vy, vz = nx - ox, ny - oy, nz - oz
    wx, wy, wz = tx - ox, ty - oy, tz - oz
    vv = vx * vx + vy * vy + vz * vz
    if vv <= 0.0:
        return math.sqrt(wx * wx + wy * wy + wz * wz)
    t = max(0.0, min(1.0, (wx * vx + wy * vy + wz * vz) / vv))
    cx, cy, cz = ox + t * vx, oy + t * vy, oz + t * vz
    dx, dy, dz = tx - cx, ty - cy, tz - cz
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def advance_missile(
    missile: MissileState,
    target_pos: tuple[float, float, float],
    *,
    support_alive: bool = True,
) -> tuple[float, float, float]:
    """Pursue target for one tick. Returns previous position. Mutates missile.

    Marks LOST_LOCK / FUEL_EXPIRED when applicable. Does not resolve hit rolls.
    """
    if missile.status != "IN_FLIGHT":
        return missile.position

    weapon = config.WEAPON_CATALOG[missile.weapon_type]
    old = missile.position
    dx = target_pos[0] - old[0]
    dy = target_pos[1] - old[1]
    dz = target_pos[2] - old[2]
    dist = math.sqrt(dx * dx + dy * dy + dz * dz)

    # Semi-active radar: lose lock if support dies before terminal.
    if (
        weapon.guidance == "RADAR"
        and weapon.requires_support
        and not support_alive
        and dist > weapon.terminal_range
    ):
        missile.status = "LOST_LOCK"
        return old

    if dist > 1e-12:
        missile.heading_xy_rad = math.atan2(dy, dx)
        step = min(missile.speed, dist)
        ux, uy, uz = dx / dist, dy / dist, dz / dist
        missile.position = (
            old[0] + ux * step,
            old[1] + uy * step,
            old[2] + uz * step,
        )
    missile.remaining_fuel_ticks -= 1
    return old


def apply_fuel_expiry(missile: MissileState) -> None:
    """Mark FUEL_EXPIRED after the tick's contact check if fuel is spent."""
    if missile.status == "IN_FLIGHT" and missile.remaining_fuel_ticks <= 0:
        missile.status = "FUEL_EXPIRED"


def terminal_contact(
    missile: MissileState,
    old_pos: tuple[float, float, float],
    target_pos: tuple[float, float, float],
) -> bool:
    """True if the missile swept within terminal_range of the target this tick."""
    if missile.status != "IN_FLIGHT":
        return False
    weapon = config.WEAPON_CATALOG[missile.weapon_type]
    thresh = weapon.terminal_range if weapon.terminal_range > 0 else 0.5
    return closest_approach_distance(old_pos, missile.position, target_pos) <= thresh


def resolve_impact(
    missile: MissileState,
    combat_seed: int,
    *,
    hit_probability: float | None = None,
) -> MissileStatus:
    """Keyed hit roll at terminal contact. Mutates ``missile.status``."""
    if missile.status != "IN_FLIGHT":
        return missile.status
    weapon = config.WEAPON_CATALOG[missile.weapon_type]
    p = (
        weapon.base_hit_probability
        if hit_probability is None
        else hit_probability
    )
    p = max(0.0, min(1.0, p))
    roll = keyed_unit_float(
        combat_seed,
        "impact",
        missile.missile_id,
        missile.shooter_id,
        missile.target_id,
        missile.weapon_type,
        missile.launch_tick,
    )
    missile.status = "HIT" if roll < p else "MISS"
    return missile.status


def is_terminal_status(status: MissileStatus) -> bool:
    return status != "IN_FLIGHT"


def is_active_terminal_guidance(
    missile: MissileState,
    target_pos: tuple[float, float, float],
) -> bool:
    """Radar seeker in active terminal (Phase 39 notching geometry switch)."""
    from wraithnet.countermeasures import is_active_terminal

    weapon = config.WEAPON_CATALOG[missile.weapon_type]
    return is_active_terminal(
        guidance=weapon.guidance,
        requires_support=weapon.requires_support,
        terminal_range=weapon.terminal_range,
        missile_pos=missile.position,
        target_pos=target_pos,
    )


def support_emitter_xy(
    missile: MissileState,
    *,
    jet_positions: dict[str, tuple[float, float]],
    sam_positions: dict[str, tuple[float, float]],
) -> tuple[float, float] | None:
    """World XY of the missile's support emitter, if known."""
    sid = missile.support_source_id
    if sid is None:
        return None
    if sid in jet_positions:
        return jet_positions[sid]
    if sid in sam_positions:
        return sam_positions[sid]
    return None

