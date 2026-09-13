"""2D HUD overlay for viewer3d: four corner-anchored panels styled after the
Ace Combat mission-briefing reference (design doc §6.3/§6.5), each using a
'[ BRACKETED LABEL ]' + monospace-value convention. Distinct from
viewer/render.py's single-sidebar layout by design -- see design doc §6.5.

Minimap: a hand-drawn 2D DirectGUI dot-plot (design-doc option (b)), not a
second orthographic Camera/DisplayRegion. Option (a) needs a real
GraphicsWindow; Phase 16's headless `window-type none` config leaves
ShowBase.win as None, so a second viewport is not reliable here.
"""

from __future__ import annotations

from collections import Counter

from direct.gui.DirectGui import DirectFrame
from direct.gui.OnscreenText import OnscreenText
from panda3d.core import TextNode, TransparencyAttrib

from viewer3d.highlights import RunSummary, compute_run_summary
from wraithnet.trace_schemas import TraceHeader, TraceTick

# Copied (not imported) from viewer/render.py so viewer3d does not depend
# on the Pygame package. RGB 0-1 for OnscreenText.
ROLE_RING_COLOR = {
    "IDLE": (140 / 255, 140 / 255, 140 / 255, 1.0),
    "MANAGER": (230 / 255, 200 / 255, 60 / 255, 1.0),
    "CONTRACTOR": (90 / 255, 220 / 255, 90 / 255, 1.0),
    "CONTRACTOR_PENDING": (230 / 255, 140 / 255, 40 / 255, 1.0),
    "RTB": (80 / 255, 160 / 255, 255 / 255, 1.0),
}

CYAN = (0.45, 0.88, 1.0, 1.0)
CYAN_DIM = (0.38, 0.70, 0.85, 1.0)
WHITE = (0.92, 0.95, 0.98, 1.0)
MUTED = (0.55, 0.62, 0.70, 1.0)
PANEL_BG = (0.02, 0.07, 0.11, 0.62)
GREEN = (0.35, 0.82, 0.43, 1.0)
RED = (0.86, 0.31, 0.31, 1.0)
JET_PALETTE = [(80 / 255, 200 / 255, 255 / 255, 1.0), (255 / 255, 160 / 255, 60 / 255, 1.0), (170 / 255, 255 / 255, 120 / 255, 1.0)]
TARGET_DOT = {
    "HARDENED_BUNKER": (150 / 255, 100 / 255, 60 / 255, 1.0),
    "MOBILE_RADAR": (70 / 255, 120 / 255, 220 / 255, 1.0),
    "SAM_SITE": (210 / 255, 60 / 255, 60 / 255, 1.0),
}
JAMMER_DOT = (170 / 255, 90 / 255, 220 / 255, 1.0)
TYPE_LABEL = {
    "HARDENED_BUNKER": "BUNKER",
    "MOBILE_RADAR": "RADAR",
    "SAM_SITE": "SAM",
}

_STATUS_LABEL = {
    "connecting": "CONNECTING",
    "editor": "EDITOR",
    "brief": "MISSION BRIEF",
    "live": "LIVE",
    "finished": "FINISHED",
    "launch_failed": "LAUNCH FAILED",
}

_MAX_MINIMAP_DOTS = 48


def _text(
    parent,
    *,
    pos: tuple[float, float],
    align,
    scale: float = 0.038,
    fg=CYAN,
) -> OnscreenText:
    return OnscreenText(
        text=" ",
        parent=parent,
        pos=pos,
        scale=scale,
        fg=fg,
        align=align,
        mayChange=True,
        shadow=(0, 0, 0, 0.7),
        shadowOffset=(0.04, 0.04),
    )


def _corner_parent(base, attr: str):
    node = getattr(base, attr, None)
    if node is not None:
        return node
    return getattr(base, "aspect2d", None)


