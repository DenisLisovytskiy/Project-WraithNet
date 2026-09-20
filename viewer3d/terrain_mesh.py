"""Batched terrain geometry for the 3D viewer (Phase 36).

Uses only stored TerrainGrid cells/heights — no render-time randomness.
No JPEG albedos: plains, forest, water, and city are procedural meshes.
City facades get a simple window grid so the blocks read as buildings.
"""

from __future__ import annotations

import math

from panda3d.core import (
    Geom,
    GeomNode,
    GeomTriangles,
    GeomVertexData,
    GeomVertexFormat,
    GeomVertexWriter,
    LineSegs,
    NodePath,
    TransparencyAttrib,
)

from viewer3d.ui_chrome import BORDER_SOFT, tint
from wraithnet.terrain import TerrainGrid, all_plains_terrain
from wraithnet.trace_schemas import TraceHeader

Color = tuple[float, float, float, float]
Vec3 = tuple[float, float, float]

WATER_COLOR = (0.020, 0.098, 0.170, 0.78)
PLAINS_COLOR = (0.014, 0.030, 0.042, 1.0)
FOREST_BASE_COLOR = (0.018, 0.052, 0.048, 1.0)
CITY_BASE_COLOR = (0.030, 0.056, 0.070, 1.0)
TREE_COLOR = (0.072, 0.215, 0.165, 1.0)
TRUNK_COLOR = (0.045, 0.070, 0.040, 1.0)
CANOPY_TOP_COLOR = (0.10, 0.28, 0.18, 1.0)
SCRUB_COLOR = (0.040, 0.090, 0.055, 1.0)
BUILDING_COLOR = (0.050, 0.082, 0.108, 1.0)
ROOF_COLOR = (0.038, 0.062, 0.082, 1.0)
WINDOW_LIT = (0.55, 0.82, 1.0, 0.92)
WINDOW_DARK = (0.035, 0.055, 0.070, 1.0)
BUILDING_EDGE_COLOR = tint(BORDER_SOFT, 1.0, alpha=0.62)

TERRAIN_TILE_WORLD = 12.0

#: Cells per chunk edge, once a grid is big enough for chunking to pay off.
#: Each chunk is its own GeomNode, so Panda3D can frustum-cull it; one
#: map-sized batch has a map-sized bounding volume and is never culled.
TERRAIN_CHUNK_CELLS = 16

#: Grids at or below this many cells stay a single batch per terrain type.
#: The PoC 100x100 map is 400 cells: culling would not pay for the extra draw
#: calls there, and keeping one batch keeps that map's geometry untouched.
TERRAIN_CHUNK_MIN_CELLS = 1024

#: Above this cell size, per-cell props (trees, scrub, windows, building
#: edges) are dropped. Their density was written against 5-unit cells and is
#: derived from cell span, so it explodes as cells grow -- and on a map whose
#: cells are this large they are sub-pixel from any useful camera distance.
PROP_DETAIL_MAX_CELL_SIZE = 12.0

#: Target footprint of a single city building, in world units. A CITY cell is
#: a district, not a building: on a large map it subdivides into blocks this
#: size rather than becoming one cell-sized slab.
CITY_BLOCK_WORLD = 14.0

#: Cap on buildings per city-cell edge, so one cell stays a handful of boxes.
CITY_BLOCKS_MAX = 3


def resolve_header_terrain(header: TraceHeader) -> TerrainGrid:
    if header.terrain is not None:
        return header.terrain
    return all_plains_terrain(header.grid_width, header.grid_height)


def _cell_bounds(
    terrain: TerrainGrid, col: int, row: int
) -> tuple[float, float, float, float]:
    x0 = col * terrain.cell_size
    y0 = row * terrain.cell_size
    x1 = min((col + 1) * terrain.cell_size, terrain.world_width)
    y1 = min((row + 1) * terrain.cell_size, terrain.world_height)
    return x0, y0, x1, y1


def _hash_u32(col: int, row: int, seed: int, salt: int = 0) -> int:
    h = (seed ^ salt ^ (col * 73856093) ^ (row * 19349663)) & 0xFFFFFFFF
    h = (h * 1664525 + 1013904223) & 0xFFFFFFFF
    return h


def _hash01(col: int, row: int, seed: int, salt: int = 0) -> float:
    return ((_hash_u32(col, row, seed, salt) >> 8) & 0xFFFF) / 65535.0


