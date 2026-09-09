"""Pure functions computing a jet's flight altitude baseline and bank angle
from TraceHeader/TraceTick data. Phase 37: when JetFrame.altitude is set,
it is the authoritative desired Z; takeoff/dive cosmetics apply only as a
legacy fallback for traces that omit altitude. Bank easing remains cosmetic.
Nothing in wraithnet/ imports this module.
"""

from __future__ import annotations

import math

from wraithnet.trace_schemas import TraceHeader, TraceTick

GROUND_ALTITUDE = 0.0
CRUISE_ALTITUDE = 8.0  # matches viewer3d/scene.py's existing JET_ALTITUDE
ATTACK_ALTITUDE = 2.0
TAKEOFF_CLIMB_SECONDS = 2.5
ENGAGEMENT_RADIUS_MULTIPLIER = 3.0
POST_STRIKE_CLIMB_BOOST_TICKS = 3
POST_STRIKE_CLIMB_BOOST_ALTITUDE = 4.0
MAX_CLIMB_RATE_PER_SECOND = 6.0
MAX_BANK_RATE_PER_SECOND = 90.0
MAX_BANK_DEG = 35.0

_MAX_ALTITUDE = CRUISE_ALTITUDE + POST_STRIKE_CLIMB_BOOST_ALTITUDE


def nearest_alive_target_distance(
    jet_position: tuple[float, float],
    header: TraceHeader,
    tick: TraceTick,
) -> float | None:
    """Distance from jet_position to the closest target that is still alive
    this tick (hp_remaining > 0). None if no targets exist or all are dead."""
    best: float | None = None
    for target in tick.targets:
        if target.hp_remaining <= 0:
            continue
        pos = header.target_positions.get(target.target_id)
        if pos is None:
            continue
        dist = math.dist(jet_position, pos)
        if best is None or dist < best:
            best = dist
    return best


def _takeoff_baseline(seconds_airborne: float) -> float:
    if seconds_airborne <= 0.0:
        return GROUND_ALTITUDE
    if seconds_airborne >= TAKEOFF_CLIMB_SECONDS:
        return CRUISE_ALTITUDE
    frac = seconds_airborne / TAKEOFF_CLIMB_SECONDS
    return GROUND_ALTITUDE + frac * (CRUISE_ALTITUDE - GROUND_ALTITUDE)


def _legacy_cosmetic_altitude(
    seconds_airborne: float,
    jet_position: tuple[float, float],
    header: TraceHeader,
    tick: TraceTick,
    just_struck: bool,
) -> float:
    """Pre-Phase-37 takeoff/dive fiction for traces without JetFrame.altitude."""
    baseline = _takeoff_baseline(seconds_airborne)
    threshold = header.strike_range * ENGAGEMENT_RADIUS_MULTIPLIER
    nearest = nearest_alive_target_distance(jet_position, header, tick)
    altitude = baseline
    if nearest is not None and threshold > 0.0 and nearest < threshold:
        proximity = (threshold - nearest) / threshold
        # A nearby target must not lift a jet that has not yet climbed: dive
        # toward min(ATTACK_ALTITUDE, baseline) so seconds_airborne=0 stays
        # on the ground even when a bunker is inside the engagement zone.
        attack_target = min(ATTACK_ALTITUDE, baseline)
        altitude = baseline + proximity * (attack_target - baseline)
    if just_struck:
        altitude += POST_STRIKE_CLIMB_BOOST_ALTITUDE
    return max(GROUND_ALTITUDE, min(altitude, _MAX_ALTITUDE))


def desired_altitude(
    seconds_airborne: float,
    jet_position: tuple[float, float],
    header: TraceHeader,
    tick: TraceTick,
    just_struck: bool,
    *,
    authoritative_altitude: float | None = None,
) -> float:
    """Prefer trace JetFrame.altitude; else legacy takeoff/dive cosmetics."""
    if authoritative_altitude is not None:
        return max(GROUND_ALTITUDE, float(authoritative_altitude))
    return _legacy_cosmetic_altitude(
        seconds_airborne, jet_position, header, tick, just_struck
    )


def desired_bank_deg(
    previous_altitude_target: float | None,
    current_altitude_target: float,
    dt_seconds: float,
) -> float:
    """Bank from the rate of change of the desired altitude target."""
    if previous_altitude_target is None or dt_seconds <= 0.0:
        return 0.0
    rate = (current_altitude_target - previous_altitude_target) / dt_seconds
    if rate == 0.0:
        return 0.0
    # Climbing -> positive bank; diving -> negative bank.
    bank = rate * (MAX_BANK_DEG / MAX_CLIMB_RATE_PER_SECOND)
    return max(-MAX_BANK_DEG, min(MAX_BANK_DEG, bank))


def ease_toward(
    current: float, target: float, max_rate_per_second: float, dt_seconds: float
) -> float:
    """Move current toward target by at most max_rate_per_second * dt_seconds."""
    if current == target:
        return current
    step = max_rate_per_second * dt_seconds
    diff = target - current
    if abs(diff) <= step:
        return target
    return current + math.copysign(step, diff)


def strike_boost_active(last_strike_tick: int | None, current_tick: int) -> bool:
    """True while the post-strike climb boost window is still open."""
    if last_strike_tick is None:
        return False
    return 0 <= current_tick - last_strike_tick < POST_STRIKE_CLIMB_BOOST_TICKS
