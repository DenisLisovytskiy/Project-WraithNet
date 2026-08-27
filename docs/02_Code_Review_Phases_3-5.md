# Code Review: Phases 3–5 (CNP Negotiation, Hybrid Bridge, Metrics & Validation)

Reviewed against: `docs/phases/Phase_3_CNP_Negotiation.md`, `docs/phases/Phase_4_Hybrid_Bridge.md`, `docs/phases/Phase_5_Metrics_Termination_Validation.md`.

Files reviewed: `wraithnet/agents.py`, `wraithnet/broker.py` (Phase 3 extensions), `wraithnet/bridge.py`, `wraithnet/main.py`, `wraithnet/metrics.py`, `wraithnet/world.py` (Phase 5 `DataCollector` additions), `tests/test_cnp.py`, `tests/test_bridge.py`, `tests/test_scenario.py`. This builds on [`docs/01_Code_Review_Phases_0-2.md`](01_Code_Review_Phases_0-2.md) — nearly every item from that review has since been fixed in the code (see §5).

**Verification performed:**
- Full suite: `pytest -q` → **61/61 passed**. Re-ran with `-W error` → still 61/61, zero warnings.
- Wrote and ran a standalone repro script (not part of the suite) directly against `wraithnet.agents.JetAgentRuntime` to force a scenario the fixed PoC numbers never naturally trigger: a manager whose own self-bid can't fully cover the target, plus a second bidder that only becomes available *after* round 1's CFP window closes. This confirms a real, currently-untested bug in multi-round re-negotiation (see §1 below) — it is masked by the specific numbers in `config.SCENARIO`, not actually absent from the code.

**Overall assessment: the "happy path" for the fixed PoC scenario is solid and all 61 tests genuinely pass** — manager election, single-round multi-winner bidding, tie-breaks, the hybrid tick loop, termination, and metrics export are all correctly implemented and well-tested for the one-round case. However, there is **one significant latent bug in the CNP re-negotiation path (§1)** that silently breaks down whenever a contract isn't fully resolved in its first bidding round. It doesn't currently manifest because `Target_A`'s numbers happen to always resolve in round 1, but it's a real correctness gap in the general protocol the design doc claims to implement ("bounded retries" / multi-round negotiation), and it can permanently strand an agent. I'd fix this before calling Phase 3 done, even though it isn't blocking the current scenario's tests.

---

## 1. [Significant — fix before sign-off] Multi-round CNP re-negotiation is broken: a manager's own self-commit silently breaks bid collection in later rounds, and `_resolve_contract` forgets what was already pledged

### Reproduction

I ran this against the real code (not a mock):

```python
# Target requires hp=10. Manager can only self-bid for 1 (nowhere near enough).
# Helper only gets an Observation set *after* round 1's 0.05s window closes,
# so it can't participate in round 1 — it must be picked up in round 2.
target = TargetInfo(target_id="T", position=(0.0, 0.0), hp_required=10, hp_remaining=10, claimed=False)
manager.set_observation(obs("Manager", (0.0, 0.0), payload=1, target))
await manager.scan_and_negotiate()          # round 1 fires immediately
# ...0.02s later, mid-round-1-wait...
helper.set_observation(obs("Helper", (0.0, 0.0), payload=9, target))  # should win round 2
```

Result after the manager's negotiation coroutine returns and everything settles:

```
manager.role                          = CONTRACTOR
manager.managed_target_id             = None
manager.committed_target_id/payload   = T 1        # only ever secured 1 of the 10 needed
helper.role                           = CONTRACTOR_PENDING   # <-- permanently stuck!
helper.committed_target_id/payload    = None 0
broker.open_contracts                 = {}          # contract force-closed, still unfulfilled
manager cfp_round reached             = 5           # burned all 5 rounds and gave up
```

`Helper` never receives an `ACCEPT` or `REJECT` for the `BID` it sent in round 2. Since `decide_intent()` only ever looks at `committed_target_id` (never set) and `scan_and_negotiate()`/`_maybe_bid()` both require `role == "IDLE"` to do anything, **`Helper` is now permanently inert for the rest of the simulation** — it will never move, strike, manage, or bid again. Meanwhile `Manager` burns through all `MAX_CFP_ROUNDS` and gives up having secured only its own tiny self-bid, even though a perfectly good second bidder was available.

### Root cause (two compounding bugs in `wraithnet/agents.py`)

