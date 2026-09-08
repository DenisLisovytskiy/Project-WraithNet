# Design Document: Terrain, Naval Warfare & Combined Arms

> This document is the source of truth for Phases 34–44. It extends WraithNet from an open, flat, jet-versus-static-target simulation into a terrain-aware combined-arms simulation with water, plains, forests, cities, naval bases and combatants, guided missiles and countermeasures, gun-based air defenses, and opposing tank forces. Nothing in this document is implemented yet.

## 0. Requested outcome

Users can:

* procedurally generate terrain or paint it in the existing editor;
* use water, plains, forests, and city blocks with persistent random building heights;
* place land units only on land and ships only on water;
* use friendly aircraft carriers as jet bases;
* fight enemy corvettes with layered gun and missile defenses;
* distinguish infrared/heat-seeking missiles from radar-guided missiles;
* defend aircraft with flares against IR missiles and notching against radar missiles;
* place close-range, inaccurate anti-aircraft gun units;
* include friendly and enemy tanks that engage ground vehicles only;
* generate, edit, save, preview, launch, replay, and inspect all of this through existing workflows.

## 1. Critical corrections to the naïve feature list

### 1.1 Terrain cannot be viewer-only

Water spawn restrictions and city LOS affect gameplay, so terrain must live in `ScenarioConfig` and `WraithNetModel`. The trace/viewer consume the same authoritative terrain. The renderer must never invent city heights that differ from the LOS model.

### 1.2 Existing jet altitude is cosmetic

Phases 25–26 deliberately kept altitude in `viewer3d/`. City height cannot consistently block sight against an aircraft whose Z position exists only in a client. Phase 37 therefore introduces authoritative but still lightweight combat altitude for aircraft. It does not add full aerodynamic physics; it provides position `(x, y, z)`, climb limits, and a consistent LOS endpoint. The 3D viewer must use this authoritative altitude as its baseline.

### 1.3 Current SAMs do not launch missiles

Today a SAM damages every jet in range every tick with no shot, flyout, miss, or defense. Heat seekers, radar guidance, flares, and notching require explicit missile engagements. Phase 38 replaces the passive SAM damage shortcut with a deterministic in-flight missile model.

### 1.4 “Inaccurate” must remain reproducible

Gun and missile accuracy use keyed deterministic rolls derived from scenario seed, event ID, shooter, target, and tick. Do not consume a shared random stream whose result changes when iteration order changes.

### 1.5 Forest means decoration only

Forest tiles do not block LOS, change speed, provide concealment, or restrict spawning in this expansion. They render trees only. These mechanics may be added later without changing the terrain schema.

### 1.6 Tanks target ground vehicles only

Tank guns may target units classified `GROUND_VEHICLE`—initially friendly/enemy tanks. They may not target aircraft, ships, buildings, SAM installations, AAA emplacements, or jammers. Both AI target selection and world adjudication enforce this rule.

## 2. Terrain model

### 2.1 Stored representation

Use a coarse tile grid, default cell size `5.0` world units on the existing `100 × 100` map.

```python
TerrainType = Literal["WATER", "PLAINS", "FOREST", "CITY"]

class TerrainCell(BaseModel):
    terrain_type: TerrainType = "PLAINS"
    building_height: float | None = None

class TerrainGrid(BaseModel):
    world_width: float
    world_height: float
    terrain_edit_seed: int = 0
    cell_size: float = Field(default=5.0, gt=0)
    columns: int
    rows: int
    cells: list[list[TerrainCell]]
```

Rules:

* `columns=ceil(world_width/cell_size)` and `rows=ceil(world_height/cell_size)`; cells on maximum edges are clipped to the remaining width/height. This supports legacy arbitrary map dimensions.
* Non-city cells require `building_height=None`.
* City cells require a persisted height in a bounded range, initially `4–25`.
* Missing terrain in legacy scenarios loads as an all-plains grid generated during validation/migration.
* Terrain list ordering is row-major and stable.

### 2.2 Coordinate contract

* World `(0, 0)` maps to cell `(column=0, row=0)`.
* Points exactly on maximum map edges clamp to the last cell.
* A segment exactly on a tile boundary follows a documented supercover rule so LOS is symmetric: `los(a,b) == los(b,a)`.
* Editor, generator, world, and viewer use shared helpers in `wraithnet/terrain.py`.

### 2.3 Passability

```python
MovementDomain = Literal["AIR", "GROUND", "SURFACE"]
```

| Terrain | AIR | GROUND | SURFACE |
|---|---:|---:|---:|
| WATER | yes | no | yes |
| PLAINS | yes | yes | no |
| FOREST | yes | yes | no |
| CITY | yes | yes | no |

