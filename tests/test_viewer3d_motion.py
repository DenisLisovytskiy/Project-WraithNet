"""Panda3D-free unit tests for viewer3d.motion (Phase 46)."""

from __future__ import annotations

from pathlib import Path

import pytest

from viewer3d.motion import (
    MAX_EXTRAPOLATION_ALPHA,
    SampledPose,
    TickBuffer,
    clamp_alpha,
    lerp,
    lerp_heading_deg,
)
from wraithnet.trace_schemas import (
    CountermeasureEvent,
    JetFrame,
    JetOutcomeEvent,
    StrikeEvent,
    TargetFrame,
    TraceTick,
)


def _jet(
    agent_id: str,
    x: float,
    y: float,
    heading: float = 0.0,
    **overrides,
) -> JetFrame:
    data = {
        "agent_id": agent_id,
        "position": (x, y),
        "hp": 5,
        "payload": {"JDAM": 1},
        "destroyed": False,
        "jammed": False,
        "role": "IDLE",
        "heading": heading,
    }
    data.update(overrides)
    return JetFrame(**data)


def _tick(tick: int, jets: list[JetFrame], **overrides) -> TraceTick:
    data = {
        "tick": tick,
        "jets": jets,
        "targets": [TargetFrame(target_id="T1", hp_remaining=1, claimed=False)],
        "strikes": [],
        "running": True,
        "swarm_wiped_out": False,
    }
    data.update(overrides)
    return TraceTick(**data)


def test_motion_module_has_no_rendering_imports():
    source = Path("viewer3d/motion.py").read_text(encoding="utf-8")
    lowered = source.lower()
    assert "import panda3d" not in lowered
    assert "from panda3d" not in lowered
    assert "from direct" not in lowered
    assert "import pygame" not in lowered
    assert "from pygame" not in lowered


def test_importing_motion_does_not_require_panda3d():
    import viewer3d.motion as motion

    assert hasattr(motion, "TickBuffer")
    assert hasattr(motion, "SampledPose")


def test_lerp_midpoint_xy_between_two_ticks():
    buf = TickBuffer(interp_delay_s=0.0)
    buf.push(_tick(0, [_jet("Jet_1", 0.0, 0.0)]), t_accept=0.0)
    buf.push(_tick(1, [_jet("Jet_1", 10.0, 20.0)]), t_accept=1.0)
    buf.set_presentation_time(0.5)
    poses = buf.sample_jet_poses()
    pose = poses["Jet_1"]
    assert pose.x == pytest.approx(5.0)
    assert pose.y == pytest.approx(10.0)
    assert pose.alpha == pytest.approx(0.5)
    assert pose.from_tick == 0
    assert pose.to_tick == 1


def test_heading_wrap_350_to_10_passes_through_zero():
    assert lerp_heading_deg(350.0, 10.0, 0.5) == pytest.approx(0.0)
    # Long way would be ~180°; shortest mid must stay near 0°.
    mid = lerp_heading_deg(350.0, 10.0, 0.5)
    assert mid < 20.0 or mid > 340.0

    buf = TickBuffer(interp_delay_s=0.0)
    buf.push(_tick(0, [_jet("Jet_1", 0.0, 0.0, heading=350.0)]), t_accept=0.0)
    buf.push(_tick(1, [_jet("Jet_1", 0.0, 0.0, heading=10.0)]), t_accept=1.0)
    buf.set_presentation_time(0.5)
    pose = buf.sample_jet_poses()["Jet_1"]
    assert pose.heading_deg == pytest.approx(0.0)


def test_alpha_zero_and_one_return_exact_endpoint_poses():
    buf = TickBuffer(interp_delay_s=0.0)
    buf.push(_tick(0, [_jet("Jet_1", 1.0, 2.0, heading=40.0)]), t_accept=0.0)
    buf.push(_tick(1, [_jet("Jet_1", 9.0, 8.0, heading=80.0)]), t_accept=1.0)

    buf.set_presentation_time(0.0)
    at0 = buf.sample_jet_poses()["Jet_1"]
    assert at0.x == pytest.approx(1.0)
    assert at0.y == pytest.approx(2.0)
    assert at0.heading_deg == pytest.approx(40.0)
    assert at0.alpha == pytest.approx(0.0)

    buf.set_presentation_time(1.0)
    at1 = buf.sample_jet_poses()["Jet_1"]
    assert at1.x == pytest.approx(9.0)
    assert at1.y == pytest.approx(8.0)
    assert at1.heading_deg == pytest.approx(80.0)


