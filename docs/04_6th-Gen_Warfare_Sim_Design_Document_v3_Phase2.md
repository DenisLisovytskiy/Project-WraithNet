# Software Design Document (v3): 6th-Gen Warfare Multi-Agent Simulator — "Phase 2 Expansion"

> This is a corrected, concrete revision of the "Software Design Document: Phase 2 Expansion (Advanced Tactical Mechanics)" proposal. Every change made here is explained in `docs/03_Phase2_Expansion_Design_Review_Findings.md`. This document **extends** `docs/6th-Gen_Warfare_Sim_Design_Document_v2.md` (the V1 PoC) — everything in v2 not explicitly overridden below still applies. This is the source of truth for implementation; see `docs/phases/Phase_6..10_*.md` for the phase-by-phase build plan.
>
> **Prerequisite (already satisfied):** the V1 CNP multi-round re-negotiation bug documented in `docs/02_Code_Review_Phases_3-5.md` §1–§2 is fixed in the current `wraithnet/agents.py`. Features 3 and 4 below depend on that fix; do not build against a checkout that predates it.

## 1. Goal (unchanged from the source proposal, restated precisely)

Upgrade the V1 PoC from a static, perfect-information, single-weapon-type optimization problem into a dynamic, incomplete-information tactical simulation: agents only sense what's nearby (Fog of War), carry mixed, target-suitable-or-not ordnance (Heterogeneous Weaponry), face reactive threats that can destroy them (SAM sites), and must operate through unreliable comms (Electronic Warfare / Jamming) — all four mechanics reusing the existing Contract Net Protocol machinery wherever possible, rather than inventing parallel systems.

## 2. The V2 Scenario (extends V1 §2)

The V1 grid, targets, and jets are kept as the geometric backbone; this expansion adds weapon typing, one SAM site, one jammer, and a shared sensor radius. **Numbers below are deliberately chosen and must not be "simplified"** — see the rationale after each group, in the same spirit as v2 doc's `Target_A.hp_required` rationale.

* **Grid:** unchanged — continuous, non-toroidal, `100.0 × 100.0`, Euclidean distance.
* **`WeaponType = Literal["JDAM", "AIM9X"]`**, **`TargetType = Literal["HARDENED_BUNKER", "MOBILE_RADAR", "SAM_SITE"]`**.
* **`WEAPON_SUITABILITY: dict[tuple[WeaponType, TargetType], float]`** (binary for this PoC):

  | | `HARDENED_BUNKER` | `MOBILE_RADAR` | `SAM_SITE` |
  |---|---|---|---|
  | `JDAM` | `1.0` | `0.0` | `1.0` |
  | `AIM9X` | `0.0` | `1.0` | `0.0` |

* **Targets:**
  * `Target_A`: position `(75.0, 75.0)`, `target_type="HARDENED_BUNKER"`, `hp_required=5` (needs `JDAM` only).
  * `Target_B`: position `(25.0, 25.0)`, `target_type="MOBILE_RADAR"`, `hp_required=1` (needs `AIM9X` only).
  * `SAM_Site_1` (see §5 — also a normal CNP target once discovered): position `(40.0, 85.0)`, `target_type="SAM_SITE"`, `hp_required=1` (needs `JDAM` only).
* **Jets** (`JetAgent.hp = JET_HP = 5` for all three, new):
  * `Jet_1`: position `(70.0, 70.0)`. Loadout: `{"JDAM": 1, "AIM9X": 0}`.
  * `Jet_2`: position `(5.0, 95.0)`. Loadout: `{"JDAM": 4, "AIM9X": 0}`.
  * `Jet_3`: position `(95.0, 5.0)`. Loadout: `{"JDAM": 3, "AIM9X": 2}`.