**(a) The BID-collection guard in `handle_message` depends on `role`, but `_resolve_contract` changes `role` away from `"MANAGER"` the moment the manager wins its own self-bid — even mid-negotiation:**

```python
elif (
    message.message_type == "BID"
    and self.role == "MANAGER"          # <-- breaks as soon as self-commit happens
    and message.target_id == self.managed_target_id
):
    self._collected_bids.append(message)
```

`_commit()` unconditionally sets `self.role = "CONTRACTOR"`. It's called on the manager itself inside `_resolve_contract` whenever the self-bid is among the round's winners — which is common, since the manager is usually well-placed to hit the target it's managing. But `self.managed_target_id` / `self.open_contract_id` are *not* cleared at that point (they're only cleared once the contract is fully resolved or `MAX_CFP_ROUNDS` is hit), so the code goes on to broadcast further CFP rounds while `role` no longer equals `"MANAGER"`. Every `BID` that arrives during those later rounds fails the `role == "MANAGER"` check and is **silently dropped** (not even logged) — leaving the sender stuck in `"CONTRACTOR_PENDING"` forever.

**(b) `_resolve_contract` recomputes `remaining` from the target's raw, unchanged `hp_remaining` every round, instead of tracking what's actually still needed after prior rounds' accepted bids:**

```python
async def _resolve_contract(self) -> None:
    ...
    remaining = next(
        t for t in self.latest_observation.known_targets
        if t.target_id == self.managed_target_id
    ).hp_remaining          # <-- always re-reads the ORIGINAL full hp_required (e.g. 10),
                             #     never "10 minus what round 1 already secured"
```

The `hp_needed` parameter that's correctly threaded into `_broadcast_cfp_and_collect()` (and correctly announced in the CFP's own `hp_remaining` field for *other* agents to see) is never consulted here — `_resolve_contract` re-derives its own, different, stale "how much do we still need" number from scratch every round. Since `self._collected_bids` is also reset to `[]` at the top of every round (by design — it's supposed to only hold *that round's* respondents), earlier rounds' accepted contributions are never subtracted from `remaining` on subsequent rounds. Combined with bug (a) already preventing any real second-round bids from arriving, the manager ends up re-committing the same self-bid on every remaining round without making any progress, until it gives up at `MAX_CFP_ROUNDS`.

### Why the test suite doesn't catch this