def test_missing_next_tick_clamps_and_sets_extrapolating():
    buf = TickBuffer(
        interp_delay_s=0.0,
        max_extrapolation_alpha=MAX_EXTRAPOLATION_ALPHA,
    )
    buf.push(_tick(0, [_jet("Jet_1", 0.0, 0.0)]), t_accept=0.0)
    buf.push(_tick(1, [_jet("Jet_1", 10.0, 0.0)]), t_accept=1.0)

    # Past the newest tick: extrapolate along last segment, clamped.
    buf.set_presentation_time(1.5)
    pose = buf.sample_jet_poses()["Jet_1"]
    assert buf.is_extrapolating is True
    assert pose.alpha == pytest.approx(MAX_EXTRAPOLATION_ALPHA)
    assert pose.x == pytest.approx(lerp(0.0, 10.0, MAX_EXTRAPOLATION_ALPHA))

    # Single tick only: hold, not extrapolating.
    alone = TickBuffer(interp_delay_s=0.0)
    alone.push(_tick(0, [_jet("Jet_1", 3.0, 4.0)]), t_accept=0.0)
    alone.set_presentation_time(5.0)
    held = alone.sample_jet_poses()["Jet_1"]
    assert alone.is_extrapolating is False
    assert held.x == pytest.approx(3.0)
    assert held.y == pytest.approx(4.0)


def test_burst_push_then_early_sample_uses_early_segment():
    buf = TickBuffer(interp_delay_s=0.0)
    # Burst of ticks accepted in rapid succession.
    for i in range(5):
        buf.push(
            _tick(i, [_jet("Jet_1", float(i * 10), 0.0)]),
            t_accept=float(i),
        )
    assert buf.backlog_len == 5

    buf.set_presentation_time(0.5)
    pose = buf.sample_jet_poses()["Jet_1"]
    assert pose.from_tick == 0
    assert pose.to_tick == 1
    assert pose.x == pytest.approx(5.0)


def test_pop_newly_presented_events_fires_once_per_crossed_tick():
    strike = StrikeEvent(agent_id="Jet_1", target_id="T1", weapon_type="JDAM")
    outcome = JetOutcomeEvent(agent_id="Jet_2", tick=1, outcome="destroyed")
    cm = CountermeasureEvent(event="flare", agent_id="Jet_1", accepted=True)

    buf = TickBuffer(interp_delay_s=0.0)
    buf.push(_tick(0, [_jet("Jet_1", 0.0, 0.0)], strikes=[strike]), t_accept=0.0)
    buf.push(
        _tick(
            1,
            [_jet("Jet_1", 1.0, 0.0)],
            jet_outcomes=[outcome],
            countermeasures=[cm],
        ),
        t_accept=1.0,
    )

    buf.set_presentation_time(0.0)
    first = buf.pop_newly_presented_events()
    assert len(first) == 1
    assert first[0].kind == "STRIKE"
    assert first[0].payload is strike
    assert buf.pop_newly_presented_events() == []

    buf.advance(1.0)
    second = buf.pop_newly_presented_events()
    kinds = {e.kind for e in second}
    assert kinds == {"JET_OUTCOME", "COUNTERMEASURE"}
    assert buf.pop_newly_presented_events() == []


def test_disappeared_jet_does_not_nan_or_jump_to_origin():
    buf = TickBuffer(interp_delay_s=0.0)
    buf.push(_tick(0, [_jet("Jet_1", 5.0, 7.0, heading=90.0)]), t_accept=0.0)
    buf.push(_tick(1, []), t_accept=1.0)  # jet gone on tick 1

    # Mid-segment: hold last pose (do not lerp toward 0,0).
    buf.set_presentation_time(0.5)
    poses = buf.sample_jet_poses()
    assert "Jet_1" in poses
    pose = poses["Jet_1"]
    assert pose.x == pytest.approx(5.0)
    assert pose.y == pytest.approx(7.0)
    assert pose.heading_deg == pytest.approx(90.0)

    # Once tick 1 is HUD-current, omit the jet.
    buf.set_presentation_time(1.0)
    assert "Jet_1" not in buf.sample_jet_poses()
    assert buf.current_tick_for_hud() is not None
    assert buf.current_tick_for_hud().tick == 1


