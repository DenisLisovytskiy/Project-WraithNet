"""Builds and updates the 3D scene graph from TraceHeader/TraceTick data.
Coordinate mapping: sim (x, y) -> Panda3D (x, y), with z reserved for a
synthetic, presentation-only flight altitude -- see design doc §6.1-6.2.
No gameplay logic reads z; it is purely cosmetic.

Ground entities use camera-facing billboard glyphs. Jets use a small,
non-billboarded procedural dart mesh oriented in full 3D (Phase 25).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Callable

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

from viewer3d.flight_dynamics import (
    CRUISE_ALTITUDE,
    GROUND_ALTITUDE,
    MAX_BANK_RATE_PER_SECOND,
    MAX_CLIMB_RATE_PER_SECOND,
    desired_altitude,
    desired_bank_deg,
    ease_toward,
)
from viewer3d.highlights import TickHighlights
from wraithnet.trace_schemas import StrikeEvent, TraceHeader, TraceTick

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
HIGHLIGHT_DURATION = 0.45
STRIKE_COLOR = (1.0, 0.90, 0.32, 1.0)
DESTRUCTION_COLOR = (1.0, 1.0, 1.0, 1.0)
JAM_FLASH_COLOR = (1.0, 0.35, 1.0, 1.0)
UNJAM_FLASH_COLOR = (0.31, 0.86, 1.0, 1.0)
RTB_FLASH_COLOR = (0.31, 0.63, 1.0, 1.0)
HOME_BASE_COLOR = (0.35, 0.78, 0.55, 1.0)


@dataclass
class _FxItem:
    node: NodePath
    age: float = 0.0
    duration: float = HIGHLIGHT_DURATION
    restore: Callable[[], None] | None = None


@dataclass
class SceneHandles:
    root: NodePath
    jets: dict[str, NodePath] = field(default_factory=dict)
    jet_glyphs: dict[str, NodePath] = field(default_factory=dict)
    targets: dict[str, NodePath] = field(default_factory=dict)
    jammers: dict[str, NodePath] = field(default_factory=dict)
    last_jet_xy: dict[str, tuple[float, float]] = field(default_factory=dict)
    jet_heading_deg: dict[str, float] = field(default_factory=dict)
    rendered_altitude: dict[str, float] = field(default_factory=dict)
    rendered_bank_deg: dict[str, float] = field(default_factory=dict)
    desired_altitude: dict[str, float] = field(default_factory=dict)
    desired_bank_deg: dict[str, float] = field(default_factory=dict)
    previous_desired_altitude: dict[str, float] = field(default_factory=dict)
    first_seen_tick: dict[str, int] = field(default_factory=dict)
    last_struck_tick: dict[str, int] = field(default_factory=dict)
    home_base: NodePath | None = None
    fx_root: NodePath | None = None
    highlight_fx: list[_FxItem] = field(default_factory=list)


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


def _dart_geom(name: str, color) -> GeomNode:
    """Small double-sided dart: nose +Y, wings in X, shallow tail Z inset."""
    verts = [
        (0.0, 0.7, 0.0),
        (-0.5, -0.4, -0.08),
        (0.5, -0.4, -0.08),
        (0.0, -0.15, 0.08),
    ]
    tris = [(0, 1, 2), (1, 2, 3)]
    fmt = GeomVertexFormat.get_v3c4()
    vdata = GeomVertexData(name, fmt, Geom.UH_static)
    vertex = GeomVertexWriter(vdata, "vertex")
    col = GeomVertexWriter(vdata, "color")
    r, g, b, a = color
    for x, y, z in verts:
        vertex.add_data3(x, y, z)
        col.add_data4(r, g, b, a)
    prim = GeomTriangles(Geom.UH_static)
    for a, b, c in tris:
        prim.add_vertices(a, b, c)
    prim.close_primitive()
    geom = Geom(vdata)
    geom.add_primitive(prim)
    node = GeomNode(name)
    node.add_geom(geom)
    return node


def _attach_jet_dart(parent: NodePath, name: str, color) -> NodePath:
    """Jet glyph without billboarding -- true 3D heading/bank via set_hpr."""
    np = parent.attach_new_node(_dart_geom(name, color))
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


def jet_altitude(agent_id: str, header: TraceHeader) -> float:
    try:
        idx = header.jet_ids.index(agent_id)
    except ValueError:
        idx = 0
    return JET_ALTITUDE + 0.35 * idx


def _jet_altitude(agent_id: str, header: TraceHeader) -> float:
    return jet_altitude(agent_id, header)


def _pop_jet_state(handles: SceneHandles, aid: str) -> None:
    handles.jet_glyphs.pop(aid, None)
    handles.last_jet_xy.pop(aid, None)
    handles.jet_heading_deg.pop(aid, None)
    handles.rendered_altitude.pop(aid, None)
    handles.rendered_bank_deg.pop(aid, None)
    handles.desired_altitude.pop(aid, None)
    handles.desired_bank_deg.pop(aid, None)
    handles.previous_desired_altitude.pop(aid, None)
    handles.first_seen_tick.pop(aid, None)
    handles.last_struck_tick.pop(aid, None)


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

    handles = SceneHandles(root=root, fx_root=root.attach_new_node("fx-root"))
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

    home = getattr(header, "home_base", None)
    if home is not None:
        hx, hy = float(home[0]), float(home[1])
        home_np = root.attach_new_node("home-base")
        home_np.set_pos(hx, hy, 0.2)
        _attach_glyph(
            home_np, "home-pole", [(0.0, 0.0), (0.12, 0.0), (0.12, 1.2), (0.0, 1.2)], (0.85, 0.85, 0.9, 1.0)
        )
        _attach_glyph(
            home_np,
            "home-flag",
            [(0.12, 1.2), (0.85, 0.95), (0.12, 0.7)],
            HOME_BASE_COLOR,
        )
        _ground_disc(home_np, "home-pad", 0.0, 0.0, 1.4, (0.35, 0.78, 0.55, 0.22))
        handles.home_base = home_np

    for aid in header.jet_ids:
        anchor = root.attach_new_node(f"jet-{aid}")
        anchor.set_pos(cx, cy, GROUND_ALTITUDE)
        glyph = _attach_jet_dart(anchor, f"glyph-{aid}", _jet_color(aid, header))
        anchor.hide()  # no jet position in the header; first tick reveals them
        handles.jets[aid] = anchor
        handles.jet_glyphs[aid] = glyph
        handles.rendered_altitude[aid] = GROUND_ALTITUDE
        handles.rendered_bank_deg[aid] = 0.0

    return handles


def apply_jet_flight_easing(handles: SceneHandles, dt: float) -> None:
    """Ease rendered altitude/bank toward the latest per-tick targets every frame."""
    for aid, anchor in list(handles.jets.items()):
        if anchor.is_empty() or anchor.is_hidden():
            continue
        target_z = handles.desired_altitude.get(aid, CRUISE_ALTITUDE)
        target_bank = handles.desired_bank_deg.get(aid, 0.0)
        current_z = handles.rendered_altitude.get(aid, GROUND_ALTITUDE)
        current_bank = handles.rendered_bank_deg.get(aid, 0.0)
        new_z = ease_toward(current_z, target_z, MAX_CLIMB_RATE_PER_SECOND, dt)
        new_bank = ease_toward(current_bank, target_bank, MAX_BANK_RATE_PER_SECOND, dt)
        handles.rendered_altitude[aid] = new_z
        handles.rendered_bank_deg[aid] = new_bank
        pos = anchor.get_pos()
        anchor.set_pos(pos.x, pos.y, new_z)
        heading = handles.jet_heading_deg.get(aid, 0.0)
        glyph = handles.jet_glyphs.get(aid)
        if glyph is not None and not glyph.is_empty():
            # pitch=0.0: optional climb-rate pitch tilt deferred to a later phase.
            glyph.set_hpr(heading, 0.0, -new_bank)


def update_tick(
    handles: SceneHandles,
    header: TraceHeader,
    tick: TraceTick,
    *,
    seconds_airborne: dict[str, float] | None = None,
    just_struck: set[str] | None = None,
    tick_dt: float = 1.0 / 60.0,
) -> None:
    airborne = seconds_airborne or {}
    struck = just_struck or set()
    alive_ids = {frame.agent_id for frame in tick.jets}
    for aid in list(handles.jets):
        if aid not in alive_ids:
            handles.jets[aid].remove_node()
            handles.jets.pop(aid)
            _pop_jet_state(handles, aid)

    for frame in tick.jets:
        aid = frame.agent_id
        anchor = handles.jets.get(aid)
        if anchor is None or anchor.is_empty():
            continue
        x, y = frame.position
        anchor.set_pos(x, y, handles.rendered_altitude.get(aid, GROUND_ALTITUDE))
        anchor.show()
        if aid not in handles.first_seen_tick:
            handles.first_seen_tick[aid] = tick.tick
        heading = float(frame.heading)
        handles.jet_heading_deg[aid] = heading
        target_z = desired_altitude(
            airborne.get(aid, 0.0),
            frame.position,
            header,
            tick,
            just_struck=aid in struck,
        )
        prev_z = handles.previous_desired_altitude.get(aid)
        handles.desired_altitude[aid] = target_z
        handles.desired_bank_deg[aid] = desired_bank_deg(prev_z, target_z, tick_dt)
        handles.previous_desired_altitude[aid] = target_z
        handles.last_jet_xy[aid] = (x, y)

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


def _fx_parent(handles: SceneHandles) -> NodePath:
    if handles.fx_root is not None and not handles.fx_root.is_empty():
        return handles.fx_root
    return handles.root


def _ground_ring(parent: NodePath, name: str, x: float, y: float, z: float, radius: float, color) -> NodePath:
    segs = LineSegs(name)
    segs.set_thickness(2.5)
    segs.set_color(*color)
    segments = 24
    for i in range(segments + 1):
        ang = 2 * math.pi * i / segments
        px = x + radius * math.cos(ang)
        py = y + radius * math.sin(ang)
        if i == 0:
            segs.move_to(px, py, z)
        else:
            segs.draw_to(px, py, z)
    np = parent.attach_new_node(segs.create())
    np.set_light_off()
    np.set_transparency(TransparencyAttrib.MAlpha)
    return np


def flash_strike(handles: SceneHandles, strike: StrikeEvent, jet_xy, target_xy, z_jet: float = JET_ALTITUDE) -> None:
    """Short-lived colored beam from the striking jet to the target."""
    parent = _fx_parent(handles)
    segs = LineSegs(f"strike-{strike.agent_id}-{strike.target_id}")
    segs.set_thickness(3.0)
    segs.set_color(*STRIKE_COLOR)
    segs.move_to(jet_xy[0], jet_xy[1], z_jet)
    segs.draw_to(target_xy[0], target_xy[1], 0.25)
    np = parent.attach_new_node(segs.create())
    np.set_light_off()
    np.set_transparency(TransparencyAttrib.MAlpha)
    handles.highlight_fx.append(_FxItem(node=np))


def pulse_destruction(handles: SceneHandles, entity_id: str, position=None) -> None:
    """Brief white ring at the destroyed entity's last-known position."""
    x = y = z = None
    if position is not None:
        x, y = position[0], position[1]
        z = position[2] if len(position) > 2 else 0.3
    else:
        node = handles.jets.get(entity_id) or handles.targets.get(entity_id)
        if node is not None and not node.is_empty():
            p = node.get_pos()
            x, y, z = p.x, p.y, p.z + 0.2
    if x is None:
        return
    np = _ground_ring(_fx_parent(handles), f"pulse-{entity_id}", x, y, z or 0.3, 2.4, DESTRUCTION_COLOR)
    handles.highlight_fx.append(_FxItem(node=np, duration=0.55))


