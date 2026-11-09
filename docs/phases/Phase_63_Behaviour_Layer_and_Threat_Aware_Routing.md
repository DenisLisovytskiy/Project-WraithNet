# Phase 63 — Behaviour Layer & Threat-Aware Routing

> **LLM implementation prompt:** Assume Phases 0–62 are complete. Read `docs/23_Project_State_Audit_and_Next_Phases_Roadmap.md` §5 and `JetAgentRuntime.decide_intent` in full before coding. Part A is a pure refactor and must land alone, with the equivalence fixture from Phase 58 unchanged. Parts B–C change behaviour and are judged by the Phase 57 sweep. `networkx` is already in `requirements.txt` and is imported nowhere; use it for the route graph instead of writing a pathfinder, or remove it from the requirements if you do not.

## Goal

What a jet does each tick is chosen by small, named behaviours with explicit priorities, and the path it flies goes around known threats instead of straight through them.

## Why

* `decide_intent` is one 220-line method with nested branches for RTB, seek, loiter, run-in, attitude line-up, release, gate approach and turn-circle escape. Defence is bolted on by wrapping every return in `_with_defense`. Each new behaviour (Phase 59 winchester, Phase 62 queue, decommit) adds more branches to the same method.
* Movement is a straight line to a point. Altitude is the only way a jet reacts to a threat (`transit_altitude`: over the gun ceiling, under the SAM floor). A jet never flies *around* an envelope, and RTB is a straight line home through whatever is in between.
* Bids therefore price a route the jet has no alternative to (Phase 61 Part A2). Giving the jet a choice of route is what gives the price meaning.

## Part A — behaviours (refactor only)

Split `decide_intent` into behaviour classes in a new `wraithnet/behaviours.py`, each with two methods: `applies(state, obs) -> bool` and `intent(state, obs) -> ActionIntent`. A fixed priority list picks the first that applies:

1. `Recover` (RTB and short final)
2. `Release` (in envelope, in arc, attitude legal)
3. `LineUp` (in range, fix heading/attitude)
4. `RunIn` (fly to the release gate, including the turn-circle escape)
5. `Seek` (transit to the nearest briefed position)
6. `Loiter` (orbit on station)
7. `Hold`

`Defend` stays a modifier applied to whatever was chosen, as `_with_defense` is now.

Record the chosen behaviour name on the trace per jet per tick. The viewer roster can then show "RUN-IN" or "LOITER" instead of only the CNP role, and stalled runs become readable (Phase 59 Part E needed exactly this).

No behaviour tree library, no generic framework. Seven small classes and a list.

## Part B — route planning around known threats

1. Build a coarse navigation graph once per run from the terrain grid (cell size tied to the slowest airframe's turn radius), 8-connected, edges blocked where terrain makes level flight at any legal altitude impossible.
2. Edge cost = distance × (1 + `ROUTE_RISK_WEIGHT` × exposure), where exposure comes from the jet's own `threat_view` (Phase 61) at the altitude band the jet would fly on that edge. Two altitude layers are enough: low and transit.
3. `networkx.astar_path` from current cell to the goal cell; smooth to a short list of waypoints by dropping collinear points; re-plan when `threat_view` changes materially or the goal changes, not every tick.
4. `RunIn`, `Seek` and `Recover` follow waypoints instead of pointing at the goal. The final leg into the release gate stays the existing straight-in geometry.
5. Fuel: the bingo calculation uses the planned recovery path length, not the straight-line distance home.

Bid pricing (Phase 61 Part A2, Phase 62 Part B) switches from the straight line to the planned path, so price and behaviour agree.

`ROUTE_RISK_WEIGHT = 0` must reproduce straight-line flight.

## Part C — two cooperative behaviours

Add only what the existing mechanics already reward:

* **Weapon-release deconfliction in time.** Contractors on the same target share estimated time on target in their `HEARTBEAT` (Phase 60). A contractor that would arrive after the rounds already in flight are enough to kill the target slows down or holds one orbit before committing its own. This attacks overkill at the behaviour level.
* **Threat suppression timing.** When precedence (Phase 62 Part D) says a covered target waits for a SAM strike, the jet assigned the covered target holds outside the envelope (`Loiter` at the envelope edge) until it hears the SAM is dead or the hold times out.

Escort, formation and air-to-air belong to a later design, once there is something in the air to escort against.

## Tests

* Part A: equivalence fixture unchanged; each behaviour unit-tested with a hand-built observation; exactly one behaviour applies per tick (asserted in the shared invariant helper from Phase 56).
* Routing: with a SAM envelope between jet and target and open space around it, the planned path stays outside the envelope and is longer than the straight line; with weight 0 it is the straight line; with no gap it goes through at the low layer.
* Re-plan: a threat learned mid-flight changes the path within one tick; an unchanged picture does not trigger a re-plan.
* Fuel: a jet whose straight-line distance allows a strike but whose safe path does not, does not bid.
* Deconfliction: second contractor delays; if the first round misses, it proceeds.

## Acceptance criteria

1. `decide_intent` is a short dispatcher; no behaviour exceeds about 60 lines.
2. Behaviour name is on the trace and shown in the 3D roster.
3. Sweep, routing on vs off: jets lost per mission down; solve rate not lower; median ticks reported (expected slightly up).
4. Planning cost: no measurable drop in ticks per second on the Phase 58 benchmark beyond 10%.

## Non-goals

* No continuous-space planner (RRT, potential fields), no 3D graph beyond two altitude layers.
* No learned policies.
