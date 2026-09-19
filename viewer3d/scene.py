"""Builds and updates the 3D scene graph from TraceHeader/TraceTick data.
Coordinate mapping: sim (x, y) -> Panda3D (x, y). Phase 37: jet Z prefers
authoritative Trace JetFrame.altitude; bank easing remains cosmetic.

Ground entities, jets, and ships use non-billboarded procedural meshes.
Faction is paint: teal-green friendlies vs rust-red hostiles. Heading is
written onto `glyph.get_h()` for jets and onto vehicle/ship anchors.

Presentation notes (holographic mission-briefing style, design doc §6.3)
-----------------------------------------------------------------------
* Every glyph is a *group* node (`glyph-<id>`). Meshes tag `jet-mesh`,
  `ship-mesh`, or `ground-mesh`. Jam/RTB flashes still tint the group.
* Ground entities are drawn as *pins*: a stem from their true ground point
  up to the glyph plus a ground reticle at the anchor. The anchor position
  is untouched; the lift only stops the billboard from being sliced in
  half by the terrain it stands on.
* Jets get the mirror-image cue: a drop line down to the map and a ground
  reticle at their ground track. Both live under a single `altcue-<id>`
  node whose Z scale is the rendered altitude, so the cue follows the
  authoritative altitude without any extra geometry rebuilds.
* Range discs are boundaries, not washes: a nearly transparent interior,
  a gradient rim band inside the true radius, a crisp edge circle *at*
  the true radius and inward bearing ticks.
"""

from __future__ import annotations

import math
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Callable

