"""Headless Panda3D smoke tests for the 3D live viewer.

Skipped cleanly when panda3d is not installed (CI that only has
requirements-dev.txt). window-type none must be set before ShowBase.
"""

from __future__ import annotations

import asyncio
import math
import threading
import time

import pytest

# Builds the full viewer application or launches the viewer's simulator.
pytestmark = pytest.mark.gui

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
from wraithnet.trace_builders import build_trace_header, build_trace_tick
from wraithnet.trace_schemas import JetFrame, JetOutcomeEvent, StrikeEvent, TargetFrame, TraceHeader, TraceTick
from viewer3d.scene import (
    SceneHandles,
    apply_jet_flight_easing,
    apply_tick_highlights,
    build_static_scene,
    update_tick,
)
from viewer3d.flight_dynamics import CRUISE_ALTITUDE, GROUND_ALTITUDE, TAKEOFF_CLIMB_SECONDS
from viewer3d.stream_client import DisconnectedEvent, EndEvent, HeaderEvent, TickEvent
from viewer3d.stream_client import StreamClient
from tests.conftest import HANG_GUARD_S, tiny_scenario as _tiny_scenario


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
    # Sim heading is CW from +y; Panda H is the opposite sense, so the glyph
    # stores -heading (90° sim → nose along world +X via H=-90).
    assert glyph.get_h() == pytest.approx(-90.0)


def test_jet_orientation_follows_authoritative_pitch_roll():
    """Phase 54: glyph P/R track sim pitch/roll, not altitude-rate bank fiction."""
    from viewer3d.motion import SampledPose
    from viewer3d.scene import apply_sampled_jet_poses

    header = _sample_header()
    root = NodePath("att-root")
    handles = build_static_scene(root, header)
    targets = [
        TargetFrame(target_id="Target_A", hp_remaining=5, claimed=False),
        TargetFrame(target_id="SAM_Site_1", hp_remaining=1, claimed=False),
    ]
    frame = _jet_frame(
        "Jet_1",
        (10.0, 10.0),
        heading=0.0,
        pitch_deg=20.0,
        roll_deg=35.0,
        altitude=20.0,
    )
    update_tick(
        handles,
        header,
        _tick(0, [frame], targets),
        seconds_airborne={"Jet_1": 3.0},
        apply_jet_pose=False,
    )
    apply_sampled_jet_poses(
        handles,
        {
            "Jet_1": SampledPose(
                entity_id="Jet_1",
                x=10.0,
                y=10.0,
                heading_deg=0.0,
                alpha=0.0,
                from_tick=0,
                to_tick=0,
                pitch_deg=20.0,
                roll_deg=35.0,
            )
        },
    )
    glyph = handles.jet_glyphs["Jet_1"]
    assert glyph.get_p() == pytest.approx(20.0)
    assert glyph.get_r() == pytest.approx(-35.0)
    # Desired bank is authoritative roll, not a climb-fiction bank.
    assert handles.desired_bank_deg["Jet_1"] == pytest.approx(35.0)
    assert handles.desired_pitch_deg["Jet_1"] == pytest.approx(20.0)


def _record_live_stream(ticks: int) -> list:
    """Run the tiny mission behind a real LiveStreamServer and read it back.

    The mission plays ``ticks`` ticks, then a real StreamClient connects and
    gets the server backlog over the socket. Events are taken with a blocking
    queue get, in arrival order; HANG_GUARD_S only stops a hang.
    """
    loop = asyncio.new_event_loop()
    ready = threading.Event()
    box: dict = {}

    async def _run_sim() -> None:
        box["release"] = asyncio.Event()
        server = LiveStreamServer(port=0)
        await server.start()
        box["port"] = server.port
        bridge = WraithNetBridge(sinks=[server], scenario=_tiny_scenario())
        bridge.start_agent_loops()
        try:
            for _ in range(ticks):
                await bridge._tick()
            ready.set()
            await box["release"].wait()
        finally:
            await bridge.stop_agent_loops()
            await server.close()

    def _thread() -> None:
        try:
            loop.run_until_complete(_run_sim())
        except Exception as exc:  # pragma: no cover
            box["err"] = exc

    thread = threading.Thread(target=_thread, daemon=True)
    thread.start()
    assert ready.wait(timeout=HANG_GUARD_S)
    client = StreamClient("127.0.0.1", box["port"])
    client.start()
    events: list = []
    try:
        # Tick 0 (bridge construction) plus one per bridge tick.
        while sum(isinstance(e, TickEvent) for e in events) < ticks + 1:
            event = client.events.get(timeout=HANG_GUARD_S)
            assert not isinstance(event, DisconnectedEvent), event.reason
            events.append(event)
    finally:
        client.stop()
        loop.call_soon_threadsafe(box["release"].set)
        thread.join(timeout=HANG_GUARD_S)
    assert "err" not in box
    return events


