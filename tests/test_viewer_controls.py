"""Layer-1 tests for viewer.controls.apply_keydown. No pygame import."""

from __future__ import annotations

from viewer.controls import (
    DEFAULT_TPS,
    MAX_TPS,
    MIN_TPS,
    ReplayViewState,
    apply_keydown,
)


def _state(**overrides) -> ReplayViewState:
    data = dict(
        tick_index=0,
        last_index=4,
        playing=False,
        ticks_per_second=DEFAULT_TPS,
        show_sensor_radius=False,
        playback_accumulator_ms=12.0,
        quit_requested=False,
    )
    data.update(overrides)
    return ReplayViewState(**data)


def test_right_steps_forward_and_pauses():
    nxt = apply_keydown(_state(tick_index=0, playing=True), "right")
    assert nxt.tick_index == 1
    assert nxt.playing is False
    assert nxt.playback_accumulator_ms == 0.0


def test_right_while_playing_pauses():
    nxt = apply_keydown(_state(tick_index=2, playing=True), "right")
    assert nxt.playing is False
    assert nxt.tick_index == 3


def test_right_at_last_index_stays():
    nxt = apply_keydown(_state(tick_index=4, last_index=4), "right")
    assert nxt.tick_index == 4


def test_left_at_zero_stays_and_left_from_two_goes_to_one():
    at_zero = apply_keydown(_state(tick_index=0, playing=True), "left")
    assert at_zero.tick_index == 0
    assert at_zero.playing is False

    from_two = apply_keydown(_state(tick_index=2, playing=True), "left")
    assert from_two.tick_index == 1
    assert from_two.playing is False
    assert from_two.playback_accumulator_ms == 0.0


def test_space_toggles_playing_and_zeroes_accumulator():
    into_play = apply_keydown(_state(playing=False, playback_accumulator_ms=9.0), "space")
    assert into_play.playing is True
    assert into_play.playback_accumulator_ms == 0.0

    into_pause = apply_keydown(_state(playing=True, playback_accumulator_ms=9.0), "space")
    assert into_pause.playing is False
    assert into_pause.playback_accumulator_ms == 0.0


def test_plus_and_minus_clamp_tps():
    bumped = apply_keydown(_state(ticks_per_second=DEFAULT_TPS), "plus")
    assert bumped.ticks_per_second > DEFAULT_TPS

    state = _state(ticks_per_second=DEFAULT_TPS)
    for _ in range(40):
        state = apply_keydown(state, "plus")
    assert state.ticks_per_second == MAX_TPS

    state = _state(ticks_per_second=DEFAULT_TPS)
    for _ in range(40):
        state = apply_keydown(state, "minus")
    assert state.ticks_per_second == MIN_TPS


def test_s_toggles_sensor_r_resets_escape_quits():
    sensed = apply_keydown(_state(show_sensor_radius=False), "s")
    assert sensed.show_sensor_radius is True
    sensed_off = apply_keydown(sensed, "s")
    assert sensed_off.show_sensor_radius is False

    reset = apply_keydown(
        _state(tick_index=3, playing=True, playback_accumulator_ms=5.0), "r"
    )
    assert reset.tick_index == 0
    assert reset.playing is False
    assert reset.playback_accumulator_ms == 0.0

    quit_state = apply_keydown(_state(tick_index=2), "escape")
    assert quit_state.quit_requested is True
    assert quit_state.tick_index == 2


def test_unknown_key_is_noop():
    start = _state(tick_index=2, playing=True, playback_accumulator_ms=3.0)
    assert apply_keydown(start, "q") == start
