"""Phase 55 — track-level acceptance for docs/22 §11 (Phases 50–55).

Automates the design-doc Definition of Done items that do not require a
display. Manual smoke (launcher bank/pitch, bomb pass, F3 attitude) remains
a PR / operator checklist item — see docs/22 §11 item 6 note.
"""

from __future__ import annotations

import asyncio
import math

import pytest

from viewer3d.motion import TickBuffer
from wraithnet import config
from wraithnet.bridge import WraithNetBridge
from wraithnet.metrics import summarize
from wraithnet.scenario_schema import JetSpec, TargetSpec, new_scenario_data, scenario_from_data
from wraithnet.schemas import ActionIntent
from wraithnet.trace_builders import build_trace_tick
from wraithnet.weapons_release import release_attitude_ok
from wraithnet.world import WraithNetModel


@pytest.fixture
def fast_cnp(monkeypatch):
    monkeypatch.setattr(config, "BID_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(config, "MAX_TICK_WAIT_SECONDS", 1.0)


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


# --- §11.1 Per-frame sharpness ---


def test_acceptance_per_frame_sharpness_inequalities():
    """INTERCEPTOR rates > STRIKE_FIGHTER > HEAVY_BOMBER on yaw/pitch/roll."""
    interceptor = config.JET_FRAME_CATALOG["INTERCEPTOR"]
    strike = config.JET_FRAME_CATALOG["STRIKE_FIGHTER"]
    bomber = config.JET_FRAME_CATALOG["HEAVY_BOMBER"]
    for axis in (
        "yaw_rate_deg_per_tick",
        "pitch_rate_deg_per_tick",
        "roll_rate_deg_per_tick",
    ):
        i = getattr(interceptor, axis)
        s = getattr(strike, axis)
        b = getattr(bomber, axis)
        assert i > s > b, f"{axis}: {i} > {s} > {b} failed"


# --- §11.2 3D pose on living jets ---


def test_acceptance_living_jets_expose_full_attitude_pose():
    model = WraithNetModel()
    for jet in model.jets.values():
        assert hasattr(jet, "heading")
        assert hasattr(jet, "pitch_deg")
        assert hasattr(jet, "roll_deg")
        assert hasattr(jet, "altitude")
        assert jet.pitch_deg == pytest.approx(0.0)
        assert jet.roll_deg == pytest.approx(0.0)

    jet = model.jets["Jet_1"]
    frame = config.JET_FRAME_CATALOG[jet.frame_type]
    start = (jet.heading, jet.pitch_deg, jet.roll_deg, jet.altitude)
    model.step(
        {
            "Jet_1": ActionIntent(
                agent_id="Jet_1",
                tick=0,
                action_type="IDLE",
                desired_heading=(jet.heading + 90.0) % 360.0,
                desired_pitch_deg=frame.pitch_limit_deg,
                desired_roll_deg=45.0,
            )
        }
    )
    assert (jet.heading, jet.pitch_deg, jet.roll_deg, jet.altitude) != start
    assert jet.pitch_deg != 0.0
    assert jet.roll_deg != 0.0


# --- §11.3 Pitch → altitude coupling ---


def test_acceptance_pitch_changes_altitude_for_strike_fighter():
    model = _single_jet_model("STRIKE_FIGHTER")
    jet = model.jets["Jet_A"]
    frame = config.JET_FRAME_CATALOG["STRIKE_FIGHTER"]
    start_alt = jet.altitude
    for _ in range(8):
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


# --- §11.4 Bomb gate: inverted reject → upright accept ---


def test_acceptance_jdam_inverted_reject_then_upright_accept():
    model = WraithNetModel()
    jet = model.jets["Jet_2"]
    target = model.targets["Target_A"]
    model.space.move_agent(jet, target.position)
    jet.heading = 90.0
    jet.desired_heading = 90.0
    jet.pitch_deg = 0.0
    jet.roll_deg = 170.0
    jdam_before = jet.payload["JDAM"]
    hp_before = target.hp_remaining

    model._apply_strike(jet, "Target_A", "JDAM")
    assert jet.payload["JDAM"] == jdam_before
    assert target.hp_remaining == hp_before
    assert model.strike_log == []

    for tick in range(40):
        ok, _ = release_attitude_ok(
            config.WEAPON_CATALOG["JDAM"],
            pitch_deg=jet.pitch_deg,
            roll_deg=jet.roll_deg,
        )
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
    assert target.hp_remaining == hp_before - 1


# --- §11.5 AAM vs JDAM contrast under extreme roll ---


def test_acceptance_aam_launchable_where_jdam_rejected():
    model = WraithNetModel()
    jet = model.jets["Jet_3"]
    jet.payload = {"AIM9X": 1, "JDAM": 1, "AGM65": 0}
    jet.roll_deg = 120.0
    jet.pitch_deg = 0.0

    assert model._jet_release_attitude_reason(jet, "JDAM") == (
        "jet_inverted_release_forbidden"
    )
    assert model._jet_release_attitude_reason(jet, "AIM9X") is None

    aim_ack = model.try_launch_from_jet(jet, "Target_A", "AIM9X")
    assert aim_ack.reason not in {
        "jet_inverted_release_forbidden",
        "outside_release_attitude_roll",
        "outside_release_attitude_pitch",
    }


# --- §11.6 Trace → motion sample keeps pitch/roll (viewer parity wire path) ---


def test_acceptance_trace_builder_to_motion_preserves_attitude():
    class _Bridge:
        def __init__(self, model: WraithNetModel) -> None:
            self.model = model
            self.agents: dict = {}
            self.broker = type("B", (), {"is_claimed": staticmethod(lambda _tid: False)})()

    model = WraithNetModel()
    jet = model.jets["Jet_1"]
    jet.pitch_deg = -15.0
    jet.roll_deg = 42.0
    tick = build_trace_tick(_Bridge(model))
    frame = next(j for j in tick.jets if j.agent_id == "Jet_1")
    assert frame.pitch_deg == pytest.approx(-15.0)
    assert frame.roll_deg == pytest.approx(42.0)

    buf = TickBuffer(interp_delay_s=0.0)
    buf.push(tick, t_accept=0.0)
    buf.set_presentation_time(0.0)
    pose = buf.sample_jet_poses()["Jet_1"]
    assert pose.pitch_deg == pytest.approx(-15.0)
    assert pose.roll_deg == pytest.approx(42.0)


# --- §11.7 Wings-level PoC still solves (no deferred golden TODOs) ---


@pytest.mark.asyncio
async def test_acceptance_poc_wings_level_mission_still_solves(fast_cnp):
    """Phase 55: fixed PoC remains solvable under attitude defaults.

    No summarize golden drift refresh was required — wings-level cruise
    defaults keep the Phase 5/23 PoC contract intact (docs/22 §11.7).
    """
    bridge = WraithNetBridge()
    for jet in bridge.model.jets.values():
        assert jet.pitch_deg == pytest.approx(0.0)
        assert jet.roll_deg == pytest.approx(0.0)

    await asyncio.wait_for(bridge.run(max_ticks=500), timeout=60)
    summary = summarize(bridge.model)

    assert summary["solved"] is True
    assert summary["swarm_wiped_out"] is False
    assert summary["targets_destroyed"] == 3
    assert bridge.model.tick < 500
