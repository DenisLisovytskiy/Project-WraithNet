"""Shared tactical UI chrome for viewer3d (editor + live HUD).

DirectGUI defaults look like unstyled form controls. This module supplies a
single muted-teal / glass-panel look so editor and HUD read as one product.
"""

from __future__ import annotations

from typing import Any, Sequence

from direct.gui.DirectGui import DirectButton, DirectEntry, DirectFrame, DirectLabel
from direct.gui import DirectGuiGlobals as DGG
from panda3d.core import TextNode, TransparencyAttrib

# --- palette (mockup: charcoal glass + desaturated teal) -----------------
BG_PANEL = (0.045, 0.08, 0.10, 0.86)
BG_NESTED = (0.03, 0.06, 0.08, 0.72)
BG_DEEP = (0.02, 0.045, 0.06, 0.92)
BORDER = (0.32, 0.52, 0.56, 0.90)
BORDER_DIM = (0.22, 0.38, 0.42, 0.70)

CYAN = (0.55, 0.82, 0.86, 1.0)
CYAN_DIM = (0.40, 0.62, 0.66, 1.0)
TEXT = (0.82, 0.92, 0.94, 1.0)
TEXT_MUTED = (0.52, 0.64, 0.68, 1.0)
WHITE = (0.92, 0.95, 0.98, 1.0)

BTN = (0.07, 0.15, 0.18, 0.96)
BTN_HOVER = (0.12, 0.28, 0.32, 1.0)
BTN_PRESS = (0.05, 0.22, 0.26, 1.0)
BTN_DISABLED = (0.05, 0.08, 0.10, 0.55)

BTN_PRIMARY = (0.10, 0.34, 0.38, 0.98)
BTN_PRIMARY_HOVER = (0.14, 0.42, 0.46, 1.0)
BTN_PRIMARY_PRESS = (0.08, 0.28, 0.32, 1.0)

BTN_DANGER = (0.28, 0.10, 0.10, 0.96)
BTN_DANGER_HOVER = (0.40, 0.14, 0.14, 1.0)
BTN_DANGER_PRESS = (0.22, 0.08, 0.08, 1.0)

ENTRY_BG = (0.04, 0.07, 0.09, 0.98)
ENTRY_FOCUS = (0.06, 0.12, 0.14, 1.0)
ENTRY_BORDER = (0.28, 0.46, 0.50, 0.85)

GREEN = (0.35, 0.82, 0.50, 1.0)
RED = (0.86, 0.35, 0.32, 1.0)
AMBER = (0.86, 0.70, 0.31, 1.0)

# Back-compat aliases used by older call sites
PANEL_BG = BG_PANEL
BTN_BG = BTN


def _button_colors(style: str) -> tuple:
    if style == "primary":
        return (BTN_PRIMARY, BTN_PRIMARY_PRESS, BTN_PRIMARY_HOVER, BTN_DISABLED)
    if style == "danger":
        return (BTN_DANGER, BTN_DANGER_PRESS, BTN_DANGER_HOVER, BTN_DISABLED)
    if style == "ghost":
        ghost = (0.05, 0.10, 0.12, 0.55)
        return (ghost, BTN_PRESS, BTN_HOVER, BTN_DISABLED)
    return (BTN, BTN_PRESS, BTN_HOVER, BTN_DISABLED)


def make_panel(
    parent,
    *,
    frame_size: Sequence[float],
    pos: tuple[float, float, float] = (0, 0, 0),
    nested: bool = False,
    border: float = 0.008,
) -> DirectFrame:
    """Glass panel with a thin teal border (outer frame + inset fill)."""
    left, right, bottom, top = frame_size
    bg = BG_NESTED if nested else BG_PANEL
    shell = DirectFrame(
        parent=parent,
        frameColor=BORDER,
        frameSize=tuple(frame_size),
        pos=pos,
        relief=DGG.FLAT,
        borderWidth=(0, 0),
        state=DGG.DISABLED,
    )
    shell.set_transparency(TransparencyAttrib.MAlpha)
    fill = DirectFrame(
        parent=shell,
        frameColor=bg,
        frameSize=(left + border, right - border, bottom + border, top - border),
        pos=(0, 0, 0),
        relief=DGG.FLAT,
        borderWidth=(0, 0),
        state=DGG.DISABLED,
    )
    fill.set_transparency(TransparencyAttrib.MAlpha)
    shell._chrome_fill = fill  # noqa: SLF001 — stash for callers that need the fill node
    return shell


def make_header(
    parent,
    text: str,
    *,
    pos: tuple[float, float, float],
    scale: float = 0.036,
    align=TextNode.ALeft,
) -> DirectLabel:
    return DirectLabel(
        parent=parent,
        text=text,
        pos=pos,
        scale=scale,
        text_fg=CYAN,
        text_align=align,
        frameColor=(0, 0, 0, 0),
        relief=None,
    )


def make_label(
    parent,
    text: str,
    *,
    pos: tuple[float, float, float],
    scale: float = 0.030,
    fg=TEXT_MUTED,
    align=TextNode.ALeft,
) -> DirectLabel:
    return DirectLabel(
        parent=parent,
        text=text,
        pos=pos,
        scale=scale,
        text_fg=fg,
        text_align=align,
        frameColor=(0, 0, 0, 0),
        relief=None,
    )


def make_button(
    parent,
    text: str,
    pos: tuple[float, float, float] | tuple[float, float],
    command=None,
    *,
    extra_args: list[Any] | None = None,
    scale: float = 0.034,
    style: str = "default",
    pad: tuple[float, float] = (0.20, 0.12),
) -> DirectButton:
    if len(pos) == 2:
        pos = (pos[0], 0, pos[1])
    kwargs: dict[str, Any] = dict(
        parent=parent,
        text=text,
        pos=pos,
        scale=scale,
        command=command,
        frameColor=_button_colors(style),
        text_fg=TEXT,
        relief=DGG.RIDGE,
        borderWidth=(0.018, 0.018),
        pad=pad,
        pressEffect=1,
    )
    if extra_args is not None:
        kwargs["extraArgs"] = extra_args
    return DirectButton(**kwargs)


def make_entry(
    parent,
    *,
    pos: tuple[float, float, float] | tuple[float, float],
    width: float = 12,
    scale: float = 0.032,
    initial: str = "",
    command=None,
) -> DirectEntry:
    if len(pos) == 2:
        pos = (pos[0], 0, pos[1])
    entry = DirectEntry(
        parent=parent,
        pos=pos,
        scale=scale,
        width=width,
        initialText=initial,
        numLines=1,
        focus=0,
        frameColor=(ENTRY_BG, ENTRY_FOCUS, ENTRY_BG, ENTRY_BG),
        text_fg=TEXT,
        relief=DGG.FLAT,
        borderWidth=(0.015, 0.015),
        pad=(0.08, 0.06),
    )
    if command is not None:
        entry["command"] = command
    return entry


def style_existing_entry(entry: DirectEntry) -> None:
    """Apply chrome colors to an already-constructed DirectEntry."""
    entry["frameColor"] = (ENTRY_BG, ENTRY_FOCUS, ENTRY_BG, ENTRY_BG)
    entry["text_fg"] = TEXT
    entry["relief"] = DGG.FLAT
    entry["borderWidth"] = (0.015, 0.015)
