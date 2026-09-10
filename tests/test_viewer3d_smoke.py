"""Headless Panda3D smoke tests for the 3D live viewer.

Skipped cleanly when panda3d is not installed (CI that only has
requirements-dev.txt). window-type none must be set before ShowBase.
"""

from __future__ import annotations

import asyncio
import threading
import time

import pytest

pytest.importorskip("panda3d")

from panda3d.core import NodePath, RenderModeAttrib, loadPrcFileData

loadPrcFileData("", "window-type none")
loadPrcFileData("", "audio-library-name null")
loadPrcFileData("", "notify-level error")
# window-type none is confirmed working on this Python/Panda3D (1.10.16 /
# Windows) without opening a GUI. ShowBase.camera is then None, so app.py
# skips camera placement; the drain task and scene graph still run.

from wraithnet.bridge import WraithNetBridge
from wraithnet.live_stream import LiveStreamServer
from wraithnet.scenario_schema import JetSpec, ScenarioConfig, TargetSpec, new_scenario_data, scenario_from_data
from wraithnet.trace_schemas import JetFrame, JetOutcomeEvent, StrikeEvent, TargetFrame, TraceHeader, TraceTick
from viewer3d.scene import (
    SceneHandles,
    apply_jet_flight_easing,
    apply_tick_highlights,
    build_static_scene,
    update_tick,
)
from viewer3d.flight_dynamics import (
    CRUISE_ALTITUDE,
    GROUND_ALTITUDE,
    POST_STRIKE_CLIMB_BOOST_ALTITUDE,
    TAKEOFF_CLIMB_SECONDS,
)
from viewer3d.stream_client import DisconnectedEvent, EndEvent, HeaderEvent, TickEvent


def _tiny_scenario() -> ScenarioConfig:
    return scenario_from_data(new_scenario_data(
        jets=[
            JetSpec(
                agent_id="Solo_Jet",
                position=(20.0, 20.0),
                payload={"JDAM": 1, "AIM9X": 0},
            )
        ],
        targets=[
            TargetSpec(
                target_id="Near_Bunker",
                position=(20.5, 20.0),
                hp_required=1,
                target_type="HARDENED_BUNKER",
            )
        ],
    ))


def _sample_header() -> TraceHeader:
    return TraceHeader(
        grid_width=100.0,
        grid_height=100.0,
        sensor_radius=65.0,
        jam_radius=12.0,
        sam_engagement_range=15.0,
        strike_range=1.0,
        jet_ids=["Jet_1", "Jet_2"],
        initial_payload={
            "Jet_1": {"JDAM": 1, "AIM9X": 0},
            "Jet_2": {"JDAM": 4, "AIM9X": 0},
        },
        target_ids=["Target_A", "SAM_Site_1"],
        target_types={"Target_A": "HARDENED_BUNKER", "SAM_Site_1": "SAM_SITE"},
        target_hp_required={"Target_A": 5, "SAM_Site_1": 1},
        sam_site_ids=["SAM_Site_1"],
        jammer_positions={"EW_Jammer_1": (46.0, 19.0)},
        target_positions={"Target_A": (75.0, 75.0), "SAM_Site_1": (40.0, 85.0)},
    )


def _jet_frame(agent_id: str, position: tuple[float, float], **overrides) -> JetFrame:
    data = {
        "agent_id": agent_id,
        "position": position,
        "hp": 5,
        "payload": {"JDAM": 1, "AIM9X": 0},
        "destroyed": False,
        "jammed": False,
        "role": "IDLE",
    }
    data.update(overrides)
    return JetFrame(**data)


def _tick(n: int, jets: list[JetFrame], targets: list[TargetFrame]) -> TraceTick:
    return TraceTick(
        tick=n,
        jets=jets,
        targets=targets,
        strikes=[],
        running=True,
        swarm_wiped_out=False,
    )


def test_build_static_scene_and_update_tick_destroyed_target_and_removed_jet():
    header = _sample_header()
    root = NodePath("test-root")
    handles = build_static_scene(root, header)
    assert isinstance(handles, SceneHandles)
    assert set(handles.jets) == {"Jet_1", "Jet_2"}
    assert set(handles.targets) == {"Target_A", "SAM_Site_1"}
    assert "EW_Jammer_1" in handles.jammers
    assert handles.jets["Jet_1"].is_hidden()
    home = handles.root.find("**/home-base")
    assert not home.is_empty()
    assert handles.home_base is not None
    assert (handles.home_base.get_pos().x, handles.home_base.get_pos().y) == pytest.approx(
        header.home_base
    )

    alive_targets = [
        TargetFrame(target_id="Target_A", hp_remaining=5, claimed=False),
        TargetFrame(target_id="SAM_Site_1", hp_remaining=1, claimed=False),
    ]
    tick0 = _tick(
        0,
        [
            _jet_frame("Jet_1", (10.0, 10.0)),
            _jet_frame("Jet_2", (20.0, 20.0)),
        ],
        alive_targets,
    )
    update_tick(handles, header, tick0)
    assert not handles.jets["Jet_1"].is_hidden()
    pos = handles.jets["Jet_1"].get_pos()
    assert (pos.x, pos.y) == pytest.approx((10.0, 10.0))

    tick1 = _tick(
        1,
        [_jet_frame("Jet_2", (21.0, 20.0))],
        [
            TargetFrame(target_id="Target_A", hp_remaining=0, claimed=False),
            TargetFrame(target_id="SAM_Site_1", hp_remaining=1, claimed=False),
        ],
    )
    update_tick(handles, header, tick1)

    assert "Jet_1" not in handles.jets
    assert handles.root.find("**/jet-Jet_1").is_empty()
    assert "Jet_2" in handles.jets
    assert not handles.jets["Jet_2"].is_empty()

    destroyed = handles.targets["Target_A"]
    rm = destroyed.get_attrib(RenderModeAttrib)
    assert rm is not None
    assert rm.get_mode() == RenderModeAttrib.M_wireframe


