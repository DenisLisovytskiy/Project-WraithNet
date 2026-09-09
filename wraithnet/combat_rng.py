"""Deterministic keyed combat RNG (Phase 38).

Combat outcomes must be reproducible from ``ScenarioConfig.combat_seed`` plus
an event identity, independent of dict iteration order. Do not use Python
``hash()`` or a shared ``random.Random`` stream for combat rolls.
"""

from __future__ import annotations

import hashlib
from typing import Any


def keyed_unit_float(combat_seed: int, *parts: Any) -> float:
    """Return a float in ``[0, 1)`` derived from ``combat_seed`` and ``parts``.

    Parts are joined with a NUL separator after ``str()`` conversion so the
    digest is order-sensitive only to the caller's argument order — callers
    must pass a stable identity (e.g. sorted missile fields), not an
    insertion-ordered dict walk.
    """
    payload = "\0".join(str(p) for p in (combat_seed, *parts))
    digest = hashlib.sha256(payload.encode("utf-8")).digest()
    # 53 bits → IEEE float mantissa-safe uniform in [0, 1)
    n = int.from_bytes(digest[:7], "big")  # 56 bits; mask to 53
    n &= (1 << 53) - 1
    return n / float(1 << 53)
