# Phase 44 — Combined-Arms Editor, Generation, Metrics & Validation

> **LLM implementation prompt:** Assume Phases 34–43 are complete. Read all of `docs/19_Terrain_Naval_and_Combined_Arms_Design_Document.md`. Complete cross-feature editor/random-generation integration, feasibility, metrics, migration, balancing, automated tests, and packaged verification. Do not add new combat mechanics.

## Goal

Make the terrain/naval/missile/AAA/tank features usable as one coherent product rather than isolated test fixtures.

Ensure observations, world-view contacts, base forecasts, objective priorities, and in-flight reservations retain the typed information required by Phase 45. Phase 44 does not implement risk-aware bid weights.

## Part A — complete random-generation parameters

Extend Phase 30–35 contracts with explicit:

* terrain preset/coverage;
* naval enabled;
* friendly carrier count/base mode;
* enemy corvette count and defense tier;
* AAA count;
* friendly/enemy tank counts;
* SAM IR/radar mix;
* aircraft flare reserve/loadout;
* mission-objective mix.

Difficulty presets resolve every behavior into scalar/typed parameters; generator code must not branch on preset names.

Use named RNG streams per terrain, enemy land, enemy naval, friendly air, friendly ground, and loadout. Changing tank count must not redraw terrain or move existing air objectives.

## Part B — domain-aware placement and feasibility

Placement:

* land installations/tanks on connected land;
* ships/routes on connected water;
* jets at fixed base or carrier launch ring;
* entity footprint/separation;
* city LOS discovery;
* launch/recovery safety;
* no silent count reductions.

Feasibility report adds stable issue codes:

* `NO_VALID_LAND_COMPONENT`
* `NO_VALID_WATER_COMPONENT`
* `INVALID_DOMAIN_SPAWN`
* `NO_RECOVERY_BASE`
* `UNREACHABLE_CARRIER_ROUTE`
* `NO_ANTI_SHIP_CAPABILITY`
* `NO_ANTI_ARMOR_CAPABILITY`
* `MISSILE_DEFENSE_OVERMATCH`
* `LOS_DISCOVERY_GAP`
* `GROUND_DISCOVERY_DEADLOCK`
* `OBJECTIVE_UNACHIEVABLE`

Static allocation accounts for finite ammunition, target class, carrier reach/recovery, missile defense burden, and terrain connectivity. Continue to label this “statically feasible,” never guaranteed victory.

Use Phase 37's authoritative per-objective/target-class feasibility model. Do not extend the old `required_hp_by_target_type` maps with ad-hoc ship/tank strings. Validate at least one friendly active platform rather than at least one jet, and run explicit jetless tank-only and naval-only scenarios.

## Part C — editor completeness

Add property/list/placement forms for:

* carriers and routes/base assignments;
* corvettes and layered loadouts;
* AAA sites;
* friendly/enemy tanks and orders;
* SAM IR/radar loadouts;
* jet flare inventory;
* mission objectives.

Generate controls include the new counts/mixes. Manual mode remains fully available. All catalogs drive controls dynamically; no hardcoded weapon/frame subset.

Validation errors identify exact entity/objective and offer no silent repair. Save/load/preview/launch reuse existing pathways.

## Part D — viewer/replay completeness

Verify editor, mission brief, live 3D, and supported 2D replay handling for:

* terrain;
* authoritative altitude;
* ships/tanks/AAA;
* missile tracks and guidance;
* flares/notch states;
* gun tracers/interceptions;
* objective status.

If the legacy 2D viewer intentionally omits a visual, it must still parse the trace and show a clear simplified glyph/event—not crash.

## Part E — metrics/reporting

Add/export:

* objective outcome/reason;
* losses and damage by faction/domain/cause;
* guided launches/outcomes by seeker;
* flare/notch effectiveness;
* gun bursts/rounds/hits;
* CIWS intercepts;
* carrier launches/recoveries/loss;
* tank/naval movement and attrition;
* ammunition remaining.

Removed units retain final outcome records analogous to `JetOutcome`.

## Part F — compatibility/migration

Implement and document scenario versions:

* old scenario → all plains;
* old home base → fixed base;
* old target list → synthesized DESTROY objective;
* old SAM → default guided loadout;
* pre-guidance AIM9X migrates to AGM65 and AMRAAM is demand-aware allocated between AGM65/AGM88 with old/new feasibility verification; new-version AIM9X/AMRAAM remain air-to-air.
* generation metadata v1/v2 remains readable provenance; new combined-arms output uses generator v3 and only promises regeneration through a matching supported generator version.

Add fixture files for each supported version. Unsupported future versions fail clearly and never partially load.

## Part G — deterministic integration tests

Verify and retain Phase 37's removal of logical dependence on wall-clock asyncio scheduling:

* contract/message/missile IDs derive from stable manager/shooter ID, target ID, logical tick, and per-source counter—not UUIDs;
* negotiation deadlines are logical ticks;
* bridge submits agents and drains broker messages in stable `(tick, sender_id, sequence)` order;
* simultaneous bids use documented utility/sender tie-breaks.

Async queues may remain an implementation mechanism, but scheduling order may not decide claims or IDs.

Required matrices:

1. full pytest and GUI isolation tests;
2. same seed/run produces identical terrain/combat/metrics;
3. dictionary/list insertion reordering does not change keyed combat outcomes;
4. at least 100 generated scenarios per preset are schema/domain/feasibility valid;
5. smaller real-simulation sample per preset records completion/failure rates;
6. carrier scenario: generate → edit → save → launch → recover;
7. corvette scenario exercises radar SAM, CIWS, and AGM84;
8. IR missile + flare;
9. radar missile + successful/failed notch;
10. AAA blocked by city and firing in clear close range;
11. tanks path around water and fight only tanks;
12. simultaneous cross-domain destruction/objective precedence;
13. legacy scenario fixtures;
14. trace replay and live-stream late join.

Calibrate catalogs/presets from observed results. Do not weaken assertions merely to make labels pass.

## Part H — packaged verification/documentation

Rebuild PyInstaller output and manually verify:

* paint/generate terrain;
* place each new unit;
* custom guided loadouts/countermeasures;
* save/load;
* preview;
* live launch;
* output/trace/replay.

Update README controls/workflow and `packaging/BUILDING.md`. Record actual executable result paths.

## Acceptance criteria

* Every feature is available in manual and appropriate random workflows.
* Domain/LOS/target restrictions are enforced in core world code.
* Full deterministic and packaged matrices pass.
* Legacy scenarios migrate explicitly.
* No unresolved TODO/stub/placeholder combat branch remains.
