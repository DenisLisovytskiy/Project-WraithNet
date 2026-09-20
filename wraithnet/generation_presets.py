"""Immutable difficulty presets and seed-resolved GenerationParameters.

Presets expand into explicit scalar fields before generation. Downstream
generator code must never branch on preset names — only on resolved
``GenerationParameters``. Presets never mutate runtime catalog/config constants.
"""

from __future__ import annotations

import random
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from wraithnet import config
from wraithnet.generation_rng import (  # noqa: F401 — re-exported for callers
    ENEMY_SALT,
    FRIENDLY_SALT,
    PRESET_SALT,
    TERRAIN_SALT,
    derived_seed,
)
from wraithnet.generation_schemas import (
    DifficultyPreset,
    GenerationParameters,
    GroupStyle,
    GroupTemplateName,
    TerrainGenerationParameters,
)


@dataclass(frozen=True)
class DifficultyPresetRecord:
    """Template values before seed resolution.

    Count fields may be a scalar or an inclusive ``(min, max)`` range. Ranges
    are drawn with ``random.Random(derived_seed(seed, PRESET_SALT))``.
    """

    objective_count: int | tuple[int, int]
    sam_count: int | tuple[int, int]
    jammer_count: int | tuple[int, int]
    objective_hp_min: int
    objective_hp_max: int
    cooperation_objective_fraction: float
    group_style: GroupStyle
    allowed_group_templates: tuple[GroupTemplateName, ...]
    objective_distance_min_fraction: float
    objective_distance_max_fraction: float
    threat_overlap: float
    launch_threat_clearance: float
    friendly_count_min: int
    friendly_count_max: int
    friendly_reserve_factor: float
    terrain: TerrainGenerationParameters
    grid_width: float = config.DEFAULT_GENERATION_GRID_WIDTH
    grid_height: float = config.DEFAULT_GENERATION_GRID_HEIGHT
    map_margin: float = 5.0
    base_safety_radius: float = 18.0
    base_safety_fraction: float = 0.06
    anchor_min_angular_separation_deg: float = 35.0
    group_min_separation: float = 16.0
    entity_min_separation: float = 3.0
    max_placement_attempts: int = 200
    # Phase 44 combined-arms (scalars only — generator never branches on preset name).
    aaa_count: int | tuple[int, int] = 0
    friendly_tank_count: int | tuple[int, int] = 0
    enemy_tank_count: int | tuple[int, int] = 0
    friendly_carrier_count: int | tuple[int, int] = 0
    enemy_corvette_count: int | tuple[int, int] = 0
    carrier_base_mode: bool = False
    naval_enabled: bool = False
    corvette_defense_tier: str = "STANDARD"
    sam_ir_fraction: float = 0.0
    jet_flare_fraction: float = 1.0


_NO_SAM_PAIR: tuple[GroupTemplateName, ...] = (
    "OBJECTIVE_ONLY",
    "DEFENDED_OBJECTIVE",
    "JAMMED_OBJECTIVE",
    "IADS_CLUSTER",
)

_WITH_SAM_PAIR: tuple[GroupTemplateName, ...] = (
    "OBJECTIVE_ONLY",
    "DEFENDED_OBJECTIVE",
    "JAMMED_OBJECTIVE",
    "IADS_CLUSTER",
    "SAM_PAIR",
)


