"""CLI tests for python -m wraithnet.main --scenario."""

import subprocess
import sys
from pathlib import Path

from wraithnet.scenario_io import save_scenario
from wraithnet.scenario_schema import ScenarioConfig, new_scenario_data, scenario_from_data

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
    custom = scenario_from_data(new_scenario_data(
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
    ))
    path = tmp_path / "custom.json"
    save_scenario(custom, path)
    result = _run_main("--scenario", str(path))
    assert result.returncode == 0
    assert "Solved:" in result.stdout
    assert "Near_Bunker" in result.stdout
    assert "Solo_Jet" in result.stdout
    assert "Target_A" not in result.stdout


def test_main_live_completes_without_a_client(tmp_path):
    custom = scenario_from_data(new_scenario_data(
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
    ))
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


def test_main_explicit_argv_matches_sys_argv(tmp_path, monkeypatch, capsys):
    custom = scenario_from_data(new_scenario_data(
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
    ))
    path = tmp_path / "custom.json"
    save_scenario(custom, path)

    from wraithnet.main import main

    monkeypatch.setattr(sys, "argv", ["wraithnet.main", "--scenario", str(path)])
    main()
    via_sys_argv = capsys.readouterr().out

    monkeypatch.setattr(sys, "argv", ["wraithnet.main", "--would-fail-if-read"])
    main(["--scenario", str(path)])
    via_explicit_argv = capsys.readouterr().out

    assert via_sys_argv == via_explicit_argv
    assert "Solved:" in via_sys_argv
    assert "Near_Bunker" in via_sys_argv


def test_main_writes_results_via_user_data_dir(tmp_path, monkeypatch):
    custom = scenario_from_data(new_scenario_data(
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
    ))
    scenario_path = tmp_path / "custom.json"
    save_scenario(custom, scenario_path)

    def fake_user_data_dir(subdir: str) -> Path:
        path = tmp_path / subdir
        path.mkdir(parents=True, exist_ok=True)
        return path

    monkeypatch.setattr("wraithnet.main.user_data_dir", fake_user_data_dir)

    from wraithnet.main import main

    main(["--scenario", str(scenario_path)])

    results = tmp_path / "results"
    assert (results / "trace.jsonl").is_file()
    assert (results / "model_vars.csv").is_file()
    assert (results / "agent_vars.csv").is_file()


def test_main_output_dir_isolates_results(tmp_path):
    custom = scenario_from_data(new_scenario_data(
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
    ))
    scenario_path = tmp_path / "custom.json"
    save_scenario(custom, scenario_path)
    out_a = tmp_path / "run-a"
    out_b = tmp_path / "run-b"
    from wraithnet.main import main

    main(["--scenario", str(scenario_path), "--output-dir", str(out_a)])
    main(["--scenario", str(scenario_path), "--output-dir", str(out_b)])
    for folder in (out_a, out_b):
        assert (folder / "trace.jsonl").is_file()
        assert (folder / "model_vars.csv").is_file()
        assert (folder / "agent_vars.csv").is_file()
    assert (out_a / "trace.jsonl").read_bytes()
    assert (out_b / "trace.jsonl").read_bytes()