def test_reset_clears_backlog_and_presentation_state():
    buf = TickBuffer(interp_delay_s=0.05)
    buf.push(_tick(0, [_jet("Jet_1", 0.0, 0.0)]), t_accept=1.0)
    buf.push(_tick(1, [_jet("Jet_1", 1.0, 0.0)]), t_accept=2.0)
    buf.set_presentation_time(2.0)
    buf.sample_jet_poses()
    buf.pop_newly_presented_events()

    buf.reset()
    assert buf.backlog_len == 0
    assert buf.sample_jet_poses() == {}
    assert buf.current_tick_for_hud() is None
    assert buf.pop_newly_presented_events() == []
    assert buf.is_extrapolating is False


def test_stale_out_of_order_push_is_dropped():
    buf = TickBuffer(interp_delay_s=0.0)
    buf.push(_tick(2, [_jet("Jet_1", 2.0, 0.0)]), t_accept=2.0)
    buf.push(_tick(1, [_jet("Jet_1", 1.0, 0.0)]), t_accept=3.0)  # stale
    assert buf.backlog_len == 1
    buf.set_presentation_time(2.0)
    assert buf.sample_jet_poses()["Jet_1"].x == pytest.approx(2.0)


def test_clamp_alpha_helper():
    assert clamp_alpha(-0.5, max_extrapolation=1.1) == 0.0
    assert clamp_alpha(0.5, max_extrapolation=1.1) == 0.5
    assert clamp_alpha(1.05, max_extrapolation=1.1) == pytest.approx(1.05)
    assert clamp_alpha(2.0, max_extrapolation=1.1) == pytest.approx(1.1)


def test_interp_delay_keeps_sample_behind_presentation_time():
    buf = TickBuffer(interp_delay_s=0.10)
    buf.push(_tick(0, [_jet("Jet_1", 0.0, 0.0)]), t_accept=0.0)
    buf.push(_tick(1, [_jet("Jet_1", 10.0, 0.0)]), t_accept=1.0)
    # Wall presentation at 1.0 samples at 0.9 → still on segment 0→1.
    buf.set_presentation_time(1.0)
    pose = buf.sample_jet_poses()["Jet_1"]
    assert pose.from_tick == 0
    assert pose.to_tick == 1
    assert pose.alpha == pytest.approx(0.9)
    assert isinstance(pose, SampledPose)


def test_spawned_jet_does_not_lerp_from_origin():
    buf = TickBuffer(interp_delay_s=0.0)
    buf.push(_tick(0, []), t_accept=0.0)
    buf.push(_tick(1, [_jet("Jet_1", 12.0, 34.0, heading=45.0)]), t_accept=1.0)
    buf.set_presentation_time(0.5)
    pose = buf.sample_jet_poses()["Jet_1"]
    assert pose.x == pytest.approx(12.0)
    assert pose.y == pytest.approx(34.0)
    assert pose.heading_deg == pytest.approx(45.0)


# --- Phase 48: burst absorption, catch-up, delay ---


def test_flood_snap_bounds_backlog_and_skips_intermediate_events():
    from viewer3d.motion import CATCHUP_BACKLOG_TICKS

    buf = TickBuffer(interp_delay_s=0.0, catchup_backlog_ticks=CATCHUP_BACKLOG_TICKS)
    strike = StrikeEvent(agent_id="Jet_1", target_id="T1", weapon_type="JDAM")
    t0 = 100.0
    for i in range(50):
        strikes = [strike] if i == 10 else []
        buf.push(
            _tick(i, [_jet("Jet_1", float(i), 0.0)], strikes=strikes),
            t_accept=t0 + i * 0.001,
        )
    assert buf.backlog_len <= CATCHUP_BACKLOG_TICKS
    assert buf.consume_snap_flag() is True

    # Strike on discarded tick 10 must never fire after the snap.
    buf.set_presentation_time(t0 + 1.0)
    events = buf.pop_newly_presented_events()
    struck_ticks = {e.tick for e in events if e.kind == "STRIKE"}
    assert 10 not in struck_ticks


def test_delay_with_close_ticks_samples_earlier_or_waits():
    """delay=0.1, ticks at 1.0 and 1.05, presentation at 1.0 → t_eff=0.9 (waits)."""
    buf = TickBuffer(interp_delay_s=0.10)
    buf.push(_tick(0, [_jet("Jet_1", 0.0, 0.0)]), t_accept=1.0)
    buf.push(_tick(1, [_jet("Jet_1", 10.0, 0.0)]), t_accept=1.05)
    buf.set_presentation_time(1.0)
    pose = buf.sample_jet_poses()["Jet_1"]
    # Effective time 0.9 is before first accept → hold first tick, not latest.
    assert pose.x == pytest.approx(0.0)
    assert pose.from_tick == 0
    assert pose.to_tick == 0
    assert buf.current_tick_for_hud() is None