def flash_jam_transition(handles: SceneHandles, jet_id: str, jammed: bool) -> None:
    """Color-flash the jet glyph on jam / unjam transitions."""
    glyph = handles.jet_glyphs.get(jet_id)
    anchor = handles.jets.get(jet_id)
    color = JAM_FLASH_COLOR if jammed else UNJAM_FLASH_COLOR
    if glyph is not None and not glyph.is_empty():
        original = glyph.get_color()
        glyph.set_color(*color)

        def _restore(g=glyph, c=original) -> None:
            if g is not None and not g.is_empty():
                g.set_color(c)

        dummy = _fx_parent(handles).attach_new_node(f"jam-flash-{jet_id}")
        handles.highlight_fx.append(_FxItem(node=dummy, restore=_restore, duration=0.5))
        return
    if anchor is None or anchor.is_empty():
        return
    p = anchor.get_pos()
    np = _ground_ring(_fx_parent(handles), f"jam-ring-{jet_id}", p.x, p.y, p.z, 2.0, color)
    handles.highlight_fx.append(_FxItem(node=np, duration=0.5))


def flash_rtb_transition(handles: SceneHandles, jet_id: str) -> None:
    """Brief blue ring when a jet begins RTB, analogous to the jam flash."""
    anchor = handles.jets.get(jet_id)
    glyph = handles.jet_glyphs.get(jet_id)
    if glyph is not None and not glyph.is_empty():
        original = glyph.get_color()
        glyph.set_color(*RTB_FLASH_COLOR)

        def _restore(g=glyph, c=original) -> None:
            if g is not None and not g.is_empty():
                g.set_color(c)

        dummy = _fx_parent(handles).attach_new_node(f"rtb-flash-{jet_id}")
        handles.highlight_fx.append(_FxItem(node=dummy, restore=_restore, duration=0.5))
        return
    if anchor is None or anchor.is_empty():
        last = handles.last_jet_xy.get(jet_id)
        if last is None:
            return
        np = _ground_ring(
            _fx_parent(handles), f"rtb-ring-{jet_id}", last[0], last[1], 0.4, 2.0, RTB_FLASH_COLOR
        )
        handles.highlight_fx.append(_FxItem(node=np, duration=0.5))
        return
    p = anchor.get_pos()
    np = _ground_ring(_fx_parent(handles), f"rtb-ring-{jet_id}", p.x, p.y, p.z, 2.0, RTB_FLASH_COLOR)
    handles.highlight_fx.append(_FxItem(node=np, duration=0.5))


