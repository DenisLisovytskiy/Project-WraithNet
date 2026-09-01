# Phase 18 — Scenario Editor

> **How to use this document:** This is a complete, standalone prompt. Give an LLM coding agent this entire file as its task. Assume Phases 0–17 are complete and passing (scenarios are data-driven and validated — Phase 14; `viewer3d` connects to a live stream and renders it with camera modes, HUD, and event highlights — Phases 16–17). Read `docs/09_3D_Live_Visualization_and_Scenario_Editor_Design_Document.md` §8 in full before starting. This phase adds a new mode to the *same* `viewer3d` application — not a new package, not a second GUI toolkit — for visually building/editing a `ScenarioConfig` (Phase 14) and saving/loading it as JSON. No live streaming/sim launching in this phase (Phase 19).

## Context

Phases 14–17 gave WraithNet a way to load a scenario from a file and watch it live in 3D. This phase gives a human a way to *build* that file in the first place, without hand-writing JSON: a point-and-click, top-down editor, inside the same window `viewer3d/app.py` already opens for live viewing.

## Part A — `viewer3d/editor.py` (new file)

```python
"""Scenario editor mode for viewer3d. A top-down orthographic camera (not
the perspective 'Ace Combat' camera used for live/brief modes -- design doc
§8) plus DirectGUI panels for adding/editing/deleting jets, targets, SAM
sites, and jammers, and Save/Load buttons wired to wraithnet.scenario_io.

Precision-first: the sim's own coordinate space is 2D, so an orthographic
top-down view maps 1:1 onto "click here to place this entity" with no
depth ambiguity -- deliberately simpler than the live-view camera, not a
missing feature.
"""

from wraithnet.scenario_schema import JammerSpec, JetSpec, SamSiteSpec, ScenarioConfig, TargetSpec
from wraithnet.scenario_io import load_scenario, save_scenario


class OrthoEditorCamera:
    """Fixed top-down orthographic projection over the whole grid, panned/
    zoomed with the mouse (scroll to zoom, middle-drag or arrow keys to
    pan). No orbit, no tilt -- this is a 2D placement tool wearing a 3D
    engine, not a 3D placement tool."""
    def __init__(self, base, grid_width: float, grid_height: float) -> None: ...
    def screen_to_world(self, mouse_x: float, mouse_y: float) -> tuple[float, float]:
        """Inverse-projects a screen-space click into a (world_x, world_y)
        point on the z=0 ground plane -- the core operation every 'place an
        entity here' interaction in this phase depends on."""
        ...


class ScenarioEditor:
    def __init__(self, base) -> None:
        self.base = base
        self.scenario_draft = ScenarioConfig(jets=[], targets=[])  # start empty, or...
        self.camera = OrthoEditorCamera(base, self.scenario_draft.grid_width, self.scenario_draft.grid_height)
        self._build_panels()  # DirectGUI: entity list, add-entity buttons, save/load, validation banner

    def _build_panels(self) -> None: ...

    def begin_placement(self, entity_kind: str) -> None:
        """Called when the user clicks 'Add Jet' / 'Add Target' / etc. --
        arms 'placement mode': the next click on the ground plane places a
        new entity of this kind at that world position, then opens its
        property form (payload for a jet; target_type/hp_required for a
        target/SAM site; nothing extra for a jammer)."""
        ...

    def on_ground_clicked(self, world_pos: tuple[float, float]) -> None:
        """Dispatches to either 'place a new entity here' (if placement
        mode is armed) or 'select the nearest existing entity' (otherwise,
        for editing/deleting)."""
        ...

    def open_property_form(self, entity) -> None:
        """DirectGUI form for editing one entity's fields in place. On
        submit, re-validates the WHOLE draft scenario (not just this one
        entity) by attempting ScenarioConfig(**updated_data) and catching
        ValidationError -- see Part B."""
        ...

    def delete_entity(self, entity_id: str) -> None: ...

    def save(self, path: str) -> "list[str] | None":
        """Attempts to build a ScenarioConfig from the current draft state
        and save it via wraithnet.scenario_io.save_scenario. Returns None
        on success, or a list of human-readable validation error messages
        on failure (surfaced in the UI -- see Part B). Does NOT write a
        partially-invalid file."""
        ...

    def load(self, path: str) -> None:
        """Loads a ScenarioConfig via wraithnet.scenario_io.load_scenario
        and replaces the current draft entirely, rebuilding the visual
        placement of every entity to match."""
        ...
```

