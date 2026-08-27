# Software Design Document (v2): 6th-Gen Warfare Multi-Agent Simulator

> This is a corrected revision of `6th-Gen Warfare Sim Design Document.txt`. Every change made here is explained in `00_Design_Review_Findings.md`. This document is the source of truth for implementation; see `docs/phases/` for the phase-by-phase build plan.

## 1. Project Overview

This project is a Proof of Concept (PoC) for a 6th-generation warfare simulator. It demonstrates autonomous drone/jet swarms ("loyal wingmen") using decentralized negotiation to assign targets efficiently, including cooperating to destroy targets that no single jet can finish alone.

**Architectural Paradigm: Single-Threaded Hybrid (Sync World + Async Agents, One Event Loop)**

* **Environment (World):** Managed by the Mesa Python framework — spatial continuous space, target/jet state, and data collection. `Model.step()` is a plain, synchronous function.
* **Agent Logic (Brains) & Comms:** Custom Python coroutines (`asyncio`) implementing the Contract Net Protocol (CNP). These run on the **same event loop** as the code that drives Mesa — there is no separate OS thread. Mesa's synchronous `step()` is simply called from a coroutine; because it does no I/O, this never blocks other coroutines for more than the duration of one step's computation.
* Negotiation is **not required to finish within a single tick** — a contract can span multiple ticks — but the Bridge will not advance past the current tick's negotiation phase until every idle agent has had a chance to negotiate, or a timeout elapses; any agent not yet committed simply yields an `IDLE` intent for that tick when the Bridge pulls it (see §6).

## 2. The PoC Scenario (The Test Case)

Fixed, reproducible initial state (no randomness at t=0):

* **Grid:** Continuous, non-toroidal, `100.0 x 100.0` (`mesa.space.ContinuousSpace`).
* **Distance metric:** Euclidean.
* **Targets:**
  * `Target_A`: position `(75.0, 75.0)`. `hp_required = 5` (needs 5 missile strikes to destroy).
  * `Target_B`: position `(25.0, 25.0)`. `hp_required = 1` (needs 1 missile strike).
* **Agents (Jets):**
  * `Jet_1`: position `(70.0, 70.0)` — proximal to `Target_A` (distance ≈ 7.07). Payload: **1 missile**.
  * `Jet_2`: position `(5.0, 95.0)` — distant from both targets (≈72.8 from each). Payload: **4 missiles** (full).
  * `Jet_3`: position `(95.0, 5.0)` — distant from both targets (≈72.8 from each). Payload: **4 missiles** (full).
* **Rule:** 1 missile strike = 1 HP of damage. A jet must be within `STRIKE_RANGE` (default `1.0` units) of a target's position to strike it.
* **Objective:** Destroy both targets (`hp_remaining <= 0` for each) while minimizing total distance traveled across all jets (fuel consumption).

**Why these exact numbers matter (do not change without updating this rationale):**

`Target_A.hp_required = 5` is deliberately set **higher than any single jet's maximum payload (4)** — not just higher than `Jet_1`'s payload. This is a correction of a subtle flaw found during design review (see `docs/00_Design_Review_Findings.md`, item F1): with the original numbers (`hp_required=2`), a rational, distance-aware utility function does **not** actually need `Jet_1` to participate at all — a single fully-loaded distant jet can solo-cover a 2-HP target more cheaply (in total fleet distance) than splitting the kill with `Jet_1`, because `Jet_1`'s own movement has a real, non-zero distance cost, while *not* dispatching it costs nothing. A "coordination scenario" whose optimal solution is "don't coordinate, just send one distant jet and leave the proximal one idle" doesn't actually exercise the multi-winner negotiation path it's supposed to demonstrate. By requiring more HP than any single jet can deliver, at least two jets are **structurally required** to destroy `Target_A`, no matter how the utility weights are tuned — this guarantees the multi-winner CNP flow (§5) is exercised on every run, not just in hand-constructed unit tests.

