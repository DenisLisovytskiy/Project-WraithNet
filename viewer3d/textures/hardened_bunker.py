"""Earth-covered hardened bunker — buried keep with concrete panel texture."""

from __future__ import annotations

from panda3d.core import NodePath

from viewer3d.textures.geom import (
    Color,
    MeshBuilder,
    add_box,
    add_box_panels,
    add_slab,
    attach_mesh_group,
    flat_fn,
    paint_fn,
    planform_outline,
)

_PAD = [(-0.95, -0.95), (0.95, -0.95), (0.95, 0.95), (-0.95, 0.95)]

EARTH = (0.36, 0.24, 0.12, 1.0)
EARTH_DARK = (0.22, 0.15, 0.08, 1.0)
CONCRETE = (0.50, 0.42, 0.32, 1.0)
CONCRETE_DARK = (0.30, 0.24, 0.18, 1.0)
STEEL = (0.16, 0.14, 0.12, 1.0)
RUST = (0.58, 0.30, 0.14, 1.0)
CAMO = (0.20, 0.32, 0.14, 1.0)
SLIT_LIT = (0.62, 0.48, 0.28, 0.92)
SLIT_DARK = (0.08, 0.07, 0.05, 1.0)


def attach_hardened_bunker(parent: NodePath, name: str, color: Color) -> NodePath:
    mesh = MeshBuilder()
    earth = paint_fn(EARTH, extra_dark=0.12)
    earth_d = paint_fn(EARTH_DARK, extra_dark=0.08)
    conc = paint_fn(CONCRETE)
    conc_d = paint_fn(CONCRETE_DARK)
    steel = paint_fn(STEEL)
    rust = flat_fn(RUST)
    camo = flat_fn(CAMO)
    add_box(mesh, -0.95, 0.95, -0.95, 0.95, 0.0, 0.42, earth_d)
    add_box(mesh, -0.88, 0.88, -0.88, 0.88, 0.32, 0.52, earth)
    add_box(mesh, -0.72, 0.72, -0.72, 0.72, 0.28, 1.05, conc)
    add_box_panels(
        mesh,
        -0.72,
        0.72,
        -0.72,
        0.72,
        0.42,
        1.02,
        SLIT_LIT,
        SLIT_DARK,
        cols=4,
        rows=2,
        fill=0.52,
        proud=0.028,
        threshold=160,
    )
    add_box(mesh, -0.80, 0.80, -0.80, 0.80, 0.95, 1.18, conc_d)
    add_box(mesh, -0.24, 0.24, 0.55, 0.78, 0.32, 0.88, steel)
    add_box(mesh, -0.18, 0.18, 0.76, 0.82, 0.40, 0.82, rust)
    add_box(mesh, -0.02, 0.02, 0.76, 0.83, 0.38, 0.84, rust)
    add_slab(
        mesh,
        [
            (-0.32, 0.70, 0.92),
            (0.32, 0.70, 0.92),
            (0.24, 0.98, 0.55),
            (-0.24, 0.98, 0.55),
        ],
        (0.0, 0.0, 0.16),
        steel,
    )
    add_box(mesh, -0.42, -0.30, -0.22, -0.10, 1.18, 1.32, conc)
    add_box(mesh, 0.30, 0.42, -0.22, -0.10, 1.18, 1.32, conc)
    add_box(mesh, -0.38, -0.34, -0.18, -0.14, 1.32, 1.40, steel)
    add_box(mesh, 0.34, 0.38, -0.18, -0.14, 1.32, 1.40, steel)
    add_box(mesh, -0.58, -0.18, -0.62, -0.28, 1.18, 1.24, camo)
    add_box(mesh, 0.22, 0.58, 0.10, 0.42, 1.18, 1.24, camo)
    outline = planform_outline("sym-edge", _PAD, lambda _x, _y: 0.42)
    return attach_mesh_group(
        parent,
        name,
        mesh.to_geom("sym-fill"),
        color,
        scale=2.20,
        outline=outline,
        vertex_colors=True,
    )
