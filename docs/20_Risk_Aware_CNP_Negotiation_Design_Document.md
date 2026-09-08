# Design Document: Risk-Aware Contract Net Negotiation

> This document is the source of truth for Phase 45. It updates jet Contract Net Protocol (CNP) bidding and manager award selection after terrain, guided defenses, naval units, AAA, countermeasures, dynamic bases, and combined-arms objectives exist. It explicitly makes low/no flare inventory a disadvantage when a task exposes the bidder to known infrared threats. Nothing in this document is implemented yet.

## 0. Problem

Current `compute_bid()` considers:

* weapon suitability;
* offered payload relative to requested HP;
* straight-line distance.

It ignores whether the bidder is likely to survive the assignment. After Phases 34–44, two jets with identical weapons/distance can have very different risk:

* one has flares and the other has none;
* one can notch effectively and the other is slow/damaged;
* one route crosses known IR SAM, radar SAM, AAA, jammer, or corvette envelopes;
* one has enough fuel to attack and recover to its moving carrier while another does not;
* one weapon permits standoff and another requires entering CIWS/AAA range;
* city LOS may make the naïve attack point invalid;
* accepted missiles may already reserve enough damage on the target.

CNP must become risk-aware without giving agents hidden ground-truth information or making a no-flare jet universally useless.

## 1. Core rule for flares

Flare inventory influences negotiation as:

```text
IR countermeasure penalty
    = estimated known IR exposure
      × (1 - IR_mitigation_effectiveness × flare_readiness)
```

```text
flare_readiness
    = (flares_remaining / flare_capacity)
      × projected_cooldown_readiness_at_first_IR_encounter
```

Let `t_ir` be ticks until first estimated IR-envelope entry and `cd_max` the frame flare cooldown:

```text
projected_cooldown_readiness =
    1                                      if cooldown_remaining <= t_ir
    max(0, 1 - (cooldown_remaining-t_ir)
               / max(1, cd_max))          otherwise
```

Both values are clamped to `[0,1]`. A jet with zero flares receives the maximum countermeasure penalty only for a route/attack exposed to a known IR threat. If estimated IR exposure is zero, flare count does not distort the bid. This meets the requirement that low/no flares are a disadvantage while avoiding irrational behavior on missions where flares cannot help.

Flares never improve a bid against radar missiles, AAA, tank guns, or jamming.

## 2. Information boundary

Risk estimates may use only:

* the bidder's current observation/state;
* its non-expired world-view/datalink contacts;
* threat details explicitly included in the CFP;
* known terrain and mission objectives;
* public platform/weapon catalogs;
* accepted in-flight nominal-damage reservations communicated by the manager/world.

They may not query hidden `model.sam_sites`, corvette loadouts, unseen AAA, or future random combat outcomes. Unknown threats contribute zero specific exposure; an optional preset/doctrine uncertainty reserve may be explicit, but it cannot secretly access ground truth.

Add:

```python
ThreatKind = Literal["IR_MISSILE", "RADAR_MISSILE", "AAA_GUN", "CIWS", "JAMMER"]

class ThreatContact(BaseModel):
    threat_id: str
    position: tuple[float, float]
    velocity: tuple[float, float] = (0.0, 0.0)
    altitude: float
    source_sight_height: float
    heading: float
    threat_kinds: list[ThreatKind]  # validated unique and stored sorted
    known_ranges: dict[ThreatKind, float]
    engagement_arc_deg: dict[ThreatKind, float]
    altitude_ceiling: float | None
    readiness: dict[ThreatKind, float]
    last_observed_tick: int
    confidence: float = Field(ge=0.0, le=1.0)
    source: Literal["DIRECT", "DATALINK"]
    observer_id: str
```

Direct sensing may reveal exact catalog/frame data according to sensor rules. Datalink copies preserve age/confidence. Stale contacts expire under a documented TTL.

To prevent a less-informed bidder appearing artificially safer, bidding uses a bounded normalization round:

1. CFP publishes the manager's common threat-contact set.
2. Bidders return all legal plans plus any additional non-expired contacts used/found.
3. Manager forms the sorted union and broadcasts one `RISK_UPDATE`.
4. Every bidder reprices its plans against that same union; these final proposals are award-comparable.

