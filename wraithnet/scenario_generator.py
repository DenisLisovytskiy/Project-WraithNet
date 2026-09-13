"""Deterministic enemy placement and friendly roster generation (Phases 31–32, 35).

Pure simulation-core code: no viewer, filesystem, network, or wall-clock.

Discovery-chain rule (conservative fog-of-war):
``parent_child_dist <= SENSOR_RADIUS - max_weapon_range - DISCOVERY_MARGIN``,
city LOS must be clear, and the root center satisfies
``home_dist + LAUNCH_RING_OUTER_RADIUS <= SENSOR_RADIUS``.

Threat-overlap target (intra-group SAM pairs only):
``overlap = max(0, 1 - dist / (2 * SAM_ENGAGEMENT_RANGE))``, accepted within
``THREAT_OVERLAP_TOLERANCE`` of ``parameters.threat_overlap``.
"""

from __future__ import annotations

import math
import random
from dataclasses import dataclass, field
from typing import Callable

from pydantic import BaseModel

from wraithnet import config
from wraithnet.config import JetFrameType, TargetType, WeaponType
from wraithnet.generation_rng import (
    ENEMY_GROUND_SALT,
    ENEMY_NAVAL_SALT,
    ENEMY_SALT,
    FRIENDLY_GROUND_SALT,
    FRIENDLY_SALT,
    GENERATOR_VERSION,
    LOADOUT_SALT,
    TERRAIN_SALT,
    derived_seed,
)
from wraithnet.generation_schemas import (
    FeasibilityIssue,
    FeasibilityReport,
    GenerationMetadata,
    GenerationParameters,
    GroupStyle,
    GroupTemplateName,
)
from wraithnet.geometry import bearing_to
from wraithnet.scenario_feasibility import (
    analyze_feasibility,
    discovery_link_clear,
    has_discovery_los,
    jet_can_reach_round_trip,
    land_cells,
    round_trip_fuel_required,
    weapon_eligible_damage,
)
from wraithnet.aaa import AaaSiteSpec
from wraithnet.tanks import TankSpec, tanks_mutually_reachable
from wraithnet.scenario_schema import (
    JammerSpec,
    JetSpec,
    SamSiteSpec,
    ScenarioConfig,
    TargetSpec,
    new_scenario_data,
    scenario_from_data,
)
from wraithnet.terrain import (
    OutOfTerrainBoundsError,
    TerrainGrid,
    cell_center,
    is_passable,
    world_to_cell,
)
from wraithnet.terrain_generator import TerrainGenerationError, generate_terrain

LAUNCH_RING_INNER_RADIUS: float = 2.0
LAUNCH_RING_OUTER_RADIUS: float = 6.0
THREAT_MEMBER_MAX_OFFSET: float = 24.0
DISCOVERY_MARGIN: float = 2.0

# Absolute tolerance on the intra-group SAM overlap ratio.
THREAT_OVERLAP_TOLERANCE: float = 0.20

# Stable GenerationError codes.
PLACEMENT_EXHAUSTED = "PLACEMENT_EXHAUSTED"
IMPOSSIBLE_GEOMETRY = "IMPOSSIBLE_GEOMETRY"
COUNT_BUDGET_ERROR = "COUNT_BUDGET_ERROR"
AUTO_BALANCE_CAPACITY = "AUTO_BALANCE_CAPACITY"

# Launch-ring jet separation (ring is tight; entity_min_separation is often too large).
LAUNCH_JET_MIN_SEPARATION: float = 1.0

# Preferred weapons per destroyable type (deterministic order).
_WEAPON_PREFERENCE: dict[TargetType, tuple[WeaponType, ...]] = {
    "HARDENED_BUNKER": ("JDAM", "GUN_POD", "AGM65"),
    "MOBILE_RADAR": ("AGM65", "AGM88"),
    "SAM_SITE": ("JDAM", "AGM88", "AGM65", "GUN_POD"),
    "AAA_SITE": ("JDAM", "AGM65", "GUN_POD"),
    "GROUND_VEHICLE": ("AGM65", "JDAM", "GUN_POD"),
    "SURFACE_VESSEL": ("AGM84", "JDAM"),
}

_AUTO_REPAIR_ATTEMPTS: int = 64

# Template SAM/jammer consumption.
_TEMPLATE_COST: dict[GroupTemplateName, tuple[int, int]] = {
    "OBJECTIVE_ONLY": (0, 0),
    "DEFENDED_OBJECTIVE": (1, 0),
    "JAMMED_OBJECTIVE": (0, 1),
    "IADS_CLUSTER": (1, 1),
    "SAM_PAIR": (2, 0),
}

# Attempt counter hook for tests (incremented once per candidate try).
_placement_attempt_hook: Callable[[], None] | None = None


class GenerationError(ValueError):
    """Structured generation failure with a stable ``code`` and ``details``."""

    def __init__(self, code: str, details: dict[str, object]) -> None:
        self.code = code
        self.details = details
        super().__init__(self._format_message())

    def _format_message(self) -> str:
        kind = self.details.get("kind", "placement")
        suggestions = self.details.get("suggestions", [])
        suggestion_text = ""
        if isinstance(suggestions, (list, tuple)) and suggestions:
            suggestion_text = " Suggestions: " + "; ".join(str(s) for s in suggestions)
        return (
            f"Scenario generation failed ({self.code}) while placing {kind}."
            f"{suggestion_text}"
        )


@dataclass(frozen=True)
class EnemyGroup:
    group_id: str
    template: GroupTemplateName
    center: tuple[float, float]
    target_ids: tuple[str, ...]
    sam_site_ids: tuple[str, ...]
    jammer_ids: tuple[str, ...]
    discovery_parent_group_id: str | None = None


@dataclass(frozen=True)
class EnemyGeneration:
    home_base: tuple[float, float]
    targets: tuple[TargetSpec, ...]
    sam_sites: tuple[SamSiteSpec, ...]
    jammers: tuple[JammerSpec, ...]
    groups: tuple[EnemyGroup, ...]
    cooperation_candidate_ids: tuple[str, ...]
    terrain: TerrainGrid
    aaa_sites: tuple[AaaSiteSpec, ...] = ()


@dataclass
class _CenterRecord:
    position: tuple[float, float]
    style: GroupStyle
    parent_index: int | None
    is_anchor: bool = False


def distance_from_home(
    home_base: tuple[float, float], position: tuple[float, float]
) -> float:
    return math.dist(home_base, position)


def position_in_bounds(
    position: tuple[float, float],
    *,
    margin: float,
    grid_width: float = config.GRID_WIDTH,
    grid_height: float = config.GRID_HEIGHT,
) -> bool:
    x, y = position
    return (
        margin <= x <= grid_width - margin and margin <= y <= grid_height - margin
    )


def position_respects_separation(
    position: tuple[float, float],
    others: list[tuple[float, float]],
    min_separation: float,
) -> bool:
    return all(math.dist(position, other) >= min_separation for other in others)


def discovery_link_max_distance() -> float:
    max_weapon = max(spec.range for spec in config.WEAPON_CATALOG.values())
    return config.SENSOR_RADIUS - max_weapon - DISCOVERY_MARGIN


def max_normalized_distance(home_base: tuple[float, float]) -> float:
    """Farthest in-bounds corner distance from home (normalization denominator)."""
    corners = (
        (0.0, 0.0),
        (config.GRID_WIDTH, 0.0),
        (0.0, config.GRID_HEIGHT),
        (config.GRID_WIDTH, config.GRID_HEIGHT),
    )
    return max(math.dist(home_base, corner) for corner in corners) or 1.0


def sam_min_distance_from_home(parameters: GenerationParameters) -> float:
    return (
        config.SAM_ENGAGEMENT_RANGE
        + LAUNCH_RING_OUTER_RADIUS
        + parameters.launch_threat_clearance
    )


def threat_overlap_ratio(distance: float, radius_a: float, radius_b: float) -> float:
    denom = radius_a + radius_b
    if denom <= 0.0:
        return 0.0
    return max(0.0, 1.0 - distance / denom)


def _note_attempt() -> None:
    if _placement_attempt_hook is not None:
        _placement_attempt_hook()


def _format_id(prefix: str, index: int) -> str:
    return f"{prefix}_{index:02d}"


def _is_ground_land(terrain: TerrainGrid, position: tuple[float, float]) -> bool:
    try:
        return is_passable(terrain, position, "GROUND")
    except OutOfTerrainBoundsError:
        return False


def _project_to_land(
    terrain: TerrainGrid,
    position: tuple[float, float],
    land: list[tuple[int, int]],
) -> tuple[float, float] | None:
    """Keep on-land positions; otherwise snap to the nearest land cell center."""
    if not land:
        return None
    if _is_ground_land(terrain, position):
        return position
    best: tuple[float, float] | None = None
    best_d = float("inf")
    px, py = position
    for col, row in land:
        cx, cy = cell_center(terrain, col, row)
        d = (cx - px) * (cx - px) + (cy - py) * (cy - py)
        if d < best_d:
            best_d = d
            best = (cx, cy)
    return best


def _jitter_in_cell(
    terrain: TerrainGrid,
    col: int,
    row: int,
    rng: random.Random,
) -> tuple[float, float]:
    """Small deterministic jitter that stays inside the cell AABB."""
    x0 = col * terrain.cell_size
    y0 = row * terrain.cell_size
    x1 = min((col + 1) * terrain.cell_size, terrain.world_width)
    y1 = min((row + 1) * terrain.cell_size, terrain.world_height)
    pad_x = max(0.0, (x1 - x0) * 0.15)
    pad_y = max(0.0, (y1 - y0) * 0.15)
    return (
        rng.uniform(x0 + pad_x, x1 - pad_x) if x1 - x0 > 2 * pad_x else (x0 + x1) / 2.0,
        rng.uniform(y0 + pad_y, y1 - pad_y) if y1 - y0 > 2 * pad_y else (y0 + y1) / 2.0,
    )


def _land_position_from_sample(
    terrain: TerrainGrid,
    sample: tuple[float, float],
    land: list[tuple[int, int]],
    rng: random.Random,
    *,
    prefer_plains: bool = True,
) -> tuple[float, float] | None:
    """Snap a sample onto land; prefer PLAINS/FOREST over CITY for spawn points."""
    if not land:
        return None
    if _is_ground_land(terrain, sample):
        try:
            col, row = world_to_cell(terrain, sample[0], sample[1])
        except OutOfTerrainBoundsError:
            return None
        if prefer_plains and terrain.cells[row][col].terrain_type == "CITY":
            # Try a nearby non-city land cell first.
            nearby = [
                (c, r)
                for c, r in land
                if max(abs(c - col), abs(r - row)) <= 2
                and terrain.cells[r][c].terrain_type != "CITY"
            ]
            if nearby:
                c, r = nearby[rng.randrange(len(nearby))]
                return _jitter_in_cell(terrain, c, r, rng)
        return _jitter_in_cell(terrain, col, row, rng)

    # Nearest non-city land if possible, else any land.
    px, py = sample
    best_plain: tuple[float, float] | None = None
    best_plain_d = float("inf")
    best_any: tuple[float, float] | None = None
    best_any_d = float("inf")
    for col, row in land:
        cx, cy = cell_center(terrain, col, row)
        d = (cx - px) * (cx - px) + (cy - py) * (cy - py)
        if d < best_any_d:
            best_any_d = d
            best_any = (col, row)
        if prefer_plains and terrain.cells[row][col].terrain_type != "CITY" and d < best_plain_d:
            best_plain_d = d
            best_plain = (col, row)
    chosen = best_plain if best_plain is not None else best_any
    if chosen is None:
        return None
    return _jitter_in_cell(terrain, chosen[0], chosen[1], rng)


def _root_approach_point(
    home_base: tuple[float, float], center: tuple[float, float]
) -> tuple[float, float]:
    dist = math.dist(home_base, center)
    if dist <= 1e-9:
        return home_base
    t = min(1.0, LAUNCH_RING_OUTER_RADIUS / dist)
    return (
        home_base[0] + (center[0] - home_base[0]) * t,
        home_base[1] + (center[1] - home_base[1]) * t,
    )