def test_jet_orientation_follows_heading_not_travel_direction():
    """Crab: the jet moves +x while heading stays 0° (+y). Orientation must
    follow heading, proving the old position-delta estimate is gone."""
    header = _sample_header()
    root = NodePath("crab-root")
    handles = build_static_scene(root, header)
    targets = [
        TargetFrame(target_id="Target_A", hp_remaining=5, claimed=False),
        TargetFrame(target_id="SAM_Site_1", hp_remaining=1, claimed=False),
    ]
    update_tick(
        handles,
        header,
        _tick(0, [_jet_frame("Jet_1", (10.0, 10.0), heading=0.0)], targets),
        seconds_airborne={"Jet_1": 3.0},
    )
    apply_jet_flight_easing(handles, 0.016)
    update_tick(
        handles,
        header,
        _tick(1, [_jet_frame("Jet_1", (20.0, 10.0), heading=0.0)], targets),
        seconds_airborne={"Jet_1": 3.0},
    )
    apply_jet_flight_easing(handles, 0.016)
    assert handles.jet_heading_deg["Jet_1"] == pytest.approx(0.0)
    glyph = handles.jet_glyphs["Jet_1"]
    assert glyph.get_h() == pytest.approx(0.0)

    update_tick(
        handles,
        header,
        _tick(2, [_jet_frame("Jet_1", (20.0, 20.0), heading=90.0)], targets),
        seconds_airborne={"Jet_1": 3.0},
    )
    apply_jet_flight_easing(handles, 0.016)
    assert handles.jet_heading_deg["Jet_1"] == pytest.approx(90.0)
    assert glyph.get_h() == pytest.approx(90.0)


def test_viewer_app_drains_live_stream_without_raising():
    from viewer3d.app import WraithNetViewer3D

    loop = asyncio.new_event_loop()
    ready = threading.Event()
    port_box: dict = {}
    error_box: dict = {}

    async def _run_sim() -> None:
        server = LiveStreamServer(port=0)
        await server.start()
        port_box["server"] = server
        port_box["port"] = server.port
        ready.set()
        bridge = WraithNetBridge(sinks=[server], scenario=_tiny_scenario())
        bridge.start_agent_loops()
        try:
            for _ in range(3):
                await bridge._tick()
            await asyncio.sleep(1.5)
        finally:
            await bridge.stop_agent_loops()
            await server.close()

    def _thread() -> None:
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_run_sim())
        except Exception as exc:  # pragma: no cover
            error_box["err"] = exc

    thread = threading.Thread(target=_thread, daemon=True)
    thread.start()
    assert ready.wait(timeout=10.0)
    port = port_box["port"]

    app = WraithNetViewer3D("127.0.0.1", port)
    try:
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            app.task_mgr.step()
            if app._handles is not None and app._latest_tick is not None:
                break
            time.sleep(0.05)
        assert app._header is not None
        assert app._handles is not None
        assert app._header.jet_ids == ["Solo_Jet"]
        assert "Solo_Jet" in app._handles.jets
        assert app.view_mode in ("live", "finished")
    finally:
        app._client.stop()
        try:
            app.destroy()
        except Exception:
            pass
        thread.join(timeout=10.0)
    assert "err" not in error_box


def test_live_jet_altitude_eases_over_wall_clock_time():
    """Phase 25: rendered altitude must change over real elapsed time, not stay fixed."""
    from viewer3d.app import WraithNetViewer3D

    loop = asyncio.new_event_loop()
    ready = threading.Event()
    port_box: dict = {}

    async def _run_sim() -> None:
        server = LiveStreamServer(port=0)
        await server.start()
        port_box["port"] = server.port
        ready.set()
        bridge = WraithNetBridge(sinks=[server], scenario=_tiny_scenario())
        bridge.start_agent_loops()
        try:
            for _ in range(6):
                await bridge._tick()
            await asyncio.sleep(2.0)
        finally:
            await bridge.stop_agent_loops()
            await server.close()

    def _thread() -> None:
        asyncio.set_event_loop(loop)
        loop.run_until_complete(_run_sim())

    thread = threading.Thread(target=_thread, daemon=True)
    thread.start()
    assert ready.wait(timeout=10.0)

    app = WraithNetViewer3D("127.0.0.1", port_box["port"])
    try:
        samples: list[float] = []
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline:
            app.task_mgr.step()
            time.sleep(0.08)
            if app._handles is None or "Solo_Jet" not in app._handles.jets:
                continue
            z = app._handles.rendered_altitude.get("Solo_Jet")
            if z is not None:
                samples.append(z)
            if len(samples) >= 3 and max(samples) - min(samples) > 0.05:
                break
        assert samples, "expected rendered altitude samples from the live viewer"
        assert max(samples) - min(samples) > 0.05
        assert min(samples) >= GROUND_ALTITUDE - 0.01
        assert max(samples) <= CRUISE_ALTITUDE + POST_STRIKE_CLIMB_BOOST_ALTITUDE + 0.5
    finally:
        app._client.stop()
        try:
            app.destroy()
        except Exception:
            pass
        thread.join(timeout=10.0)


