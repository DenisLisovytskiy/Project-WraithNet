"""Friend / enemy paint for procedural meshes.

Silhouette tells you *what* a unit is. These colors tell you *whose* it is:
cool teal-green for friendlies, rust-red for hostiles. Jet variants stay
inside the friendly family so multiple aircraft remain distinguishable.
"""

from __future__ import annotations

Color = tuple[float, float, float, float]

FRIENDLY: Color = (0.32, 0.82, 0.62, 1.0)
ENEMY: Color = (0.86, 0.34, 0.26, 1.0)

FRIENDLY_VARIANTS: tuple[Color, ...] = (
    (0.30, 0.78, 0.90, 1.0),  # cyan
    (0.28, 0.84, 0.56, 1.0),  # teal-green
    (0.45, 0.80, 0.74, 1.0),  # mint
)


def faction_paint(faction: str, *, variant: int = 0) -> Color:
    if str(faction or "").upper() == "FRIENDLY":
        return FRIENDLY_VARIANTS[int(variant) % len(FRIENDLY_VARIANTS)]
    return ENEMY


def friendly_variant(index: int) -> Color:
    return FRIENDLY_VARIANTS[int(index) % len(FRIENDLY_VARIANTS)]
