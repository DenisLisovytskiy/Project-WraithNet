# Phase 39 — Aircraft Countermeasures: Flares & Notching

> **LLM implementation prompt:** Assume Phase 38 is complete. Read `docs/19_Terrain_Naval_and_Combined_Arms_Design_Document.md` §9. Add seeker-specific aircraft defense decisions and effects. Flares affect infrared missiles only; notching affects radar tracks only. Defenses must consume resources/maneuver time and use the keyed deterministic combat-roll system.

## Deliverables

### A. Jet/frame/scenario state

Add frame defaults and optional per-jet overrides:

* flare capacity/current count;
* flare cooldown;
* radar warning receiver capability;
* notch angle window;
* notch minimum speed;
* notch hold ticks;
* defensive state/target missile.

Trace includes remaining flares and defensive state.

Observation/state must also expose flare remaining, capacity, cooldown, radar-warning capability, and notch geometry inputs to Phase 45's typed CNP bid proposal. Low/no flares are not scored here; Phase 45 applies the conditional IR-exposure penalty.

Increment persisted scenario schema to v5 with `migrate_v4_to_v5()` supplying frame-default countermeasure values.

### B. Observation and intent

Observation exposes only detectable inbound threats:

```python
class InboundThreat:
    missile_id: str
    guidance: GuidanceType
    estimated_bearing: float
    estimated_time_to_impact: float
    support_emitter_id: str | None

class MobilityIntent:
    ...
    maneuver_defense: Literal["NONE", "NOTCH"] = "NONE"
    defensive_threat_id: str | None = None

class FastDefenseIntent:
    countermeasure_action: Literal["NONE", "FLARE"] = "NONE"
    defensive_threat_id: str | None = None
```

Pre-movement observations expose already tracked threats and NOTCH may override desired heading/movement because geometry is required. After movement/new launches, FLARE is available as a fast countermeasure and does not freeze movement. A radar launch first detected after movement may trigger a notch on the next tick only; never grant an extra instantaneous turn.

### C. Flare behavior

* Validate flares > 0 and cooldown ready.
* Consume one flare and emit trace event.
* Affect nearby inbound IR missiles targeting that jet during a short window.
* Decoy probability combines flare quality, missile resistance, aspect/range, and keyed roll.
* Successful decoy resolves missile `DECOYED`.
* No effect on radar missiles.

### D. Notch behavior

For each radar threat:

* before active terminal guidance, derive line-of-sight bearing to the support emitter;
* after active terminal guidance, derive geometry from missile-to-target relative position and velocity instead;
* command velocity heading approximately ±90°;
* require actual speed and angle inside configured window;
* accumulate consecutive hold ticks;
* on completion, make keyed break-lock check;
* supported missile outside active terminal range becomes `LOST_LOCK` if support track breaks;
* active-terminal weapon may have reduced but nonzero notch susceptibility according to catalog, evaluated against its own seeker geometry.

Notching does not affect IR missiles and is not successful from an IDLE zero-speed state.

### E. Defensive AI

Deterministic priority:

1. shortest estimated time to impact;
2. radar/IR action compatible with available defense;
3. stable missile ID tie-break.

Agent resumes contract/RTB after threat clears. Defense may delay attacks and burns normal fuel. A jet low on fuel still defends itself before continuing RTB.

### F. Viewer/HUD/metrics

Render flare particles from events, notch heading cue, inbound missile warning, seeker type, and lock state. Do not infer defense success visually.

Metrics: flare attempts/success, flares remaining, notch attempts/completions/lock breaks, deaths by guidance type.

### G. Tests

* flare inventory/cooldown;
* IR decoy success/failure deterministic fixtures;
* flare has zero radar effect;
* valid perpendicular moving notch;
* wrong angle/low speed/insufficient hold fails;
* notch has zero IR effect;
* support/terminal behavior;
* AI threat priority and contract resumption;
* trace/viewer event consumption;
* insertion-order determinism.

## Acceptance criteria

* Seeker-specific defenses are enforced in world adjudication.
* Countermeasures are finite and cannot be spammed.
* Notching is geometry/motion-based, not a probability button.
* Existing missions without inbound missiles behave unchanged.
