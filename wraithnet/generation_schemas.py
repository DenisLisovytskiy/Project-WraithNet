"""Pydantic contracts for deterministic scenario generation (Phase 30).

Dependency-neutral: imports only stdlib, Pydantic, and catalog literal types /
catalogs from ``wraithnet.config``. Must not import ``ScenarioConfig`` so
``scenario_schema`` can safely import ``GenerationMetadata`` without a cycle.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

from wraithnet import config
from wraithnet.config import JetFrameType, TargetType, WeaponType

DifficultyPreset = Literal["TRAINING", "STANDARD", "CONTESTED", "NIGHTMARE", "CUSTOM"]
FriendlyMode = Literal["AUTO_BALANCED", "RANDOM_LEGAL", "CUSTOM_ROSTER"]
GroupStyle = Literal["DISPERSED", "CLUSTERED", "MIXED"]
GroupTemplateName = Literal[
    "OBJECTIVE_ONLY",
    "DEFENDED_OBJECTIVE",
    "JAMMED_OBJECTIVE",
    "IADS_CLUSTER",
    "SAM_PAIR",
]

_DEFAULT_GROUP_TEMPLATES: tuple[GroupTemplateName, ...] = (
    "OBJECTIVE_ONLY",
    "DEFENDED_OBJECTIVE",
    "JAMMED_OBJECTIVE",
    "IADS_CLUSTER",
)

# Geometric plausibility only — reject obvious impossibilities, not exact packing.
_MIN_USABLE_MAP_SPAN: float = 10.0


def _default_group_templates() -> list[GroupTemplateName]:
    return list(_DEFAULT_GROUP_TEMPLATES)


def _default_frame_types() -> list[JetFrameType]:
    return list(config.JET_FRAME_CATALOG)


def _payload_slots_used(payload: dict[WeaponType, int]) -> int:
    return sum(
        config.WEAPON_CATALOG[weapon].slot_cost * count
        for weapon, count in payload.items()
    )


class FriendlyTemplate(BaseModel):
    frame_type: JetFrameType
    payload: dict[WeaponType, int]
    count: int = Field(ge=1, le=20)


class TerrainGenerationParameters(BaseModel):
    """Procedural terrain coverage and connectivity (Phase 35).

    ``water_fraction`` is of all map cells. ``forest_fraction`` and
    ``city_fraction`` are of remaining land cells after water assignment.
    """

    water_fraction: float = Field(default=0.20, ge=0.0, le=0.75)
    forest_fraction: float = Field(default=0.20, ge=0.0, le=0.80)
    city_fraction: float = Field(default=0.10, ge=0.0, le=0.50)
    smoothing_passes: int = Field(default=3, ge=0, le=8)
    city_cluster_radius: int = Field(default=2, ge=1, le=6)
    building_height_min: float = Field(default=4.0, ge=4.0, le=25.0)
    building_height_max: float = Field(default=18.0, ge=4.0, le=25.0)
    minimum_land_component_cells: int = Field(default=40, ge=1)
    minimum_water_component_cells: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _validate_terrain(self) -> TerrainGenerationParameters:
        errors: list[str] = []
        if self.forest_fraction + self.city_fraction > 1.0 + 1e-12:
            errors.append(
                f"forest_fraction ({self.forest_fraction}) + city_fraction "
                f"({self.city_fraction}) must be <= 1.0."
            )
        if self.building_height_min > self.building_height_max:
            errors.append(
                f"building_height_min ({self.building_height_min}) must be <= "
                f"building_height_max ({self.building_height_max})."
            )
        if errors:
            raise ValueError("\n".join(errors))
        return self


def _default_terrain_parameters() -> TerrainGenerationParameters:
    return TerrainGenerationParameters()


class GenerationParameters(BaseModel):
    seed: int = Field(ge=0, le=2**64 - 1)
    preset: DifficultyPreset = "STANDARD"
    objective_count: int = Field(default=4, ge=1, le=20)
    sam_count: int = Field(default=2, ge=0, le=12)
    jammer_count: int = Field(default=1, ge=0, le=8)
    objective_hp_min: int = Field(default=1, ge=1, le=10)
    objective_hp_max: int = Field(default=4, ge=1, le=12)
    cooperation_objective_fraction: float = Field(default=0.25, ge=0.0, le=1.0)
    group_style: GroupStyle = "MIXED"
    allowed_group_templates: list[GroupTemplateName] = Field(
        default_factory=_default_group_templates
    )
    objective_distance_min_fraction: float = Field(default=0.20, ge=0.0, le=1.0)
    objective_distance_max_fraction: float = Field(default=0.85, ge=0.0, le=1.0)
    map_margin: float = Field(default=5.0, ge=1.0)
    base_safety_radius: float = Field(default=18.0, ge=5.0)
    group_min_separation: float = Field(default=16.0, ge=3.0)
    entity_min_separation: float = Field(default=3.0, ge=0.5)
    threat_overlap: float = Field(default=0.35, ge=0.0, le=1.0)
    launch_threat_clearance: float = Field(default=5.0, ge=0.0, le=30.0)
    friendly_mode: FriendlyMode = "AUTO_BALANCED"
    friendly_count_min: int = Field(default=3, ge=1, le=20)
    friendly_count_max: int = Field(default=6, ge=1, le=20)
    allowed_frame_types: list[JetFrameType] = Field(default_factory=_default_frame_types)
    custom_roster: list[FriendlyTemplate] = Field(default_factory=list)
    friendly_reserve_factor: float = Field(default=1.25, ge=1.0, le=3.0)
    max_placement_attempts: int = Field(default=200, ge=20, le=5000)
    terrain: TerrainGenerationParameters = Field(
        default_factory=_default_terrain_parameters
    )
    # Phase 40: optional naval friendlies (placement enforced when count > 0).
    friendly_carrier_count: int = Field(default=0, ge=0, le=4)
    carrier_base_mode: bool = False
    # Phase 41: optional enemy corvettes (water placement when count > 0).
    enemy_corvette_count: int = Field(default=0, ge=0, le=8)
    corvette_defense_tier: Literal["LIGHT", "STANDARD"] = "STANDARD"
    # Phase 42: optional land AAA gun sites.
    aaa_count: int = Field(default=0, ge=0, le=16)
    # Phase 43: friendly/enemy tanks (land pathing + cannon).
    friendly_tank_count: int = Field(default=0, ge=0, le=12)
    enemy_tank_count: int = Field(default=0, ge=0, le=12)
    # Phase 44: combined-arms generation mixes.
    naval_enabled: bool = False
    sam_ir_fraction: float = Field(default=0.0, ge=0.0, le=1.0)
    jet_flare_fraction: float = Field(default=1.0, ge=0.0, le=1.0)

    @model_validator(mode="after")
    def _validate(self) -> GenerationParameters:
        errors: list[str] = []

        if self.objective_hp_min > self.objective_hp_max:
            errors.append(
                f"objective_hp_min ({self.objective_hp_min}) must be <= "
                f"objective_hp_max ({self.objective_hp_max})."
            )
        if self.friendly_count_min > self.friendly_count_max:
            errors.append(
                f"friendly_count_min ({self.friendly_count_min}) must be <= "
                f"friendly_count_max ({self.friendly_count_max})."
            )
        if self.objective_distance_min_fraction > self.objective_distance_max_fraction:
            errors.append(
                f"objective_distance_min_fraction "
                f"({self.objective_distance_min_fraction}) must be <= "
                f"objective_distance_max_fraction "
                f"({self.objective_distance_max_fraction})."
            )

        if self.objective_count + self.sam_count < 1:
            errors.append(
                "A generation request needs at least one destroyable entity "
                "(objective_count + sam_count >= 1)."
            )

        if not self.allowed_group_templates:
            errors.append("allowed_group_templates must contain at least one template.")
        elif len(self.allowed_group_templates) != len(set(self.allowed_group_templates)):
            errors.append("allowed_group_templates must not contain duplicates.")

        if not self.allowed_frame_types:
            errors.append("allowed_frame_types must contain at least one frame type.")
        elif len(self.allowed_frame_types) != len(set(self.allowed_frame_types)):
            errors.append("allowed_frame_types must not contain duplicates.")

        # CUSTOM_ROSTER requires a non-empty roster; other modes reject a
        # non-empty roster so accidental GUI leftovers never silently apply.
        if self.friendly_mode == "CUSTOM_ROSTER":
            if not self.custom_roster:
                errors.append(
                    "friendly_mode CUSTOM_ROSTER requires at least one "
                    "custom_roster template."
                )
        elif self.custom_roster:
            errors.append(
                f"friendly_mode {self.friendly_mode} rejects a non-empty "
                "custom_roster; clear the roster or switch to CUSTOM_ROSTER."
            )

        for index, template in enumerate(self.custom_roster):
            label = f"custom_roster[{index}] ({template.frame_type} x{template.count})"
            for weapon, count in template.payload.items():
                if count < 0:
                    errors.append(
                        f"{label} has a {weapon} count of {count}. "
                        "Weapon counts cannot be negative."
                    )
            slots_used = _payload_slots_used(template.payload)
            slots_max = config.JET_FRAME_CATALOG[template.frame_type].ordnance_slots
            if slots_used > slots_max:
                errors.append(
                    f"{label} payload requires {slots_used} ordnance slots "
                    f"but its {template.frame_type} frame only has {slots_max}."
                )

        if self.carrier_base_mode and self.friendly_carrier_count < 1:
            errors.append(
                "carrier_base_mode requires friendly_carrier_count >= 1."
            )
        naval_needed = (
            self.naval_enabled
            or self.friendly_carrier_count > 0
            or self.carrier_base_mode
            or self.enemy_corvette_count > 0
        )
        if naval_needed and self.terrain.minimum_water_component_cells < 1:
            self.terrain.minimum_water_component_cells = 40
        if naval_needed:
            self.naval_enabled = True

        usable_width = config.GRID_WIDTH - 2.0 * self.map_margin
        usable_height = config.GRID_HEIGHT - 2.0 * self.map_margin
        if usable_width < _MIN_USABLE_MAP_SPAN or usable_height < _MIN_USABLE_MAP_SPAN:
            errors.append(
                f"map_margin ({self.map_margin}) leaves less than "
                f"{_MIN_USABLE_MAP_SPAN} usable span on the "
                f"{config.GRID_WIDTH}x{config.GRID_HEIGHT} map."
            )
        # Base safety plus margin must leave room for at least one group center.
        if self.base_safety_radius >= min(usable_width, usable_height):
            errors.append(
                f"base_safety_radius ({self.base_safety_radius}) is too large "
                f"for the usable map after map_margin ({self.map_margin})."
            )
        if self.group_min_separation > max(usable_width, usable_height):
            errors.append(
                f"group_min_separation ({self.group_min_separation}) exceeds "
                "the usable map span."
            )

        if errors:
            raise ValueError("\n".join(errors))
        return self


class GenerationMetadata(BaseModel):
    # Phase 35 emits 2; v1 provenance remains loadable for older scenarios.
    generator_version: int = Field(default=3, ge=1)
    seed: int
    preset: DifficultyPreset
    resolved_parameters: GenerationParameters
    deliberate_cooperative_objective_ids: list[str] = Field(default_factory=list)
    modified_after_generation: bool = False


class FeasibilityIssue(BaseModel):
    severity: Literal["ERROR", "WARNING", "INFO"]
    code: str
    message: str
    related_ids: list[str] = Field(default_factory=list)


class ObjectiveFeasibility(BaseModel):
    """Per-objective allocation summary (Phase 37). Type totals remain derived."""

    objective_id: str
    objective_type: str = "DESTROY"
    subject_ids: list[str] = Field(default_factory=list)
    required_hp: int = 0
    allocated_damage_capacity: int = 0
    reachable: bool = True


class FeasibilityReport(BaseModel):
    feasible: bool
    issues: list[FeasibilityIssue] = Field(default_factory=list)
    total_damage_capacity_by_target_type: dict[TargetType, int]
    required_hp_by_target_type: dict[TargetType, int]
    unreachable_group_ids: list[str] = Field(default_factory=list)
    cooperative_objective_ids: list[str] = Field(default_factory=list)
    challenge_score: float = Field(ge=0.0, le=100.0)
    challenge_components: dict[str, float] = Field(default_factory=dict)
    # Phase 37: optional per-objective rows; type totals above stay authoritative
    # for legacy consumers and may be derived from these later.
    objective_feasibility: list[ObjectiveFeasibility] = Field(default_factory=list)
