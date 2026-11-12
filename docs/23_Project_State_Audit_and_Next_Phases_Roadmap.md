# Project State Audit & Next-Phases Roadmap (Phases 56–65)

> **Status:** the measurements below are a snapshot taken before Phase 56 and are not updated. Progress on Phases 56–65 is tracked in the Status column of the table in §6. This document records what was measured, on which code, and why each new phase exists. The phase files in `docs/phases/` are the work orders.
>
> **Measured on:** branch `fix/jets-loiter-orbit` at `c7c2919` (`main` at `fa930a5` plus the holding-orbit change), Windows 11, Python 3.14.3, Mesa 3.5.1, 2026-11-10. Numbers will differ on other machines; the ratios should not.

## 0. Summary

1. **The phase plan is built.** Phases 0–54 are in the code. Phase 55 was written and never merged. Phase 45 is the one phase that is materially incomplete, and it is the one that defines how agents choose.
2. **A lot was built outside the plan.** Nine pieces of work after "all phases done" have no phase document, including the tactical altitude model that now drives agent behaviour.
3. **The swarm does not finish most generated missions.** 14 of 32 sampled missions are solved. Every failure is a stall at the 1000-tick cap with jets circling and thousands of empty auctions. The static feasibility check passes all 32.
4. **The tests are good at small things and silent about that.** Deliberately breaking 18 backbone behaviours was caught 16 times. But no test asserts that a generated mission ends, so finding 3 is invisible to a green suite.
5. **A non-GUI mode already exists** and the GUI already uses it as a worker process. What is missing is a batch runner on top. The simulation, not the GUI, is what is slow: about 100 ticks per second, 80% of it in one terrain function.
6. **"No central commander" is not quite true in the code.** Target claims, contractor-loss detection and mission recall go through shared state or the bridge, not the radio.

## 1. Phase documents versus code

### 1.1 Method

* Every back-ticked identifier and file path in each of the 56 phase documents was checked for presence in the code and tests.
* The existing code reviews (docs 01, 02, 05, 06, 08, 11, 13, 15, 17) cover Phases 0–29 line by line and were taken as given.
* Phases 45 and 55 were read in full against the code.
* Remote branches were checked for unmerged work.

This is a presence check, not a re-run of every phase's acceptance criteria.

### 1.2 Result by track

| Track | Phases | State |
|---|---|---|
| PoC core: schemas, world, broker, CNP, bridge, metrics | 0–5 | Implemented |
| Weapons, fog of war, SAMs, jamming, integration | 6–10 | Implemented |
| Trace recorder, 2D viewer | 11–13 | Implemented |
| Scenario schema, live stream, 3D viewer, editor | 14–19 | Implemented. Phase 15 names `tests/test_main_cli.py`; tests are in `tests/test_main.py` |
| Airframes, weapon catalog, fuel/RTB, evasion metrics | 20–24 | Implemented |
| Visual flight dynamics, weapon effects | 25–26 | Implemented |
| GUI testing, launcher, packaging | 27–29 | Implemented (`packaging/` present; the build itself was not run for this audit) |
| Scenario generation | 30–33 | Implemented |
| Terrain | 34–36 | Implemented |
| Combined arms: missiles, countermeasures, naval, AAA, tanks | 37–44 | Implemented. Phase 38 names `MissileTrackEvent`, which does not exist |
| Risk-aware CNP | 45 | **Partially implemented**, see 1.3 |
| Smooth live motion | 46–49 | Implemented. Doc 21 still says "Nothing in this document is implemented yet" |
| Authoritative flight attitude | 50–54 | Implemented |
| Attitude acceptance and doc closeout | 55 | **Not on `main`.** Commit `6550d62` on `origin/feat/phase-54-55-attitude-parity-and-acceptance` adds the acceptance tests and doc updates and was never merged. Doc 22 still says "Nothing in this document is implemented yet" |

### 1.3 Phase 45: specified but not built

