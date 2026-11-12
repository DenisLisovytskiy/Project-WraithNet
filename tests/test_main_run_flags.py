"""Phase 57 Part A: generate-from-seed, tick cap, no-trace, summary JSON, outcome."""

import asyncio
import json

import pytest

from wraithnet.bridge import WraithNetBridge
from wraithnet.main import main, parse_args
from wraithnet.metrics import classify_outcome
from wraithnet.scenario_io import save_scenario
from wraithnet.scenario_schema import new_scenario_data, scenario_from_data


def _solo_scenario(bunker_at: tuple[float, float]):
    return scenario_from_data(new_scenario_data(
        jets=[
            {
                "agent_id": "Solo_Jet",
                "position": (20.0, 20.0),
                "payload": {"JDAM": 1, "AIM9X": 0},
            }
        ],
        targets=[
            {
                "target_id": "Bunker",
                "position": bunker_at,
                "hp_required": 1,
                "target_type": "HARDENED_BUNKER",
            }
        ],
    ))


NEAR = (20.5, 20.0)
FAR = (90.0, 90.0)


def test_generate_and_scenario_are_mutually_exclusive():
    with pytest.raises(SystemExit):
        parse_args(["--generate", "TRAINING", "--seed", "1", "--scenario", "x.json"])


def test_generate_requires_seed_and_seed_requires_generate():
    with pytest.raises(SystemExit):
        parse_args(["--generate", "TRAINING"])
    with pytest.raises(SystemExit):
        parse_args(["--seed", "3"])
    args = parse_args(["--generate", "TRAINING", "--seed", "3"])
    assert (args.generate, args.seed, args.max_ticks) == ("TRAINING", 3, 1000)


def test_no_trace_writes_no_trace_and_summary_json_round_trips(tmp_path):
    scenario_path = tmp_path / "solo.json"
    save_scenario(_solo_scenario(NEAR), scenario_path)
    out_dir = tmp_path / "out"
    summary_path = tmp_path / "summary.json"

    main([
        "--scenario", str(scenario_path),
        "--output-dir", str(out_dir),
        "--no-trace",
        "--summary-json", str(summary_path),
    ])

    assert not (out_dir / "trace.jsonl").exists()
    assert not (out_dir / "model_vars.csv").exists()
    record = json.loads(summary_path.read_text(encoding="utf-8"))
    assert record["outcome"] == "SOLVED"
    assert record["tick_cap_hit"] is False
    assert record["max_ticks"] == 1000
    assert record["solved"] is True
    assert record["feasible"] is True
    assert record["preset"] is None and record["generation_seed"] is None
    assert len(record["scenario_hash"]) == 16
    assert record["wall_time_s"] >= 0.0


def test_generated_run_records_preset_seed_and_hits_the_tick_cap(tmp_path):
    summary_path = tmp_path / "summary.json"
    main([
        "--generate", "TRAINING", "--seed", "0",
        "--max-ticks", "3",
        "--no-trace",
        "--summary-json", str(summary_path),
    ])
    record = json.loads(summary_path.read_text(encoding="utf-8"))
    assert record["preset"] == "TRAINING"
    assert record["generation_seed"] == 0
    assert record["ticks_elapsed"] == 3
    assert record["outcome"] == "TICK_CAP"
    assert record["tick_cap_hit"] is True
    assert record["solved"] is False


def _run(bridge: WraithNetBridge, max_ticks: int) -> str:
    asyncio.run(bridge.run(max_ticks=max_ticks))
    return classify_outcome(bridge.model)


def test_outcome_solved():
    assert _run(WraithNetBridge(scenario=_solo_scenario(NEAR)), 50) == "SOLVED"


def test_outcome_tick_cap_is_not_reported_as_a_failure():
    bridge = WraithNetBridge(scenario=_solo_scenario(FAR))
    assert _run(bridge, 2) == "TICK_CAP"
    assert bridge.model.running is True


def test_outcome_wiped_when_the_only_jet_runs_dry():
    bridge = WraithNetBridge(scenario=_solo_scenario(FAR))
    bridge.model.jets["Solo_Jet"].fuel = 1.0
    assert _run(bridge, 50) == "WIPED"


def test_outcome_aborted_when_the_swarm_goes_home_unfinished():
    bridge = WraithNetBridge(scenario=_solo_scenario(FAR))
    bridge.agents["Solo_Jet"].begin_rtb()
    bridge.model.jets["Solo_Jet"].is_rtb = True
    assert _run(bridge, 100) == "ABORTED"
    assert bridge.model.targets["Bunker"].hp_remaining == 1
