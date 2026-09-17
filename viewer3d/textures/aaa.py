"""AAA emplacements: open gun pit vs turreted SPAAG.

Barrels point at the sky. Hull, pit walls, and gun house are thick solids.
"""

from __future__ import annotations

from panda3d.core import NodePath

from viewer3d.textures.geom import (
    Color,
    MeshBuilder,
    add_box,
    add_loft,
    add_prism,
    add_solid_planform,
    attach_mesh_group,
    box_ring,
    palette_color_fn,
    planform_outline,
)

_PIT = [(-0.52, -0.52), (0.52, -0.52), (0.52, 0.52), (-0.52, 0.52)]


def _sky_barrel(
    mesh: MeshBuilder,
    x: float,
    y: float,
    z0: float,
    *,
    length: float,
    lean: float,
    r0: float,
    r1: float,
    fn,
) -> None:
    y1 = y + length * lean
    z1 = z0 + length
    top = [
        (x - r1, y1 - r1, z1),
        (x + r1, y1 - r1, z1),
        (x + r1, y1 + r1, z1),
        (x - r1, y1 + r1, z1),
    ]
    bot = [
        (x - r0, y - r0, z0),
        (x + r0, y - r0, z0),
        (x + r0, y + r0, z0),
        (x - r0, y + r0, z0),
    ]
    add_prism(mesh, top, bot, fn)
    # Jacket around the lower third so the barrel is a tube, not a needle.
    mid = length * 0.32
    yj = y + mid * lean
    zj = z0 + mid
    rj = r0 * 1.45
    add_prism(
        mesh,
        [
            (x - r0, yj - r0, zj),
            (x + r0, yj - r0, zj),
            (x + r0, yj + r0, zj),
            (x - r0, yj + r0, zj),
        ],
        [
            (x - rj, y - rj, z0),
            (x + rj, y - rj, z0),
            (x + rj, y + rj, z0),
            (x - rj, y + rj, z0),
        ],
        fn,
    )


def _twin_sky_barrels(mesh, y, z0, *, length, spread, lean, fn) -> None:
    for sx in (-spread, spread):
        _sky_barrel(mesh, sx, y, z0, length=length, lean=lean, r0=0.11, r1=0.038, fn=fn)


def attach_aaa_light(parent: NodePath, name: str, color: Color) -> NodePath:
    mesh = MeshBuilder()
    fn = palette_color_fn(color)
    dark = palette_color_fn(color, extra_dark=0.40)
    add_solid_planform(mesh, _PIT, lambda *_: 0.14, lambda *_: -0.08, dark)
    # Thick sandbag ring — walls have both height and thickness.
    add_box(mesh, -0.54, 0.54, -0.54, -0.28, 0.10, 0.52, fn)
    add_box(mesh, -0.54, 0.54, 0.28, 0.54, 0.10, 0.52, fn)
    add_box(mesh, -0.54, -0.28, -0.28, 0.28, 0.10, 0.52, fn)
    add_box(mesh, 0.28, 0.54, -0.28, 0.28, 0.10, 0.52, fn)
    add_box(mesh, -0.22, 0.22, -0.22, 0.22, 0.12, 0.42, dark)
    add_box(mesh, -0.18, 0.18, -0.18, 0.18, 0.42, 0.68, fn)
    add_box(mesh, -0.28, -0.08, 0.10, 0.28, 0.14, 0.36, dark)
    add_box(mesh, 0.08, 0.28, 0.10, 0.28, 0.14, 0.36, dark)
    _twin_sky_barrels(mesh, 0.02, 0.62, length=1.38, spread=0.12, lean=0.10, fn=dark)
    outline = planform_outline("sym-edge", _PIT, lambda *_: 0.52)
    return attach_mesh_group(parent, name, mesh.to_geom("sym-fill"), color, scale=1.58, outline=outline)


def attach_aaa_turreted(parent: NodePath, name: str, color: Color) -> NodePath:
    mesh = MeshBuilder()
    fn = palette_color_fn(color)
    dark = palette_color_fn(color, extra_dark=0.35)
    add_loft(
        mesh,
        [
            box_ring(-0.40, 0.36, 0.22, z=0.30),
            box_ring(-0.08, 0.38, 0.24, z=0.32),
            box_ring(0.22, 0.34, 0.20, z=0.34),
            box_ring(0.40, 0.28, 0.16, z=0.30),
        ],
        fn,
        upper_hint=lambda p: p[2] >= 0.28,
    )
    add_box(mesh, -0.42, 0.42, -0.38, 0.38, 0.00, 0.18, dark)
    for y in (-0.28, 0.00, 0.26):
        add_box(mesh, -0.46, -0.34, y - 0.10, y + 0.10, 0.00, 0.20, dark)
        add_box(mesh, 0.34, 0.46, y - 0.10, y + 0.10, 0.00, 0.20, dark)
    add_box(mesh, -0.26, 0.26, -0.24, 0.22, 0.48, 0.92, fn)
    add_box(mesh, -0.22, 0.22, -0.34, -0.16, 0.55, 0.88, dark)
    add_box(mesh, -0.16, 0.16, -0.12, 0.14, 0.92, 1.12, dark)
    _twin_sky_barrels(mesh, 0.02, 0.88, length=1.48, spread=0.14, lean=0.08, fn=dark)
    hull = [(-0.46, -0.40), (0.46, -0.40), (0.46, 0.40), (-0.46, 0.40)]
    outline = planform_outline("sym-edge", hull, lambda *_: 0.48)
    return attach_mesh_group(parent, name, mesh.to_geom("sym-fill"), color, scale=1.68, outline=outline)
