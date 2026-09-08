"""python -m viewer.app <path-to-trace.jsonl>"""

from __future__ import annotations

import sys

from viewer.controls import (
    DEFAULT_TPS,
    ReplayViewState,
    apply_keydown,
)
from viewer.playback import advance_playback, compute_highlights
from viewer.trace_loader import load_trace

USAGE = "Usage: python -m viewer.app <path-to-trace.jsonl>"


def parse_viewer_argv(argv: list[str]) -> str:
    """Return the trace-file path. Raise SystemExit if len(argv) != 1 after
    the script name has been stripped -- same usage string main() already prints.
    """
    if len(argv) != 1:
        raise SystemExit(USAGE)
    return argv[0]


def _key_name(pygame_module, key: int) -> str | None:
    """Map K_* codes onto the closed string set understood by apply_keydown."""
    mapping = {
        pygame_module.K_RIGHT: "right",
        pygame_module.K_LEFT: "left",
        pygame_module.K_SPACE: "space",
        pygame_module.K_PLUS: "plus",
        pygame_module.K_EQUALS: "plus",
        pygame_module.K_KP_PLUS: "plus",
        pygame_module.K_MINUS: "minus",
        pygame_module.K_KP_MINUS: "minus",
        pygame_module.K_s: "s",
        pygame_module.K_r: "r",
        pygame_module.K_ESCAPE: "escape",
    }
    return mapping.get(key)


def main(argv: list[str] | None = None) -> None:
    if argv is None:
        argv = sys.argv[1:]
    try:
        path = parse_viewer_argv(argv)
    except SystemExit as exc:
        print(exc.code if isinstance(exc.code, str) else USAGE)
        sys.exit(1)

    # Pygame is imported only when actually launching the window so
    # parse_viewer_argv (and Layer-1 CLI tests) stay pygame-free.
    import pygame

    from viewer.render import (
        WINDOW_SIZE,
        draw_destruction_pulses,
        draw_end_of_run_overlay,
        draw_grid_background,
        draw_hud,
        draw_jam_transition_flash,
        draw_jammer,
        draw_home_base,
        draw_jets,
        draw_strike_highlights,
        draw_targets,
    )

    header, ticks = load_trace(path)
    last_index = len(ticks) - 1
    state = ReplayViewState(
        tick_index=0,
        last_index=last_index,
        playing=False,
        ticks_per_second=DEFAULT_TPS,
        show_sensor_radius=False,
        playback_accumulator_ms=0.0,
    )

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
                name = _key_name(pygame, event.key)
                if name is not None:
                    state = apply_keydown(state, name)
                    if state.quit_requested:
                        running = False

        tick_index, playing, playback_accumulator_ms = advance_playback(
            state.tick_index,
            state.playing,
            state.playback_accumulator_ms,
            dt_ms,
            state.ticks_per_second,
            state.last_index,
        )
        state = ReplayViewState(
            tick_index=tick_index,
            last_index=state.last_index,
            playing=playing,
            ticks_per_second=state.ticks_per_second,
            show_sensor_radius=state.show_sensor_radius,
            playback_accumulator_ms=playback_accumulator_ms,
            quit_requested=state.quit_requested,
        )

        current = ticks[state.tick_index]
        previous = ticks[state.tick_index - 1] if state.tick_index > 0 else None
        highlights = compute_highlights(previous, current)

        draw_grid_background(screen, header)
        draw_jammer(screen, header)
        draw_home_base(screen, header)
        draw_targets(screen, header, current)
        draw_jets(screen, header, current, state.show_sensor_radius)
        draw_strike_highlights(screen, header, current, highlights, previous)
        draw_destruction_pulses(screen, header, current, highlights, previous)
        draw_jam_transition_flash(screen, header, current, highlights)
        draw_hud(
            screen,
            header,
            current,
            state.tick_index,
            len(ticks),
            state.show_sensor_radius,
            playing=state.playing,
            ticks_per_second=state.ticks_per_second,
        )
        if state.tick_index == state.last_index:
            draw_end_of_run_overlay(screen, header, ticks)
        pygame.display.flip()

    pygame.quit()


if __name__ == "__main__":
    main()
