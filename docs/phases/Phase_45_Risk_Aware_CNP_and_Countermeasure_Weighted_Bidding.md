# Phase 45 — Risk-Aware CNP & Countermeasure-Weighted Bidding

> **LLM implementation prompt:** Assume Phases 0–44 are complete. Read `docs/20_Risk_Aware_CNP_Negotiation_Design_Document.md` completely and inspect current CNP, observation, threat, base, objective, and in-flight reservation contracts before coding. Replace scalar tuple bidding with typed, auditable, risk-aware proposals. A jet with fewer/no flares must be disadvantaged specifically when a known IR threat affects its planned route/attack. Preserve fog of war and deterministic logical-tick execution.

## Goal

Make CNP select capable jets that can plausibly reach, attack, survive, and recover—not merely the closest jet with ammunition.

## Part A — schemas

Add typed, range-validated:

* `ThreatKind`
* `ThreatContact`
* `BidContext`
* `BidRiskBreakdown`
* `BidUtilityBreakdown`
* `BidProposal`
* `BidUtilityWeights`
* `AttackPlanSummary`
* `TimedRouteSegment`
* `BidderStateSnapshot`
* `BaseForecastSnapshot`
* `AwardContext`
* `BidRejectReason`

Add `RISK_UPDATE` to the CNP message state machine. Update BID to carry required `proposals: list[BidProposal]`, one per legal weapon/plan; migrate/remove loose `payload_offered`, `weapon_type`, and `bid_utility` only after all call sites/tests update. CFP carries raw target HP, accepted nominal reservations, objective priority, and the manager's non-hidden threat contacts. `unreserved_hp=max(0, raw_hp-reservations)` is computed exactly once.

Threat contact sets serialize in stable sorted form.

Every final proposal carries `normalized_context_hash`. Its bidder snapshot includes heading, velocity, defensive state/inbound threats, and primary/alternate base forecasts. Attack plans use timed route segments plus turn/altitude-change ticks so manager recomputation has all required inputs.

## Part B — observation/world-view inputs

Expose to each jet:

* flare remaining/capacity/cooldown;
* current HP/max HP;
* frame speed/turn/evasion and radar-warning/notch capabilities;
* authoritative altitude/fuel;
* primary/alternate base status and forecast position;
* current inbound defensive state;
* directly detected threat capabilities allowed by sensors.

Extend world-view/datalink to retain typed threat contacts with tick/confidence. Do not expose unseen world entities or exact hidden loadouts.

`ThreatContact` must include observed velocity, altitude/source height, heading/engagement arc, altitude ceiling, readiness, source kind, and observer ID where known. Add a bounded common-knowledge round: bidders append additional valid contacts, manager broadcasts one sorted-union `RISK_UPDATE`, and final bids are all repriced against the same contact set/context hash. For duplicate threat IDs choose the whole record with maximum `(observed_tick, DIRECT source rank, confidence, observer_id)`—never arrival order or field-wise mixing. This prevents ignorance from appearing artificially safe without leaking unseen world state.

## Part C — pure planning/scoring module

Create `wraithnet/bid_utility.py` with no asyncio, Mesa, viewer, filesystem, or mutable world imports.

Public functions:

```python
def flare_readiness(...)->float: ...
def notch_readiness(...)->float: ...
def estimate_attack_plan(...)->AttackPlan | None: ...
def estimate_threat_exposure(...)->BidRiskBreakdown: ...
def score_bid(...)->BidUtilityBreakdown: ...
def build_bid_proposals(...)->list[BidProposal]: ...
```

Requirements:

* all factors `[0,1]`;
* fixed/supercover route sampling;
* city LOS/legal firing point;
* moving-target prediction;
* dynamic carrier recovery;
* standoff-derived geometry/exposure, without a second positive standoff reward;
* fuel and engagement ticks;
* no combat random-roll peeking;
* sorted deterministic inputs/ties.

## Part D — flare and seeker risk

Implement exactly:

```text
flare_readiness =
    clamp(flares_remaining / max(1, flare_capacity))
    × projected_cooldown_readiness_at_first_IR_encounter

IR risk =
    known_IR_exposure
    × (1 - IR_mitigation_effectiveness × flare_readiness)
```

Cooldown readiness is `1` when cooldown expires by first IR encounter; otherwise `max(0, 1 - (cooldown_remaining-ticks_to_encounter)/max(1, frame_cooldown))`. Clamp IR mitigation below one (default maximum `0.80`) so full flares reduce but never erase risk.

No capacity (`flare_capacity=0`) means readiness zero without division error. Tests must prove:

* fewer flares lowers bid under equal known IR exposure;
* zero flares has maximum IR countermeasure penalty;
* with zero IR exposure, flare count has no utility effect;
* flare count has no radar/gun effect.

Radar uses notch readiness with residual mitigation capped below one (default `0.75`); guns use HP/evasion/altitude/LOS with mitigation capped below one (default `0.50`), not flares or notch.

HP/evasion reduces relevant threat risk but is not also awarded as an independent positive term. Mission-objective priority schedules which CFP the manager opens; do not add the same priority once per winning bidder.

