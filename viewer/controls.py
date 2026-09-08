"""Pure replay-viewer input/state. No rendering imports -- tests drive this
with string key names so the keyboard table is Layer-1 testable.
See docs/14_Automated_GUI_Testing_Design_Document.md §5.1.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

MIN_TPS = 0.5
MAX_TPS = 20.0
DEFAULT_TPS = 4.0


@dataclass(frozen=True)
class ReplayViewState:
    tick_index: int
    last_index: int
    playing: bool
    ticks_per_second: float
    show_sensor_radius: bool
    playback_accumulator_ms: float
    quit_requested: bool = False


def apply_keydown(state: ReplayViewState, key: str) -> ReplayViewState:
    """`key` is a stable name from this closed set:
    'right', 'left', 'space', 'plus', 'minus', 's', 'r', 'escape'.
    Unknown keys are a no-op (return state unchanged).
    """
    if key == "right":
        return replace(
            state,
            playing=False,
            playback_accumulator_ms=0.0,
            tick_index=min(state.tick_index + 1, state.last_index),
        )
    if key == "left":
        return replace(
            state,
            playing=False,
            playback_accumulator_ms=0.0,
            tick_index=max(state.tick_index - 1, 0),
        )
    if key == "space":
        return replace(
            state,
            playing=not state.playing,
            playback_accumulator_ms=0.0,
        )
    if key == "plus":
        return replace(
            state,
            ticks_per_second=min(MAX_TPS, state.ticks_per_second * 1.5),
        )
    if key == "minus":
        return replace(
            state,
            ticks_per_second=max(MIN_TPS, state.ticks_per_second / 1.5),
        )
    if key == "s":
        return replace(state, show_sensor_radius=not state.show_sensor_radius)
    if key == "r":
        return replace(
            state,
            tick_index=0,
            playing=False,
            playback_accumulator_ms=0.0,
        )
    if key == "escape":
        return replace(state, quit_requested=True)
    return state
