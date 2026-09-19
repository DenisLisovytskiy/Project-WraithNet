"""Friendly forward airbase: concrete pad, hangar, and control tower."""

from __future__ import annotations

from panda3d.core import NodePath

from viewer3d.textures.geom import (
    Color,
    MeshBuilder,
    add_box,
    add_box_shattered,
    add_shattered_grid,
    add_slab,
    attach_mesh_group,
    flat_fn,
    paint_fn,
    planform_outline,
)

_PAD = [(-0.58, -0.90), (0.58, -0.90), (0.58, 0.90), (-0.58, 0.90)]

CONCRETE = (0.46, 0.48, 0.44, 1.0)
CONCRETE_DARK = (0.28, 0.30, 0.28, 1.0)
HANGAR = (0.32, 0.42, 0.38, 1.0)
TEAL = (0.28, 0.72, 0.50, 1.0)
DOOR = (0.18, 0.48, 0.36, 1.0)
TOWER = (0.36, 0.46, 0.44, 1.0)
RUBBER = (0.12, 0.12, 0.10, 1.0)
CHEVRON = (0.82, 0.78, 0.42, 0.95)
GLASS_LIT = (0.55, 0.88, 0.78, 0.92)
GLASS_DARK = (0.08, 0.14, 0.12, 1.0)
PAD_SHARDS = (
    (0.44, 0.46, 0.42, 1.0),
    (0.36, 0.38, 0.34, 1.0),
    (0.52, 0.54, 0.50, 1.0),
    (0.30, 0.32, 0.30, 1.0),
)
HANGAR_SHARDS = (
    (0.30, 0.40, 0.36, 1.0),
    (0.22, 0.32, 0.28, 1.0),
    TEAL,
    GLASS_LIT,
    GLASS_DARK,
    DOOR,
)
TOWER_SHARDS = (
    (0.34, 0.44, 0.42, 1.0),
    GLASS_LIT,
    GLASS_DARK,
    TEAL,
    (0.20, 0.28, 0.26, 1.0),
)


def attach_home_base(parent: NodePath, name: str, color: Color) -> NodePath:
    mesh = MeshBuilder()
    pad = paint_fn(CONCRETE_DARK)
    hangar = paint_fn(HANGAR)
    roof = paint_fn(CONCRETE)
    tower = paint_fn(TOWER)
    dark = paint_fn(RUBBER)
    teal = paint_fn(TEAL)
    add_box(mesh, -0.55, 0.55, -0.85, 0.85, 0.0, 0.10, pad)
    add_shattered_grid(
        mesh,
        [(-0.52, -0.82, 0.11), (0.52, -0.82, 0.11), (0.52, 0.82, 0.11), (-0.52, 0.82, 0.11)],
        5,
        7,
        PAD_SHARDS,
        fill=0.90,
        proud=0.012,
        jitter=0.22,
        skip=0.06,
        seed=2,
    )
    for i, y in enumerate((-0.20, 0.08, 0.36, 0.64)):
        add_slab(
            mesh,
            [(-0.10, y - 0.04, 0.125), (0.10, y - 0.04, 0.125), (0.10, y + 0.04, 0.125), (-0.10, y + 0.04, 0.125)],
            (0.0, 0.0, 0.01),
            flat_fn(CHEVRON if i % 2 == 0 else (0.88, 0.88, 0.82, 0.95)),
        )
    add_box(mesh, -0.46, 0.46, -0.74, 0.22, 0.10, 0.62, hangar)
    add_box_shattered(
        mesh,
        -0.46,
        0.46,
        -0.74,
        0.22,
        0.14,
        0.60,
        HANGAR_SHARDS,
        cols=4,
        rows=2,
        include_top=False,
        jitter=0.32,
        skip=0.08,
        seed=11,
    )
    add_box(mesh, -0.28, 0.28, 0.18, 0.24, 0.14, 0.52, paint_fn(DOOR))
    add_slab(
        mesh,
        [(-0.50, -0.78, 0.82), (0.50, -0.78, 0.82), (0.50, 0.26, 0.82), (-0.50, 0.26, 0.82)],
        (0.0, 0.0, 0.18),
        roof,
    )
    add_box(mesh, 0.18, 0.46, 0.28, 0.58, 0.10, 1.05, tower)
    add_box_shattered(
        mesh,
        0.18,
        0.46,
        0.28,
        0.58,
        0.22,
        1.02,
        TOWER_SHARDS,
        cols=2,
        rows=3,
        include_top=True,
        jitter=0.28,
        skip=0.08,
        seed=18,
    )
    add_box(mesh, 0.24, 0.40, 0.34, 0.52, 1.05, 1.28, dark)
    add_box(mesh, 0.28, 0.44, 0.48, 0.62, 1.12, 1.22, teal)
    add_box(mesh, -0.50, -0.32, 0.48, 0.72, 0.10, 0.22, dark)
    add_box(mesh, -0.48, -0.34, 0.52, 0.68, 0.22, 0.30, dark)
    outline = planform_outline("sym-edge", _PAD, lambda *_: 0.10)
    return attach_mesh_group(
        parent,
        name,
        mesh.to_geom("sym-fill"),
        color,
        scale=2.05,
        outline=outline,
        vertex_colors=True,
    )
