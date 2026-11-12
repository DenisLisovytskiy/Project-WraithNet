# Post-Phase Changes Record

> **Status (2026-11, Phase 56):** retrospective record. Everything listed here is in the code on `main`. None of it had a phase document when it was built; most of it had no design document either. This file exists so that a reader of `docs/phases/` is not misled about what the code does.
>
> Each section says what the change does, where it lives, which commit added it, and which tests cover it. "No dedicated test" means a search of `tests/` found nothing that names the feature; it may still be exercised indirectly.

## 1. Mission-brief transit and end-of-mission recall

* **Commit:** `e3a66fe`
* **What:** A jet with no task flies to the nearest position named in the mission brief instead of waiting where it spawned. The world puts the positions of the briefed DESTROY subjects into `Observation.briefed_seek_positions`; local sensing still decides what the jet actually knows on arrival. When every required DESTROY objective is complete, the bridge calls `begin_rtb()` on every jet so the mission ends with the swarm going home.
* **Where:** `wraithnet/world.py` (`_briefed_seek_positions`, `required_destroy_objectives_complete`), `wraithnet/agents.py` (`decide_intent`, `begin_rtb`), `wraithnet/bridge.py` (`_tick`, step 1e), `wraithnet/objectives.py`, `wraithnet/schemas.py`.
* **Tests:** `tests/test_agents.py` (`test_decide_intent_idle_seeks_briefed_destroy_waypoint`, `test_out_of_sensor_range_jet_moves_toward_briefed_bunker`), `tests/test_objectives.py`.
* **Note:** the recall is issued by the bridge from ground truth, not by a radio message. Phase 62 Part D replaces it.

## 2. Shared UI chrome, military restyle, textures, markers, fonts, highlights

* **Commits:** `1acdf4f` … `fe096c4`, `1bbfaed`
* **What:** One module owns the look of every panel, button and label in the 3D viewer and editor. Bundled fonts (JetBrains Mono, Saira Condensed, both OFL). Per-vertex baked paint for the three airframes and the ground units, unit markers, and selection highlights.
* **Where:** `viewer3d/ui_chrome.py`, `viewer3d/fonts.py`, `viewer/fonts.py`, `viewer3d/highlights.py`, `viewer3d/textures/`, `viewer3d/hud.py`, `viewer3d/scene.py`, `assets/fonts/`.
* **Tests:** `tests/test_viewer3d_ui_chrome.py`, `tests/test_viewer3d_hud_style.py`, `tests/test_viewer3d_fonts.py`, `tests/test_viewer_fonts.py`, `tests/test_viewer3d_highlights.py`, `tests/test_viewer3d_textures.py`, `tests/test_viewer_textures.py`, `tests/test_viewer3d_jet_meshes.py`.

## 3. Configurable battlespace size and map-scaled claim TTL

* **Commit:** `ea2d9ff`
* **What:** The generated map size is a generation parameter instead of a constant. The contract claim time limit grows with the map diagonal, so a contractor on a large map is not timed out while still in transit.
* **Where:** `wraithnet/generation_schemas.py`, `wraithnet/generation_presets.py`, `wraithnet/scenario_generator.py`, `wraithnet/config.py` (`contract_claim_ttl_for_grid`), `wraithnet/bridge.py`, `wraithnet/broker.py`.
* **Tests:** `tests/test_generation_map_size.py`, `tests/test_generation_schemas.py`. The claim TTL itself is covered in `tests/test_broker.py`; there is no dedicated test for the scaling function.

## 4. Performance on a 5000×5000 map

* **Commit:** `8783bb3`
* **What:** Three quadratic loops in generation were replaced with linear ones. Terrain cell size is derived from the map size, so a large map does not get millions of cells. Two placement bugs that only showed at large cell sizes were fixed. `GENERATOR_VERSION` went to 4 because a seed now yields a different valid layout.
* **Where:** `wraithnet/terrain_generator.py`, `wraithnet/terrain.py` (`terrain_cell_size_for_grid`), `wraithnet/scenario_generator.py`, `viewer3d/terrain_mesh.py`, `viewer3d/world_scale.py`.
* **Tests:** `tests/test_generation_map_size.py`, `tests/test_viewer3d_terrain_meshes.py`, `tests/test_scenario_generator_enemies.py`.

## 5. Friendly-base keep-out and area-of-operations spread

