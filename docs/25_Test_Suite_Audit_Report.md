# 25 — Test Suite Audit Report (Phase 58)

This report is the output of [Phase 58](phases/Phase_58_Test_Suite_Audit_and_Hardening.md). It records how the suite was judged, what changed, and what is still open. All numbers come from commands run on Windows 11, Python 3.14, on branch `test/phase-58-test-suite-audit`.

## 1. Summary

| | Before (main after Phase 57) | After (this branch) |
|---|---|---|
| Tests collected | 1,038 | 1,115 |
| Result of a full run | 1 failed, 1,034 passed, 3 skipped | 1,103 passed, 12 xfailed (strict) |
| Full run time | 248.8 s | 207.1 s (`pytest -m ""`) |
| Default lane | did not exist | 920 passed, 3 xfailed, about 16.4 s (`pytest`) |
| Lanes and markers | none | `slow`, `gui`, `e2e`, set in `pyproject.toml` |
| Shared fixtures | none (`conftest.py` missing) | `tests/conftest.py` |
| Coverage tool | none | `pytest-cov` (dev only) |
| Mutation tool | none | `cosmic-ray` (dev only) and `tools/mutation_sample.py` |
| CI workflow | none | `.github/workflows/tests.yml` (written, **not yet executed**) |

The default lane was green on three consecutive runs: 16.39 s, 16.36 s, 16.48 s.

## 2. Lanes

| Lane | Command | What it runs | Tests | Time |
|---|---|---|---:|---:|
| Default | `pytest` | everything not marked `slow` or `gui` | 923 | about 16 s |
| Full | `pytest -m ""` | everything | 1,115 | about 207 s |
| End-to-end only | `pytest -m e2e` | `tests/test_mission_outcomes.py` | 61 | about 32 s |

Markers:

| Marker | Meaning | Tests |
|---|---|---:|
| `slow` | takes a second or more, or measures speed; full lane only | 95 |
| `gui` | builds the 3D viewer application or launches the viewer's simulator | 97 |
| `e2e` | runs whole missions and asserts on their results | 61 |

`pyproject.toml` also sets `asyncio_mode = "auto"` and turns warnings raised from `wraithnet.*`, and Mesa's "No Agents specified" warning, into errors.

## 3. Verdict for every test file

Time is the sum of setup, call and teardown for the file in one full-lane run (`--durations=0`, which hides entries under 5 ms). Layer uses the list from the phase plan. Verdicts: KEEP, STRENGTHEN, REWRITE, MOVE, DELETE, plus NEW for files this phase added. MOVE means the file was renamed after its subsystem, or some of its tests went to the slow lane, or both.

