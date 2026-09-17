"""Procedural low-poly strike fighter — simplified F-22-like jet.

Pointed nose, canopy, trapezoid wings, twin canted tails, twin nozzles.
Vertex-colored only — no JPEG.
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

_SCALE = MESH_SCALE


def _mirror(left: list[tuple[float, float]]) -> list[tuple[float, float]]:
    right = [(-x, y) for x, y in reversed(left[1:-1])]
    return [*left, *right]


# Top-down silhouette used for the keyline (body + wings, not the tails).
_LEFT_SILHOUETTE = [
    (0.00, 1.24),
    (-0.08, 0.92),
    (-0.16, 0.42),
    (-0.78, -0.10),
    (-0.52, -0.42),
    (-0.16, -0.28),
    (-0.12, -0.98),
    (0.00, -1.00),
]
SILHOUETTE = _mirror(_LEFT_SILHOUETTE)

_LEFT_WING = [
    (-0.14, 0.34),
    (-0.80, -0.10),
    (-0.50, -0.44),
    (-0.14, -0.24),
]


def _wing_upper(x: float, y: float) -> float:
    return 0.048 - 0.028 * min(abs(x) / 0.80, 1.0)


def _wing_lower(x: float, y: float) -> float:
    return -0.018 - 0.012 * min(abs(x) / 0.80, 1.0)


def _add_fuselage(mesh: MeshBuilder, color_fn) -> None:
    stations = (
        (1.24, 0.018, 0.016, 0.012),
        (0.92, 0.055, 0.045, 0.018),
        (0.50, 0.11, 0.085, 0.028),
        (0.12, 0.155, 0.082, 0.012),
        (-0.32, 0.135, 0.075, 0.012),
        (-0.72, 0.115, 0.068, 0.018),
        (-0.98, 0.095, 0.052, 0.022),
    )
    rings = [box_ring(y, hw, hh, z) for y, hw, hh, z in stations]
    add_loft(mesh, rings, color_fn, cap=True)


def _add_wings(mesh: MeshBuilder, color_fn) -> None:
    left = list(_LEFT_WING)
    right = [(-x, y) for x, y in left]
    right.reverse()
    add_solid_planform(mesh, left, _wing_upper, _wing_lower, color_fn)
    add_solid_planform(mesh, right, _wing_upper, _wing_lower, color_fn)


def _add_tails(mesh: MeshBuilder, color_fn) -> None:
    """Twin outward-canted vertical tails plus small all-moving stabs."""
    for sx in (-1.0, 1.0):
        # Vertical fin (canted ~25°).
        top = [
            (sx * 0.10, -0.48, 0.07),
            (sx * 0.10, -0.84, 0.06),
            (sx * 0.24, -0.90, 0.40),
            (sx * 0.22, -0.56, 0.38),
        ]
        bot = [
            (sx * 0.07, -0.48, 0.06),
            (sx * 0.07, -0.84, 0.05),
            (sx * 0.21, -0.90, 0.38),
            (sx * 0.19, -0.56, 0.36),
        ]
        add_prism(mesh, top, bot, color_fn)
        # Horizontal stab.
        add_slab(
            mesh,
            [
                (sx * 0.10, -0.70, 0.045),
                (sx * 0.40, -0.78, 0.030),
                (sx * 0.36, -0.94, 0.028),
                (sx * 0.09, -0.92, 0.040),
            ],
            (0.0, 0.0, 0.025),
            color_fn,
        )


def _add_details(mesh: MeshBuilder, base: Color) -> None:
    glass_fn = palette_color_fn((0.40, 0.62, 0.82, 1.0), extra_dark=0.12)
    dark_fn = palette_color_fn(base, extra_dark=0.50)
    color_fn = palette_color_fn(base)

    add_slab(
        mesh,
        [
            (-0.06, 0.78, 0.10),
            (0.06, 0.78, 0.10),
            (0.055, 0.28, 0.12),
            (-0.055, 0.28, 0.12),
        ],
        (0.0, 0.0, 0.10),
        glass_fn,
    )
    # Twin nozzles.
    for sx in (-1.0, 1.0):
        add_loft(
            mesh,
            [_nozzle_ring(sx, -0.96), _nozzle_ring(sx, -1.10, hw=0.038, hh=0.028)],
            dark_fn,
            cap=True,
        )
        # Side intake scoop.
        add_slab(
            mesh,
            [
                (sx * 0.14, 0.22, 0.04),
                (sx * 0.20, 0.10, 0.02),
                (sx * 0.18, -0.18, 0.02),
                (sx * 0.14, -0.10, 0.04),
            ],
            (sx * 0.04, 0.0, -0.05),
            color_fn,
        )


def _nozzle_ring(
    sx: float, y: float, hw: float = 0.042, hh: float = 0.032
) -> list[tuple[float, float, float]]:
    cx = sx * 0.058
    z = 0.02
    return [
        (cx + hw, y, z - hh * 0.15),
        (cx + hw * 0.55, y, z + hh),
        (cx - hw * 0.55, y, z + hh),
        (cx - hw, y, z - hh * 0.15),
        (cx - hw * 0.70, y, z - hh),
        (cx + hw * 0.70, y, z - hh),
    ]


def attach_strike_fighter(parent: NodePath, name: str, color: Color) -> NodePath:
    """Non-billboarded simplified jet. ``color`` is the jet palette tint."""
    mesh = MeshBuilder()
    color_fn = palette_color_fn(color)
    _add_fuselage(mesh, color_fn)
    _add_wings(mesh, color_fn)
    _add_tails(mesh, color_fn)
    _add_details(mesh, color)
    outline = planform_outline(
        "sym-edge", SILHOUETTE, lambda x, y: max(_wing_upper(x, y), 0.08)
    )
    return attach_mesh_group(
        parent,
        name,
        mesh.to_geom("sym-fill"),
        color,
        scale=_SCALE,
        outline=outline,
    )