| Specified | In the code |
|---|---|
| Risk priced along the bidder's route against threat envelopes | Risk ignores position: the highest `readiness × confidence` per threat kind, the same for every bidder on a CFP |
| SAM sites as known threats | Only missiles already inbound and observed gun sites become threat contacts |
| Threat contacts remembered and shared on datalink | Rebuilt from the current observation each time; not shared |
| Bidders add their contacts, manager broadcasts the union, bidders reprice | Manager broadcasts its own contacts once; bidders add nothing |
| CFP carries rounds already reserved in flight | `reserved_hp=0` hard-coded in CFP and award context |
| Targets opened in objective-priority order | Opened in the order they were first seen |
| Scalar bid fields removed after migration | Legacy path remains, with a fabricated bidder snapshot |

The typed schemas, the proposal list, the dynamic-programming award selection, the flare/notch readiness maths and the negotiation audit log are all there and well tested. What is missing is the information those mechanisms were meant to run on. Details and fixes: Phase 63.

### 1.4 Built without a phase

| Change | Commit |
|---|---|
| Mission-brief transit waypoints; end-of-mission recall of all jets | `e3a66fe` |
| UI chrome module, restyle, textures, markers, fonts | `1acdf4f` … `fe096c4`, `1bbfaed` |
| Configurable map size; claim TTL scaled to the map | `ea2d9ff` |
| 5000×5000 map performance | `8783bb3` |
| Friendly-base keep-out and AO spread in generation (doc 18 amended) | `eafb3a2` |
| Camera fixes, F11 fullscreen | `62022d7`, `bebb783` |
| Jets keep airspeed when idle and at the map edge | `89afd44` |
| Tactical altitude: per-scenario rules, terrain look-ahead, minimum overflight altitude, nose-flown climbs, crash log, feasibility altitude checks | `464add7`, `d5e1f2a` |
| Holding orbit on station | `c7c2919` (not yet on `main`) |

Phase 56 writes these down.

## 2. Test suite

| Fact | Value |
|---|---|
| Tests collected | 1,012 (README says 775) |
| Result | 1,008 passed, 1 failed, 3 skipped |
| Run time | 7 min 20 s |
| Failing test | `test_viewer3d_smoke.py::test_live_stream_strike_triggers_spawn_strike_effect` (12 s wall-clock deadline on a live socket) |
| Slowest test | 107 s, asserts only "no exception" over 20 missions |
| Config | no `conftest.py`, no pytest config, no markers, no coverage tool, no CI |
| Duplicated fixtures | `fast_cnp` in 7 files; 26 local scenario/observation builders |

Static scan of 961 `test_` functions: 3 without any assertion, 3 asserting only "not None", 3 asserting on source text, 138 with a single assertion, 192 touching private attributes (564 of 706 such references are in five `viewer3d` files), 26 depending on wall-clock time.

**Mutation spot-check.** Eighteen single-line behaviour breaks were applied one at a time to a throwaway copy of `broker.py`, `agents.py`, `bridge.py`, `bid_utility.py` and `altitude_tactics.py`, and 17 core test files (228 tests) were run against each.

| Outcome | Count | Examples |
|---|---|---|
| Caught | 16 | jammed sender still transmits; two managers claim one target; manager accepts every bidder; strike on an unsensed target; bingo fuel never triggers; flare count ignored in bids; busy jets still bid; no end-of-mission recall; orbit collapses |
| Not caught | 2 | the bridge never tells a manager its contractor died; the notch manoeuvre points at the missile instead of across it |

Limits: 18 hand-picked mutants, a subset of the suite. It is a sample, not a score.

**What the suite does not cover.** Nothing fails when a generated mission stalls (§3). `summarize()["objective_outcomes"]` is always empty, because it reads `model.objective_states`, which nothing sets, and `metrics.py` reads every field through `getattr(..., default)`. The 25-seed and 100-seed matrices check that scenarios are *generated* feasible; they do not run them.

Conclusion: unit-level protection is real. System-level protection is missing. Phase 58.

## 3. Mission outcomes