def _choose_home_base(
    parameters: GenerationParameters,
    rng: random.Random,
    terrain: TerrainGrid,
    land: list[tuple[int, int]],
) -> tuple[float, float]:
    inset = max(parameters.map_margin, LAUNCH_RING_OUTER_RADIUS)
    if 2.0 * inset >= config.GRID_WIDTH or 2.0 * inset >= config.GRID_HEIGHT:
        raise GenerationError(
            IMPOSSIBLE_GEOMETRY,
            {
                "kind": "home_base",
                "seed": parameters.seed,
                "requested_counts": {
                    "objective_count": parameters.objective_count,
                    "sam_count": parameters.sam_count,
                    "jammer_count": parameters.jammer_count,
                },
                "grid": (config.GRID_WIDTH, config.GRID_HEIGHT),
                "active_constraints": {"inset": inset, "map_margin": parameters.map_margin},
                "suggestions": ["reduce map_margin"],
            },
        )
    corners = [
        (inset, inset),
        (config.GRID_WIDTH - inset, inset),
        (inset, config.GRID_HEIGHT - inset),
        (config.GRID_WIDTH - inset, config.GRID_HEIGHT - inset),
    ]
    rng.shuffle(corners)
    for corner in corners:
        pos = _land_position_from_sample(terrain, corner, land, rng)
        if pos is None:
            continue
        if not position_in_bounds(pos, margin=0.0):
            continue
        # Prefer bases that stay near the intended corner inset region.
        if math.dist(pos, corner) <= max(terrain.cell_size * 3.0, inset * 0.5):
            return pos
    # Fallback: any land cell near a corner band.
    candidates = []
    for col, row in land:
        cx, cy = cell_center(terrain, col, row)
        if min(cx, cy, config.GRID_WIDTH - cx, config.GRID_HEIGHT - cy) >= inset * 0.5:
            candidates.append((cx, cy))
    if not candidates:
        raise GenerationError(
            PLACEMENT_EXHAUSTED,
            {
                "kind": "home_base",
                "seed": parameters.seed,
                "requested_counts": {},
                "grid": (config.GRID_WIDTH, config.GRID_HEIGHT),
                "active_constraints": {"land_cells": len(land)},
                "suggestions": [
                    "reduce water_fraction",
                    "increase minimum_land_component_cells coverage",
                ],
            },
        )
    rng.shuffle(candidates)
    return candidates[0]


def _distance_fraction_ok(
    home_base: tuple[float, float],
    position: tuple[float, float],
    parameters: GenerationParameters,
) -> bool:
    max_dist = max_normalized_distance(home_base)
    frac = distance_from_home(home_base, position) / max_dist
    return (
        parameters.objective_distance_min_fraction
        <= frac
        <= parameters.objective_distance_max_fraction
    )


def _center_candidate_ok(
    position: tuple[float, float],
    *,
    home_base: tuple[float, float],
    parameters: GenerationParameters,
    existing: list[tuple[float, float]],
    min_pairwise: float,
    require_root_sensor: bool,
    terrain: TerrainGrid,
) -> bool:
    if not position_in_bounds(position, margin=parameters.map_margin):
        return False
    if not _is_ground_land(terrain, position):
        return False
    if distance_from_home(home_base, position) < parameters.base_safety_radius:
        return False
    if not _distance_fraction_ok(home_base, position, parameters):
        return False
    if require_root_sensor:
        if (
            distance_from_home(home_base, position) + LAUNCH_RING_OUTER_RADIUS
            > config.SENSOR_RADIUS
        ):
            return False
        approach = _root_approach_point(home_base, position)
        if not has_discovery_los(terrain, approach, position):
            return False
    if not position_respects_separation(position, existing, min_pairwise):
        return False
    if math.dist(position, home_base) < 1e-6:
        return False
    return True


def _sample_in_annulus(
    center: tuple[float, float],
    radius_min: float,
    radius_max: float,
    rng: random.Random,
) -> tuple[float, float]:
    if radius_max < radius_min:
        radius_min, radius_max = radius_max, radius_min
    # Sample radius with area-uniform distribution.
    u = rng.random()
    radius = math.sqrt(u * (radius_max**2 - radius_min**2) + radius_min**2)
    angle = rng.uniform(0.0, 2.0 * math.pi)
    return (
        center[0] + radius * math.cos(angle),
        center[1] + radius * math.sin(angle),
    )


def _assign_center_styles(
    style: GroupStyle, count: int
) -> list[GroupStyle]:
    if count <= 0:
        return []
    if style == "DISPERSED":
        return ["DISPERSED"] * count
    if style == "CLUSTERED":
        return ["CLUSTERED"] * count
    # MIXED: roughly half each; index 0 (root) counts toward dispersed preference.
    dispersed_n = (count + 1) // 2
    return ["DISPERSED"] * dispersed_n + ["CLUSTERED"] * (count - dispersed_n)


def _pairwise_min_for_style(style: GroupStyle, group_min_separation: float) -> float:
    if style == "DISPERSED":
        return 1.5 * group_min_separation
    return group_min_separation


def _place_group_centers(
    parameters: GenerationParameters,
    home_base: tuple[float, float],
    rng: random.Random,
    terrain: TerrainGrid,
    land: list[tuple[int, int]],
) -> list[_CenterRecord]:
    n = parameters.objective_count
    if n < 1:
        raise GenerationError(
            COUNT_BUDGET_ERROR,
            {
                "kind": "objective_centers",
                "seed": parameters.seed,
                "requested_counts": {"objective_count": n},
                "grid": (config.GRID_WIDTH, config.GRID_HEIGHT),
                "active_constraints": {},
                "suggestions": ["set objective_count >= 1"],
            },
        )

    styles = _assign_center_styles(parameters.group_style, n)
    link_max = discovery_link_max_distance()
    if link_max <= parameters.entity_min_separation:
        raise GenerationError(
            IMPOSSIBLE_GEOMETRY,
            {
                "kind": "discovery_link",
                "seed": parameters.seed,
                "requested_counts": {"objective_count": n},
                "grid": (config.GRID_WIDTH, config.GRID_HEIGHT),
                "active_constraints": {"link_max": link_max},
                "suggestions": [
                    "increase SENSOR_RADIUS",
                    "reduce max weapon range used in discovery margin",
                ],
            },
        )

    # Pre-check: root annulus must be non-empty.
    # Distance fractions are relative to map diagonal, but discovery still
    # caps root_max at SENSOR_RADIUS. On large grids, frac_min * diagonal can
    # exceed that cap — clamp root_min into the discoverable annulus.
    max_dist = max_normalized_distance(home_base)
    sensor_cap = config.SENSOR_RADIUS - LAUNCH_RING_OUTER_RADIUS
    root_max = min(
        parameters.objective_distance_max_fraction * max_dist,
        sensor_cap,
    )
    root_min = max(
        parameters.base_safety_radius,
        parameters.objective_distance_min_fraction * max_dist,
    )
    if root_min > root_max:
        # Keep relative spacing inside the sensor-capped band.
        band_lo = parameters.base_safety_radius
        if root_max <= band_lo:
            root_min = root_max
        else:
            frac = parameters.objective_distance_min_fraction / max(
                parameters.objective_distance_max_fraction, 1e-9
            )
            root_min = band_lo + frac * (root_max - band_lo)
    if root_max < root_min:
        raise GenerationError(
            IMPOSSIBLE_GEOMETRY,
            {
                "kind": "root_center",
                "seed": parameters.seed,
                "requested_counts": {"objective_count": n},
                "grid": (config.GRID_WIDTH, config.GRID_HEIGHT),
                "active_constraints": {
                    "root_min": root_min,
                    "root_max": root_max,
                    "base_safety_radius": parameters.base_safety_radius,
                    "sensor_cap": sensor_cap,
                },
                "suggestions": [
                    "widen objective_distance fractions",
                    "reduce base_safety_radius",
                ],
            },
        )

    records: list[_CenterRecord] = []
    positions: list[tuple[float, float]] = []

    def try_place_root() -> bool:
        for _ in range(parameters.max_placement_attempts):
            _note_attempt()
            cand = _sample_in_annulus(home_base, root_min, root_max, rng)
            cand = _land_position_from_sample(terrain, cand, land, rng)
            if cand is None:
                continue
            pairwise = _pairwise_min_for_style(styles[0], parameters.group_min_separation)
            if _center_candidate_ok(
                cand,
                home_base=home_base,
                parameters=parameters,
                existing=positions,
                min_pairwise=pairwise,
                require_root_sensor=True,
                terrain=terrain,
            ):
                records.append(
                    _CenterRecord(
                        position=cand,
                        style=styles[0],
                        parent_index=None,
                        is_anchor=True,
                    )
                )
                positions.append(cand)
                return True
        # Deterministic grid fallback for the root.
        return _grid_fallback_root()

    def _grid_fallback_root() -> bool:
        step = max(2.0, parameters.entity_min_separation)
        x = parameters.map_margin
        while x <= config.GRID_WIDTH - parameters.map_margin:
            y = parameters.map_margin
            while y <= config.GRID_HEIGHT - parameters.map_margin:
                _note_attempt()
                cand = (float(x), float(y))
                cand = _land_position_from_sample(terrain, cand, land, rng)
                if cand is None:
                    y += step
                    continue
                pairwise = _pairwise_min_for_style(
                    styles[0], parameters.group_min_separation
                )
                if _center_candidate_ok(
                    cand,
                    home_base=home_base,
                    parameters=parameters,
                    existing=positions,
                    min_pairwise=pairwise,
                    require_root_sensor=True,
                    terrain=terrain,
                ):
                    records.append(
                        _CenterRecord(
                            position=cand,
                            style=styles[0],
                            parent_index=None,
                            is_anchor=True,
                        )
                    )
                    positions.append(cand)
                    return True
                y += step
            x += step
        return False

    if not try_place_root():
        raise GenerationError(
            PLACEMENT_EXHAUSTED,
            {
                "kind": "root_center",
                "seed": parameters.seed,
                "requested_counts": {
                    "objective_count": parameters.objective_count,
                    "sam_count": parameters.sam_count,
                    "jammer_count": parameters.jammer_count,
                },
                "grid": (config.GRID_WIDTH, config.GRID_HEIGHT),
                "active_constraints": {
                    "base_safety_radius": parameters.base_safety_radius,
                    "group_min_separation": parameters.group_min_separation,
                    "map_margin": parameters.map_margin,
                },
                "suggestions": [
                    "reduce enemy count or group separation",
                    "widen objective distance band",
                ],
            },
        )

    # Anchors for clustered members: 1–3 of the eventual centers.
    n_anchors = 1
    if n > 1 and parameters.group_style in ("CLUSTERED", "MIXED"):
        n_anchors = min(3, n)
        if parameters.group_style == "CLUSTERED":
            n_anchors = rng.randint(1, min(3, n))

    for index in range(1, n):
        style = styles[index]
        placed = False
        pairwise = _pairwise_min_for_style(style, parameters.group_min_separation)

        for _ in range(parameters.max_placement_attempts):
            _note_attempt()
            parent_index = rng.randrange(len(records))
            parent_pos = records[parent_index].position

            if style == "CLUSTERED":
                anchor_index = rng.randrange(min(n_anchors, len(records)))
                anchor_pos = records[anchor_index].position
                # Prefer discovery parent = nearest existing that can link; sample
                # near anchor within cluster band, then require a discovery parent.
                band_lo = 0.5 * parameters.group_min_separation
                band_hi = 1.0 * parameters.group_min_separation
                cand = _sample_in_annulus(anchor_pos, band_lo, band_hi, rng)
            else:
                # DISPERSED: sample within discovery link of a random parent.
                inner = max(pairwise * 0.5, parameters.entity_min_separation)
                outer = link_max
                if outer < inner:
                    continue
                cand = _sample_in_annulus(parent_pos, inner, outer, rng)

            cand = _land_position_from_sample(terrain, cand, land, rng)
            if cand is None:
                continue
            if not _center_candidate_ok(
                cand,
                home_base=home_base,
                parameters=parameters,
                existing=positions,
                min_pairwise=pairwise,
                require_root_sensor=False,
                terrain=terrain,
            ):
                continue

            # Must attach to some existing center within link_max + LOS.
            parent_index = _nearest_linkable_parent(
                cand, positions, link_max, terrain=terrain
            )
            if parent_index is None:
                continue

            if style == "CLUSTERED":
                # Enforce cluster-band vs at least one anchor.
                anchors = positions[: min(n_anchors, len(positions))]
                if not any(
                    0.5 * parameters.group_min_separation
                    <= math.dist(cand, anchor)
                    <= 1.0 * parameters.group_min_separation
                    + 1e-6
                    for anchor in anchors
                ):
                    # Allow slightly looser if still within group_min of an anchor
                    # after discovery-forced placement — reject if nowhere near.
                    if not any(
                        math.dist(cand, anchor)
                        <= 1.0 * parameters.group_min_separation + THREAT_OVERLAP_TOLERANCE
                        for anchor in anchors
                    ):
                        continue

            records.append(
                _CenterRecord(
                    position=cand,
                    style=style,
                    parent_index=parent_index,
                    is_anchor=len(records) < n_anchors,
                )
            )
            positions.append(cand)
            placed = True
            break

        if not placed:
            # Grid fallback around existing parents.
            placed = _grid_fallback_child(
                records=records,
                positions=positions,
                styles=styles,
                index=index,
                parameters=parameters,
                home_base=home_base,
                link_max=link_max,
                pairwise=pairwise,
                n_anchors=n_anchors,
                terrain=terrain,
                land=land,
            )

        if not placed:
            raise GenerationError(
                PLACEMENT_EXHAUSTED,
                {
                    "kind": "group_center",
                    "seed": parameters.seed,
                    "requested_counts": {
                        "objective_count": parameters.objective_count,
                        "sam_count": parameters.sam_count,
                        "jammer_count": parameters.jammer_count,
                        "placed_centers": len(records),
                    },
                    "grid": (config.GRID_WIDTH, config.GRID_HEIGHT),
                    "active_constraints": {
                        "group_style": parameters.group_style,
                        "group_min_separation": parameters.group_min_separation,
                        "link_max": link_max,
                        "base_safety_radius": parameters.base_safety_radius,
                        "land_cells": len(land),
                    },
                    "suggestions": [
                        "reduce enemy count or group separation",
                        "reduce city_fraction / water_fraction",
                        "use MIXED/CLUSTERED with lower separation",
                    ],
                },
            )

    return records


