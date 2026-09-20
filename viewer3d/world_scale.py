"""One multiplier tying hand-tuned world-unit presentation constants to map size.

Every camera speed, follow distance, glyph size and altitude in the viewer was
chosen against the PoC 100x100 battlespace. Those numbers are in world units,
so on a 5000x5000 map they silently become 50x too small: the free camera
crawls, chase sits inside its jet, and a 1.85-unit target glyph lands on well
under a pixel from any framing that shows the map.

Scaling them by the map's diagonal over the PoC diagonal keeps the viewer
looking and handling the way it always has, at any battlespace size. PoC-sized
maps get a multiplier of exactly 1.0, so nothing about them changes.

Nothing in ``wraithnet/`` imports this -- it is presentation only, and never
feeds back into the simulation.
"""

from __future__ import annotations

import math

#: Battlespace the viewer's world-unit constants were tuned against.
REFERENCE_MAP_DIAGONAL: float = math.hypot(100.0, 100.0)


def map_scale(grid_width: float, grid_height: float) -> float:
    """Multiplier for world-unit presentation distances on this map.

    Never drops below 1.0: a map smaller than the PoC should not shrink glyphs
    and camera speeds further, it should keep the tuned ones.
    """
    diag = math.hypot(float(grid_width), float(grid_height))
    if diag <= 0.0:
        return 1.0
    return max(1.0, diag / REFERENCE_MAP_DIAGONAL)


def header_map_scale(header) -> float:
    """``map_scale`` for a TraceHeader, tolerating a missing/partial header."""
    if header is None:
        return 1.0
    return map_scale(
        getattr(header, "grid_width", 100.0),
        getattr(header, "grid_height", 100.0),
    )
