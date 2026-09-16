"""Shared tactical UI chrome for viewer3d (editor + live HUD).

This is the design-token layer *and* the widget factory layer: every panel,
label, button, entry, badge and rule in `viewer3d` should come from here so
the editor and the HUD read as one instrument, per the "Ace Combat
holographic mission-briefing" style reference in
`docs/09_3D_Live_Visualization_and_Scenario_Editor_Design_Document.md` §6.3 --
dark ground, glowing cyan/teal edges, `[ BRACKETED LABEL ]` panels,
monochrome cyan/blue with sparse accents (red hostile, green/teal friendly).

Layout model
------------
All geometry is in Panda3D aspect2d units. Sizes snap to the `SPACE_*` scale
and rows to `ROW_H` (see `row_z()` / `Stack`) instead of being hand-tuned per
call site, and text uses the five `TYPE_*` steps -- nothing below
`LEGIBILITY_FLOOR`, because scales near 0.018 were measured as unreadable.

Hard DirectGUI constraint
-------------------------
`relief=None` makes a `DirectFrame`/`DirectLabel` paint **nothing**: its
`frameColor` is ignored outright (verified by pixel-sampling an offscreen
render -- a `relief=None` frame samples identical to the bare background,
while `DGG.FLAT` paints). So any widget here that carries a *visible* fill
sets an explicit relief, and `_resolve_relief()` refuses a caller-supplied
`None` for those widgets. Only pure-text widgets (fully transparent
`frameColor`) are left relief-less, which is what keeps them glyph-only.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable, Iterable, Sequence

from direct.gui.DirectGui import DirectButton, DirectEntry, DirectFrame, DirectLabel
from direct.gui import DirectGuiGlobals as DGG
from panda3d.core import NodePath, PandaNode, TextNode, TransparencyAttrib

from viewer3d.fonts import mono_font, ui_font, ui_font_regular

Color = tuple[float, float, float, float]

# =========================================================================
# (a) Spacing scale -- one rhythm, in aspect2d units
# =========================================================================
SPACE_1 = 0.008
SPACE_2 = 0.016
SPACE_3 = 0.024
SPACE_4 = 0.036
SPACE_5 = 0.052
SPACE_6 = 0.072

#: Vertical pitch of one control/text row (label, entry, kv-row, button).
ROW_H = 0.052
#: Pitch for a dense numeric table (the generator "Advanced" grid).
ROW_H_TIGHT = 0.042
#: Inner padding between a panel's border and its content.
PANEL_PAD = SPACE_3
#: Thickness of the outer border ring drawn by `make_panel`.
BORDER_W = SPACE_1
#: Thickness of a horizontal rule.
RULE_H = 0.0026

SPACE_SCALE: tuple[float, ...] = (SPACE_1, SPACE_2, SPACE_3, SPACE_4, SPACE_5, SPACE_6)


def row_z(index: float, *, top: float = 0.0, row_h: float = ROW_H) -> float:
    """`z` of row `index` (0-based) counting down from `top`.

    Replaces per-call-site hand-tuned `z` values so panels share a rhythm.
    """
    return top - index * row_h


class Stack:
    """Top-down row cursor for laying out a panel's content.

    >>> s = Stack(-0.10, x=0.08)
    >>> s.row()            # z of the first row, cursor advances
    >>> s.gap(SPACE_2)     # insert whitespace
    >>> s.pos()            # (x, 0, z) tuple for the *next* row
    """

    def __init__(self, top: float, *, row_h: float = ROW_H, x: float = 0.0) -> None:
        self.top = float(top)
        self.row_h = float(row_h)
        self.x = float(x)
        self._z = float(top)

    @property
    def z(self) -> float:
        """`z` of the next row, without advancing."""
        return self._z

    def peek(self, *, rows: float = 1.0) -> float:
        return self._z - rows * self.row_h

    def row(self, *, rows: float = 1.0) -> float:
        """Return the current row's `z`, then advance by `rows` rows."""
        z = self._z
        self._z -= rows * self.row_h
        return z

    def pos(self, *, dx: float = 0.0, rows: float = 1.0) -> tuple[float, float, float]:
        """Return `(x, 0, z)` for the current row, then advance."""
        return (self.x + dx, 0.0, self.row(rows=rows))

    def gap(self, amount: float = SPACE_2) -> float:
        self._z -= amount
        return self._z

    def height(self) -> float:
        """Total vertical extent consumed so far (positive)."""
        return self.top - self._z


# =========================================================================
# (b) Type scale -- five steps, hard legibility floor
# =========================================================================
#: Nothing in the chrome may be smaller than this; 0.018 was illegible.
LEGIBILITY_FLOOR = 0.024

TYPE_TITLE = 0.046
TYPE_HEADER = 0.038
TYPE_BODY = 0.032
TYPE_LABEL = 0.028
TYPE_MICRO = 0.024

