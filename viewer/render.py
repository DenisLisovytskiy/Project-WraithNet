"""Coordinate mapping + all drawing primitives for the WraithNet replay viewer.

Pygame is imported lazily so coordinate-math tests can run without it
installed (acceptance criterion: pytest against requirements-dev.txt only).
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from wraithnet.config import JET_HP
from wraithnet.trace_schemas import TraceHeader, TraceTick
from viewer.hud_text import (
    jets_line,
    overlay_lines,
    sensor_line,
    status_line,
    targets_line,
    tick_line,
)
from viewer.playback import RunSummary, TickHighlights, compute_run_summary

if TYPE_CHECKING:
    from pygame import Surface

# --- Layout (do not hard-code these in app.py) ---
CANVAS_SIZE = 900
MARGIN = 40
HUD_WIDTH = 300
WINDOW_SIZE = (CANVAS_SIZE + HUD_WIDTH, CANVAS_SIZE)

# Chrome palette — 8-bit counterpart of viewer3d.ui_chrome (copied, not imported).
BACKGROUND_COLOR = (4, 9, 11)
GRID_LINE_COLOR = (18, 42, 50)
GRID_MAJOR_COLOR = (32, 68, 78)
BORDER_COLOR = (97, 178, 199)
HUD_BG_COLOR = (13, 24, 29)
HUD_NESTED_COLOR = (22, 36, 42)
HUD_TEXT_COLOR = (219, 240, 245)
HUD_MUTED_COLOR = (168, 199, 209)
HUD_ACCENT_COLOR = (122, 224, 242)
STATUS_FRIENDLY = (97, 219, 133)
STATUS_HOSTILE = (235, 92, 82)
STATUS_WARNING = (240, 189, 77)

JET_PALETTE = [(80, 200, 255), (255, 160, 60), (170, 255, 120)]
ROLE_RING_COLOR = {
    "IDLE": (140, 140, 140),
    "MANAGER": (230, 200, 60),
    "CONTRACTOR": (90, 220, 90),
    "CONTRACTOR_PENDING": (230, 140, 40),
    "RTB": (80, 160, 255),
}

TARGET_TYPE_STYLE = {
    "HARDENED_BUNKER": {"shape": "square", "color": (150, 100, 60)},
    "MOBILE_RADAR": {"shape": "diamond", "color": (70, 120, 220)},
    "SAM_SITE": {"shape": "octagon", "color": (210, 60, 60)},
}
DESTROYED_COLOR = (70, 70, 70)
JAMMER_COLOR = (170, 90, 220)
JAMMED_RING_COLOR = (220, 80, 220)
STRIKE_RANGE_UNUSED_HINT = None  # STRIKE_RANGE is ~1.0 world units; not drawn

JET_RADIUS = 8
TARGET_RADIUS = 11
JAMMER_RADIUS = 9
HP_BAR_WIDTH = 24
HP_BAR_HEIGHT = 4
FUEL_BAR_WIDTH = 24
FUEL_BAR_HEIGHT = 4
HOME_BASE_COLOR = (90, 200, 140)
HEADING_INDICATOR_COLOR = (230, 245, 250)
FUEL_BAR_COLOR = (70, 180, 230)
FUEL_BAR_EMPTY = (18, 38, 48)

STRIKE_LINE_COLOR = (255, 230, 80)
DESTRUCTION_PULSE_COLOR = (255, 255, 255)
JAM_FLASH_COLOR = (255, 90, 255)
UNJAM_FLASH_COLOR = (80, 220, 255)
OVERLAY_BG = (10, 22, 27, 250)

# Type scale in pixels (pygame TTF size). Floor stays readable on the 300px HUD.
TYPE_TITLE = 20
TYPE_HEADER = 14
TYPE_BODY = 15
TYPE_MONO = 15
TYPE_MICRO = 13
HUD_PAD = 12
HUD_ACCENT_W = 3
GRID_MINOR_STEP = 10.0
GRID_MAJOR_STEP = 50.0


def world_to_screen(
    pos: tuple[float, float],
    grid_w: float,
    grid_h: float,
    canvas_size: int,
    margin: int,
) -> tuple[int, int]:
    """Maps a world-space (x, y) in [0, grid_w] x [0, grid_h] to a screen-space
    pixel coordinate inside a canvas_size x canvas_size square, inset by
    margin pixels on every side. Flips y: world's +y is "up" (matching
    docs/04_..._v3_Phase2.md's position tuples, e.g. Target_A=(75,75) is
    drawn toward the top-right, not bottom-right) while Pygame's surface
    origin is top-left with +y "down"."""
    x, y = pos
    usable = canvas_size - 2 * margin
    sx = margin + (x / grid_w) * usable
    sy = margin + usable - (y / grid_h) * usable
    return int(sx), int(sy)


def world_length_to_screen(length: float, grid_w: float, canvas_size: int, margin: int) -> int:
    """Scales a world-space *distance* (e.g. SENSOR_RADIUS, JAM_RADIUS) into
    pixels, using the same scale factor world_to_screen uses for x. Grid is
    square (GRID_WIDTH == GRID_HEIGHT in this project) so one scale factor
    is correct for both axes; do not special-case a non-square grid here."""
    usable = canvas_size - 2 * margin
    return int(length / grid_w * usable)


def _require_pygame():
    try:
        import pygame
    except ImportError as exc:  # pragma: no cover
        raise RuntimeError(
            "pygame is required for rendering; pip install -r requirements-viz.txt"
        ) from exc
    return pygame


def _font(size: int = TYPE_BODY, *, face: str = "ui"):
    """Resolve a pygame Font. `face` is 'ui', 'regular', or 'mono'."""
    from viewer.fonts import mono_font, ui_font, ui_font_regular

    if face == "mono":
        return mono_font(size)
    if face == "regular":
        return ui_font_regular(size)
    return ui_font(size)


def overlay_card_origin(
    width: int, height: int, canvas_size: int = CANVAS_SIZE
) -> tuple[int, int]:
    """Top-left of a card that stays fully inside the square canvas."""
    width = min(max(width, 1), canvas_size)
    height = min(max(height, 1), canvas_size)
    ox = max(0, (canvas_size - width) // 2)
    oy = max(0, (canvas_size - height) // 2)
    return ox, oy


def _xy(pos: tuple[float, float], header: TraceHeader) -> tuple[int, int]:
    return world_to_screen(pos, header.grid_width, header.grid_height, CANVAS_SIZE, MARGIN)


def _scale(length: float, header: TraceHeader) -> int:
    return world_length_to_screen(length, header.grid_width, CANVAS_SIZE, MARGIN)


def heading_indicator_endpoints(
    position: tuple[float, float],
    heading_deg: float,
    header: TraceHeader,
    length_world: float = 3.0,
) -> tuple[tuple[int, int], tuple[int, int]]:
    """Screen-space (center, tip) of a heading tick. World heading 0° = +y;
    world_to_screen flips y, so the tip of a 0° heading is *above* the jet
    on the pygame surface."""
    rad = math.radians(heading_deg)
    tip = (
        position[0] + math.sin(rad) * length_world,
        position[1] + math.cos(rad) * length_world,
    )
    return _xy(position, header), _xy(tip, header)


def fuel_bar_fill_width(
    fuel: float, capacity: float, bar_width: int = FUEL_BAR_WIDTH
) -> int:
    if capacity <= 0.0:
        return 0
    frac = max(0.0, min(1.0, fuel / capacity))
    return max(0, int(bar_width * frac))


def _frame_stat(header: TraceHeader, agent_id: str, key: str, fallback: float) -> float:
    frame_type = (header.jet_frame_types or {}).get(agent_id, "STRIKE_FIGHTER")
    stats = (header.jet_frame_stats or {}).get(frame_type) or {}
    value = stats.get(key)
    return float(value) if value is not None else fallback


def _jet_color(agent_id: str, header: TraceHeader) -> tuple[int, int, int]:
    try:
        idx = header.jet_ids.index(agent_id)
    except ValueError:
        idx = 0
    return JET_PALETTE[idx % len(JET_PALETTE)]


def _polygon(center: tuple[int, int], radius: int, n: int, rotation: float) -> list[tuple[float, float]]:
    cx, cy = center
    return [
        (
            cx + radius * math.cos(rotation + i * 2 * math.pi / n),
            cy + radius * math.sin(rotation + i * 2 * math.pi / n),
        )
        for i in range(n)
    ]


def _draw_dashed_circle(surface, color, center: tuple[int, int], radius: int, width: int = 1) -> None:
    pygame = _require_pygame()
    if radius <= 0:
        return
    dash_deg, gap_deg = 10.0, 7.0
    cx, cy = center
    a = 0.0
    while a < 360.0:
        a0 = math.radians(a)
        a1 = math.radians(min(a + dash_deg, 360.0))
        p0 = (cx + radius * math.cos(a0), cy + radius * math.sin(a0))
        p1 = (cx + radius * math.cos(a1), cy + radius * math.sin(a1))
        pygame.draw.line(surface, color, p0, p1, width)
        a += dash_deg + gap_deg


def _draw_dotted_circle(surface, color, center: tuple[int, int], radius: int) -> None:
    pygame = _require_pygame()
    if radius <= 0:
        return
    cx, cy = center
    for deg in range(0, 360, 6):
        rad = math.radians(deg)
        p = (int(cx + radius * math.cos(rad)), int(cy + radius * math.sin(rad)))
        pygame.draw.circle(surface, color, p, 1)


def _blit_text(
    surface,
    text: str,
    xy: tuple[int, int],
    color,
    size: int = TYPE_BODY,
    *,
    face: str = "regular",
) -> int:
    """Draw text; return the rendered height so callers can stack lines."""
    font = _font(size, face=face)
    rendered = font.render(text, True, color)
    surface.blit(rendered, xy)
    return rendered.get_height()


def _text_size(text: str, size: int, *, face: str = "regular") -> tuple[int, int]:
    font = _font(size, face=face)
    return font.size(text)


def _section_header(surface, label: str, x: int, y: int, width: int) -> int:
    """`[ LABEL ]` plus a rule. Returns total block height including gap."""
    pygame = _require_pygame()
    h = _blit_text(
        surface, f"[ {label} ]", (x, y), HUD_ACCENT_COLOR, TYPE_HEADER, face="ui"
    )
    rule_y = y + h + 2
    pygame.draw.line(surface, BORDER_COLOR, (x, rule_y), (x + width, rule_y), 1)
    return h + 5


def _soft_disc(surface, color, center: tuple[int, int], radius: int, alpha: int) -> None:
    pygame = _require_pygame()
    if radius <= 0:
        return
    size = radius * 2 + 2
    disc = pygame.Surface((size, size), pygame.SRCALPHA)
    pygame.draw.circle(disc, (*color[:3], alpha), (radius + 1, radius + 1), radius)
    surface.blit(disc, (center[0] - radius - 1, center[1] - radius - 1))


def draw_grid_background(surface: Surface, header: TraceHeader) -> None:
    pygame = _require_pygame()
    surface.fill(BACKGROUND_COLOR)

    def _grid(step: float, color, width: int) -> None:
        x = 0.0
        while x <= header.grid_width + 1e-9:
            p0 = _xy((x, 0.0), header)
            p1 = _xy((x, header.grid_height), header)
            pygame.draw.line(surface, color, p0, p1, width)
            x += step
        y = 0.0
        while y <= header.grid_height + 1e-9:
            p0 = _xy((0.0, y), header)
            p1 = _xy((header.grid_width, y), header)
            pygame.draw.line(surface, color, p0, p1, width)
            y += step

    _grid(GRID_MINOR_STEP, GRID_LINE_COLOR, 1)
    _grid(GRID_MAJOR_STEP, GRID_MAJOR_COLOR, 1)
    top_left = _xy((0.0, header.grid_height), header)
    bottom_right = _xy((header.grid_width, 0.0), header)
    border = pygame.Rect(
        top_left[0],
        top_left[1],
        bottom_right[0] - top_left[0],
        bottom_right[1] - top_left[1],
    )
    pygame.draw.rect(surface, BORDER_COLOR, border, 2)


def draw_jammer(surface: Surface, header: TraceHeader) -> None:
    pygame = _require_pygame()
    jam_r = _scale(header.jam_radius, header)
    for pos in header.jammer_positions.values():
        center = _xy(pos, header)
        _soft_disc(surface, JAMMER_COLOR, center, jam_r, 36)
        _draw_dashed_circle(surface, JAMMER_COLOR, center, jam_r, width=2)
        hexagon = _polygon(center, JAMMER_RADIUS, 6, math.pi / 6)
        pygame.draw.polygon(surface, JAMMER_COLOR, hexagon)
        pygame.draw.polygon(surface, HUD_TEXT_COLOR, hexagon, 1)
        pygame.draw.circle(surface, BACKGROUND_COLOR, center, 3)


def draw_home_base(surface: Surface, header: TraceHeader) -> None:
    """Always-visible home-base marker (flag + pad), independent of tick."""
    pygame = _require_pygame()
    home = getattr(header, "home_base", None)
    if home is None:
        return
    center = _xy(tuple(home), header)
    pad = pygame.Rect(center[0] - 7, center[1] - 5, 14, 10)
    pygame.draw.rect(surface, HOME_BASE_COLOR, pad, 0)
    pygame.draw.rect(surface, HUD_ACCENT_COLOR, pad, 1)
    pygame.draw.line(
        surface, HUD_TEXT_COLOR, (center[0] - 2, center[1] + 5), (center[0] - 2, center[1] - 14), 2
    )
    flag = [
        (center[0] - 2, center[1] - 14),
        (center[0] + 10, center[1] - 10),
        (center[0] - 2, center[1] - 6),
    ]
    pygame.draw.polygon(surface, HOME_BASE_COLOR, flag)


def draw_targets(surface: Surface, header: TraceHeader, tick: TraceTick) -> None:
    pygame = _require_pygame()
    for frame in tick.targets:
        pos = header.target_positions[frame.target_id]
        center = _xy(pos, header)
        target_type = header.target_types[frame.target_id]
        style = TARGET_TYPE_STYLE.get(
            target_type, {"shape": "square", "color": (180, 180, 180)}
        )
        destroyed = frame.hp_remaining <= 0
        color = DESTROYED_COLOR if destroyed else style["color"]
        shape = style["shape"]
        r = TARGET_RADIUS
        if shape == "diamond":
            points = [
                (center[0], center[1] - r),
                (center[0] + r, center[1]),
                (center[0], center[1] + r),
                (center[0] - r, center[1]),
            ]
            pygame.draw.polygon(surface, color, points, 2 if destroyed else 0)
            if not destroyed:
                pygame.draw.polygon(surface, HUD_TEXT_COLOR, points, 1)
        elif shape == "octagon":
            points = _polygon(center, r, 8, math.pi / 8)
            pygame.draw.polygon(surface, color, points, 2 if destroyed else 0)
            if not destroyed:
                pygame.draw.polygon(surface, HUD_TEXT_COLOR, points, 1)
        else:
            rect = pygame.Rect(center[0] - r, center[1] - r, 2 * r, 2 * r)
            pygame.draw.rect(surface, color, rect, 2 if destroyed else 0)
            if not destroyed:
                pygame.draw.rect(surface, HUD_TEXT_COLOR, rect, 1)

        if destroyed:
            continue
        hp_required = header.target_hp_required[frame.target_id]
        label = f"{frame.hp_remaining}/{hp_required}"
        tw, th = _text_size(label, TYPE_MICRO, face="mono")
        _blit_text(
            surface,
            label,
            (center[0] - tw // 2, center[1] - r - th - 2),
            HUD_TEXT_COLOR,
            size=TYPE_MICRO,
            face="mono",
        )
        if target_type == "SAM_SITE":
            sam_r = _scale(header.sam_engagement_range, header)
            _soft_disc(surface, color, center, sam_r, 28)
            _draw_dashed_circle(
                surface,
                color,
                center,
                sam_r,
                width=2,
            )


def draw_jets(
    surface: Surface,
    header: TraceHeader,
    tick: TraceTick,
    show_sensor_radius: bool,
) -> None:
    pygame = _require_pygame()
    sensor_r = _scale(header.sensor_radius, header)
    for frame in tick.jets:
        center = _xy(frame.position, header)
        fill = _jet_color(frame.agent_id, header)
        pygame.draw.circle(surface, BACKGROUND_COLOR, center, JET_RADIUS + 1)
        pygame.draw.circle(surface, fill, center, JET_RADIUS)
        origin, tip = heading_indicator_endpoints(frame.position, frame.heading, header)
        pygame.draw.line(surface, HEADING_INDICATOR_COLOR, origin, tip, 2)
        pygame.draw.circle(surface, HEADING_INDICATOR_COLOR, tip, 2)
        if frame.role is not None:
            ring = ROLE_RING_COLOR.get(frame.role, ROLE_RING_COLOR["IDLE"])
            pygame.draw.circle(surface, ring, center, JET_RADIUS + 4, 2)
        if frame.jammed:
            pygame.draw.circle(surface, JAMMED_RING_COLOR, center, JET_RADIUS + 8, 2)
        # HP / fuel sit below the glyph so they do not collide with the heading tick.
        bar_x = center[0] - HP_BAR_WIDTH // 2
        bar_y = center[1] + JET_RADIUS + 6
        pygame.draw.rect(
            surface, HUD_NESTED_COLOR, pygame.Rect(bar_x, bar_y, HP_BAR_WIDTH, HP_BAR_HEIGHT)
        )
        hp_max = _frame_stat(header, frame.agent_id, "hp", JET_HP)
        frac = max(0.0, min(1.0, frame.hp / hp_max)) if hp_max else 0.0
        hp_color = (
            STATUS_FRIENDLY if frac > 0.5 else STATUS_WARNING if frac > 0.25 else STATUS_HOSTILE
        )
        pygame.draw.rect(
            surface,
            hp_color,
            pygame.Rect(bar_x, bar_y, max(0, int(HP_BAR_WIDTH * frac)), HP_BAR_HEIGHT),
        )
        fuel_y = bar_y + HP_BAR_HEIGHT + 2
        pygame.draw.rect(
            surface,
            FUEL_BAR_EMPTY,
            pygame.Rect(bar_x, fuel_y, FUEL_BAR_WIDTH, FUEL_BAR_HEIGHT),
        )
        capacity = _frame_stat(header, frame.agent_id, "fuel_capacity", 1.0)
        fill_w = fuel_bar_fill_width(frame.fuel, capacity)
        pygame.draw.rect(
            surface,
            FUEL_BAR_COLOR,
            pygame.Rect(bar_x, fuel_y, fill_w, FUEL_BAR_HEIGHT),
        )
        if show_sensor_radius:
            _draw_dotted_circle(surface, GRID_MAJOR_COLOR, center, sensor_r)


def draw_hud(
    surface: Surface,
    header: TraceHeader,
    tick: TraceTick,
    tick_index: int,
    total_ticks: int,
    show_sensor_radius: bool,
    playing: bool = False,
    ticks_per_second: float = 0.0,
) -> None:
    pygame = _require_pygame()
    hud_rect = pygame.Rect(CANVAS_SIZE, 0, HUD_WIDTH, CANVAS_SIZE)
    pygame.draw.rect(surface, HUD_BG_COLOR, hud_rect)
    pygame.draw.rect(
        surface,
        HUD_ACCENT_COLOR,
        pygame.Rect(CANVAS_SIZE, 0, HUD_ACCENT_W, CANVAS_SIZE),
    )
    pygame.draw.line(
        surface,
        BORDER_COLOR,
        (CANVAS_SIZE + HUD_WIDTH - 1, 0),
        (CANVAS_SIZE + HUD_WIDTH - 1, CANVAS_SIZE),
        1,
    )

    x = CANVAS_SIZE + HUD_PAD
    inner_w = HUD_WIDTH - HUD_PAD * 2
    y = HUD_PAD
    y += _blit_text(
        surface, "WraithNet Replay", (x, y), HUD_TEXT_COLOR, TYPE_TITLE, face="ui"
    ) + 4
    pygame.draw.line(surface, HUD_ACCENT_COLOR, (x, y), (x + inner_w, y), 1)
    y += 8

    y += _section_header(surface, "TICK", x, y, inner_w)
    y += _blit_text(
        surface,
        tick_line(tick, tick_index, total_ticks),
        (x, y),
        HUD_TEXT_COLOR,
        TYPE_MONO,
        face="mono",
    ) + 4
    status = status_line(playing, ticks_per_second)
    if status is not None:
        status_color = STATUS_FRIENDLY if playing else STATUS_WARNING
        y += _blit_text(
            surface,
            status,
            (x, y),
            status_color,
            TYPE_MONO,
            face="mono",
        ) + 4
    sensor = sensor_line(show_sensor_radius)
    sensor_color = HUD_ACCENT_COLOR if show_sensor_radius else HUD_MUTED_COLOR
    y += _blit_text(surface, sensor, (x, y), sensor_color, TYPE_BODY, face="regular") + 8

    y += _section_header(surface, "FORCE", x, y, inner_w)
    y += _blit_text(
        surface,
        targets_line(tick),
        (x, y),
        HUD_TEXT_COLOR,
        TYPE_MONO,
        face="mono",
    ) + 2
    y += _blit_text(
        surface,
        jets_line(header, tick),
        (x, y),
        HUD_TEXT_COLOR,
        TYPE_MONO,
        face="mono",
    ) + 8

    y += _section_header(surface, "JETS", x, y, inner_w)
    for agent_id in header.jet_ids:
        color = _jet_color(agent_id, header)
        pygame.draw.circle(surface, BACKGROUND_COLOR, (x + 8, y + 8), 7)
        pygame.draw.circle(surface, color, (x + 8, y + 8), 6)
        alive = any(j.agent_id == agent_id for j in tick.jets)
        label = agent_id if alive else f"{agent_id}  (lost)"
        label_color = HUD_TEXT_COLOR if alive else HUD_MUTED_COLOR
        y += _blit_text(surface, label, (x + 22, y), label_color, TYPE_BODY, face="regular") + 3
    y += 4

    y += _section_header(surface, "ROLES", x, y, inner_w)
    for role, color in ROLE_RING_COLOR.items():
        pygame.draw.circle(surface, color, (x + 8, y + 8), 7, 2)
        y += _blit_text(
            surface, role.replace("_", " "), (x + 22, y), HUD_TEXT_COLOR, TYPE_BODY, face="regular"
        ) + 1
    pygame.draw.circle(surface, JAMMED_RING_COLOR, (x + 8, y + 8), 8, 2)
    y += _blit_text(surface, "JAMMED", (x + 22, y), HUD_TEXT_COLOR, TYPE_BODY, face="regular") + 6

    y += _section_header(surface, "TARGETS", x, y, inner_w)
    pygame.draw.rect(
        surface,
        TARGET_TYPE_STYLE["HARDENED_BUNKER"]["color"],
        pygame.Rect(x + 2, y + 2, 12, 12),
    )
    pygame.draw.rect(surface, HUD_TEXT_COLOR, pygame.Rect(x + 2, y + 2, 12, 12), 1)
    y += _blit_text(surface, "Bunker (square)", (x + 22, y), HUD_TEXT_COLOR, TYPE_BODY, face="regular") + 1
    diamond = [(x + 8, y + 1), (x + 14, y + 8), (x + 8, y + 15), (x + 2, y + 8)]
    pygame.draw.polygon(surface, TARGET_TYPE_STYLE["MOBILE_RADAR"]["color"], diamond)
    pygame.draw.polygon(surface, HUD_TEXT_COLOR, diamond, 1)
    y += _blit_text(surface, "Radar (diamond)", (x + 22, y), HUD_TEXT_COLOR, TYPE_BODY, face="regular") + 1
    pygame.draw.polygon(
        surface,
        TARGET_TYPE_STYLE["SAM_SITE"]["color"],
        _polygon((x + 8, y + 8), 7, 8, math.pi / 8),
    )
    pygame.draw.polygon(
        surface,
        HUD_TEXT_COLOR,
        _polygon((x + 8, y + 8), 7, 8, math.pi / 8),
        1,
    )
    y += _blit_text(surface, "SAM (octagon)", (x + 22, y), HUD_TEXT_COLOR, TYPE_BODY, face="regular") + 1
    pygame.draw.circle(surface, JAMMER_COLOR, (x + 8, y + 8), 6)
    pygame.draw.circle(surface, HUD_TEXT_COLOR, (x + 8, y + 8), 6, 1)
    y += _blit_text(
        surface, "EW Jammer + jam radius", (x + 22, y), HUD_TEXT_COLOR, TYPE_BODY, face="regular"
    ) + 8

    y += _section_header(surface, "CONTROLS", x, y, inner_w)
    for line in (
        "Space   play / pause",
        "Left / Right   step tick",
        "+ / -   speed",
        "S   sensor rings",
        "R   restart",
        "Esc   quit",
    ):
        y += _blit_text(surface, line, (x, y), HUD_MUTED_COLOR, TYPE_MICRO, face="mono") + 1


def draw_strike_highlights(
    surface: Surface,
    header: TraceHeader,
    tick: TraceTick,
    highlights: TickHighlights,
    previous_tick: TraceTick | None = None,
) -> None:
    pygame = _require_pygame()
    jet_pos: dict[str, tuple[float, float]] = {}
    if previous_tick is not None:
        for jet in previous_tick.jets:
            jet_pos[jet.agent_id] = jet.position
    for jet in tick.jets:
        jet_pos[jet.agent_id] = jet.position
    for strike in highlights.strikes:
        if strike.agent_id not in jet_pos:
            continue
        if strike.target_id not in header.target_positions:
            continue
        p0 = _xy(jet_pos[strike.agent_id], header)
        p1 = _xy(header.target_positions[strike.target_id], header)
        pygame.draw.line(surface, (120, 100, 30), p0, p1, 4)
        pygame.draw.line(surface, STRIKE_LINE_COLOR, p0, p1, 2)
        pygame.draw.circle(surface, STRIKE_LINE_COLOR, p1, 5, 1)


def draw_destruction_pulses(
    surface: Surface,
    header: TraceHeader,
    current_tick: TraceTick,
    highlights: TickHighlights,
    previous_tick: TraceTick | None = None,
) -> None:
    pygame = _require_pygame()
    for tid in highlights.newly_destroyed_target_ids:
        pos = header.target_positions.get(tid)
        if pos is None:
            continue
        pygame.draw.circle(surface, DESTRUCTION_PULSE_COLOR, _xy(pos, header), TARGET_RADIUS + 10, 2)
    prev_jets = {j.agent_id: j.position for j in previous_tick.jets} if previous_tick else {}
    for aid in highlights.newly_destroyed_jet_ids:
        pos = prev_jets.get(aid)
        if pos is None:
            continue
        pygame.draw.circle(surface, DESTRUCTION_PULSE_COLOR, _xy(pos, header), JET_RADIUS + 12, 2)


def draw_jam_transition_flash(
    surface: Surface,
    header: TraceHeader,
    current_tick: TraceTick,
    highlights: TickHighlights,
) -> None:
    pygame = _require_pygame()
    by_id = {j.agent_id: j for j in current_tick.jets}
    for aid in highlights.newly_jammed_jet_ids:
        jet = by_id.get(aid)
        if jet is None:
            continue
        pygame.draw.circle(
            surface, JAM_FLASH_COLOR, _xy(jet.position, header), JET_RADIUS + 14, 3
        )
    for aid in highlights.newly_unjammed_jet_ids:
        jet = by_id.get(aid)
        if jet is None:
            continue
        pygame.draw.circle(
            surface, UNJAM_FLASH_COLOR, _xy(jet.position, header), JET_RADIUS + 14, 2
        )


def draw_end_of_run_overlay(
    surface: Surface,
    header: TraceHeader,
    ticks: list[TraceTick],
    summary: RunSummary | None = None,
) -> None:
    pygame = _require_pygame()
    if summary is None:
        summary = compute_run_summary(header, ticks)

    if summary.swarm_wiped_out:
        title_color = STATUS_HOSTILE
    elif getattr(summary, "mission_aborted", False):
        title_color = STATUS_WARNING
    elif summary.solved:
        title_color = STATUS_FRIENDLY
    else:
        title_color = HUD_TEXT_COLOR

    text_lines = overlay_lines(header, ticks, summary=summary)
    colored = [(text_lines[0], title_color, TYPE_TITLE, "ui")]
    for text in text_lines[1:]:
        colored.append((text, HUD_TEXT_COLOR, TYPE_MONO, "mono"))

    pad_x, pad_y = 22, 18
    rule_gap = 10
    line_gap = 6
    max_w = 0
    total_h = 0
    measured: list[tuple[str, tuple[int, int, int], int, str, int, int]] = []
    for i, (text, color, size, face) in enumerate(colored):
        tw, th = _text_size(text, size, face=face)
        measured.append((text, color, size, face, tw, th))
        max_w = max(max_w, tw)
        total_h += th
        if i == 0:
            total_h += rule_gap
        else:
            total_h += line_gap

    card_w = min(max_w + pad_x * 2, CANVAS_SIZE - 24)
    card_h = min(total_h + pad_y * 2, CANVAS_SIZE - 24)
    overlay = pygame.Surface((card_w, card_h), pygame.SRCALPHA)
    overlay.fill(OVERLAY_BG)
    pygame.draw.rect(overlay, HUD_ACCENT_COLOR, overlay.get_rect(), 2)
    pygame.draw.line(
        overlay,
        HUD_ACCENT_COLOR,
        (pad_x, 4),
        (card_w - pad_x, 4),
        1,
    )

    y = pad_y
    for i, (text, color, size, face, _tw, th) in enumerate(measured):
        rendered = _font(size, face=face).render(text, True, color)
        overlay.blit(rendered, (pad_x, y))
        y += th
        if i == 0:
            rule_y = y + 4
            pygame.draw.line(
                overlay, HUD_ACCENT_COLOR, (pad_x, rule_y), (card_w - pad_x, rule_y), 1
            )
            y += rule_gap
        else:
            y += line_gap

    ox, oy = overlay_card_origin(card_w, card_h)
    surface.blit(overlay, (ox, oy))