Fill in every method. Key implementation notes:

* **The "draft" scenario is not necessarily a valid `ScenarioConfig` at every intermediate moment** (e.g. while the user has only placed a target and no jet yet — Phase 14's validator requires at least one jet). Do not try to keep a live `ScenarioConfig` instance around at all times; keep the draft as simpler internal state (e.g. plain lists/dicts of in-progress entity data) and only attempt to *construct* a real `ScenarioConfig` (and thus run its validators) at well-defined moments: on `save()`, and on `preview()` (Part C). This avoids fighting Pydantic's validation at every single keystroke/click.
* **Reuse Phase 14's validators — do not re-implement any of the five rules** (unique IDs, in-bounds positions, `hp_required >= 1`, non-negative payloads, at least one jet) anywhere in `editor.py`. The only place validation logic should exist is `wraithnet/scenario_schema.py::ScenarioConfig`'s `model_validator`. `save()`/`preview()` simply attempt construction and catch `pydantic.ValidationError`, translating `.errors()` into the human-readable list `save()` is specified to return above.
* **Ground-plane click-to-place** depends on `OrthoEditorCamera.screen_to_world` being correct — test this thoroughly and in isolation (Part D) since every other interaction in this phase builds on it.

## Part B — Validation-error surfacing (DirectGUI)

A small, persistent "validation banner" area in the editor's panel layout (Part A's `_build_panels`) that:

* Is empty/hidden when the last `save()`/`preview()` attempt succeeded (or none has been attempted yet).
* Shows each message from `save()`'s returned error list as a separate line, in plain scenario-builder language — e.g. "Target 'Target_A' and Jet 'Target_A' cannot share the same ID," "Jammer 'EW_1' is placed at (120, 40), outside the 100x100 grid," "At least one jet is required" — not raw Pydantic `ValidationError` repr text. This means translating each `pydantic.ValidationError` entry's `loc`/`msg`/`input` into a sentence; write a small `_humanize_validation_error(err: dict) -> str` helper rather than displaying `str(exc)` directly.

## Part C — Preview (reuses Phase 17's mission-brief mode, does not reimplement it)

```python
def preview(self) -> "list[str] | None":
    """Attempts to construct a ScenarioConfig from the current draft (same
    validation path as save(), see Part A/B). On success, builds a
    TraceHeader-shaped object directly from that ScenarioConfig (no sim,
    no socket, no ticks -- purely local data transformation) and feeds it
    to the exact same viewer3d.scene.build_static_scene(...) Phase 16
    already implements, then switches view_mode to "brief" so Phase 17's
    MissionBriefCamera takes over -- see design doc §7. Returns the same
    error-list-or-None shape as save() if validation fails.
    """
    ...
```

The one new piece of glue this needs: a small function (e.g. `viewer3d/editor.py::scenario_to_header(config: ScenarioConfig) -> TraceHeader`) that fills in every `TraceHeader` field from a `ScenarioConfig` directly (no bridge, no model, no sinks involved) — compare field-by-field against how `wraithnet/live_stream.py`'s shared builder (Phase 15, Part B) constructs a `TraceHeader` from a live `WraithNetBridge`, and make sure every field has a sensible source in `ScenarioConfig` alone (e.g. `initial_payload` comes directly from each `JetSpec.payload`; `sensor_radius`/`jam_radius`/`sam_engagement_range`/`strike_range` come from `wraithnet.config`'s constants, since those aren't currently part of `ScenarioConfig` and this phase should not expand that schema without a good reason — flag this explicitly rather than silently duplicating/hardcoding a second copy of these constants).

A "Preview" button in the editor's panel triggers this; a "Back to editing" control (button or `Esc`) returns `view_mode` to `"editor"` and restores the `OrthoEditorCamera`.

## Part D — Tests

Panda3D-heavy phase — most of this needs the same headless windowless config established in Phase 16.