TYPE_SCALE: tuple[float, ...] = (
    TYPE_TITLE,
    TYPE_HEADER,
    TYPE_BODY,
    TYPE_LABEL,
    TYPE_MICRO,
)
#: Smallest legal type token.
TYPE_MIN = TYPE_MICRO


def clamp_scale(scale: float) -> float:
    """Raise anything below the legibility floor. Callers may pass raw sizes."""
    return max(float(scale), LEGIBILITY_FLOOR)


# =========================================================================
# (c) Palette -- tuned dark instrumentation, semantic roles on top
# =========================================================================
# Surfaces, darkest to lightest. Panels are nearly opaque so text over the
# 3D scene stays legible (a panel fill must keep alpha > 0.5).
SURFACE_DEEP: Color = (0.016, 0.034, 0.044, 0.94)
SURFACE_PANEL: Color = (0.052, 0.094, 0.115, 0.93)
SURFACE_NESTED: Color = (0.086, 0.140, 0.166, 0.94)
SURFACE_RAISED: Color = (0.118, 0.186, 0.216, 0.96)
#: Faint zebra wash for alternating rows in a table.
SURFACE_ROW: Color = (0.145, 0.220, 0.255, 0.30)

# Borders, brightest first. The bright edge is what makes a panel read as a
# panel against the wireframe ground.
BORDER_BRIGHT: Color = (0.52, 0.86, 0.92, 0.98)
BORDER_STRONG: Color = (0.38, 0.70, 0.78, 0.96)
BORDER_SOFT: Color = (0.24, 0.44, 0.50, 0.88)

# Text levels.
TEXT_STRONG: Color = (0.95, 0.98, 1.00, 1.0)
TEXT_BODY: Color = (0.86, 0.94, 0.96, 1.0)
TEXT_SECONDARY: Color = (0.66, 0.78, 0.82, 1.0)
TEXT_DISABLED: Color = (0.40, 0.48, 0.52, 1.0)

ACCENT: Color = (0.48, 0.88, 0.95, 1.0)
ACCENT_DIM: Color = (0.32, 0.60, 0.66, 1.0)

# --- required legacy names (values retuned, names/semantics preserved) ---
BG_PANEL: Color = SURFACE_PANEL
BG_NESTED: Color = SURFACE_NESTED
BG_DEEP: Color = SURFACE_DEEP
BORDER: Color = BORDER_STRONG
BORDER_DIM: Color = BORDER_SOFT

CYAN: Color = ACCENT
CYAN_DIM: Color = ACCENT_DIM
TEXT: Color = TEXT_BODY
TEXT_MUTED: Color = TEXT_SECONDARY
WHITE: Color = TEXT_STRONG

# Buttons. Resting fill is deliberately well above the panel surface so a
# button reads as a pressable plate rather than floating text; hover brightens
# hard, press sinks below the panel, disabled goes flat and desaturated.
BTN: Color = (0.132, 0.258, 0.300, 1.0)
BTN_HOVER: Color = (0.210, 0.410, 0.470, 1.0)
BTN_PRESS: Color = (0.062, 0.140, 0.172, 1.0)
BTN_DISABLED: Color = (0.105, 0.128, 0.140, 1.0)

BTN_PRIMARY: Color = (0.160, 0.470, 0.540, 1.0)
BTN_PRIMARY_HOVER: Color = (0.230, 0.610, 0.690, 1.0)
BTN_PRIMARY_PRESS: Color = (0.096, 0.320, 0.380, 1.0)

BTN_DANGER: Color = (0.400, 0.132, 0.126, 1.0)
BTN_DANGER_HOVER: Color = (0.560, 0.196, 0.180, 1.0)
BTN_DANGER_PRESS: Color = (0.280, 0.088, 0.084, 1.0)

BTN_GHOST: Color = (0.070, 0.128, 0.152, 0.86)
BTN_GHOST_HOVER: Color = (0.150, 0.280, 0.320, 0.98)

#: Latched / currently-selected toggle.
BTN_TOGGLE_ON: Color = (0.250, 0.560, 0.620, 1.0)
BTN_TOGGLE_ON_HOVER: Color = (0.320, 0.660, 0.720, 1.0)
BTN_TOGGLE_ON_PRESS: Color = (0.170, 0.420, 0.470, 1.0)

# Entries stay near-black so typed values pop and read as input wells.
ENTRY_BG: Color = (0.034, 0.064, 0.082, 1.0)
ENTRY_FOCUS: Color = (0.070, 0.135, 0.165, 1.0)
ENTRY_BORDER: Color = (0.340, 0.600, 0.660, 0.96)

GREEN: Color = (0.38, 0.86, 0.52, 1.0)
RED: Color = (0.92, 0.36, 0.32, 1.0)
AMBER: Color = (0.94, 0.74, 0.30, 1.0)

