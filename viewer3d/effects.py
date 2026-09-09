"""Panda3D-backed weapon/destruction visual effects for viewer3d. Every
spawn_* function here builds a small node under `root`, attaches a
self-driving direct.interval Sequence (move/fade/scale over a fixed,
purely cosmetic duration), and cleans the node up itself via a trailing
Func callback -- no manual per-frame bookkeeping needed beyond what
Panda3D's own interval manager already provides. See
docs/12_Flight_Dynamics_and_Weapon_Effects_Design_Document.md §5.
"""

from __future__ import annotations

import math

from direct.interval.IntervalGlobal import (
    Func,
    LerpColorScaleInterval,
    LerpFunc,
    LerpHprInterval,
    LerpPosInterval,
    LerpScaleInterval,
    Parallel,
    Sequence,
    Wait,
)
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

# weapon_type -> ("gun_tracer" | "missile" | "bomb", duration_seconds).
# Unrecognized weapon_type values fall back to ("missile", 0.65) -- see
# design doc §5.2's table. Keep this a flat, easily-extended dict, not a
# chain of if/elif branches, so a future new WeaponType needs at most a
# one-line addition here.
WEAPON_EFFECT_STYLES: dict[str, tuple[str, float]] = {
    "JDAM": ("bomb", 1.1),
    "AIM9X": ("missile", 0.65),
    "AMRAAM": ("missile", 0.7),
    "AGM65": ("missile", 0.65),
    "AGM88": ("missile", 0.7),
    "AGM84": ("missile", 0.75),
    "SAM_IR": ("missile", 0.6),
    "SAM_RADAR": ("missile", 0.6),
    "GUN_POD": ("gun_tracer", 0.12),
    "CIWS": ("gun_tracer", 0.1),
    "DECK_GUN": ("gun_tracer", 0.15),
    "AAA": ("gun_tracer", 0.1),
    "AAA_TURRETED": ("gun_tracer", 0.1),
    "TANK_CANNON": ("gun_tracer", 0.14),
}
DEFAULT_EFFECT_STYLE: tuple[str, float] = ("missile", 0.65)
GROUND_Z = 0.0
_TRAIL_HISTORY = 8
_TRAIL_SPAN = 0.22

# Weapons that use authoritative MissileFrame tracks (skip cosmetic travel).
GUIDED_WEAPON_TYPES: frozenset[str] = frozenset(
    {"AIM9X", "AMRAAM", "AGM65", "AGM88", "AGM84", "SAM_IR", "SAM_RADAR"}
)


def weapon_effect_style(weapon_type: str) -> tuple[str, float]:
    return WEAPON_EFFECT_STYLES.get(weapon_type, DEFAULT_EFFECT_STYLE)


def uses_instant_strike_beam(weapon_type: str) -> bool:
    """True for gun/tracer weapons; bombs and missiles use travel effects."""
    return weapon_effect_style(weapon_type)[0] == "gun_tracer"


def _lerp3(
    a: tuple[float, float, float], b: tuple[float, float, float], t: float
) -> tuple[float, float, float]:
    return (
        a[0] + (b[0] - a[0]) * t,
        a[1] + (b[1] - a[1]) * t,
        a[2] + (b[2] - a[2]) * t,
    )


def bomb_arc_waypoints(
    launch: tuple[float, float, float],
    impact: tuple[float, float, float],
) -> tuple[tuple[float, float, float], tuple[float, float, float]]:
    """Two interior points of a monotonic downward curve that never goes
    below the impact/ground altitude."""
    floor_z = max(impact[2], GROUND_Z)

    def _z_at(t: float) -> float:
        # t^2 ease-in: stays near launch altitude early, then drops.
        z = launch[2] + (impact[2] - launch[2]) * (t * t)
        return max(z, floor_z)

    mid1 = _lerp3(launch, impact, 1.0 / 3.0)
    mid2 = _lerp3(launch, impact, 2.0 / 3.0)
    return (mid1[0], mid1[1], _z_at(1.0 / 3.0)), (mid2[0], mid2[1], _z_at(2.0 / 3.0))


def bomb_path_samples(
    launch: tuple[float, float, float],
    impact: tuple[float, float, float],
    steps_per_segment: int = 8,
) -> list[tuple[float, float, float]]:
    """Sample the three-segment bomb lerp, excluding the impact point."""
    mid1, mid2 = bomb_arc_waypoints(launch, impact)
    points = [launch, mid1, mid2, impact]
    samples: list[tuple[float, float, float]] = []
    for i in range(len(points) - 1):
        a, b = points[i], points[i + 1]
        # Skip the final sample of the last segment (the impact itself).
        last = steps_per_segment if i < len(points) - 2 else steps_per_segment
        for s in range(last):
            if i == len(points) - 2 and s == last - 1:
                continue
            t = s / steps_per_segment
            samples.append(_lerp3(a, b, t))
    return samples

