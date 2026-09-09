"""Fixed airbases and jet base assignments (Phase 37)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

HOME_BASE_ID = "Base_Home"


class FixedAirbaseSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    base_id: str
    position: tuple[float, float]


class BaseAssignment(BaseModel):
    model_config = ConfigDict(extra="forbid")

    primary_base_id: str
    alternate_base_id: str | None = None


def default_home_airbase(position: tuple[float, float]) -> FixedAirbaseSpec:
    return FixedAirbaseSpec(base_id=HOME_BASE_ID, position=position)


def default_jet_assignment() -> BaseAssignment:
    return BaseAssignment(primary_base_id=HOME_BASE_ID, alternate_base_id=None)


def resolve_base_position(
    fixed_airbases: list[FixedAirbaseSpec] | dict[str, tuple[float, float]],
    base_id: str,
) -> tuple[float, float]:
    if isinstance(fixed_airbases, dict):
        if base_id not in fixed_airbases:
            raise KeyError(f"Unknown base_id '{base_id}'")
        return fixed_airbases[base_id]
    for base in fixed_airbases:
        if base.base_id == base_id:
            return base.position
    raise KeyError(f"Unknown base_id '{base_id}'")
