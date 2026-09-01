# Phase 25 — Flight Dynamics: Real 3D Altitude & Attitude

> **How to use this document:** This is a complete, standalone prompt. Give an LLM coding agent this entire file as its task. Assume Phase 16 is complete and passing (`viewer3d/app.py` connects to a live stream, builds a static 3D scene from the header, and updates jet/target positions per tick, with jets rendered as camera-facing billboard glyphs at a fixed altitude). Read `docs/12_Flight_Dynamics_and_Weapon_Effects_Design_Document.md` §4 in full before starting. **This phase does not depend on Phases 17–24** — see that design doc's §7. This phase touches only `viewer3d/`; it makes zero changes to `wraithnet/`, the trace wire protocol, or `viewer/`.

> **Automated GUI tests are required.** Follow `docs/14_Automated_GUI_Testing_Design_Document.md`. Every new or changed `viewer3d/` behavior this phase adds must ship with automated tests: Layer 1 (Panda3D-free pure logic, e.g. `flight_dynamics.py`) where the decision can be extracted, Layer 2 (headless `window-type none` + `pytest.importorskip("panda3d")`) for scene/app wiring. A manual visual check is in addition to, not instead of, those tests. Pixel/screenshot tests remain out of scope.

## Context

Today, every jet in `viewer3d/scene.py` sits at `JET_ALTITUDE + 0.35 * idx` forever — a constant that never changes, computed once and never revisited. This phase replaces that constant with a real, continuously varying altitude (climb on takeoff, dive in to attack, climb back out) and a true 3D orientation (heading + bank), which requires switching jets from a camera-facing billboard glyph to a small, non-billboarded procedural mesh — billboards cannot show pitch/bank by construction (see design doc §4.4 for the full rationale; do not skip that section, it explains why targets/jammers are deliberately *not* changed in this phase).

## Part A — `viewer3d/flight_dynamics.py` (new file, Panda3D-free)

Pure functions only — no `panda3d`/`direct` import anywhere in this file, verified by grep in Part D. This mirrors `viewer/playback.py`'s "pure logic, Pygame-free, unit-testable" pattern exactly.

