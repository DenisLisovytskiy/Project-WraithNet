# Phase 33 — Quick Generate GUI & Manual-Editor Handoff

> **LLM implementation prompt:** Assume Phases 30–32 are complete and passing. Read `docs/18_Random_Scenario_Generation_Design_Document.md` §§0–1, 11–14 and inspect the current `viewer3d/editor.py` before coding. Add a few-click random-scenario workflow to the existing editor while preserving every manual placement/edit/save/load/preview/launch feature. Generated output must become the normal editor draft; do not build a separate “random mode” simulator or viewer.

## Goal

A first-time user can open `python -m launcher`, choose Standard, click Generate, inspect the map, and click Launch. An advanced user can expand parameters or define exact friendly counts/frames/loadouts, generate positions/enemies, then continue using the existing deep manual editor.

## Part A — UI structure

Add a separate right-side or collapsible `DirectFrame` named **QUICK GENERATE**. Do not squeeze generation controls into the existing entity/property panel.

Compact controls:

* Difficulty button cycling `TRAINING → STANDARD → CONTESTED → NIGHTMARE`.
* Seed entry displaying an unsigned integer.
* **New Seed** button (uses `secrets.randbits(64)` in the UI only).
* Friendly mode button cycling:
  * `AUTO-BALANCED`
  * `EXPERIMENTAL RANDOM`
  * `CUSTOM ROSTER`
* **Generate** button.
* **Advanced…** expand/collapse.
* **Undo Generation** button, hidden/disabled until available.

Status area:

* generated seed;
* challenge score;
* counts: jets/objectives/SAMs/jammers;
* `Statically feasible` or warning/error count;
* concise first issue plus **Details** toggle/list.

Use existing palette/button helpers. Pixel-perfect layout is manual-only, but every widget command and state transition requires a headless test.

## Part B — editor-side state

`ScenarioEditor` gains:

```python
self._generation_parameters: GenerationParameters
self._generation_report: FeasibilityReport | None
self._generation_undo_snapshot: EditorSnapshot | None
self._generation_in_progress: bool
self._draft_dirty: bool
self._pending_generation_result: GenerationResult | None
self._pending_generation_action: Literal["REPLACE", "USE_ANYWAY"] | None
```

`EditorSnapshot` must deep-copy:

* `scenario_draft`;
* current path;
* selected entity identity if restorable;
* generation metadata/report;
* relevant camera framing if practical.

One-level undo is sufficient. Do not retain unbounded generated drafts.

Every existing manual mutation path—placement, Apply, Delete, home-base move—calls one helper:

```python
def _mark_manual_edit(self) -> None:
    self._draft_dirty = True
    if generation metadata exists:
        metadata.modified_after_generation = True
```

Every successful Load sets `_draft_dirty=False`, regardless of metadata: `modified_after_generation=True` means the saved scenario has historical manual changes, not that the just-loaded editor has unsaved changes. Saving also clears `_draft_dirty` but never resets `modified_after_generation`.

## Part C — preset and advanced fields

Selecting a preset calls `parameters_for_preset(preset, seed=...)` and repopulates all visible advanced fields.

Advanced fields:

* objectives/SAMs/jammers;
* objective HP min/max;
* cooperation fraction;
* group style;
* allowed group templates and objective-distance min/max fractions;
* threat overlap;
* base safety, launch-threat clearance, and group/entity separation;
* friendly min/max;
* reserve factor.

Rules:

* Editing any advanced value sets displayed preset to `CUSTOM`.
* Parsing/validation uses `GenerationParameters`; do not reproduce numeric bounds in GUI conditionals.
* Validation errors are humanized through the editor's existing banner conventions.
* The resolved parameter summary is visible before Generate; difficulty is never hidden magic.

## Part D — custom roster editor

Visible only in `CUSTOM_ROSTER`.

Each row contains:

* count;
* frame type cycle/dropdown;
* one count entry for every current `WEAPON_CATALOG` key, generated dynamically;
* `X / Y slots` live feedback;
* remove-row button.

Include **Add roster row**.

Requirements:

* No hardcoded JDAM/AIM9X-only form.
* Payload keys must come from `WEAPON_CATALOG`.
* Slot preview may call the existing `ordnance_slots_used()` helper, but final validation is `FriendlyTemplate`/`GenerationParameters`.
* Expanding templates never silently merges rows; two identical rows may remain distinct inputs but produce deterministic concrete IDs.
* A custom roster can be valid yet statically infeasible; Generate then requires confirmation rather than silently changing it.

## Part E — generation command

Flow:

1. Parse widgets into resolved `GenerationParameters`.
2. If `_draft_dirty` or the draft contains any jet/target/SAM/jammer entity, show a confirmation state: **Generate will replace the current draft; Save first or confirm Replace**. A fresh editor's dimensions/home base alone do not count as content, so its first Generate remains one click.
3. Capture undo snapshot.
4. Call `generate_scenario(parameters)`.
5. On `GenerationError`/Pydantic error, retain the old draft and show actionable messages.
6. If mode is Auto-balanced and report is not feasible, treat this as an internal generation failure; do not install the draft.
7. If Random/Custom is infeasible, store it in `_pending_generation_result`, set the pending action to `USE_ANYWAY`, show issues, and require **Use Anyway** before installing. Clear pending state after acceptance, cancellation, a new request, load, or shutdown.
8. On acceptance, replace `scenario_draft` with the concrete generated scenario, rebuild scene/entity list/camera bounds, clear selected entity, store report/metadata, and mark the draft unsaved but not manually modified.