from panda3d.core import (
    CardMaker,
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

from viewer3d.effects import uses_instant_strike_beam
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
from viewer3d.motion import SampledPose
from viewer3d.terrain_mesh import build_terrain_root, resolve_header_terrain
from viewer3d.textures import (
    ENEMY,
    FRIENDLY,
    attach_ground_mesh,
    attach_jet_mesh,
    attach_ship_mesh,
    faction_paint,
    friendly_variant,
)
from viewer3d.ui_chrome import (
    ACCENT,
    ACCENT_DIM,
    BORDER_BRIGHT,
    BORDER_SOFT,
    BORDER_STRONG,
    STATUS_INACTIVE,
    tint,
)
from wraithnet.trace_schemas import (
    CarrierFrame,
    CorvetteFrame,
    StrikeEvent,
    TankFrame,
    TraceHeader,
    TraceTick,
)

JET_ALTITUDE = 8.0  # world-units above the ground plane; presentation only

# Same palette as viewer/render.py::JET_PALETTE is no longer used for 3D
# jets — friendlies share a teal family so they never read as hostile.
TARGET_COLORS = {
    "HARDENED_BUNKER": ENEMY,
    "MOBILE_RADAR": ENEMY,
    "SAM_SITE": ENEMY,
    "AAA_SITE": ENEMY,
}
DESTROYED_COLOR = STATUS_INACTIVE
JAMMER_COLOR = ENEMY

# --- world chrome: agrees with viewer3d.ui_chrome so the panels and the map
# read as one instrument. Minor gridlines are deliberately much quieter than
# before; the hierarchy now comes from GRID_MAJOR_COLOR / GRID_EDGE_COLOR.
GRID_COLOR = tint(ACCENT_DIM, 0.62, alpha=0.42)
GRID_MAJOR_COLOR = (*BORDER_STRONG[:3], 0.80)
GRID_EDGE_COLOR = (*BORDER_BRIGHT[:3], 0.95)
GRID_INNER_EDGE_COLOR = (*BORDER_SOFT[:3], 0.55)
#: Every Nth cell line is promoted to a major gridline.
GRID_MAJOR_EVERY = 5
#: Minor lines fade this far toward transparent at the map boundary.
GRID_DEPTH_FADE = 0.62
GROUND_COLOR = (0.014, 0.030, 0.042, 1.0)
#: Glow band standing on the map boundary -- the horizon treatment.
HORIZON_COLOR = (*BORDER_BRIGHT[:3], 0.20)
#: Band height as a fraction of the map's longer side.
HORIZON_HEIGHT_FRACTION = 0.055
#: Atmospheric wash pooling at the map edge (depth cue).
HAZE_COLOR = tint(ACCENT, 0.40, alpha=0.09)

# Keyline / halo used by every glyph so symbols survive any backdrop.
GLYPH_KEYLINE_COLOR = (0.88, 0.97, 1.0, 0.92)
GLYPH_HALO_COLOR = (0.015, 0.035, 0.048, 0.86)

SAM_DISC_COLOR = (0.90, 0.26, 0.24, 1.0)
AAA_DISC_COLOR = (0.95, 0.58, 0.18, 1.0)
JAM_DISC_COLOR = (0.62, 0.32, 0.85, 1.0)
GLYPH_SCALE = 1.85
HIGHLIGHT_DURATION = 0.45
STRIKE_COLOR = (1.0, 0.90, 0.32, 1.0)
DESTRUCTION_COLOR = (1.0, 1.0, 1.0, 1.0)
JAM_FLASH_COLOR = (1.0, 0.35, 1.0, 1.0)
UNJAM_FLASH_COLOR = (0.31, 0.86, 1.0, 1.0)
RTB_FLASH_COLOR = (0.31, 0.63, 1.0, 1.0)
HOME_BASE_COLOR = FRIENDLY
CARRIER_COLOR = FRIENDLY
CARRIER_RECOVERY_RING = (0.45, 0.78, 0.95, 1.0)
CORVETTE_COLOR = ENEMY
CORVETTE_SAM_RING = (0.88, 0.28, 0.26, 1.0)
CORVETTE_CIWS_RING = (0.96, 0.58, 0.22, 1.0)
TANK_FRIENDLY_COLOR = FRIENDLY
TANK_ENEMY_COLOR = ENEMY
MISSILE_COLOR = (1.0, 0.82, 0.25, 1.0)


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
    carriers: dict[str, NodePath] = field(default_factory=dict)
    corvettes: dict[str, NodePath] = field(default_factory=dict)
    tanks: dict[str, NodePath] = field(default_factory=dict)
    fx_root: NodePath | None = None
    highlight_fx: list[_FxItem] = field(default_factory=list)
    pending_jet_removal: set[str] = field(default_factory=set)
    terrain_root: NodePath | None = None
    water_anim_phase: float = 0.0
    # Deg/sec spin for mobile-radar AESA heads (cosmetic, presentation clock).
    radar_spin_deg_per_sec: float = 45.0
    # Phase 38: authoritative in-flight missile glyphs keyed by missile_id.
    missiles: dict[str, NodePath] = field(default_factory=dict)


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


def _gradient_geom(name: str, verts, tris) -> GeomNode:
    """Triangles with a per-vertex color. ``verts`` is ``[(x, y, z, rgba)]``."""
    fmt = GeomVertexFormat.get_v3c4()
    vdata = GeomVertexData(name, fmt, Geom.UH_static)
    vertex = GeomVertexWriter(vdata, "vertex")
    col = GeomVertexWriter(vdata, "color")
    for x, y, z, rgba in verts:
        vertex.add_data3(x, y, z)
        col.add_data4(*rgba)
    prim = GeomTriangles(Geom.UH_static)
    for i, j, k in tris:
        prim.add_vertices(i, j, k)
    prim.close_primitive()
    geom = Geom(vdata)
    geom.add_primitive(prim)
    node = GeomNode(name)
    node.add_geom(geom)
    return node


def _polyline_geom(
    name: str, points_xz, color, thickness: float = 1.6, closed: bool = True
) -> GeomNode:
    """Outline of a glyph polygon, in the same local XZ plane as the fill."""
    segs = LineSegs(name)
    segs.set_thickness(thickness)
    segs.set_color(*color)
    x0, z0 = points_xz[0]
    segs.move_to(x0, 0.0, z0)
    for x, z in points_xz[1:]:
        segs.draw_to(x, 0.0, z)
    if closed:
        segs.draw_to(x0, 0.0, z0)
    return segs.create()


def _scaled_pts(points_xz, factor: float):
    return [(x * factor, z * factor) for x, z in points_xz]


def _ring_band_geom(
    name: str,
    inner_radius: float,
    outer_radius: float,
    inner_color,
    outer_color,
    segments: int = 64,
) -> GeomNode:
    """Flat XY annulus whose color ramps from the inner to the outer edge."""
    verts = []
    tris = []
    for i in range(segments + 1):
        ang = 2 * math.pi * i / segments
        cos_a, sin_a = math.cos(ang), math.sin(ang)
        verts.append((inner_radius * cos_a, inner_radius * sin_a, 0.0, inner_color))
        verts.append((outer_radius * cos_a, outer_radius * sin_a, 0.0, outer_color))
    for i in range(segments):
        a, b, c, d = 2 * i, 2 * i + 1, 2 * i + 2, 2 * i + 3
        tris.append((a, b, c))
        tris.append((b, d, c))
    return _gradient_geom(name, verts, tris)


def _attach_glyph(
    parent: NodePath,
    name: str,
    points_xz,
    color,
    *,
    pin: bool = False,
    inner: float | None = None,
    halo: bool = True,
    keyline=GLYPH_KEYLINE_COLOR,
) -> NodePath:
    """Camera-facing symbol: dark halo ring + colored fill + bright keyline.

    The result is a group node, so ``set_color()`` on it recolors the fill
    while the halo and keyline keep their own colors -- that is what makes a
    glyph legible over a range disc or a city block. ``pin=True`` lifts the
    symbol clear of the ground and attaches a stem plus a ground reticle
    under ``parent``, which marks the entity's true ground point without
    moving the anchor.
    """
    group = parent.attach_new_node(name)
    group.set_billboard_point_eye()
    group.set_light_off()
    group.set_two_sided(True)
    group.set_transparency(TransparencyAttrib.MAlpha)
    group.set_scale(GLYPH_SCALE)

    if halo:
        # Only meaningful for a glyph centered on the origin; the home-base
        # pole/flag are drawn offset, so they opt out.
        backing = group.attach_new_node(
            _polygon_geom("sym-halo", _scaled_pts(points_xz, 1.34), GLYPH_HALO_COLOR)
        )
        backing.set_color(*GLYPH_HALO_COLOR)

    group.attach_new_node(_polygon_geom("sym-fill", points_xz, color)).set_depth_offset(1)

    edge = group.attach_new_node(_polyline_geom("sym-edge", points_xz, keyline, 1.8))
    edge.set_color(*keyline)
    edge.set_depth_offset(2)

    if inner is not None:
        detail = group.attach_new_node(
            _polyline_geom("sym-inner", _scaled_pts(points_xz, inner), keyline, 1.2)
        )
        detail.set_color(*tint(keyline, 0.85, alpha=0.65))
        detail.set_depth_offset(2)

    if pin:
        lift = -min(z for _x, z in points_xz) * GLYPH_SCALE + 0.14
        group.set_z(lift)
        _attach_ground_pin(parent, color, lift)
    return group


def _is_textured(node: NodePath) -> bool:
    return (not node.is_empty()) and node.get_tag("textured-sprite") == "1"


def _paint_glyph(glyph: NodePath, color) -> None:
    """Recolor a procedural glyph. Textured sprites reset to un-tinted white."""
    if glyph.is_empty():
        return
    if _is_textured(glyph):
        glyph.set_color(1.0, 1.0, 1.0, 1.0)
        return
    glyph.set_color(*color)


def _attach_textured_sprite(
    parent: NodePath,
    name: str,
    tex,
    color,
    *,
    pin: bool = True,
    billboard: bool = True,
    half: float = 1.35,
) -> NodePath:
    """Camera-facing (or heading-oriented) card with a keyed JPEG."""
    group = parent.attach_new_node(name)
    if billboard:
        group.set_billboard_point_eye()
    group.set_light_off()
    group.set_two_sided(True)
    group.set_transparency(TransparencyAttrib.MAlpha)
    group.set_tag("textured-sprite", "1")
    group.set_color(1.0, 1.0, 1.0, 1.0)
    cm = CardMaker("sym-sprite")
    cm.set_frame(-half, half, -half, half)
    card = group.attach_new_node(cm.generate())
    card.set_name("sym-sprite")
    card.set_texture(tex)
    card.set_transparency(TransparencyAttrib.MAlpha)
    card.set_two_sided(True)
    card.set_depth_write(False)
    card.set_depth_offset(1)
    if pin:
        lift = half + 0.14
        group.set_z(lift)
        _attach_ground_pin(parent, color, lift)
    return group


def _attach_unit(
    parent: NodePath,
    name: str,
    color,
    *,
    sprite_key: str | None,
    points,
    pin: bool = True,
    inner: float | None = None,
    billboard: bool = True,
    halo: bool = True,
) -> NodePath:
    del sprite_key, billboard  # JPEG unit billboards are retired in viewer3d.
    return _attach_glyph(
        parent, name, points, color, pin=pin, inner=inner, halo=halo
    )


def _attach_ground(
    parent: NodePath,
    name: str,
    key: str,
    color,
    points,
    *,
    pin: bool = False,
) -> NodePath:
    glyph = attach_ground_mesh(parent, name, key, color)
    if glyph is not None:
        return glyph
    return _attach_unit(
        parent, name, color, sprite_key=key, points=points, pin=pin
    )


def _attach_ground_pin(parent: NodePath, color, height: float) -> NodePath:
    """Stem from the anchor's ground point up to a lifted glyph, plus a tick.

    Purely a legibility aid: it ties a billboarded symbol back to the exact
    map coordinate it stands on.
    """
    pin = parent.attach_new_node("pin")
    stem_color = tint(color, 0.85, alpha=0.55)
    segs = LineSegs("pin-stem")
    segs.set_thickness(1.6)
    segs.set_color(*stem_color)
    segs.move_to(0.0, 0.0, -0.12)
    segs.draw_to(0.0, 0.0, height)
    pin.attach_new_node(segs.create())
    mark = pin.attach_new_node(_reticle_geom("pin-base", 0.62, color))
    mark.set_z(-0.10)
    pin.set_light_off()
    pin.set_transparency(TransparencyAttrib.MAlpha)
    pin.set_depth_offset(1)
    return pin


def _reticle_geom(name: str, radius: float, color, ticks: int = 4) -> GeomNode:
    """Flat XY ground marker: a thin ring with short cardinal ticks."""
    segs = LineSegs(name)
    segs.set_thickness(1.8)
    segs.set_color(*tint(color, 1.0, alpha=0.80))
    steps = 24
    for i in range(steps + 1):
        ang = 2 * math.pi * i / steps
        px, py = radius * math.cos(ang), radius * math.sin(ang)
        if i == 0:
            segs.move_to(px, py, 0.0)
        else:
            segs.draw_to(px, py, 0.0)
    for i in range(ticks):
        ang = 2 * math.pi * i / ticks + math.pi / 4.0
        cos_a, sin_a = math.cos(ang), math.sin(ang)
        segs.move_to(radius * 1.05 * cos_a, radius * 1.05 * sin_a, 0.0)
        segs.draw_to(radius * 1.75 * cos_a, radius * 1.75 * sin_a, 0.0)
    return segs.create()


def _dart_geom(name: str, color) -> GeomNode:
    """Elongated double-sided dart: long nose +Y so heading reads clearly.

    Aspect is roughly 3:1 (nose→tail vs wingtip→wingtip). Wings sit aft of
    mid-body so the silhouette reads as an arrow from orbit/chase cameras.
    """
    verts = [
        (0.0, 1.35, 0.0),  # nose
        (-0.42, -0.15, -0.06),  # left wing leading
        (-0.55, -0.45, -0.08),  # left wingtip
        (-0.12, -0.95, 0.02),  # left tail
        (0.0, -0.75, 0.12),  # dorsal fin / spine
        (0.12, -0.95, 0.02),  # right tail
        (0.55, -0.45, -0.08),  # right wingtip
        (0.42, -0.15, -0.06),  # right wing leading
    ]
    # Fan from nose for the forward body, then aft deck triangles.
    tris = [
        (0, 1, 7),  # nose plate
        (1, 2, 3),  # left wing
        (1, 3, 4),  # left aft
        (7, 4, 5),  # right aft (via spine)
        (7, 5, 6),  # right wing
        (1, 4, 7),  # center deck
    ]
    fmt = GeomVertexFormat.get_v3c4()
    vdata = GeomVertexData(name, fmt, Geom.UH_static)
    vertex = GeomVertexWriter(vdata, "vertex")
    col = GeomVertexWriter(vdata, "color")
    r, g, b, a = color
    for x, y, z in verts:
        vertex.add_data3(x, y, z)
        col.add_data4(r, g, b, a)
    prim = GeomTriangles(Geom.UH_static)
    for i, j, k in tris:
        prim.add_vertices(i, j, k)
    prim.close_primitive()
    geom = Geom(vdata)
    geom.add_primitive(prim)
    node = GeomNode(name)
    node.add_geom(geom)
    return node


#: Silhouette loop through `_dart_geom`'s perimeter vertices, plus the spine.
_DART_OUTLINE = ((0, 1, 2, 3, 5, 6, 7, 0), (0, 4), (3, 4, 5))


def _dart_outline_geom(name: str, color) -> GeomNode:
    """Edge-light wireframe over the dart so it reads against any backdrop."""
    verts = [
        (0.0, 1.35, 0.0),
        (-0.42, -0.15, -0.06),
        (-0.55, -0.45, -0.08),
        (-0.12, -0.95, 0.02),
        (0.0, -0.75, 0.12),
        (0.12, -0.95, 0.02),
        (0.55, -0.45, -0.08),
        (0.42, -0.15, -0.06),
    ]
    segs = LineSegs(name)
    segs.set_thickness(1.7)
    segs.set_color(*color)
    for path in _DART_OUTLINE:
        segs.move_to(*verts[path[0]])
        for idx in path[1:]:
            segs.draw_to(*verts[idx])
    return segs.create()


def _attach_jet_dart(parent: NodePath, name: str, color) -> NodePath:
    """Jet glyph without billboarding -- true 3D heading/bank via set_hpr.

    A group, like `_attach_glyph`: the keyline child keeps its own color so
    the jam/RTB `set_color()` flashes only tint the body.
    """
    group = parent.attach_new_node(name)
    group.set_light_off()
    group.set_two_sided(True)
    group.set_transparency(TransparencyAttrib.MAlpha)
    group.set_scale(GLYPH_SCALE)
    group.attach_new_node(_dart_geom("sym-fill", color))
    edge = group.attach_new_node(_dart_outline_geom("sym-edge", GLYPH_KEYLINE_COLOR))
    edge.set_color(*GLYPH_KEYLINE_COLOR)
    edge.set_depth_offset(2)
    # Explicit, so get_color()/set_color() round-trip for the jam/RTB flashes.
    group.set_color(*color)
    return group


def _attach_altitude_cue(parent: NodePath, name: str, color) -> NodePath:
    """Drop line + ground reticle tying a jet glyph to its map position.

    Built one world-unit tall and Z-scaled to the rendered altitude by
    `_update_altitude_cue`, so nothing is rebuilt per frame and no altitude
    value is invented here -- it only draws the altitude the sim already has.
    """
    cue = parent.attach_new_node(name)
    segs = LineSegs("drop-line")
    segs.set_thickness(1.5)
    segs.set_color(*tint(color, 0.9, alpha=0.55))
    segs.move_to(0.0, 0.0, -0.04)
    segs.draw_to(0.0, 0.0, -1.0)
    cue.attach_new_node(segs.create())
    mark = cue.attach_new_node(_reticle_geom("ground-mark", 0.85, color))
    mark.set_z(-1.0)
    cue.set_light_off()
    cue.set_transparency(TransparencyAttrib.MAlpha)
    cue.set_depth_offset(1)
    return cue


def _update_altitude_cue(handles: SceneHandles, agent_id: str, altitude: float) -> None:
    """Stretch a jet's altitude cue so its reticle lands on the map plane."""
    anchor = handles.jets.get(agent_id)
    if anchor is None or anchor.is_empty():
        return
    cue = anchor.find(f"**/altcue-{agent_id}")
    if cue.is_empty():
        return
    cue.set_sz(max(float(altitude), 0.02))


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


def _grid_fade(x: float, y: float, cx: float, cy: float, reach: float) -> float:
    """Alpha multiplier for a gridline vertex -- quieter toward the boundary.

    A flat-alpha grid gives the map no sense of depth or scale; ramping the
    minor lines down toward the edge does, and costs nothing but vertices.
    """
    if reach <= 0.0:
        return 1.0
    d = min(math.hypot(x - cx, y - cy) / reach, 1.0)
    return max(1.0 - GRID_DEPTH_FADE * (d**1.4), 0.18)


def _grid_lines(parent: NodePath, width: float, height: float, step: float = 5.0) -> NodePath:
    """Two-tier tactical grid: faded minor cells under bold major lines."""
    root = parent.attach_new_node("grid")
    cx, cy = width / 2.0, height / 2.0
    reach = math.hypot(cx, cy)
    major_step = step * GRID_MAJOR_EVERY

    minor = LineSegs("grid-minor")
    minor.set_thickness(1.0)
    major = LineSegs("grid-major")
    major.set_thickness(2.2)
    mr, mg, mb, ma = GRID_COLOR

    def _run(fixed: float, axis: str, is_major: bool) -> None:
        """One full gridline, chopped per cell so the fade can vary along it."""
        span = height if axis == "y" else width
        segs = major if is_major else minor
        pos = 0.0
        while pos < span - 1e-9:
            nxt = min(pos + step, span)
            for value, start in ((pos, True), (nxt, False)):
                px, py = (fixed, value) if axis == "y" else (value, fixed)
                if is_major:
                    segs.set_color(*GRID_MAJOR_COLOR)
                else:
                    segs.set_color(mr, mg, mb, ma * _grid_fade(px, py, cx, cy, reach))
                if start:
                    segs.move_to(px, py, 0.02)
                else:
                    segs.draw_to(px, py, 0.02)
            pos = nxt

    lines_x = int(round(width / step))
    for i in range(lines_x + 1):
        x = min(i * step, width)
        _run(x, "y", abs(x % major_step) < 1e-6 or abs(x - width) < 1e-6)
    lines_y = int(round(height / step))
    for i in range(lines_y + 1):
        y = min(i * step, height)
        _run(y, "x", abs(y % major_step) < 1e-6 or abs(y - height) < 1e-6)

    for segs in (minor, major):
        node = root.attach_new_node(segs.create())
        node.set_light_off()
        node.set_transparency(TransparencyAttrib.MAlpha)
    return root


def _world_boundary(parent: NodePath, width: float, height: float) -> NodePath:
    """Defined map edge: a bright keyline, an inset rule and corner brackets."""
    root = parent.attach_new_node("world-boundary")
    inset = min(width, height) * 0.016
    bracket = min(width, height) * 0.085

    edge = LineSegs("boundary-edge")
    edge.set_thickness(2.8)
    edge.set_color(*GRID_EDGE_COLOR)
    for i, (px, py) in enumerate(
        ((0.0, 0.0), (width, 0.0), (width, height), (0.0, height), (0.0, 0.0))
    ):
        if i == 0:
            edge.move_to(px, py, 0.04)
        else:
            edge.draw_to(px, py, 0.04)

    rule = LineSegs("boundary-inset")
    rule.set_thickness(1.2)
    rule.set_color(*GRID_INNER_EDGE_COLOR)
    for i, (px, py) in enumerate(
        (
            (inset, inset),
            (width - inset, inset),
            (width - inset, height - inset),
            (inset, height - inset),
            (inset, inset),
        )
    ):
        if i == 0:
            rule.move_to(px, py, 0.05)
        else:
            rule.draw_to(px, py, 0.05)

    corners = LineSegs("boundary-corners")
    corners.set_thickness(4.0)
    corners.set_color(*GRID_EDGE_COLOR)
    for ox, oy, sx, sy in (
        (0.0, 0.0, 1.0, 1.0),
        (width, 0.0, -1.0, 1.0),
        (width, height, -1.0, -1.0),
        (0.0, height, 1.0, -1.0),
    ):
        corners.move_to(ox + sx * bracket, oy, 0.07)
        corners.draw_to(ox, oy, 0.07)
        corners.draw_to(ox, oy + sy * bracket, 0.07)

    for segs in (edge, rule, corners):
        node = root.attach_new_node(segs.create())
        node.set_light_off()
        node.set_transparency(TransparencyAttrib.MAlpha)
    return root


def _horizon_curtain(parent: NodePath, width: float, height: float) -> NodePath:
    """Low glow band standing on the boundary, fading out with altitude.

    Replaces the old hard cut from lit grid straight into black: the map now
    has a lit horizon line. Kept deliberately short -- a tall wall reads as a
    flat wash over a third of the frame instead of as a horizon.
    """
    wall_h = HORIZON_HEIGHT_FRACTION * max(width, height)
    base = HORIZON_COLOR
    top = (base[0], base[1], base[2], 0.0)
    verts = []
    tris = []
    corners = ((0.0, 0.0), (width, 0.0), (width, height), (0.0, height), (0.0, 0.0))
    for i in range(len(corners) - 1):
        (x0, y0), (x1, y1) = corners[i], corners[i + 1]
        b = len(verts)
        verts.extend(
            [
                (x0, y0, 0.0, base),
                (x1, y1, 0.0, base),
                (x1, y1, wall_h, top),
                (x0, y0, wall_h, top),
            ]
        )
        tris.extend([(b, b + 1, b + 2), (b, b + 2, b + 3)])
    node = parent.attach_new_node(_gradient_geom("horizon-curtain", verts, tris))
    node.set_light_off()
    node.set_transparency(TransparencyAttrib.MAlpha)
    node.set_two_sided(True)
    node.set_depth_write(False)
    return node


def _ground_haze(parent: NodePath, width: float, height: float) -> NodePath:
    """Atmospheric wash inside the boundary, brightest at the map edge.

    A depth cue that stays *on* the map: an outward skirt past the boundary
    stretches almost edge-on from a low camera and turns into a large muddy
    smear rather than atmosphere.
    """
    band = min(width, height) * 0.22
    edge = HAZE_COLOR
    middle = (edge[0], edge[1], edge[2], 0.0)
    outer = ((0.0, 0.0), (width, 0.0), (width, height), (0.0, height), (0.0, 0.0))
    inner = (
        (band, band),
        (width - band, band),
        (width - band, height - band),
        (band, height - band),
        (band, band),
    )
    verts = []
    tris = []
    for i in range(len(outer) - 1):
        b = len(verts)
        verts.extend(
            [
                (outer[i][0], outer[i][1], 0.0, edge),
                (outer[i + 1][0], outer[i + 1][1], 0.0, edge),
                (inner[i + 1][0], inner[i + 1][1], 0.0, middle),
                (inner[i][0], inner[i][1], 0.0, middle),
            ]
        )
        tris.extend([(b, b + 1, b + 2), (b, b + 2, b + 3)])
    node = parent.attach_new_node(_gradient_geom("ground-haze", verts, tris))
    node.set_pos(0.0, 0.0, 0.03)
    node.set_light_off()
    node.set_transparency(TransparencyAttrib.MAlpha)
    node.set_depth_write(False)
    return node


#: Alpha budget for a range disc: a near-invisible interior, a rim that
#: carries the read, and a crisp edge exactly on the true radius.
_DISC_CORE_ALPHA = 0.030
_DISC_RIM_ALPHA = 0.34
_DISC_EDGE_ALPHA = 0.92
#: Fraction of the radius occupied by the rim gradient band.
_DISC_RIM_BAND = 0.14
_DISC_BEARING_TICKS = 12


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
    """A range boundary at ``radius`` -- rim-led, not a filled wash.

    ``radius`` is still the literal engagement/jam radius: the edge circle
    sits exactly on it and the rim band grows inward, so overlapping discs
    stop swamping the terrain and each other while still reading as areas.
    Returns a group node named ``name`` (callers hide it wholesale).
    """
    group = parent.attach_new_node(name)
    group.set_pos(cx, cy, z)
    r, g, b = color[0], color[1], color[2]
    core = (r, g, b, _DISC_CORE_ALPHA)
    rim_inner = (r, g, b, 0.0)
    rim_outer = (r, g, b, _DISC_RIM_ALPHA)
    edge_color = (min(r * 1.15, 1.0), min(g * 1.15, 1.0), min(b * 1.15, 1.0), _DISC_EDGE_ALPHA)

    inner_r = radius * (1.0 - _DISC_RIM_BAND)
    group.attach_new_node(
        _ring_band_geom("disc-core", 0.0, inner_r, core, core, segments)
    )
    group.attach_new_node(
        _ring_band_geom("disc-rim", inner_r, radius, rim_inner, rim_outer, segments)
    )

    outline = LineSegs("disc-edge")
    outline.set_thickness(2.2)
    outline.set_color(*edge_color)
    for i in range(segments + 1):
        ang = 2 * math.pi * i / segments
        px, py = radius * math.cos(ang), radius * math.sin(ang)
        if i == 0:
            outline.move_to(px, py, 0.0)
        else:
            outline.draw_to(px, py, 0.0)
    for i in range(_DISC_BEARING_TICKS):
        ang = 2 * math.pi * i / _DISC_BEARING_TICKS
        cos_a, sin_a = math.cos(ang), math.sin(ang)
        outline.move_to(radius * cos_a, radius * sin_a, 0.0)
        outline.draw_to(radius * 0.90 * cos_a, radius * 0.90 * sin_a, 0.0)
    group.attach_new_node(outline.create())

    group.set_light_off()
    group.set_transparency(TransparencyAttrib.MAlpha)
    group.set_two_sided(True)
    # Overlapping discs must blend rather than punch holes in one another.
    group.set_depth_write(False)
    return group


def _jam_dome(
    parent: NodePath,
    name: str,
    radius: float,
    color,
    z: float = 0.05,
    segments: int = 48,
) -> NodePath:
    """EW coverage as a wireframe hemisphere, not another flat ground disc.

    Requirement 6: a jam bubble and a SAM footprint mean different things, so
    they must not share a treatment. The ground circle still sits exactly on
    ``radius``; the dome height is decoration.
    """
    group = parent.attach_new_node(name)
    group.set_pos(0.0, 0.0, z)
    r, g, b = color[0], color[1], color[2]
    dome_h = radius * 0.34

    group.attach_new_node(
        _ring_band_geom(
            "dome-core",
            0.0,
            radius,
            (r, g, b, _DISC_CORE_ALPHA),
            (r, g, b, _DISC_RIM_ALPHA * 0.55),
            segments,
        )
    )

    wire = LineSegs("dome-wire")
    wire.set_thickness(1.6)
    wire.set_color(r, g, b, 0.55)
    arc_steps = 16
    for meridian in range(4):
        phi = math.pi * meridian / 4.0
        cos_p, sin_p = math.cos(phi), math.sin(phi)
        for i in range(arc_steps + 1):
            t = math.pi * i / arc_steps
            rr = radius * math.cos(t - math.pi / 2.0)
            px = rr * cos_p
            py = rr * sin_p
            pz = dome_h * math.sin(t)
            if i == 0:
                wire.move_to(px, py, pz)
            else:
                wire.draw_to(px, py, pz)
    for frac in (0.55, 0.86):
        ring_r = radius * math.sqrt(max(1.0 - frac * frac, 0.0))
        ring_z = dome_h * frac
        for i in range(segments + 1):
            ang = 2 * math.pi * i / segments
            px, py = ring_r * math.cos(ang), ring_r * math.sin(ang)
            if i == 0:
                wire.move_to(px, py, ring_z)
            else:
                wire.draw_to(px, py, ring_z)
    group.attach_new_node(wire.create())

    edge = LineSegs("dome-edge")
    edge.set_thickness(2.4)
    edge.set_color(min(r * 1.15, 1.0), min(g * 1.15, 1.0), min(b * 1.15, 1.0), _DISC_EDGE_ALPHA)
    for i in range(segments + 1):
        ang = 2 * math.pi * i / segments
        px, py = radius * math.cos(ang), radius * math.sin(ang)
        if i == 0:
            edge.move_to(px, py, 0.0)
        else:
            edge.draw_to(px, py, 0.0)
    group.attach_new_node(edge.create())

    group.set_light_off()
    group.set_transparency(TransparencyAttrib.MAlpha)
    group.set_two_sided(True)
    group.set_depth_write(False)
    return group


def _jet_color(agent_id: str, header: TraceHeader):
    try:
        idx = header.jet_ids.index(agent_id)
    except ValueError:
        idx = 0
    return friendly_variant(idx)


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


def finish_removing_jet(handles: SceneHandles, agent_id: str) -> None:
    """Remove a jet node and all per-jet bookkeeping (deferred wreck callback)."""
    handles.pending_jet_removal.discard(agent_id)
    jet = handles.jets.pop(agent_id, None)
    if jet is not None and not jet.is_empty():
        jet.remove_node()
    _pop_jet_state(handles, agent_id)


def _target_points(target_type: str):
    if target_type == "MOBILE_RADAR":
        return _diamond_pts()
    if target_type == "SAM_SITE":
        return _ngon_pts(16, 0.55)
    return _square_pts()


def build_static_scene(render_root, header: TraceHeader) -> SceneHandles:
    """Called once, when the header first arrives. Jets start hidden until tick 0."""
    root = render_root.attach_new_node("wraithnet-scene")
    terrain = resolve_header_terrain(header)
    terrain_root = build_terrain_root(root, terrain)
    _ground_haze(root, header.grid_width, header.grid_height)
    _grid_lines(root, header.grid_width, header.grid_height, step=terrain.cell_size)
    _world_boundary(root, header.grid_width, header.grid_height)
    _horizon_curtain(root, header.grid_width, header.grid_height)

    handles = SceneHandles(
        root=root,
        fx_root=root.attach_new_node("fx-root"),
        terrain_root=terrain_root,
    )
    cx, cy = header.grid_width / 2.0, header.grid_height / 2.0

    for tid in header.target_ids:
        pos = header.target_positions[tid]
        ttype = header.target_types[tid]
        color = faction_paint("ENEMY")
        anchor = root.attach_new_node(f"target-{tid}")
        anchor.set_pos(pos[0], pos[1], 0.15)
        mesh_key = "AAA_LIGHT" if ttype == "AAA_SITE" else ttype
        _attach_ground(
            anchor,
            f"glyph-{tid}",
            mesh_key,
            color,
            points=_target_points(ttype),
            pin=False,
        )
        if ttype == "SAM_SITE" or tid in header.sam_site_ids:
            _ground_disc(
                anchor,
                f"sam-ring-{tid}",
                0.0,
                0.0,
                header.sam_engagement_range,
                SAM_DISC_COLOR,
            )
        if ttype == "AAA_SITE" or tid in getattr(header, "aaa_site_ids", []):
            _ground_disc(
                anchor,
                f"aaa-ring-{tid}",
                0.0,
                0.0,
                5.0,
                AAA_DISC_COLOR,
            )
        handles.targets[tid] = anchor

    for jid, pos in header.jammer_positions.items():
        anchor = root.attach_new_node(f"jammer-{jid}")
        anchor.set_pos(pos[0], pos[1], 0.15)
        _attach_ground(
            anchor,
            f"glyph-{jid}",
            "JAMMER",
            JAMMER_COLOR,
            points=_ngon_pts(6, 0.5, math.pi / 6),
            pin=False,
        )
        _jam_dome(anchor, f"jam-ring-{jid}", header.jam_radius, JAM_DISC_COLOR)
        handles.jammers[jid] = anchor

    home = getattr(header, "home_base", None)
    if home is not None:
        hx, hy = float(home[0]), float(home[1])
        home_np = root.attach_new_node("home-base")
        home_np.set_pos(hx, hy, 0.2)
        _attach_ground(
            home_np,
            "home-flag",
            "HOME_BASE",
            HOME_BASE_COLOR,
            points=[(0.12, 1.2), (0.85, 0.95), (0.12, 0.7)],
            pin=False,
        )
        _ground_disc(home_np, "home-pad", 0.0, 0.0, 1.4, HOME_BASE_COLOR)
        handles.home_base = home_np

    for aid in header.jet_ids:
        anchor = root.attach_new_node(f"jet-{aid}")
        anchor.set_pos(cx, cy, GROUND_ALTITUDE)
        jet_color = _jet_color(aid, header)
        frame_type = (header.jet_frame_types or {}).get(aid, "STRIKE_FIGHTER")
        glyph = attach_jet_mesh(anchor, f"glyph-{aid}", str(frame_type), jet_color)
        if glyph is None:
            glyph = _attach_jet_dart(anchor, f"glyph-{aid}", jet_color)
        _attach_altitude_cue(anchor, f"altcue-{aid}", jet_color)
        anchor.hide()  # no jet position in the header; first tick reveals them
        handles.jets[aid] = anchor
        handles.jet_glyphs[aid] = glyph
        handles.rendered_altitude[aid] = GROUND_ALTITUDE
        handles.rendered_bank_deg[aid] = 0.0

    return handles


def sync_static_unit_glyphs(
    handles: SceneHandles,
    *,
    carriers: list[CarrierFrame] | None = None,
    corvettes: list[CorvetteFrame] | None = None,
    tanks: list[TankFrame] | None = None,
) -> None:
    """Place/update carrier, corvette, and tank glyphs for editor static scenes."""
    tick = TraceTick(
        tick=0,
        jets=[],
        targets=[],
        strikes=[],
        running=True,
        swarm_wiped_out=False,
        carriers=list(carriers or []),
        corvettes=list(corvettes or []),
        tanks=list(tanks or []),
    )
    _update_carrier_glyphs(handles, tick)
    _update_corvette_glyphs(handles, tick)
    _update_tank_glyphs(handles, tick)


def apply_jet_flight_easing(handles: SceneHandles, dt: float) -> None:
    """Ease rendered altitude/bank toward the latest per-tick targets every frame.

    XY/heading are owned by ``apply_sampled_jet_poses`` on the live path; this
    only updates Z and bank so easing does not fight ground-track sampling.
    """
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
        _update_altitude_cue(handles, aid, new_z)
        heading = handles.jet_heading_deg.get(aid, 0.0)
        glyph = handles.jet_glyphs.get(aid)
        if glyph is not None and not glyph.is_empty():
            # Sim heading is clockwise from +y (0=+y, 90=+x). Panda3D H is
            # the opposite sense (H=90 sends local +Y to world -X), so negate.
            glyph.set_hpr(-heading, 0.0, -new_bank)


def apply_sampled_jet_poses(
    handles: SceneHandles, poses: Mapping[str, SampledPose]
) -> None:
    """Drive jet XY/heading from presentation-time samples (Phase 47).

    Safe when a pose id has no handle (late spawn) or a handle has no pose
    (despawn / pending wreck). Does not touch targets/SAMs/jammers.
    """
    for aid, pose in poses.items():
        if aid in handles.pending_jet_removal:
            continue
        anchor = handles.jets.get(aid)
        if anchor is None or anchor.is_empty():
            continue
        z = handles.rendered_altitude.get(aid)
        if z is None:
            z = handles.desired_altitude.get(aid, GROUND_ALTITUDE)
            handles.rendered_altitude[aid] = z
        anchor.set_pos(pose.x, pose.y, z)
        _update_altitude_cue(handles, aid, z)
        anchor.show()
        handles.jet_heading_deg[aid] = pose.heading_deg
        handles.last_jet_xy[aid] = (pose.x, pose.y)
        glyph = handles.jet_glyphs.get(aid)
        if glyph is not None and not glyph.is_empty():
            bank = handles.rendered_bank_deg.get(aid, 0.0)
            # pose.heading_deg is sim convention (CW from +y); Panda H is CCW.
            glyph.set_hpr(-pose.heading_deg, 0.0, -bank)


def update_tick(
    handles: SceneHandles,
    header: TraceHeader,
    tick: TraceTick,
    *,
    seconds_airborne: dict[str, float] | None = None,
    just_struck: set[str] | None = None,
    tick_dt: float = 1.0 / 60.0,
    on_jet_removed: Callable[[str, tuple[float, float, float]], None] | None = None,
    apply_jet_pose: bool = True,
) -> None:
    """Apply discrete tick visual state.

    When ``apply_jet_pose`` is False (live dual-clock path), XY/heading are left
    to ``apply_sampled_jet_poses``; this still updates altitude desires, target
    colors, removals, and other non-jet-pose glyphs.
    """
    airborne = seconds_airborne or {}
    struck = just_struck or set()
    alive_ids = {frame.agent_id for frame in tick.jets}
    for aid in list(handles.jets):
        if aid not in alive_ids:
            if aid in handles.pending_jet_removal:
                continue
            anchor = handles.jets[aid]
            if on_jet_removed is not None:
                if anchor is not None and not anchor.is_empty():
                    p = anchor.get_pos()
                    last_pos = (float(p.x), float(p.y), float(p.z))
                else:
                    xy = handles.last_jet_xy.get(aid, (0.0, 0.0))
                    z = handles.rendered_altitude.get(
                        aid, handles.desired_altitude.get(aid, GROUND_ALTITUDE)
                    )
                    last_pos = (xy[0], xy[1], z)
                handles.pending_jet_removal.add(aid)
                on_jet_removed(aid, last_pos)
            else:
                # Default: immediate removal (Phase 16 callers / tests).
                anchor.remove_node()
                handles.jets.pop(aid)
                _pop_jet_state(handles, aid)

    for frame in tick.jets:
        aid = frame.agent_id
        anchor = handles.jets.get(aid)
        if anchor is None or anchor.is_empty():
            continue
        x, y = frame.position
        if apply_jet_pose:
            z_now = handles.rendered_altitude.get(aid, GROUND_ALTITUDE)
            anchor.set_pos(x, y, z_now)
            _update_altitude_cue(handles, aid, z_now)
            anchor.show()
            handles.jet_heading_deg[aid] = float(frame.heading)
            handles.last_jet_xy[aid] = (x, y)
        if aid not in handles.first_seen_tick:
            handles.first_seen_tick[aid] = tick.tick
        auth_z = getattr(frame, "altitude", None)
        target_z = desired_altitude(
            airborne.get(aid, 0.0),
            frame.position,
            header,
            tick,
            just_struck=aid in struck,
            authoritative_altitude=auth_z,
        )
        prev_z = handles.previous_desired_altitude.get(aid)
        handles.desired_altitude[aid] = target_z
        handles.desired_bank_deg[aid] = desired_bank_deg(prev_z, target_z, tick_dt)
        handles.previous_desired_altitude[aid] = target_z

    for frame in tick.targets:
        anchor = handles.targets.get(frame.target_id)
        if anchor is None or anchor.is_empty():
            continue
        if frame.hp_remaining <= 0:
            # Priority 1 so the glyph's own keyline/halo colors give way and
            # the whole marker really does go dim grey, not just its fill.
            anchor.set_color(*DESTROYED_COLOR, 1)
            anchor.set_render_mode_wireframe()
            ring = anchor.find("**/sam-ring-*")
            if not ring.is_empty():
                ring.hide()
            rotor = anchor.find("**/radar-rotor")
            if not rotor.is_empty():
                rotor.set_tag("radar-spin", "0")
        else:
            ttype = header.target_types[frame.target_id]
            anchor.clear_render_mode()
            anchor.clear_color()
            glyph = anchor.find("**/glyph-*")
            _paint_glyph(glyph, TARGET_COLORS.get(ttype, ENEMY))
            rotor = anchor.find("**/radar-rotor")
            if not rotor.is_empty():
                rotor.set_tag("radar-spin", "1")

    _sync_aaa_sprites(handles, tick)
    _update_missile_glyphs(handles, tick)
    _update_carrier_glyphs(handles, tick)
    _update_corvette_glyphs(handles, tick)
    _update_tank_glyphs(handles, tick)


def _carrier_deck_points() -> list[tuple[float, float]]:
    """Simple elongated rectangle glyph for a carrier deck (x, z local)."""
    return [(-0.4, -1.2), (0.4, -1.2), (0.4, 1.2), (-0.4, 1.2)]


def _corvette_hull_points() -> list[tuple[float, float]]:
    """Compact diamond/hull glyph for a corvette."""
    return [(0.0, 0.9), (0.35, 0.0), (0.0, -0.7), (-0.35, 0.0)]


def _update_carrier_glyphs(handles: SceneHandles, tick: TraceTick) -> None:
    """Sync Phase 40 carrier frames: deck glyph + recovery ring."""
    frames = list(getattr(tick, "carriers", None) or [])
    alive = {c.unit_id for c in frames}
    for cid in list(handles.carriers):
        if cid not in alive:
            node = handles.carriers.pop(cid)
            if node is not None and not node.is_empty():
                node.remove_node()
    for c in frames:
        anchor = handles.carriers.get(c.unit_id)
        if anchor is None or anchor.is_empty():
            anchor = handles.root.attach_new_node(f"carrier-{c.unit_id}")
            frame_type = str(getattr(c, "frame_type", None) or "ESCORT_CARRIER")
            glyph = attach_ship_mesh(
                anchor, f"glyph-{c.unit_id}", frame_type, CARRIER_COLOR
            )
            if glyph is None:
                _attach_unit(
                    anchor,
                    f"glyph-{c.unit_id}",
                    CARRIER_COLOR,
                    sprite_key=frame_type,
                    points=_carrier_deck_points(),
                    pin=False,
                )
            from wraithnet import config as wn_config

            _ground_disc(
                anchor,
                f"recovery-ring-{c.unit_id}",
                0.0,
                0.0,
                float(wn_config.RECOVERY_APPROACH_RADIUS),
                CARRIER_RECOVERY_RING,
            )
            handles.carriers[c.unit_id] = anchor
        x, y = float(c.position[0]), float(c.position[1])
        anchor.set_pos(x, y, 0.18)
        # Panda H matches sim heading after negation (sim CW from +y; Panda CCW).
        anchor.set_h(-float(c.heading))
        glyph = anchor.find(f"**/glyph-{c.unit_id}")
        if c.destroyed or c.hp <= 0:
            if not glyph.is_empty():
                glyph.set_color(*DESTROYED_COLOR)
            ring = anchor.find("**/recovery-ring-*")
            if not ring.is_empty():
                ring.hide()
        else:
            _paint_glyph(glyph, CARRIER_COLOR)
            ring = anchor.find("**/recovery-ring-*")
            if not ring.is_empty():
                ring.show()


def _update_corvette_glyphs(handles: SceneHandles, tick: TraceTick) -> None:
    """Sync Phase 41 corvette frames: hull glyph + SAM/CIWS threat rings."""
    frames = list(getattr(tick, "corvettes", None) or [])
    alive = {c.unit_id for c in frames}
    for cid in list(handles.corvettes):
        if cid not in alive:
            node = handles.corvettes.pop(cid)
            if node is not None and not node.is_empty():
                node.remove_node()
    for c in frames:
        anchor = handles.corvettes.get(c.unit_id)
        if anchor is None or anchor.is_empty():
            anchor = handles.root.attach_new_node(f"corvette-{c.unit_id}")
            frame_type = str(getattr(c, "frame_type", None) or "CORVETTE_STANDARD")
            glyph = attach_ship_mesh(
                anchor, f"glyph-{c.unit_id}", frame_type, CORVETTE_COLOR
            )
            if glyph is None:
                _attach_unit(
                    anchor,
                    f"glyph-{c.unit_id}",
                    CORVETTE_COLOR,
                    sprite_key=frame_type,
                    points=_corvette_hull_points(),
                    pin=False,
                )
            _ground_disc(
                anchor,
                f"sam-ring-{c.unit_id}",
                0.0,
                0.0,
                float(getattr(c, "sam_range", 15.0) or 15.0),
                CORVETTE_SAM_RING,
            )
            _ground_disc(
                anchor,
                f"ciws-ring-{c.unit_id}",
                0.0,
                0.0,
                float(getattr(c, "ciws_range", 4.0) or 4.0),
                CORVETTE_CIWS_RING,
            )
            handles.corvettes[c.unit_id] = anchor
        x, y = float(c.position[0]), float(c.position[1])
        anchor.set_pos(x, y, 0.16)
        anchor.set_h(-float(c.heading))
        glyph = anchor.find(f"**/glyph-{c.unit_id}")
        destroyed = bool(c.destroyed or c.hp <= 0)
        if destroyed:
            if not glyph.is_empty():
                glyph.set_color(*DESTROYED_COLOR)
            for pattern in ("**/sam-ring-*", "**/ciws-ring-*"):
                ring = anchor.find(pattern)
                if not ring.is_empty():
                    ring.hide()
        else:
            _paint_glyph(glyph, CORVETTE_COLOR)
            for pattern in ("**/sam-ring-*", "**/ciws-ring-*"):
                ring = anchor.find(pattern)
                if not ring.is_empty():
                    ring.show()


def _tank_hull_points() -> list[tuple[float, float]]:
    """Compact trapezoid hull for tanks."""
    return [(-0.55, -0.35), (0.55, -0.35), (0.4, 0.4), (-0.4, 0.4)]


def _update_tank_glyphs(handles: SceneHandles, tick: TraceTick) -> None:
    """Sync Phase 43 tank frames with faction colors."""
    frames = list(getattr(tick, "tanks", None) or [])
    alive = {t.unit_id for t in frames}
    for tid in list(handles.tanks):
        if tid not in alive:
            node = handles.tanks.pop(tid)
            if node is not None and not node.is_empty():
                node.remove_node()
    for t in frames:
        color = (
            TANK_FRIENDLY_COLOR
            if str(getattr(t, "faction", "")) == "FRIENDLY"
            else TANK_ENEMY_COLOR
        )
        anchor = handles.tanks.get(t.unit_id)
        if anchor is None or anchor.is_empty():
            anchor = handles.root.attach_new_node(f"tank-{t.unit_id}")
            _attach_ground(
                anchor,
                f"glyph-{t.unit_id}",
                str(getattr(t, "frame_type", "") or "MBT_STANDARD"),
                color,
                points=_tank_hull_points(),
                pin=False,
            )
            handles.tanks[t.unit_id] = anchor
        x, y = float(t.position[0]), float(t.position[1])
        anchor.set_pos(x, y, 0.14)
        anchor.set_h(-float(t.heading))
        glyph = anchor.find(f"**/glyph-{t.unit_id}")
        destroyed = bool(t.destroyed or t.hp <= 0)
        if destroyed:
            if not glyph.is_empty():
                glyph.set_color(*DESTROYED_COLOR)
        else:
            _paint_glyph(glyph, color)


def _sync_aaa_sprites(handles: SceneHandles, tick: TraceTick) -> None:
    """Swap the AAA mesh when the tick names AAA_TURRETED vs AAA_LIGHT."""
    for frame in list(getattr(tick, "aaa_sites", None) or []):
        key = str(getattr(frame, "frame_type", "") or "AAA_LIGHT")
        anchor = handles.targets.get(frame.target_id)
        if anchor is None or anchor.is_empty():
            continue
        glyph = anchor.find(f"**/glyph-{frame.target_id}")
        if not glyph.is_empty() and glyph.get_tag("ground-mesh") == key:
            anchor.set_h(-float(getattr(frame, "heading", 0.0) or 0.0))
            continue
        if not glyph.is_empty():
            glyph.remove_node()
        color = faction_paint(str(getattr(frame, "faction", "") or "ENEMY"))
        attach_ground_mesh(anchor, f"glyph-{frame.target_id}", key, color)
        anchor.set_h(-float(getattr(frame, "heading", 0.0) or 0.0))


def _update_missile_glyphs(handles: SceneHandles, tick: TraceTick) -> None:
    """Sync authoritative MissileFrame positions; remove stale glyphs."""
    frames = list(getattr(tick, "missiles", None) or [])
    alive = {m.missile_id for m in frames}
    for mid in list(handles.missiles):
        if mid not in alive:
            node = handles.missiles.pop(mid)
            if node is not None and not node.is_empty():
                node.remove_node()
    parent = _fx_parent(handles)
    for m in frames:
        pos = m.position
        node = handles.missiles.get(m.missile_id)
        if node is None or node.is_empty():
            node = parent.attach_new_node(f"missile-{m.missile_id}")
            glyph = _attach_glyph(
                node, f"missile-glyph-{m.missile_id}", _diamond_pts(), MISSILE_COLOR
            )
            glyph.set_scale(GLYPH_SCALE * 0.55)
            handles.missiles[m.missile_id] = node
        node.set_pos(float(pos[0]), float(pos[1]), float(pos[2]))
        node.show()


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
    animate_water(handles, dt)
    animate_radars(handles, dt)
    animate_jammers(handles, dt)


def clear_highlight_fx(handles: SceneHandles) -> None:
    """Drop all pending highlight cosmetics (Phase 49 snap / catch-up hygiene)."""
    for item in handles.highlight_fx:
        if item.restore is not None:
            try:
                item.restore()
            except Exception:
                pass
        if item.node is not None and not item.node.is_empty():
            item.node.remove_node()
    handles.highlight_fx.clear()



def animate_water(handles: SceneHandles, dt: float) -> None:
    """Cosmetic water shimmer — never mutates terrain data."""
    if handles.terrain_root is None or handles.terrain_root.is_empty():
        return
    water = handles.terrain_root.find("**/terrain-water")
    if water.is_empty():
        return
    handles.water_anim_phase = (handles.water_anim_phase + dt * 1.2) % (2.0 * math.pi)
    # Subtle alpha pulse; Z stays at surface zero so entities remain consistent.
    pulse = 0.78 + 0.08 * math.sin(handles.water_anim_phase)
    water.set_color_scale(1.0, 1.0, 1.0, pulse)


def animate_radars(handles: SceneHandles, dt: float) -> None:
    """Spin mobile-radar AESA heads around the mast axis (presentation clock)."""
    if handles.root is None or handles.root.is_empty() or dt <= 0.0:
        return
    step = float(handles.radar_spin_deg_per_sec) * float(dt)
    for rotor in handles.root.find_all_matches("**/radar-rotor"):
        if rotor.is_empty() or rotor.get_tag("radar-spin") != "1":
            continue
        rotor.set_h(rotor.get_h() + step)


def animate_jammers(handles: SceneHandles, dt: float) -> None:
    """Spin each jammer dipole ring at its own rate (presentation clock)."""
    if handles.root is None or handles.root.is_empty() or dt <= 0.0:
        return
    for ring in handles.root.find_all_matches("**/jammer-ring-*"):
        if ring.is_empty() or ring.get_tag("jammer-spin") != "1":
            continue
        rate = ring.get_python_tag("spin_deg_per_sec")
        if rate is None:
            rate = 45.0
        ring.set_h(ring.get_h() + float(rate) * float(dt))


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
        if not uses_instant_strike_beam(strike.weapon_type):
            continue
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
