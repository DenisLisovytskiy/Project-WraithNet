"""Authoritative terrain grid, passability, and 2.5D line of sight.

Phase 34 — see docs/19_Terrain_Naval_and_Combined_Arms_Design_Document.md §§2–3.
No Panda3D/Pygame imports; shared by world, scenario schema, editor, and tests.
"""

from __future__ import annotations

import math
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

TerrainType = Literal["WATER", "PLAINS", "FOREST", "CITY"]
MovementDomain = Literal["AIR", "GROUND", "SURFACE"]

CITY_HEIGHT_MIN: float = 4.0
CITY_HEIGHT_MAX: float = 25.0

_PASSABLE: dict[tuple[TerrainType, MovementDomain], bool] = {
    ("WATER", "AIR"): True,
    ("WATER", "GROUND"): False,
    ("WATER", "SURFACE"): True,
    ("PLAINS", "AIR"): True,
    ("PLAINS", "GROUND"): True,
    ("PLAINS", "SURFACE"): False,
    ("FOREST", "AIR"): True,
    ("FOREST", "GROUND"): True,
    ("FOREST", "SURFACE"): False,
    ("CITY", "AIR"): True,
    ("CITY", "GROUND"): True,
    ("CITY", "SURFACE"): False,
}


class OutOfTerrainBoundsError(ValueError):
    """Raised when a world point lies strictly outside the terrain map."""


class TerrainCell(BaseModel):
    model_config = ConfigDict(extra="forbid")

    terrain_type: TerrainType = "PLAINS"
    building_height: float | None = None

    @model_validator(mode="after")
    def _validate_height_rules(self) -> TerrainCell:
        if self.terrain_type == "CITY":
            if self.building_height is None:
                raise ValueError("CITY cells require building_height")
            if not (CITY_HEIGHT_MIN <= self.building_height <= CITY_HEIGHT_MAX):
                raise ValueError(
                    f"CITY building_height must be in "
                    f"[{CITY_HEIGHT_MIN}, {CITY_HEIGHT_MAX}], "
                    f"got {self.building_height}"
                )
        elif self.building_height is not None:
            raise ValueError(
                f"{self.terrain_type} cells forbid building_height "
                f"(got {self.building_height})"
            )
        return self


class TerrainGrid(BaseModel):
    model_config = ConfigDict(extra="forbid")

    world_width: float = Field(gt=0)
    world_height: float = Field(gt=0)
    terrain_edit_seed: int = 0
    cell_size: float = Field(default=5.0, gt=0)
    columns: int = Field(gt=0)
    rows: int = Field(gt=0)
    cells: list[list[TerrainCell]]

    @model_validator(mode="after")
    def _validate_grid(self) -> TerrainGrid:
        expected_cols = math.ceil(self.world_width / self.cell_size)
        expected_rows = math.ceil(self.world_height / self.cell_size)
        if self.columns != expected_cols or self.rows != expected_rows:
            raise ValueError(
                f"columns/rows must be ceil(world/cell_size) "
                f"({expected_cols}x{expected_rows}), "
                f"got {self.columns}x{self.rows}"
            )
        if len(self.cells) != self.rows:
            raise ValueError(
                f"cells must have {self.rows} rows, got {len(self.cells)}"
            )
        for r, row in enumerate(self.cells):
            if len(row) != self.columns:
                raise ValueError(
                    f"row {r} must have {self.columns} columns, got {len(row)}"
                )
        return self


#: Cell size the PoC 100×100 map has always used, and the floor for any map.
DEFAULT_CELL_SIZE: float = 5.0

#: Cells along the longer side once a map is big enough to need coarser cells.
#: 100 keeps the PoC's 20×20 feel on a 100-unit map while capping a 5000-unit
#: battlespace at 100×100 = 10k cells instead of 1000×1000 = 1M.
TERRAIN_CELLS_ON_LONG_SIDE: float = 100.0


def terrain_cell_size_for_grid(grid_width: float, grid_height: float) -> float:
    """Cell size that keeps a map's cell count bounded as it grows.

    Maps up to ``DEFAULT_CELL_SIZE * TERRAIN_CELLS_ON_LONG_SIDE`` units get the
    historical 5.0, so PoC-scale scenarios are untouched; past that, cells grow
    with the map so cell count stays ~100 on the long side. Terrain generation
    and mesh building are both superlinear in cell count, so letting a 5000-unit
    map keep 5.0 would mean a million cells.
    """
    long_side = max(float(grid_width), float(grid_height))
    return max(DEFAULT_CELL_SIZE, long_side / TERRAIN_CELLS_ON_LONG_SIDE)