_MISSILE_COLOR = (1.0, 0.85, 0.25, 1.0)
_BOMB_COLOR = (0.85, 0.75, 0.45, 1.0)
_TRACER_COLOR = (1.0, 0.95, 0.45, 1.0)
_EXPLOSION_COLOR = (1.0, 0.55, 0.15, 1.0)
_WRECK_COLOR = (0.55, 0.45, 0.35, 1.0)


def _disc_geom(name: str, radius: float, color, segments: int = 16) -> GeomNode:
    fmt = GeomVertexFormat.get_v3c4()
    vdata = GeomVertexData(name, fmt, Geom.UH_static)
    vertex = GeomVertexWriter(vdata, "vertex")
    col = GeomVertexWriter(vdata, "color")
    r, g, b, a = color
    vertex.add_data3(0.0, 0.0, 0.0)
    col.add_data4(r, g, b, a)
    for i in range(segments + 1):
        ang = 2 * math.pi * i / segments
        vertex.add_data3(radius * math.cos(ang), 0.0, radius * math.sin(ang))
        col.add_data4(r, g, b, a)
    prim = GeomTriangles(Geom.UH_static)
    for i in range(1, segments + 1):
        prim.add_vertices(0, i, i + 1)
    prim.close_primitive()
    geom = Geom(vdata)
    geom.add_primitive(prim)
    node = GeomNode(name)
    node.add_geom(geom)
    return node


def _attach_billboard_disc(
    parent: NodePath, name: str, radius: float, color
) -> NodePath:
    """Simplified billboard glyph (inline equivalent of scene._attach_glyph)."""
    np = parent.attach_new_node(_disc_geom(name, radius, color))
    np.set_billboard_point_eye()
    np.set_light_off()
    np.set_two_sided(True)
    np.set_transparency(TransparencyAttrib.MAlpha)
    return np


def _safe_remove(node: NodePath) -> None:
    if node is not None and not node.is_empty():
        node.remove_node()


def spawn_destruction_explosion(
    root: NodePath, position: tuple[float, float, float], scale: float = 1.0
) -> None:
    """A brief expanding-then-fading flash at `position`."""
    flash = root.attach_new_node("fx-explosion")
    flash.set_pos(position[0], position[1], position[2])
    disc = _attach_billboard_disc(flash, "explosion-disc", 0.9 * scale, _EXPLOSION_COLOR)
    disc.set_scale(0.35)
    duration = 0.5
    Sequence(
        Parallel(
            LerpScaleInterval(disc, duration, 2.4 * scale, startScale=0.35),
            LerpColorScaleInterval(disc, duration, (1.0, 1.0, 1.0, 0.0)),
        ),
        Func(_safe_remove, flash),
    ).start()


def _spawn_gun_tracer(
    root: NodePath,
    launch: tuple[float, float, float],
    impact: tuple[float, float, float],
    duration: float,
) -> None:
    segs = LineSegs("gun-tracer")
    segs.set_thickness(2.5)
    segs.set_color(*_TRACER_COLOR)
    segs.move_to(launch[0], launch[1], launch[2])
    segs.draw_to(impact[0], impact[1], impact[2])
    node = root.attach_new_node(segs.create())
    node.set_light_off()
    node.set_transparency(TransparencyAttrib.MAlpha)
    Sequence(
        LerpColorScaleInterval(node, duration, (1.0, 1.0, 1.0, 0.0)),
        Func(_safe_remove, node),
    ).start()


def _rebuild_line(
    parent: NodePath, name: str, points: list[tuple[float, float, float]], color
) -> None:
    for child in parent.get_children():
        child.remove_node()
    if len(points) < 2:
        return
    segs = LineSegs(name)
    segs.set_thickness(2.0)
    segs.set_color(*color)
    segs.move_to(points[0][0], points[0][1], points[0][2])
    for px, py, pz in points[1:]:
        segs.draw_to(px, py, pz)
    node = parent.attach_new_node(segs.create())
    node.set_light_off()
    node.set_transparency(TransparencyAttrib.MAlpha)


