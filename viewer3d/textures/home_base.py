"""Friendly forward airbase: hangar, pad, and control tower."""

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

_PAD = [(-0.58, -0.90), (0.58, -0.90), (0.58, 0.90), (-0.58, 0.90)]


def attach_home_base(parent: NodePath, name: str, color: Color) -> NodePath:
    mesh = MeshBuilder()
    fn = palette_color_fn(color)
    dark = palette_color_fn(color, extra_dark=0.38)
    add_box(mesh, -0.55, 0.55, -0.85, 0.85, 0.0, 0.10, dark)
    add_box(mesh, -0.46, 0.46, -0.74, 0.22, 0.10, 0.62, fn)
    add_slab(
        mesh,
        [(-0.50, -0.78, 0.82), (0.50, -0.78, 0.82), (0.50, 0.26, 0.82), (-0.50, 0.26, 0.82)],
        (0.0, 0.0, 0.18),
        dark,
    )
    add_box(mesh, 0.18, 0.46, 0.28, 0.58, 0.10, 1.05, fn)
    add_box(mesh, 0.24, 0.40, 0.34, 0.52, 1.05, 1.28, dark)
    outline = planform_outline("sym-edge", _PAD, lambda *_: 0.10)
    return attach_mesh_group(parent, name, mesh.to_geom("sym-fill"), color, scale=2.05, outline=outline)
