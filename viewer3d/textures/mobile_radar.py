"""Truck-mounted mobile search radar: thick chassis + a volumetric diamond AESA."""

from __future__ import annotations

from panda3d.core import NodePath

from viewer3d.textures.geom import (
    Color,
    MeshBuilder,
    add_box,
    add_loft,
    add_slab,
    attach_mesh_group,
    box_ring,
    palette_color_fn,
    planform_outline,
    shade,
)

_DIAMOND = [(0.0, 0.88), (0.68, 0.10), (0.0, -0.78), (-0.68, 0.10)]


def _panel_fn(base: Color):
    def _fn(x: float, y: float, z: float, upper: bool, rim: bool) -> Color:
        del upper, rim
        return shade(base, upper=True, rim=True, x=x, y=y)

    return _fn


def attach_mobile_radar(parent: NodePath, name: str, color: Color) -> NodePath:
    mesh = MeshBuilder()
    fn = palette_color_fn(color)
    dark = palette_color_fn(color, extra_dark=0.42)
    face = _panel_fn(color)
    add_loft(
        mesh,
        [
            box_ring(-0.72, 0.30, 0.20, z=0.36),
            box_ring(-0.20, 0.32, 0.22, z=0.38),
            box_ring(0.28, 0.28, 0.20, z=0.40),
            box_ring(0.62, 0.24, 0.16, z=0.42),
        ],
        fn,
        upper_hint=lambda p: p[2] >= 0.32,
    )
    add_box(mesh, -0.26, 0.26, 0.08, 0.66, 0.52, 0.98, fn)
    add_box(mesh, -0.22, 0.22, 0.48, 0.68, 0.72, 1.02, dark)
    add_box(mesh, -0.34, 0.34, -0.70, 0.58, 0.00, 0.22, dark)
    for y in (-0.55, -0.22, 0.12, 0.44):
        add_box(mesh, -0.38, -0.26, y - 0.11, y + 0.11, 0.00, 0.24, dark)
        add_box(mesh, 0.26, 0.38, y - 0.11, y + 0.11, 0.00, 0.24, dark)
    add_box(mesh, -0.44, -0.28, -0.12, 0.12, 0.04, 0.14, dark)
    add_box(mesh, 0.28, 0.44, -0.12, 0.12, 0.04, 0.14, dark)
    add_box(mesh, -0.20, 0.20, -0.48, -0.08, 0.50, 0.78, dark)
    add_box(mesh, -0.10, 0.10, -0.16, 0.10, 0.50, 1.42, dark)
    add_box(mesh, -0.22, 0.22, -0.14, 0.16, 1.32, 1.58, fn)
    # Thick AESA: front face plus a deep backplane and side rails.
    add_slab(
        mesh,
        [
            (0.00, -0.02, 0.92),
            (0.72, 0.28, 1.64),
            (0.00, 0.56, 2.32),
            (-0.72, 0.28, 1.64),
        ],
        (0.0, 0.28, 0.0),
        face,
    )
    add_box(mesh, -0.18, 0.18, -0.08, 0.22, 1.48, 1.72, dark)
    add_box(mesh, -0.06, 0.06, 0.10, 0.42, 1.20, 1.55, dark)
    outline = planform_outline("sym-edge", _DIAMOND, lambda _x, _y: 0.52)
    return attach_mesh_group(parent, name, mesh.to_geom("sym-fill"), color, scale=1.92, outline=outline)
