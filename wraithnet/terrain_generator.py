"""Deterministic procedural terrain generation (Phase 35).

Stdlib only — no Panda3D/Pygame. Same ``(grid, parameters, seed)`` always
yields identical ``TerrainGrid`` JSON; never touches the process-global RNG.
"""

from __future__ import annotations

import math
import random
from collections import deque
from dataclasses import dataclass

from wraithnet.generation_schemas import TerrainGenerationParameters
from wraithnet.terrain import (
    TerrainCell,
    TerrainGrid,
    TerrainType,
)

# Coverage may miss the exact fraction by at most this many cells, or 3% of
# the map — whichever is larger (Phase 35 acceptance).
COVERAGE_TOLERANCE_CELLS_MIN: int = 2
COVERAGE_TOLERANCE_FRACTION: float = 0.03

# Low-resolution noise upsample factor for coherent water blobs.
_NOISE_BLOCK: int = 4


class TerrainGenerationError(Exception):
    """Structured failure when terrain constraints cannot be satisfied."""

    def __init__(self, code: str, details: dict) -> None:
        self.code = code
        self.details = details
        super().__init__(self._format())

    def _format(self) -> str:
        suggestions = self.details.get("suggestions", [])
        hint = ""
        if isinstance(suggestions, (list, tuple)) and suggestions:
            hint = " Suggestions: " + "; ".join(str(s) for s in suggestions)
        return f"Terrain generation failed ({self.code}).{hint}"


def coverage_tolerance_cells(total_cells: int) -> int:
    return max(
        COVERAGE_TOLERANCE_CELLS_MIN,
        int(math.ceil(COVERAGE_TOLERANCE_FRACTION * total_cells)),
    )


def _neighbors4(col: int, row: int, columns: int, rows: int) -> list[tuple[int, int]]:
    out: list[tuple[int, int]] = []
    if col > 0:
        out.append((col - 1, row))
    if col + 1 < columns:
        out.append((col + 1, row))
    if row > 0:
        out.append((col, row - 1))
    if row + 1 < rows:
        out.append((col, row + 1))
    return out


def _connected_components(
    mask: list[list[bool]],
) -> list[list[tuple[int, int]]]:
    rows = len(mask)
    cols = len(mask[0]) if rows else 0
    seen = [[False] * cols for _ in range(rows)]
    components: list[list[tuple[int, int]]] = []
    for r in range(rows):
        for c in range(cols):
            if not mask[r][c] or seen[r][c]:
                continue
            comp: list[tuple[int, int]] = []
            queue: deque[tuple[int, int]] = deque([(c, r)])
            seen[r][c] = True
            while queue:
                cc, rr = queue.popleft()
                comp.append((cc, rr))
                for nc, nr in _neighbors4(cc, rr, cols, rows):
                    if mask[nr][nc] and not seen[nr][nc]:
                        seen[nr][nc] = True
                        queue.append((nc, nr))
            components.append(comp)
    components.sort(key=len, reverse=True)
    return components


def _largest_component_size(mask: list[list[bool]]) -> int:
    comps = _connected_components(mask)
    return len(comps[0]) if comps else 0


def _count_true(mask: list[list[bool]]) -> int:
    return sum(1 for row in mask for v in row if v)


@dataclass
class _NoiseGrid:
    values: list[list[float]]


def _seeded_value_noise(
    columns: int,
    rows: int,
    rng: random.Random,
    block: int = _NOISE_BLOCK,
) -> _NoiseGrid:
    """Blocky value noise upsampled with bilinear interpolation."""
    coarse_cols = max(2, math.ceil(columns / block) + 1)
    coarse_rows = max(2, math.ceil(rows / block) + 1)
    coarse = [
        [rng.random() for _ in range(coarse_cols)] for _ in range(coarse_rows)
    ]
    values = [[0.0] * columns for _ in range(rows)]
    for r in range(rows):
        gy = r / block
        y0 = min(int(gy), coarse_rows - 2)
        ty = gy - y0
        for c in range(columns):
            gx = c / block
            x0 = min(int(gx), coarse_cols - 2)
            tx = gx - x0
            v00 = coarse[y0][x0]
            v10 = coarse[y0][x0 + 1]
            v01 = coarse[y0 + 1][x0]
            v11 = coarse[y0 + 1][x0 + 1]
            top = v00 * (1.0 - tx) + v10 * tx
            bot = v01 * (1.0 - tx) + v11 * tx
            values[r][c] = top * (1.0 - ty) + bot * ty
    return _NoiseGrid(values=values)