def test_snap_path_exceeding_max_backlog_leaves_length_bounded():
    from viewer3d.motion import CATCHUP_BACKLOG_TICKS

    buf = TickBuffer(catchup_backlog_ticks=CATCHUP_BACKLOG_TICKS)
    # Crossing the threshold snaps to the latest pair immediately.
    for i in range(CATCHUP_BACKLOG_TICKS + 1):
        buf.push(_tick(i, [_jet("Jet_1", float(i), 0.0)]), t_accept=float(i))
    assert buf.backlog_len == 2
    assert [t.tick for t in buf.buffered_ticks()] == [
        CATCHUP_BACKLOG_TICKS - 1,
        CATCHUP_BACKLOG_TICKS,
    ]
    # Further growth stays capped by the same snap threshold.
    for i in range(CATCHUP_BACKLOG_TICKS + 1, CATCHUP_BACKLOG_TICKS + 25):
        buf.push(_tick(i, [_jet("Jet_1", float(i), 0.0)]), t_accept=float(i))
    assert buf.backlog_len <= CATCHUP_BACKLOG_TICKS



def test_extrapolation_does_not_duplicate_presented_events():
    strike = StrikeEvent(agent_id="Jet_1", target_id="T1", weapon_type="JDAM")
    buf = TickBuffer(interp_delay_s=0.0, max_extrapolation_alpha=MAX_EXTRAPOLATION_ALPHA)
    buf.push(_tick(0, [_jet("Jet_1", 0.0, 0.0)], strikes=[strike]), t_accept=0.0)
    buf.push(_tick(1, [_jet("Jet_1", 10.0, 0.0)]), t_accept=1.0)
    buf.set_presentation_time(0.0)
    first = buf.pop_newly_presented_events()
    assert len(first) == 1 and first[0].kind == "STRIKE"

    buf.set_presentation_time(1.0)
    second = buf.pop_newly_presented_events()
    assert second == []  # tick 1 has no events

    # Extrapolate past newest tick — must not re-fire.
    buf.set_presentation_time(1.5)
    buf.sample_jet_poses()
    assert buf.is_extrapolating is True
    assert buf.pop_newly_presented_events() == []
    assert buf.pop_newly_presented_events() == []

    # Next tick arrives; old strike still not re-emitted.
    buf.push(_tick(2, [_jet("Jet_1", 20.0, 0.0)]), t_accept=2.0)
    buf.set_presentation_time(2.0)
    assert buf.pop_newly_presented_events() == []


def test_catchup_rate_speeds_presentation_when_lagged():
    from viewer3d.motion import CATCHUP_MAX_RATE

    buf = TickBuffer(interp_delay_s=0.10, catchup_max_rate=CATCHUP_MAX_RATE)
    buf.push(_tick(0, [_jet("Jet_1", 0.0, 0.0)]), t_accept=0.0)
    buf.push(_tick(1, [_jet("Jet_1", 10.0, 0.0)]), t_accept=10.0)
    buf.set_presentation_time(0.0)
    assert buf.presentation_lag_s() == pytest.approx(10.0)
    assert buf.catchup_rate() == pytest.approx(CATCHUP_MAX_RATE)
    buf.advance(0.1)
    assert buf._t_p == pytest.approx(0.1 * CATCHUP_MAX_RATE)
    assert buf.is_catching_up is True


def test_push_sample_1000_ticks_under_ci_ceiling():
    import time

    from viewer3d.motion import CATCHUP_BACKLOG_TICKS

    buf = TickBuffer(interp_delay_s=0.0, catchup_backlog_ticks=CATCHUP_BACKLOG_TICKS)
    t0 = time.perf_counter()
    for i in range(1000):
        buf.push(_tick(i, [_jet("Jet_1", float(i % 50), 0.0)]), t_accept=float(i) * 0.01)
        buf.set_presentation_time(float(i) * 0.01)
        buf.sample_jet_poses()
    elapsed_ms = (time.perf_counter() - t0) * 1000.0
    assert elapsed_ms < 100.0, f"motion flood took {elapsed_ms:.1f} ms"
    assert buf.backlog_len <= CATCHUP_BACKLOG_TICKS