City cells abstract roads around buildings; ground movement is allowed. An entity may begin/end LOS inside a city cell, but intermediate city blocks can obstruct it.

## 3. Authoritative 2.5D line of sight

The world remains horizontally 2D but LOS uses endpoint heights.

```python
def has_line_of_sight(
    terrain: TerrainGrid,
    start: tuple[float, float, float],
    end: tuple[float, float, float],
) -> bool:
    ...
```

Use grid traversal/supercover. For every intermediate CITY cell, interpolate segment height at entry/exit; LOS is blocked if the stored building column reaches the segment. Ignore the start and end cells so a tank on an abstract city street is not considered inside a solid building.

Initial sight heights:

* tank/ground sensor: `1.8`;
* SAM/AAA sensor: `3.0`;
* ship sensor: `8.0`;
* aircraft: authoritative current altitude, normally `20+`.

City LOS gates direct sensing and direct-fire weapons. Radar/IR missile launch also requires initial LOS; later guidance rules are defined in Phase 38. Forest and terrain type elevation do not affect LOS.

## 4. Procedural terrain

`TerrainGenerationParameters` includes:

* root seed and named terrain RNG stream;
* water coverage;
* forest coverage of land cells;
* city coverage of land cells;
* minimum land-region size;
* minimum water-region size;
* coastline smoothing iterations;
* city height range;
* city clustering;
* optional guarantee of carrier-capable water.

Generation sequence:

1. Create coherent water using seeded low-resolution value noise or seeded cellular regions implemented with stdlib only.
2. Smooth for bounded iterations.
3. Enforce at least one connected land region for land objectives/vehicles.
4. If naval units are requested, enforce a water component large enough for their footprints and routes.
5. Assign forest and city only to land, respecting exact/near-exact requested coverage tolerances.
6. Generate and persist each city height once.
7. Terrain-aware entity placement uses passability masks and LOS/discovery feasibility.

Water fraction uses all map cells as denominator. Forest/city fractions use land cells after water generation; validate `forest_fraction + city_fraction <= 1.0`.

Same seed and resolved parameters produce identical terrain and entities. Terrain uses a named RNG stream so changing a friendly loadout does not change coastlines.

## 5. Terrain editor

Add a Terrain mode alongside Entity mode:

* brush type: Water / Plains / Forest / City;
* brush radius: 1–5 cells;
* click-drag painting with supercover interpolation to avoid gaps;
* city height mode: Random / fixed numeric height;
* Fill tool;
* Generate Terrain panel using the same pure generator;
* terrain-only undo/redo with bounded history;
* invalid-entity highlighting after paint;
* explicit **Relocate invalid entities** or manual correction—never silently delete units.

Painting updates the draft only. Save/Preview/Launch still validate through `ScenarioConfig`.

## 6. Platform and target taxonomy

Introduce explicit faction/domain/classification rather than extending `TargetType` conditionals indefinitely.

```python
Faction = Literal["FRIENDLY", "ENEMY"]
PlatformDomain = Literal["AIR", "GROUND", "SURFACE", "INSTALLATION"]
TargetClass = Literal[
    "AIRCRAFT", "GROUND_VEHICLE", "SURFACE_VESSEL", "INSTALLATION", "IN_FLIGHT_MISSILE"
]
```

Every combat unit has stable ID, faction, domain, target class, position, altitude/sight height, HP, heading, sensors, and weapons. Preserve `JetAgent`/`JetAgentRuntime` while extracting shared world adjudication into platform-neutral helpers; do not force tanks and corvettes into the CNP jet runtime.

`ScenarioConfig.combat_seed` is authoritative for keyed combat outcomes and exists independently of informational generation metadata. Manual/legacy scenarios default to a documented zero seed; generated scenarios copy their root seed. The bridge passes it to the model.

Scenario additions are typed lists:

* `carriers: list[CarrierSpec]`
* `corvettes: list[CorvetteSpec]`
* `aaa_sites: list[AaaSiteSpec]`
* `tanks: list[TankSpec]`

Old target/SAM/jammer lists remain compatible during migration.

## 7. Mission objectives

“Destroy every `TargetState`” no longer scales when friendly units and optional enemy units coexist. Add:

```python
class MissionObjective(BaseModel):
    objective_id: str
    objective_type: Literal["DESTROY", "PROTECT", "SURVIVE", "RETURN"]
    subject_ids: list[str]
    required: bool = True
    priority: float = Field(default=0.5, ge=0.0, le=1.0)
    until_tick: int | None = None
```

