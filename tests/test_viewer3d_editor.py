"""Headless tests for viewer3d.editor (Phase 18).

Uses the same window-type none config as tests/test_viewer3d_smoke.py.
"""

from __future__ import annotations

import copy

import pytest
from pydantic import ValidationError
from pathlib import Path

pytest.importorskip("panda3d")

from panda3d.core import NodePath, loadPrcFileData

loadPrcFileData("", "window-type none")
loadPrcFileData("", "audio-library-name null")
loadPrcFileData("", "notify-level error")

from wraithnet import config as wn_config
from wraithnet.scenario_io import load_scenario, save_scenario
from wraithnet.scenario_schema import (
    JammerSpec,
    JetSpec,
    SamSiteSpec,
    ScenarioConfig,
    TargetSpec,
    new_scenario_data,
    scenario_from_data
)
from viewer3d.editor import (
    OrthoEditorCamera,
    _humanize_validation_error,
    ordnance_slots_used,
    scenario_to_header,
    slots_usage_text,
)


def _destroy_app(app) -> None:
    try:
        app._client.stop()
    except Exception:
        pass
    try:
        app.destroy()
    except Exception:
        pass


def _invoke(button) -> None:
    """Fire a DirectGUI button the same way a click would."""
    command = button["command"]
    extra = button["extraArgs"] or ()
    command(*extra)


def _editor_app():
    from viewer3d.app import WraithNetViewer3D

    return WraithNetViewer3D(editor_mode=True, start_client=False)


def test_screen_to_world_corners_and_center():
    dummy = type("B", (), {})()
    dummy.camera = None
    dummy.render = NodePath("r")
    dummy.cam = None
    dummy.mouseWatcherNode = None
    cam = OrthoEditorCamera(dummy, 100.0, 100.0)
    assert cam.screen_to_world(0.0, 0.0) == pytest.approx((50.0, 50.0))
    assert cam.screen_to_world(-1.0, -1.0) == pytest.approx((0.0, 0.0))
    assert cam.screen_to_world(1.0, 1.0) == pytest.approx((100.0, 100.0))
    assert cam.screen_to_world(1.0, -1.0) == pytest.approx((100.0, 0.0))
    assert cam.screen_to_world(-1.0, 1.0) == pytest.approx((0.0, 100.0))


def test_begin_placement_then_click_adds_each_kind():
    app = _editor_app()
    try:
        editor = app._editor
        editor.begin_placement("jet")
        editor.on_ground_clicked((30.0, 40.0))
        assert len(editor.scenario_draft["jets"]) == 1
        assert editor.scenario_draft["jets"][0]["position"] == pytest.approx((30.0, 40.0))

        editor.begin_placement("target")
        editor.on_ground_clicked((10.0, 20.0))
        assert len(editor.scenario_draft["targets"]) == 1
        assert editor.scenario_draft["targets"][0]["position"] == pytest.approx((10.0, 20.0))

        editor.begin_placement("sam_site")
        editor.on_ground_clicked((55.0, 60.0))
        assert len(editor.scenario_draft["sam_sites"]) == 1
        assert editor.scenario_draft["sam_sites"][0]["target_type"] == "SAM_SITE"

        editor.begin_placement("jammer")
        editor.on_ground_clicked((70.0, 15.0))
        assert len(editor.scenario_draft["jammers"]) == 1
        assert editor.scenario_draft["jammers"][0]["position"] == pytest.approx((70.0, 15.0))
    finally:
        _destroy_app(app)


def test_save_without_required_objective_returns_errors_and_does_not_write(tmp_path):
    app = _editor_app()
    try:
        editor = app._editor
        # Empty draft: no targets/SAMs → no required DESTROY objective.
        dest = tmp_path / "no_objectives.json"
        errors = editor.save(str(dest))
        assert errors is not None
        assert any("objective" in msg.lower() for msg in errors)
        assert not dest.exists()
    finally:
        _destroy_app(app)


def test_save_jetless_with_target_is_allowed(tmp_path):
    app = _editor_app()
    try:
        editor = app._editor
        editor.begin_placement("target")
        editor.on_ground_clicked((10.0, 10.0))
        dest = tmp_path / "jetless.json"
        assert editor.save(str(dest)) is None
        loaded = load_scenario(dest)
        assert loaded.jets == []
        assert len(loaded.targets) == 1
        assert any(o.required for o in loaded.mission_objectives)
    finally:
        _destroy_app(app)


def test_save_valid_draft_round_trips(tmp_path):
    app = _editor_app()
    try:
        editor = app._editor
        editor.begin_placement("jet")
        editor.on_ground_clicked((30.0, 40.0))
        editor.begin_placement("target")
        editor.on_ground_clicked((70.0, 80.0))
        dest = tmp_path / "ok.json"
        assert editor.save(str(dest)) is None
        loaded = load_scenario(dest)
        assert loaded.jets[0].position == pytest.approx((30.0, 40.0))
        assert loaded.targets[0].position == pytest.approx((70.0, 80.0))
        assert loaded.jets[0].agent_id == editor.scenario_draft["jets"][0]["agent_id"]
        from wraithnet.world import WraithNetModel

        model = WraithNetModel(scenario=loaded)
        assert loaded.jets[0].agent_id in model.jets
    finally:
        _destroy_app(app)


