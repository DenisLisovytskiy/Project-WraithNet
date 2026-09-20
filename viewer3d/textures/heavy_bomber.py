"""Procedural flying-wing mesh for HEAVY_BOMBER.

Inspired by the concept art: cranked-kite stealth bomber, buried engines,
serrated trailing edge, no vertical tail. Paint follows the same JPEG — the
darkest, flattest airframe of the three: near-black navy, almost no
spine-to-tip ramp, a cold blue sheen on the outer panels rather than a hot
leading edge. Vertex-colored only — no JPEG at render time.
"""

from __future__ import annotations

from panda3d.core import NodePath

from viewer3d.textures.geom import (
    MESH_SCALE,
    Color,
    MeshBuilder,
    add_prism,
    add_slab,
    add_solid_planform,
    attach_mesh_group,
    heat_fn,
    palette_color_fn,
    planform_outline,
    stealth_fn,
)

# Wider than it is long — the silhouette that separates this from a fighter.
_SCALE = MESH_SCALE * 1.08

# From assets/textures/pics/heavy_bomber.jpeg: near-black navy skin, faint blue
# panel sheen, cold shielded exhausts (no visible flame on a stealth bomber).
BODY = (0.07, 0.085, 0.115, 1.0)
GLASS = (0.20, 0.31, 0.44, 1.0)
IRON = (0.12, 0.12, 0.14, 1.0)
SCORCH = (0.30, 0.22, 0.20, 1.0)


def _mirror(left: list[tuple[float, float]]) -> list[tuple[float, float]]:
    right = [(-x, y) for x, y in reversed(left[1:-1])]
    return [*left, *right]


# Convex kite (fan-safe). Teeth and engine beaks are added as extra prisms.
_LEFT = [
    (0.00, 1.16),
    (-0.30, 0.88),
    (-0.78, 0.32),
    (-1.28, -0.04),
    (-1.68, -0.32),
    (-1.18, -0.50),
    (-0.52, -0.46),
    (0.00, -0.50),
]
PLANFORM = _mirror(_LEFT)


def _upper_z(x: float, y: float) -> float:
    span = max(0.0, 1.0 - abs(x) / 1.72)
    body = max(0.0, 1.0 - ((y - 0.08) ** 2) / 1.55)
    z = 0.028 + 0.13 * (span**1.55) * (0.42 + 0.58 * body)
    # Shallow cockpit blister.
    if abs(x) < 0.15 and 0.50 < y < 0.94:
        t = 1.0 - abs(y - 0.72) / 0.22
        z += 0.09 * max(t, 0.0) * (1.0 - abs(x) / 0.15)
    # Twin buried-engine humps.
    ax = abs(x)
    if 0.06 < ax < 0.34 and -0.38 < y < 0.22:
        t = 1.0 - abs(ax - 0.18) / 0.16
        s = 1.0 - abs(y + 0.06) / 0.30
        z += 0.075 * max(t, 0.0) * max(s, 0.0)
    return z


def _lower_z(x: float, y: float) -> float:
    span = max(0.0, 1.0 - abs(x) / 1.72)
    return -0.018 - 0.065 * span * span


def _add_saw_teeth(mesh: MeshBuilder, color_fn) -> None:
    """Aft-pointing serrations on the outer trailing edge (both wings)."""
    teeth = (
        ((-1.68, -0.32), (-1.46, -0.62), (-1.18, -0.50)),
        ((-1.18, -0.50), (-0.88, -0.66), (-0.52, -0.46)),
    )
    for a, b, c in teeth:
        for sx in (-1.0, 1.0):
            top = [
                (sx * a[0], a[1], _upper_z(sx * a[0], a[1])),
                (sx * b[0], b[1], 0.012),
                (sx * c[0], c[1], _upper_z(sx * c[0], c[1])),
            ]
            bot = [
                (sx * a[0], a[1], _lower_z(sx * a[0], a[1])),
                (sx * b[0], b[1], -0.012),
                (sx * c[0], c[1], _lower_z(sx * c[0], c[1])),
            ]
            add_prism(mesh, top, bot, color_fn)