`Jet_2` and `Jet_3` are placed at equal distance from both targets, which means their bids can be exactly tied in utility whenever both are still free to bid on the same target — this is why a deterministic tie-break rule (§5) is necessary at all, and it is exercised directly by Phase 3's unit tests. (In a full end-to-end run, scheduling order may cause one of them to commit to `Target_B` before the tie ever has a chance to materialize — that's a fine, valid outcome too; the tie-break rule exists to make the *outcome* deterministic whenever a tie *does* occur, not to guarantee one occurs on every run.) Total available missiles (1+4+4=9) comfortably exceeds total required (5+1=6), so infeasibility is never the failure mode being tested — coordination and efficiency are. Note that *which* two (or three) jets end up contributing to `Target_A` is intentionally **not** pinned down by this document — that emerges from the utility function — but that at least two distinct jets must contribute is a hard, testable invariant (see Phase 5).

## 3. System Architecture & Tech Stack

### Tech Stack (pinned)

* Python `>=3.11`
* Simulation Engine: `mesa>=3.1,<4.0` — use the **stable** `mesa.space.ContinuousSpace` and `AgentSet`/`self.agents` scheduling APIs. Do **not** use the Mesa 4.0-alpha experimental `mesa.experimental.continuous_space` module (different, unstable API: vectorized, `agent.position` instead of `agent.pos`).
* Communication: stdlib `asyncio`, using `asyncio.Queue` exclusively (single event loop; no OS threads, no `queue.Queue`, no ZeroMQ/WebSockets — those are explicitly out of scope for this PoC).
* Data Validation: `pydantic>=2.0,<3.0` for all inter-component messages.

### Component Breakdown

1. **Mesa Model (The World):** Tracks jet positions/payloads and target positions/HP in continuous space. Exposes a synchronous `step(intents)` that applies physics (movement, strikes) for the given `dict[agent_id, ActionIntent]`, and updates a Mesa `DataCollector`. Owns the termination check (§6).
2. **The Bridge:** A coroutine (not a thread) that, once per tick: (a) pushes a fresh `Observation` directly into each agent (`agent.set_observation(obs)`), (b) lets idle agents attempt/continue negotiation (bounded by a timeout), then (c) **pulls** each agent's next move by calling its synchronous `agent.decide_intent() -> ActionIntent`, and finally (d) calls `World.step(intents)`. There is no separate "intent queue" — `decide_intent()` is a pure, synchronous function of the agent's current commitment state, so it can simply be called directly; no submission/publish step is needed for it.
3. **Message Broker:** The single component name for the router that handles CFP broadcasts and direct BID/ACCEPT/REJECT messages between agents. Also the sole arbiter of "who is allowed to manage a negotiation for a given target" (§5, Manager Election).
4. **Custom Agents (The Brains):** One coroutine per jet, running the CNP state machine (`IDLE → MANAGER` or `IDLE → CONTRACTOR → COMMITTED`) and producing `ActionIntent`s.

## 4. Data Schemas (Pydantic Models)

All inter-component communication must use these schemas. `known_targets` uses a typed model (not `dict`) to actually get the validation benefit Pydantic is chosen for.

```python
from pydantic import BaseModel
from typing import Optional, Literal
import uuid

BROADCAST_ID = "BROADCAST"  # sentinel value for Message.receiver_id

# --- World to Agent Data ---

class TargetInfo(BaseModel):
    target_id: str
    position: tuple[float, float]
    hp_required: int
    hp_remaining: int
    claimed: bool  # True if the Broker currently has an open contract for this target

class Observation(BaseModel):
    agent_id: str
    tick: int
    current_position: tuple[float, float]
    payload_remaining: int
    known_targets: list[TargetInfo]

# --- Agent to World Data ---

class ActionIntent(BaseModel):
    agent_id: str
    tick: int
    action_type: Literal["MOVE", "STRIKE", "IDLE"]
    target_position: Optional[tuple[float, float]] = None
    target_id: Optional[str] = None

# --- Agent to Agent Data (Contract Net Protocol) ---

class Message(BaseModel):
    message_id: str  # uuid4 hex, unique per message
    contract_id: str  # uuid4 hex, minted by the Manager when a CFP opens; correlates one whole negotiation
    tick: int  # simulation tick this message was authored on
    sender_id: str
    receiver_id: str  # BROADCAST_ID or a specific agent_id
    message_type: Literal["CFP", "BID", "ACCEPT", "REJECT"]
    target_id: str
    hp_remaining: Optional[int] = None     # set on CFP: damage still needed
    payload_offered: Optional[int] = None  # set on BID: missiles this bidder proposes to commit
    bid_utility: Optional[float] = None    # set on BID: computed utility score
```

