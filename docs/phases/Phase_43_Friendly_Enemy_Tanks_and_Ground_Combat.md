# Phase 43 — Friendly/Enemy Tanks & Ground Combat

> **LLM implementation prompt:** Assume Phase 42 is complete. Read `docs/19_Terrain_Naval_and_Combined_Arms_Design_Document.md` §12. Add friendly and enemy mobile tanks with terrain-aware pathing, city LOS, and direct-fire cannon. Tank weapons must target `GROUND_VEHICLE` only in both AI and world adjudication. Do not add CNP to tanks.

## Deliverables

### A. Tank schema/catalog

Add `TankSpec`:

* ID/faction;
* land position/heading;
* frame type;
* HP/armor;
* cannon ammunition;
* order: HOLD, PATROL, ADVANCE;
* optional waypoint list.

Tank frames define speed, turn rate, sight height/range, cannon, HP, and evasion/armor terms. Tanks are domain GROUND and target class GROUND_VEHICLE.

Increment scenario schema to v9 with `migrate_v8_to_v9()` adding an empty tank list.

Validate spawn/waypoints on PLAINS/FOREST/CITY. Water is forbidden.

### B. Ground pathing/movement

Implement deterministic tile A*:

* WATER impassable;
* plains/forest/city equal cost in this expansion;
* stable neighbor order/tie-break;
* path cache invalidated only when needed;
* continuous movement toward cell centers;
* no diagonal corner cutting across water;
* bounded failure returns HOLD/no-path state, not teleport.

No collision physics; define simple minimum separation/occupancy resolution in stable ID order.

### C. Tank AI

Per tick:

1. retain visible valid target if alive/in LOS;
2. otherwise choose nearest visible hostile GROUND_VEHICLE, stable ID tie-break;
3. if in range/arc/LOS, turn/fire;
4. otherwise path/move according to order/target;
5. if no target, follow patrol/advance or hold.

Friendly and enemy use identical logic with faction hostility.

Randomly generated tank forces must not all HOLD outside sensor range. The generator assigns opposing ADVANCE routes toward one or more deterministic land engagement zones (or intersecting patrols). Feasibility verifies each required enemy tank can eventually enter sensor/weapon reach of a friendly ground vehicle on connected land. A manually authored all-HOLD deadlock is allowed only with an explicit `GROUND_DISCOVERY_DEADLOCK` warning/error according to objective requirements.

### D. Cannon adjudication

Use Phase 41 shared gun resolver with tank-specific gun:

* allowed target class exactly `GROUND_VEHICLE`;
* city LOS;
* range/arc/ammo/cooldown;
* keyed accuracy and armor-adjusted damage.

World rejects attempts against aircraft, surface vessels, AAA/SAM installations, buildings, bunkers, jammers, or missiles even if an invalid intent is injected.

### E. Objectives and observations

Enemy tanks may be DESTROY subjects; friendly tanks may be PROTECT/SURVIVE subjects. Tanks receive local detections but do not use jet CNP or broker in this phase.

Define whether tanks share faction contacts: default no datalink; each acts on direct LOS only.

When an enemy tank is a jet DESTROY objective, its observed position/velocity and target class are available to Phase 45's jet bid planner for intercept estimation. Tanks themselves never submit CNP bids.

### F. Trace/viewer/editor/generation

Trace moving tank position, heading, HP, target/order, cannon events. Render distinct faction colors, turrets/tracers, HP.

Editor supports faction/frame/order/ammo/path and land-only placement. Random generator adds friendly/enemy tank counts, ensures land connectivity and joint objective feasibility.

### G. Metrics/tests

Metrics: distance, rounds/hits, tank kills/losses by faction, objectives.

Tests:

* spawn/path water rejection;
* stable A* and no corner cut;
* forest no modifier;
* city movement allowed but LOS blocked;
* target class restrictions at AI and world;
* generated opposing routes converge into mutual discovery; all-HOLD separated fixtures report deadlock;
* faction symmetry/friendly-fire rejection;
* ammo/cooldown/accuracy/armor;
* objective outcomes;
* editor/generator/trace.

## Acceptance criteria

* Tanks can fight each other autonomously across land.
* They can never damage non-ground-vehicle targets.
* Water is impassable; forest remains decorative mechanically.
* Tank logic does not depend on `JetAgentRuntime` or CNP.