One update round maximum prevents a negotiation loop. Newly shared contacts become normal datalink knowledge. Proposals identify the exact contact IDs and plan geometry used; the manager rejects a mismatched context hash.

Conflicting records for one `threat_id` are not field-merged. Select one whole record by maximum key `(last_observed_tick, source_rank where DIRECT>DATALINK, confidence, observer_id)`; then sort by threat ID before hashing. This makes the normalized context independent of message arrival order.

## 3. Bid context and proposal

Replace tuple returns with typed contracts:

```python
class BidContext(BaseModel):
    target: TargetInfo
    target_hp_remaining: int
    inflight_reserved_nominal_damage: int
    objective_priority: float
    threat_contacts: list[ThreatContact]
    cfp_tick: int

    @property
    def unreserved_hp_needed(self) -> int:
        return max(0, self.target_hp_remaining - self.inflight_reserved_nominal_damage)

class BidRiskBreakdown(BaseModel):
    infrared_exposure: float
    radar_exposure: float
    gun_exposure: float
    jamming_exposure: float
    flare_readiness: float
    notch_readiness: float
    hp_readiness: float

class BidUtilityBreakdown(BaseModel):
    capability: float
    payload_coverage: float
    travel_cost: float
    fuel_risk: float
    threat_risk: float
    recovery_risk: float
    engagement_delay: float
    final_utility: float

class TimedRouteSegment(BaseModel):
    start: tuple[float, float, float]
    end: tuple[float, float, float]
    start_tick: int
    end_tick: int

class BaseForecastSnapshot(BaseModel):
    base_id: str
    available: bool
    current_position: tuple[float, float]
    current_velocity: tuple[float, float]
    predicted_recovery_position: tuple[float, float]
    predicted_recovery_tick: int
    hp_fraction: float = Field(ge=0.0, le=1.0)

class AttackPlanSummary(BaseModel):
    predicted_target_position: tuple[float, float]
    firing_position: tuple[float, float, float]
    recovery_position: tuple[float, float]
    route_segments: list[TimedRouteSegment]
    turn_ticks: int
    altitude_change_ticks: int
    first_ir_encounter_tick: int | None
    weapon_support_survival: float = Field(ge=0.0, le=1.0)
    target_local_intercept_probability: float = Field(ge=0.0, le=1.0)

class BidderStateSnapshot(BaseModel):
    frame_type: JetFrameType
    hp_fraction: float = Field(ge=0.0, le=1.0)
    fuel: float = Field(ge=0.0)
    altitude: float
    speed: float = Field(ge=0.0)
    heading: float
    velocity: tuple[float, float]
    defensive_state: str
    inbound_threat_ids: list[str]
    flares_remaining: int = Field(ge=0)
    flare_capacity: int = Field(ge=0)
    flare_cooldown_remaining: int = Field(ge=0)
    base_forecasts: list[BaseForecastSnapshot]

class BidProposal(BaseModel):
    bidder_id: str
    target_id: str
    observation_tick: int
    normalized_context_hash: str
    weapon_type: WeaponType
    rounds_offered: int
    nominal_damage: int
    delivery_probability: float
    expected_damage_milli: int
    estimated_travel_ticks: float
    estimated_fuel_on_recovery: float
    recovery_base_id: str
    flares_remaining: int
    flare_capacity: int
    flare_cooldown_remaining: int
    plan: AttackPlanSummary
    planning_contact_ids: list[str]
    additional_threat_contacts: list[ThreatContact]
    bidder_state: BidderStateSnapshot
    risk: BidRiskBreakdown
    utility: BidUtilityBreakdown
```

`AttackPlanSummary` contains predicted intercept/firing/recovery positions, ordered route segments, encounter ticks, and weapon support/CIWS assumptions needed for manager audit/repricing. The BID message carries `proposals: list[BidProposal]`, one per legal weapon/plan; manager may select at most one proposal per bidder. Do not add another group of loose optional message fields.

## 4. Candidate plan construction