def _nearest_linkable_parent(
    cand: tuple[float, float],
    positions: list[tuple[float, float]],
    link_max: float,
    *,
    terrain: TerrainGrid,
) -> int | None:
    best_i: int | None = None
    best_d = float("inf")
    for i, pos in enumerate(positions):
        d = math.dist(cand, pos)
        if d <= link_max and d < best_d and discovery_link_clear(
            terrain, cand, pos, link_max=link_max
        ):
            best_d = d
            best_i = i
    return best_i


def _grid_fallback_child(
    *,
    records: list[_CenterRecord],
    positions: list[tuple[float, float]],
    styles: list[GroupStyle],
    index: int,
    parameters: GenerationParameters,
    home_base: tuple[float, float],
    link_max: float,
    pairwise: float,
    n_anchors: int,
    terrain: TerrainGrid,
    land: list[tuple[int, int]],
) -> bool:
    step = max(2.0, parameters.entity_min_separation)
    style = styles[index]
    x = parameters.map_margin
    while x <= config.GRID_WIDTH - parameters.map_margin + 1e-9:
        y = parameters.map_margin
        while y <= config.GRID_HEIGHT - parameters.map_margin + 1e-9:
            _note_attempt()
            projected = _project_to_land(terrain, (float(x), float(y)), land)
            y += step
            if projected is None:
                continue
            cand = projected
            if not _center_candidate_ok(
                cand,
                home_base=home_base,
                parameters=parameters,
                existing=positions,
                min_pairwise=pairwise,
                require_root_sensor=False,
                terrain=terrain,
            ):
                continue
            parent_index = _nearest_linkable_parent(
                cand, positions, link_max, terrain=terrain
            )
            if parent_index is None:
                continue
            records.append(
                _CenterRecord(
                    position=cand,
                    style=style,
                    parent_index=parent_index,
                    is_anchor=len(records) < n_anchors,
                )
            )
            positions.append(cand)
            return True
        x += step
    return False


def _template_fits(
    template: GroupTemplateName, sam_left: int, jammer_left: int
) -> bool:
    sam_cost, jam_cost = _TEMPLATE_COST[template]
    return sam_cost <= sam_left and jam_cost <= jammer_left


def _choose_templates(
    parameters: GenerationParameters, rng: random.Random
) -> tuple[list[GroupTemplateName], int, int]:
    """Return primary templates (len == objective_count) and leftover sam/jammer."""
    allowed = list(parameters.allowed_group_templates)
    if not allowed:
        raise GenerationError(
            COUNT_BUDGET_ERROR,
            {
                "kind": "templates",
                "seed": parameters.seed,
                "requested_counts": {},
                "grid": (config.GRID_WIDTH, config.GRID_HEIGHT),
                "active_constraints": {},
                "suggestions": ["provide allowed_group_templates"],
            },
        )

    sam_left = parameters.sam_count
    jam_left = parameters.jammer_count
    chosen: list[GroupTemplateName] = []

    for _ in range(parameters.objective_count):
        candidates = [t for t in allowed if _template_fits(t, sam_left, jam_left)]
        if not candidates:
            raise GenerationError(
                COUNT_BUDGET_ERROR,
                {
                    "kind": "templates",
                    "seed": parameters.seed,
                    "requested_counts": {
                        "objective_count": parameters.objective_count,
                        "sam_count": parameters.sam_count,
                        "jammer_count": parameters.jammer_count,
                    },
                    "grid": (config.GRID_WIDTH, config.GRID_HEIGHT),
                    "active_constraints": {
                        "sam_left": sam_left,
                        "jammer_left": jam_left,
                        "allowed": list(allowed),
                    },
                    "suggestions": [
                        "include OBJECTIVE_ONLY in allowed_group_templates",
                        "reduce objective_count relative to threat budgets",
                    ],
                },
            )
        # Prefer templates that spend remaining budget when possible.
        spending = [
            t
            for t in candidates
            if _TEMPLATE_COST[t] != (0, 0)
            and (sam_left > 0 or jam_left > 0)
        ]
        pool = spending if spending and (sam_left > 0 or jam_left > 0) else candidates
        # If budget remains high, bias toward heavier templates deterministically.
        template = rng.choice(pool)
        sam_cost, jam_cost = _TEMPLATE_COST[template]
        sam_left -= sam_cost
        jam_left -= jam_cost
        chosen.append(template)

    return chosen, sam_left, jam_left


def _objective_types(count: int, rng: random.Random) -> list[TargetType]:
    if count <= 0:
        return []
    if count == 1:
        return [rng.choice(["HARDENED_BUNKER", "MOBILE_RADAR"])]
    # Guarantee both types when count >= 2; fill the rest randomly.
    types: list[TargetType] = ["HARDENED_BUNKER", "MOBILE_RADAR"]
    for _ in range(count - 2):
        types.append(rng.choice(["HARDENED_BUNKER", "MOBILE_RADAR"]))
    rng.shuffle(types)
    return types


def _place_member_near_center(
    center: tuple[float, float],
    *,
    home_base: tuple[float, float],
    parameters: GenerationParameters,
    existing: list[tuple[float, float]],
    rng: random.Random,
    terrain: TerrainGrid,
    land: list[tuple[int, int]],
    desired_distance: float | None = None,
    enforce_sam_clearance: bool = False,
) -> tuple[float, float]:
    sam_clearance = sam_min_distance_from_home(parameters)
    for _ in range(parameters.max_placement_attempts):
        _note_attempt()
        if desired_distance is None:
            radius = rng.uniform(
                parameters.entity_min_separation,
                THREAT_MEMBER_MAX_OFFSET,
            )
        else:
            jitter = rng.uniform(-1.5, 1.5)
            radius = min(
                THREAT_MEMBER_MAX_OFFSET,
                max(parameters.entity_min_separation, desired_distance + jitter),
            )
        angle = rng.uniform(0.0, 2.0 * math.pi)
        sample = (
            center[0] + radius * math.cos(angle),
            center[1] + radius * math.sin(angle),
        )
        cand = _land_position_from_sample(terrain, sample, land, rng)
        if cand is None:
            continue
        if not position_in_bounds(cand, margin=parameters.map_margin):
            continue
        if math.dist(cand, home_base) < 1e-6:
            continue
        if not position_respects_separation(
            cand, existing, parameters.entity_min_separation
        ):
            continue
        if enforce_sam_clearance and distance_from_home(home_base, cand) < sam_clearance:
            continue
        # Offset must not exceed max from center.
        if math.dist(cand, center) > THREAT_MEMBER_MAX_OFFSET + 1e-6:
            continue
        return cand

    # Grid fallback around center.
    step = max(1.0, parameters.entity_min_separation)
    radius = (
        parameters.entity_min_separation
        if desired_distance is None
        else min(THREAT_MEMBER_MAX_OFFSET, max(parameters.entity_min_separation, desired_distance))
    )
    angle = 0.0
    while angle < 2.0 * math.pi - 1e-9:
        _note_attempt()
        sample = (
            center[0] + radius * math.cos(angle),
            center[1] + radius * math.sin(angle),
        )
        angle += step / max(radius, 1.0)
        cand = _project_to_land(terrain, sample, land)
        if cand is None:
            continue
        if not position_in_bounds(cand, margin=parameters.map_margin):
            continue
        if not position_respects_separation(
            cand, existing, parameters.entity_min_separation
        ):
            continue
        if enforce_sam_clearance and distance_from_home(home_base, cand) < sam_clearance:
            continue
        if math.dist(cand, center) > THREAT_MEMBER_MAX_OFFSET + 1e-6:
            continue
        return cand

    # Land-cell bucket fallback: scan precomputed land near the center.
    try:
        c0, r0 = world_to_cell(terrain, center[0], center[1])
    except OutOfTerrainBoundsError:
        c0, r0 = 0, 0
    nearby = sorted(
        land,
        key=lambda cr: max(abs(cr[0] - c0), abs(cr[1] - r0)),
    )
    for col, row in nearby:
        _note_attempt()
        cheb = max(abs(col - c0), abs(row - r0))
        if cheb > int(THREAT_MEMBER_MAX_OFFSET / terrain.cell_size) + 2:
            break
        cand = cell_center(terrain, col, row)
        if cheb == 0:
            # Center cell already occupied by the canonical entity — skip.
            continue
        if not position_in_bounds(cand, margin=parameters.map_margin):
            continue
        if not position_respects_separation(
            cand, existing, parameters.entity_min_separation * 0.75
        ):
            continue
        if enforce_sam_clearance and distance_from_home(home_base, cand) < sam_clearance:
            continue
        if math.dist(cand, center) > THREAT_MEMBER_MAX_OFFSET + terrain.cell_size:
            continue
        return cand

    raise GenerationError(
        PLACEMENT_EXHAUSTED,
        {
            "kind": "group_member",
            "seed": parameters.seed,
            "requested_counts": {},
            "grid": (config.GRID_WIDTH, config.GRID_HEIGHT),
            "active_constraints": {
                "center": center,
                "entity_min_separation": parameters.entity_min_separation,
                "sam_clearance": sam_clearance if enforce_sam_clearance else None,
                "land_cells": len(land),
            },
            "suggestions": [
                "reduce entity_min_separation",
                "reduce water_fraction / city coverage near groups",
                "reduce threat_overlap pressure",
            ],
        },
    )


def _desired_sam_pair_distance(parameters: GenerationParameters) -> float:
    """Distance between two SAM centers targeting ``threat_overlap``."""
    # overlap = 1 - d/(2R)  =>  d = 2R * (1 - overlap)
    return 2.0 * config.SAM_ENGAGEMENT_RANGE * (1.0 - parameters.threat_overlap)