class Hud:
    def __init__(self, base) -> None:
        # Build four DirectFrame/OnscreenText groups once, positioned at the
        # four screen corners via aspect2d anchoring: top-left (mission
        # info), top-right (force status table), bottom-right (coordinates
        # + minimap), bottom-left (camera-mode indicator + key bindings).
        self.base = base
        self.mission_info_text = ""
        self.force_status_text = ""
        self.coordinates_text = ""
        self.controls_text = ""
        self.summary_text = ""

        tl = _corner_parent(base, "a2dTopLeft")
        tr = _corner_parent(base, "a2dTopRight")
        br = _corner_parent(base, "a2dBottomRight")
        bl = _corner_parent(base, "a2dBottomLeft")

        self._mission_frame = DirectFrame(
            parent=tl,
            frameColor=PANEL_BG,
            frameSize=(0.04, 0.78, -0.28, -0.02),
            pos=(0, 0, 0),
            relief=None,
        )
        self._mission_frame.set_transparency(TransparencyAttrib.MAlpha)
        self._mission_node = _text(
            tl, pos=(0.08, -0.07), align=TextNode.ALeft, scale=0.036
        )

        self._force_frame = DirectFrame(
            parent=tr,
            frameColor=PANEL_BG,
            frameSize=(-0.78, -0.04, -0.36, -0.02),
            pos=(0, 0, 0),
            relief=None,
        )
        self._force_frame.set_transparency(TransparencyAttrib.MAlpha)
        self._force_node = _text(
            tr, pos=(-0.08, -0.07), align=TextNode.ARight, scale=0.034
        )
        # Role-ring swatches reuse viewer/render.py::ROLE_RING_COLOR so the
        # 2D and 3D HUDs share a palette even though they are independent.
        self._role_swatches = []
        for i, (_role, color) in enumerate(ROLE_RING_COLOR.items()):
            swatch = DirectFrame(
                parent=tr,
                frameColor=color,
                frameSize=(-0.016, 0.016, -0.016, 0.016),
                pos=(-0.74, 0, -0.40 - i * 0.04),
                relief=None,
            )
            self._role_swatches.append(swatch)

        self._coord_frame = DirectFrame(
            parent=br,
            frameColor=PANEL_BG,
            frameSize=(-0.52, -0.04, 0.04, 0.62),
            pos=(0, 0, 0),
            relief=None,
        )
        self._coord_frame.set_transparency(TransparencyAttrib.MAlpha)
        self._coord_node = _text(
            br, pos=(-0.08, 0.55), align=TextNode.ARight, scale=0.034
        )

        radar_size = 0.18
        self._minimap_frame = DirectFrame(
            parent=br,
            frameColor=(0.01, 0.04, 0.07, 0.85),
            frameSize=(-radar_size, radar_size, -radar_size, radar_size),
            pos=(-0.28, 0, 0.26),
            relief=None,
            borderWidth=(0.005, 0.005),
        )
        self._minimap_frame.set_transparency(TransparencyAttrib.MAlpha)
        self._minimap_half = radar_size
        self._minimap_dots: list[DirectFrame] = []
        for _ in range(_MAX_MINIMAP_DOTS):
            dot = DirectFrame(
                parent=self._minimap_frame,
                frameColor=(1, 1, 1, 1),
                frameSize=(-0.012, 0.012, -0.012, 0.012),
                pos=(0, 0, 0),
                relief=None,
            )
            dot.hide()
            self._minimap_dots.append(dot)

        self._controls_frame = DirectFrame(
            parent=bl,
            frameColor=PANEL_BG,
            frameSize=(0.04, 0.72, 0.04, 0.36),
            pos=(0, 0, 0),
            relief=None,
        )
        self._controls_frame.set_transparency(TransparencyAttrib.MAlpha)
        self._controls_node = _text(
            bl, pos=(0.08, 0.30), align=TextNode.ALeft, scale=0.032, fg=CYAN_DIM
        )

        aspect = getattr(base, "aspect2d", None)
        self._summary_node = _text(
            aspect,
            pos=(0.0, 0.12),
            align=TextNode.ACenter,
            scale=0.045,
            fg=WHITE,
        )
        self._summary_node.hide()
        self._debug_node = _text(
            aspect,
            pos=(0.0, -0.92),
            align=TextNode.ACenter,
            scale=0.028,
            fg=MUTED,
        )
        self._debug_node.hide()
        self.presentation_debug_text = ""

        # Seed placeholder copy so every panel is independently non-empty
        # even before the first update() call.
        self._set_mission("[ SCENARIO ]\n  --\n[ STATUS ]\n  CONNECTING\n[ TICK ]\n  --")
        self._set_force("[ ALL ENEMIES ]\n  --\n[ JETS ]\n  --")
        self._set_coords("[ COORDINATES ]\n  --")
        self._set_controls("[ CAMERA ]\n  --\n[ CONTROLS ]\n  O  orbit\n  C  chase")
        self._visible = True

    def set_visible(self, visible: bool) -> None:
        self._visible = visible
        nodes = [
            self._mission_frame,
            self._mission_node,
            self._force_frame,
            self._force_node,
            self._coord_frame,
            self._coord_node,
            self._minimap_frame,
            self._controls_frame,
            self._controls_node,
            self._summary_node,
            self._debug_node,
            *self._role_swatches,
        ]
        for node in nodes:
            if node is None:
                continue
            if visible:
                node.show()
            else:
                node.hide()
        if visible and not self.summary_text:
            self._summary_node.hide()
        if visible and not self.presentation_debug_text:
            self._debug_node.hide()

    def _set_mission(self, text: str) -> None:
        self.mission_info_text = text
        self._mission_node.setText(text)

    def _set_force(self, text: str) -> None:
        self.force_status_text = text
        self._force_node.setText(text)

    def _set_coords(self, text: str) -> None:
        self.coordinates_text = text
        self._coord_node.setText(text)

    def _set_controls(self, text: str) -> None:
        self.controls_text = text
        self._controls_node.setText(text)

    def update(
        self,
        mode: str,  # "connecting" | "brief" | "live" | "finished"
        header: TraceHeader | None,
        latest_tick: TraceTick | None,
        camera_mode: str,  # "orbit" | "chase" | "brief"
        selected_jet_id: str | None,
        disconnect_reason: str | None = None,  # from DisconnectedEvent, Phase 16
        ticks: list[TraceTick] | None = None,
        orbit_center: tuple[float, float, float] | None = None,
        sampled_xy: dict[str, tuple[float, float]] | None = None,
        presentation_debug: str | None = None,
    ) -> None:
        self._update_mission_info(mode, header, latest_tick, disconnect_reason)
        self._update_force_status(header, latest_tick)
        self._update_coordinates_and_minimap(
            header,
            latest_tick,
            selected_jet_id,
            camera_mode,
            orbit_center,
            sampled_xy,
        )
        self._update_controls(camera_mode)
        self._update_presentation_debug(presentation_debug)
        # Overlay only when the *presented* tick is terminal — not merely
        # because ingest/stream already closed (mode may flip early otherwise).
        if latest_tick is not None and not latest_tick.running:
            self._show_end_of_run_summary(header, latest_tick, ticks or [])
        else:
            self._summary_node.hide()
            self.summary_text = ""

    def _update_presentation_debug(self, text: str | None) -> None:
        if not text:
            self.presentation_debug_text = ""
            self._debug_node.hide()
            return
        self.presentation_debug_text = text
        self._debug_node.setText(text)
        if self._visible:
            self._debug_node.show()

    def _update_mission_info(
        self,
        mode: str,
        header: TraceHeader | None,
        latest_tick: TraceTick | None,
        disconnect_reason: str | None,
    ) -> None:
        scenario = "WraithNet"
        if header is not None:
            scenario = f"WraithNet  {header.grid_width:.0f}x{header.grid_height:.0f}"
        status = _STATUS_LABEL.get(mode, mode.upper())
        if mode == "launch_failed":
            detail = disconnect_reason or "sim subprocess exited before sending a header"
            status = f"LAUNCH FAILED\n  {detail}"
        elif disconnect_reason:
            status = f"DISCONNECTED\n  {disconnect_reason}"
        if latest_tick is not None:
            tick_line = f"{latest_tick.tick}"
        elif mode == "connecting":
            tick_line = "--"
        else:
            tick_line = "awaiting"
        text = (
            f"[ SCENARIO ]\n  {scenario}\n"
            f"[ STATUS ]\n  {status}\n"
            f"[ TICK ]\n  {tick_line}"
        )
        self._set_mission(text)

    def _update_force_status(
        self, header: TraceHeader | None, latest_tick: TraceTick | None
    ) -> None:
        if header is None:
            self._set_force("[ ALL ENEMIES ]\n  --\n[ JETS ]\n  --")
            return
        totals = Counter(header.target_types[tid] for tid in header.target_ids)
        destroyed_ids: set[str] = set()
        if latest_tick is not None:
            destroyed_ids = {
                t.target_id for t in latest_tick.targets if t.hp_remaining <= 0
            }
        alive = Counter()
        for tid in header.target_ids:
            if tid not in destroyed_ids:
                alive[header.target_types[tid]] += 1
        lines = ["[ ALL ENEMIES ]"]
        for ttype in ("HARDENED_BUNKER", "MOBILE_RADAR", "SAM_SITE"):
            total = totals.get(ttype, 0)
            if total == 0:
                continue
            remaining = alive.get(ttype, 0)
            lines.append(f"  {TYPE_LABEL[ttype]:<7} {remaining} / {total}")
        jets_alive = len(latest_tick.jets) if latest_tick is not None else len(header.jet_ids)
        lines.append("[ JETS ]")
        lines.append(f"  ALIVE   {jets_alive} / {len(header.jet_ids)}")
        if latest_tick is not None:
            by_role = Counter((j.role or "IDLE") for j in latest_tick.jets)
            for role in ("MANAGER", "CONTRACTOR", "CONTRACTOR_PENDING", "IDLE", "RTB"):
                if by_role.get(role, 0):
                    lines.append(f"  {role}  {by_role[role]}")
            for frame in latest_tick.jets:
                ftype = (header.jet_frame_types or {}).get(frame.agent_id, "STRIKE_FIGHTER")
                stats = (header.jet_frame_stats or {}).get(ftype) or {}
                cap = float(stats.get("fuel_capacity") or 0.0) or 1.0
                hp_max = float(stats.get("hp") or 5.0)
                lines.append(
                    f"  {frame.agent_id}  HP {frame.hp:.0f}/{hp_max:.0f}  "
                    f"FUEL {frame.fuel:.0f}/{cap:.0f}"
                )
        self._set_force("\n".join(lines))

    def _update_coordinates_and_minimap(
        self,
        header: TraceHeader | None,
        latest_tick: TraceTick | None,
        selected_jet_id: str | None,
        camera_mode: str,
        orbit_center: tuple[float, float, float] | None,
        sampled_xy: dict[str, tuple[float, float]] | None = None,
    ) -> None:
        focus_label = "MAP"
        coords = (50.0, 50.0, 0.0)
        if camera_mode == "chase" and selected_jet_id:
            if sampled_xy and selected_jet_id in sampled_xy:
                xy = sampled_xy[selected_jet_id]
                coords = (xy[0], xy[1], 0.0)
                focus_label = selected_jet_id
            elif latest_tick is not None:
                for frame in latest_tick.jets:
                    if frame.agent_id == selected_jet_id:
                        coords = (frame.position[0], frame.position[1], 0.0)
                        focus_label = selected_jet_id
                        break
        elif orbit_center is not None:
            coords = orbit_center
            focus_label = "MAP"
        elif header is not None:
            coords = (header.grid_width / 2.0, header.grid_height / 2.0, 0.0)
        text = (
            f"[ COORDINATES ]\n"
            f"  {coords[0]:6.1f}  {coords[1]:6.1f}\n"
            f"  FOCUS  {focus_label}"
        )
        self._set_coords(text)
        self._update_minimap(header, latest_tick, selected_jet_id, sampled_xy)

    def _update_minimap(
        self,
        header: TraceHeader | None,
        latest_tick: TraceTick | None,
        selected_jet_id: str | None,
        sampled_xy: dict[str, tuple[float, float]] | None = None,
    ) -> None:
        for dot in self._minimap_dots:
            dot.hide()
        if header is None:
            return
        gw, gh = header.grid_width, header.grid_height
        half = self._minimap_half * 0.88
        idx = 0

        def _place(x: float, y: float, color, scale: float = 1.0) -> None:
            nonlocal idx
            if idx >= len(self._minimap_dots):
                return
            nx = (x / gw) * 2.0 - 1.0
            ny = (y / gh) * 2.0 - 1.0
            dot = self._minimap_dots[idx]
            dot.set_pos(nx * half, 0, ny * half)
            dot["frameColor"] = color
            s = 0.012 * scale
            dot["frameSize"] = (-s, s, -s, s)
            dot.show()
            idx += 1

        for jid, pos in header.jammer_positions.items():
            _place(pos[0], pos[1], JAMMER_DOT, 0.8)
        destroyed: set[str] = set()
        if latest_tick is not None:
            destroyed = {t.target_id for t in latest_tick.targets if t.hp_remaining <= 0}
        for tid in header.target_ids:
            pos = header.target_positions[tid]
            ttype = header.target_types[tid]
            color = TARGET_DOT.get(ttype, (0.8, 0.8, 0.8, 1.0))
            if tid in destroyed:
                color = (0.3, 0.3, 0.3, 0.8)
            _place(pos[0], pos[1], color, 1.0)
        if latest_tick is not None:
            for i, frame in enumerate(latest_tick.jets):
                color = JET_PALETTE[i % len(JET_PALETTE)]
                scale = 1.4 if frame.agent_id == selected_jet_id else 1.0
                if sampled_xy and frame.agent_id in sampled_xy:
                    xy = sampled_xy[frame.agent_id]
                    _place(xy[0], xy[1], color, scale)
                else:
                    _place(frame.position[0], frame.position[1], color, scale)

    def _update_controls(self, camera_mode: str) -> None:
        mode = camera_mode.upper()
        text = (
            f"[ CAMERA ]\n  {mode}\n"
            f"[ CONTROLS ]\n"
            f"  O      orbit\n"
            f"  C      chase\n"
            f"  Tab/N  next jet\n"
            f"  F3     motion dbg\n"
            f"  Esc    quit"
        )
        self._set_controls(text)

    def _show_end_of_run_summary(
        self,
        header: TraceHeader | None,
        latest_tick: TraceTick | None,
        ticks: list[TraceTick],
    ) -> None:
        if header is None:
            self._summary_node.hide()
            self.summary_text = ""
            return
        summary: RunSummary = compute_run_summary(header, ticks)
        if summary.swarm_wiped_out:
            title, fg = "SWARM WIPED OUT", RED
        elif getattr(summary, "mission_aborted", False):
            title, fg = "MISSION ABORTED", (0.86, 0.70, 0.31, 1.0)
        elif summary.solved:
            title, fg = "MISSION COMPLETE", GREEN
        else:
            title, fg = "END OF RUN", WHITE
        weapon_bits = ", ".join(
            f"{k} {v}" for k, v in sorted(summary.per_weapon_type_missiles_fired.items())
        ) or "none"
        text = (
            f"{title}\n"
            f"Targets destroyed  {summary.targets_destroyed}/{summary.total_targets}\n"
            f"Jets destroyed     {summary.jets_destroyed}/{summary.total_jets}\n"
            f"Returned to base   {getattr(summary, 'jets_returned_to_base', 0)}\n"
            f"Still flying       {getattr(summary, 'jets_still_flying', 0)}\n"
            f"Missiles fired     {summary.total_missiles_fired}  ({weapon_bits})"
        )
        self.summary_text = text
        self._summary_node.setText(text)
        self._summary_node.setFg(fg)
        self._summary_node.show()