Thirty-two missions, 8 seeds (0–7) × 4 presets, default generation (200×200 map), `WraithNetBridge.run()` with its default 1000-tick cap, no GUI.

| Preset | Solved | Tick cap | Aborted | Wiped | Jets lost (total) |
|---|---|---|---|---|---|
| TRAINING | 8 / 8 | 0 | 0 | 0 | 0 |
| STANDARD | 5 / 8 | 3 | 0 | 0 | 0 |
| CONTESTED | 1 / 8 | 7 | 0 | 0 | 7 |
| NIGHTMARE | 0 / 8 | 8 | 0 | 0 | 10 |

All 32 were reported feasible by `analyze_feasibility`.

In the stalled runs:

* Surviving jets have none of the weapon the remaining targets need (typically no AGM-65 left, `MOBILE_RADAR`s alive).
* They do not go home: fuel capacity is 2000–8000 ticks, so bingo is far beyond the cap, and there is no "out of useful weapons" rule.
* Negotiation spins. CFPs logged per run: 4,231; 4,698; 13,271; 9,105; 23,311; 22,345 … against 8–99 bids. About one `reservation_release` per five CFPs, which matches a manager running all five rounds with no bidders and reopening next tick. From the code, a jet with zero payload is allowed to manage any target (`scan_and_negotiate`), which fits.
* Several jets remain `CONTRACTOR` from tick 200 to the end.
* NIGHTMARE seed 3 destroyed 12 of 12 targets and did not finish.

The README debrief screenshot (CONTESTED seed 6, 7/7 targets, 0 lost, 5 home) reproduces exactly. It is the one CONTESTED seed of the eight that solves.

Why the weapons run out when the analyser says the roster is sufficient was not determined. Candidates and the order to check them are in Phase 59.

## 4. Headless mode and speed

**Does a non-GUI mode exist?** Yes.

* `python -m wraithnet.main --scenario X --output-dir Y` runs a mission with no window. `python -m launcher --headless …` forwards to it.
* The 3D viewer launches the simulation as that same headless worker in a subprocess (`viewer3d/launch.py`) and reads ticks over TCP. The simulation never imports Panda3D and is not paced by the viewer.

So GUI and non-GUI are already separate. A non-GUI run is not faster because rendering is skipped; rendering was never in the simulation process.

**What is missing for data gathering:**

* no way to generate a scenario from preset + seed on the command line (only the editor does it);
* no batch over seeds, no parallel workers, no resume;
* the summary is printed text; there is no machine-readable result;
* `trace.jsonl` and two CSVs are always written;
* the tick cap is not a flag, and a capped run is reported as `Solved: False`, same as a real failure.

**Speed.** 16 missions (8 STANDARD, 8 CONTESTED): generation 3.4 s total, simulation 120 s for 11,999 ticks, about **100 ticks/s**. Writing the trace adds about 16%.

Profile of one 1000-tick CONTESTED run:

| Share | Function |
|---|---|
| ~80% | `terrain.lookahead_required_altitude` |
| ~64% of the run | …reached via `world._min_overflight_altitude`, called per jet per known target per tick from `build_observation`. Its inputs (airframe, position, terrain, altitude rules) are constant for static targets |
| ~18% of the run | …reached via the autopilot terrain check, once per jet per tick |
| ~4% | all negotiation code |

**Determinism.** Each of the 16 scenarios was run three times in one process (twice bare, once with a trace recorder); summaries matched in all 16. Not tested: separate processes, different hash seeds, loaded machine. The tick loop contains a wall-clock timeout (`MAX_TICK_WAIT_SECONDS`) that can truncate a negotiation on a slow machine.

Phases 57 and 60.

## 5. The agent backbone: what it is and where it is thin

What exists: one asyncio task per jet with a mailbox; a tick-synchronous bridge (observe → share contacts → negotiate → collect intents → world step → cleanup); CNP with CFP / BID / ACCEPT / REJECT, multi-round re-auction for shortfall, multi-winner awards by dynamic programming, claim TTL, pending-bid TTL, contractor-loss re-auction, target memory with datalink sharing, binary jamming.

