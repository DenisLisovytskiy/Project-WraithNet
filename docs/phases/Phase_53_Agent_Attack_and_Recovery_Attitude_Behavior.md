# Phase 53 — Agent Attack & Recovery Attitude Behavior

> **LLM implementation prompt:** Assume Phases 50–52 are complete. Read `docs/22_Authoritative_3D_Flight_Attitude_Design_Document.md` §7. This phase updates `decide_intent` (and light observation use) so jets **roll upright and pitch legally before bombing**, and use pitch for climb/dive/recovery. It does **not** redesign CNP messages or the viewer.

## Goal

Agents must be able to complete strike missions under attitude gates: they maneuver into a legal release attitude instead of spamming illegal `STRIKE`s.

## Part A — attack geometry policy

In `wraithnet/agents.py` (and helpers if you extract pure functions):

When the assigned weapon’s envelope requires upright / limited roll/pitch:

1. If target is in range but attitude is illegal → do **not** emit `STRIKE`/`LAUNCH`.
2. Emit movement/idle intent with:
   * `desired_roll_deg` toward 0 (wings level),
   * `desired_heading` toward target (existing),
   * `desired_pitch_deg` toward a mild delivery pitch inside the weapon’s pitch cap (level `0` is acceptable).
3. Only when `release_attitude_ok` and existing range/arc/LOS prechecks pass → fire.

Prefer extracting a small pure helper, e.g. `needed_attitude_for_release(weapon, current_pitch, current_roll) -> AttitudeCommand`, tested without the full agent loop.

## Part B — cruise / RTB pitch

* En route: bias pitch toward 0 and/or altitude toward frame `cruise_altitude` using Phase 51 precedence rules.
* RTB / carrier recovery: set pitch/altitude intents consistent with Phase 51 recovery behavior (level on short final).
* Do not fight notch defense: if `maneuver_defense == NOTCH`, keep Phase 39 yaw notch behavior dominant; avoid commanding extreme pitch/roll that breaks notch hold tests.

## Part C — optional bid utility nudge

If easy and localized in `bid_utility.py`: add a small penalty when the jet would need a long roll-out before a legal bomb release (e.g. currently inverted). **Do not** change CNP wire messages. Phase 45 risk terms remain.

Skip this part entirely if it risks destabilizing default assignments — note “deferred” in the phase completion summary.

## Part D — tests

Add `tests/test_agent_attitude_employment.py` (name flexible):

1. Given observation with JDAM selected, target in range, jet inverted → intent is not STRIKE; desired_roll moves toward upright.
2. Given wings-level legal envelope + in range/arc → STRIKE/LAUNCH as today.
3. Closing from long range still moves / rotates toward target (no regression to idle forever).
4. Notch regression: existing notch tests still pass.
5. Optional: a short world integration where an inverted bomber rolls then releases (may be slower; keep deterministic).

## Non-goals

* New airframes or weapons.
* Full terrain-masking dive tactics / pop-up attacks as a doctrine engine.
* Viewer work.
* Trace schema (unless agents require a field already added).

## Done means (Phase 53)

Phase 53 is **done** when:

1. Agents no longer issue JDAM `STRIKE` while inverted or outside the roll/pitch envelope.
2. Agents command roll/pitch toward a legal envelope when that is the blocker.
3. Legal wings-level attacks still fire.
4. Notch/flare regressions pass.
5. Full `pytest` green; any PoC drift handled per Phase 51/55 policy.