def _threshold_water(
    noise: _NoiseGrid, water_fraction: float
) -> list[list[bool]]:
    """``True`` = water. Exact target count via sorted threshold."""
    rows = len(noise.values)
    cols = len(noise.values[0])
    total = rows * cols
    target = int(round(water_fraction * total))
    target = max(0, min(total, target))
    flat = [
        (noise.values[r][c], c, r)
        for r in range(rows)
        for c in range(cols)
    ]
    # Stable order: value, then col, then row.
    flat.sort(key=lambda t: (t[0], t[1], t[2]))
    water = [[False] * cols for _ in range(rows)]
    for i in range(target):
        _, c, r = flat[i]
        water[r][c] = True
    return water


def _smooth_water(water: list[list[bool]], passes: int) -> list[list[bool]]:
    """Majority cellular smoothing; preserves rectangular bounds."""
    if passes <= 0:
        return water
    rows = len(water)
    cols = len(water[0])
    current = [row[:] for row in water]
    for _ in range(passes):
        nxt = [row[:] for row in current]
        for r in range(rows):
            for c in range(cols):
                votes = 1 if current[r][c] else 0
                ncount = 1
                for nc, nr in _neighbors4(c, r, cols, rows):
                    ncount += 1
                    if current[nr][nc]:
                        votes += 1
                # Strict majority → water; ties keep current.
                if votes * 2 > ncount:
                    nxt[r][c] = True
                elif votes * 2 < ncount:
                    nxt[r][c] = False
        current = nxt
    return current


def _retarget_count(
    water: list[list[bool]],
    target_water: int,
    rng: random.Random,
) -> list[list[bool]]:
    """Grow/shrink water toward ``target_water`` via random boundary flips."""
    rows = len(water)
    cols = len(water[0])
    current = [row[:] for row in water]
    total = rows * cols
    target_water = max(0, min(total, target_water))

    def water_count() -> int:
        return _count_true(current)

    # Bounded: at most total cells flips.
    for _ in range(total * 2):
        n = water_count()
        if n == target_water:
            break
        if n < target_water:
            # Flip a land cell adjacent to water (or any land if isolated).
            candidates = [
                (c, r)
                for r in range(rows)
                for c in range(cols)
                if not current[r][c]
                and any(
                    current[nr][nc]
                    for nc, nr in _neighbors4(c, r, cols, rows)
                )
            ]
            if not candidates:
                candidates = [
                    (c, r)
                    for r in range(rows)
                    for c in range(cols)
                    if not current[r][c]
                ]
            if not candidates:
                break
            c, r = candidates[rng.randrange(len(candidates))]
            current[r][c] = True
        else:
            candidates = [
                (c, r)
                for r in range(rows)
                for c in range(cols)
                if current[r][c]
                and any(
                    not current[nr][nc]
                    for nc, nr in _neighbors4(c, r, cols, rows)
                )
            ]
            if not candidates:
                candidates = [
                    (c, r)
                    for r in range(rows)
                    for c in range(cols)
                    if current[r][c]
                ]
            if not candidates:
                break
            c, r = candidates[rng.randrange(len(candidates))]
            current[r][c] = False
    return current


