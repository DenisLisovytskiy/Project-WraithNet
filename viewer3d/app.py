"""python -m viewer3d.app
python -m viewer3d.app --edit [scenario.json]
python -m viewer3d.app <host> <port>

Default (no args) opens the scenario editor (Phase 18/19). `--edit` is
the same, and may load a JSON path. `<host> <port>` connects to an
already-running live stream as in Phases 16–17.
"""

from __future__ import annotations

import math
import sys
import time

from panda3d.core import ClockObject, loadPrcFileData

loadPrcFileData("", "window-title WraithNet 3D")
loadPrcFileData("", "audio-library-name null")

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
from viewer3d.scene import (
    apply_jet_flight_easing,
    apply_tick_highlights,
    build_static_scene,
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
from wraithnet.paths import user_data_dir

# view_mode transitions:
#   "connecting"  -- no header yet (live mode only)
#   "editor"      -- Phase 18 scenario editor (ortho camera, no stream)
#   "brief"       -- header received, zero ticks received yet (MissionBriefCamera)
#                    also used for editor Preview
#   "live"        -- at least one tick received
#   "finished"     -- EndEvent or running=False tick


class WraithNetViewer3D(ShowBase):
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 8765,
        *,
        start_client: bool | None = None,
        editor_mode: bool = False,
    ) -> None:
        super().__init__()
        self.disable_mouse()
        if self.win is not None:
            self.set_background_color(0.02, 0.03, 0.06, 1.0)

        self._editor_mode = editor_mode
        self._previewing_from_editor = False
        self.view_mode = "editor" if editor_mode else "connecting"
        self.camera_mode = "orbit"
        self.selected_jet_id: str | None = None
        self._disconnect_reason: str | None = None
        self._header = None
        self._handles = None
        self._latest_tick = None
        self._previous_tick = None
        self._ticks: list = []
        self._orbit = OrbitCamera(self)
        self._chase = ChaseCamera(self)
        self._brief = MissionBriefCamera(self)
        self._hud = Hud(self)
        self._editor = None
        self._sim_handle: SimLaunchHandle | None = None
        self._first_seen_wallclock: dict[str, float] = {}
        self._last_tick_wallclock: float | None = None

        if start_client is None:
            start_client = not editor_mode
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
        self.accept("escape", self._on_escape)
        self.exitFunc = self.on_window_close

        self.task_mgr.add(self._drain_queue_task, "drain-stream-queue")

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

    def _cycle_selected_jet(self) -> None:
        """Tab/N cycles header.jet_ids -- no 3D mouse-picking (Phase 17 fallback)."""
        if self._header is None or not self._header.jet_ids:
            return
        ids = list(self._header.jet_ids)
        alive = set()
        if self._latest_tick is not None:
            alive = {j.agent_id for j in self._latest_tick.jets}
        prefer = [i for i in ids if i in alive] or ids
        if self.selected_jet_id not in prefer:
            self.selected_jet_id = prefer[0]
            return
        idx = prefer.index(self.selected_jet_id)
        self.selected_jet_id = prefer[(idx + 1) % len(prefer)]

    def _drain_queue_task(self, task):
        clock = ClockObject.get_global_clock()
        dt = clock.get_dt()
        if dt <= 0.0:
            dt = 1.0 / 60.0
        self._drain_events()
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
            apply_jet_flight_easing(self._handles, dt)
            decay_highlights(self._handles, dt)
        self._refresh_hud()
        return task.cont

    def _drain_events(self) -> None:
        while not self._client.events.empty():
            event = self._client.events.get_nowait()
            if isinstance(event, HeaderEvent):
                old = self._handles
                self._header = event.header
                self._handles = build_static_scene(self.render, self._header)
                self._first_seen_wallclock.clear()
                self._last_tick_wallclock = None
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
                self._on_tick(event.tick)
            elif isinstance(event, EndEvent):
                self.view_mode = "finished"
            elif isinstance(event, DisconnectedEvent):
                self._disconnect_reason = event.reason
                if self.view_mode == "connecting":
                    pass  # HUD surfaces the reason; stay in connecting
                elif self.view_mode == "live" and (
                    self._latest_tick is None or not self._latest_tick.running
                ):
                    self.view_mode = "finished"

    def _on_tick(self, tick) -> None:
        if self.view_mode == "brief":
            # Happens exactly once: first TickEvent while still in brief.
            self.view_mode = "live"
            self.camera_mode = "orbit"
            if self.selected_jet_id is None and self._header is not None:
                if self._header.jet_ids:
                    self.selected_jet_id = self._header.jet_ids[0]
        now = time.monotonic()
        tick_dt = 1.0 / 60.0
        if self._last_tick_wallclock is not None:
            tick_dt = max(0.001, now - self._last_tick_wallclock)
        self._last_tick_wallclock = now

        seconds_airborne: dict[str, float] = {}
        for frame in tick.jets:
            aid = frame.agent_id
            if aid not in self._first_seen_wallclock:
                self._first_seen_wallclock[aid] = now
            seconds_airborne[aid] = now - self._first_seen_wallclock[aid]

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
            )
        self._previous_tick = tick
        self._latest_tick = tick
        self._ticks.append(tick)
        if not tick.running:
            self.view_mode = "finished"

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
            if frame is None:
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

        Tick-drain can assign every queued tick the same ``time.monotonic()``
        value (late-connect backfill). Desired altitude must keep advancing
        after the queue empties, or takeoff freezes near the ground.
        """
        if self._handles is None or self._header is None or self._latest_tick is None:
            return
        now = time.monotonic()
        tick = self._latest_tick
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
            handles.desired_altitude[aid] = desired_altitude(
                now - first,
                frame.position,
                header,
                tick,
                just_struck,
                authoritative_altitude=auth_z,
            )

    def _selected_jet_pose(self):
        if self._latest_tick is None or self.selected_jet_id is None:
            return None
        for frame in self._latest_tick.jets:
            if frame.agent_id != self.selected_jet_id:
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
        self._hud.update(
            mode=self.view_mode,
            header=self._header,
            latest_tick=self._latest_tick,
            camera_mode=self.camera_mode,
            selected_jet_id=self.selected_jet_id,
            disconnect_reason=self._disconnect_reason,
            ticks=self._ticks,
            orbit_center=center,
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
    app = WraithNetViewer3D(parsed.host, parsed.port)
    app.run()


if __name__ == "__main__":
    main()
