"""Phase 51 — authoritative pitch/roll/yaw movement integration."""

from __future__ import annotations

import math

import pytest

from wraithnet import config
from wraithnet.scenario_schema import JetSpec, TargetSpec, new_scenario_data, scenario_from_data
from wraithnet.schemas import ActionIntent
from wraithnet.world import WraithNetModel


def _single_jet_model(
    frame_type: config.JetFrameType,
    *,
    agent_id: str = "Jet_A",
    position: tuple[float, float] = (50.0, 50.0),
    heading: float = 0.0,
) -> WraithNetModel:
    custom = scenario_from_data(
        new_scenario_data(
            jets=[
                JetSpec(
                    agent_id=agent_id,
                    position=position,
                    payload={"JDAM": 1, "AIM9X": 0},
                    frame_type=frame_type,
                    initial_heading=heading,
                )
            ],
            targets=[
                TargetSpec(
                    target_id="Bunker",
                    position=(80.0, 80.0),
                    hp_required=1,
                    target_type="HARDENED_BUNKER",
                )
            ],
        )
    )
    return WraithNetModel(scenario=custom)


def _forward_move(
    agent_id: str,
    pos: tuple[float, float],
    heading: float,
    speed: float,
    **kwargs,
) -> ActionIntent:
    h = math.radians(heading)
    dest = (pos[0] + speed * math.sin(h), pos[1] + speed * math.cos(h))
    return ActionIntent(
        agent_id=agent_id,
        tick=0,
        action_type="MOVE",
        target_position=dest,
        **kwargs,
    )


def test_positive_pitch_increases_altitude_over_ticks():
    model = _single_jet_model("STRIKE_FIGHTER")
    jet = model.jets["Jet_A"]
    frame = config.JET_FRAME_CATALOG["STRIKE_FIGHTER"]
    start_alt = jet.altitude
    for _ in range(5):
        model.step(
            {
                "Jet_A": _forward_move(
                    "Jet_A",
                    jet.pos,
                    jet.heading,
                    frame.max_speed,
                    desired_pitch_deg=frame.pitch_limit_deg,
                )
            }
        )
    assert jet.pitch_deg > 0.0
    assert jet.altitude > start_alt


def test_negative_pitch_descends_but_not_below_min_combat():
    model = _single_jet_model("STRIKE_FIGHTER")
    jet = model.jets["Jet_A"]
    frame = config.JET_FRAME_CATALOG["STRIKE_FIGHTER"]
    for _ in range(20):
        model.step(
            {
                "Jet_A": _forward_move(
                    "Jet_A",
                    jet.pos,
                    jet.heading,
                    frame.max_speed,
                    desired_pitch_deg=-frame.pitch_limit_deg,
                )
            }
        )
    assert jet.pitch_deg < 0.0
    assert jet.altitude >= frame.min_combat_altitude
    assert jet.altitude == pytest.approx(frame.min_combat_altitude)


def test_pitch_rate_interceptor_faster_than_bomber():
    intercept = _single_jet_model("INTERCEPTOR", agent_id="Fast")
    bomber = _single_jet_model("HEAVY_BOMBER", agent_id="Slow")
    i_jet = intercept.jets["Fast"]
    b_jet = bomber.jets["Slow"]
    cmd = 35.0
    intercept.stage_pre_move_obs(
        {
            "Fast": ActionIntent(
                agent_id="Fast",
                tick=0,
                action_type="IDLE",
                desired_pitch_deg=cmd,
            )
        }
    )
    bomber.stage_pre_move_obs(
        {
            "Slow": ActionIntent(
                agent_id="Slow",
                tick=0,
                action_type="IDLE",
                desired_pitch_deg=cmd,
            )
        }
    )
    i_rate = config.JET_FRAME_CATALOG["INTERCEPTOR"].pitch_rate_deg_per_tick
    b_rate = config.JET_FRAME_CATALOG["HEAVY_BOMBER"].pitch_rate_deg_per_tick
    assert i_jet.pitch_deg == pytest.approx(i_rate)
    assert b_jet.pitch_deg == pytest.approx(b_rate)
    assert i_jet.pitch_deg > b_jet.pitch_deg

