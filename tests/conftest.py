"""Shared test builders and fixtures.

Builders are plain functions, imported by name (``from tests.conftest import
...``), so a test file reads the same as when each file kept its own copy.
"""

from __future__ import annotations

import types

import pytest

from wraithnet import config
from wraithnet.agents import JetAgentRuntime
from wraithnet.scenario_schema import (
    JetSpec,
    ScenarioConfig,
    TargetSpec,
    new_scenario_data,
    scenario_from_data,
)
from wraithnet.schemas import Observation, TargetInfo


def target_info(**overrides) -> TargetInfo:
    """A live, unclaimed 5-HP bunker at (75, 75)."""
    data = {
        "target_id": "Target_A",
        "position": (75.0, 75.0),
        "target_type": "HARDENED_BUNKER",
        "hp_required": 5,
        "hp_remaining": 5,
        "claimed": False,
    }
    data.update(overrides)
    return TargetInfo(**data)


def observation(
    agent_id: str,
    position: tuple[float, float],
    payload: dict[str, int],
    targets: list[TargetInfo],
    tick: int = 0,
    comms_jammed: bool = False,
) -> Observation:
    return Observation(
        agent_id=agent_id,
        tick=tick,
        current_position=position,
        sensor_radius=config.SENSOR_RADIUS,
        payload=payload,
        known_targets=targets,
        comms_jammed=comms_jammed,
    )


def set_observation(jet: JetAgentRuntime, obs: Observation) -> None:
    """set_observation + refresh_world_view so effective_known_targets is filled."""
    jet.set_observation(obs)
    jet.refresh_world_view()


def tiny_scenario() -> ScenarioConfig:
    """One JDAM jet half a unit from a 1-HP bunker: solves in about 25 ticks."""
    return scenario_from_data(new_scenario_data(
        jets=[
            JetSpec(
                agent_id="Solo_Jet",
                position=(20.0, 20.0),
                payload={"JDAM": 1, "AIM9X": 0},
            )
        ],
        targets=[
            TargetSpec(
                target_id="Near_Bunker",
                position=(20.5, 20.0),
                hp_required=1,
                target_type="HARDENED_BUNKER",
            )
        ],
    ))


# Upper bound for a wait on another thread or OS process (a socket, a
# launched simulator). It only fires if the code under test hangs; what the
# test asserts never depends on how long the wait took.
HANG_GUARD_S = 60.0


@pytest.fixture
def panda_fake_clock(monkeypatch):
    """Make viewer time a pure function of frames stepped.

    The Panda3D global clock runs in non-real-time mode, so every
    ``task_mgr.step()`` advances intervals and ``dt`` by exactly 1/20 s, and
    ``viewer3d.app`` reads that same clock instead of ``time.monotonic``.
    Yields the frame rate.
    """
    from panda3d.core import ClockObject

    import viewer3d.app as app_module

    fps = 20.0
    clock = ClockObject.get_global_clock()
    mode = clock.get_mode()
    clock.set_mode(ClockObject.M_non_real_time)
    clock.set_frame_rate(fps)
    monkeypatch.setattr(
        app_module, "time", types.SimpleNamespace(monotonic=clock.get_frame_time)
    )
    try:
        yield fps
    finally:
        clock.set_mode(mode)