def test_viewer_app_drains_live_stream_without_raising():
    from viewer3d.app import WraithNetViewer3D

    events = _record_live_stream(ticks=3)
    assert isinstance(events[0], HeaderEvent)

    app = WraithNetViewer3D("127.0.0.1", 9, start_client=False)
    try:
        for event in events:
            app._client.events.put(event)
        app.task_mgr.step()
        assert app.header is not None
        assert app.scene is not None
        assert app.header.jet_ids == ["Solo_Jet"]
        assert "Solo_Jet" in app.scene.jets
        assert app.latest_tick is not None and app.latest_tick.tick == 3
        assert app.view_mode == "live"
    finally:
        _destroy_app(app)


def test_live_jet_altitude_eases_over_presentation_time(panda_fake_clock):
    """Phase 25: rendered altitude changes as presentation time passes.

    Time is the fake clock, so the same frames always give the same samples.
    """
    from viewer3d.app import WraithNetViewer3D

    events = _record_live_stream(ticks=6)
    app = WraithNetViewer3D("127.0.0.1", 9, start_client=False)
    try:
        for event in events:
            app._client.events.put(event)
        samples: list[float] = []
        for _ in range(int(3 * panda_fake_clock)):
            app.task_mgr.step()
            z = app.scene.rendered_altitude.get("Solo_Jet")
            if z is not None:
                samples.append(z)
        assert len(samples) >= 3
        assert max(samples) - min(samples) > 0.05
        assert min(samples) >= GROUND_ALTITUDE - 0.01
        # Easing never overshoots the highest altitude the simulator sent.
        sent = [
            frame.altitude
            for e in events
            if isinstance(e, TickEvent)
            for frame in e.tick.jets
        ]
        assert max(samples) <= max(sent) + 0.01
    finally:
        _destroy_app(app)