| File | Tests | Time (s) | Layer | Verdict | Note |
|---|---:|---:|---|---|---|
| `test_aaa.py` | 13 | 0.0 | world step | MOVE | renamed from test_phase42_aaa.py |
| `test_agent_attitude_employment.py` | 12 | 0.0 | agent | STRENGTHEN | notch heading test kills the known `heading = bearing` mutant |
| `test_agents.py` | 18 | 0.1 | agent | STRENGTHEN | deadline waits replaced by `settle_until` round counts |
| `test_bid_awards.py` | 11 | 0.0 | pure function | MOVE | renamed from test_phase45_bid_utility.py; award with no hp left and award score formula added |
| `test_bid_utility.py` | 13 | 0.0 | pure function | STRENGTHEN | scores bounded to [0, 1]; risk-term values pinned |
| `test_bridge.py` | 14 | 0.1 | bridge end-to-end | STRENGTHEN | contractor loss through `_tick` (kills the known notify mutant); jetless sinks; bingo plus recall |
| `test_broker.py` | 14 | 0.0 | agent | STRENGTHEN | claim TTL boundary; direct message to an id before BROADCAST |
| `test_carriers.py` | 13 | 0.0 | world step | MOVE | renamed from test_phase40_carriers.py |
| `test_cnp.py` | 17 | 0.0 | agent | KEEP | builders moved to conftest |
| `test_cnp_risk_awards.py` | 11 | 0.0 | agent | KEEP |  |
| `test_combined_arms.py` | 11 | 4.2 | world step | MOVE | renamed from test_phase44_combined_arms.py; 25-seed smoke in slow lane (checks generation only, see §8) |
| `test_config_weapons.py` | 7 | 0.0 | pure function | KEEP |  |
| `test_corvettes.py` | 17 | 0.0 | world step | MOVE | renamed from test_phase41_corvettes.py |
| `test_countermeasures.py` | 19 | 0.1 | pure function | MOVE | renamed from test_phase39_countermeasures.py; helper values and flare cooldown added |
| `test_determinism_subprocess.py` | 1 | 2.4 | bridge end-to-end | MOVE | slow lane |
| `test_dynamic_threats.py` | 5 | 0.0 | world step | KEEP | builders moved to conftest |
| `test_electronic_warfare.py` | 4 | 0.0 | agent | KEEP | builders moved to conftest |
| `test_experiment.py` | 7 | 11.7 | bridge end-to-end | MOVE | 4 batch tests in slow lane |
| `test_flight_attitude_contracts.py` | 6 | 0.0 | pure function | KEEP |  |
| `test_flight_attitude_track_acceptance.py` | 7 | 0.1 | bridge end-to-end | KEEP | `wait_for` deadline removed |
| `test_flight_movement_attitude.py` | 14 | 0.0 | pure function | KEEP |  |
| `test_fog_of_war.py` | 5 | 0.0 | agent | KEEP | `settle_until` instead of deadline waits |
| `test_generation_base_keepout.py` | 7 | 0.1 | generator | KEEP |  |
| `test_generation_map_size.py` | 5 | 0.1 | generator | KEEP |  |
| `test_generation_presets.py` | 13 | 0.0 | generator | KEEP |  |
| `test_generation_schemas.py` | 25 | 0.0 | generator | KEEP |  |
| `test_gui_isolation.py` | 6 | 2.5 | import probe | REWRITE | source-text checks replaced by import probes in a fresh interpreter; absorbed test_viewer3d_isolation.py |
| `test_jet_altitude.py` | 15 | 0.0 | pure function | KEEP |  |
| `test_launcher_cli.py` | 12 | 1.6 | bridge end-to-end | KEEP | one subprocess test in slow lane |
| `test_launcher_dispatch.py` | 8 | 0.0 | pure function | KEEP |  |
| `test_live_stream.py` | 6 | 0.2 | bridge end-to-end | STRENGTHEN | deadline waits replaced by blocking gets with the hang guard |
| `test_main.py` | 6 | 16.8 | bridge end-to-end | MOVE | 3 subprocess tests in slow lane |
| `test_main_run_flags.py` | 8 | 0.2 | bridge end-to-end | KEEP |  |
| `test_metrics.py` | 8 | 0.1 | world step | STRENGTHEN | objective outcomes asserted; source-grep test dropped (golden summary covers it) |
| `test_missiles.py` | 18 | 0.0 | pure function | MOVE | renamed from test_phase38_missiles.py; RCS, firing arc, off-axis step tests added |
| `test_mission_outcomes.py` | 61 | 32.0 | bridge end-to-end | NEW | Part C: outcomes, tick invariants, claims, summary keys, determinism, golden |
| `test_objectives.py` | 10 | 0.0 | pure function | STRENGTHEN | default priority, one-tick SURVIVE, wipe-out flags |
| `test_paths.py` | 4 | 0.0 | pure function | KEEP |  |
| `test_platform_stages.py` | 1 | 0.0 | world step | KEEP |  |
| `test_recorder.py` | 11 | 0.5 | bridge end-to-end | KEEP | `wait_for` deadline removed |
| `test_scenario.py` | 9 | 0.7 | bridge end-to-end | KEEP | `wait_for` deadline removed |
| `test_scenario_feasibility.py` | 17 | 0.1 | generator | KEEP |  |
| `test_scenario_generation_integration.py` | 2 | 0.1 | bridge end-to-end | STRENGTHEN | asserts SOLVED and all targets destroyed; no-exception sample test deleted |
| `test_scenario_generator_enemies.py` | 20 | 7.3 | generator | STRENGTHEN | source-grep test removed; seed matrix in slow lane |
| `test_scenario_generator_friendlies.py` | 25 | 40.5 | generator | MOVE | 5 seed-matrix and speed tests in slow lane |
| `test_scenario_io.py` | 4 | 0.1 | pure function | KEEP |  |
| `test_scenario_schema.py` | 24 | 0.0 | pure function | KEEP |  |
| `test_scenario_v2.py` | 10 | 0.8 | bridge end-to-end | KEEP | `wait_for` deadline removed |
| `test_schemas.py` | 29 | 0.0 | pure function | KEEP |  |
| `test_smoke.py` | 3 | 0.0 | bridge end-to-end | KEEP |  |
| `test_tanks.py` | 15 | 0.0 | world step | MOVE | renamed from test_phase43_tanks.py |
| `test_taxonomy.py` | 5 | 0.0 | pure function | KEEP |  |
| `test_terrain.py` | 33 | 0.0 | pure function | KEEP |  |
| `test_terrain_generator.py` | 21 | 12.9 | generator | MOVE | 4 seed-matrix and speed tests in slow lane |
| `test_terrain_paint.py` | 12 | 0.8 | generator | KEEP | editor test marked gui |
| `test_trace_schemas.py` | 17 | 0.0 | pure function | KEEP |  |
| `test_viewer3d_cameras.py` | 17 | 0.0 | viewer logic | KEEP | many private reads of camera state (see §8) |
| `test_viewer3d_cli.py` | 7 | 0.0 | viewer logic | KEEP |  |
| `test_viewer3d_editor.py` | 48 | 20.9 | viewer with window | STRENGTHEN | gui lane; reads viewer state through public accessors; doc-check test removed |
| `test_viewer3d_flight_dynamics.py` | 11 | 0.0 | viewer logic | STRENGTHEN | source-grep test removed |
| `test_viewer3d_fonts.py` | 6 | 0.0 | viewer logic | KEEP |  |
| `test_viewer3d_ground_meshes.py` | 20 | 0.4 | viewer logic | KEEP |  |
| `test_viewer3d_highlights.py` | 11 | 0.0 | viewer logic | KEEP |  |
| `test_viewer3d_hud_style.py` | 15 | 0.2 | viewer logic | REWRITE | chrome test checks shared colour objects, not source |
| `test_viewer3d_jet_meshes.py` | 10 | 0.1 | viewer logic | KEEP |  |
| `test_viewer3d_launch.py` | 8 | 17.6 | viewer with window | STRENGTHEN | gui lane; blocking waits with hang guard; concurrent-launch assert can now fail |
| `test_viewer3d_motion.py` | 20 | 0.0 | viewer logic | STRENGTHEN | source-grep test removed; speed budget in slow lane |
| `test_viewer3d_motion_live.py` | 7 | 1.0 | viewer with window | STRENGTHEN | gui lane; fake clock; altitude bound from the frames sent |
| `test_viewer3d_playback.py` | 3 | 0.0 | viewer logic | KEEP |  |
| `test_viewer3d_replay.py` | 6 | 0.0 | viewer logic | MOVE | renamed from test_viewer3d_phase49.py |
| `test_viewer3d_ship_meshes.py` | 4 | 0.1 | viewer logic | KEEP |  |
| `test_viewer3d_smoke.py` | 33 | 21.8 | viewer with window | REWRITE | gui lane; fake Panda3D clock; real strike test replaces the failing one |
| `test_viewer3d_stream_client.py` | 3 | 1.1 | viewer logic | STRENGTHEN | blocking gets with the hang guard; retry by count |
| `test_viewer3d_terrain_meshes.py` | 4 | 0.0 | viewer logic | KEEP |  |
| `test_viewer3d_textures.py` | 4 | 0.0 | viewer logic | STRENGTHEN | 2 strict xfails name the sprite path bug (§8) |
| `test_viewer3d_ui_chrome.py` | 42 | 0.0 | viewer logic | KEEP |  |
| `test_viewer_cli.py` | 3 | 0.0 | viewer logic | KEEP |  |
| `test_viewer_controls.py` | 8 | 0.0 | viewer logic | KEEP |  |
| `test_viewer_fonts.py` | 5 | 0.0 | viewer logic | KEEP |  |
| `test_viewer_hud_text.py` | 3 | 0.0 | viewer logic | KEEP |  |
| `test_viewer_playback.py` | 11 | 0.0 | viewer logic | KEEP |  |
| `test_viewer_render.py` | 10 | 0.0 | viewer logic | KEEP |  |
| `test_viewer_smoke.py` | 6 | 3.4 | viewer logic | STRENGTHEN | overlay test asserts title, change and fuel frames; 3-tick pipeline in slow lane |
| `test_viewer_textures.py` | 3 | 0.5 | viewer logic | STRENGTHEN | skip replaced by a strict xfail on the sprite path bug |
| `test_weapon_release_attitude.py` | 11 | 0.0 | pure function | STRENGTHEN | inclusive limits, inverted roll, roll-only envelope |
| `test_world.py` | 67 | 0.0 | world step | KEEP |  |
| `test_viewer3d_isolation.py` | — | — | import probe | DELETE | merged into `test_gui_isolation.py` |

