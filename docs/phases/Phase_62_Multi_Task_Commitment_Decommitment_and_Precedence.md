# Phase 62 — Multi-Task Commitment, Decommitment & Task Precedence

> **LLM implementation prompt:** Assume Phases 0–61 are complete. Read `docs/23_Project_State_Audit_and_Next_Phases_Roadmap.md` §5 and write `docs/25_Task_Allocation_Beyond_Single_Contract_Design_Document.md` first, following the format of doc 20, and have it reviewed before coding. This phase changes the shape of an agent's commitment state, which most of `agents.py` and many tests depend on. Keep the single-contract behaviour available behind a switch so Phase 64 can compare the two on the same seeds. Use the Phase 57 runner as the judge.

## Goal

A jet plans more than one strike ahead, can give a task back when a better use of it appears, and the swarm knows that some targets should die before others.

## Why

Today each jet holds exactly one commitment (`committed_target_id`, `committed_payload`, `committed_weapon_type`) and one role. Consequences visible in the code and in runs:

* **One at a time, nearest first.** A jet with four JDAMs and three bunkers along its route wins one, flies it, goes `IDLE`, and only then bids again, from wherever it ended up. Nobody plans a tour. Bids are priced as if each task started from the jet's current position, which is wrong for every task after the first.
* **Commitment is absolute.** Once `CONTRACTOR`, a jet ignores all CFPs (`_maybe_bid` returns unless `IDLE`). A jet two minutes from a low-value bunker cannot be re-tasked to the SAM that just started killing its wingmen. The only exits are target dead, rounds expended, or RTB.
* **No ordering between tasks.** The mission brief knows which objectives are required, but nothing tells the swarm that the SAM covering a bunker should be struck before the bunker. Phase 61's survival term discourages the dangerous bid; it does not create the enabling one.
* **Managing blocks bidding.** While a jet is `MANAGER` it cannot bid on another contract. In-tick negotiation hides this today; with Phase 60 latency it becomes a real cost.

## Part A — task queue per jet

Replace the single commitment with an ordered list of commitments (target, weapon, rounds). `decide_intent` works on the head of the list. Rounds committed anywhere in the list are excluded from new offers (the "exclude already committed ammunition" rule from Phase 45 Part F, which today only has one commitment to exclude).

Cap the list (`MAX_COMMITMENTS_PER_JET`, default 3). The world, the broker and the observation contract do not change.

## Part B — bids priced by insertion

A jet may bid while it holds commitments, as long as it has uncommitted suitable rounds and fuel. Its bid is the **marginal** cost of inserting the new target into its queue at the best position: extra distance, extra time, extra exposure (Phase 61 Part A2 along the changed path), and the fuel check for the whole tour plus recovery.

This is the sequential single-item auction with insertion heuristic. It needs no new message type. Do not implement bundle bidding or consensus (CBBA) in this phase; note it in the design doc as the next step if Phase 64 shows insertion bids leave a lot on the table.

## Part C — leveled decommitment

A contractor may give a task back:

* Triggers: a new CFP whose marginal utility exceeds the held task's by more than `DECOMMIT_MARGIN`; fuel or exposure for the held task crossing a threshold; a defensive abort.
* Mechanism: `REPORT ABORTED` to the manager (Phase 60 Part A), who re-auctions. The contractor pays a `DECOMMIT_PENALTY` in its next bids for a number of ticks, so the swarm does not thrash.
* Guard: no decommit inside the final run-in (within the weapon's release sequence), and at most one per jet per `DECOMMIT_COOLDOWN_TICKS`.

Measure thrash explicitly: count of decommits per mission, and tasks that changed hands more than twice.

## Part D — precedence between targets

Derive, from what a jet knows, which threats cover which targets: target T is *covered by* threat S if T lies inside S's known engagement envelope. Then:

* the CFP ordering key from Phase 61 Part D gains a first term: uncovered-or-enabling targets before covered ones;
* a bid on a covered target carries the covering threat ids; the manager prefers an award set in which the covering threat is assigned to someone with an earlier estimated time on target, or defers the covered target (short backoff) if nobody has taken the threat yet.

Soft precedence only: a covered target may still be struck with a standoff weapon from outside the envelope, and Phase 61's exposure term already prices that correctly.

## Part E — manager is not a role

Split "I am running an auction for target X" from the jet's flying role. A jet can manage one or more open contracts and still bid, fly and strike. `role` keeps the flying states only; contract bookkeeping moves to a per-contract record (the `managed_contracts` dict already half does this).

## Tests

* Tour: one jet, three reachable bunkers, enough rounds → all three assigned to it in one tour whose length is shorter than nearest-first from scratch each time.
* Marginal pricing: the second bid of a committed jet is lower than the same bid from an idle jet at the same position, by the detour cost.
* Rounds accounting: offers never exceed carried minus committed, across the whole queue.
* Decommit: higher-value CFP arrives mid-transit → task handed back, manager re-awards, penalty visible in next bid; not triggered inside the run-in; cooldown respected.
* Precedence: bunker under a known SAM envelope and the SAM both unclaimed → SAM is opened first; with a standoff-capable jet available the bunker may be taken from outside the envelope.
* Manager-not-a-role: a jet managing a contract wins a different contract in the same tick.
* Switch off: single-contract behaviour reproduces the pre-phase equivalence fixture.

## Acceptance criteria

1. Design doc 25 written and linked from the README.
2. On the Phase 57 sweep (at least 50 seeds per preset), compared with single-contract mode: median mission ticks down, total distance flown down, solve rate not lower, jets lost not higher. Report all four.
3. Decommits per mission bounded; no task changes hands more than `N` times (state `N` in the design doc).
4. No scheduling-order dependence: same result under shuffled in-tick delivery.

## Non-goals

* No CBBA / bundle consensus, no centralised optimiser (Phase 64 uses one only as a yardstick).
* No formation flying or escort behaviour (Phase 63).
