# Phase 37 — Platform, Faction, Objective & Authoritative Altitude Contracts

> **LLM implementation prompt:** Assume Phases 34–36 are complete. Read `docs/19_Terrain_Naval_and_Combined_Arms_Design_Document.md` §§1.2, 6–7, 13–16. Build typed cross-domain contracts before adding ships/tanks/new defenses. Preserve existing jet/CNP behavior while extracting shared world helpers. This phase is a refactor plus authoritative aircraft altitude and mission-objective migration; it adds no naval/tank combat.

## Deliverables

### A. Shared taxonomy

In a dependency-neutral module define:

```python
Faction = Literal["FRIENDLY", "ENEMY"]
PlatformDomain = Literal["AIR", "GROUND", "SURFACE", "INSTALLATION"]
TargetClass = Literal[
    "AIRCRAFT", "GROUND_VEHICLE", "SURFACE_VESSEL",
    "INSTALLATION", "IN_FLIGHT_MISSILE",
]
```

Add catalog-driven helpers for hostility and legal target class. World adjudication must reject friendly fire and illegal domain targets even for injected intents.

Add authoritative `ScenarioConfig.combat_seed: int` independent of generation metadata. Generated scenarios copy their root seed; manual/legacy scenarios default to zero. Bridge/model constructors receive it explicitly. Later keyed combat outcomes may use only this seed plus stable event identity.

### B. Authoritative aircraft altitude

Extend frame specs/state:

* `cruise_altitude`;
* `max_climb_per_tick`;
* `min_combat_altitude`;
* jet `altitude` and `desired_altitude`;
* observation/current altitude;
* optional desired altitude in intent.

Movement remains 2.5D:

* X/Y speed stays current;
* altitude changes are independently clamped;
* fuel behavior remains flat per tick in this expansion;
* terrain collision clamps/rejects below safe altitude;
* takeoff/landing transitions use base altitude zero.

City LOS uses actual jet altitude. Trace `JetFrame` carries altitude. The 3D viewer uses it as authoritative baseline and removes competing client-only takeoff/dive targets; cosmetic bank/easing may remain but may not contradict trace Z.

### C. Shared platform state/adjudication

Introduce a protocol/base dataclass exposing:

* ID/faction/domain/target class;
* current 2D position and sight/altitude;
* heading, HP/alive;
* weapon inventory access where supported.

Do not make Mesa inheritance unnecessarily deep. Keep `JetAgent` and its runtime, but make LOS/range/arc/damage/hostility helpers platform-neutral for later ships/tanks.

### D. Mission objectives

Add typed `MissionObjective` and objective status:

* DESTROY
* PROTECT
* SURVIVE
* RETURN

The schema includes `priority: float = Field(default=0.5, ge=0.0, le=1.0)` for later deterministic manager target/CFP scheduling. It also includes `until_tick: int | None`: required and positive only for SURVIVE and forbidden otherwise.

`ScenarioConfig.mission_objectives` is optional for legacy input. Migration synthesizes the old “destroy all targets/SAMs” condition. Validate unique objective IDs, known subject IDs, nonempty subjects, and sensible faction/type relationships.

Replace hardcoded termination with an evaluator producing:

* RUNNING
* SUCCESS
* FAILURE
* ABORTED
* TIMEOUT

Preserve old metric booleans as derived compatibility fields until consumers migrate.

Exact semantics/precedence:

* DESTROY completes when every subject is destroyed.
* PROTECT fails immediately when any subject is destroyed; all active required PROTECT objectives complete together when every required non-PROTECT objective is complete and protected subjects remain alive.
* SURVIVE has `until_tick`, fails on any subject loss before it, and completes at that tick.
* RETURN fails when any required subject is destroyed or cannot reach a valid base; it completes only when every required subject returns alive.
* Optional objectives never gate global result.
* Required FAILURE wins simultaneous success; otherwise all required complete → SUCCESS; unresolved at tick limit → TIMEOUT; ABORTED only when an unresolved objective requires active progress and no friendly platform can make it (passive SURVIVE/PROTECT objectives keep waiting); else RUNNING.