`until_tick` is required and positive only for SURVIVE, and forbidden for other objective types.

Legacy scenarios synthesize one required DESTROY objective covering old targets/SAMs. Enemy corvettes/tanks/AAA may opt into DESTROY objectives. Friendly carriers normally receive PROTECT. Termination evaluates required objectives and distinguishes success, failure, abort, and timeout.

Objective semantics:

* DESTROY completes when every subject is destroyed.
* PROTECT fails immediately when any protected subject is destroyed. All active required PROTECT objectives complete together once every required non-PROTECT objective is complete and their subjects remain alive.
* SURVIVE fails if any subject is destroyed before `until_tick`; it completes at that tick.
* RETURN fails when any required subject is destroyed or cannot reach a valid base; it completes only when every required subject returns alive.
* Optional objectives track status but never gate global outcome.

Required FAILURE takes precedence over simultaneous completion, then SUCCESS when every required objective is complete, then TIMEOUT at the tick limit. ABORTED applies only when at least one unresolved objective requires active progress and no surviving friendly platform can make that progress; passive SURVIVE/PROTECT objectives continue waiting. Jetless tank/naval scenarios are valid: schema requires at least one friendly active platform and one required objective, not at least one jet.

Phase 37 also replaces the editor's hardcoded load/save field whitelists with a lossless schema-backed draft adapter. Fields without a UI yet remain preserved byte-equivalently across Load → Save.

## 8. Guided weapons

### 8.1 Guidance types

```python
GuidanceType = Literal["NONE", "INFRARED", "RADAR"]
```

Catalog mapping:

* `AIM9X`: infrared/heat-seeking air-to-air missile.
* `AMRAAM`: radar-guided air-to-air missile.
* `AGM65`: infrared-guided air-to-ground missile.
* `AGM88`: radar/anti-radiation air-to-ground missile.
* `SAM_IR`: heat-seeking surface-to-air missile.
* `SAM_RADAR`: radar-guided surface-to-air missile.
* `AGM84`: active-radar anti-ship missile.
* JDAM and direct-fire guns remain `NONE`.

Suitability/allowed-target classes are explicit. “All-aspect” remains a launch-envelope property, separate from guidance type.

Platforms expose seeker signatures separately from target class:

```python
class SensorSignature(BaseModel):
    heat: float = Field(ge=0.0)
    radar_cross_section: float = Field(ge=0.0)
    radar_emitter: bool = False
```

IR weapons require sufficient heat, not specifically an aircraft; AGM65 can therefore lock a hot ground vehicle. AGM88 additionally requires `radar_emitter=True`. Radar air-to-air weapons use radar cross section. Target class still controls legal damage.

### 8.2 In-flight engagements

```python
class MissileState:
    missile_id: str
    shooter_id: str
    target_id: str
    weapon_type: WeaponType
    guidance: GuidanceType
    position: tuple[float, float, float]
    speed: float
    remaining_fuel_ticks: int
    support_source_id: str | None
```

Launch consumes ammunition immediately. Missiles advance each tick, update pursuit heading, and resolve hit/miss/intercept/fuel-expired events. They do not become Mesa agents or CNP participants.

A launch request changes runtime commitment only after a typed world acknowledgement. Accepted missiles reserve nominal damage against their target to prevent duplicate over-launch; selection separately uses risk-adjusted expected milli-damage. The reservation becomes damage on hit and is released on every non-hit terminal outcome.

### 8.3 Radar and IR behavior

* IR launch requires LOS, valid heat target, and seeker range. Flares may decoy it.
* Radar launch requires LOS and radar track. Semi-active weapons require support; active weapons become independent inside terminal range.
* Before terminal activation, notching geometry is target velocity versus support-emitter LOS. After an active seeker takes over, use missile-to-target relative position/velocity instead. Notching has no effect on IR guidance.
* Flares affect IR only.
* Building LOS can deny launch/track when the geometry actually intersects.

## 9. Aircraft defenses

Jets gain:

* flare inventory and cooldown;
* radar-warning/inbound-missile observation;
* defensive state;
* optional `FLARE` and `NOTCH` defensive action alongside the primary move/strike action.

Notching is not a magic button. The jet must:

* know the radar emitter/support bearing;
* turn velocity approximately perpendicular to line of sight;
* maintain minimum speed;
* stay inside a configurable angular window for required ticks.

This maneuver can delay an attack and consume fuel. A keyed deterministic roll combines seeker resistance, geometry, countermeasure quality, and target evasion.

## 10. Naval platforms

### 10.1 Aircraft carriers

Friendly carriers:

