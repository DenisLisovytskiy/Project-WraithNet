"""Shared direct-gun burst resolver (Phase 41+).

Platform-neutral adjudication for CIWS, AAA, and tank cannon. World code must
call ``resolve_gun_burst``; guns never bypass hostility/class/range/LOS gates.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from wraithnet.combat_rng import keyed_unit_float
from wraithnet.geometry import is_in_firing_arc
from wraithnet.taxonomy import Faction, TargetClass, are_hostile

GunKind = Literal["CIWS", "DECK_GUN", "AAA", "TANK_CANNON", "GENERIC"]


@dataclass(frozen=True)
class GunSpec:
    """Catalog entry for a direct-fire burst weapon (docs/19 §11)."""

    gun_id: str
    range: float
    min_range: float
    firing_arc_deg: float
    rounds_per_burst: int
    accuracy: float
    damage_per_hit: int
    cooldown_ticks: int
    allowed_target_classes: frozenset[TargetClass]
    altitude_ceiling: float | None = None
    kind: GunKind = "GENERIC"


@dataclass(frozen=True)
class GunBurstRequest:
    """One burst attempt submitted to the world for adjudication."""

    shooter_id: str
    target_id: str
    gun: GunSpec
    shooter_faction: Faction
    target_faction: Faction
    target_class: TargetClass
    shooter_position: tuple[float, float]
    target_position: tuple[float, float]
    shooter_heading_deg: float
    target_altitude: float
    ammo_available: int
    has_los: bool
    combat_seed: int
    tick: int
    burst_index: int = 0


@dataclass(frozen=True)
class GunRoundResult:
    round_index: int
    hit: bool
    roll: float


@dataclass(frozen=True)
class GunBurstResult:
    accepted: bool
    reason: str | None
    rounds: tuple[GunRoundResult, ...] = ()
    hits: int = 0
    damage: int = 0
    ammo_consumed: int = 0


# Phase 41 default corvette guns (catalog constants).
CIWS_GUN = GunSpec(
    gun_id="CIWS",
    range=4.0,
    min_range=0.0,
    firing_arc_deg=360.0,
    rounds_per_burst=4,
    accuracy=0.35,
    damage_per_hit=1,
    cooldown_ticks=1,
    allowed_target_classes=frozenset({"AIRCRAFT", "IN_FLIGHT_MISSILE"}),
    altitude_ceiling=25.0,
    kind="CIWS",
)

DECK_GUN = GunSpec(
    gun_id="DECK_GUN",
    range=12.0,
    min_range=0.5,
    firing_arc_deg=360.0,
    rounds_per_burst=1,
    accuracy=0.55,
    damage_per_hit=2,
    cooldown_ticks=2,
    allowed_target_classes=frozenset({"SURFACE_VESSEL"}),
    altitude_ceiling=None,
    kind="DECK_GUN",
)

# Phase 42 land AAA: substantially below IR SAM range (15.0), low accuracy.
# Jet frame evasion reduces applied damage in world._apply_platform_damage;
# it does not alter keyed hit rolls (documented Phase 42 term).
AAA_GUN = GunSpec(
    gun_id="AAA",
    range=5.0,
    min_range=0.0,
    firing_arc_deg=120.0,
    rounds_per_burst=3,
    accuracy=0.12,
    damage_per_hit=1,
    cooldown_ticks=2,
    allowed_target_classes=frozenset({"AIRCRAFT"}),
    altitude_ceiling=18.0,
    kind="AAA",
)

AAA_TURRETED_GUN = GunSpec(
    gun_id="AAA_TURRETED",
    range=5.5,
    min_range=0.0,
    firing_arc_deg=360.0,
    rounds_per_burst=4,
    accuracy=0.15,
    damage_per_hit=1,
    cooldown_ticks=2,
    allowed_target_classes=frozenset({"AIRCRAFT"}),
    altitude_ceiling=20.0,
    kind="AAA",
)

# Phase 43 tank cannon: GROUND_VEHICLE only (AI + world gate).
TANK_CANNON = GunSpec(
    gun_id="TANK_CANNON",
    range=8.0,
    min_range=0.0,
    firing_arc_deg=60.0,
    rounds_per_burst=1,
    accuracy=0.65,
    damage_per_hit=2,
    cooldown_ticks=2,
    allowed_target_classes=frozenset({"GROUND_VEHICLE"}),
    altitude_ceiling=None,
    kind="TANK_CANNON",
)


def validate_gun_burst(req: GunBurstRequest) -> str | None:
    """Return rejection reason, or None if the burst is allowed."""
    gun = req.gun
    if req.ammo_available <= 0:
        return "no_ammo"
    if not are_hostile(req.shooter_faction, req.target_faction):
        return "friendly_fire"
    if req.target_class not in gun.allowed_target_classes:
        return "illegal_target_class"
    # Taxonomy mirror: gun kinds that map to weapon types still respect class.
    if gun.kind == "CIWS" and not (
        req.target_class in ("AIRCRAFT", "IN_FLIGHT_MISSILE")
    ):
        return "illegal_target_class"
    if gun.kind == "DECK_GUN" and req.target_class != "SURFACE_VESSEL":
        return "illegal_target_class"
    if gun.kind == "AAA" and req.target_class != "AIRCRAFT":
        return "illegal_target_class"
    if gun.kind == "TANK_CANNON" and req.target_class != "GROUND_VEHICLE":
        return "illegal_target_class"

    dist = (
        (req.target_position[0] - req.shooter_position[0]) ** 2
        + (req.target_position[1] - req.shooter_position[1]) ** 2
    ) ** 0.5
    if dist > gun.range:
        return "out_of_range"
    if dist < gun.min_range:
        return "inside_min_range"
    if not is_in_firing_arc(
        req.shooter_heading_deg,
        req.shooter_position,
        req.target_position,
        gun.firing_arc_deg,
    ):
        return "outside_firing_arc"
    if not req.has_los:
        return "no_los"
    if gun.altitude_ceiling is not None and req.target_altitude > gun.altitude_ceiling:
        return "above_altitude_ceiling"
    return None


def resolve_gun_burst(req: GunBurstRequest) -> GunBurstResult:
    """Validate, consume ammo, and roll keyed accuracy per round in the burst."""
    reason = validate_gun_burst(req)
    if reason is not None:
        return GunBurstResult(accepted=False, reason=reason)

    gun = req.gun
    rounds_to_fire = min(gun.rounds_per_burst, req.ammo_available)
    rounds: list[GunRoundResult] = []
    hits = 0
    for i in range(rounds_to_fire):
        roll = keyed_unit_float(
            req.combat_seed,
            "gun_burst",
            req.tick,
            req.shooter_id,
            req.target_id,
            gun.gun_id,
            req.burst_index,
            i,
        )
        hit = roll < gun.accuracy
        if hit:
            hits += 1
        rounds.append(GunRoundResult(round_index=i, hit=hit, roll=roll))

    damage = hits * gun.damage_per_hit
    return GunBurstResult(
        accepted=True,
        reason=None,
        rounds=tuple(rounds),
        hits=hits,
        damage=damage,
        ammo_consumed=rounds_to_fire,
    )
