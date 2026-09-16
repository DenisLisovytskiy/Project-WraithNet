"""2D HUD overlay for viewer3d: four corner-anchored instrument panels styled
after the Ace Combat mission-briefing reference (design doc §6.3/§6.5), each
using the '[ BRACKETED LABEL ]' + monospace-value convention. Distinct from
viewer/render.py's single-sidebar layout by design -- see design doc §6.5.

Chrome comes from `viewer3d.ui_chrome` (surfaces, borders, text levels, the
spacing/type scales, panels, rules, sections, kv-rows) so the live HUD and the
scenario editor read as one instrument instead of two hand-tuned palettes.
Only the *tactical entity* colors below stay local: they are copied (not
imported) from viewer/render.py so viewer3d never pulls in Pygame while the 2D
and 3D pictures still agree on what a SAM site or a MANAGER ring looks like.

Every filled widget here carries an explicit relief. `relief=None` makes
DirectGUI ignore `frameColor` outright, so the panels, the minimap box, all 48
minimap dots and the role swatches used to paint *nothing* -- the live HUD was
unbacked text floating over the wireframe. See ui_chrome's module docstring.

Layout: each panel owns a content node placed at its inner top-left, and all
content is laid out downward from that node in `SPACE_*`/`ROW_H_*` steps, so a
panel's height is derived from what it actually contains (`_resize_panel`)
rather than reserving dead space, and a panel and its text can no longer drift
apart -- the text is parented *into* the panel.

Minimap: a hand-drawn 2D DirectGUI dot-plot (design-doc option (b)), not a
second orthographic Camera/DisplayRegion. Option (a) needs a real
GraphicsWindow; Phase 16's headless `window-type none` config leaves
ShowBase.win as None, so a second viewport is not reliable here.
"""

from __future__ import annotations

import textwrap
from collections import Counter

from direct.gui import DirectGuiGlobals as DGG
from direct.gui.DirectGui import DirectFrame
from direct.gui.OnscreenText import OnscreenText
from panda3d.core import TextNode, TransparencyAttrib

from viewer3d.fonts import mono_font, ui_font
from viewer3d.highlights import RunSummary, compute_run_summary
from viewer3d.ui_chrome import (
    ACCENT,
    ACCENT_DIM,
    BORDER_BRIGHT,
    BORDER_SOFT,
    BORDER_STRONG,
    BORDER_W,
    PANEL_PAD,
    ROW_H_TIGHT,
    RULE_H,
    SPACE_1,
    SPACE_2,
    SPACE_3,
    SPACE_4,
    STATUS_FRIENDLY,
    STATUS_HOSTILE,
    STATUS_INACTIVE,
    STATUS_NEUTRAL,
    STATUS_WARNING,
    SURFACE_DEEP,
    TEXT_BODY,
    TEXT_SECONDARY,
    TEXT_STRONG,
    TYPE_BODY,
    TYPE_HEADER,
    # ui_chrome's row-text step, aliased: this module's own TYPE_LABEL is the
    # target-type name map copied from viewer/render.py.
    TYPE_LABEL as TYPE_ROW,
    TYPE_MICRO,
    TYPE_TITLE,
    clamp_scale,
    make_kv_row,
    make_label,
    make_panel,
    make_rule,
    make_section,
    make_title,
    tint,
)
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

#: Accent/stripe tone per view mode -- cyan is the resting state, amber means
#: "waiting", green means "running", red is a failure (design doc §6.3: sparse
#: accents on a monochrome cyan HUD).
_MODE_TONE = {
    "connecting": STATUS_WARNING,
    "editor": ACCENT_DIM,
    "brief": ACCENT,
    "live": STATUS_FRIENDLY,
    "finished": STATUS_NEUTRAL,
    "launch_failed": STATUS_HOSTILE,
}

_TARGET_ORDER = ("HARDENED_BUNKER", "MOBILE_RADAR", "SAM_SITE")
_ROLE_ORDER = ("MANAGER", "CONTRACTOR", "CONTRACTOR_PENDING", "IDLE", "RTB")

_MAX_MINIMAP_DOTS = 48
#: Per-jet element rows kept in the pool; the last one degrades to "+ N MORE".
_MAX_JET_ROWS = 8

