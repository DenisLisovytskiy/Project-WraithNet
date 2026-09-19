# Phase 55 — End-to-End Acceptance & Documentation Closeout

> **LLM implementation prompt:** Assume Phases 50–54 are implemented on the branch. Read `docs/22_Authoritative_3D_Flight_Attitude_Design_Document.md` §11 (Definition of Done — entire track). This phase does **not** invent new flight features; it proves the track, refreshes goldens deliberately, and closes docs.

## Goal

Prove the full attitude rework against the track-level Done criteria, fix integration gaps, update documentation banners/README, and leave `main`-ready acceptance evidence.

## Part A — track checklist tests

Add `tests/test_flight_attitude_track_acceptance.py` (or split equivalents) that automate as much of design doc §11 as practical:

1. **Per-frame sharpness inequalities** for yaw/pitch/roll rates (may already exist from Phase 50 — assert once in an acceptance module).
2. **Pitch → altitude:** scripted intents over N ticks change altitude for a Strike Fighter.
3. **JDAM inverted reject → upright accept** in one world fixture sequence.
4. **AAM vs JDAM contrast** under a shared extreme roll (Phase 52); keep here as acceptance guard.
5. **Trace round-trip:** build a tick frame with nonzero pitch/roll; ensure builder → schema → motion sample does not drop fields.
6. **Wings-level default mission path:** fixed PoC or a tiny scenario still reaches a defined success/progress criterion after golden refresh.

Mark any item that truly requires a display as manual in Part C; do not skip automatable items.

## Part B — PoC / golden handling

If Phases 50–54 deferred summarize drift:

1. Run the fixed PoC (`python -m wraithnet.main` / existing summary tests).
2. Determine whether drift is caused by attitude defaults (prefer fixing defaults to wings-level cruise) or genuine timing changes.
3. Update golden expectations **only** with a clear test comment referencing Phase 55 and the design doc.
4. Never weaken assertions by deleting coverage.

## Part C — manual smoke script (document results in PR / phase note)

Record a short checklist in `docs/phases/` completion is usually the PR body; optionally add `docs/22_...` §11 checkbox update:

1. Launcher → Generate/Load → Launch.
2. Confirm jets bank/pitch visibly in 3D when turning/diving.
3. Confirm a bomb strike still occurs on a normal pass.
4. Confirm F3/debug attitude roughly matches motion.

## Part D — documentation closeout

1. Update the banner on `docs/22_Authoritative_3D_Flight_Attitude_Design_Document.md` from “Nothing in this document is implemented yet” to a **status** line listing Phases 50–55 as implemented (date optional).
2. Ensure `README.md` Documentation section links this design doc and mentions Phases 50–55 (if not already).
3. Cross-link from `docs/12_...` and/or `docs/10_...` with a one-line “superseded for authoritative attitude by docs/22” note — minimal edit, no rewrite of old docs.

## Part E — non-goals

* No new maneuver types, airframes, or weapons.
* No continuous physics.
* No drive-by refactors unrelated to failing acceptance.

## Done means (Phase 55)

Phase 55 is **done** when:

1. All track-level Done criteria in design doc §11 are either automated-green or manually checked with notes.
2. Full `pytest` is green on a clean tree.
3. Design doc + README reflect implementation status.
4. No open “deferred golden” TODOs remain without an explicit follow-up issue/note.

## Done means (entire track — copy for agents)

The **entire** 3D attitude rework is done only when Phase 55’s Done means are met **and** design doc §11 items 1–8 are satisfied. Completing Phase 50–54 alone is **not** track completion.
