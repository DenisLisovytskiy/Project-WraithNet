# Design Document: Airframes, Weapon Behavior, Fuel & RTB ("Phase 4 Expansion")

> This document is the source of truth for a new expansion of the WraithNet **simulation core** — unlike the Phase 3 Expansion (`docs/09_..._Design_Document.md`, 3D live viewer + scenario editor), this one changes `wraithnet/` itself: movement, combat resolution, and CNP bidding all gain new behavior. **Nothing in this document is implemented yet.** See `docs/phases/Phase_20..24_*.md` for the phase-by-phase build plan.

## 0. What this expansion is, in one paragraph

Today every jet is mechanically identical (same speed, same "weapon" abstraction — a suitability-scored count with no range/arc/fuel), and combat is a single global `STRIKE_RANGE` check. This expansion gives jets distinct **airframes** (speed/turn-rate/fuel/HP/evasion, drawn from a small named catalog), gives weapons **range and firing arcs** (some are all-aspect/self-guided and can be launched at — and will track — a target anywhere around the jet regardless of which way the jet is currently facing; others need the jet's nose actually pointed within a narrow forward cone of the target before they can fire at all), and gives jets **fuel** that can run out — forcing an autonomous Return-to-Base (RTB) decision, with contract hand-off to the rest of the swarm exactly like a destroyed jet already triggers today (Phase 8's manager-fallback).

## 1. Decisions already made (with the user, before design)

| Decision | Choice | Why it matters architecturally |
|---|---|---|
| Fuel exhaustion | **RTB-or-lost**: a jet must fly home before fuel runs out; if it doesn't make it, it's lost | Needs a scenario-level home base, autonomous bingo-fuel logic, and a *third* jet outcome (returned-to-base) distinct from "destroyed" and "survived-at-scenario-end" |
| Heading | **Independent of travel direction** (a jet can fly one way while facing/aiming another — crab) | Heading becomes real, persisted, agent-controlled per-tick state, not just a rendering-time derived quantity (as `viewer3d` Phase 16 was going to infer it) |
| Maneuverability | **Turn rate AND evasion/survivability** — one stat, two effects | Ties directly into both the firing-arc mechanic (§4) and SAM damage resolution (§6), without adding a second unrelated stat |
| Jet airframes | **Fixed named catalog** (e.g. `INTERCEPTOR`/`STRIKE_FIGHTER`/`HEAVY_BOMBER`), not free-form per-jet stats | Consistent with how `TargetType`/`WeaponType` already work — easy to validate, easy to balance, easy for Phase 18's editor to present as a dropdown rather than five raw number fields |
| Weapon catalog | **Fixed named catalog, extended with more entries** — not a fully data-driven/user-definable system | Keeps weapon behavior (range/arc/suitability/damage) as reviewable, testable Python constants, exactly like `WEAPON_SUITABILITY` already is today — no new "weapon authoring" surface to validate/secure |

## 2. New concepts and how they fit the existing architecture

### 2.1 Jet frames (`wraithnet/config.py`)

```python
JetFrameType = Literal["INTERCEPTOR", "STRIKE_FIGHTER", "HEAVY_BOMBER"]

@dataclass(frozen=True)
class JetFrameSpec:
    max_speed: float               # replaces the current global JET_SPEED
    turn_rate_deg_per_tick: float  # heading change limit per tick
    fuel_capacity: float
    fuel_burn_per_tick: float      # flat burn while engine is running — see §5's scope note
    ordnance_slots: int            # total weapon-loadout capacity; weapons cost slot_cost each (§3)
    hp: int                        # replaces the current global JET_HP
    evasion_bonus: int             # flat reduction to incoming SAM damage, floored at 0 — see §6

JET_FRAME_CATALOG: dict[JetFrameType, JetFrameSpec] = { ... }
```