def test_load_replaces_draft_and_scene(tmp_path):
    app = _editor_app()
    try:
        editor = app._editor
        editor.begin_placement("jet")
        editor.on_ground_clicked((5.0, 5.0))
        editor.begin_placement("jammer")
        editor.on_ground_clicked((8.0, 8.0))
        assert len(editor.scenario_draft["jets"]) == 1
        assert len(editor.scenario_draft["jammers"]) == 1

        other = scenario_from_data(new_scenario_data(
            jets=[
                JetSpec(
                    agent_id="Loaded_Jet",
                    position=(22.0, 33.0),
                    payload={"JDAM": 2, "AIM9X": 1},
                )
            ],
            targets=[
                TargetSpec(
                    target_id="Loaded_Bunker",
                    position=(40.0, 41.0),
                    hp_required=2,
                    target_type="HARDENED_BUNKER",
                )
            ],
            sam_sites=[
                SamSiteSpec(
                    target_id="Loaded_SAM",
                    position=(50.0, 50.0),
                    hp_required=1,
                )
            ],
            jammers=[JammerSpec(jammer_id="Loaded_Jam", position=(12.0, 13.0))],
        ))
        path = tmp_path / "loaded.json"
        save_scenario(other, path)
        editor.load(str(path))

        assert [j["agent_id"] for j in editor.scenario_draft["jets"]] == ["Loaded_Jet"]
        assert [t["target_id"] for t in editor.scenario_draft["targets"]] == ["Loaded_Bunker"]
        assert [s["target_id"] for s in editor.scenario_draft["sam_sites"]] == ["Loaded_SAM"]
        assert [j["jammer_id"] for j in editor.scenario_draft["jammers"]] == ["Loaded_Jam"]
        assert "Jet_1" not in [j["agent_id"] for j in editor.scenario_draft["jets"]]

        handles = editor._handles
        assert handles is not None
        assert set(handles.jets) == {"Loaded_Jet"}
        assert set(handles.targets) == {"Loaded_Bunker", "Loaded_SAM"}
        assert set(handles.jammers) == {"Loaded_Jam"}
        pos = handles.jets["Loaded_Jet"].get_pos()
        assert (pos.x, pos.y) == pytest.approx((22.0, 33.0))
        assert not handles.jets["Loaded_Jet"].is_hidden()
    finally:
        _destroy_app(app)


def _error_dict_for(invalid_kwargs: dict) -> dict:
    with pytest.raises(ValidationError) as exc_info:
        ScenarioConfig.model_validate(invalid_kwargs)
    return exc_info.value.errors()[0]


def _valid_kwargs(**overrides) -> dict:
    data = {
        "jets": [
            {
                "agent_id": "Jet_1",
                "position": (10.0, 10.0),
                "payload": {"JDAM": 1, "AIM9X": 0},
            }
        ],
        "targets": [
            {
                "target_id": "Target_A",
                "position": (50.0, 50.0),
                "hp_required": 1,
                "target_type": "HARDENED_BUNKER",
            }
        ],
    }
    data.update(overrides)
    return new_scenario_data(**data)


def test_humanize_validation_error_for_each_phase14_rule():
    cases = [
        (_valid_kwargs(mission_objectives=[]), "objective"),
        (
            _valid_kwargs(
                jets=[
                    {
                        "agent_id": "Shared_ID",
                        "position": (10.0, 10.0),
                        "payload": {"JDAM": 1, "AIM9X": 0},
                    }
                ],
                targets=[
                    {
                        "target_id": "Shared_ID",
                        "position": (50.0, 50.0),
                        "hp_required": 1,
                        "target_type": "HARDENED_BUNKER",
                    }
                ],
            ),
            "unique",
        ),
        (
            _valid_kwargs(
                jets=[
                    {
                        "agent_id": "Jet_1",
                        "position": (150.0, 10.0),
                        "payload": {"JDAM": 1, "AIM9X": 0},
                    }
                ]
            ),
            "outside",
        ),
        (
            _valid_kwargs(
                targets=[
                    {
                        "target_id": "Target_A",
                        "position": (50.0, 50.0),
                        "hp_required": 0,
                        "target_type": "HARDENED_BUNKER",
                    }
                ]
            ),
            "hp_required",
        ),
        (
            _valid_kwargs(
                jets=[
                    {
                        "agent_id": "Jet_1",
                        "position": (10.0, 10.0),
                        "payload": {"JDAM": -1, "AIM9X": 0},
                    }
                ]
            ),
            "negative",
        ),
    ]
    for kwargs, needle in cases:
        err = _error_dict_for(kwargs)
        text = _humanize_validation_error(err)
        assert text.strip()
        assert "Traceback" not in text
        assert "ValidationError" not in text
        assert needle.lower() in text.lower()


def test_preview_valid_draft_enters_brief():
    app = _editor_app()
    try:
        assert app.view_mode == "editor"
        editor = app._editor
        editor.begin_placement("jet")
        editor.on_ground_clicked((20.0, 25.0))
        editor.begin_placement("target")
        editor.on_ground_clicked((60.0, 65.0))
        assert editor.preview() is None
        assert app.view_mode == "brief"
        assert app.camera_mode == "brief"
        assert app._header is not None
        assert app._handles is not None
        assert "Jet_1" in app._header.jet_ids
        assert "Target_1" in app._header.target_ids
        assert not app._handles.root.find("**/target-Target_1").is_empty()
    finally:
        _destroy_app(app)


def test_preview_invalid_draft_keeps_editor_mode():
    app = _editor_app()
    try:
        editor = app._editor
        # Empty draft has no required objective → invalid.
        errors = editor.preview()
        assert errors is not None
        assert any("objective" in msg.lower() for msg in errors)
        assert app.view_mode == "editor"
    finally:
        _destroy_app(app)


def test_scenario_to_header_matches_config_constants_and_entities():
    config = scenario_from_data(new_scenario_data(
        jets=[
            JetSpec(
                agent_id="Alpha",
                position=(11.0, 12.0),
                payload={"JDAM": 3, "AIM9X": 1},
            )
        ],
        targets=[
            TargetSpec(
                target_id="Bunker_1",
                position=(70.0, 71.0),
                hp_required=4,
                target_type="HARDENED_BUNKER",
            )
        ],
        sam_sites=[
            SamSiteSpec(
                target_id="SAM_X",
                position=(40.0, 41.0),
                hp_required=2,
            )
        ],
        jammers=[JammerSpec(jammer_id="Jam_X", position=(15.0, 16.0))],
    ))
    header = scenario_to_header(config)
    assert header.sensor_radius == wn_config.SENSOR_RADIUS
    assert header.jam_radius == wn_config.JAM_RADIUS
    assert header.sam_engagement_range == wn_config.SAM_ENGAGEMENT_RANGE
    assert header.strike_range == wn_config.WEAPON_CATALOG["JDAM"].range
    assert header.grid_width == config.grid_width
    assert header.grid_height == config.grid_height
    assert header.jet_ids == ["Alpha"]
    assert header.initial_payload["Alpha"] == {"JDAM": 3, "AIM9X": 1}
    assert header.target_ids == ["Bunker_1", "SAM_X"]
    assert header.target_types["Bunker_1"] == "HARDENED_BUNKER"
    assert header.target_types["SAM_X"] == "SAM_SITE"
    assert header.target_hp_required["Bunker_1"] == 4
    assert header.sam_site_ids == ["SAM_X"]
    assert header.jammer_positions["Jam_X"] == pytest.approx((15.0, 16.0))
    assert header.target_positions["SAM_X"] == pytest.approx((40.0, 41.0))
    assert header.terrain is not None
    assert header.trace_version == 3
    assert header.terrain.world_width == config.grid_width


