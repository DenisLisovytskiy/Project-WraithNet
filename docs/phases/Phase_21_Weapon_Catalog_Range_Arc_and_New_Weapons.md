# Phase 21 — Weapon Catalog: Range, Firing Arc, Slot Cost & New Weapons

> **How to use this document:** This is a complete, standalone prompt. Give an LLM coding agent this entire file as its task. Assume Phases 0–20 are complete and passing (jets have named airframes with `max_speed`/`hp`/`turn_rate_deg_per_tick`, and independent, turn-rate-limited `heading`/`desired_heading` state — see Phase 20). Read `docs/10_Airframes_Weapons_and_Fuel_Design_Document.md` §1, §2.2, §2.3, §3, and §8 in full before starting. This phase replaces the flat `config.STRIKE_RANGE`/`WEAPON_SUITABILITY` globals with a per-weapon catalog (range, firing arc, ordnance-slot cost, damage), adds at least two new weapons demonstrating the two firing-arc categories the user asked for, and teaches `decide_intent` to turn a jet to bear a narrow-arc weapon before striking. **The fixed PoC scenario's outcome must not change** — `JDAM`/`AIM9X` must keep their existing suitability numbers and get `range=1.0` (today's `STRIKE_RANGE`) and `firing_arc_deg=360` (i.e. omnidirectional, exactly matching today's implicit behavior).

> **Automated GUI tests:** Follow `docs/14_Automated_GUI_Testing_Design_Document.md`. This phase is simulation-core, but any new viewer-facing state must be covered by automated `viewer3d` tests in the phase that visualizes it (Phase 24) — and if you touch `viewer3d/` here, add those tests in *this* phase. Do not leave 3D GUI coverage as a later follow-up or as manual-only. Pixel/screenshot tests remain out of scope; headless Layer-1 / Layer-2 tests are required for GUI changes.

## Context

Today, `_apply_strike` checks a single global `STRIKE_RANGE` and a flat `WEAPON_SUITABILITY[(weapon_type, target_type)]` lookup, with no notion of facing at all. Phase 20 gave jets a real, persisted `heading`. This phase is where that heading finally matters: some weapons are all-aspect/self-guided — they can be launched at, and will track, a target in any direction relative to the jet, including one behind it, entirely independent of which way the jet's nose is currently pointed (the user's literal ask: the missile isn't "mounted backwards," it simply doesn't need the jet to be facing the target at all to engage it) — while others require the jet's actual heading to be within a narrow forward cone of the target before they can fire at all.

## Part A — `wraithnet/config.py` changes

```python
WeaponType = Literal["JDAM", "AIM9X", "AMRAAM", "GUN_POD"]  # exact final names/count are your call; must include
                                                              # at least one 360deg (all-aspect/self-guided) weapon
                                                              # and at least one narrow-forward-cone weapon beyond JDAM/AIM9X

@dataclass(frozen=True)
class WeaponSpec:
    range: float
    firing_arc_deg: float   # 360 = all-aspect/self-guided: fires and tracks regardless of the jet's heading
                             # (including a target behind the jet -- this is a guidance property of the
                             # weapon itself, not a statement about how it's mounted on the airframe);
                             # <360 = the jet's actual heading must be within this cone of the target
                             # before the weapon can fire at all
    slot_cost: int          # this weapon consumes this many of the frame's ordnance_slots, per unit carried
    damage_per_hit: int
    suitability: dict[TargetType, float]  # 0.0 = physically cannot damage this target type

WEAPON_CATALOG: dict[WeaponType, WeaponSpec] = {
    "JDAM": WeaponSpec(
        range=STRIKE_RANGE_LEGACY_VALUE,  # must equal 1.0 -- see context above
        firing_arc_deg=360.0,
        slot_cost=1, damage_per_hit=1,
        suitability={"HARDENED_BUNKER": 1.0, "MOBILE_RADAR": 0.0, "SAM_SITE": 1.0},
    ),
    "AIM9X": WeaponSpec(
        range=1.0, firing_arc_deg=360.0, slot_cost=1, damage_per_hit=1,
        suitability={"HARDENED_BUNKER": 0.0, "MOBILE_RADAR": 1.0, "SAM_SITE": 0.0},
    ),
    "AMRAAM": WeaponSpec(
        range=..., firing_arc_deg=360.0, slot_cost=..., damage_per_hit=...,
        suitability={...},  # long-range, all-aspect/self-guided -- the concrete example of a weapon that can
                             # engage a target behind the jet without the jet needing to turn toward it first
    ),
    "GUN_POD": WeaponSpec(
        range=..., firing_arc_deg=...,  # e.g. 30-60deg -- forward cone only
        slot_cost=..., damage_per_hit=...,
        suitability={...},  # short-range, narrow-arc -- the concrete "must point at the target" example
    ),
}
```