def all_plains_terrain(
    width: float, height: float, cell_size: float | None = None
) -> TerrainGrid:
    """Uniform PLAINS grid covering ``width`` × ``height`` world units.

    ``cell_size`` defaults to :func:`terrain_cell_size_for_grid` so a large
    battlespace cannot silently fall back to 5.0 and build a million cells.
    """
    if cell_size is None:
        cell_size = terrain_cell_size_for_grid(width, height)
    if cell_size <= 0:
        raise ValueError(f"cell_size must be positive, got {cell_size}")
    if width <= 0 or height <= 0:
        raise ValueError(f"world dimensions must be positive, got {width}x{height}")
    columns = math.ceil(width / cell_size)
    rows = math.ceil(height / cell_size)
    cells = [
        [TerrainCell(terrain_type="PLAINS") for _ in range(columns)]
        for _ in range(rows)
    ]
    return TerrainGrid(
        world_width=width,
        world_height=height,
        terrain_edit_seed=0,
        cell_size=cell_size,
        columns=columns,
        rows=rows,
        cells=cells,
    )


def _cell_bounds(
    terrain: TerrainGrid, col: int, row: int
) -> tuple[float, float, float, float]:
    """Return (x0, y0, x1, y1) world AABB of a cell (max-edge cells clipped)."""
    x0 = col * terrain.cell_size
    y0 = row * terrain.cell_size
    x1 = min((col + 1) * terrain.cell_size, terrain.world_width)
    y1 = min((row + 1) * terrain.cell_size, terrain.world_height)
    return x0, y0, x1, y1


def world_to_cell(
    terrain: TerrainGrid, x: float, y: float
) -> tuple[int, int]:
    """Map a world point to ``(column, row)``. Max-edge clamps to the final cell."""
    if x < 0.0 or y < 0.0 or x > terrain.world_width or y > terrain.world_height:
        raise OutOfTerrainBoundsError(
            f"point ({x}, {y}) is outside terrain "
            f"[0, {terrain.world_width}] x [0, {terrain.world_height}]"
        )
    if x >= terrain.world_width:
        col = terrain.columns - 1
    else:
        col = min(int(x / terrain.cell_size), terrain.columns - 1)
    if y >= terrain.world_height:
        row = terrain.rows - 1
    else:
        row = min(int(y / terrain.cell_size), terrain.rows - 1)
    return col, row


def cell_center(terrain: TerrainGrid, col: int, row: int) -> tuple[float, float]:
    """World-space center of a cell (uses clipped bounds on max edges)."""
    if not (0 <= col < terrain.columns and 0 <= row < terrain.rows):
        raise OutOfTerrainBoundsError(
            f"cell ({col}, {row}) out of range "
            f"{terrain.columns}x{terrain.rows}"
        )
    x0, y0, x1, y1 = _cell_bounds(terrain, col, row)
    return (x0 + x1) / 2.0, (y0 + y1) / 2.0


def is_passable(
    terrain: TerrainGrid,
    position: tuple[float, float],
    domain: MovementDomain,
) -> bool:
    col, row = world_to_cell(terrain, position[0], position[1])
    cell = terrain.cells[row][col]
    return _PASSABLE[(cell.terrain_type, domain)]


def validate_position_for_domain(
    terrain: TerrainGrid,
    position: tuple[float, float],
    domain: MovementDomain,
) -> None:
    """Raise ``ValueError`` if ``position`` is impassable for ``domain``."""
    if not is_passable(terrain, position, domain):
        col, row = world_to_cell(terrain, position[0], position[1])
        cell = terrain.cells[row][col]
        raise ValueError(
            f"position {position} is on {cell.terrain_type} which is "
            f"impassable for domain {domain}"
        )


