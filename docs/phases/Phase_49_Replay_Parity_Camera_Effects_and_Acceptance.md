# Phase 49 — Replay Parity, Camera/Effects Alignment & Acceptance

> **LLM implementation prompt:** Assume Phases 46–48 are complete and passing. Read `docs/21_Smooth_Live_Motion_Design_Document.md` §§0, 8–12, 14 before coding. Reuse the same `TickBuffer` / sampler for replay-style presentation where applicable, finish camera/effects alignment, and land docs + packaging acceptance notes. Do not start terrain/naval work here.

## Goal

Live and replay presentation share the same motion contracts; cameras and cosmetic effects stay aligned with presented poses; a new user can Launch and immediately perceive smooth motion.

## Part A — replay / offline presentation path

Where `viewer3d` (or launcher preview) can play a finished trace or a recorded backlog:

* Feed `TraceTick`s into the same `TickBuffer`.
* Advance presentation time using playback rate × `dt` (1×, 2×, etc. if speed controls exist; if not, add only a minimal rate constant / stub rather than a full UI redesign).
* Do not specially-case live with a second interpolator.

If the project’s primary replay remains the 2D Pygame viewer:

* Prefer wiring 3D mission-brief → live and any 3D offline tick playback first.
* Treat 2D Pygame smooth motion as **optional**; only implement if it is a small reuse of the pure module with no Pygame coupling inside `motion.py`.

## Part B — camera & effects alignment audit

Verify and fix:

1. Chase/orbit/follow use sampled poses every frame.
2. Strike/missile cosmetic spawns use presented-tick timing (Phase 47) and still look correct after catch-up snaps (Phase 48) — on snap, drop pending outdated cosmetic queues rather than playing a storm of backlog effects.
3. HUD tick index / feasible status / roles match `current_tick_for_hud()`.
4. Selected jet highlight tracks sampled position.

## Part C — quality-of-life presentation HUD (lightweight)

Add a concise live HUD or debug readout (can be toggleable):

* presentation backlog length
* whether catch-up or extrapolating is active

Do not clutter the default mission HUD; a small debug line or Advanced toggle is enough.

## Part D — documentation

Update `README.md` with a short **Smooth live view** note:

* Sim ticks remain discrete; the viewer interpolates between them.
* Smooth motion does not mean continuous combat resolution.
* Optional cinematic pacing flag only if Phase 48 added one.

Add a short subsection to `packaging/BUILDING.md`:

* Confirm frozen build imports `viewer3d.motion` (or final module name) through the viewer import graph.
* Manual smoke: Launch → watch 10 seconds of smooth jet motion → exit cleanly.

## Part E — tests & acceptance

1. Replay or synthetic offline feed: sampler advances with rate × dt.
2. Catch-up snap clears or suppresses backlog effect spam (unit or headless).
3. Regression: Phase 46–48 tests still pass.
4. Full `pytest` passes.
5. Manual acceptance checklist (record in PR/summary, not as flaky CI):
   * `python -m launcher`
   * Standard generate → Launch
   * Jets move smoothly at a glance (~30 FPS class hardware)
   * Strike flashes still readable
   * Headless summarize on the same scenario still matches pre-expansion expectations for the default PoC (or current default scenario)

## Non-goals

* Pixel-perfect CI.
* Reworking CNP or weapon catalogs.
* Terrain/naval presentation (hook points only if already trivial).
* Replacing the discrete sim.

## Acceptance criteria

1. One motion sampler serves live and the chosen replay/offline path.
2. Cameras/effects/HUD agree on presented time.
3. README + packaging notes updated.
4. Full `pytest` passes.
5. Manual Launch smoke shows continuous jet motion without gameplay changes.
