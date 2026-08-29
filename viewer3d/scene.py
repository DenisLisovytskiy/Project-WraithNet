"""Builds and updates the 3D scene graph from TraceHeader/TraceTick data.
Coordinate mapping: sim (x, y) -> Panda3D (x, y), with z reserved for a
synthetic, presentation-only flight altitude -- see design doc §6.1-6.2.
No gameplay logic reads z; it is purely cosmetic.

Glyphs are vertex-colored Geom polygons parented under a billboard node
(set_billboard_point_eye) rather than textured CardMaker quads -- simpler
silhouettes, no asset files, and the same contract Phase 17/18 will extend.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

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

from wraithnet.trace_schemas import TraceHeader, TraceTick

JET_ALTITUDE = 8.0  # world-units above the ground plane; presentation only

# Same palette as viewer/render.py::JET_PALETTE (copied, not imported, so
# viewer3d does not depend on the 2D Pygame package).
JET_PALETTE = [(80, 200, 255), (255, 160, 60), (170, 255, 120)]
TARGET_COLORS = {
    "HARDENED_BUNKER": (150 / 255, 100 / 255, 60 / 255, 1.0),
    "MOBILE_RADAR": (70 / 255, 120 / 255, 220 / 255, 1.0),
    "SAM_SITE": (210 / 255, 60 / 255, 60 / 255, 1.0),
}
DESTROYED_COLOR = (70 / 255, 70 / 255, 70 / 255, 1.0)
JAMMER_COLOR = (170 / 255, 90 / 255, 220 / 255, 1.0)
GRID_COLOR = (0.15, 0.55, 0.72, 0.9)
GROUND_COLOR = (0.02, 0.03, 0.06, 1.0)
SAM_DISC_COLOR = (0.82, 0.22, 0.22, 0.18)
JAM_DISC_COLOR = (0.55, 0.25, 0.75, 0.16)
GLYPH_SCALE = 1.6


@dataclass
class SceneHandles:
    root: NodePath
    jets: dict[str, NodePath] = field(default_factory=dict)
    jet_glyphs: dict[str, NodePath] = field(default_factory=dict)
    targets: dict[str, NodePath] = field(default_factory=dict)
    jammers: dict[str, NodePath] = field(default_factory=dict)
    last_jet_xy: dict[str, tuple[float, float]] = field(default_factory=dict)


def _rgb(rgb: tuple[int, int, int], a: float = 1.0) -> tuple[float, float, float, float]:
    return (rgb[0] / 255.0, rgb[1] / 255.0, rgb[2] / 255.0, a)


def _polygon_geom(name: str, points_xz: list[tuple[float, float]], color) -> GeomNode:
    fmt = GeomVertexFormat.get_v3c4()
    vdata = GeomVertexData(name, fmt, Geom.UH_static)
    vertex = GeomVertexWriter(vdata, "vertex")
    col = GeomVertexWriter(vdata, "color")
    r, g, b, a = color
    for x, z in points_xz:
        vertex.add_data3(x, 0.0, z)
        col.add_data4(r, g, b, a)
    prim = GeomTriangles(Geom.UH_static)
    for i in range(1, len(points_xz) - 1):
        prim.add_vertices(0, i, i + 1)
    prim.close_primitive()
    geom = Geom(vdata)
    geom.add_primitive(prim)
    node = GeomNode(name)
    node.add_geom(geom)
    return node


def _attach_glyph(parent: NodePath, name: str, points_xz, color) -> NodePath:
    np = parent.attach_new_node(_polygon_geom(name, points_xz, color))
    np.set_billboard_point_eye()
    np.set_light_off()
    np.set_two_sided(True)
    np.set_scale(GLYPH_SCALE)
    return np


def _triangle_pts():
    return [(-0.5, -0.45), (0.5, -0.45), (0.0, 0.65)]


def _square_pts():
    return [(-0.5, -0.5), (0.5, -0.5), (0.5, 0.5), (-0.5, 0.5)]


def _diamond_pts():
    return [(0.0, 0.65), (0.5, 0.0), (0.0, -0.65), (-0.5, 0.0)]


def _ngon_pts(n: int, radius: float = 0.55, rotation: float = 0.0):
    return [
        (
            radius * math.cos(rotation + 2 * math.pi * i / n),
            radius * math.sin(rotation + 2 * math.pi * i / n),
        )
        for i in range(n)
    ]


def _ground_quad(parent: NodePath, width: float, height: float) -> NodePath:
    pts = [(0.0, 0.0), (width, 0.0), (width, height), (0.0, height)]
    fmt = GeomVertexFormat.get_v3c4()
    vdata = GeomVertexData("ground", fmt, Geom.UH_static)
    vertex = GeomVertexWriter(vdata, "vertex")
    col = GeomVertexWriter(vdata, "color")
    r, g, b, a = GROUND_COLOR
    for x, y in pts:
        vertex.add_data3(x, y, 0.0)
        col.add_data4(r, g, b, a)
    prim = GeomTriangles(Geom.UH_static)
    prim.add_vertices(0, 1, 2)
    prim.add_vertices(0, 2, 3)
    prim.close_primitive()
    geom = Geom(vdata)
    geom.add_primitive(prim)
    node = GeomNode("ground")
    node.add_geom(geom)
    np = parent.attach_new_node(node)
    np.set_light_off()
    return np


def _grid_lines(parent: NodePath, width: float, height: float, step: float = 5.0) -> NodePath:
    segs = LineSegs("grid")
    segs.set_thickness(1.2)
    segs.set_color(*GRID_COLOR)
    x = 0.0
    while x <= width + 1e-9:
        segs.move_to(x, 0.0, 0.02)
        segs.draw_to(x, height, 0.02)
        x += step
    y = 0.0
    while y <= height + 1e-9:
        segs.move_to(0.0, y, 0.02)
        segs.draw_to(width, y, 0.02)
        y += step
    np = parent.attach_new_node(segs.create())
    np.set_light_off()
    np.set_transparency(TransparencyAttrib.MAlpha)
    return np


def _ground_disc(
    parent: NodePath,
    name: str,
    cx: float,
    cy: float,
    radius: float,
    color,
    z: float = 0.05,
    segments: int = 48,
) -> NodePath:
    fmt = GeomVertexFormat.get_v3c4()
    vdata = GeomVertexData(name, fmt, Geom.UH_static)
    vertex = GeomVertexWriter(vdata, "vertex")
    col = GeomVertexWriter(vdata, "color")
    r, g, b, a = color
    vertex.add_data3(cx, cy, z)
    col.add_data4(r, g, b, a)
    for i in range(segments + 1):
        ang = 2 * math.pi * i / segments
        vertex.add_data3(cx + radius * math.cos(ang), cy + radius * math.sin(ang), z)
        col.add_data4(r, g, b, a)
    prim = GeomTriangles(Geom.UH_static)
    for i in range(1, segments + 1):
        prim.add_vertices(0, i, i + 1)
    prim.close_primitive()
    geom = Geom(vdata)
    geom.add_primitive(prim)
    node = GeomNode(name)
    node.add_geom(geom)
    np = parent.attach_new_node(node)
    np.set_light_off()
    np.set_transparency(TransparencyAttrib.MAlpha)
    np.set_two_sided(True)
    return np


def _jet_color(agent_id: str, header: TraceHeader):
    try:
        idx = header.jet_ids.index(agent_id)
    except ValueError:
        idx = 0
    return _rgb(JET_PALETTE[idx % len(JET_PALETTE)])


def _jet_altitude(agent_id: str, header: TraceHeader) -> float:
    try:
        idx = header.jet_ids.index(agent_id)
    except ValueError:
        idx = 0
    return JET_ALTITUDE + 0.35 * idx


def _target_points(target_type: str):
    if target_type == "MOBILE_RADAR":
        return _diamond_pts()
    if target_type == "SAM_SITE":
        return _ngon_pts(16, 0.55)
    return _square_pts()


def build_static_scene(render_root, header: TraceHeader) -> SceneHandles:
    """Called once, when the header first arrives. Jets start hidden until tick 0."""
    root = render_root.attach_new_node("wraithnet-scene")
    _ground_quad(root, header.grid_width, header.grid_height)
    _grid_lines(root, header.grid_width, header.grid_height)

    handles = SceneHandles(root=root)
    cx, cy = header.grid_width / 2.0, header.grid_height / 2.0

    for tid in header.target_ids:
        pos = header.target_positions[tid]
        ttype = header.target_types[tid]
        color = TARGET_COLORS.get(ttype, (0.8, 0.8, 0.8, 1.0))
        anchor = root.attach_new_node(f"target-{tid}")
        anchor.set_pos(pos[0], pos[1], 0.15)
        _attach_glyph(anchor, f"glyph-{tid}", _target_points(ttype), color)
        if ttype == "SAM_SITE" or tid in header.sam_site_ids:
            _ground_disc(
                anchor,
                f"sam-ring-{tid}",
                0.0,
                0.0,
                header.sam_engagement_range,
                SAM_DISC_COLOR,
            )
        handles.targets[tid] = anchor

    for jid, pos in header.jammer_positions.items():
        anchor = root.attach_new_node(f"jammer-{jid}")
        anchor.set_pos(pos[0], pos[1], 0.15)
        _attach_glyph(anchor, f"glyph-{jid}", _ngon_pts(6, 0.5, math.pi / 6), JAMMER_COLOR)
        _ground_disc(anchor, f"jam-ring-{jid}", 0.0, 0.0, header.jam_radius, JAM_DISC_COLOR)
        handles.jammers[jid] = anchor

    for aid in header.jet_ids:
        anchor = root.attach_new_node(f"jet-{aid}")
        anchor.set_pos(cx, cy, _jet_altitude(aid, header))
        glyph = _attach_glyph(anchor, f"glyph-{aid}", _triangle_pts(), _jet_color(aid, header))
        anchor.hide()  # no jet position in the header; first tick reveals them
        handles.jets[aid] = anchor
        handles.jet_glyphs[aid] = glyph

    return handles


def update_tick(handles: SceneHandles, header: TraceHeader, tick: TraceTick) -> None:
    alive_ids = {frame.agent_id for frame in tick.jets}
    for aid in list(handles.jets):
        if aid not in alive_ids:
            handles.jets[aid].remove_node()
            handles.jets.pop(aid)
            handles.jet_glyphs.pop(aid, None)
            handles.last_jet_xy.pop(aid, None)

    for frame in tick.jets:
        anchor = handles.jets.get(frame.agent_id)
        if anchor is None or anchor.is_empty():
            continue
        x, y = frame.position
        z = _jet_altitude(frame.agent_id, header)
        anchor.set_pos(x, y, z)
        anchor.show()
        prev = handles.last_jet_xy.get(frame.agent_id)
        glyph = handles.jet_glyphs.get(frame.agent_id)
        if glyph is not None and not glyph.is_empty():
            if prev is not None:
                dx, dy = x - prev[0], y - prev[1]
                if dx * dx + dy * dy > 1e-8:
                    # Billboard in-plane roll: 0 faces +y (sim "north") on first tick.
                    glyph.set_r(-math.degrees(math.atan2(dx, dy)))
        handles.last_jet_xy[frame.agent_id] = (x, y)

    for frame in tick.targets:
        anchor = handles.targets.get(frame.target_id)
        if anchor is None or anchor.is_empty():
            continue
        if frame.hp_remaining <= 0:
            anchor.set_color(*DESTROYED_COLOR)
            anchor.set_render_mode_wireframe()
            ring = anchor.find("**/sam-ring-*")
            if not ring.is_empty():
                ring.hide()
        else:
            ttype = header.target_types[frame.target_id]
            r, g, b, a = TARGET_COLORS.get(ttype, (0.8, 0.8, 0.8, 1.0))
            anchor.clear_render_mode()
            glyph = anchor.find("**/glyph-*")
            if not glyph.is_empty():
                glyph.set_color(r, g, b, a)
