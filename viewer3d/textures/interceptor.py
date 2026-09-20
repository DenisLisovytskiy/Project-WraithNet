"""Procedural interceptor — needle-nosed, canarded, cropped-delta air superiority jet.

Smaller and sharper than the strike fighter: close-coupled canards, short-span
delta, twin outward-canted tails. Paint follows the concept JPEG: the darkest
airframe of the three, hard spine-to-wingtip falloff, hot cyan leading edges.
Vertex-colored only — no JPEG at render time.
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
    heat_fn,
    palette_color_fn,
    planform_outline,
    stealth_fn,
)

_SCALE = MESH_SCALE * 0.82

# From assets/textures/pics/interceptor.jpeg: near-black graphite airframe,
# smoked canopy, glowing nozzle cans.
BODY = (0.21, 0.24, 0.30, 1.0)
GLASS = (0.16, 0.26, 0.38, 1.0)
IRON = (0.14, 0.14, 0.16, 1.0)
EMBER = (1.00, 0.46, 0.18, 1.0)


def _mirror(left: list[tuple[float, float]]) -> list[tuple[float, float]]:
    right = [(-x, y) for x, y in reversed(left[1:-1])]
    return [*left, *right]


_LEFT_SILHOUETTE = [
    (0.00, 1.32),
    (-0.038, 1.02),
    (-0.07, 0.78),  # canard LE root
    (-0.34, 0.60),  # canard tip
    (-0.28, 0.48),
    (-0.09, 0.18),  # wing LE root
    (-0.54, -0.16),  # wing tip
    (-0.36, -0.38),
    (-0.09, -0.16),
    (-0.075, -0.70),
    (-0.26, -0.94),  # tail tip planform
    (-0.062, -1.00),
    (0.00, -1.12),  # nozzle aft
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


def _add_details(mesh: MeshBuilder, dark_fn) -> None:
    glass_fn = palette_color_fn(GLASS, extra_dark=0.10)
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
            heat_fn(IRON, EMBER, -0.98, -1.12),
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
            dark_fn,
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


def _outline_z(x: float, y: float) -> float:
    """Follow canard/wing skin so the keyline is not floating above the mesh."""
    ax = abs(x)
    if ax > 0.08 and -0.42 <= y <= 0.82:
        return _surf_upper(x, y)
    return 0.016 + 0.03 * max(0.0, 1.0 - abs(y) / 1.32)


def attach_interceptor(parent: NodePath, name: str, color: Color) -> NodePath:
    mesh = MeshBuilder()
    color_fn = stealth_fn(BODY, color, span=0.54, spine=0.34, glow=0.60, tint_mix=0.28)
    dark_fn = stealth_fn(
        BODY, color, span=0.54, spine=0.34, glow=0.0, tint_mix=0.28, extra_dark=0.30
    )
    _add_fuselage(mesh, color_fn)
    _add_pair(mesh, _LEFT_CANARD, color_fn)
    _add_pair(mesh, _LEFT_WING, color_fn)
    _add_tails(mesh, color_fn)
    _add_details(mesh, dark_fn)
    outline = planform_outline("sym-edge", SILHOUETTE, _outline_z)
    return attach_mesh_group(
        parent,
        name,
        mesh.to_geom("sym-fill"),
        color,
        scale=_SCALE,
        outline=outline,
        vertex_colors=True,
    )