```python
"""Pure functions computing a jet's cosmetic flight altitude and bank angle
from data already present in TraceHeader/TraceTick -- no wraithnet change,
no new trace field. See docs/12_Flight_Dynamics_and_Weapon_Effects_Design_Document.md
§4. Altitude/bank here are a rendering-only fiction; nothing here is read by
any gameplay logic, and nothing in wraithnet/ imports this module.
"""

from __future__ import annotations

import math

GROUND_ALTITUDE = 0.0
CRUISE_ALTITUDE = 8.0          # matches viewer3d/scene.py's existing JET_ALTITUDE
ATTACK_ALTITUDE = 2.0
TAKEOFF_CLIMB_SECONDS = 2.5     # wall-clock seconds to reach cruise after first appearing
ENGAGEMENT_RADIUS_MULTIPLIER = 3.0   # x header.strike_range -> "close enough to be attacking"
POST_STRIKE_CLIMB_BOOST_TICKS = 3    # how many *ticks* the boost persists once triggered
POST_STRIKE_CLIMB_BOOST_ALTITUDE = 4.0  # added on top of the proximity-based desired altitude
MAX_CLIMB_RATE_PER_SECOND = 6.0    # world-units/sec, used by ease_toward for altitude
MAX_BANK_RATE_PER_SECOND = 90.0    # deg/sec, used by ease_toward for bank
MAX_BANK_DEG = 35.0                # clamp so dives/climbs don't produce absurd bank angles


def nearest_alive_target_distance(
    jet_position: tuple[float, float],
    header: "TraceHeader",
    tick: "TraceTick",
) -> float | None:
    """Distance from jet_position to the closest target that is still alive
    this tick (hp_remaining > 0). None if no targets exist or all are dead."""
    ...


def desired_altitude(
    seconds_airborne: float,
    jet_position: tuple[float, float],
    header: "TraceHeader",
    tick: "TraceTick",
    just_struck: bool,
) -> float:
    """Combine three signals into one target altitude for this tick:
    1. Takeoff climb: linearly interpolate GROUND_ALTITUDE -> CRUISE_ALTITUDE
       over the first TAKEOFF_CLIMB_SECONDS of a jet's life; CRUISE_ALTITUDE
       thereafter, as the baseline before the next two signals adjust it.
    2. Proximity dive: if nearest_alive_target_distance() <= header.strike_range
       * ENGAGEMENT_RADIUS_MULTIPLIER, ease the baseline down towards
       ATTACK_ALTITUDE (linearly, by how much closer than the threshold it is
       -- do not hard-snap to ATTACK_ALTITUDE the instant the threshold is
       crossed); beyond the threshold, use the takeoff-climb baseline as-is.
    3. Post-strike boost: if just_struck is True, add
       POST_STRIKE_CLIMB_BOOST_ALTITUDE on top of whatever (1)+(2) produced.
       (The caller in Part C is responsible for tracking how many ticks ago
       a jet last struck and passing just_struck=True for
       POST_STRIKE_CLIMB_BOOST_TICKS ticks after that strike -- this function
       itself is stateless and only needs a bool for "is the boost active
       right now".)
    Never returns a value below GROUND_ALTITUDE or (informally) much above
    CRUISE_ALTITUDE + POST_STRIKE_CLIMB_BOOST_ALTITUDE.
    """
    ...


def desired_bank_deg(
    previous_altitude_target: float | None,
    current_altitude_target: float,
    dt_seconds: float,
) -> float:
    """Bank angle purely from the *rate of change* of the desired altitude
    (climbing -> bank one way, diving -> bank the other), clamped to
    +/- MAX_BANK_DEG. previous_altitude_target is None on a jet's first-ever
    tick (return 0.0). This is deliberately independent of heading -- the
    caller (Part B) adds this on top of the existing heading-derived roll,
    it does not replace it.
    """
    ...


def ease_toward(current: float, target: float, max_rate_per_second: float, dt_seconds: float) -> float:
    """Move `current` towards `target` by at most max_rate_per_second * dt_seconds.
    Shared by altitude (MAX_CLIMB_RATE_PER_SECOND) and bank (MAX_BANK_RATE_PER_SECOND)
    easing in Part C -- one small, well-tested helper, not two near-duplicate ones."""
    ...
```