def _enforce_min_component(
    water: list[list[bool]],
    *,
    want_water: bool,
    minimum: int,
    rng: random.Random,
    total_cells: int,
    target_water: int,
    tolerance: int,
) -> list[list[bool]]:
    """Grow the largest matching component until ``minimum`` or fail."""
    if minimum <= 0:
        return water
    current = [row[:] for row in water]
    rows = len(current)
    cols = len(current[0])

    for _ in range(total_cells * 3):
        mask = [
            [current[r][c] == want_water for c in range(cols)]
            for r in range(rows)
        ]
        comps = _connected_components(mask)
        if comps and len(comps[0]) >= minimum:
            return current
        if not comps:
            # Seed a single cell of the desired type.
            landish = [
                (c, r)
                for r in range(rows)
                for c in range(cols)
                if current[r][c] != want_water
            ]
            if not landish:
                break
            c, r = landish[rng.randrange(len(landish))]
            current[r][c] = want_water
            continue

        # Expand largest component into adjacent opposite cells.
        largest = comps[0]
        border: list[tuple[int, int]] = []
        in_largest = set(largest)
        for c, r in largest:
            for nc, nr in _neighbors4(c, r, cols, rows):
                if (nc, nr) not in in_largest and current[nr][nc] != want_water:
                    border.append((nc, nr))
        if not border:
            break
        # Prefer expanding without wrecking coverage beyond tolerance.
        c, r = border[rng.randrange(len(border))]
        current[r][c] = want_water
        # Soft coverage repair if we drifted too far.
        n_water = _count_true(current)
        if abs(n_water - target_water) > tolerance:
            current = _retarget_count(current, target_water, rng)

    mask = [
        [current[r][c] == want_water for c in range(cols)]
        for r in range(rows)
    ]
    if _largest_component_size(mask) >= minimum:
        return current
    kind = "water" if want_water else "land"
    raise TerrainGenerationError(
        "COMPONENT_CONSTRAINT",
        {
            "kind": kind,
            "minimum": minimum,
            "achieved": _largest_component_size(mask),
            "suggestions": [
                f"reduce minimum_{kind}_component_cells",
                "adjust water_fraction",
                "increase smoothing_passes or change seed",
            ],
        },
    )


