"""CLI tests for python -m wraithnet.main --scenario."""

import subprocess
import sys
from pathlib import Path

from wraithnet.scenario_io import save_scenario
from wraithnet.scenario_schema import ScenarioConfig

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCENARIO_PATH = REPO_ROOT / "scenarios" / "default.json"


def _run_main(*extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "wraithnet.main", *extra],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        timeout=120,
        check=True,
    )


def test_main_with_default_json_matches_main_with_no_flags():
    without_flag = _run_main()
    with_flag = _run_main("--scenario", str(DEFAULT_SCENARIO_PATH))
    assert without_flag.stdout == with_flag.stdout
    assert "Solved:" in without_flag.stdout


def test_main_runs_a_custom_scenario_file(tmp_path):
    custom = ScenarioConfig(
        jets=[
            {
                "agent_id": "Solo_Jet",
                "position": (20.0, 20.0),
                "payload": {"JDAM": 1, "AIM9X": 0},
            }
        ],
        targets=[
            {
                "target_id": "Near_Bunker",
                "position": (20.5, 20.0),
                "hp_required": 1,
                "target_type": "HARDENED_BUNKER",
            }
        ],
    )
    path = tmp_path / "custom.json"
    save_scenario(custom, path)
    result = _run_main("--scenario", str(path))
    assert result.returncode == 0
    assert "Solved:" in result.stdout
    assert "Near_Bunker" in result.stdout
    assert "Solo_Jet" in result.stdout
    assert "Target_A" not in result.stdout


def test_main_live_completes_without_a_client(tmp_path):
    custom = ScenarioConfig(
        jets=[
            {
                "agent_id": "Solo_Jet",
                "position": (20.0, 20.0),
                "payload": {"JDAM": 1, "AIM9X": 0},
            }
        ],
        targets=[
            {
                "target_id": "Near_Bunker",
                "position": (20.5, 20.0),
                "hp_required": 1,
                "target_type": "HARDENED_BUNKER",
            }
        ],
    )
    path = tmp_path / "custom.json"
    save_scenario(custom, path)
    result = _run_main(
        "--scenario",
        str(path),
        "--live",
        "--live-port",
        "0",
    )
    assert result.returncode == 0
    assert "Live stream listening on 127.0.0.1:" in result.stdout
    assert "Solved:" in result.stdout
    assert "Near_Bunker" in result.stdout
