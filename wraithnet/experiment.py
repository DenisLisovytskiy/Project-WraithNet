"""Batch runner: N seeded headless missions, one JSON row per run.

    python -m wraithnet.experiment --preset STANDARD --seeds 0-199 --workers 8 --out results/exp-standard

Each run is ``wraithnet.main.run_scenario`` on a generated scenario, in its
own worker process. Rows go to ``<out>/runs.jsonl`` as they finish, so a
killed batch keeps what it had and ``--resume`` continues it. The file loads
with ``pandas.read_json(path, lines=True)``.
"""

from __future__ import annotations

import argparse
import ast
import asyncio
import json
import shutil
import statistics
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from pathlib import Path

from wraithnet import config
from wraithnet.generation_presets import DIFFICULTY_PRESETS, parameters_for_preset
from wraithnet.main import run_scenario
from wraithnet.metrics import build_run_record, git_commit
from wraithnet.scenario_generator import generate_scenario

RUNS_FILE = "runs.jsonl"


def parse_seeds(spec: str) -> list[int]:
    """``"0-3,7,10-11"`` -> ``[0, 1, 2, 3, 7, 10, 11]`` (order kept, no duplicates)."""
    seeds: list[int] = []
    for part in spec.split(","):
        low, dash, high = part.strip().partition("-")
        first = int(low)
        last = int(high) if dash else first
        if last < first:
            raise ValueError(f"empty seed range: {part!r}")
        seeds.extend(range(first, last + 1))
    return list(dict.fromkeys(seeds))


def parse_assignments(items: list[str]) -> dict[str, object]:
    """``["sam_count=3", "group_style=CLUSTERED"]`` -> typed dict.

    Values are Python literals when they parse as one, otherwise plain strings.
    """
    parsed: dict[str, object] = {}
    for item in items:
        key, eq, raw = item.partition("=")
        if not eq or not key:
            raise ValueError(f"expected NAME=value, got {item!r}")
        try:
            parsed[key] = ast.literal_eval(raw)
        except (ValueError, SyntaxError):
            parsed[key] = raw
    return parsed


def apply_config_overrides(overrides: dict[str, object]) -> None:
    """Set ``wraithnet.config`` constants for this process.

    ponytail: only constants read as ``config.NAME`` at call time see the new
    value. A module that did ``from wraithnet.config import NAME`` keeps its
    copy; change that import if such a constant ever needs sweeping.
    """
    for name, value in overrides.items():
        if not name.isupper() or not hasattr(config, name):
            raise ValueError(f"unknown config constant: {name}")
        current = getattr(config, name)
        if _is_number(current):
            ok = _is_number(value)
        else:
            ok = isinstance(current, (bool, str)) and type(value) is type(current)
        if not ok:
            raise ValueError(
                f"{name} is {type(current).__name__}, cannot take {value!r}"
            )
        setattr(config, name, value)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def run_one(job: dict) -> dict:
    """Worker: generate and fly one seed. Never raises; failures become ERROR rows."""
    row_base = {
        "preset": job["preset"],
        "generation_seed": job["seed"],
        "params": job["params"],
        "config_overrides": job["config_overrides"],
    }
    trace_dir = Path(job["trace_dir"]) if job["trace_dir"] else None
    started = time.perf_counter()
    try:
        apply_config_overrides(job["config_overrides"])
        generated = generate_scenario(
            parameters_for_preset(
                job["preset"], seed=job["seed"], overrides=job["params"] or None
            )
        )
        bridge = asyncio.run(
            run_scenario(
                generated.scenario,
                output_dir=str(trace_dir) if trace_dir else None,
                max_ticks=job["max_ticks"],
                trace=trace_dir is not None,
            )
        )
        record = build_run_record(
            bridge.model,
            max_ticks=job["max_ticks"],
            wall_time_s=time.perf_counter() - started,
            preset=job["preset"],
            generation_seed=job["seed"],
            feasible=generated.feasibility.feasible,
            commit=job["commit"] or "unknown",
        )
    except Exception as exc:  # noqa: BLE001 - one bad seed must not sink the batch
        record = {
            "outcome": "ERROR",
            "error": f"{type(exc).__name__}: {exc}",
            "wall_time_s": round(time.perf_counter() - started, 3),
            "git_commit": job["commit"] or "unknown",
        }
    record.update(row_base)
    keep = job["keep_traces"]
    if trace_dir is not None and keep == "failed" and record["outcome"] == "SOLVED":
        shutil.rmtree(trace_dir, ignore_errors=True)
        trace_dir = None
    record["trace_path"] = (
        str(trace_dir / "trace.jsonl")
        if trace_dir is not None and (trace_dir / "trace.jsonl").is_file()
        else None
    )
    return record


