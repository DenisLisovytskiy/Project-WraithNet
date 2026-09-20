"""Headless Panda3D tests for Phase 47 dual-clock live motion integration."""

from __future__ import annotations

import time

import pytest

pytest.importorskip("panda3d")

from panda3d.core import loadPrcFileData

loadPrcFileData("", "window-type none")
loadPrcFileData("", "audio-library-name null")
loadPrcFileData("", "notify-level error")

from viewer3d.app import WraithNetViewer3D
from viewer3d.motion import DEFAULT_INTERP_DELAY_S
from viewer3d.stream_client import HeaderEvent, TickEvent
from wraithnet.trace_schemas import JetFrame, TargetFrame, TraceHeader, TraceTick


def _destroy_app(app: WraithNetViewer3D) -> None:
    try:
        app.destroy()
    except Exception:
        pass


def _header() -> TraceHeader:
    return TraceHeader(
        grid_width=100.0,
        grid_height=100.0,
        sensor_radius=65.0,
        jam_radius=12.0,
        sam_engagement_range=15.0,
        strike_range=1.0,
        jet_ids=["Jet_1"],
        initial_payload={"Jet_1": {"JDAM": 1}},
        target_ids=["T1"],
        target_types={"T1": "HARDENED_BUNKER"},
        target_hp_required={"T1": 1},
        sam_site_ids=[],
        jammer_positions={},
        target_positions={"T1": (50.0, 50.0)},
    )


def _jet(x: float, y: float, heading: float = 0.0) -> JetFrame:
    return JetFrame(
        agent_id="Jet_1",
        position=(x, y),
        hp=5,
        payload={"JDAM": 1},
        destroyed=False,
        jammed=False,
        role="IDLE",
        heading=heading,
    )


def _tick(n: int, x: float, y: float, heading: float = 0.0) -> TraceTick:
    return TraceTick(
        tick=n,
        jets=[_jet(x, y, heading)],
        targets=[TargetFrame(target_id="T1", hp_remaining=1, claimed=False)],
        strikes=[],
        running=True,
        swarm_wiped_out=False,
    )


def _boot_live(app: WraithNetViewer3D) -> None:
    app._client.events.put(HeaderEvent(header=_header()))
    app.task_mgr.step()


def test_sampled_poses_move_gradually_between_ticks():
    app = WraithNetViewer3D("127.0.0.1", 9, start_client=False)
    try:
        _boot_live(app)
        # Zero delay so we can drive α with presentation time directly.
        app._tick_buffer = type(app._tick_buffer)(interp_delay_s=0.0)

        t0 = time.monotonic()
        app._client.events.put(TickEvent(tick=_tick(0, 0.0, 0.0, heading=0.0)))
        app.task_mgr.step()
        # Force accept times to a known schedule.
        app._tick_buffer._entries.clear()
        app._ticks.clear()
        app._presented_tick = None
        app._previous_tick = None
        app._tick_buffer.push(_tick(0, 0.0, 0.0, heading=0.0), t_accept=t0)
        app._tick_buffer.push(_tick(1, 10.0, 0.0, heading=90.0), t_accept=t0 + 1.0)
        app._ticks = [e[1] for e in app._tick_buffer._entries]
        app._latest_tick = app._ticks[-1]

        app._tick_buffer.set_presentation_time(t0 + 0.25)
        app._pres_wall_last = time.monotonic()
        app._apply_presentation_state()
        x1 = app._handles.jets["Jet_1"].get_pos().x
        h1 = app._handles.jet_heading_deg["Jet_1"]
        assert x1 == pytest.approx(2.5)
        assert x1 < 10.0
        assert h1 == pytest.approx(22.5)

        app._tick_buffer.set_presentation_time(t0 + 0.75)
        app._apply_presentation_state()
        x2 = app._handles.jets["Jet_1"].get_pos().x
        h2 = app._handles.jet_heading_deg["Jet_1"]
        assert x2 == pytest.approx(7.5)
        assert x2 > x1
        assert h2 == pytest.approx(67.5)
        assert h2 > h1
    finally:
        _destroy_app(app)


def test_hud_uses_presented_tick_not_newest_buffered():
    app = WraithNetViewer3D("127.0.0.1", 9, start_client=False)
    try:
        _boot_live(app)
        assert app._tick_buffer._interp_delay_s == pytest.approx(DEFAULT_INTERP_DELAY_S)

        app._client.events.put(TickEvent(tick=_tick(0, 0.0, 0.0)))
        app._client.events.put(TickEvent(tick=_tick(1, 10.0, 0.0)))
        app.task_mgr.step()

        assert app._latest_tick is not None
        assert app._latest_tick.tick == 1
        # With default delay, a single step should not yet present tick 1.
        assert app._presented_tick is None or app._presented_tick.tick < 1

        app._flush_presentation()
        assert app._presented_tick is not None
        assert app._presented_tick.tick == 1
        app._refresh_hud()
    finally:
        _destroy_app(app)


