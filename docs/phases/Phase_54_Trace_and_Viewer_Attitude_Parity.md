# Phase 54 — Trace & Viewer Attitude Parity

> **LLM implementation prompt:** Assume Phases 50–51 are complete (52–53 preferred so demos show gates). Read `docs/22_Authoritative_3D_Flight_Attitude_Design_Document.md` §8 and `docs/21_Smooth_Live_Motion_Design_Document.md` interpolation rules. This phase streams and renders **authoritative** pitch/roll. It does **not** change combat math.

## Goal

Live and replay 3D views show the same pitch/roll/yaw the simulation uses, with smooth interpolation, retiring conflicting cosmetic bank fiction when authoritative roll is present.

## Part A — trace schema

Extend `wraithnet/trace_schemas.py` `JetFrame` with:

* `pitch_deg: float = 0.0`
* `roll_deg: float = 0.0`

Keep `heading` and `altitude` as they are. Legacy traces without fields must still load (Pydantic defaults).

Update `trace_builders.py` to copy from `JetAgent`.

Bump `trace_version` **only if** repo policy requires it for non-optional wire breaks; prefer optional fields with defaults to avoid breaking old JSONL. Document the choice in the module comment.

Include pitch/roll in `jet_frame_stats` header metadata if that dict is the pattern for frame limits (optional).

## Part B — motion interpolator

Extend `viewer3d/motion.py` `SampledPose` with `pitch_deg`, `roll_deg` (and `altitude` if not already interpolated — if altitude is still eased elsewhere, document a single rule).

Interpolate:

* heading: existing shortest-arc
* pitch: linear within limits (no wrap) **or** shortest-arc if you used a wrapping convention — match Phase 50
* roll: shortest-arc compatible with Phase 50 convention

Tests in `tests/test_viewer3d_motion.py`: midpoint pitch/roll; roll wrap if applicable; legacy ticks missing fields behave as 0.

## Part C — scene orientation

In `viewer3d/scene.py` (and camera chase if it reads HPR):

* Drive glyph `set_hpr` from sampled/authoritative yaw, pitch, roll (map to Panda’s HPR carefully; add a single conversion helper with unit tests if signs are tricky).
* When `JetFrame.roll_deg` / pitch are present, **do not** overwrite roll with altitude-rate cosmetic bank from `flight_dynamics.py`.

## Part D — flight_dynamics.py role shrink

Update `viewer3d/flight_dynamics.py` docs and code paths:

* If authoritative attitude exists on the tick/pose, cosmetic `desired_bank_deg` from altitude rate is skipped or only used as residual smoothing toward authoritative roll.
* Keep easing helpers for presentation smoothness.

## Part E — HUD / debug

* F3 motion debug (or HUD line) shows pitch/roll for the selected jet when cheap.
* No pixel tests required.

## Part F — tests

1. Trace builder emits pitch/roll matching agent state.
2. Motion sampler interpolates new fields.
3. Headless scene/app tests (existing patterns) accept new fields without crash.
4. Import purity: `motion.py` remains Panda-free.

## Non-goals

* 2D Pygame full attitude rendering (optional tiny HUD only).
* Replacing weapon effect meshes.
* Combat or agent changes.

## Done means (Phase 54)

Phase 54 is **done** when:

1. New traces carry pitch/roll; old traces still load.
2. Interpolated poses include pitch/roll; live scene orients jets from them.
3. Cosmetic bank does not fight authoritative roll when both would apply.
4. Viewer motion/scene tests pass; full `pytest` green.
5. A manual smoke (`python -m launcher` → Launch, or replay) shows visible roll/pitch when a jet banks/dives — noted in the completion summary (manual check in addition to automated tests).