def _assign_forest_and_city(
    water: list[list[bool]],
    parameters: TerrainGenerationParameters,
    rng: random.Random,
) -> list[list[TerrainType]]:
    rows = len(water)
    cols = len(water[0])
    types: list[list[TerrainType]] = [
        ["WATER" if water[r][c] else "PLAINS" for c in range(cols)]
        for r in range(rows)
    ]
    land = [(c, r) for r in range(rows) for c in range(cols) if not water[r][c]]
    land_n = len(land)
    if land_n == 0:
        return types

    city_target = int(round(parameters.city_fraction * land_n))
    forest_target = int(round(parameters.forest_fraction * land_n))
    # Keep sum within land.
    if city_target + forest_target > land_n:
        forest_target = max(0, land_n - city_target)

    city_cells: set[tuple[int, int]] = set()
    if city_target > 0:
        # Seed cluster centers, then grow by Chebyshev radius preference.
        remaining = list(land)
        rng.shuffle(remaining)
        seeds: list[tuple[int, int]] = []
        n_seeds = max(1, min(city_target, max(1, city_target // max(1, parameters.city_cluster_radius * 2))))
        for i in range(min(n_seeds, len(remaining))):
            seeds.append(remaining[i])
        # Rank all land by distance to nearest seed (clustered fill).
        def cluster_key(cell: tuple[int, int]) -> tuple[int, int, int]:
            c, r = cell
            best = min(
                max(abs(c - sc), abs(r - sr)) for sc, sr in seeds
            )
            return (best, c, r)

        ranked = sorted(land, key=cluster_key)
        for cell in ranked:
            if len(city_cells) >= city_target:
                break
            # Soft radius preference: still fill beyond radius if needed.
            city_cells.add(cell)

    forest_cells: set[tuple[int, int]] = set()
    if forest_target > 0:
        leftover = [cell for cell in land if cell not in city_cells]
        rng.shuffle(leftover)
        for cell in leftover[:forest_target]:
            forest_cells.add(cell)

    for c, r in city_cells:
        types[r][c] = "CITY"
    for c, r in forest_cells:
        types[r][c] = "FOREST"
    return types


def generate_terrain(
    grid_width: float,
    grid_height: float,
    parameters: TerrainGenerationParameters,
    *,
    seed: int,
    cell_size: float = 5.0,
) -> TerrainGrid:
    """Build a coherent ``TerrainGrid`` from ``parameters`` and ``seed``."""
    if cell_size <= 0 or grid_width <= 0 or grid_height <= 0:
        raise TerrainGenerationError(
            "INVALID_DIMENSIONS",
            {
                "grid": (grid_width, grid_height),
                "cell_size": cell_size,
                "suggestions": ["use positive world dimensions and cell_size"],
            },
        )

    columns = math.ceil(grid_width / cell_size)
    rows = math.ceil(grid_height / cell_size)
    total = columns * rows
    tolerance = coverage_tolerance_cells(total)
    target_water = int(round(parameters.water_fraction * total))
    target_water = max(0, min(total, target_water))

    # Impossible: not enough land cells for the land component requirement.
    max_land = total - target_water
    if parameters.minimum_land_component_cells > max_land + tolerance:
        raise TerrainGenerationError(
            "IMPOSSIBLE_COVERAGE",
            {
                "kind": "land",
                "minimum_land_component_cells": parameters.minimum_land_component_cells,
                "approx_land_cells": max_land,
                "suggestions": [
                    "reduce water_fraction",
                    "reduce minimum_land_component_cells",
                ],
            },
        )
    if (
        parameters.minimum_water_component_cells > 0
        and parameters.minimum_water_component_cells > target_water + tolerance
    ):
        raise TerrainGenerationError(
            "IMPOSSIBLE_COVERAGE",
            {
                "kind": "water",
                "minimum_water_component_cells": parameters.minimum_water_component_cells,
                "approx_water_cells": target_water,
                "suggestions": [
                    "increase water_fraction",
                    "reduce minimum_water_component_cells",
                ],
            },
        )

    rng = random.Random(int(seed) & ((1 << 64) - 1))
    noise = _seeded_value_noise(columns, rows, rng)
    water = _threshold_water(noise, parameters.water_fraction)
    water = _smooth_water(water, parameters.smoothing_passes)
    water = _retarget_count(water, target_water, rng)

    water = _enforce_min_component(
        water,
        want_water=False,
        minimum=parameters.minimum_land_component_cells,
        rng=rng,
        total_cells=total,
        target_water=target_water,
        tolerance=tolerance,
    )
    if parameters.minimum_water_component_cells > 0:
        water = _enforce_min_component(
            water,
            want_water=True,
            minimum=parameters.minimum_water_component_cells,
            rng=rng,
            total_cells=total,
            target_water=target_water,
            tolerance=tolerance,
        )

    # Final coverage nudge within tolerance (never erase all requested water).
    water = _retarget_count(water, target_water, rng)
    actual_water = _count_true(water)
    if abs(actual_water - target_water) > tolerance:
        raise TerrainGenerationError(
            "COVERAGE_TOLERANCE",
            {
                "target_water": target_water,
                "actual_water": actual_water,
                "tolerance": tolerance,
                "suggestions": [
                    "relax water_fraction",
                    "reduce component minimums",
                ],
            },
        )

    types = _assign_forest_and_city(water, parameters, rng)

    # City heights — one draw per city cell, persisted.
    h_min = parameters.building_height_min
    h_max = parameters.building_height_max
    cells: list[list[TerrainCell]] = []
    for r in range(rows):
        row_cells: list[TerrainCell] = []
        for c in range(columns):
            t = types[r][c]
            if t == "CITY":
                # Inclusive float range via discrete millisteps for JSON stability.
                steps = max(1, int(round((h_max - h_min) * 1000.0)))
                height = h_min + rng.randint(0, steps) / 1000.0
                height = min(h_max, max(h_min, height))
                row_cells.append(
                    TerrainCell(terrain_type="CITY", building_height=height)
                )
            else:
                row_cells.append(TerrainCell(terrain_type=t))
        cells.append(row_cells)

    return TerrainGrid(
        world_width=grid_width,
        world_height=grid_height,
        terrain_edit_seed=int(seed) & ((1 << 64) - 1),
        cell_size=cell_size,
        columns=columns,
        rows=rows,
        cells=cells,
    )
