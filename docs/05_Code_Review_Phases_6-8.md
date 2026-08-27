# Code Review: Phases 6–8 (Weapon Systems, Fog of War, Dynamic Threats)

Reviewed against: `docs/phases/Phase_6_Weapon_System_and_Schema_Foundations.md`, `docs/phases/Phase_7_Sensor_Fusion_and_Fog_of_War.md`, `docs/phases/Phase_8_Dynamic_Threats_SAM_Sites.md`.

Files reviewed: `wraithnet/config.py`, `wraithnet/schemas.py`, `wraithnet/world.py`, `wraithnet/broker.py`, `wraithnet/agents.py`, `wraithnet/bridge.py`, `wraithnet/metrics.py`, `wraithnet/main.py`, and all of `tests/` (`test_schemas.py`, `test_world.py`, `test_cnp.py`, `test_fog_of_war.py`, `test_dynamic_threats.py`, `test_bridge.py`, `test_broker.py`, `test_agents.py`, `test_scenario.py`). This builds on [`docs/02_Code_Review_Phases_3-5.md`](02_Code_Review_Phases_3-5.md) — the multi-round CNP re-negotiation bug documented there is confirmed fixed (see §5).

**Verification performed:**
- Full suite: `pytest -q` → **104/104 passed** (77.8s — the wall-clock is dominated by `test_scenario.py`'s seven full async scenario runs, not a hang; verified by isolating a single slow-looking test, which completed in 10s).
- `python -m wraithnet.main` run to completion: `Simulation finished at tick 32`, `Solved: True (3/3 targets destroyed)`, `Total missiles fired: 7` (5 on Target_A + 1 on Target_B + 1 on SAM_Site_1, matching the design doc's numeric invariants exactly), no jets destroyed in this particular run.
- `grep -rP "payload_remaining\b"` (excluding `total_payload_remaining`/`payload_offered`) across `wraithnet/` and `tests/` → zero matches, satisfying Phase 6's acceptance criterion #1.
- Wrote and ran two standalone repro scripts (not part of the suite, deleted after use) directly against a live `WraithNetBridge` to stress-test the Manager Fallback path from Phase 8 with a scenario the fixed PoC's own geometry doesn't naturally trigger (a manager and one of its own accepted contractors destroyed in the *same* tick). This surfaced a real, currently-untested bug that can permanently strand a surviving agent — see §1.

**Overall assessment: this is a very high-fidelity implementation.** Nearly every function in `world.py`, `schemas.py`, and `agents.py` matches its phase doc's prescribed code close to verbatim, all three phases' acceptance criteria are met, and the full scenario resolves correctly and repeatedly. Test coverage is thorough and well-isolated (unit-level fog-of-war/SAM tests that don't need a live model, plus full-scenario integration tests). However, there is **one significant latent bug in the Phase 8 Manager Fallback path (§1)** — structurally analogous to the Phase 3 re-negotiation bug from the last review — that can permanently strand a healthy, contributing agent in `CONTRACTOR_PENDING` if a manager and one of its own contractors happen to die in the same tick. It doesn't manifest in the current fixed scenario (no run I observed produced simultaneous manager+contractor deaths), so `pytest -q` passing does not certify this path is correct. I'd fix this before calling Phase 8 done, and definitely before Phase 9 adds jamming (which will make dropped mid-negotiation agents *more* common, not less).

---

## 1. [Significant — fix before sign-off] `_notify_managers_of_losses` can select a manager that is itself destroyed in the same tick, permanently stranding any real bidder who answers its "zombie" CFP

### Reproduction

I ran this against the real code (not a mock). Setup: `Jet_2` is manually recorded as the manager-of-record for `Target_A` with `Jet_3` as its sole accepted contractor (`jet2.managed_contracts["Target_A"] = {"Jet_3"}`) — a completely normal post-resolution state per Phase 8 Part C.2. Both `Jet_2` and `Jet_3` are then destroyed in the same tick (simulating a SAM catching both at once, or any other simultaneous-kill scenario):

```python
jet2.role = "IDLE"
jet2.managed_contracts["Target_A"] = {"Jet_3"}
bridge.model.jets["Jet_2"].hp = 0
bridge.model.jets["Jet_3"].hp = 0
bridge.model._apply_sam_damage()          # pops both out of model.jets/space
for aid in ("Jet_2", "Jet_3"):
    bridge.agents[aid].stop()             # cancels their run() tasks — real Bridge order
await bridge._notify_managers_of_losses({"Jet_2", "Jet_3"})
```

Result: `jet2.role` flips from `IDLE` → `CONTRACTOR` (`committed_target_id="Target_A"`), even though `Jet_2` no longer exists in `model.jets` and its background message loop was already cancelled. A **destroyed agent silently re-entered the CNP state machine and broadcast a live `CFP` for `Target_A` with `sender_id="Jet_2"`.**

I then extended this to show the real damage: with a third, genuinely alive, idle agent (`Jet_1`) aware of `Target_A` and listening on the broker, it does exactly what a well-behaved agent should — answers `Jet_2`'s CFP with a `BID`. But `Jet_2`'s `run()` loop is cancelled, so it can never call `handle_message` again to collect that bid, resolve the contract, or send `Jet_1` an `ACCEPT`/`REJECT`:

```
Jet_1 role right after zombie CFP round: CONTRACTOR_PENDING
tick 1..10: Jet_1.role=CONTRACTOR_PENDING  committed_target_id=None  Target_A.hp_remaining=5
```

`Jet_1` — the swarm's remaining `Target_A`-capable agent in this scenario — is now **permanently inert** for the rest of the run. `_maybe_bid`/`scan_and_negotiate` both require `role == "IDLE"`; `handle_message`'s `ACCEPT`/`REJECT` branches both require `role == "CONTRACTOR_PENDING"` and depend on a message that will never arrive (Phase 9's `CONTRACTOR_PENDING_TTL_TICKS` self-healing timeout, which would eventually rescue this exact state, does not exist yet in the code — that's still Phase 9, not yet implemented). `Target_A.hp_remaining` never moves off `5`. In a scenario where this is the only remaining `JDAM`-capable jet, the run never resolves and only terminates via the `max_ticks` safety cap in `WraithNetBridge.run()`, reporting neither success nor `swarm_wiped_out` — a silent hang, not a clean failure.

### Root cause

`wraithnet/bridge.py`'s `_notify_managers_of_losses`:

```python
async def _notify_managers_of_losses(self, destroyed_agent_ids: set[str]) -> None:
    notifications = [
        manager.notify_contractor_lost(lost_id, target_id)
        for manager in self.agents.values()          # <-- includes agents about to be deleted
        for target_id, contractor_ids in manager.managed_contracts.items()
        for lost_id in destroyed_agent_ids & contractor_ids
    ]
    if notifications:
        await asyncio.gather(*notifications)
```

This iterates `self.agents.values()` — **at the point it's called, destroyed-this-tick agents are still in that dict** (`_tick()` intentionally defers the `del` step until *after* notification, per Phase 8 Part D's own comment: *"Destroyed agents are deleted from `self.agents` ... only after notification"*). The list comprehension never excludes `manager_id in destroyed_agent_ids`, so a manager that died this tick is just as eligible to be treated as a "surviving manager who lost a contractor" as one that's genuinely still alive — provided it separately had its own `managed_contracts` entry for some *other* jet that also died this tick.

`docs/phases/Phase_8_Dynamic_Threats_SAM_Sites.md` itself asserts the opposite is true: *"note `manager.notify_contractor_lost(...)` itself is called on **surviving** managers (never on the destroyed agent)."* That claim is only true when the manager and the lost contractor are different sets with no overlap with `destroyed_agent_ids` on the manager side — it does not hold for the simultaneous-death case, which the doc's own reasoning doesn't actually rule out. This is a spec bug that got faithfully implemented, not an implementer error — same pattern as the Phase 3 CNP bug from the last review.

### Why the test suite doesn't catch this

`tests/test_dynamic_threats.py`'s `notify_contractor_lost` tests all call the method directly on a manager that is never itself among the "destroyed" set — they test the *method's own logic* in isolation, correctly, but nothing exercises `_notify_managers_of_losses`'s *selection* of which agents count as eligible managers. `tests/test_bridge.py::test_bridge_removes_destroyed_jet_and_notifies` forces exactly one jet's death per test, never a manager+its-own-contractor pair in the same tick. And in the real fixed scenario, this requires two specific jets to die within the same SAM-damage tick, which — based on every run I observed — doesn't happen (jets don't currently linger long enough near `SAM_Site_1` in pairs). The bug is real and reachable (any scenario with a denser SAM engagement zone, more jets, or Phase 9's jamming making negotiations take longer and thus more ticks for jets to be near the SAM together), just not currently triggered by `config.SCENARIO`'s specific numbers — the same category of gap as the Phase 3 bug.

### Recommended fix

Exclude any manager that is itself in `destroyed_agent_ids` before considering its `managed_contracts`:

```python
async def _notify_managers_of_losses(self, destroyed_agent_ids: set[str]) -> None:
    notifications = [
        manager.notify_contractor_lost(lost_id, target_id)
        for manager_id, manager in self.agents.items()
        if manager_id not in destroyed_agent_ids
        for target_id, contractor_ids in manager.managed_contracts.items()
        for lost_id in destroyed_agent_ids & contractor_ids
    ]
    if notifications:
        await asyncio.gather(*notifications)
```

This is a one-line, low-risk fix (add the `if manager_id not in destroyed_agent_ids` guard). It does leave one follow-up question worth deciding explicitly rather than leaving implicit: **if the manager and its contractor die together, who re-tasks the shortfall?** With this fix, nobody does automatically in the tick the pair dies — the target's `TargetInfo.claimed` stays `True` (the broker's `open_contracts[target_id]` entry is never closed by this path, since the dead manager never calls `close_contract`), so it will sit unclaimed-but-marked-claimed until `expire_stale_contracts` frees it after `CONTRACT_CLAIM_TTL_TICKS` (50) ticks, after which a surviving `IDLE` agent's normal `scan_and_negotiate` will pick it back up. That's a bounded, self-healing (if slow) fallback and is an acceptable resolution for a PoC, but it should be a stated design decision (and covered by a regression test — see below) rather than an accidental side effect of the fix.

**Add a regression test** (e.g. in `tests/test_dynamic_threats.py` or `test_bridge.py`) mirroring the repro above: a manager and one of its accepted contractors destroyed in the same tick; assert the manager's `JetAgentRuntime` (captured by reference before deletion) never transitions out of whatever role it held pre-destruction, and that no third, genuinely alive agent that later bids on the target is ever left stuck in `CONTRACTOR_PENDING` past `expire_stale_contracts` freeing the claim.

## 2. [Notable deviation, low risk with current scenario numbers] `scan_and_negotiate` adds an un-specified weapon-suitability pre-filter before attempting to become Manager

```python
# wraithnet/agents.py, scan_and_negotiate
if (
    obs.total_payload_remaining > 0
    and compute_bid(obs, target) is None
):
    continue
won = await self.try_become_manager(target)
```

`docs/phases/Phase_6_Weapon_System_and_Schema_Foundations.md` Part D.5 explicitly states the opposite design intent: *"an agent can still become Manager for a target it can't personally strike (e.g., coordinating others) ... this requires no change to `try_become_manager` [or `scan_and_negotiate`]."* The actual code adds a guard that skips `try_become_manager` entirely whenever the agent holds *some* payload but nothing suitable for *this specific target's* type — which only allows the "pure coordinator" behavior for agents holding **zero** payload of any kind, not agents holding the *wrong* kind.

This doesn't currently break anything: every jet in `config.SCENARIO` carries at least one `JDAM` (suitable for both `Target_A` and `SAM_Site_1`), and `Target_B` (the one `MOBILE_RADAR` target) is only ever manageable by `Jet_3`, which is also the only jet that happens to carry the required `AIM9X` — so the "wrong weapon, still want to coordinate" case never arises with today's numbers, and `test_tie_break_prefers_lexicographically_smaller_agent_id` (which relies on a **zero**-payload manager) still passes. But it's an untested, undocumented deviation from the phase doc's stated behavior, and it's a latent robustness gap: if a future scenario ever has a jet whose only idle, aware option for covering a shortfall is to coordinate (not personally strike) a target outside its own weapon's suitability, this filter silently prevents it from ever opening that CFP.

**Recommendation:** either (a) remove the filter to match the documented "any idle agent, regardless of loadout, can act as a pure coordinator" behavior, or (b) keep it as a deliberate optimization (avoiding wasted CFP timeouts when no one who could possibly answer is even scanning) but say so explicitly in a comment, and add a unit test proving a nonzero-but-wrong-weapon jet still becomes manager when it's the only IDLE/aware agent for a target (today's tie-break test doesn't cover this because its "coordinator" has *zero* payload, not merely unsuitable payload).

## 3. [Good catch, worth calling out] `DataCollector` was correctly switched from flat `agent_reporters` to type-scoped `agenttype_reporters` — necessary once `SamSiteAgent` exists

Both `docs/phases/Phase_6_...md` Part C.6 and `docs/phases/Phase_1_...` (inherited) specify:

```python
agent_reporters={"TotalPayloadRemaining": "total_payload_remaining", "DistanceTraveled": "distance_traveled"}
```

The actual `wraithnet/world.py` instead uses:

```python
agenttype_reporters={JetAgent: {"TotalPayloadRemaining": "total_payload_remaining", "DistanceTraveled": "distance_traveled"}}
```

This is the *correct* choice, not an unjustified deviation — I confirmed by reading Mesa 3.5.1's `datacollection.py` directly. `DataCollector`'s plain `agent_reporters` path (`_record_agents`) applies every reporter function to **every** agent in `model.agents`, with no type filtering — once Phase 8 adds `SamSiteAgent` as a second `mesa.Agent` subclass in the same model, a flat `agent_reporters={"TotalPayloadRemaining": "total_payload_remaining", ...}` would raise `AttributeError` the first time `datacollector.collect()` runs, because `SamSiteAgent` has neither attribute. `agenttype_reporters={JetAgent: {...}}` filters to `JetAgent` instances only (via `_record_agenttype`/`model.agents_by_type`), sidestepping the problem entirely. The implementer consistently updated `metrics.py::export_results` and `tests/test_world.py`/`test_scenario.py` to use `get_agenttype_vars_dataframe(JetAgent)` to match. No action needed in the code — **I've noted this so the Phase 6 and Phase 8 phase docs' own code samples can be patched to use `agenttype_reporters` from the start**, so a future from-scratch implementation of these phases doesn't get bitten by the same `AttributeError` this implementer evidently caught and fixed proactively.

## 4. [Confirmed fixed since the last review] The Phase 3 multi-round CNP re-negotiation bug from `02_Code_Review_Phases_3-5.md` §1 is resolved

Verified directly in the current `wraithnet/agents.py`:
- `handle_message`'s `BID`-collection guard now keys off `self.managed_target_id is not None and message.target_id == self.managed_target_id` (not `role == "MANAGER"`), with an explicit comment referencing the fix. ✅
- `_resolve_contract` now takes `hp_needed` as an explicit parameter and computes `remaining = hp_needed - sum(winning bids)`, rather than re-reading a stale `Observation`. ✅
- `test_multi_round_renegotiation_accepts_late_bidder` in `test_cnp.py` is a direct regression test for this exact bug, explicitly cross-referenced to `docs/02_Code_Review_Phases_3-5.md §1`, and passes. ✅

## 5. Acceptance-criteria check against each phase doc

| Phase | Criterion | Status |
|---|---|---|
| 6 | `compute_bid` hard-disqualifies (returns `None`) for an unsuitable weapon/target-type pairing | ✅ `test_compute_bid_returns_none_for_unsuitable_weapon`, `test_compute_bid_mobile_radar_requires_aim9x` |
| 6 | `_apply_strike` refuses to damage/consume ammo for a mismatched weapon/target pairing (defense-in-depth) | ✅ `test_strike_unsuitable_weapon_is_noop` |
| 6 | No scalar `payload_remaining` references remain anywhere | ✅ verified via `grep` |
| 6 | `python -m wraithnet.main` still resolves with dict-based payload accounting | ✅ |
| 7 | `build_observation` never returns a target farther than `SENSOR_RADIUS` | ✅ `test_build_observation_respects_sensor_radius`, `test_v3_scenario_initial_sensor_visibility` |
| 7 | No agent ever `STRIKE`s a target absent from that tick's direct `Observation.known_targets` | ✅ `test_decide_intent_refuses_strike_on_memory_only_target` |
| 7 | An agent with zero direct sensor coverage can still bid purely from a `DATALINK_CONTACT` | ✅ `test_helper_bids_from_datalink_contact_only` |
| 7 | Full scenario still resolves despite contact-propagation delay | ✅ `test_distant_jets_received_datalink_from_jet1` + full-scenario tests |
| 8 | Jets gain `hp`; SAM damage applies only in range, only from a live SAM | ✅ `test_sam_damage_in_range_and_out_of_range`, `test_destroyed_sam_deals_no_damage` |
| 8 | A destroyed jet is fully removed from `model.jets`/`model.space`/`bridge.agents` and gets no further `Observation`/`decide_intent` calls | ✅ `test_jet_destroyed_removed_from_jets_and_space`, `test_bridge_removes_destroyed_jet_and_notifies` |
| 8 | Swarm wipeout is a distinct, correctly-flagged failure termination | ✅ `test_swarm_wipeout_terminates_as_failure`, `test_bridge_swarm_wipeout_terminates` |
| 8 | `managed_contracts` survives past a resolved contract's own state cleanup | ✅ `test_managed_contracts_populated_and_survives_resolution` |
| 8 | `notify_contractor_lost` re-opens bidding for exactly the right shortfall and is a no-op for irrelevant/already-destroyed cases | ✅ `test_notify_contractor_lost_*` (4 tests) |
| 8 | `notify_contractor_lost` is never invoked on a manager that is itself destroyed the same tick | ❌ **broken, see §1** — untested and reachable |
| 8 | `python -m wraithnet.main` resolves (or cleanly reports swarm wipeout) even with SAM damage in play | ✅ observed directly (tick 32, 3/3 destroyed, no deaths this run) |

## Recommendation

**Fix §1 before considering Phase 8 done**, and definitely before starting Phase 9 — jamming will make agents spend more real time mid-negotiation and further apart from their squad, which only increases the odds of two related agents (a manager and its own contractor) dying in the same SAM-damage tick. The fix is one line; the harder part is deciding (and testing) what should happen to the target's claim afterward, which §1 already proposes (let `expire_stale_contracts` reclaim it — acceptable for a PoC, but state it explicitly rather than leaving it implicit). §2 is a minor, currently-inert deviation worth a one-line comment or a revert, and §3 is informational — patch the Phase 6/8 docs' `DataCollector` code samples so future implementations don't hit the same `AttributeError` this one evidently avoided.