def generate_enemies(parameters: GenerationParameters) -> EnemyGeneration:
    """Place home base, objectives, SAMs, and jammers deterministically."""
    try:
        terrain = generate_terrain(
            config.GRID_WIDTH,
            config.GRID_HEIGHT,
            parameters.terrain,
            seed=derived_seed(parameters.seed, TERRAIN_SALT),
        )
    except TerrainGenerationError as exc:
        raise GenerationError(
            exc.code,
            {
                "kind": "terrain",
                "seed": parameters.seed,
                "requested_counts": {
                    "objective_count": parameters.objective_count,
                    "sam_count": parameters.sam_count,
                    "jammer_count": parameters.jammer_count,
                },
                "grid": (config.GRID_WIDTH, config.GRID_HEIGHT),
                "active_constraints": dict(exc.details),
                "suggestions": list(exc.details.get("suggestions", [])),
            },
        ) from exc

    land = land_cells(terrain)
    if not land:
        raise GenerationError(
            PLACEMENT_EXHAUSTED,
            {
                "kind": "terrain",
                "seed": parameters.seed,
                "requested_counts": {},
                "grid": (config.GRID_WIDTH, config.GRID_HEIGHT),
                "active_constraints": {"land_cells": 0},
                "suggestions": ["reduce water_fraction"],
            },
        )

    rng = random.Random(derived_seed(parameters.seed, ENEMY_SALT))
    home_base = _choose_home_base(parameters, rng, terrain, land)
    centers = _place_group_centers(parameters, home_base, rng, terrain, land)
    templates, sam_left, jam_left = _choose_templates(parameters, rng)
    obj_types = _objective_types(parameters.objective_count, rng)

    targets: list[TargetSpec] = []
    sam_sites: list[SamSiteSpec] = []
    jammers: list[JammerSpec] = []
    groups: list[EnemyGroup] = []
    occupied: list[tuple[float, float]] = [home_base]

    target_seq = 1
    sam_seq = 1
    jammer_seq = 1
    group_seq = 1

    group_ids: list[str] = []

    for i, center_rec in enumerate(centers):
        template = templates[i]
        center = center_rec.position
        group_id = _format_id("Group", group_seq)
        group_seq += 1
        group_ids.append(group_id)

        parent_group_id = None
        if center_rec.parent_index is not None:
            parent_group_id = group_ids[center_rec.parent_index]

        # Canonical objective at the center.
        target_id = _format_id("Target", target_seq)
        target_seq += 1
        hp = rng.randint(parameters.objective_hp_min, parameters.objective_hp_max)
        targets.append(
            TargetSpec(
                target_id=target_id,
                position=center,
                hp_required=hp,
                target_type=obj_types[i],
            )
        )
        occupied.append(center)

        group_target_ids = [target_id]
        group_sam_ids: list[str] = []
        group_jam_ids: list[str] = []

        sam_cost, jam_cost = _TEMPLATE_COST[template]

        if template == "SAM_PAIR":
            desired = _desired_sam_pair_distance(parameters)
            half = min(
                THREAT_MEMBER_MAX_OFFSET,
                max(parameters.entity_min_separation, desired / 2.0),
            )
            sam_positions: list[tuple[float, float]] | None = None
            for _ in range(parameters.max_placement_attempts):
                _note_attempt()
                angle = rng.uniform(0.0, 2.0 * math.pi)
                candidates = [
                    (
                        center[0] + sign * half * math.cos(angle),
                        center[1] + sign * half * math.sin(angle),
                    )
                    for sign in (1.0, -1.0)
                ]
                ok = True
                tentative_occupied = list(occupied)
                for cand in candidates:
                    if not position_in_bounds(cand, margin=parameters.map_margin):
                        ok = False
                        break
                    if not _is_ground_land(terrain, cand):
                        ok = False
                        break
                    if not position_respects_separation(
                        cand, tentative_occupied, parameters.entity_min_separation
                    ):
                        ok = False
                        break
                    if distance_from_home(home_base, cand) < sam_min_distance_from_home(
                        parameters
                    ):
                        ok = False
                        break
                    if math.dist(cand, center) > THREAT_MEMBER_MAX_OFFSET + 1e-6:
                        ok = False
                        break
                    tentative_occupied.append(cand)
                if ok:
                    sam_positions = candidates
                    break

            if sam_positions is None:
                # Last resort: place sequentially near center (overlap soft).
                sam_positions = []
                for i in range(2):
                    desired_i = desired if i == 1 else half
                    pos = _place_member_near_center(
                        center if i == 0 else sam_positions[0],
                        home_base=home_base,
                        parameters=parameters,
                        existing=occupied + sam_positions,
                        rng=rng,
                        desired_distance=desired_i,
                        enforce_sam_clearance=True,
                        terrain=terrain,
                        land=land,
                    )
                    if math.dist(pos, center) > THREAT_MEMBER_MAX_OFFSET:
                        pos = _place_member_near_center(
                            center,
                            home_base=home_base,
                            parameters=parameters,
                            existing=occupied + sam_positions,
                            rng=rng,
                            desired_distance=half,
                            enforce_sam_clearance=True,
                        terrain=terrain,
                        land=land,
                    )
                    sam_positions.append(pos)

            for pos in sam_positions:
                sam_id = _format_id("SAM", sam_seq)
                sam_seq += 1
                sam_sites.append(
                    SamSiteSpec(
                        target_id=sam_id,
                        position=pos,
                        hp_required=1,
                        target_type="SAM_SITE",
                    )
                )
                group_sam_ids.append(sam_id)
                occupied.append(pos)
        else:
            for _ in range(sam_cost):
                desired: float | None = None
                if group_sam_ids:
                    desired = _desired_sam_pair_distance(parameters)
                pos = _place_member_near_center(
                    center,
                    home_base=home_base,
                    parameters=parameters,
                    existing=occupied,
                    rng=rng,
                    desired_distance=desired,
                    enforce_sam_clearance=True,
                        terrain=terrain,
                        land=land,
                    )
                sam_id = _format_id("SAM", sam_seq)
                sam_seq += 1
                sam_sites.append(
                    SamSiteSpec(
                        target_id=sam_id,
                        position=pos,
                        hp_required=1,
                        target_type="SAM_SITE",
                    )
                )
                group_sam_ids.append(sam_id)
                occupied.append(pos)

        for _ in range(jam_cost):
            pos = _place_member_near_center(
                center,
                home_base=home_base,
                parameters=parameters,
                existing=occupied,
                rng=rng,
                        terrain=terrain,
                        land=land,
                    )
            jam_id = _format_id("Jammer", jammer_seq)
            jammer_seq += 1
            jammers.append(JammerSpec(jammer_id=jam_id, position=pos))
            group_jam_ids.append(jam_id)
            occupied.append(pos)

        groups.append(
            EnemyGroup(
                group_id=group_id,
                template=template,
                center=center,
                target_ids=tuple(group_target_ids),
                sam_site_ids=tuple(group_sam_ids),
                jammer_ids=tuple(group_jam_ids),
                discovery_parent_group_id=parent_group_id,
            )
        )

    # Leftover threats as standalone groups (canonical destroyable at center for SAMs).
    while sam_left > 0 or jam_left > 0:
        # Prefer pairing leftover SAM+jammer when both remain and IADS allowed.
        if (
            sam_left > 0
            and jam_left > 0
            and "IADS_CLUSTER" in parameters.allowed_group_templates
        ):
            leftover_template: GroupTemplateName = "IADS_CLUSTER"
            use_sam, use_jam = 1, 1
        elif sam_left >= 2 and "SAM_PAIR" in parameters.allowed_group_templates:
            leftover_template = "SAM_PAIR"
            use_sam, use_jam = 2, 0
        elif sam_left > 0 and "DEFENDED_OBJECTIVE" in parameters.allowed_group_templates:
            leftover_template = "DEFENDED_OBJECTIVE"
            use_sam, use_jam = 1, 0
        elif jam_left > 0 and "JAMMED_OBJECTIVE" in parameters.allowed_group_templates:
            leftover_template = "JAMMED_OBJECTIVE"
            use_sam, use_jam = 0, 1
        elif sam_left > 0:
            leftover_template = "DEFENDED_OBJECTIVE"
            use_sam, use_jam = 1, 0
        else:
            leftover_template = "JAMMED_OBJECTIVE"
            use_sam, use_jam = 0, 1

        # Place a new center linked into the discovery tree when it holds a SAM;
        # jammer-only leftovers do not need discovery edges.
        needs_discovery = use_sam > 0
        center, parent_index = _place_leftover_center(
            parameters=parameters,
            home_base=home_base,
            rng=rng,
            existing_centers=[g.center for g in groups],
            occupied=occupied,
            needs_discovery=needs_discovery,
            enforce_sam_clearance=use_sam > 0,
            terrain=terrain,
            land=land,
        )

        group_id = _format_id("Group", group_seq)
        group_seq += 1
        parent_group_id = groups[parent_index].group_id if parent_index is not None else None

        group_sam_ids = []
        group_jam_ids = []
        group_target_ids: list[str] = []

        if use_sam > 0:
            # Canonical SAM at center.
            sam_id = _format_id("SAM", sam_seq)
            sam_seq += 1
            sam_sites.append(
                SamSiteSpec(
                    target_id=sam_id,
                    position=center,
                    hp_required=1,
                    target_type="SAM_SITE",
                )
            )
            group_sam_ids.append(sam_id)
            occupied.append(center)
            use_sam -= 1
            sam_left -= 1

            while use_sam > 0:
                desired = _desired_sam_pair_distance(parameters)
                pos = _place_member_near_center(
                    center,
                    home_base=home_base,
                    parameters=parameters,
                    existing=occupied,
                    rng=rng,
                    desired_distance=desired,
                    enforce_sam_clearance=True,
                        terrain=terrain,
                        land=land,
                    )
                sam_id = _format_id("SAM", sam_seq)
                sam_seq += 1
                sam_sites.append(
                    SamSiteSpec(
                        target_id=sam_id,
                        position=pos,
                        hp_required=1,
                        target_type="SAM_SITE",
                    )
                )
                group_sam_ids.append(sam_id)
                occupied.append(pos)
                use_sam -= 1
                sam_left -= 1

        while use_jam > 0 and jam_left > 0:
            # Jammer-only group: jammer sits at center when no SAM canonical.
            if not group_sam_ids and not group_jam_ids:
                pos = center
            else:
                pos = _place_member_near_center(
                    center,
                    home_base=home_base,
                    parameters=parameters,
                    existing=occupied,
                    rng=rng,
                        terrain=terrain,
                        land=land,
                    )
            jam_id = _format_id("Jammer", jammer_seq)
            jammer_seq += 1
            jammers.append(JammerSpec(jammer_id=jam_id, position=pos))
            group_jam_ids.append(jam_id)
            occupied.append(pos)
            use_jam -= 1
            jam_left -= 1

        groups.append(
            EnemyGroup(
                group_id=group_id,
                template=leftover_template,
                center=center,
                target_ids=tuple(group_target_ids),
                sam_site_ids=tuple(group_sam_ids),
                jammer_ids=tuple(group_jam_ids),
                discovery_parent_group_id=parent_group_id if needs_discovery else None,
            )
        )

    # Sort entity lists by numeric ID (already monotonic, but enforce).
    targets.sort(key=lambda t: t.target_id)
    sam_sites.sort(key=lambda s: s.target_id)
    jammers.sort(key=lambda j: j.jammer_id)
    groups_sorted = tuple(sorted(groups, key=lambda g: g.group_id))

    # Phase 42: place AAA near defended group centers (land only, launch-safe).
    aaa_sites: list[AaaSiteSpec] = []
    aaa_seq = 1
    aaa_needed = int(getattr(parameters, "aaa_count", 0) or 0)
    group_centers = [g.center for g in groups_sorted] or [home_base]
    from wraithnet.guns import AAA_GUN

    aaa_clearance = float(AAA_GUN.range) + LAUNCH_RING_OUTER_RADIUS
    for i in range(aaa_needed):
        center = group_centers[i % len(group_centers)]
        placed: tuple[float, float] | None = None
        for _ in range(parameters.max_placement_attempts):
            _note_attempt()
            cand = _place_member_near_center(
                center,
                home_base=home_base,
                parameters=parameters,
                existing=occupied,
                rng=rng,
                desired_distance=parameters.entity_min_separation * 1.5,
                enforce_sam_clearance=False,
                terrain=terrain,
                land=land,
            )
            if distance_from_home(home_base, cand) < aaa_clearance:
                continue
            if not _is_ground_land(terrain, cand):
                continue
            placed = cand
            break
        if placed is None:
            raise GenerationError(
                PLACEMENT_EXHAUSTED,
                {
                    "kind": "aaa",
                    "seed": parameters.seed,
                    "requested_counts": {"aaa_count": aaa_needed},
                    "grid": (config.GRID_WIDTH, config.GRID_HEIGHT),
                    "active_constraints": {
                        "placed": len(aaa_sites),
                        "land_cells": len(land),
                    },
                    "suggestions": [
                        "reduce aaa_count",
                        "increase land coverage",
                        "reduce entity_min_separation",
                    ],
                },
            )
        aaa_id = _format_id("AAA", aaa_seq)
        aaa_seq += 1
        heading = (bearing_to(placed, center) if placed != center else 0.0) % 360.0
        aaa_sites.append(
            AaaSiteSpec(
                target_id=aaa_id,
                position=placed,
                heading=heading,
                faction="ENEMY",
                frame_type="AAA_LIGHT",
            )
        )
        occupied.append(placed)
    aaa_sites.sort(key=lambda a: a.target_id)

    # Cooperation candidates from primary objectives only.
    coop_n = int(round(parameters.cooperation_objective_fraction * len(targets)))
    coop_n = max(0, min(coop_n, len(targets)))
    candidate_pool = [t.target_id for t in targets]
    if coop_n == 0:
        coop_ids: tuple[str, ...] = ()
    else:
        # Deterministic sample without replacement.
        pool = list(candidate_pool)
        rng.shuffle(pool)
        coop_ids = tuple(sorted(pool[:coop_n]))

    result = EnemyGeneration(
        home_base=home_base,
        targets=tuple(targets),
        sam_sites=tuple(sam_sites),
        jammers=tuple(jammers),
        groups=groups_sorted,
        cooperation_candidate_ids=coop_ids,
        terrain=terrain,
        aaa_sites=tuple(aaa_sites),
    )

    # Final exact-budget assertion (programming error if violated).
    if len(result.targets) != parameters.objective_count:
        raise GenerationError(
            COUNT_BUDGET_ERROR,
            {
                "kind": "objective_count_mismatch",
                "seed": parameters.seed,
                "requested_counts": {"objective_count": parameters.objective_count},
                "grid": (config.GRID_WIDTH, config.GRID_HEIGHT),
                "active_constraints": {"emitted": len(result.targets)},
                "suggestions": [],
            },
        )
    if len(result.sam_sites) != parameters.sam_count:
        raise GenerationError(
            COUNT_BUDGET_ERROR,
            {
                "kind": "sam_count_mismatch",
                "seed": parameters.seed,
                "requested_counts": {"sam_count": parameters.sam_count},
                "grid": (config.GRID_WIDTH, config.GRID_HEIGHT),
                "active_constraints": {"emitted": len(result.sam_sites)},
                "suggestions": [],
            },
        )
    if len(result.jammers) != parameters.jammer_count:
        raise GenerationError(
            COUNT_BUDGET_ERROR,
            {
                "kind": "jammer_count_mismatch",
                "seed": parameters.seed,
                "requested_counts": {"jammer_count": parameters.jammer_count},
                "grid": (config.GRID_WIDTH, config.GRID_HEIGHT),
                "active_constraints": {"emitted": len(result.jammers)},
                "suggestions": [],
            },
        )
    if len(result.aaa_sites) != int(getattr(parameters, "aaa_count", 0) or 0):
        raise GenerationError(
            COUNT_BUDGET_ERROR,
            {
                "kind": "aaa_count_mismatch",
                "seed": parameters.seed,
                "requested_counts": {"aaa_count": parameters.aaa_count},
                "grid": (config.GRID_WIDTH, config.GRID_HEIGHT),
                "active_constraints": {"emitted": len(result.aaa_sites)},
                "suggestions": [],
            },
        )
    return result