Fill in every function. `nearest_alive_target_distance` should use `math.dist` against `header.target_positions[t.target_id]` for every `t in tick.targets` where `t.hp_remaining > 0`, matching the exact style already used in `wraithnet/world.py` and `viewer3d/scene.py`. Do not import `wraithnet` from this file — `TraceHeader`/`TraceTick` are already available via `wraithnet.trace_schemas`, which is fine to import (it's a pure Pydantic schema module, not simulation logic), but nothing from `wraithnet/world.py`, `agents.py`, `bridge.py`, etc.

## Part B — `viewer3d/scene.py` changes: jets become oriented dart meshes, not billboards

* **New procedural mesh builder**, alongside the existing `_polygon_geom`/`_attach_glyph` helpers: a small double-sided "dart" — e.g. a nose vertex at `(0, 0.7)`, two wingtip vertices at `(-0.5, -0.4)`/`(0.5, -0.4)`, and a shallow tail vertex at `(0, -0.15)` slightly *inset* in a synthetic third dimension (a tiny local-Z offset on the tail vertex, e.g. `-0.08`, purely so the shape has some non-zero thickness/cross-section from off-axis angles — do not overthink this, three or four triangles is enough; see design doc §4.4's "accepted trade-off" note about edge-on viewing). Build it with `Geom`/`GeomVertexData`/`GeomTriangles` exactly the way `_polygon_geom` already does, just with a 3-coordinate vertex list instead of the existing 2D `(x, z)` helper's shape.
* **Attach this mesh WITHOUT `set_billboard_point_eye()`** — this is the one deliberate divergence from every other entity type in this file. Use plain `set_hpr(heading_deg, pitch_deg, roll_deg)` instead (Panda3D's heading/pitch/roll convention — heading is yaw around Z, pitch is around the local X/right axis, roll is around the local Y/forward axis; get the axis mapping right for this Panda3D version and sanity-check visually if a display is available, per the phase's acceptance criteria).
* **`SceneHandles` gains two new per-jet dicts**: `rendered_altitude: dict[str, float]` and `rendered_bank_deg: dict[str, float]` (the actual, eased, per-frame values — distinct from whatever `flight_dynamics.py` computes as this tick's *desired* values). Also track `first_seen_tick: dict[str, int]` (or a wall-clock timestamp — pick whichever is simpler given how `seconds_airborne` needs to be computed in Part C) and `last_struck_tick: dict[str, int]` (for the post-strike boost window).
* **`update_tick` signature and body**: keep setting `x`, `y` exactly as today; stop hardcoding `z = _jet_altitude(...)` from the static formula — instead, `update_tick` should compute this tick's *desired* altitude (via `flight_dynamics.desired_altitude(...)`) and *desired* bank delta (via `flight_dynamics.desired_bank_deg(...)`) and store them somewhere `app.py`'s per-frame easing step (Part C) can read on the next frame — do not try to fully resolve rendered altitude/orientation inside `update_tick` itself, since that only runs once per received tick, not once per frame (see Part C for why the actual node transform update needs to happen every frame, not every tick).
* **Jet removal (`aid not in alive_ids` branch)**: unchanged in this phase — still an immediate `remove_node()`. (Phase 26 changes this to a deferred wreck-effect removal; do not implement that here, it is out of scope for this phase.)

## Part C — `viewer3d/app.py` changes: per-frame easing task

`WraithNetViewer3D._drain_queue_task` currently only drains the event queue once per frame. This phase adds the missing "ease rendered altitude/bank toward the latest desired values every frame, using real elapsed time" step — this cannot live inside `update_tick` (which only fires once per *tick*, and ticks do not arrive at a fixed real-time cadence) and cannot be a one-shot computation.

* Track `self._desired_altitude: dict[str, float]` / `self._desired_bank_deg: dict[str, float]` (or store these directly on `SceneHandles`, whichever keeps `app.py` simpler — pick one and be consistent with Part B's data ownership).
* Track `self._first_seen_wallclock: dict[str, float]` (via `time.monotonic()` the first frame a jet's `agent_id` is seen) so `seconds_airborne` in `flight_dynamics.desired_altitude` reflects real elapsed time, not tick count (ticks can arrive faster or slower than real time; the takeoff-climb animation should look consistent regardless of how fast the underlying sim is actually running).
* Track `self._last_struck_wallclock_tick: dict[str, int]` (or similar) updated whenever a `TickEvent`'s `tick.strikes` includes a given `agent_id`, consumed by the `just_struck` boolean passed into `desired_altitude` for `POST_STRIKE_CLIMB_BOOST_TICKS` *ticks* after that (tick-counted, not wall-clock — a strike is a discrete per-tick event, so bounding the boost window in ticks rather than seconds is the more natural fit here).
* Add a per-frame step (in the same task, or a second `task_mgr.add(...)` task — either is fine, document the choice) that, for every currently-alive jet in `handles.jets`:
  1. Reads `dt = globalClock.get_dt()` (Panda3D's standard per-frame delta-time accessor) or the task's own `dt` if using a `task_mgr` task signature that provides it directly.
  2. Eases `handles.rendered_altitude[aid]` toward `self._desired_altitude.get(aid, CRUISE_ALTITUDE)` via `flight_dynamics.ease_toward(..., MAX_CLIMB_RATE_PER_SECOND, dt)`.
  3. Eases `handles.rendered_bank_deg[aid]` similarly via `MAX_BANK_RATE_PER_SECOND`.
  4. Sets the jet anchor's `z` to the eased altitude, and calls `set_hpr(heading, pitch, roll)` on the glyph using the existing heading estimate (already computed in `update_tick` today) plus the eased bank as roll (and, optionally, a small pitch tilt derived from the *sign* of `rendered_altitude`'s current rate of change — a nice-to-have, not required for this phase's acceptance criteria; if you skip it, leave `pitch=0.0` and say so in a one-line comment).

## Part D — Tests

### `tests/test_viewer3d_flight_dynamics.py` (new file, Panda3D-free)

* `grep -rn "import panda3d\|from panda3d\|from direct" viewer3d/flight_dynamics.py` returns no matches — assert this via a test that reads the file's source and checks, or simply via a documented manual grep in your final summary; either is acceptable, but do not skip stating it was checked.
* `nearest_alive_target_distance` returns `None` when every target is destroyed (`hp_remaining == 0` for all), and the correct nearest distance otherwise, using a synthetic `TraceHeader`/`TraceTick` (construct these directly, the same style `tests/test_viewer3d_smoke.py`'s `_sample_header`/`_tick` helpers already use).
* `desired_altitude`: at `seconds_airborne=0.0`, returns a value at or very near `GROUND_ALTITUDE`; at `seconds_airborne >= TAKEOFF_CLIMB_SECONDS` with no nearby live target, returns a value at or very near `CRUISE_ALTITUDE`; with a live target within the engagement radius, returns a value below `CRUISE_ALTITUDE` (closer to `ATTACK_ALTITUDE`, strictly less than the far-away case with otherwise-identical inputs); with `just_struck=True`, returns a strictly higher value than an otherwise-identical call with `just_struck=False`.
* `desired_bank_deg`: `previous_altitude_target=None` returns exactly `0.0` (no false-positive bank on a jet's first tick); a climbing target (current > previous) and a diving target (current < previous) produce opposite-signed results; the magnitude is clamped and never exceeds `MAX_BANK_DEG` even with an artificially huge altitude delta and `dt_seconds`.
* `ease_toward`: moving `current` towards a `target` that's farther away than `max_rate_per_second * dt_seconds` moves by exactly that bounded amount (in the correct direction); moving towards a `target` that's closer than that bound lands exactly on `target` (no overshoot); `current == target` is a no-op.

### `tests/test_viewer3d_smoke.py` additions (extends Phases 16/17's file)

* Using the same headless `window-type none` config already established, drive a real `WraithNetBridge` + `LiveStreamServer` run (the existing `_tiny_scenario()` helper, or a new one with a target close enough to the jet's flight path to plausibly trigger the proximity-dive) through several ticks, step the Panda3D task manager (and real wall-clock `time.sleep`, since the easing step is wall-clock-driven, not tick-driven — you will need actual elapsed time between `task_mgr.step()` calls, not just repeated instantaneous steps) long enough to observe: the jet's rendered altitude is not the old fixed constant across every tick (assert it actually changes value at least once between two different points in the run), and it never goes negative or absurdly far above `CRUISE_ALTITUDE + POST_STRIKE_CLIMB_BOOST_ALTITUDE`.

## Explicit non-goals for this phase

* No gameplay effect whatsoever — nothing in `wraithnet/` reads, needs, or is aware of anything this phase computes.
* No true flight/aerodynamics simulation (no lift, stall, thrust, or drag model) — this is a small, deterministic, hand-tuned cosmetic state machine, not a physics engine.
* No terrain-following or ground-collision avoidance — the ground plane stays flat at `z=0`; `GROUND_ALTITUDE` is only ever touched during the takeoff-climb ramp.
* No pitch/attitude changes for targets, SAM sites, or jammers — they remain exactly the billboarded glyphs Phase 16 built; only jets change in this phase.
* No changes to jet removal/despawn behavior on destruction — still an instant `remove_node()` in this phase; the "falling wreck" effect is Phase 26's job.
* No change to `wraithnet/`, the trace wire protocol, or `viewer/` — grep-verifiable (`git diff --stat` should show changes only under `viewer3d/`, `tests/`, and `docs/`).

## Acceptance criteria

1. `pytest` passes, including the new `tests/test_viewer3d_flight_dynamics.py` and the extended `tests/test_viewer3d_smoke.py`.
2. `grep -rn "import panda3d\|from panda3d\|from direct" viewer3d/flight_dynamics.py` returns no matches.
3. `git diff --stat` (or equivalent) shows no changes outside `viewer3d/`, `tests/`, `docs/`.
4. Manually running `python -m wraithnet.main --live` then `python -m viewer3d.app 127.0.0.1 8765` (with a real display) shows jets visibly changing altitude over the course of a run — climbing after appearing, dipping when passing near a target, banking through turns — rather than flying dead-flat at a constant height. Note in your final summary if a display wasn't available to confirm this manually, and rely on the headless smoke test's altitude-changes-over-time assertion as the closest available proxy.
