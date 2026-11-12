"""End-to-end scenario generation + bridge integration (Phase 32)."""

from __future__ import annotations


import pytest

from wraithnet import config
from wraithnet.bridge import WraithNetBridge
from wraithnet.generation_presets import parameters_for_preset
from wraithnet.metrics import classify_outcome, summarize
from wraithnet.scenario_generator import generate_scenario
from wraithnet.scenario_schema import ScenarioConfig


@pytest.mark.asyncio
async def test_generation_result_runs_through_bridge_without_special_casing():
    # Compact map keeps transit short so 400 ticks remain meaningful in CI.
    result = generate_scenario(
        parameters_for_preset(
            "TRAINING",
            seed=7,
            overrides={"grid_width": 100.0, "grid_height": 100.0},
        )
    )
    scenario: ScenarioConfig = result.scenario
    # Bridge must accept an ordinary ScenarioConfig; generation metadata is ignored.
    bridge = WraithNetBridge(scenario=scenario)
    await bridge.run(max_ticks=400)
    summary = summarize(bridge.model)
    assert summary["total_targets"] == len(scenario.targets) + len(scenario.sam_sites)
    assert classify_outcome(bridge.model) == "SOLVED"
    assert summary["targets_destroyed"] == summary["total_targets"]


@pytest.mark.asyncio
async def test_auto_balanced_training_often_progresses():
    result = generate_scenario(
        parameters_for_preset(
            "TRAINING",
            seed=0,
            overrides={"grid_width": 100.0, "grid_height": 100.0},
        )
    )
    assert result.feasibility.feasible is True
    bridge = WraithNetBridge(scenario=result.scenario)
    await bridge.run(max_ticks=400)
    summary = summarize(bridge.model)
    assert classify_outcome(bridge.model) == "SOLVED"
    assert summary["targets_destroyed"] == summary["total_targets"]
    assert summary["jets_destroyed"] == 0