# Semantic status roles (what the tactical picture means, not what it looks
# like) -- use these in new code.
STATUS_FRIENDLY: Color = GREEN
STATUS_HOSTILE: Color = RED
STATUS_WARNING: Color = AMBER
STATUS_NEUTRAL: Color = ACCENT
STATUS_INACTIVE: Color = TEXT_DISABLED

# Back-compat aliases used by older call sites
PANEL_BG: Color = BG_PANEL
BTN_BG: Color = BTN

_TONES: dict[str, Color] = {
    "neutral": STATUS_NEUTRAL,
    "friendly": STATUS_FRIENDLY,
    "hostile": STATUS_HOSTILE,
    "warning": STATUS_WARNING,
    "inactive": STATUS_INACTIVE,
    "info": ACCENT_DIM,
}


def tone_color(tone: str) -> Color:
    """Foreground color for a semantic status tone (see `_TONES`)."""
    return _TONES.get(tone, STATUS_NEUTRAL)


def tint(color: Sequence[float], factor: float, *, alpha: float | None = None) -> Color:
    """Scale a color's RGB toward black (factor < 1) keeping/overriding alpha."""
    a = color[3] if alpha is None else alpha
    return (color[0] * factor, color[1] * factor, color[2] * factor, a)


# =========================================================================
# internals
# =========================================================================
_UNSET: Any = object()


def _resolve_relief(relief: Any, default: Any) -> Any:
    """Relief for a widget that must actually paint.

    `relief=None` suppresses all frame drawing in DirectGUI, so a `None`
    (explicit or defaulted) collapses to the widget's style default. This is
    the bug class that made the HUD panels invisible.
    """
    if relief is _UNSET or relief is None:
        return default
    return relief


def _maybe_font(kwargs: dict[str, Any], key: str, font: Any) -> None:
    """Set a font option only when we actually have a font.

    DirectGUI/`OnscreenText` treat a missing option as "built-in face"; some
    components choke on an explicit `None`, so we simply omit the key.
    """
    if font is not None:
        kwargs[key] = font


def _alpha_transparency(widget: Any, color: Sequence[float] | None) -> None:
    if color is not None and len(color) > 3 and color[3] < 1.0:
        widget.set_transparency(TransparencyAttrib.MAlpha)


def _as3(pos: Sequence[float]) -> tuple[float, float, float]:
    if len(pos) == 2:
        return (float(pos[0]), 0.0, float(pos[1]))
    return (float(pos[0]), float(pos[1]), float(pos[2]))


def _clip_widget_to_frame(widget: Any) -> None:
    """Scissor a DirectGUI widget to its painted well.

    DirectEntry leaves `frameSize` as None and keeps the well on
    `guiItem.get_frame()` (character units of the entry). A bundled mono
    face can paint glyphs wider than that box, so a long Path (or a numeric
    Advanced field) spilled into the next column. Scissoring is visual only:
    `get()` / `enterText()` still carry the full string.
    """
    fs = None
    try:
        raw = widget["frameSize"]
        if raw is not None:
            fs = (float(raw[0]), float(raw[1]), float(raw[2]), float(raw[3]))
    except Exception:  # noqa: BLE001 - some widgets expose no frameSize
        fs = None
    if fs is None:
        gui = getattr(widget, "guiItem", None)
        frame = getattr(gui, "get_frame", None)
        if callable(frame):
            try:
                box = frame()
                fs = (float(box[0]), float(box[1]), float(box[2]), float(box[3]))
            except Exception:  # noqa: BLE001 - PGItem without a frame
                fs = None
    if fs is None:
        return
    left, right, bottom, top = fs
    if right <= left or top <= bottom:
        return
    try:
        widget.setScissor(left, right, bottom, top)
    except Exception:  # noqa: BLE001 - older Panda3D, or a non-clippable node
        return


def _chars_that_fit(span: float, scale: float, *, pad: float = 0.45) -> float:
    """`make_entry(width=)` that keeps the plate inside `span` aspect2d units."""
    return max(float(span) / clamp_scale(scale) - pad, 1.0)


def _container(parent, name: str, pos: Sequence[float]) -> NodePath:
    """Plain scene-graph node used to group a composite primitive.

    Not a DirectFrame: a grouping node needs no fill, and an invisible
    DirectFrame would be exactly the `relief=None` trap this module warns
    about.
    """
    node = parent.attach_new_node(PandaNode(name))
    node.set_pos(*_as3(pos))
    return node


def _button_colors(style: str) -> tuple[Color, Color, Color, Color]:
    """DirectGUI's 4-state `frameColor`: (ready, press, rollover, disabled)."""
    if style == "primary":
        return (BTN_PRIMARY, BTN_PRIMARY_PRESS, BTN_PRIMARY_HOVER, BTN_DISABLED)
    if style == "danger":
        return (BTN_DANGER, BTN_DANGER_PRESS, BTN_DANGER_HOVER, BTN_DISABLED)
    if style == "ghost":
        return (BTN_GHOST, BTN_PRESS, BTN_GHOST_HOVER, BTN_DISABLED)
    if style in ("toggle", "selected", "active"):
        return (
            BTN_TOGGLE_ON,
            BTN_TOGGLE_ON_PRESS,
            BTN_TOGGLE_ON_HOVER,
            BTN_DISABLED,
        )
    return (BTN, BTN_PRESS, BTN_HOVER, BTN_DISABLED)


