"""Pydantic schema for a WraithNet scenario: the full set of jets, targets,
SAM sites, and jammers that make up one runnable scenario. This is the
data-driven replacement for the hardcoded wraithnet.config.SCENARIO dict —
see docs/09_..._Design_Document.md §4.
"""

from pydantic import BaseModel, Field, model_validator

from wraithnet import config
from wraithnet.config import JetFrameType, TargetType, WeaponType


class JetSpec(BaseModel):
    agent_id: str
    position: tuple[float, float]
    payload: dict[WeaponType, int]
    frame_type: JetFrameType = "STRIKE_FIGHTER"
    initial_heading: float = Field(default=0.0, ge=0.0, lt=360.0)


class TargetSpec(BaseModel):
    target_id: str
    position: tuple[float, float]
    hp_required: int
    target_type: TargetType


class SamSiteSpec(BaseModel):
    target_id: str
    position: tuple[float, float]
    hp_required: int
    target_type: TargetType = "SAM_SITE"


class JammerSpec(BaseModel):
    jammer_id: str
    position: tuple[float, float]


class ScenarioConfig(BaseModel):
    grid_width: float = config.GRID_WIDTH
    grid_height: float = config.GRID_HEIGHT
    jets: list[JetSpec]
    targets: list[TargetSpec]
    sam_sites: list[SamSiteSpec] = []
    jammers: list[JammerSpec] = []
    home_base: tuple[float, float] = config.DEFAULT_HOME_BASE

    @model_validator(mode="after")
    def _validate(self) -> "ScenarioConfig":
        errors: list[str] = []

        if not self.jets:
            errors.append("A scenario needs at least one jet.")

        seen: dict[str, str] = {}
        for entity_id, kind in _entity_ids(self):
            if entity_id in seen:
                errors.append(
                    f"The ID '{entity_id}' is used more than once "
                    f"(as a {seen[entity_id]} and a {kind}). "
                    "Every jet, target, SAM site, and jammer must have a unique ID."
                )
            else:
                seen[entity_id] = kind

        for label, position in _entity_positions(self):
            x, y = position
            if not (0.0 <= x <= self.grid_width and 0.0 <= y <= self.grid_height):
                errors.append(
                    f"{label} is at ({x}, {y}), which is outside the map "
                    f"(0 to {self.grid_width} wide, 0 to {self.grid_height} tall)."
                )

        for spec in (*self.targets, *self.sam_sites):
            if spec.hp_required < 1:
                kind = "SAM site" if isinstance(spec, SamSiteSpec) else "Target"
                errors.append(
                    f"{kind} '{spec.target_id}' must have hp_required of at least 1 "
                    f"(currently {spec.hp_required})."
                )

        for jet in self.jets:
            for weapon, count in jet.payload.items():
                if count < 0:
                    errors.append(
                        f"Jet '{jet.agent_id}' has a {weapon} count of {count}. "
                        "Weapon counts cannot be negative."
                    )
            slots_used = sum(
                config.WEAPON_CATALOG[wt].slot_cost * count
                for wt, count in jet.payload.items()
            )
            slots_max = config.JET_FRAME_CATALOG[jet.frame_type].ordnance_slots
            if slots_used > slots_max:
                errors.append(
                    f"Jet '{jet.agent_id}' payload requires {slots_used} ordnance slots "
                    f"but its {jet.frame_type} frame only has {slots_max}"
                )

        if errors:
            raise ValueError("\n".join(errors))
        return self


def _entity_ids(scenario: ScenarioConfig) -> list[tuple[str, str]]:
    ids: list[tuple[str, str]] = []
    ids.extend((jet.agent_id, "jet") for jet in scenario.jets)
    ids.extend((target.target_id, "target") for target in scenario.targets)
    ids.extend((sam.target_id, "SAM site") for sam in scenario.sam_sites)
    ids.extend((jammer.jammer_id, "jammer") for jammer in scenario.jammers)
    return ids


def _entity_positions(
    scenario: ScenarioConfig,
) -> list[tuple[str, tuple[float, float]]]:
    positions: list[tuple[str, tuple[float, float]]] = []
    positions.extend(
        (f"Jet '{jet.agent_id}'", jet.position) for jet in scenario.jets
    )
    positions.extend(
        (f"Target '{target.target_id}'", target.position)
        for target in scenario.targets
    )
    positions.extend(
        (f"SAM site '{sam.target_id}'", sam.position) for sam in scenario.sam_sites
    )
    positions.extend(
        (f"Jammer '{jammer.jammer_id}'", jammer.position)
        for jammer in scenario.jammers
    )
    positions.append(("Home base", scenario.home_base))
    return positions


def _default_scenario() -> ScenarioConfig:
    # config.SCENARIO remains the single literal source of truth; this is a thin typed wrapper.
    return ScenarioConfig.model_validate(config.SCENARIO)