def _place_leftover_center(
    *,
    parameters: GenerationParameters,
    home_base: tuple[float, float],
    rng: random.Random,
    existing_centers: list[tuple[float, float]],
    occupied: list[tuple[float, float]],
    needs_discovery: bool,
    enforce_sam_clearance: bool,
    terrain: TerrainGrid,
    land: list[tuple[int, int]],
) -> tuple[tuple[float, float], int | None]:
    link_max = discovery_link_max_distance()
    pairwise = parameters.group_min_separation
    sam_clearance = sam_min_distance_from_home(parameters)

    for _ in range(parameters.max_placement_attempts):
        _note_attempt()
        if not existing_centers:
            break
        parent_index = rng.randrange(len(existing_centers))
        parent = existing_centers[parent_index]
        inner = parameters.entity_min_separation
        outer = link_max if needs_discovery else max(link_max, pairwise * 2.0)
        sample = _sample_in_annulus(parent, inner, outer, rng)
        cand = _land_position_from_sample(terrain, sample, land, rng)
        if cand is None:
            continue
        if not position_in_bounds(cand, margin=parameters.map_margin):
            continue
        if distance_from_home(home_base, cand) < parameters.base_safety_radius:
            continue
        if enforce_sam_clearance and distance_from_home(home_base, cand) < sam_clearance:
            continue
        if not position_respects_separation(cand, existing_centers, pairwise):
            continue
        if not position_respects_separation(
            cand, occupied, parameters.entity_min_separation
        ):
            continue
        if needs_discovery:
            parent_index = _nearest_linkable_parent(
                cand, existing_centers, link_max, terrain=terrain
            )
            if parent_index is None:
                continue
        else:
            parent_index = None
        return cand, parent_index

    # Grid fallback.
    step = max(2.0, parameters.entity_min_separation)
    x = parameters.map_margin
    while x <= config.GRID_WIDTH - parameters.map_margin + 1e-9:
        y = parameters.map_margin
        while y <= config.GRID_HEIGHT - parameters.map_margin + 1e-9:
            _note_attempt()
            sample = (float(x), float(y))
            y += step
            cand = _project_to_land(terrain, sample, land)
            if cand is None:
                continue
            if not position_in_bounds(cand, margin=parameters.map_margin):
                continue
            if distance_from_home(home_base, cand) < parameters.base_safety_radius:
                continue
            if enforce_sam_clearance and distance_from_home(home_base, cand) < sam_clearance:
                continue
            if not position_respects_separation(cand, existing_centers, pairwise):
                continue
            if not position_respects_separation(
                cand, occupied, parameters.entity_min_separation
            ):
                continue
            parent_index = None
            if needs_discovery:
                parent_index = _nearest_linkable_parent(
                    cand, existing_centers, link_max, terrain=terrain
                )
                if parent_index is None:
                    continue
            return cand, parent_index
        x += step

    raise GenerationError(
        PLACEMENT_EXHAUSTED,
        {
            "kind": "leftover_center",
            "seed": parameters.seed,
            "requested_counts": {
                "sam_count": parameters.sam_count,
                "jammer_count": parameters.jammer_count,
            },
            "grid": (config.GRID_WIDTH, config.GRID_HEIGHT),
            "active_constraints": {
                "group_min_separation": parameters.group_min_separation,
                "needs_discovery": needs_discovery,
                "land_cells": len(land),
            },
            "suggestions": [
                "reduce threat counts or group separation",
                "reduce water_fraction / city_fraction",
            ],
        },
    )


def enemies_to_scenario(
    enemies: EnemyGeneration,
    jets: list[JetSpec],
    *,
    grid_width: float = config.GRID_WIDTH,
    grid_height: float = config.GRID_HEIGHT,
) -> ScenarioConfig:
    """Build a validating ``ScenarioConfig`` from generated enemies plus jets."""
    return scenario_from_data(
        new_scenario_data(
            grid_width=grid_width,
            grid_height=grid_height,
            jets=jets,
            targets=list(enemies.targets),
            sam_sites=list(enemies.sam_sites),
            jammers=list(enemies.jammers),
            aaa_sites=list(enemies.aaa_sites),
            home_base=enemies.home_base,
            terrain=enemies.terrain,
        )
    )


def make_test_jet(
    agent_id: str = "Jet_01",
    position: tuple[float, float] = (4.0, 4.0),
) -> JetSpec:
    """Minimal legal strike-fighter used by Phase 31 tests."""
    return JetSpec(
        agent_id=agent_id,
        position=position,
        payload={"JDAM": 4, "AGM65": 2},
        frame_type="STRIKE_FIGHTER",
    )


# ---------------------------------------------------------------------------
# Phase 32 — friendly roster + public generate_scenario()
# ---------------------------------------------------------------------------


class GeneratedGroupSummary(BaseModel):
    group_id: str
    template: GroupTemplateName
    center: tuple[float, float]
    entity_ids: list[str]
    discovery_parent_group_id: str | None = None


class GenerationResult(BaseModel):
    scenario: ScenarioConfig
    parameters: GenerationParameters
    feasibility: FeasibilityReport
    groups: list[GeneratedGroupSummary]


@dataclass
class _FriendlyBuildState:
    """Mutable working set while packing an AUTO_BALANCED roster."""

    targets: list[TargetSpec]
    sam_sites: list[SamSiteSpec]
    tanks: list[TankSpec] = field(default_factory=list)
    corvettes: list = field(default_factory=list)
    deliberate_cooperative_ids: list[str] = field(default_factory=list)
    unrealized_cooperative_ids: list[str] = field(default_factory=list)


def payload_slots_used(payload: dict[WeaponType, int]) -> int:
    return sum(
        config.WEAPON_CATALOG[weapon].slot_cost * count
        for weapon, count in payload.items()
        if count > 0
    )


def map_center(
    grid_width: float = config.GRID_WIDTH,
    grid_height: float = config.GRID_HEIGHT,
) -> tuple[float, float]:
    return (grid_width / 2.0, grid_height / 2.0)


def initial_heading_toward_map_center(
    position: tuple[float, float],
    *,
    grid_width: float = config.GRID_WIDTH,
    grid_height: float = config.GRID_HEIGHT,
) -> float:
    return bearing_to(position, map_center(grid_width, grid_height))


def _jet_contribution(jet: JetSpec, target_type: TargetType) -> int:
    total = 0
    for weapon, count in jet.payload.items():
        if count <= 0:
            continue
        dmg = weapon_eligible_damage(weapon, target_type)
        if dmg > 0:
            total += dmg * count
    return total


def _empty_payload() -> dict[WeaponType, int]:
    return {
        w: 0
        for w in config.WEAPON_CATALOG
        if w not in ("SAM_IR", "SAM_RADAR")
    }


_JET_PACKABLE_WEAPONS: tuple[WeaponType, ...] = (
    "JDAM",
    "GUN_POD",
    "AGM65",
    "AGM88",
    "AGM84",
    "AIM9X",
    "AMRAAM",
)


def _payload_has_weapon(payload: dict[WeaponType, int]) -> bool:
    return any(count > 0 for count in payload.values())


def _add_weapon(
    payload: dict[WeaponType, int],
    weapon: WeaponType,
    frame_type: JetFrameType,
    count: int = 1,
) -> bool:
    """Try to add ``count`` units; return False if slots would overflow."""
    if count <= 0:
        return True
    slots_max = config.JET_FRAME_CATALOG[frame_type].ordnance_slots
    cost = config.WEAPON_CATALOG[weapon].slot_cost * count
    if payload_slots_used(payload) + cost > slots_max:
        return False
    payload[weapon] = payload.get(weapon, 0) + count
    return True


def _destroyable_records(
    targets: list[TargetSpec], sam_sites: list[SamSiteSpec]
) -> list[tuple[str, TargetType, tuple[float, float], int]]:
    records = [
        (t.target_id, t.target_type, t.position, t.hp_required) for t in targets
    ]
    records.extend(
        (s.target_id, s.target_type, s.position, s.hp_required) for s in sam_sites
    )
    return records


def _combined_arms_forecast_records(
    parameters: GenerationParameters,
    home_base: tuple[float, float],
) -> list[tuple[str, TargetType, tuple[float, float], int]]:
    """Forecast tank/naval HP when units are not yet placed (count-accurate)."""
    from wraithnet.naval import NAVAL_FRAME_CATALOG
    from wraithnet.tanks import TANK_FRAME_CATALOG

    records: list[tuple[str, TargetType, tuple[float, float], int]] = []
    enemy_tanks = int(getattr(parameters, "enemy_tank_count", 0) or 0)
    friendly_tanks = int(getattr(parameters, "friendly_tank_count", 0) or 0)
    if enemy_tanks > 0 and friendly_tanks <= 0:
        tank_hp = int(TANK_FRAME_CATALOG["MBT_STANDARD"].hp)
        for i in range(enemy_tanks):
            records.append(
                (
                    f"_forecast_ETank_{i + 1:02d}",
                    "GROUND_VEHICLE",
                    home_base,
                    tank_hp,
                )
            )

    corvette_n = int(getattr(parameters, "enemy_corvette_count", 0) or 0)
    if corvette_n > 0:
        tier = str(getattr(parameters, "corvette_defense_tier", "STANDARD") or "STANDARD")
        frame = "CORVETTE_LIGHT" if tier == "LIGHT" else "CORVETTE_STANDARD"
        ship_hp = int(NAVAL_FRAME_CATALOG[frame].hp)
        for i in range(corvette_n):
            records.append(
                (
                    f"_forecast_Corvette_{i + 1:02d}",
                    "SURFACE_VESSEL",
                    home_base,
                    ship_hp,
                )
            )
    return records


def _combined_arms_unit_records(
    *,
    tanks: list[TankSpec] | None,
    corvettes: list | None,
    credit_friendly_tanks: bool,
) -> list[tuple[str, TargetType, tuple[float, float], int]]:
    """Destroyable HP records from placed tanks/corvettes for auto-balance."""
    from wraithnet.naval import NAVAL_FRAME_CATALOG
    from wraithnet.tanks import TANK_FRAME_CATALOG

    records: list[tuple[str, TargetType, tuple[float, float], int]] = []
    tank_list = list(tanks or [])
    has_friendly = any(t.faction == "FRIENDLY" for t in tank_list)
    if not (credit_friendly_tanks and has_friendly):
        for tank in tank_list:
            if tank.faction != "ENEMY":
                continue
            frame = TANK_FRAME_CATALOG[tank.frame_type]
            hp = int(tank.hp if tank.hp is not None else frame.hp)
            records.append((tank.unit_id, "GROUND_VEHICLE", tank.position, hp))

    for corvette in list(corvettes or []):
        frame = NAVAL_FRAME_CATALOG[corvette.frame_type]
        records.append(
            (corvette.unit_id, "SURFACE_VESSEL", corvette.position, int(frame.hp))
        )
    return records


def _records_for_auto_balance(
    parameters: GenerationParameters,
    targets: list[TargetSpec],
    sam_sites: list[SamSiteSpec],
    home_base: tuple[float, float],
    *,
    tanks: list[TankSpec] | None = None,
    corvettes: list | None = None,
) -> list[tuple[str, TargetType, tuple[float, float], int]]:
    base = _destroyable_records(targets, sam_sites)
    if tanks is not None or corvettes is not None:
        return base + _combined_arms_unit_records(
            tanks=tanks,
            corvettes=corvettes,
            credit_friendly_tanks=True,
        )
    return base + _combined_arms_forecast_records(parameters, home_base)


def _frame_reaches_any(
    frame_type: JetFrameType,
    home_base: tuple[float, float],
    positions: list[tuple[float, float]],
) -> bool:
    capacity = config.JET_FRAME_CATALOG[frame_type].fuel_capacity
    return any(
        round_trip_fuel_required(frame_type, home_base, pos) <= capacity
        for pos in positions
    )


def _select_frames(
    parameters: GenerationParameters,
    home_base: tuple[float, float],
    objective_positions: list[tuple[float, float]],
    count: int,
    rng: random.Random,
) -> list[JetFrameType]:
    allowed = list(parameters.allowed_frame_types)
    reachable = [
        f for f in allowed if _frame_reaches_any(f, home_base, objective_positions)
    ]
    pool = reachable if reachable else list(allowed)
    # Prefer variety: shuffle a stable copy then round-robin in shuffled order.
    order = list(pool)
    rng.shuffle(order)
    if not order:
        order = ["STRIKE_FIGHTER"]
    return [order[i % len(order)] for i in range(count)]


def _demand_by_type(
    records: list[tuple[str, TargetType, tuple[float, float], int]],
    reserve_factor: float,
) -> dict[TargetType, int]:
    raw: dict[TargetType, int] = {
        "HARDENED_BUNKER": 0,
        "MOBILE_RADAR": 0,
        "SAM_SITE": 0,
        "AAA_SITE": 0,
        "GROUND_VEHICLE": 0,
        "SURFACE_VESSEL": 0,
    }
    for _, ttype, _, hp in records:
        raw[ttype] = raw.get(ttype, 0) + hp
    return {
        ttype: int(math.ceil(hp * reserve_factor)) for ttype, hp in raw.items()
    }


