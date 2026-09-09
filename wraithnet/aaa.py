"""Land-based anti-aircraft artillery (Phase 42).

AAA sites are INSTALLATION entities that fire short-range inaccurate guns
via the shared ``resolve_gun_burst`` resolver. They never launch missiles.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from wraithnet.geometry import is_in_firing_arc
from wraithnet.guns import AAA_GUN, AAA_TURRETED_GUN, GunSpec
from wraithnet.taxonomy import Faction
from wraithnet.terrain import TerrainGrid, validate_position_for_domain

AaaFrameType = Literal["AAA_LIGHT", "AAA_TURRETED"]


@dataclass(frozen=True)
class AaaFrameSpec:
    """Catalog frame for an AAA emplacement."""

    frame_id: AaaFrameType
    hp: int
    sight_height: float
    default_ammo: int
    cooldown_ticks: int
    gun: GunSpec
    # Sensor / threat-contact confidence when observed at full clarity.
    default_confidence: float = 1.0


AAA_FRAME_CATALOG: dict[AaaFrameType, AaaFrameSpec] = {
    "AAA_LIGHT": AaaFrameSpec(
        frame_id="AAA_LIGHT",
        hp=2,
        sight_height=3.0,
        default_ammo=48,
        cooldown_ticks=2,
        gun=AAA_GUN,
    ),
    "AAA_TURRETED": AaaFrameSpec(
        frame_id="AAA_TURRETED",
        hp=3,
        sight_height=3.0,
        default_ammo=60,
        cooldown_ticks=2,
        gun=AAA_TURRETED_GUN,
    ),
}

DEFAULT_AAA_FRAME: AaaFrameType = "AAA_LIGHT"
DEFAULT_AAA_AMMO: int = AAA_FRAME_CATALOG[DEFAULT_AAA_FRAME].default_ammo


class AaaSiteSpec(BaseModel):
    """Scenario AAA emplacement (Phase 42). Land INSTALLATION gun defense."""

    model_config = ConfigDict(extra="forbid")

    target_id: str
    position: tuple[float, float]
    heading: float = Field(default=0.0, ge=0.0, lt=360.0)
    faction: Faction = "ENEMY"
    frame_type: AaaFrameType = DEFAULT_AAA_FRAME
    # HP override; None → frame catalog HP.
    hp_required: int | None = Field(default=None, ge=1)
    # Ammo override; None → frame catalog default_ammo.
    ammo: int | None = Field(default=None, ge=0)
    target_type: Literal["AAA_SITE"] = "AAA_SITE"

    def resolved_hp(self) -> int:
        if self.hp_required is not None:
            return int(self.hp_required)
        return int(AAA_FRAME_CATALOG[self.frame_type].hp)

    def resolved_ammo(self) -> int:
        if self.ammo is not None:
            return int(self.ammo)
        return int(AAA_FRAME_CATALOG[self.frame_type].default_ammo)


@dataclass
class AaaSiteState:
    """Live AAA runtime state (Mesa agent mirrors identity; HP lives in TargetState)."""

    target_id: str
    position: tuple[float, float]
    heading: float
    faction: Faction
    frame_type: AaaFrameType
    ammo: int
    cooldown_ticks: int
    cooldown_remaining: int = 0
    burst_seq: int = 0
    # Cumulative metrics for this site (mirrored into model totals).
    bursts_fired: int = 0
    rounds_fired: int = 0
    hits: int = 0
    damage_dealt: int = 0

    @property
    def platform_id(self) -> str:
        return self.target_id

    @property
    def domain(self) -> Literal["INSTALLATION"]:
        return "INSTALLATION"

    @property
    def target_class(self) -> Literal["INSTALLATION"]:
        return "INSTALLATION"

    def position_xy(self) -> tuple[float, float]:
        return self.position

    def altitude_z(self) -> float:
        return 0.0

    @property
    def gun(self) -> GunSpec:
        return AAA_FRAME_CATALOG[self.frame_type].gun

    @property
    def sight_height(self) -> float:
        return float(AAA_FRAME_CATALOG[self.frame_type].sight_height)


def validate_aaa_placement(terrain: TerrainGrid, site: AaaSiteSpec) -> list[str]:
    """Return placement/schema errors for one AAA site (empty if valid)."""
    errors: list[str] = []
    try:
        validate_position_for_domain(terrain, site.position, "GROUND")
    except ValueError as exc:
        errors.append(f"AAA site '{site.target_id}': {exc}")
    if site.ammo is not None and site.ammo < 0:
        errors.append(
            f"AAA site '{site.target_id}' ammo ({site.ammo}) cannot be negative."
        )
    if site.frame_type not in AAA_FRAME_CATALOG:
        errors.append(
            f"AAA site '{site.target_id}' unknown frame_type '{site.frame_type}'."
        )
    return errors


def select_aaa_target(
    site: AaaSiteState,
    aircraft: list[tuple[str, tuple[float, float], float, bool]],
) -> str | None:
    """Pick at most one hostile aircraft for a ready burst.

    ``aircraft`` entries: (jet_id, xy, altitude, has_los).
    Filters: range, altitude ceiling, LOS, firing arc; nearest then stable ID.
    """
    if site.ammo <= 0 or site.cooldown_remaining > 0:
        return None
    gun = site.gun
    candidates: list[tuple[float, str]] = []
    for jid, pos, alt, has_los in aircraft:
        if not has_los:
            continue
        if gun.altitude_ceiling is not None and alt > gun.altitude_ceiling:
            continue
        dist = math.dist(site.position, pos)
        if dist > gun.range or dist < gun.min_range:
            continue
        if not is_in_firing_arc(
            site.heading, site.position, pos, gun.firing_arc_deg
        ):
            continue
        candidates.append((dist, jid))
    if not candidates:
        return None
    candidates.sort(key=lambda x: (x[0], x[1]))
    return candidates[0][1]