Totals: 86 files, 1,115 tests. KEEP 48, STRENGTHEN 20, MOVE 14, REWRITE 3, NEW 1, DELETE 1.

### 3.1 Tests deleted, and what covers their behaviour now

| Deleted test | What it checked | Covered now by |
|---|---|---|
| `test_viewer3d_isolation.py` (whole file) and the four `*_do_not_import_*` tests in `test_gui_isolation.py` | headless modules do not import Panda3D or pygame, read from source text | `test_gui_isolation.py::test_layer_does_not_load_forbidden_packages[...]`: imports each layer in a fresh interpreter and lists every forbidden package in `sys.modules`, so transitive imports are caught too |
| `test_scenario_generator_enemies.py::test_generator_module_has_no_viewer_imports`, `test_viewer3d_flight_dynamics.py::test_flight_dynamics_module_has_no_panda3d_imports`, `test_viewer3d_motion.py::test_motion_module_has_no_rendering_imports` | same, for one module each | the `wraithnet` and `viewer3d-headless` probe cases above |
| `test_viewer3d_editor.py::test_sync_generation_decision_documented` | a design document contains a phrase | nothing; it protected no behaviour |
| `test_metrics.py::test_legacy_survived_metric_key_is_gone` | no `.py` file contains `jets_survived` | `test_mission_outcomes.py::test_default_scenario_summary_matches_the_golden_file` compares the whole summary, key set included |
| `test_scenario_generation_integration.py::test_dynamic_calibration_sample_no_exceptions` | 20 generated missions run without raising (107 s) | `test_mission_outcomes.py` runs 19 generated missions and asserts their outcome, every tick invariant and their claims |
| `test_viewer3d_smoke.py::test_live_stream_strike_triggers_spawn_strike_effect` | strike effect from a live stream, against a 12 s wall-clock deadline; it failed because the mission releases at tick 19 and the test played 12 ticks | `test_real_sim_strike_triggers_spawn_strike_effect`: records a real 50-tick run and feeds it one tick per frame; asserts exactly one strike effect and the finished view |

