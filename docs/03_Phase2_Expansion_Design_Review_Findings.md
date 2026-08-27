# Design Review: Findings & Fixes — "Phase 2 Expansion" (Advanced Tactical Mechanics)

Source reviewed: the "Software Design Document: Phase 2 Expansion (Advanced Tactical Mechanics)" proposal (Fog of War, Heterogeneous Weaponry, Dynamic Threats/SAM sites, Electronic Warfare/Jamming), evaluated against the **as-built V1 system** (`wraithnet/schemas.py`, `world.py`, `broker.py`, `agents.py`, `bridge.py`, `metrics.py` and `docs/6th-Gen_Warfare_Sim_Design_Document_v2.md`), not just against itself.

This document lists every inconsistency, ambiguity, bad practice, and integration hazard found, and states how each is resolved in the corrected design (`docs/04_6th-Gen_Warfare_Sim_Design_Document_v3_Phase2.md`) and the new phase docs (`docs/phases/Phase_6..10_*.md`).

**Naming note:** to avoid confusion with the existing "Phase 0–5" *implementation* phases, this document calls the four proposed features "**V2 Features 1–4**" (matching the source doc's own numbering) and reserves "**Phase 6–10**" for the new *implementation* phases that build them. "Phase 2 Expansion" (the feature-set's own name) is unrelated to "Phase 2" of the V1 build plan (Message Broker & Agent Loop) — this is confusing and is flagged as G1 below.

