# Design Document: Flight Dynamics & Weapon Effects (3D Viewer Visual Expansion)

> This document is the source of truth for a new, self-contained visual expansion layered on top of the already-implemented `viewer3d/` package (Phases 14–16, reviewed in `docs/11_Code_Review_Phases_14-16.md`). **Nothing in this document is implemented yet.** See `docs/phases/Phase_25_Flight_Dynamics_3D_Altitude_and_Attitude.md` and `docs/phases/Phase_26_Weapon_Effects_Missiles_Bombs_and_Gun_Tracers.md` for the phase-by-phase build plan.

## 0. What this expansion is, in one paragraph

Today, `viewer3d` renders every jet at a fixed, synthetic altitude that never changes (`JET_ALTITUDE = 8.0 + 0.35 * idx` — a constant, not a simulated flight path) and resolves every strike silently: a `StrikeEvent` just changes a target's color on the tick it lands, with no missile, bomb, or gun round ever visibly traveling between attacker and target. This expansion fixes both: jets gain a real, continuously varying altitude and banking attitude driven by their actual behavior (climbing out after takeoff, diving in to attack, banking through turns), and every strike spawns a weapon-type-appropriate visual effect — a glide-bombing arc for `JDAM`, a missile-with-trail for `AIM9X`/future air-to-air weapons, and an instant tracer streak for future gun weapons — plus explosion effects for destroyed targets and jets. **Both features are pure rendering additions, entirely inside `viewer3d/`.** Neither touches `wraithnet/`, the trace wire protocol, or any gameplay mechanic — exactly the same architectural boundary Phases 11–16 already established and that `docs/09_..._Design_Document.md §10` made an explicit, permanent non-goal ("no altitude-based gameplay").

## 1. Decisions already made (with the user, before design)