def test_editor_mode_places_without_stream_ticks():
    app = WraithNetViewer3D(editor_mode=True, start_client=False)
    try:
        assert app.view_mode == "editor"
        assert app._editor is not None
        editor = app._editor
        editor.begin_placement("jet")
        assert editor._placement_kind == "jet"
        # Motion buffer exists but is unused in editor; no stream required.
        assert app._tick_buffer.backlog_len == 0
        assert app._sampled_poses == {}
    finally:
        _destroy_app(app)


def test_drain_task_still_returns_cont():
    app = WraithNetViewer3D("127.0.0.1", 9, start_client=False)
    try:
        _boot_live(app)
        app._client.events.put(TickEvent(tick=_tick(0, 1.0, 2.0)))
        result = app._drain_queue_task(type("T", (), {"cont": object()})())
        assert result is not None
    finally:
        _destroy_app(app)


def test_live_flood_does_not_multi_present_every_tick():
    """Paced watch: a 50-tick ingest burst must not present all 50 in one frame.

    Live apps raise catchup backlog to WATCH_MAX_BACKLOG_TICKS so mid-mission
    snap-to-latest is disabled; absorption is time spacing + delayed advance,
    not Phase-48's old snap-at-30 threshold.
    """
    from viewer3d.app import WATCH_MAX_BACKLOG_TICKS

    app = WraithNetViewer3D("127.0.0.1", 9, start_client=False)
    try:
        _boot_live(app)
        presents: list[int] = []
        orig = app._present_tick

        def _spy(tick):
            presents.append(tick.tick)
            return orig(tick)

        app._present_tick = _spy  # type: ignore[method-assign]
        for i in range(50):
            app._client.events.put(TickEvent(tick=_tick(i, float(i), 0.0)))
        app.task_mgr.step()
        assert app._tick_buffer.backlog_len == 50
        assert app._tick_buffer.backlog_len <= WATCH_MAX_BACKLOG_TICKS
        # One live presentation frame must not teleport through the flood.
        app._advance_presentation(dt=1.0 / 60.0)
        assert len(presents) < 50
        assert len(presents) <= 3
    finally:
        _destroy_app(app)


def test_snap_clears_pending_highlight_fx():
    """Phase 49: catch-up snap drops backlog cosmetics instead of playing a storm."""
    app = WraithNetViewer3D("127.0.0.1", 9, start_client=False)
    try:
        _boot_live(app)
        app._client.events.put(TickEvent(tick=_tick(0, 0.0, 0.0)))
        app.task_mgr.step()
        app._flush_presentation()
        from viewer3d.scene import _FxItem

        dummy = app.render.attach_new_node("fx-test-spam")
        app._handles.highlight_fx.append(_FxItem(node=dummy, duration=10.0))
        assert app._handles.highlight_fx
        app._clear_backlog_cosmetics()
        assert app._handles.highlight_fx == []
        assert dummy.is_empty()
    finally:
        _destroy_app(app)


def test_offline_replay_uses_same_tick_buffer(tmp_path):
    """Phase 49: --replay path feeds TraceTicks into TickBuffer."""
    header = _header()
    ticks = [
        _tick(0, 0.0, 0.0),
        _tick(1, 10.0, 0.0, heading=90.0),
        _tick(2, 20.0, 0.0, heading=180.0),
    ]
    # Patch TraceTick running on last
    ticks[-1] = ticks[-1].model_copy(update={"running": False})
    path = tmp_path / "offline.jsonl"
    path.write_text(
        header.model_dump_json()
        + "\n"
        + "\n".join(t.model_dump_json() for t in ticks),
        encoding="utf-8",
    )
    app = WraithNetViewer3D(
        start_client=False, replay_path=str(path), playback_rate=2.0
    )
    try:
        assert app._offline_replay is True
        assert app._tick_buffer.backlog_len == 3
        assert app.view_mode == "live"
        x0 = app._handles.jets["Jet_1"].get_pos().x
        for _ in range(30):
            app._advance_presentation(0.05)
        x1 = app._handles.jets["Jet_1"].get_pos().x
        assert x1 > x0
    finally:
        _destroy_app(app)