def decay_highlights(handles: SceneHandles, dt: float) -> None:
    """Fade/remove short-lived highlight nodes. Called once per frame."""
    still: list[_FxItem] = []
    for item in handles.highlight_fx:
        if item.node is None or item.node.is_empty():
            if item.restore is not None:
                item.restore()
            continue
        item.age += dt
        frac = 1.0 - (item.age / item.duration)
        if frac <= 0.0:
            if item.restore is not None:
                item.restore()
            if not item.node.is_empty():
                item.node.remove_node()
            continue
        item.node.set_color_scale(1.0, 1.0, 1.0, max(0.0, frac))
        still.append(item)
    handles.highlight_fx = still


def apply_tick_highlights(
    handles: SceneHandles,
    header: TraceHeader,
    previous: TraceTick | None,
    current: TraceTick,
    highlights: TickHighlights,
) -> None:
    """Spawn 3D flashes for this tick's highlights. Call *before* update_tick
    so destroyed-jet nodes still exist for position lookup.
    """
    jet_xy: dict[str, tuple[float, float]] = {}
    if previous is not None:
        for jet in previous.jets:
            jet_xy[jet.agent_id] = jet.position
    for jet in current.jets:
        jet_xy[jet.agent_id] = jet.position

    for strike in highlights.strikes:
        origin = jet_xy.get(strike.agent_id)
        target = header.target_positions.get(strike.target_id)
        if origin is None or target is None:
            continue
        z_jet = handles.rendered_altitude.get(
            strike.agent_id,
            handles.desired_altitude.get(
                strike.agent_id, jet_altitude(strike.agent_id, header)
            ),
        )
        flash_strike(handles, strike, origin, target, z_jet=z_jet)
    for tid in highlights.newly_destroyed_target_ids:
        pos = header.target_positions.get(tid)
        pulse_destruction(handles, tid, pos)
    for aid in highlights.newly_destroyed_jet_ids:
        pos = jet_xy.get(aid)
        if pos is not None:
            z = handles.rendered_altitude.get(
                aid, handles.desired_altitude.get(aid, jet_altitude(aid, header))
            )
            pulse_destruction(handles, aid, (pos[0], pos[1], z))
        else:
            pulse_destruction(handles, aid)
    for aid in highlights.newly_jammed_jet_ids:
        flash_jam_transition(handles, aid, True)
    for aid in highlights.newly_unjammed_jet_ids:
        flash_jam_transition(handles, aid, False)
    for aid in getattr(highlights, "newly_rtb_jet_ids", []):
        flash_rtb_transition(handles, aid)
