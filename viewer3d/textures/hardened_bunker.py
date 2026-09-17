"""Earth-covered hardened bunker — a buried keep, not a floor tile."""

from __future__ import annotations

from panda3d.core import NodePath

from viewer3d.textures.geom import (
    Color,
    MeshBuilder,
    add_box,
    add_slab,
    attach_mesh_group,
    palette_color_fn,
    planform_outline,
)

_PAD = [(-0.95, -0.95), (0.95, -0.95), (0.95, 0.95), (-0.95, 0.95)]


def attach_hardened_bunker(parent: NodePath, name: str, color: Color) -> NodePath:
    mesh = MeshBuilder()
    fn = palette_color_fn(color)
    dark = palette_color_fn(color, extra_dark=0.38)
    add_box(mesh, -0.95, 0.95, -0.95, 0.95, 0.0, 0.42, dark)
    add_box(mesh, -0.72, 0.72, -0.72, 0.72, 0.28, 1.05, fn)
    add_box(mesh, -0.80, 0.80, -0.80, 0.80, 0.95, 1.18, dark)
    add_box(mesh, -0.24, 0.24, 0.55, 0.78, 0.32, 0.88, dark)
    add_slab(
        mesh,
        [
            (-0.32, 0.70, 0.92),
            (0.32, 0.70, 0.92),
            (0.24, 0.98, 0.55),
            (-0.24, 0.98, 0.55),
        ],
        (0.0, 0.0, 0.16),
        dark,
    )
    add_box(mesh, -0.42, -0.30, -0.22, -0.10, 1.18, 1.32, fn)
    add_box(mesh, 0.30, 0.42, -0.22, -0.10, 1.18, 1.32, fn)
    outline = planform_outline("sym-edge", _PAD, lambda _x, _y: 0.42)
    return attach_mesh_group(
        parent, name, mesh.to_geom("sym-fill"), color, scale=2.20, outline=outline
    )