**Prerequisite already resolved:** two of the four new features (V2 Feature 3's "swarm renegotiation" and Feature 4's "jamming forces a contract to be dropped and later resumed") both depend on the V1 Contract Net Protocol's multi-round re-negotiation path working correctly. At the time this review started, that path had a confirmed, reproducible bug (see `docs/02_Code_Review_Phases_3-5.md` §1–§2 — a manager's own self-commit silently broke bid collection in later rounds, permanently stranding a contractor in `CONTRACTOR_PENDING`). **That bug is now fixed** in `wraithnet/agents.py` (verified: `handle_message`'s BID-collection guard now keys off `managed_target_id`, not `role`; `_resolve_contract` threads `hp_needed` explicitly instead of re-reading stale state; `_abort_open_management` cleanly releases a claim if a tick-level timeout cancels a manager mid-round) and covered by regression tests. This review assumes that fix is in place — **do not begin V2 Feature 3 or 4 on a checkout where it isn't.**

---

## G. Architectural inconsistencies

**G1. "WebSocket" appears out of nowhere and contradicts the entire existing transport design.**
V2 Feature 4 says a jammed jet "cannot send or receive messages on the **WebSocket**/Asyncio bus." There is no WebSocket anywhere in this project — `docs/6th-Gen_Warfare_Sim_Design_Document_v2.md` §3 explicitly rules out networked transports ("no ZeroMQ/WebSockets — those are explicitly out of scope for this PoC") in favor of a single in-process `asyncio.Queue`-based `MessageBroker`. Left as-is, an implementing LLM could reasonably conclude a real network layer is now in scope and start adding one.
*Fix:* Removed. Jamming is defined purely as a `MessageBroker`-level routing rule (see K1) — no networking of any kind is introduced by this expansion.

**G2. "Squad Leader" (Feature 3) is introduced without reconciling it with the existing, per-contract "Manager" role.**
V1's CNP has no persistent leadership concept — "Manager" is an ad-hoc, per-target, per-contract role that any idle agent can win by being first to claim a target (§5.1 of the v2 design doc). Feature 3 talks about "the Squad Leader (Manager)" detecting a dropped wingman and re-tasking the swarm, implying a single, persistent, swarm-wide leader — a fundamentally different concept from "whoever happens to be managing a given target's contract right now." As written, it's ambiguous whether "Squad Leader" is (a) a new, separate, persistent role, or (b) just loose language for "whichever agent happens to be the Manager of the contract that broke."
*Fix:* No new persistent "Squad Leader" role is introduced. Fallback re-tasking (K-numbering avoided; see J-series below) is scoped to exactly the existing Manager role: **whichever agent is Manager of a contract is responsible for noticing that contract's own contractors have gone silent or been destroyed, and re-opening bidding for the shortfall** — using the *same* re-negotiation machinery already fixed for the base game (`_broadcast_cfp_and_collect` / `_resolve_contract`), not a new mechanism. "Squad Leader" is retired as a term.

**G3. Jamming requires coupling the Broker (comms layer) to the World (physics layer) — a coupling the base architecture deliberately avoids — and the design doc must say so explicitly, not leave it implicit.**
V1's `MessageBroker` has zero awareness of Mesa positions; `WraithNetModel`/`world.py` has zero awareness of the Broker (aside from `build_observation` reading `broker.is_claimed()` — a one-way, already-explicit dependency, not routing logic). "If a jet flies too close to the jammer, ... it cannot send or receive messages" requires the Broker's `_route()` to know, per-tick, which agents are currently jammed — information that only the World (jet positions) and a new `EW_Jammer` entity can produce. Left unstated, each phase's implementer would invent a different wiring (e.g., some might make `JetAgentRuntime` itself poll world state, others might leak Mesa objects into `broker.py`), producing inconsistent architectures across phases exactly like the original project's A2/A3 findings.
*Fix:* Defined one explicit integration point (see K1): each tick, **the Bridge** (which already touches both the World and the Broker) computes each jet's `comms_jammed: bool` from world positions and calls `broker.set_jammed(agent_id, comms_jammed)` *before* that tick's negotiation phase. `MessageBroker._route()` consults this per-agent flag and drops (does not queue) any message where the sender or the intended receiver is currently jammed. Neither `broker.py` nor `world.py` needs to import the other — the Bridge remains the sole integration point, consistent with V1's existing separation of concerns.

**G4. `Observation` delivery (sensor/GPS feed) vs. `Message` delivery (radio) must be explicitly distinguished once jamming exists, or "still relies on its own sensors" (Feature 4) is meaningless.**
In V1, `agent.set_observation(...)` is a direct, synchronous Python call from the Bridge — it never goes through the Broker at all. Feature 4 says a jammed jet "must rely strictly on its last known world state and its own sensors," which only makes sense if `Observation` delivery is **unaffected** by jamming (it's onboard sensors/GPS, not radio) while only `Message` (CFP/BID/ACCEPT/REJECT/DATALINK_CONTACT) delivery is blocked. The source doc doesn't say this explicitly, and a plausible misreading ("jammed = no fresh data at all") would silently break `decide_intent()` (which needs a fresh `Observation` every tick to know current position/target HP) for every jammed jet.
*Fix:* Stated explicitly in the v3 design doc (§ Jamming): jamming affects **only** `MessageBroker` routing. `Bridge._tick()` continues to call `agent.set_observation(model.build_observation(...))` for every jet every tick regardless of jamming state — a jammed jet still has full local sensor awareness (subject to its own `sensor_radius`, per V2 Feature 1) and can still move/strike based on it; it just cannot exchange CFP/BID/ACCEPT/REJECT/DATALINK_CONTACT with anyone else while jammed.

**G5. SAM sites' place in the hybrid architecture (sync World vs. async Agent) is not specified, and the wrong choice would be a serious architectural regression.**
"SAM sites will run as independent agents on the Mesa engine tick" is consistent with them being simple `mesa.Agent` subclasses stepped synchronously inside `WraithNetModel.step()` — but the phrase "independent agents" is dangerously close to "give them a `JetAgentRuntime`-style async loop and a Broker mailbox," which would be architecturally wrong: SAM sites have no negotiation behavior (they don't bid, don't manage contracts, don't need `Observation`/CNP messaging at all) — they're purely reactive, deterministic physics entities, exactly like `JetAgent` in `world.py`, not like `JetAgentRuntime` in `agents.py`.
*Fix:* Stated explicitly: SAM sites (and EW jammers) are **Mesa-only** entities — plain `mesa.Agent` subclasses with state (position, engagement/jam radius) that `WraithNetModel.step()` reads and updates directly. They are never registered with the `MessageBroker` and never get a `JetAgentRuntime`. This mirrors the same sync/async split the base architecture already uses and requires no new architectural category.

---

## H. Feature 1 (Sensor Fusion / Fog of War): schema and protocol issues

**H1. The proposed `Observation` schema silently drops `tick` and reverts `known_targets` to an untyped `list[dict]` — both regressions of fixes already made in V1.**
```python
class Observation(BaseModel):
    agent_id: str
    current_position: tuple[float, float]
    sensor_radius: float
    payload: dict[str, int]
    known_targets: list[dict]   # <-- was list[TargetInfo] in V1
```
Dropping `tick` breaks every tick-dependent mechanism already built and tested in V1: `Message.tick` correlation, `CONTRACT_CLAIM_TTL_TICKS`/`expire_stale_contracts`, and `decide_intent()`'s use of `obs.tick` to stamp its `ActionIntent`. Reverting `known_targets` to `list[dict]` reintroduces the exact issue the original design review already fixed (finding B1 in `00_Design_Review_Findings.md`) — an untyped dict on the single most important piece of shared state throws away Pydantic's validation on target position/HP data, which is precisely the data multi-winner bidding depends on being correct.
*Fix:* `Observation` gains `sensor_radius: float` as an **addition**; `tick` is kept; `known_targets` stays `list[TargetInfo]` (with `TargetInfo` gaining a `target_type` field per Feature 2 — see I1). See the v3 design doc's updated schema.

**H2. `payload: dict[str, int]` has no defined key set, and no plan for how it interacts with the existing `payload_remaining: int` used throughout `agents.py`/`world.py`.**
"UPDATED (See Feature 2)" hands the reader to Feature 2's section, which never actually defines what the dict's keys are (weapon type names? Which ones?), nor updates any of the many call sites that currently read `observation.payload_remaining` as a single integer (`compute_bid`, `decide_intent`, `_apply_strike`, `_maybe_bid`, etc.). This is an underspecified core mechanic — every implementing LLM would invent different keys and a different `payload_remaining`-replacement convention, guaranteeing inconsistent phases.
*Fix:* Defined concretely (see I2): `payload: dict[WeaponType, int]` where `WeaponType = Literal["JDAM", "AIM9X"]` (the two weapons this expansion introduces — see I1), and a computed convenience property `total_payload_remaining = sum(payload.values())` is used everywhere the old scalar `payload_remaining` was used for "do I have anything left at all" checks; per-target bidding uses the weapon-suitability-filtered subset (I3).

**H3. No rule for how "known but not currently sensed" targets (received via `DATALINK_CONTACT`) stay in sync with reality, or go stale.**
Once an agent can know about a target purely from a broadcast (not its own sensor), that knowledge is a *memory*, not a live reading — the target could have moved, been destroyed, or been fully claimed since the contact was reported, and the receiving agent has no way to tell from the message alone. Left unresolved, an agent could confidently bid on (or literally try to `STRIKE`) a target based on arbitrarily stale coordinates/HP.
*Fix:* Defined a concrete confidence/staleness rule (see the v3 doc, Feature 1 section): each agent maintains a local `world_view: dict[target_id, (TargetInfo, last_updated_tick)]`, updated either by direct sensor detection (always trusted, always fresh) or by `DATALINK_CONTACT` (trusted only for **bidding/routing decisions**, never for the final `STRIKE` decision). An agent may only ever emit a `STRIKE` intent for a target it is *currently* sensing directly (`distance <= sensor_radius` this tick) — a `DATALINK_CONTACT`-only target can be *moved toward* and *bid on*, but never struck blind. A world-view entry older than `DATALINK_CONTACT_TTL_TICKS` ticks is dropped and no longer offered as a bid candidate.

**H4. `DATALINK_CONTACT` doesn't fit the existing `Message` schema's mandatory `contract_id`, and `target_data: Optional[dict]` repeats the untyped-dict mistake (H1) instead of reusing `TargetInfo`.**
Every existing `Message` requires a non-optional `contract_id: str` (correlates one CFP→BID→ACCEPT/REJECT negotiation) — a contact report isn't part of any negotiation and has no natural contract to correlate with. `Message._check_required_fields_for_message_type` also has no case at all for `DATALINK_CONTACT`, so a naive implementation would either violate the schema's own invariant or need an awkward sentinel `contract_id`.
*Fix:* `Message.contract_id` becomes `Optional[str] = None`, valid only when `None` for `message_type == "DATALINK_CONTACT"` (and still required/non-`None` for the four existing types — enforced in the updated `_check_required_fields_for_message_type` validator). `target_data: Optional[dict]` is replaced with `target_info: Optional[TargetInfo] = None`, required (non-`None`) exactly when `message_type == "DATALINK_CONTACT"`.

**H5. No debounce rule for `DATALINK_CONTACT` — naive "broadcast on every detection" would flood the Broker every tick for every jet that can see a target.**
Not specified at all in the source doc; without a rule, an agent that continuously sees `Target_A` would broadcast a fresh `DATALINK_CONTACT` for it every single tick forever, for no benefit (every other agent already either sees it too or already has a recent contact).
*Fix:* An agent broadcasts `DATALINK_CONTACT` for a target only on a **state-changing** detection: the first tick it senses a target not yet in its `world_view`, or a tick where a sensed target's `hp_remaining` or `claimed` status differs from what's in its `world_view` (i.e., "new information," not "same information again").

**H6. No behavior specified for "no targets known" — "fly search patterns (loiter/patrol)" is not an operational rule.**
Without a concrete rule, this is unimplementable/untestable as stated (what shape, what radius, deterministic or not?).
*Fix:* Defined a minimal, deterministic patrol rule (v3 doc): an `IDLE` agent with an empty `world_view` moves toward a fixed per-agent `patrol_waypoint` (its own scenario-start position, i.e., "hold position" for this PoC — a full patrol *pattern* is explicitly out of scope, same spirit as V1's C7 "explicitly scoped out" note) rather than emitting `IDLE`/doing nothing. This keeps the mechanic simple, deterministic, and testable while still satisfying "don't just sit still with zero sensor coverage" for a scenario that actually needs it.

---

## I. Feature 2 (Heterogeneous Weaponry): utility function and mechanics issues

**I1. `WeaponSuitabilityMatrix` and `target_type` are referenced but never defined anywhere — the single most load-bearing new data structure in this feature has zero concrete content.**
Without a fixed table, every implementing LLM invents its own weapon list, target-type list, and suitability numbers, making Feature 2 impossible to build consistently across phases or to test against a fixed scenario (the same class of failure the original project's F1 finding warned about — vague numbers silently changing what the scenario actually proves).
*Fix:* Fully specified in the v3 doc: two weapon types (`"JDAM"`, `"AIM9X"`), two target types (`"HARDENED_BUNKER"`, `"MOBILE_RADAR"`), and a fixed `WEAPON_SUITABILITY: dict[tuple[WeaponType, TargetType], float]` constant in `config.py`:

| | `HARDENED_BUNKER` | `MOBILE_RADAR` |
|---|---|---|
| `JDAM` | `1.0` | `0.0` |
| `AIM9X` | `0.0` | `1.0` |

(Binary suitability — a weapon either works on a target type or it doesn't, for this PoC. A continuous partial-suitability model is an explicit non-goal, same spirit as V1's C7.) `TargetInfo` gains `target_type: TargetType`.

**I2. The proposed utility formula reintroduces the exact un-normalized-distance bug the original review already found and fixed (C2), plus adds an unjustified, unnormalized bonus term that actively contradicts the "minimize total distance" objective.**
```
U = (w1 * S_weapon) - (w2 * D) + (w3 * P_total_remaining)
```
`D` here is raw Euclidean distance (up to ≈141 on the 100×100 grid) — not divided by `MAX_GRID_DISTANCE` as `docs/6th-Gen_Warfare_Sim_Design_Document_v2.md` §5.2 already established and `wraithnet/agents.py::compute_bid` already implements. Reintroducing raw distance silently defeats the "reward suitability/payload fit" intent exactly as C2 originally described. Worse, `+ w3 * P_total_remaining` (a jet's *total*, cross-weapon-type remaining payload) rewards jets simply for carrying more ordnance overall, regardless of whether any of it is relevant to the target being bid on — a full-inventory jet far away could out-bid a nearly-empty, well-suited, *nearby* jet, which is precisely the "send the wrong jet" failure mode `docs/00_Design_Review_Findings.md` finding F1 exists to prevent.
*Fix:* Keep the existing, already-normalized two-term form and extend it minimally rather than redesigning it:
```
payload_score = min(1.0, offered_count / hp_needed)      # of the SUITABLE weapon only
distance_score = distance / MAX_GRID_DISTANCE
U = (W_WEAPON * S_weapon) + (W1 * payload_score) - (W2 * distance_score)
```
`S_weapon` (the suitability of the *best suitable* weapon this jet is bidding with) is a `{0.0, 1.0}` gate folded additively at a fixed, dominant weight (`W_WEAPON = 10.0`, chosen so it always outranks the ≤`W1 + W2` = ≤1.5 combined range of the other two terms) — see I3 for why this must be a hard filter, not just a large weight, but the weight is kept anyway as defense-in-depth and to preserve a single, consistently-signed additive formula. No `P_total_remaining` term — irrelevant inventory must never influence a specific target's bid.

**I3. "Utility score drops to 0" does not actually disqualify an unsuitable bid under an additive formula that allows negative terms — this is a real logic bug, not just a style issue.**
If `S_weapon = 0` simply zeroes out the `w1 * S_weapon` term while `-w2 * D` still applies, an *unsuitable* jet sitting at zero distance scores `0 - 0 = 0`, while a *suitable* jet far away could score `1.0 - (0.5 * ~1.0) = 0.5` — suitable wins, fine — but a *suitable* jet at *maximum* distance combined with a low payload score could still net a **negative** utility, which is *lower* than an unsuitable jet's `0`. Under naive "highest utility wins" ranking, the manager could accept an unsuitable bid over a suitable-but-costly one. Folding disqualification into a continuous score is fundamentally the wrong mechanism for a hard yes/no constraint.
*Fix:* Suitability is enforced as a **hard precondition**, not a score component: `_maybe_bid()` and the self-bid path in `_broadcast_cfp_and_collect()` must check "does this jet carry at least one unit of a weapon with `WEAPON_SUITABILITY[weapon, target.target_type] > 0`?" *before* computing any utility at all — if not, the agent does not bid, full stop, regardless of how the score would come out. (The `W_WEAPON` term in I2 is kept only as a secondary safeguard/tie-influence among already-suitable bids, e.g. if a future weapon has partial `0 < S < 1` suitability — not as the disqualification mechanism itself.)

**I4. `payload_offered` (and the whole multi-winner damage-accounting model) is not updated for weapon-type-specific damage — a critical, unaddressed gap.**
V1's contract math treats "1 missile = 1 HP" uniformly (`Message.payload_offered: int`, `TargetState.hp_remaining -= 1` per strike). With two weapon types potentially doing different damage, or a target only being damageable by *one* of them, the existing integer-count `payload_offered` and hp-decrement logic are ambiguous: does offering "2 JDAMs" mean `payload_offered=2`, and does each JDAM strike still remove exactly 1 HP, or does damage vary by weapon? The source doc never says.
*Fix:* Keep it simple and explicit rather than open-ended: **each weapon type does exactly 1 HP of damage per hit, same as V1** — heterogeneity affects *whether* a weapon can be used on a target (I1/I3), not *how much* damage a successful hit does. `Message.payload_offered` gains a sibling field `weapon_type: WeaponType` (which weapon this bid's `payload_offered` count refers to); `ActionIntent` for `STRIKE` gains `weapon_type: WeaponType` so `world.py::_apply_strike` decrements the correct entry in `jet.payload[weapon_type]` (not a scalar `payload_remaining`).

---

## J. Feature 3 (Dynamic Threats / SAM Sites): mechanics and integration issues

**J1. "Deducts health or destroys the jet" is an unresolved either/or on a game mechanic that doesn't exist yet in V1 — jets currently have no health/armor concept at all.**
`JetAgent` (in `world.py`) has only `payload_remaining` and position — no hit points, no "destroyed" state, no removal-from-simulation path. Introducing jet destruction is a major mechanic that this sentence treats as a one-liner.
*Fix:* Defined concretely: jets gain `JetAgent.hp: int` (config constant `JET_HP`, e.g. `3`) and `JetAgent.is_destroyed` property. A SAM hit (deterministic per J2, not probabilistic) deducts exactly 1 HP; at `hp <= 0`, the jet is marked destroyed: removed from `space` (`self.space.remove_agent(jet)`), excluded from future `Observation`/`decide_intent`/negotiation entirely, and its `JetAgentRuntime` task is cancelled by the Bridge (see J4). "Health or destroy" is resolved to "health-based, and destruction is what happens when health reaches zero" — not an alternative mode.

**J2. No SAM engagement numbers (range, fire rate, damage, hit probability) are specified anywhere — same class of gap as I1.**
*Fix:* Concrete constants added to `config.py`: `SAM_ENGAGEMENT_RANGE` (e.g. `15.0`), `SAM_DAMAGE_PER_TICK: int = 1` (deterministic, no RNG — consistent with the rest of V1's PoC being fully reproducible/seedless-by-design per the v2 doc's "no randomness at t=0" and Phase 5's determinism acceptance criterion), fired automatically every tick a jet is within range (no separate "reload" state needed at this fidelity level).

**J3. SAM sites are never connected to the existing `target_type`/`TargetInfo` system (Feature 2), missing an obvious and valuable consistency win, and leaving "can SAMs be suppressed/destroyed" completely unanswered.**
If SAM sites are pure unkillable hazards, the tactical decision space is just "route around the ring," which is a much less interesting demonstration of "6th-gen" coordination than the source doc's framing implies (SEAD — Suppression of Enemy Air Defenses — is a standard, expected part of this domain and a natural fit for the existing multi-winner CNP machinery).
*Fix:* SAM sites are modeled as a **third `target_type`**, `"SAM_SITE"`, with their own `hp_required`/`hp_remaining`, participating in ordinary CNP contracts as targets like any other (subject to weapon suitability — a SAM site plausibly needs a specific weapon, e.g. `AIM9X`, chosen for the fixed scenario in the v3 doc). This reuses 100% of the existing multi-winner bidding/manager-election machinery for "destroy the SAM site" instead of inventing a separate mechanic, and gives jets an actual incentive to coordinate around suppressing a threat rather than only avoiding it.

**J4. "Its active contracts are broken" and "Manager Fallback" both assume a re-tasking mechanism that must be built on the *already-fixed* re-negotiation path (see prerequisite note above) — and the source doc never specifies what "detects a wingman has dropped off comms" actually means operationally, since V1 has no liveness/heartbeat mechanism at all.**
The Broker is fire-and-forget (no delivery acknowledgment, no presence tracking); a Manager currently has no way to distinguish "my contractor is silently making progress" from "my contractor no longer exists." Without an explicit mechanism, "detects... has dropped off" is unimplementable as stated.
*Fix:* Defined a concrete detection rule, reusing existing state rather than adding a new heartbeat protocol: each tick, the **Bridge** (which already iterates all jets and already knows about destruction from `world.py`) checks every open contract's committed contractors against `model.jets`; if a contractor agent_id is missing (destroyed) or its jet's `hp` dropped this tick, the Bridge calls a new `agent.notify_contractor_lost(contractor_id)` on that contract's Manager. The Manager (still using its existing `managed_target_id`/`_broadcast_cfp_and_collect` machinery — G2/prerequisite) re-opens a CFP for the shortfall still needed, exactly as it already does when round-1 bids don't fully cover a target (no new negotiation code path — just a new *trigger* for the existing one).

**J5. "Forced to abort (fly evasive maneuvers)" implies a new `ActionIntent` state not present in the `Literal["MOVE", "STRIKE", "IDLE"]` schema, and is left as an unexplained aside with no trigger condition, duration, or effect defined.**
*Fix:* Scoped out of this expansion as an explicit non-goal (same pattern as V1's C7): a damaged-but-alive jet continues to behave exactly as before (no automatic evasion AI). Only outright destruction (J1) changes agent behavior. If evasive maneuvers are wanted in a future expansion, they need their own `ActionIntent.action_type` addition and a concrete trigger rule — not implied by a single adjective in a feature bullet.

---

## K. Feature 4 (Electronic Warfare / Jamming): mechanics and integration issues

**K1. The core jamming mechanism ("cannot send or receive") needs an explicit Broker-level implementation, not just a Mesa-side flag — already raised architecturally in G3/G4; this item specifies the exact mechanism.**
*Fix:* `MessageBroker` gains `self._jammed: set[str]` and `set_jammed(agent_id: str, jammed: bool) -> None`. `_route()` is updated: a broadcast message is delivered to a given recipient only if *neither* the sender nor that recipient is in `_jammed`; a direct message is delivered only if *neither* sender nor receiver is jammed (otherwise dropped, exactly like the existing "unregistered receiver" drop case — no exception raised, no retry). The Bridge computes and pushes jammed status once per tick, before the negotiation phase (G3).

**K2. No `EW_Jammer` parameters (position, radius, count, mobility) are specified — same class of gap as I1/J2.**
*Fix:* One static `EW_Jammer` Mesa agent for this PoC, concrete position and `JAM_RADIUS` constant defined in the v3 doc's updated scenario section. Mobile/multiple jammers are an explicit non-goal for this expansion (can be revisited later without schema changes, since jamming state is already computed fresh every tick).

**K3. No rule for what happens to an agent's in-flight negotiation state when it becomes jammed mid-negotiation, or when it exits the jam radius later — without one, this directly re-triggers the exact class of bug already found and fixed for the base CNP (a manager or contractor left in a state no one can update).**
This is a real correctness risk: unlike a normal `MAX_TICK_WAIT_SECONDS` cutoff (already handled by `_abort_open_management`, see prerequisite note), jamming can persist for many ticks in a row.
*Fix, Manager side (turns out to need no new code, just an explicit analysis):* a jammed Manager's own CFPs never leave the mailbox and incoming BIDs never arrive (K1), so its `_collected_bids` stays effectively empty every round it's jammed. It therefore either gets cut off by the existing `MAX_TICK_WAIT_SECONDS` cancellation (already-implemented `_abort_open_management`) or naturally exhausts `MAX_CFP_ROUNDS` and gives up on its own (already-implemented give-up path) — both already release the claim for a differently-positioned, unjammed agent to pick up later. No jamming-specific branching needs to be added to `_broadcast_cfp_and_collect`/`_resolve_contract`.
*Fix, bidder side (this one does need new code):* a bidder in `CONTRACTOR_PENDING`, in the base protocol, waits indefinitely for an `ACCEPT`/`REJECT` that was previously always reliably delivered (every registered agent's mailbox always received it). Jamming is the *first* mechanism that can make that message never arrive despite both agents being registered — if either side is jammed between `BID` and the resulting decision, the bidder is stuck in `CONTRACTOR_PENDING` forever, the same zombie shape as the original bug in `docs/02_Code_Review_Phases_3-5.md`, just triggered a different way. Fixed with a new `CONTRACTOR_PENDING_TTL_TICKS` (mirroring the existing `CONTRACT_CLAIM_TTL_TICKS` stale-claim pattern rather than inventing a new kind of mechanism): a bidder records the tick it entered `CONTRACTOR_PENDING` and reverts itself to `IDLE` if that many ticks pass with no decision.
A jammed **Contractor** (already-`ACCEPT`ed) continues executing its already-committed `decide_intent()` plan unaffected (per G4, movement/strike is sensor-driven, not comms-driven).

---

## L. Cross-cutting gaps (apply to the whole expansion, not one feature)

**L1. No updated, concrete scenario.**
V1's entire methodology (see `00_Design_Review_Findings.md` finding F1) is built on the principle that vague or convenient numbers silently change what a scenario actually proves — every new mechanic here (sensor radii, weapon loadouts, SAM range/position, jammer range/position) needs the same treatment: fixed, deliberately-chosen numbers with a written rationale, not left to each phase's implementer to invent.
*Fix:* A full concrete V2 scenario (positions, radii, loadouts, SAM/jammer placement) is defined in `docs/04_6th-Gen_Warfare_Sim_Design_Document_v3_Phase2.md` §2, with the same kind of "why these numbers" rationale V1 §2 has for `hp_required=5`.

**L2. No termination-condition update for jet loss.**
V1 terminates only on "all targets destroyed." With jets now destructible (J1), the corrected doc must also define what happens if the swarm is wiped out before finishing (a legitimate failure mode that didn't exist before) — otherwise the simulation could hang forever waiting for a termination condition that can now never be reached.
*Fix:* `WraithNetModel.step()` also sets `self.running = False` if every jet is destroyed (failure termination, distinct from success termination — both reported by `metrics.summarize()`, see L3).

**L3. No metrics/observability updates for any of the four new mechanics.**
The existing `DataCollector`/`metrics.summarize()` tracks distance/missiles/targets-destroyed only; none of jets-lost, contacts-shared, jamming-time, or per-weapon-type expenditure are tracked, so none of the new mechanics' effects would be observable in the final validation run.
*Fix:* `DataCollector` model reporters extended with `JetsDestroyed`, `SAMSitesDestroyed`, `TotalJammedAgentTicks`; `metrics.summarize()` extended with per-weapon-type missile counts and a `swarm_wiped_out: bool` flag. Specified fully in the new Phase 10 doc.

**L4. No stated implementation order for the four features, despite real dependencies between them.**
Feature 2 (weapon types) changes `TargetInfo`/`Message`/`ActionIntent` schemas that Features 3 and 4 both build on top of (SAM sites are targets with a `target_type`; jamming interacts with the same `Message` flow used for bidding). Feature 1 (fog of war) changes how `known_targets` is populated, which every other feature's negotiation logic reads. Attempting these in the order listed in the source doc (Fog of War, Weaponry, SAM, Jamming) is *fine* by coincidence, but nothing says so — a naive parallel-phases approach could easily build Feature 3 or 4 against pre-Feature-1/2 schemas and require a rewrite.
*Fix:* Defined a strict build order for the new implementation phases: **Phase 6 (Weapon System & Schema Foundations, Feature 2) → Phase 7 (Sensor Fusion & Fog of War, Feature 1) → Phase 8 (Dynamic Threats/SAM, Feature 3) → Phase 9 (Electronic Warfare/Jamming, Feature 4) → Phase 10 (Integration, Metrics & Validation)**. Weapon typing is sequenced *before* fog-of-war because `TargetInfo.target_type` and weapon-suitability filtering touch every place `known_targets` is read, and it's simpler to fold sensor-radius filtering into an already-updated data model than the reverse.

---

## Summary of structural changes

| Source doc proposal | Corrected |
|---|---|
| `Observation` drops `tick`, `known_targets: list[dict]` | `tick` kept; `known_targets: list[TargetInfo]` (typed), `TargetInfo` gains `target_type` |
| `payload: dict[str, int]`, undefined keys | `payload: dict[WeaponType, int]`, `WeaponType = Literal["JDAM", "AIM9X"]`, fixed |
| `WeaponSuitabilityMatrix` referenced, never defined | Fixed `WEAPON_SUITABILITY` table in `config.py` (2 weapons × 2(+1) target types) |
| `U = w1*S_weapon - w2*D + w3*P_total_remaining` (raw `D`, unjustified `P_total_remaining` term) | `U = W_WEAPON*S_weapon + W1*payload_score - W2*distance_score`, both non-gate terms normalized to `[0,1]` as in V1 |
| Suitability "drops utility to 0" (soft, exploitable) | Suitability is a **hard precondition** on bidding at all |
| `DATALINK_CONTACT` with mandatory `contract_id`, untyped `target_data: dict` | `contract_id` made `Optional`; `target_data` → typed `target_info: Optional[TargetInfo]` |
| No contact staleness rule | `world_view` memory model with `DATALINK_CONTACT_TTL_TICKS`; direct-sensor-only `STRIKE` rule |
| No contact broadcast debounce | Broadcast only on new/changed detections |
| "SAM fires (deducts health or destroys)" — ambiguous, no jet-health concept exists | Concrete `JetAgent.hp`/`is_destroyed`; deterministic 1-dmg/tick while in range |
| No SAM/jammer numeric parameters | Concrete `SAM_ENGAGEMENT_RANGE`, `SAM_DAMAGE_PER_TICK`, `JAM_RADIUS`, fixed positions in the v3 scenario |
| SAM sites disconnected from `target_type`/CNP system | SAM sites are ordinary CNP targets (`target_type="SAM_SITE"`) — reuses existing multi-winner machinery |
| "Squad Leader" role, undefined relationship to "Manager" | Retired; fallback re-tasking stays scoped to the existing per-contract Manager role |
| No liveness/heartbeat mechanism for "dropped off comms" detection | Bridge-driven `notify_contractor_lost` using existing destruction/HP state, no new heartbeat protocol |
| "WebSocket/Asyncio bus" | Removed; jamming is purely an in-process `MessageBroker` routing rule |
| No Broker↔World coupling defined for jamming | Bridge computes jammed status once/tick, pushes to `broker.set_jammed(...)`; `_route()` consults it |
| Ambiguous whether jamming blocks `Observation` (sensors) too | Explicit: jamming affects `Message` routing only, never `Observation` delivery |
| No rule for in-flight negotiation state during/after jamming | Jammed rounds don't advance `_cfp_round`; existing `CONTRACT_CLAIM_TTL_TICKS` remains the backstop |
| No jet-loss termination condition | `model.running = False` also on full swarm loss (failure termination) |
| No metrics for any new mechanic | `DataCollector`/`summarize()` extended: jets/SAMs destroyed, jammed-ticks, per-weapon-type usage |
| No specified build order across the 4 features | Fixed order: Phase 6 (Weapons/Schema) → 7 (Fog of War) → 8 (SAM) → 9 (Jamming) → 10 (Validation) |
| Assumes base CNP re-negotiation "just works" for retasking | Explicit prerequisite: base CNP re-negotiation bug (see `02_Code_Review_Phases_3-5.md`) must be (and is now) fixed first |