def _spawn_missile(
    root: NodePath,
    launch: tuple[float, float, float],
    impact: tuple[float, float, float],
    duration: float,
    *,
    impact_scale: float = 0.55,
) -> None:
    holder = root.attach_new_node("fx-missile")
    glyph = holder.attach_new_node("missile-glyph")
    glyph.set_pos(launch[0], launch[1], launch[2])
    _attach_billboard_disc(glyph, "missile-disc", 0.35, _MISSILE_COLOR)
    trail = holder.attach_new_node("missile-trail")
    history: list[tuple[float, float, float]] = [launch]

    def _update_flight(t: float) -> None:
        pos = _lerp3(launch, impact, t)
        glyph.set_pos(pos[0], pos[1], pos[2])
        history.append(pos)
        if len(history) > _TRAIL_HISTORY:
            del history[: len(history) - _TRAIL_HISTORY]
        t0 = max(0.0, t - _TRAIL_SPAN)
        tail = _lerp3(launch, impact, t0)
        points = [tail, pos] if t > 0.0 else [launch, launch]
        _rebuild_line(trail, "missile-trail-seg", points, _MISSILE_COLOR)

    def _impact_and_cleanup() -> None:
        spawn_destruction_explosion(root, impact, scale=impact_scale)
        _safe_remove(holder)

    Sequence(
        LerpFunc(_update_flight, duration, fromData=0.0, toData=1.0, blendType="easeIn"),
        Func(_impact_and_cleanup),
    ).start()


def _spawn_bomb(
    root: NodePath,
    launch: tuple[float, float, float],
    impact: tuple[float, float, float],
    duration: float,
) -> None:
    node = root.attach_new_node("fx-bomb")
    node.set_pos(launch[0], launch[1], launch[2])
    _attach_billboard_disc(node, "bomb-glyph", 0.4, _BOMB_COLOR)
    mid1, mid2 = bomb_arc_waypoints(launch, impact)
    seg = duration / 3.0

    def _impact_and_cleanup() -> None:
        spawn_destruction_explosion(root, impact, scale=1.0)
        _safe_remove(node)

    Sequence(
        LerpPosInterval(node, seg, mid1, startPos=launch, blendType="easeOut"),
        LerpPosInterval(node, seg, mid2, blendType="noBlend"),
        LerpPosInterval(node, seg, impact, blendType="easeIn"),
        Func(_impact_and_cleanup),
    ).start()


def spawn_strike_effect(
    root: NodePath,
    weapon_type: str,
    launch_position: tuple[float, float, float],
    impact_position: tuple[float, float, float],
) -> None:
    """Looks up WEAPON_EFFECT_STYLES and builds the corresponding archetype."""
    style, duration = weapon_effect_style(weapon_type)
    if style == "gun_tracer":
        _spawn_gun_tracer(root, launch_position, impact_position, duration)
    elif style == "bomb":
        _spawn_bomb(root, launch_position, impact_position, duration)
    else:
        _spawn_missile(root, launch_position, impact_position, duration)


_FLARE_COLOR = (1.0, 0.85, 0.2, 0.95)


def spawn_flare_burst(
    root: NodePath,
    position: tuple[float, float, float],
    *,
    duration: float = 0.55,
) -> None:
    """Cosmetic IR flare particles — does not imply decoy success."""
    holder = root.attach_new_node("fx-flare-burst")
    holder.set_pos(position[0], position[1], position[2])
    for i, offset in enumerate(
        ((0.4, 0.0, 0.2), (-0.3, 0.3, 0.1), (0.1, -0.4, 0.25), (-0.2, -0.1, 0.35))
    ):
        mote = holder.attach_new_node(f"flare-mote-{i}")
        start = offset
        mote.set_pos(*start)
        glyph = _attach_billboard_disc(mote, f"flare-disc-{i}", 0.22, _FLARE_COLOR)
        end = (offset[0] * 2.5, offset[1] * 2.5, offset[2] + 1.2)
        Sequence(
            Parallel(
                LerpPosInterval(mote, duration, end, startPos=start, blendType="easeOut"),
                LerpColorScaleInterval(glyph, duration, (1.0, 1.0, 1.0, 0.0)),
            ),
        ).start()
    Sequence(Wait(duration + 0.05), Func(_safe_remove, holder)).start()


def spawn_jet_wreck(
    root: NodePath,
    position: tuple[float, float, float],
    on_complete,
) -> None:
    """Falling wreck at the jet's last-known position, then explosion + callback."""
    wreck = root.attach_new_node("fx-jet-wreck")
    wreck.set_pos(position[0], position[1], position[2])
    glyph = _attach_billboard_disc(wreck, "wreck-glyph", 0.55, _WRECK_COLOR)
    ground = (position[0], position[1], 0.0)
    duration = 0.9

    def _finish() -> None:
        spawn_destruction_explosion(root, ground, scale=1.2)
        _safe_remove(wreck)
        if on_complete is not None:
            on_complete()

    Sequence(
        Parallel(
            LerpPosInterval(wreck, duration, ground, startPos=position, blendType="easeIn"),
            LerpColorScaleInterval(glyph, duration, (1.0, 1.0, 1.0, 0.25)),
            LerpHprInterval(glyph, duration, (180.0, 90.0, 45.0)),
        ),
        Func(_finish),
    ).start()
