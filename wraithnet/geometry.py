"""Heading and firing-arc helpers shared by World and agent runtimes.

Phase 20 heading convention (used everywhere below): 0° = facing +y
(world "up"), increasing clockwise when viewed from above (90° = facing +x).
Phase 21's strike-arc gate and decide_intent's "rotate to bear" branch must
use this same formula — a mismatch would let a jet believe it's in-arc
and never land a hit, or vice versa.
"""

from __future__ import annotations

import math


def bearing_to(
    from_pos: tuple[float, float], to_pos: tuple[float, float]
) -> float:
    """Clockwise-from-+y bearing of the vector ``from_pos → to_pos``, in [0, 360).

    ``atan2(dx, dy)`` (not the usual math ``atan2(dy, dx)``) is what maps
    +y → 0° and +x → 90° under Phase 20's heading convention. A coincident
    pair (zero vector) yields 0.0.
    """
    dx = to_pos[0] - from_pos[0]
    dy = to_pos[1] - from_pos[1]
    return math.degrees(math.atan2(dx, dy)) % 360.0


def shortest_signed_angle(from_deg: float, to_deg: float) -> float:
    """Signed shortest angular distance from ``from_deg`` to ``to_deg``, in (-180, 180]."""
    return (to_deg - from_deg + 180.0) % 360.0 - 180.0


def angular_distance(a: float, b: float) -> float:
    """Absolute shortest angular distance between two headings, in [0, 180]."""
    return abs(shortest_signed_angle(a, b))


def is_in_firing_arc(
    heading: float,
    from_pos: tuple[float, float],
    to_pos: tuple[float, float],
    firing_arc_deg: float,
) -> bool:
    """True if ``to_pos`` sits inside the weapon's forward cone around ``heading``.

    ``firing_arc_deg >= 360`` is all-aspect / self-guided: the check is skipped
    entirely (the missile tracks regardless of which way the jet's nose points,
    including a target behind it). Otherwise the target must lie within
    ``firing_arc_deg / 2`` of the jet's current heading.
    """
    if firing_arc_deg >= 360.0:
        return True
    return angular_distance(heading, bearing_to(from_pos, to_pos)) <= firing_arc_deg / 2.0
