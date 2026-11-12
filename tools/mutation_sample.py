"""Sampled mutation testing for one module with cosmic-ray.

A full cosmic-ray run is too slow here (bid_utility.py alone gives 1,574
mutants), so this script keeps a fixed-seed sample of the mutants and runs
only those. Same module, sample size, seed and code give the same sample.

cosmic-ray edits files on disk. Run this from a throwaway git worktree,
never from the working tree you develop in:

    git worktree add --detach ../wn-mut-bridge HEAD
    cd ../wn-mut-bridge
    python tools/mutation_sample.py wraithnet/bridge.py --sample 40 --seed 58 \\
        --tests tests/test_bridge.py tests/test_scenario.py --out bridge.json

Full run (every mutant): pass --sample 0. Mutants inside function
annotations are dropped before sampling (they cannot change behaviour).

The JSON written to --out has the counts and one entry per surviving
mutant (operator, line, column, diff), for the audit report.
"""

from __future__ import annotations

import argparse
import ast
import json
import random
import sqlite3
import subprocess
import sys
from pathlib import Path


def _config(module: str, tests: list[str], timeout: float) -> str:
    command = " ".join(
        [Path(sys.executable).as_posix(), "-m", "pytest", "-x", "-q", "-p", "no:cacheprovider", *tests]
    )
    return (
        "[cosmic-ray]\n"
        f'module-path = "{module}"\n'
        f"timeout = {timeout}\n"
        "excluded-modules = []\n"
        f"test-command = {json.dumps(command)}\n"
        "\n[cosmic-ray.distributor]\n"
        'name = "local"\n'
    )


def _annotation_spans(module: str) -> list[tuple[tuple[int, int], tuple[int, int]]]:
    """Source spans of function annotations (arguments, returns, local AnnAssign).

    Python never evaluates these at run time here (postponed annotations), so
    a mutant inside one is equivalent by construction. Class-level field
    annotations are kept: pydantic and dataclasses do read those.
    """
    spans = []
    for fn in ast.walk(ast.parse(Path(module).read_text(encoding="utf-8"))):
        if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        a = fn.args
        nodes = [x.annotation for x in [*a.posonlyargs, *a.args, *a.kwonlyargs, a.vararg, a.kwarg] if x]
        nodes.append(fn.returns)
        nodes += [n.annotation for n in ast.walk(fn) if isinstance(n, ast.AnnAssign)]
        spans += [((n.lineno, n.col_offset), (n.end_lineno, n.end_col_offset)) for n in nodes if n]
    return spans


def _keep_sample(db: Path, module: str, sample: int, seed: int) -> tuple[int, int, int]:
    """Delete every mutant not in the sample. Returns (total, annotation, kept)."""
    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT job_id, module_path, start_pos_row, start_pos_col, operator_name, "
        "occurrence, operator_args FROM mutation_specs"
    ).fetchall()
    total = len(rows)
    spans = _annotation_spans(module)
    in_annotation = {r[0] for r in rows if any(a <= (r[2], r[3]) < b for a, b in spans)}
    rows = [r for r in rows if r[0] not in in_annotation]
    # job_id is a random uuid: order by what the mutant is, not by its id.
    rows.sort(key=lambda r: (r[1], r[2], r[3], r[4], r[5], str(r[6])))
    keep = rows if sample <= 0 or sample >= len(rows) else random.Random(seed).sample(rows, sample)
    keep_ids = {r[0] for r in keep}
    drop = [(r[0],) for r in rows if r[0] not in keep_ids] + [(j,) for j in in_annotation]
    con.executemany("DELETE FROM mutation_specs WHERE job_id = ?", drop)
    con.executemany("DELETE FROM work_items WHERE job_id = ?", drop)
    con.commit()
    con.close()
    return total, len(in_annotation), len(keep)


def _results(db: Path) -> dict:
    con = sqlite3.connect(db)
    rows = con.execute(
        "SELECT s.operator_name, s.start_pos_row, s.start_pos_col, s.definition_name, "
        "r.worker_outcome, r.test_outcome, r.diff, r.output "
        "FROM mutation_specs s JOIN work_results r ON r.job_id = s.job_id "
        "ORDER BY s.start_pos_row, s.start_pos_col, s.operator_name"
    ).fetchall()
    con.close()
    counts: dict[str, int] = {}
    survivors = []
    for op, line, col, func, worker, test, diff, output in rows:
        key = test if worker == "NORMAL" else worker
        if worker == "NORMAL" and test == "KILLED" and "timeout" in (output or "")[-20:]:
            key = "KILLED_BY_TIMEOUT"
        counts[key] = counts.get(key, 0) + 1
        if worker == "NORMAL" and test == "SURVIVED":
            survivors.append(
                {"operator": op, "line": line, "col": col, "function": func, "diff": diff}
            )
    return {"outcomes": counts, "survivors": survivors}


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("module", help="module path, e.g. wraithnet/bridge.py")
    parser.add_argument("--tests", nargs="+", required=True, help="test files to run per mutant")
    parser.add_argument("--sample", type=int, default=40, help="mutants to keep; 0 = all")
    parser.add_argument("--seed", type=int, default=58)
    parser.add_argument("--timeout", type=float, default=60.0, help="seconds per mutant")
    parser.add_argument("--out", required=True, help="JSON summary to write")
    args = parser.parse_args(argv)

    stem = Path(args.module).stem
    cfg, db = Path(f"cr-{stem}.toml"), Path(f"cr-{stem}.sqlite")
    cfg.write_text(_config(args.module, args.tests, args.timeout), encoding="utf-8")
    db.unlink(missing_ok=True)
    cosmic_ray = [sys.executable, "-m", "cosmic_ray.cli"]
    subprocess.run([*cosmic_ray, "init", str(cfg), str(db)], check=True)
    total, annotation, kept = _keep_sample(db, args.module, args.sample, args.seed)
    subprocess.run([*cosmic_ray, "exec", str(cfg), str(db)], check=True)
    result = {"module": args.module, "total_mutants": total,
              "annotation_mutants_excluded": annotation, "sampled": kept,
              "seed": args.seed, "tests": args.tests, **_results(db)}
    Path(args.out).write_text(json.dumps(result, indent=1), encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k != "survivors"}))
    print(f"survivors: {len(result['survivors'])}")


if __name__ == "__main__":
    main()