def segment_supercover_cells(
    terrain: TerrainGrid,
    start: tuple[float, float],
    end: tuple[float, float],
) -> list[tuple[int, int]]:
    """Every cell a segment touches, in traversal order from ``start`` to ``end``.

    Uses an Amanatides–Woo-style walk over clipped cell boundaries. When the
    ray hits a grid corner exactly, both neighboring cells are visited so the
    cell set is independent of travel direction (LOS symmetry).
    """
    c0, r0 = world_to_cell(terrain, start[0], start[1])
    c1, r1 = world_to_cell(terrain, end[0], end[1])
    if (c0, r0) == (c1, r1):
        return [(c0, r0)]

    x0, y0 = start
    x1, y1 = end
    dx = x1 - x0
    dy = y1 - y0

    step_x = 0 if dx == 0.0 else (1 if dx > 0.0 else -1)
    step_y = 0 if dy == 0.0 else (1 if dy > 0.0 else -1)

    col, row = c0, r0
    cells: list[tuple[int, int]] = [(col, row)]

    # Parametric distance along the segment in [0, 1]. Avoid division by zero
    # by treating a zero component as never crossing that axis.
    def _t_to_boundary(pos: float, step: int, lo: float, hi: float, delta: float) -> float:
        if step > 0:
            return (hi - pos) / delta if delta != 0.0 else math.inf
        if step < 0:
            return (lo - pos) / delta if delta != 0.0 else math.inf
        return math.inf

    # Cap iterations: a segment crosses at most columns+rows cells, plus a
    # small corner-visit slack — never a full-map scan.
    max_steps = terrain.columns + terrain.rows + 2
    for _ in range(max_steps):
        if (col, row) == (c1, r1):
            break
        x_lo, y_lo, x_hi, y_hi = _cell_bounds(terrain, col, row)
        t_max_x = _t_to_boundary(x0, step_x, x_lo, x_hi, dx)
        t_max_y = _t_to_boundary(y0, step_y, y_lo, y_hi, dy)

        # Numerical tie → corner: visit both axis-adjacent cells so the set is
        # independent of travel direction (true supercover).
        if abs(t_max_x - t_max_y) <= 1e-12 * max(1.0, abs(t_max_x), abs(t_max_y)):
            if step_x == 0 and step_y == 0:
                break
            if step_x != 0 and step_y != 0:
                side_x = (col + step_x, row)
                side_y = (col, row + step_y)
                if 0 <= side_x[0] < terrain.columns:
                    cells.append(side_x)
                if 0 <= side_y[1] < terrain.rows:
                    cells.append(side_y)
                col += step_x
                row += step_y
                if not (0 <= col < terrain.columns and 0 <= row < terrain.rows):
                    break
                cells.append((col, row))
            elif step_x != 0:
                col += step_x
                if not (0 <= col < terrain.columns):
                    break
                cells.append((col, row))
            else:
                row += step_y
                if not (0 <= row < terrain.rows):
                    break
                cells.append((col, row))
        elif t_max_x < t_max_y:
            if step_x == 0:
                break
            col += step_x
            if not (0 <= col < terrain.columns):
                break
            cells.append((col, row))
        else:
            if step_y == 0:
                break
            row += step_y
            if not (0 <= row < terrain.rows):
                break
            cells.append((col, row))

    if cells[-1] != (c1, r1):
        # Floating-edge cases: ensure the endpoint cell is present.
        cells.append((c1, r1))
    return cells


def _segment_cell_t_range(
    terrain: TerrainGrid,
    start: tuple[float, float],
    end: tuple[float, float],
    col: int,
    row: int,
) -> tuple[float, float]:
    """Return parametric ``[t_enter, t_exit]`` of the XY segment inside a cell."""
    x0, y0 = start
    x1, y1 = end
    dx = x1 - x0
    dy = y1 - y0
    x_lo, y_lo, x_hi, y_hi = _cell_bounds(terrain, col, row)

    t0, t1 = 0.0, 1.0
    # Liang–Barsky style clip against the cell AABB in parameter space.
    for p, q in (
        (-dx, x0 - x_lo),
        (dx, x_hi - x0),
        (-dy, y0 - y_lo),
        (dy, y_hi - y0),
    ):
        if p == 0.0:
            if q < 0.0:
                return 0.0, 0.0  # parallel and outside — degenerate
            continue
        r = q / p
        if p < 0.0:
            if r > t1:
                return 0.0, 0.0
            if r > t0:
                t0 = r
        else:
            if r < t0:
                return 0.0, 0.0
            if r < t1:
                t1 = r
    return t0, t1


def has_line_of_sight(
    terrain: TerrainGrid,
    start: tuple[float, float, float],
    end: tuple[float, float, float],
) -> bool:
    """2.5D LOS: intermediate CITY columns block if they reach the segment.

    Start and end cells are ignored so an entity in a city street is not
    treated as inside a solid building. Forest is transparent. Result is
    symmetric: ``los(a, b) == los(b, a)``.
    """
    start_xy = (start[0], start[1])
    end_xy = (end[0], end[1])
    cells = segment_supercover_cells(terrain, start_xy, end_xy)
    if len(cells) <= 2:
        # Same cell, or only start+end — no intermediate blockers.
        # Still skip when start==end cell; when two cells, both are endpoints.
        if len(cells) == 1:
            return True
        # Two cells: each is an endpoint cell → no intermediates.
        return True

    z0, z1 = start[2], end[2]
    # Intermediate cells only (exclude start and end cells).
    intermediates = cells[1:-1]
    for col, row in intermediates:
        cell = terrain.cells[row][col]
        if cell.terrain_type != "CITY":
            continue
        assert cell.building_height is not None
        t_enter, t_exit = _segment_cell_t_range(terrain, start_xy, end_xy, col, row)
        z_enter = z0 + (z1 - z0) * t_enter
        z_exit = z0 + (z1 - z0) * t_exit
        if cell.building_height >= min(z_enter, z_exit):
            return False
    return True
