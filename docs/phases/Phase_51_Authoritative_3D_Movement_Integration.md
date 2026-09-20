# Phase 51 — Authoritative 3D Movement Integration

> **LLM implementation prompt:** Assume Phase 50 is complete and merged. Read `docs/22_Authoritative_3D_Flight_Attitude_Design_Document.md` §§3–5, 9 before coding. This phase makes pitch/roll/yaw **drive movement** on discrete ticks. It does **not** add weapon attitude gates (Phase 52), agent tactics (Phase 53), or viewer trace fields (Phase 54) except where unavoidable for compilation.

## Goal

Replace “yaw + independent altitude slider” as the primary combat flight path with **rate-limited attitude** and **pitch-coupled altitude**, while preserving map clamps, RTB/carrier recovery floors, and nose-along-track XY motion.

## Part A — attitude rate application

In `wraithnet/world.py`, alongside `_apply_heading_rotation`:

1. Apply `desired_pitch_deg` capped by `pitch_rate_deg_per_tick` and `pitch_limit_deg`.
2. Apply `desired_roll_deg` capped by `roll_rate_deg_per_tick` and `roll_limit_deg`.
3. Keep yaw behavior via existing `turn_rate_deg_per_tick`.

Unset desired axis ⇒ hold current (same as heading today).

Wire these into the same staging points that already apply heading before/with movement (`stage_pre_move_obs` / `stage_apply_movement` — match existing order; document if you change order).

## Part B — translation + altitude coupling

Update movement so that each tick:

1. **Yaw** still defines the ground-track nose direction (existing convention).
2. **Pitch** contributes vertical change; clamp by `max_climb_per_tick`.
3. **XY** advance remains nose-along-track; choose and document one of:
   * **A (recommended):** ground speed ≈ `max_speed * cos(pitch)` with `dz ≈ max_speed * sin(pitch)` then climb-clamped, or
   * **B:** full `max_speed` on XY plus separate pitch-derived `dz` (simpler, less “energy”).

Do not crab-slide.

### Recovery / `desired_altitude`

Implement the design-doc recommendation:

* **Combat:** pitch is primary; if both `desired_pitch` and `desired_altitude` are set, document precedence (recommend: pitch wins in combat).
* **RTB / carrier recovery:** existing altitude floor / deck logic remains; you may ease altitude directly on short final while driving pitch toward 0. Cover with at least one recovery test.

Remove or narrow paths that change altitude with **no** attitude change in normal cruise — avoid two competing authorities without a written rule.

## Part C — optional bank assist

If `desired_roll_deg is None` and the jet is actively yawing toward a heading, optionally set an assisted roll from yaw error / commanded yaw step (clamped). If implemented, expose a constant for gain and test that explicit `desired_roll_deg` overrides assist.

## Part D — geometry / release pose prep

Extend release helpers so later phases can freeze attitude:

* Prefer renaming/extending `_weapon_release_xy` toward `_weapon_release_pose` returning `(x, y, altitude, heading, pitch, roll)` at pre-move commit.
* Keep XY-only callers working via a thin wrapper if needed.

No weapon rejection yet.

## Part E — tests

Add/extend tests (e.g. `tests/test_flight_movement_attitude.py`):

1. Positive pitch over N ticks increases altitude (until cruise/limit/climb clamp).
2. Negative pitch decreases altitude but not below `min_combat_altitude` (except documented recovery exception).
3. Pitch rate: a large desired pitch is not reached in one tick if rate < delta (Interceptor reaches faster than Bomber for same command — comparative test).
4. Roll rate comparative test across frames.
5. Yaw inequalities still hold; existing heading tests updated if needed.
6. Map-edge clamp still safe (no Mesa out-of-bounds).
7. Wings-level cruise with no attitude intents preserves prior XY cruise behavior as closely as practical (snapshot test or distance-per-tick check).

## Non-goals

* Weapon `requires_upright` gates (Phase 52).
* `decide_intent` dive/roll policies (Phase 53).
* Trace/viewer pitch-roll streaming (Phase 54).
* Fuel burn tied to maneuvering.
* Stall / G / continuous physics.

## Done means (Phase 51)

Phase 51 is **done** when:

1. Commanded pitch/roll/yaw are rate-limited per airframe and visible on `JetAgent` after ticks.
2. Pitch changes produce altitude changes under climb clamps; altitude is no longer only an uncoupled slider for combat flight.
3. XY motion remains nose-along-track; recovery floors still prevent underground / illegal deck clips.
4. Pre-move release pose can expose attitude for Phase 52.
5. New movement tests pass; full `pytest` green. If PoC summarize shifts, **stop** and either adjust defaults toward wings-level cruise compatibility or explicitly defer golden updates to Phase 55 with a note in the PR — do not silently leave red tests.