def _button_text_color(style: str) -> Color:
    """Label color for a style.

    One color for all four states: DirectGUI creates the per-state text
    components from a single `text_fg` group option, and a per-state tuple
    raises inside `OnscreenText`. `set_button_enabled()` is how the disabled
    label goes grey.
    """
    return TEXT_SECONDARY if style == "ghost" else TEXT_STRONG


BUTTON_STYLES: tuple[str, ...] = (
    "default",
    "primary",
    "danger",
    "ghost",
    "toggle",
)


# =========================================================================
# (f) panels
# =========================================================================
def make_panel(
    parent,
    *,
    frame_size: Sequence[float],
    pos: tuple[float, float, float] = (0, 0, 0),
    nested: bool = False,
    border: float = BORDER_W,
    inset: float = 0.0,
    fill_color: Sequence[float] | None = None,
    border_color: Sequence[float] | None = None,
    title: str | None = None,
    title_scale: float | None = None,
    title_fg: Sequence[float] = CYAN,
    pad: float = PANEL_PAD,
    relief: Any = _UNSET,
) -> DirectFrame:
    """Glass panel: bright border ring + inset fill, optional bracket title.

    The outer frame *is* the border (an inset fill is drawn on top of it),
    which is why editor panels are visible at all -- DirectGUI's own
    `borderWidth` does not draw a colored outline for `DGG.FLAT`.

    `inset` shrinks the whole panel inside `frame_size`; pass `SPACE_1` (or
    use `nest_frame_size()`) for a nested panel so its border cannot land on
    top of its parent's border. Attributes stashed on the result:
    `_chrome_fill` (the fill frame), `_chrome_title`, `_chrome_rule`,
    `_chrome_content_top` (z where content should start),
    `_chrome_frame_size` (the effective, post-inset rect).
    """
    left, right, bottom, top = (float(v) for v in frame_size)
    left += inset
    right -= inset
    bottom += inset
    top -= inset

    # Keep the fill non-degenerate no matter how thin the caller's rect is.
    half_w = max((right - left) / 2.0 - 1e-4, 0.0)
    half_h = max((top - bottom) / 2.0 - 1e-4, 0.0)
    b = max(min(float(border), half_w, half_h), 0.0)

    bg = tuple(fill_color) if fill_color is not None else (
        SURFACE_NESTED if nested else SURFACE_PANEL
    )
    edge = tuple(border_color) if border_color is not None else (
        BORDER_SOFT if nested else BORDER_STRONG
    )
    shell_relief = _resolve_relief(relief, DGG.FLAT)

    shell = DirectFrame(
        parent=parent,
        frameColor=edge,
        frameSize=(left, right, bottom, top),
        pos=pos,
        relief=shell_relief,
        borderWidth=(0, 0),
        state=DGG.DISABLED,
    )
    _alpha_transparency(shell, edge)
    fill = DirectFrame(
        parent=shell,
        frameColor=bg,
        frameSize=(left + b, right - b, bottom + b, top - b),
        pos=(0, 0, 0),
        relief=DGG.FLAT,
        borderWidth=(0, 0),
        state=DGG.DISABLED,
    )
    _alpha_transparency(fill, bg)
    shell._chrome_fill = fill  # noqa: SLF001 — stash for callers that need the fill node
    shell._chrome_frame_size = (left, right, bottom, top)  # noqa: SLF001
    shell._chrome_title = None  # noqa: SLF001
    shell._chrome_rule = None  # noqa: SLF001

    content_top = top - b - pad
    if title:
        scale = clamp_scale(title_scale if title_scale is not None else TYPE_HEADER)
        title_z = top - b - pad - scale * 0.82
        label = make_header(
            shell,
            title,
            pos=(left + b + pad, 0, title_z),
            scale=scale,
            fg=title_fg,
        )
        rule_z = title_z - scale * 0.55
        rule = make_rule(
            shell,
            pos=(left + b + pad, 0, rule_z),
            width=(right - b - pad) - (left + b + pad),
            color=BORDER_SOFT if nested else BORDER_STRONG,
        )
        shell._chrome_title = label  # noqa: SLF001
        shell._chrome_rule = rule  # noqa: SLF001
        content_top = rule_z - SPACE_3
    shell._chrome_content_top = content_top  # noqa: SLF001
    return shell