def test_directgui_add_jet_command_arms_placement():
    app = _editor_app()
    try:
        editor = app._editor
        _invoke(editor._btn_add_jet)
        assert editor._placement_kind == "jet"
        editor.on_ground_clicked((30.0, 40.0))
        assert len(editor.scenario_draft["jets"]) == 1
        assert editor.scenario_draft["jets"][0]["position"] == pytest.approx((30.0, 40.0))
    finally:
        _destroy_app(app)


def test_directgui_add_each_entity_kind_via_buttons():
    app = _editor_app()
    try:
        editor = app._editor
        _invoke(editor._btn_add_target)
        editor.on_ground_clicked((10.0, 20.0))
        _invoke(editor._btn_add_sam)
        editor.on_ground_clicked((55.0, 60.0))
        _invoke(editor._btn_add_jammer)
        editor.on_ground_clicked((70.0, 15.0))
        assert len(editor.scenario_draft["targets"]) == 1
        assert len(editor.scenario_draft["sam_sites"]) == 1
        assert len(editor.scenario_draft["jammers"]) == 1
    finally:
        _destroy_app(app)


def test_directgui_save_button_writes_valid_draft(tmp_path):
    app = _editor_app()
    try:
        editor = app._editor
        editor.begin_placement("jet")
        editor.on_ground_clicked((30.0, 40.0))
        editor.begin_placement("target")
        editor.on_ground_clicked((70.0, 80.0))
        dest = tmp_path / "from_button.json"
        editor._path = str(dest)
        editor._path_entry.enterText(str(dest))
        _invoke(editor._btn_save)
        assert dest.exists()
        loaded = load_scenario(dest)
        assert loaded.jets[0].position == pytest.approx((30.0, 40.0))
    finally:
        _destroy_app(app)


def test_directgui_preview_and_back_buttons(tmp_path):
    app = _editor_app()
    try:
        editor = app._editor
        editor.begin_placement("jet")
        editor.on_ground_clicked((20.0, 25.0))
        editor.begin_placement("target")
        editor.on_ground_clicked((60.0, 65.0))
        _invoke(editor._btn_preview)
        assert app.view_mode == "brief"
        _invoke(editor._back_btn)
        assert app.view_mode == "editor"
        assert editor._panel is not None and not editor._panel.is_hidden()
    finally:
        _destroy_app(app)


def test_directgui_launch_button_enters_brief(tmp_path):
    app = _editor_app()
    try:
        editor = app._editor
        editor.begin_placement("jet")
        editor.on_ground_clicked((20.0, 20.0))
        editor.begin_placement("target")
        editor.on_ground_clicked((20.5, 20.0))
        dest = tmp_path / "launch_btn.json"
        editor._path = str(dest)
        editor._path_entry.enterText(str(dest))
        _invoke(editor._btn_launch)
        assert app.view_mode == "brief"
        assert app._sim_handle is not None
        app.cancel_launch()
        assert app.view_mode == "editor"
    finally:
        _destroy_app(app)


def test_delete_entity_removes_jet_from_draft_and_scene():
    app = _editor_app()
    try:
        editor = app._editor
        editor.begin_placement("jet")
        editor.on_ground_clicked((30.0, 40.0))
        editor.begin_placement("target")
        editor.on_ground_clicked((70.0, 80.0))
        jet_id = editor.scenario_draft["jets"][0]["agent_id"]
        editor.delete_entity(jet_id)
        assert editor.scenario_draft["jets"] == []
        assert editor._handles is not None
        assert jet_id not in editor._handles.jets
    finally:
        _destroy_app(app)


def test_property_form_apply_updates_payload():
    app = _editor_app()
    try:
        editor = app._editor
        editor.begin_placement("jet")
        editor.on_ground_clicked((12.0, 13.0))
        editor._id_entry.enterText("Renamed_Jet")
        editor._payload_entries["JDAM"].enterText("3")
        editor._payload_entries["AIM9X"].enterText("2")
        _invoke(editor._btn_apply)
        jet = editor.scenario_draft["jets"][0]
        assert jet["agent_id"] == "Renamed_Jet"
        assert jet["payload"]["JDAM"] == 3
        assert jet["payload"]["AIM9X"] == 2
    finally:
        _destroy_app(app)


def test_ground_click_without_placement_selects_nearest():
    app = _editor_app()
    try:
        editor = app._editor
        editor.begin_placement("jet")
        editor.on_ground_clicked((30.0, 40.0))
        editor._placement_kind = None
        editor.on_ground_clicked((31.0, 40.0))
        assert editor._selected is editor.scenario_draft["jets"][0]
    finally:
        _destroy_app(app)


def test_ortho_camera_zoom_and_pan():
    dummy = type("B", (), {})()
    dummy.camera = None
    dummy.render = NodePath("r")
    dummy.cam = None
    dummy.mouseWatcherNode = None
    cam = OrthoEditorCamera(dummy, 100.0, 100.0)
    assert cam.zoom == pytest.approx(1.0)
    cam.zoom_by(2.0)
    assert cam.zoom == pytest.approx(2.0)
    cam.zoom_by(100.0)
    assert cam.zoom == pytest.approx(8.0)
    cam.zoom_by(0.01)
    assert cam.zoom == pytest.approx(0.4)
    cam.pan(5.0, -3.0)
    assert cam.center_x == pytest.approx(55.0)
    assert cam.center_y == pytest.approx(47.0)


