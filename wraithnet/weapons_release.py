"""Weapon release attitude envelope checks (Phase 52 / docs/22 §6).

Phase 53: ``needed_attitude_for_release`` — pure delivery-attitude command
used by ``decide_intent`` when lining up a legal bomb/ASM pass.
"""

from __future__ import annotations

from dataclasses import dataclass

from wraithnet.config import WeaponSpec
from wraithnet.geometry import normalize_roll_deg


@dataclass(frozen=True)
class AttitudeCommand:
    """Desired pitch/roll for a legal weapon release (degrees)."""

    desired_pitch_deg: float
    desired_roll_deg: float


def release_attitude_ok(
    weapon: WeaponSpec,
    *,
    pitch_deg: float,
    roll_deg: float,
    altitude: float | None = None,
) -> tuple[bool, str | None]:
    """Return ``(ok, reason_code)``. ``reason_code`` is None when ok.

    Stable rejection codes:
    ``outside_release_attitude_roll`` |
    ``outside_release_attitude_pitch`` |
    ``jet_inverted_release_forbidden`` |
    ``above_max_release_altitude``

    Inverted uses the Phase 50 signed-roll convention: ``|roll| > 90``.
    ``altitude`` None skips the altitude gate (attitude-only callers).
    """
    if (
        altitude is not None
        and weapon.max_release_altitude is not None
        and altitude > weapon.max_release_altitude
    ):
        return False, "above_max_release_altitude"
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


#: Fly this far under ``max_release_altitude`` so ease overshoot still releases.
RELEASE_ALTITUDE_MARGIN: float = 2.0


def attack_altitude(weapon: WeaponSpec, transit_altitude: float) -> float:
    """Altitude for the attack run: transit unless the weapon must come down."""
    if weapon.max_release_altitude is None:
        return transit_altitude
    return min(transit_altitude, weapon.max_release_altitude - RELEASE_ALTITUDE_MARGIN)


def release_reachable(weapon: WeaponSpec, min_overflight_altitude: float) -> bool:
    """Can a jet get low enough over the target to release this weapon?

    Terrain (roofs/treetops around the target) can hold jets above an
    unguided weapon's release ceiling; such attacks would orbit forever.
    """
    if weapon.max_release_altitude is None:
        return True
    # The run aims RELEASE_ALTITUDE_MARGIN under the ceiling but holds the
    # terrain floor when that is higher; release only needs <= the ceiling.
    return min_overflight_altitude <= weapon.max_release_altitude


def weapon_has_attitude_envelope(weapon: WeaponSpec) -> bool:
    """True when the weapon gates release on roll and/or pitch."""
    return bool(
        weapon.requires_upright
        or weapon.max_release_abs_roll_deg is not None
        or weapon.max_release_abs_pitch_deg is not None
    )


def needed_attitude_for_release(
    weapon: WeaponSpec,
    current_pitch_deg: float,
    current_roll_deg: float,
) -> AttitudeCommand:
    """Command attitude that moves the jet into a legal release envelope.

    Policy (Phase 53 / docs/22 §7):
    * Roll toward wings-level (0°).
    * Pitch toward a mild delivery attitude inside the weapon's pitch cap;
      level ``0`` is always acceptable when the weapon has no pitch gate, and
      is clamped into ``±max_release_abs_pitch_deg`` when it does.

    ``current_*`` are accepted so callers/tests can reason about the delta
    without requiring the full agent loop; the command itself is envelope-
    driven (not a proportional controller).
    """
    _ = (current_pitch_deg, current_roll_deg)  # envelope target, not P-controller
    delivery_pitch = 0.0
    if weapon.max_release_abs_pitch_deg is not None:
        cap = abs(float(weapon.max_release_abs_pitch_deg))
        delivery_pitch = max(-cap, min(cap, delivery_pitch))
    return AttitudeCommand(
        desired_pitch_deg=delivery_pitch,
        desired_roll_deg=0.0,
    )