def nest_frame_size(
    frame_size: Sequence[float],
    *,
    pad: float = SPACE_2,
    top: float | None = None,
    bottom: float | None = None,
) -> tuple[float, float, float, float]:
    """A child rect inset from `frame_size`, so borders never collide.

    `top`/`bottom` override those edges outright (absolute z), for a nested
    panel that only occupies part of its parent's height.
    """
    left, right, low, high = (float(v) for v in frame_size)
    return (
        left + pad,
        right - pad,
        low + pad if bottom is None else float(bottom),
        high - pad if top is None else float(top),
    )


def panel_content_top(panel: DirectFrame) -> float:
    """`z` at which content should start inside a `make_panel` result."""
    return float(getattr(panel, "_chrome_content_top", 0.0))


# =========================================================================
# text
# =========================================================================
def make_header(
    parent,
    text: str,
    *,
    pos: tuple[float, float, float],
    scale: float = TYPE_HEADER,
    align=TextNode.ALeft,
    fg: Sequence[float] = CYAN,
    font: Any = _UNSET,
) -> DirectLabel:
    """`[ BRACKETED ]` section title. Glyph-only (no frame), so no relief."""
    kwargs: dict[str, Any] = dict(
        parent=parent,
        text=text,
        pos=pos,
        scale=clamp_scale(scale),
        text_fg=tuple(fg),
        text_align=align,
        frameColor=(0, 0, 0, 0),
        relief=None,
    )
    _maybe_font(kwargs, "text_font", ui_font() if font is _UNSET else font)
    return DirectLabel(**kwargs)


def make_label(
    parent,
    text: str,
    *,
    pos: tuple[float, float, float],
    scale: float = TYPE_LABEL,
    fg=TEXT_MUTED,
    align=TextNode.ALeft,
    mono: bool = False,
    font: Any = _UNSET,
    wordwrap: float | None = None,
) -> DirectLabel:
    """Body/label text. Glyph-only (no frame), so no relief."""
    kwargs: dict[str, Any] = dict(
        parent=parent,
        text=text,
        pos=pos,
        scale=clamp_scale(scale),
        text_fg=tuple(fg),
        text_align=align,
        frameColor=(0, 0, 0, 0),
        relief=None,
    )
    if font is _UNSET:
        chosen = mono_font() if mono else ui_font_regular()
    else:
        chosen = font
    _maybe_font(kwargs, "text_font", chosen)
    if wordwrap is not None:
        kwargs["text_wordwrap"] = wordwrap
    return DirectLabel(**kwargs)


def make_title(
    parent,
    text: str,
    *,
    pos: tuple[float, float, float],
    scale: float = TYPE_TITLE,
    align=TextNode.ALeft,
    fg: Sequence[float] = TEXT_STRONG,
) -> DirectLabel:
    """Top-level screen/mode title (one per panel at most)."""
    return make_header(parent, text, pos=pos, scale=scale, align=align, fg=fg)


# =========================================================================
# (d) buttons
# =========================================================================
def make_button(
    parent,
    text: str,
    pos: tuple[float, float, float] | tuple[float, float],
    command=None,
    *,
    extra_args: list[Any] | None = None,
    scale: float = TYPE_BODY,
    style: str = "default",
    pad: tuple[float, float] = (0.26, 0.16),
    frame_size: Sequence[float] | None = None,
    align=TextNode.ACenter,
    relief: Any = _UNSET,
    border_width: tuple[float, float] = (0.055, 0.055),
) -> DirectButton:
    """A button that reads as a button at rest.

    `DGG.RIDGE` + a lifted fill gives the plate a lit top edge and a shaded
    bottom edge (DirectGUI's `borderWidth` only draws an outline for the
    bevel reliefs -- `DGG.FLAT` would be a flat patch with no edge, and
    `relief=None` would paint nothing at all).
    """
    if len(pos) == 2:
        pos = (pos[0], 0, pos[1])
    kwargs: dict[str, Any] = dict(
        parent=parent,
        text=text,
        pos=pos,
        scale=clamp_scale(scale),
        command=command,
        frameColor=_button_colors(style),
        text_fg=_button_text_color(style),
        text_align=align,
        relief=_resolve_relief(relief, DGG.FLAT if style == "ghost" else DGG.RIDGE),
        borderWidth=border_width,
        pad=pad,
        pressEffect=1,
    )
    _maybe_font(kwargs, "text_font", ui_font())
    if frame_size is not None:
        kwargs["frameSize"] = tuple(frame_size)
    if extra_args is not None:
        kwargs["extraArgs"] = extra_args
    btn = DirectButton(**kwargs)
    btn.set_transparency(TransparencyAttrib.MAlpha)
    return btn


def style_existing_button(button: DirectButton, *, style: str = "default") -> None:
    """Re-skin an already-constructed DirectButton (e.g. toggle latching)."""
    button["frameColor"] = _button_colors(style)
    button["text_fg"] = _button_text_color(style)
    button["relief"] = DGG.FLAT if style == "ghost" else DGG.RIDGE
    button["borderWidth"] = (0.055, 0.055)


