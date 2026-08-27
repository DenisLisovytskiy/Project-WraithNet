# Phase 6 — Weapon System & Schema Foundations

> **How to use this document:** This is a complete, standalone prompt. Give an LLM coding agent this entire file as its task. Assume Phases 0–5 are complete and passing (`pytest -q` is green, `python -m wraithnet.main` runs the V1 scenario to completion) — the full V1 PoC described in `docs/6th-Gen_Warfare_Sim_Design_Document_v2.md` (single weapon type, perfect information, no hazards). This phase begins the "Phase 2 Expansion" described in `docs/04_6th-Gen_Warfare_Sim_Design_Document_v3_Phase2.md`. Read that document's §2, §4, and §6 before starting — this phase implements exactly the schema and weapon-system pieces of it, nothing else. Do not implement Fog of War, SAM sites, or jamming in this phase; they are Phases 7–9 and depend on this phase's schema changes landing first.

## Context

WraithNet's V1 PoC treats every jet's ordnance as one interchangeable integer (`payload_remaining`) and every target as equally strikeable. This phase introduces **heterogeneous weaponry**: two weapon types (`JDAM`, `AIM9X`), three target types (`HARDENED_BUNKER`, `MOBILE_RADAR`, `SAM_SITE` — the third exists now for schema completeness even though no `SAM_SITE` targets are spawned until Phase 8), and a fixed suitability table that determines which weapon can damage which target type. This is a **breaking schema change** — `Observation.payload_remaining: int` is replaced by `Observation.payload: dict[WeaponType, int]`, and every place in the V1 codebase that reads the old scalar field must be updated.

This phase also adds two `Observation` fields (`sensor_radius`, `comms_jammed`) that later phases (7 and 9) will actually *use* — in this phase, populate them with fixed, inert values (`sensor_radius` = the new `config.SENSOR_RADIUS` constant; `comms_jammed` always `False`) and do not change `build_observation`'s target-visibility behavior (it still returns *all* live targets, exactly like V1 — sensor-based filtering is Phase 7's job). This mirrors how V1 Phase 1 added `TargetInfo.claimed` and hard-coded it to `False` until Phase 4 wired in the real value — keep that same "field exists now, behavior lands later" pattern here.

## Part A — `wraithnet/config.py` additions

Add, without removing or renaming anything existing:

```python
from typing import Literal

WeaponType = Literal["JDAM", "AIM9X"]
TargetType = Literal["HARDENED_BUNKER", "MOBILE_RADAR", "SAM_SITE"]

WEAPON_SUITABILITY: dict[tuple[str, str], float] = {
    ("JDAM", "HARDENED_BUNKER"): 1.0,
    ("JDAM", "MOBILE_RADAR"): 0.0,
    ("JDAM", "SAM_SITE"): 1.0,
    ("AIM9X", "HARDENED_BUNKER"): 0.0,
    ("AIM9X", "MOBILE_RADAR"): 1.0,
    ("AIM9X", "SAM_SITE"): 0.0,
}

UTILITY_W_WEAPON: float = 10.0   # dominates W1+W2's <=1.5 combined range; see docs/03_..., finding I2/I3

# --- New: sensor radius (used starting Phase 7; populated but inert in this phase) ---
SENSOR_RADIUS: float = 65.0

# --- Updated scenario: v3 doc §2 (extends, does not replace, the v2 scenario in spirit) ---
SCENARIO = {
    "targets": [
        {"target_id": "Target_A", "position": (75.0, 75.0), "hp_required": 5, "target_type": "HARDENED_BUNKER"},
        {"target_id": "Target_B", "position": (25.0, 25.0), "hp_required": 1, "target_type": "MOBILE_RADAR"},
    ],
    "jets": [
        {"agent_id": "Jet_1", "position": (70.0, 70.0), "payload": {"JDAM": 1, "AIM9X": 0}},
        {"agent_id": "Jet_2", "position": (5.0, 95.0), "payload": {"JDAM": 4, "AIM9X": 0}},
        {"agent_id": "Jet_3", "position": (95.0, 5.0), "payload": {"JDAM": 3, "AIM9X": 2}},
    ],
}
```