Where it is thin:

| Area | Today | Phase |
|---|---|---|
| Liveness | Empty auctions repeat every tick; no backoff; jets with no useful weapons never leave | 59 |
| Evidence | No baseline to compare the swarm against; weights tuned by eye | 61 |
| Decentralisation | Claims are a global lock in the broker, readable and writable while jammed; the bridge tells managers about losses and recalls everyone at mission end; agents hold a handle to the world model | 62 |
| Protocol | No message from contractor to manager. A contractor that fails or gives up is silent; the target stays locked until TTL | 62 |
| Radio | Infinite range, zero latency, no loss; jamming is all-or-nothing | 62 |
| Information | Threats are not remembered or shared; risk is not geometric; reservations are not used (§1.3) | 63 |
| Commitment | One task per jet, never given back, no ordering between targets, managing blocks bidding | 64 |
| Behaviour | One 220-line decision method; straight-line movement; altitude is the only reaction to a threat | 65 |

## 6. Roadmap

Phase numbers are the order of work.

| Phase | Title | Depends on | Why here | Status |
|---|---|---|---|---|
| 56 | Documentation and repository hygiene | — | Independent and cheap; lands the unmerged Phase 55 before anything builds on `main` | **Done** |
| 57 | Headless experiment runner and determinism | — | The measuring instrument for everything after | **Done** |
| 58 | Test suite audit and hardening | 57 | The safety net before behaviour changes; reuses the runner for outcome tests | **Done** |
| 59 | CNP liveness and mission completion | 57, 58 | Fixes the largest visible defect | Not started |
| 60 | Simulation hot-path performance | 57, 59 | Makes large sweeps cheap; profiled after the stalls are gone, because they distort where time goes | Not started |
| 61 | Baseline allocators and comparative experiments | 57–60 | The yardstick has to exist before the backbone is changed; its matrix is rerun after each later phase | Not started |
| 62 | Radio-true coordination | 59, 61 | Makes the central claim true | Not started |
| 63 | Shared threat picture and Phase 45 completion | 62 | Gives bids real information; uses 62's contractor messages | Not started |
| 64 | Multi-task commitment, decommitment, precedence | 62, 63 | Largest behaviour change; needs a design doc first | Not started |
| 65 | Behaviour layer and threat-aware routing | 63, 64 | Refactor first, then routing and the two cooperative behaviours | Not started |

Phases 56–59 are the first batch. From Phase 60 on, every phase is judged by a sweep table instead of a replay, and from Phase 62 on, against the Phase 61 baselines.

## 7. Considered and left out

* **LLM-driven or learned agents.** The value of the project is an inspectable, deterministic protocol. A learned policy would need the experiment harness of Phase 61 first, and would replace the thing being studied.
* **Bundle consensus (CBBA).** A natural step after Phase 64, deferred until Phase 61 shows that insertion bids leave real value unclaimed.
* **Reactive enemy agents** (SAMs that hold fire, relocate, or coordinate). Worth doing, but while the swarm stalls against a static enemy it would only hide the current problems.
* **Enemy aircraft and air-to-air.** AIM-9X and AMRAAM are in the catalog with nothing to shoot at. Needs its own design document.
* **Rewriting the simulation core for speed** (numpy, compiled code). One cache is expected to give most of the gain (Phase 60); measure before going further.

## 8. Reproducing the measurements

All measurements were taken with throwaway scripts that import the package and write nothing into the repository. The commands that matter:

```text
pytest -q --durations=25                                  # §2 counts and timings
python -m wraithnet.main --scenario scenarios/default.json --output-dir <tmp>   # §4 single run
```

The mission sweep (§3), the profile (§4) and the mutation check (§2) are what Phase 57 Part B, Phase 60 Part D and Phase 58 Part B turn into permanent, repeatable commands. Until then, the sweep is: for each preset and seed, `generate_scenario(parameters_for_preset(preset, seed=seed))`, `WraithNetBridge(scenario=…)`, `asyncio.run(bridge.run())`, `summarize(bridge.model)`.