def _lerp_color(a: Color, b: Color, t: float) -> Color:
    t = max(0.0, min(1.0, t))
    return (
        a[0] + (b[0] - a[0]) * t,
        a[1] + (b[1] - a[1]) * t,
        a[2] + (b[2] - a[2]) * t,
        a[3] + (b[3] - a[3]) * t,
    )


def _water_z(x: float, y: float) -> float:
    return 0.10 * math.sin(x * 0.62) * math.cos(y * 0.48) + 0.05 * math.sin(
        x * 1.35 - y * 0.90
    )


def _plains_z(x: float, y: float) -> float:
    return 0.12 * math.sin(x * 0.38) * math.cos(y * 0.33) + 0.05 * math.sin(
        x * 0.95 + y * 0.70
    )


def _forest_z(x: float, y: float) -> float:
    return 0.10 * math.sin(x * 0.44 + 1.7) * math.cos(y * 0.51) + 0.04 * math.sin(
        x * 1.1 - y * 0.4
    )


def _water_color(x: float, y: float) -> Color:
    t = 0.5 + 0.5 * math.sin(x * 0.55 + y * 0.40)
    deep = (0.012, 0.055, 0.110, 0.82)
    streak = (0.040, 0.18, 0.28, 0.72)
    return _lerp_color(deep, streak, t)


def _plains_color(x: float, y: float) -> Color:
    t = 0.5 + 0.5 * math.sin(x * 0.28) * math.cos(y * 0.31)
    dirt = (0.018, 0.036, 0.032, 1.0)
    scrub = (0.028, 0.058, 0.040, 1.0)
    return _lerp_color(dirt, scrub, t)


def _forest_floor_color(x: float, y: float) -> Color:
    t = 0.5 + 0.5 * math.sin(x * 0.70 + y * 0.22)
    return _lerp_color(FOREST_BASE_COLOR, (0.030, 0.080, 0.055, 1.0), t)


def _city_street_color(x: float, y: float) -> Color:
    t = 0.5 + 0.5 * math.sin(x * 1.1) * math.sin(y * 1.1)
    return _lerp_color(CITY_BASE_COLOR, (0.022, 0.038, 0.048, 1.0), t)


def _surface_batch(
    name: str,
    patches: list[tuple[float, float, float, float]],
    n: int,
    z_of,
    color_of,
) -> GeomNode | None:
    """Subdivided heightfield. ``patches`` are ``(x0, y0, x1, y1)``."""
    if not patches or n < 1:
        return None
    fmt = GeomVertexFormat.get_v3c4()
    vdata = GeomVertexData(name, fmt, Geom.UH_static)
    vertex = GeomVertexWriter(vdata, "vertex")
    col = GeomVertexWriter(vdata, "color")
    prim = GeomTriangles(Geom.UH_static)
    for x0, y0, x1, y1 in patches:
        dx = (x1 - x0) / n
        dy = (y1 - y0) / n
        grid: list[list[int]] = []
        for j in range(n + 1):
            row_ids: list[int] = []
            y = y0 + j * dy
            for i in range(n + 1):
                x = x0 + i * dx
                row_ids.append(vertex.get_write_row())
                vertex.add_data3(x, y, z_of(x, y))
                col.add_data4(*color_of(x, y))
            grid.append(row_ids)
        for j in range(n):
            for i in range(n):
                a = grid[j][i]
                b = grid[j][i + 1]
                c = grid[j + 1][i + 1]
                d = grid[j + 1][i]
                prim.add_vertices(a, b, c)
                prim.add_vertices(a, c, d)
    prim.close_primitive()
    geom = Geom(vdata)
    geom.add_primitive(prim)
    node = GeomNode(name)
    node.add_geom(geom)
    return node