def _destroy_app(app) -> None:
    try:
        app.on_window_close()
    except Exception:
        pass
    try:
        app._client.stop()
    except Exception:
        pass
    try:
        app.destroy()
    except Exception:
        pass


def _alive_targets():
    return [
        TargetFrame(target_id="Target_A", hp_remaining=5, claimed=False),
        TargetFrame(target_id="SAM_Site_1", hp_remaining=1, claimed=False),
    ]


def test_view_mode_connecting_brief_live_finished():
    """Design doc §7: brief is header-with-no-ticks; live starts on first tick."""
    from viewer3d.app import WraithNetViewer3D

    app = WraithNetViewer3D("127.0.0.1", 9, start_client=False)
    try:
        assert app.view_mode == "connecting"

        app._client.events.put(HeaderEvent(header=_sample_header()))
        app.task_mgr.step()
        assert app.view_mode == "brief"
        assert app.camera_mode == "brief"
        assert app._header is not None
        assert app._handles is not None
        assert app._latest_tick is None

        tick0 = _tick(
            0,
            [_jet_frame("Jet_1", (10.0, 10.0)), _jet_frame("Jet_2", (20.0, 20.0))],
            _alive_targets(),
        )
        app._client.events.put(TickEvent(tick=tick0))
        app.task_mgr.step()
        assert app.view_mode == "live"
        assert app.camera_mode == "orbit"
        assert app._latest_tick is tick0

        app._client.events.put(EndEvent())
        app.task_mgr.step()
        assert app.view_mode == "finished"
    finally:
        _destroy_app(app)


def test_running_false_tick_marks_finished():
    from viewer3d.app import WraithNetViewer3D

    app = WraithNetViewer3D("127.0.0.1", 9, start_client=False)
    try:
        app._client.events.put(HeaderEvent(header=_sample_header()))
        app.task_mgr.step()
        done = _tick(
            1,
            [_jet_frame("Jet_1", (11.0, 10.0))],
            _alive_targets(),
        )
        done = done.model_copy(update={"running": False})
        app._client.events.put(TickEvent(tick=done))
        app.task_mgr.step()
        assert app.view_mode == "finished"
    finally:
        _destroy_app(app)


def test_camera_orbit_chase_switching_invokes_chase_with_jet_position():
    from viewer3d.app import WraithNetViewer3D

    app = WraithNetViewer3D("127.0.0.1", 9, start_client=False)
    try:
        header = _sample_header()
        app._client.events.put(HeaderEvent(header=header))
        tick0 = _tick(
            0,
            [_jet_frame("Jet_1", (10.0, 10.0)), _jet_frame("Jet_2", (20.0, 20.0))],
            _alive_targets(),
        )
        app._client.events.put(TickEvent(tick=tick0))
        app.task_mgr.step()
        assert app.view_mode == "live"

        app._flush_presentation()
        app.camera_mode = "orbit"
        app.task_mgr.step()

        calls: list = []
        orig = app._chase.update

        def _spy(dt, jet_position, jet_heading):
            calls.append((jet_position, jet_heading))
            return orig(dt, jet_position, jet_heading)

        app._chase.update = _spy
        app.selected_jet_id = "Jet_1"
        app.camera_mode = "chase"
        app.task_mgr.step()
        assert calls, "ChaseCamera.update should have been invoked"
        pos, _heading = calls[-1]
        assert pos[0] == pytest.approx(10.0)
        assert pos[1] == pytest.approx(10.0)

        app._on_orbit()
        assert app.camera_mode == "orbit"
        app._on_chase()
        assert app.camera_mode == "chase"
    finally:
        _destroy_app(app)


def test_hud_update_across_modes_panels_and_disconnect_reason():
    from viewer3d.app import WraithNetViewer3D

    app = WraithNetViewer3D("127.0.0.1", 9, start_client=False)
    try:
        hud = app._hud
        connecting_mission = hud.mission_info_text
        assert connecting_mission
        hud.update(
            mode="connecting",
            header=None,
            latest_tick=None,
            camera_mode="orbit",
            selected_jet_id=None,
        )
        assert hud.mission_info_text
        connecting_mission = hud.mission_info_text

        header = _sample_header()
        app._client.events.put(HeaderEvent(header=header))
        app.task_mgr.step()
        brief_mission = hud.mission_info_text
        assert brief_mission != connecting_mission
        assert "MISSION BRIEF" in brief_mission or "brief" in brief_mission.lower()

        tick0 = _tick(
            0,
            [_jet_frame("Jet_1", (10.0, 10.0)), _jet_frame("Jet_2", (20.0, 20.0))],
            _alive_targets(),
        )
        app._client.events.put(TickEvent(tick=tick0))
        app.task_mgr.step()

        assert hud.mission_info_text.strip()
        assert hud.force_status_text.strip()
        assert hud.coordinates_text.strip()
        assert hud.controls_text.strip()
        assert "[ SCENARIO ]" in hud.mission_info_text
        assert "[ ALL ENEMIES ]" in hud.force_status_text
        assert "[ COORDINATES ]" in hud.coordinates_text
        assert "[ CAMERA ]" in hud.controls_text
        assert hud._mission_node.getText() == hud.mission_info_text
        assert hud._minimap_frame is not None

        hud.update(
            mode="live",
            header=header,
            latest_tick=tick0,
            camera_mode="orbit",
            selected_jet_id="Jet_1",
            disconnect_reason="could not connect after retries",
        )
        assert "could not connect after retries" in hud.mission_info_text
        assert "DISCONNECTED" in hud.mission_info_text

        finished = tick0.model_copy(update={"running": False})
        hud.update(
            mode="finished",
            header=header,
            latest_tick=finished,
            camera_mode="orbit",
            selected_jet_id="Jet_1",
            ticks=[tick0, finished],
        )
        assert hud.summary_text
        assert "Targets destroyed" in hud.summary_text
    finally:
        _destroy_app(app)


