# Phase 42 — Gun-Based Anti-Aircraft Artillery

> **LLM implementation prompt:** Assume Phase 41 is complete. Read `docs/19_Terrain_Naval_and_Combined_Arms_Design_Document.md` §11. Add land-based anti-aircraft artillery that uses guns, not missiles. It must be very short-range and inaccurate relative to SAMs, terrain/LOS aware, finite-ammunition, and reusable through the shared gun resolver.

## Deliverables

### A. Schema/catalog

Add `AaaSiteSpec`:

* unique ID;
* faction (enemy default, friendly allowed by schema);
* land position and heading;
* AAA frame/type;
* HP;
* ammunition override/doctrine.

AAA is `INSTALLATION` with target class `INSTALLATION`, but its gun allows `AIRCRAFT` only.

Increment scenario schema to v8 with `migrate_v7_to_v8()` adding an empty AAA-site list.

Catalog starting behavior:

* engagement range substantially below IR SAM range;
* minimum/maximum altitude or low altitude ceiling;
* narrow/moderate tracking arc unless turreted;
* low per-round accuracy;
* burst fire and cooldown;
* finite ammunition;
* damage calibrated below unavoidable one-tick kills.

### B. Targeting

Each AAA site selects at most one target per ready burst:

1. hostile aircraft;
2. inside range and altitude ceiling;
3. city LOS clear;
4. allowed arc;
5. nearest then stable ID.

No missile state is created. Flares/notching have no effect on gunfire. Jet frame evasion may influence hit probability only through an explicit documented term.

### C. Destruction/mission integration

AAA can be a DESTROY objective and a valid target for appropriate air-to-ground weapons. Destroyed sites stop firing in the same tick phase defined by simultaneous-resolution policy.

Add target/catalog type without conflating AAA with SAM.

### D. AI threat awareness

Expose observed AAA range, altitude ceiling, LOS requirements, and confidence as typed threat contacts. Existing CNP must recognize AAA as a strike target when objective and weapon suitability allow; Phase 45—not this phase—implements deterministic gun-exposure bidding penalties.

### E. Viewer/editor/generation

Render distinct AAA icon/ring and gun tracers. Editor placement only on land, with frame/ammo/heading/objective settings. Random generator adds `aaa_count`, keeps launch ring safe by resolved clearance, and places defended groups without breaking LOS discovery.

### F. Metrics/tests

Metrics: bursts, rounds, hits, damage/kills, ammo remaining, aircraft losses to AAA.

Tests:

* water rejection;
* range/ceiling/arc/LOS;
* low deterministic accuracy fixtures;
* one target/burst and cooldown/ammo;
* flares/notch no effect;
* legal target class only;
* destruction/objectives;
* editor/generator/trace.

## Acceptance criteria

* AAA never launches missiles.
* Close-range inaccurate behavior is catalog-defined and reproducible.
* Buildings can block AAA fire; forests cannot.
* AAA cannot attack ships/tanks/installations.