`tests/test_cnp.py::test_multi_winner_partial_fulfillment_for_target_a` only exercises the **single-round** case (all three jets have their `Observation` set up-front, so the round-1 CFP always fully covers `Target_A`'s `hp_required=5` — either via `{Jet_2, Jet_3}` or `{Jet_1, one of Jet_2/Jet_3}`). No test in `test_cnp.py`, `test_bridge.py`, or `test_scenario.py` forces a shortfall in round 1, so the `_broadcast_cfp_and_collect` → `_resolve_contract` recursive re-negotiation branch (`if remaining > 0 and self._cfp_round < MAX_CFP_ROUNDS:`) is **never actually exercised** by the suite. It's also why this survives undetected in the full scenario: `config.SCENARIO`'s numbers (payloads 1/4/4 against requirements 5/1) happen to always let round 1 fully resolve `Target_A` given the deterministic single-threaded scheduling, so the broken code path simply never runs in practice today. This is a case where 100% of the current tests passing does not mean the protocol the design doc describes is actually correct — only that the one scenario currently in `config.py` never triggers the buggy branch.

### Recommended fix

1. **Decouple "am I still the negotiating manager for this round" from `role`.** The `BID`-collection guard in `handle_message` should key off `self.managed_target_id is not None and message.target_id == self.managed_target_id`, not `self.role == "MANAGER"`. That state already survives a self-commit correctly; `role` should not double as this flag.
2. **Track cumulative shortfall explicitly instead of re-deriving it from a stale `Observation`.** `_resolve_contract` should accept (or store) the `hp_needed` that this round was opened with, and compute `remaining = hp_needed - sum(winning bid payloads)` — not re-fetch `target.hp_remaining` from `self.latest_observation`, which never changes mid-negotiation (no real strikes have happened yet within the same tick).
3. **Have `compute_bid`/`_maybe_bid`/the self-bid block use the CFP's actual announced ask, not the bidder's own raw `target.hp_remaining`.** Right now `_maybe_bid` computes `payload_offered = min(payload_remaining, target.hp_remaining)` using the *bidder's own* `Observation.known_targets` lookup, completely ignoring `cfp.hp_remaining` (the number the manager actually announced for this round). In round 1 these two values are always identical, so this is currently invisible — but from round 2 onward they diverge, and a bidder could pledge more than the manager actually still needs. Pass `cfp.hp_remaining` (or the resolved `hp_needed`) explicitly into the bid computation instead of re-deriving it.
4. **Add a test that forces a round-1 shortfall** (a manager + a bidder deliberately withheld until after round 1's timeout, as in the repro above) and assert: the withheld bidder eventually reaches `role == "CONTRACTOR"` with the correct `committed_payload`, the manager's total committed + accepted payload sums to at least `hp_required`, and no agent is left in `"CONTRACTOR_PENDING"` once the contract is closed.

This is present in both the implementation and in `docs/phases/Phase_3_CNP_Negotiation.md` itself (the spec's own `_resolve_contract`/`_maybe_bid` pseudocode has the identical issue) — it's a design bug that got faithfully implemented, not an implementer error. Worth patching both the doc and the code together.

## 2. [Related, lower severity] `MAX_TICK_WAIT_SECONDS` is too small to ever let `MAX_CFP_ROUNDS` actually complete within one tick

```python
BID_TIMEOUT_SECONDS: float = 0.5
MAX_TICK_WAIT_SECONDS: float = 1.0
```
```python
MAX_CFP_ROUNDS = 5  # wraithnet/agents.py
```

Worst case, a single manager's negotiation could take up to `5 × 0.5s = 2.5s`, but `WraithNetBridge._tick()` cancels the whole `scan_and_negotiate()` gather after `MAX_TICK_WAIT_SECONDS = 1.0s` via `asyncio.wait_for`. If that cancellation lands mid-`asyncio.sleep` inside `_broadcast_cfp_and_collect`, the code returns early **without ever calling `_resolve_contract()`** — so `_commit()` is never called, no `ACCEPT`/`REJECT` is sent, and the contract stays open in `broker.open_contracts` (until `expire_stale_contracts` eventually clears it after `CONTRACT_CLAIM_TTL_TICKS=10` ticks). Worse, the manager's own `role` stays `"MANAGER"` indefinitely, which excludes it from `idle_agents` in `_tick()` (`role == "IDLE"` filter) — so it can **never be re-selected to retry `scan_and_negotiate()` on a later tick**, even after the broker frees up the target. This agent becomes a permanent zombie, similar in effect to the `CONTRACTOR_PENDING` zombie in §1.

This is currently unreachable in the fixed scenario (round 1 always finishes in ~0.5s, well under the 1.0s budget), but it's a config-level inconsistency worth resolving either way:
- If negotiation is meant to be a hard, bounded, single-tick operation: set `MAX_TICK_WAIT_SECONDS >= BID_TIMEOUT_SECONDS * MAX_CFP_ROUNDS` (e.g. `3.0`) so a manager can always exhaust its retries within one tick.
- If negotiation is meant to be able to span multiple ticks (per the design doc's A1 "semi-synchronous" model): a cancelled/interrupted manager needs a path back into `idle_agents` on a later tick — e.g., don't gate `idle_agents` purely on `role == "IDLE"`, or add a recovery path that resets a stuck `"MANAGER"`/`"CONTRACTOR_PENDING"` agent back to `IDLE` once its contract's TTL expires.

## 3. [Good practice, worth calling out] The self-bid mechanism (Manager bidding on its own contract) is a genuinely good design catch

```python
# Self-bid: the manager may also strike its own managed target. Without
# this, a jet that spots and manages a target it's well-placed to hit
# itself would never fire, which contradicts minimizing total distance.
if obs.payload_remaining > 0:
    ...
```

This isn't in the letter of the original design doc's CNP description but is clearly the right call — without it, `Jet_1` (which is the one that discovers and manages `Target_A` in the canonical run) would never contribute its own missile toward the very target it's closest to, undermining the "minimize total distance" objective. Good implementation judgment; no action needed.

## 4. [Minor] `docs/01_Code_Review_Phases_0-2.md` item 3 is still outstanding: `tests/__init__.py` is still missing

Checked again for this review — the file still doesn't exist. Harmless for `pytest` (all 61 tests still collect and run fine), but it's a repeated, trivial spec-compliance gap from the Phase 0 layout. Add the empty file so it stops recurring in future reviews.

## 5. [Confirmed fixed since the last review] Everything else from `01_Code_Review_Phases_0-2.md` has been addressed

Verified directly in the current code:
- Item 1 (Mesa `step()`-shadowing regression test) — present: `test_step_is_not_shadowed_by_mesas_internal_wrapped_step` in `test_world.py`. ✅
- Item 1's doc/comment fix (`# Broker fills this correctly from Phase 4 onward`) — the comment in `world.py`'s `build_observation` is gone entirely now (superseded by the Phase 4 `broker` parameter actually being wired in), so the stale reference is moot. ✅
- Item 2 (zero-payload-in-range strike test) — present: `test_strike_with_zero_payload_in_range_is_noop`. ✅
- Item 4 (`config.py` rationale comment for `hp_required=5`) — present, word-for-word as suggested. ✅
- Item 5 (redundant `or distance == 0`) — removed; `_apply_move` now just checks `distance <= config.JET_SPEED`. ✅
- Item 6 (`math.hypot` vs `math.dist` inconsistency) — `_apply_move` now uses `math.dist` throughout, matching `_apply_strike` and the tests. ✅
- Item 7 (`seed=` → `rng=` `FutureWarning`) — `WraithNetModel.__init__` now calls `super().__init__(rng=seed)`. ✅
- Item 8 (schema hardening) — `TargetInfo._check_hp_remaining_not_above_required` and `Message._check_required_fields_for_message_type`'s non-broadcast check for `BID`/`ACCEPT`/`REJECT` are both present in `schemas.py`. ✅
- Item 3 (`tests/__init__.py`) — **still open**, see §4 above.

## 6. [Trivial] Stale line in `README.md`

```
`wraithnet/main.py` will become the simulation entrypoint once Phases 4–5 are complete. It is a placeholder in early phases and is not runnable yet.
```

Phases 4–5 are now done and `main.py` is a real, runnable entrypoint (`python -m wraithnet.main`). Update this line to describe how to actually run the simulation now, e.g.:

```markdown
## Running the simulation

python -m wraithnet.main
```

## Acceptance-criteria check against each phase doc

| Phase | Criterion | Status |
|---|---|---|
| 3 | Manager election has no duplicate-manager race | ✅ `test_manager_election_no_duplicate_contracts` |
| 3 | Multi-winner partial fulfillment works for `Target_A` (single round) | ✅ `test_multi_winner_partial_fulfillment_for_target_a` |
| 3 | Tie-break is deterministic (lexicographic `agent_id`) | ✅ `test_tie_break_prefers_lexicographically_smaller_agent_id` |
| 3 | `decide_intent` resets cleanly on destroyed target / exhausted payload | ✅ |
| 3 | Multi-round re-negotiation (shortfall → second CFP round) actually works | ❌ **broken, see §1** — untested and non-functional beyond round 1 |
| 3 | `compute_bid` is a pure function, no side effects, no asyncio | ✅ |
| 4 | Bridge advances ticks until `model.running is False` | ✅ `test_bridge_runs_scenario_to_completion` |
| 4 | `build_observation` reflects live broker claims | ✅ `test_build_observation_reflects_broker_claims` |
| 4 | Contracts are cleaned up on target destruction / staleness | ✅ (`_tick`'s step 5) |
| 5 | `DataCollector` wired up with tick/distance/missiles/targets-destroyed reporters | ✅ |
| 5 | `metrics.summarize` / `metrics.export_results` implemented and tested | ✅ `test_export_results_writes_csv` |
| 5 | Full scenario resolves; `Target_A` requires ≥2 contributors; `Target_B` solo | ✅ `test_scenario.py` (4/5 tests) |
| 5 | Total distance traveled is near-optimal | ✅ `test_total_distance_traveled_is_near_optimal` |

## Recommendation

**Fix §1 before considering the CNP layer complete.** It's the one item in this review that represents an actual protocol-correctness bug (not a style nit or doc gap), it can permanently strand an agent, and the fact that it's currently invisible in `pytest -q` is precisely why it's worth calling out explicitly rather than leaving for a future scenario change to discover the hard way. §2 should be resolved alongside it (same root cause: rounds spanning more time/ticks than the state machine correctly supports). Items 4 and 6 are trivial cleanups; item 3 (self-bid) needs no changes, just noted as good design.