### 3.2 The known problem tests from the phase plan

| Test | Now |
|---|---|
| `test_dynamic_calibration_sample_no_exceptions` | deleted, see §3.1 |
| `test_bid_utility.py::test_score_bid_no_nan_inf_at_boundaries` | asserts finite scores in [0, 1] |
| `test_viewer_smoke.py::test_end_of_run_overlay_...` | renamed `..._fuel_bars_draw`; asserts the overlay title, that the surface changed, and three distinct fuel frames |
| `test_viewer3d_ui_chrome.py::test_no_filled_chrome_widget_is_built_with_relief_none` | asserts through `_assert_paints` on every widget (the static scan counted no `assert` keyword; it does assert) |
| `test_viewer3d_editor.py::test_sync_generation_decision_documented` | deleted, see §3.1 |
| `test_viewer3d_hud_style.py::test_chrome_colors_come_from_ui_chrome_but_tactical_colors_stay_local` | rewritten: the RGBA colours are the same objects as in `ui_chrome`; the tactical palette exists only in `hud` |
| `test_viewer3d_textures.py::test_repo_checkout_has_the_gemini_jpegs` | strict xfail that names the sprite path bug (§8); it passes, so turns XPASS and fails, when the sprites are where the loader looks |
| `test_viewer3d_smoke.py::test_live_stream_strike_triggers_spawn_strike_effect` | replaced, see §3.1 |
| `test_phase44_combined_arms.py::test_contested_25_seed_combined_arms_smoke` | now `test_combined_arms.py`, slow lane. It still checks generation only; running those 25 missions is Phase 59's sweep |

## 4. Coverage

Command: `pytest -m "" --cov=wraithnet --cov=viewer --cov=viewer3d --cov=launcher --cov-branch`, run on commit `c50c883` (before the mutation-gap tests of §5 were added, so the numbers for the scoped modules are now a little higher).

| Package | Lines | Line coverage | Branches | Branch coverage |
|---|---:|---:|---:|---:|
| `wraithnet` | 7,540 / 8,351 | 90.3 % | 2,613 / 3,264 | 80.1 % |
| `viewer3d` | 6,415 / 7,687 | 83.5 % | 1,389 / 2,104 | 66.0 % |
| `viewer` | 724 / 898 | 80.6 % | 185 / 288 | 64.2 % |
| `launcher` | 70 / 98 | 71.4 % | 25 / 36 | 69.4 % |
| **Total** | 14,749 / 17,034 | **86.6 %** | 4,212 / 5,692 | **74.0 %** |

Scoped modules:

| Module | Lines | Branches |
|---|---:|---:|
| `agents.py` | 452 / 468 (96.6 %) | 153 / 178 |
| `broker.py` | 53 / 53 (100 %) | 19 / 20 |
| `bridge.py` | 108 / 111 (97.3 %) | 52 / 56 |
| `bid_utility.py` | 269 / 295 (91.2 %) | 87 / 108 |
| `altitude_tactics.py` | 22 / 22 (100 %) | 4 / 4 |
| `weapons_release.py` | 39 / 39 (100 %) | 17 / 18 |
| `objectives.py` | 107 / 111 (96.4 %) | 52 / 56 |
| `countermeasures.py` | 72 / 81 (88.9 %) | 15 / 22 |
| `missiles.py` | 133 / 161 (82.6 %) | 30 / 56 |

Lowest-covered files: `launcher/__main__.py` 0 % (entry point, run only as a subprocess), `viewer/app.py` 19.5 % (the pygame window loop), `viewer3d/textures/loader.py` 22.3 % (the sprite path bug in §8 means most of it never runs), `viewer/textures.py` 46.9 % (same bug), `wraithnet/bases.py` 50 %.

Coverage says which lines ran, not which would fail. Mutation testing (§5) is the judge.

## 5. Mutation testing

### 5.1 Method

`tools/mutation_sample.py` runs cosmic-ray on a **fixed-seed sample** of one module's mutants. A full run is too slow here (for example 1,574 mutants for `bid_utility.py`, each running a test command of several seconds).

