"""Phase 57 Part D: one seed, two fresh processes, two hash seeds, one result.

In-process repetition shares the hash seed, import order and warm caches, so
it cannot catch set/dict-order dependence. Two subprocesses can.
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _start(out_dir: Path, hash_seed: str) -> subprocess.Popen:
    env = {**os.environ, "PYTHONHASHSEED": hash_seed}
    return subprocess.Popen(
        [
            sys.executable, "-m", "wraithnet.main",
            "--generate", "CONTESTED", "--seed", "6",
            "--max-ticks", "150",
            "--output-dir", str(out_dir),
            "--summary-json", str(out_dir / "summary.json"),
        ],
        cwd=REPO_ROOT,
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
    )


@pytest.mark.slow
def test_same_seed_in_two_processes_with_different_hash_seeds(tmp_path):
    runs = {seed: _start(tmp_path / f"hash-{seed}", seed) for seed in ("1", "2")}
    for seed, process in runs.items():
        _, stderr = process.communicate(timeout=300)
        assert process.returncode == 0, f"PYTHONHASHSEED={seed}: {stderr}"

    summaries, trace_hashes = [], []
    for seed in runs:
        out_dir = tmp_path / f"hash-{seed}"
        summary = json.loads((out_dir / "summary.json").read_text(encoding="utf-8"))
        summary.pop("wall_time_s")
        summaries.append(summary)
        trace_hashes.append(
            hashlib.sha256((out_dir / "trace.jsonl").read_bytes()).hexdigest()
        )

    assert summaries[0]["ticks_elapsed"] == 150
    assert summaries[0]["total_missiles_fired"] > 0, "the fixture run must see combat"
    assert summaries[0] == summaries[1]
    assert trace_hashes[0] == trace_hashes[1]
