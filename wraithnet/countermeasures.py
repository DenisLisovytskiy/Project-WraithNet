"""Aircraft flares and notching (Phase 39).

Pure helpers for decoy / break-lock rolls and notch geometry. World applies
these during the countermeasures stage before combat resolution.
"""

from __future__ import annotations

import math
from typing import Literal

from wraithnet.combat_rng import keyed_unit_float
from wraithnet.geometry import angular_distance, bearing_to

# World-unit radius: flares only affect IR missiles inside this bubble.
FLARE_EFFECT_RANGE: float = 12.0

# Active-terminal radar seekers remain notch-susceptible but at reduced odds.
ACTIVE_TERMINAL_NOTCH_FACTOR: float = 0.4

DefensiveManeuver = Literal["NONE", "NOTCH"]
CountermeasureAction = Literal["NONE", "FLARE"]


def clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def missile_to_target_range(
    missile_pos: tuple[float, float, float],
    target_pos: tuple[float, float, float],
) -> float:
    dx = target_pos[0] - missile_pos[0]
    dy = target_pos[1] - missile_pos[1]
    dz = target_pos[2] - missile_pos[2]
    return math.sqrt(dx * dx + dy * dy + dz * dz)


def is_active_terminal(
    *,
    guidance: str,
    requires_support: bool,
    terminal_range: float,
    missile_pos: tuple[float, float, float],
    target_pos: tuple[float, float, float],
) -> bool:
    """True when a radar seeker is in (or always-on) active terminal mode."""
    if guidance != "RADAR":
        return False
    if not requires_support:
        return True
    thresh = terminal_range if terminal_range > 0 else 0.5
    return missile_to_target_range(missile_pos, target_pos) <= thresh


def notch_reference_bearing_deg(
    *,
    jet_pos: tuple[float, float],
    jet_velocity_xy: tuple[float, float],
    support_pos: tuple[float, float] | None,
    missile_pos: tuple[float, float, float],
    active_terminal: bool,
) -> float | None:
    """LOS bearing the jet must go perpendicular to.

    Before active terminal: jet → support emitter.
    After active terminal: missile → target (jet), i.e. seeker look-axis.
    """
    if active_terminal:
        return bearing_to(
            (missile_pos[0], missile_pos[1]),
            jet_pos,
        )
    if support_pos is None:
        return None
    return bearing_to(jet_pos, support_pos)


def velocity_heading_deg(velocity_xy: tuple[float, float]) -> float | None:
    vx, vy = velocity_xy
    speed = math.hypot(vx, vy)
    if speed <= 1e-9:
        return None
    # Match Phase 20 convention: 0 = +y, clockwise (atan2(dx, dy)).
    return math.degrees(math.atan2(vx, vy)) % 360.0


def notch_heading_choices(los_bearing_deg: float) -> tuple[float, float]:
    """Two perpendicular headings (±90°) relative to LOS."""
    a = (los_bearing_deg + 90.0) % 360.0
    b = (los_bearing_deg - 90.0) % 360.0
    return a, b


def pick_notch_heading(current_heading: float, los_bearing_deg: float) -> float:
    """Prefer the perpendicular heading closer to the jet's current nose."""
    a, b = notch_heading_choices(los_bearing_deg)
    if angular_distance(current_heading, a) <= angular_distance(current_heading, b):
        return a
    return b


def notch_geometry_ok(
    *,
    velocity_xy: tuple[float, float],
    los_bearing_deg: float,
    angle_window_deg: float,
    min_speed: float,
) -> bool:
    """Velocity must be near ±90° to LOS, at/above min_speed. IDLE fails."""
    speed = math.hypot(velocity_xy[0], velocity_xy[1])
    if speed < min_speed:
        return False
    heading = velocity_heading_deg(velocity_xy)
    if heading is None:
        return False
    # Angle between velocity and LOS; perpendicular ⇒ ≈ 90°.
    delta = angular_distance(heading, los_bearing_deg)
    return abs(delta - 90.0) <= angle_window_deg


def flare_decoy_probability(
    *,
    flare_quality: float,
    countermeasure_resistance: float,
    range_to_missile: float,
    closing_aspect: float,
) -> float:
    """Combine quality, seeker resistance, range, and aspect into [0, 1]."""
    if range_to_missile > FLARE_EFFECT_RANGE:
        return 0.0
    range_factor = 1.0 - (range_to_missile / FLARE_EFFECT_RANGE)
    aspect_factor = 0.45 + 0.55 * clamp01(closing_aspect)
    raw = (
        clamp01(flare_quality)
        * (1.0 - clamp01(countermeasure_resistance))
        * clamp01(range_factor)
        * aspect_factor
    )
    return clamp01(raw)


def closing_aspect(
    jet_pos: tuple[float, float],
    missile_pos: tuple[float, float],
    missile_heading_xy_rad: float,
) -> float:
    """1 when missile nose points at the jet, 0 when flying away."""
    dx = jet_pos[0] - missile_pos[0]
    dy = jet_pos[1] - missile_pos[1]
    dist = math.hypot(dx, dy)
    if dist <= 1e-9:
        return 1.0
    ux, uy = dx / dist, dy / dist
    mx, my = math.cos(missile_heading_xy_rad), math.sin(missile_heading_xy_rad)
    # missile heading_xy_rad is atan2(dy, dx) math convention from missiles.py
    return clamp01(mx * ux + my * uy)


def roll_flare_decoy(
    combat_seed: int,
    *,
    missile_id: str,
    jet_id: str,
    tick: int,
    probability: float,
) -> bool:
    roll = keyed_unit_float(
        combat_seed, "decoy", missile_id, jet_id, tick
    )
    return roll < clamp01(probability)


def notch_break_probability(
    *,
    countermeasure_resistance: float,
    active_terminal: bool,
    evasion_bonus: int = 0,
) -> float:
    base = (1.0 - clamp01(countermeasure_resistance)) * 0.9
    if active_terminal:
        base *= ACTIVE_TERMINAL_NOTCH_FACTOR
    base += 0.04 * max(0, int(evasion_bonus))
    return clamp01(min(base, 0.95))


def roll_notch_break(
    combat_seed: int,
    *,
    missile_id: str,
    jet_id: str,
    tick: int,
    probability: float,
) -> bool:
    roll = keyed_unit_float(
        combat_seed, "notch", missile_id, jet_id, tick
    )
    return roll < clamp01(probability)