1. `cosmic-ray init` writes every mutant to a session database.
2. Mutants inside function annotations are dropped. The modules use postponed annotations, so these are never evaluated and the mutants are equivalent by construction. Class-level field annotations are kept, because pydantic and dataclasses read them.
3. Of the rest, a `random.Random(seed).sample` of N mutants is kept, ordered by what the mutant is (file, line, column, operator, occurrence), not by its random job id. All other rows are deleted from `mutation_specs` and `work_items`.
4. `cosmic-ray exec` runs the kept mutants. The test command is `python -m pytest -x -q -p no:cacheprovider <files>`. The default `addopts` applies, so `slow` and `gui` tests do not run.
5. Each module ran in its own throwaway `git worktree`, all nine in parallel, with a 60 s timeout per mutant. A mutant that hangs past the timeout counts as killed.

**The scores below come from a sample, not a full run.** Sample size: **40 mutants per module** (broker has only 48 after the annotation filter). Seed: **58**.

Sample command, one module (run it from a throwaway worktree, never from the tree you work in):

```bash
git worktree add --detach ../wn-mut-bridge HEAD
cd ../wn-mut-bridge
python tools/mutation_sample.py wraithnet/bridge.py --sample 40 --seed 58 \
    --tests tests/test_bridge.py tests/test_scenario.py tests/test_scenario_v2.py \
            tests/test_recorder.py tests/test_live_stream.py tests/test_mission_outcomes.py \
    --out bridge.json
```

Full run (every mutant of the module): the same command with `--sample 0`.

Test files per module:

| Module | Test files |
|---|---|
| `agents.py` | test_agents, test_cnp, test_cnp_risk_awards, test_agent_attitude_employment, test_dynamic_threats, test_fog_of_war, test_electronic_warfare, test_bridge, test_jet_altitude, test_scenario, test_countermeasures (second run only), test_mission_outcomes |
| `broker.py` | test_broker, test_cnp, test_agents, test_electronic_warfare, test_bridge, test_mission_outcomes |
| `bridge.py` | test_bridge, test_scenario, test_scenario_v2, test_recorder, test_live_stream, test_mission_outcomes |
| `bid_utility.py` | test_bid_utility, test_bid_awards, test_cnp_risk_awards, test_cnp, test_mission_outcomes |
| `altitude_tactics.py` | test_jet_altitude, test_agent_attitude_employment, test_flight_attitude_contracts, test_flight_attitude_track_acceptance, test_flight_movement_attitude, test_mission_outcomes |
| `weapons_release.py` | test_weapon_release_attitude, test_agent_attitude_employment, test_flight_attitude_contracts, test_flight_attitude_track_acceptance, test_mission_outcomes |
| `objectives.py` | test_objectives, test_scenario_v2, test_metrics, test_world, test_mission_outcomes |
| `countermeasures.py` | test_countermeasures, test_missiles, test_mission_outcomes |
| `missiles.py` | test_missiles, test_countermeasures, test_corvettes, test_aaa, test_mission_outcomes |

### 5.2 Scores

First run: commit `c50c883`. Second run: the same sample (same seed) on commit `cfac9f0`, after the tests in §5.3 were added. "By hand" means I applied the mutant to a scratch copy and ran the test that now covers it, after commit `766085e`.

| Module | Mutants | In annotations | Sampled | First run killed | Second run killed | Killed by hand after | Left | Score now |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| `agents.py` | 1,463 | 77 | 40 | 24 | 26 | — | 14: 10 equivalent, 4 gaps | 26 / 40 = 65 % |
| `broker.py` | 59 | 11 | 40 | 36 | 39 | — | 1 | 39 / 40 = 98 % |
| `bridge.py` | 130 | 22 | 40 | 34 | 36 | — | 4 | 36 / 40 = 90 % |
| `bid_utility.py` | 1,574 | 88 | 40 | 27 | 34 | 3 | 3 | 37 / 40 = 93 % |
| `altitude_tactics.py` | 142 | 0 | 40 | 39 | 39 | — | 1 | 39 / 40 = 98 % |
| `weapons_release.py` | 120 | 22 | 40 | 32 | 39 | — | 1 | 39 / 40 = 98 % |
| `objectives.py` | 314 | 66 | 40 | 33 | 37 | — | 3 | 37 / 40 = 93 % |
| `countermeasures.py` | 632 | 33 | 40 | 23 | 40 | — | 0 | 40 / 40 = 100 % |
| `missiles.py` | 1,113 | 33 | 40 | 30 | 38 | 1 | 1 | 39 / 40 = 98 % |

Broker counts include 12 mutants killed by the 60 s timeout in both runs (they make a negotiation wait forever). Every survivor left in the other modules is equivalent. For `agents.py`, 14 remain: 10 equivalent and 4 real gaps, all listed in §5.4 and handed on in §9.

The two survivors known from the Phase 57 hand check are killed:

| Mutant | Killed by |
|---|---|
| `_notify_managers_of_losses(...)` in `WraithNetBridge._tick` replaced by `pass` | `test_bridge.py::test_contractor_shot_down_mid_contract_is_replaced_through_tick` (checked by hand: fails with the mutant) |
| notch heading = threat bearing instead of bearing ± 90° | `test_agent_attitude_employment.py::test_notch_heading_is_perpendicular_to_the_threat_bearing` (fails in all 5 bearings with the mutant) |