def read_rows(path: Path) -> list[dict]:
    if not path.is_file():
        return []
    with path.open(encoding="utf-8") as handle:
        return [json.loads(line) for line in handle if line.strip()]


def summary_table(rows: list[dict]) -> str:
    """Short text table: outcome counts, median ticks, losses, targets."""
    outcomes = Counter(str(r.get("outcome")) for r in rows)
    flown = [r for r in rows if r.get("outcome") != "ERROR"]
    lines = [f"runs: {len(rows)}"]
    for name in ("SOLVED", "ABORTED", "WIPED", "TICK_CAP", "ERROR"):
        if outcomes[name]:
            lines.append(f"  {name:<9}{outcomes[name]:>5}")
    if flown:
        lines.append(
            f"median ticks: {statistics.median(r['ticks_elapsed'] for r in flown):g}"
        )
        lines.append(f"jets lost: {sum(r['jets_destroyed'] for r in flown)}")
        lines.append(
            "targets destroyed: "
            f"{sum(r['targets_destroyed'] for r in flown)}"
            f" / {sum(r['total_targets'] for r in flown)}"
        )
    return "\n".join(lines)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Run a batch of seeded headless WraithNet missions."
    )
    parser.add_argument("--preset", required=True, choices=sorted(DIFFICULTY_PRESETS))
    parser.add_argument(
        "--seeds", required=True, type=parse_seeds, help='e.g. "0-199" or "1,4,10-12"'
    )
    parser.add_argument("--out", required=True, help="Output directory for runs.jsonl.")
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--max-ticks", type=int, default=1000)
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Skip seeds that already have a row in runs.jsonl.",
    )
    parser.add_argument(
        "--keep-traces",
        choices=("none", "failed", "all"),
        default="failed",
        help="Which runs keep their trace.jsonl for replay (default: failed).",
    )
    parser.add_argument(
        "--param",
        action="append",
        default=[],
        metavar="KEY=VALUE",
        help="Generation parameter override (repeatable).",
    )
    parser.add_argument(
        "--config-override",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="Set a wraithnet.config constant in every worker (repeatable).",
    )
    args = parser.parse_args(argv)
    if args.workers < 1:
        parser.error("--workers must be at least 1")
    try:
        args.params = parse_assignments(args.param)
        args.config_overrides = parse_assignments(args.config_override)
    except ValueError as exc:
        parser.error(str(exc))
    return args


def run_batch(args: argparse.Namespace) -> list[dict]:
    """Run every seed not already done; return all rows now in runs.jsonl."""
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    runs_path = out / RUNS_FILE
    existing = read_rows(runs_path)
    if existing and not args.resume:
        raise SystemExit(
            f"{runs_path} already has {len(existing)} rows; "
            "pass --resume to continue it or choose another --out."
        )
    done = {r["generation_seed"] for r in existing if r.get("preset") == args.preset}
    commit = git_commit()
    jobs = [
        {
            "preset": args.preset,
            "seed": seed,
            "max_ticks": args.max_ticks,
            "params": args.params,
            "config_overrides": args.config_overrides,
            "keep_traces": args.keep_traces,
            "trace_dir": (
                None
                if args.keep_traces == "none"
                else str(out / "traces" / f"{args.preset}-seed-{seed}")
            ),
            "commit": commit,
        }
        for seed in args.seeds
        if seed not in done
    ]
    rows = list(existing)
    if jobs:
        with (
            runs_path.open("a", encoding="utf-8") as handle,
            ProcessPoolExecutor(max_workers=args.workers) as pool,
        ):
            for future in as_completed([pool.submit(run_one, job) for job in jobs]):
                row = future.result()
                handle.write(json.dumps(row, sort_keys=True) + "\n")
                handle.flush()
                rows.append(row)
    traces = out / "traces"
    if traces.is_dir() and not any(traces.iterdir()):
        traces.rmdir()
    return rows


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    rows = run_batch(args)
    print(summary_table(rows))
    print(f"rows: {Path(args.out) / RUNS_FILE}")


if __name__ == "__main__":
    main()
