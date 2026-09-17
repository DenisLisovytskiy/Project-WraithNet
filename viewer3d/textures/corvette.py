"""Procedural enemy corvettes — light missile boat vs heavier standard escort.

Shared hull loft; the variants differ in beam, superstructure, mast count,
VLS farm size, and whether a 76mm-class deck gun is present.
"""

from __future__ import annotations

from dataclasses import dataclass

from panda3d.core import NodePath

from viewer3d.textures.geom import (
    Color,
    MeshBuilder,
    add_loft,
    add_slab,
    attach_mesh_group,
    hull_ring,
    palette_color_fn,
    planform_outline,
)


@dataclass(frozen=True)
class CorvetteSpec:
    scale: float
    bow: float
    stern: float
    mid_beam: float
    deck_z: float
    keel_z: float
    super_len: float
    super_beam: float
    super_h: float
    twin_mast: bool
    deck_gun: bool
    vls_cols: int
    vls_rows: int
    ciws_aft: bool


LIGHT = CorvetteSpec(
    scale=1.28,
    bow=1.18,
    stern=-1.05,
    mid_beam=0.30,
    deck_z=0.07,
    keel_z=-0.10,
    super_len=0.55,
    super_beam=0.18,
    super_h=0.16,
    twin_mast=False,
    deck_gun=False,
    vls_cols=2,
    vls_rows=4,
    ciws_aft=False,
)
STANDARD = CorvetteSpec(
    scale=1.52,
    bow=1.28,
    stern=-1.18,
    mid_beam=0.42,
    deck_z=0.08,
    keel_z=-0.12,
    super_len=0.78,
    super_beam=0.26,
    super_h=0.22,
    twin_mast=True,
    deck_gun=True,
    vls_cols=4,
    vls_rows=6,
    ciws_aft=True,
)


def _stations(spec: CorvetteSpec) -> list:
    b, d, k = spec.mid_beam, spec.deck_z, spec.keel_z
    return [
        hull_ring(spec.bow, b * 0.12, d * 0.55, k * 0.35),
        hull_ring(spec.bow * 0.72, b * 0.55, d * 0.85, k * 0.70),
        hull_ring(spec.bow * 0.28, b * 0.92, d, k),
        hull_ring(0.05, b, d, k),
        hull_ring(spec.stern * 0.45, b * 0.95, d, k),
        hull_ring(spec.stern, b * 0.62, d * 0.90, k * 0.75),
    ]


def _deck_planform(spec: CorvetteSpec) -> list[tuple[float, float]]:
    hw = spec.mid_beam * 0.5
    return [
        (0.00, spec.bow),
        (-hw * 0.55, spec.bow * 0.72),
        (-hw, 0.05),
        (-hw * 0.62, spec.stern),
        (hw * 0.62, spec.stern),
        (hw, 0.05),
        (hw * 0.55, spec.bow * 0.72),
    ]


def _add_superstructure(mesh: MeshBuilder, spec: CorvetteSpec, color_fn) -> None:
    y0, y1 = -0.05, -0.05 - spec.super_len
    hw = spec.super_beam * 0.5
    z0 = spec.deck_z
    add_slab(
        mesh,
        [
            (-hw, y0, z0 + spec.super_h),
            (hw, y0, z0 + spec.super_h),
            (hw * 0.85, y1, z0 + spec.super_h * 0.85),
            (-hw * 0.85, y1, z0 + spec.super_h * 0.85),
        ],
        (0.0, 0.0, spec.super_h),
        color_fn,
    )
    # Bridge face.
    add_slab(
        mesh,
        [
            (-hw * 0.70, y0 + 0.04, z0 + spec.super_h + 0.07),
            (hw * 0.70, y0 + 0.04, z0 + spec.super_h + 0.07),
            (hw * 0.55, y0 - 0.16, z0 + spec.super_h + 0.05),
            (-hw * 0.55, y0 - 0.16, z0 + spec.super_h + 0.05),
        ],
        (0.0, 0.0, 0.07),
        color_fn,
    )


