"""Phase 50 — attitude contracts and airframe rate catalog invariants."""

import pytest

from wraithnet import config
from wraithnet.broker import MessageBroker
from wraithnet.geometry import clamp_pitch_deg, normalize_roll_deg, shortest_signed_angle
from wraithnet.schemas import ActionIntent, Observation
from wraithnet.world import WraithNetModel


def test_catalog_attitude_rates_interceptor_sharpest_bomber_dullest():
    """INTERCEPTOR.{yaw,pitch,roll}_rate > STRIKE_FIGHTER.* > HEAVY_BOMBER.*"""
    interceptor = config.JET_FRAME_CATALOG["INTERCEPTOR"]
    strike = config.JET_FRAME_CATALOG["STRIKE_FIGHTER"]
    bomber = config.JET_FRAME_CATALOG["HEAVY_BOMBER"]

    for axis in ("yaw_rate_deg_per_tick", "pitch_rate_deg_per_tick", "roll_rate_deg_per_tick"):
        i = getattr(interceptor, axis)
        s = getattr(strike, axis)
        b = getattr(bomber, axis)
        assert i > s > b, f"{axis}: {i} > {s} > {b} failed"

    # yaw alias matches legacy turn_rate field
    assert interceptor.yaw_rate_deg_per_tick == interceptor.turn_rate_deg_per_tick
    assert strike.yaw_rate_deg_per_tick == strike.turn_rate_deg_per_tick
    assert bomber.yaw_rate_deg_per_tick == bomber.turn_rate_deg_per_tick


def test_catalog_matches_design_doc_section_5_2_numbers():
    """Lock design-doc §5.2 table unless intentionally retuned."""
    expected = {
        "INTERCEPTOR": (45.0, 20.0, 60.0, 35.0),
        "STRIKE_FIGHTER": (25.0, 12.0, 40.0, 30.0),
        "HEAVY_BOMBER": (12.0, 6.0, 20.0, 20.0),
    }
    for name, (yaw, pitch_rate, roll_rate, pitch_limit) in expected.items():
        spec = config.JET_FRAME_CATALOG[name]
        assert spec.turn_rate_deg_per_tick == yaw
        assert spec.pitch_rate_deg_per_tick == pitch_rate
        assert spec.roll_rate_deg_per_tick == roll_rate
        assert spec.pitch_limit_deg == pitch_limit
        assert spec.roll_limit_deg == 180.0


def test_poc_jets_spawn_wings_level():
    model = WraithNetModel()
    broker = MessageBroker()
    for agent_id, jet in model.jets.items():
        assert jet.pitch_deg == 0.0
        assert jet.roll_deg == 0.0
        obs = model.build_observation(agent_id, broker)
        assert obs.current_pitch_deg == pytest.approx(0.0)
        assert obs.current_roll_deg == pytest.approx(0.0)


def test_action_intent_accepts_optional_desired_pitch_and_roll():
    without = ActionIntent(agent_id="Jet_1", tick=0, action_type="IDLE")
    assert without.desired_pitch_deg is None
    assert without.desired_roll_deg is None

    with_att = ActionIntent(
        agent_id="Jet_1",
        tick=0,
        action_type="IDLE",
        desired_pitch_deg=-10.0,
        desired_roll_deg=45.0,
    )
    assert with_att.desired_pitch_deg == pytest.approx(-10.0)
    assert with_att.desired_roll_deg == pytest.approx(45.0)


def test_observation_defaults_wings_level_when_fields_omitted():
    obs = Observation(
        agent_id="Jet_1",
        tick=0,
        current_position=(0.0, 0.0),
        sensor_radius=65.0,
        payload={"JDAM": 1},
        known_targets=[],
    )
    assert obs.current_pitch_deg == pytest.approx(0.0)
    assert obs.current_roll_deg == pytest.approx(0.0)
    assert obs.current_altitude == pytest.approx(
        config.JET_FRAME_CATALOG["STRIKE_FIGHTER"].cruise_altitude
    )


def test_normalize_roll_and_clamp_pitch_helpers():
    assert normalize_roll_deg(0.0) == pytest.approx(0.0)
    # ±180 wrap to -180 under [-180, 180) (same formula as shortest_signed_angle).
    assert normalize_roll_deg(180.0) == pytest.approx(-180.0)
    assert normalize_roll_deg(-180.0) == pytest.approx(-180.0)
    assert normalize_roll_deg(270.0) == pytest.approx(-90.0)
    assert normalize_roll_deg(-270.0) == pytest.approx(90.0)

    assert clamp_pitch_deg(10.0, 30.0) == pytest.approx(10.0)
    assert clamp_pitch_deg(40.0, 30.0) == pytest.approx(30.0)
    assert clamp_pitch_deg(-50.0, 35.0) == pytest.approx(-35.0)

    # Deltas for normalized rolls reuse shortest_signed_angle.
    assert shortest_signed_angle(170.0, -170.0) == pytest.approx(20.0)