Note: `SCENARIO["targets"]` gains `target_type`; `SCENARIO["jets"]`'s `"payload"` value changes from an `int` to a `dict[str, int]`. `SAM_Site_1` and `EW_Jammer_1` (v3 doc §2) are **not** added to `SCENARIO` in this phase — they belong to Phases 8 and 9, which will extend this same dict with new keys (`"sam_sites"`, `"jammers"`). Do not add empty placeholder keys for them now; that's noise this phase doesn't need.

## Part B — `wraithnet/schemas.py` changes

Update in place (do not create new files):

```python
from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator
from wraithnet.config import BROADCAST_ID, WeaponType, TargetType

class TargetInfo(BaseModel):
    target_id: str
    position: tuple[float, float]
    target_type: TargetType                    # NEW, required
    hp_required: int = Field(ge=1)
    hp_remaining: int = Field(ge=0)
    claimed: bool = False

    @model_validator(mode="after")
    def _check_hp_remaining_not_above_required(self) -> "TargetInfo":
        if self.hp_remaining > self.hp_required:
            raise ValueError("hp_remaining cannot exceed hp_required")
        return self


class Observation(BaseModel):
    agent_id: str
    tick: int = Field(ge=0)
    current_position: tuple[float, float]
    sensor_radius: float = Field(gt=0)          # NEW — populate with config.SENSOR_RADIUS; unused for filtering until Phase 7
    payload: dict[WeaponType, int]               # REPLACES payload_remaining: int
    known_targets: list[TargetInfo]
    comms_jammed: bool = False                   # NEW — always False until Phase 9

    @property
    def total_payload_remaining(self) -> int:
        return sum(self.payload.values())


class ActionIntent(BaseModel):
    agent_id: str
    tick: int = Field(ge=0)
    action_type: Literal["MOVE", "STRIKE", "IDLE"]
    target_position: Optional[tuple[float, float]] = None
    target_id: Optional[str] = None
    weapon_type: Optional[WeaponType] = None     # NEW — required when action_type == "STRIKE"

    @model_validator(mode="after")
    def _check_required_fields_for_action(self) -> "ActionIntent":
        if self.action_type == "MOVE" and self.target_position is None:
            raise ValueError("MOVE requires target_position")
        if self.action_type == "STRIKE":
            if self.target_id is None:
                raise ValueError("STRIKE requires target_id")
            if self.weapon_type is None:
                raise ValueError("STRIKE requires weapon_type")
        return self


class Message(BaseModel):
    message_id: str
    contract_id: str          # NOTE: stays required (non-Optional) in THIS phase.
    tick: int = Field(ge=0)   # Phase 7 makes it Optional to support DATALINK_CONTACT — do not
    sender_id: str            # change this yet; it is out of scope for Phase 6, which does not
    receiver_id: str          # add any new message_type.
    message_type: Literal["CFP", "BID", "ACCEPT", "REJECT"]
    target_id: str
    hp_remaining: Optional[int] = None
    payload_offered: Optional[int] = None
    weapon_type: Optional[WeaponType] = None   # NEW — required on BID (which weapon this count refers to)
    bid_utility: Optional[float] = None

    @model_validator(mode="after")
    def _check_required_fields_for_message_type(self) -> "Message":
        if self.message_type == "CFP":
            if self.hp_remaining is None:
                raise ValueError("CFP requires hp_remaining")
            if self.receiver_id != BROADCAST_ID:
                raise ValueError("CFP must be broadcast")
        if self.message_type == "BID":
            if self.payload_offered is None or self.bid_utility is None or self.weapon_type is None:
                raise ValueError("BID requires payload_offered, bid_utility, and weapon_type")
        if self.message_type in ("BID", "ACCEPT", "REJECT"):
            if self.receiver_id == BROADCAST_ID:
                raise ValueError(f"{self.message_type} must not be broadcast")
        return self
```