## 5. Contract Net Protocol (CNP) Implementation

Agents negotiate using an extended CNP that supports **partial fulfillment across multiple contractors**, because the PoC scenario requires it (see §2 rationale).

### 5.1 Manager Election (fixes duplicate-CFP race)

* Any agent that observes an unclaimed target (`TargetInfo.claimed == False`) and currently has no assignment of its own may attempt to become Manager by sending a `CFP` for that `target_id`.
* The **Message Broker** is the sole arbiter: it keeps `open_contracts: dict[target_id, contract_id]`. When it receives a `CFP`:
  * If `target_id` is **not** in `open_contracts` → register it (`open_contracts[target_id] = contract_id`), mark that agent as Manager for this `contract_id`, and forward the CFP to all other agents.
  * If `target_id` **is already** in `open_contracts` → silently drop the CFP (do not forward it). The sender does not become Manager.
* An open contract expires after `CONTRACT_CLAIM_TTL_TICKS` (default `10`) ticks if it hasn't resulted in `hp_remaining <= 0` for that target, freeing it up for a new CFP. This prevents a stalled negotiation from permanently locking a target.
* This arbitration is race-free specifically *because* the Broker and all agents share one event loop (§1) — there is no true concurrency, only interleaving at `await` points, so a dict lookup-then-set inside the Broker's message handler is atomic in practice.

### 5.2 The Utility Function (normalized)

```python
payload_score = min(1.0, jet.payload_remaining / target.hp_remaining)   # capped at 1.0
distance_score = euclidean_distance(jet.position, target.position) / MAX_GRID_DISTANCE

U = (w1 * payload_score) - (w2 * distance_score)
```

* `MAX_GRID_DISTANCE = sqrt(GRID_WIDTH**2 + GRID_HEIGHT**2)` (≈141.42 for a 100×100 grid) — normalizes distance into `[0, 1]`.
* `payload_score` is capped at `1.0` so a jet with much more payload than required doesn't get an unbounded bonus over a jet that fits the requirement exactly.
* `w1 = 1.0`, `w2 = 0.5` (tunable), now meaningful because both terms are in `[0, 1]`.
* **A jet only bids if `payload_remaining > 0`.** There is no separate "zero out if underfilled" rule — a jet with less payload than required is expected to bid; it simply offers less (see `payload_offered` below). This is what makes multi-winner contracts possible.
* `payload_offered = min(jet.payload_remaining, target.hp_remaining)` — never offer more than what's needed or more than what you have.
* **Tie-break:** if two bids have equal `bid_utility` (to floating point tolerance `1e-9`), the bid with the lexicographically smaller `agent_id` wins.

### 5.3 CFP → BID → ACCEPT/REJECT Flow (multi-winner)

1. Manager broadcasts `CFP` (`target_id`, `hp_remaining`, `contract_id`) via the Broker.
2. Each other idle agent that can see the target computes a bid and replies with a `BID` (`bid_utility`, `payload_offered`) addressed to the Manager, or does not reply if `payload_remaining == 0`.
3. Manager collects bids until `BID_TIMEOUT_SECONDS` (default `0.5`s of wall-clock, or `BID_TIMEOUT_TICKS = 3` ticks — whichever fires first) elapses.
4. Manager sorts collected bids by `(bid_utility desc, agent_id asc)` and greedily accepts:
   ```python
   remaining = target.hp_remaining
   winners = []
   for bid in sorted_bids:
       if remaining <= 0:
           break
       winners.append(bid)
       remaining -= bid.payload_offered
   ```