def test_load_button_surfaces_missing_file(tmp_path):
    app = _editor_app()
    try:
        editor = app._editor
        missing = tmp_path / "nope.json"
        editor._path_entry.enterText(str(missing))
        _invoke(editor._btn_load)
        assert editor.banner_messages
        assert any("Could not load" in msg for msg in editor.banner_messages)
    finally:
        _destroy_app(app)


def test_non_default_frame_type_save_reload_round_trips(tmp_path):
    app = _editor_app()
    try:
        editor = app._editor
        editor.begin_placement("jet")
        editor.on_ground_clicked((30.0, 40.0))
        editor.set_frame_type("HEAVY_BOMBER")
        editor.begin_placement("target")
        editor.on_ground_clicked((70.0, 80.0))
        dest = tmp_path / "bomber.json"
        assert editor.save(str(dest)) is None
        loaded = load_scenario(dest)
        assert loaded.jets[0].frame_type == "HEAVY_BOMBER"

        editor.load(str(dest))
        assert editor.scenario_draft["jets"][0]["frame_type"] == "HEAVY_BOMBER"
        editor.on_ground_clicked((30.0, 40.0))
        assert "HEAVY_BOMBER" in editor._frame_btn["text"]
    finally:
        _destroy_app(app)


def test_place_home_base_is_a_single_moved_marker(tmp_path):
    app = _editor_app()
    try:
        editor = app._editor
        assert editor.scenario_draft["home_base"] == list(wn_config.DEFAULT_HOME_BASE)

        editor.begin_placement("home_base")
        editor.on_ground_clicked((11.0, 22.0))
        editor.begin_placement("home_base")
        editor.on_ground_clicked((33.0, 44.0))

        home = editor.scenario_draft["home_base"]
        assert home == pytest.approx([33.0, 44.0])
        assert len(home) == 2

        editor.begin_placement("jet")
        editor.on_ground_clicked((10.0, 10.0))
        editor.begin_placement("target")
        editor.on_ground_clicked((50.0, 50.0))
        dest = tmp_path / "home.json"
        assert editor.save(str(dest)) is None
        loaded = load_scenario(dest)
        assert loaded.home_base == pytest.approx((33.0, 44.0))
        assert isinstance(loaded.home_base, tuple)
        assert len(loaded.home_base) == 2
    finally:
        _destroy_app(app)


def test_save_without_placing_home_base_keeps_the_default(tmp_path):
    app = _editor_app()
    try:
        editor = app._editor
        editor.begin_placement("jet")
        editor.on_ground_clicked((10.0, 10.0))
        editor.begin_placement("target")
        editor.on_ground_clicked((50.0, 50.0))
        dest = tmp_path / "default_home.json"
        assert editor.save(str(dest)) is None
        loaded = load_scenario(dest)
        assert loaded.home_base == pytest.approx(wn_config.DEFAULT_HOME_BASE)
    finally:
        _destroy_app(app)


def test_slots_usage_indicator_matches_scenario_validator():
    fit = {"JDAM": 3, "AIM9X": 1}
    overflow = {"JDAM": 8, "AIM9X": 0}
    assert slots_usage_text(fit, "STRIKE_FIGHTER") == "4 / 7 slots"
    used, cap = ordnance_slots_used(fit, "STRIKE_FIGHTER")
    assert used <= cap
    ScenarioConfig.model_validate(_valid_kwargs(jets=[
        {"agent_id": "Jet_1", "position": (10.0, 10.0), "payload": fit}
    ]))

    assert slots_usage_text(overflow, "STRIKE_FIGHTER") == "8 / 7 slots"
    used, cap = ordnance_slots_used(overflow, "STRIKE_FIGHTER")
    assert used > cap
    with pytest.raises(ValidationError):
        ScenarioConfig.model_validate(_valid_kwargs(jets=[
            {"agent_id": "Jet_1", "position": (10.0, 10.0), "payload": overflow}
        ]))

    interceptor_overflow = {"JDAM": 5, "AIM9X": 0}
    assert slots_usage_text(interceptor_overflow, "INTERCEPTOR") == "5 / 4 slots"
    assert slots_usage_text(interceptor_overflow, "STRIKE_FIGHTER") == "5 / 7 slots"


def test_slots_preview_updates_when_payload_or_frame_changes():
    app = _editor_app()
    try:
        editor = app._editor
        editor.begin_placement("jet")
        editor.on_ground_clicked((12.0, 13.0))
        assert editor._slots_label["text"] == "1 / 7 slots"

        editor._payload_entries["JDAM"].enterText("8")
        editor._payload_entries["AIM9X"].enterText("0")
        editor._refresh_slots_preview()
        assert editor._slots_label["text"] == "8 / 7 slots"

        editor.set_frame_type("INTERCEPTOR")
        assert editor._slots_label["text"] == "8 / 4 slots"

        editor.set_frame_type("HEAVY_BOMBER")
        editor._payload_entries["JDAM"].enterText("1")
        editor._refresh_slots_preview()
        assert editor._slots_label["text"] == "1 / 10 slots"
    finally:
        _destroy_app(app)


# --- Phase 33: Quick Generate ----------------------------------------------


def test_preset_selection_populates_resolved_values():
    app = _editor_app()
    try:
        editor = app._editor
        from wraithnet.generation_presets import parameters_for_preset

        editor._seed_entry.enterText("42")
        editor._apply_preset("TRAINING", 42)
        expected = parameters_for_preset("TRAINING", seed=42)
        assert editor._generation_parameters.preset == "TRAINING"
        assert editor._generation_parameters.objective_count == expected.objective_count
        assert editor._generation_parameters.sam_count == expected.sam_count
        assert editor._advanced_entries["objective_count"].get() == str(
            expected.objective_count
        )
        assert "TRAINING" in editor._btn_difficulty["text"]
    finally:
        _destroy_app(app)