def _step_until(app, done, hang_guard_s: float = HANG_GUARD_S) -> list[str]:
    """Step frames until ``done()``; for flows that wait on a launched process.

    The simulator runs in its own OS process, so the viewer must keep
    stepping while it works. The bound is a hang guard: the assertions after
    this call decide the result. Returns the view modes seen, in order.
    """
    modes: list[str] = []
    end = time.monotonic() + hang_guard_s
    while not done():
        assert time.monotonic() < end, f"hang guard hit; modes seen: {modes}"
        app.task_mgr.step()
        if not modes or modes[-1] != app.view_mode:
            modes.append(app.view_mode)
        time.sleep(0.01)
    return modes


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
        assert app.header is not None
        assert app.scene is not None
        assert app.latest_tick is None

        tick0 = _tick(
            0,
            [_jet_frame("Jet_1", (10.0, 10.0)), _jet_frame("Jet_2", (20.0, 20.0))],
            _alive_targets(),
        )
        app._client.events.put(TickEvent(tick=tick0))
        app.task_mgr.step()
        assert app.view_mode == "live"
        assert app.camera_mode == "orbit"
        assert app.latest_tick is tick0

        # Stream end alone must not finish while presentation is mid-run.
        app._client.events.put(EndEvent())
        app.task_mgr.step()
        assert app._stream_ended is True
        assert app.view_mode == "live"

        done = tick0.model_copy(update={"tick": 1, "running": False})
        app._client.events.put(TickEvent(tick=done))
        app.task_mgr.step()
        app._flush_presentation()
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
        app._flush_presentation()
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
        hud = app.hud
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
        editor = app.editor
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
        assert app.sim_handle is not None

        modes = _step_until(app, lambda: app.view_mode == "finished")
        assert "live" in modes
        assert app.view_mode == "finished"
        assert app.latest_tick is not None
        assert app.header is not None
        assert app.header.jet_ids == ["Jet_1"]
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
        _step_until(app, lambda: app.view_mode == "launch_failed")
        assert app.view_mode == "launch_failed"
        assert "LAUNCH FAILED" in app.hud.mission_info_text
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
        editor = app.editor
        editor.begin_placement("jet")
        editor.on_ground_clicked((20.0, 20.0))
        editor.begin_placement("target")
        editor.on_ground_clicked((20.5, 20.0))
        dest = tmp_path / "cancel_launch.json"
        editor._path = str(dest)
        editor._path_entry.enterText(str(dest))

        app.on_launch_clicked()
        assert app.view_mode == "brief"
        handle = app.sim_handle
        assert handle is not None
        assert handle.is_running()

        app.cancel_launch()

        assert app.view_mode == "editor"
        assert app.sim_handle is None
        assert app.header is None
        assert app.editor is editor
        assert editor._panel is not None and not editor._panel.is_hidden()

        handle.process.wait(timeout=HANG_GUARD_S)
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
        editor = app.editor
        editor.begin_placement("jet")
        editor.on_ground_clicked((20.0, 20.0))
        editor.begin_placement("target")
        editor.on_ground_clicked((20.5, 20.0))
        dest = tmp_path / "escape_cancel.json"
        editor._path = str(dest)
        editor._path_entry.enterText(str(dest))

        app.on_launch_clicked()
        assert app.view_mode == "brief"
        handle = app.sim_handle
        assert handle is not None

        app._on_escape()

        assert app.view_mode == "editor"
        assert app.sim_handle is None
        handle.process.wait(timeout=HANG_GUARD_S)
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
        editor = app.editor
        editor.begin_placement("jet")
        editor.on_ground_clicked((15.0, 15.0))
        editor.begin_placement("target")
        editor.on_ground_clicked((60.0, 60.0))
        assert editor.preview() is None
        assert app.view_mode == "brief"
        assert app.sim_handle is None

        app.exit_preview()

        assert app.view_mode == "editor"
        assert app.header is None
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
        chase_distance = app._chase.follow_distance
        app.messenger.send("+")
        assert app._chase.follow_distance < chase_distance
        app.messenger.send("-")
        assert app._chase.follow_distance == pytest.approx(chase_distance)
        app._on_orbit()
        assert app.camera_mode == "orbit"

        orbit_distance = math.dist(app._orbit._pos, app._orbit.center)
        app.messenger.send("wheel_up")
        assert math.dist(app._orbit._pos, app._orbit.center) < orbit_distance
        app.messenger.send("wheel_down")
        assert math.dist(app._orbit._pos, app._orbit.center) == pytest.approx(
            orbit_distance
        )

        before = app._orbit._pos
        app._orbit._held = {"forward": True}
        app._orbit.update(1.0)
        assert app._orbit._pos != before
        framed = app._orbit._pos
        app._orbit.reset()  # capture expected framing from current defaults
        expected = app._orbit._pos
        app._orbit._pos = framed
        app._orbit._apply()
        app._on_chase()
        assert app.camera_mode == "chase"
        app._on_center_camera()
        assert app.camera_mode == "orbit"
        assert app._orbit._pos == pytest.approx(expected, abs=1e-3)
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
        app.hud.update(
            mode="live",
            header=header,
            latest_tick=tick0,
            camera_mode="orbit",
            selected_jet_id="Jet_1",
        )
        force = app.hud.force_status_text
        assert "BUNKER" in force
        assert "0 / 1" in force
        assert "SAM" in force
        assert "1 / 1" in force
        assert "ALIVE   1 / 2" in force
        assert "MANAGER" in force
        shown = [d for d in app.hud._minimap_dots if not d.is_hidden()]
        assert shown, "minimap should place at least one dot for jammer/targets/jets"
        assert "ORBIT" in app.hud.controls_text
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
        assert "DISCONNECTED" in app.hud.mission_info_text
        assert "could not connect after retries" in app.hud.mission_info_text
    finally:
        _destroy_app(app)


def test_escape_cancels_editor_placement_without_quitting():
    from viewer3d.app import WraithNetViewer3D

    app = WraithNetViewer3D(editor_mode=True, start_client=False)
    try:
        editor = app.editor
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
        start = app.editor.camera.zoom
        app.messenger.send("wheel_up")
        assert app.editor.camera.zoom > start
        zoomed = app.editor.camera.zoom
        app.messenger.send("wheel_down")
        assert app.editor.camera.zoom < zoomed
        app.messenger.send("+")
        assert app.editor.camera.zoom > start
        app.messenger.send("-")
        assert app.editor.camera.zoom == pytest.approx(start)
        app._editor_pan(4.0, -2.0)
        assert app.editor.camera.center_x == pytest.approx(54.0)
        assert app.editor.camera.center_y == pytest.approx(48.0)
    finally:
        _destroy_app(app)


def test_hud_hidden_in_editor_mode():
    from viewer3d.app import WraithNetViewer3D

    app = WraithNetViewer3D(editor_mode=True, start_client=False)
    try:
        assert app.hud._visible is False
        assert app.hud._mission_frame.is_hidden()
    finally:
        _destroy_app(app)


def test_window_close_terminates_launched_sim(tmp_path):
    from viewer3d.app import WraithNetViewer3D

    app = WraithNetViewer3D(editor_mode=True, start_client=False)
    handle = None
    try:
        editor = app.editor
        editor.begin_placement("jet")
        editor.on_ground_clicked((20.0, 20.0))
        editor.begin_placement("target")
        editor.on_ground_clicked((20.5, 20.0))
        dest = tmp_path / "close_launch.json"
        editor._path = str(dest)
        editor._path_entry.enterText(str(dest))
        app.on_launch_clicked()
        handle = app.sim_handle
        assert handle is not None
        assert handle.is_running()
        app.on_window_close()
        assert app.sim_handle is None
        handle.process.wait(timeout=HANG_GUARD_S)
        assert handle.is_running() is False
    finally:
        _destroy_app(app)


