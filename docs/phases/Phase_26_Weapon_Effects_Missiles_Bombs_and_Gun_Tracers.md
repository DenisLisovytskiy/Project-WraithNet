# Phase 26 — Weapon Effects: Missiles, Bombs & Gun Tracers

> **How to use this document:** This is a complete, standalone prompt. Give an LLM coding agent this entire file as its task. Assume Phase 16 is complete and passing. Read `docs/12_Flight_Dynamics_and_Weapon_Effects_Design_Document.md` §5 in full before starting. **This phase does not depend on Phase 25 or Phases 17–24** — see that design doc's §7 for the one soft interaction worth knowing about (`viewer3d/highlights.py` is also independently sketched by the unimplemented `docs/phases/Phase_17_Camera_Modes_HUD_and_Mission_Brief.md`; if that phase has already been implemented by the time you start this one, **do not create a second copy** — extend the existing file instead, keeping the exact shape this document's Part A describes). This phase touches only `viewer3d/`; it makes zero changes to `wraithnet/`, the trace wire protocol, or `viewer/`.

> **Automated GUI tests are required.** Follow `docs/14_Automated_GUI_Testing_Design_Document.md`. Every new or changed `viewer3d/` behavior this phase adds must ship with automated tests: Layer 1 (Panda3D-free `highlights.py`) where the decision can be extracted, Layer 2 (headless `window-type none` + `pytest.importorskip("panda3d")`) for effect spawn/cleanup and deferred jet removal. A manual visual check is in addition to, not instead of, those tests. Pixel/screenshot tests remain out of scope.

## Context

Today, a landed strike is invisible — `viewer3d/scene.py::update_tick` only changes a target's color/render-mode once its `hp_remaining` reaches zero; nothing visibly travels from the attacking jet to the target, and a destroyed jet simply vanishes from the scene the instant it disappears from `TraceTick.jets`. This phase adds: a weapon-type-appropriate projectile animation for every `StrikeEvent`, an explosion effect for every newly-destroyed target, and a falling-wreck-then-explosion effect for every destroyed jet (replacing its previous instant, silent removal). All of it is derived from data already streamed — no `wraithnet/` change, no trace schema change.

## Part A — `viewer3d/highlights.py` (new file, Panda3D-free — unless Phase 17 already created it)

**Check first**: if `viewer3d/highlights.py` already exists (because `docs/phases/Phase_17_Camera_Modes_HUD_and_Mission_Brief.md` was implemented before this phase), read it and confirm it already exposes the shape below; if so, skip straight to adding anything this phase needs that it's missing, and do not duplicate the file. If it does not exist yet, create it exactly as follows:

```python
"""Incremental (single-tick-at-a-time) equivalent of
viewer/playback.py::compute_highlights, adapted for a live stream where
ticks arrive one at a time rather than being pre-loaded into a list.
Deliberately does NOT import from viewer/ (viewer3d must not depend on the
Pygame viewer package) -- reimplements the same small amount of comparison
logic directly against TraceTick. No panda3d/direct import anywhere in this
file -- it is pure data comparison, unit-tested headlessly.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from wraithnet.trace_schemas import StrikeEvent, TraceTick


@dataclass(frozen=True)
class TickHighlights:
    strikes: list[StrikeEvent] = field(default_factory=list)
    newly_destroyed_target_ids: list[str] = field(default_factory=list)
    newly_destroyed_jet_ids: list[str] = field(default_factory=list)
    newly_jammed_jet_ids: list[str] = field(default_factory=list)
    newly_unjammed_jet_ids: list[str] = field(default_factory=list)


def compute_highlights(previous: TraceTick | None, current: TraceTick) -> TickHighlights:
    """previous=None (current is the very first tick ever seen) must produce
    an empty TickHighlights -- no false-positive "newly X" transitions just
    because there was nothing to compare against. Match
    viewer/playback.py::compute_highlights's field-level semantics exactly:
    - strikes: current.strikes, verbatim (already per-tick, not cumulative).
    - newly_destroyed_target_ids: target_id present as hp_remaining > 0 in
      `previous` (or absent from `previous` entirely -- e.g. first tick it's
      already destroyed) and hp_remaining == 0 in `current`.
    - newly_destroyed_jet_ids: agent_id present in `previous.jets` and absent
      from `current.jets`.
    - newly_jammed_jet_ids / newly_unjammed_jet_ids: agent_id present in both
      `previous.jets` and `current.jets` whose `jammed` flag flipped
      False->True / True->False respectively.
    """
    ...
```