def test_pitch_rate_large_command_not_instant():
    model = _single_jet_model("STRIKE_FIGHTER")
    jet = model.jets["Jet_A"]
    frame = config.JET_FRAME_CATALOG["STRIKE_FIGHTER"]
    target = frame.pitch_limit_deg
    assert target > frame.pitch_rate_deg_per_tick
    model.stage_pre_move_obs(
        {
            "Jet_A": ActionIntent(
                agent_id="Jet_A",
                tick=0,
                action_type="IDLE",
                desired_pitch_deg=target,
            )
        }
    )
    assert jet.pitch_deg == pytest.approx(frame.pitch_rate_deg_per_tick)
    assert jet.pitch_deg < target


def test_roll_rate_interceptor_faster_than_bomber():
    intercept = _single_jet_model("INTERCEPTOR", agent_id="Fast")
    bomber = _single_jet_model("HEAVY_BOMBER", agent_id="Slow")
    cmd = 90.0
    intercept.stage_pre_move_obs(
        {
            "Fast": ActionIntent(
                agent_id="Fast",
                tick=0,
                action_type="IDLE",
                desired_roll_deg=cmd,
            )
        }
    )
    bomber.stage_pre_move_obs(
        {
            "Slow": ActionIntent(
                agent_id="Slow",
                tick=0,
                action_type="IDLE",
                desired_roll_deg=cmd,
            )
        }
    )
    assert intercept.jets["Fast"].roll_deg == pytest.approx(
        config.JET_FRAME_CATALOG["INTERCEPTOR"].roll_rate_deg_per_tick
    )
    assert bomber.jets["Slow"].roll_deg == pytest.approx(
        config.JET_FRAME_CATALOG["HEAVY_BOMBER"].roll_rate_deg_per_tick
    )
    assert intercept.jets["Fast"].roll_deg > bomber.jets["Slow"].roll_deg


def test_explicit_desired_roll_overrides_bank_assist():
    model = _single_jet_model("STRIKE_FIGHTER")
    jet = model.jets["Jet_A"]
    jet.heading = 0.0
    # Yaw hard right while commanding roll left — assist would bank right.
    model.stage_pre_move_obs(
        {
            "Jet_A": ActionIntent(
                agent_id="Jet_A",
                tick=0,
                action_type="IDLE",
                desired_heading=90.0,
                desired_roll_deg=-40.0,
            )
        }
    )
    assert jet.roll_deg < 0.0
    assert jet.heading > 0.0


def test_bank_assist_rolls_with_yaw_when_roll_unset():
    model = _single_jet_model("STRIKE_FIGHTER")
    jet = model.jets["Jet_A"]
    jet.heading = 0.0
    model.stage_pre_move_obs(
        {
            "Jet_A": ActionIntent(
                agent_id="Jet_A",
                tick=0,
                action_type="IDLE",
                desired_heading=90.0,
            )
        }
    )
    # Positive yaw step → positive assisted roll (roll right).
    assert jet.roll_deg > 0.0
    turn = config.JET_FRAME_CATALOG["STRIKE_FIGHTER"].turn_rate_deg_per_tick
    assert jet.roll_deg == pytest.approx(
        min(
            turn * config.BANK_ASSIST_GAIN,
            config.JET_FRAME_CATALOG["STRIKE_FIGHTER"].roll_rate_deg_per_tick,
        )
    )


def test_wings_level_cruise_preserves_full_ground_speed():
    model = _single_jet_model("STRIKE_FIGHTER", heading=0.0)
    jet = model.jets["Jet_A"]
    frame = config.JET_FRAME_CATALOG["STRIKE_FIGHTER"]
    assert jet.pitch_deg == 0.0
    start = (float(jet.pos[0]), float(jet.pos[1]))
    model.step(
        {
            "Jet_A": _forward_move("Jet_A", start, 0.0, frame.max_speed * 10)
        }
    )
    moved = math.dist(start, jet.pos)
    assert moved == pytest.approx(frame.max_speed)


def test_pitched_cruise_reduces_ground_speed_option_a():
    model = _single_jet_model("STRIKE_FIGHTER", heading=0.0)
    jet = model.jets["Jet_A"]
    frame = config.JET_FRAME_CATALOG["STRIKE_FIGHTER"]
    # Reach a known pitch without translating.
    while jet.pitch_deg < 20.0 - 1e-9:
        model.stage_pre_move_obs(
            {
                "Jet_A": ActionIntent(
                    agent_id="Jet_A",
                    tick=0,
                    action_type="IDLE",
                    desired_pitch_deg=20.0,
                )
            }
        )
        # Complete the tick so stages reset cleanly.
        model.stage_apply_movement({})
        model.stage_post_move_obs({})
        model.stage_register_launches()
        model.stage_countermeasures()
        model.stage_resolve_combat({})
    start = (float(jet.pos[0]), float(jet.pos[1]))
    expected = frame.max_speed * math.cos(math.radians(jet.pitch_deg))
    model.step(
        {
            "Jet_A": _forward_move(
                "Jet_A",
                start,
                jet.heading,
                frame.max_speed * 10,
                desired_pitch_deg=20.0,
            )
        }
    )
    moved = math.dist(start, jet.pos)
    assert moved == pytest.approx(expected, rel=1e-6)