# --- panel geometry (aspect2d units; corner-relative, aspect-independent) ---
_MARGIN = SPACE_4
_PANEL_INSET = BORDER_W + PANEL_PAD
_WIDE_W = 0.86  # mission (top-left) and force (top-right)
_CONTROLS_W = 0.62
_RADAR_W = 0.46
_MINIMAP_SIZE = 0.36
_SUMMARY_W = 0.62
#: A panel never shrinks below this, so short copy still reads as an instrument.
_MIN_PANEL_W = 0.44
#: Hanging indent of a value row under its `[ BRACKET ]` label (two mono ems).
_ROW_INDENT = 0.034
#: One source for both the rendered key table and `controls_text`.
_KEY_BINDINGS = (
    ("O", "orbit"),
    ("C", "chase"),
    ("Tab/N", "next jet"),
    ("F3", "motion dbg"),
    ("Esc", "quit"),
)


def _text(
    parent,
    *,
    pos: tuple[float, float],
    align=TextNode.ALeft,
    scale: float = TYPE_ROW,
    fg=TEXT_BODY,
    mono: bool = True,
) -> OnscreenText:
    """A live-updating readout. Glyph-only, so no relief (nothing to fill)."""
    kwargs = {}
    font = mono_font() if mono else ui_font()
    if font is not None:
        kwargs["font"] = font
    return OnscreenText(
        text=" ",
        parent=parent,
        pos=pos,
        scale=clamp_scale(scale),
        fg=fg,
        align=align,
        mayChange=True,
        shadow=(0, 0, 0, 0.45),
        shadowOffset=(0.05, 0.05),
        **kwargs,
    )


def _rect(parent, color, *, pos, half_w: float, half_h: float) -> DirectFrame:
    """A filled rectangle (swatch / minimap dot / crosshair arm).

    `relief=DGG.FLAT` is mandatory: with `relief=None` DirectGUI ignores
    `frameColor` and paints nothing at all.
    """
    frame = DirectFrame(
        parent=parent,
        frameColor=tuple(color),
        frameSize=(-half_w, half_w, -half_h, half_h),
        pos=pos,
        relief=DGG.FLAT,
        borderWidth=(0, 0),
        state=DGG.DISABLED,
    )
    frame.set_transparency(TransparencyAttrib.MAlpha)
    return frame


def _line_pitch(scale: float) -> float:
    """Baseline-to-baseline distance of a multi-line mono readout."""
    height = 1.0
    font = mono_font()
    if font is not None:
        try:
            height = float(font.get_line_height()) or 1.0
        except Exception:  # noqa: BLE001 - a static font, or an older Panda3D
            height = 1.0
    return scale * height


def _block_bottom(top_baseline: float, lines: int, scale: float) -> float:
    """`z` just under a `lines`-line block whose first baseline is `top_baseline`."""
    return top_baseline - max(lines - 1, 0) * _line_pitch(scale) - scale * 0.42


def _text_extent(node, scale: float) -> tuple[float, float]:
    """Measured `(right, bottom)` of a readout, in its parent's units.

    Panels are fitted to this instead of a guessed line count, so a readout
    can never overflow its panel or leave a band of dead space beside it.
    `scale` has to be passed in: `OnscreenText` bakes its scale into a
    transform matrix, so the NodePath's own scale reads back as 1.
    """
    text_node = getattr(node, "textNode", None)
    try:
        return (
            float(text_node.get_right()) * scale,
            float(text_node.get_bottom()) * scale,
        )
    except Exception:  # noqa: BLE001 - measurement is a nicety, never a crash
        return 0.0, -scale


def _resize_panel(
    panel,
    *,
    bottom: float | None = None,
    top: float | None = None,
    right: float | None = None,
) -> None:
    """Re-fit a `make_panel` result (border shell + inset fill) to its content."""
    left, edge, low, high = (float(v) for v in panel["frameSize"])
    fill = getattr(panel, "_chrome_fill", None)
    border = 0.0
    if fill is not None:
        border = max(float(fill["frameSize"][0]) - left, 0.0)
    low = low if bottom is None else float(bottom)
    high = high if top is None else float(top)
    edge = edge if right is None else float(right)
    if high - low < 4.0 * border:  # never let the fill go degenerate
        low = high - 4.0 * border
    panel["frameSize"] = (left, edge, low, high)
    if fill is not None:
        f_left, _fr, _fb, _ft = (float(v) for v in fill["frameSize"])
        fill["frameSize"] = (f_left, edge - border, low + border, high - border)