def _colored_box_batch(
    name: str,
    boxes: list[tuple[float, float, float, float, float, float, Color]],
) -> GeomNode | None:
    """Each box is ``(x0, y0, x1, y1, z0, z1, rgba)`` — top + four sides."""
    if not boxes:
        return None
    fmt = GeomVertexFormat.get_v3c4()
    vdata = GeomVertexData(name, fmt, Geom.UH_static)
    vertex = GeomVertexWriter(vdata, "vertex")
    col = GeomVertexWriter(vdata, "color")
    prim = GeomTriangles(Geom.UH_static)

    def _face(pts: list[Vec3], color: Color) -> None:
        base = vertex.get_write_row()
        for p in pts:
            vertex.add_data3(*p)
            col.add_data4(*color)
        prim.add_vertices(base, base + 1, base + 2)
        prim.add_vertices(base, base + 2, base + 3)

    for x0, y0, x1, y1, z0, z1, color in boxes:
        _face([(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)], color)
        _face([(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)], color)
        _face([(x1, y1, z0), (x0, y1, z0), (x0, y1, z1), (x1, y1, z1)], color)
        _face([(x0, y0, z0), (x0, y1, z0), (x0, y1, z1), (x0, y0, z1)], color)
        _face([(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)], color)
    prim.close_primitive()
    geom = Geom(vdata)
    geom.add_primitive(prim)
    node = GeomNode(name)
    node.add_geom(geom)
    return node


def _face_batch(
    name: str,
    faces: list[tuple[list[Vec3], Color]],
) -> GeomNode | None:
    if not faces:
        return None
    fmt = GeomVertexFormat.get_v3c4()
    vdata = GeomVertexData(name, fmt, Geom.UH_static)
    vertex = GeomVertexWriter(vdata, "vertex")
    col = GeomVertexWriter(vdata, "color")
    prim = GeomTriangles(Geom.UH_static)
    for pts, color in faces:
        base = vertex.get_write_row()
        for p in pts:
            vertex.add_data3(*p)
            col.add_data4(*color)
        prim.add_vertices(base, base + 1, base + 2)
        prim.add_vertices(base, base + 2, base + 3)
    prim.close_primitive()
    geom = Geom(vdata)
    geom.add_primitive(prim)
    node = GeomNode(name)
    node.add_geom(geom)
    return node


def _box_edges(
    name: str,
    boxes: list[tuple[float, float, float, float, float, float]],
    color: Color,
) -> GeomNode | None:
    if not boxes:
        return None
    segs = LineSegs(name)
    segs.set_thickness(1.3)
    segs.set_color(*color)
    for x0, y0, x1, y1, z0, z1 in boxes:
        segs.move_to(x0, y0, z1)
        for px, py in ((x1, y0), (x1, y1), (x0, y1), (x0, y0)):
            segs.draw_to(px, py, z1)
        for px, py in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
            segs.move_to(px, py, z0)
            segs.draw_to(px, py, z1)
    return segs.create()


def _tree_layout(col: int, row: int, seed: int) -> list[tuple[float, float, float]]:
    """Deterministic pine positions + size jitter (fractional 0–1, scale)."""
    h = _hash_u32(col, row, seed, 11)
    count = 3 + (h % 4)
    pts: list[tuple[float, float, float]] = []
    for i in range(count):
        h = (h * 1664525 + 1013904223) & 0xFFFFFFFF
        u = ((h >> 8) & 0xFF) / 255.0
        h = (h * 1664525 + 1013904223) & 0xFFFFFFFF
        v = ((h >> 8) & 0xFF) / 255.0
        h = (h * 1664525 + 1013904223) & 0xFFFFFFFF
        s = 0.75 + 0.50 * (((h >> 8) & 0xFF) / 255.0)
        pts.append((0.18 + 0.64 * u, 0.18 + 0.64 * v, s))
    return pts


def _scrub_layout(col: int, row: int, seed: int) -> list[tuple[float, float]]:
    h = _hash_u32(col, row, seed, 29)
    count = 2 + (h % 3)
    pts: list[tuple[float, float]] = []
    for _ in range(count):
        h = (h * 1664525 + 1013904223) & 0xFFFFFFFF
        u = ((h >> 8) & 0xFF) / 255.0
        h = (h * 1664525 + 1013904223) & 0xFFFFFFFF
        v = ((h >> 8) & 0xFF) / 255.0
        pts.append((0.15 + 0.70 * u, 0.15 + 0.70 * v))
    return pts