Replace the old “scenario needs one jet” invariant with “at least one friendly active platform and one required objective.” Jetless tank/naval scenarios must run; bridge creates CNP runtimes only for jets while scripted units continue through world stages.

### E. Dynamic base references

Introduce:

```python
class FixedAirbaseSpec(BaseModel):
    base_id: str
    position: tuple[float, float]

class BaseAssignment(BaseModel):
    primary_base_id: str
    alternate_base_id: str | None = None
```

Legacy `home_base` migrates to implicit `Base_Home`. Jets gain base assignment while compatibility accessors still expose resolved home position. A world `base_position(base_id)` lookup is the only RTB destination source. Phase 40 will add carriers to this registry.

### F. Staged bridge/world tick

Replace the monolithic observation → intent → `model.step()` contract with explicit stages:

1. pre-movement observation and mobility/task intents, including maneuver defenses against existing tracks;
2. apply movement;
3. post-movement observation and launch/direct-fire intents;
4. register launches and expose immediate inbound threats;
5. fast non-maneuver countermeasure intents plus evaluation of already-flown defensive geometry;
6. combat advance/resolution/finalization.

Keep a compatibility `step()` wrapper for focused legacy tests only. Production bridge uses stages, each callable exactly once per tick with state guards. This ordering is required before Phases 38–39.

Stabilize CNP as part of this bridge refactor:

* contract/message IDs derive from stable manager ID, target ID, logical tick, and per-source sequence—not UUIDs;
* bid deadlines use logical ticks rather than wall-clock sleep;
* submit agents and drain messages in stable `(tick, sender_id, sequence)` order;
* claim/bid ties use existing documented deterministic tie-breaks.

Async queues may remain, but scheduler timing may not choose claims or IDs. This is required before Phase 38 can claim full-run missile determinism.

### G. Lossless editor draft migration

Refactor editor load/save conversion so it preserves every schema field even when no form exists yet. Use a schema-backed draft model or merge edited fields into a deep copy of `ScenarioConfig.model_dump()`. Loading then saving without edits must preserve objectives, bases, combat seed, generation metadata, and future guided loadouts byte-equivalently apart from documented formatting/key order.

Update preview/header builders for altitude/objectives/bases. Do not postpone preservation to Phase 44.

### H. Generalized feasibility contracts

Introduce versioned `ObjectiveFeasibility` and per-concrete-objective/target-class allocations. Phase 30 target-type totals become derived compatibility summaries only. Consume Phase 35's generator-v2 metadata without relabeling it; old metadata loads as provenance and may be regenerated only by a matching supported generator implementation. Phase 44 increments again when combined-arms generation changes recipes.

### I. Generalized trace

Add versioned:

* objective definitions/status;
* fixed bases;
* altitude;
* platform faction/domain/class metadata.

Keep old target/jet fields where needed by 2D/3D viewers. Do not add empty ship/tank frame lists without a clear versioned default.

### J. Scenario migration

Increment scenario version to v3 through raw sequential `migrate_v2_to_v3()`. Never rely on Pydantic defaults to identify old input.

### K. Tests

Cover:

* taxonomy/hostility/target-class rejection;
* altitude climb/descent clamps and city LOS change;
* trace/viewer uses authoritative altitude;
* legacy scenario objective/base migration;
* each mission objective status and termination precedence;
* old default scenario has unchanged outcome;
* required-objective truth table, optional aggregation, and simultaneous precedence;
* an injected non-CNP platform test double reaches world tick stages without jet-runtime assumptions; concrete jetless carrier/tank scenarios are added and tested in their later phases;
* RTB through base registry;
* combat seed reaches model and survives save/load;
* each stage rejects duplicate/out-of-order calls;
* stable IDs/logical deadlines/message ordering produce identical CNP results under deliberately varied asyncio scheduling;
* Load → Save preserves fields without editor controls;
* generalized objective feasibility and generator metadata migration;
* injected friendly-fire/illegal-target action rejected;
* no GUI imports in shared modules.

## Acceptance criteria

* Existing CNP and default run pass unchanged apart from versioned trace/altitude fields.
* One authoritative altitude drives LOS and viewer Z.
* Mission termination no longer assumes every combat unit is an enemy target.
* Later platform phases can register units without editing jet CNP internals.