def _add_mast(mesh: MeshBuilder, spec: CorvetteSpec, x: float, y: float, h: float, color_fn) -> None:
    z0 = spec.deck_z + spec.super_h
    add_slab(
        mesh,
        [
            (x - 0.018, y - 0.025, z0 + h),
            (x + 0.018, y - 0.025, z0 + h),
            (x + 0.018, y + 0.025, z0 + h),
            (x - 0.018, y + 0.025, z0 + h),
        ],
        (0.0, 0.0, h),
        color_fn,
    )
    add_slab(
        mesh,
        [
            (x - 0.09, y - 0.012, z0 + h * 0.78),
            (x + 0.09, y - 0.012, z0 + h * 0.78),
            (x + 0.09, y + 0.012, z0 + h * 0.78),
            (x - 0.09, y + 0.012, z0 + h * 0.78),
        ],
        (0.0, 0.0, 0.03),
        color_fn,
    )


def _add_vls(mesh: MeshBuilder, spec: CorvetteSpec, dark_fn) -> None:
    z = spec.deck_z + 0.025
    cell = 0.055
    gap = 0.012
    farm_w = spec.vls_cols * (cell + gap)
    x0 = -farm_w * 0.5 + cell * 0.5
    y0 = 0.42
    for col in range(spec.vls_cols):
        for row in range(spec.vls_rows):
            x = x0 + col * (cell + gap)
            y = y0 - row * (cell + gap)
            add_slab(
                mesh,
                [
                    (x - cell * 0.4, y - cell * 0.4, z),
                    (x + cell * 0.4, y - cell * 0.4, z),
                    (x + cell * 0.4, y + cell * 0.4, z),
                    (x - cell * 0.4, y + cell * 0.4, z),
                ],
                (0.0, 0.0, 0.03),
                dark_fn,
            )


def _add_gun(mesh: MeshBuilder, spec: CorvetteSpec, color_fn, dark_fn) -> None:
    y = 0.72
    z = spec.deck_z
    add_slab(
        mesh,
        [
            (-0.07, y - 0.07, z + 0.08),
            (0.07, y - 0.07, z + 0.08),
            (0.07, y + 0.07, z + 0.08),
            (-0.07, y + 0.07, z + 0.08),
        ],
        (0.0, 0.0, 0.08),
        color_fn,
    )
    add_slab(
        mesh,
        [
            (-0.025, y + 0.05, z + 0.07),
            (0.025, y + 0.05, z + 0.07),
            (0.020, y + 0.28, z + 0.06),
            (-0.020, y + 0.28, z + 0.06),
        ],
        (0.0, 0.0, 0.035),
        dark_fn,
    )


def _add_ciws(mesh: MeshBuilder, spec: CorvetteSpec, y: float, color_fn) -> None:
    z = spec.deck_z
    add_slab(
        mesh,
        [
            (-0.05, y - 0.05, z + 0.07),
            (0.05, y - 0.05, z + 0.07),
            (0.05, y + 0.05, z + 0.07),
            (-0.05, y + 0.05, z + 0.07),
        ],
        (0.0, 0.0, 0.07),
        color_fn,
    )


def _attach(parent: NodePath, name: str, color: Color, spec: CorvetteSpec) -> NodePath:
    mesh = MeshBuilder()
    color_fn = palette_color_fn(color)
    dark_fn = palette_color_fn(color, extra_dark=0.40)
    add_loft(mesh, _stations(spec), color_fn, cap=True)
    _add_superstructure(mesh, spec, color_fn)
    _add_vls(mesh, spec, dark_fn)
    mast_y = -0.22
    _add_mast(mesh, spec, 0.0, mast_y, 0.28 if not spec.twin_mast else 0.34, color_fn)
    if spec.twin_mast:
        _add_mast(mesh, spec, 0.0, mast_y - 0.28, 0.26, color_fn)
    if spec.deck_gun:
        _add_gun(mesh, spec, color_fn, dark_fn)
    else:
        _add_ciws(mesh, spec, 0.62, color_fn)
    if spec.ciws_aft:
        _add_ciws(mesh, spec, spec.stern + 0.28, color_fn)
    outline = planform_outline("sym-edge", _deck_planform(spec), lambda _x, _y: spec.deck_z)
    return attach_mesh_group(
        parent, name, mesh.to_geom("sym-fill"), color, scale=spec.scale, outline=outline
    )


def attach_corvette_light(parent: NodePath, name: str, color: Color) -> NodePath:
    return _attach(parent, name, color, LIGHT)


def attach_corvette_standard(parent: NodePath, name: str, color: Color) -> NodePath:
    return _attach(parent, name, color, STANDARD)
