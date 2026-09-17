"""Shared vertex-colored mesh helpers for procedural airframes.

Nose is local +Y, right wing +X, up +Z — the same frame the dart glyph and
``glyph.set_hpr()`` already use. Vertex colors are shading multipliers on
the jet palette (applied later via ``NodePath.set_color``).
"""

from __future__ import annotations

from collections.abc import Callable, Sequence

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

# Matches viewer3d.scene.GLYPH_SCALE so airframes sit next to the old dart.
MESH_SCALE = 1.85
KEYLINE_COLOR = (0.88, 0.97, 1.0, 0.92)
RIM_COLOR = (0.45, 0.82, 1.0)


Color = tuple[float, float, float, float]
Vec3 = tuple[float, float, float]
ColorFn = Callable[[float, float, float, bool, bool], Color]


class MeshBuilder:
    """Accumulates (vertex, color) + triangles, then emits a GeomNode."""

    def __init__(self) -> None:
        self.verts: list[tuple[float, float, float, Color]] = []
        self.tris: list[tuple[int, int, int]] = []

    def add(self, x: float, y: float, z: float, color: Color) -> int:
        self.verts.append((x, y, z, color))
        return len(self.verts) - 1

    def tri(self, a: int, b: int, c: int) -> None:
        self.tris.append((a, b, c))

    def quad(self, a: int, b: int, c: int, d: int) -> None:
        self.tri(a, b, c)
        self.tri(a, c, d)

    def fan(self, hub: int, ring: Sequence[int], *, reverse: bool = False) -> None:
        n = len(ring)
        for i in range(n):
            a, b = ring[i], ring[(i + 1) % n]
            if reverse:
                self.tri(hub, b, a)
            else:
                self.tri(hub, a, b)

    def to_geom(self, name: str) -> GeomNode:
        fmt = GeomVertexFormat.get_v3c4()
        vdata = GeomVertexData(name, fmt, Geom.UH_static)
        vertex = GeomVertexWriter(vdata, "vertex")
        col = GeomVertexWriter(vdata, "color")
        for x, y, z, rgba in self.verts:
            vertex.add_data3(x, y, z)
            col.add_data4(*rgba)
        prim = GeomTriangles(Geom.UH_static)
        for i, j, k in self.tris:
            prim.add_vertices(i, j, k)
        prim.close_primitive()
        geom = Geom(vdata)
        geom.add_primitive(prim)
        node = GeomNode(name)
        node.add_geom(geom)
        return node


def shade(
    base: Color,
    *,
    upper: bool,
    rim: bool = False,
    x: float = 0.0,
    y: float = 0.0,
    dark: float = 0.0,
) -> Color:
    """Cheap directional shade: cooler below, brighter on the left-front."""
    light = 0.92 if upper else 0.38
    light *= 1.0 + 0.10 * max(-x, 0.0) + 0.05 * y
    light = max(0.18, min(1.15, light - dark))
    r, g, b, a = base
    rgb = (min(1.0, r * light), min(1.0, g * light), min(1.0, b * light))
    if rim:
        rgb = (
            rgb[0] * 0.62 + RIM_COLOR[0] * 0.38,
            rgb[1] * 0.62 + RIM_COLOR[1] * 0.38,
            rgb[2] * 0.62 + RIM_COLOR[2] * 0.38,
        )
    return (rgb[0], rgb[1], rgb[2], a)


def palette_color_fn(base: Color, *, extra_dark: float = 0.0) -> ColorFn:
    def _fn(x: float, y: float, z: float, upper: bool, rim: bool) -> Color:
        del z
        return shade(base, upper=upper, rim=rim, x=x, y=y, dark=extra_dark)

    return _fn


def add_solid_planform(
    mesh: MeshBuilder,
    points_xy: Sequence[tuple[float, float]],
    upper_z: Callable[[float, float], float],
    lower_z: Callable[[float, float], float],
    color_fn: ColorFn,
) -> tuple[list[int], list[int]]:
    """Closed 3D plate: upper fan, lower fan, rim quads. ``points_xy`` is CCW."""
    n = len(points_xy)
    up: list[int] = []
    lo: list[int] = []
    for x, y in points_xy:
        uz, lz = upper_z(x, y), lower_z(x, y)
        up.append(mesh.add(x, y, uz, color_fn(x, y, uz, True, True)))
        lo.append(mesh.add(x, y, lz, color_fn(x, y, lz, False, True)))
    cx = sum(p[0] for p in points_xy) / n
    cy = sum(p[1] for p in points_xy) / n
    czu, czl = upper_z(cx, cy), lower_z(cx, cy)
    hub_u = mesh.add(cx, cy, czu, color_fn(cx, cy, czu, True, False))
    hub_l = mesh.add(cx, cy, czl, color_fn(cx, cy, czl, False, False))
    mesh.fan(hub_u, up)
    mesh.fan(hub_l, lo, reverse=True)
    for i in range(n):
        j = (i + 1) % n
        mesh.quad(up[i], up[j], lo[j], lo[i])
    return up, lo