### 5.3 Survivors of the first run that are now killed

| Module | Line | Mutant | Killed by |
|---|---:|---|---|
| bridge | 78 | jetless tick skips the sinks | `test_jetless_bridge_ticks_world_with_zero_cnp_runtimes` (sink assertion) |
| bridge | 114 | `continue` → `break` in the end-of-mission recall | `test_one_jet_at_bingo_does_not_stop_the_end_of_mission_recall_of_the_others` |
| broker | 57 | `receiver_id == BROADCAST_ID` → `<=` | `test_direct_message_to_an_id_sorting_before_broadcast_is_not_broadcast` |
| broker | 104 | TTL `>` → `>=`, and `-` → `>>` | `test_claim_expires_only_after_more_than_ttl_ticks` |
| agents | 938 | flare cooldown `<= 0` → `!= 0` | `test_agent_defensive_priority_and_flare` (already existed; the first run did not include `test_countermeasures.py` for `agents.py`) |
| agents | 938 | flare cooldown `<= 0` → `<= 1` | `test_agent_does_not_flare_while_the_flare_cooldown_runs` |
| weapons_release | 15 | `frozen=True` → `False` | `test_release_envelope_limits_are_inclusive` |
| weapons_release | 44, 54, 80 | `>` → `>=` and `<=` → `<` on the altitude and pitch limits | same test |
| weapons_release | 48 | inverted at 91° instead of 90° | same test |
| weapons_release | 87, 88 | `or` → `and` in `weapon_has_attitude_envelope` | same test |
| objectives | 24 | default priority 1.5 (pydantic does not validate defaults) | `test_objective_defaults_and_single_tick_survive_are_valid` |
| objectives | 30 | SURVIVE needs `until_tick >= 2` | same test |
| objectives | 170, 182 | default `any_friendly_returned=True`; empty mission reports a wipe-out | `test_wipeout_without_a_returned_jet_is_reported_as_wiped_out` |
| bid_utility | 64, 71 | flare readiness during cooldown | `test_risk_terms_give_the_designed_values` |
| bid_utility | 119 | `*` → `**` in gun risk | same test |
| bid_utility | 132 | `-` → `+` in combined risk | same test |
| bid_utility | 269, 275 | attack-plan tick counts | `test_no_unreserved_hp_rejects_every_bid` |
| bid_utility | 594 | `raw_need <= 0` → `<= -1` | same test |
| bid_utility | 569, 570, 572 | award score: team-size term, overkill term, recovery tie-break | `test_award_score_components_follow_the_documented_formula` (by hand) |
| missiles | 117 (`/`), 118, 119 | firing-arc bearing and delta | `test_firing_arc_is_measured_from_the_shooter_heading` |
| missiles | 129, 132 (×2) | RADAR branch and minimum RCS | `test_radar_missile_needs_the_minimum_radar_cross_section` |
| missiles | 182 (×2) | closest approach of a missile that did not move | `test_closest_approach_of_a_missile_that_did_not_move` |
| missiles | 224 | `ux * step` → `ux / step` | `test_missile_moves_its_speed_per_tick_toward_the_target` (by hand) |
| countermeasures | all 17 | range, terminal threshold, notch headings, notch minimum speed, flare and notch probabilities, roll comparisons | `test_countermeasure_helpers_give_the_designed_values`, `test_notch_needs_minimum_speed_even_when_perpendicular`, `test_notch_break_roll_respects_certain_probabilities` |

### 5.4 Survivors left, with the reason

Equivalent means no input the program can produce tells the mutant apart from the original.