Before scoring, construct one candidate plan per legal weapon:

1. Confirm target class/signature/weapon suitability.
2. Compute `unreserved_hp_needed` once from raw target HP minus accepted in-flight nominal reservations.
3. Estimate target position at intercept for moving ships/vehicles.
4. Find a legal firing point using weapon range, firing arc, authoritative altitude, and city LOS.
5. Estimate travel and turn ticks.
6. Resolve current/forecast recovery base position, including moving carrier.
7. Estimate total fuel and recovery margin.
8. Sample the outbound, attack, and recovery segments against known threat contacts.
9. Compute countermeasure/survivability readiness.
10. Estimate offensive delivery probability from weapon baseline, support-loss exposure, and target-local interception such as CIWS. Standoff may reduce route exposure but does not erase CIWS at the target.
11. Score; return no bid when no legal firing/recovery plan exists.

Return every legal candidate to the manager. Do not discard a bidder's second-best weapon: it may produce the best global combination. Award selection enforces at most one accepted plan per bidder.

## 5. Normalized factors

Every factor is documented and clamped to `[0,1]`.

### Positive

* `capability`: target suitability and legal target class/signature.
* `payload_coverage`: risk-adjusted expected delivered damage divided by currently unreserved HP need. Use integer milli-damage (`round(expected_damage × 1000)`) for deterministic manager DP.

### Costs/risks

* `travel_cost`: estimated ticks normalized by mission/map horizon.
* `fuel_risk`: required fuel including recovery safety margin divided by fuel remaining; impossible recovery is a hard no-bid.
* `infrared_exposure`: route/attack time inside known IR engagement envelopes, confidence weighted.
* `radar_exposure`: equivalent radar-missile exposure.
* `gun_exposure`: AAA/CIWS exposure including altitude ceiling and city LOS.
* `jamming_exposure`: expected route time unable to negotiate/share data.
* `recovery_risk`: moving/damaged base, no alternate, narrow fuel margin.
* `engagement_delay`: turn, climb/descent, notch interruption, current inbound defense, and weapon flyout where operationally relevant.

### Defensive readiness

* flare readiness as §1;
* notch readiness from current speed, turn rate, fuel, radar-warning capability, and geometry—never flare count;
* HP/evasion readiness;
* current defensive engagement.

Standoff is not a second positive reward; it changes firing geometry, travel, and measured exposure. HP/evasion is not a second positive reward; it reduces relevant threat risk. This avoids double-counting.

## 6. Utility function

Suggested structure:

```text
IR risk     = IR exposure
              × (1 - IR_mitigation_effectiveness × flare readiness)
Radar risk  = radar exposure
              × (1 - radar_mitigation_effectiveness × notch readiness)
Gun risk    = gun exposure
              × (1 - gun_mitigation_effectiveness × HP/evasion readiness)

Threat risk = clamp(
    w_ir × IR risk
  + w_radar × Radar risk
  + w_gun × Gun risk
  + w_jam × jamming exposure
)

Utility =
    + w_capability × capability
    + w_payload × payload coverage
    - w_travel × travel cost
    - w_fuel × fuel risk
    - w_threat × threat risk
    - w_recovery × recovery risk
    - w_delay × engagement delay
```

Mitigation effectiveness is threat/weapon-derived and clamped below 1 (`MAX_IR_MITIGATION=0.80`, `MAX_RADAR_MITIGATION=0.75`, `MAX_GUN_MITIGATION=0.50` defaults). Readiness therefore reduces but never erases exposure; actual Phase 39 defenses can still fail.

Offensive delivery:

```text
delivery_probability =
    clamp(base_hit_probability
          × weapon_support_survival
          × (1 - target_local_intercept_probability))

expected_damage_milli =
    round(rounds_offered × damage_per_hit
          × delivery_probability × 1000)
```

Direct-fire weapons use their catalog accuracy and zero interception unless a defense explicitly applies.

Weights live in a typed immutable `BidUtilityWeights` config and are covered by sensitivity tests. `coverage_reserve_factor: float = Field(default=1.25, ge=1.0, le=3.0)` belongs to the award config. Do not let one unnormalized world-unit value dominate.

