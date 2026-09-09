"""Panda3D-free unit tests for viewer3d.flight_dynamics (Phase 25)."""

from pathlib import Path

import pytest

from viewer3d.flight_dynamics import (
    ATTACK_ALTITUDE,
    CRUISE_ALTITUDE,
    ENGAGEMENT_RADIUS_MULTIPLIER,
    GROUND_ALTITUDE,
    MAX_BANK_DEG,
    MAX_CLIMB_RATE_PER_SECOND,
    POST_STRIKE_CLIMB_BOOST_ALTITUDE,
    TAKEOFF_CLIMB_SECONDS,
    desired_altitude,
    desired_bank_deg,
    ease_toward,
    nearest_alive_target_distance,
    strike_boost_active,
)
from wraithnet.trace_schemas import JetFrame, TargetFrame, TraceHeader, TraceTick


def _header(**overrides) -> TraceHeader:
    data = {
        "grid_width": 100.0,
        "grid_height": 100.0,
        "sensor_radius": 65.0,
        "jam_radius": 12.0,
        "sam_engagement_range": 15.0,
        "strike_range": 1.0,
        "jet_ids": ["Jet_1"],
        "initial_payload": {"Jet_1": {"JDAM": 1, "AIM9X": 0}},
        "target_ids": ["Near", "Far"],
        "target_types": {"Near": "HARDENED_BUNKER", "Far": "HARDENED_BUNKER"},
        "target_hp_required": {"Near": 1, "Far": 5},
        "sam_site_ids": [],
        "jammer_positions": {},
        "target_positions": {"Near": (20.5, 20.0), "Far": (90.0, 90.0)},
    }
    data.update(overrides)
    return TraceHeader(**data)


def _tick(**overrides) -> TraceTick:
    data = {
        "tick": 0,
        "jets": [
            JetFrame(
                agent_id="Jet_1",
                position=(20.0, 20.0),
                hp=5,
                payload={"JDAM": 1, "AIM9X": 0},
                destroyed=False,
                jammed=False,
                role="IDLE",
            )
        ],
        "targets": [
            TargetFrame(target_id="Near", hp_remaining=1, claimed=False),
            TargetFrame(target_id="Far", hp_remaining=5, claimed=False),
        ],
        "strikes": [],
        "running": True,
        "swarm_wiped_out": False,
    }
    data.update(overrides)
    return TraceTick(**data)


def test_flight_dynamics_module_has_no_panda3d_imports():
    source = Path("viewer3d/flight_dynamics.py").read_text(encoding="utf-8")
    lowered = source.lower()
    assert "import panda3d" not in lowered
    assert "from panda3d" not in lowered
    assert "from direct" not in lowered


def test_nearest_alive_target_distance_ignores_destroyed_targets():
    header = _header()
    tick = _tick(
        targets=[
            TargetFrame(target_id="Near", hp_remaining=0, claimed=False),
            TargetFrame(target_id="Far", hp_remaining=5, claimed=False),
        ]
    )
    dist = nearest_alive_target_distance((20.0, 20.0), header, tick)
    assert dist == pytest.approx(98.99, abs=0.1)

    all_dead = _tick(
        targets=[
            TargetFrame(target_id="Near", hp_remaining=0, claimed=False),
            TargetFrame(target_id="Far", hp_remaining=0, claimed=False),
        ]
    )
    assert nearest_alive_target_distance((20.0, 20.0), header, all_dead) is None


def test_nearest_alive_target_distance_picks_closest_live_target():
    header = _header()
    tick = _tick()
    dist = nearest_alive_target_distance((20.0, 20.0), header, tick)
    assert dist == pytest.approx(0.5)


def test_desired_altitude_takeoff_and_cruise():
    header = _header()
    tick = _tick(
        targets=[TargetFrame(target_id="Far", hp_remaining=5, claimed=False)],
        jets=[
            JetFrame(
                agent_id="Jet_1",
                position=(5.0, 5.0),
                hp=5,
                payload={"JDAM": 1, "AIM9X": 0},
                destroyed=False,
                jammed=False,
                role="IDLE",
            )
        ],
    )
    at_spawn = desired_altitude(0.0, (5.0, 5.0), header, tick, just_struck=False)
    assert at_spawn == pytest.approx(GROUND_ALTITUDE, abs=0.05)
    at_cruise = desired_altitude(
        TAKEOFF_CLIMB_SECONDS, (5.0, 5.0), header, tick, just_struck=False
    )
    assert at_cruise == pytest.approx(CRUISE_ALTITUDE, abs=0.05)


def test_desired_altitude_proximity_dive_and_post_strike_boost():
    header = _header(strike_range=1.0)
    tick = _tick()
    far_pos = (5.0, 5.0)
    near_pos = (20.0, 20.0)
    far_alt = desired_altitude(
        TAKEOFF_CLIMB_SECONDS, far_pos, header, tick, just_struck=False
    )
    near_alt = desired_altitude(
        TAKEOFF_CLIMB_SECONDS, near_pos, header, tick, just_struck=False
    )
    assert near_alt < far_alt
    assert near_alt <= CRUISE_ALTITUDE
    assert near_alt >= ATTACK_ALTITUDE

    boosted = desired_altitude(
        TAKEOFF_CLIMB_SECONDS, near_pos, header, tick, just_struck=True
    )
    assert boosted > near_alt
    assert boosted <= CRUISE_ALTITUDE + POST_STRIKE_CLIMB_BOOST_ALTITUDE


def test_desired_bank_deg_first_tick_and_clamp():
    assert desired_bank_deg(None, CRUISE_ALTITUDE, 0.1) == 0.0
    climbing = desired_bank_deg(2.0, 8.0, 1.0)
    diving = desired_bank_deg(8.0, 2.0, 1.0)
    assert climbing > 0.0
    assert diving < 0.0
    huge = desired_bank_deg(0.0, 100.0, 0.01)
    assert abs(huge) <= MAX_BANK_DEG


def test_ease_toward_moves_bounded_amount_and_does_not_overshoot():
    moved = ease_toward(0.0, 10.0, 2.0, 0.5)
    assert moved == pytest.approx(1.0)
    landed = ease_toward(9.5, 10.0, 2.0, 0.5)
    assert landed == pytest.approx(10.0)
    assert ease_toward(5.0, 5.0, 2.0, 0.5) == pytest.approx(5.0)


def test_strike_boost_active_window_is_tick_counted():
    assert strike_boost_active(5, 5) is True
    assert strike_boost_active(5, 7) is True
    assert strike_boost_active(5, 8) is False
    assert strike_boost_active(None, 3) is False


def test_engagement_radius_uses_strike_range_multiplier():
    header = _header(strike_range=2.0)
    threshold = header.strike_range * ENGAGEMENT_RADIUS_MULTIPLIER
    assert threshold == pytest.approx(6.0)


def test_desired_altitude_near_target_at_takeoff_stays_on_ground():
    header = _header(strike_range=1.0)
    tick = _tick()
    near_pos = (20.0, 20.0)
    at_spawn = desired_altitude(0.0, near_pos, header, tick, just_struck=False)
    assert at_spawn == pytest.approx(GROUND_ALTITUDE, abs=0.05)
    assert at_spawn < ATTACK_ALTITUDE


def test_desired_altitude_prefers_authoritative_trace_altitude():
    header = _header()
    tick = _tick()
    auth = desired_altitude(
        0.0,
        (5.0, 5.0),
        header,
        tick,
        just_struck=True,
        authoritative_altitude=20.0,
    )
    assert auth == pytest.approx(20.0)
