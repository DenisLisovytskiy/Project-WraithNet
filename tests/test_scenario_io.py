"""Load/save tests for scenario JSON IO."""

from pathlib import Path

from wraithnet.scenario_io import load_scenario, save_scenario
from wraithnet.scenario_schema import _default_scenario

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCENARIO_PATH = REPO_ROOT / "scenarios" / "default.json"


def test_save_then_load_round_trips_losslessly(tmp_path):
    original = _default_scenario()
    path = tmp_path / "nested" / "scenario.json"
    save_scenario(original, path)
    loaded = load_scenario(path)
    assert loaded == original
    assert loaded.model_dump() == original.model_dump()


def test_load_committed_default_json_matches_builtin_scenario():
    loaded = load_scenario(DEFAULT_SCENARIO_PATH)
    default = _default_scenario()
    assert loaded.model_dump() == default.model_dump()
    assert loaded == default