def test_apply_tick_highlights_spawns_fx_nodes():
    from viewer3d.highlights import compute_highlights

    header = _sample_header()
    root = NodePath("fx-root")
    handles = build_static_scene(root, header)
    prev = _tick(
        0,
        [_jet_frame("Jet_1", (10.0, 10.0)), _jet_frame("Jet_2", (20.0, 20.0))],
        _alive_targets(),
    )
    update_tick(handles, header, prev)
    curr = TraceTick(
        tick=1,
        jets=[_jet_frame("Jet_2", (21.0, 20.0), jammed=True)],
        targets=[
            TargetFrame(target_id="Target_A", hp_remaining=0, claimed=False),
            TargetFrame(target_id="SAM_Site_1", hp_remaining=1, claimed=False),
        ],
        strikes=[StrikeEvent(agent_id="Jet_1", target_id="Target_A", weapon_type="JDAM")],
        running=True,
        swarm_wiped_out=False,
    )
    hl = compute_highlights(prev, curr)
    apply_tick_highlights(handles, header, prev, curr, hl)
    assert hl.strikes
    assert "Target_A" in hl.newly_destroyed_target_ids
    assert "Jet_1" in hl.newly_destroyed_jet_ids
    assert handles.highlight_fx


def test_editor_launch_transitions_editor_brief_live_finished(tmp_path):
    """Headline Phase 19 flow: customize -> launch -> brief -> live -> finished."""
    from viewer3d.app import WraithNetViewer3D

    app = WraithNetViewer3D(editor_mode=True, start_client=False)
    try:
        editor = app._editor
        assert app.view_mode == "editor"
        editor.begin_placement("jet")
        editor.on_ground_clicked((20.0, 20.0))
        editor.begin_placement("target")
        editor.on_ground_clicked((20.5, 20.0))
        dest = tmp_path / "tiny_launch.json"
        editor._path = str(dest)
        editor._path_entry.enterText(str(dest))

        app.on_launch_clicked()
        assert app.view_mode == "brief"
        assert app._sim_handle is not None

        seen_live = False
        deadline = time.monotonic() + 60.0
        while time.monotonic() < deadline:
            app.task_mgr.step()
            if app.view_mode == "live":
                seen_live = True
            if app.view_mode == "finished":
                break
            time.sleep(0.05)
        assert seen_live or app.view_mode == "finished"
        assert app.view_mode == "finished"
        assert app._latest_tick is not None
        assert app._header is not None
        assert app._header.jet_ids == ["Jet_1"]
    finally:
        _destroy_app(app)


def test_malformed_scenario_launch_sets_launch_failed(tmp_path):
    from viewer3d.app import WraithNetViewer3D
    from viewer3d.launch import launch_scenario

    app = WraithNetViewer3D(editor_mode=True, start_client=False)
    try:
        bad = tmp_path / "broken.json"
        bad.write_text("{not-json", encoding="utf-8")
        handle = launch_scenario(str(bad))
        app._attach_sim(handle)
        deadline = time.monotonic() + 20.0
        while time.monotonic() < deadline:
            app.task_mgr.step()
            if app.view_mode == "launch_failed":
                break
            time.sleep(0.05)
        assert app.view_mode == "launch_failed"
        assert "LAUNCH FAILED" in app._hud.mission_info_text
    finally:
        _destroy_app(app)


def test_cancel_launch_terminates_sim_and_returns_to_editor(tmp_path):
    """Phase 19 Part C: 'Cancel launch' / 'Back to editor' must terminate the
    subprocess and leave the app back in editor mode, not stuck watching an
    indefinite connecting/brief spinner.
    """
    from viewer3d.app import WraithNetViewer3D

    app = WraithNetViewer3D(editor_mode=True, start_client=False)
    try:
        editor = app._editor
        editor.begin_placement("jet")
        editor.on_ground_clicked((20.0, 20.0))
        editor.begin_placement("target")
        editor.on_ground_clicked((20.5, 20.0))
        dest = tmp_path / "cancel_launch.json"
        editor._path = str(dest)
        editor._path_entry.enterText(str(dest))

        app.on_launch_clicked()
        assert app.view_mode == "brief"
        handle = app._sim_handle
        assert handle is not None
        assert handle.is_running()

        app.cancel_launch()

        assert app.view_mode == "editor"
        assert app._sim_handle is None
        assert app._header is None
        assert app._editor is editor
        assert editor._panel is not None and not editor._panel.is_hidden()

        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline and handle.is_running():
            time.sleep(0.05)
        assert handle.is_running() is False
    finally:
        _destroy_app(app)


