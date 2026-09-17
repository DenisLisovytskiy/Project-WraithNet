"""EW jammer: hexagonal pad, equipment shack, and a thick lattice of dipole arrays."""

from __future__ import annotations

import math

from panda3d.core import NodePath

from viewer3d.textures.geom import (
    Color,
    MeshBuilder,
    add_box,
    add_slab,
    add_solid_planform,
    attach_mesh_group,
    palette_color_fn,
    planform_outline,
)


def _hex(radius: float) -> list[tuple[float, float]]:
    return [
        (
            radius * math.cos(math.pi / 6 + i * math.pi / 3),
            radius * math.sin(math.pi / 6 + i * math.pi / 3),
        )
        for i in range(6)
    ]


def _dipole_arm(mesh: MeshBuilder, angle: float, z: float, length: float, fn, dark) -> None:
    ca, sa = math.cos(angle), math.sin(angle)
    x1, y1 = length * ca, length * sa
    px, py = -sa * 0.07, ca * 0.07
    add_slab(
        mesh,
        [
            (-px, -py, z + 0.06),
            (px, py, z + 0.06),
            (x1 + px, y1 + py, z + 0.06),
            (x1 - px, y1 - py, z + 0.06),
        ],
        (0.0, 0.0, 0.12),
        dark,
    )
    qx, qy = -sa * 0.20, ca * 0.20
    nx, ny = ca * 0.11, sa * 0.11
    add_slab(
        mesh,
        [
            (x1 - qx + nx, y1 - qy + ny, z + 0.32),
            (x1 + qx + nx, y1 + qy + ny, z + 0.32),
            (x1 + qx + nx, y1 + qy + ny, z - 0.26),
            (x1 - qx + nx, y1 - qy + ny, z - 0.26),
        ],
        (nx, ny, 0.0),
        fn,
    )


def attach_jammer(parent: NodePath, name: str, color: Color) -> NodePath:
    mesh = MeshBuilder()
    fn = palette_color_fn(color)
    dark = palette_color_fn(color, extra_dark=0.40)
    pad = _hex(0.64)
    add_solid_planform(mesh, pad, lambda *_: 0.22, lambda *_: 0.0, dark)
    inner = _hex(0.42)
    add_solid_planform(mesh, inner, lambda *_: 0.30, lambda *_: 0.18, fn)
    add_box(mesh, 0.08, 0.46, -0.42, -0.04, 0.22, 0.68, fn)
    add_box(mesh, 0.14, 0.40, -0.38, -0.08, 0.68, 0.80, dark)
    add_box(mesh, -0.46, -0.18, 0.10, 0.38, 0.22, 0.52, dark)
    legs = []
    for ang in (0.0, 2.094, 4.189):
        x, y = 0.14 * math.cos(ang), 0.14 * math.sin(ang)
        legs.append((x, y))
        add_box(mesh, x - 0.07, x + 0.07, y - 0.07, y + 0.07, 0.22, 1.88, dark)
    for z in (0.70, 1.18, 1.62):
        for i, (x, y) in enumerate(legs):
            x2, y2 = legs[(i + 1) % 3]
            add_box(
                mesh,
                min(x, x2) - 0.03,
                max(x, x2) + 0.03,
                min(y, y2) - 0.03,
                max(y, y2) + 0.03,
                z - 0.05,
                z + 0.05,
                fn,
            )
        for i in range(6):
            _dipole_arm(mesh, math.pi / 6 + i * math.pi / 3, z, 0.42 + (z - 0.70) * 0.12, fn, dark)
    add_box(mesh, -0.16, 0.16, -0.16, 0.16, 1.78, 2.05, fn)
    outline = planform_outline("sym-edge", pad, lambda *_: 0.22)
    return attach_mesh_group(parent, name, mesh.to_geom("sym-fill"), color, scale=1.88, outline=outline)
