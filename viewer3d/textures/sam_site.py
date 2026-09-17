"""Land-based SAM battery: octagonal pad and upright canisters."""

from __future__ import annotations

import math

from panda3d.core import NodePath

from viewer3d.textures.geom import (
    Color,
    MeshBuilder,
    add_box,
    add_prism,
    attach_mesh_group,
    palette_color_fn,
    planform_outline,
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
    fn = palette_color_fn(color)
    dark = palette_color_fn(color, extra_dark=0.40)
    pad = _octagon(0.58)
    add_box(mesh, -0.52, 0.52, -0.52, 0.52, 0.0, 0.16, dark)
    add_box(mesh, -0.22, 0.22, -0.22, 0.22, 0.16, 0.48, fn)
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
        add_prism(mesh, top, bot, fn if i % 2 == 0 else dark)
    add_box(mesh, -0.05, 0.05, -0.05, 0.05, 0.48, 0.95, dark)
    outline = planform_outline("sym-edge", pad, lambda _x, _y: 0.16)
    return attach_mesh_group(parent, name, mesh.to_geom("sym-fill"), color, scale=1.72, outline=outline)