def add_loft(
    mesh: MeshBuilder,
    rings: Sequence[Sequence[Vec3]],
    color_fn: ColorFn,
    *,
    cap: bool = True,
    upper_hint: Callable[[Vec3], bool] | None = None,
) -> None:
    """Connect consecutive rings of equal length. Caps the first/last ring."""
    if len(rings) < 2:
        return
    count = len(rings[0])
    if any(len(r) != count for r in rings):
        raise ValueError("loft rings must be the same length")

    def _upper(p: Vec3) -> bool:
        if upper_hint is not None:
            return upper_hint(p)
        return p[2] >= 0.0

    ids: list[list[int]] = []
    for ring in rings:
        row = []
        for x, y, z in ring:
            rim = True
            row.append(mesh.add(x, y, z, color_fn(x, y, z, _upper((x, y, z)), rim)))
        ids.append(row)
    for i in range(len(ids) - 1):
        a, b = ids[i], ids[i + 1]
        for k in range(count):
            k2 = (k + 1) % count
            mesh.quad(a[k], a[k2], b[k2], b[k])
    if cap:
        def _cap(row: list[int], pts: Sequence[Vec3], *, reverse: bool) -> None:
            cx = sum(p[0] for p in pts) / count
            cy = sum(p[1] for p in pts) / count
            cz = sum(p[2] for p in pts) / count
            hub = mesh.add(cx, cy, cz, color_fn(cx, cy, cz, _upper((cx, cy, cz)), False))
            mesh.fan(hub, row, reverse=reverse)

        _cap(ids[0], rings[0], reverse=True)
        _cap(ids[-1], rings[-1], reverse=False)


def add_prism(
    mesh: MeshBuilder,
    top: Sequence[Vec3],
    bottom: Sequence[Vec3],
    color_fn: ColorFn,
) -> None:
    """Closed n-gon prism. ``top`` and ``bottom`` are the same length, CCW on top."""
    if len(top) != len(bottom) or len(top) < 3:
        raise ValueError("prism needs matching rings of 3+ vertices")
    t_ids = [mesh.add(x, y, z, color_fn(x, y, z, True, True)) for x, y, z in top]
    b_ids = [mesh.add(x, y, z, color_fn(x, y, z, False, True)) for x, y, z in bottom]
    for i in range(1, len(t_ids) - 1):
        mesh.tri(t_ids[0], t_ids[i], t_ids[i + 1])
        mesh.tri(b_ids[0], b_ids[i + 1], b_ids[i])
    for i in range(len(t_ids)):
        j = (i + 1) % len(t_ids)
        mesh.quad(t_ids[i], t_ids[j], b_ids[j], b_ids[i])


def add_slab(
    mesh: MeshBuilder,
    quad: Sequence[Vec3],
    thickness: Vec3,
    color_fn: ColorFn,
) -> None:
    """Quad extruded by ``thickness`` (typically (0,0,height) for a raised panel)."""
    if len(quad) != 4:
        raise ValueError("slab needs 4 corners")
    bottom = [
        (x - thickness[0], y - thickness[1], z - thickness[2]) for x, y, z in quad
    ]
    add_prism(mesh, quad, bottom, color_fn)


def add_box(
    mesh: MeshBuilder,
    xmin: float,
    xmax: float,
    ymin: float,
    ymax: float,
    zmin: float,
    zmax: float,
    color_fn: ColorFn,
) -> None:
    """Axis-aligned box. Top face at ``zmax``, extruded down to ``zmin``."""
    add_slab(
        mesh,
        [
            (xmin, ymin, zmax),
            (xmax, ymin, zmax),
            (xmax, ymax, zmax),
            (xmin, ymax, zmax),
        ],
        (0.0, 0.0, zmax - zmin),
        color_fn,
    )


def hull_ring(y: float, beam: float, deck_z: float, keel_z: float) -> list[Vec3]:
    """Ship cross-section: deck corners, chines, keel (CCW looking +Y)."""
    hw = beam * 0.5
    return [
        (hw, y, deck_z),
        (hw * 0.90, y, deck_z * 0.15),
        (hw * 0.18, y, keel_z),
        (-hw * 0.18, y, keel_z),
        (-hw * 0.90, y, deck_z * 0.15),
        (-hw, y, deck_z),
    ]


def box_ring(y: float, hw: float, hh: float, z: float = 0.0) -> list[Vec3]:
    """Axis-aligned cross-section: right, up, left, down (CCW looking +Y)."""
    return [
        (hw, y, z - hh * 0.15),
        (hw * 0.55, y, z + hh),
        (-hw * 0.55, y, z + hh),
        (-hw, y, z - hh * 0.15),
        (-hw * 0.70, y, z - hh),
        (hw * 0.70, y, z - hh),
    ]


def planform_outline(name: str, points_xy: Sequence[tuple[float, float]], z_of) -> GeomNode:
    segs = LineSegs(name)
    segs.set_thickness(1.6)
    segs.set_color(*KEYLINE_COLOR)
    x0, y0 = points_xy[0]
    segs.move_to(x0, y0, z_of(x0, y0) + 0.01)
    for x, y in points_xy[1:]:
        segs.draw_to(x, y, z_of(x, y) + 0.01)
    segs.draw_to(x0, y0, z_of(x0, y0) + 0.01)
    return segs.create()


def attach_mesh_group(
    parent: NodePath,
    name: str,
    geom: GeomNode,
    color: Color,
    *,
    scale: float = MESH_SCALE,
    outline: GeomNode | None = None,
) -> NodePath:
    group = parent.attach_new_node(name)
    group.set_light_off()
    group.set_two_sided(True)
    group.set_transparency(TransparencyAttrib.MAlpha)
    group.set_scale(scale)
    group.attach_new_node(geom)
    if outline is not None:
        edge = group.attach_new_node(outline)
        edge.set_color(*KEYLINE_COLOR)
        edge.set_depth_offset(2)
    group.set_color(*color)
    return group