Fill in every `...`. Requirements for the two new weapons (exact names above are illustrative — pick your own if you prefer, but the *properties* below are required, not optional):

* **One new weapon with `firing_arc_deg=360.0`** and a **longer `range`** than `JDAM`/`AIM9X`'s `1.0` — this is the concrete demonstration of "some missiles can be fired and will track a target even when it's behind the jet" from the original request: the jet can be facing directly away from the target and still successfully engage it with this weapon, because the arc check is skipped entirely, not because the weapon is somehow mounted facing backwards. Give it a `suitability` profile that makes it a meaningfully different tactical choice from the existing two (e.g. good against `MOBILE_RADAR` and `SAM_SITE`, mediocre against `HARDENED_BUNKER`) rather than a strictly-better `AIM9X`.
* **One new weapon with `firing_arc_deg` well under `360` (e.g. `30`–`60`)** and a **shorter `range`** — the concrete demonstration of "others need to be fired in a cone of a specific size in front of the plane." Give it a high suitability against at least one target type to make it worth the extra maneuvering cost (otherwise no rational agent would ever choose it — mirror the same "give every design number a reason to exist" discipline `docs/00_Design_Review_Findings.md`/`config.SCENARIO`'s comments already established).
* **Remove `config.STRIKE_RANGE` and `config.WEAPON_SUITABILITY` entirely** once every read site is migrated (see Parts B–C) — no two sources of truth for the same data. `grep -rn "STRIKE_RANGE\|WEAPON_SUITABILITY" wraithnet/ tests/` must return no matches when you're done, other than inside `WEAPON_CATALOG`'s own definition/tests referencing it by its new name.

## Part B — `wraithnet/world.py` changes

`_apply_strike` gains a firing-arc check, and both its range and suitability checks now read from `WEAPON_CATALOG`:

```python
def _apply_strike(self, jet: JetAgent, target_id, weapon_type) -> None:
    target = self.targets.get(target_id) if target_id else None
    if target is None or weapon_type is None or jet.payload.get(weapon_type, 0) <= 0:
        return
    weapon = config.WEAPON_CATALOG[weapon_type]
    if weapon.suitability.get(target.target_type, 0.0) <= 0.0:
        return
    if math.dist(jet.pos, target.position) > weapon.range:
        return
    if weapon.firing_arc_deg < 360.0 and not self._target_in_firing_arc(jet, target, weapon):
        return  # in range but not pointed at it -- see decide_intent's "rotate to bear" behavior, Part C
    jet.payload[weapon_type] -= 1
    target.hp_remaining = max(0, target.hp_remaining - weapon.damage_per_hit)
    ...  # strike_log append unchanged in shape, still one entry per landed strike
```

New helper:

```python
def _target_in_firing_arc(self, jet: JetAgent, target: TargetState, weapon: "WeaponSpec") -> bool:
    bearing = math.degrees(math.atan2(target.position[0] - jet.pos[0], target.position[1] - jet.pos[1])) % 360.0
    # NOTE: match this to whichever heading convention Phase 20 actually chose
    # (0deg = facing +y, clockwise) -- get atan2's argument order/sign right for
    # THAT convention, not a generic math convention. Write a focused unit test
    # (Part E) with an unambiguous, hand-computed case before trusting this.
    delta = (bearing - jet.heading + 180.0) % 360.0 - 180.0
    return abs(delta) <= weapon.firing_arc_deg / 2.0
```

`target.hp_remaining = max(0, target.hp_remaining - weapon.damage_per_hit)` generalizes today's hardcoded `- 1` — verify `TargetInfo`/`TargetState`'s existing invariant (`hp_remaining` never negative, never above `hp_required`) still holds for `damage_per_hit > 1` (it does, since `max(0, ...)` already floors it — no schema change needed here, but add a test, Part E).

## Part C — `wraithnet/agents.py` changes: "rotate to bear" behavior

`compute_bid` now reads the weapon catalog instead of the flat dict, with **no change to the utility formula's shape** (per the design's key simplification — arc feasibility is never a bid-time factor):

