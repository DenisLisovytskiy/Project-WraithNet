"""Truck-mounted mobile search radar: olive chassis and a shattered amber AESA."""

from __future__ import annotations

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
    box_ring,
    paint_fn,
    planform_outline,
    steel_fn,
)

_DIAMOND = [(0.0, 0.88), (0.68, 0.10), (0.0, -0.78), (-0.68, 0.10)]

HULL = (0.52, 0.56, 0.32, 1.0)
HULL_DARK = (0.24, 0.22, 0.14, 1.0)
CAB = (0.44, 0.46, 0.28, 1.0)
GLASS = (
    (0.85, 0.70, 0.32, 0.92),
    (0.32, 0.28, 0.16, 1.0),
    (0.68, 0.52, 0.22, 0.90),
)
AESA_FRAME = (0.32, 0.30, 0.20, 1.0)
AESA_SHARDS = (
    (1.00, 0.84, 0.28, 0.96),
    (1.00, 0.94, 0.55, 0.96),
    (0.82, 0.64, 0.22, 0.95),
    (0.95, 0.72, 0.20, 0.95),
    (0.62, 0.54, 0.26, 1.0),
    (0.90, 0.90, 0.48, 0.92),
)
HULL_SHARDS = (
    (0.58, 0.62, 0.34, 1.0),
    (0.44, 0.46, 0.24, 1.0),
    (0.64, 0.60, 0.36, 1.0),
    (0.38, 0.40, 0.22, 1.0),
)


def attach_mobile_radar(parent: NodePath, name: str, color: Color) -> NodePath:
    mesh = MeshBuilder()
    hull = steel_fn(HULL)
    dark = paint_fn(HULL_DARK)
    cab = steel_fn(CAB)
    frame = steel_fn(AESA_FRAME)
    add_loft(
        mesh,
        [
            box_ring(-0.72, 0.30, 0.20, z=0.36),
            box_ring(-0.20, 0.32, 0.22, z=0.38),
            box_ring(0.28, 0.28, 0.20, z=0.40),
            box_ring(0.62, 0.24, 0.16, z=0.42),
        ],
        hull,
        upper_hint=lambda p: p[2] >= 0.32,
    )
    add_box_shattered(
        mesh,
        -0.30,
        0.30,
        -0.68,
        0.08,
        0.22,
        0.58,
        HULL_SHARDS,
        cols=5,
        rows=2,
        include_top=True,
        jitter=0.48,
        skip=0.12,
        seed=11,
    )
    add_box(mesh, -0.26, 0.26, 0.08, 0.66, 0.52, 0.98, cab)
    add_box_shattered(
        mesh,
        -0.26,
        0.26,
        0.08,
        0.66,
        0.58,
        0.94,
        GLASS,
        cols=3,
        rows=2,
        fill=0.78,
        jitter=0.36,
        skip=0.05,
        seed=4,
    )
    add_box(mesh, -0.22, 0.22, 0.48, 0.68, 0.72, 1.02, dark)
    add_box(mesh, -0.34, 0.34, -0.70, 0.58, 0.00, 0.22, dark)
    for y in (-0.55, -0.22, 0.12, 0.44):
        add_box(mesh, -0.38, -0.26, y - 0.11, y + 0.11, 0.00, 0.24, dark)
        add_box(mesh, 0.26, 0.38, y - 0.11, y + 0.11, 0.00, 0.24, dark)
    add_box(mesh, -0.44, -0.28, -0.12, 0.12, 0.04, 0.14, dark)
    add_box(mesh, 0.28, 0.44, -0.12, 0.12, 0.04, 0.14, dark)
    add_box(mesh, -0.20, 0.20, -0.48, -0.08, 0.50, 0.78, dark)
    add_box(mesh, -0.10, 0.10, -0.16, 0.10, 0.50, 1.42, dark)
    add_box(mesh, -0.22, 0.22, -0.14, 0.16, 1.32, 1.58, hull)
    face = [
        (0.00, -0.02, 0.92),
        (0.72, 0.28, 1.64),
        (0.00, 0.56, 2.32),
        (-0.72, 0.28, 1.64),
    ]
    add_slab(mesh, face, (0.0, 0.28, 0.0), frame)
    add_shattered_grid(
        mesh,
        face,
        7,
        9,
        AESA_SHARDS,
        fill=0.92,
        proud=0.045,
        jitter=0.40,
        skip=0.06,
        seed=21,
    )
    add_box(mesh, -0.18, 0.18, -0.08, 0.22, 1.48, 1.72, dark)
    add_box(mesh, -0.06, 0.06, 0.10, 0.42, 1.20, 1.55, dark)
    outline = planform_outline("sym-edge", _DIAMOND, lambda _x, _y: 0.52)
    return attach_mesh_group(
        parent,
        name,
        mesh.to_geom("sym-fill"),
        color,
        scale=1.92,
        outline=outline,
        vertex_colors=True,
    )