def _add_pine(
    boxes: list[tuple[float, float, float, float, float, float, Color]],
    cx: float,
    cy: float,
    ground: float,
    half: float,
    scale: float,
) -> None:
    trunk_h = 0.90 * scale
    canopy_h = 1.35 * scale
    th = half * 0.28
    boxes.append(
        (cx - th, cy - th, cx + th, cy + th, ground, ground + trunk_h, TRUNK_COLOR)
    )
    z1 = ground + trunk_h * 0.55
    boxes.append(
        (
            cx - half,
            cy - half,
            cx + half,
            cy + half,
            z1,
            z1 + canopy_h,
            TREE_COLOR,
        )
    )
    top = half * 0.55
    z2 = z1 + canopy_h * 0.55
    boxes.append(
        (
            cx - top,
            cy - top,
            cx + top,
            cy + top,
            z2,
            z2 + canopy_h * 0.70,
            CANOPY_TOP_COLOR,
        )
    )


def _window_faces(
    x0: float,
    y0: float,
    x1: float,
    y1: float,
    z0: float,
    z1: float,
    col: int,
    row: int,
    seed: int,
) -> list[tuple[list[Vec3], Color]]:
    """Grid of lit/dark windows on all four facades."""
    faces: list[tuple[list[Vec3], Color]] = []
    span_x = x1 - x0
    span_y = y1 - y0
    height = z1 - z0
    if span_x < 0.8 or span_y < 0.8 or height < 2.0:
        return faces
    cols_x = max(2, int(span_x / 1.15))
    cols_y = max(2, int(span_y / 1.15))
    floors = max(2, int(height / 1.70))
    wx = span_x / cols_x * 0.42
    wy = span_y / cols_y * 0.42
    wh = (height - 0.70) / floors * 0.55
    pad_x = (span_x / cols_x - wx) * 0.5
    pad_y = (span_y / cols_y - wy) * 0.5
    z_pad = 0.35
    proud = 0.04
    h = _hash_u32(col, row, seed, 47)

    def _lit() -> Color:
        nonlocal h
        h = (h * 1664525 + 1013904223) & 0xFFFFFFFF
        return WINDOW_LIT if ((h >> 8) & 0xFF) > 145 else WINDOW_DARK

    for floor in range(floors):
        z = z0 + z_pad + floor * ((height - 0.70) / floors)
        zt = z + wh
        if zt > z1 - 0.20:
            continue
        for i in range(cols_x):
            xa = x0 + pad_x + i * (span_x / cols_x)
            xb = xa + wx
            color = _lit()
            faces.append(
                ([(xa, y0 - proud, z), (xb, y0 - proud, z), (xb, y0 - proud, zt), (xa, y0 - proud, zt)], color)
            )
            color = _lit()
            faces.append(
                ([(xb, y1 + proud, z), (xa, y1 + proud, z), (xa, y1 + proud, zt), (xb, y1 + proud, zt)], color)
            )
        for j in range(cols_y):
            ya = y0 + pad_y + j * (span_y / cols_y)
            yb = ya + wy
            color = _lit()
            faces.append(
                ([(x0 - proud, yb, z), (x0 - proud, ya, z), (x0 - proud, ya, zt), (x0 - proud, yb, zt)], color)
            )
            color = _lit()
            faces.append(
                ([(x1 + proud, ya, z), (x1 + proud, yb, z), (x1 + proud, yb, zt), (x1 + proud, ya, zt)], color)
            )
    return faces


def _chunk_edge_cells(terrain: TerrainGrid) -> int:
    """Cells per chunk edge; one chunk for PoC-sized grids."""
    if terrain.columns * terrain.rows <= TERRAIN_CHUNK_MIN_CELLS:
        return max(terrain.columns, terrain.rows, 1)
    return TERRAIN_CHUNK_CELLS


def _prop_detail(terrain: TerrainGrid) -> bool:
    """Whether to emit per-cell props (trees, scrub, windows, edges)."""
    return float(terrain.cell_size) <= PROP_DETAIL_MAX_CELL_SIZE


def _surface_subdivision(terrain: TerrainGrid, n: int) -> int:
    """Heightfield subdivision for one cell, capped on coarse grids.

    The ``_*_z`` functions wobble on a ~16-unit wavelength. Subdividing a
    50-unit cell into 2x2 samples that wavelength far too coarsely to read as
    rolling ground, so it buys vertices and no visual -- keep such cells flat.
    """
    return n if _prop_detail(terrain) else 1