DIFFICULTY_PRESETS: dict[DifficultyPreset, DifficultyPresetRecord] = {
    "TRAINING": DifficultyPresetRecord(
        objective_count=2,
        sam_count=0,
        jammer_count=(0, 1),
        objective_hp_min=1,
        objective_hp_max=2,
        cooperation_objective_fraction=0.0,
        group_style="DISPERSED",
        allowed_group_templates=_NO_SAM_PAIR,
        objective_distance_min_fraction=0.25,
        objective_distance_max_fraction=0.65,
        threat_overlap=0.15,
        launch_threat_clearance=10.0,
        base_safety_fraction=0.08,
        anchor_min_angular_separation_deg=45.0,
        friendly_count_min=3,
        friendly_count_max=4,
        friendly_reserve_factor=1.6,
        terrain=TerrainGenerationParameters(
            water_fraction=0.10,
            forest_fraction=0.15,
            city_fraction=0.03,
            smoothing_passes=2,
            city_cluster_radius=2,
            building_height_min=4.0,
            building_height_max=10.0,
            minimum_land_component_cells=80,
            minimum_water_component_cells=0,
        ),
        aaa_count=0,
        friendly_tank_count=0,
        enemy_tank_count=0,
        sam_ir_fraction=0.0,
        jet_flare_fraction=1.0,
    ),
    "STANDARD": DifficultyPresetRecord(
        objective_count=(3, 4),
        sam_count=(1, 2),
        jammer_count=1,
        objective_hp_min=1,
        objective_hp_max=4,
        cooperation_objective_fraction=0.25,
        group_style="MIXED",
        allowed_group_templates=_NO_SAM_PAIR,
        objective_distance_min_fraction=0.20,
        objective_distance_max_fraction=0.85,
        threat_overlap=0.35,
        launch_threat_clearance=5.0,
        base_safety_fraction=0.06,
        anchor_min_angular_separation_deg=35.0,
        friendly_count_min=3,
        friendly_count_max=6,
        friendly_reserve_factor=1.35,
        terrain=TerrainGenerationParameters(
            water_fraction=0.18,
            forest_fraction=0.18,
            city_fraction=0.05,
            smoothing_passes=3,
            city_cluster_radius=2,
            building_height_min=4.0,
            building_height_max=12.0,
            minimum_land_component_cells=40,
            minimum_water_component_cells=0,
        ),
        aaa_count=(0, 1),
        friendly_tank_count=0,
        enemy_tank_count=0,
        sam_ir_fraction=0.25,
        jet_flare_fraction=1.0,
    ),
    "CONTESTED": DifficultyPresetRecord(
        objective_count=(5, 7),
        sam_count=(2, 4),
        jammer_count=(1, 2),
        objective_hp_min=2,
        objective_hp_max=5,
        cooperation_objective_fraction=0.40,
        group_style="MIXED",
        allowed_group_templates=_WITH_SAM_PAIR,
        objective_distance_min_fraction=0.30,
        objective_distance_max_fraction=0.90,
        threat_overlap=0.50,
        launch_threat_clearance=3.0,
        base_safety_fraction=0.05,
        anchor_min_angular_separation_deg=30.0,
        friendly_count_min=4,
        friendly_count_max=10,
        friendly_reserve_factor=1.2,
        terrain=TerrainGenerationParameters(
            water_fraction=0.24,
            forest_fraction=0.18,
            city_fraction=0.06,
            smoothing_passes=3,
            city_cluster_radius=2,
            building_height_min=4.0,
            building_height_max=14.0,
            minimum_land_component_cells=40,
            minimum_water_component_cells=50,
        ),
        max_placement_attempts=400,
        aaa_count=(0, 1),
        friendly_tank_count=(0, 1),
        enemy_tank_count=(0, 1),
        friendly_carrier_count=0,
        enemy_corvette_count=(0, 1),
        naval_enabled=True,
        corvette_defense_tier="STANDARD",
        sam_ir_fraction=0.40,
        jet_flare_fraction=0.85,
    ),
    "NIGHTMARE": DifficultyPresetRecord(
        objective_count=(7, 10),
        sam_count=(4, 6),
        jammer_count=(2, 4),
        objective_hp_min=3,
        objective_hp_max=7,
        cooperation_objective_fraction=0.60,
        group_style="CLUSTERED",
        allowed_group_templates=_WITH_SAM_PAIR,
        objective_distance_min_fraction=0.35,
        objective_distance_max_fraction=0.95,
        threat_overlap=0.65,
        launch_threat_clearance=1.0,
        base_safety_fraction=0.04,
        anchor_min_angular_separation_deg=25.0,
        friendly_count_min=5,
        friendly_count_max=12,
        friendly_reserve_factor=1.05,
        terrain=TerrainGenerationParameters(
            water_fraction=0.28,
            forest_fraction=0.18,
            city_fraction=0.05,
            smoothing_passes=3,
            city_cluster_radius=2,
            building_height_min=4.0,
            building_height_max=14.0,
            minimum_land_component_cells=50,
            minimum_water_component_cells=60,
        ),
        max_placement_attempts=500,
        aaa_count=(0, 2),
        friendly_tank_count=(0, 1),
        enemy_tank_count=(1, 2),
        friendly_carrier_count=(0, 1),
        enemy_corvette_count=(0, 2),
        naval_enabled=True,
        corvette_defense_tier="STANDARD",
        sam_ir_fraction=0.50,
        jet_flare_fraction=0.70,
    ),
}