def _pack_payloads_for_demand(
    frames: list[JetFrameType],
    home_base: tuple[float, float],
    records: list[tuple[str, TargetType, tuple[float, float], int]],
    demand: dict[TargetType, int],
    rng: random.Random,
) -> list[dict[WeaponType, int]]:
    payloads = [_empty_payload() for _ in frames]
    remaining = dict(demand)

    # Cover each objective type with preferred weapons on jets that can reach
    # at least one objective of that type.
    type_positions: dict[TargetType, list[tuple[float, float]]] = {
        "HARDENED_BUNKER": [],
        "MOBILE_RADAR": [],
        "SAM_SITE": [],
        "AAA_SITE": [],
        "GROUND_VEHICLE": [],
        "SURFACE_VESSEL": [],
    }
    for _, ttype, pos, _ in records:
        type_positions.setdefault(ttype, []).append(pos)

    # Deterministic jet order, slight RNG for weapon mix when ties exist.
    jet_order = list(range(len(frames)))
    rng.shuffle(jet_order)

    pack_types: tuple[TargetType, ...] = (
        "HARDENED_BUNKER",
        "MOBILE_RADAR",
        "SAM_SITE",
        "AAA_SITE",
        "GROUND_VEHICLE",
        "SURFACE_VESSEL",
    )
    for ttype in pack_types:
        need = remaining.get(ttype, 0)
        if need <= 0:
            continue
        prefs = list(_WEAPON_PREFERENCE[ttype])
        # Occasionally insert GUN_POD earlier for bunkers (deterministic coin).
        if ttype == "HARDENED_BUNKER" and rng.random() < 0.25 and "GUN_POD" in prefs:
            prefs = ["GUN_POD", "JDAM"] + [w for w in prefs if w not in ("GUN_POD", "JDAM")]

        positions = type_positions.get(ttype, [])
        progress = True
        while need > 0 and progress:
            progress = False
            for ji in jet_order:
                if need <= 0:
                    break
                frame = frames[ji]
                probe = JetSpec(
                    agent_id="_",
                    position=home_base,
                    payload={"JDAM": 1},
                    frame_type=frame,
                )
                if positions and not any(
                    jet_can_reach_round_trip(probe, home_base, pos) for pos in positions
                ):
                    continue
                for weapon in prefs:
                    dmg = weapon_eligible_damage(weapon, ttype)
                    if dmg <= 0:
                        continue
                    if _add_weapon(payloads[ji], weapon, frame, 1):
                        need -= dmg
                        remaining[ttype] = need
                        progress = True
                        break
            if not progress:
                break

    # Ensure every jet carries at least one weapon; fill leftover slots with
    # preferred ammo for still-short types, else mixed JDAM/AGM65.
    short_types = [t for t, n in remaining.items() if n > 0]
    for ji, frame in enumerate(frames):
        payload = payloads[ji]
        if not _payload_has_weapon(payload):
            for weapon in ("JDAM", "AGM65", "AGM88", "AGM84", "GUN_POD"):
                if _add_weapon(payload, weapon, frame, 1):
                    break
        # Spend remaining free slots on short types or variety.
        guard = 0
        while payload_slots_used(payload) < config.JET_FRAME_CATALOG[frame].ordnance_slots:
            guard += 1
            if guard > 32:
                break
            if short_types:
                ttype = short_types[guard % len(short_types)]
                weapon = _WEAPON_PREFERENCE[ttype][0]
                dmg = weapon_eligible_damage(weapon, ttype)
                if dmg > 0 and _add_weapon(payload, weapon, frame, 1):
                    remaining[ttype] = remaining.get(ttype, 0) - dmg
                    if remaining[ttype] <= 0:
                        short_types = [t for t, n in remaining.items() if n > 0]
                    continue
            # Variety fill: alternate primary weapons (no air-to-air / SAM).
            fill_order: list[WeaponType] = ["JDAM", "AGM65", "AGM88", "AGM84", "GUN_POD"]
            placed = False
            for weapon in fill_order:
                if _add_weapon(payload, weapon, frame, 1):
                    placed = True
                    break
            if not placed:
                break

    # Strip zero counts for cleaner payloads.
    return [
        {w: c for w, c in payload.items() if c > 0} or {"JDAM": 1}
        for payload in payloads
    ]


def _roster_passes_static(
    jets: list[JetSpec],
    targets: list[TargetSpec],
    sam_sites: list[SamSiteSpec],
    home_base: tuple[float, float],
    group_ids_by_entity: dict[str, str] | None,
    group_centers: dict[str, tuple[float, float]] | None = None,
    *,
    tanks: list[TankSpec] | None = None,
    corvettes: list | None = None,
    terrain: TerrainGrid | None = None,
) -> bool:
    tank_list = list(tanks or [])
    corvette_list = list(corvettes or [])
    enemy_tank_ids = [t.unit_id for t in tank_list if t.faction == "ENEMY"]
    enemy_corvette_ids = [c.unit_id for c in corvette_list]
    objectives = None
    if enemy_tank_ids or enemy_corvette_ids:
        from wraithnet.objectives import default_strike_and_return_objectives

        subject_ids = [t.target_id for t in targets]
        subject_ids.extend(s.target_id for s in sam_sites)
        subject_ids.extend(enemy_tank_ids)
        subject_ids.extend(enemy_corvette_ids)
        objectives = default_strike_and_return_objectives(
            subject_ids, [j.agent_id for j in jets]
        )

    scenario_kwargs: dict = {
        "jets": jets,
        "targets": targets,
        "sam_sites": sam_sites,
        "jammers": [],
        "home_base": home_base,
        "tanks": tank_list,
        "corvettes": corvette_list,
    }
    if terrain is not None:
        scenario_kwargs["terrain"] = terrain
    if objectives is not None:
        scenario_kwargs["mission_objectives"] = objectives
    scenario = scenario_from_data(new_scenario_data(**scenario_kwargs))
    report = analyze_feasibility(
        scenario,
        group_ids_by_entity=group_ids_by_entity,
        group_centers=group_centers,
    )
    error_codes = {i.code for i in report.issues if i.severity == "ERROR"}
    # Discovery/launch issues are enemy-layout concerns; AUTO cares about
    # ammunition/fuel coverage from the roster.
    blocking = error_codes & {
        "NO_SUITABLE_WEAPON",
        "INSUFFICIENT_DAMAGE",
        "FUEL_REACH",
        "EMPTY_LOADOUT",
        "NO_ANTI_ARMOR_CAPABILITY",
        "OBJECTIVE_UNACHIEVABLE",
    }
    # EMPTY_LOADOUT is WARNING in analyzer — still treat zero-weapon as fail.
    if any(not _payload_has_weapon(j.payload) for j in jets):
        return False
    return not blocking


def _try_realize_cooperation(
    state: _FriendlyBuildState,
    jets: list[JetSpec],
    home_base: tuple[float, float],
    parameters: GenerationParameters,
    candidate_ids: tuple[str, ...],
) -> None:
    """Raise candidate HP when the roster can realize true cooperation."""
    by_id = {t.target_id: t for t in state.targets}
    for cid in sorted(candidate_ids):
        target = by_id.get(cid)
        if target is None:
            state.unrealized_cooperative_ids.append(cid)
            continue
        contributions = [
            _jet_contribution(jet, target.target_type)
            for jet in jets
            if jet_can_reach_round_trip(jet, home_base, target.position)
        ]
        if len(contributions) < 2:
            state.unrealized_cooperative_ids.append(cid)
            continue
        best = max(contributions)
        total = sum(contributions)
        # Need best < hp <= total within configured bounds.
        lo = max(parameters.objective_hp_min, best + 1)
        hi = min(parameters.objective_hp_max, total)
        if lo > hi:
            state.unrealized_cooperative_ids.append(cid)
            continue
        # Prefer the highest feasible cooperative HP (PoC-style pressure).
        target.hp_required = hi
        state.deliberate_cooperative_ids.append(cid)


def _auto_balanced_roster(
    parameters: GenerationParameters,
    enemies: EnemyGeneration,
    rng: random.Random,
    state: _FriendlyBuildState,
) -> list[JetSpec]:
    home = enemies.home_base
    records = _records_for_auto_balance(
        parameters,
        state.targets,
        state.sam_sites,
        home,
        tanks=state.tanks,
        corvettes=state.corvettes,
    )
    positions = [pos for _, _, pos, _ in records]
    group_map = _group_ids_by_entity(enemies)
    centers = _canonical_group_centers(enemies)

    # Estimate minimum jets from total reserved demand vs best-frame slots.
    demand = _demand_by_type(records, parameters.friendly_reserve_factor)
    total_need = sum(demand.values())
    best_slots = max(
        (config.JET_FRAME_CATALOG[f].ordnance_slots for f in parameters.allowed_frame_types),
        default=7,
    )
    # Rough lower bound: each slot ≈ 1 damage for JDAM/AIM9X.
    min_from_demand = max(1, int(math.ceil(total_need / max(best_slots, 1))))
    count = max(parameters.friendly_count_min, min(parameters.friendly_count_max, min_from_demand))

    last_detail: dict[str, object] = {}
    for attempt in range(_AUTO_REPAIR_ATTEMPTS):
        frames = _select_frames(parameters, home, positions, count, rng)
        # Before coop HP bump, pack against current demand including tanks/naval.
        pack_records = _records_for_auto_balance(
            parameters,
            state.targets,
            state.sam_sites,
            home,
            tanks=state.tanks,
            corvettes=state.corvettes,
        )
        pack_demand = _demand_by_type(pack_records, parameters.friendly_reserve_factor)
        payloads = _pack_payloads_for_demand(frames, home, pack_records, pack_demand, rng)

        # Placeholder positions — launch placement happens later.
        jets = [
            JetSpec(
                agent_id=_format_id("Jet", i + 1),
                position=home,
                payload=payloads[i],
                frame_type=frames[i],
                initial_heading=0.0,
            )
            for i in range(count)
        ]

        # Realize cooperation against this packed roster, then re-check.
        state.deliberate_cooperative_ids.clear()
        state.unrealized_cooperative_ids.clear()
        # Reset HP to enemy originals before each realization attempt.
        for i, original in enumerate(enemies.targets):
            state.targets[i].hp_required = original.hp_required
        _try_realize_cooperation(
            state, jets, home, parameters, enemies.cooperation_candidate_ids
        )

        # After HP bumps, ensure capacity still covers with reserve.
        post_records = _records_for_auto_balance(
            parameters,
            state.targets,
            state.sam_sites,
            home,
            tanks=state.tanks,
            corvettes=state.corvettes,
        )
        post_demand = _demand_by_type(post_records, parameters.friendly_reserve_factor)
        # Top-up payloads if coop raised demand.
        for ttype, need in post_demand.items():
            have = sum(_jet_contribution(j, ttype) for j in jets)
            short = need - have
            if short <= 0:
                continue
            for jet in jets:
                if short <= 0:
                    break
                prefs = _WEAPON_PREFERENCE.get(ttype, ("JDAM",))
                for weapon in prefs:
                    dmg = weapon_eligible_damage(weapon, ttype)
                    if dmg <= 0:
                        continue
                    while short > 0 and _add_weapon(
                        jet.payload, weapon, jet.frame_type, 1
                    ):
                        short -= dmg
                        have += dmg

        if _roster_passes_static(
            jets,
            state.targets,
            state.sam_sites,
            home,
            group_map,
            centers,
            tanks=state.tanks,
            corvettes=state.corvettes,
            terrain=enemies.terrain,
        ):
            # Re-verify cooperation inequality still holds after top-up.
            state.deliberate_cooperative_ids.clear()
            state.unrealized_cooperative_ids.clear()
            for i, original in enumerate(enemies.targets):
                # Keep raised HP only if still cooperative; else revert.
                state.targets[i].hp_required = original.hp_required
            _try_realize_cooperation(
                state, jets, home, parameters, enemies.cooperation_candidate_ids
            )
            if _roster_passes_static(
                jets,
                state.targets,
                state.sam_sites,
                home,
                group_map,
                centers,
                tanks=state.tanks,
                corvettes=state.corvettes,
                terrain=enemies.terrain,
            ):
                return jets

        last_detail = {
            "attempt": attempt,
            "jet_count": count,
            "total_need": total_need,
            "demand": dict(demand),
            "available_slots": sum(
                config.JET_FRAME_CATALOG[f].ordnance_slots for f in frames
            ),
        }
        # Repair: grow count, or reshuffle frames/payloads via next RNG draws.
        if count < parameters.friendly_count_max and attempt % 3 == 2:
            count += 1
        elif count < parameters.friendly_count_max and total_need > count * best_slots:
            count = min(parameters.friendly_count_max, count + 1)

    raise GenerationError(
        AUTO_BALANCE_CAPACITY,
        {
            "kind": "friendly_roster",
            "seed": parameters.seed,
            "requested_counts": {
                "friendly_count_min": parameters.friendly_count_min,
                "friendly_count_max": parameters.friendly_count_max,
                "objective_count": parameters.objective_count,
                "sam_count": parameters.sam_count,
            },
            "grid": (config.GRID_WIDTH, config.GRID_HEIGHT),
            "active_constraints": last_detail,
            "required_damage": total_need,
            "available_slots": last_detail.get("available_slots"),
            "suggestions": [
                "increase friendly_count_max",
                "raise friendly_reserve_factor only if capacity allows",
                "allow HEAVY_BOMBER or more ordnance-capable frames",
                "reduce objective_count / objective_hp_max",
            ],
        },
    )


