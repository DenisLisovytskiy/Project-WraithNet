# Phase 50 — Attitude Contracts & Airframe Rate Catalog

> **LLM implementation prompt:** Assume Phases 0–49 are present on `main` as of the design branch base. Read `docs/22_Authoritative_3D_Flight_Attitude_Design_Document.md` §§0–5, 9–11 before coding. This phase **defines data contracts and catalog numbers only**. It does **not** change movement integration, weapon gates, agent tactics, or the 3D viewer beyond compiling/type-default fallout.

## Goal

Introduce authoritative attitude fields and per-airframe pitch/roll rate limits so later phases can implement 3D flight without renegotiating schemas.

## Part A — `JetFrameSpec` catalog

In `wraithnet/config.py`, extend `JetFrameSpec` with at least:

* `pitch_rate_deg_per_tick: float`
* `roll_rate_deg_per_tick: float`
* `pitch_limit_deg: float` (symmetric ± limit unless you document asymmetry)
* `roll_limit_deg: float` (max |roll| the airframe may hold; recommend 180 so inverted flight is possible for fighters)

Keep existing `turn_rate_deg_per_tick` as the **yaw** rate. Add a short comment that it is the yaw-axis cap. Optional alias property `yaw_rate_deg_per_tick` → same value is nice but not required.

Populate `JET_FRAME_CATALOG` so inequalities hold:

```text
INTERCEPTOR.{yaw,pitch,roll}_rate > STRIKE_FIGHTER.* > HEAVY_BOMBER.*
```

Use the suggested table in design doc §5.2 unless you have a strong reason to tune; document any retune in the PR/commit body.

Defaults must not break construction of existing scenarios: any new required dataclass fields need defaults **or** every catalog entry updated in the same change.

## Part B — runtime state on `JetAgent`

In `wraithnet/world.py` (or wherever `JetAgent` is defined), add:

* `pitch_deg: float` (default `0.0`)
* `roll_deg: float` (default `0.0`)

Freeze angle conventions in a module docstring comment:

* yaw/`heading`: existing (0° = +y, clockwise)
* pitch: 0 = level, + = nose up
* roll: choose **one** of `(-180, 180]` or `[0, 360)` and use it consistently; document the choice in `geometry` helper docs if you add normalization helpers

Do **not** yet apply rate-limited rotation toward desired pitch/roll in the movement stage (Phase 51).

## Part C — schemas

Extend `wraithnet/schemas.py`:

* `Observation`: `current_pitch_deg`, `current_roll_deg` (and `current_altitude` if missing)
* `ActionIntent`: optional `desired_pitch_deg`, `desired_roll_deg`

Defaults must keep existing callers valid. Populate new observation fields from `JetAgent` wherever observations are built (grep `Observation(`).

## Part D — pure helpers (optional but recommended)

If useful, add to `wraithnet/geometry.py`:

* `normalize_roll_deg(...)` matching the chosen convention
* `clamp_pitch_deg(pitch, limit)`
* `shortest_signed_angle` already exists — reuse for pitch/roll deltas when convention allows

No weapon or movement behavior changes.

## Part E — tests

Add `tests/test_flight_attitude_contracts.py` (name flexible) covering:

1. Catalog rate inequalities across the three frames.
2. `JetAgent` / observation defaults are wings-level (pitch=0, roll=0) at spawn for the fixed PoC scenario.
3. Schema accepts intents with/without desired pitch/roll.
4. Existing tests still collect; fix only type/default breakages caused by this phase.

## Non-goals

* No changes to `_apply_move` / `_apply_heading_rotation` / `_apply_altitude` behavior beyond reading new fields if required for observation.
* No weapon envelope fields yet (Phase 52).
* No `TraceTick` pitch/roll yet (Phase 54) unless a one-line optional default is needed to keep imports clean — prefer deferring.
* No agent dive/roll tactics (Phase 53).
* No viewer work.

## Done means (Phase 50)

Phase 50 is **done** when:

1. `JET_FRAME_CATALOG` exposes pitch/roll rates and limits for all three frames with locked inequalities in tests.
2. Living jets expose `pitch_deg` / `roll_deg` in sim state; observations report them.
3. `ActionIntent` can carry desired pitch/roll without breaking old intents.
4. `pytest` suite is green with **no intentional PoC outcome golden changes** (behavior unchanged).
5. Design-doc field conventions are commented in code at the definition site.
