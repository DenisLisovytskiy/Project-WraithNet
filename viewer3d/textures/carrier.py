"""Procedural aircraft carriers — pocket escort vs angled-deck fleet carrier.

Straight short deck + small island versus a longer, wider, angled deck with
catapults, blast deflectors, and a taller island. Vertex-colored only.
"""

from __future__ import annotations

from dataclasses import dataclass

from panda3d.core import NodePath

from viewer3d.textures.geom import (
    Color,
    MeshBuilder,
    add_loft,
    add_slab,
    add_solid_planform,
    attach_mesh_group,
    hull_ring,
    palette_color_fn,
    planform_outline,
)


@dataclass(frozen=True)
class CarrierSpec:
    scale: float
    deck: list[tuple[float, float]]
    hull_bow: float
    hull_stern: float
    hull_beam: float
    deck_z: float
    keel_z: float
    island_y: float
    island_x: float
    island_len: float
    island_w: float
    island_h: float
    angled: bool
    catapults: int


# Straight pocket deck.
ESCORT = CarrierSpec(
    scale=1.62,
    deck=[(-0.38, 1.22), (0.38, 1.22), (0.40, -1.22), (-0.40, -1.22)],
    hull_bow=1.12,
    hull_stern=-1.18,
    hull_beam=0.62,
    deck_z=0.10,
    keel_z=-0.14,
    island_y=-0.05,
    island_x=0.28,
    island_len=0.42,
    island_w=0.16,
    island_h=0.22,
    angled=False,
    catapults=0,
)
# Longer / wider angled deck, bigger island, two cats.
FLEET = CarrierSpec(
    scale=2.18,
    deck=[
        (0.08, 1.72),
        (0.50, 1.42),
        (0.56, -0.15),
        (0.44, -1.78),
        (-0.54, -1.70),
        (-0.60, 0.35),
        (-0.40, 1.55),
    ],
    hull_bow=1.48,
    hull_stern=-1.68,
    hull_beam=0.82,
    deck_z=0.12,
    keel_z=-0.18,
    island_y=-0.10,
    island_x=0.38,
    island_len=0.62,
    island_w=0.22,
    island_h=0.34,
    angled=True,
    catapults=2,
)


def _deck_z(_x: float, _y: float, spec: CarrierSpec) -> float:
    return spec.deck_z + 0.012


def _deck_z_lo(_x: float, _y: float, spec: CarrierSpec) -> float:
    return spec.deck_z - 0.01


def _add_hull(mesh: MeshBuilder, spec: CarrierSpec, color_fn) -> None:
    b, d, k = spec.hull_beam, spec.deck_z * 0.55, spec.keel_z
    rings = [
        hull_ring(spec.hull_bow, b * 0.22, d * 0.70, k * 0.40),
        hull_ring(spec.hull_bow * 0.55, b * 0.78, d * 0.92, k * 0.85),
        hull_ring(0.10, b, d, k),
        hull_ring(spec.hull_stern * 0.45, b * 0.95, d, k),
        hull_ring(spec.hull_stern, b * 0.70, d * 0.85, k * 0.70),
    ]
    add_loft(mesh, rings, color_fn, cap=True)


def _add_island(mesh: MeshBuilder, spec: CarrierSpec, color_fn, dark_fn) -> None:
    x = spec.island_x
    y0 = spec.island_y + spec.island_len * 0.5
    y1 = spec.island_y - spec.island_len * 0.5
    hw = spec.island_w * 0.5
    z = spec.deck_z
    add_slab(
        mesh,
        [
            (x - hw, y0, z + spec.island_h),
            (x + hw, y0, z + spec.island_h),
            (x + hw * 0.9, y1, z + spec.island_h * 0.9),
            (x - hw * 0.9, y1, z + spec.island_h * 0.9),
        ],
        (0.0, 0.0, spec.island_h),
        color_fn,
    )
    # Bridge / radar top.
    add_slab(
        mesh,
        [
            (x - hw * 0.7, y0 - 0.04, z + spec.island_h + 0.10),
            (x + hw * 0.7, y0 - 0.04, z + spec.island_h + 0.10),
            (x + hw * 0.55, y0 - spec.island_len * 0.45, z + spec.island_h + 0.08),
            (x - hw * 0.55, y0 - spec.island_len * 0.45, z + spec.island_h + 0.08),
        ],
        (0.0, 0.0, 0.10),
        color_fn,
    )
    mast_h = 0.22 if spec.angled else 0.14
    add_slab(
        mesh,
        [
            (x - 0.02, spec.island_y - 0.03, z + spec.island_h + mast_h),
            (x + 0.02, spec.island_y - 0.03, z + spec.island_h + mast_h),
            (x + 0.02, spec.island_y + 0.03, z + spec.island_h + mast_h),
            (x - 0.02, spec.island_y + 0.03, z + spec.island_h + mast_h),
        ],
        (0.0, 0.0, mast_h),
        dark_fn,
    )


