"""Weapon release attitude envelope checks (Phase 52 / docs/22 §6)."""

from __future__ import annotations

from wraithnet.config import WeaponSpec
from wraithnet.geometry import normalize_roll_deg


def release_attitude_ok(
    weapon: WeaponSpec,
    *,
    pitch_deg: float,
    roll_deg: float,
) -> tuple[bool, str | None]:
    """Return ``(ok, reason_code)``. ``reason_code`` is None when ok.

    Stable rejection codes:
    ``outside_release_attitude_roll`` |
    ``outside_release_attitude_pitch`` |
    ``jet_inverted_release_forbidden``

    Inverted uses the Phase 50 signed-roll convention: ``|roll| > 90``.
    """
    roll = normalize_roll_deg(roll_deg)
    if weapon.requires_upright and abs(roll) > 90.0:
        return False, "jet_inverted_release_forbidden"
    if weapon.max_release_abs_roll_deg is not None:
        if abs(roll) > weapon.max_release_abs_roll_deg:
            return False, "outside_release_attitude_roll"
    if weapon.max_release_abs_pitch_deg is not None:
        if abs(pitch_deg) > weapon.max_release_abs_pitch_deg:
            return False, "outside_release_attitude_pitch"
    return True, None
