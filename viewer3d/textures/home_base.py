"""Friendly forward airbase: apron, runway, Quonset hangar, control tower."""

from __future__ import annotations

import math

from panda3d.core import NodePath

from viewer3d.textures.geom import (
    Color,
    MeshBuilder,
    add_box,
    add_box_shattered,
    add_loft,
    add_shattered_grid,
    add_slab,
    attach_mesh_group,
    flat_fn,
    paint_fn,
    planform_outline,
)

# Apron footprint (outline).
_PAD = [(-0.95, -0.95), (0.95, -0.95), (0.95, 0.95), (-0.95, 0.95)]

CONCRETE = (0.50, 0.52, 0.48, 1.0)
CONCRETE_DARK = (0.30, 0.32, 0.30, 1.0)
ASPHALT = (0.22, 0.24, 0.26, 1.0)
HANGAR = (0.34, 0.42, 0.36, 1.0)
TEAL = (0.28, 0.72, 0.50, 1.0)
CYAN = (0.35, 0.88, 0.95, 0.95)
DOOR = (0.16, 0.22, 0.18, 1.0)
LIT = (0.92, 0.94, 0.90, 1.0)
TOWER = (0.36, 0.44, 0.40, 1.0)
RUBBER = (0.12, 0.12, 0.10, 1.0)
WOOD = (0.42, 0.32, 0.18, 1.0)
PAD_SHARDS = (
    (0.46, 0.48, 0.44, 1.0),
    (0.38, 0.40, 0.36, 1.0),
    (0.54, 0.56, 0.52, 1.0),
    (0.32, 0.34, 0.32, 1.0),
)
HANGAR_SHARDS = (
    (0.32, 0.40, 0.34, 1.0),
    (0.24, 0.32, 0.26, 1.0),
    TEAL,
    (0.55, 0.88, 0.78, 0.92),
    DOOR,
)
TOWER_SHARDS = (
    (0.34, 0.42, 0.38, 1.0),
    (0.55, 0.88, 0.78, 0.92),
    (0.08, 0.14, 0.12, 1.0),
    TEAL,
)


def _quonset_ring(y: float, radius: float, z0: float, segs: int = 10):
    """Half-cylinder cross-section (arch above z0), CCW looking +Y."""
    pts = []
    for i in range(segs + 1):
        ang = math.pi * i / segs  # 0 = +X, pi = -X over the top
        pts.append((radius * math.cos(ang), y, z0 + radius * math.sin(ang)))
    return pts


