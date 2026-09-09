"""Stable 64-bit seed derivation for deterministic scenario generation.

Uses only integer mixing — never Python's randomized ``hash()`` — so the same
``(seed, salt)`` pair yields the same derived seed across processes and
interpreter invocations.
"""

from __future__ import annotations

# Distinct salts for named RNG streams. Values are arbitrary fixed constants;
# changing them intentionally reshuffles every derived stream.
PRESET_SALT: int = 0x50524553_4554_0001  # "PRESET" + tag
ENEMY_SALT: int = 0x454E454D_5900_0002  # "ENEMY" + tag
FRIENDLY_SALT: int = 0x46524945_4E44_0003  # "FRIEND" + tag
TERRAIN_SALT: int = 0x54455252_4149_0004  # "TERRA" + tag
ENEMY_NAVAL_SALT: int = 0x454E4156_0005  # enemy naval stream
FRIENDLY_GROUND_SALT: int = 0x4652474E_0006  # friendly ground / tanks
ENEMY_GROUND_SALT: int = 0x4547524E_0007  # enemy ground / tanks
LOADOUT_SALT: int = 0x4C4F4144_0008  # loadout stream
GENERATOR_VERSION: int = 3

_MASK64: int = (1 << 64) - 1
# SplitMix64 finalizer constants (Steele / Lea / Hacker's Delight lineage).
_SPLITMIX_INCREMENT: int = 0x9E3779B97F4A7C15
_SPLITMIX_MUL1: int = 0xBF58476D1CE4E5B9
_SPLITMIX_MUL2: int = 0x94D049BB133111EB


def derived_seed(seed: int, salt: int) -> int:
    """Mix ``seed`` with ``salt`` into a stable unsigned 64-bit integer.

    The result is suitable for ``random.Random(...)`` construction. Both inputs
    are reduced modulo 2**64 before mixing.
    """
    z = ((int(seed) & _MASK64) ^ (int(salt) & _MASK64)) & _MASK64
    z = (z + _SPLITMIX_INCREMENT) & _MASK64
    z = ((z ^ (z >> 30)) * _SPLITMIX_MUL1) & _MASK64
    z = ((z ^ (z >> 27)) * _SPLITMIX_MUL2) & _MASK64
    return (z ^ (z >> 31)) & _MASK64