* **Commit:** `eafb3a2`
* **What:** No enemy unit of any kind may be generated near the friendly base. The keep-out distance grows with the map diagonal. DISPERSED group centres are fanned out to avoid empty wedges of map.
* **Where:** `wraithnet/scenario_generator.py`, `wraithnet/scenario_feasibility.py`, `wraithnet/generation_schemas.py`, `wraithnet/generation_presets.py`. Doc 18 was amended in the same commit.
* **Tests:** `tests/test_generation_base_keepout.py`, `tests/test_scenario_generator_enemies.py`.

## 6. Camera fixes and F11 fullscreen

* **Commits:** `62022d7`, `bebb783`
* **What:** The orbit camera pans along its own view axes, eases in and out, and is independent of frame rate. F11 toggles a borderless window that covers the taskbar.
* **Where:** `viewer3d/cameras.py`, `viewer3d/app.py` (`_toggle_fullscreen`).
* **Tests:** `tests/test_viewer3d_cameras.py`. No dedicated test for the F11 toggle.

## 7. Jets keep airspeed when idle and at the map edge

* **Commit:** `89afd44`
* **What:** An `IDLE` intent used to freeze the jet in mid-air for that tick. It now coasts along the nose while heading, pitch and roll still steer. A jet flying into the map edge is reflected instead of stopped.
* **Where:** `wraithnet/world.py` (`stage_apply_movement`, `_apply_move`).
* **Tests:** `tests/test_world.py`, `tests/test_flight_movement_attitude.py`, `tests/test_agent_attitude_employment.py`.

## 8. Altitude as a tactical factor

* **Commits:** `464add7`, `d5e1f2a`
* **What:** Altitude decides what can shoot a jet and what a jet can release. This is the largest change in this record and it changes agent behaviour and feasibility. It is described in full in [doc 22 §14](22_Authoritative_3D_Flight_Attitude_Design_Document.md).
* **Where:** `wraithnet/altitude_tactics.py`, `wraithnet/terrain.py`, `wraithnet/weapons_release.py`, `wraithnet/world.py`, `wraithnet/agents.py`, `wraithnet/bid_utility.py`, `wraithnet/scenario_feasibility.py`, `wraithnet/schemas.py` (`AltitudeRules`).
* **Tests:** `tests/test_jet_altitude.py`, `tests/test_scenario_feasibility.py`, `tests/test_bid_utility.py`, `tests/test_world.py`.

## 9. Holding orbit on station

* **Commit:** `c7c2919`
* **What:** An untasked jet that has reached its briefed waypoint flies a wide orbit (three full-speed turn radii) at 60% airspeed instead of looping tightly over the point. `ActionIntent` gained `speed_fraction`; the world eases it by 0.1 per tick, and a notch manoeuvre always flies at full speed.
* **Where:** `wraithnet/agents.py`, `wraithnet/config.py` (`LOITER_RADIUS_TURN_RADII`, `LOITER_SPEED_FRACTION`), `wraithnet/schemas.py`, `wraithnet/world.py`.
* **Tests:** `tests/test_agents.py` (`test_untasked_jet_on_station_flies_wide_slow_orbit`).

## 10. Names in phase documents that do not match the code

Old phase documents are the historical record and are not edited. These two names in them are wrong:

| Phase document | Says | In the code |
|---|---|---|
| Phase 15 | tests live in `tests/test_main_cli.py` | they live in `tests/test_main.py` |
| Phase 38 | a `MissileTrackEvent` schema | never created. Missile state is on the trace as missile frames and in `model.missile_events` |

## 11. Repository decisions taken in Phase 56

* **Generated scenarios are not committed.** `scenarios/custom.json` and `scenarios/custom2.json` were editor output that had been committed: about 6,800 and 40,500 lines, because terrain cells are stored expanded and pretty-printed. Nothing in the code or the tests reads them; the editor saves to the per-user data folder (`user_data_dir("scenarios")`), not to the repository. They are removed from version control and `scenarios/custom*.json` is ignored. `scenarios/default.json`, the hand-authored PoC mission, stays. The scenario schema is unchanged; a compact terrain encoding (seed plus parameters) was considered and not done, because it would change the schema for no user-visible gain once the files are not committed.
* **`networkx` stays in `requirements.txt`.** Nothing imports it today. Phase 65 Part B uses it for the route graph (`networkx.astar_path`), so it is kept and the requirement line now carries a comment saying so. If Phase 65 ends up not using it, that phase removes it.
* **`pyproject.toml` and CI** arrive in Phase 58 Part D. **`results/` retention** is Phase 57 Part E.
