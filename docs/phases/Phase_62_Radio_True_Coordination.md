# Phase 62 — Radio-True Coordination (Remove the Omniscient Shortcuts)

> **LLM implementation prompt:** Assume Phases 0–61 are complete. Read `docs/23_Project_State_Audit_and_Next_Phases_Roadmap.md` §5 and the current `wraithnet/broker.py`, `wraithnet/bridge.py` and `wraithnet/agents.py` before coding. The README says there is no central commander and that jets coordinate only over a jammable radio. Four mechanisms currently bypass the radio. Replace each with a message-based equivalent, one part at a time, each behind a scenario/config switch so Phase 61 can compare old and new. Run the Phase 57 seed sweep after every part and record the outcome table in the PR.

## Goal

Every piece of coordination state a jet acts on must have reached it through a message that the broker could have dropped. After this phase, jamming and range affect *all* coordination, not only CFP/BID/ACCEPT traffic.

## The four shortcuts today

| # | Shortcut | Where | Why it is not decentralised |
|---|---|---|---|
| 1 | Global claim registry | `MessageBroker.open_contracts`, `try_open_contract`, `is_claimed`; `TargetInfo.claimed` filled from it in `build_observation` | A single shared lock decides who manages a target. A jammed jet can take the lock (the call is not routed) and every jet reads it instantly, jammed or not. |
| 2 | Contractor-loss notification | `WraithNetBridge._notify_managers_of_losses` calls `manager.notify_contractor_lost(...)` directly | The bridge sees ground truth and tells the manager the same tick, at any distance, through any jamming. |
| 3 | End-of-mission recall | `WraithNetBridge._tick` step 1e calls `agent.begin_rtb()` on every jet once `required_destroy_objectives_complete()` | Every jet learns the mission is over from the world model, not from what it saw or heard. |
| 4 | Direct model handle | `agent._model = self.model` in the bridge; used for the logical tick and the negotiation log | Agents hold a reference to the whole world. Nothing stops future code from reading it. |

Also missing from the protocol: there is no message from a contractor back to its manager. The README sequence diagram shows `DONE`, but `Message.message_type` has only `CFP`, `BID`, `ACCEPT`, `REJECT`, `DATALINK_CONTACT`, `RISK_UPDATE`. A contractor that runs out of committed rounds, loses the release envelope, or drops the task goes `IDLE` silently, and the target stays locked until the claim TTL expires.

## Part A — contract lifecycle messages

Add message types:

* `REPORT` (contractor → manager, direct): `status` one of `DONE`, `FAILED`, `ABORTED`, plus rounds expended and a reason code. Sent when the commitment ends for any reason (target destroyed, rounds expended with target alive, release unreachable, RTB, defensive abort).
* `RELEASE` (manager → broadcast): the manager gives up a target; anyone may reopen it.
* `HEARTBEAT` (contractor → manager, direct, every `CONTRACT_HEARTBEAT_TICKS`): carries position and rounds still committed.

Manager behaviour: on `REPORT FAILED/ABORTED` or on `CONTRACT_HEARTBEAT_MISS_LIMIT` missed heartbeats, treat the contractor as lost and re-auction the shortfall. This replaces shortcut 2. Remove the bridge call; the bridge keeps only runtime cleanup (stop task, delete runtime).

A jammed contractor now looks dead to its manager. That is the intended, realistic consequence: the manager may over-assign, and the award logic's overkill term has to cope. Measure it.

## Part B — claims by message, not by lock

Replace the broker registry with announced, expiring claims held in each jet's own memory:

1. A would-be manager broadcasts `CLAIM(target_id, contract_id, tick)`.
2. Each receiver stores `claims[target_id] = (manager_id, contract_id, tick)` with a TTL, exactly like `world_view` stores contacts.
3. Conflict rule, applied identically by everyone: the earlier `tick` wins; on equal tick the lexicographically smaller `manager_id` wins. A manager that hears a winning rival claim abandons its own (`RELEASE`), including mid-negotiation.
4. `scan_and_negotiate` consults the local claim table, never the broker.
5. `TargetInfo.claimed` stops being filled from broker state. Either remove the field or fill it from the receiving jet's own table.

Consequence to accept and test: two jammed-apart groups can each elect a manager for the same target and both attack it. When the groups reconnect, rule 3 resolves it. Duplicate effort under partition is the correct behaviour for a decentralised system; it must be bounded, not impossible.

The broker returns to what its docstring says it is: routing.

## Part C — link model

Routing is currently binary: delivered instantly to everyone, or dropped if either end is inside a jammer radius. Add to `MessageBroker._route`, all deterministic and scenario-configurable, defaults reproducing today's behaviour:

* `radio_range` — a message is delivered only if sender and receiver are within range. The broker needs positions: pass a `position_of(agent_id)` callable from the bridge, not the model.
* `relay` — optional one-hop relay through any unjammed jet in range of both, so the swarm can form a chain. Off by default.
* `latency_ticks` — messages are delivered at `tick + latency`. Requires a per-tick delivery queue in the broker instead of immediate `put`.
* `loss_probability` — per-delivery drop decided by `combat_rng.keyed_unit_float(combat_seed, "RADIO", message_id, receiver_id)`, so it is order-independent and does not disturb any combat roll.

With latency above zero the bid window must be measured in real ticks. `BID_TIMEOUT_TICKS` already exists; the in-tick yield budget in `_await_bid_window` becomes the special case `latency_ticks == 0`.

## Part D — mission-complete by knowledge

Replace shortcut 3: a jet goes home when *it* believes every briefed DESTROY subject is dead (from its own `world_view`, which datalink keeps updated) or when it has no usable weapons for anything it knows to be alive. Add a `MISSION_COMPLETE` broadcast so the first jet to conclude it tells the rest.

Keep the bridge-side omniscient recall behind a switch for the fixed PoC scenario and for A/B comparison.

## Part E — cut the model handle

Replace `agent._model` with two narrow injections: a `tick_clock` callable (already supported via `_tick_clock`) and a `negotiation_log` sink callable. Add a test that fails if `wraithnet/agents.py` references `WraithNetModel` outside `TYPE_CHECKING`, or if a runtime instance has an attribute holding the model.

## Tests

* Lifecycle: contractor expends its rounds on a surviving target → manager receives `REPORT FAILED` and reopens within N ticks, well before the claim TTL.
* Heartbeat: jam a contractor for longer than the miss limit → manager re-auctions; unjam → contractor's late `REPORT` is handled idempotently.
* Claims: two managers claim the same target on the same tick in two partitions; after reconnect exactly one contract survives and the loser's contractors are released.
* Link model: out-of-range jet receives nothing; relay delivers across two hops; latency 2 delays a CFP by exactly 2 ticks; loss is reproducible for a fixed seed and differs across seeds.
* Information boundary: Part E test.
* Regression: with all new switches off, the Phase 60 equivalence fixture is unchanged.

## Acceptance criteria

1. With the new switches on, `grep` finds no call from `bridge.py` into agent negotiation methods other than `set_observation`, `scan_and_negotiate`, `decide_intent`, and lifecycle start/stop.
2. `MessageBroker` holds no contract state.
3. Seed-sweep table (Phase 57) recorded for: baseline, each part enabled alone, all enabled. Solve rate with everything on at zero latency/loss/unlimited range is within a few points of baseline; any larger drop is explained.
4. README sequence diagram matches the implemented message set.

## Non-goals

* No encryption, spoofing, or adversarial message injection.
* No change to bid scoring (Phase 63) or to how many tasks a jet may hold (Phase 64).