def _city_buildings(
    terrain: TerrainGrid,
    cell,
    col: int,
    row: int,
    bounds: tuple[float, float, float, float],
    seed: int,
) -> list[tuple[float, float, float, float, float]]:
    """``(x0, y0, x1, y1, height)`` footprints for one CITY cell.

    At PoC cell sizes this is the single inset block the viewer has always
    drawn. On a large map the cell subdivides into a grid of ~CITY_BLOCK_WORLD
    blocks with jittered heights, so a city reads as a skyline rather than one
    flat slab the width of a cell.
    """
    x0, y0, x1, y1 = bounds
    span = min(x1 - x0, y1 - y0)
    n = max(1, min(CITY_BLOCKS_MAX, int(span / CITY_BLOCK_WORLD)))
    base_height = float(cell.building_height or 8.0)
    out: list[tuple[float, float, float, float, float]] = []
    for j in range(n):
        for i in range(n):
            bx0 = x0 + (x1 - x0) * (i / n)
            by0 = y0 + (y1 - y0) * (j / n)
            bx1 = x0 + (x1 - x0) * ((i + 1) / n)
            by1 = y0 + (y1 - y0) * ((j + 1) / n)
            inset = min(bx1 - bx0, by1 - by0) * 0.18
            if n == 1:
                height = base_height
            else:
                jitter = _hash01(col * CITY_BLOCKS_MAX + i, row * CITY_BLOCKS_MAX + j, seed, 13)
                height = base_height * (0.55 + 0.90 * jitter)
            out.append((bx0 + inset, by0 + inset, bx1 - inset, by1 - inset, height))
    return out


def _attach(root: NodePath, node: GeomNode | None, *, alpha: bool = False, two_sided: bool = False) -> None:
    if node is None:
        return
    np = root.attach_new_node(node)
    np.set_light_off()
    if two_sided:
        np.set_two_sided(True)
    if alpha:
        np.set_transparency(TransparencyAttrib.MAlpha)


class _ChunkGeometry:
    """Accumulated geometry for one spatial chunk of the terrain grid."""

    __slots__ = (
        "water_patches",
        "plains_patches",
        "forest_patches",
        "city_patches",
        "prop_boxes",
        "building_boxes",
        "building_edges",
        "window_faces",
    )

    def __init__(self) -> None:
        self.water_patches: list[tuple[float, float, float, float]] = []
        self.plains_patches: list[tuple[float, float, float, float]] = []
        self.forest_patches: list[tuple[float, float, float, float]] = []
        self.city_patches: list[tuple[float, float, float, float]] = []
        self.prop_boxes: list[tuple[float, float, float, float, float, float, Color]] = []
        self.building_boxes: list[tuple[float, float, float, float, float, float, Color]] = []
        self.building_edges: list[tuple[float, float, float, float, float, float]] = []
        self.window_faces: list[tuple[list[Vec3], Color]] = []


def _collect_cell(
    chunk: _ChunkGeometry,
    terrain: TerrainGrid,
    col: int,
    row: int,
    seed: int,
    detail: bool,
) -> None:
    """Append one cell's geometry to its chunk."""
    cell = terrain.cells[row][col]
    x0, y0, x1, y1 = _cell_bounds(terrain, col, row)
    t = cell.terrain_type
    if t == "WATER":
        chunk.water_patches.append((x0, y0, x1, y1))
    elif t == "FOREST":
        chunk.forest_patches.append((x0, y0, x1, y1))
        if detail:
            cell_w = min(x1 - x0, y1 - y0)
            for u, v, scale in _tree_layout(col, row, seed):
                cx = x0 + (x1 - x0) * u
                cy = y0 + (y1 - y0) * v
                half = cell_w * 0.10 * scale
                _add_pine(chunk.prop_boxes, cx, cy, _forest_z(cx, cy), half, scale)
    elif t == "CITY":
        chunk.city_patches.append((x0, y0, x1, y1))
        for bx0, by0, bx1, by1, height in _city_buildings(
            terrain, cell, col, row, (x0, y0, x1, y1), seed
        ):
            chunk.building_boxes.append(
                (bx0, by0, bx1, by1, 0.0, height, BUILDING_COLOR)
            )
            if detail:
                chunk.building_edges.append((bx0, by0, bx1, by1, 0.0, height))
                chunk.window_faces.extend(
                    _window_faces(bx0, by0, bx1, by1, 0.0, height, col, row, seed)
                )
            hx = bx0 + (bx1 - bx0) * (0.25 + 0.35 * _hash01(col, row, seed, 3))
            hy = by0 + (by1 - by0) * (0.25 + 0.35 * _hash01(col, row, seed, 5))
            hw = min(bx1 - bx0, by1 - by0) * 0.16
            chunk.building_boxes.append(
                (hx - hw, hy - hw * 0.7, hx + hw, hy + hw * 0.7, height, height + 0.55, ROOF_COLOR)
            )
    else:
        chunk.plains_patches.append((x0, y0, x1, y1))
        if detail:
            cell_w = min(x1 - x0, y1 - y0)
            for u, v in _scrub_layout(col, row, seed):
                cx = x0 + (x1 - x0) * u
                cy = y0 + (y1 - y0) * v
                half = cell_w * 0.06
                gz = _plains_z(cx, cy)
                chunk.prop_boxes.append(
                    (cx - half, cy - half, cx + half, cy + half, gz, gz + 0.22, SCRUB_COLOR)
                )