def test_escape_during_preview_cancels_launch_via_on_escape(tmp_path):
    """The Esc keybinding (Phase 19 Part C) must route through the same
    cancel_launch() path as an explicit 'Back to editor' click while a sim
    is attached, rather than quitting the whole app.
    """
    from viewer3d.app import WraithNetViewer3D

    app = WraithNetViewer3D(editor_mode=True, start_client=False)
    try:
        editor = app._editor
        editor.begin_placement("jet")
        editor.on_ground_clicked((20.0, 20.0))
        editor.begin_placement("target")
        editor.on_ground_clicked((20.5, 20.0))
        dest = tmp_path / "escape_cancel.json"
        editor._path = str(dest)
        editor._path_entry.enterText(str(dest))

        app.on_launch_clicked()
        assert app.view_mode == "brief"
        handle = app._sim_handle
        assert handle is not None

        app._on_escape()

        assert app.view_mode == "editor"
        assert app._sim_handle is None
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline and handle.is_running():
            time.sleep(0.05)
        assert handle.is_running() is False
    finally:
        _destroy_app(app)


def test_preview_without_sim_exits_back_to_editor(tmp_path):
    """exit_preview() alone (no launched sim, e.g. the 'Preview' button, not
    'Launch') must cleanly restore editor mode and its scene/panels.
    """
    from viewer3d.app import WraithNetViewer3D

    app = WraithNetViewer3D(editor_mode=True, start_client=False)
    try:
        editor = app._editor
        editor.begin_placement("jet")
        editor.on_ground_clicked((15.0, 15.0))
        editor.begin_placement("target")
        editor.on_ground_clicked((60.0, 60.0))
        assert editor.preview() is None
        assert app.view_mode == "brief"
        assert app._sim_handle is None

        app.exit_preview()

        assert app.view_mode == "editor"
        assert app._header is None
        assert editor._panel is not None and not editor._panel.is_hidden()
        assert editor._handles is not None
        assert set(editor._handles.jets) == {"Jet_1"}
    finally:
        _destroy_app(app)


def test_keybindings_cycle_jets_and_switch_camera():
    from viewer3d.app import WraithNetViewer3D

    app = WraithNetViewer3D("127.0.0.1", 9, start_client=False)
    try:
        app._client.events.put(HeaderEvent(header=_sample_header()))
        tick0 = _tick(
            0,
            [_jet_frame("Jet_1", (10.0, 10.0)), _jet_frame("Jet_2", (20.0, 20.0))],
            _alive_targets(),
        )
        app._client.events.put(TickEvent(tick=tick0))
        app.task_mgr.step()
        assert app.view_mode == "live"
        assert app.selected_jet_id == "Jet_1"

        app._cycle_selected_jet()
        assert app.selected_jet_id == "Jet_2"
        app._cycle_selected_jet()
        assert app.selected_jet_id == "Jet_1"

        app._on_chase()
        assert app.camera_mode == "chase"
        app._on_orbit()
        assert app.camera_mode == "orbit"
    finally:
        _destroy_app(app)


def test_hud_force_status_counts_and_minimap_dots():
    from viewer3d.app import WraithNetViewer3D

    app = WraithNetViewer3D("127.0.0.1", 9, start_client=False)
    try:
        header = _sample_header()
        tick0 = _tick(
            0,
            [_jet_frame("Jet_1", (10.0, 10.0), role="MANAGER")],
            [
                TargetFrame(target_id="Target_A", hp_remaining=0, claimed=False),
                TargetFrame(target_id="SAM_Site_1", hp_remaining=1, claimed=False),
            ],
        )
        app._hud.update(
            mode="live",
            header=header,
            latest_tick=tick0,
            camera_mode="orbit",
            selected_jet_id="Jet_1",
        )
        force = app._hud.force_status_text
        assert "BUNKER" in force
        assert "0 / 1" in force
        assert "SAM" in force
        assert "1 / 1" in force
        assert "ALIVE   1 / 2" in force
        assert "MANAGER" in force
        shown = [d for d in app._hud._minimap_dots if not d.is_hidden()]
        assert shown, "minimap should place at least one dot for jammer/targets/jets"
        assert "ORBIT" in app._hud.controls_text
    finally:
        _destroy_app(app)


def test_disconnected_event_surfaces_on_hud():
    from viewer3d.app import WraithNetViewer3D

    app = WraithNetViewer3D("127.0.0.1", 9, start_client=False)
    try:
        assert app.view_mode == "connecting"
        app._client.events.put(
            DisconnectedEvent(reason="could not connect after retries")
        )
        app.task_mgr.step()
        assert app._disconnect_reason == "could not connect after retries"
        assert app.view_mode == "connecting"
        assert "DISCONNECTED" in app._hud.mission_info_text
        assert "could not connect after retries" in app._hud.mission_info_text
    finally:
        _destroy_app(app)


def test_escape_cancels_editor_placement_without_quitting():
    from viewer3d.app import WraithNetViewer3D

    app = WraithNetViewer3D(editor_mode=True, start_client=False)
    try:
        editor = app._editor
        editor.begin_placement("jet")
        assert editor._placement_kind == "jet"
        app._on_escape()
        assert editor._placement_kind is None
        assert app.view_mode == "editor"
    finally:
        _destroy_app(app)