| Decision | Choice | Why it matters architecturally |
|---|---|---|
| Altitude is gameplay-affecting or cosmetic? | **Purely cosmetic**, exactly like the existing fixed altitude it replaces | Reaffirms `docs/09_...md §6.2`/`§10`'s existing non-goal. The user asked for jets to visibly *move* in 3D, not for altitude to change sensor range, SAM survivability, or fuel burn — those all stay 2D-plane calculations, untouched. |
| Where is altitude/attitude computed? | **Entirely client-side, inside `viewer3d/`**, as a deterministic function of data already in `TraceHeader`/`TraceTick` | No `wraithnet/` change, no trace schema change, no new wire-protocol field. Every connected viewer (including a late joiner replaying the backlog) computes the identical flight path from the identical tick sequence — consistent without any shared server-side state, the same principle `viewer3d/scene.py` already uses today for its position-delta heading estimate. |
| Are weapon effects simulated physics or cosmetic animation? | **Cosmetic animation only**, decoupled from simulation timing | `wraithnet/world.py::_apply_strike` resolves a strike instantly, within one tick, and that does not change — see `docs/06_Code_Review_Full_Project.md §1`'s "cumulative vs. per-tick" lesson, which this expansion must not regress. The viewer is allowed to *pretend* a projectile took some fraction of a second to travel, purely for legibility, the same sleight-of-hand `viewer/render.py`'s strike/destruction "flash" pulses already use in the 2D viewer. |
| Do jets stay camera-facing billboards? | **No — jets switch to a small, true-3D-oriented procedural glyph.** Targets, SAM sites, and jammers remain billboards. | Billboards can't convey climbing/diving/banking — a `set_billboard_point_eye()` node is, by definition, always reoriented to face the camera, which would make a "diving" jet look visually identical to a "level" one from most angles. Ground entities have no orientation to convey and gain nothing from dropping billboarding (see §4.4). |
| New dependency for animation? | **None.** `direct.interval` (`Sequence`, `LerpPosInterval`, `LerpColorScaleInterval`, `Func`) ships with the `panda3d` package already declared in `requirements-3d.txt`. | No `requirements-3d.txt` change needed. |
| Extensibility for future weapon types (Phase 21/23's `AMRAAM`, `GUN_POD`)? | **A small `weapon_type -> visual archetype` lookup table with a sane default fallback**, not a hardcoded per-weapon-type branch | The weapon catalog described in `docs/10_Airframes_Weapons_and_Fuel_Design_Document.md` (not yet implemented) will add new `WeaponType` values. This expansion must not need to be revisited just because a new weapon name shows up in a `StrikeEvent.weapon_type` string — an unrecognized type falls back to a reasonable default archetype instead of crashing or silently doing nothing. |

## 2. Architecture overview

```
                 ┌───────────────────────────────────────────┐
                 │  wraithnet (core sim)                       │   <- ZERO changes. Still 2D-only,
                 │  TraceHeader / TraceTick (Phase 11, frozen)  │      still zero rendering deps.
                 └───────────────────────────────────────────┘
                                   │ (unchanged wire protocol)
                                   ▼
                 ┌───────────────────────────────────────────┐
                 │  viewer3d/stream_client.py  (unchanged)      │
                 └───────────────────────────────────────────┘
                                   │ HeaderEvent / TickEvent
                                   ▼
        ┌───────────────────────────────────────────────────────────┐
        │  viewer3d/app.py — per-frame queue-drain task (Phase 16)     │
        │       │                                                       │
        │       ├──▶ viewer3d/flight_dynamics.py  (NEW, pure, Phase 25)  │──▶ per-jet target
        │       │        desired_altitude() / desired_bank_deg()          │    altitude/bank
        │       │                                                       │
        │       ├──▶ viewer3d/highlights.py  (NEW, pure, Phase 26)       │──▶ strikes /
        │       │        compute_highlights(previous, current)            │    destructions /
        │       │                                                       │    jam transitions
        │       ▼                                                       │
        │  viewer3d/scene.py  (extended)   viewer3d/effects.py (NEW, Phase 26)
        │    - jet glyph: dart mesh,          - spawn_strike_effect(...)
        │      true 3D orientation              - spawn_destruction_explosion(...)
        │    - per-frame altitude/bank easing   - spawn_jet_wreck(...)
        └───────────────────────────────────────────────────────────┘
```

Both new "brains" (`flight_dynamics.py`, `highlights.py`) are **pure functions with no Panda3D import**, tested headlessly, exactly matching the pattern `viewer/playback.py` (Phase 13) and the already-planned-but-not-yet-built `viewer3d/highlights.py` (sketched in `docs/phases/Phase_17_Camera_Modes_HUD_and_Mission_Brief.md` Part D) both use: **separate the "what should happen" decision from the "how to draw it" mechanics**, so the decision logic is unit-testable without a display.

## 3. What does *not* change

* `wraithnet/` — every module, every test. No new field on `TraceHeader`, `TraceTick`, `JetFrame`, `TargetFrame`, or `StrikeEvent`. This expansion proves it can add real visual richness using *only* data the wire protocol already carries.
* `viewer/` (the 2D Pygame replay viewer) — untouched, exactly as every prior 3D-viewer phase has left it.
* `requirements.txt`, `requirements-dev.txt`, `requirements-viz.txt`, `requirements-3d.txt` — no new dependency.
* Simulation timing/determinism/outcomes — every existing test that asserts a specific `summarize()` result, tick count, or strike log continues to pass unmodified. `wraithnet/world.py::_apply_strike`'s per-tick, instant-resolution semantics are unchanged; only the *viewer's* interpretation of "an event happened this tick" gains a cosmetic animation window layered on top.

## 4. Feature 1: Flight Dynamics — real, continuously varying altitude and attitude

### 4.1 The core constraint, restated

Per `docs/09_...md §6.2` and §10 of that same document, **the simulation itself stays 2D forever** — no terrain-following, no altitude-based sensor/SAM/evasion mechanics, no vertical component in `wraithnet/schemas.py::Observation`/`ActionIntent`. Everything in this section is the viewer *inventing* a plausible flight path for a jet whose only ground-truth data is a 2D `(x, y)` position per tick, `hp`, `payload`, `jammed`, and `role`. This is not a limitation to work around quietly — it is the same deliberate, explicit boundary this project has held since Phase 16's own scene.py docstring ("No gameplay logic reads z; it is purely cosmetic"), now extended from a static offset to a dynamic one.

### 4.2 What "real" altitude means here: three deterministic phases, driven by tick data alone

For each jet, every tick, a **desired altitude** is computed as the combination of:

1. **Takeoff climb** — for the first few seconds after a jet is first seen (tick 0, or whichever tick it first appears in if it's added mid-run by a future scenario feature), altitude ramps up from `GROUND_ALTITUDE = 0.0` to `CRUISE_ALTITUDE = 8.0` (the same constant Phase 16 already uses, kept for the "already-flying" cruise baseline). This alone is what makes takeoff visually obvious instead of a jet just materializing at cruise height.
2. **Proximity-driven attack dive / climb-out** — every tick, the viewer computes the jet's distance to the nearest *currently alive* target (using `TraceHeader.target_positions` + the current tick's `TargetFrame.hp_remaining`, both already available, no new data). Within an "engagement zone" (a small multiple of `TraceHeader.strike_range`, since that's the range at which a strike can actually land), the desired altitude dips toward a low `ATTACK_ALTITUDE`; outside it, the desired altitude eases back toward `CRUISE_ALTITUDE`. This produces exactly the "dive in for the kill" silhouette without needing to know the jet's *intent* — proximity to a live target it's capable of hitting is a reasonable, fully-derivable-from-trace-data proxy for "probably attacking."
3. **Post-strike climb boost** — if the jet appears in the current tick's `strikes` list (i.e., it just fired), the desired altitude gets a brief, extra upward nudge for the next few ticks, exaggerating the "pull up after the bomb run" motion rather than letting it hover flat at attack altitude indefinitely while still near the (possibly still-alive, if the target needed multiple hits) target.

None of this requires knowing the jet's actual commanded destination, its CNP role, or anything from `wraithnet/agents.py` — it is reconstructed entirely from position + target state + the strike log, all already streamed.

### 4.3 Smoothing: altitude must ease, not teleport

Ticks do not arrive at a fixed wall-clock cadence (real CNP negotiation, `asyncio.sleep`-based timeouts, and network/queueing jitter all affect when a given tick is drained from `StreamClient.events`). If the *rendered* altitude snapped directly to the *desired* altitude every time a new tick was drained, a jet could visibly teleport several world-units up or down in one frame. Instead:

* `viewer3d/flight_dynamics.py` computes a **desired** altitude/bank pair from the latest tick's data — a pure function, no state, no smoothing.
* A small piece of **per-frame** (not per-tick) state — one `(current_altitude, current_bank_deg)` pair per live jet — eases toward whatever the most recent desired values are, at a bounded rate (`MAX_CLIMB_RATE_PER_SEC` world-units/second, `MAX_BANK_RATE_PER_SEC` degrees/second), driven by real elapsed wall-clock time (`dt`) inside the same per-frame task `viewer3d/app.py` already runs for queue-draining (Phase 16's `_drain_queue_task`). This is the only piece of this feature that is *not* a pure function, and it is a thin, mechanical "ease toward a target" — the interesting decision logic (§4.2) stays pure and testable.

### 4.4 Why jets need to stop being billboards (and why targets/jammers don't)

A `set_billboard_point_eye()` node is recomputed every frame to face the camera directly — by construction, it **cannot** show pitch (nose up/down) or true roll relative to the world, only whatever 2D rotation the billboard effect happens to preserve around the view axis (which is what today's heading-estimate hack already (ab)uses). Once altitude actually changes, a billboarded jet glyph would still always present the exact same face-on silhouette to the camera regardless of whether it's climbing, diving, or banking — visually contradicting the very attitude this feature computes. So:

* **Jets** switch from a billboarded flat quad to a small, non-billboarded, double-sided **procedural "dart" mesh** — a handful of triangles (nose vertex, two wingtip vertices, a shallow tail vertex) built the same way `scene.py::_polygon_geom`/`_ground_disc` already build flat-colored geometry from raw vertices today (no texture, no asset file, consistent with `docs/09_...md §10`'s "no 3D asset pipeline" non-goal). It is oriented in full 3D — heading (yaw) from the position-delta estimate already used today, plus the new bank angle (§4.2/4.3) applied as roll, plus a small pitch tilt tied to climb/dive rate — using Panda3D's ordinary `set_hpr()`, no billboard effect attached at all.
* **Targets, SAM sites, and jammers** keep the existing billboard glyphs exactly as Phase 16 built them — they are stationary, have no attitude to convey, and billboarding keeps them legible (readable silhouette) from any camera angle, which still matters for the orbit/mission-brief camera modes Phase 17 (unimplemented) will add.
* **Accepted trade-off, stated explicitly**: a flat, non-billboarded dart viewed exactly edge-on (nose or tail pointed straight at the camera) will look razor-thin for an instant. This is a known, acceptable placeholder-primitive quirk (the same category of simplification `docs/09_...md §10` already blesses for every other entity in this project) — not a bug to chase, and not worth the complexity of a full lofted 3D aircraft model.

### 4.5 New module: `viewer3d/flight_dynamics.py`

Pure functions, zero Panda3D import, unit-tested exactly like `viewer/playback.py`:

```python
def nearest_alive_target_distance(jet_position, header, tick) -> float | None: ...
def desired_altitude(agent_id, jet_position, header, tick, ticks_airborne, just_struck) -> float: ...
def desired_bank_deg(prev_position, curr_position, prev_target_altitude, curr_target_altitude) -> float: ...
def ease_toward(current, target, max_rate_per_second, dt_seconds) -> float: ...  # shared scalar easing helper
```

See `docs/phases/Phase_25_Flight_Dynamics_3D_Altitude_and_Attitude.md` for exact signatures, constants, and test requirements.

## 5. Feature 2: Weapon Effects — missiles, bombs, and gun tracers

### 5.1 The trigger data already exists

Every strike that lands is already recorded, per-tick, as a `StrikeEvent(agent_id, target_id, weapon_type)` inside `TraceTick.strikes` (Phase 11, frozen). Every destroyed target is already detectable by diffing consecutive `TargetFrame.hp_remaining` values (exactly `viewer/playback.py::compute_highlights` already does for the 2D viewer). Every destroyed jet is already detectable by a jet's `agent_id` disappearing from one tick's `jets` list to the next (exactly what `viewer3d/scene.py::update_tick` already does today, just silently). **No new trace data is needed** — this feature is "notice events that already exist in the stream, and draw something for them," the same category of work Phase 13 already did for the 2D viewer's flash/pulse highlights.

### 5.2 Per-weapon-type visual archetypes, with a safe default

A small lookup table, keyed by the `weapon_type` string already carried on every `StrikeEvent`:

| `weapon_type` | Archetype | Visual | Cosmetic travel duration |
|---|---|---|---|
| `JDAM` | `"bomb"` | Small glyph released from the jet, follows a gentle downward-arcing glide path to the target, larger impact flash on arrival | ~1.0–1.2s |
| `AIM9X` (and any future air-to-air missile, e.g. `AMRAAM`) | `"missile"` | Small elongated glyph + a short fading engine-trail, travels in a near-straight line (slight terminal curve toward the target) | ~0.5–0.8s |
| Any future gun-type weapon (e.g. `GUN_POD`) | `"gun_tracer"` | An instantaneous, fast-fading bright line directly connecting attacker and target — no travel arc, since gun rounds are effectively instant at this simulation's scale | ~0.1–0.15s |
| *(anything unrecognized)* | falls back to `"missile"` | Same as `AIM9X` | ~0.5–0.8s |

This table lives in `viewer3d/effects.py`, not hardcoded per-call-site logic, specifically so that when `docs/phases/Phase_21_Weapon_Catalog_Range_Arc_and_New_Weapons.md` (unimplemented, part of the separate Airframes/Weapons/Fuel expansion) eventually adds `AMRAAM`/`GUN_POD` as real, fireable weapon types, this feature needs **at most a one-line table addition**, and produces a sane result even with **zero** changes if that phase lands first.

### 5.3 The cosmetic travel-time sleight-of-hand, and why it's fine

`wraithnet/world.py::_apply_strike` still resolves damage instantly, within the tick the strike is logged — this expansion does not add flight-time, ballistics, or a chance-to-miss to the simulation. What changes is purely presentational: when the viewer notices a `StrikeEvent` on tick *N*, it spawns an animation that visually takes a fraction of a second of *wall-clock* time to travel from the attacking jet's tick-*N* position to the target's position, even though the simulation itself considered it resolved instantly. This is exactly the same category of embellishment `viewer/render.py`'s strike/destruction "flash" pulses already use (a brief highlight that outlives the single instantaneous tick it represents) — established precedent, not a new kind of fudge.

### 5.4 Destroyed-target and destroyed-jet explosions

* **Destroyed target**: in addition to whatever regular impact flash the killing strike's effect already shows, a target whose `hp_remaining` transitions to `0` on this tick gets a larger, distinct explosion effect at its (static) position — reusing `TraceHeader.target_positions`.
* **Destroyed jet**: today, `scene.py::update_tick` removes a jet's node the instant its `agent_id` disappears from `tick.jets` — instant, silent despawn. This expansion changes that to: spawn a brief "falling wreck" effect at the jet's last known position (already cached in `SceneHandles.last_jet_xy`) — reusing whatever altitude Feature 1 had most recently rendered it at as the wreck's starting height, if Feature 1 is also implemented — and only remove the node once that effect's short animation completes, instead of on the same frame it's noticed.

### 5.5 New modules

* **`viewer3d/highlights.py`** (pure, no Panda3D import) — `compute_highlights(previous: TraceTick | None, current: TraceTick) -> TickHighlights`, an incremental (single-tick-at-a-time) equivalent of `viewer/playback.py::compute_highlights`, adapted for a live stream where ticks arrive one at a time rather than being pre-loaded into a list. **Note:** `docs/phases/Phase_17_Camera_Modes_HUD_and_Mission_Brief.md` (unimplemented) already sketches this exact file for its own event-flash purposes. Whichever phase — this one or Phase 17 — is implemented first should create it with this shape; the other should extend/reuse it rather than create a second, competing copy. See the note added to Phase 17's own doc.
* **`viewer3d/effects.py`** (Panda3D-dependent) — `spawn_strike_effect(...)`, `spawn_destruction_explosion(...)`, `spawn_jet_wreck(...)`, each building a small node + a self-driving `direct.interval.Sequence` (move/fade/scale over the archetype's duration, then a `Func` callback that cleans up the node) — no manual per-frame bookkeeping needed beyond what Panda3D's own interval/task manager already provides.

See `docs/phases/Phase_26_Weapon_Effects_Missiles_Bombs_and_Gun_Tracers.md` for exact signatures, the full archetype table's numeric constants, and test requirements.

## 6. Explicit non-goals

* **No altitude-based gameplay, ever** — reaffirming `docs/09_...md §10`. Sensor radius, SAM engagement range, jamming radius, fuel burn, evasion — all stay exactly the 2D-plane calculations they are today.
* **No real projectile physics/ballistics** — no gravity simulation, no miss chance, no collision detection for the cosmetic projectile animations. They are guaranteed to "arrive" at the target's position when their fixed duration elapses, matching the simulation's own already-instant, deterministic resolution.
* **No 3D asset pipeline, no textured/modeled missiles or explosions** — procedural placeholder primitives only (small vertex-built meshes, `LineSegs` for tracers, simple color/scale-lerp "explosions"), exactly the standard this project has held since Phase 16.
* **No sound effects** — visual-only expansion; audio is out of scope for this project entirely (no audio has been added anywhere, and `viewer3d/app.py` already explicitly disables Panda3D's audio library at startup).
* **No change to `wraithnet/`, `wraithnet/trace_schemas.py`, the live-stream wire protocol, or `viewer/`** — this is a pure `viewer3d/`-internal rendering expansion, exactly like every feature described in this document being "notice something that's already in the data, draw it better."
* **No requirement that this land before or after Phases 17–24** — see §7.

## 7. Ordering / dependency note

**Phases 25 and 26 (this document) depend only on the already-implemented Phase 16** (`viewer3d/scene.py`, `viewer3d/app.py`, `viewer3d/stream_client.py`) and can be implemented immediately, in either order, independently of whether Phases 17–24 (Camera Modes/HUD, Scenario Editor, Launch Integration, and the separate Airframes/Weapons/Fuel expansion) have been started yet. They were numbered 25/26 (continuing after the already-fully-designed Phase 24) purely to avoid renumbering eight already-cross-referenced, unimplemented phase documents — the numbers are identifiers, not a mandated build order. If you want to implement them right after Phase 16, before Phase 17, that is fully supported and arguably the more natural order visually (a more convincing live-tactical scene before adding camera modes/HUD polish on top of it).

One soft interaction worth knowing about, not a hard dependency: `docs/phases/Phase_17_Camera_Modes_HUD_and_Mission_Brief.md` independently sketches its own `viewer3d/highlights.py` for HUD flash purposes (§5.5 above). Whichever of "Phase 17" or "Phase 26" is actually implemented first should build that one file; the other phase should extend it, not duplicate it.

## 8. Implementation phases for the LLM

* **Phase 25 — Flight Dynamics: Real 3D Altitude & Attitude** (`docs/phases/Phase_25_Flight_Dynamics_3D_Altitude_and_Attitude.md`): `viewer3d/flight_dynamics.py`, jet glyph switched from billboard to oriented dart mesh, per-frame altitude/bank easing in `viewer3d/app.py`. Depends only on Phase 16.
* **Phase 26 — Weapon Effects: Missiles, Bombs & Gun Tracers** (`docs/phases/Phase_26_Weapon_Effects_Missiles_Bombs_and_Gun_Tracers.md`): `viewer3d/highlights.py`, `viewer3d/effects.py`, deferred jet-removal wired through `scene.py::update_tick`. Depends only on Phase 16 (and, softly, on not duplicating Phase 17's `highlights.py` if that lands first — see §7).