## Part E — legal candidate plans

Build one candidate per legal weapon:

* target class/signature/suitability;
* available and uncommitted rounds;
* target unreserved HP;
* legal range/arc/LOS firing point;
* predicted intercept for moving units;
* outbound/attack/recovery travel;
* known envelope exposure;
* offensive delivery probability including radar-support loss and target-local CIWS interception;
* recoverable fuel.

Hard no-bid: no weapon, no legal LOS/firing point, no fuel/recovery, fully reserved target, destroyed/RTB/jammed bidder.

Return every legal weapon/plan proposal. Do not discard a lower individual choice that may improve the manager's global combination. Manager may accept at most one proposal per bidder.

Compute `delivery_probability = clamp(base_hit_probability × support_survival × (1-CIWS_intercept_probability))` and `expected_damage_milli = round(rounds × damage × delivery_probability × 1000)`.

## Part F — CNP runtime integration

Replace `compute_bid()` tuple use with `BidProposal`.

Runtime:

* snapshots observation tick;
* excludes already committed ammunition;
* transmits actual flare state;
* expires stale CFP/contact data;
* does not decrement payload/countermeasures while bidding;
* on ACCEPT reserves exact offered rounds and nominal target damage;
* on launch follows Phase 38 acknowledgement and nominal-damage reservation rules;
* on missile non-hit releases target reservation and enables re-tasking;
* launch rejection, pre-launch contractor loss, timeout/abandonment, target invalidation, and invalidated attack/recovery plan release all remaining round/damage reservations idempotently.

Jammed agents cannot send/receive bids as before. Expected future jammer exposure is a penalty, not a current transport block.

## Part G — deterministic manager selection

Create pure:

```python
def select_winning_bids(
    proposals: Sequence[BidProposal],
    context: AwardContext,
) -> AwardPlan:
    ...
```

Use bounded integer-damage DP or bounded subset enumeration. Optimize:

`AwardContext` contains CFP/raw-HP/reservation data, normalized contacts, weights, bounded `coverage_reserve_factor=1.25`, overkill/team-size penalty weights, and current tick. Recompute every proposal from its concrete plan and bidder snapshot.

1. require raw coverage `unreserved_hp × 1000` using deterministic integer milli-damage and discard any subset with a covering proper subset;
2. prefer subsets meeting `raw coverage × coverage_reserve_factor`; if none do, retain raw-covering subsets and mark `LOW_CONFIDENCE_COVERAGE` instead of rejecting exact-capability missions;
3. maximize `mean_utility - overkill_weight × normalized_overkill - team_size_weight × normalized_team_size`;
4. minimize mean threat/fuel/recovery risk on equal award score;
5. prefer complementary plans only as a documented tie-break;
6. sorted bidder/weapon IDs final tie-break.

Reject malformed, stale, incompatible-context, duplicate-plan, impossible-round, and world-stale proposals with stable reasons. Enforce at most one selected proposal per bidder. Recompute final utility/risk against the normalized threat set and transmitted plan.

Before CFP creation, sort known unclaimed DESTROY subjects by required-first, descending objective priority, oldest-unserviced tick, then target ID. Use Phase 37's stable claim ordering. This makes objective priority operational.

## Part H — negotiation observability

Add trace/debug negotiation events:

* CFP context identifier;
* each valid/rejected bid summary;
* utility/risk components including flare state;
* selected award plan;
* reservations and release reasons.

Add optional viewer negotiation inspector or structured JSON export. Normal HUD shows only concise assignment/reason.

Metrics include awards grouped by flare readiness, predicted risk versus losses, decline reasons, reservation overkill avoided, and assignment outcomes.

## Part I — tests

Create:

* `tests/test_bid_utility.py`
* `tests/test_cnp_risk_awards.py`
* extend CNP/full-run/trace tests.

Test all cases from the design document plus:

* every factor boundary;
* no NaN/inf;
* weights sensitivity/normalization;
* attack-plan failure reasons;
* direct/datalink/stale threat distinctions;
* HP/evasion damage effects;
* active defense delay;
* same proposal JSON across insertion orders;
* same award under varied asyncio scheduling;
* old default scenario remains completable with a documented changed assignment if scoring rationally changes it.

## Part J — documentation/calibration

Document default weights with rationale. Run a deterministic scenario matrix:

* no threats;
* IR-heavy;
* radar-heavy;
* AAA-heavy;
* jammer corridor;
* carrier recovery;
* corvette layered defense;
* mixed combined arms.

Inspect assignments rather than tuning only for win rate. Weight changes must not make one factor dominate all others or prevent a uniquely capable jet from bidding.

## Acceptance criteria

1. Low/no flare inventory is an explicit BID field and reduces utility under known IR exposure.
2. Flare scarcity has no unrelated penalty when IR exposure is zero.
3. All designed survivability/recovery/capability factors listed in the design are integrated or explicitly excluded with tests.
4. Manager selection covers damage with deterministic, risk-aware combinations.
5. No hidden-ground-truth leak or future-roll peek.
6. Full pytest, deterministic runs, replay/live trace, and packaged smoke tests pass.
