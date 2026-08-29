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
from wraithnet.scenario_schema import JetSpec, ScenarioConfig, TargetSpec
from wraithnet.trace_schemas import JetFrame, TargetFrame, TraceHeader, TraceTick
from viewer3d.scene import SceneHandles, build_static_scene, update_tick


def _tiny_scenario() -> ScenarioConfig:
    return ScenarioConfig(
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
    )


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
    finally:
        app._client.stop()
        thread.join(timeout=10.0)
    assert "err" not in error_box