def set_button_enabled(button: DirectButton, enabled: bool) -> None:
    """Toggle a button's state *and* its label color together.

    Needed because the disabled plate alone (`BTN_DISABLED`) cannot carry a
    dimmed label -- DirectGUI shares one `text_fg` across all four states.
    """
    button["state"] = DGG.NORMAL if enabled else DGG.DISABLED
    button["text_fg"] = TEXT_STRONG if enabled else TEXT_DISABLED


# =========================================================================
# entries
# =========================================================================
def make_entry(
    parent,
    *,
    pos: tuple[float, float, float] | tuple[float, float],
    width: float = 12,
    scale: float = TYPE_BODY,
    initial: str = "",
    command=None,
    mono: bool = True,
    overflow: bool = False,
) -> DirectEntry:
    """Dark input well. Monospace by default -- most fields are numeric.

    `overflow=True` lets a long value (a Path) scroll inside the well instead
    of painting past it. Either way the glyphs are scissored to `frameSize`.
    """
    if len(pos) == 2:
        pos = (pos[0], 0, pos[1])
    kwargs: dict[str, Any] = dict(
        parent=parent,
        pos=pos,
        scale=clamp_scale(scale),
        width=width,
        initialText=initial,
        numLines=1,
        focus=0,
        overflow=1 if overflow else 0,
        frameColor=(ENTRY_BG, ENTRY_FOCUS, ENTRY_BG, ENTRY_BG),
        text_fg=TEXT_STRONG,
        relief=DGG.SUNKEN,
        borderWidth=(0.045, 0.045),
        pad=(0.12, 0.10),
    )
    _maybe_font(kwargs, "entryFont", mono_font() if mono else ui_font_regular())
    entry = DirectEntry(**kwargs)
    if command is not None:
        entry["command"] = command
    _clip_widget_to_frame(entry)
    return entry


def style_existing_entry(entry: DirectEntry) -> None:
    """Apply chrome colors to an already-constructed DirectEntry."""
    entry["frameColor"] = (ENTRY_BG, ENTRY_FOCUS, ENTRY_BG, ENTRY_BG)
    entry["text_fg"] = TEXT_STRONG
    entry["relief"] = DGG.SUNKEN
    entry["borderWidth"] = (0.045, 0.045)


# =========================================================================
# (e) new primitives
# =========================================================================
def make_rule(
    parent,
    *,
    pos: tuple[float, float, float] | tuple[float, float],
    width: float,
    thickness: float = RULE_H,
    color: Sequence[float] = BORDER_SOFT,
    anchor: str = "left",
) -> DirectFrame:
    """Thin horizontal divider. `anchor` is where `pos` sits on its length."""
    pos3 = _as3(pos)
    w = abs(float(width))
    if anchor == "center":
        left, right = -w / 2.0, w / 2.0
    elif anchor == "right":
        left, right = -w, 0.0
    else:
        left, right = 0.0, w
    t = max(float(thickness), 1e-4)
    rule = DirectFrame(
        parent=parent,
        frameColor=tuple(color),
        frameSize=(left, right, -t / 2.0, t / 2.0),
        pos=pos3,
        relief=DGG.FLAT,
        borderWidth=(0, 0),
        state=DGG.DISABLED,
    )
    _alpha_transparency(rule, color)
    return rule


def make_separator(
    parent,
    *,
    pos: tuple[float, float, float] | tuple[float, float],
    width: float,
    thickness: float = RULE_H,
    color: Sequence[float] = BORDER_SOFT,
    anchor: str = "left",
) -> DirectFrame:
    """Alias of `make_rule` for call sites that read better as "separator"."""
    return make_rule(
        parent,
        pos=pos,
        width=width,
        thickness=thickness,
        color=color,
        anchor=anchor,
    )


def make_badge(
    parent,
    text: str,
    *,
    pos: tuple[float, float, float] | tuple[float, float],
    tone: str = "neutral",
    scale: float = TYPE_MICRO,
    align=TextNode.ALeft,
    pad: tuple[float, float] = (0.34, 0.22),
    fg: Sequence[float] | None = None,
    fill: Sequence[float] | None = None,
) -> DirectLabel:
    """Small status pill (TRAINING / CONTESTED / LIVE / INVALID ...).

    Filled, so it needs a real relief; `DGG.FLAT` keeps it a crisp chip.
    """
    accent = tone_color(tone)
    text_fg = tuple(fg) if fg is not None else accent
    frame = tuple(fill) if fill is not None else tint(accent, 0.22, alpha=0.92)
    kwargs: dict[str, Any] = dict(
        parent=parent,
        text=text,
        pos=_as3(pos),
        scale=clamp_scale(scale),
        text_fg=text_fg,
        text_align=align,
        frameColor=frame,
        relief=DGG.FLAT,
        borderWidth=(0, 0),
        pad=pad,
        state=DGG.DISABLED,
    )
    _maybe_font(kwargs, "text_font", ui_font())
    badge = DirectLabel(**kwargs)
    badge.set_transparency(TransparencyAttrib.MAlpha)
    badge._chrome_tone = tone  # noqa: SLF001
    return badge


