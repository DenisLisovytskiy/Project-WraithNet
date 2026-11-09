# Phase 61 — Shared Threat Picture & Phase 45 Completion

> **LLM implementation prompt:** Assume Phases 0–60 are complete. Read `docs/20_Risk_Aware_CNP_Negotiation_Design_Document.md` completely, then `docs/23_Project_State_Audit_and_Next_Phases_Roadmap.md` §1.3, which lists what Phase 45 specified and the code does not do. This phase finishes that list. Nothing here is a new idea; it is the unbuilt remainder of an accepted design. Where the design document and this file disagree, the design document wins. Run the Phase 57 sweep before and after and attach both tables.

## Goal

Bids are compared on the same information, targets are opened in priority order, and rounds already in the air are counted before more are requested.

## What Phase 45 specified and is not in the code

| Design (doc 20 / Phase 45) | Code today |
|---|---|
| Exposure estimated along the bidder's own route against known threat envelopes ("fixed/supercover route sampling") | `exposure_from_threat_contacts` ignores position. It returns the highest `readiness × confidence` per threat kind, wherever the threat is. Every bidder on a CFP gets the **same** exposure; only flare, notch and HP readiness differ between them. A jet whose route avoids the SAM is priced the same as one flying through it |
| Known SAM sites are threat contacts with range, arc and ceiling | Contacts are built only from **missiles already inbound** (stamped with the bidder's own position) and observed gun sites. A SAM site that has not fired yet contributes no risk to any bid |
| Threat contacts kept in world-view and shared on datalink with tick and confidence | `world_view` holds targets only. Threat contacts are rebuilt from the current observation each time (`_threat_contacts_from_observation`); a threat a jet saw ten ticks ago is forgotten, and one a wingman sees is unknown |
| Normalisation round: bidders return extra contacts, manager broadcasts the sorted union, everyone reprices against it | Manager broadcasts `RISK_UPDATE` with **its own** contacts before any bid. Bidders never contribute. A bidder that knows about a SAM the manager cannot see is scored as if the SAM were not there |
| CFP carries accepted nominal reservations; `unreserved_hp = raw − reserved` | `reserved_hp=0` is hard-coded in the CFP and in `AwardContext`. The world tracks `damage_reservations` for in-flight rounds, but only uses them to reject a late shot |
| Open CFPs in order: required-first, objective priority, oldest unserviced, target id | `scan_and_negotiate` walks `world_view` in dict insertion order, i.e. the order targets were first seen |
| BID carries `proposals`; loose scalar fields removed once call sites migrate | Both exist. `_resolve_contract` still has a legacy branch that fabricates a `BidderStateSnapshot` (hp 3, position 0,0, no flares) for bids without proposals |
| Award audit uses real bidder values | Award log calls `flare_readiness(..., ticks_to_first_ir_encounter=None, frame_flare_cooldown_ticks=2)` with constants |
| `MissileTrackEvent` (Phase 38) | not present |

## Part A — threat memory and datalink

1. Add `threat_view: dict[threat_id, (ThreatContact, last_tick)]` to `JetAgentRuntime`, maintained like `world_view`: refreshed from direct observation, expired by TTL, merged from datalink.
2. New broadcast `DATALINK_THREAT` carrying one `ThreatContact`, sent when a contact is new or materially changed. Receivers store it with `source="DATALINK"` and reduced confidence.
3. Conflict rule exactly as doc 20 §2: keep the whole record with the greatest `(last_observed_tick, DIRECT > DATALINK, confidence, observer_id)`.
4. `transit_altitude` and the bid risk estimate read `threat_view`, not only the current observation. A jet that was told about a gun site climbs before it sees it.
5. Known SAM sites, corvettes and AAA sites become `ThreatContact`s with position, engagement range and altitude ceiling taken from the public catalog for the observed type. Inbound missiles stay as a separate, short-lived kind.

## Part A2 — exposure from geometry

Replace the position-blind maximum in `exposure_from_threat_contacts` with what the design asked for: sample the bidder's planned path (current position → release point → recovery base, at the planned altitude for each leg) at a fixed step, and for each threat kind accumulate the fraction of samples inside a known envelope and below its ceiling, weighted by readiness and confidence. Keep the result in `[0, 1]`.

This is the change that makes the survival term differ between bidders. Until Phase 63 adds routing the path is the straight line the jet will actually fly, which is exactly what should be priced.

## Part B — reservations in the auction

1. The manager's CFP carries the rounds it has already accepted for this target and not yet seen resolved. With Phase 60 `REPORT`/`HEARTBEAT` messages the manager knows this from its contractors; without Phase 60, read the world's `unreserved_hp` through the observation (add the field to `TargetInfo`), never through the model handle.
2. `AwardContext.reserved_hp` and `unreserved_hp` are filled from it.
3. On a contractor `REPORT` of a non-hit, the manager reduces the reservation and reopens the shortfall.

## Part C — normalisation round

Implement doc 20 §2 steps 1–4 with the one-round bound:

1. CFP publishes the manager's contact set.
2. Each bidder replies with its plans **and** any non-expired contact from its `threat_view` that the CFP lacked.
3. Manager forms the sorted union, broadcasts one `RISK_UPDATE` with the new context hash.
4. Bidders reprice once and send final proposals; the manager rejects any proposal whose hash does not match.

With Phase 60 latency above zero this costs extra ticks. Make the round skippable when no bidder returned a new contact.

## Part D — target ordering

Before opening a CFP, sort candidate targets by: required objective first, objective priority descending, oldest unserviced tick, target id. The jet needs objective priority for this: put it on `TargetInfo` from the mission brief (the brief is legitimately known to every jet).

## Part E — remove the legacy bid path

Delete the scalar-bid branch in `_resolve_contract` and the fabricated snapshot. Make `proposals` required on `BID`. Keep `payload_offered` / `weapon_type` / `bid_utility` on the message only if a test or the viewer still reads them; otherwise remove them and the `compute_bid` tuple wrapper. Replace the constant arguments in the award audit with the proposal's own values.

## Tests

* Threat memory: a jet that saw an IR SAM, then lost sight of it, still prices it for TTL ticks; after TTL it does not.
* Datalink threat: jet B bids lower on a route through a gun site only jet A has seen, once A has shared it.
* Normalisation: two bidders with different private contacts produce final proposals with the same context hash; a bidder that skips repricing is rejected with the hash-mismatch reason.
* Reservations: with 2 HP remaining and one 2-damage round in flight, the CFP asks for 0 and no second jet is awarded; when that round misses, the shortfall reopens.
* Ordering: with two unclaimed targets, the required higher-priority one is opened first regardless of discovery order.
* Legacy path: a BID without proposals fails validation.
* Arrival-order independence: shuffle message delivery order within a tick; award is identical.

## Acceptance criteria

1. Every row of the table above is either implemented and tested, or struck out in doc 20 with a written reason.
2. No bid or award code reads a hard-coded bidder state.
3. Sweep table before/after attached; overkill (`reservation_overkill_milli`, rounds fired at already-dead targets) goes down.

## Non-goals

* No new scoring factors and no weight retuning beyond what the before/after sweep forces.
* No multi-task bidding (Phase 62).