### `tests/test_viewer3d_editor.py` (new file)

* `OrthoEditorCamera.screen_to_world` round-trips correctly for at least the four corners and the center of a known grid size (construct the projection, project a known world point to screen space via whatever Panda3D gives you for the inverse operation, or — simpler and more robust — pick a handful of hand-computed screen coordinates for a known camera configuration and assert `screen_to_world` returns the expected world coordinates within a small tolerance).
* `begin_placement("jet")` followed by `on_ground_clicked((30.0, 40.0))` results in exactly one new jet in the draft at that position; repeating for `"target"`/`"sam_site"`/`"jammer"` similarly adds the right kind of entity.
* `save()` on a draft missing any jets returns a non-`None` error list containing a human-readable message about needing at least one jet, and does **not** create/overwrite the target file.
* `save()` on a valid draft returns `None` and the resulting file, loaded via `wraithnet.scenario_io.load_scenario`, matches the draft's entities exactly.
* `load(path)` on a previously-saved file fully replaces the draft (old entities gone, new ones present) and re-populates the visual scene to match (assert against the underlying node count/positions, whichever Phase 16/17's scene module exposes for this kind of check).
* `_humanize_validation_error` produces a non-empty, non-stack-trace-looking string for each of Phase 14's five validation rules (feed it synthetic `pydantic.ValidationError` instances covering each rule, e.g. via `ScenarioConfig.model_validate({...invalid...})` wrapped in `pytest.raises`).
* `preview()` on a valid draft results in `view_mode == "brief"` and a scene built from the draft's data (same assertions as Phase 17's mission-brief smoke test, but sourced from an editor draft instead of a live header) — this is the test that proves the "preview reuses mission-brief, doesn't reimplement it" claim in Part C.
* `preview()` on an invalid draft returns the error list and does **not** change `view_mode`.
* `scenario_to_header` produces a `TraceHeader` whose fields match a hand-constructed expectation for a small representative `ScenarioConfig` (one jet, one target, one SAM site, one jammer) — covering the "where do sensor_radius/jam_radius/etc. come from" question explicitly (Part C).

## Explicit non-goals for this phase

* No sim launching, no subprocess spawning, no "Launch" button behavior — Phase 19.
* No true free-form 3D placement (altitude editing, tilted terrain, etc.) — the editor is deliberately flat/2D-in-a-3D-engine, per Part A.
* No undo/redo stack — delete-and-re-add is the only "undo" available this phase.
* No multi-scenario tabs/workspace — one draft scenario in memory at a time; loading a new one discards the current draft (optionally with an "unsaved changes" warning if you want to add one, but it is not required).
* No expansion of `ScenarioConfig`'s fields (e.g. do not add `sensor_radius` to the schema just to avoid Part C's "where does this come from" question — flag it as noted, don't silently work around it by changing Phase 14's schema).
* No change to `wraithnet/` — this phase only reads `wraithnet.scenario_schema`/`scenario_io`/`config`, all already public, stable APIs from Phase 14.

## Acceptance criteria

1. `pytest` passes, including the new test file (headless Panda3D config).
2. Manually running `python -m viewer3d.app` in editor mode (exact entrypoint/flag for entering editor-vs-live mode is this phase's call — document whichever you choose, e.g. a `--edit` flag or a startup menu) lets a human place a jet, a target, a SAM site, and a jammer by clicking on the ground plane, fill in each one's properties, see a validation error if they delete all jets, save a valid scenario to a file, and reload it later with everything intact. Note in your final summary if a display wasn't available to confirm this manually.
3. A scenario saved by this phase's editor loads and runs correctly via `python -m wraithnet.main --scenario <that file>` (Phase 14), producing a sensible (not necessarily "solved," depending on what was placed) run.
4. `grep -rn "hp_required\s*[<>=]\|len(.*jets.*)\s*==\s*0" viewer3d/editor.py` turns up no hand-rolled validation logic duplicating Phase 14's `ScenarioConfig` validators (a light-touch check that Part A's "don't re-implement validation" rule was actually followed — use your judgment on what this grep should actually look like given your implementation, the point is: validation logic lives in exactly one place).
