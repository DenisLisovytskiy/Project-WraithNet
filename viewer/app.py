"""python -m viewer.app <path-to-trace.jsonl>"""

import sys

import pygame

from viewer.playback import advance_playback, compute_highlights
from viewer.render import (
    WINDOW_SIZE,
    draw_destruction_pulses,
    draw_end_of_run_overlay,
    draw_grid_background,
    draw_hud,
    draw_jam_transition_flash,
    draw_jammer,
    draw_jets,
    draw_strike_highlights,
    draw_targets,
)
from viewer.trace_loader import load_trace

MIN_TPS = 0.5
MAX_TPS = 20.0


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python -m viewer.app <path-to-trace.jsonl>")
        sys.exit(1)

    header, ticks = load_trace(sys.argv[1])
    last_index = len(ticks) - 1
    tick_index = 0
    show_sensor_radius = False
    playing = False
    ticks_per_second = 4.0
    playback_accumulator_ms = 0.0

    pygame.init()
    screen = pygame.display.set_mode(WINDOW_SIZE)
    pygame.display.set_caption("WraithNet Replay Viewer")
    clock = pygame.time.Clock()

    running = True
    while running:
        dt_ms = clock.tick(60)
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key == pygame.K_ESCAPE:
                    running = False
                elif event.key == pygame.K_RIGHT:
                    playing = False
                    playback_accumulator_ms = 0.0
                    tick_index = min(tick_index + 1, last_index)
                elif event.key == pygame.K_LEFT:
                    playing = False
                    playback_accumulator_ms = 0.0
                    tick_index = max(tick_index - 1, 0)
                elif event.key == pygame.K_SPACE:
                    playing = not playing
                    playback_accumulator_ms = 0.0
                elif event.key in (pygame.K_PLUS, pygame.K_EQUALS, pygame.K_KP_PLUS):
                    ticks_per_second = min(MAX_TPS, ticks_per_second * 1.5)
                elif event.key in (pygame.K_MINUS, pygame.K_KP_MINUS):
                    ticks_per_second = max(MIN_TPS, ticks_per_second / 1.5)
                elif event.key == pygame.K_s:
                    show_sensor_radius = not show_sensor_radius
                elif event.key == pygame.K_r:
                    tick_index = 0
                    playing = False
                    playback_accumulator_ms = 0.0

        tick_index, playing, playback_accumulator_ms = advance_playback(
            tick_index,
            playing,
            playback_accumulator_ms,
            dt_ms,
            ticks_per_second,
            last_index,
        )

        current = ticks[tick_index]
        previous = ticks[tick_index - 1] if tick_index > 0 else None
        highlights = compute_highlights(previous, current)

        draw_grid_background(screen, header)
        draw_jammer(screen, header)
        draw_targets(screen, header, current)
        draw_jets(screen, header, current, show_sensor_radius)
        draw_strike_highlights(screen, header, current, highlights, previous)
        draw_destruction_pulses(screen, header, current, highlights, previous)
        draw_jam_transition_flash(screen, header, current, highlights)
        draw_hud(
            screen,
            header,
            current,
            tick_index,
            len(ticks),
            show_sensor_radius,
            playing=playing,
            ticks_per_second=ticks_per_second,
        )
        if tick_index == last_index:
            draw_end_of_run_overlay(screen, header, ticks)
        pygame.display.flip()

    pygame.quit()


if __name__ == "__main__":
    main()