Every jet in a scenario now references one `frame_type`; today's single global `JET_SPEED`/`JET_HP` become per-frame lookups. This is the same "hardcoded global → named catalog" move the project already made once for weapon suitability (Phase 6) — same pattern, same level of rigor.

### 2.2 Weapon catalog extension (`wraithnet/config.py`)

Today, `WeaponType` is a two-entry `Literal` and `WEAPON_SUITABILITY` is the *only* per-weapon data. This expansion turns each weapon into a full spec and adds at least two new weapons that concretely demonstrate the two firing-arc categories the user asked for:

```python
WeaponType = Literal["JDAM", "AIM9X", "AMRAAM", "FLARE_DECOY_MISSILE"]  # exact final names are Phase 21's call

@dataclass(frozen=True)
class WeaponSpec:
    range: float                          # replaces the current global STRIKE_RANGE
    firing_arc_deg: float                 # 360 = all-aspect/self-guided: fires and tracks regardless of jet heading,
                                           # including a target behind the jet; <360 = the jet's heading itself must
                                           # be within this cone of the target before the weapon can fire at all
    slot_cost: int                        # consumes this many of the frame's ordnance_slots per unit carried
    damage_per_hit: int                   # generalizes today's implicit "1 hp per strike"
    suitability: dict[TargetType, float]  # today's WEAPON_SUITABILITY, now grouped under its weapon

WEAPON_CATALOG: dict[WeaponType, WeaponSpec] = { ... }
```

