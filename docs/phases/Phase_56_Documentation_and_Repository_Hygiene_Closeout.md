# Phase 56 — Documentation & Repository Hygiene Closeout

> **Status: DONE (2026-11-12).** Phase 55 is on `main` with its acceptance tests green; nine design documents have status banners; the unplanned work is recorded in `docs/24_Post_Phase_Changes_Record.md` and doc 22 §14; README numbers have their commands. Decisions taken: generated scenarios are no longer committed, `networkx` stays for Phase 65.

> **LLM implementation prompt:** Assume Phases 0–54 are in the code and Phase 55 is unmerged. This phase depends on nothing after it and goes first, so that later phases build on a `main` whose documents are true. Read `docs/23_Project_State_Audit_and_Next_Phases_Roadmap.md` §1. Every item below is a small, mechanical correction of something that is stale or untracked. Do not redesign anything and do not rewrite old phase documents: they are the historical record. Add status notes and new documents instead.

## Goal

The documents say what the code does, the unfinished attitude track is closed, and work that landed without a phase is written down.

## Part A — land Phase 55

`origin/feat/phase-54-55-attitude-parity-and-acceptance` is one commit ahead of `main` (`6550d62`, "Close out the 3D flight attitude track with Phase 55 acceptance and docs"). It adds `tests/test_flight_attitude_track_acceptance.py` and the documentation closeout. It was never merged, so on `main`:

* Phase 55's acceptance test file does not exist;
* doc 22 still opens with "Nothing in this document is implemented yet", although Phases 50–54 are in the code.

Rebase that commit onto current `main`, re-run its acceptance tests against the altitude work that landed afterwards (`464add7`, `d5e1f2a`), fix what drifted, and merge.

## Part B — status banners

Add one status line under the title of each design document, without touching the body:

| Document | Says | Should say |
|---|---|---|
| `21_Smooth_Live_Motion` | "Nothing in this document is implemented yet" | Phases 46–49 implemented (`viewer3d/motion.py`, `test_viewer3d_motion*.py`, `test_viewer3d_phase49.py`) |
| `22_Authoritative_3D_Flight_Attitude` | "Nothing in this document is implemented yet" | Phases 50–55 implemented (after Part A) |
| `20_Risk_Aware_CNP_Negotiation` | no status | Phase 45 partially implemented; link to Phase 63 for the remainder |
| `12_Flight_Dynamics_and_Weapon_Effects`, `09_3D_Live_Visualization…` | altitude is cosmetic | superseded for altitude/attitude by doc 22 (part of the unmerged Phase 55 commit) |

## Part C — write down what was built without a phase

These changes are in the code and have no phase document, and most have no design document. Add one retrospective document, `docs/24_Post_Phase_Changes_Record.md`, with a short section per item: what it does, where it lives, which commit, which tests cover it.

| Change | Commit(s) | Main files |
|---|---|---|
| Mission brief transit (`briefed_seek_positions`) and end-of-mission recall of all jets | `e3a66fe` | `agents.py`, `bridge.py`, `objectives.py`, `world.py` |
| Shared UI chrome, military restyle, baked textures, markers, fonts, highlights | `1acdf4f` … `fe096c4`, `1bbfaed` | `viewer3d/ui_chrome.py`, `fonts.py`, `highlights.py`, `textures/` |
| Configurable battlespace size; claim TTL scaled to map diagonal | `ea2d9ff` | `scenario_generator.py`, `generation_schemas.py`, `config.contract_claim_ttl_for_grid`, `broker.py` |
| 5000×5000 map performance (terrain mesh, world scale) | `8783bb3` | `viewer3d/terrain_mesh.py`, `world_scale.py`, `terrain_generator.py` |
| Friendly-base keep-out and AO spread in generation | `eafb3a2` | `scenario_generator.py`, `scenario_feasibility.py` (doc 18 was amended; no phase) |
| Camera fixes, F11 borderless fullscreen | `62022d7`, `bebb783` | `viewer3d/cameras.py`, `app.py` |
| Jets keep airspeed on IDLE and at the map edge | `89afd44` | `world.py` |
| Altitude as a tactical factor: per-scenario altitude rules, terrain look-ahead, minimum overflight altitude, nose-flown climbs, crash log | `464add7`, `d5e1f2a` | `altitude_tactics.py`, `terrain.py`, `weapons_release.py`, `world.py`, `scenario_feasibility.py` |
| Wide, slower holding orbit on station | `c7c2919` (branch `fix/jets-loiter-orbit`, not yet on `main`) | `agents.py`, `config.py` |

The altitude work is the largest of these and changes agent behaviour and feasibility. Give it a proper section in doc 22 rather than a paragraph in the record.

## Part D — README facts

* Test count: badge and text say 775; the suite collects 1,012. Remove the hard-coded number or generate it.
* "~36k lines of application code": `wraithnet` + `viewer` + `viewer3d` + `launcher` measure about 32.8k lines.
* The sequence diagram shows a `DONE` message and "known threats" in the CFP. `DONE` does not exist in the protocol (see Phase 62); fix the diagram now and again when Phase 62 lands.
* "100-seed regression matrix: every generated STANDARD mission must pass the feasibility check" is true and should stay, with one added sentence: feasibility is a static check, and the solve rate of the simulated mission is reported separately (link Phase 59 results once they exist).
* The mission-debrief screenshot (CONTESTED seed 6) is the one CONTESTED seed out of eight sampled that the swarm currently solves. Keep it; do not present it as typical until Phase 59 is done.

## Part E — small repo items

* `requirements.txt` lists `networkx`; nothing imports it. Remove it, or leave it for Phase 65 with a comment.
* No `pyproject.toml`, no CI workflow (both arrive in Phase 58 Part D; do not duplicate here).
* `scenarios/custom.json` and `custom2.json` are ~40,000 lines each because terrain cells are stored expanded and pretty-printed. Either store terrain compactly (seed + parameters when the scenario is unmodified from generation) or stop committing generated scenarios. Decide and record the decision; do not silently change the schema.
* Phase 15 names `tests/test_main_cli.py`; the tests live in `tests/test_main.py`. Phase 38 names `MissileTrackEvent`, which was never created. Note both in the record document.
* `results/` retention is handled in Phase 57 Part E.

## Acceptance criteria

1. Phase 55 commit merged; `pytest tests/test_flight_attitude_track_acceptance.py` green on `main`.
2. No design document claims "nothing implemented" for work that is in the code.
3. `docs/24_Post_Phase_Changes_Record.md` covers every row of the Part C table.
4. Every number in the README can be reproduced with a stated command.

## Non-goals

* No edits to the body of existing phase documents.
* No code behaviour changes.