def test_editing_advanced_value_sets_custom_preset():
    app = _editor_app()
    try:
        editor = app._editor
        editor._apply_preset("STANDARD", 7)
        editor._advanced_entries["objective_count"].enterText("6")
        editor._on_advanced_field_edited()
        assert editor._generation_parameters.preset == "CUSTOM"
        assert "CUSTOM" in editor._btn_difficulty["text"]
        assert editor._generation_parameters.objective_count == 6
    finally:
        _destroy_app(app)


def test_new_seed_uses_secrets_randbits(monkeypatch):
    app = _editor_app()
    try:
        editor = app._editor
        monkeypatch.setattr("viewer3d.editor.secrets.randbits", lambda bits: 0xABCDEF0123456789)
        _invoke(editor._btn_new_seed)
        assert editor._seed_entry.get() == str(0xABCDEF0123456789)
        assert editor._generation_parameters.seed == 0xABCDEF0123456789
        assert 0 <= editor._generation_parameters.seed <= 2**64 - 1
    finally:
        _destroy_app(app)


def test_custom_roster_includes_all_weapon_catalog_keys():
    app = _editor_app()
    try:
        editor = app._editor
        while editor._generation_parameters.friendly_mode != "CUSTOM_ROSTER":
            editor._cycle_friendly_mode()
        assert editor._roster_rows
        row = editor._roster_rows[0]
        assert set(row["payload_entries"]) == set(wn_config.WEAPON_CATALOG)
        editor._refresh_roster_slot_previews()
        assert "slots" in row["slots_label"]["text"]
    finally:
        _destroy_app(app)


def test_generate_installs_exact_generation_result_scenario():
    app = _editor_app()
    try:
        from wraithnet.generation_presets import parameters_for_preset
        from wraithnet.scenario_generator import generate_scenario

        editor = app._editor
        params = parameters_for_preset("STANDARD", seed=11)
        editor._generation_parameters = params
        editor._sync_generation_widgets_from_parameters()
        expected = generate_scenario(params)
        editor._generate_clicked()
        assert editor.scenario_draft["jets"]
        assert [j["agent_id"] for j in editor.scenario_draft["jets"]] == [
            j.agent_id for j in expected.scenario.jets
        ]
        for draft_t, exp_t in zip(
            editor.scenario_draft["targets"], expected.scenario.targets, strict=True
        ):
            assert tuple(draft_t["position"]) == pytest.approx(tuple(exp_t.position))
        assert editor.scenario_draft["generation"] is not None
        assert editor._generation_report is not None
        assert editor._draft_dirty is True
        assert editor.scenario_draft["generation"]["modified_after_generation"] is False
    finally:
        _destroy_app(app)


def test_generation_failure_leaves_prior_draft_byte_equivalent(monkeypatch):
    app = _editor_app()
    try:
        from wraithnet.scenario_generator import GenerationError

        editor = app._editor
        editor.begin_placement("jet")
        editor.on_ground_clicked((12.0, 13.0))
        editor.begin_placement("target")
        editor.on_ground_clicked((40.0, 41.0))
        # Clear dirty confirmation path by saving conceptually — use Replace.
        prior = copy.deepcopy(editor.scenario_draft)

        def boom(_params):
            raise GenerationError("TEST_FAIL", {"kind": "test"})

        monkeypatch.setattr("viewer3d.editor.generate_scenario", boom)
        editor._pending_generation_action = None
        editor._draft_dirty = False
        # Still has content → confirmation first
        editor._generate_clicked()
        assert editor._pending_generation_action == "REPLACE"
        editor._confirm_replace_generation()
        assert editor.scenario_draft == prior
        assert any("TEST_FAIL" in msg or "fail" in msg.lower() for msg in editor.banner_messages) or editor.banner_messages
    finally:
        _destroy_app(app)


def test_dirty_draft_confirmation_prevents_accidental_replacement():
    app = _editor_app()
    try:
        editor = app._editor
        editor.begin_placement("jet")
        editor.on_ground_clicked((10.0, 10.0))
        assert editor._draft_dirty
        editor._generate_clicked()
        assert editor._pending_generation_action == "REPLACE"
        assert len(editor.scenario_draft["jets"]) == 1
        assert editor.scenario_draft.get("generation") is None
    finally:
        _destroy_app(app)


def test_fresh_editor_generate_is_one_click():
    app = _editor_app()
    try:
        editor = app._editor
        assert editor._draft_dirty is False
        assert not editor._draft_has_content()
        editor._seed_entry.enterText("3")
        editor._apply_preset("TRAINING", 3)
        editor._generate_clicked()
        assert editor._pending_generation_action is None
        assert editor.scenario_draft["jets"]
        assert editor.scenario_draft.get("generation") is not None
    finally:
        _destroy_app(app)


def test_custom_infeasible_requires_use_anyway_auto_rejects(monkeypatch):
    app = _editor_app()
    try:
        from wraithnet.generation_presets import parameters_for_preset
        from wraithnet.generation_schemas import GenerationParameters
        from wraithnet.scenario_generator import GenerationError, generate_scenario

        editor = app._editor
        base = parameters_for_preset("TRAINING", seed=1).model_dump()
        base.update(
            {
                "friendly_mode": "CUSTOM_ROSTER",
                "custom_roster": [
                    {
                        "frame_type": "INTERCEPTOR",
                        "payload": {
                            "AIM9X": 2,
                            "JDAM": 0,
                            "AMRAAM": 0,
                            "GUN_POD": 0,
                        },
                        "count": 1,
                    }
                ],
                "objective_count": 3,
                "sam_count": 0,
                "jammer_count": 0,
                "objective_hp_min": 4,
                "objective_hp_max": 4,
            }
        )
        params = GenerationParameters.model_validate(base)
        editor._generation_parameters = params
        editor._sync_generation_widgets_from_parameters()
        editor._generate_clicked()
        assert editor._pending_generation_action == "USE_ANYWAY"
        assert editor._pending_generation_result is not None
        assert editor.scenario_draft.get("generation") is None
        editor._confirm_use_anyway()
        assert editor.scenario_draft.get("generation") is not None
        assert editor._pending_generation_action is None

        # AUTO infeasible: generate_scenario raises; draft unchanged.
        def auto_fail(p):
            raise GenerationError("AUTO_BALANCE_CAPACITY", {"kind": "friendly_roster"})

        monkeypatch.setattr("viewer3d.editor.generate_scenario", auto_fail)
        editor._draft_dirty = False
        # Wipe entities so one-click generate path runs without Replace.
        editor.scenario_draft["jets"] = []
        editor.scenario_draft["targets"] = []
        editor.scenario_draft["sam_sites"] = []
        editor.scenario_draft["jammers"] = []
        editor.scenario_draft["generation"] = None
        editor._apply_preset("STANDARD", 5)
        editor._generate_clicked()
        assert editor.scenario_draft["jets"] == []
        assert editor.scenario_draft.get("generation") is None
        assert editor.banner_messages
    finally:
        _destroy_app(app)


