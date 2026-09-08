# Phase 36 — Terrain Painting & 3D Terrain Rendering

> **LLM implementation prompt:** Assume Phases 34–35 are complete. Read `docs/19_Terrain_Naval_and_Combined_Arms_Design_Document.md` §5 and §14. Add terrain tools to the existing Panda3D editor and render the same stored terrain in editor, preview, and live modes. Do not add gameplay units.

## Deliverables

### A. Trace transport

Add the complete `TerrainGrid` to `TraceHeader`. Populate it from `ScenarioConfig` in both `trace_builders.py` and editor preview conversion. Terrain is static per run and must not be repeated in every tick.

Version trace schema. Legacy headers with no terrain render all plains.

### B. Scene rendering

Replace the single ground quad with terrain-cell batches:

* WATER: dark blue emissive/transparent surface with subtle client-side animation;
* PLAINS: dark desaturated ground;
* FOREST: plains base plus deterministic low-poly tree decorations;
* CITY: city base plus procedural building blocks whose maximum Z equals stored `building_height`.

Requirements:

* rendering uses stored cells/heights only;
* no render-time randomness;
* batch/static-combine geometry to avoid one expensive scene subtree per decorative object;
* preserve grid overlay and Ace Combat cyan visual language;
* entity Z placement uses terrain surface zero for now;
* water animation is cosmetic and never mutates terrain.

Add pure geometry/layout helpers outside Panda imports where practical.

### C. Editor modes

Add explicit `ENTITY` and `TERRAIN` modes. Terrain mode disables entity placement selection while dragging.

Toolbar:

* Water / Plains / Forest / City;
* brush radius 1–5 cells;
* City Height: Random or fixed 4–25;
* Fill;
* Generate Terrain;
* Undo / Redo;
* return to Entity mode.

Painting:

* click-drag maps mouse rays to cells;
* interpolate drag with supercover to avoid gaps;
* one mouse-down-to-up stroke is one undo command;
* Random city height is derived from persisted `TerrainGrid.terrain_edit_seed` + cell coordinate + stroke sequence, then persisted; it does not depend on Phase 37's later combat seed;
* repainting city with fixed height updates height;
* painting non-city clears height.

### D. Validation after painting

Painting may make existing land entities invalid.

* Highlight invalid entities red and list IDs/reasons.
* Save/Preview/Launch remain blocked by `ScenarioConfig`.
* Offer **Relocate invalid entities** using a deterministic nearest-valid-cell search with separation.
* Require confirmation before relocation.
* Never silently move or delete entities during a brush stroke.

### E. Terrain generation panel

Expose Phase 35 parameters in an advanced terrain panel and install generated terrain only after success. Terrain generation:

* preserves entities initially;
* reports invalidated entities;
* gets its own undo snapshot;
* does not implicitly rerandomize entities.

The Phase 33 full Scenario Generate flow may generate both terrain and entities as before.

### F. Tests

Extend headless editor/scene tests:

1. mode switching and widget callbacks;
2. drag interpolation paints no-gap paths;
3. brush radius clipping at map edges;
4. city heights persist and non-city clears them;
5. stroke-level undo/redo;
6. fill and generated terrain;
7. invalid entities highlighted but not moved;
8. deterministic relocation;
9. trace/header round-trip;
10. editor/preview/live scene receives same terrain;
11. no render-time random calls;
12. scene node count remains bounded on a maximum grid.

Manual checks: paint coastline/city, inspect building heights, save/load, preview, launch, and verify visual/LOS agreement.

## Acceptance criteria

* Existing manual entity editor remains usable.
* Painted/generated terrain round-trips exactly.
* City visuals match authoritative heights.
* Invalid placements are explicit and cannot launch.
* Packaged build includes no new dependency and passes terrain editor smoke test.