def _add_deck_gear(mesh: MeshBuilder, spec: CarrierSpec, dark_fn, color_fn) -> None:
    z = spec.deck_z + 0.018
    if spec.catapults:
        for i, x in enumerate((-0.18, 0.08)):
            y_bow = 1.45 - i * 0.08
            add_slab(
                mesh,
                [
                    (x - 0.025, y_bow, z),
                    (x + 0.025, y_bow, z),
                    (x + 0.04, -0.85, z),
                    (x - 0.01, -0.85, z),
                ],
                (0.0, 0.0, 0.012),
                dark_fn,
            )
        # Blast deflectors near the bow.
        add_slab(
            mesh,
            [
                (-0.28, 1.05, z + 0.04),
                (-0.08, 1.05, z + 0.04),
                (-0.08, 1.18, z + 0.04),
                (-0.28, 1.18, z + 0.04),
            ],
            (0.0, 0.0, 0.05),
            color_fn,
        )
        add_slab(
            mesh,
            [
                (0.00, 0.98, z + 0.04),
                (0.20, 0.98, z + 0.04),
                (0.20, 1.12, z + 0.04),
                (0.00, 1.12, z + 0.04),
            ],
            (0.0, 0.0, 0.05),
            color_fn,
        )
    else:
        # Single elevator on the escort.
        add_slab(
            mesh,
            [
                (-0.16, 0.15, z),
                (0.10, 0.15, z),
                (0.10, 0.42, z),
                (-0.16, 0.42, z),
            ],
            (0.0, 0.0, 0.01),
            dark_fn,
        )
        # Arresting chevrons at the stern.
        for y in (-0.92, -1.02, -1.12):
            add_slab(
                mesh,
                [
                    (-0.18, y - 0.015, z),
                    (0.18, y - 0.015, z),
                    (0.18, y + 0.015, z),
                    (-0.18, y + 0.015, z),
                ],
                (0.0, 0.0, 0.008),
                color_fn,
            )


def _attach(parent: NodePath, name: str, color: Color, spec: CarrierSpec) -> NodePath:
    mesh = MeshBuilder()
    color_fn = palette_color_fn(color)
    dark_fn = palette_color_fn(color, extra_dark=0.45)
    deck_fn = palette_color_fn(color, extra_dark=0.22)
    _add_hull(mesh, spec, color_fn)
    add_solid_planform(
        mesh,
        spec.deck,
        lambda x, y: _deck_z(x, y, spec),
        lambda x, y: _deck_z_lo(x, y, spec),
        deck_fn,
    )
    _add_island(mesh, spec, color_fn, dark_fn)
    _add_deck_gear(mesh, spec, dark_fn, color_fn)
    outline = planform_outline(
        "sym-edge", spec.deck, lambda x, y: _deck_z(x, y, spec)
    )
    return attach_mesh_group(
        parent, name, mesh.to_geom("sym-fill"), color, scale=spec.scale, outline=outline
    )


def attach_escort_carrier(parent: NodePath, name: str, color: Color) -> NodePath:
    return _attach(parent, name, color, ESCORT)


def attach_fleet_carrier(parent: NodePath, name: str, color: Color) -> NodePath:
    return _attach(parent, name, color, FLEET)