5. Manager sends `ACCEPT` to each winner (echoing the `payload_offered` it is expected to honor) and `REJECT` to every other bidder.
6. **If `remaining > 0` after exhausting all bids** (not enough total payload responded), the Manager keeps the contract open and re-broadcasts a follow-up `CFP` with the updated `hp_remaining` on a later tick (bounded by the `CONTRACT_CLAIM_TTL_TICKS` in §5.1).
7. Winning contractors (and the Manager itself, if it also bid) move toward the target and, once within `STRIKE_RANGE`, submit `STRIKE` intents until their committed `payload_offered` is exhausted or the target's `hp_remaining` reaches 0.
8. **Simplifying assumption (explicit, not a gap):** an `ACCEPT` is always honored — contractors do not retract bids or fail mid-flight in this PoC. Handling contractor failure/retraction is out of scope.

## 6. The "Hybrid Bridging" Loop (Execution Flow)

Single event loop, one iteration = one tick:

1. **Broadcast State:** The Bridge builds a fresh `Observation` per jet from the current `World` state (including up-to-date `TargetInfo.claimed` from the Broker's `open_contracts`) and calls `agent.set_observation(obs)` directly (no queue — this is a same-event-loop, non-blocking handoff).
2. **Negotiate:** For every currently-`IDLE` agent, the Bridge calls `await agent.scan_and_negotiate()`, all concurrently (`asyncio.gather`), bounded by `MAX_TICK_WAIT_SECONDS` (default `3.0`s — at least `BID_TIMEOUT_SECONDS * MAX_CFP_ROUNDS`) via `asyncio.wait_for`. Negotiation internally exchanges `CFP`/`BID`/`ACCEPT`/`REJECT` via the Broker per §5.3, and may take multiple `BID_TIMEOUT_SECONDS` rounds (bounded by `MAX_CFP_ROUNDS`) if a target's requirement isn't covered on the first pass. If the overall per-tick timeout fires first, the Bridge proceeds anyway — any agent not yet committed simply produces `IDLE` in the next step, which is a safe default, not an error.
3. **Collect Intents:** The Bridge calls each agent's synchronous `agent.decide_intent() -> ActionIntent` and gathers them into `intents: dict[agent_id, ActionIntent]`. This is a **pull**, not a queue read — `decide_intent()` has no side effects other than decrementing the agent's local `committed_payload` counter when it emits a `STRIKE`.
4. **Physics Tick:** The Bridge calls `World.step(intents)` synchronously: applies `MOVE` (clamped by `JET_SPEED` per tick), applies `STRIKE` (decrements `payload_remaining` by 1 and the target's `hp_remaining` by 1, only if the jet is within `STRIKE_RANGE`), and updates the `DataCollector`.
5. **Contract Cleanup:** The Bridge calls `broker.close_contract(target_id)` for any target whose `hp_remaining` just reached `0`, and `broker.expire_stale_contracts(model.tick)` to clear any stalled negotiations.
6. **Termination Check:** `World.step()` sets `World.running = False` if every target has `hp_remaining <= 0`. The Bridge stops after this tick if so.
7. Repeat from step 1 until `World.running == False`.

## 7. Implementation Phases for the LLM

Each phase is documented as its own standalone file under `docs/phases/`, written to be given to an LLM as a single, self-contained prompt (it restates the scenario, schemas, and constraints it needs — it does not assume the LLM has seen prior phases' conversations, only their code output). Build and validate strictly in order:

* **Phase 0 — Project Setup:** `docs/phases/Phase_0_Project_Setup.md`
* **Phase 1 — Schemas & World Scaffolding:** `docs/phases/Phase_1_Schemas_and_World.md`
* **Phase 2 — Message Broker & Agent Loop skeleton:** `docs/phases/Phase_2_Message_Broker_and_Agent_Loop.md`
* **Phase 3 — CNP Negotiation Protocol:** `docs/phases/Phase_3_CNP_Negotiation.md`
* **Phase 4 — Hybrid Bridge Integration:** `docs/phases/Phase_4_Hybrid_Bridge.md`
* **Phase 5 — Metrics, Termination & Scenario Validation:** `docs/phases/Phase_5_Metrics_Termination_Validation.md`

See `00_Design_Review_Findings.md` for the full rationale behind every change from the original document.