`Message.contract_id`'s change to `Optional[str]` and the new `DATALINK_CONTACT`/`target_info` fields are explicitly **out of scope for this phase** — Phase 7 owns that change, since `DATALINK_CONTACT` is a Feature 1 (Fog of War) concept. Adding `weapon_type: Optional[WeaponType] = None` to `Message` now (required only on `BID`) is in scope because bidding is a Feature 2 concern.

## Part C — `wraithnet/world.py` changes

1. `JetAgent.__init__` replaces `payload_remaining: int` with `payload: dict[str, int]` (a mutable copy of the scenario's loadout dict — e.g. `dict(payload)`, not a shared reference). Add a computed property:
   ```python
   @property
   def total_payload_remaining(self) -> int:
       return sum(self.payload.values())
   ```
2. `TargetState` gains a `target_type: str` field (populated from `config.SCENARIO`).
3. `WraithNetModel._spawn_scenario` reads `j["payload"]` as a dict and `t["target_type"]` when constructing `TargetState`/`JetAgent`.
4. `WraithNetModel._apply_strike(self, jet, target_id, weapon_type)` — **signature change**: now takes `weapon_type` (passed through from the `ActionIntent`). Logic:
   ```python
   def _apply_strike(self, jet: JetAgent, target_id: str | None, weapon_type: str | None) -> None:
       target = self.targets.get(target_id) if target_id else None
       if target is None or weapon_type is None or jet.payload.get(weapon_type, 0) <= 0:
           return
       if config.WEAPON_SUITABILITY.get((weapon_type, target.target_type), 0.0) <= 0.0:
           return  # physically cannot damage this target type with this weapon — no-op, not an error
       if math.dist(jet.pos, target.position) > config.STRIKE_RANGE:
           return
       jet.payload[weapon_type] -= 1
       target.hp_remaining = max(0, target.hp_remaining - 1)
       self.strike_log.append(
           {"tick": self.tick, "agent_id": jet.agent_id, "target_id": target.target_id, "weapon_type": weapon_type}
       )
   ```
   Note the **new suitability guard**: even if an `ActionIntent` somehow requests an unsuitable weapon/target pairing (which well-behaved agent logic should never produce, per Part D below — but the World must not trust agent input blindly), the physics layer refuses to apply damage. This is a second line of defense, not a substitute for the CNP-level hard filter in Part D.
5. `WraithNetModel.step(intents)` — the `STRIKE` branch now passes `intent.weapon_type` through: `self._apply_strike(jet, intent.target_id, intent.weapon_type)`.
6. **Compatibility fixes required by this phase's own schema change (easy to miss — these only fail at runtime, not at import time):** Phase 5 introduced two other places that read the old scalar `payload_remaining`, both of which must be updated now:
   * `WraithNetModel.__init__`'s `self._initial_total_payload = sum(j.payload_remaining for j in self.jets.values())` → `sum(j.total_payload_remaining for j in self.jets.values())`.
   * The `DataCollector`'s `agent_reporters={"PayloadRemaining": "payload_remaining", "DistanceTraveled": "distance_traveled"}` → 
     ```python
     agenttype_reporters={
         JetAgent: {
             "TotalPayloadRemaining": "total_payload_remaining",
             "DistanceTraveled": "distance_traveled",
         },
     },
     ```
     Use `agenttype_reporters` (keyed by `JetAgent`), **not** the flat `agent_reporters`, even though this phase doesn't yet add a second agent class. `agent_reporters` applies every reporter function to *every* agent in `model.agents` with no type filtering — Phase 8 adds `SamSiteAgent` as a second `mesa.Agent` subclass in this same model, and a flat `agent_reporters` would then raise `AttributeError` the first time `datacollector.collect()` runs (a `SamSiteAgent` has neither `total_payload_remaining` nor `distance_traveled`). Making this switch now avoids a breaking rework in Phase 8. Update `wraithnet/metrics.py::export_results` and any test that reads agent-level collected data to use `model.datacollector.get_agenttype_vars_dataframe(JetAgent)` instead of `get_agent_vars_dataframe()`.
   * `wraithnet/metrics.py::summarize()` has **two** more references to fix: `total_fired = model._initial_total_payload - sum(j.payload_remaining for j in model.jets.values())` → use `j.total_payload_remaining`; and the returned dict's `"per_jet_payload_remaining": {aid: j.payload_remaining for aid, j in model.jets.items()}` → rename the key to `"per_jet_total_payload_remaining"` and use `j.total_payload_remaining` (update any test/`main.py` code that reads the old key name accordingly).

   Verify `tests/test_scenario.py` (all of it — `summarize()` is used by nearly every test in that file) still passes and `agent_vars.csv` (written via `get_agenttype_vars_dataframe(JetAgent)`) contains a `TotalPayloadRemaining` column. An unfixed reference here raises an `AttributeError` the first time `datacollector.collect()`/`summarize()` actually runs, not at import time, so it's easy to miss without running the full suite end-to-end.

7. `WraithNetModel.build_observation(agent_id, broker)` — update to populate the new `Observation` fields without changing target-visibility behavior:
   ```python
   def build_observation(self, agent_id: str, broker: "MessageBroker") -> Observation:
       jet = self.jets[agent_id]
       known_targets = [
           TargetInfo(
               target_id=t.target_id, position=t.position, target_type=t.target_type,
               hp_required=t.hp_required, hp_remaining=t.hp_remaining,
               claimed=broker.is_claimed(t.target_id),
           )
           for t in self.targets.values()   # ALL live targets, unfiltered — Phase 7 adds sensor filtering here
       ]
       return Observation(
           agent_id=agent_id, tick=self.tick, current_position=jet.pos,
           sensor_radius=config.SENSOR_RADIUS, payload=dict(jet.payload),
           known_targets=known_targets, comms_jammed=False,
       )
   ```

## Part D — `wraithnet/agents.py` changes

1. **`compute_bid` signature and body change** — this is the core of the utility-function fix (v3 doc §6 / findings I2, I3):
   ```python
   def compute_bid(
       observation: Observation, target: TargetInfo, hp_needed: int | None = None,
   ) -> tuple[float, int, str] | None:
       """Returns (bid_utility, payload_offered, weapon_type), or None if this
       jet has no weapon suitable for target.target_type (hard disqualification —
       do not fold this into the utility score, see docs/03_..., finding I3)."""
       ask = target.hp_remaining if hp_needed is None else hp_needed
       suitable = [
           (wt, count) for wt, count in observation.payload.items()
           if count > 0 and config.WEAPON_SUITABILITY.get((wt, target.target_type), 0.0) > 0.0
       ]
       if not suitable:
           return None
       # Prefer the suitable weapon this jet holds the most of; tie-break alphabetically for determinism.
       weapon_type, count = max(suitable, key=lambda pair: (pair[1], pair[0]))
       suitability = config.WEAPON_SUITABILITY[(weapon_type, target.target_type)]
       payload_score = min(1.0, count / ask)
       distance = math.dist(observation.current_position, target.position)
       distance_score = distance / config.MAX_GRID_DISTANCE
       utility = (
           config.UTILITY_W_WEAPON * suitability
           + config.UTILITY_W1 * payload_score
           - config.UTILITY_W2 * distance_score
       )
       payload_offered = min(count, ask)
       return utility, payload_offered, weapon_type
   ```
   This is a **breaking signature change** (2-tuple → 3-tuple-or-`None`). Every call site must be updated to unpack 3 values and to handle `None` by not bidding, mirroring the existing `payload_remaining <= 0` early-return pattern.

2. Update every call site (`_maybe_bid`, the self-bid block in `_broadcast_cfp_and_collect`) to:
   * Call `compute_bid(...)`.
   * If it returns `None`, do not bid (same as the existing `payload_remaining <= 0` guard — just another reason to skip).
   * Otherwise unpack `utility, payload_offered, weapon_type` and include `weapon_type=weapon_type` in the constructed `Message(message_type="BID", ...)`.
   * Replace every remaining reference to `observation.payload_remaining` with `observation.total_payload_remaining` (the "do I have anything at all" checks in `_maybe_bid`/`_broadcast_cfp_and_collect` care about *any* payload, not a specific weapon — the weapon-specific check happens inside `compute_bid`).

3. **`_resolve_contract`'s `_commit` call** — a winning bid now carries a `weapon_type`; `_commit(target_id, payload_offered, weapon_type)` must store it (`self.committed_weapon_type: str | None = None`, initialized in `__init__` alongside `committed_target_id`/`committed_payload`).

4. **`decide_intent`** — when emitting a `STRIKE`, include `weapon_type=self.committed_weapon_type`:
   ```python
   if distance <= config.STRIKE_RANGE:
       self.committed_payload -= 1
       return ActionIntent(
           agent_id=self.agent_id, tick=obs.tick, action_type="STRIKE",
           target_id=target.target_id, weapon_type=self.committed_weapon_type,
       )
   ```

5. **`scan_and_negotiate`** — no signature change, but note it will now correctly skip a target for which this agent holds no suitable weapon at all (via `try_become_manager` → `_broadcast_cfp_and_collect`'s self-bid returning `None`) — an agent can still *become Manager* for a target it can't personally strike (e.g., coordinating others), as long as it can still *become Manager* at all. Confirm this by re-reading `try_become_manager`: it does not call `compute_bid` itself, only `_broadcast_cfp_and_collect` does (for the self-bid) — so this requires no change to `try_become_manager`, only to the self-bid block already covered in point 2.

## Part E — Tests

### `tests/test_schemas.py` (additions/updates)

* `TargetInfo` requires `target_type`; construct one of each of the three `TargetType` literals successfully; an invalid string raises `ValidationError`.
* `Observation.payload` accepts a `dict[str, int]`; `total_payload_remaining` sums correctly (e.g. `{"JDAM": 2, "AIM9X": 3}` → `5`).
* `ActionIntent(action_type="STRIKE", target_id=..., weapon_type=None)` raises `ValidationError` (weapon_type now required for STRIKE); providing `weapon_type="JDAM"` succeeds.
* `Message(message_type="BID", ...)` without `weapon_type` raises `ValidationError`; with it, succeeds.

### `tests/test_world.py` (updates)

* Update every existing test that constructed a jet/target/observation/intent to use the new dict-based payload and `target_type`/`weapon_type` fields (e.g. `model.jets["Jet_1"].payload == {"JDAM": 1, "AIM9X": 0}` instead of `payload_remaining == 1`).
* New: `_apply_strike` with a **mismatched** weapon/target-type pair (e.g. `AIM9X` against `Target_A`, a `HARDENED_BUNKER`) while in range and with ammo available is a no-op: `target.hp_remaining` unchanged, `jet.payload[weapon_type]` unchanged, `strike_log` unchanged. This is the World-layer suitability guard from Part C.4 — test it directly, independent of whether `agents.py` would ever produce such an intent.
* New: `_apply_strike` with a suitable pairing (`JDAM` vs. `HARDENED_BUNKER`) in range decrements `jet.payload["JDAM"]` and `target.hp_remaining`, and logs `weapon_type` in the `strike_log` entry.
* Update `build_observation` tests: `obs.sensor_radius == config.SENSOR_RADIUS`, `obs.comms_jammed is False`, `obs.payload == dict(jet.payload)` at that point in time, and — importantly — confirm `known_targets` still contains **every** live target regardless of distance (sensor filtering is explicitly not yet implemented; assert this is still true so Phase 7 has a clear "before" baseline to change).
* New: after `model.step({})` a couple of times, `model.datacollector.collect(model)` does not raise, and `model.datacollector.get_agent_vars_dataframe()` has a `TotalPayloadRemaining` column (regression test for the Part C.6 `DataCollector` fix).

### `tests/test_cnp.py` (updates)

* Update `compute_bid` unit tests for the new 3-tuple-or-`None` return: a jet with a suitable weapon and full payload at zero distance scores `W_WEAPON*1.0 + W1*1.0 - 0`; increasing distance strictly decreases utility, holding everything else constant; a jet whose only weapon is unsuitable for the target's type gets `None` back (test this explicitly — e.g. an `AIM9X`-only jet bidding on a `HARDENED_BUNKER` target).
* New: a jet with **both** weapon types, where only one is suitable for the target, must select the suitable one and its bid's `payload_offered`/`weapon_type` must reflect that weapon specifically (not accidentally use the unsuitable one's count).
* Update the multi-winner partial-fulfillment test (`test_multi_winner_partial_fulfillment_for_target_a`) to use `target_type="HARDENED_BUNKER"` and jets bidding with `JDAM` counts — should otherwise still hold the same invariants (≥2 contractors, sum of committed `JDAM` payload ≥ `hp_required`).
* Update the tie-break test similarly if it constructs `TargetInfo`/`Observation` objects directly (add `target_type` and dict `payload`).

### `tests/test_scenario.py` / `tests/test_bridge.py` (updates)

Update all `config.SCENARIO`-derived assertions for the new dict-based jet payloads and the `target_type` field. The **numeric invariants themselves are unchanged from V1** (`Target_A` needs ≥2 `JDAM`-carrying contributors since `hp_required=5 > 4` = max single-jet `JDAM` count; `Target_B` is solo-able) — see `docs/04_..._v3_Phase2.md` §2 for why. Update assertions to check `sum(strike['weapon_type'] == 'JDAM' for strike in ... if strike['target_id']=='Target_A')`-style conditions instead of raw counts where weapon type matters.

## Explicit non-goals for this phase

* No sensor-radius-based filtering of `known_targets` (Phase 7).
* No `DATALINK_CONTACT` message type, no `world_view` memory (Phase 7).
* No SAM sites, no jet health/destruction, no `SAM_SITE` targets actually spawned (Phase 8) — the `TargetType` literal includes `"SAM_SITE"` now purely so the schema doesn't need another breaking change later.
* No jamming, no `EwJammerAgent`, no `Message.contract_id` becoming `Optional` (Phase 9).
* No change to `MAX_CFP_ROUNDS`, `BID_TIMEOUT_SECONDS`, tie-break rules, or manager-election logic — those are frozen from Phase 3/the prerequisite bugfix. If a test fails, first suspect a wiring bug in this phase's weapon-typing changes before touching negotiation-timing logic.

## Acceptance criteria

1. `pytest` passes, including all updated/new tests, with no reference to `payload_remaining` (scalar) remaining anywhere in `wraithnet/` or `tests/` — a `grep -r payload_remaining` should return zero matches (it is fully replaced by `payload: dict[...]` / `total_payload_remaining`).
2. `python -m wraithnet.main` still runs the (weapon-typed) scenario to completion, both `Target_A` and `Target_B` destroyed, using the new dict-based payload accounting.
3. `compute_bid` never returns a bid for a weapon/target-type pairing with `WEAPON_SUITABILITY <= 0.0` — verified by a direct unit test, not just by the absence of a failure in the full scenario.
4. `_apply_strike` never decrements `hp_remaining` or `jet.payload[weapon_type]` for a physically-unsuitable pairing, even if called directly with such an `ActionIntent` (defense-in-depth, tested independently of `agents.py`'s own filtering).
