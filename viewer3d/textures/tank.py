"""Friendly/enemy tanks — compact light cavalry tank vs heavier standard MBT.

Chamfered hull, road wheels, bustle turret, shattered armor plates.
"""

from __future__ import annotations

from dataclasses import dataclass

from panda3d.core import NodePath

from viewer3d.textures.geom import (
    Color,
    MeshBuilder,
    add_box,
    add_box_shattered,
    add_loft,
    add_slab,
    attach_mesh_group,
    box_ring,
    mix_color,
    paint_fn,
    planform_outline,
)


@dataclass(frozen=True)
class TankSpec:
    scale: float
    hull_l: float
    hull_w: float
    hull_h: float
    turret_l: float
    turret_w: float
    turret_h: float
    gun_l: float


LIGHT = TankSpec(1.58, 1.08, 0.62, 0.42, 0.34, 0.40, 0.30, 0.62)
STANDARD = TankSpec(1.78, 1.26, 0.74, 0.50, 0.42, 0.50, 0.36, 0.95)

RUBBER = (0.10, 0.10, 0.09, 1.0)
STEEL = (0.16, 0.16, 0.14, 1.0)


def _armor_shards(paint: Color) -> tuple[Color, ...]:
    return (
        mix_color((0.22, 0.26, 0.14, 1.0), paint, 0.42),
        mix_color((0.14, 0.16, 0.10, 1.0), paint, 0.28),
        mix_color((0.32, 0.30, 0.18, 1.0), paint, 0.35),
        mix_color(STEEL, paint, 0.18),
        mix_color((0.40, 0.28, 0.16, 1.0), paint, 0.50),
    )


def _attach(parent: NodePath, name: str, color: Color, spec: TankSpec) -> NodePath:
    mesh = MeshBuilder()
    armor = mix_color((0.20, 0.24, 0.14, 1.0), color, 0.40)
    fn = paint_fn(armor)
    dark = paint_fn(mix_color(RUBBER, color, 0.12), extra_dark=0.08)
    shards = _armor_shards(color)
    hl, hw, hh = spec.hull_l * 0.5, spec.hull_w * 0.5, spec.hull_h
    z_mid = 0.18 + hh * 0.42
    add_loft(
        mesh,
        [
            box_ring(-hl, hw * 0.92, hh * 0.48, z=z_mid),
            box_ring(-hl * 0.35, hw, hh * 0.55, z=z_mid + 0.02),
            box_ring(hl * 0.20, hw * 0.96, hh * 0.50, z=z_mid + 0.06),
            box_ring(hl, hw * 0.72, hh * 0.32, z=z_mid + 0.02),
        ],
        fn,
        upper_hint=lambda p: p[2] >= z_mid - 0.04,
    )
    add_box_shattered(
        mesh,
        -hw * 0.90,
        hw * 0.90,
        -hl * 0.82,
        hl * 0.70,
        z_mid - hh * 0.12,
        z_mid + hh * 0.42,
        shards,
        cols=5,
        rows=2,
        include_top=True,
        jitter=0.48,
        skip=0.12,
        seed=5,
    )
    add_box(mesh, -hw - 0.12, -hw + 0.02, -hl + 0.04, hl - 0.06, 0.0, 0.26, dark)
    add_box(mesh, hw - 0.02, hw + 0.12, -hl + 0.04, hl - 0.06, 0.0, 0.26, dark)
    for fy in (-0.72, -0.40, -0.08, 0.24, 0.54):
        y = fy * hl
        add_box(mesh, -hw - 0.14, -hw - 0.02, y - 0.09, y + 0.09, 0.02, 0.24, dark)
        add_box(mesh, hw + 0.02, hw + 0.14, y - 0.09, y + 0.09, 0.02, 0.24, dark)
    tw, tl, th = spec.turret_w * 0.5, spec.turret_l * 0.5, spec.turret_h
    z0 = 0.16 + hh
    y_off = 0.06
    add_box(mesh, -tw, tw, -tl + y_off, tl + y_off, z0, z0 + th, fn)
    add_box_shattered(
        mesh,
        -tw,
        tw,
        -tl + y_off,
        tl + y_off,
        z0,
        z0 + th,
        shards,
        cols=4,
        rows=2,
        include_top=True,
        jitter=0.46,
        skip=0.10,
        seed=19,
    )
    add_box(
        mesh,
        -tw * 0.78,
        tw * 0.78,
        -tl + y_off - 0.16,
        -tl + y_off + 0.04,
        z0 + 0.04,
        z0 + th * 0.82,
        dark,
    )
    add_box(
        mesh,
        -tw * 0.62,
        tw * 0.62,
        tl + y_off - 0.02,
        tl + y_off + 0.14,
        z0 + th * 0.12,
        z0 + th * 0.88,
        dark,
    )
    gz = z0 + th * 0.48
    gun_y0 = tl + y_off + 0.12
    add_slab(
        mesh,
        [
            (-0.055, gun_y0, gz + 0.048),
            (0.055, gun_y0, gz + 0.048),
            (0.038, gun_y0 + spec.gun_l, gz + 0.028),
            (-0.038, gun_y0 + spec.gun_l, gz + 0.028),
        ],
        (0.0, 0.0, 0.090),
        dark,
    )
    fx = gun_y0 + spec.gun_l * 0.55
    add_box(mesh, -0.07, 0.07, fx - 0.06, fx + 0.06, gz - 0.04, gz + 0.08, fn)
    pad = [
        (-hw - 0.14, -hl),
        (hw + 0.14, -hl),
        (hw + 0.14, hl),
        (-hw - 0.14, hl),
    ]
    outline = planform_outline("sym-edge", pad, lambda *_: 0.16 + hh)
    return attach_mesh_group(
        parent,
        name,
        mesh.to_geom("sym-fill"),
        color,
        scale=spec.scale,
        outline=outline,
        vertex_colors=True,
    )


def attach_mbt_light(parent: NodePath, name: str, color: Color) -> NodePath:
    return _attach(parent, name, color, LIGHT)


def attach_mbt_standard(parent: NodePath, name: str, color: Color) -> NodePath:
    return _attach(parent, name, color, STANDARD)
