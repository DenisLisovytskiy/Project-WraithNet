# Phase 40 — Naval Movement & Aircraft Carrier Bases

> **LLM implementation prompt:** Assume Phases 34–39 are complete. Read `docs/19_Terrain_Naval_and_Combined_Arms_Design_Document.md` §10.1. Add water-constrained naval movement and friendly aircraft carriers as dynamic jet bases. Do not implement enemy corvette weapons yet.

## Deliverables

### A. Naval frame/schema

Add `NavalFrameSpec` catalog and:

```python
class CarrierSpec(BaseModel):
    unit_id: str
    faction: Literal["FRIENDLY"] = "FRIENDLY"
    position: tuple[float, float]
    heading: float
    frame_type: CarrierFrameType
    route: list[tuple[float, float]] = Field(default_factory=list)
    hold_position: bool = True
```

Frame includes HP, speed, turn rate, sight height, footprint/recovery radius, and optional capacity metadata. Carriers are `SURFACE` / `SURFACE_VESSEL`.

Increment scenario schema to v6 with `migrate_v5_to_v6()` adding empty carriers and preserving fixed-base assignments.

Validate position and every route point on WATER. For each naval frame, build a navigable-water mask by inflating land/shore obstacles by the vessel's footprint radius. Require route connectivity on that frame-specific mask.

### B. Naval movement

Add deterministic waypoint following:

* A* or water-only steering on the frame-specific footprint-inflated mask;
* bounded speed/turn;
* swept footprint remains in water for the full movement segment—no clipping land or squeezing through channels narrower than the hull;
* stable collision/separation policy;
* hold-position default.

Do not add hydrodynamics.

### C. Dynamic base registry

Register carrier IDs as air bases. Jets use Phase 37 `BaseAssignment`.

RTB:

* resolve carrier's current position every tick;
* move toward current recovery point;
* state machine `RTB → APPROACH → RECOVERED`;
* enter APPROACH within `RECOVERY_APPROACH_RADIUS=8.0`;
* target an approach point astern of carrier heading, descend to `RECOVERY_ALTITUDE=2.0`, and align within `RECOVERY_HEADING_TOLERANCE_DEG=30`;
* capture as RECOVERED only within `RECOVERY_CAPTURE_RADIUS=2.0`, altitude `<=2.0`, heading tolerance met, and relative speed `<=RECOVERY_RELATIVE_SPEED_MAX=2.0` world units/tick;
* record carrier recovery outcome;
* if carrier destroyed/unavailable, switch to valid alternate or begin mission abort;
* no teleporting to original carrier position.

Initial carrier-based jets spawn in air launch positions around the carrier, even though underlying tile is water.

### D. Objectives and combat status

Carriers have HP and may be PROTECT subjects. They are unarmed by default in this phase. Existing enemy threats may target them only if their catalog permits `SURFACE_VESSEL`; otherwise they remain safe until Phase 41+.

Scenario validation requires every jet base ID to exist and at least one recoverable base/alternate for Auto-balanced generated scenarios.

Expose carrier current position, route forecast, HP/availability, recovery envelope, and alternate-base status through the jet observation/base registry so Phase 45 can estimate recovery risk and fuel. Do not duplicate that scoring here.

### E. Trace/viewer/editor

Trace moving carrier position, heading, HP, route/status, and base relationship. Render a simple carrier mesh/glyph and recovery ring. HUD shows assigned base and carrier health.

Editor adds carrier placement only on water, route points, hold/move toggle, frame, delete/edit, and assigning jets to a primary/alternate base.

### F. Random generation

Add optional `friendly_carrier_count`/`carrier_base_mode`. Ensure a connected water component large enough before placement. If carrier base mode is enabled, terrain generator's minimum water component is mandatory.

### G. Tests

Cover water-only placement/path, footprint overlap at coast, rejection of one-cell channels for large hulls, land barrier, movement/turn limits, each recovery-envelope boundary and state transition, moving recovery, carrier destruction alternate/abort, objective protection, trace/editor round-trip, generated water viability, and legacy fixed bases.

## Acceptance criteria

* Carriers never spawn/move on land.
* Carrier-based jets launch/recover against current carrier position.
* Legacy fixed-base scenarios still work.
* No carrier weapon behavior is smuggled into this phase.