def test_undo_restores_prior_draft_and_metadata():
    app = _editor_app()
    try:
        editor = app._editor
        editor.begin_placement("jet")
        editor.on_ground_clicked((9.0, 9.0))
        editor.begin_placement("target")
        editor.on_ground_clicked((50.0, 50.0))
        before = copy.deepcopy(editor.scenario_draft)
        editor._generate_clicked()
        editor._confirm_replace_generation()
        assert editor.scenario_draft.get("generation") is not None
        editor._undo_generation()
        assert [j["agent_id"] for j in editor.scenario_draft["jets"]] == [
            j["agent_id"] for j in before["jets"]
        ]
        assert editor.scenario_draft.get("generation") is None
        assert editor._generation_undo_snapshot is None
    finally:
        _destroy_app(app)


def test_manual_mutation_marks_generation_metadata_modified():
    app = _editor_app()
    try:
        editor = app._editor
        editor._seed_entry.enterText("8")
        editor._apply_preset("TRAINING", 8)
        editor._generate_clicked()
        assert editor.scenario_draft["generation"]["modified_after_generation"] is False
        jet = editor.scenario_draft["jets"][0]
        editor.open_property_form(jet)
        editor._payload_entries["JDAM"].enterText("3")
        _invoke(editor._btn_apply)
        assert editor.scenario_draft["generation"]["modified_after_generation"] is True
        assert "manually modified" in editor._gen_meta_label["text"]
    finally:
        _destroy_app(app)


def test_save_load_preserves_generation_metadata(tmp_path):
    app = _editor_app()
    try:
        editor = app._editor
        editor._seed_entry.enterText("15")
        editor._apply_preset("TRAINING", 15)
        editor._generate_clicked()
        dest = tmp_path / "generated.json"
        assert editor.save(str(dest)) is None
        assert editor._draft_dirty is False
        assert editor.scenario_draft["generation"]["modified_after_generation"] is False

        editor.begin_placement("jammer")
        editor.on_ground_clicked((5.0, 5.0))
        assert editor.scenario_draft["generation"]["modified_after_generation"] is True
        dest2 = tmp_path / "generated_mod.json"
        assert editor.save(str(dest2)) is None

        editor.scenario_draft = {
            "grid_width": wn_config.GRID_WIDTH,
            "grid_height": wn_config.GRID_HEIGHT,
            "jets": [],
            "targets": [],
            "sam_sites": [],
            "jammers": [],
            "home_base": list(wn_config.DEFAULT_HOME_BASE),
            "generation": None,
        }
        editor.load(str(dest2))
        assert editor._draft_dirty is False
        assert editor.scenario_draft["generation"] is not None
        assert editor.scenario_draft["generation"]["modified_after_generation"] is True
        assert editor.scenario_draft["generation"]["seed"] == 15
        # Load never regenerates — entity counts match file.
        loaded = load_scenario(dest2)
        assert len(editor.scenario_draft["jets"]) == len(loaded.jets)
        assert len(editor.scenario_draft["jammers"]) == len(loaded.jammers)
    finally:
        _destroy_app(app)


def test_quick_generate_buttons_invoke_methods():
    app = _editor_app()
    try:
        editor = app._editor
        _invoke(editor._btn_difficulty)
        assert editor._generation_parameters.preset in (
            "TRAINING",
            "STANDARD",
            "CONTESTED",
            "NIGHTMARE",
        )
        start_mode = editor._generation_parameters.friendly_mode
        _invoke(editor._btn_friendly_mode)
        assert editor._generation_parameters.friendly_mode != start_mode
        assert editor._generation_parameters.friendly_mode in (
            "AUTO_BALANCED",
            "RANDOM_LEGAL",
            "CUSTOM_ROSTER",
        )
        # Return to auto-balanced so Generate installs without Use Anyway.
        while editor._generation_parameters.friendly_mode != "AUTO_BALANCED":
            editor._cycle_friendly_mode()
        _invoke(editor._btn_advanced)
        assert editor._advanced_expanded is True
        _invoke(editor._btn_generate)
        assert editor.scenario_draft["jets"]
    finally:
        _destroy_app(app)


def test_generated_scene_contains_entities_and_home_base():
    app = _editor_app()
    try:
        editor = app._editor
        editor._seed_entry.enterText("21")
        editor._apply_preset("TRAINING", 21)
        editor._generate_clicked()
        handles = editor._handles
        assert handles is not None
        assert set(handles.jets) == {j["agent_id"] for j in editor.scenario_draft["jets"]}
        assert set(handles.targets) == {
            t["target_id"] for t in editor.scenario_draft["targets"]
        } | {s["target_id"] for s in editor.scenario_draft["sam_sites"]}
        assert set(handles.jammers) == {
            j["jammer_id"] for j in editor.scenario_draft["jammers"]
        }
        assert handles.home_base is not None
        assert not handles.home_base.is_empty()
        assert editor.scenario_draft["home_base"]
    finally:
        _destroy_app(app)


def test_entity_list_and_property_editor_after_generation():
    app = _editor_app()
    try:
        editor = app._editor
        editor._seed_entry.enterText("22")
        editor._apply_preset("TRAINING", 22)
        editor._generate_clicked()
        assert editor._list_buttons
        jet = editor.scenario_draft["jets"][0]
        editor.open_property_form(jet)
        assert set(editor._payload_entries) == set(wn_config.WEAPON_CATALOG)
        assert "Frame:" in editor._frame_btn["text"]
        editor._payload_entries["JDAM"].enterText("2")
        _invoke(editor._btn_apply)
        assert jet["payload"]["JDAM"] == 2
    finally:
        _destroy_app(app)


