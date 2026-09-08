# Phase 41 — Corvettes & Layered Naval Defense

> **LLM implementation prompt:** Assume Phase 40 is complete. Read `docs/19_Terrain_Naval_and_Combined_Arms_Design_Document.md` §§10.2–11. Add enemy corvettes as mobile objectives with radar missiles, CIWS, and deck guns. Build a reusable direct-gun burst resolver that Phase 42/43 can reuse. Add an anti-ship weapon so jets can legally defeat corvettes.

## Deliverables

### A. Corvette schema/catalog

Add `CorvetteSpec` with frame, ENEMY faction by default, water position/route, heading, finite loadout, and doctrine.

Frame includes HP, naval movement, footprint radius, sensor/radar, and hardpoint/magazine capacities. Validate routes on Phase 40's frame-specific footprint-inflated water mask and validate loadout.

Increment scenario schema to v7 with `migrate_v6_to_v7()` adding an empty corvette list.

### B. Anti-ship attack

Add `AGM84`:

* active radar;
* allowed target `SURFACE_VESSEL`;
* finite speed/fuel/range/damage;
* all-aspect or documented launch arc;
* no suitability against aircraft/ground installations.

Update jet bidding/assignment so corvette DESTROY objectives can be serviced by suitable jets without allowing tank/air-to-air weapons to damage ships.

### C. Shared gun resolver

Add typed `GunSpec` and `resolve_gun_burst()` implementing:

* hostility/target-class validation;
* range/min range;
* arc;
* LOS;
* optional altitude ceiling;
* finite ammunition/cooldown;
* keyed deterministic accuracy per round/burst;
* simultaneous damage event output.

No gun may bypass world-side validation.

### D. Layered corvette doctrine

Stable priority:

1. CIWS against inbound anti-ship missiles inside intercept range;
2. radar SAM against hostile aircraft in range/LOS;
3. CIWS against very close hostile aircraft;
4. deck gun against hostile surface vessel;
5. stable distance/ID tie-break.

Weapons:

* finite SAM_RADAR magazine;
* CIWS short range, high rate, imperfect interception;
* deck gun surface-only;
* independent cooldowns where catalog says so.

Corvettes do not target tanks/installations in this phase and do not join CNP.

Directly detected/datalink-shared corvette radar-SAM and CIWS capabilities become typed threat contacts for Phase 45. Hidden loadouts remain hidden; only observed capability may influence jet bids.

### E. Objectives/termination

Corvette may be a required DESTROY objective. Destroyed corvette stops moving/firing/supporting radar immediately after simultaneous damage resolution. Mission success follows objective evaluator, not “all platforms dead.”

### F. Trace/viewer/editor/generation

Add corvette frames, loadout/status, gun bursts, CIWS interceptions, missile events, patrol route, threat rings, editor forms, and random count/group placement in water.

Viewer differentiates SAM launches, CIWS tracers, deck-gun tracers, AGM84 tracks, hits/intercepts.

### G. Metrics/tests

Metrics by layer: launches, CIWS attempts/intercepts, deck-gun hits, corvette losses, anti-ship hit rate.

Tests:

* water spawn/path;
* target-class restrictions;
* exact doctrine priority/ties;
* CIWS intercept before impact in tick order;
* finite ammo/cooldown;
* keyed inaccuracy;
* AGM84 CNP/launch/impact;
* objective completion;
* trace/editor/generator round-trip.

## Acceptance criteria

* Corvettes use all configured defensive layers without attacking illegal domains.
* CIWS can fail; behavior is deterministic for a seed.
* Jets have at least one legal anti-ship option.
* Shared gun resolver is platform-neutral and ready for AAA/tanks.
