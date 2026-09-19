"""python -m viewer3d.app
python -m viewer3d.app --edit [scenario.json]
python -m viewer3d.app --replay <trace.jsonl> [--rate N]
python -m viewer3d.app <host> <port>

Default (no args) opens the scenario editor (Phase 18/19). `--edit` is
the same, and may load a JSON path. `--replay` plays a recorded trace through
the same TickBuffer sampler as live. `<host> <port>` connects to an
already-running live stream as in Phases 16–17.
"""

from __future__ import annotations

import math
import sys
import time

from panda3d.core import ClockObject, WindowProperties, loadPrcFileData

loadPrcFileData("", "window-title WraithNet 3D")
loadPrcFileData("", "audio-library-name null")
loadPrcFileData("", "fullscreen #f")

from direct.showbase.ShowBase import ShowBase

from viewer3d.argv import USAGE, parse_viewer3d_argv
from viewer3d.cameras import ChaseCamera, MissionBriefCamera, OrbitCamera
from viewer3d import effects
from viewer3d.highlights import compute_highlights
from viewer3d.hud import Hud
from viewer3d.launch import SimLaunchHandle, launch_scenario
from viewer3d.flight_dynamics import (
    CRUISE_ALTITUDE,
    GROUND_ALTITUDE,
    desired_altitude,
    strike_boost_active,
)
from viewer3d.motion import (
    CATCHUP_BACKLOG_TICKS,
    CATCHUP_MAX_RATE,
    DEFAULT_INTERP_DELAY_S,
    DEFAULT_MAX_BACKLOG_TICKS,
    DEFAULT_PLAYBACK_RATE,
    DEFAULT_REPLAY_TICK_SPACING_S,
    MAX_EXTRAPOLATION_ALPHA,
    SampledPose,
    TickBuffer,
)
from viewer3d.scene import (
    apply_jet_flight_easing,
    apply_sampled_jet_poses,
    apply_tick_highlights,
    build_static_scene,
    clear_highlight_fx,
    decay_highlights,
    finish_removing_jet,
    update_tick,
)
from viewer3d.stream_client import (
    DisconnectedEvent,
    EndEvent,
    HeaderEvent,
    StreamClient,
    TickEvent,
)
from viewer3d.trace_io import load_trace
from wraithnet.paths import user_data_dir

# view_mode transitions:
#   "connecting"  -- no header yet (live mode only)
#   "editor"      -- Phase 18 scenario editor (ortho camera, no stream)
#   "brief"       -- header received, zero ticks received yet (MissionBriefCamera)
#                    also used for editor Preview
#   "live"        -- at least one tick received
#   "finished"     -- EndEvent or running=False tick

# Live Launch watch: space ticks on a human timeline so a fast sim does not
# collapse a 60-tick mission into ~2s of wall-clock accept times (and so the
# TickBuffer never snap-skips to the final pair).
LIVE_PRESENTATION_TPS = 3.0
LIVE_TICK_SPACING_S = 1.0 / LIVE_PRESENTATION_TPS
WATCH_MAX_BACKLOG_TICKS = 10_000


