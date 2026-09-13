"""Layer-1 tests for viewer3d live presentation pacing (no panda3d)."""

from __future__ import annotations

from viewer3d.playback import (
    DEFAULT_TPS,
    advance_playback,
    apply_keydown,
    initial_live_view_state,
)


def test_initial_state_is_playing_at_default_tps():
    state = initial_live_view_state()
    assert state.playing is True
    assert state.ticks_per_second == DEFAULT_TPS
    assert state.tick_index == -1


def test_advance_playback_paces_forward():
    idx, playing, acc = advance_playback(
        tick_index=0,
        playing=True,
        accumulator_ms=0.0,
        dt_ms=500.0,
        ticks_per_second=4.0,
        last_index=10,
    )
    assert idx == 2
    assert playing is True
    assert acc == 0.0


def test_space_toggles_pause_and_arrows_step():
    state = initial_live_view_state()
    state = state.__class__(
        tick_index=5,
        last_index=20,
        playing=True,
        ticks_per_second=DEFAULT_TPS,
        playback_accumulator_ms=0.0,
    )
    paused = apply_keydown(state, "space")
    assert paused.playing is False
    stepped = apply_keydown(paused, "left")
    assert stepped.tick_index == 4
    assert stepped.playing is False
    forward = apply_keydown(stepped, "right")
    assert forward.tick_index == 5