def test_editor_zoom_keybindings_change_ortho_zoom():
    from viewer3d.app import WraithNetViewer3D

    app = WraithNetViewer3D(editor_mode=True, start_client=False)
    try:
        start = app._editor.camera.zoom
        app._on_editor_zoom_in()
        assert app._editor.camera.zoom > start
        zoomed = app._editor.camera.zoom
        app._on_editor_zoom_out()
        assert app._editor.camera.zoom < zoomed
        app._editor_pan(4.0, -2.0)
        assert app._editor.camera.center_x == pytest.approx(54.0)
        assert app._editor.camera.center_y == pytest.approx(48.0)
    finally:
        _destroy_app(app)


def test_hud_hidden_in_editor_mode():
    from viewer3d.app import WraithNetViewer3D

    app = WraithNetViewer3D(editor_mode=True, start_client=False)
    try:
        assert app._hud._visible is False
        assert app._hud._mission_frame.is_hidden()
    finally:
        _destroy_app(app)


def test_window_close_terminates_launched_sim(tmp_path):
    from viewer3d.app import WraithNetViewer3D

    app = WraithNetViewer3D(editor_mode=True, start_client=False)
    handle = None
    try:
        editor = app._editor
        editor.begin_placement("jet")
        editor.on_ground_clicked((20.0, 20.0))
        editor.begin_placement("target")
        editor.on_ground_clicked((20.5, 20.0))
        dest = tmp_path / "close_launch.json"
        editor._path = str(dest)
        editor._path_entry.enterText(str(dest))
        app.on_launch_clicked()
        handle = app._sim_handle
        assert handle is not None
        assert handle.is_running()
        app.on_window_close()
        assert app._sim_handle is None
        deadline = time.monotonic() + 8.0
        while time.monotonic() < deadline and handle.is_running():
            time.sleep(0.05)
        assert handle.is_running() is False
    finally:
        _destroy_app(app)


def _fx_child_names(root) -> set[str]:
    return {child.get_name() for child in root.get_children()}


def _advance_intervals(app, seconds: float, step_s: float = 0.05) -> None:
    """Intervals are wall-clock driven; step the task mgr while sleeping."""
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        app.task_mgr.step()
        time.sleep(step_s)


def test_weapon_effects_spawn_and_self_cleanup():
    """Phase 26: each spawn_* attaches a node, then cleans it up after its duration."""
    from viewer3d import effects
    from viewer3d.app import WraithNetViewer3D

    app = WraithNetViewer3D("127.0.0.1", 9, start_client=False)
    try:
        root = app.render.attach_new_node("fx-test-root")
        before = _fx_child_names(root)

        effects.spawn_strike_effect(
            root, "AIM9X", (0.0, 0.0, 8.0), (10.0, 10.0, 0.25)
        )
        effects.spawn_strike_effect(
            root, "JDAM", (1.0, 1.0, 8.0), (11.0, 11.0, 0.25)
        )
        effects.spawn_strike_effect(
            root, "GUN_POD", (2.0, 2.0, 8.0), (12.0, 12.0, 0.25)
        )
        effects.spawn_destruction_explosion(root, (5.0, 5.0, 0.3), scale=1.5)
        completed: list[bool] = []
        effects.spawn_jet_wreck(
            root, (3.0, 3.0, 8.0), on_complete=lambda: completed.append(True)
        )

        after_spawn = _fx_child_names(root)
        assert after_spawn - before, "expected effect nodes attached under root"
        assert any(
            n.startswith("fx-") or n == "gun-tracer" or "LineSegs" in n or n
            for n in (after_spawn - before)
        )

        # Longest archetype is bomb (~1.1s) + wreck (~0.9s); wait generously.
        _advance_intervals(app, 2.2)
        remaining = {
            child.get_name()
            for child in root.get_children()
            if not child.is_empty()
        }
        assert remaining <= before or remaining == set()
        assert completed == [True]
    finally:
        _destroy_app(app)


def test_live_stream_strike_triggers_spawn_strike_effect():
    """Phase 26 end-to-end: a real tiny scenario produces at least one strike effect."""
    from viewer3d import effects
    from viewer3d.app import WraithNetViewer3D

    loop = asyncio.new_event_loop()
    ready = threading.Event()
    port_box: dict = {}
    error_box: dict = {}
    strike_calls: list = []

    async def _run_sim() -> None:
        server = LiveStreamServer(port=0)
        await server.start()
        port_box["port"] = server.port
        ready.set()
        bridge = WraithNetBridge(sinks=[server], scenario=_tiny_scenario())
        bridge.start_agent_loops()
        try:
            for _ in range(12):
                await bridge._tick()
            await asyncio.sleep(1.0)
        finally:
            await bridge.stop_agent_loops()
            await server.close()

    def _thread() -> None:
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(_run_sim())
        except Exception as exc:  # pragma: no cover
            error_box["err"] = exc

    orig = effects.spawn_strike_effect

    def _spy(*args, **kwargs):
        strike_calls.append((args, kwargs))
        return orig(*args, **kwargs)

    effects.spawn_strike_effect = _spy  # type: ignore[assignment]
    thread = threading.Thread(target=_thread, daemon=True)
    thread.start()
    assert ready.wait(timeout=10.0)

    app = WraithNetViewer3D("127.0.0.1", port_box["port"])
    try:
        deadline = time.monotonic() + 12.0
        while time.monotonic() < deadline:
            app.task_mgr.step()
            if strike_calls:
                break
            if app.view_mode == "finished" and app._latest_tick is not None:
                # Presentation delay may still hold the strike tick; flush so
                # discrete combat VFX commit without waiting on wall-clock.
                app._flush_presentation()
                for _ in range(5):
                    app.task_mgr.step()
                break
            time.sleep(0.05)
        assert strike_calls, "expected at least one spawn_strike_effect call from a live strike"
    finally:
        effects.spawn_strike_effect = orig  # type: ignore[assignment]
        app._client.stop()
        try:
            app.destroy()
        except Exception:
            pass
        thread.join(timeout=10.0)
    assert "err" not in error_box