```python
def compute_bid(observation, target, hp_needed=None):
    ask = target.hp_remaining if hp_needed is None else hp_needed
    suitable = [
        (wt, count) for wt, count in observation.payload.items()
        if count > 0 and config.WEAPON_CATALOG[wt].suitability.get(target.target_type, 0.0) > 0.0
    ]
    if not suitable:
        return None
    weapon_type, count = max(suitable, key=lambda pair: (pair[1], pair[0]))
    suitability = config.WEAPON_CATALOG[weapon_type].suitability[target.target_type]
    ...  # rest of the formula (payload_score, distance_score, utility) unchanged in shape
```

`decide_intent` gains the arc-aware branch (replacing the current single `if distance <= config.STRIKE_RANGE and directly_sensed:` check):

```python
def decide_intent(self) -> ActionIntent:
    ...  # unchanged up through computing `distance` and `directly_sensed`
    weapon = config.WEAPON_CATALOG[self.committed_weapon_type]
    in_range = distance <= weapon.range
    if in_range and directly_sensed:
        bearing = _bearing_to(obs.current_position, target.position)  # same formula/convention as world.py's _target_in_firing_arc -- factor into a shared helper if practical, do NOT let the two implementations drift
        in_arc = weapon.firing_arc_deg >= 360.0 or _angular_distance(bearing, obs.current_heading) <= weapon.firing_arc_deg / 2.0
        if in_arc:
            self.committed_payload -= 1
            return ActionIntent(..., action_type="STRIKE", target_id=target.target_id,
                                 weapon_type=self.committed_weapon_type)
        # In range but not pointed at the target yet: turn toward it this tick
        # instead of striking. Still counts as "in position," just not yet
        # able to fire -- does not consume payload, does not lose the contract.
        return ActionIntent(..., action_type="IDLE", desired_heading=bearing)
    return ActionIntent(..., action_type="MOVE", target_position=target.position, desired_heading=bearing)
```

**Important**: even while `MOVE`-ing toward a target that's still out of range, set `desired_heading` toward it too (last line above) — this lets a jet start turning *while* closing the distance, rather than arriving in range with the wrong heading and needing extra idle ticks to turn afterward. This is a meaningful, deliberate improvement over "only turn once in range" and should not be skipped for the sake of a simpler diff.

Write `_bearing_to(from_pos, to_pos) -> float` and `_angular_distance(a, b) -> float` as small shared helpers in `agents.py` (or a tiny shared module both `agents.py` and `world.py` import from, if you prefer — your call, but **do not duplicate the bearing/angle-difference formula with any drift between the two files**; a mismatched convention between `decide_intent`'s prediction and `_apply_strike`'s actual gate would silently produce jets that believe they're in-arc but never actually land a hit, or vice versa — this is the single easiest bug to introduce in this phase, guard against it explicitly with a shared-implementation or a cross-checking test, Part E).

## Part D — `wraithnet/scenario_schema.py` changes: ordnance-slot capacity

`ScenarioConfig`'s `model_validator` (Phase 14) gains a new rule: for every jet, `sum(config.WEAPON_CATALOG[wt].slot_cost * count for wt, count in jet.payload.items()) <= config.JET_FRAME_CATALOG[jet.frame_type].ordnance_slots`. Violating this raises the same kind of human-readable `ValueError` Phase 14's other four rules already do (e.g. `"Jet 'Jet_2' payload requires 9 ordnance slots but its STRIKE_FIGHTER frame only has 7"`). Update `scenarios/default.json`/`_default_scenario()` if the existing fixed-scenario jets' payloads don't already fit within `STRIKE_FIGHTER`'s `ordnance_slots` (they should, given Phase 20's chosen `ordnance_slots=7` for `STRIKE_FIGHTER` and the existing scenario's largest single-jet payload — verify this arithmetic explicitly rather than assuming it).

## Part E — Tests

### `tests/test_config_weapons.py` (new file, or add to `tests/test_world.py` if this project has no dedicated config-tests file — check first)

* Every entry in `WEAPON_CATALOG` has `range > 0`, `slot_cost >= 1`, `damage_per_hit >= 1`, and at least one `suitability` value `> 0.0` (a weapon useless against every target type would be dead content).
* At least one weapon has `firing_arc_deg == 360.0` and at least one has `firing_arc_deg < 360.0` (a structural check that the two required categories actually exist, independent of their specific names).
* `JDAM`/`AIM9X` specifically: `range == 1.0`, `firing_arc_deg == 360.0`, and their `suitability` dicts match today's `WEAPON_SUITABILITY` values exactly (the backward-compatibility regression check called out in this phase's header).