def _random_legal_roster(
    parameters: GenerationParameters,
    enemies: EnemyGeneration,
    rng: random.Random,
) -> list[JetSpec]:
    count = rng.randint(parameters.friendly_count_min, parameters.friendly_count_max)
    frames = list(parameters.allowed_frame_types)
    jets: list[JetSpec] = []
    for i in range(count):
        frame = rng.choice(frames)
        slots_max = config.JET_FRAME_CATALOG[frame].ordnance_slots
        payload = _empty_payload()
        weapons = list(_JET_PACKABLE_WEAPONS)
        # Always at least one weapon.
        first = rng.choice(weapons)
        _add_weapon(payload, first, frame, 1)
        # Fill remaining slots randomly.
        guard = 0
        while payload_slots_used(payload) < slots_max and guard < 40:
            guard += 1
            if rng.random() < 0.35:
                break
            weapon = rng.choice(weapons)
            _add_weapon(payload, weapon, frame, 1)
        cleaned = {w: c for w, c in payload.items() if c > 0}
        jets.append(
            JetSpec(
                agent_id=_format_id("Jet", i + 1),
                position=enemies.home_base,
                payload=cleaned,
                frame_type=frame,
                initial_heading=0.0,
            )
        )
    return jets


def _custom_roster(
    parameters: GenerationParameters,
    enemies: EnemyGeneration,
) -> list[JetSpec]:
    jets: list[JetSpec] = []
    seq = 1
    for template in parameters.custom_roster:
        for _ in range(template.count):
            jets.append(
                JetSpec(
                    agent_id=_format_id("Jet", seq),
                    position=enemies.home_base,
                    payload=dict(template.payload),
                    frame_type=template.frame_type,
                    initial_heading=0.0,
                )
            )
            seq += 1
    return jets


def generate_friendly_roster(
    parameters: GenerationParameters,
    enemies: EnemyGeneration,
    rng: random.Random,
    *,
    build_state: _FriendlyBuildState | None = None,
) -> list[JetSpec]:
    """Materialize jets for the resolved friendly mode (positions are placeholders)."""
    mode = parameters.friendly_mode
    if mode == "AUTO_BALANCED":
        if build_state is None:
            build_state = _FriendlyBuildState(
                targets=[t.model_copy() for t in enemies.targets],
                sam_sites=[s.model_copy() for s in enemies.sam_sites],
            )
        return _auto_balanced_roster(parameters, enemies, rng, build_state)
    if mode == "RANDOM_LEGAL":
        return _random_legal_roster(parameters, enemies, rng)
    if mode == "CUSTOM_ROSTER":
        return _custom_roster(parameters, enemies)
    raise GenerationError(
        COUNT_BUDGET_ERROR,
        {
            "kind": "friendly_mode",
            "seed": parameters.seed,
            "requested_counts": {},
            "grid": (config.GRID_WIDTH, config.GRID_HEIGHT),
            "active_constraints": {"friendly_mode": mode},
            "suggestions": ["use AUTO_BALANCED, RANDOM_LEGAL, or CUSTOM_ROSTER"],
        },
    )


def _sam_threatens_launch(
    jet_pos: tuple[float, float], sam_pos: tuple[float, float]
) -> bool:
    return math.dist(jet_pos, sam_pos) <= config.SAM_ENGAGEMENT_RANGE


def _aaa_threatens_launch(
    jet_pos: tuple[float, float], aaa_pos: tuple[float, float]
) -> bool:
    from wraithnet.guns import AAA_GUN

    return math.dist(jet_pos, aaa_pos) <= AAA_GUN.range


def _jammer_covers(jet_pos: tuple[float, float], jammer_pos: tuple[float, float]) -> bool:
    return math.dist(jet_pos, jammer_pos) <= config.JAM_RADIUS


def place_jets_on_launch_ring(
    jets: list[JetSpec],
    *,
    home_base: tuple[float, float],
    parameters: GenerationParameters,
    sam_sites: list[SamSiteSpec],
    jammers: list[JammerSpec],
    rng: random.Random,
    aaa_sites: list[AaaSiteSpec] | None = None,
) -> list[JetSpec]:
    """Assign in-bounds, separated launch positions and headings toward map center."""
    if not jets:
        raise GenerationError(
            COUNT_BUDGET_ERROR,
            {
                "kind": "friendly_launch",
                "seed": parameters.seed,
                "requested_counts": {"jet_count": 0},
                "grid": (config.GRID_WIDTH, config.GRID_HEIGHT),
                "active_constraints": {},
                "suggestions": ["provide at least one jet"],
            },
        )

    placed_positions: list[tuple[float, float]] = []
    result: list[JetSpec] = []
    sam_positions = [s.position for s in sam_sites]
    jammer_positions = [j.position for j in jammers]
    aaa_positions = [a.position for a in (aaa_sites or [])]

    for jet in jets:
        position: tuple[float, float] | None = None
        # Prefer positions outside jammer rings; fall back if impossible.
        for prefer_clear_jammer in (True, False):
            for _ in range(parameters.max_placement_attempts):
                cand = _sample_in_annulus(
                    home_base,
                    LAUNCH_RING_INNER_RADIUS,
                    LAUNCH_RING_OUTER_RADIUS,
                    rng,
                )
                if not position_in_bounds(cand, margin=parameters.map_margin):
                    # Launch ring near map edge: still require on-map (margin 0).
                    if not position_in_bounds(cand, margin=0.0):
                        continue
                if math.dist(cand, home_base) < 1e-6:
                    continue
                if not position_respects_separation(
                    cand, placed_positions, LAUNCH_JET_MIN_SEPARATION
                ):
                    continue
                if any(_sam_threatens_launch(cand, sam) for sam in sam_positions):
                    continue
                if any(_aaa_threatens_launch(cand, aaa) for aaa in aaa_positions):
                    continue
                if prefer_clear_jammer and any(
                    _jammer_covers(cand, jam) for jam in jammer_positions
                ):
                    continue
                position = cand
                break
            if position is not None:
                break

        if position is None:
            # Deterministic angular grid fallback on the mid-ring.
            mid_r = 0.5 * (LAUNCH_RING_INNER_RADIUS + LAUNCH_RING_OUTER_RADIUS)
            n = max(len(jets) * 4, 16)
            for k in range(n):
                angle = (2.0 * math.pi * k) / n
                cand = (
                    home_base[0] + mid_r * math.cos(angle),
                    home_base[1] + mid_r * math.sin(angle),
                )
                if not position_in_bounds(cand, margin=0.0):
                    continue
                if math.dist(cand, home_base) < 1e-6:
                    continue
                if not position_respects_separation(
                    cand, placed_positions, LAUNCH_JET_MIN_SEPARATION
                ):
                    continue
                if any(_sam_threatens_launch(cand, sam) for sam in sam_positions):
                    continue
                if any(_aaa_threatens_launch(cand, aaa) for aaa in aaa_positions):
                    continue
                position = cand
                break

        if position is None:
            raise GenerationError(
                PLACEMENT_EXHAUSTED,
                {
                    "kind": "friendly_launch",
                    "seed": parameters.seed,
                    "requested_counts": {"jet_count": len(jets), "placed": len(placed_positions)},
                    "grid": (config.GRID_WIDTH, config.GRID_HEIGHT),
                    "active_constraints": {
                        "launch_inner": LAUNCH_RING_INNER_RADIUS,
                        "launch_outer": LAUNCH_RING_OUTER_RADIUS,
                        "min_separation": LAUNCH_JET_MIN_SEPARATION,
                    },
                    "suggestions": [
                        "reduce friendly jet count",
                        "increase launch ring clearance from SAMs/AAA",
                    ],
                },
            )

        heading = initial_heading_toward_map_center(position)
        placed_positions.append(position)
        result.append(
            jet.model_copy(
                update={"position": position, "initial_heading": heading}
            )
        )
    return result


def _group_ids_by_entity(enemies: EnemyGeneration) -> dict[str, str]:
    mapping: dict[str, str] = {}
    for group in enemies.groups:
        for eid in (*group.target_ids, *group.sam_site_ids, *group.jammer_ids):
            mapping[eid] = group.group_id
    return mapping


def _canonical_group_centers(enemies: EnemyGeneration) -> dict[str, tuple[float, float]]:
    """Centers for groups that contain at least one destroyable entity."""
    return {
        group.group_id: group.center
        for group in enemies.groups
        if group.target_ids or group.sam_site_ids
    }


def _group_summaries(enemies: EnemyGeneration) -> list[GeneratedGroupSummary]:
    summaries: list[GeneratedGroupSummary] = []
    for group in enemies.groups:
        entity_ids = list(group.target_ids) + list(group.sam_site_ids) + list(group.jammer_ids)
        summaries.append(
            GeneratedGroupSummary(
                group_id=group.group_id,
                template=group.template,
                center=group.center,
                entity_ids=entity_ids,
                discovery_parent_group_id=group.discovery_parent_group_id,
            )
        )
    return summaries