def _fx_child_names(root) -> set[str]:
    return {child.get_name() for child in root.get_children()}


def _advance_intervals(app, seconds: float) -> None:
    """Step exactly ``seconds`` of frames on the fake clock (panda_fake_clock)."""
    from panda3d.core import ClockObject

    clock = ClockObject.get_global_clock()
    assert clock.get_mode() == ClockObject.M_non_real_time, "use panda_fake_clock"
    for _ in range(math.ceil(seconds / clock.get_dt()) + 1):
        app.task_mgr.step()


def test_weapon_effects_spawn_and_self_cleanup(panda_fake_clock):
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


class _CollectTrace:
    """Trace sink that keeps the header and ticks the live stream would send."""

    def __init__(self) -> None:
        self.header = None
        self.ticks: list = []
        self._seen: set[str] = set()

    def record_header(self, bridge) -> None:
        self.header = build_trace_header(bridge)

    def record_tick(self, bridge) -> None:
        self.ticks.append(build_trace_tick(bridge, seen_outcome_ids=self._seen))


def test_real_sim_strike_triggers_spawn_strike_effect():
    """Phase 26 end-to-end: a real tiny mission produces a strike effect.

    The mission runs to completion first (it releases at tick 19, after the
    descent to release altitude). Its ticks are then fed to the viewer one per
    frame, so the result does not depend on socket timing or the wall clock.
    The socket path itself is covered by test_live_stream.py and
    test_viewer3d_stream_client.py.
    """
    from viewer3d import effects
    from viewer3d.app import WraithNetViewer3D

    sink = _CollectTrace()
    bridge = WraithNetBridge(sinks=[sink], scenario=_tiny_scenario())
    asyncio.run(bridge.run(max_ticks=50))
    assert bridge.model.mission_solved
    assert any(tick.strikes for tick in sink.ticks)

    strike_calls: list = []
    orig = effects.spawn_strike_effect

    def _spy(*args, **kwargs):
        strike_calls.append((args, kwargs))
        return orig(*args, **kwargs)

    effects.spawn_strike_effect = _spy  # type: ignore[assignment]
    app = WraithNetViewer3D("127.0.0.1", 9, start_client=False)
    try:
        app._client.events.put(HeaderEvent(header=sink.header))
        app.task_mgr.step()
        for tick in sink.ticks:
            app._client.events.put(TickEvent(tick=tick))
            app.task_mgr.step()
            app._flush_presentation()
        assert len(strike_calls) == 1
        assert app.view_mode == "finished"
    finally:
        effects.spawn_strike_effect = orig  # type: ignore[assignment]
        _destroy_app(app)


def test_destroyed_jet_removal_is_deferred_until_wreck_completes(panda_fake_clock):
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
        assert "Jet_1" in app.scene.jets

        # Force-destroy Jet_1 by omitting it from the next tick (same as recorder pattern).
        tick1 = _tick(
            1,
            [_jet_frame("Jet_2", (21.0, 20.0))],
            _alive_targets(),
        )
        app._client.events.put(TickEvent(tick=tick1))
        app.task_mgr.step()
        app._flush_presentation()

        assert "Jet_1" in app.scene.jets, "jet must still be present during wreck"
        assert "Jet_1" in app.scene.pending_jet_removal
        jet_node = app.scene.jets["Jet_1"]
        assert not jet_node.is_empty()

        _advance_intervals(app, 1.5)
        assert "Jet_1" not in app.scene.jets
        assert "Jet_1" not in app.scene.pending_jet_removal
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
        assert app.editor is None
        assert app._current_scenario_path() == str(tmp_path / "scenarios" / "custom.json")
    finally:
        _destroy_app(app)


def test_takeoff_continues_after_burst_tick_drain(panda_fake_clock):
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
        z_burst = app.scene.rendered_altitude["Jet_1"]
        assert z_burst < 1.5

        _advance_intervals(app, TAKEOFF_CLIMB_SECONDS + 1.6)
        z_later = app.scene.rendered_altitude["Jet_1"]
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


def test_wreck_cleanup_does_not_remove_replacement_scene_jet(panda_fake_clock):
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
        assert "Jet_1" in app.scene.pending_jet_removal

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
        replacement = app.scene.jets["Jet_1"]
        assert not replacement.is_empty()
        _advance_intervals(app, 1.5)
        assert "Jet_1" in app.scene.jets
        assert not app.scene.jets["Jet_1"].is_empty()
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


def test_missile_trail_spawns_and_cleans_independently_of_glyph(panda_fake_clock):
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


def test_unknown_weapon_falls_back_to_missile_with_trail(panda_fake_clock):
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
