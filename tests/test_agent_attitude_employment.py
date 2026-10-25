"""Phase 53 — agent attack / recovery attitude employment."""

from __future__ import annotations

import math

import pytest

from wraithnet import config
from wraithnet.agents import JetAgentRuntime
from wraithnet.broker import MessageBroker
from wraithnet.geometry import bearing_to
from wraithnet.schemas import ActionIntent, Observation, TargetInfo
from wraithnet.weapons_release import (
    AttitudeCommand,
    needed_attitude_for_release,
    release_attitude_ok,
)
from wraithnet.world import WraithNetModel


def _bunker(**overrides) -> TargetInfo:
    data = {
        "target_id": "Bunker",
        "position": (50.3, 50.0),
        "target_type": "HARDENED_BUNKER",
        "hp_required": 5,
        "hp_remaining": 5,
        "claimed": False,
    }
    data.update(overrides)
    return TargetInfo(**data)


def _obs(
    *,
    position: tuple[float, float],
    heading: float,
    payload: dict[str, int],
    targets: list[TargetInfo],
    pitch_deg: float = 0.0,
    roll_deg: float = 0.0,
    frame_type: str = "STRIKE_FIGHTER",
    agent_id: str = "Bomber",
) -> Observation:
    return Observation(
        agent_id=agent_id,
        tick=0,
        current_position=position,
        sensor_radius=config.SENSOR_RADIUS,
        payload=payload,
        known_targets=targets,
        current_heading=heading,
        current_pitch_deg=pitch_deg,
        current_roll_deg=roll_deg,
        frame_type=frame_type,  # type: ignore[arg-type]
        current_altitude=config.JET_FRAME_CATALOG[frame_type].cruise_altitude,  # type: ignore[index]
    )


def test_needed_attitude_for_release_commands_wings_level():
    jdam = config.WEAPON_CATALOG["JDAM"]
    cmd = needed_attitude_for_release(jdam, current_pitch_deg=25.0, current_roll_deg=170.0)
    assert isinstance(cmd, AttitudeCommand)
    assert cmd.desired_roll_deg == pytest.approx(0.0)
    assert abs(cmd.desired_pitch_deg) <= jdam.max_release_abs_pitch_deg
    ok, _ = release_attitude_ok(
        jdam, pitch_deg=cmd.desired_pitch_deg, roll_deg=cmd.desired_roll_deg
    )
    assert ok is True


def test_inverted_jdam_in_range_does_not_strike_rolls_upright():
    broker = MessageBroker()
    jet = JetAgentRuntime("Bomber", broker)
    target = _bunker()
    jet.set_observation(
        _obs(
            position=(50.0, 50.0),
            heading=90.0,
            payload={"JDAM": 2},
            targets=[target],
            pitch_deg=0.0,
            roll_deg=170.0,
        )
    )
    jet.refresh_world_view()
    jet._commit("Bunker", 2, "JDAM")
    payload_before = jet.committed_payload
    intent = jet.decide_intent()
    assert intent.action_type == "IDLE"
    assert intent.action_type != "STRIKE"
    assert intent.desired_roll_deg is not None
    assert abs(intent.desired_roll_deg) < abs(170.0)
    assert intent.desired_roll_deg == pytest.approx(0.0)
    assert intent.desired_pitch_deg == pytest.approx(0.0)
    assert intent.desired_heading is not None
    assert jet.committed_payload == payload_before


def test_wings_level_legal_envelope_strikes():
    broker = MessageBroker()
    jet = JetAgentRuntime("Bomber", broker)
    target = _bunker()
    jet.set_observation(
        _obs(
            position=(50.0, 50.0),
            heading=90.0,
            payload={"JDAM": 2},
            targets=[target],
            pitch_deg=0.0,
            roll_deg=0.0,
        ).model_copy(update={"current_altitude": 8.0})  # JDAM releases at or below max_release_altitude
    )
    jet.refresh_world_view()
    jet._commit("Bunker", 2, "JDAM")
    intent = jet.decide_intent()
    assert intent.action_type == "STRIKE"
    assert intent.target_id == "Bunker"
    assert intent.weapon_type == "JDAM"
    assert jet.committed_payload == 1


def test_closing_from_long_range_moves_toward_target():
    broker = MessageBroker()
    jet = JetAgentRuntime("Bomber", broker)
    target = _bunker(position=(75.0, 75.0), target_id="Target_A")
    jet.set_observation(
        _obs(
            position=(10.0, 10.0),
            heading=0.0,
            payload={"JDAM": 1},
            targets=[target],
            pitch_deg=5.0,
            roll_deg=20.0,
        )
    )
    jet.refresh_world_view()
    jet._commit("Target_A", 1, "JDAM")
    intent = jet.decide_intent()
    assert intent.action_type == "MOVE"
    assert intent.target_position is not None
    assert intent.desired_heading == pytest.approx(45.0)
    assert intent.desired_pitch_deg == pytest.approx(0.0)
    cruise = config.JET_FRAME_CATALOG["STRIKE_FIGHTER"].cruise_altitude
    assert intent.desired_altitude == pytest.approx(cruise)