def place_generated_tanks(
    parameters: GenerationParameters,
    *,
    terrain: TerrainGrid,
    home_base: tuple[float, float],
    occupied: list[tuple[float, float]],
) -> list[TankSpec]:
    """Place friendly/enemy tanks with ADVANCE routes toward a shared zone."""
    friendly_n = int(getattr(parameters, "friendly_tank_count", 0) or 0)
    enemy_n = int(getattr(parameters, "enemy_tank_count", 0) or 0)
    if friendly_n <= 0 and enemy_n <= 0:
        return []

    land = land_cells(terrain)
    if not land:
        raise GenerationError(
            PLACEMENT_EXHAUSTED,
            {
                "kind": "tank",
                "seed": parameters.seed,
                "requested_counts": {
                    "friendly_tank_count": friendly_n,
                    "enemy_tank_count": enemy_n,
                },
                "grid": (config.GRID_WIDTH, config.GRID_HEIGHT),
                "active_constraints": {"land_cells": 0},
                "suggestions": ["reduce water_fraction"],
            },
        )

    enemy_rng = random.Random(derived_seed(parameters.seed, ENEMY_GROUND_SALT))
    friendly_rng = random.Random(derived_seed(parameters.seed, FRIENDLY_GROUND_SALT))

    # Shared engagement zone: land cell near map center, reachable from home.
    cx = config.GRID_WIDTH * 0.55
    cy = config.GRID_HEIGHT * 0.55
    zone = _project_to_land(terrain, (cx, cy), land) or cell_center(
        terrain, land[len(land) // 2][0], land[len(land) // 2][1]
    )

    tanks: list[TankSpec] = []
    occ = list(occupied)

    def _place_side(
        *,
        count: int,
        faction: str,
        prefix: str,
        rng: random.Random,
        approach_from: tuple[float, float],
    ) -> None:
        nonlocal tanks, occ
        for i in range(count):
            placed = None
            attempts = max(parameters.max_placement_attempts, 400)
            # Phase 1: annulus near the approach anchor.
            for attempt in range(attempts):
                if attempt < attempts // 2:
                    outer = max(12.0, parameters.group_min_separation) * (
                        1.0 + 0.5 * (attempt / max(attempts // 2, 1))
                    )
                    sample = _sample_in_annulus(
                        approach_from,
                        parameters.entity_min_separation * 2.0,
                        outer,
                        rng,
                    )
                    cand = _project_to_land(terrain, sample, land)
                else:
                    # Phase 2: uniform land-cell fallback for scarce corridors.
                    c, r = land[rng.randrange(len(land))]
                    cand = cell_center(terrain, c, r)
                if cand is None:
                    continue
                if not position_in_bounds(cand, margin=parameters.map_margin):
                    continue
                if not position_respects_separation(
                    cand, occ, parameters.entity_min_separation
                ):
                    continue
                if not tanks_mutually_reachable(terrain, cand, zone):
                    continue
                placed = cand
                break
            if placed is None:
                raise GenerationError(
                    PLACEMENT_EXHAUSTED,
                    {
                        "kind": "tank",
                        "seed": parameters.seed,
                        "requested_counts": {
                            "friendly_tank_count": friendly_n,
                            "enemy_tank_count": enemy_n,
                            "faction": faction,
                        },
                        "grid": (config.GRID_WIDTH, config.GRID_HEIGHT),
                        "active_constraints": {"placed": len(tanks)},
                        "suggestions": [
                            "reduce tank counts",
                            "increase land coverage",
                        ],
                    },
                )
            heading = bearing_to(placed, zone)
            tanks.append(
                TankSpec(
                    unit_id=f"{prefix}_{i + 1:02d}",
                    faction=faction,  # type: ignore[arg-type]
                    position=placed,
                    heading=heading,
                    frame_type="MBT_STANDARD",
                    order="ADVANCE",
                    waypoints=[zone],
                )
            )
            occ.append(placed)

    # Enemy approaches from far side of zone; friendlies from home side.
    enemy_anchor = (
        min(config.GRID_WIDTH - parameters.map_margin, zone[0] + 20.0),
        zone[1],
    )
    enemy_anchor = _project_to_land(terrain, enemy_anchor, land) or zone
    _place_side(
        count=enemy_n,
        faction="ENEMY",
        prefix="ETank",
        rng=enemy_rng,
        approach_from=enemy_anchor,
    )
    _place_side(
        count=friendly_n,
        faction="FRIENDLY",
        prefix="FTank",
        rng=friendly_rng,
        approach_from=home_base,
    )
    tanks.sort(key=lambda t: t.unit_id)
    return tanks


def _water_cells(terrain: TerrainGrid) -> list[tuple[int, int]]:
    return [
        (c, r)
        for r in range(terrain.rows)
        for c in range(terrain.columns)
        if terrain.cells[r][c].terrain_type == "WATER"
    ]


def apply_sam_ir_radar_mix(
    sam_sites: list[SamSiteSpec],
    *,
    sam_ir_fraction: float,
    seed: int,
) -> list[SamSiteSpec]:
    """Assign SAM_IR vs SAM_RADAR magazines from LOADOUT_SALT (Phase 44)."""
    if not sam_sites:
        return sam_sites
    rng = random.Random(derived_seed(seed, LOADOUT_SALT))
    n_ir = int(round(len(sam_sites) * float(sam_ir_fraction)))
    n_ir = max(0, min(len(sam_sites), n_ir))
    indices = list(range(len(sam_sites)))
    rng.shuffle(indices)
    ir_set = set(indices[:n_ir])
    out: list[SamSiteSpec] = []
    for i, site in enumerate(sam_sites):
        if i in ir_set:
            loadout = {"SAM_IR": 12}
        else:
            loadout = {"SAM_RADAR": 12}
        out.append(site.model_copy(update={"missile_loadout": loadout}))
    out.sort(key=lambda s: s.target_id)
    return out


def apply_jet_flare_fraction(
    jets: list[JetSpec],
    *,
    jet_flare_fraction: float,
    seed: int,
) -> list[JetSpec]:
    """Scale flares_remaining from frame capacity using LOADOUT_SALT stream."""
    rng = random.Random(derived_seed(seed, LOADOUT_SALT) ^ 0xF1A5E001)
    frac = max(0.0, min(1.0, float(jet_flare_fraction)))
    out: list[JetSpec] = []
    for jet in jets:
        frame = config.JET_FRAME_CATALOG[jet.frame_type]
        cap = int(getattr(jet, "flare_capacity", None) or frame.flare_capacity)
        # Deterministic per-jet jitter ±0 so identical frac yields identical remaining.
        remaining = int(round(cap * frac))
        remaining = max(0, min(cap, remaining))
        # Tiny salt consumption so loadout stream advances with jet count.
        _ = rng.random()
        out.append(
            jet.model_copy(
                update={
                    "flare_capacity": cap,
                    "flares_remaining": remaining,
                    "flare_cooldown_ticks": int(frame.flare_cooldown_ticks),
                    "flare_quality": float(frame.flare_quality),
                }
            )
        )
    return out


def place_generated_naval(
    parameters: GenerationParameters,
    *,
    terrain: TerrainGrid,
    occupied: list[tuple[float, float]],
) -> tuple[list, list]:
    """Place optional carriers/corvettes on water (ENEMY_NAVAL_SALT)."""
    from wraithnet.naval import (
        CarrierSpec,
        CorvetteSpec,
        DEFAULT_CORVETTE_LOADOUT,
        NAVAL_FRAME_CATALOG,
        build_navigable_water_mask,
    )
    from wraithnet.terrain import cell_center, validate_position_for_domain

    carrier_n = int(getattr(parameters, "friendly_carrier_count", 0) or 0)
    corvette_n = int(getattr(parameters, "enemy_corvette_count", 0) or 0)
    if carrier_n <= 0 and corvette_n <= 0:
        return [], []

    water = _water_cells(terrain)
    if not water:
        raise GenerationError(
            PLACEMENT_EXHAUSTED,
            {
                "kind": "naval",
                "seed": parameters.seed,
                "requested_counts": {
                    "friendly_carrier_count": carrier_n,
                    "enemy_corvette_count": corvette_n,
                },
                "grid": (config.GRID_WIDTH, config.GRID_HEIGHT),
                "active_constraints": {"water_cells": 0},
                "suggestions": ["increase water_fraction", "enable naval terrain"],
            },
        )

    rng = random.Random(derived_seed(parameters.seed, ENEMY_NAVAL_SALT))
    occ = list(occupied)
    carriers: list[CarrierSpec] = []
    corvettes: list[CorvetteSpec] = []

    def _navigable_cells(footprint_radius: float) -> list[tuple[int, int]]:
        mask = build_navigable_water_mask(terrain, footprint_radius)
        return [
            (c, r)
            for r in range(terrain.rows)
            for c in range(terrain.columns)
            if mask[r][c]
        ]

    def _place_water(prefix: str, footprint_radius: float) -> tuple[float, float]:
        pool = _navigable_cells(footprint_radius)
        if not pool:
            raise GenerationError(
                PLACEMENT_EXHAUSTED,
                {
                    "kind": "naval",
                    "seed": parameters.seed,
                    "requested_counts": {"unit": prefix},
                    "grid": (config.GRID_WIDTH, config.GRID_HEIGHT),
                    "active_constraints": {
                        "navigable_water": 0,
                        "footprint_radius": footprint_radius,
                    },
                    "suggestions": [
                        "increase water_fraction",
                        "reduce naval footprint / counts",
                    ],
                },
            )
        for _ in range(parameters.max_placement_attempts):
            c, r = pool[rng.randrange(len(pool))]
            cand = cell_center(terrain, c, r)
            try:
                validate_position_for_domain(terrain, cand, "SURFACE")
            except Exception:
                continue
            if not position_in_bounds(cand, margin=parameters.map_margin):
                continue
            if not position_respects_separation(
                cand, occ, parameters.entity_min_separation
            ):
                continue
            return cand
        raise GenerationError(
            PLACEMENT_EXHAUSTED,
            {
                "kind": "naval",
                "seed": parameters.seed,
                "requested_counts": {"unit": prefix},
                "grid": (config.GRID_WIDTH, config.GRID_HEIGHT),
                "active_constraints": {"occupied": len(occ)},
                "suggestions": ["reduce naval counts", "increase water coverage"],
            },
        )

    carrier_frame = "ESCORT_CARRIER"
    for i in range(carrier_n):
        fp = NAVAL_FRAME_CATALOG[carrier_frame].footprint_radius
        pos = _place_water("carrier", fp)
        occ.append(pos)
        carriers.append(
            CarrierSpec(
                unit_id=_format_id("Carrier", i + 1),
                position=pos,
                frame_type=carrier_frame,  # type: ignore[arg-type]
                hold_position=True,
            )
        )
    tier = str(getattr(parameters, "corvette_defense_tier", "STANDARD") or "STANDARD")
    frame = "CORVETTE_LIGHT" if tier == "LIGHT" else "CORVETTE_STANDARD"
    loadout = dict(DEFAULT_CORVETTE_LOADOUT)
    if tier == "LIGHT":
        loadout = {"SAM_RADAR": 4, "CIWS": 20, "DECK_GUN": 10}
    for i in range(corvette_n):
        fp = NAVAL_FRAME_CATALOG[frame].footprint_radius
        pos = _place_water("corvette", fp)
        occ.append(pos)
        corvettes.append(
            CorvetteSpec(
                unit_id=_format_id("Corvette", i + 1),
                position=pos,
                frame_type=frame,  # type: ignore[arg-type]
                loadout=loadout,
                hold_position=True,
            )
        )
    carriers.sort(key=lambda c: c.unit_id)
    corvettes.sort(key=lambda c: c.unit_id)
    return carriers, corvettes


def generate_scenario(parameters: GenerationParameters) -> GenerationResult:
    """Full end-to-end generation: enemies, friendlies, feasibility, metadata."""
    enemies = generate_enemies(parameters)
    friendly_rng = random.Random(derived_seed(parameters.seed, FRIENDLY_SALT))

    build_state = _FriendlyBuildState(
        targets=[t.model_copy() for t in enemies.targets],
        sam_sites=[s.model_copy() for s in enemies.sam_sites],
    )
    # Phase 44: SAM IR/radar mix (loadout stream; does not move positions).
    build_state.sam_sites = apply_sam_ir_radar_mix(
        build_state.sam_sites,
        sam_ir_fraction=float(getattr(parameters, "sam_ir_fraction", 0.0) or 0.0),
        seed=parameters.seed,
    )

    # Place tanks/naval before roster packing so HP demand uses real positions.
    occupied = [enemies.home_base]
    occupied.extend(t.position for t in build_state.targets)
    occupied.extend(s.position for s in build_state.sam_sites)
    occupied.extend(j.position for j in enemies.jammers)
    occupied.extend(a.position for a in enemies.aaa_sites)
    tanks = place_generated_tanks(
        parameters,
        terrain=enemies.terrain,
        home_base=enemies.home_base,
        occupied=occupied,
    )
    occupied.extend(t.position for t in tanks)
    carriers, corvettes = place_generated_naval(
        parameters,
        terrain=enemies.terrain,
        occupied=occupied,
    )
    build_state.tanks = list(tanks)
    build_state.corvettes = list(corvettes)

    jets = generate_friendly_roster(
        parameters, enemies, friendly_rng, build_state=build_state
    )
    jets = apply_jet_flare_fraction(
        jets,
        jet_flare_fraction=float(getattr(parameters, "jet_flare_fraction", 1.0) or 1.0),
        seed=parameters.seed,
    )
    jets = place_jets_on_launch_ring(
        jets,
        home_base=enemies.home_base,
        parameters=parameters,
        sam_sites=build_state.sam_sites,
        jammers=list(enemies.jammers),
        rng=friendly_rng,
        aaa_sites=list(enemies.aaa_sites),
    )

    deliberate = list(build_state.deliberate_cooperative_ids)
    metadata = GenerationMetadata(
        generator_version=GENERATOR_VERSION,
        seed=parameters.seed,
        preset=parameters.preset,
        resolved_parameters=parameters,
        deliberate_cooperative_objective_ids=deliberate,
        modified_after_generation=False,
    )
    # DESTROY+RETURN when generated enemy tanks/corvettes are present.
    objectives = None
    enemy_tank_ids = [t.unit_id for t in tanks if t.faction == "ENEMY"]
    enemy_corvette_ids = [c.unit_id for c in corvettes]
    if enemy_tank_ids or enemy_corvette_ids:
        from wraithnet.objectives import default_strike_and_return_objectives

        subject_ids = [t.target_id for t in build_state.targets]
        subject_ids.extend(s.target_id for s in build_state.sam_sites)
        subject_ids.extend(enemy_tank_ids)
        subject_ids.extend(enemy_corvette_ids)
        objectives = default_strike_and_return_objectives(
            subject_ids, [j.agent_id for j in jets]
        )

    scenario = scenario_from_data(
        new_scenario_data(
            grid_width=config.GRID_WIDTH,
            grid_height=config.GRID_HEIGHT,
            jets=jets,
            targets=build_state.targets,
            sam_sites=build_state.sam_sites,
            jammers=list(enemies.jammers),
            aaa_sites=list(enemies.aaa_sites),
            tanks=tanks,
            carriers=carriers,
            corvettes=corvettes,
            home_base=enemies.home_base,
            terrain=enemies.terrain,
            combat_seed=parameters.seed,
            generation=metadata,
            **({"mission_objectives": objectives} if objectives is not None else {}),
        )
    )

    group_map = _group_ids_by_entity(enemies)
    centers = _canonical_group_centers(enemies)
    feasibility = analyze_feasibility(
        scenario,
        group_ids_by_entity=group_map,
        group_centers=centers,
    )

    # AUTO must be feasible by construction; surface capacity bugs early.
    if parameters.friendly_mode == "AUTO_BALANCED" and not feasibility.feasible:
        raise GenerationError(
            AUTO_BALANCE_CAPACITY,
            {
                "kind": "friendly_roster",
                "seed": parameters.seed,
                "requested_counts": {
                    "friendly_count_max": parameters.friendly_count_max,
                },
                "grid": (config.GRID_WIDTH, config.GRID_HEIGHT),
                "active_constraints": {
                    "error_codes": [
                        i.code for i in feasibility.issues if i.severity == "ERROR"
                    ],
                },
                "suggestions": [
                    "increase friendly_count_max",
                    "reduce enemy HP or counts",
                ],
            },
        )

    # INFO for unrealized cooperation candidates (AUTO only).
    if parameters.friendly_mode == "AUTO_BALANCED":
        extra_issues = list(feasibility.issues)
        for cid in build_state.unrealized_cooperative_ids:
            extra_issues.append(
                FeasibilityIssue(
                    severity="INFO",
                    code="COOPERATION_NOT_REALIZED",
                    message=(
                        f"Cooperation candidate '{cid}' could not be realized "
                        "within roster/count/HP bounds; left as an ordinary objective."
                    ),
                    related_ids=[cid],
                )
            )
        feasibility = feasibility.model_copy(update={"issues": extra_issues})

    return GenerationResult(
        scenario=scenario,
        parameters=parameters,
        feasibility=feasibility,
        groups=_group_summaries(enemies),
    )
