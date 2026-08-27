"""Coordinate mapping + all drawing primitives for the WraithNet replay viewer.

Pygame is imported lazily so coordinate-math tests can run without it
installed (acceptance criterion: pytest against requirements-dev.txt only).
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING

from wraithnet.config import JET_HP
from wraithnet.trace_schemas import TraceHeader, TraceTick
from viewer.playback import RunSummary, TickHighlights, compute_run_summary

if TYPE_CHECKING:
    from pygame import Surface

# --- Layout (do not hard-code these in app.py) ---
CANVAS_SIZE = 900
MARGIN = 40
HUD_WIDTH = 300
WINDOW_SIZE = (CANVAS_SIZE + HUD_WIDTH, CANVAS_SIZE)

BACKGROUND_COLOR = (18, 18, 24)
GRID_LINE_COLOR = (40, 40, 50)
BORDER_COLOR = (90, 90, 110)
HUD_BG_COLOR = (22, 22, 30)
HUD_TEXT_COLOR = (210, 210, 220)
HUD_MUTED_COLOR = (130, 130, 145)

JET_PALETTE = [(80, 200, 255), (255, 160, 60), (170, 255, 120)]
ROLE_RING_COLOR = {
    "IDLE": (140, 140, 140),
    "MANAGER": (230, 200, 60),
    "CONTRACTOR": (90, 220, 90),
    "CONTRACTOR_PENDING": (230, 140, 40),
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
HP_BAR_WIDTH = 22
HP_BAR_HEIGHT = 3

STRIKE_LINE_COLOR = (255, 230, 80)
DESTRUCTION_PULSE_COLOR = (255, 255, 255)
JAM_FLASH_COLOR = (255, 90, 255)
UNJAM_FLASH_COLOR = (80, 220, 255)
OVERLAY_BG = (12, 12, 18, 210)


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


_font_cache: dict[int, object] = {}


def _font(size: int = 22):
    pygame = _require_pygame()
    if not pygame.font.get_init():
        pygame.font.init()
    cached = _font_cache.get(size)
    if cached is None:
        cached = pygame.font.Font(None, size)
        _font_cache[size] = cached
    return cached


def _xy(pos: tuple[float, float], header: TraceHeader) -> tuple[int, int]:
    return world_to_screen(pos, header.grid_width, header.grid_height, CANVAS_SIZE, MARGIN)


def _scale(length: float, header: TraceHeader) -> int:
    return world_length_to_screen(length, header.grid_width, CANVAS_SIZE, MARGIN)


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
    for deg in range(0, 360, 8):
        rad = math.radians(deg)
        p = (int(cx + radius * math.cos(rad)), int(cy + radius * math.sin(rad)))
        pygame.draw.circle(surface, color, p, 1)


def _blit_text(surface, text: str, xy: tuple[int, int], color, size: int = 22) -> int:
    """Draw text; return the rendered height so callers can stack lines."""
    font = _font(size)
    rendered = font.render(text, True, color)
    surface.blit(rendered, xy)
    return rendered.get_height()


def draw_grid_background(surface: Surface, header: TraceHeader) -> None:
    pygame = _require_pygame()
    surface.fill(BACKGROUND_COLOR)
    step = 10.0
    x = 0.0
    while x <= header.grid_width + 1e-9:
        p0 = _xy((x, 0.0), header)
        p1 = _xy((x, header.grid_height), header)
        pygame.draw.line(surface, GRID_LINE_COLOR, p0, p1, 1)
        x += step
    y = 0.0
    while y <= header.grid_height + 1e-9:
        p0 = _xy((0.0, y), header)
        p1 = _xy((header.grid_width, y), header)
        pygame.draw.line(surface, GRID_LINE_COLOR, p0, p1, 1)
        y += step
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
        _draw_dashed_circle(surface, JAMMER_COLOR, center, jam_r, width=1)
        hexagon = _polygon(center, JAMMER_RADIUS, 6, math.pi / 6)
        pygame.draw.polygon(surface, JAMMER_COLOR, hexagon)
        pygame.draw.circle(surface, BACKGROUND_COLOR, center, 3)


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
        elif shape == "octagon":
            points = _polygon(center, r, 8, math.pi / 8)
            pygame.draw.polygon(surface, color, points, 2 if destroyed else 0)
        else:
            rect = pygame.Rect(center[0] - r, center[1] - r, 2 * r, 2 * r)
            pygame.draw.rect(surface, color, rect, 2 if destroyed else 0)

        if destroyed:
            continue
        hp_required = header.target_hp_required[frame.target_id]
        label = f"{frame.hp_remaining}/{hp_required}"
        _blit_text(
            surface,
            label,
            (center[0] - 12, center[1] - r - 16),
            HUD_TEXT_COLOR,
            size=18,
        )
        if target_type == "SAM_SITE":
            _draw_dashed_circle(
                surface,
                color,
                center,
                _scale(header.sam_engagement_range, header),
                width=1,
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
        pygame.draw.circle(surface, fill, center, JET_RADIUS)
        if frame.role is not None:
            ring = ROLE_RING_COLOR.get(frame.role, ROLE_RING_COLOR["IDLE"])
            pygame.draw.circle(surface, ring, center, JET_RADIUS + 3, 2)
        if frame.jammed:
            pygame.draw.circle(surface, JAMMED_RING_COLOR, center, JET_RADIUS + 7, 2)
        # HP bar
        bar_x = center[0] - HP_BAR_WIDTH // 2
        bar_y = center[1] - JET_RADIUS - 10
        pygame.draw.rect(
            surface, (40, 40, 48), pygame.Rect(bar_x, bar_y, HP_BAR_WIDTH, HP_BAR_HEIGHT)
        )
        frac = max(0.0, min(1.0, frame.hp / JET_HP))
        hp_color = (80, 200, 90) if frac > 0.5 else (220, 160, 50) if frac > 0.25 else (210, 60, 60)
        pygame.draw.rect(
            surface,
            hp_color,
            pygame.Rect(bar_x, bar_y, max(0, int(HP_BAR_WIDTH * frac)), HP_BAR_HEIGHT),
        )
        if show_sensor_radius:
            _draw_dotted_circle(surface, (90, 90, 110), center, sensor_r)


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
    pygame.draw.line(
        surface, BORDER_COLOR, (CANVAS_SIZE, 0), (CANVAS_SIZE, CANVAS_SIZE), 1
    )

    x = CANVAS_SIZE + 16
    y = 16
    y += _blit_text(surface, "WraithNet Replay", (x, y), HUD_TEXT_COLOR, 28) + 8
    y += _blit_text(
        surface,
        f"Tick {tick.tick}   ({tick_index + 1}/{total_ticks})",
        (x, y),
        HUD_TEXT_COLOR,
        22,
    ) + 4
    status = "PLAYING" if playing else "PAUSED"
    speed = f"{ticks_per_second:.1f} tps" if ticks_per_second else ""
    y += _blit_text(surface, f"{status}  {speed}".rstrip(), (x, y), HUD_MUTED_COLOR, 20) + 4
    y += _blit_text(
        surface,
        f"Sensor rings: {'ON' if show_sensor_radius else 'OFF'}",
        (x, y),
        HUD_MUTED_COLOR,
        20,
    ) + 12

    targets_destroyed = sum(1 for t in tick.targets if t.hp_remaining <= 0)
    y += _blit_text(
        surface,
        f"Targets  {targets_destroyed}/{len(tick.targets)} destroyed",
        (x, y),
        HUD_TEXT_COLOR,
        20,
    ) + 2
    y += _blit_text(
        surface,
        f"Jets     {len(tick.jets)}/{len(header.jet_ids)} alive",
        (x, y),
        HUD_TEXT_COLOR,
        20,
    ) + 14

    y += _blit_text(surface, "Jets", (x, y), HUD_MUTED_COLOR, 18) + 4
    for agent_id in header.jet_ids:
        color = _jet_color(agent_id, header)
        pygame.draw.circle(surface, color, (x + 8, y + 8), 6)
        alive = any(j.agent_id == agent_id for j in tick.jets)
        label = agent_id if alive else f"{agent_id}  (lost)"
        y += _blit_text(surface, label, (x + 22, y), HUD_TEXT_COLOR, 20) + 4
    y += 8

    y += _blit_text(surface, "Role rings", (x, y), HUD_MUTED_COLOR, 18) + 4
    for role, color in ROLE_RING_COLOR.items():
        pygame.draw.circle(surface, color, (x + 8, y + 8), 7, 2)
        y += _blit_text(surface, role, (x + 22, y), HUD_TEXT_COLOR, 20) + 2
    pygame.draw.circle(surface, JAMMED_RING_COLOR, (x + 8, y + 8), 8, 2)
    y += _blit_text(surface, "JAMMED", (x + 22, y), HUD_TEXT_COLOR, 20) + 12

    y += _blit_text(surface, "Targets", (x, y), HUD_MUTED_COLOR, 18) + 4
    legend_targets = [
        ("HARDENED_BUNKER", "Bunker (square)"),
        ("MOBILE_RADAR", "Radar (diamond)"),
        ("SAM_SITE", "SAM (octagon)"),
    ]
    for key, label in legend_targets:
        color = TARGET_TYPE_STYLE[key]["color"]
        pygame.draw.rect(surface, color, pygame.Rect(x + 2, y + 2, 12, 12))
        y += _blit_text(surface, label, (x + 22, y), HUD_TEXT_COLOR, 20) + 2
    pygame.draw.circle(surface, JAMMER_COLOR, (x + 8, y + 8), 6)
    y += _blit_text(surface, "EW Jammer + jam radius", (x + 22, y), HUD_TEXT_COLOR, 20) + 16

    y += _blit_text(surface, "Controls", (x, y), HUD_MUTED_COLOR, 18) + 4
    for line in (
        "Space   play / pause",
        "Left/Right   step tick",
        "+ / -   speed",
        "S   sensor rings",
        "R   restart",
        "Esc   quit",
    ):
        y += _blit_text(surface, line, (x, y), HUD_MUTED_COLOR, 18) + 2


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

    overlay = pygame.Surface((420, 170), pygame.SRCALPHA)
    overlay.fill(OVERLAY_BG)
    pygame.draw.rect(overlay, BORDER_COLOR, overlay.get_rect(), 1)

    if summary.swarm_wiped_out:
        title, title_color = "SWARM WIPED OUT", (220, 80, 80)
    elif summary.solved:
        title, title_color = "MISSION COMPLETE", (90, 210, 110)
    else:
        title, title_color = "END OF TRACE", HUD_TEXT_COLOR

    weapon_bits = ", ".join(
        f"{k} {v}" for k, v in sorted(summary.per_weapon_type_missiles_fired.items())
    ) or "none"
    lines = [
        (title, title_color, 26),
        (
            f"Targets destroyed  {summary.targets_destroyed}/{summary.total_targets}",
            HUD_TEXT_COLOR,
            22,
        ),
        (
            f"Jets lost          {summary.jets_destroyed}/{summary.total_jets}",
            HUD_TEXT_COLOR,
            22,
        ),
        (
            f"Missiles fired     {summary.total_missiles_fired}  ({weapon_bits})",
            HUD_TEXT_COLOR,
            22,
        ),
    ]
    y = 12
    for text, color, size in lines:
        rendered = _font(size).render(text, True, color)
        overlay.blit(rendered, (16, y))
        y += rendered.get_height() + 6

    # Lower-left of the canvas so the map stays visible.
    surface.blit(overlay, (MARGIN, CANVAS_SIZE - 170 - MARGIN))