* `JDAM`/`AIM9X` keep their existing suitability numbers and get a first `range`/`firing_arc_deg`/`slot_cost`/`damage_per_hit` (retrofitted, not redesigned — the fixed PoC scenario's outcome must not change, see §8).
* At least one **new long-range, all-aspect/self-guided weapon** (`firing_arc_deg = 360`) demonstrates that a jet can launch it at — and it will track — a target behind the jet even while the jet itself is still facing forward, away from that target. This is a property of the *missile's own guidance*, not of how it's mounted on the airframe — nothing about the jet's physical orientation changes; only the arc *check* is skipped for this weapon.
* At least one **new short-range, narrow-forward-cone weapon** (`firing_arc_deg` well under 360, e.g. 30–60°) demonstrates the case that forces a jet to actually turn to bear.
* `config.WEAPON_SUITABILITY` (the old flat dict) is removed entirely once every read site is migrated to `WEAPON_CATALOG[weapon_type].suitability[target_type]` — no two sources of truth for the same data.

### 2.3 Heading, turn rate, and firing arcs

* `JetAgent` (world.py) gains `heading: float` (degrees, `0` = facing `+y`, matching the existing world convention, increasing clockwise or counter-clockwise — Phase 20's call, document whichever is chosen) and `desired_heading: float | None`.
* `ActionIntent` gains `desired_heading: Optional[float]`, settable independent of `target_position`/`action_type` — an agent can issue `MOVE` toward one point while separately commanding a heading change toward another (the "independent heading" decision, §1).
* Each tick, `WraithNetModel.step` rotates `heading` toward `desired_heading` by at most `frame.turn_rate_deg_per_tick` (shortest angular direction) — exactly analogous to how `_apply_move` already caps *positional* movement at `frame.max_speed` per tick; this is a new, parallel "turn-rate-limited" update, not a replacement for the existing movement cap.
* `_apply_strike` gains an arc check: the angle between the jet's current `heading` and the vector from the jet to the target must be within `weapon_spec.firing_arc_deg / 2` (using `weapon_spec.range` in place of the old global `STRIKE_RANGE` for the existing distance check). `firing_arc_deg == 360` always passes trivially, regardless of which way the jet is facing — this single field cleanly expresses both "all-aspect/self-guided: fire and track in any direction, jet heading is irrelevant" and "must have the jet's nose pointed roughly at the target before it can fire" (forward cone), with no separate boolean and no notion of "mounting direction" needed.

**Key simplification — arc feasibility is an execution-time gate, not a bid-time disqualifier.** `compute_bid` (agents.py) is *not* changed to predict "how many ticks until this jet can turn to bear" — that would require path/turn-time optimization the rest of the CNP protocol has no analogue for (bids are still scored on suitability/payload/distance exactly as today). Instead, `decide_intent` handles arc infeasibility purely at execution time: if a jet is in weapon range but not yet in arc, its intent this tick is "rotate toward the target" (`desired_heading` set, `action_type` stays `MOVE`/`IDLE` as appropriate) rather than `STRIKE`; it fires on a later tick once turned. This keeps the CNP utility function's shape completely unchanged (§8) while still making narrow-arc weapons meaningfully harder to employ than omnidirectional ones — the cost shows up as *extra ticks to first strike*, not as a bidding-time penalty.

### 2.4 Fuel & RTB

* `JetAgent` gains `fuel: float`, initialized to `frame.fuel_capacity`, decremented by `frame.fuel_burn_per_tick` every tick it's alive and not yet landed (flat per-tick burn, not distance-scaled — see §5's explicit scope note).
* `ScenarioConfig` (Phase 14) gains `home_base: tuple[float, float]` — one shared base for the whole scenario (not per-jet, not multiple bases; PoC scope, same discipline as the project's other single-scenario simplifications).
* Each tick, an `IDLE` or negotiating agent computes its **bingo fuel**: the fuel needed to fly straight home from its current position at `frame.max_speed`, burning `frame.fuel_burn_per_tick` per tick, plus a fixed safety margin (e.g. ×1.2). Once `fuel <= bingo_fuel`, the agent autonomously aborts whatever it's doing (relinquishing any managed/contracted role — see §2.5) and flies straight home, ignoring further CFPs, until it lands.
* **Landing** (within a small radius of `home_base`, mirroring `STRIKE_RANGE`-style proximity checks elsewhere) marks the jet `returned_to_base = True` and removes it from `model.jets` (active play) — but this is **not** the same outcome as being destroyed, and must be tracked separately (see §6/§7). A jet that runs out of fuel *before* reaching home (`fuel <= 0` while still en route) is destroyed — reusing the exact same removal/manager-fallback plumbing Phase 8 already built for SAM kills, no new destruction code path needed.

### 2.5 Contract hand-off on RTB (reusing Phase 8's manager-fallback machinery)

A jet beginning RTB while holding a contract (as a `CONTRACTOR` or `MANAGER`) must not silently strand its manager/contractors — this is the same problem Phase 8 solved for destruction, just triggered by a different cause. Concretely:

* A contractor beginning RTB behaves, from its manager's point of view, exactly like a lost contractor — the manager needs to hear about it and re-task. Rather than inventing a second notification pathway, **the RTB-triggering jet's Bridge-side handling reuses `WraithNetBridge._notify_managers_of_losses`** (extended to accept "jets that are RTBing this tick," not only "jets destroyed this tick") — see Phase 22 for the exact wiring.
* A manager beginning RTB while other jets are still bidding into its open CFP needs the same graceful-abort behavior `_abort_open_management` already provides for a tick-level timeout (per `docs/02_Code_Review_Phases_3-5.md`'s original CNP bug fix) — reused, not reimplemented.

## 3. Interaction with existing invariants — what must NOT change

* **No RNG, anywhere.** Evasion (§6) reduces SAM damage by a flat, deterministic amount — never a hit-probability roll. This preserves `config.SAM_DAMAGE_PER_TICK`'s existing `# deterministic, no RNG` invariant, and every test that currently asserts exact damage/HP numbers across a run continues to be exactly reproducible.
* **The fixed PoC scenario's outcome must not silently change.** Retrofitting `JDAM`/`AIM9X` with a `range`/`firing_arc_deg` requires choosing values that reproduce the exact existing behavior for the default scenario (§8) — `range` should equal today's `STRIKE_RANGE` for both, and `firing_arc_deg` should be `360` for both (i.e., today's jets are omnidirectional shooters — arc is a *new* constraint only the new weapons introduce, not a retroactive nerf to the existing two).
* **CNP bid scoring's shape is unchanged** (§2.3) — `compute_bid`'s formula gains a weapon-lookup indirection (reading `range`/`suitability` from `WEAPON_CATALOG` instead of `config.STRIKE_RANGE`/`WEAPON_SUITABILITY` directly) but not a new term.
* **Determinism across repeated runs** (`tests/test_scenario_v2.py::test_determinism_across_repeated_runs`) must still hold for every new mechanic — turn-rate rotation, fuel burn, and bingo-fuel decisions are all pure functions of existing deterministic state, never wall-clock or random.

## 4. Metrics & termination — a new jet outcome bucket

`wraithnet/metrics.py::summarize` today has exactly two jet buckets: `jets_destroyed` (`initial - current`) and `jets_survived` (`current`, i.e. `len(model.jets)`). A jet that lands safely at home base is neither — it's not in `model.jets` anymore (so today's formula would wrongly count it as "destroyed"), but it wasn't lost in combat either. This expansion adds a third bucket, `jets_returned_to_base`, tracked explicitly (`model.jets_returned_to_base: set[str]`, mirroring how `model._initial_jet_ids` already tracks the starting roster) so `jets_destroyed = initial - current - returned`, not `initial - current`. See Phase 23 for the exact metrics/termination rewrite — this is flagged here because it's a genuine correctness trap, not just a nice-to-have addition, and every phase touching jet removal must be aware a *third* removal reason now exists (destroyed, RTB-landed, or — unchanged — none, still flying).

## 5. Explicit scope simplifications (not oversights)

* **Fuel burn is flat per tick, not scaled by distance/speed/afterburner use.** A jet loitering `IDLE` burns fuel at the same rate as one flying at `max_speed`. This is a deliberate PoC-level simplification — modeling power-setting-dependent burn would require a much richer flight-dynamics model this project has never had. Noted explicitly so it isn't "discovered" later as a bug.
* **Bingo-fuel math assumes a straight-line flight home at `max_speed`**, not accounting for any remaining turn-rate-limited heading changes en route, jamming, or further combat — a reasonable, conservative-if-anything approximation for a PoC, not a flight-planning system.
* **One shared home base per scenario**, not per-jet or multiple bases (§2.4).
* **No fuel resupply/rearm on landing** — a landed jet is done for the run; it does not relaunch. (If this project ever wants "sortie generation," that's a much larger future feature, not in scope here.)
* **Arc feasibility never blocks a bid** (§2.3) — only ever affects execution-time ticks-to-first-strike.

## 6. SAM damage & evasion

`WraithNetModel._apply_sam_damage` today: `jet.hp -= config.SAM_DAMAGE_PER_TICK` (flat `1`, deterministic). This expansion changes it to:

```python
damage = max(0, config.SAM_DAMAGE_PER_TICK - frame.evasion_bonus)
jet.hp -= damage
```

Still fully deterministic (§3) — a high-evasion frame (e.g. `INTERCEPTOR`) can reduce or fully negate `SAM_DAMAGE_PER_TICK`, making it meaningfully tougher to kill with SAMs than a `HEAVY_BOMBER`, without introducing any probability model.

## 7. Scenario schema & editor impact (builds on the Phase 3 Expansion)

* `wraithnet/scenario_schema.py::JetSpec` (Phase 14) gains `frame_type: JetFrameType`. A new `model_validator` enforces that the sum of `WEAPON_CATALOG[wt].slot_cost * count` over a jet's `payload` does not exceed `JET_FRAME_CATALOG[frame_type].ordnance_slots` — the "customize the amount and loadout of friendly jets" ask from this request is largely *already* satisfied by Phase 14's `JetSpec.payload`/Phase 18's editor payload form; this expansion's job is making that loadout meaningfully *constrained* by the chosen airframe, not re-building the loadout UI from scratch.
* `ScenarioConfig` gains `home_base: tuple[float, float]` (§2.4), with the same in-bounds validation Phase 14 already applies to every other position field.
* Phase 18's editor (`viewer3d/editor.py`) needs a frame-type picker per jet (a dropdown over `JetFrameType`, not a free-form stat form — consistent with §1's fixed-catalog decision) and a way to place/move the home base marker. This is Phase 24's job, not a re-opening of Phase 18 itself.

## 8. Implementation phases for the LLM

Build strictly in this order:

* **Phase 20 — Jet Frame Catalog & Independent Heading** (`docs/phases/Phase_20_Jet_Frame_Catalog_and_Heading.md`): `JetFrameType`/`JetFrameSpec`/`JET_FRAME_CATALOG`; `JetAgent.heading`/`desired_heading`; turn-rate-limited heading rotation each tick; `frame.max_speed`/`frame.hp` replace the global `JET_SPEED`/`JET_HP`; `ScenarioConfig.JetSpec.frame_type`. Fuel/evasion fields exist on the catalog but are not yet consumed by any simulation logic (flagged explicitly, consumed in Phases 22/23 — same forward-compatible-field discipline the project already used for `TraceTick.JetFrame.destroyed` in Phase 11).
* **Phase 21 — Weapon Catalog: Range, Firing Arc, Slot Cost & New Weapons** (`docs/phases/Phase_21_Weapon_Catalog_Range_Arc_and_New_Weapons.md`): `WeaponSpec`/`WEAPON_CATALOG` replaces `STRIKE_RANGE`/`WEAPON_SUITABILITY`; at least one new all-aspect/self-guided weapon (fires/tracks regardless of jet heading) and one new narrow-forward-cone weapon (requires the jet's heading to be pointed at the target); `_apply_strike`'s range+arc check; `decide_intent`'s "rotate to bear" behavior (§2.3); ordnance-slot capacity validation in `ScenarioConfig` (consumes Phase 20's `ordnance_slots`).
* **Phase 22 — Fuel Consumption & Bingo-Fuel RTB** (`docs/phases/Phase_22_Fuel_Consumption_and_RTB.md`): per-tick fuel burn (consumes Phase 20's `fuel_capacity`/`fuel_burn_per_tick`); `ScenarioConfig.home_base`; bingo-fuel calculation and autonomous RTB decision; contract-abort/manager-notification reuse (§2.5); landing/removal bookkeeping (`returned_to_base`, distinct from destruction); fuel-exhausted-en-route destruction (reusing Phase 8's plumbing exactly).
* **Phase 23 — Evasion, Metrics & Termination** (`docs/phases/Phase_23_Evasion_Metrics_and_Termination.md`): deterministic evasion-reduced SAM damage (consumes Phase 20's `evasion_bonus`, §6); the new `jets_returned_to_base` metrics bucket and corrected `jets_destroyed` formula (§4); a review of whether/how a scenario where jets RTB before completing the objective should be reported (solved / failed / partial — this phase's explicit job to define precisely, mirroring the rigor `docs/00_Design_Review_Findings.md`/Phase 5 already applied to the original termination condition).
* **Phase 24 — Scenario Editor, Viewers & Trace Schema Updates** (`docs/phases/Phase_24_Editor_and_Viewer_Updates_for_Airframes.md`): frame-type picker + home-base placement in `viewer3d/editor.py` (Phase 18); fuel gauges, heading indicators, firing-arc-cone overlays, home-base marker, and RTB status in both `viewer/` (2D, Phases 12–13) and `viewer3d/` (3D, Phases 16–17); `wraithnet/trace_schemas.py`/`TraceRecorder`/`LiveStreamServer`'s shared builder (Phases 11/15) extended with the new per-tick fields (`heading`, `fuel`, `returned_to_base`) both viewers need to visualize any of this.