class WraithNetViewer3D(ShowBase):
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8765,
        *,
        start_client: bool | None = None,
        editor_mode: bool = False,
        replay_path: str | None = None,
        playback_rate: float = DEFAULT_PLAYBACK_RATE,
    ) -> None:
        super().__init__()
        self.disable_mouse()
        if self.win is not None:
            self.set_background_color(0.02, 0.03, 0.06, 1.0)
            self._maximize_window()
            # HWND can still be finishing setup during __init__; re-assert once.
            self.task_mgr.do_method_later(
                0.05, self._maximize_window_task, "maximize-window"
            )

        self._editor_mode = editor_mode
        self._offline_replay = replay_path is not None
        self._playback_rate = float(playback_rate) if playback_rate > 0.0 else DEFAULT_PLAYBACK_RATE
        self._show_presentation_debug = False
        self._previewing_from_editor = False
        self.view_mode = "editor" if editor_mode else ("brief" if self._offline_replay else "connecting")
        self.camera_mode = "orbit"
        self.selected_jet_id: str | None = None
        self._disconnect_reason: str | None = None
        self._header = None
        self._handles = None
        self._latest_tick = None  # newest ingested (may be ahead of presentation)
        self._presented_tick = None  # HUD / discrete authority
        self._previous_tick = None  # previous presented tick (highlights)
        self._ticks: list = []
        self._tick_buffer = TickBuffer(
            interp_delay_s=DEFAULT_INTERP_DELAY_S,
            max_extrapolation_alpha=MAX_EXTRAPOLATION_ALPHA,
            # Retain a full mission for paced watch (no mid-run snap-to-latest).
            max_backlog_ticks=WATCH_MAX_BACKLOG_TICKS,
            catchup_backlog_ticks=WATCH_MAX_BACKLOG_TICKS,
            catchup_max_rate=CATCHUP_MAX_RATE,
        )
        self._pres_wall_last: float | None = None
        self._live_pres_epoch: float | None = None
        self._sampled_poses: dict[str, SampledPose] = {}
        self._orbit = OrbitCamera(self)
        self._chase = ChaseCamera(self)
        self._brief = MissionBriefCamera(self)
        self._hud = Hud(self)
        self._editor = None
        self._sim_handle: SimLaunchHandle | None = None
        self._first_seen_wallclock: dict[str, float] = {}
        self._last_tick_wallclock: float | None = None
        self._last_present_wallclock: float | None = None
        self._stream_ended: bool = False

        if start_client is None:
            start_client = not editor_mode and not self._offline_replay
        self._client = StreamClient(host, port)
        if start_client:
            self._client.start()

        if editor_mode:
            from viewer3d.editor import ScenarioEditor

            self._hud.set_visible(False)
            self._editor = ScenarioEditor(self)
            self._terrain_painting = False
            self.accept("mouse1", self._on_editor_mouse1_down)
            self.accept("mouse1-up", self._on_editor_mouse1_up)
            self.accept("wheel_up", self._on_editor_zoom_in)
            self.accept("wheel_down", self._on_editor_zoom_out)
            self.accept("arrow_up", lambda: self._editor_pan(0.0, 4.0))
            self.accept("arrow_down", lambda: self._editor_pan(0.0, -4.0))
            self.accept("arrow_left", lambda: self._editor_pan(-4.0, 0.0))
            self.accept("arrow_right", lambda: self._editor_pan(4.0, 0.0))

        self.accept("o", self._on_orbit)
        self.accept("O", self._on_orbit)
        self.accept("c", self._on_chase)
        self.accept("C", self._on_chase)
        self.accept("tab", self._cycle_selected_jet)
        self.accept("n", self._cycle_selected_jet)
        self.accept("N", self._cycle_selected_jet)
        self.accept("home", self._on_center_camera)
        self.accept("f3", self._toggle_presentation_debug)
        self.accept("F3", self._toggle_presentation_debug)
        self.accept("escape", self._on_escape)
        if not editor_mode:
            self.accept("wheel_up", self._on_orbit_wheel_in)
            self.accept("wheel_down", self._on_orbit_wheel_out)
        self.exitFunc = self.on_window_close

        self.task_mgr.add(self._drain_queue_task, "drain-stream-queue")

        if replay_path is not None:
            self._load_offline_replay(replay_path)

    def _maximize_window(self) -> None:
        """Fill the screen as a normal maximized window (not exclusive fullscreen)."""
        if self.win is None:
            return
        # Keep decorations / taskbar; never request exclusive fullscreen.
        props = WindowProperties()
        props.set_fullscreen(False)
        self.win.request_properties(props)

        if sys.platform == "win32":
            handle = self.win.get_window_handle()
            if handle is not None:
                hwnd = int(handle.get_int_handle())
                if hwnd:
                    import ctypes

                    user32 = ctypes.windll.user32
                    # Panda may hand back a child HWND; maximize the top-level frame.
                    GA_ROOT = 2
                    root = int(user32.GetAncestor(hwnd, GA_ROOT) or 0)
                    if root:
                        hwnd = root
                    # SW_MAXIMIZE — title bar + taskbar remain usable.
                    user32.ShowWindow(hwnd, 3)
                    return

        # Fallback: size to the display without exclusive fullscreen.
        pipe = self.pipe
        if pipe is None:
            return
        width = int(pipe.get_display_width())
        height = int(pipe.get_display_height())
        if width <= 0 or height <= 0:
            return
        props = WindowProperties()
        props.set_fullscreen(False)
        props.set_origin(0, 0)
        props.set_size(width, height)
        self.win.request_properties(props)

    def _maximize_window_task(self, task):
        self._maximize_window()
        return task.done

    def _editor_map_world_pos(self):
        if self._editor is None or self.view_mode != "editor":
            return None
        mw = getattr(self, "mouseWatcherNode", None)
        if mw is None:
            return None
        try:
            if not mw.has_mouse():
                return None
            mx = mw.get_mouse_x()
            my = mw.get_mouse_y()
        except (AttributeError, AssertionError, TypeError):
            return None
        if mx < -0.45 or mx > 0.45:
            return None
        return self._editor.camera.screen_to_world(mx, my)

    def _on_editor_mouse1_down(self) -> None:
        if self._editor is None or self.view_mode != "editor":
            return
        if getattr(self._editor, "_edit_mode", "ENTITY") == "TERRAIN":
            world = self._editor_map_world_pos()
            if world is None:
                return
            self._terrain_painting = True
            self._editor.terrain_paint_begin(world)
            return
        self._editor.on_map_click_from_mouse()

    def _on_editor_mouse1_up(self) -> None:
        if self._editor is None or self.view_mode != "editor":
            return
        if self._terrain_painting:
            self._terrain_painting = False
            self._editor.terrain_paint_end()

    def _on_editor_click(self) -> None:
        # Kept for tests that invoke the old entry point.
        self._on_editor_mouse1_down()

    def _on_editor_zoom_in(self) -> None:
        if self._editor is not None and self.view_mode == "editor":
            self._editor.camera.zoom_by(1.15)

    def _on_editor_zoom_out(self) -> None:
        if self._editor is not None and self.view_mode == "editor":
            self._editor.camera.zoom_by(1 / 1.15)

    def _editor_pan(self, dx: float, dy: float) -> None:
        if self._editor is not None and self.view_mode == "editor":
            self._editor.camera.pan(dx, dy)

    def _on_escape(self) -> None:
        if self._sim_handle is not None or self._previewing_from_editor:
            if self.view_mode != "editor":
                self.cancel_launch()
                return
        if self._editor is not None and self.view_mode == "editor":
            if self._editor.handle_escape():
                self._terrain_painting = False
                return
        self.userExit()

    def enter_preview(self, header) -> None:
        """Editor Preview: reuse Phase 17 mission-brief (header, no ticks)."""
        self._previewing_from_editor = True
        if self._editor is not None:
            self._editor.hide_panels()
            if self._editor._handles is not None and not self._editor._handles.root.is_empty():
                self._editor._handles.root.hide()
        if self._handles is not None and not self._handles.root.is_empty():
            self._handles.root.remove_node()
        self._header = header
        self._handles = build_static_scene(self.render, header)
        self._rebuild_cameras()
        self.view_mode = "brief"
        self.camera_mode = "brief"
        self._hud.set_visible(True)

    def exit_preview(self) -> None:
        self._previewing_from_editor = False
        if self._handles is not None and not self._handles.root.is_empty():
            self._handles.root.remove_node()
        self._handles = None
        self._header = None
        self.view_mode = "editor"
        self._hud.set_visible(False)
        if self._editor is not None:
            self._editor.show_panels()
            if self._editor._handles is not None and not self._editor._handles.root.is_empty():
                self._editor._handles.root.show()
            else:
                self._editor.rebuild_scene()
            self._editor.camera.apply()

    def _current_scenario_path(self) -> str:
        if self._editor is None:
            return str(user_data_dir("scenarios") / "custom.json")
        return self._editor.current_path()

    def on_launch_clicked(self) -> None:
        """Editor Launch: save, preview immediately, spawn sim, connect."""
        if self._editor is None:
            return
        path = self._current_scenario_path()
        errors = self._editor.save(path)
        if errors is not None:
            return
        # One live view at a time: a new Launch replaces any previous sim.
        self._stop_sim()
        self._ticks = []
        self._previous_tick = None
        self._latest_tick = None
        self._presented_tick = None
        self._sampled_poses = {}
        self._tick_buffer.reset()
        self._pres_wall_last = None
        self._last_present_wallclock = None
        self._live_pres_epoch = None
        self._stream_ended = False
        self._disconnect_reason = None
        # Spawn + connect first so StreamClient can attach while the live
        # server is still up; preview is local and does not wait on the child.
        handle = launch_scenario(path)
        self._attach_sim(handle)
        self._editor.preview()

    def _attach_sim(self, handle: SimLaunchHandle) -> None:
        self._sim_handle = handle
        # Tight retries: a tiny scenario can finish in ~2s, and the live
        # server closes when the sim does. 0.5s spacing would miss it.
        self._client = StreamClient(
            handle.host, handle.port, connect_retries=80, retry_delay_s=0.05
        )
        self._client.start()

    def _stop_sim(self) -> None:
        if self._client is not None:
            self._client.stop()
        if self._sim_handle is not None:
            self._sim_handle.terminate()
            self._sim_handle = None

    def cancel_launch(self) -> None:
        """Terminate the sim subprocess and return to the editor."""
        self._stop_sim()
        self.exit_preview()

    def on_window_close(self) -> None:
        """Shutdown hook: do not leave a launched sim orphaned."""
        self._stop_sim()

    def userExit(self) -> None:  # noqa: N802 — ShowBase API
        self.on_window_close()
        super().userExit()

    def destroy(self) -> None:
        self.on_window_close()
        super().destroy()

    def _poll_sim_health(self) -> None:
        handle = self._sim_handle
        if handle is None:
            return
        rc = handle.process.poll()
        if rc is None:
            return
        if self.view_mode in ("live", "finished"):
            return
        if self._latest_tick is not None:
            return
        if rc == 0:
            # Clean exit — not a crash. StreamClient may still be draining
            # the last packets; leave view_mode alone.
            return
        tail = handle.stderr_tail()
        self._disconnect_reason = tail or "sim subprocess exited before sending a header"
        self.view_mode = "launch_failed"
        self.camera_mode = "brief"
        self._hud.set_visible(True)
        if self._client is not None:
            self._client.stop()
        if self._editor is not None:
            self._editor._back_btn.show()


    def _rebuild_cameras(self) -> None:
        header = self._header
        if header is None:
            return
        cx, cy = header.grid_width / 2.0, header.grid_height / 2.0
        diag = math.hypot(header.grid_width, header.grid_height)
        self._orbit = OrbitCamera(self, center=(cx, cy, 0.0), radius=max(90.0, diag * 0.75))
        self._chase = ChaseCamera(self)
        self._brief = MissionBriefCamera(
            self, center=(cx, cy, 0.0), radius=max(70.0, diag * 0.55)
        )

    def _on_orbit(self) -> None:
        if self.view_mode in ("live", "finished"):
            self.camera_mode = "orbit"

    def _on_chase(self) -> None:
        if self.view_mode not in ("live", "finished"):
            return
        if self.selected_jet_id is None:
            self._cycle_selected_jet()
        self.camera_mode = "chase"

    def _on_center_camera(self) -> None:
        """Restore the default map framing and return to free-look orbit."""
        if self.view_mode not in ("live", "finished", "brief", "launch_failed"):
            return
        if self._orbit is not None:
            self._orbit.reset()
        if self.view_mode in ("live", "finished"):
            self.camera_mode = "orbit"

    def _on_orbit_wheel_in(self) -> None:
        if self.camera_mode == "orbit" and self._orbit is not None:
            self._orbit.nudge_along_look(1.0)

    def _on_orbit_wheel_out(self) -> None:
        if self.camera_mode == "orbit" and self._orbit is not None:
            self._orbit.nudge_along_look(-1.0)

    def _cycle_selected_jet(self) -> None:
        """Tab/N cycles header.jet_ids -- no 3D mouse-picking (Phase 17 fallback)."""
        if self._header is None or not self._header.jet_ids:
            return
        ids = list(self._header.jet_ids)
        alive = set()
        alive = set()
        src = self._presented_tick or self._latest_tick
        if src is not None:
            alive = {j.agent_id for j in src.jets}
        prefer = [i for i in ids if i in alive] or ids
        if self.selected_jet_id not in prefer:
            self.selected_jet_id = prefer[0]
            return
        idx = prefer.index(self.selected_jet_id)
        self.selected_jet_id = prefer[(idx + 1) % len(prefer)]

    def _toggle_presentation_debug(self) -> None:
        self._show_presentation_debug = not self._show_presentation_debug

    def _load_offline_replay(self, path: str) -> None:
        """Load a recorded trace into the same TickBuffer used for live view."""
        header, ticks = load_trace(path)
        self._header = header
        self._handles = build_static_scene(self.render, header)
        self._rebuild_cameras()
        self._ticks = list(ticks)
        self._latest_tick = ticks[-1] if ticks else None
        self._previous_tick = None
        self._presented_tick = None
        self._sampled_poses = {}
        self._first_seen_wallclock.clear()
        now = time.monotonic()
        for tick in ticks:
            for frame in tick.jets:
                self._first_seen_wallclock.setdefault(frame.agent_id, now)
        self._tick_buffer.reset()
        # Offline: retain the full schedule; advance with playback_rate × dt only.
        self._tick_buffer.load_scheduled_ticks(
            ticks,
            t0=0.0,
            spacing_s=DEFAULT_REPLAY_TICK_SPACING_S,
        )
        # Start after delay so the first segment has a next tick when possible.
        self._tick_buffer.set_presentation_time(self._tick_buffer.interp_delay_s)
        self._pres_wall_last = time.monotonic()
        self.view_mode = "live"
        self.camera_mode = "orbit"
        if self.selected_jet_id is None and header.jet_ids:
            self.selected_jet_id = header.jet_ids[0]
        self._hud.set_visible(True)
        self._apply_presentation_state()

    def _drain_queue_task(self, task):
        clock = ClockObject.get_global_clock()
        dt = clock.get_dt()
        if dt <= 0.0:
            dt = 1.0 / 60.0
        if not self._offline_replay:
            self._drain_events()
        # Live / brief→live / offline replay: advance presentation clock and sample.
        # Editor placement does not use the interpolator.
        if self.view_mode != "editor":
            self._advance_presentation(dt)
        self._refresh_flight_targets()
        self._poll_sim_health()
        self._update_camera(dt)
        if self.view_mode == "editor" and self._editor is not None:
            if getattr(self, "_terrain_painting", False):
                world = self._editor_map_world_pos()
                if world is not None:
                    self._editor.terrain_paint_drag(world)
            ed_handles = getattr(self._editor, "_handles", None)
            if ed_handles is not None:
                decay_highlights(ed_handles, dt)
        if self._handles is not None:
            # Altitude/bank ease toward desires from the presented HUD tick;
            # XY/heading already come from sampled poses this frame.
            apply_jet_flight_easing(self._handles, dt)
            decay_highlights(self._handles, dt)
        self._refresh_hud()
        return task.cont

    def _reset_motion_buffer(self) -> None:
        self._tick_buffer.reset()
        self._pres_wall_last = None
        self._last_present_wallclock = None
        self._presented_tick = None
        self._sampled_poses = {}
        self._live_pres_epoch = None

    def _advance_presentation(self, dt: float | None = None) -> None:
        """Advance presentation time and apply samples/events."""
        now = time.monotonic()
        if self._pres_wall_last is None:
            if not self._offline_replay:
                self._tick_buffer.set_presentation_time(now)
            # Offline path seeds t_p in ``_load_offline_replay``.
        else:
            wall_dt = float(dt) if dt is not None else (now - self._pres_wall_last)
            if wall_dt < 0.0:
                wall_dt = 0.0
            if self._offline_replay:
                self._tick_buffer.advance(
                    wall_dt * self._playback_rate, catchup=False
                )
            else:
                # Synthetic accept times already pace the mission; catching up
                # would 8× through Launch and reintroduce the end-screen flash.
                self._tick_buffer.advance(wall_dt, catchup=False)
        self._pres_wall_last = now
        self._apply_presentation_state()
        if (
            self._offline_replay
            and self._presented_tick is not None
            and not self._presented_tick.running
        ):
            self.view_mode = "finished"

    def _flush_presentation(self) -> None:
        """Snap presentation to the newest buffered tick (headless tests / join).

        Production live frames use ``_advance_presentation`` with delay; tests that
        inject ticks in one task step call this so discrete state is visible
        without sleeping through ``interp_delay_s``.
        """
        if not self._tick_buffer._entries:
            return
        last_t = self._tick_buffer._entries[-1][0]
        self._tick_buffer.set_presentation_time(
            last_t + self._tick_buffer.interp_delay_s
        )
        self._pres_wall_last = time.monotonic()
        self._apply_presentation_state()

    def _clear_backlog_cosmetics(self) -> None:
        """On catch-up snap, drop pending highlight/effect spam (Phase 49)."""
        if self._handles is not None:
            clear_highlight_fx(self._handles)
        effects.clear_fx_under_root(self.render)

    def _apply_presentation_state(self) -> None:
        # Sample first so newly presented effects can use this frame's poses.
        self._sampled_poses = self._tick_buffer.sample_jet_poses()
        snapped = self._tick_buffer.consume_snap_flag()
        if snapped:
            self._clear_backlog_cosmetics()

        hud = self._tick_buffer.current_tick_for_hud()
        self._tick_buffer.pop_newly_presented_events()
        if hud is not None:
            prev_n = self._presented_tick.tick if self._presented_tick is not None else -1
            if hud.tick > prev_n:
                # Only walk ticks still retained in the buffer. After a snap,
                # discarded backlog is skipped (no multi-teleport through flood).
                candidates = self._tick_buffer.buffered_ticks()
                if snapped and len(candidates) >= 1:
                    # Commit only the newest kept tick's discrete state.
                    self._present_tick(candidates[-1])
                else:
                    for tick in candidates:
                        if tick.tick <= prev_n:
                            continue
                        if tick.tick > hud.tick:
                            break
                        self._present_tick(tick)
                # Re-sample after discrete presents (despawn hold rules may change).
                self._sampled_poses = self._tick_buffer.sample_jet_poses()

        if self._handles is not None and self._sampled_poses:
            apply_sampled_jet_poses(self._handles, self._sampled_poses)

    def _drain_events(self) -> None:
        while not self._client.events.empty():
            event = self._client.events.get_nowait()
            if isinstance(event, HeaderEvent):
                old = self._handles
                self._header = event.header
                self._handles = build_static_scene(self.render, self._header)
                self._first_seen_wallclock.clear()
                self._last_tick_wallclock = None
                self._reset_motion_buffer()
                self._previous_tick = None
                self._latest_tick = None
                self._ticks = []
                self._stream_ended = False
                self._live_pres_epoch = None
                if (
                    old is not None
                    and old.root is not None
                    and not old.root.is_empty()
                    and old.root != self._handles.root
                ):
                    old.root.remove_node()
                self._rebuild_cameras()
                if self.view_mode in ("connecting", "launch_failed"):
                    self.view_mode = "brief"
                    self.camera_mode = "brief"
            elif isinstance(event, TickEvent):
                self._ingest_tick(event.tick)
            elif isinstance(event, EndEvent):
                # Stream closed — keep presenting buffered ticks. ``finished``
                # flips only when the *presented* tick is terminal.
                self._stream_ended = True
            elif isinstance(event, DisconnectedEvent):
                self._disconnect_reason = event.reason
                if self.view_mode == "connecting":
                    pass  # HUD surfaces the reason; stay in connecting
                elif (
                    self.view_mode == "live"
                    and self._stream_ended
                    and self._presented_tick is not None
                    and not self._presented_tick.running
                ):
                    self.view_mode = "finished"

    def _ingest_tick(self, tick) -> None:
        """Queue a tick into the presentation buffer (not pose authority)."""
        if self.view_mode == "brief":
            # Happens exactly once: first TickEvent while still in brief.
            self.view_mode = "live"
            self.camera_mode = "orbit"
            if self.selected_jet_id is None and self._header is not None:
                if self._header.jet_ids:
                    self.selected_jet_id = self._header.jet_ids[0]

        now = time.monotonic()
        for frame in tick.jets:
            aid = frame.agent_id
            if aid not in self._first_seen_wallclock:
                self._first_seen_wallclock[aid] = now

        # Space ticks on a watchable timeline (tick index × spacing), not the
        # wall clock of a bursty sim finish.
        if self._live_pres_epoch is None:
            self._live_pres_epoch = now
        t_accept = self._live_pres_epoch + float(tick.tick) * LIVE_TICK_SPACING_S
        self._tick_buffer.push(tick, t_accept=t_accept)
        self._latest_tick = tick
        self._ticks.append(tick)
        # Do not mark finished here — presentation may still be catching up.

    def _present_tick(self, tick) -> None:
        """Commit one authoritative tick for HUD, highlights, and discrete scene state."""
        now = time.monotonic()
        tick_dt = 1.0 / 60.0
        if self._last_present_wallclock is not None:
            tick_dt = max(0.001, now - self._last_present_wallclock)
        self._last_present_wallclock = now

        seconds_airborne: dict[str, float] = {}
        for frame in tick.jets:
            aid = frame.agent_id
            first = self._first_seen_wallclock.get(aid, now)
            seconds_airborne[aid] = now - first

        just_struck: set[str] = set()
        if self._handles is not None:
            for strike in tick.strikes:
                self._handles.last_struck_tick[strike.agent_id] = tick.tick
            just_struck = {
                aid
                for aid in {j.agent_id for j in tick.jets}
                if strike_boost_active(
                    self._handles.last_struck_tick.get(aid), tick.tick
                )
            }

        highlights = compute_highlights(self._previous_tick, tick)
        if self._handles is not None and self._header is not None:
            # Sampled poses for this frame are applied after all presents; prefer
            # them when spawning effects so VFX line up with the mesh.
            self._spawn_weapon_effects(tick, highlights)
            apply_tick_highlights(
                self._handles, self._header, self._previous_tick, tick, highlights
            )
            destroyed = set(highlights.newly_destroyed_jet_ids)

            def _on_jet_removed(aid: str, last_pos: tuple[float, float, float]) -> None:
                handles = self._handles
                if aid in destroyed:
                    jet = handles.jets.get(aid) if handles is not None else None
                    if jet is not None and not jet.is_empty():
                        jet.hide()
                    effects.spawn_jet_wreck(
                        self.render,
                        last_pos,
                        on_complete=lambda agent_id=aid, scene=handles: (
                            self._finish_removing_jet(agent_id, scene)
                        ),
                    )
                else:
                    # RTB / non-destruction exit: remove immediately.
                    self._finish_removing_jet(aid, handles)

            update_tick(
                self._handles,
                self._header,
                tick,
                seconds_airborne=seconds_airborne,
                just_struck=just_struck,
                tick_dt=tick_dt,
                on_jet_removed=_on_jet_removed,
                apply_jet_pose=False,
            )
        self._previous_tick = tick
        self._presented_tick = tick
        if not tick.running:
            self.view_mode = "finished"

    # Back-compat for tests that still call the old ingest entry point.
    def _on_tick(self, tick) -> None:
        self._ingest_tick(tick)

    def _spawn_weapon_effects(self, tick, highlights) -> None:
        """Spawn cosmetic strike / destruction effects from this tick's highlights."""
        if self._header is None:
            return
        jets_now = {j.agent_id: j for j in tick.jets}
        jets_prev = (
            {j.agent_id: j for j in self._previous_tick.jets}
            if self._previous_tick is not None
            else {}
        )
        for strike in highlights.strikes:
            # Prefer authoritative missile frames/launches over cosmetic tracers.
            auth = bool(
                getattr(tick, "missiles", None) or getattr(tick, "weapon_launches", None)
            )
            if auth and strike.weapon_type in effects.GUIDED_WEAPON_TYPES:
                continue
            frame = jets_now.get(strike.agent_id) or jets_prev.get(strike.agent_id)
            pose = self._sampled_poses.get(strike.agent_id)
            if frame is None and pose is None:
                continue
            target_xy = self._header.target_positions.get(strike.target_id)
            if target_xy is None:
                continue
            z = CRUISE_ALTITUDE
            if self._handles is not None:
                z = self._handles.rendered_altitude.get(
                    strike.agent_id,
                    self._handles.desired_altitude.get(strike.agent_id, CRUISE_ALTITUDE),
                )
            if pose is not None:
                launch_pos = (pose.x, pose.y, float(z))
            else:
                assert frame is not None
                launch_pos = (float(frame.position[0]), float(frame.position[1]), float(z))
            impact_pos = (float(target_xy[0]), float(target_xy[1]), 0.25)
            effects.spawn_strike_effect(
                self.render, strike.weapon_type, launch_pos, impact_pos
            )
        for tid in highlights.newly_destroyed_target_ids:
            pos = self._header.target_positions.get(tid)
            if pos is None:
                continue
            effects.spawn_destruction_explosion(
                self.render, (float(pos[0]), float(pos[1]), 0.3), scale=1.5
            )
        # Phase 39: flare particles from authoritative countermeasure events.
        for cm in getattr(tick, "countermeasures", None) or []:
            if getattr(cm, "event", None) != "flare":
                continue
            if getattr(cm, "accepted", None) is False:
                continue
            frame = jets_now.get(cm.agent_id) or jets_prev.get(cm.agent_id)
            pose = self._sampled_poses.get(cm.agent_id)
            if pose is not None:
                z = CRUISE_ALTITUDE
                if self._handles is not None:
                    z = self._handles.rendered_altitude.get(
                        cm.agent_id,
                        self._handles.desired_altitude.get(cm.agent_id, CRUISE_ALTITUDE),
                    )
                effects.spawn_flare_burst(self.render, (pose.x, pose.y, float(z)))
                continue
            if frame is None:
                continue
            z = float(getattr(frame, "altitude", None) or CRUISE_ALTITUDE)
            effects.spawn_flare_burst(
                self.render,
                (float(frame.position[0]), float(frame.position[1]), z),
            )

    def _finish_removing_jet(self, agent_id: str, handles=None) -> None:
        """Deferred cleanup after a jet wreck effect (or immediate RTB exit).

        ``handles`` is the scene that spawned the wreck. A later header/preview
        can replace ``self._handles`` before the 0.9s interval fires; cleaning
        only the originating scene avoids deleting a replacement jet.
        """
        target = handles if handles is not None else self._handles
        if target is None:
            return
        finish_removing_jet(target, agent_id)

    def _refresh_flight_targets(self) -> None:
        """Recompute takeoff/attack altitude from wall-clock time every frame.

        Uses the **presented** HUD tick (not the newest buffered ingest) so
        altitude desires stay aligned with discrete combat state. XY for
        proximity comes from sampled poses when available.
        """
        tick = self._presented_tick
        if self._handles is None or self._header is None or tick is None:
            return
        now = time.monotonic()
        header = self._header
        handles = self._handles
        for frame in tick.jets:
            aid = frame.agent_id
            first = self._first_seen_wallclock.get(aid)
            if first is None:
                continue
            just_struck = strike_boost_active(
                handles.last_struck_tick.get(aid), tick.tick
            )
            auth_z = getattr(frame, "altitude", None)
            pose = self._sampled_poses.get(aid)
            xy = (pose.x, pose.y) if pose is not None else frame.position
            handles.desired_altitude[aid] = desired_altitude(
                now - first,
                xy,
                header,
                tick,
                just_struck,
                authoritative_altitude=auth_z,
            )

    def _selected_jet_pose(self):
        if self.selected_jet_id is None:
            return None
        aid = self.selected_jet_id
        pose = self._sampled_poses.get(aid)
        if pose is not None:
            z = GROUND_ALTITUDE
            if self._handles is not None:
                z = self._handles.rendered_altitude.get(
                    aid,
                    self._handles.desired_altitude.get(aid, CRUISE_ALTITUDE),
                )
            return (pose.x, pose.y, z), pose.heading_deg
        tick = self._presented_tick or self._latest_tick
        if tick is None:
            return None
        for frame in tick.jets:
            if frame.agent_id != aid:
                continue
            z = GROUND_ALTITUDE
            if self._handles is not None:
                z = self._handles.rendered_altitude.get(
                    frame.agent_id,
                    self._handles.desired_altitude.get(frame.agent_id, CRUISE_ALTITUDE),
                )
            heading = self._heading_deg(frame.agent_id)
            return (frame.position[0], frame.position[1], z), heading
        return None

    def _heading_deg(self, agent_id: str) -> float:
        if self._handles is not None:
            stored = self._handles.jet_heading_deg.get(agent_id)
            if stored is not None:
                return stored
        return 0.0

    def _update_camera(self, dt: float) -> None:
        if self.view_mode == "editor":
            if self._editor is not None:
                self._editor.camera.update(dt)
            return
        if self.view_mode in ("brief", "launch_failed") or self.camera_mode == "brief":
            self._brief.update(dt)
            return
        if self.camera_mode == "chase":
            pose = self._selected_jet_pose()
            if pose is not None:
                self._chase.update(dt, pose[0], pose[1])
                return
        self._orbit.update(dt)

    def _refresh_hud(self) -> None:
        if self.view_mode == "editor":
            return
        center = self._orbit.center if self._orbit is not None else (50.0, 50.0, 0.0)
        sampled_xy = {aid: (p.x, p.y) for aid, p in self._sampled_poses.items()}
        debug = None
        if self._show_presentation_debug:
            flags = []
            if self._tick_buffer.is_catching_up:
                flags.append("CATCHUP")
            if self._tick_buffer.is_extrapolating:
                flags.append("EXTRAP")
            flag_s = ",".join(flags) if flags else "ok"
            mode = "REPLAY" if self._offline_replay else "LIVE"
            debug = (
                f"[ MOTION ]  {mode}  backlog={self._tick_buffer.backlog_len}  "
                f"{flag_s}  rate={self._playback_rate:.2f}x"
            )
        display_mode = self.view_mode
        if self._offline_replay and self.view_mode == "live":
            display_mode = "live"
        self._hud.update(
            mode=display_mode,
            header=self._header,
            latest_tick=self._presented_tick,
            camera_mode=self.camera_mode,
            selected_jet_id=self.selected_jet_id,
            disconnect_reason=self._disconnect_reason,
            ticks=self._ticks,
            orbit_center=center,
            sampled_xy=sampled_xy,
            presentation_debug=debug,
        )


def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]
    try:
        parsed = parse_viewer3d_argv(argv)
    except SystemExit as exc:
        print(exc.code if isinstance(exc.code, str) else USAGE)
        sys.exit(1)
    if parsed.editor_mode:
        app = WraithNetViewer3D(editor_mode=True)
        if parsed.edit_path and app._editor is not None:
            app._editor.load(parsed.edit_path)
        app.run()
        return
    if parsed.replay_path:
        app = WraithNetViewer3D(
            start_client=False,
            replay_path=parsed.replay_path,
            playback_rate=parsed.playback_rate,
        )
        app.run()
        return
    app = WraithNetViewer3D(parsed.host, parsed.port)
    app.run()


if __name__ == "__main__":
    main()