def _wrap(text: str, width: int = 40, indent: str = "  ") -> list[str]:
    """Word-wrap a detail string into indented mono lines (never mid-phrase
    for a short reason, which HUD tests match as a whole substring)."""
    clean = " ".join(str(text).split())
    if not clean:
        return []
    return [indent + line for line in textwrap.wrap(clean, width=width) or [clean]]


def _kv_line(label: str, value: str, width: int = 14) -> str:
    return f"{label:<{width}}{value}"


def _hp_tone(hp: float, hp_max: float):
    frac = hp / hp_max if hp_max > 0 else 0.0
    if frac <= 0.34:
        return STATUS_HOSTILE
    if frac <= 0.67:
        return STATUS_WARNING
    return STATUS_FRIENDLY


def _corner_parent(base, attr: str):
    node = getattr(base, attr, None)
    if node is not None:
        return node
    return getattr(base, "aspect2d", None)


class Hud:
    def __init__(self, base) -> None:
        # Four corner-anchored instrument panels: top-left mission state,
        # top-right force status + element table, bottom-right coordinates +
        # radar, bottom-left camera/controls/role legend. Each panel is a
        # ui_chrome glass panel that *contains* its own text.
        self.base = base
        self.mission_info_text = ""
        self.force_status_text = ""
        self.coordinates_text = ""
        self.controls_text = ""
        self.summary_text = ""
        self.presentation_debug_text = ""

        tl = _corner_parent(base, "a2dTopLeft")
        tr = _corner_parent(base, "a2dTopRight")
        br = _corner_parent(base, "a2dBottomRight")
        bl = _corner_parent(base, "a2dBottomLeft")

        self._tone = None
        self._build_mission_panel(tl)
        self._build_force_panel(tr)
        self._build_radar_panel(br)
        self._build_controls_panel(bl)
        self._build_overlays(getattr(base, "aspect2d", None))

        # Seed placeholder copy so every panel is independently non-empty
        # even before the first update() call.
        self._update_mission_info("connecting", None, None, None)
        self._update_force_status(None, None)
        self._update_coordinates_and_minimap(None, None, None, "orbit", None, None)
        self._update_controls("orbit")
        self._visible = True

    # ------------------------------------------------------------------ build
    def _content_node(self, panel, x: float, top: float):
        """Grouping node at a panel's inner top-left; content hangs below it.

        Attached after the panel's fill so it draws over it, and a plain
        PandaNode rather than a DirectFrame -- an invisible DirectFrame is
        exactly the `relief=None` trap ui_chrome warns about.
        """
        node = panel.attach_new_node("hud-content")
        node.set_pos(x, 0, top)
        return node

    def _build_mission_panel(self, parent) -> None:
        top = -_MARGIN
        self._mission_left = _MARGIN
        self._mission_frame = make_panel(
            parent,
            frame_size=(_MARGIN, _MARGIN + _WIDE_W, top - 0.30, top),
            border_color=BORDER_STRONG,
        )
        self._mission_content_z = top - _PANEL_INSET
        content = self._content_node(
            self._mission_frame, _MARGIN + _PANEL_INSET, self._mission_content_z
        )
        # Mode stripe: the one glance-level cue for connecting / live / failed.
        self._mission_stripe = make_rule(
            content,
            pos=(0, 0, 0),
            width=_WIDE_W - 2 * _PANEL_INSET,
            thickness=RULE_H * 2.4,
            color=ACCENT,
        )
        self._mission_top = -SPACE_3 - TYPE_BODY * 0.82
        self._mission_node = _text(
            content,
            pos=(0, self._mission_top),
            scale=TYPE_BODY,
            fg=TEXT_BODY,
        )

    def _build_force_panel(self, parent) -> None:
        top = -_MARGIN
        right = -_MARGIN
        self._force_frame = make_panel(
            parent,
            frame_size=(right - _WIDE_W, right, top - 0.50, top),
            border_color=BORDER_STRONG,
        )
        content_w = _WIDE_W - 2 * _PANEL_INSET
        self._force_content_z = top - _PANEL_INSET
        content = self._content_node(
            self._force_frame, right - _WIDE_W + _PANEL_INSET, self._force_content_z
        )
        self._force_content_w = content_w
        self._force_top = -TYPE_BODY * 0.82
        self._force_node = _text(
            content, pos=(0, self._force_top), scale=TYPE_BODY, fg=TEXT_BODY
        )
        # Underlines for the readout's own `[ BRACKET ]` lines: the counts are
        # one text node (it *is* `force_status_text`), so the section structure
        # is drawn around it rather than baked into per-row widgets.
        self._force_section_rules = [
            make_rule(content, pos=(0, 0, -0.30), width=content_w, color=ACCENT_DIM)
            for _ in range(2)
        ]
        self._force_rule = make_rule(
            content, pos=(0, 0, -0.30), width=content_w, color=BORDER_SOFT
        )
        for rule in (*self._force_section_rules, self._force_rule):
            rule.hide()
        # Per-jet element table: pooled kv-rows, mono values, so HP/FUEL
        # columns line up (space-padding a proportional font never did).
        self._jet_rows = []
        for _ in range(_MAX_JET_ROWS):
            row = make_kv_row(
                content,
                "",
                "",
                pos=(_ROW_INDENT, 0, 0),
                width=content_w - _ROW_INDENT,
                scale=TYPE_BODY,
                key_fg=TEXT_SECONDARY,
            )
            row.hide()
            self._jet_rows.append(row)

    def _build_radar_panel(self, parent) -> None:
        right = -_MARGIN
        left = right - _RADAR_W
        content_w = _RADAR_W - 2 * _PANEL_INSET
        self._coord_frame = make_panel(
            parent,
            frame_size=(left, right, _MARGIN, _MARGIN + 0.20),
            border_color=BORDER_STRONG,
        )
        content = self._content_node(self._coord_frame, left + _PANEL_INSET, 0.0)

        section = make_section(
            content, "[ RADAR ]", pos=(0, 0, -TYPE_BODY * 0.82), width=content_w,
            scale=TYPE_BODY,
        )
        half = _MINIMAP_SIZE / 2.0
        map_center = section.next_z - half
        border = SPACE_1 * 0.7
        self._minimap_frame = make_panel(
            content,
            frame_size=(-half, half, -half, half),
            pos=(content_w / 2.0, 0, map_center),
            border=border,
            fill_color=SURFACE_DEEP,
            border_color=BORDER_BRIGHT,
        )
        self._minimap_half = half - border - SPACE_1 * 0.5
        plate = getattr(self._minimap_frame, "_chrome_fill", self._minimap_frame)
        grid = tint(BORDER_SOFT, 0.9, alpha=0.35)
        make_rule(
            plate,
            pos=(0, 0, 0),
            width=self._minimap_half * 2.0,
            color=grid,
            anchor="center",
        )
        _rect(plate, grid, pos=(0, 0, 0), half_w=RULE_H / 2.0, half_h=self._minimap_half)
        self._minimap_dots: list[DirectFrame] = []
        for _ in range(_MAX_MINIMAP_DOTS):
            dot = _rect(plate, (1, 1, 1, 1), pos=(0, 0, 0), half_w=0.010, half_h=0.010)
            dot.hide()
            self._minimap_dots.append(dot)

        coord_section = make_section(
            content,
            "[ COORDINATES ]",
            pos=(0, 0, map_center - half - SPACE_3 - TYPE_BODY * 0.82),
            width=content_w,
            scale=TYPE_BODY,
        )
        coord_top = coord_section.next_z - TYPE_HEADER * 0.82
        self._coord_node = _text(
            content, pos=(0, coord_top), scale=TYPE_HEADER, fg=TEXT_STRONG
        )
        used = -_block_bottom(coord_top, 2, TYPE_HEADER)
        height = used + 2 * _PANEL_INSET
        _resize_panel(self._coord_frame, bottom=_MARGIN, top=_MARGIN + height)
        content.set_z(_MARGIN + height - _PANEL_INSET)

    def _build_controls_panel(self, parent) -> None:
        left = _MARGIN
        content_w = _CONTROLS_W - 2 * _PANEL_INSET
        self._controls_frame = make_panel(
            parent,
            frame_size=(left, left + _CONTROLS_W, _MARGIN, _MARGIN + 0.20),
            border_color=BORDER_STRONG,
        )
        content = self._content_node(self._controls_frame, left + _PANEL_INSET, 0.0)

        camera = make_section(
            content, "[ CAMERA ]", pos=(0, 0, -TYPE_BODY * 0.82), width=content_w,
            scale=TYPE_BODY,
        )
        mode_z = camera.next_z - TYPE_HEADER * 0.82
        self._controls_node = _text(
            content,
            pos=(_ROW_INDENT, mode_z),
            scale=TYPE_HEADER,
            fg=ACCENT,
        )
        z = _block_bottom(mode_z, 1, TYPE_HEADER) - SPACE_3

        keys = make_section(
            content, "[ CONTROLS ]", pos=(0, 0, z - TYPE_BODY * 0.82), width=content_w,
            scale=TYPE_BODY,
        )
        # A row's baseline has to clear the section rule by its own ascent,
        # otherwise the rule strikes through the first line.
        row_z = keys.next_z - TYPE_ROW * 0.78
        for key, action in _KEY_BINDINGS:
            make_kv_row(
                content,
                action.upper(),
                key,
                pos=(_ROW_INDENT, 0, row_z),
                width=content_w - _ROW_INDENT,
                scale=TYPE_ROW,
                key_fg=TEXT_SECONDARY,
                value_fg=ACCENT,
            )
            row_z -= ROW_H_TIGHT
        z = row_z + ROW_H_TIGHT - TYPE_ROW * 0.42 - SPACE_3

        legend = make_section(
            content, "[ ROLES ]", pos=(0, 0, z - TYPE_BODY * 0.82), width=content_w,
            scale=TYPE_BODY,
        )
        # Role-ring swatches reuse viewer/render.py::ROLE_RING_COLOR so the
        # 2D and 3D HUDs share a palette even though they are independent.
        # They also explain the ring colors drawn around jets in the scene.
        row_z = legend.next_z - TYPE_ROW * 0.78
        swatch = TYPE_ROW * 0.40
        self._role_swatches = []
        for role, color in ROLE_RING_COLOR.items():
            self._role_swatches.append(
                _rect(
                    content,
                    color,
                    pos=(_ROW_INDENT + swatch, 0, row_z + TYPE_ROW * 0.3),
                    half_w=swatch,
                    half_h=swatch,
                )
            )
            make_label(
                content,
                role.replace("_", " "),
                pos=(_ROW_INDENT + swatch * 3.4, 0, row_z),
                scale=TYPE_MICRO,
                fg=TEXT_SECONDARY,
            )
            row_z -= ROW_H_TIGHT * 0.9
        used = -(row_z + ROW_H_TIGHT * 0.9 - TYPE_ROW * 0.42)
        height = used + 2 * _PANEL_INSET
        _resize_panel(self._controls_frame, bottom=_MARGIN, top=_MARGIN + height)
        content.set_z(_MARGIN + height - _PANEL_INSET)

    def _build_overlays(self, aspect) -> None:
        # End-of-run summary: a real card, not a bare centered text node.
        top = 0.30
        self._summary_frame = make_panel(
            aspect,
            frame_size=(-_SUMMARY_W / 2.0, _SUMMARY_W / 2.0, top - 0.42, top),
            border_color=BORDER_BRIGHT,
            fill_color=SURFACE_DEEP,
        )
        content_w = _SUMMARY_W - 2 * _PANEL_INSET
        self._summary_content_z = top - _PANEL_INSET
        content = self._content_node(
            self._summary_frame, -_SUMMARY_W / 2.0 + _PANEL_INSET, self._summary_content_z
        )
        self._summary_title = make_title(
            content,
            "",
            pos=(content_w / 2.0, 0, -TYPE_TITLE * 0.82),
            scale=TYPE_TITLE,
            align=TextNode.ACenter,
            fg=TEXT_STRONG,
        )
        self._summary_rule = make_rule(
            content,
            pos=(0, 0, _block_bottom(-TYPE_TITLE * 0.82, 1, TYPE_TITLE) - SPACE_2),
            width=content_w,
            color=BORDER_SOFT,
        )
        self._summary_top = self._summary_rule.get_z() - SPACE_3 - TYPE_BODY * 0.82
        self._summary_node = _text(
            content, pos=(0, self._summary_top), scale=TYPE_BODY, fg=TEXT_BODY
        )
        self._summary_frame.hide()
        self._summary_node.hide()

        # Motion-debug strip (F3): bottom-centre, backed so it stays readable.
        self._debug_frame = make_panel(
            aspect,
            frame_size=(-0.39, 0.39, -0.955, -0.895),
            border=SPACE_1 * 0.6,
            fill_color=tint(SURFACE_DEEP, 1.0, alpha=0.88),
            border_color=BORDER_SOFT,
        )
        self._debug_node = _text(
            self._debug_frame,
            pos=(0.0, -0.936),
            align=TextNode.ACenter,
            scale=TYPE_MICRO,
            fg=TEXT_SECONDARY,
        )
        self._debug_frame.hide()
        self._debug_node.hide()

    # ---------------------------------------------------------------- visibility
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
            self._summary_frame,
            self._summary_node,
            self._debug_frame,
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
            self._summary_frame.hide()
            self._summary_node.hide()
        if visible and not self.presentation_debug_text:
            self._debug_frame.hide()
            self._debug_node.hide()

    # ------------------------------------------------------------------ setters
    def _set_mission(self, text: str) -> None:
        self.mission_info_text = text
        self._mission_node.setText(text)
        # Fit the panel to the measured readout in both axes: a mission block
        # is three short lines most of the time and five wide ones when a
        # disconnect reason has to be spelled out.
        right, bottom = _text_extent(self._mission_node, TYPE_BODY)
        width = min(max(right + 2 * _PANEL_INSET, _MIN_PANEL_W), _WIDE_W)
        _resize_panel(
            self._mission_frame,
            right=self._mission_left + width,
            bottom=self._mission_content_z + self._mission_top + bottom - _PANEL_INSET,
        )
        inner = width - 2 * _PANEL_INSET
        self._mission_stripe["frameSize"] = (0.0, inner, -RULE_H * 1.2, RULE_H * 1.2)

    def _set_force(self, text: str, full_text: str | None = None) -> None:
        self.force_status_text = text if full_text is None else full_text
        self._force_node.setText(text)

    def _set_coords(self, text: str, panel_text: str | None = None) -> None:
        self.coordinates_text = text
        self._coord_node.setText(text if panel_text is None else panel_text)

    def _set_controls(self, text: str, panel_text: str | None = None) -> None:
        self.controls_text = text
        self._controls_node.setText(text if panel_text is None else panel_text)

    def _set_tone(self, tone) -> None:
        """Recolor the mission panel's stripe/border for the current state."""
        if self._tone == tone:
            return
        self._tone = tone
        self._mission_stripe["frameColor"] = tuple(tone)
        self._mission_frame["frameColor"] = tint(tone, 0.82, alpha=0.96)

    # ------------------------------------------------------------------- update
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
            self._summary_frame.hide()
            self._summary_node.hide()
            self.summary_text = ""

    def _update_presentation_debug(self, text: str | None) -> None:
        if not text:
            self.presentation_debug_text = ""
            self._debug_frame.hide()
            self._debug_node.hide()
            return
        self.presentation_debug_text = text
        self._debug_node.setText(text)
        if self._visible:
            self._debug_frame.show()
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
        detail: list[str] = []
        tone = _MODE_TONE.get(mode, ACCENT)
        if mode == "launch_failed":
            status = "LAUNCH FAILED"
            detail = _wrap(
                disconnect_reason or "sim subprocess exited before sending a header"
            )
        elif disconnect_reason:
            status = "DISCONNECTED"
            detail = _wrap(disconnect_reason)
            tone = STATUS_HOSTILE
        if latest_tick is not None:
            tick_line = f"{latest_tick.tick}"
        elif mode == "connecting":
            tick_line = "--"
        else:
            tick_line = "awaiting"
        lines = [
            _kv_line("[ SCENARIO ]", scenario),
            _kv_line("[ STATUS ]", status),
            *detail,
            _kv_line("[ TICK ]", tick_line),
        ]
        self._set_tone(tone)
        self._set_mission("\n".join(lines))

    def _update_force_status(
        self, header: TraceHeader | None, latest_tick: TraceTick | None
    ) -> None:
        if header is None:
            counts = ["[ ALL ENEMIES ]", "  --", "", "[ JETS ]", "  --"]
            self._set_force("\n".join(counts))
            self._layout_force(counts, 0)
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
        counts = ["[ ALL ENEMIES ]"]
        for ttype in _TARGET_ORDER:
            total = totals.get(ttype, 0)
            if total == 0:
                continue
            remaining = alive.get(ttype, 0)
            counts.append(f"  {TYPE_LABEL[ttype]:<8}{remaining} / {total}")
        jets_alive = len(latest_tick.jets) if latest_tick is not None else len(header.jet_ids)
        counts.append("")
        counts.append("[ JETS ]")
        counts.append(f"  {'ALIVE':<8}{jets_alive} / {len(header.jet_ids)}")
        frames = list(latest_tick.jets) if latest_tick is not None else []
        if latest_tick is not None:
            by_role = Counter((j.role or "IDLE") for j in latest_tick.jets)
            for role in _ROLE_ORDER:
                if by_role.get(role, 0):
                    counts.append(f"  - {role:<19}{by_role[role]}")
        jet_lines = []
        for frame in frames:
            hp_max, cap = self._jet_limits(header, frame)
            jet_lines.append(
                f"  {frame.agent_id}  HP {frame.hp:.0f}/{hp_max:.0f}  "
                f"FUEL {frame.fuel:.0f}/{cap:.0f}"
            )
        self._set_force("\n".join(counts), "\n".join(counts + jet_lines))
        used = self._fill_jet_rows(header, frames)
        self._layout_force(counts, used)

    @staticmethod
    def _jet_limits(header: TraceHeader, frame) -> tuple[float, float]:
        ftype = (header.jet_frame_types or {}).get(frame.agent_id, "STRIKE_FIGHTER")
        stats = (header.jet_frame_stats or {}).get(ftype) or {}
        cap = float(stats.get("fuel_capacity") or 0.0) or 1.0
        hp_max = float(stats.get("hp") or 5.0)
        return hp_max, cap

    def _fill_jet_rows(self, header: TraceHeader, frames: list) -> int:
        """Fill the pooled element rows; returns how many are in use."""
        budget = len(self._jet_rows)
        overflow = max(len(frames) - budget, 0)
        shown = frames[: budget - 1] if overflow else frames[:budget]
        used = 0
        for index, frame in enumerate(shown):
            hp_max, cap = self._jet_limits(header, frame)
            row = self._jet_rows[index]
            row.set_key(frame.agent_id)
            row.key["text_fg"] = JET_PALETTE[index % len(JET_PALETTE)]
            row.set_value(
                f"HP {frame.hp:>2.0f}/{hp_max:<2.0f} FUEL {frame.fuel:>4.0f}/{cap:<4.0f}",
                fg=_hp_tone(float(frame.hp), hp_max),
            )
            row.show()
            used += 1
        if overflow:
            row = self._jet_rows[used]
            row.set_key("+ MORE")
            row.key["text_fg"] = TEXT_SECONDARY
            row.set_value(f"{overflow + 1} JETS", fg=STATUS_INACTIVE)
            row.show()
            used += 1
        for row in self._jet_rows[used:]:
            row.hide()
        return used

    def _layout_force(self, count_lines: list[str], jet_rows: int) -> None:
        """Hang the element table under the counts block and re-fit the panel."""
        pitch = _line_pitch(TYPE_BODY)
        brackets = [i for i, line in enumerate(count_lines) if line.startswith("[")]
        for rule, index in zip(self._force_section_rules, brackets):
            rule.set_z(self._force_top + -index * pitch - TYPE_BODY * 0.40)
            rule.show()
        for rule in self._force_section_rules[len(brackets):]:
            rule.hide()
        bottom = self._force_top + _text_extent(self._force_node, TYPE_BODY)[1]
        if jet_rows:
            rule_z = bottom - SPACE_2
            self._force_rule.set_z(rule_z)
            self._force_rule.show()
            z = rule_z - SPACE_3 - TYPE_BODY * 0.7
            for index in range(jet_rows):
                self._jet_rows[index].node.set_pos(_ROW_INDENT, 0, z)
                z -= ROW_H_TIGHT
            bottom = z + ROW_H_TIGHT - TYPE_BODY * 0.42
        else:
            self._force_rule.hide()
        _resize_panel(
            self._force_frame, bottom=self._force_content_z + bottom - _PANEL_INSET
        )

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
        readout = f"X {coords[0]:6.1f}  Y {coords[1]:6.1f}\nFOCUS  {focus_label}"
        text = (
            f"[ COORDINATES ]\n"
            f"  {coords[0]:6.1f}  {coords[1]:6.1f}\n"
            f"  FOCUS  {focus_label}"
        )
        self._set_coords(text, readout)
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
        half = self._minimap_half * 0.92
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
            s = 0.011 * scale
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
                scale = 1.5 if frame.agent_id == selected_jet_id else 1.0
                if sampled_xy and frame.agent_id in sampled_xy:
                    xy = sampled_xy[frame.agent_id]
                    _place(xy[0], xy[1], color, scale)
                else:
                    _place(frame.position[0], frame.position[1], color, scale)

    def _update_controls(self, camera_mode: str) -> None:
        mode = camera_mode.upper()
        lines = ["[ CAMERA ]", f"  {mode}", "[ CONTROLS ]"]
        for key, action in _KEY_BINDINGS:
            lines.append(f"  {key:<7}{action}")
        self._set_controls("\n".join(lines), mode)

    def _show_end_of_run_summary(
        self,
        header: TraceHeader | None,
        latest_tick: TraceTick | None,
        ticks: list[TraceTick],
    ) -> None:
        if header is None:
            self._summary_frame.hide()
            self._summary_node.hide()
            self.summary_text = ""
            return
        summary: RunSummary = compute_run_summary(header, ticks)
        if summary.swarm_wiped_out:
            title, fg = "SWARM WIPED OUT", STATUS_HOSTILE
        elif getattr(summary, "mission_aborted", False):
            title, fg = "MISSION ABORTED", STATUS_WARNING
        elif summary.solved:
            title, fg = "MISSION COMPLETE", STATUS_FRIENDLY
        else:
            title, fg = "END OF RUN", TEXT_STRONG
        weapon_bits = ", ".join(
            f"{k} {v}" for k, v in sorted(summary.per_weapon_type_missiles_fired.items())
        ) or "none"
        rows = [
            _kv_line("Targets destroyed", f"{summary.targets_destroyed}/{summary.total_targets}", 20),
            _kv_line("Jets destroyed", f"{summary.jets_destroyed}/{summary.total_jets}", 20),
            _kv_line("Returned to base", f"{getattr(summary, 'jets_returned_to_base', 0)}", 20),
            _kv_line("Still flying", f"{getattr(summary, 'jets_still_flying', 0)}", 20),
            _kv_line("Missiles fired", f"{summary.total_missiles_fired}", 20),
            *_wrap(weapon_bits, width=28, indent="  "),
        ]
        body = "\n".join(rows)
        self.summary_text = f"{title}\n{body}"
        self._summary_title["text"] = title
        self._summary_title["text_fg"] = tuple(fg)
        self._summary_frame["frameColor"] = tint(fg, 0.9, alpha=0.96)
        self._summary_node.setText(body)
        self._summary_node.setFg(TEXT_BODY)
        _resize_panel(
            self._summary_frame,
            bottom=self._summary_content_z
            + self._summary_top
            + _text_extent(self._summary_node, TYPE_BODY)[1]
            - _PANEL_INSET,
        )
        if self._visible:
            self._summary_frame.show()
            self._summary_node.show()
