"""Land-based SAM battery: scorched pad, sandbags, and brick-red canisters."""

from __future__ import annotations

import math

from panda3d.core import NodePath

from viewer3d.textures.geom import (
    Color,
    MeshBuilder,
    add_box,
    add_box_shattered,
    add_prism,
    add_shattered_grid,
    add_slab,
    attach_mesh_group,
    paint_fn,
    planform_outline,
)

CONCRETE = (0.30, 0.28, 0.26, 1.0)
IRON = (0.16, 0.12, 0.10, 1.0)
OXIDE = (0.72, 0.22, 0.16, 1.0)
NOSE = (0.84, 0.26, 0.18, 1.0)
PAD_SHARDS = (
    (0.28, 0.26, 0.24, 1.0),
    (0.12, 0.10, 0.09, 1.0),
    (0.36, 0.32, 0.28, 1.0),
    (0.18, 0.14, 0.12, 1.0),
    (0.22, 0.16, 0.14, 1.0),
)
BAG_SHARDS = (
    (0.48, 0.22, 0.16, 1.0),
    (0.32, 0.16, 0.12, 1.0),
    (0.58, 0.28, 0.18, 1.0),
    (0.18, 0.12, 0.10, 1.0),
)
TUBE_SHARDS = (
    OXIDE,
    IRON,
    NOSE,
    (0.42, 0.14, 0.12, 1.0),
    (0.22, 0.12, 0.10, 1.0),
    (0.90, 0.32, 0.20, 0.95),
)


def _octagon(radius: float) -> list[tuple[float, float]]:
    return [
        (
            radius * math.cos(math.pi / 8 + i * math.pi / 4),
            radius * math.sin(math.pi / 8 + i * math.pi / 4),
        )
        for i in range(8)
    ]


def attach_sam_site(parent: NodePath, name: str, color: Color) -> NodePath:
    mesh = MeshBuilder()
    conc = paint_fn(CONCRETE)
    iron = paint_fn(IRON)
    oxide = paint_fn(OXIDE)
    pad = _octagon(0.58)
    add_box(mesh, -0.52, 0.52, -0.52, 0.52, 0.0, 0.16, conc)
    add_shattered_grid(
        mesh,
        [(-0.48, -0.48, 0.17), (0.48, -0.48, 0.17), (0.48, 0.48, 0.17), (-0.48, 0.48, 0.17)],
        5,
        5,
        PAD_SHARDS,
        fill=0.88,
        proud=0.018,
        jitter=0.46,
        skip=0.14,
        seed=9,
    )
    add_box_shattered(
        mesh,
        -0.54,
        0.54,
        -0.54,
        0.54,
        0.04,
        0.28,
        BAG_SHARDS,
        cols=4,
        rows=1,
        jitter=0.42,
        skip=0.10,
        seed=15,
    )
    add_box(mesh, -0.22, 0.22, -0.22, 0.22, 0.16, 0.48, iron)
    add_box_shattered(
        mesh,
        -0.22,
        0.22,
        -0.22,
        0.22,
        0.20,
        0.48,
        TUBE_SHARDS,
        cols=2,
        rows=2,
        jitter=0.36,
        skip=0.08,
        seed=3,
    )
    for i, ang in enumerate((0.40, 0.90, 1.40, 1.90, 2.40, 2.90)):
        ca, sa = math.cos(ang), math.sin(ang)
        x, y = 0.28 * ca, 0.28 * sa
        x1, y1 = x + 0.10 * ca, y + 0.10 * sa
        top = [
            (x1 - 0.05, y1 - 0.05, 1.15),
            (x1 + 0.05, y1 - 0.05, 1.15),
            (x1 + 0.05, y1 + 0.05, 1.15),
            (x1 - 0.05, y1 + 0.05, 1.15),
        ]
        bot = [
            (x - 0.07, y - 0.07, 0.20),
            (x + 0.07, y - 0.07, 0.20),
            (x + 0.07, y + 0.07, 0.20),
            (x - 0.07, y + 0.07, 0.20),
        ]
        add_prism(mesh, top, bot, oxide if i % 2 == 0 else iron)
        xmin = min(x, x1) - 0.07
        xmax = max(x, x1) + 0.07
        ymin = min(y, y1) - 0.07
        ymax = max(y, y1) + 0.07
        add_box_shattered(
            mesh,
            xmin,
            xmax,
            ymin,
            ymax,
            0.28,
            1.08,
            TUBE_SHARDS,
            cols=1,
            rows=4,
            jitter=0.40,
            skip=0.12,
            seed=20 + i,
        )
        add_slab(
            mesh,
            [
                (x1 - 0.04, y1 - 0.04, 1.18),
                (x1 + 0.04, y1 - 0.04, 1.18),
                (x1 + 0.04, y1 + 0.04, 1.18),
                (x1 - 0.04, y1 + 0.04, 1.18),
            ],
            (0.0, 0.0, 0.06),
            paint_fn(NOSE),
        )
    add_box(mesh, -0.05, 0.05, -0.05, 0.05, 0.48, 0.95, iron)
    add_slab(
        mesh,
        [(-0.16, -0.04, 1.02), (0.16, -0.04, 1.02), (0.16, 0.08, 1.18), (-0.16, 0.08, 1.18)],
        (0.0, 0.06, 0.0),
        paint_fn(IRON),
    )
    add_shattered_grid(
        mesh,
        [(-0.16, -0.04, 1.02), (0.16, -0.04, 1.02), (0.16, 0.08, 1.18), (-0.16, 0.08, 1.18)],
        3,
        2,
        TUBE_SHARDS,
        fill=0.82,
        proud=0.03,
        jitter=0.38,
        skip=0.08,
        seed=44,
    )
    outline = planform_outline("sym-edge", pad, lambda _x, _y: 0.16)
    return attach_mesh_group(
        parent,
        name,
        mesh.to_geom("sym-fill"),
        color,
        scale=1.72,
        outline=outline,
        vertex_colors=True,
    )