Hard no-bid conditions:

* no legal weapon/rounds;
* illegal target class/signature;
* no legal firing point/LOS;
* no recoverable fuel plan;
* bidder destroyed/RTB/communications unavailable;
* target need already fully reserved.

Low/no flares alone is not a hard no-bid.

Objective priority belongs to manager target/CFP scheduling and is not repeated once per winning bidder inside utility.

## 7. Threat exposure estimation

Use deterministic fixed-step or terrain-cell-supercover sampling of planned segments.

For each known contact:

* test distance to envelope;
* test applicable altitude ceiling;
* forecast moving source position and test heading/engagement arc/readiness when those fields are known;
* test city LOS where the threat requires it;
* multiply exposure duration by contact confidence;
* classify by seeker/gun/jamming kind;
* cap each aggregate at one.

Do not run combat Monte Carlo in bidding. Do not call the keyed hit-roll function to “peek” at future outcomes.

Terrain effects:

* WATER/PLAINS/FOREST do not affect jet travel.
* Forest has no risk modifier.
* CITY affects legal LOS/firing points and can block known ground threat exposure.

## 8. Other designed features that influence negotiation

| Feature | CNP effect |
|---|---|
| Weapon catalog/target class/signature | Capability and hard eligibility |
| Weapon range/standoff | Firing geometry and potentially avoided route exposure; target-local CIWS remains |
| Finite ammunition/damage | Payload coverage |
| In-flight missiles | Reserved target damage; prevents duplicate launch/awards |
| Fuel and RTB | Fuel/recovery risk or no-bid |
| Dynamic carrier base | Forecast recovery position, carrier health, alternate base |
| HP/evasion/frame maneuverability | Survivability and notch readiness |
| IR missiles | Exposure weighted by flare readiness |
| Radar missiles | Exposure weighted by notch readiness |
| AAA/CIWS | Gun exposure, altitude/LOS/range |
| Jammers | Expected communications blackout exposure |
| City buildings | LOS-valid attack point and blocked threat lines |
| Moving corvette/tank objective | Predicted intercept position |
| Mission objectives | Explicit priority for manager CFP/target scheduling, not repeated per bidder |
| Current inbound threats | Engagement delay/temporary risk |

Features intentionally excluded:

* forest decoration;
* cosmetic particles/visual effects;
* hidden enemies;
* future random hit-roll results;
* friendly/enemy tank internal AI unless a jet is bidding to strike a tank objective.

## 9. Manager award selection

Current multi-winner selection must account for combinations, not merely sort scalar utilities.

Before opening a CFP, each idle manager candidate sorts known, alive, unclaimed DESTROY subjects by:

1. required objectives before optional;
2. higher `MissionObjective.priority`;
3. older unserviced objective first;
4. target ID.

Phase 37's stable agent-order claim operation decides which jet becomes manager for the first target it can legally service. Priority is therefore operational rather than an unused schema field.
Managers maintain `last_cfp_tick_by_target` (never serviced sorts oldest) so the fairness key is explicit and deterministic.

```python
class AwardContext(BaseModel):
    bid_context: BidContext
    normalized_threat_contacts: list[ThreatContact]
    utility_weights: BidUtilityWeights
    coverage_reserve_factor: float = Field(default=1.25, ge=1.0, le=3.0)
    overkill_penalty_weight: float = Field(default=0.20, ge=0.0, le=1.0)
    team_size_penalty_weight: float = Field(default=0.05, ge=0.0, le=1.0)
    current_tick: int
```

`select_winning_bids(proposals, award_context)` recomputes every proposal from its concrete `AttackPlanSummary`, `BidderStateSnapshot`, normalized contacts, CFP context, and weights.

Manager:

1. computes unreserved HP need exactly once from raw HP minus accepted nominal reservations;
2. rejects stale/malformed/ineligible proposals;
3. finds deterministic, inclusion-minimal subsets (no proper subset still covers) with at most one proposal per bidder whose expected milli-damage meets raw required coverage `unreserved_hp × 1000`;
4. if any subset also meets preferred reserve coverage `unreserved_hp × 1000 × coverage_reserve_factor`, discard lower-confidence subsets; otherwise retain raw-covering subsets and mark the award `LOW_CONFIDENCE_COVERAGE` rather than rejecting a uniquely capable mission;
5. maximizes `award_score = mean_utility - overkill_penalty_weight × normalized_overkill - team_size_penalty_weight × normalized_team_size`;
6. minimizes mean threat/fuel/recovery risk on equal award score;
7. prefers complementary delivery/seeker plans only as a documented tie-break;
8. tie-breaks by sorted bidder IDs and weapon types.

Use bounded dynamic programming over integer expected damage or exhaustive subset search under a documented maximum bidder count. Do not introduce an unbounded optimizer.

No-flare jets are less likely to be selected for IR-exposed tasks, but may correctly win when they have the only suitable weapon, safe standoff, or much better fuel/location.

## 10. Trust and validation

WraithNet agents are cooperative; Byzantine lying is out of scope. Still:

* Pydantic validates every component range/count.
* Manager rejects proposals older than configured ticks.
* Manager recomputes exposure/components/final utility from the transmitted state, plan geometry, normalized contact set, and weights; bidder totals are not trusted blindly.
* World remains authoritative for ammo, fuel, flares, launch legality, and damage.
* ACCEPT cannot reserve more rounds than the current world/runtime payload snapshot.

Reservation lifecycle:

* no target-damage reservation exists before ACCEPT;
* ACCEPT reserves offered rounds at the contractor and planned nominal damage at the target;
* launch rejection, contractor destruction before launch, contract timeout/abandonment, target invalidation, or loss of legal plan releases both remaining round and unlaunched damage reservations;
* accepted launch transfers reservation ownership from contract to missile;
* HIT consumes it; MISS/INTERCEPTED/DECOYED/LOST_LOCK/FUEL_EXPIRED releases it;
* every transition is idempotent and keyed by contract/missile ID.

## 11. Determinism

* Pure scoring: same context/state/weights gives identical proposal.
* Threat contacts and bids sorted by stable IDs.
* No wall clock, global random, UUID, set iteration, or combat-roll peeking.
* Phase 37 logical-tick CNP ordering remains mandatory.
* Floating comparisons use a documented epsilon followed by stable IDs.

## 12. Trace, metrics, and UI

Record negotiation audit events:

* CFP context hash;
* bid utility/risk breakdown;
* flare readiness and remaining count;
* reject reason;
* selected subset and manager rationale.

Do not put every rejected bid in the ordinary HUD. Add an optional negotiation-inspector panel/debug export.

Metrics:

* assignments by flare state;
* mean risk/utility by outcome;
* assignments/declines by threat type;
* mission losses versus predicted exposure;
* overkill avoided through reservations.

## 13. Testing

Required tests:

1. identical jets except flare count under known IR threat: more-ready jet scores higher;
2. no IR threat: flare count does not change utility;
3. flare count does not reduce radar/gun risk;
4. notch readiness affects radar only;
5. zero-flare jet may still win a safe/unique-capability task;
6. known versus hidden threat information boundary;
7. stale contact confidence/TTL;
8. city LOS and standoff firing points;
9. fuel/recovery hard no-bid and dynamic carrier forecast;
10. jammer/AAA/CIWS exposure;
11. moving-target intercept estimate;
12. in-flight reservation prevents duplicate coverage;
13. deterministic subset awards and tie-breaks;
14. malformed/stale proposal rejection;
15. full-run deterministic negotiation audit.
16. projected flare cooldown at first IR encounter;
17. common-contact normalization prevents ignorance advantage;
18. target-local CIWS lowers AGM84 delivery despite long launch standoff;
19. every reservation release/transfer terminal path is idempotent;
20. manager can choose a bidder's non-top individual weapon as the best global combination.

## 14. Non-goals

* CNP for tanks, carriers, corvettes, SAMs, or AAA.
* Omniscient global risk maps.
* Combat Monte Carlo inside bidding.
* Machine-learned utility weights.
* Guaranteed survival or globally optimal mission planning.