def make_status_chip(
    parent,
    text: str,
    *,
    pos: tuple[float, float, float] | tuple[float, float],
    tone: str = "neutral",
    scale: float = TYPE_MICRO,
    align=TextNode.ALeft,
    pad: tuple[float, float] = (0.34, 0.22),
    fg: Sequence[float] | None = None,
    fill: Sequence[float] | None = None,
) -> DirectLabel:
    """Alias of `make_badge`."""
    return make_badge(
        parent,
        text,
        pos=pos,
        tone=tone,
        scale=scale,
        align=align,
        pad=pad,
        fg=fg,
        fill=fill,
    )


def set_badge(badge: DirectLabel, text: str | None = None, *, tone: str | None = None) -> None:
    """Update a badge's text and/or tone in place."""
    if text is not None:
        badge["text"] = text
    if tone is not None:
        accent = tone_color(tone)
        badge["text_fg"] = accent
        badge["frameColor"] = tint(accent, 0.22, alpha=0.92)
        badge._chrome_tone = tone  # noqa: SLF001


@dataclass
class KVRow:
    """A label/value pair sharing one baseline, value column-aligned."""

    node: NodePath
    key: DirectLabel
    value: DirectLabel
    background: DirectFrame | None = None
    width: float = 0.0

    def set_value(self, text: str, *, fg: Sequence[float] | None = None) -> None:
        self.value["text"] = text
        if fg is not None:
            self.value["text_fg"] = tuple(fg)

    def set_key(self, text: str) -> None:
        self.key["text"] = text

    def show(self) -> None:
        self.node.show()

    def hide(self) -> None:
        self.node.hide()

    def destroy(self) -> None:
        for widget in (self.key, self.value, self.background):
            if widget is not None:
                widget.destroy()
        if not self.node.is_empty():
            self.node.remove_node()


#: Default content width of a kv-row / field grid inside a standard panel.
CONTENT_W = 0.72


def make_kv_row(
    parent,
    label: str,
    value: str = "",
    *,
    pos: tuple[float, float, float] | tuple[float, float],
    width: float = CONTENT_W,
    scale: float = TYPE_LABEL,
    key_fg: Sequence[float] = CYAN_DIM,
    value_fg: Sequence[float] = TEXT_BODY,
    value_scale: float | None = None,
    value_align=TextNode.ARight,
    mono_value: bool = True,
    background: bool = False,
) -> KVRow:
    """Label on the left, value right-aligned (or mono) at `width`.

    The one primitive the editor most needs: every hand-placed label/value
    pair there currently drifts out of alignment because the two halves are
    positioned independently.
    """
    node = _container(parent, "chrome-kv-row", pos)
    bg: DirectFrame | None = None
    if background:
        # Filled, so it must carry a relief (see module docstring).
        bg = DirectFrame(
            parent=node,
            frameColor=SURFACE_ROW,
            frameSize=(-SPACE_1, width + SPACE_1, -scale * 0.34, scale * 0.98),
            pos=(0, 0, 0),
            relief=DGG.FLAT,
            borderWidth=(0, 0),
            state=DGG.DISABLED,
        )
        bg.set_transparency(TransparencyAttrib.MAlpha)
    key = make_label(
        node,
        label,
        pos=(0, 0, 0),
        scale=scale,
        fg=key_fg,
        align=TextNode.ALeft,
    )
    value_x = width if value_align == TextNode.ARight else 0.0
    val = make_label(
        node,
        value,
        pos=(value_x, 0, 0),
        scale=clamp_scale(value_scale if value_scale is not None else scale),
        fg=value_fg,
        align=value_align,
        mono=mono_value,
    )
    return KVRow(node=node, key=key, value=val, background=bg, width=width)


@dataclass
class Section:
    """A `[ BRACKET ]` header paired with a rule underneath it."""

    label: DirectLabel
    rule: DirectFrame
    next_z: float

    def destroy(self) -> None:
        self.label.destroy()
        self.rule.destroy()


def make_section(
    parent,
    text: str,
    *,
    pos: tuple[float, float, float] | tuple[float, float],
    width: float = CONTENT_W,
    scale: float = TYPE_HEADER,
    fg: Sequence[float] = CYAN,
    color: Sequence[float] = BORDER_SOFT,
    gap: float = SPACE_2,
) -> Section:
    """Section header + divider. `next_z` is where the section's body starts."""
    x, _y, z = _as3(pos)
    scale = clamp_scale(scale)
    label = make_header(parent, text, pos=(x, 0, z), scale=scale, fg=fg)
    rule_z = z - scale * 0.55
    rule = make_rule(parent, pos=(x, 0, rule_z), width=width, color=color)
    return Section(label=label, rule=rule, next_z=rule_z - gap)


