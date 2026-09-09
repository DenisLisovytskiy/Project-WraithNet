"""Pure terrain painting / relocation helpers (Phase 36).

No Panda3D/Pygame. Shared by the editor and headless tests.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable, Mapping, Sequence
from typing import Literal

from wraithnet.terrain import (
    CITY_HEIGHT_MAX,
    CITY_HEIGHT_MIN,
    OutOfTerrainBoundsError,
    TerrainCell,
    TerrainGrid,
    cell_center,
    is_passable,
    segment_supercover_cells,
    world_to_cell,
)

BrushType = Literal["WATER", "PLAINS", "FOREST", "CITY"]
CityHeightMode = Literal["RANDOM", "FIXED"]

_MASK64 = (1 << 64) - 1


def cells_in_brush(
    terrain: TerrainGrid,
    col: int,
    row: int,
    radius: int,
) -> list[tuple[int, int]]:
    """Chebyshev disk of cells around ``(col, row)``, clipped to the map."""
    radius = max(1, min(5, int(radius)))
    out: list[tuple[int, int]] = []
    for r in range(max(0, row - radius + 1), min(terrain.rows, row + radius)):
        for c in range(max(0, col - radius + 1), min(terrain.columns, col + radius)):
            if max(abs(c - col), abs(r - row)) < radius:
                out.append((c, r))
    return out


def stroke_cells(
    terrain: TerrainGrid,
    samples: Sequence[tuple[float, float]],
    radius: int,
) -> list[tuple[int, int]]:
    """Cells touched by a drag path: supercover between samples + brush disks."""
    if not samples:
        return []
    centers: list[tuple[int, int]] = []
    for pos in samples:
        try:
            centers.append(world_to_cell(terrain, pos[0], pos[1]))
        except OutOfTerrainBoundsError:
            continue
    if not centers:
        return []

    visited: set[tuple[int, int]] = set()
    ordered: list[tuple[int, int]] = []

    def _add(cell: tuple[int, int]) -> None:
        if cell not in visited:
            visited.add(cell)
            ordered.append(cell)

    for cell in cells_in_brush(terrain, centers[0][0], centers[0][1], radius):
        _add(cell)

    for i in range(1, len(centers)):
        a = cell_center(terrain, centers[i - 1][0], centers[i - 1][1])
        b = cell_center(terrain, centers[i][0], centers[i][1])
        for col, row in segment_supercover_cells(terrain, a, b):
            for cell in cells_in_brush(terrain, col, row, radius):
                _add(cell)
    return ordered


def deterministic_city_height(
    terrain_edit_seed: int,
    col: int,
    row: int,
    stroke_sequence: int,
    *,
    height_min: float = CITY_HEIGHT_MIN,
    height_max: float = CITY_HEIGHT_MAX,
) -> float:
    """Stable height from edit seed + cell + stroke index (no process RNG)."""
    x = (
        int(terrain_edit_seed)
        ^ (col * 0x9E3779B1)
        ^ (row * 0x85EBCA77)
        ^ (stroke_sequence * 0xC2B2AE3D)
    ) & _MASK64
    x = (x ^ (x >> 30)) * 0xBF58476D1CE4E5B9 & _MASK64
    x = (x ^ (x >> 27)) * 0x94D049BB133111EB & _MASK64
    x ^= x >> 31
    t = (x & 0xFFFFFF) / float(0xFFFFFF)
    return height_min + t * (height_max - height_min)


def apply_paint_to_cells(
    terrain: TerrainGrid,
    cells: Iterable[tuple[int, int]],
    brush: BrushType,
    *,
    city_height_mode: CityHeightMode = "RANDOM",
    fixed_city_height: float = 10.0,
    stroke_sequence: int = 0,
) -> TerrainGrid:
    """Return a deep-copied grid with ``cells`` painted. Never mutates input."""
    new_cells = [[copy.deepcopy(cell) for cell in row] for row in terrain.cells]
    fixed = min(CITY_HEIGHT_MAX, max(CITY_HEIGHT_MIN, float(fixed_city_height)))
    for col, row in cells:
        if not (0 <= col < terrain.columns and 0 <= row < terrain.rows):
            continue
        if brush == "CITY":
            if city_height_mode == "FIXED":
                height = fixed
            else:
                height = deterministic_city_height(
                    terrain.terrain_edit_seed, col, row, stroke_sequence
                )
            new_cells[row][col] = TerrainCell(
                terrain_type="CITY", building_height=height
            )
        else:
            new_cells[row][col] = TerrainCell(terrain_type=brush)
    return TerrainGrid(
        world_width=terrain.world_width,
        world_height=terrain.world_height,
        terrain_edit_seed=terrain.terrain_edit_seed,
        cell_size=terrain.cell_size,
        columns=terrain.columns,
        rows=terrain.rows,
        cells=new_cells,
    )


def fill_terrain(
    terrain: TerrainGrid,
    brush: BrushType,
    *,
    city_height_mode: CityHeightMode = "RANDOM",
    fixed_city_height: float = 10.0,
    stroke_sequence: int = 0,
) -> TerrainGrid:
    all_cells = [
        (c, r) for r in range(terrain.rows) for c in range(terrain.columns)
    ]
    return apply_paint_to_cells(
        terrain,
        all_cells,
        brush,
        city_height_mode=city_height_mode,
        fixed_city_height=fixed_city_height,
        stroke_sequence=stroke_sequence,
    )


def terrain_from_draft(raw: Mapping | TerrainGrid) -> TerrainGrid:
    if isinstance(raw, TerrainGrid):
        return raw
    return TerrainGrid.model_validate(raw)


def invalid_ground_entities(draft: Mapping) -> list[tuple[str, str]]:
    """Return ``(label, reason)`` for entities on impassable domain cells.

    GROUND entities (targets/SAMs/jammers/home/AAA/tanks) on water/bounds, and
    SURFACE vessels (carriers/corvettes) on land/bounds. Relocate only moves
    GROUND entities — naval issues are highlight/banner only.
    """
    terrain = terrain_from_draft(draft["terrain"])
    issues: list[tuple[str, str]] = []

    def _check(
        label: str,
        position: tuple[float, float],
        domain: Literal["GROUND", "SURFACE"],
        impassable_reason: str,
    ) -> None:
        try:
            if not is_passable(terrain, position, domain):
                issues.append((label, impassable_reason))
        except OutOfTerrainBoundsError:
            issues.append((label, "outside terrain bounds"))

    for t in draft.get("targets") or []:
        pos = tuple(t["position"])
        _check(
            f"Target '{t['target_id']}'",
            (float(pos[0]), float(pos[1])),
            "GROUND",
            "impassable GROUND terrain",
        )
    for s in draft.get("sam_sites") or []:
        pos = tuple(s["position"])
        _check(
            f"SAM '{s['target_id']}'",
            (float(pos[0]), float(pos[1])),
            "GROUND",
            "impassable GROUND terrain",
        )
    for a in draft.get("aaa_sites") or []:
        pos = tuple(a["position"])
        _check(
            f"AAA '{a['target_id']}'",
            (float(pos[0]), float(pos[1])),
            "GROUND",
            "impassable GROUND terrain",
        )
    for tank in draft.get("tanks") or []:
        pos = tuple(tank["position"])
        _check(
            f"Tank '{tank['unit_id']}'",
            (float(pos[0]), float(pos[1])),
            "GROUND",
            "impassable GROUND terrain",
        )
    for j in draft.get("jammers") or []:
        pos = tuple(j["position"])
        _check(
            f"Jammer '{j['jammer_id']}'",
            (float(pos[0]), float(pos[1])),
            "GROUND",
            "impassable GROUND terrain",
        )
    home = draft.get("home_base")
    if home is not None:
        _check(
            "Home base",
            (float(home[0]), float(home[1])),
            "GROUND",
            "impassable GROUND terrain",
        )
    for c in draft.get("carriers") or []:
        pos = tuple(c["position"])
        _check(
            f"Carrier '{c['unit_id']}'",
            (float(pos[0]), float(pos[1])),
            "SURFACE",
            "not on navigable WATER",
        )
    for c in draft.get("corvettes") or []:
        pos = tuple(c["position"])
        _check(
            f"Corvette '{c['unit_id']}'",
            (float(pos[0]), float(pos[1])),
            "SURFACE",
            "not on navigable WATER",
        )
    return issues


def _land_cell_centers(terrain: TerrainGrid) -> list[tuple[float, float]]:
    centers: list[tuple[float, float]] = []
    for r in range(terrain.rows):
        for c in range(terrain.columns):
            if terrain.cells[r][c].terrain_type != "WATER":
                centers.append(cell_center(terrain, c, r))
    return centers


def relocate_invalid_entities(
    draft: Mapping,
    *,
    min_separation: float = 3.0,
) -> dict:
    """Deterministic nearest-land relocation for invalid GROUND entities.

    Relocates targets, SAMs, AAA, tanks, jammers, and home base onto passable
    GROUND. Jets (AIR) and naval vessels (SURFACE) are left unchanged — ships
    on land stay put for highlight/banner repair rather than silent move onto
    land or water guessing.

    Returns a new draft dict. Never deletes.
    """
    out = copy.deepcopy(dict(draft))
    terrain = terrain_from_draft(out["terrain"])
    land = _land_cell_centers(terrain)
    if not land:
        return out

    occupied: list[tuple[float, float]] = []
    for jet in out.get("jets") or []:
        occupied.append((float(jet["position"][0]), float(jet["position"][1])))
    for c in out.get("carriers") or []:
        occupied.append((float(c["position"][0]), float(c["position"][1])))
    for c in out.get("corvettes") or []:
        occupied.append((float(c["position"][0]), float(c["position"][1])))

    def _place(pos: tuple[float, float]) -> tuple[float, float]:
        try:
            if is_passable(terrain, pos, "GROUND") and all(
                (pos[0] - o[0]) ** 2 + (pos[1] - o[1]) ** 2 >= min_separation**2
                for o in occupied
            ):
                return pos
        except OutOfTerrainBoundsError:
            pass
        # Stable nearest: sort land by distance, then x, then y.
        ranked = sorted(
            land,
            key=lambda c: (
                (c[0] - pos[0]) ** 2 + (c[1] - pos[1]) ** 2,
                c[0],
                c[1],
            ),
        )
        for cand in ranked:
            if all(
                (cand[0] - o[0]) ** 2 + (cand[1] - o[1]) ** 2 >= min_separation**2
                for o in occupied
            ):
                return cand
        return ranked[0]

    for t in out.get("targets") or []:
        old = (float(t["position"][0]), float(t["position"][1]))
        new = _place(old)
        t["position"] = [new[0], new[1]]
        occupied.append(new)
    for s in out.get("sam_sites") or []:
        old = (float(s["position"][0]), float(s["position"][1]))
        new = _place(old)
        s["position"] = [new[0], new[1]]
        occupied.append(new)
    for a in out.get("aaa_sites") or []:
        old = (float(a["position"][0]), float(a["position"][1]))
        new = _place(old)
        a["position"] = [new[0], new[1]]
        occupied.append(new)
    for tank in out.get("tanks") or []:
        old = (float(tank["position"][0]), float(tank["position"][1]))
        new = _place(old)
        tank["position"] = [new[0], new[1]]
        occupied.append(new)
    for j in out.get("jammers") or []:
        old = (float(j["position"][0]), float(j["position"][1]))
        new = _place(old)
        j["position"] = [new[0], new[1]]
        occupied.append(new)
    if out.get("home_base") is not None:
        old = (float(out["home_base"][0]), float(out["home_base"][1]))
        new = _place(old)
        out["home_base"] = [new[0], new[1]]
        occupied.append(new)
    return out