def test_preview_and_launch_receive_generated_state(tmp_path):
    app = _editor_app()
    try:
        editor = app._editor
        editor._seed_entry.enterText("23")
        editor._apply_preset("TRAINING", 23)
        editor._generate_clicked()
        jet_ids = [j["agent_id"] for j in editor.scenario_draft["jets"]]
        assert editor.preview() is None
        assert app.view_mode == "brief"
        assert app._header is not None
        assert app._header.jet_ids == jet_ids
        app.exit_preview()
        dest = tmp_path / "gen_launch.json"
        editor._path = str(dest)
        editor._path_entry.enterText(str(dest))
        _invoke(editor._btn_launch)
        assert app.view_mode == "brief"
        assert app._sim_handle is not None
        app.cancel_launch()
    finally:
        _destroy_app(app)


def test_generate_panel_survives_preview_transition():
    app = _editor_app()
    try:
        editor = app._editor
        editor._seed_entry.enterText("24")
        editor._apply_preset("TRAINING", 24)
        editor._generate_clicked()
        assert not editor._gen_panel.is_hidden()
        assert editor.preview() is None
        assert editor._gen_panel.is_hidden()
        app.exit_preview()
        assert not editor._gen_panel.is_hidden()
        assert not editor._panel.is_hidden()
    finally:
        _destroy_app(app)


def test_sync_generation_decision_documented():
    """Phase 32 generation is <100ms; editor uses synchronous generate_scenario."""
    import viewer3d.editor as editor_mod

    assert editor_mod._SYNC_GENERATION_BUDGET_MS == 100
    # Comment/constant present; no worker-thread helpers on ScenarioEditor.
    assert not hasattr(editor_mod.ScenarioEditor, "_generation_worker")
    source = Path(editor_mod.__file__).read_text(encoding="utf-8")
    assert "synchronously" in source
    assert "_SYNC_GENERATION_BUDGET_MS" in source


def test_payload_round_trip_keeps_amraam_and_gun_pod(tmp_path):
    dest = tmp_path / "full_payload.json"
    save_scenario(
        ScenarioConfig.model_validate(
            _valid_kwargs(
                jets=[
                    {
                        "agent_id": "Jet_1",
                        "position": (10.0, 10.0),
                        "payload": {
                            "JDAM": 1,
                            "AIM9X": 0,
                            "AMRAAM": 1,
                            "GUN_POD": 1,
                        },
                    }
                ]
            )
        ),
        dest,
    )
    app = _editor_app()
    try:
        editor = app._editor
        editor.load(str(dest))
        jet = editor.scenario_draft["jets"][0]
        editor.open_property_form(jet)
        assert set(editor._payload_entries) == set(wn_config.WEAPON_CATALOG)
        editor._payload_entries["JDAM"].enterText("2")
        jet["payload"]["FUTURE_WPN"] = 4
        _invoke(editor._btn_apply)
        assert jet["payload"]["AMRAAM"] == 1
        assert jet["payload"]["GUN_POD"] == 1
        assert jet["payload"]["JDAM"] == 2
        assert jet["payload"]["FUTURE_WPN"] == 4
        dest2 = tmp_path / "full_payload_saved.json"
        # Drop the unknown key before schema validation on Save.
        jet["payload"].pop("FUTURE_WPN")
        assert editor.save(str(dest2)) is None
        loaded = load_scenario(dest2)
        assert loaded.jets[0].payload["AMRAAM"] == 1
        assert loaded.jets[0].payload["GUN_POD"] == 1
        assert loaded.jets[0].payload["JDAM"] == 2
    finally:
        _destroy_app(app)


def test_load_save_preserves_combat_seed_objectives_and_bases(tmp_path):
    from wraithnet.bases import HOME_BASE_ID, default_home_airbase, default_jet_assignment

    scenario = scenario_from_data(
        new_scenario_data(
            combat_seed=99,
            jets=[
                {
                    "agent_id": "Jet_1",
                    "position": (10.0, 10.0),
                    "payload": {"JDAM": 1, "AIM9X": 0},
                    "base_assignment": default_jet_assignment().model_dump(),
                }
            ],
            targets=[
                {
                    "target_id": "Target_A",
                    "position": (50.0, 50.0),
                    "hp_required": 1,
                    "target_type": "HARDENED_BUNKER",
                }
            ],
            fixed_airbases=[default_home_airbase((5.0, 5.0)).model_dump()],
            home_base=(5.0, 5.0),
            mission_objectives=[
                {
                    "objective_id": "OBJ_CUSTOM",
                    "objective_type": "DESTROY",
                    "subject_ids": ["Target_A"],
                    "required": True,
                }
            ],
        )
    )
    src = tmp_path / "seeded.json"
    dest = tmp_path / "seeded_out.json"
    save_scenario(scenario, src)

    app = _editor_app()
    try:
        editor = app._editor
        editor.load(str(src))
        assert editor.scenario_draft["combat_seed"] == 99
        assert editor.scenario_draft["mission_objectives"][0]["objective_id"] == "OBJ_CUSTOM"
        assert editor.scenario_draft["fixed_airbases"][0]["base_id"] == HOME_BASE_ID
        assert editor.save(str(dest)) is None
    finally:
        _destroy_app(app)

    loaded = load_scenario(dest)
    assert loaded.combat_seed == 99
    assert loaded.mission_objectives[0].objective_id == "OBJ_CUSTOM"
    assert loaded.fixed_airbases[0].base_id == HOME_BASE_ID
    assert tuple(loaded.fixed_airbases[0].position) == pytest.approx((5.0, 5.0))

def _paint_water_patch(editor, cells):
    from wraithnet.terrain_paint import apply_paint_to_cells, terrain_from_draft

    terrain = terrain_from_draft(editor.scenario_draft["terrain"])
    painted = apply_paint_to_cells(terrain, cells, "WATER")
    editor.scenario_draft["terrain"] = painted.model_dump()
    editor.rebuild_scene()