def _draw_count(rng: random.Random, value: int | tuple[int, int]) -> int:
    if isinstance(value, tuple):
        low, high = value
        return rng.randint(low, high)
    return int(value)


def parameters_for_preset(
    preset: DifficultyPreset,
    *,
    seed: int,
    overrides: Mapping[str, object] | None = None,
) -> GenerationParameters:
    """Expand a named preset into validated ``GenerationParameters``.

    Scalar counts are drawn from inclusive preset ranges using
    ``random.Random(derived_seed(seed, PRESET_SALT))``. Any override forces
    ``preset="CUSTOM"`` while retaining all other resolved values. The frozen
    preset record is never mutated.
    """
    if preset == "CUSTOM":
        raise ValueError(
            "parameters_for_preset() requires TRAINING, STANDARD, CONTESTED, "
            "or NIGHTMARE; CUSTOM is only produced by overrides."
        )
    record = DIFFICULTY_PRESETS[preset]
    rng = random.Random(derived_seed(seed, PRESET_SALT))

    resolved: dict[str, Any] = {
        "seed": seed,
        "preset": preset,
        "objective_count": _draw_count(rng, record.objective_count),
        "sam_count": _draw_count(rng, record.sam_count),
        "jammer_count": _draw_count(rng, record.jammer_count),
        "objective_hp_min": record.objective_hp_min,
        "objective_hp_max": record.objective_hp_max,
        "cooperation_objective_fraction": record.cooperation_objective_fraction,
        "group_style": record.group_style,
        "allowed_group_templates": list(record.allowed_group_templates),
        "objective_distance_min_fraction": record.objective_distance_min_fraction,
        "objective_distance_max_fraction": record.objective_distance_max_fraction,
        "grid_width": record.grid_width,
        "grid_height": record.grid_height,
        "map_margin": record.map_margin,
        "base_safety_radius": record.base_safety_radius,
        "base_safety_fraction": record.base_safety_fraction,
        "anchor_min_angular_separation_deg": record.anchor_min_angular_separation_deg,
        "group_min_separation": record.group_min_separation,
        "entity_min_separation": record.entity_min_separation,
        "threat_overlap": record.threat_overlap,
        "launch_threat_clearance": record.launch_threat_clearance,
        "friendly_count_min": record.friendly_count_min,
        "friendly_count_max": record.friendly_count_max,
        "friendly_reserve_factor": record.friendly_reserve_factor,
        "max_placement_attempts": record.max_placement_attempts,
        "terrain": record.terrain.model_dump(),
        "aaa_count": _draw_count(rng, record.aaa_count),
        "friendly_tank_count": _draw_count(rng, record.friendly_tank_count),
        "enemy_tank_count": _draw_count(rng, record.enemy_tank_count),
        "friendly_carrier_count": _draw_count(rng, record.friendly_carrier_count),
        "enemy_corvette_count": _draw_count(rng, record.enemy_corvette_count),
        "carrier_base_mode": record.carrier_base_mode,
        "naval_enabled": record.naval_enabled,
        "corvette_defense_tier": record.corvette_defense_tier,
        "sam_ir_fraction": record.sam_ir_fraction,
        "jet_flare_fraction": record.jet_flare_fraction,
    }

    if overrides:
        for key, value in overrides.items():
            if key == "seed":
                raise ValueError(
                    "Do not override 'seed' via overrides; pass seed= explicitly."
                )
            if key == "preset":
                raise ValueError(
                    "Do not override 'preset' directly; any other override "
                    "automatically sets preset to CUSTOM."
                )
            resolved[key] = value
        resolved["preset"] = "CUSTOM"

    return GenerationParameters.model_validate(resolved)