* **`EW_Jammer_1`** (not a CNP target — a pure Mesa hazard entity, see §7): position `(46.0, 19.0)`, `JAM_RADIUS = 12.0`.
* **`SENSOR_RADIUS = 65.0`** (uniform across all jets — per-jet heterogeneous sensors are an explicit non-goal for this expansion).
* **`SAM_ENGAGEMENT_RANGE = 15.0`**, **`SAM_DAMAGE_PER_TICK = 1`** (deterministic, no RNG — consistent with V1's fully-reproducible-at-t=0 requirement).

**Why these exact numbers matter:**

* **Forced cooperation on `Target_A` is preserved from V1, scoped to `JDAM` specifically:** `Target_A.hp_required = 5` still exceeds the maximum *single-jet* `JDAM` count (`Jet_2`'s `4`), so — exactly as in the V1 F1 finding — no single jet can solo it regardless of utility tuning; at least two `JDAM`-carrying jets must contribute. Total fleet `JDAM` supply is `1 + 4 + 3 = 8` against total `JDAM` demand of `5` (`Target_A`) `+ 1` (`SAM_Site_1`) `= 6`, leaving deliberate slack (`2`, ~33%) so infeasibility is never the failure mode under test, matching the V1 §2 rationale.
* **`Target_B` stays solo-able by design:** `Jet_3` is the *only* `AIM9X` carrier (`2`, against a demand of `1`), so `Target_B` is destroyed by exactly one jet, same invariant as V1's `test_target_b_does_not_require_unnecessary_cooperation`.
* **`SENSOR_RADIUS = 65.0` is chosen so exactly one jet has any initial visibility, forcing genuine reliance on `DATALINK_CONTACT`:** `Jet_1` is `≈7.07` from `Target_A` and `≈63.64` from `Target_B` — both within `65.0`, so `Jet_1` alone can see *both* targets at tick 0. `Jet_2` and `Jet_3` are each `≈72.80` from both targets — outside `65.0` — so at tick 0 neither can see anything and must rely entirely on `Jet_1`'s `DATALINK_CONTACT` broadcasts to even learn `Target_A`/`Target_B` exist. This makes Feature 1 load-bearing for the fixed scenario, not just a decorative addition: without correct contact-sharing, `Jet_2` and `Jet_3` can never contribute to either target.
* **`SAM_Site_1` at `(40.0, 85.0)` sits directly on the straight-line path from `Jet_2` `(5.0, 95.0)` to `Target_A` `(75.0, 75.0)`** (perpendicular distance from the site to that segment is `0.0` — it is placed *on* the line, `≈36.4` units from `Jet_2`'s start, well inside `Jet_2`'s own initial `SENSOR_RADIUS` footprint, so `Jet_2` discovers it immediately at tick 0). Any jet — most plausibly `Jet_2`, given the geometry — that flies toward `Target_A` along this line passes through the `15.0`-unit engagement ring for several ticks before reaching `STRIKE_RANGE`. This is intentional: it guarantees the SAM is *actually encountered* by a real run rather than sitting in an unvisited corner, without pathfinding/avoidance AI (an explicit non-goal — see §7).
* **`EW_Jammer_1` at `(46.0, 19.0)` sits directly on the straight-line path from `Jet_3` `(95.0, 5.0)` to `Target_B` `(25.0, 25.0)`**, similarly guaranteeing `Jet_3` — the sole `AIM9X` carrier needed to finish `Target_B` — passes through the jamming radius en route, exercising Feature 4 on the one flight that must happen for the scenario to resolve.
* **Deliberately not hand-derived here: exact tick-by-tick combat/jamming outcomes (e.g., "does `Jet_2` survive the SAM").** Given four interacting mechanics, precisely hand-computing every emergent outcome (as V1 §2 did for the much simpler two-target, no-hazards case) would be error-prone and is not attempted. Instead: (a) each mechanic (SAM damage/destruction, jamming/re-negotiation interaction, contact sharing, weapon suitability) is unit-tested in isolation with hand-constructed inputs that *force* the exact condition under test (Phases 6–9), exactly as V1 Phase 3 tested CNP logic without relying on the full scenario to organically produce every code path; and (b) the full end-to-end scenario (Phase 10) asserts robustness invariants that hold *regardless* of exactly which jet takes SAM damage or gets jammed (e.g., "the run terminates," "all three CNP targets end destroyed OR the swarm-wiped-out failure flag is set and is consistent with `strike_log`/`hp` state," "no agent is left in a non-`IDLE`/non-`CONTRACTOR` zombie state"), not a single hard-coded outcome.

## 3. Updated System Architecture

Extends `docs/6th-Gen_Warfare_Sim_Design_Document_v2.md` §3. New/changed components only:

1. **Mesa Model (The World)** — gains two new plain `mesa.Agent` types, stepped synchronously inside `WraithNetModel.step()` exactly like `JetAgent`, **never** given a `JetAgentRuntime` or registered with the `MessageBroker`:
   * `SamSiteAgent` (also tracked as a normal CNP `TargetInfo` with `target_type="SAM_SITE"` — see §5).
   * `EwJammerAgent` (pure hazard; never a CNP target).
2. **The Bridge** — gains two new per-tick responsibilities, both inserted **before** the existing negotiation phase (see `docs/6th-Gen_Warfare_Sim_Design_Document_v2.md` §6 for the unchanged base steps):
   * Compute each jet's `comms_jammed: bool` (distance to `EwJammerAgent` `<= JAM_RADIUS`) and call `broker.set_jammed(agent_id, comms_jammed)`.
   * Check every open contract's contractors against current `model.jets`/`hp`; for any contractor that no longer exists or was hit this tick, call `manager_agent.notify_contractor_lost(contractor_id)` (Feature 3 — see §7).
3. **Message Broker** — gains jamming-aware routing (`set_jammed`/`is_jammed`, consulted in `_route`) and one new message type (`DATALINK_CONTACT`). No new coupling to Mesa/World objects — the Bridge remains the only component that touches both sides (see finding G3).
4. **Custom Agents (The Brains)** — `JetAgentRuntime` gains: a persistent `world_view` memory (Feature 1), weapon-suitability-aware bidding (Feature 2), and a `notify_contractor_lost` handler that re-triggers the *existing* re-negotiation machinery (Feature 3) — no parallel negotiation system.

No new "Squad Leader" role, no WebSocket/network transport, no separate SAM/jammer negotiation protocol — see findings G1/G2/G3/G5.

## 4. Updated Data Schemas

Extends `wraithnet/schemas.py`. Only new/changed fields are shown; everything else from V1 is unchanged.

```python
from typing import Literal, Optional
from pydantic import BaseModel, Field, model_validator

WeaponType = Literal["JDAM", "AIM9X"]
TargetType = Literal["HARDENED_BUNKER", "MOBILE_RADAR", "SAM_SITE"]

class TargetInfo(BaseModel):
    target_id: str
    position: tuple[float, float]
    target_type: TargetType          # NEW
    hp_required: int = Field(ge=1)
    hp_remaining: int = Field(ge=0)
    claimed: bool = False
    # ... existing hp_remaining <= hp_required validator unchanged ...

class Observation(BaseModel):
    agent_id: str
    tick: int = Field(ge=0)                      # kept (source proposal dropped this — see H1)
    current_position: tuple[float, float]
    sensor_radius: float = Field(gt=0)            # NEW
    payload: dict[WeaponType, int]                # REPLACES payload_remaining: int (see H2)
    known_targets: list[TargetInfo]               # kept typed (source proposal reverted to list[dict] — see H1)
    comms_jammed: bool = False                    # NEW — informational; does not affect Observation delivery itself (see G4)

    @property
    def total_payload_remaining(self) -> int:
        return sum(self.payload.values())

class ActionIntent(BaseModel):
    agent_id: str
    tick: int = Field(ge=0)
    action_type: Literal["MOVE", "STRIKE", "IDLE"]   # no new action types — see J5
    target_position: Optional[tuple[float, float]] = None
    target_id: Optional[str] = None
    weapon_type: Optional[WeaponType] = None          # NEW — required when action_type == "STRIKE"

class Message(BaseModel):
    message_id: str
    contract_id: Optional[str] = None   # was required str; now optional — see H4
    tick: int = Field(ge=0)
    sender_id: str
    receiver_id: str
    message_type: Literal["CFP", "BID", "ACCEPT", "REJECT", "DATALINK_CONTACT"]  # NEW type
    target_id: str
    hp_remaining: Optional[int] = None
    payload_offered: Optional[int] = None
    weapon_type: Optional[WeaponType] = None      # NEW — required on BID/ACCEPT (which weapon this count refers to)
    bid_utility: Optional[float] = None
    target_info: Optional[TargetInfo] = None      # NEW — replaces untyped target_data: dict (see H4); required on DATALINK_CONTACT

    @model_validator(mode="after")
    def _check_required_fields_for_message_type(self) -> "Message":
        if self.message_type == "DATALINK_CONTACT":
            if self.target_info is None:
                raise ValueError("DATALINK_CONTACT requires target_info")
            if self.contract_id is not None:
                raise ValueError("DATALINK_CONTACT must not carry a contract_id")
        else:
            if self.contract_id is None:
                raise ValueError(f"{self.message_type} requires contract_id")
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

## 5. Feature 1 — Sensor Fusion & Fog of War

* `WraithNetModel.build_observation(agent_id, broker)` only includes a target in `Observation.known_targets` if `distance(jet.pos, target.position) <= SENSOR_RADIUS` — this field stays **direct-sensor-only, ground truth** (the World has no knowledge of any agent's `world_view`). The merge with remembered/relayed contacts happens one layer up, in `JetAgentRuntime`: `refresh_world_view()` folds each tick's direct `Observation.known_targets` into `self.world_view` (keyed by `target_id`, value = `(TargetInfo, last_updated_tick)`), and `effective_known_targets()` returns that memory's values — the "union of currently sensed and remembered" — for use by `scan_and_negotiate`/bidding/`MOVE` decisions. Keeping the merge out of `Observation` preserves it as a pure sensor snapshot and keeps memory/staleness bookkeeping entirely agent-side, where `DATALINK_CONTACT_TTL_TICKS` expiry is also applied.
* **Staleness/trust rule (finding H3):** each `JetAgentRuntime` keeps `world_view: dict[target_id, tuple[TargetInfo, int]]` (value = last-updated tick). A world-view entry may be used to **bid** or **move toward**, but an agent may only emit `STRIKE` for a target it is *currently* sensing directly this tick (`distance <= sensor_radius` computed fresh from the live `Observation`, never from memory). Entries older than `DATALINK_CONTACT_TTL_TICKS = 15` ticks are dropped from `world_view` and no longer offered as bid candidates.
* **Contact broadcast rule (finding H5):** an agent broadcasts `DATALINK_CONTACT` for a target only when its own direct-sensor reading of that target is new (not yet in its `world_view`) or has changed (`hp_remaining` or `claimed` differs from the stored value) — never on every tick it merely continues to see something unchanged.
* **No-known-targets behavior (finding H6):** an `IDLE` agent whose merged `known_targets` is empty moves toward its own scenario-start position (i.e., holds station) rather than idling with no `ActionIntent` at all — full patrol *patterns* are an explicit non-goal for this expansion.

## 6. Feature 2 — Heterogeneous Weaponry

* **Suitability is a hard precondition on bidding, not a soft score component (finding I3):** before computing any utility, `_maybe_bid`/the self-bid path must find at least one `weapon_type` in `observation.payload` with `payload[weapon_type] > 0` and `WEAPON_SUITABILITY[(weapon_type, target.target_type)] > 0`. If none exists, the agent does not bid on that target at all, full stop — this is a filter, not a score of `0`.
* **Updated utility function (finding I2 — replaces the source proposal's un-normalized, `P_total_remaining`-inflated formula):**
  ```python
  payload_score = min(1.0, payload[best_weapon] / hp_needed)
  distance_score = distance(position, target.position) / MAX_GRID_DISTANCE
  U = (W_WEAPON * suitability[best_weapon, target.target_type]) + (W1 * payload_score) - (W2 * distance_score)
  ```
  `best_weapon` = the suitable weapon type this jet has the most of. `W_WEAPON = 10.0` (dominates the ≤`W1 + W2` = ≤`1.5` combined range of the other two terms — kept as defense-in-depth alongside the hard filter above, not as the filter mechanism itself), `W1 = 1.0`, `W2 = 0.5` (unchanged from V1).
* **Damage model stays uniform (finding I4):** every weapon type does exactly `1` HP of damage per successful `STRIKE`, same as V1 — heterogeneity governs *whether* a weapon may be used on a target, never *how much* damage a hit does. `payload_offered`/`ActionIntent.weapon_type` track *which* weapon-type count is being committed/fired, so `world.py::_apply_strike` decrements `jet.payload[weapon_type]` (not a scalar) and still decrements `target.hp_remaining` by exactly `1` per strike, unchanged.

## 7. Feature 3 — Dynamic Threats (SAM Sites)

* `SamSiteAgent` is a plain, synchronous Mesa agent (never a `JetAgentRuntime`, never registered with the `MessageBroker` — finding G5). Each tick, `WraithNetModel.step()` applies `SAM_DAMAGE_PER_TICK` to every jet within `SAM_ENGAGEMENT_RANGE` of a live (`hp_remaining > 0`) SAM site.
* **Jet health/destruction (finding J1):** `JetAgent` gains `hp: int = JET_HP` and `is_destroyed` (`hp <= 0`). On destruction: removed from `space`, excluded from all future `Observation`/negotiation/`decide_intent` calls, and the Bridge cancels its `JetAgentRuntime` task (`agent.stop()`) and removes it from the active agent set for the rest of the run.
* **SAM sites are ordinary CNP targets (finding J3):** `SamSiteAgent` has a corresponding `TargetState`/`TargetInfo` with `target_type="SAM_SITE"`, discoverable via sensors/`DATALINK_CONTACT` exactly like `Target_A`/`Target_B`, and destroyed via the same multi-winner CNP flow (subject to `WEAPON_SUITABILITY` — only `JDAM` works on a `SAM_SITE`, §2). No separate "suppression" mechanic is introduced.
* **Manager Fallback re-tasking (finding J4) — reuses existing re-negotiation, no new protocol:** each tick, before the negotiation phase, the Bridge scans every open contract's accepted contractors; if one is missing from `model.jets` (destroyed) or its jet's `hp` decreased this tick, the Bridge calls `manager_agent.notify_contractor_lost(contractor_id, target_id)`. That method computes the contract's true remaining shortfall (`hp_needed = target.hp_remaining - sum(payload still credited to surviving contractors)`) and, if the Manager is still alive and still holds `managed_target_id == target_id`, calls the *existing* `_broadcast_cfp_and_collect(hp_needed)` — the identical code path already used for an underfilled first round. There is no "Squad Leader" and no new negotiation state machine (finding G2).
* **Explicit non-goals (finding J5, and pathfinding avoidance from §2's rationale):** no `EVADE`/evasive-maneuver `ActionIntent`, no automatic SAM-avoidance pathfinding — jets fly the same straight-line `MOVE` behavior as V1 and simply accept SAM risk en route; the only mitigation available is destroying/suppressing the SAM via CNP.

## 8. Feature 4 — Electronic Warfare (Comms Jamming)

* No WebSocket, no new transport (finding G1). Jamming is purely a `MessageBroker` routing rule.
* `EwJammerAgent` is a plain, static, synchronous Mesa agent, never a CNP target, never targetable/destroyable in this expansion (finding K2 — mobile/multiple/destroyable jammers are an explicit non-goal).
* **Mechanism (finding K1):** `MessageBroker.set_jammed(agent_id, bool)` / `is_jammed(agent_id)`. `_route()` drops (does not enqueue) any message where the sender or the intended recipient is currently jammed — broadcasts are filtered per-recipient (a jammed agent doesn't receive broadcasts; a jammed sender's broadcast reaches no one), direct messages are dropped outright if either side is jammed. `WraithNetModel.build_observation` computes `comms_jammed` (`distance(jet.pos, jammer.position) <= JAM_RADIUS`, exactly analogous to how it already owns the SAM-engagement check) and the Bridge simply relays that already-computed value to `broker.set_jammed(agent_id, obs.comms_jammed)` once per tick, before the negotiation phase — this avoids duplicating distance math in two places while keeping the Broker and World mutually unaware of each other (the Bridge remains the sole connector, per G3).
* **Jamming affects `Message` routing only, never `Observation` delivery (finding G4):** `Bridge._tick()` still calls `agent.set_observation(...)` directly (bypassing the Broker entirely, as in V1) for every jet every tick regardless of jamming — a jammed jet retains full local sensor/GPS awareness (subject to its own `sensor_radius`) and can still move/strike based on it; only inter-agent coordination (CFP/BID/ACCEPT/REJECT/DATALINK_CONTACT) is blocked while jammed. `Observation.comms_jammed` is populated purely for the agent's own informational/logging use (e.g., to justify why it isn't currently negotiating) — it does not gate anything on the `Observation` side.
* **In-flight negotiation state under jamming (finding K3) — reuses existing timeout/give-up paths, no new Manager-side state needed:** a jammed Manager's CFPs never leave the mailbox and jammed-out BIDs never arrive, so its rounds simply collect nothing (beyond its own locally-computed self-bid, which never touches the Broker at all). It therefore either gets cut off by the existing per-tick `MAX_TICK_WAIT_SECONDS` cancellation (triggering the already-implemented `_abort_open_management`) or naturally exhausts `MAX_CFP_ROUNDS` and gives up on its own (already-implemented give-up path in `_resolve_contract`) — both already release the claim so a different, unjammed agent can pick it up on a later tick. No jamming-specific branching is added to `_broadcast_cfp_and_collect`/`_resolve_contract`.
* **A jamming-specific zombie risk that *does* need a small, new backstop:** a bidder in `CONTRACTOR_PENDING` is, in the base protocol, waiting indefinitely for an `ACCEPT`/`REJECT` that (pre-jamming) was always reliably delivered. Jamming is the first mechanism that can make that message *never arrive despite both agents being registered* — if the bidder becomes jammed (or the Manager does) between sending its `BID` and receiving a decision, it would otherwise be stuck in `CONTRACTOR_PENDING` forever, the same zombie shape as the original CNP bug in `docs/02_Code_Review_Phases_3-5.md`, just triggered a different way. *Fix:* a new `CONTRACTOR_PENDING_TTL_TICKS` (`10`, matching `CONTRACT_CLAIM_TTL_TICKS`'s existing value) — an agent records the tick it entered `CONTRACTOR_PENDING` and reverts itself to `IDLE` if that many ticks pass without an `ACCEPT`/`REJECT`, mirroring the existing stale-contract-TTL pattern rather than inventing a new kind of mechanism.

## 9. Termination & Metrics Updates

* **Termination (finding L2):** `WraithNetModel.step()` now also sets `self.running = False` if every jet is destroyed (`all(j.is_destroyed for j in self.jets.values())`) — a distinct **failure** termination from the existing **success** termination (`all(t.is_destroyed for t in self.targets.values())`, now covering `Target_A`, `Target_B`, and `SAM_Site_1` together).
* **Metrics (finding L3):** `DataCollector` model reporters extended with `JetsDestroyed`, `SamSitesDestroyed`, `TotalJammedAgentTicks` (sum, across jets and ticks, of ticks spent with `comms_jammed=True`). `metrics.summarize()` extended with `per_weapon_type_missiles_fired: dict[WeaponType, int]` and `swarm_wiped_out: bool`.

## 10. Implementation Phases for the LLM

Build strictly in this order (finding L4 — Feature 2's schema changes must land before Feature 1's sensor filtering is layered on top; both must land before the World gains any new hazard entities):

* **Phase 6 — Weapon System & Schema Foundations (Feature 2):** `docs/phases/Phase_6_Weapon_System_and_Schema_Foundations.md`
* **Phase 7 — Sensor Fusion & Fog of War (Feature 1):** `docs/phases/Phase_7_Sensor_Fusion_and_Fog_of_War.md`
* **Phase 8 — Dynamic Threats: SAM Sites (Feature 3):** `docs/phases/Phase_8_Dynamic_Threats_SAM_Sites.md`
* **Phase 9 — Electronic Warfare: Comms Jamming (Feature 4):** `docs/phases/Phase_9_Electronic_Warfare_Jamming.md`
* **Phase 10 — Integration, Metrics & V2 Scenario Validation:** `docs/phases/Phase_10_Integration_Metrics_Validation.md`

See `docs/03_Phase2_Expansion_Design_Review_Findings.md` for the full rationale behind every change from the source proposal.