### Threading decision

First measure Phase 32 generation. If normal maximum is reliably below 100 ms, synchronous generation is acceptable. Otherwise:

* run only the pure `generate_scenario()` call on a worker thread;
* return a result/error through a `queue.Queue`;
* poll from a Panda3D task;
* never mutate DirectGUI or scene nodes from the worker;
* disable Generate while one request is active;
* discard stale results using a monotonically increasing request ID.

Document the measured choice in code/tests. Do not add background complexity without measurement.

## Part F — undo and manual handoff

**Undo Generation** restores the snapshot atomically and rebuilds scene/list/form state. It is invalidated by:

* another generation (replaced with the immediately previous snapshot);
* loading a scenario;
* optionally saving after extensive manual edits—choose one clear rule and show it in the UI.

After generation:

* all normal entity list selections work;
* jets expose all weapon keys and frame type;
* users can move/delete/add entities manually;
* home base can be moved;
* Save writes a normal scenario JSON;
* Load does not regenerate;
* Preview uses generated concrete state;
* Launch follows the existing save → subprocess → live-stream path.

Do not add a generator-specific Launch button.

## Part G — generation metadata behavior

When generated:

```python
generation.modified_after_generation = False
```

After any manual entity/home/loadout edit:

```python
generation.modified_after_generation = True
```

The UI displays:

* `Generated: Standard, seed 12345` when unchanged;
* `Generated then manually modified: seed 12345` after edits.

Loading a generated scenario restores this display from metadata. Loading an ordinary scenario shows `Manual scenario`.

The **Regenerate same** action is optional but recommended: if implemented, it uses `generation.resolved_parameters` exactly and still goes through replacement confirmation.

## Part H — tests

Extend/create:

* `tests/test_viewer3d_editor.py`
* `tests/test_viewer3d_smoke.py`
* `tests/test_scenario_io.py`

Layer-1/editor-state tests:

1. Preset selection populates expected resolved values.
2. Editing one advanced value changes preset display to Custom.
3. New Seed supplies a valid 64-bit value; monkeypatch `secrets.randbits` for determinism.
4. Custom roster dynamically includes every weapon catalog key and validates slots.
5. Generate installs exact `GenerationResult.scenario` data.
6. Generation failure leaves prior draft byte-equivalent.
7. Dirty-draft confirmation prevents accidental replacement.
8. Random/custom infeasibility requires Use Anyway; auto-balanced infeasibility is rejected.
9. Undo restores prior draft and metadata.
10. Manual mutation marks metadata modified.
11. Save/load preserves generated metadata and never implicitly regenerates.

Headless Panda3D tests:

12. All compact and advanced button commands call the intended methods.
13. Generated scene contains exact generated entity nodes and one home base.
14. Entity list/manual property editor still operates after generation.
15. Preview and Launch callbacks receive generated state/path through existing methods.
16. Panel show/hide survives editor → preview → back transitions.
17. If threaded, stale result and shutdown-safe queue handling.

No screenshot/pixel assertions.

## Part I — manual and packaged acceptance

From source:

1. `python -m launcher`.
2. Standard → Generate.
3. Inspect counts/report.
4. Modify one generated jet manually.
5. Save, reload, preview, and launch.
6. Confirm live scenario matches the edited generated map.

Also test:

* same seed and settings twice gives the same map;
* New Seed changes it;
* custom roster preserves exact selected frames/loadouts;
* impossible dense parameters return a useful error without freezing;
* Experimental random can show a clear feasibility warning.

From the existing packaged build recipe:

1. Rebuild `dist/WraithNet`.
2. Repeat Standard → Generate → Save → Launch.
3. Confirm scenario saves under `%LOCALAPPDATA%\WraithNet\scenarios`.
4. Confirm no missing hidden import for new generator modules.

Record results in `packaging/BUILDING.md`.

## Part J — documentation

Update `README.md` with a **Quick random scenario** section:

* `python -m launcher`;
* choose preset/seed/friendly mode;
* Generate;
* optionally edit manually;
* Save/Preview/Launch.

Explain:

* same seed + same resolved parameters reproduces the scenario;
* Auto-balanced means statically feasible, not guaranteed victory;
* Experimental random/custom can be unwinnable;
* enemy jets are not currently generated because hostile aircraft are not implemented.

## Non-goals

* Replacing manual controls.
* Auto-launch after generation.
* File-dialog dependency or new GUI framework.
* Generator CLI.
* Hostile aircraft.
* Runtime difficulty adjustment.

## Acceptance criteria

1. Full `pytest` passes with all new Layer-1/Layer-2 tests.
2. A new user can generate a Standard scenario in a few clicks and then use the normal Save/Load/Preview/Launch flow.
3. Custom roster preserves exact counts, frames, and all weapon loadouts.
4. Same seed/settings reproduce identical output; invalid/dense inputs fail quickly with useful feedback.
5. Existing manual-only editor workflow remains fully functional.
6. Rebuilt executable completes the packaged generation/save/launch smoke test.