| Module | Line | Mutant | Reason |
|---|---:|---|---|
| bridge | 32 | `getattr(..., "combat_seed", -1)` | equivalent: inside an `assert`, and `ScenarioConfig` always has `combat_seed` |
| bridge | 116 | `role == "RTB"` → `>` | equivalent: no role sorts after `"RTB"`, so RTB jets are not skipped, but `begin_rtb()` is idempotent and they are already RTB |
| bridge | 125 | `==` → `is` on `"IDLE"` | equivalent in CPython: short identifier-like string constants are interned |
| bridge | 138 | set `-` → `^` | equivalent: a friendly jet is never added during `model.step`, so the symmetric difference equals the difference |
| broker | 59 | `!=` → `is not` on agent ids | equivalent in CPython for these interned ids |
| altitude_tactics | 61 | `==` → `is` on `"SAM_SITE"` | equivalent in CPython (interning) |
| objectives | 139, 203 | `==` → `is` on string literals | equivalent in CPython (interning) |
| objectives | 254 | `or` → `and` in `mission_aborted` | equivalent: the second clause is a subset of the first, and ABORTED is only produced when `no_friendly_platforms` is true |
| weapons_release | 113 | `max(-cap, ...)` → `max(not cap, ...)` | equivalent: `delivery_pitch` is the constant 0.0, so the clamp always gives 0 |
| missiles | 117 | `% 360` → `+ 360` on the bearing | equivalent: the next line wraps the delta with `% 360` again |
| bid_utility | 259 | default `target_altitude = -1.0` | equivalent: the parameter is unused (`_ = target_altitude`) |
| bid_utility | 331 | `ir_exp > 0` → `> -1` | equivalent: with zero IR exposure the IR risk is zero whatever the flare term |
| bid_utility | 350 | `count <= 0` → `== 0` | equivalent: payload counts are never negative |
| agents | 74 | direct threat contact confidence 2.0 | equivalent in practice: confidence only breaks ties between contacts of the same tick and source, and every direct contact gets the same value |
| agents | 227 | message sequence `+= 2` | equivalent: ids only need to be unique and stable |
| agents | 339 | `== "RISK_UPDATE"` → `>=` | equivalent: no message type sorts after `"RISK_UPDATE"` |
| agents | 868 | `role == "RTB"` → `>=` | equivalent: no role sorts after `"RTB"` |
| agents | 942 | `==` → `is` on `"RADAR"` | equivalent in CPython (interning) |
| agents | 1142 | gate standoff floor `-0.999` | equivalent: every weapon range is at least 0.5, so `range * 0.5` always wins |
| agents | 563, 569, 573, 579 | placeholder plan, position, hp and radar risk in the legacy-bid adapter of `_resolve_contract` | equivalent: award scoring reads only utility, recovery, combined risk and damage from these placeholders |
| agents | 589 | `offered * damage * 1000` → `offered \| damage * 1000` | **gap, small**: changes the legacy-bid damage by 1 milli-hp, which no test observes |
| agents | 343 | risk-update context hash `or ""` → `and ""` | **gap**: with the hash blanked, the stale-context check in `select_winning_bids` is skipped; no test sends a RISK_UPDATE and then a bid with an old hash through an agent |
| agents | 441 | managed target found with `<=` instead of `==` | **gap**: no test has a manager that knows several targets and manages one that does not sort first |
| agents | 1189 | skip dead targets with `< 0` instead of `<= 0` | **gap, not verified**: no test shows whether a target at 0 hp can still reach `scan_and_negotiate`; I did not trace why it survives |

The four gaps are handed to Phase 59, which changes this code (§9).

## 6. Outcome tests (Part C)

All in `tests/test_mission_outcomes.py`, marker `e2e`. Generated missions run in the slow lane on **19 fixed seeds**: TRAINING 0–7, STANDARD 0–7, CONTESTED 0 and 6, NIGHTMARE 5.

| Test | Lane | What it asserts |
|---|---|---|
| `test_generated_mission_ends_with_an_explicit_outcome` | slow | the mission ends before the tick cap with SOLVED, FAILED or ABORTED. 5 seeds stall (STANDARD 0, 2, 6; CONTESTED 0; NIGHTMARE 5) and are strict xfails for Phase 59 |
| `test_generated_mission_keeps_every_tick_invariant` | slow | every tick, through one helper `tick_invariant_violations`: no target hp below 0; no payload below 0; fuel never rises in flight; no jet both RTB and committed; no strike on a target the jet does not sense; no release outside range, arc, altitude or attitude envelope |
| `test_every_open_claim_names_a_live_manager` | slow | every open claim names a live manager. 4 seeds fail (STANDARD 1, 4; CONTESTED 0; NIGHTMARE 5) and are strict xfails for Phase 59 |
| `test_every_summary_key_moves_off_its_default_in_some_fixture_run` | slow | each `summarize()` key differs from its default in at least one fixture run (the generated missions plus a forced wipe-out and a forced abort). 15 keys are not reached and are listed by name in `NOT_REACHED_BY_FIXTURES`, so the set cannot grow or shrink silently |
| `test_default_scenario_keeps_every_tick_invariant` | default | the same helper on `scenarios/default.json` |
| `test_same_scenario_twice_gives_the_same_summary_and_trace` | default | same summary and same trace hash for two runs in one process |
| `test_default_scenario_summary_matches_the_golden_file` | default | the full summary equals `tests/golden/default_summary.json`. Update on purpose with `WRAITHNET_UPDATE_GOLDEN=1 pytest tests/test_mission_outcomes.py -k golden` |

Check of the helper: with both the agent `in_range` gate and the world range check broken by hand, it reports the long releases.

Summary keys not reached by any fixture run (the second column says where they are tested at all):

| Key | Tested |
|---|---|
| `agm84_hits`, `agm84_launches`, `ciws_intercepts`, `corvette_losses` | unit tests in `test_corvettes.py` |
| `carrier_recoveries` | unit tests in `test_carriers.py` |
| `notch_completions`, `notch_lock_breaks` | unit tests in `test_countermeasures.py` |
| `aaa_kills`, `aircraft_losses_to_aaa`, `agm84_hit_rate`, `deck_gun_hits`, `jets_crashed`, `missile_fuel_expired`, `missile_intercepted`, `missile_misses` | **no test** |

