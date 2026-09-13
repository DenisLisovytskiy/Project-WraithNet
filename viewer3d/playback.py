"""Pure live/replay presentation pacing for viewer3d — no panda3d import.

Mirrors viewer/controls.py + viewer/playback.advance_playback so the live
3D path can pause, step, and rewind buffered ticks without importing pygame.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

MIN_TPS = 0.5
MAX_TPS = 20.0
DEFAULT_TPS = 3.0


@dataclass(frozen=True)
class LiveViewState:
    """Presentation cursor over a growing tick buffer."""

    tick_index: int  # -1 = nothing presented yet
    last_index: int  # inclusive; -1 if buffer empty
    playing: bool
    ticks_per_second: float
    playback_accumulator_ms: float


def initial_live_view_state() -> LiveViewState:
    return LiveViewState(
        tick_index=-1,
        last_index=-1,
        playing=True,
        ticks_per_second=DEFAULT_TPS,
        playback_accumulator_ms=0.0,
    )


def advance_playback(
    tick_index: int,
    playing: bool,
    accumulator_ms: float,
    dt_ms: float,
    ticks_per_second: float,
    last_index: int,
) -> tuple[int, bool, float]:
    """Advance tick_index from a frame delta. Does not auto-pause at end while
    the buffer may still grow (live ingest); caller pauses when the presented
    tick has ``running=False`` and no further ticks arrive.
    """
    if not playing or last_index < 0:
        return tick_index, playing, accumulator_ms
    if tick_index < 0:
        return 0, playing, 0.0
    accumulator_ms += dt_ms
    ms_per_tick = 1000.0 / ticks_per_second
    while accumulator_ms >= ms_per_tick and tick_index < last_index:
        tick_index += 1
        accumulator_ms -= ms_per_tick
    return tick_index, playing, accumulator_ms


def apply_keydown(state: LiveViewState, key: str) -> LiveViewState:
    """Stable key names: 'right', 'left', 'space', 'plus', 'minus', 'r'."""
    if state.last_index < 0:
        if key == "space":
            return replace(state, playing=not state.playing, playback_accumulator_ms=0.0)
        return state
    if key == "right":
        return replace(
            state,
            playing=False,
            playback_accumulator_ms=0.0,
            tick_index=min(state.tick_index + 1, state.last_index)
            if state.tick_index >= 0
            else 0,
        )
    if key == "left":
        cur = max(state.tick_index, 0)
        return replace(
            state,
            playing=False,
            playback_accumulator_ms=0.0,
            tick_index=max(cur - 1, 0),
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
    if key == "r":
        return replace(
            state,
            tick_index=0 if state.last_index >= 0 else -1,
            playing=False,
            playback_accumulator_ms=0.0,
        )
    return state