Fill in `compute_highlights`. Build small `{id: frame}` lookup dicts for `previous`/`current` and diff them field-by-field per the docstring's rules — this is a direct, deliberately simple port of `viewer/playback.py`'s equivalent logic (Phase 13), just operating on one `previous`/`current` pair instead of a whole pre-loaded list, since `viewer3d` never has the whole list in memory at once (ticks are drained one at a time from `StreamClient.events`).

## Part B — `viewer3d/effects.py` (new file, Panda3D-dependent)

```python
"""Panda3D-backed weapon/destruction visual effects for viewer3d. Every
spawn_* function here builds a small node under `root`, attaches a
self-driving direct.interval Sequence (move/fade/scale over a fixed,
purely cosmetic duration), and cleans the node up itself via a trailing
Func callback -- no manual per-frame bookkeeping needed beyond what
Panda3D's own interval manager already provides. See
docs/12_Flight_Dynamics_and_Weapon_Effects_Design_Document.md §5.
"""

from __future__ import annotations

from direct.interval.IntervalGlobal import Func, LerpColorScaleInterval, LerpPosInterval, Parallel, Sequence
from panda3d.core import NodePath

# weapon_type -> ("gun_tracer" | "missile" | "bomb", duration_seconds).
# Unrecognized weapon_type values fall back to ("missile", 0.65) -- see
# design doc §5.2's table. Keep this a flat, easily-extended dict, not a
# chain of if/elif branches, so a future new WeaponType needs at most a
# one-line addition here.
WEAPON_EFFECT_STYLES: dict[str, tuple[str, float]] = {
    "JDAM": ("bomb", 1.1),
    "AIM9X": ("missile", 0.65),
}
DEFAULT_EFFECT_STYLE: tuple[str, float] = ("missile", 0.65)


def spawn_strike_effect(
    root: NodePath,
    weapon_type: str,
    launch_position: tuple[float, float, float],
    impact_position: tuple[float, float, float],
) -> None:
    """Looks up WEAPON_EFFECT_STYLES.get(weapon_type, DEFAULT_EFFECT_STYLE)
    and builds the corresponding archetype:
    - "gun_tracer": a thin LineSegs streak directly connecting the two
      positions, full-bright, fading out (LerpColorScaleInterval towards
      alpha 0) over the archetype's duration, then removed. No positional
      travel -- it is drawn once, in its final place, and only fades.
    - "missile": a small billboarded glyph (reuse the existing
      _attach_glyph-style construction from viewer3d/scene.py, or a
      simplified inline equivalent -- your choice, document it) that
      LerpPosInterval's from launch_position to impact_position over the
      archetype's duration, then triggers a small impact flash (reuse
      spawn_destruction_explosion with a smaller scale) and removes itself.
    - "bomb": same idea as "missile" but the position Lerp follows a
      gentle downward arc (e.g. a Sequence of two or three LerpPosIntervals
      through an intermediate waypoint sitting partway between
      launch/impact and a bit below a straight-line interpolation, rather
      than Panda3D's default linear Lerp) instead of a straight line, and
      a larger impact flash on arrival.
    Every archetype ends with a Func(node.remove_node) (or equivalent
    cleanup) so nothing leaks -- do not rely on a caller to clean these up.
    """
    ...


def spawn_destruction_explosion(root: NodePath, position: tuple[float, float, float], scale: float = 1.0) -> None:
    """A brief expanding-then-fading flash at `position` -- e.g. a small
    billboarded quad/disc that LerpScaleInterval's up while
    LerpColorScaleInterval fades its alpha to 0 over ~0.4-0.6s, then
    removes itself. `scale` lets callers make target-destruction explosions
    bigger than a routine impact flash (see Part C)."""
    ...


def spawn_jet_wreck(
    root: NodePath,
    position: tuple[float, float, float],
    on_complete,
) -> None:
    """A short 'falling wreck' effect: the jet's last-known position
    (including whatever altitude it was last rendered at, if Phase 25 is
    also implemented -- otherwise just the fixed cruise altitude it was
    always rendered at) drops towards the ground plane (LerpPosInterval to
    the same x/y but z=0) over roughly 0.8-1.0s while fading/tumbling
    (a LerpHprInterval spin is a nice touch, not required), then triggers
    spawn_destruction_explosion at the final position, then calls
    on_complete() -- the caller (Part C) uses this callback to finally
    remove the jet's own scene node, which it deliberately has NOT done
    yet by the time this function is called."""
    ...
```

