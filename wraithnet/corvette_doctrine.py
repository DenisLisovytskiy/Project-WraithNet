"""Corvette layered-defense target selection (Phase 41).

Stable priority (docs/phases/Phase_41… / docs/19 §10.2):

1. CIWS vs inbound anti-ship missiles inside intercept range
2. radar SAM vs hostile aircraft in range/LOS
3. CIWS vs very close hostile aircraft
4. deck gun vs hostile surface vessel
5. distance / ID tie-break

Corvettes do not engage tanks/installations and do not join CNP.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from wraithnet.guns import CIWS_GUN, DECK_GUN
from wraithnet.naval import NAVAL_FRAME_CATALOG, CorvetteState

LayerKind = Literal["CIWS_MISSILE", "SAM_RADAR", "CIWS_AIRCRAFT", "DECK_GUN"]


@dataclass(frozen=True)
class DoctrineCandidate:
    layer: LayerKind
    target_id: str
    distance: float


def _tie_key(distance: float, target_id: str) -> tuple[float, str]:
    return (distance, target_id)


def select_ciws_missile_target(
    corvette: CorvetteState,
    inbound: list[tuple[str, tuple[float, float], float]],
) -> DoctrineCandidate | None:
    """``inbound`` entries: (missile_id, xy, altitude)."""
    if corvette.loadout.get("CIWS", 0) <= 0:
        return None
    if corvette.ciws_cooldown_remaining > 0:
        return None
    best: DoctrineCandidate | None = None
    for mid, pos, _alt in inbound:
        dist = math.dist(corvette.position, pos)
        if dist > CIWS_GUN.range:
            continue
        cand = DoctrineCandidate("CIWS_MISSILE", mid, dist)
        if best is None or _tie_key(cand.distance, cand.target_id) < _tie_key(
            best.distance, best.target_id
        ):
            best = cand
    return best


def select_sam_aircraft_target(
    corvette: CorvetteState,
    aircraft: list[tuple[str, tuple[float, float], float, bool]],
) -> DoctrineCandidate | None:
    """``aircraft``: (jet_id, xy, altitude, has_los)."""
    if corvette.loadout.get("SAM_RADAR", 0) <= 0:
        return None
    if corvette.sam_cooldown_remaining > 0:
        return None
    frame = NAVAL_FRAME_CATALOG[corvette.frame_type]
    eng = frame.sam_engagement_range or 15.0
    best: DoctrineCandidate | None = None
    for jid, pos, _alt, has_los in aircraft:
        if not has_los:
            continue
        dist = math.dist(corvette.position, pos)
        if dist > eng:
            continue
        cand = DoctrineCandidate("SAM_RADAR", jid, dist)
        if best is None or _tie_key(cand.distance, cand.target_id) < _tie_key(
            best.distance, best.target_id
        ):
            best = cand
    return best


def select_ciws_aircraft_target(
    corvette: CorvetteState,
    aircraft: list[tuple[str, tuple[float, float], float, bool]],
) -> DoctrineCandidate | None:
    if corvette.loadout.get("CIWS", 0) <= 0:
        return None
    if corvette.ciws_cooldown_remaining > 0:
        return None
    best: DoctrineCandidate | None = None
    for jid, pos, alt, has_los in aircraft:
        if not has_los:
            continue
        if CIWS_GUN.altitude_ceiling is not None and alt > CIWS_GUN.altitude_ceiling:
            continue
        dist = math.dist(corvette.position, pos)
        if dist > CIWS_GUN.range:
            continue
        cand = DoctrineCandidate("CIWS_AIRCRAFT", jid, dist)
        if best is None or _tie_key(cand.distance, cand.target_id) < _tie_key(
            best.distance, best.target_id
        ):
            best = cand
    return best


def select_deck_gun_target(
    corvette: CorvetteState,
    vessels: list[tuple[str, tuple[float, float], bool]],
) -> DoctrineCandidate | None:
    """``vessels``: (unit_id, xy, has_los) hostile surface vessels only."""
    if corvette.loadout.get("DECK_GUN", 0) <= 0:
        return None
    if corvette.deck_gun_cooldown_remaining > 0:
        return None
    best: DoctrineCandidate | None = None
    for uid, pos, has_los in vessels:
        if not has_los:
            continue
        dist = math.dist(corvette.position, pos)
        if dist > DECK_GUN.range or dist < DECK_GUN.min_range:
            continue
        cand = DoctrineCandidate("DECK_GUN", uid, dist)
        if best is None or _tie_key(cand.distance, cand.target_id) < _tie_key(
            best.distance, best.target_id
        ):
            best = cand
    return best


def select_doctrine_actions(
    corvette: CorvetteState,
    *,
    inbound_missiles: list[tuple[str, tuple[float, float], float]],
    aircraft: list[tuple[str, tuple[float, float], float, bool]],
    vessels: list[tuple[str, tuple[float, float], bool]],
) -> list[DoctrineCandidate]:
    """Return ordered engagements for this tick (one per independent layer).

    CIWS missile and CIWS aircraft share ammo/cooldown — missile priority wins
    when both are eligible, so at most one CIWS engagement is returned.
    """
    actions: list[DoctrineCandidate] = []
    ciws_missile = select_ciws_missile_target(corvette, inbound_missiles)
    if ciws_missile is not None:
        actions.append(ciws_missile)
    sam = select_sam_aircraft_target(corvette, aircraft)
    if sam is not None:
        actions.append(sam)
    if ciws_missile is None:
        ciws_air = select_ciws_aircraft_target(corvette, aircraft)
        if ciws_air is not None:
            actions.append(ciws_air)
    deck = select_deck_gun_target(corvette, vessels)
    if deck is not None:
        actions.append(deck)
    return actions
