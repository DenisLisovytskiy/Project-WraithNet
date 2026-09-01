"""Headless tests for viewer3d.editor (Phase 18).

Uses the same window-type none config as tests/test_viewer3d_smoke.py.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

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


def test_save_without_jets_returns_errors_and_does_not_write(tmp_path):
    app = _editor_app()
    try:
        editor = app._editor
        editor.begin_placement("target")
        editor.on_ground_clicked((10.0, 10.0))
        dest = tmp_path / "no_jets.json"
        errors = editor.save(str(dest))
        assert errors is not None
        assert any("jet" in msg.lower() for msg in errors)
        assert not dest.exists()
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

        other = ScenarioConfig(
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
        )
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
    return data


def test_humanize_validation_error_for_each_phase14_rule():
    cases = [
        (_valid_kwargs(jets=[]), "jet"),
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
        editor.begin_placement("target")
        editor.on_ground_clicked((10.0, 10.0))
        errors = editor.preview()
        assert errors is not None
        assert any("jet" in msg.lower() for msg in errors)
        assert app.view_mode == "editor"
    finally:
        _destroy_app(app)


def test_scenario_to_header_matches_config_constants_and_entities():
    config = ScenarioConfig(
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
    )
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
        editor._field_a_entry.enterText("3")
        editor._field_b_entry.enterText("2")
        _invoke(editor._btn_apply)
        jet = editor.scenario_draft["jets"][0]
        assert jet["agent_id"] == "Renamed_Jet"
        assert jet["payload"] == {"JDAM": 3, "AIM9X": 2}
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

        editor._field_a_entry.enterText("8")
        editor._field_b_entry.enterText("0")
        editor._refresh_slots_preview()
        assert editor._slots_label["text"] == "8 / 7 slots"

        editor.set_frame_type("INTERCEPTOR")
        assert editor._slots_label["text"] == "8 / 4 slots"

        editor.set_frame_type("HEAVY_BOMBER")
        editor._field_a_entry.enterText("1")
        editor._refresh_slots_preview()
        assert editor._slots_label["text"] == "1 / 10 slots"
    finally:
        _destroy_app(app)
