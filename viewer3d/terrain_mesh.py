"""Batched terrain geometry for the 3D viewer (Phase 36).

Uses only stored TerrainGrid cells/heights — no render-time randomness.

Palette: the cell fills all sit in the same dark cyan/teal family as
`viewer3d.ui_chrome`, so terrain reads as the substrate *under* the
holographic grid rather than as a second art style painted on top. Massing
(trees, buildings) is dark; what makes it legible is the glowing edge
wireframe drawn over the building blocks, not a bright fill.
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

WATER_COLOR = (0.020, 0.098, 0.170, 0.78)
PLAINS_COLOR = (0.014, 0.030, 0.042, 1.0)
FOREST_BASE_COLOR = (0.018, 0.052, 0.048, 1.0)
CITY_BASE_COLOR = (0.030, 0.056, 0.070, 1.0)
TREE_COLOR = (0.072, 0.215, 0.165, 1.0)
BUILDING_COLOR = (0.050, 0.082, 0.108, 1.0)
#: Glowing keyline over the building blocks — the holographic "massing" read.
BUILDING_EDGE_COLOR = tint(BORDER_SOFT, 1.0, alpha=0.62)


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


def _quad_batch(
    name: str,
    quads: list[tuple[float, float, float, float, float]],
    color: tuple[float, float, float, float],
) -> GeomNode | None:
    """Each quad is ``(x0, y0, x1, y1, z)``."""
    if not quads:
        return None
    fmt = GeomVertexFormat.get_v3c4()
    vdata = GeomVertexData(name, fmt, Geom.UH_static)
    vertex = GeomVertexWriter(vdata, "vertex")
    col = GeomVertexWriter(vdata, "color")
    r, g, b, a = color
    prim = GeomTriangles(Geom.UH_static)
    for x0, y0, x1, y1, z in quads:
        base = vertex.get_write_row()
        for x, y in ((x0, y0), (x1, y0), (x1, y1), (x0, y1)):
            vertex.add_data3(x, y, z)
            col.add_data4(r, g, b, a)
        prim.add_vertices(base, base + 1, base + 2)
        prim.add_vertices(base, base + 2, base + 3)
    prim.close_primitive()
    geom = Geom(vdata)
    geom.add_primitive(prim)
    node = GeomNode(name)
    node.add_geom(geom)
    return node


def _box_batch(
    name: str,
    boxes: list[tuple[float, float, float, float, float, float]],
    color: tuple[float, float, float, float],
) -> GeomNode | None:
    """Each box is ``(x0, y0, x1, y1, z0, z1)`` — top + four sides."""
    if not boxes:
        return None
    fmt = GeomVertexFormat.get_v3c4()
    vdata = GeomVertexData(name, fmt, Geom.UH_static)
    vertex = GeomVertexWriter(vdata, "vertex")
    col = GeomVertexWriter(vdata, "color")
    r, g, b, a = color
    prim = GeomTriangles(Geom.UH_static)

    def _face(pts: list[tuple[float, float, float]]) -> None:
        base = vertex.get_write_row()
        for p in pts:
            vertex.add_data3(*p)
            col.add_data4(r, g, b, a)
        prim.add_vertices(base, base + 1, base + 2)
        prim.add_vertices(base, base + 2, base + 3)

    for x0, y0, x1, y1, z0, z1 in boxes:
        _face([(x0, y0, z1), (x1, y0, z1), (x1, y1, z1), (x0, y1, z1)])
        _face([(x0, y0, z0), (x1, y0, z0), (x1, y0, z1), (x0, y0, z1)])
        _face([(x0, y1, z0), (x1, y1, z0), (x1, y1, z1), (x0, y1, z1)])
        _face([(x0, y0, z0), (x0, y1, z0), (x0, y1, z1), (x0, y0, z1)])
        _face([(x1, y0, z0), (x1, y1, z0), (x1, y1, z1), (x1, y0, z1)])
    prim.close_primitive()
    geom = Geom(vdata)
    geom.add_primitive(prim)
    node = GeomNode(name)
    node.add_geom(geom)
    return node


def _box_edges(
    name: str,
    boxes: list[tuple[float, float, float, float, float, float]],
    color: tuple[float, float, float, float],
) -> GeomNode | None:
    """One batched wireframe over `boxes`: roof outline plus vertical corners.

    Kept to a single GeomNode so the terrain root stays inside its batch
    budget (see `terrain_batch_node_count`).
    """
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


def _tree_layout(col: int, row: int, seed: int) -> list[tuple[float, float]]:
    """Deterministic tree offsets inside a cell (fractional 0–1)."""
    h = (seed ^ (col * 73856093) ^ (row * 19349663)) & 0xFFFFFFFF
    count = 1 + (h % 3)
    pts: list[tuple[float, float]] = []
    for i in range(count):
        h = (h * 1664525 + 1013904223) & 0xFFFFFFFF
        u = ((h >> 8) & 0xFF) / 255.0
        h = (h * 1664525 + 1013904223) & 0xFFFFFFFF
        v = ((h >> 8) & 0xFF) / 255.0
        pts.append((0.2 + 0.6 * u, 0.2 + 0.6 * v))
    return pts


def build_terrain_root(parent: NodePath, terrain: TerrainGrid) -> NodePath:
    """Attach batched terrain meshes under ``parent``; return terrain root."""
    root = parent.attach_new_node("terrain-root")
    water_quads: list[tuple[float, float, float, float, float]] = []
    plains_quads: list[tuple[float, float, float, float, float]] = []
    forest_quads: list[tuple[float, float, float, float, float]] = []
    city_quads: list[tuple[float, float, float, float, float]] = []
    tree_boxes: list[tuple[float, float, float, float, float, float]] = []
    building_boxes: list[tuple[float, float, float, float, float, float]] = []

    for row in range(terrain.rows):
        for col in range(terrain.columns):
            cell = terrain.cells[row][col]
            x0, y0, x1, y1 = _cell_bounds(terrain, col, row)
            t = cell.terrain_type
            if t == "WATER":
                water_quads.append((x0, y0, x1, y1, 0.0))
            elif t == "FOREST":
                forest_quads.append((x0, y0, x1, y1, 0.0))
                for u, v in _tree_layout(col, row, terrain.terrain_edit_seed):
                    cx = x0 + (x1 - x0) * u
                    cy = y0 + (y1 - y0) * v
                    half = min(x1 - x0, y1 - y0) * 0.08
                    tree_boxes.append(
                        (cx - half, cy - half, cx + half, cy + half, 0.0, 1.6)
                    )
            elif t == "CITY":
                city_quads.append((x0, y0, x1, y1, 0.0))
                height = float(cell.building_height or 8.0)
                # Building footprint inset so city streets remain visible.
                inset = min(x1 - x0, y1 - y0) * 0.18
                building_boxes.append(
                    (x0 + inset, y0 + inset, x1 - inset, y1 - inset, 0.0, height)
                )
            else:
                plains_quads.append((x0, y0, x1, y1, 0.0))

    batches = (
        ("terrain-water", water_quads, WATER_COLOR, True),
        ("terrain-plains", plains_quads, PLAINS_COLOR, False),
        ("terrain-forest-base", forest_quads, FOREST_BASE_COLOR, False),
        ("terrain-city-base", city_quads, CITY_BASE_COLOR, False),
    )
    for name, quads, color, alpha in batches:
        node = _quad_batch(name, quads, color)
        if node is None:
            continue
        np = root.attach_new_node(node)
        np.set_light_off()
        if alpha:
            np.set_transparency(TransparencyAttrib.MAlpha)

    tree_node = _box_batch("terrain-trees", tree_boxes, TREE_COLOR)
    if tree_node is not None:
        np = root.attach_new_node(tree_node)
        np.set_light_off()
        np.set_two_sided(True)

    bld_node = _box_batch("terrain-buildings", building_boxes, BUILDING_COLOR)
    if bld_node is not None:
        np = root.attach_new_node(bld_node)
        np.set_light_off()
        np.set_two_sided(True)

    edge_node = _box_edges("terrain-building-edges", building_boxes, BUILDING_EDGE_COLOR)
    if edge_node is not None:
        np = root.attach_new_node(edge_node)
        np.set_light_off()
        np.set_transparency(TransparencyAttrib.MAlpha)
        np.set_depth_offset(1)

    # Cosmetic water shimmer: slight Z scale pulse driven by viewer task later.
    water_np = root.find("**/terrain-water")
    if not water_np.is_empty():
        water_np.set_shader_auto()
        water_np.set_tag("water-anim", "1")

    return root


def terrain_batch_node_count(terrain_root: NodePath) -> int:
    """Number of GeomNode children under the terrain root (bounded batches)."""
    return sum(1 for child in terrain_root.find_all_matches("**/+GeomNode"))