@dataclass
class FieldGrid:
    """A grid of label+entry pairs on a shared rhythm (numeric field tables)."""

    node: NodePath
    labels: dict[str, DirectLabel] = field(default_factory=dict)
    entries: dict[str, DirectEntry] = field(default_factory=dict)
    order: list[str] = field(default_factory=list)
    rows: int = 0
    columns: int = 1
    row_h: float = ROW_H_TIGHT
    height: float = 0.0
    bottom_z: float = 0.0

    def get(self, key: str) -> str:
        entry = self.entries.get(key)
        return entry.get() if entry is not None else ""

    def set_value(self, key: str, text: str) -> None:
        entry = self.entries.get(key)
        if entry is not None:
            entry.enterText(text)

    def values(self) -> dict[str, str]:
        return {key: self.entries[key].get() for key in self.order}

    def show(self) -> None:
        self.node.show()

    def hide(self) -> None:
        self.node.hide()

    def destroy(self) -> None:
        for widget in list(self.labels.values()) + list(self.entries.values()):
            widget.destroy()
        self.labels.clear()
        self.entries.clear()
        self.order.clear()
        if not self.node.is_empty():
            self.node.remove_node()


def make_field_grid(
    parent,
    fields: Iterable[str | tuple[str, str]],
    *,
    pos: tuple[float, float, float] | tuple[float, float],
    width: float = CONTENT_W,
    columns: int = 1,
    row_h: float = ROW_H_TIGHT,
    scale: float = TYPE_MICRO,
    label_fg: Sequence[float] = CYAN_DIM,
    label_ratio: float = 0.56,
    entry_chars: float = 8,
    entry_scale: float | None = None,
    command: Callable[..., Any] | None = None,
    initial: dict[str, str] | None = None,
    gap: float = 0.0,
) -> FieldGrid:
    """Lay out `fields` as label+entry rows, row-major across `columns`.

    Built for the generator "Advanced" panel's 16 numeric fields, which
    currently collide because each row picks its own `z`. `fields` items are
    either `"key"` or `("key", "Display label")`. `gap` is the gutter
    between columns; default 0 keeps the original packed grid.
    """
    node = _container(parent, "chrome-field-grid", pos)
    normalized: list[tuple[str, str]] = []
    for item in fields:
        if isinstance(item, str):
            normalized.append((item, item))
        else:
            normalized.append((str(item[0]), str(item[1])))

    columns = max(int(columns), 1)
    gutter = max(float(gap), 0.0)
    col_w = (width - (columns - 1) * gutter) / columns
    rows = (len(normalized) + columns - 1) // columns
    grid = FieldGrid(
        node=node,
        rows=rows,
        columns=columns,
        row_h=row_h,
        height=rows * row_h,
        bottom_z=_as3(pos)[2] - rows * row_h,
    )
    for index, (key, text) in enumerate(normalized):
        col = index % columns
        row = index // columns
        x = col * (col_w + gutter)
        z = row_z(row, row_h=row_h)
        grid.labels[key] = make_label(
            node,
            text,
            pos=(x, 0, z),
            scale=scale,
            fg=label_fg,
        )
        used_scale = clamp_scale(entry_scale if entry_scale is not None else scale)
        remain = col_w * max(1.0 - float(label_ratio), 0.18)
        chars = min(float(entry_chars), _chars_that_fit(remain, used_scale))
        entry = make_entry(
            node,
            pos=(x + col_w * label_ratio, 0, z),
            width=chars,
            scale=used_scale,
            initial=(initial or {}).get(key, ""),
            command=command,
        )
        grid.entries[key] = entry
        grid.order.append(key)
    return grid


def make_field_row(
    parent,
    label: str,
    *,
    pos: tuple[float, float, float] | tuple[float, float],
    width: float = CONTENT_W,
    scale: float = TYPE_LABEL,
    label_ratio: float = 0.56,
    entry_chars: float = 10,
    initial: str = "",
    command: Callable[..., Any] | None = None,
    label_fg: Sequence[float] = CYAN_DIM,
) -> tuple[DirectLabel, DirectEntry]:
    """One label+entry pair on a shared baseline (single-row `make_field_grid`)."""
    x, _y, z = _as3(pos)
    used_scale = clamp_scale(scale)
    remain = width * max(1.0 - float(label_ratio), 0.18)
    chars = min(float(entry_chars), _chars_that_fit(remain, used_scale))
    lbl = make_label(parent, label, pos=(x, 0, z), scale=scale, fg=label_fg)
    entry = make_entry(
        parent,
        pos=(x + width * label_ratio, 0, z),
        width=chars,
        scale=used_scale,
        initial=initial,
        command=command,
    )
    return lbl, entry
