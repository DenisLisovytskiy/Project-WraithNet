# Phase 24 — Scenario Editor, Viewers & Trace Schema Updates for Airframes

> **How to use this document:** This is a complete, standalone prompt. Give an LLM coding agent this entire file as its task. Assume Phases 0–23 are complete and passing (jets have named airframes, independent heading, fuel, autonomous RTB; weapons have range/arc/slot-cost; metrics/termination correctly account for all three jet outcomes). Read `docs/10_Airframes_Weapons_and_Fuel_Design_Document.md` §7 and §8, plus `docs/09_3D_Live_Visualization_and_Scenario_Editor_Design_Document.md` (both viewers' existing designs) in full before starting. **This is the final phase of the Phase 4 Expansion.** It makes every mechanic from Phases 20–23 visible and editable — extending the trace schema (Phase 11), both viewers (`viewer/` Phases 12–13, `viewer3d/` Phases 16–17), and the scenario editor (`viewer3d/editor.py`, Phase 18). No new simulation behavior — purely observational/editorial, exactly like Phases 11–19 were relative to the mechanics they visualized.

> **Automated GUI tests are required.** Follow `docs/14_Automated_GUI_Testing_Design_Document.md`. Every new or changed `viewer3d/` (and `viewer/` if touched) behavior this phase adds must ship with automated tests: Layer 1 (Panda3D-free pure logic) where the decision can be extracted, Layer 2 (headless `window-type none` + `pytest.importorskip("panda3d")`) for scene/HUD/editor/app wiring. A manual visual check is in addition to, not instead of, those tests. Pixel/screenshot tests remain out of scope.

## Context

Phases 20–23 added heading, fuel, airframes, and a third jet outcome (returned-to-base) — none of it is visible in either viewer, and none of it is settable in the editor yet. This phase closes that gap, following the same "extend the shared trace schema once, both viewers and the recorder/live-stream builder consume it identically" pattern Phases 11–17 already established.

## Part A — `wraithnet/trace_schemas.py` changes (Phase 11)

```python
class TraceHeader(BaseModel):
    ...  # existing fields unchanged
    home_base: tuple[float, float]                          # NEW
    jet_frame_types: dict[str, JetFrameType]                 # NEW -- agent_id -> frame_type
    weapon_ranges: dict[WeaponType, float]                   # NEW -- for arc/range overlays without hardcoding config lookups client-side
    weapon_firing_arcs_deg: dict[WeaponType, float]           # NEW

class JetFrame(BaseModel):
    ...  # existing fields unchanged
    heading: float                    # NEW
    fuel: float                       # NEW
    outcome: Optional[Literal["destroyed", "returned_to_base"]] = None  # NEW -- see note below
```

**On `JetFrame.outcome`**: per Phase 11's existing, deliberate convention (`destroyed: bool` always `False` because destroyed jets are never emitted as a `JetFrame` at all, only absent from the list), a *landed* jet is likewise absent from `TraceTick.jets` from the tick it lands onward — **do not add a "ghost" frame for a landed or destroyed jet**. Instead, `outcome` is populated **once**, on the final `TraceTick` frame the jet ever appears in has already happened — actually reconsider: since a removed jet produces no further frames at all, there is no frame on which to attach `outcome` after the fact. Resolve this properly: add a **new, separate, small structure** instead of overloading `JetFrame`:

```python
class JetOutcomeEvent(BaseModel):
    agent_id: str
    tick: int
    outcome: Literal["destroyed", "returned_to_base"]

class TraceTick(BaseModel):
    ...  # existing fields unchanged
    jet_outcomes: list[JetOutcomeEvent] = []   # NEW -- populated only on the tick a jet leaves play
```

Remove the incorrect `JetFrame.outcome` sketch above — use `TraceTick.jet_outcomes` instead, populated by the recorder/live-stream builder (Part B) from `model.jet_outcomes` (Phase 23) by checking which agent IDs are new in `model.jet_outcomes` since the previous tick (mirrors exactly how `StrikeEvent`s are already filtered to "landed this tick, not cumulative" — reuse that same per-tick-diffing discipline, do not regress into a cumulative dump).

## Part B — `wraithnet/recorder.py` / `wraithnet/live_stream.py` shared builder changes (Phases 11/15)

Wherever the shared `TraceHeader`/`TraceTick` builder function lives (Phase 15 factored this out of `TraceRecorder` — find it, do not reintroduce duplicate construction logic in two places):

* `TraceHeader`: populate `home_base` from `model._home_base` (Phase 22), `jet_frame_types` from each live jet's `frame_type` at header-write time (header-write happens at tick 0, before any jet could have already left play, so this is safe to read from `model.jets` directly), `weapon_ranges`/`weapon_firing_arcs_deg` from `config.WEAPON_CATALOG` (Phase 21) — a straightforward dict comprehension over `WEAPON_CATALOG.items()`.
* `TraceTick.jets`: each `JetFrame` gains `heading=jet.heading`, `fuel=jet.fuel`.
* `TraceTick.jet_outcomes`: for each `agent_id, outcome in model.jet_outcomes.items()`, if this is the tick it was just added (track a `_previously_seen_outcome_ids` set on the recorder/live-stream server instance itself, incrementally, the same per-tick-diffing pattern `_apply_strike`'s `tick == model.tick` filter already uses elsewhere — do not recompute a diff against the entire trace history every tick), emit a `JetOutcomeEvent(agent_id=agent_id, tick=model.tick, outcome=outcome.outcome)`.

## Part C — `viewer/render.py` changes (2D Pygame viewer, Phases 12–13)

* **Home base marker**: a fixed icon (e.g. a small house/flag glyph, or simply a distinctly-colored square, your call) drawn at `header.home_base`, always visible, analogous to how the jammer (Phase 12) is always drawn regardless of tick.
* **Heading indicator**: replace/extend the jet's existing filled-circle glyph with a short line or small triangle extending from the jet's center in the direction of `frame.heading` (convert the sim's heading convention — Phase 20's choice — into screen-space rotation, accounting for the 2D viewer's existing y-flip, `viewer/render.py::world_to_screen`'s mapping). This is a **new, real signal** (previously there was no heading concept in the 2D viewer at all — Phase 12/13 never needed one).
* **Fuel gauge**: a second small bar above/below the existing HP bar (`HP_BAR_WIDTH`/`HP_BAR_HEIGHT`-style constants — add analogous `FUEL_BAR_*` constants), showing `jet.fuel / JET_FRAME_CATALOG[frame_type].fuel_capacity` as a fraction, styled distinctly from the HP bar's color scheme (e.g. a blue/cyan gradient instead of green-yellow-red) so the two are never confused.
* **RTB status**: a jet whose most recent observed role (already tracked via `JetFrame.role`, Phase 11) is `"RTB"` gets a distinct visual treatment — reuse the existing `ROLE_RING_COLOR` dict (Phase 12) and simply add an `"RTB"` entry to it (e.g. a distinct blue) rather than inventing a new visual language for this one role.
* **`draw_end_of_run_overlay`**: update to show the three-way jet outcome split (`jets_destroyed` / `jets_returned_to_base` / `jets_still_flying`, per Phase 23's `metrics.py` rename) instead of the old two-way `jets_destroyed`/`jets_survived`, and the new `mission_aborted` outcome title case (alongside the existing `swarm_wiped_out`/`solved` cases in `draw_end_of_run_overlay`'s title logic).
* **Firing-arc cone (optional toggle, recommended but not required)**: for a selected/hovered jet, draw a translucent wedge from its position, centered on its heading, spanning its currently-committed weapon's `firing_arc_deg` (from the new `TraceHeader.weapon_firing_arcs_deg`) out to that weapon's `range` (`TraceHeader.weapon_ranges`) — reuse the existing sensor-radius-toggle pattern (key `S`, Phase 12) for a new toggle key if you add this; if you judge it too much clutter for the existing HUD, it is acceptable to skip and note as deferred, since the design doc does not mandate this specific visual.

## Part D — `viewer3d/scene.py`/`cameras.py`/`hud.py` changes (3D viewer, Phases 16–17)

* **Jet orientation now uses the real `heading` field, not a position-delta estimate.** Phase 16's original `update_tick` derived a jet's facing direction from the displacement between its previous and current position (a reasonable placeholder at the time, explicitly noted as such). Replace that with `TraceTick.JetFrame.heading` directly — this is strictly more correct (a jet can now face a direction different from its travel direction, per the whole point of Phases 20–21's independent-heading/firing-arc mechanic) and removes the old estimate entirely, it does not run alongside it.
* **Home base marker**: a distinct billboard glyph (per `docs/09_..._Design_Document.md` §6.3's style — a simple, recognizable icon, e.g. a flag/house glyph) placed at `header.home_base`, always visible, built once in `build_static_scene` alongside the other static header-derived geometry (ground plane, jammer, targets).
* **Fuel bar**: alongside each jet's existing HP indicator (however Phase 16/17 chose to render that — a billboard bar, a HUD-panel readout, or both), add an equivalent fuel readout using `TraceTick.JetFrame.fuel` / `JET_FRAME_CATALOG[frame_type].fuel_capacity` (note: `viewer3d` cannot import `wraithnet.config` directly per the isolation principle — it must get `fuel_capacity` from somewhere in the trace data; if `TraceHeader` doesn't already expose per-jet frame stats beyond `jet_frame_types` — Part A only added the *type name*, not the numeric stats — add a `jet_frame_stats: dict[str, dict]` or similar to `TraceHeader` carrying whatever numeric fields the viewer needs (at minimum `fuel_capacity`, `max_speed`, `hp` for potential future use) rather than hardcoding a guessed capacity client-side or improperly importing `wraithnet.config` into `viewer3d/`).
* **RTB visual state**: extend the `Hud`'s force-status/mission-info panels (Phase 17, Part B) to show a jet's `"RTB"` role distinctly (reuse the existing role→color convention already shared with the 2D viewer per Phase 17's design), and extend `viewer3d/highlights.py::compute_highlights` (Phase 17, Part D) with a new detected transition — `newly_rtb_jet_ids` — analogous to `newly_jammed_jet_ids`, so a jet beginning RTB gets a brief visual flash exactly like a jam-state transition does.
* **End-of-run overlay / `RunSummary`-equivalent**: update whatever Phase 17 built for the 3D viewer's end-of-run summary (mirroring Phase 13's `viewer/playback.py::RunSummary` but for `viewer3d`, if a separate one exists — check) to show the same three-way jet-outcome split and `mission_aborted` case as Part C's 2D update.

## Part E — `viewer3d/editor.py` changes (Phase 18)

* **Frame-type picker**: the jet property form (Phase 18, Part A's `open_property_form`) gains a dropdown/selector over `JetFrameType` (the fixed catalog, per the design doc §1's decision — not a free-form stat form), defaulting to `"STRIKE_FIGHTER"`.
* **Home base placement**: a new "Place Home Base" tool in the editor's panel (alongside the existing "Add Jet"/"Add Target"/etc. flows, Phase 18 Part A) that lets the user click the ground plane once to set `ScenarioConfig.home_base` — there is only ever one, so placing a new one moves the existing marker rather than adding another (distinct from every other entity type, which supports multiple instances).
* **Ordnance-slot capacity feedback**: since Phase 21 added a slot-cost validator to `ScenarioConfig`, the editor's payload-editing form (already built in Phase 18) should show a live "X / Y slots used" indicator as the user adjusts a jet's payload counts, keyed off the jet's currently-selected `frame_type`'s `ordnance_slots` — this reads the same `WEAPON_CATALOG`/`JET_FRAME_CATALOG` data the validator already uses (via `wraithnet.config`, which `viewer3d/editor.py` already imports per Phase 18's own design — no new dependency), it does not reimplement the validation logic, only previews it before the user hits Save.

## Part F — Tests

### `tests/test_trace_schemas.py` / `tests/test_recorder.py` additions (Phase 11)

* `TraceHeader.home_base`/`jet_frame_types`/`weapon_ranges`/`weapon_firing_arcs_deg`/`jet_frame_stats` round-trip correctly and match the bridge's actual scenario/config data for a real run.
* `TraceTick.jets[i].heading`/`fuel` match the corresponding `JetAgent`'s live state at record time.
* `TraceTick.jet_outcomes` contains exactly one `JetOutcomeEvent` for a jet forced to land or be destroyed, on the correct tick, and that agent_id never appears in any later tick's `jet_outcomes` again (per-tick, not cumulative — the same regression discipline Phase 11 already required for `StrikeEvent`s, re-applied here).

### `tests/test_viewer_render.py` / `tests/test_viewer_smoke.py` additions (2D viewer, Phases 12–13)

* A heading-indicator drawing function (however you structure it — a dedicated `draw_heading_indicator` or folded into the existing `draw_jets`) produces a plausible screen-space direction for at least one hand-computed heading/position case, accounting for the existing y-flip.
* The fuel bar renders proportionally to `fuel/fuel_capacity` without raising, across `0.0`, a mid-range value, and `1.0`.
* `draw_end_of_run_overlay` with a synthetic `RunSummary`-equivalent showing a nonzero `jets_returned_to_base` and `mission_aborted=True` renders the correct title/case without raising.

### `tests/test_viewer3d_smoke.py` / `tests/test_viewer3d_highlights.py` additions (3D viewer, Phases 16–17)

* A jet's rendered orientation matches its `TraceTick.JetFrame.heading` directly (not a position-delta estimate) — construct a case where heading and travel direction deliberately differ (a jet crabbing sideways) and assert the rendered orientation follows `heading`, proving the old estimate was actually removed, not just deprioritized.
* `compute_highlights` detects a `newly_rtb_jet_ids` transition correctly, mirroring the existing jam-transition test's structure.
* The home base marker is present in the static scene built from a `TraceHeader` alone (mission-brief mode, Phase 17 §7) — no tick needed, exactly like every other static header-derived entity.

### `tests/test_viewer3d_editor.py` additions (Phase 18)

* Placing a jet with a non-default `frame_type` via the editor and saving/reloading round-trips the `frame_type` correctly.
* Placing/moving the home base results in exactly one `home_base` value in the saved `ScenarioConfig` (never a list, never left unset if the user placed one) — and a scenario saved *without* ever placing a home base fails validation with a clear message if `ScenarioConfig.home_base` has no default (verify whether Phase 22 gave it a default — if not, this is the right place to catch that at save-time with a friendly error, matching Phase 18's existing validation-surfacing convention, rather than letting `load_scenario` fail later with a raw Pydantic error).
* The live ordnance-slot-usage indicator updates correctly as payload counts change in the form, and matches what `ScenarioConfig`'s own validator would ultimately accept or reject for the same input.

## Explicit non-goals for this phase

* No new simulation mechanics — this phase is purely observational/editorial, exactly like Phases 11–19 relative to what they visualized.
* No requirement to implement the optional firing-arc-cone overlay in the 2D viewer (Part C) if it's judged too cluttered — explicitly optional, not a hard acceptance criterion.
* No change to `wraithnet/` beyond what's strictly needed to expose already-existing Phase 20–23 state through the trace schema (Part A/B) — if you find yourself needing new simulation-side state that Phases 20–23 didn't already produce, stop and reconsider whether this phase's scope has crept.

## Acceptance criteria

1. `pytest` passes in full, including all new/extended tests across both viewers, the editor, and the trace-schema/recorder layer.
2. `python -m wraithnet.main --live` followed by `python -m viewer3d.app 127.0.0.1 8765` (or the equivalent 2D `python -m viewer.app results/trace.jsonl` flow) visibly shows: jets oriented by their real heading (not travel direction), fuel gauges depleting over the run, the home base marker, and — for a scenario engineered to force an RTB — a jet visibly detaching from its task, flying home, and landing, with the end-of-run overlay correctly reporting it as returned-to-base rather than destroyed or still-flying. Note in your final summary if a display wasn't available to confirm this manually, and rely on the headless smoke tests as the closest available proxy.
3. The scenario editor (`viewer3d/editor.py`) lets a user pick a jet's airframe from the fixed catalog, place the scenario's single home base, and see a live ordnance-slot-usage preview while editing payload — a scenario built entirely through the editor with a non-default frame type and a custom home base saves, reloads, and runs correctly via `python -m wraithnet.main --scenario <that file>`.
4. `grep -rn "position_delta\|previous_position" viewer3d/scene.py` (or whatever the old heading-estimation logic was actually named) returns no matches — confirming the old estimate was removed, not left running alongside the new real heading data.