def attach_home_base(parent: NodePath, name: str, color: Color) -> NodePath:
    mesh = MeshBuilder()
    pad = paint_fn(CONCRETE_DARK)
    asphalt = paint_fn(ASPHALT)
    hangar = paint_fn(HANGAR)
    tower = paint_fn(TOWER)
    dark = paint_fn(RUBBER)
    teal = paint_fn(TEAL)
    cyan = flat_fn(CYAN)
    wood = paint_fn(WOOD)
    lit = paint_fn(LIT)

    # Apron slab.
    add_box(mesh, -0.92, 0.92, -0.92, 0.92, 0.0, 0.08, pad)
    add_shattered_grid(
        mesh,
        [(-0.88, -0.88, 0.09), (0.88, -0.88, 0.09), (0.88, 0.88, 0.09), (-0.88, 0.88, 0.09)],
        6,
        6,
        PAD_SHARDS,
        fill=0.88,
        proud=0.010,
        jitter=0.18,
        skip=0.05,
        seed=2,
    )

    # Runway strip (along +Y), left of the hangar.
    rx0, rx1 = -0.78, -0.18
    ry0, ry1 = -0.78, 0.78
    add_box(mesh, rx0, rx1, ry0, ry1, 0.08, 0.12, asphalt)
    # Glowing edge rails.
    for x in (rx0 + 0.02, rx1 - 0.02):
        add_slab(
            mesh,
            [(x - 0.015, ry0 + 0.04, 0.125), (x + 0.015, ry0 + 0.04, 0.125),
             (x + 0.015, ry1 - 0.04, 0.125), (x - 0.015, ry1 - 0.04, 0.125)],
            (0.0, 0.0, 0.008),
            cyan,
        )
    # Dashed centerline.
    cx = 0.5 * (rx0 + rx1)
    for i in range(7):
        y = ry0 + 0.12 + i * 0.20
        add_slab(
            mesh,
            [(cx - 0.012, y, 0.125), (cx + 0.012, y, 0.125),
             (cx + 0.012, y + 0.10, 0.125), (cx - 0.012, y + 0.10, 0.125)],
            (0.0, 0.0, 0.008),
            cyan,
        )
    # Threshold bars (south end).
    for i in range(6):
        x = rx0 + 0.08 + i * 0.09
        add_slab(
            mesh,
            [(x, ry0 + 0.06, 0.125), (x + 0.045, ry0 + 0.06, 0.125),
             (x + 0.045, ry0 + 0.22, 0.125), (x, ry0 + 0.22, 0.125)],
            (0.0, 0.0, 0.008),
            cyan,
        )
    # Approach chevrons (north end).
    for i, y in enumerate((0.42, 0.54, 0.66)):
        w = 0.10 + i * 0.02
        add_slab(
            mesh,
            [(cx - w, y, 0.125), (cx, y + 0.08, 0.125),
             (cx + w, y, 0.125), (cx, y - 0.02, 0.125)],
            (0.0, 0.0, 0.008),
            cyan,
        )

    # Quonset hangar (right side).
    hx0, hx1 = 0.08, 0.78
    hy0, hy1 = -0.55, 0.35
    hcx = 0.5 * (hx0 + hx1)
    hr = 0.5 * (hx1 - hx0)
    hz0 = 0.10
    add_box(mesh, hx0, hx1, hy0, hy1, hz0, hz0 + 0.08, hangar)
    add_loft(
        mesh,
        [_quonset_ring(hy0, hr * 0.98, hz0), _quonset_ring(hy1, hr * 0.98, hz0)],
        paint_fn(HANGAR),
        cap=False,
    )
    # Closed rear; open front reads as the bay behind the sliding doors.
    add_box(mesh, hx0 + 0.02, hx1 - 0.02, hy0 - 0.02, hy0 + 0.04, hz0, hz0 + hr * 0.95, hangar)
    add_box(mesh, hx0 + 0.06, hx1 - 0.06, hy1 - 0.02, hy1 + 0.02, hz0, 0.55, lit)
    add_box(mesh, hx0 + 0.04, hcx - 0.04, hy1 - 0.04, hy1 + 0.06, hz0, 0.58, paint_fn(DOOR))
    add_box(mesh, hcx + 0.04, hx1 - 0.04, hy1 - 0.04, hy1 + 0.06, hz0, 0.58, paint_fn(DOOR))
    add_box_shattered(
        mesh,
        hx0 + 0.02,
        hx1 - 0.02,
        hy0 + 0.05,
        hy1 - 0.08,
        hz0 + 0.10,
        hz0 + 0.55,
        HANGAR_SHARDS,
        cols=3,
        rows=2,
        include_top=False,
        jitter=0.28,
        skip=0.10,
        seed=11,
    )

    # Control tower behind hangar.
    add_box(mesh, 0.28, 0.62, 0.42, 0.72, 0.10, 0.95, tower)
    add_box_shattered(
        mesh,
        0.28,
        0.62,
        0.42,
        0.72,
        0.22,
        0.92,
        TOWER_SHARDS,
        cols=2,
        rows=3,
        include_top=True,
        jitter=0.26,
        skip=0.08,
        seed=18,
    )
    add_box(mesh, 0.24, 0.66, 0.38, 0.76, 0.95, 1.18, dark)
    add_box(mesh, 0.28, 0.62, 0.42, 0.72, 1.00, 1.14, paint_fn((0.45, 0.82, 0.72, 0.90)))
    # Flag + windsock.
    add_box(mesh, 0.40, 0.44, 0.70, 0.74, 1.18, 1.42, dark)
    add_box(mesh, 0.44, 0.62, 0.68, 0.76, 1.30, 1.40, teal)
    add_box(mesh, 0.22, 0.26, 0.55, 0.59, 0.85, 1.05, dark)
    add_box(mesh, 0.10, 0.22, 0.52, 0.62, 0.98, 1.04, teal)

    # Fuel tanks + crates.
    for cy in (0.55, 0.72):
        add_loft(
            mesh,
            [
                _tank_ring(-0.55, cy, 0.22, 0.10),
                _tank_ring(-0.22, cy, 0.22, 0.10),
            ],
            paint_fn(HANGAR),
            cap=True,
        )
    add_box(mesh, 0.70, 0.88, 0.48, 0.66, 0.10, 0.28, wood)
    add_box(mesh, 0.72, 0.86, 0.68, 0.82, 0.10, 0.24, wood)
    add_box(mesh, -0.90, -0.78, 0.55, 0.70, 0.10, 0.26, dark)

    outline = planform_outline("sym-edge", _PAD, lambda *_: 0.09)
    return attach_mesh_group(
        parent,
        name,
        mesh.to_geom("sym-fill"),
        color,
        scale=2.05,
        outline=outline,
        vertex_colors=True,
    )


def _tank_ring(x: float, y: float, z: float, r: float, segs: int = 8):
    return [
        (x, y + r * math.cos(2 * math.pi * i / segs), z + r * math.sin(2 * math.pi * i / segs))
        for i in range(segs)
    ]