### `tests/test_world.py` additions

* `_target_in_firing_arc`: hand-computed cases with an unambiguous heading/target-position pair for each of "target dead ahead → in arc for a 30° weapon," "target directly behind → in arc for a 360° weapon but NOT for a 30° weapon," "target at the exact edge of a 60° arc (30° off heading) → in arc," "target 31° off heading of a 60° arc weapon → not in arc." Get the exact convention (Phase 20's heading convention) right here — this is the test that would catch a sign/axis error immediately.
* `_apply_strike` with a target in range but outside a narrow-arc weapon's firing cone is a no-op (payload not consumed, target HP unchanged) — mirrors the existing `test_strike_with_zero_payload_in_range_is_noop`-style regression test convention.
* `_apply_strike` with `damage_per_hit=2` (or any value `> 1`) correctly reduces `hp_remaining` by that amount, still floored at `0`, still never producing a negative value even on an overkill hit.

### `tests/test_agents.py` additions

* `compute_bid` behaves identically to before this phase for `JDAM`/`AIM9X` against the existing scenario's target types (a direct before/after regression, since the formula's shape didn't change but its data source did).
* `decide_intent`, given a committed narrow-arc weapon and a target currently in range but outside the firing arc, returns `action_type="IDLE"` with `desired_heading` set toward the target, and does **not** decrement `committed_payload`.
* `decide_intent`, given a committed narrow-arc weapon and a target in range and inside the firing arc, returns `action_type="STRIKE"` exactly as before this phase.
* `decide_intent`, given a target still out of range, returns `action_type="MOVE"` with **both** `target_position` and `desired_heading` set toward the target (the "turn while closing" improvement, Part C) — this is worth its own explicit test since it's easy to accidentally only set one of the two fields.
* **Cross-check test proving `agents.py` and `world.py` agree**: construct a jet/target pair, ask `decide_intent`'s helper (or the shared helper, if you factored one out) whether it believes it's in-arc, then run the same geometry through `world.py::_apply_strike`'s actual gate, and assert they agree — across at least a handful of angles including a couple of near-boundary cases. This is the test that directly guards against the "two implementations drift" risk flagged in Part C.

### Full-suite regression

* Run the entire existing `pytest` suite and confirm the fixed PoC scenario (`python -m wraithnet.main`, no flags) still produces the exact same printed summary as before this phase — this is the hard requirement stated in this phase's header, not a nice-to-have.

## Explicit non-goals for this phase

* No fuel/RTB, no evasion — Phases 22–23.
* No bid-time arc-feasibility penalty of any kind (§2.3 of the design doc — a deliberate, permanent simplification, not deferred to a later phase).
* No editor/viewer changes for the new weapons/arcs — Phase 24.
* Do not add more than the two required new weapons unless you have a specific reason to — two is sufficient to demonstrate both categories; a larger catalog is not this phase's goal.

## Acceptance criteria

1. `pytest` passes in full, including all new/extended tests.
2. `python -m wraithnet.main` (no flags) produces the identical printed summary it did before this phase.
3. A hand-crafted scenario giving one jet only the new narrow-arc weapon, positioned so its target starts outside the firing cone, results (via a short scripted run or a targeted test) in that jet visibly rotating into arc over one or more ticks before its first successful strike — not striking immediately, and not failing to strike at all.
4. `grep -rn "STRIKE_RANGE\|WEAPON_SUITABILITY" wraithnet/ tests/` returns no matches outside `WEAPON_CATALOG`'s own definition and any test that explicitly names it by its new identifier.
5. The ordnance-slot capacity validator (Part D) rejects a hand-crafted scenario where a jet's payload exceeds its frame's `ordnance_slots`, with a human-readable error message.