* spawn and move only on connected WATER cells;
* have HP, heading, speed, route/hold-position behavior;
* register as dynamic air bases;
* provide a current recovery position to assigned jets;
* may be protected by a mission objective;
* are unarmed in Phase 40 unless a later explicit loadout is configured.

Jets reference `home_base_id`, not only a fixed coordinate. Legacy `home_base` becomes an implicit fixed base. RTB resolves the base's current position each tick. If a carrier is destroyed, jets use a configured alternate base or mission-abort logic.

Carrier recovery is `RTB → APPROACH → RECOVERED`: enter approach within 8 units, fly an astern alignment point, descend to altitude 2, then capture within 2 units only when heading differs by at most 30° and relative speed is at most 2 units/tick. These are catalog/config constants, not viewer-only animation.

### 10.2 Enemy corvettes

Corvettes are surface vessels and optional DESTROY objectives. Layered defense:

* radar-guided SAMs against aircraft;
* short-range inaccurate CIWS against aircraft and inbound missiles;
* deck gun against surface vessels;
* finite ammunition, cooldown, target selection, LOS, and engagement arcs.

They use water-constrained patrol routes and do not participate in friendly CNP.

## 11. Gun combat

Use one shared burst resolver for CIWS, AAA, and tank cannon/direct guns:

```python
class GunSpec:
    range: float
    min_range: float
    firing_arc_deg: float
    rounds_per_burst: int
    accuracy: float
    damage_per_hit: int
    cooldown_ticks: int
    allowed_target_classes: frozenset[TargetClass]
    altitude_ceiling: float | None
```

Every shot/burst:

1. validates shooter/target alive and hostile;
2. validates allowed target class;
3. validates range, arc, LOS, and altitude ceiling;
4. consumes finite ammunition;
5. uses keyed deterministic accuracy;
6. records hit/miss/damage events.

AAA is intentionally very short-range and low-accuracy. CIWS may additionally target `IN_FLIGHT_MISSILE`. Tank cannon permits only `GROUND_VEHICLE`.

## 12. Tanks

`TankSpec` includes faction, frame type, position, heading, HP, cannon ammunition, and optional patrol/hold order.

* Spawn and path only on PLAINS/FOREST/CITY.
* Water is impassable.
* Forest has no modifier.
* City is passable but buildings can block direct fire/sensors.
* Simple deterministic AI: acquire nearest visible hostile ground vehicle, move into range using tile A*, turn, and fire.
* Friendly and enemy tanks use the same world rules with opposite faction.
* No CNP integration in this expansion.
* Tank cannon cannot damage installations, ships, or aircraft even if an invalid intent is injected.

## 13. Tick order

Define one authoritative order:

The bridge/world becomes staged rather than passing one monolithic intent list:

1. Pre-movement observations expose existing tracks; runtimes submit mobility/task intents, including NOTCH maneuver selection against already known radar threats.
2. Apply platform movement/heading/altitude with terrain passability.
3. Build post-movement detections/locks; runtimes submit launch/direct-fire intents.
4. Register launches and build immediate inbound-threat observations.
5. Runtimes may deploy fast FLARE countermeasures; evaluate notch geometry from the movement already performed and radar-support state. A radar missile first seen after movement can trigger a notch next tick, never an extra instantaneous turn.
6. Process direct gun bursts and advance missiles using swept-segment closest approach so fast missiles cannot tunnel through targets.
7. Resolve CIWS interception, then simultaneous missile/gun impacts.
8. Reap destroyed units and invalidate bases/contracts/targets.
9. Evaluate objectives and termination.
10. Record trace and metrics.

Tests lock this order; do not let dictionary iteration decide simultaneous combat.

## 14. Trace, viewers, and metrics

Trace header adds terrain, platform catalogs, and static scenario units. Ticks add:

* movable platform frames for carriers/corvettes/tanks/AAA;
* aircraft authoritative altitude;
* missile frames;
* launch, countermeasure, intercept, hit, miss, and destruction events;
* objective status.

The 3D viewer renders:

* animated water;
* plains base;
* deterministic forest decorations;
* city buildings at stored heights;
* ships/tanks/AAA meshes or glyphs;
* missiles, tracers, flares, and radar-lock/notch HUD cues.

Metrics include shots/launches, hits, misses, probability-of-hit result, countermeasure attempts/success, losses by cause, objective outcomes, carrier recovery, and tank/naval attrition.

## 15. Random generation integration

Phases 30–33 remain the base random-scenario system. This expansion supersedes only their explicit terrain/new-unit non-goals.

Later generation parameters add:

* terrain coverage;
* naval enable/counts;
* friendly carrier base mode;
* AAA count;
* friendly/enemy tank counts;
* SAM IR/radar mix;
* corvette count/loadout.

Placement is domain-aware. Feasibility becomes a joint check across terrain connectivity, LOS discovery, suitable weapons, guided-defense burden, base recovery, and objective domains.

Phase 37 versions feasibility around `ObjectiveFeasibility`/target-class allocations instead of Phase 30's legacy target-type totals. Legacy summary fields may be derived for compatibility, but authoritative checks operate per concrete objective, carrier-owned ammunition, reach, domain, and seeker signature. Phase 35 terrain-aware output uses `generator_version=2`; Phase 44 combined-arms recipes use version 3. Older metadata remains loadable as provenance but is regenerated only by a matching supported generator version.

## 16. Compatibility and migration

* One public `scenario_from_data()` adapter is used by file I/O, editor/default construction, launcher paths, and tests. It reads raw data, treats a missing version as legacy v1, rejects versions newer than supported, and applies sequential pure migrations before Pydantic validation. Latest `scenario_version` and terrain are required, so direct validation of legacy data fails loudly instead of bypassing migration. `ScenarioConfig` uses `extra="forbid"`.
* Version schedule: Phase 34 v2 (terrain), Phase 37 v3 (platform/objective/base/seed), Phase 38 v4 (guided loadouts), Phase 39 v5 (countermeasure state/config), Phase 40 v6 (carriers), Phase 41 v7 (corvettes), Phase 42 v8 (AAA), and Phase 43 v9 (tanks). Every phase adds a pure `migrate_vN_to_vN+1()`; Phase 44 adds no version unless it actually persists another scenario field.
* Legacy scenario with no terrain → all plains.
* Legacy `home_base` → implicit fixed airbase.
* Legacy SAM → default finite radar-guided loadout chosen to approximate prior danger, but tests should compare intent—not claim byte-identical combat after the deliberate SAM overhaul.
* Existing names remain in the catalog, but legacy scenarios used AIM9X/AMRAAM against ground targets. Version migration converts legacy AIM9X to slot-compatible AGM65. Legacy AMRAAM rounds are allocated deterministically between AGM65 (general imaging-IR installation attack, including bunkers) and AGM88 (radar emitters) against the concrete old objective demand, without duplicating ammunition or increasing slots. Migration runs the old and new feasibility allocators and fails with an actionable compatibility error rather than silently making a formerly feasible mission impossible. Updated default scenarios use semantically correct loadouts.
* Old traces remain loadable through versioned parsing where practical; new viewers must fail clearly on unsupported versions.

## 17. Explicit non-goals

* Full fluid simulation, waves affecting motion, tides, bridges, roads, or destructible terrain.
* Forest concealment or movement penalties.
* Full rigid-body flight, naval hydrodynamics, or tank suspension.
* Player direct control; units remain autonomous.
* Enemy aircraft.
* Radar horizon, electronic counter-countermeasures, chaff, sonar, submarines, artillery, logistics, or carrier launch deck scheduling.
* Photorealistic procedural buildings.

## 18. Phases

* **34 — Terrain Schema, Passability & 2.5D LOS**
* **35 — Procedural Terrain & Terrain-Aware Scenario Generation**
* **36 — Terrain Painting & 3D Terrain Rendering**
* **37 — Platform, Faction, Objective & Authoritative Altitude Contracts**
* **38 — Guided Missile Model & IR/Radar Weapon Split**
* **39 — Aircraft Countermeasures: Flares & Notching**
* **40 — Naval Movement & Aircraft Carrier Bases**
* **41 — Corvettes & Layered Naval Defense**
* **42 — Gun-Based Anti-Aircraft Artillery**
* **43 — Friendly/Enemy Tanks & Ground Combat**
* **44 — Combined-Arms Editor, Generation, Metrics & Validation**
* **45 — Risk-Aware CNP & Countermeasure-Weighted Bidding**

## 19. Negotiation impact

Phases 34–44 expose the state needed by Phase 45's risk-aware CNP. Jet bids must account for legal target capability, finite/reserved damage, standoff, terrain LOS, fuel and dynamic-base recovery, known IR/radar/AAA/CIWS/jammer exposure, HP/evasion, and current defensive delay.

Flare inventory is transmitted in the typed bid proposal. Its penalty is conditional: `IR exposure × (1 - flare readiness)`. Fewer/no flares disadvantage a jet on known IR-exposed assignments but do not irrationally penalize safe or radar-only missions. Radar exposure uses notch readiness instead. Full contracts and formulas are in `docs/20_Risk_Aware_CNP_Negotiation_Design_Document.md`.