def _add_engine_beaks(mesh: MeshBuilder, color_fn) -> None:
    """W-notch exhaust paddles hanging off the center trailing edge."""
    for sx in (-1.0, 1.0):
        top = [
            (0.00, -0.50, _upper_z(0.0, -0.50)),
            (sx * 0.28, -0.48, _upper_z(sx * 0.28, -0.48)),
            (sx * 0.14, -0.84, 0.06),
        ]
        bot = [
            (0.00, -0.50, _lower_z(0.0, -0.50)),
            (sx * 0.28, -0.48, _lower_z(sx * 0.28, -0.48)),
            (sx * 0.14, -0.84, -0.02),
        ]
        add_prism(mesh, top, bot, color_fn)


def _add_details(mesh: MeshBuilder, color_fn, dark_fn) -> None:
    glass_fn = palette_color_fn(GLASS, extra_dark=0.15)

    # Cockpit glass, sitting on the blister.
    add_slab(
        mesh,
        [
            (-0.08, 0.90, 0.16),
            (0.08, 0.90, 0.16),
            (0.07, 0.54, 0.18),
            (-0.07, 0.54, 0.18),
        ],
        (0.0, 0.0, 0.055),
        glass_fn,
    )
    # Dorsal engine fairings.
    for sx in (-1.0, 1.0):
        add_slab(
            mesh,
            [
                (sx * 0.07, 0.16, 0.155),
                (sx * 0.30, 0.14, 0.13),
                (sx * 0.26, -0.40, 0.11),
                (sx * 0.07, -0.46, 0.13),
            ],
            (0.0, 0.0, 0.065),
            color_fn,
        )
        # Intake lip (dark).
        add_slab(
            mesh,
            [
                (sx * 0.09, 0.20, 0.20),
                (sx * 0.26, 0.18, 0.18),
                (sx * 0.24, 0.08, 0.18),
                (sx * 0.09, 0.10, 0.20),
            ],
            (0.0, 0.04, 0.02),
            dark_fn,
        )
        # Exhaust face (dark).
        add_slab(
            mesh,
            [
                (sx * 0.06, -0.80, 0.07),
                (sx * 0.20, -0.82, 0.05),
                (sx * 0.18, -0.86, 0.02),
                (sx * 0.06, -0.84, 0.03),
            ],
            (0.0, 0.03, 0.0),
            heat_fn(IRON, SCORCH, -0.80, -0.86),
        )


def _outline():
    saw = [
        (0.00, 1.16),
        (-0.30, 0.88),
        (-0.78, 0.32),
        (-1.28, -0.04),
        (-1.68, -0.32),
        (-1.46, -0.62),
        (-1.18, -0.50),
        (-0.88, -0.66),
        (-0.52, -0.46),
        (-0.28, -0.50),
        (-0.14, -0.84),
        (0.00, -0.60),
    ]
    return _mirror(saw)


def attach_heavy_bomber(parent: NodePath, name: str, color: Color) -> NodePath:
    """Non-billboarded flying wing. ``color`` is the jet palette tint."""
    mesh = MeshBuilder()
    color_fn = stealth_fn(
        BODY, color, span=1.72, spine=0.34, glow=0.48, tint_mix=0.20, extra_dark=0.06
    )
    dark_fn = stealth_fn(
        BODY, color, span=1.72, spine=0.34, glow=0.0, tint_mix=0.20, extra_dark=0.26
    )
    add_solid_planform(mesh, PLANFORM, _upper_z, _lower_z, color_fn)
    _add_saw_teeth(mesh, color_fn)
    _add_engine_beaks(mesh, dark_fn)
    _add_details(mesh, color_fn, dark_fn)
    outline = planform_outline("sym-edge", _outline(), lambda x, y: _upper_z(x, y))
    return attach_mesh_group(
        parent,
        name,
        mesh.to_geom("sym-fill"),
        color,
        scale=_SCALE,
        outline=outline,
        vertex_colors=True,
    )