def test_place_carrier_on_water_succeeds_land_fails():
    from wraithnet.terrain import cell_center

    app = _editor_app()
    try:
        editor = app._editor
        # Paint a 3x3 water patch so SURFACE placement and list wiring work.
        _paint_water_patch(
            editor,
            [(c, r) for r in range(3) for c in range(3)],
        )
        terrain = editor._draft_terrain()
        water_pos = cell_center(terrain, 1, 1)
        land_pos = cell_center(terrain, 5, 5)

        editor.begin_placement("carrier")
        editor.on_ground_clicked(water_pos)
        assert len(editor.scenario_draft["carriers"]) == 1
        assert editor.scenario_draft["carriers"][0]["position"] == pytest.approx(water_pos)
        assert any(
            kind == "carrier" for kind, _ in editor._all_entities()
        )
        assert editor._btn_add_carrier is not None
        assert editor._btn_add_corvette is not None

        before = len(editor.scenario_draft["carriers"])
        editor.begin_placement("carrier")
        editor.on_ground_clicked(land_pos)
        assert len(editor.scenario_draft["carriers"]) == before
        assert any("WATER" in m or "SURFACE" in m for m in editor.banner_messages)
    finally:
        _destroy_app(app)


def test_place_tank_on_water_fails_and_ground_succeeds():
    from wraithnet.terrain import cell_center

    app = _editor_app()
    try:
        editor = app._editor
        _paint_water_patch(editor, [(0, 0)])
        terrain = editor._draft_terrain()
        water_pos = cell_center(terrain, 0, 0)
        land_pos = cell_center(terrain, 2, 2)

        editor.begin_placement("tank")
        editor.on_ground_clicked(water_pos)
        assert len(editor.scenario_draft.get("tanks") or []) == 0
        assert any("GROUND" in m or "water" in m.lower() for m in editor.banner_messages)

        editor.begin_placement("tank")
        editor.on_ground_clicked(land_pos)
        assert len(editor.scenario_draft["tanks"]) == 1
        tank = editor.scenario_draft["tanks"][0]
        assert tank["faction"] in ("FRIENDLY", "ENEMY")
        assert tank["order"] in ("HOLD", "PATROL", "ADVANCE")
        assert tank["frame_type"] in ("MBT_LIGHT", "MBT_STANDARD")
    finally:
        _destroy_app(app)


def test_place_corvette_and_naval_tank_save_load_round_trip(tmp_path):
    from wraithnet.naval import all_water_terrain
    from wraithnet.terrain import cell_center
    from wraithnet.terrain_paint import apply_paint_to_cells

    app = _editor_app()
    try:
        editor = app._editor
        # Large water basin for footprint-safe naval save validation.
        water = all_water_terrain(100.0, 100.0, cell_size=5.0)
        # Keep a land corner for home/targets/tank.
        land_cells = [(c, r) for r in range(4) for c in range(4)]
        mixed = apply_paint_to_cells(water, land_cells, "PLAINS")
        editor.scenario_draft["terrain"] = mixed.model_dump()
        editor.rebuild_scene()

        terrain = editor._draft_terrain()
        carrier_pos = cell_center(terrain, 10, 10)
        corvette_pos = cell_center(terrain, 12, 12)
        tank_pos = cell_center(terrain, 1, 1)

        editor.begin_placement("carrier")
        editor.on_ground_clicked(carrier_pos)
        carrier = editor.scenario_draft["carriers"][0]
        carrier["heading"] = 90.0
        carrier["hold_position"] = False
        carrier["frame_type"] = "FLEET_CARRIER"
        carrier["route"] = [list(cell_center(terrain, 11, 10))]

        editor.begin_placement("corvette")
        editor.on_ground_clicked(corvette_pos)
        corvette = editor.scenario_draft["corvettes"][0]
        corvette["heading"] = 45.0
        corvette["frame_type"] = "CORVETTE_LIGHT"
        corvette["hold_position"] = True
        corvette["loadout"] = {"SAM_RADAR": 6, "CIWS": 20, "DECK_GUN": 10}

        editor.begin_placement("tank")
        editor.on_ground_clicked(tank_pos)
        tank = editor.scenario_draft["tanks"][0]
        tank["faction"] = "FRIENDLY"
        tank["order"] = "PATROL"
        tank["frame_type"] = "MBT_LIGHT"
        tank["waypoints"] = [list(cell_center(terrain, 2, 2))]

        # Minimal legal companions for schema save.
        editor.begin_placement("jet")
        editor.on_ground_clicked(cell_center(terrain, 1, 2))
        editor.begin_placement("target")
        editor.on_ground_clicked(cell_center(terrain, 2, 1))

        path = tmp_path / "naval_tank.json"
        errors = editor.save(str(path))
        assert errors is None, errors

        loaded = load_scenario(path)
        assert len(loaded.carriers) == 1
        assert loaded.carriers[0].frame_type == "FLEET_CARRIER"
        assert loaded.carriers[0].heading == pytest.approx(90.0)
        assert loaded.carriers[0].hold_position is False
        assert len(loaded.carriers[0].route) == 1

        assert len(loaded.corvettes) == 1
        assert loaded.corvettes[0].frame_type == "CORVETTE_LIGHT"
        assert loaded.corvettes[0].loadout["SAM_RADAR"] == 6
        assert loaded.corvettes[0].hold_position is True

        assert len(loaded.tanks) == 1
        assert loaded.tanks[0].faction == "FRIENDLY"
        assert loaded.tanks[0].order == "PATROL"
        assert loaded.tanks[0].frame_type == "MBT_LIGHT"
        assert len(loaded.tanks[0].waypoints) == 1

        editor.load(str(path))
        assert editor.scenario_draft["carriers"][0]["frame_type"] == "FLEET_CARRIER"
        assert editor.scenario_draft["corvettes"][0]["loadout"]["SAM_RADAR"] == 6
        assert editor.scenario_draft["tanks"][0]["order"] == "PATROL"
    finally:
        _destroy_app(app)