def test_rtb_commands_level_pitch_and_cruise_altitude():
    broker = MessageBroker()
    jet = JetAgentRuntime("Bomber", broker)
    home = (5.0, 5.0)
    cruise = config.JET_FRAME_CATALOG["HEAVY_BOMBER"].cruise_altitude
    jet.set_observation(
        Observation(
            agent_id="Bomber",
            tick=1,
            current_position=(40.0, 40.0),
            sensor_radius=config.SENSOR_RADIUS,
            payload={"JDAM": 0},
            known_targets=[],
            home_base=home,
            frame_type="HEAVY_BOMBER",
            current_altitude=cruise,
        )
    )
    jet.role = "RTB"
    intent = jet.decide_intent()
    assert intent.action_type == "MOVE"
    assert intent.target_position == home
    assert intent.desired_pitch_deg == pytest.approx(0.0)
    assert intent.desired_altitude == pytest.approx(cruise)


def test_notch_clears_extreme_roll_command():
    """When NOTCH attaches, pitch is leveled and roll intent is cleared."""
    from wraithnet.schemas import InboundThreat

    broker = MessageBroker()
    jet = JetAgentRuntime("Bomber", broker)
    # Bomber evasion is low enough that imminent radar threats force NOTCH.
    assert (
        config.JET_FRAME_CATALOG["HEAVY_BOMBER"].evasion_bonus
        < config.WEAPON_CATALOG["SAM_RADAR"].damage_per_hit
    )
    target = _bunker()
    obs = _obs(
        position=(50.0, 50.0),
        heading=90.0,
        payload={"JDAM": 1},
        targets=[target],
        pitch_deg=20.0,
        roll_deg=170.0,
        frame_type="HEAVY_BOMBER",
    )
    obs = obs.model_copy(
        update={
            "inbound_threats": [
                InboundThreat(
                    missile_id="M-rad",
                    guidance="RADAR",
                    estimated_bearing=0.0,
                    estimated_time_to_impact=1.0,
                )
            ],
            "radar_warning_receiver": True,
        }
    )
    jet.set_observation(obs)
    jet.refresh_world_view()
    jet._commit("Bunker", 1, "JDAM")
    intent = jet.decide_intent()
    assert intent.maneuver_defense == "NOTCH"
    assert intent.desired_pitch_deg == pytest.approx(0.0)
    assert intent.desired_roll_deg is None
    assert intent.action_type != "STRIKE"


def test_inverted_world_integration_rolls_then_can_release():
    """Deterministic: inverted jet near target rolls upright then STRIKE lands."""
    model = WraithNetModel()
    jet = model.jets["Jet_2"]
    target = model.targets["Target_A"]
    model.space.move_agent(jet, target.position)
    jet.heading = 90.0
    jet.desired_heading = 90.0
    jet.pitch_deg = 0.0
    jet.roll_deg = 170.0
    jet.desired_roll_deg = None
    jet.altitude = 10.0  # JDAM releases at or below max_release_altitude
    jet.desired_altitude = 10.0
    jdam_before = jet.payload["JDAM"]
    hp_before = target.hp_remaining

    # Drive attitude via agent-style intents for several ticks. The jet keeps
    # airspeed while rolling (no hover), so re-close on the target like
    # decide_intent does before releasing inside JDAM range.
    for tick in range(80):
        ok, _ = release_attitude_ok(
            config.WEAPON_CATALOG["JDAM"],
            pitch_deg=jet.pitch_deg,
            roll_deg=jet.roll_deg,
        )
        dist = math.dist(tuple(jet.pos), target.position)
        if ok and dist > config.WEAPON_CATALOG["JDAM"].range:
            model.step(
                {
                    "Jet_2": ActionIntent(
                        agent_id="Jet_2",
                        tick=tick,
                        action_type="MOVE",
                        target_position=target.position,
                        desired_heading=bearing_to(tuple(jet.pos), target.position),
                        desired_pitch_deg=0.0,
                        desired_roll_deg=0.0,
                    )
                }
            )
            continue
        if ok:
            model.step(
                {
                    "Jet_2": ActionIntent(
                        agent_id="Jet_2",
                        tick=tick,
                        action_type="STRIKE",
                        target_id="Target_A",
                        weapon_type="JDAM",
                        desired_heading=90.0,
                        desired_pitch_deg=0.0,
                        desired_roll_deg=0.0,
                    )
                }
            )
            break
        model.step(
            {
                "Jet_2": ActionIntent(
                    agent_id="Jet_2",
                    tick=tick,
                    action_type="IDLE",
                    desired_heading=90.0,
                    desired_pitch_deg=0.0,
                    desired_roll_deg=0.0,
                )
            }
        )
    else:
        pytest.fail("jet never reached legal JDAM release attitude")

    assert abs(jet.roll_deg) <= config.WEAPON_CATALOG["JDAM"].max_release_abs_roll_deg
    assert jet.payload["JDAM"] == jdam_before - 1
    assert target.hp_remaining == hp_before - config.WEAPON_CATALOG["JDAM"].damage_per_hit
