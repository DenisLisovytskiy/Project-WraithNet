# Design Review: Findings & Fixes

Source reviewed: `6th-Gen Warfare Sim Design Document.txt` (original).
This document lists every inconsistency, ambiguity, and bad practice found, and states how it is resolved in the corrected design (`6th-Gen_Warfare_Sim_Design_Document_v2.md`) and in the phase implementation docs (`docs/phases/`).

The original file is left untouched for reference/history. Treat `_v2.md` as the current source of truth.

---

## A. Architectural inconsistencies

**A1. "Async, independent of tick" contradicts the lockstep execution loop.**
Section 1 claims agent comms are "independent of the simulation tick," but Section 6's loop makes Mesa block on step 4 (full negotiation) before it will tick again. That's lockstep, not independent-async. There's no middle ground described (e.g., comms running ahead across multiple ticks).
*Fix:* The doc now states plainly that this is a **single-threaded, semi-synchronous hybrid**: one asyncio event loop drives both Mesa's `step()` and the agents' negotiation coroutines; negotiation *can* span multiple ticks (a contract doesn't have to close in one tick), but the Bridge gives idle agents a bounded window each tick to negotiate before it pulls everyone's next move (any agent not yet committed simply yields `IDLE`). This removes the false claim of full independence while keeping the useful property that CNP messages aren't forced to resolve inside a single tick.

**A2. Three names for one component.**
"Communication Bus" (§3 tech stack), "Message Broker (The Radio)" (§3 component list), and "Comms Broker" (§6) all refer to the same thing.
*Fix:* Standardized on **Message Broker** everywhere.

**A3. "Thread-safe Queue" contradicts the asyncio concurrency model.**
§3 says the Bridge uses a "Thread/Async Queue Manager" and §6 says "Thread-safe Queue," but the tech stack specifies `asyncio` — and `asyncio.Queue` is coroutine-safe, not OS-thread-safe. Nothing in the doc says Mesa runs on a separate OS thread from the event loop, so invoking `queue.Queue`/thread-safety language is misleading and would push an implementer toward an unnecessary (and harder to get right) multi-threaded design.
*Fix:* Explicitly pinned to **single-threaded asyncio**: Mesa's `step()` (plain synchronous Python, no I/O) is simply called from inside a coroutine on the same event loop that runs the agents. All queues are `asyncio.Queue`. No OS threads, no thread-safety concerns, no GIL juggling.

**A4. Grid type left ambiguous ("2D continuous or discrete").**
This is a binary implementation choice with real consequences (movement math, distance metric, Mesa space class) left to the implementer to guess — bad for a spec that will be handed to an LLM one phase at a time, since different phases could each guess differently.
*Fix:* Resolved to **continuous space**, non-toroidal, 100×100 — consistent with the `tuple[float, float]` position type already used in the schemas. Explicitly names `mesa.space.ContinuousSpace` (stable API), not the Mesa 4.0-alpha experimental vectorized `ContinuousSpace`.

**A5. Distance metric never specified.**
"Distance_To_Target" is used in the utility function and objective, but no metric is named.
*Fix:* Defined as **Euclidean distance**, matching continuous space.

---

## B. Data schema issues (these directly contradict the doc's own stated goal of "strict Pydantic schemas")

**B1. `known_targets: list[dict]` is untyped.**
Using a bare `dict` inside a Pydantic model throws away all validation/type-safety on the single most important piece of shared state (target status), directly contradicting §3's stated reason for choosing Pydantic.
*Fix:* Added a proper `TargetInfo` model with typed fields (`target_id`, `position`, `hp_required`, `hp_remaining`, `claimed`).

**B2. No distinction between `hp_required` (static) and `hp_remaining` (mutable).**
Without a remaining-damage field, agents can't tell a fresh target from a partially-damaged one, and can't compute how many missiles are still needed — which is required for the coordination scenario to work at all (see C1 below).
*Fix:* `TargetInfo.hp_remaining` added; decremented by the World on each successful strike.

**C-cross-ref.** `Message.receiver_id: Literal["BROADCAST"] | str` is a no-op union — `str` already accepts `"BROADCAST"`, so the `Literal` adds zero type-safety while looking like it does. It's a red herring that could mislead an implementer into thinking receiver routing is validated when it isn't.
*Fix:* Kept `receiver_id: str`, documented `"BROADCAST"` as a sentinel constant (`BROADCAST_ID`), and note that real validation (does this ID exist?) happens at the Broker, not the schema.

**B3. No correlation ID between CFP → BID → ACCEPT/REJECT.**
If two negotiations are ever in flight at once (e.g., CFPs for Target_A and Target_B open simultaneously — which *will* happen in the PoC scenario since both targets are unassigned at tick 0), agents have no field to tell which CFP a given BID answers, or which round a message belongs to.
*Fix:* Added `contract_id: str` (UUID, minted by the Manager when it opens a CFP) and `tick: int` to `Message`.

**B4. No field for "how much can this bidder actually contribute."**
`bid_utility` is a single scalar. The Manager needs to know the *quantity* of missiles a contractor is offering, not just a relative ranking, in order to run multi-winner acceptance (see C1).
*Fix:* Added `payload_offered: int` to `Message`, and `hp_remaining` (the ask) to the CFP payload.

**B5. No timestamp/tick on `Observation`/`ActionIntent`.**
Without a tick number, staleness and timeout logic (needed to fix D3 and D4 below) can't be implemented.
*Fix:* Added `tick: int` to `Observation`, `ActionIntent`, and `Message`.

---

## C. CNP protocol / utility function gaps (the most severe issues)

**C1. No mechanism for partial/multi-winner contracts — despite the scenario requiring exactly that.**
This is the biggest hole in the original document. The scenario is explicitly built so that Jet_1 (1 missile) *cannot* solo-kill Target_A (needs 2), which only makes sense if the protocol supports a Manager accepting bids from *multiple* contractors for the same target. The original CNP section describes a classic single-winner Contract Net (one CFP → one ACCEPT) and never reconciles this with "forcing multiple jets to coordinate if needed." As written, the scenario is literally unsolvable by the protocol as specified.
*Fix:* Fully specified a **multi-winner acceptance algorithm** (see the v2 doc §5): the Manager collects bids until a deadline, ranks them, and greedily accepts bids (largest utility first) until cumulative `payload_offered` covers `hp_remaining`, REJECTing the rest. If bids don't cover the full requirement, the contract stays open and a follow-up CFP is issued with the updated (reduced) `hp_remaining`.

**C2. Utility function has a scale mismatch that silently defeats its own purpose.**
`U = w1*(Available/Required) - w2*Distance`, with `w1=1.0, w2=0.5`, on a 100×100 grid: the payload term ranges roughly 0–4, but the distance term ranges 0–~70 (0.5 × up to ~141 units). Distance completely dominates the score for almost any pair of jets, meaning the "utility function" behaves almost exactly like "always pick whichever jet is closest," regardless of payload fit — the opposite of the stated intent (rewarding payload/target fit enough to force coordination). This is a bug that would only show up empirically after implementation, which is exactly the kind of thing a design doc should catch first.
*Fix:* Both terms are now normalized to comparable ranges before weighting: `payload_score = min(1.0, payload_remaining / hp_remaining)` (capped at 1 so overloaded jets don't get an unbounded bonus) and `distance_score = distance / MAX_GRID_DISTANCE` (÷ grid diagonal ≈141.42, so it's in [0,1]). With both terms in [0,1], `w1=1.0, w2=0.5` is now a meaningful, tunable trade-off instead of an accident of scale.

**C3. Contradictory/ambiguous rule for insufficient payload.**
"If Available_Payload < Required_Payload, the bid should either be 0, or heavily penalized" — gives the implementer two different, unresolved options, and also directly conflicts with C1: if underfilled bids are zeroed out, Jet_1 (1/2 missiles) can never win a partial contract, so the scenario can't resolve. This clause and the multi-winner requirement cannot both be true as written.
*Fix:* Removed the penalty rule entirely. Partial capability is a **valid, normal bid** (that's the whole point of the capped `payload_score`); the only disqualifying case is `payload_remaining == 0` (an agent with nothing to contribute simply does not bid).

**C4. No manager-election / duplicate-CFP protection.**
"One agent acts as Manager (e.g., the first one to spot the target)" has no operational definition of "first," and nothing stops two agents from each deciding they're the first and broadcasting competing CFPs for the same target — leading to double contracts and double-committed jets.
*Fix:* Since the design is now single-threaded asyncio (A3), there's no true race between agents — only interleaving at `await` points. The Broker is made the single arbiter: it keeps `open_contracts: dict[target_id, contract_id]`, and **silently drops any CFP for a `target_id` that already has an open contract**. This is possible precisely *because* everything shares one event loop; it would not be safe under A3's rejected multi-thread interpretation.

**C5. No tie-break rule.**
Equal-utility bids (which *will* happen — see the v2 scenario coordinates, where Jet_2 and Jet_3 are symmetric) are left undefined.
*Fix:* Deterministic tie-break by ascending `agent_id` string, documented explicitly.

**C6. No bid deadline / timeout — risk of deadlock.**
§6 step 5 offers two different, unreconciled tick-advance strategies ("waits a small time interval" **or** "waits for all agents") without saying which, and neither has a fallback if an agent never responds (crashes, bug, infinite negotiation).
*Fix:* Defined one concrete rule: each tick, the Bridge gives all currently-idle agents up to `MAX_TICK_WAIT_SECONDS` (combined, via `asyncio.wait_for`) to negotiate before it moves on and pulls every agent's `decide_intent()` — any agent not yet committed simply yields `IDLE` for that tick (a safe default, not an error). Separately, a Manager's bid-collection window is bounded by `BID_TIMEOUT_SECONDS` per round (capped at `MAX_CFP_ROUNDS` re-negotiation rounds), and a stale open contract expires via `CONTRACT_CLAIM_TTL_TICKS` so a stuck negotiation doesn't permanently lock a target.

**C7. No REJECT-handling / re-bid flow specified.** *(minor, explicitly scoped out)*
*Fix:* Documented as a deliberate PoC simplification: contractors always honor an ACCEPT (no retraction/failure modeled). Noted as an explicit non-goal rather than a silent gap.

---

## F. The scenario numbers don't actually force the cooperation they're meant to demonstrate (found by simulating the protocol by hand)

**F1. `Target_A.hp_required = 2` does not require `Jet_1` to participate at all.**
This is the subtlest and most important issue found in this review, because it only shows up when you actually trace the negotiation through with real numbers rather than reading the spec at face value.

The original scenario sets `Target_A.hp_required = 2`, reasoning that since `Jet_1` only carries 1 missile, it "can't solo it" and must team up with someone. But `Jet_2` and `Jet_3` each carry a *full* 4-missile payload — comfortably enough to solo a 2-HP target by themselves. Once the utility function is normalized correctly (fix C2) and a jet only pays a distance cost *if it actually moves*, the fleet-wide-distance-minimizing answer is: send **one** fully-loaded distant jet to solo `Target_A`, leave `Jet_1` sitting exactly where it is (zero movement, zero cost), and send the other distant jet to `Target_B`. Total fleet distance for that plan is strictly less than any plan that also moves `Jet_1` (moving `Jet_1` only ever *adds* its ~7.07 units of travel, since no other jet's trip gets any shorter by `Jet_1` helping). A rational, utility-maximizing negotiation therefore has no incentive to ever accept `Jet_1`'s bid — the "forced coordination" scenario silently reduces to "one distant jet solos each target," and the multi-winner acceptance path (C1) would only ever be exercised by hand-written unit tests, never by an actual end-to-end run.

*Fix:* Changed `Target_A.hp_required` from `2` to **`5`** — i.e., strictly greater than the maximum single-jet payload (`4`), not just greater than `Jet_1`'s payload. This makes it **mathematically impossible** for any single jet to solo `Target_A`, no matter how the utility weights are tuned: at least two jets *must* contribute, guaranteeing the multi-winner CNP path is exercised on every real run. The document intentionally does *not* pin down which two (or three) jets end up contributing — that's an emergent property of the utility function — only that at least two must, which is the actual property worth demonstrating and the one Phase 5 validates.

## D. Missing termination, metrics, and observability

**D1. No termination condition.**
Nothing tells Mesa (or a human) when the simulation is "done." A PoC without a defined success/stop condition can't be objectively validated.
*Fix:* Simulation ends when every target's `hp_remaining <= 0` (Mesa's `self.running = False`). Implemented in Phase 1 (`World.step()`) since Phase 4's Bridge loop needs a real stop condition to run to completion — kept separate from metrics collection (D2), which stays in Phase 5.

**D2. Objective ("minimize total distance traveled") is never operationalized.**
§2 states the goal but no schema, component, or phase ever measures it — despite §3 explicitly crediting Mesa with "data collection" as a capability that is then never used.
*Fix:* Added a `DataCollector` spec (model reporters: `TotalDistanceTraveled`, `TicksElapsed`, `TargetsDestroyed`, `MissilesFired`; agent reporters: per-jet distance and payload) and a dedicated validation phase (Phase 5) that runs the exact scenario and reports these numbers.

**D3/D4.** Covered by C6 above (timeouts feed directly into "how do we know the run is stuck vs. done").

---

## E. Process / engineering hygiene

**E1. No pinned dependency versions.**
"Mesa (Python)" / "Pydantic" with no versions is a real risk: Mesa 3.x removed the old `mesa.time` schedulers in favor of `AgentSet`/`model.agents`, and Mesa 4.0 (currently alpha as of writing) reworks `ContinuousSpace` again (`agent.position` instead of `agent.pos`, numpy-vectorized, experimental). An LLM implementing this without a pinned version is likely to mix APIs from different Mesa generations and produce code that doesn't run.
*Fix:* Pinned `mesa>=3.1,<4.0`, `pydantic>=2.0,<3.0`, `python>=3.11`, and the phase docs explicitly say to use the **stable** `mesa.space.ContinuousSpace` + `AgentSet` APIs, not the 4.0-alpha experimental ones.

**E2. No project structure / file layout given.**
Left entirely to the implementer, which — across 4+ separate LLM prompts — invites inconsistent module names/paths between phases.
*Fix:* A concrete package layout is defined once in Phase 0 and referenced by every later phase doc.

**E3. Phase sequencing makes early phases untestable end-to-end.**
Original Phase 2 (Comms Broker) and Phase 3 (full CNP logic incl. utility function) are both built and would need to be tested with no real Mesa-driven `Observation` data, since the Bridge (the only thing that produces real Observations from the actual scenario) isn't built until Phase 4. That means Phase 3's negotiation logic can only be exercised with hand-mocked data, and integration bugs between "the CNP logic as designed" and "the CNP logic against real World state" surface all at once at the very end.
*Fix:* Re-sequenced into 6 phases (0–5). Phase 1 now includes a minimal *synchronous* stub bridge (no comms yet) so the World is runnable/inspectable from Phase 1 onward. Phase 4 becomes strictly about replacing the stub with the real async bridge, not about wiring things together for the first time. A new Phase 5 runs the literal scenario from §2 end-to-end and checks the objective was met.

**E4. No final validation/demo phase.**
The original 4 phases stop at "connect the bridge." Nothing ever runs the actual test case from §2 or checks the stated objective (targets destroyed, distance minimized). A PoC without a validation step isn't a proof of anything.
*Fix:* Added **Phase 5: Metrics, Termination & Scenario Validation**.

**E5. No phase 0 / environment setup.**
Dependency installation and repo scaffolding were left implicit.
*Fix:* Added **Phase 0: Project Setup**.

---

## Summary of structural changes

| Original | Corrected |
|---|---|
| 4 phases | 6 phases (0–5) |
| `Target_A.hp_required = 2` (soloable by any full jet) | `Target_A.hp_required = 5` (exceeds max single-jet payload; cooperation structurally required) |
| Grid: continuous OR discrete | Continuous, 100×100, non-toroidal |
| `known_targets: list[dict]` | `list[TargetInfo]` (typed) |
| No `contract_id`/`payload_offered`/`tick` on `Message` | Added |
| Single-winner-only CNP | Multi-winner, partial-fulfillment CNP with explicit acceptance algorithm |
| Unnormalized utility function | Normalized utility function (both terms in [0,1]) |
| "0 or heavily penalized" ambiguity | Removed; partial bids are valid, only zero-payload is disqualifying |
| No manager election rule | Broker-level single-open-contract-per-target arbitration |
| No tie-break rule | Deterministic ascending-`agent_id` tie-break |
| No timeout/deadlock handling | Bid timeout + tick-advance timeout + contract TTL |
| No termination condition | All targets `hp_remaining <= 0` |
| No metrics | Mesa `DataCollector` spec + Phase 5 validation |
| No dependency versions | Pinned versions, stable (non-alpha) Mesa APIs specified |
| "Thread-safe Queue" + asyncio (contradiction) | Single-threaded asyncio throughout |
| 3 names for message broker | Standardized: "Message Broker" |
