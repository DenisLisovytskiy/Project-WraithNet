"""Phase 57 Part B: batch runner over seeds."""

import json

import pytest

from wraithnet.experiment import (
    RUNS_FILE,
    apply_config_overrides,
    main,
    parse_assignments,
    parse_seeds,
    read_rows,
    summary_table,
)
from wraithnet import config

BASE = ["--preset", "TRAINING", "--max-ticks", "150", "--keep-traces", "none"]


def _by_seed(rows: list[dict]) -> dict[int, dict]:
    return {
        row["generation_seed"]: {k: v for k, v in row.items() if k != "wall_time_s"}
        for row in rows
    }


def test_parse_seeds_ranges_lists_and_duplicates():
    assert parse_seeds("0-3") == [0, 1, 2, 3]
    assert parse_seeds("5,1-2,2,9") == [5, 1, 2, 9]
    with pytest.raises(ValueError):
        parse_seeds("4-2")


def test_parse_assignments_types_values():
    assert parse_assignments(["sam_count=3", "group_style=CLUSTERED", "x=0.5"]) == {
        "sam_count": 3,
        "group_style": "CLUSTERED",
        "x": 0.5,
    }
    with pytest.raises(ValueError):
        parse_assignments(["no_equals_sign"])


def test_config_override_sets_known_constants_and_rejects_the_rest(monkeypatch):
    monkeypatch.setattr(config, "CONTRACT_CLAIM_TTL_TICKS", 50)
    apply_config_overrides({"CONTRACT_CLAIM_TTL_TICKS": 7})
    assert config.CONTRACT_CLAIM_TTL_TICKS == 7
    with pytest.raises(ValueError):
        apply_config_overrides({"NO_SUCH_CONSTANT": 1})
    with pytest.raises(ValueError):
        apply_config_overrides({"CONTRACT_CLAIM_TTL_TICKS": "soon"})
    with pytest.raises(ValueError):
        apply_config_overrides({"WEAPON_CATALOG": 1})


def test_batch_writes_one_row_per_seed_and_resume_runs_nothing(tmp_path, capsys):
    out = tmp_path / "exp"
    argv = [*BASE, "--seeds", "0-3", "--workers", "2", "--out", str(out)]
    main(argv)

    runs_path = out / RUNS_FILE
    rows = read_rows(runs_path)
    assert sorted(r["generation_seed"] for r in rows) == [0, 1, 2, 3]
    assert {r["preset"] for r in rows} == {"TRAINING"}
    assert all(r["outcome"] in {"SOLVED", "TICK_CAP"} for r in rows)
    assert all(r["trace_path"] is None for r in rows)
    assert "runs: 4" in capsys.readouterr().out

    with pytest.raises(SystemExit):
        main(argv)  # rows exist and --resume was not given

    before = runs_path.read_bytes()
    main([*argv, "--resume"])
    assert runs_path.read_bytes() == before

    main([*BASE, "--seeds", "0-4", "--workers", "1", "--out", str(out), "--resume"])
    assert sorted(r["generation_seed"] for r in read_rows(runs_path)) == [0, 1, 2, 3, 4]


def test_rows_do_not_depend_on_worker_count(tmp_path):
    one, four = tmp_path / "w1", tmp_path / "w4"
    main([*BASE, "--seeds", "0-3", "--workers", "1", "--out", str(one)])
    main([*BASE, "--seeds", "0-3", "--workers", "4", "--out", str(four)])
    assert _by_seed(read_rows(one / RUNS_FILE)) == _by_seed(read_rows(four / RUNS_FILE))


def test_invalid_override_is_an_error_row_not_a_crash(tmp_path):
    out = tmp_path / "bad"
    main([
        *BASE, "--seeds", "0", "--out", str(out),
        "--config-override", "NO_SUCH_CONSTANT=1",
    ])
    (row,) = read_rows(out / RUNS_FILE)
    assert row["outcome"] == "ERROR"
    assert "NO_SUCH_CONSTANT" in row["error"]
    assert row["config_overrides"] == {"NO_SUCH_CONSTANT": 1}
    assert "ERROR" in summary_table([row])


def test_failed_runs_keep_their_trace(tmp_path):
    out = tmp_path / "traces"
    main([
        "--preset", "TRAINING", "--seeds", "0", "--max-ticks", "5",
        "--keep-traces", "failed", "--out", str(out),
    ])
    (row,) = read_rows(out / RUNS_FILE)
    assert row["outcome"] == "TICK_CAP"
    with open(row["trace_path"], encoding="utf-8") as handle:
        assert json.loads(handle.readline())