def test_destroyed_jet_removal_is_deferred_until_wreck_completes():
    """Phase 26: jet node stays in handles.jets until the wreck effect finishes."""
    from viewer3d.app import WraithNetViewer3D

    app = WraithNetViewer3D("127.0.0.1", 9, start_client=False)
    try:
        header = _sample_header()
        app._client.events.put(HeaderEvent(header=header))
        tick0 = _tick(
            0,
            [_jet_frame("Jet_1", (10.0, 10.0)), _jet_frame("Jet_2", (20.0, 20.0))],
            _alive_targets(),
        )
        app._client.events.put(TickEvent(tick=tick0))
        app.task_mgr.step()
        app._flush_presentation()
        assert "Jet_1" in app._handles.jets

        # Force-destroy Jet_1 by omitting it from the next tick (same as recorder pattern).
        tick1 = _tick(
            1,
            [_jet_frame("Jet_2", (21.0, 20.0))],
            _alive_targets(),
        )
        app._client.events.put(TickEvent(tick=tick1))
        app.task_mgr.step()
        app._flush_presentation()

        assert "Jet_1" in app._handles.jets, "jet must still be present during wreck"
        assert "Jet_1" in app._handles.pending_jet_removal
        jet_node = app._handles.jets["Jet_1"]
        assert not jet_node.is_empty()

        _advance_intervals(app, 1.5)
        assert "Jet_1" not in app._handles.jets
        assert "Jet_1" not in app._handles.pending_jet_removal
        assert jet_node.is_empty()
    finally:
        _destroy_app(app)


def test_current_scenario_path_default_uses_user_data_dir(tmp_path, monkeypatch):
    from pathlib import Path

    from viewer3d.app import WraithNetViewer3D

    def fake_user_data_dir(subdir: str) -> Path:
        path = tmp_path / subdir
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr("viewer3d.app.user_data_dir", fake_user_data_dir)
    app = WraithNetViewer3D("127.0.0.1", 9, start_client=False)
    try:
        assert app._editor is None
        assert app._current_scenario_path() == str(tmp_path / "scenarios" / "custom.json")
    finally:
        _destroy_app(app)


def test_takeoff_continues_after_burst_tick_drain():
    """Queued ticks sharing one drain timestamp must not freeze takeoff."""
    from viewer3d.app import WraithNetViewer3D

    app = WraithNetViewer3D("127.0.0.1", 9, start_client=False)
    try:
        header = _sample_header()
        app._client.events.put(HeaderEvent(header=header))
        far_targets = _alive_targets()
        for i in range(8):
            app._client.events.put(
                TickEvent(
                    tick=_tick(i, [_jet_frame("Jet_1", (10.0, 10.0))], far_targets)
                )
            )
        app.task_mgr.step()
        app._flush_presentation()
        z_burst = app._handles.rendered_altitude["Jet_1"]
        assert z_burst < 1.5

        _advance_intervals(app, TAKEOFF_CLIMB_SECONDS + 1.6)
        z_later = app._handles.rendered_altitude["Jet_1"]
        assert z_later >= CRUISE_ALTITUDE - 0.6
    finally:
        _destroy_app(app)


def test_strike_on_same_tick_as_destruction_still_spawns_effect():
    from viewer3d import effects
    from viewer3d.app import WraithNetViewer3D

    app = WraithNetViewer3D("127.0.0.1", 9, start_client=False)
    calls: list = []
    orig = effects.spawn_strike_effect

    def _spy(*args, **kwargs):
        calls.append((args, kwargs))
        return orig(*args, **kwargs)

    effects.spawn_strike_effect = _spy  # type: ignore[assignment]
    try:
        header = _sample_header()
        app._client.events.put(HeaderEvent(header=header))
        app._client.events.put(
            TickEvent(
                tick=_tick(
                    0,
                    [_jet_frame("Jet_1", (10.0, 10.0)), _jet_frame("Jet_2", (20.0, 20.0))],
                    _alive_targets(),
                )
            )
        )
        app.task_mgr.step()
        app._flush_presentation()
        dying = TraceTick(
            tick=1,
            jets=[_jet_frame("Jet_2", (21.0, 20.0))],
            targets=_alive_targets(),
            strikes=[
                StrikeEvent(agent_id="Jet_1", target_id="Target_A", weapon_type="AIM9X")
            ],
            running=True,
            swarm_wiped_out=False,
            jet_outcomes=[
                JetOutcomeEvent(agent_id="Jet_1", tick=1, outcome="destroyed"),
            ],
        )
        app._client.events.put(TickEvent(tick=dying))
        app.task_mgr.step()
        app._flush_presentation()
        assert calls, "strike must spawn even when the jet is absent this tick"
        assert calls[0][0][1] == "AIM9X"
    finally:
        effects.spawn_strike_effect = orig  # type: ignore[assignment]
        _destroy_app(app)