Fill in every function using `direct.interval`'s `Sequence`/`Parallel`/`LerpPosInterval`/`LerpColorScaleInterval`/`Func` (import from `direct.interval.IntervalGlobal`, the standard aggregator module for these classes). Each spawned effect must be fully self-contained — starting it (`.start()`) is the only thing the caller (Part C) needs to do; the effect removes its own node when its sequence finishes. Do not add a new manual per-frame task for cleanup — that's exactly what Panda3D's own interval manager (driven by the app's existing task manager) already exists to do.

## Part C — Wiring into `viewer3d/app.py` and `viewer3d/scene.py`

* **`viewer3d/app.py`**: track `self._previous_tick: TraceTick | None = None`. Every time a `TickEvent` is drained, call `highlights.compute_highlights(self._previous_tick, event.tick)` **before** calling `update_tick` (so you still have the previous tick's jet positions available for effect launch points), then:
  * For each `strike` in `highlights.strikes`: resolve the launching jet's *current* (this-tick) position from `event.tick.jets` (fall back to skipping the effect gracefully if the jet is somehow already gone — e.g. destroyed the same tick it fired, which is possible) and the target's position from `self._header.target_positions[strike.target_id]`; call `effects.spawn_strike_effect(self.render, strike.weapon_type, launch_pos, impact_pos)`.
  * For each `newly_destroyed_target_id`: call `effects.spawn_destruction_explosion(self.render, self._header.target_positions[target_id], scale=1.5)` (bigger than a routine impact flash — see Part B's `scale` parameter).
  * For each jet present in `self._previous_tick.jets` but absent from `event.tick.jets` (this is exactly `highlights.newly_destroyed_jet_ids`): **do not** let `update_tick` remove that jet's node immediately (see the `scene.py` change below) — instead call `effects.spawn_jet_wreck(self.render, last_known_position, on_complete=lambda aid=agent_id: self._finish_removing_jet(aid))`.
  * Set `self._previous_tick = event.tick` at the end, same as before.
* **`viewer3d/scene.py::update_tick`**: change the jet-removal branch so it does **not** immediately `remove_node()`/pop from `handles.jets` when an `agent_id` disappears — that decision now belongs to `app.py` (which needs to spawn the wreck effect *before* the node goes away, and only remove it once the wreck effect's callback fires). The simplest change: add an optional parameter, e.g. `update_tick(handles, header, tick, on_jet_removed=None)`, where `on_jet_removed(agent_id, last_known_position)` is called instead of doing the removal inline, defaulting to the old immediate-removal behavior if the caller passes nothing (so any test/caller not yet updated keeps working unmodified — check `tests/test_viewer3d_smoke.py`'s existing direct calls to `update_tick` from Phase 16 still pass without changes). `app.py` passes a callback that spawns the wreck effect instead of removing the node; the actual `remove_node()`/dict cleanup moves into a small `WraithNetViewer3D._finish_removing_jet(self, agent_id)` method, invoked as `effects.spawn_jet_wreck`'s `on_complete`.

## Part D — Tests

### `tests/test_viewer3d_highlights.py` (new file, Panda3D-free — or "extend, don't recreate" if Phase 17 already made it)

* Port the equivalent cases from `tests/test_viewer_playback.py` (Phase 13) against `viewer3d/highlights.py::compute_highlights`, adapted for the single-previous/single-current signature: `previous=None` produces an empty `TickHighlights` (no false positives); a strike present in `current.strikes` is reported verbatim; a target transitioning from `hp_remaining > 0` (or absent) to `0` is detected; a jet present in `previous.jets` and absent from `current.jets` is detected; a jet's `jammed` flag flipping in each direction is detected correctly.
* `grep -rn "import panda3d\|from panda3d\|from direct" viewer3d/highlights.py` returns no matches.

### `tests/test_viewer3d_smoke.py` additions (extends Phase 16's file)

* Using the same headless `window-type none` config already established: a direct, synthetic-data test of `effects.spawn_strike_effect`/`spawn_destruction_explosion`/`spawn_jet_wreck` — call each, assert a new node was actually attached under the given root immediately after calling it, then advance enough simulated time (via repeated `task_mgr.step()` calls combined with real `time.sleep`, since intervals are wall-clock-driven — or, if this Panda3D version exposes a way to fast-forward an interval's clock directly, document and use that instead if it's more reliable than sleeping in a test) to let the effect's duration fully elapse, and assert the node has been removed/cleaned up afterward (no leaked nodes).
* An end-to-end run: drive a real `WraithNetBridge` + `LiveStreamServer` scenario engineered to produce at least one strike and one target destruction within a handful of ticks (reuse or adapt the existing `_tiny_scenario()`/rich-fixture pattern already used elsewhere in this test file), point a real `WraithNetViewer3D` at it, step the task manager through the whole run, and assert no exception is raised and that at least one strike-triggered effect spawn was observed (e.g. by monkeypatching/spying on `effects.spawn_strike_effect` for this one test, since asserting on transient visual node state directly is more fragile than asserting the call happened).
* A dedicated destroyed-jet test: force a jet's `hp` to `0` mid-run (same force-destruction pattern `tests/test_recorder.py::test_destroyed_jet_is_absent_from_subsequent_tick` already uses), confirm via `app._handles.jets` that the jet's node is **not** immediately gone the instant it disappears from the drained tick (i.e., prove the deferred-removal wiring actually defers), then advance enough time for the wreck effect to complete and confirm it *is* gone afterward.

## Explicit non-goals for this phase

* No real projectile physics — no gravity simulation, no miss chance, no collision detection. Every cosmetic projectile is guaranteed to "arrive" when its fixed duration elapses.
* No 3D asset pipeline, no textured missile/bomb/explosion models — procedural placeholder primitives only (billboarded quads/discs, `LineSegs` tracers, color/scale lerps), consistent with every other entity in this project.
* No sound effects.
* No change to `wraithnet/`, the trace wire protocol, or `viewer/`.
* No change to Phase 25's flight-dynamics logic beyond the one integration point noted in Part B (`spawn_jet_wreck` may read whatever altitude Phase 25 last rendered the jet at, if implemented — it must also work correctly if Phase 25 has *not* been implemented yet, falling back to whatever fixed altitude the jet was rendered at).
* No duplicate `viewer3d/highlights.py` — see Part A's "check first" instruction.

## Acceptance criteria

1. `pytest` passes, including the new/extended test files.
2. `grep -rn "import panda3d\|from panda3d\|from direct" viewer3d/highlights.py` returns no matches.
3. `git diff --stat` (or equivalent) shows no changes outside `viewer3d/`, `tests/`, `docs/`.
4. A destroyed jet is provably still present in the scene graph for at least one frame after it disappears from the drained tick stream (the deferred-removal test in Part D), and is provably gone once its wreck effect's duration has elapsed.
5. Manually running `python -m wraithnet.main --live` then `python -m viewer3d.app 127.0.0.1 8765` (with a real display) shows a visible projectile traveling from an attacking jet to its target on every logged strike, a distinct explosion when a target is destroyed, and a falling-wreck-then-explosion when a jet is destroyed, instead of any of these being instant/invisible. Note in your final summary if a display wasn't available to confirm this manually, and rely on the headless smoke tests as the closest available proxy.
