# Phase 38 — Guided Missile Model & IR/Radar Weapon Split

> **LLM implementation prompt:** Assume Phase 37 is complete. Read `docs/19_Terrain_Naval_and_Combined_Arms_Design_Document.md` §§8, 13–16. Replace instant guided-weapon/SAM resolution with deterministic in-flight missile engagements. Formalize AIM9X as infrared and AMRAAM as radar-guided; add IR/radar SAM ammunition. Do not implement flares/notching yet—only expose defense hooks and baseline guidance behavior.

## Deliverables

### A. Catalog contracts

Extend `WeaponSpec` or introduce typed guided/direct-fire variants:

* `guidance: NONE | INFRARED | RADAR`
* allowed target classes;
* seeker/launch range;
* speed per tick;
* fuel ticks;
* base hit probability;
* terminal range;
* support requirement;
* countermeasure resistance;
* launch arc and damage.
* required minimum heat/radar-cross-section and `requires_radar_emitter`.

Catalog:

* AIM9X → INFRARED, AIRCRAFT;
* AMRAAM → RADAR, AIRCRAFT;
* AGM65 → INFRARED, INSTALLATION/GROUND_VEHICLE as catalogued;
* AGM88 → RADAR/anti-radiation, radar-emitting INSTALLATION;
* SAM_IR → INFRARED, AIRCRAFT;
* SAM_RADAR → RADAR, AIRCRAFT;
* JDAM/GUN_POD remain direct/unguided under existing behavior.

Do not reinterpret suitability as hit probability.

Add `SensorSignature(heat, radar_cross_section, radar_emitter)` to platform specs/state. IR eligibility is sufficient heat, not “aircraft”; AGM65 can lock a hot ground vehicle. AGM88 requires `radar_emitter=True`. Target class independently controls whether damage is legal.

Legacy scenario migration is mandatory because old WraithNet scenarios used AIM9X/AMRAAM against ground targets. Convert legacy AIM9X to slot-compatible AGM65. Allocate legacy AMRAAM rounds deterministically between AGM65 (general imaging-IR installation attack, including bunkers) and AGM88 (radar emitters) according to concrete old objective demand, without duplicating rounds or increasing slot use. Compare old/new joint feasibility; if capability cannot be preserved, raise an actionable compatibility error instead of loading an unwinnable mission. Update the shipped default scenario. Do not retain physically invalid ground suitability on air-to-air missiles merely to pass old fixtures.

### B. SAM schema migration

`SamSiteSpec` gains finite missile loadout and cooldown/sensor fields through a catalog/frame reference. Legacy SAM specs get a documented default radar loadout. Remove `_apply_sam_damage` only after all call sites/tests migrate.

### C. Missile state and events

Add `MissileState` with stable monotonic ID, shooter, target, weapon, guidance, position `(x,y,z)`, velocity/heading, remaining fuel, support source, and status.

Events:

* `WeaponLaunchEvent`
* `MissileTrackEvent` or per-tick missile frame
* `WeaponImpactEvent` with outcome `HIT|MISS|INTERCEPTED|DECOYED|LOST_LOCK|FUEL_EXPIRED`

Launching consumes one round immediately and starts cooldown. A missile may damage once only.

### D. Tick processing

Deterministic order:

1. validate launch hostility/class/range/arc/LOS/track;
2. create missiles in stable shooter/target/weapon order;
3. update support/lock;
4. advance pursuit by speed;
5. detect terminal contact using swept old-position→new-position closest approach/intersection so a fast missile cannot tunnel past a nearby or moving target;
6. resolve keyed hit roll;
7. apply simultaneous damage;
8. remove resolved/expired missiles.

Use a keyed SHA-256/BLAKE2-derived float from authoritative `ScenarioConfig.combat_seed` plus event identity. Do not read generation metadata, use Python `hash()`, or consume an iteration-order-sensitive shared RNG.

### E. Guidance baseline

IR:

* valid target class with heat signature above the weapon threshold;
* launch and initial lock require LOS;
* after launch, loss of LOS may degrade hit chance by explicit rule;
* all-aspect is catalog launch-arc behavior.

Radar:

* initial radar track and LOS required;
* supported weapon loses guidance if support source dies/loses track before active terminal range;
* active terminal phase is independent;
* expose target/emitter geometry required by Phase 39 notching.

With no countermeasure action, both use baseline probability.

### F. Jet/CNP behavior

Agent intent changes from ambiguous STRIKE to launch/direct-fire semantics while preserving compatibility:

* guided weapon in range → LAUNCH;
* direct weapon → STRIKE;
* world returns a typed `LaunchAcknowledgement(accepted, missile_id, reason)` for every request;
* world ammunition and runtime committed payload decrement only after an accepted acknowledgement, never before validation;
* accepted missiles reserve nominal damage on their concrete target so CNP/salvo logic does not over-launch against HP already covered in flight; later award selection may separately use risk-adjusted expected damage;
* reservations release on MISS/INTERCEPTED/DECOYED/LOST_LOCK/FUEL_EXPIRED and convert to actual damage on HIT;
* manager re-tasking reacts to actual target survival after misses;
* avoid launching duplicate missiles beyond remaining HP unless salvo doctrine explicitly allows it.

Use Phase 37's staged API: launch choices use post-movement observations; launches are registered before immediate inbound-threat observations; impacts occur only during combat finalization.

Expose typed IR/radar threat contacts and accepted in-flight nominal-damage reservations to later bid contexts. Phase 45 owns risk-weighted scoring; this phase must not hide guidance/loadout state in viewer-only events.

### G. Trace/viewer/metrics

Trace missile positions every tick and all outcomes. 3D viewer renders actual missile tracks rather than spawning purely cosmetic travel from instant `StrikeEvent`. Keep cosmetic trails attached to authoritative tracks.

Metrics: launches, hits, misses, lock losses, fuel expiry, damage, hit rate by weapon/guidance/shooter.

### H. Tests

Cover launch validation, finite ammo/cooldown, speed/flyout, moving target pursuit, LOS, radar support loss, active terminal transition, IR target validation, keyed-roll reproducibility/order independence, simultaneous impacts, contract behavior after miss, trace round-trip, and removal of passive SAM damage.

Increment scenario version to v4 through raw `migrate_v3_to_v4()` for guided loadouts and legacy weapon conversion. Missing versions must already have migrated sequentially from v1; never infer legacy status after Pydantic validation.

## Acceptance criteria

* No SAM damages a jet without an explicit launch/impact event.
* AIM9X and AMRAAM have distinct guidance paths.
* Same scenario/seed produces identical combat events independent of dictionary insertion order.
* Viewer follows authoritative missile state.
