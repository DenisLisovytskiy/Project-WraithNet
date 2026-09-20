# Phase 52 — Weapon Release Attitude Gates

> **LLM implementation prompt:** Assume Phases 50–51 are complete (Phase 51 strongly recommended so release pose includes pitch/roll). Read `docs/22_Authoritative_3D_Flight_Attitude_Design_Document.md` §6 in full. This phase makes illegal attitudes fail `STRIKE`/`LAUNCH` checks. It does **not** teach agents to roll upright (Phase 53) or update the viewer (Phase 54).

## Goal

Weapon employment respects jet orientation. **Gravity-bomb-class releases refuse inverted / out-of-envelope attitudes**; some missiles remain launchable when bombs are not.

## Part A — catalog envelopes

Extend `WeaponSpec` in `wraithnet/config.py` with:

* `max_release_abs_roll_deg: float | None = None`
* `max_release_abs_pitch_deg: float | None = None`
* `requires_upright: bool = False`

Apply the policy table in design doc §6.2 (tunable numbers, fixed behaviors):

* `JDAM`: upright required + modest roll/pitch caps
* ASM-class AGMs: upright-ish caps
* `GUN_POD`: nose weapon — looser or pitch-focused caps; not inverted-forbidden unless you document why
* `AIM9X` / `AMRAAM`: no roll/pitch gate (`None` / `requires_upright=False`)
* SAM entries: unused for jet release; leave defaults

## Part B — shared check helper

Add a pure function (preferred location: `wraithnet/geometry.py` or `wraithnet/weapons_release.py`):

```python
def release_attitude_ok(
    weapon: WeaponSpec,
    *,
    pitch_deg: float,
    roll_deg: float,
) -> tuple[bool, str | None]:
    """Return (ok, reason_code). reason_code is None when ok.
    Stable codes: outside_release_attitude_roll |
    outside_release_attitude_pitch | jet_inverted_release_forbidden
    """
```

Define **inverted** using the Phase 50 roll convention (e.g. `|roll| > 90` for signed roll).

Use this helper from:

1. World `_apply_strike` / launch eligibility (authoritative).
2. Any agent pre-check that currently mirrors arc/range so intent does not spam doomed STRIKEs (light touch; full behavior is Phase 53).

## Part C — enforcement wiring

At the same decision point as range, firing arc, and LOS:

1. Build release pose including pitch/roll from pre-move state (Phase 51 helper).
2. If `release_attitude_ok` fails: do **not** consume ammo, do **not** deal damage, do **not** spawn a missile. Record a skip/reason if the codebase already audits failed fires; if not, a debug counter or strike-log absence is enough if tests assert no ammo change.

Horizontal `firing_arc_deg` checks remain. Do not replace them with attitude envelopes.

## Part D — tests

Add `tests/test_weapon_release_attitude.py`:

1. Jet inverted (`roll ≈ 180` or `|roll| > 90`) with JDAM in range/arc → strike rejected; ammo unchanged; target HP unchanged.
2. Same jet after roll returns to 0 within JDAM envelope → strike can succeed (construct a minimal world fixture; follow existing strike test style).
3. Jet at `|roll| = 120` can still `LAUNCH` AIM9X/AMRAAM if other launch gates pass (fixture with a valid air/ground-appropriate target per weapon rules — if AAM needs an airborne target and none exists, use the lightest legal fixture or assert only the attitude helper + launch gate unit test).
4. Pitch beyond `max_release_abs_pitch_deg` rejects JDAM.
5. `requires_upright=False` and `None` caps → helper returns ok for extreme roll.
6. Regression: wings-level JDAM still works under prior range/arc/LOS assumptions in unit fixtures.

## Non-goals

* Agent auto-upright policy (Phase 53).
* Changing damage, suitability, guidance flight, or flare/notch.
* Viewer bomb animation changes.
* CNP redesign.

## Done means (Phase 52)

Phase 52 is **done** when:

1. Weapon catalog carries release envelopes; JDAM refuses inverted release in authoritative world code.
2. Failed attitude checks consume no ammo and deal no damage.
3. At least one missile type remains attitude-unconstrained relative to JDAM (tests prove the contrast).
4. Pure helper is unit-tested without Mesa where possible; world integration tests cover the bomb reject path.
5. Full `pytest` green (PoC goldens: same rule as Phase 51 — fix compatibility or defer explicitly to Phase 55).
