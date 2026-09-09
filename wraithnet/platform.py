"""Shared platform identity for cross-domain adjudication (Phase 37).

JetAgent remains a Mesa agent; this module exposes a lightweight identity
surface so LOS/range/hostility helpers can stay platform-neutral when ships
and tanks arrive. No GUI imports.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from wraithnet.taxonomy import Faction, PlatformDomain, TargetClass

if TYPE_CHECKING:
    from wraithnet.naval import CarrierState, CorvetteState
    from wraithnet.world import JetAgent


@runtime_checkable
class PlatformIdentity(Protocol):
    """Minimal identity surface for world adjudication."""

    @property
    def platform_id(self) -> str: ...

    @property
    def faction(self) -> Faction: ...

    @property
    def domain(self) -> PlatformDomain: ...

    @property
    def target_class(self) -> TargetClass: ...

    def position_xy(self) -> tuple[float, float]: ...

    def altitude_z(self) -> float: ...


@dataclass(frozen=True)
class PlatformSnapshot:
    """Concrete identity snapshot (adapters and tests)."""

    platform_id: str
    faction: Faction
    domain: PlatformDomain
    target_class: TargetClass
    position: tuple[float, float]
    altitude: float = 0.0

    def position_xy(self) -> tuple[float, float]:
        return self.position

    def altitude_z(self) -> float:
        return self.altitude


# JetAgent adapter contract (Phase 37):
#   platform_id  → jet.agent_id
#   faction      → "FRIENDLY"
#   domain       → "AIR"
#   target_class → "AIRCRAFT"
def carrier_as_platform(carrier: CarrierState) -> PlatformSnapshot:
    """Adapt a live CarrierState into a PlatformSnapshot."""
    return PlatformSnapshot(
        platform_id=carrier.unit_id,
        faction="FRIENDLY",
        domain="SURFACE",
        target_class="SURFACE_VESSEL",
        position=tuple(carrier.position),
        altitude=0.0,
    )


def corvette_as_platform(corvette: "CorvetteState") -> PlatformSnapshot:
    """Adapt a live CorvetteState into a PlatformSnapshot."""
    return PlatformSnapshot(
        platform_id=corvette.unit_id,
        faction="ENEMY",
        domain="SURFACE",
        target_class="SURFACE_VESSEL",
        position=tuple(corvette.position),
        altitude=0.0,
    )
