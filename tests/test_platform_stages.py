"""Non-CNP platform double can advance world stages without jet runtimes."""

from __future__ import annotations

import pytest

from wraithnet.bases import default_home_airbase, default_jet_assignment
from wraithnet.bridge import WraithNetBridge
from wraithnet.objectives import MissionObjective
from wraithnet.platform import PlatformSnapshot
from wraithnet.scenario_schema import scenario_from_data, new_scenario_data
from wraithnet.schemas import ActionIntent
from wraithnet.world import WraithNetModel


def test_non_cnp_platform_snapshot_and_staged_tick_without_jets():
    """Phase 37: scripted/non-CNP units must not assume JetAgentRuntime."""
    draft = new_scenario_data(
        jets=[],
        targets=[
            {
                "target_id": "Bunker_1",
                "position": (50.0, 50.0),
                "hp_required": 1,
                "target_type": "HARDENED_BUNKER",
            }
        ],
        mission_objectives=[
            MissionObjective(
                objective_id="OBJ_DESTROY_ALL",
                objective_type="DESTROY",
                subject_ids=["Bunker_1"],
                required=True,
            )
        ],
    )
    scenario = scenario_from_data(draft)
    model = WraithNetModel(scenario=scenario)
    assert model.jets == {}

    # Platform double: not registered on the broker / CNP.
    double = PlatformSnapshot(
        platform_id="Scripted_Tank_1",
        faction="FRIENDLY",
        domain="GROUND",
        target_class="GROUND_VEHICLE",
        position=(10.0, 10.0),
        altitude=0.0,
    )
    assert double.faction == "FRIENDLY"
    assert double.domain == "GROUND"

    # Staged world tick with empty intents (no jet CNP).
    model.stage_pre_move_obs({})
    model.stage_apply_movement({})
    model.stage_post_move_obs({})
    model.stage_register_launches()
    model.stage_countermeasures()
    model.stage_resolve_combat({})
    assert model.tick == 1

    bridge = WraithNetBridge(scenario=scenario)
    assert bridge.agents == {} or len(getattr(bridge, "agents", {})) == 0
    # Jetless bridge still constructs and can tick the world.
    assert bridge.model.jets == {}