def _attach_chunk(
    parent: NodePath, chunk: _ChunkGeometry, terrain: TerrainGrid
) -> None:
    """Emit one chunk's batches under ``parent``."""
    sub = _surface_subdivision
    _attach(
        parent,
        _surface_batch("terrain-water", chunk.water_patches, sub(terrain, 3), _water_z, _water_color),
        alpha=True,
    )
    _attach(
        parent,
        _surface_batch("terrain-plains", chunk.plains_patches, sub(terrain, 2), _plains_z, _plains_color),
    )
    _attach(
        parent,
        _surface_batch("terrain-forest-base", chunk.forest_patches, sub(terrain, 2), _forest_z, _forest_floor_color),
    )
    _attach(
        parent,
        _surface_batch("terrain-city-base", chunk.city_patches, sub(terrain, 2), lambda *_: 0.02, _city_street_color),
    )
    _attach(parent, _colored_box_batch("terrain-trees", chunk.prop_boxes), two_sided=True)
    _attach(parent, _colored_box_batch("terrain-buildings", chunk.building_boxes), two_sided=True)
    _attach(parent, _face_batch("terrain-windows", chunk.window_faces), alpha=True, two_sided=True)
    edge_node = _box_edges("terrain-building-edges", chunk.building_edges, BUILDING_EDGE_COLOR)
    if edge_node is not None:
        np = parent.attach_new_node(edge_node)
        np.set_light_off()
        np.set_transparency(TransparencyAttrib.MAlpha)
        np.set_depth_offset(1)


def build_terrain_root(parent: NodePath, terrain: TerrainGrid) -> NodePath:
    """Attach batched terrain meshes under ``parent``; return terrain root.

    Geometry is split into spatial chunks so each GeomNode has a bounding
    volume smaller than the map and Panda3D can frustum-cull it. A single
    map-wide batch is always "in frustum", so a camera looking at one corner
    of a large battlespace still pays for every triangle on it. PoC-sized
    grids stay a single chunk (see ``TERRAIN_CHUNK_MIN_CELLS``).
    """
    root = parent.attach_new_node("terrain-root")
    seed = terrain.terrain_edit_seed
    edge = _chunk_edge_cells(terrain)
    detail = _prop_detail(terrain)

    chunks: dict[tuple[int, int], _ChunkGeometry] = {}
    for row in range(terrain.rows):
        for col in range(terrain.columns):
            key = (col // edge, row // edge)
            chunk = chunks.get(key)
            if chunk is None:
                chunk = chunks[key] = _ChunkGeometry()
            _collect_cell(chunk, terrain, col, row, seed, detail)

    single = len(chunks) <= 1
    for (cx, cy), chunk in sorted(chunks.items()):
        holder = root if single else root.attach_new_node(f"terrain-chunk-{cx}-{cy}")
        _attach_chunk(holder, chunk, terrain)

    for water_np in root.find_all_matches("**/terrain-water"):
        water_np.set_shader_auto()
        water_np.set_tag("water-anim", "1")

    return root


def terrain_batch_node_count(terrain_root: NodePath) -> int:
    """Number of GeomNode children under the terrain root (bounded batches)."""
    return sum(1 for child in terrain_root.find_all_matches("**/+GeomNode"))
