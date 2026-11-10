# Phase 59 — CNP Liveness & Mission Completion

> **LLM implementation prompt:** Assume Phases 0–58 are complete. Read `docs/23_Project_State_Audit_and_Next_Phases_Roadmap.md` §3 first; it contains the measured failure table this phase exists to fix. Reproduce the failures with the Phase 57 runner before changing anything, and keep the before/after outcome table in the PR. Fix causes in the order below; re-run the sweep after each part, because each one changes what the next one has to solve. Do not tune the generator or the presets to make the numbers look better.

## Goal

A generated mission must end for a reason: solved, aborted, or wiped out. It must not sit at the tick cap with jets circling, and the swarm must not burn thousands of empty auctions while it waits.

## Evidence (32 generated missions, default 200×200 map, 1000-tick cap)

| Preset | Solved | Hit tick cap | Aborted | Wiped out |
|---|---|---|---|---|
| TRAINING | 8 / 8 | 0 | 0 | 0 |
| STANDARD | 5 / 8 | 3 | 0 | 0 |
| CONTESTED | 1 / 8 | 7 | 0 | 0 |
| NIGHTMARE | 0 / 8 | 8 | 0 | 0 |

All 32 scenarios were reported **feasible** by the static analyser. Every failure is a stall at the tick cap; not one mission ended by abort or wipe-out.

What the stalled runs look like at tick 1000:

* Jets are alive and flying, mostly `IDLE`, some stuck `CONTRACTOR`.
* The weapons that could kill the remaining targets are gone. Typical: every surviving jet has `AGM65: 0`, some still carry JDAMs, and the live targets are `MOBILE_RADAR`s that only AGM-65 can kill.
* Fuel is nowhere near bingo. Airframes carry 2000 / 6000 / 8000 ticks of fuel, so a jet with nothing useful to do loiters for longer than the tick cap.
* Negotiation is spinning. One NIGHTMARE run logged **23,311 CFPs and 23,311 awards against 85 bids** in 1000 ticks. A typical stalled run logs 4,000–13,000 CFPs. Roughly one `reservation_release` per five CFPs: a manager opens a contract, runs all `MAX_CFP_ROUNDS` with no bidders, releases, and does it again next tick.
* One run destroyed 12 of 12 targets and still did not finish.

## Part A — stop the empty-auction loop

Cause, in `JetAgentRuntime.scan_and_negotiate`: a jet with **zero** payload skips the suitability check and may become a manager for any unclaimed target. With no capable bidder left, it re-runs a full five-round auction every tick, for every such jet, forever.

1. Per-jet, per-target **backoff**: after an auction ends with an uncovered shortfall, do not reopen that target for `CFP_RETRY_BACKOFF_TICKS`, doubling up to a cap. Reset when the jet's knowledge changes in a way that could matter: a new jet appears on the net, a contact update for that target, or the jet's own payload changes.
2. Do not run rounds 2–5 when round 1 collected zero bids and nothing changed. `MAX_CFP_ROUNDS` exists for partial coverage, not for retrying silence.
3. Do not record an `award` negotiation event with an empty `accepted` list; record one `no_bids` event per backoff period instead. This also stops the negotiation log, the trace and `summarize` from growing by tens of thousands of entries.

## Part B — a jet with nothing useful left goes home ("winchester")

Add a rule next to `bingo_fuel_reached`: if a jet holds no commitment and none of its remaining weapons can damage any target it knows to be alive (reuse the suitability check behind `compute_bid`), it returns to base. Re-evaluate when its world view gains a target it *can* damage before it has landed.

This is what turns "circling until the cap" into a real `ABORTED` outcome when the swarm is out of options, and it frees the stalled runs for an honest verdict.

## Part C — find out where the weapons went

The analyser says the roster can cover every objective; the swarm runs dry anyway. Before fixing anything here, measure. For each stalled seed, from the trace and `missile_events`, produce a per-target ledger: rounds fired by weapon type, hits, misses, decoyed, intercepted, overkill, and rounds fired at targets a cheaper weapon could have killed.

Then fix what the ledger shows. The candidates, in the order to check them:

1. **Scarce weapons spent on targets that did not need them** (an AGM-65 on a bunker a JDAM would kill). If confirmed: add a scarcity term to the bid so a weapon that is the only answer to some known live target is priced higher for other targets.
2. **Misses and intercepts not followed up**, leaving a target at 1 HP with the claim still held. Phase 62 Part A fixes the reporting; here make sure the contractor re-requests rather than silently going idle.
3. **Overkill**: several jets releasing at a target the first hit already destroyed. The world keeps `damage_reservations` for in-flight rounds but the CFP always sends `reserved_hp=0`. Wiring that through is Phase 63 Part B; pull it forward if the ledger says this is the main loss.
4. **Feasibility optimism**: the analyser counts nominal damage and ignores delivery probability. If the swarm really cannot do the mission with expected losses, the analyser is wrong, not the swarm: add an expected-damage check and report `feasible_nominal` and `feasible_expected` separately.

## Part D — finish when the job is done

Investigate the 12-of-12 run (NIGHTMARE seed 3): find which required objective was still open and why nobody was working on it. The likely area is DESTROY subjects that are not in `model.targets` (tanks, corvettes, AAA sites) and how they enter `world_view` and the scan. Add the missing path and a regression test built from that seed.

## Part E — stuck contractor

Some stalled runs hold jets in `CONTRACTOR` from tick 200 to tick 999. Add a progress watchdog: a contractor that has not closed distance to its release gate or released a weapon for `CONTRACT_PROGRESS_TTL_TICKS` drops the commitment and reports it (Phase 62 `REPORT ABORTED`; until then, close the claim). Find the root cause for at least one seed before relying on the watchdog.

## Tests

* Empty-auction: three zero-payload jets and one live target for 200 ticks produce a bounded, small number of CFPs (assert an explicit ceiling).
* Backoff resets when a capable jet joins.
* Winchester: jet with only JDAMs and only a `MOBILE_RADAR` known alive begins RTB; a jet whose weapons still fit a live target does not.
* Regression seeds: one test per fixed stalled seed asserting the mission **terminates** before the cap with an explicit outcome.
* Sweep guard (marked `slow`): 8 seeds per preset; TRAINING solve rate 100%, zero `TICK_CAP` outcomes in any preset.

## Acceptance criteria

1. Zero `TICK_CAP` outcomes across a 50-seed sweep per preset.
2. CFP count per run is within a small multiple of the target count in every run.
3. Solve rate per preset is reported before and after, with the weapons ledger attached for runs that still fail.
4. TRAINING stays at 100%; STANDARD does not regress.

## Non-goals

* No generator or preset retuning.
* No new protocol messages (Phase 62) and no multi-task queues (Phase 64).
