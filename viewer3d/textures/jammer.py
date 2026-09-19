"""EW jammer: fixed pad/mast with independently spinning dipole rings."""

from __future__ import annotations

import math

from panda3d.core import ColorAttrib, NodePath

from viewer3d.textures.geom import (
    Color,
    MeshBuilder,
    add_box,
    add_box_shattered,
    add_shattered_grid,
    add_slab,
    add_solid_planform,
    attach_mesh_group,
    flat_fn,
    paint_fn,
    planform_outline,
)

PAD = (0.18, 0.18, 0.20, 1.0)
PAD_INNER = (0.28, 0.30, 0.32, 1.0)
SHACK = (0.26, 0.28, 0.30, 1.0)
MAST = (0.16, 0.16, 0.18, 1.0)
LATTICE = (0.34, 0.36, 0.40, 1.0)
BEACON = (0.78, 0.90, 1.00, 0.95)
PAD_SHARDS = (
    (0.16, 0.16, 0.18, 1.0),
    (0.24, 0.26, 0.28, 1.0),
    (0.12, 0.12, 0.14, 1.0),
    (0.22, 0.24, 0.26, 1.0),
)
SHACK_SHARDS = (
    (0.30, 0.32, 0.34, 1.0),
    (0.18, 0.18, 0.20, 1.0),
    (0.55, 0.78, 0.95, 0.90),
    (0.12, 0.12, 0.14, 1.0),
    (0.42, 0.52, 0.58, 0.88),
)
DIPOLE_SHARDS = (
    (0.70, 0.86, 1.00, 0.95),
    (0.22, 0.26, 0.32, 1.0),
    (0.48, 0.64, 0.80, 0.92),
    (0.16, 0.18, 0.22, 1.0),
    (0.85, 0.92, 1.00, 0.92),
)

# Height / spin-rate (deg/s) for each dipole ring — opposite directions, different speeds.
_RING_SPECS: tuple[tuple[float, float, float], ...] = (
    (0.70, 0.42, 38.0),
    (1.18, 0.48, -62.0),
    (1.62, 0.54, 88.0),
)


def _hex(radius: float) -> list[tuple[float, float]]:
    return [
        (
            radius * math.cos(math.pi / 6 + i * math.pi / 3),
            radius * math.sin(math.pi / 6 + i * math.pi / 3),
        )
        for i in range(6)
    ]


def _dipole_arm(mesh: MeshBuilder, angle: float, z: float, length: float, seed: int) -> None:
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
        paint_fn(MAST),
    )
    qx, qy = -sa * 0.20, ca * 0.20
    nx, ny = ca * 0.11, sa * 0.11
    panel = [
        (x1 - qx + nx, y1 - qy + ny, z + 0.32),
        (x1 + qx + nx, y1 + qy + ny, z + 0.32),
        (x1 + qx + nx, y1 + qy + ny, z - 0.26),
        (x1 - qx + nx, y1 - qy + ny, z - 0.26),
    ]
    add_slab(mesh, panel, (nx * 0.4, ny * 0.4, 0.0), paint_fn(MAST))
    add_shattered_grid(
        mesh,
        panel,
        2,
        3,
        DIPOLE_SHARDS,
        fill=0.88,
        proud=0.05,
        jitter=0.48,
        skip=0.12,
        seed=seed,
    )


def _build_ring(mesh: MeshBuilder, z: float, length: float, ring_i: int) -> None:
    lattice = paint_fn(LATTICE)
    # Hex rail the dipoles hang from.
    corners = _hex(0.22)
    for i, (x, y) in enumerate(corners):
        x2, y2 = corners[(i + 1) % 6]
        add_box(
            mesh,
            min(x, x2) - 0.03,
            max(x, x2) + 0.03,
            min(y, y2) - 0.03,
            max(y, y2) + 0.03,
            z - 0.04,
            z + 0.04,
            lattice,
        )
    for i in range(6):
        _dipole_arm(
            mesh,
            math.pi / 6 + i * math.pi / 3,
            z,
            length,
            40 + ring_i * 7 + i,
        )


def _attach_ring(
    group: NodePath, ring_i: int, z: float, length: float, spin_deg_per_sec: float
) -> None:
    mesh = MeshBuilder()
    _build_ring(mesh, z, length, ring_i)
    ring = group.attach_new_node(f"jammer-ring-{ring_i}")
    ring.set_tag("jammer-spin", "1")
    ring.set_python_tag("spin_deg_per_sec", float(spin_deg_per_sec))
    fill = ring.attach_new_node(mesh.to_geom(f"ring-fill-{ring_i}"))
    fill.set_attrib(ColorAttrib.make_vertex())
    fill.set_light_off()
    fill.set_two_sided(True)


def attach_jammer(parent: NodePath, name: str, color: Color) -> NodePath:
    mesh = MeshBuilder()
    pad_fn = paint_fn(PAD, extra_dark=0.08)
    inner_fn = paint_fn(PAD_INNER)
    shack = paint_fn(SHACK)
    mast = paint_fn(MAST)
    lattice = paint_fn(LATTICE)
    beacon = flat_fn(BEACON)
    pad = _hex(0.64)
    add_solid_planform(mesh, pad, lambda *_: 0.22, lambda *_: 0.0, pad_fn)
    add_shattered_grid(
        mesh,
        [(-0.38, -0.38, 0.23), (0.38, -0.38, 0.23), (0.38, 0.38, 0.23), (-0.38, 0.38, 0.23)],
        4,
        4,
        PAD_SHARDS,
        fill=0.86,
        proud=0.02,
        jitter=0.45,
        skip=0.12,
        seed=6,
    )
    inner = _hex(0.42)
    add_solid_planform(mesh, inner, lambda *_: 0.30, lambda *_: 0.18, inner_fn)
    add_box(mesh, 0.08, 0.46, -0.42, -0.04, 0.22, 0.68, shack)
    add_box_shattered(
        mesh,
        0.08,
        0.46,
        -0.42,
        -0.04,
        0.28,
        0.68,
        SHACK_SHARDS,
        cols=3,
        rows=2,
        include_top=True,
        jitter=0.46,
        skip=0.10,
        seed=14,
    )
    add_box(mesh, 0.14, 0.40, -0.38, -0.08, 0.68, 0.80, mast)
    add_box(mesh, -0.46, -0.18, 0.10, 0.38, 0.22, 0.52, mast)
    add_box_shattered(
        mesh,
        -0.46,
        -0.18,
        0.10,
        0.38,
        0.24,
        0.52,
        PAD_SHARDS,
        cols=2,
        rows=2,
        jitter=0.40,
        skip=0.08,
        seed=22,
    )
    # Fixed mast legs — rings free-spin around them.
    for ang in (0.0, 2.094, 4.189):
        x, y = 0.14 * math.cos(ang), 0.14 * math.sin(ang)
        add_box(mesh, x - 0.07, x + 0.07, y - 0.07, y + 0.07, 0.22, 1.88, mast)
    add_box(mesh, -0.16, 0.16, -0.16, 0.16, 1.78, 2.05, lattice)
    add_box(mesh, -0.05, 0.05, -0.05, 0.05, 2.05, 2.18, beacon)
    outline = planform_outline("sym-edge", pad, lambda *_: 0.22)
    group = attach_mesh_group(
        parent,
        name,
        mesh.to_geom("sym-fill"),
        color,
        scale=1.88,
        outline=outline,
        vertex_colors=True,
    )
    for i, (z, length, rate) in enumerate(_RING_SPECS):
        _attach_ring(group, i, z, length, rate)
    return group