def test_wreck_cleanup_does_not_remove_replacement_scene_jet():
    from viewer3d.app import WraithNetViewer3D

    app = WraithNetViewer3D("127.0.0.1", 9, start_client=False)
    try:
        header = _sample_header()
        app._client.events.put(HeaderEvent(header=header))
        app._client.events.put(
            TickEvent(
                tick=_tick(
                    0,
                    [_jet_frame("Jet_1", (10.0, 10.0)), _jet_frame("Jet_2", (20.0, 20.0))],
                    _alive_targets(),
                )
            )
        )
        app.task_mgr.step()
        app._flush_presentation()
        app._client.events.put(
            TickEvent(
                tick=_tick(1, [_jet_frame("Jet_2", (21.0, 20.0))], _alive_targets())
            )
        )
        app.task_mgr.step()
        app._flush_presentation()
        assert "Jet_1" in app._handles.pending_jet_removal

        app._client.events.put(HeaderEvent(header=header))
        app._client.events.put(
            TickEvent(
                tick=_tick(
                    0,
                    [_jet_frame("Jet_1", (12.0, 12.0)), _jet_frame("Jet_2", (22.0, 22.0))],
                    _alive_targets(),
                )
            )
        )
        app.task_mgr.step()
        app._flush_presentation()
        replacement = app._handles.jets["Jet_1"]
        assert not replacement.is_empty()
        _advance_intervals(app, 1.5)
        assert "Jet_1" in app._handles.jets
        assert not app._handles.jets["Jet_1"].is_empty()
    finally:
        _destroy_app(app)


def test_bomb_waypoints_stay_above_ground():
    from viewer3d.effects import bomb_arc_waypoints, bomb_path_samples

    launch = (0.0, 0.0, 2.0)
    impact = (10.0, 10.0, 0.25)
    mid1, mid2 = bomb_arc_waypoints(launch, impact)
    assert mid1[2] >= impact[2] - 1e-9
    assert mid2[2] >= impact[2] - 1e-9
    for x, y, z in bomb_path_samples(launch, impact):
        assert z >= 0.0
        assert z >= impact[2] - 1e-9


def test_missile_trail_spawns_and_cleans_independently_of_glyph():
    from viewer3d import effects
    from viewer3d.app import WraithNetViewer3D

    app = WraithNetViewer3D("127.0.0.1", 9, start_client=False)
    try:
        root = app.render.attach_new_node("fx-missile-trail-root")
        effects.spawn_strike_effect(root, "AIM9X", (0.0, 0.0, 8.0), (10.0, 10.0, 0.25))
        app.task_mgr.step()
        trail = root.find("**/missile-trail")
        glyph = root.find("**/missile-glyph")
        assert not trail.is_empty(), "missile effect must attach a trail node"
        assert not glyph.is_empty()
        assert trail.get_name() == "missile-trail"
        _advance_intervals(app, 1.2)
        assert root.find("**/missile-trail").is_empty()
        assert root.find("**/missile-glyph").is_empty()
        assert root.find("**/fx-missile").is_empty()
    finally:
        _destroy_app(app)


def test_unknown_weapon_falls_back_to_missile_with_trail():
    from viewer3d import effects
    from viewer3d.app import WraithNetViewer3D

    app = WraithNetViewer3D("127.0.0.1", 9, start_client=False)
    try:
        root = app.render.attach_new_node("fx-unknown-root")
        effects.spawn_strike_effect(
            root, "NO_SUCH_WEAPON", (0.0, 0.0, 8.0), (4.0, 4.0, 0.25)
        )
        app.task_mgr.step()
        assert not root.find("**/fx-missile").is_empty()
        assert not root.find("**/missile-trail").is_empty()
        _advance_intervals(app, 1.2)
        assert root.find("**/missile-trail").is_empty()
        assert root.find("**/missile-glyph").is_empty()
        assert root.find("**/fx-missile").is_empty()
    finally:
        _destroy_app(app)


def test_legacy_strike_beam_reserved_for_gun_tracer():
    from viewer3d.highlights import compute_highlights

    header = _sample_header()
    root = NodePath("beam-root")
    handles = build_static_scene(root, header)
    prev = _tick(
        0,
        [_jet_frame("Jet_1", (10.0, 10.0)), _jet_frame("Jet_2", (20.0, 20.0))],
        _alive_targets(),
    )
    update_tick(handles, header, prev)

    jdam = TraceTick(
        tick=1,
        jets=[_jet_frame("Jet_1", (10.0, 10.0)), _jet_frame("Jet_2", (20.0, 20.0))],
        targets=_alive_targets(),
        strikes=[StrikeEvent(agent_id="Jet_1", target_id="Target_A", weapon_type="JDAM")],
        running=True,
        swarm_wiped_out=False,
    )
    apply_tick_highlights(handles, header, prev, jdam, compute_highlights(prev, jdam))
    names = [item.node.get_name() for item in handles.highlight_fx if not item.node.is_empty()]
    assert not any(n.startswith("strike-") for n in names)

    gun = TraceTick(
        tick=2,
        jets=[_jet_frame("Jet_1", (10.0, 10.0)), _jet_frame("Jet_2", (20.0, 20.0))],
        targets=_alive_targets(),
        strikes=[
            StrikeEvent(agent_id="Jet_1", target_id="Target_A", weapon_type="GUN_POD")
        ],
        running=True,
        swarm_wiped_out=False,
    )
    apply_tick_highlights(handles, header, jdam, gun, compute_highlights(jdam, gun))
    names = [item.node.get_name() for item in handles.highlight_fx if not item.node.is_empty()]
    assert any(n.startswith("strike-") for n in names)