def test_map_edge_clamp_stays_in_bounds():
    model = _single_jet_model(
        "STRIKE_FIGHTER", position=(1.0, 1.0), heading=225.0
    )
    jet = model.jets["Jet_A"]
    frame = config.JET_FRAME_CATALOG["STRIKE_FIGHTER"]
    for _ in range(5):
        model.step(
            {
                "Jet_A": _forward_move(
                    "Jet_A", jet.pos, jet.heading, frame.max_speed * 10
                )
            }
        )
    eps = 1e-6
    assert 0.0 <= jet.pos[0] <= model._scenario.grid_width - eps
    assert 0.0 <= jet.pos[1] <= model._scenario.grid_height - eps


def test_nose_on_map_edge_never_freezes_jet():
    """Flying straight into the wall must not zero airspeed (no hovering)."""
    model = _single_jet_model(
        "STRIKE_FIGHTER", position=(1.0, 10.0), heading=270.0
    )
    jet = model.jets["Jet_A"]
    for _ in range(5):
        before = tuple(jet.pos)
        model.step(
            {"Jet_A": ActionIntent(agent_id="Jet_A", tick=0, action_type="IDLE")}
        )
        assert math.dist(before, tuple(jet.pos)) > 0.5
        assert 0.0 <= jet.pos[0] < model._scenario.grid_width


def test_recovery_eases_altitude_and_levels_pitch():
    model = _single_jet_model("STRIKE_FIGHTER", position=(10.0, 10.0))
    jet = model.jets["Jet_A"]
    frame = config.JET_FRAME_CATALOG["STRIKE_FIGHTER"]
    jet.pitch_deg = 20.0
    jet.desired_pitch_deg = 20.0
    jet.is_rtb = True
    jet.recovery_phase = "APPROACH"
    jet.desired_altitude = config.RECOVERY_ALTITUDE
    start_alt = jet.altitude
    model.step(
        {
            "Jet_A": _forward_move(
                "Jet_A",
                jet.pos,
                jet.heading,
                frame.max_speed,
                desired_pitch_deg=30.0,  # ignored under recovery override
                desired_altitude=config.RECOVERY_ALTITUDE,
            )
        }
    )
    assert jet.pitch_deg < 20.0  # driven toward level
    assert jet.altitude < start_alt  # eased toward recovery altitude
    assert jet.altitude >= config.RECOVERY_ALTITUDE - frame.max_climb_per_tick - 1e-9


def test_combat_pitch_wins_over_desired_altitude():
    model = _single_jet_model("STRIKE_FIGHTER")
    jet = model.jets["Jet_A"]
    frame = config.JET_FRAME_CATALOG["STRIKE_FIGHTER"]
    start_alt = jet.altitude
    # Command nose-down while asking for a higher altitude — pitch wins.
    model.step(
        {
            "Jet_A": _forward_move(
                "Jet_A",
                jet.pos,
                jet.heading,
                frame.max_speed,
                desired_pitch_deg=-frame.pitch_limit_deg,
                desired_altitude=start_alt + 50.0,
            )
        }
    )
    assert jet.altitude < start_alt


def test_weapon_release_pose_exposes_attitude():
    model = _single_jet_model("STRIKE_FIGHTER", heading=90.0)
    jet = model.jets["Jet_A"]
    jet.pitch_deg = 12.0
    jet.roll_deg = -8.0
    model.stage_pre_move_obs({})
    model.stage_apply_movement(
        {
            "Jet_A": _forward_move(
                "Jet_A",
                jet.pos,
                jet.heading,
                config.JET_FRAME_CATALOG["STRIKE_FIGHTER"].max_speed,
            )
        }
    )
    pose = model._weapon_release_pose(jet)
    assert len(pose) == 6
    x, y, alt, heading, pitch, roll = pose
    assert (x, y) == jet._pos_at_move_start
    assert pitch == pytest.approx(12.0)
    assert roll == pytest.approx(-8.0)
    assert model._weapon_release_xy(jet) == (x, y)