## 7. Waits, clocks and the hang guard

A deadline that decides the result is a broken test: a busy machine fails it. This phase removed them:

* 27 `asyncio.wait_for(bridge.run(...), timeout=...)` wrappers removed. A bridge run is bounded by `max_ticks`.
* Agent and fog-of-war tests wait with `settle_until(predicate, rounds=1024)` from `tests/async_helpers.py`: a count of event-loop rounds, not seconds.
* The 3D viewer tests run on a fake Panda3D clock (fixture `panda_fake_clock`): `ClockObject` in non-real-time mode at 20 fps, so each `task_mgr.step()` advances exactly 1/20 s, and `viewer3d.app.time.monotonic` reads that clock.
* Socket and process waits are blocking calls with a **hang guard**, `HANG_GUARD_S = 60` in `tests/conftest.py`. The guard only stops a hung test. It is far longer than any normal wait and never decides a pass.

Hang guards that remain, by design: the `test_main.py` subprocess timeout (120 s), the determinism subprocess timeout (300 s), `_step_until` in `test_viewer3d_smoke.py`, and the stream and launch waits (`HANG_GUARD_S`).

Speed budgets that remain (these assert on wall time on purpose, and run in the slow lane only): `test_scenario_generator_friendlies.py::test_generation_performance_standard_and_max`, `test_terrain_generator.py::test_generation_performance_budget`, `test_viewer3d_motion.py::test_push_sample_1000_ticks_under_ci_ceiling`.

## 8. Findings outside the tests

| Finding | Where | Owner |
|---|---|---|
| Open claims outlive their dead or landed manager until the claim TTL; seen on 4 of 19 generated missions. Likely a cause of the stalls | broker / agents | Phase 59 |
| 5 of 19 generated missions stall at the tick cap | CNP | Phase 59 |
| Tank firing arc in `world._apply_strike` uses `atan2(dy, dx)` (math convention) while headings use 0° = +y clockwise. Only reachable with a gun pod (60° arc) against a tank; no test or scenario hits it | `wraithnet/world.py` around line 2811 | needs an owner; it changes outcomes, so not fixed here |
| Unit sprites never load: `SPRITE_FILES` points at `assets/textures/<name>.jpeg`, the sprites are in `assets/textures/pics/`. Terrain textures load | `viewer3d/textures/loader.py`, `viewer/textures.py` | the viewer; 3 strict xfails flip when it is fixed |
| No guided weapon has a firing arc under 360°, so the arc check in `missiles.validate_launch` is unreachable with the real catalog | `wraithnet/missiles.py` | none; now tested with a patched catalog |
| The world range check for releases is redundant: the agent already gates on range | `wraithnet/world.py` | none |
| 8 summary keys have no test at all (§6) | `wraithnet/metrics.py` | the phases that next touch AAA, naval and missiles |
| `test_combined_arms.py::test_contested_25_seed_combined_arms_smoke` checks that 25 missions are generated feasible; it never runs them | tests | Phase 59 sweep |
| `test_viewer3d_cameras.py`, `test_viewer3d_hud_style.py` and `test_viewer3d_ui_chrome.py` still read many private attributes of helper objects (cameras, HUD widgets). The viewer class now has read-only accessors for its own state; the helpers do not | tests | later viewer work |

## 9. Handoffs

| To | What |
|---|---|
| Phase 59 | turn the 9 strict xfails in `test_mission_outcomes.py` into passes (they fail loudly as XPASS when fixed); the claim-outlives-manager defect; the four agent gaps of §5.4 (context hash, managed target lookup, dead targets in the scan, legacy-bid damage) |
| Phase 59 / 61 | run the 25 CONTESTED missions of `test_combined_arms.py` in the sweep instead of only generating them |
| Viewer owner | sprite path bug (3 strict xfails) |
| Combat owner | tank firing-arc convention in `_apply_strike` |
| Anyone | a full mutation run (`--sample 0`) of the scoped modules, and a first run of the CI workflow |

## 10. CI

`.github/workflows/tests.yml`: Windows and Linux, Python 3.14. On push and pull request it runs the default lane. Nightly (and on manual dispatch) it runs the full lane: `pytest -m ""` on Windows, `pytest -m "not gui"` on Linux, because the gui lane needs a display server there. pygame uses the SDL dummy driver.

**The workflow has not been executed.** It was written in this phase and has never run on GitHub.

## 11. Reproducing

```bash
pip install -r requirements-dev.txt -r requirements-3d.txt -r requirements-viz.txt
pytest                      # default lane
pytest -m ""                # full lane
pytest -m "" --cov=wraithnet --cov=viewer --cov=viewer3d --cov=launcher --cov-branch
pytest -m "" --durations=0  # per-test times for the §3 table
python tools/mutation_sample.py <module> --sample 40 --seed 58 --tests <files> --out <json>   # from a worktree
```
