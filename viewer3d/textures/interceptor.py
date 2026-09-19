"""Procedural interceptor — needle-nosed, canarded, cropped-delta air superiority jet.

Smaller and sharper than the strike fighter: close-coupled canards, short-span
delta, twin outward-canted tails. Vertex-colored only — no JPEG.
"""

from __future__ import annotations

from panda3d.core import NodePath

from viewer3d.textures.geom import (
    MESH_SCALE,
    Color,
    MeshBuilder,
    add_loft,
    add_prism,
    add_slab,
    add_solid_planform,
    attach_mesh_group,
    box_ring,
    palette_color_fn,
    planform_outline,
)

_SCALE = MESH_SCALE * 0.82


def _mirror(left: list[tuple[float, float]]) -> list[tuple[float, float]]:
    right = [(-x, y) for x, y in reversed(left[1:-1])]
    return [*left, *right]


_LEFT_SILHOUETTE = [
    (0.00, 1.32),
    (-0.05, 1.02),
    (-0.32, 0.62),  # canard tip
    (-0.10, 0.48),
    (-0.54, -0.16),  # cropped delta
    (-0.36, -0.36),
    (-0.10, -0.22),
    (-0.08, -1.02),
    (0.00, -1.04),
]
SILHOUETTE = _mirror(_LEFT_SILHOUETTE)

_LEFT_CANARD = [
    (-0.07, 0.78),
    (-0.34, 0.60),
    (-0.28, 0.48),
    (-0.07, 0.54),
]
_LEFT_WING = [
    (-0.09, 0.18),
    (-0.54, -0.16),
    (-0.36, -0.38),
    (-0.09, -0.16),
]


def _surf_upper(x: float, y: float) -> float:
    return 0.036 - 0.018 * min(abs(x) / 0.54, 1.0)


def _surf_lower(x: float, y: float) -> float:
    return -0.014 - 0.008 * min(abs(x) / 0.54, 1.0)


def _add_fuselage(mesh: MeshBuilder, color_fn) -> None:
    stations = (
        (1.32, 0.012, 0.012, 0.010),
        (1.02, 0.038, 0.032, 0.014),
        (0.62, 0.070, 0.055, 0.022),
        (0.18, 0.095, 0.058, 0.010),
        (-0.28, 0.085, 0.052, 0.010),
        (-0.70, 0.075, 0.048, 0.014),
        (-1.00, 0.062, 0.038, 0.016),
    )
    add_loft(mesh, [box_ring(y, hw, hh, z) for y, hw, hh, z in stations], color_fn)


def _add_pair(mesh: MeshBuilder, left: list[tuple[float, float]], color_fn) -> None:
    right = [(-x, y) for x, y in left]
    right.reverse()
    add_solid_planform(mesh, left, _surf_upper, _surf_lower, color_fn)
    add_solid_planform(mesh, right, _surf_upper, _surf_lower, color_fn)


def _add_tails(mesh: MeshBuilder, color_fn) -> None:
    for sx in (-1.0, 1.0):
        top = [
            (sx * 0.07, -0.52, 0.05),
            (sx * 0.07, -0.88, 0.04),
            (sx * 0.26, -0.94, 0.42),
            (sx * 0.24, -0.58, 0.40),
        ]
        bot = [
            (sx * 0.05, -0.52, 0.04),
            (sx * 0.05, -0.88, 0.03),
            (sx * 0.23, -0.94, 0.40),
            (sx * 0.21, -0.58, 0.38),
        ]
        add_prism(mesh, top, bot, color_fn)


def _add_details(mesh: MeshBuilder, base: Color) -> None:
    glass_fn = palette_color_fn((0.32, 0.50, 0.70, 1.0), extra_dark=0.10)
    dark_fn = palette_color_fn(base, extra_dark=0.50)
    color_fn = palette_color_fn(base)
    add_slab(
        mesh,
        [
            (-0.045, 0.88, 0.07),
            (0.045, 0.88, 0.07),
            (0.040, 0.38, 0.09),
            (-0.040, 0.38, 0.09),
        ],
        (0.0, 0.0, 0.08),
        glass_fn,
    )
    for sx in (-1.0, 1.0):
        add_loft(
            mesh,
            [_nozzle_ring(sx, -0.98), _nozzle_ring(sx, -1.12, hw=0.028, hh=0.022)],
            dark_fn,
            cap=True,
        )
        add_slab(
            mesh,
            [
                (sx * 0.08, 0.58, 0.00),
                (sx * 0.14, 0.48, -0.02),
                (sx * 0.13, 0.22, -0.02),
                (sx * 0.08, 0.30, 0.00),
            ],
            (sx * 0.03, 0.0, -0.04),
            color_fn,
        )


def _nozzle_ring(
    sx: float, y: float, hw: float = 0.032, hh: float = 0.024
) -> list[tuple[float, float, float]]:
    cx = sx * 0.042
    z = 0.016
    return [
        (cx + hw, y, z - hh * 0.15),
        (cx + hw * 0.55, y, z + hh),
        (cx - hw * 0.55, y, z + hh),
        (cx - hw, y, z - hh * 0.15),
        (cx - hw * 0.70, y, z - hh),
        (cx + hw * 0.70, y, z - hh),
    ]


def attach_interceptor(parent: NodePath, name: str, color: Color) -> NodePath:
    mesh = MeshBuilder()
    color_fn = palette_color_fn(color)
    _add_fuselage(mesh, color_fn)
    _add_pair(mesh, _LEFT_CANARD, color_fn)
    _add_pair(mesh, _LEFT_WING, color_fn)
    _add_tails(mesh, color_fn)
    _add_details(mesh, color)
    outline = planform_outline(
        "sym-edge", SILHOUETTE, lambda x, y: max(_surf_upper(x, y), 0.06)
    )
    return attach_mesh_group(
        parent, name, mesh.to_geom("sym-fill"), color, scale=_SCALE, outline=outline
    )
