"""Entrypoint: run the fixed WraithNet PoC scenario end-to-end and report results."""

import argparse
import asyncio
import json
import time
from pathlib import Path

from wraithnet.bridge import WraithNetBridge
from wraithnet.generation_presets import DIFFICULTY_PRESETS, parameters_for_preset
from wraithnet.live_stream import LiveStreamServer
from wraithnet.metrics import build_run_record, export_results, summarize
from wraithnet.paths import user_data_dir
from wraithnet.recorder import TraceRecorder
from wraithnet.scenario_generator import generate_scenario
from wraithnet.scenario_io import load_scenario
from wraithnet.scenario_schema import ScenarioConfig
from wraithnet.trace_sink import TraceSink


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the WraithNet PoC scenario.")
    source = parser.add_mutually_exclusive_group()
    source.add_argument(
        "--scenario",
        type=str,
        default=None,
        help="Path to a scenario JSON file (wraithnet.scenario_schema.ScenarioConfig). "
        "Defaults to the built-in fixed PoC scenario if omitted.",
    )
    source.add_argument(
        "--generate",
        type=str,
        default=None,
        metavar="PRESET",
        choices=sorted(DIFFICULTY_PRESETS),
        help="Generate the scenario from a difficulty preset instead of loading "
        "a file. Requires --seed.",
    )
    parser.add_argument(
        "--seed", type=int, default=None, help="Generation seed for --generate."
    )
    parser.add_argument(
        "--max-ticks",
        type=int,
        default=1000,
        help="Stop after this many ticks if the mission has not ended (TICK_CAP).",
    )
    parser.add_argument(
        "--no-trace",
        action="store_true",
        help="Do not write trace.jsonl or the CSV exports.",
    )
    parser.add_argument(
        "--summary-json",
        type=str,
        default=None,
        help="Write the run summary and metadata to this path as one JSON object.",
    )
    parser.add_argument(
        "--live",
        action="store_true",
        help="Also stream ticks live over TCP.",
    )
    parser.add_argument("--live-host", type=str, default="127.0.0.1")
    parser.add_argument("--live-port", type=int, default=8765)
    parser.add_argument(
        "--output-dir",
        type=str,
        default=None,
        help="Directory for trace.jsonl / model_vars.csv / agent_vars.csv. "
        "Defaults to the user-data results folder. Concurrent runs should "
        "pass distinct directories so they do not truncate each other's files.",
    )
    args = parser.parse_args(argv)
    if args.generate is not None and args.seed is None:
        parser.error("--generate requires --seed")
    if args.seed is not None and args.generate is None:
        parser.error("--seed is only valid with --generate")
    return args


def _results_dir(output_dir: str | None = None):
    if output_dir:
        path = Path(output_dir)
        path.mkdir(parents=True, exist_ok=True)
        return path
    return user_data_dir("results")


async def run_scenario(
    scenario: ScenarioConfig | None = None,
    live: bool = False,
    live_host: str = "127.0.0.1",
    live_port: int = 8765,
    output_dir: str | None = None,
    *,
    max_ticks: int = 1000,
    trace: bool = True,
) -> WraithNetBridge:
    sinks: list[TraceSink] = []
    recorder = None
    if trace:
        recorder = TraceRecorder(str(_results_dir(output_dir) / "trace.jsonl"))
        sinks.append(recorder)
    live_server = None
    if live:
        live_server = LiveStreamServer(live_host, live_port)
        await live_server.start()
        sinks.append(live_server)
        print(f"Live stream listening on {live_host}:{live_server.port}")

    bridge = WraithNetBridge(sinks=sinks, scenario=scenario)
    try:
        await bridge.run(max_ticks=max_ticks)
    finally:
        if recorder is not None:
            recorder.close()
        if live_server is not None:
            # Tick-based CNP finishes quickly; hold the socket so a viewer that
            # connects just after launch still receives the header/backlog.
            await live_server.wait_for_client_or_timeout(10.0)
            await live_server.close()
    return bridge


def main(argv: list[str] | None = None) -> None:
    args = parse_args(argv)
    scenario = load_scenario(args.scenario) if args.scenario else None
    preset = None
    feasible = None
    if args.generate:
        generated = generate_scenario(
            parameters_for_preset(args.generate, seed=args.seed)
        )
        scenario = generated.scenario
        preset = args.generate
        feasible = generated.feasibility.feasible
    trace = not args.no_trace
    # --no-trace with no explicit directory writes nothing, so do not create one.
    results_dir = _results_dir(args.output_dir) if trace or args.output_dir else None
    started = time.perf_counter()
    bridge = asyncio.run(
        run_scenario(
            scenario,
            live=args.live,
            live_host=args.live_host,
            live_port=args.live_port,
            output_dir=str(results_dir) if results_dir else None,
            max_ticks=args.max_ticks,
            trace=trace,
        )
    )
    wall_time_s = time.perf_counter() - started
    record = build_run_record(
        bridge.model,
        max_ticks=args.max_ticks,
        wall_time_s=wall_time_s,
        preset=preset,
        generation_seed=args.seed,
        feasible=feasible,
    )
    summary = summarize(bridge.model)
    if args.summary_json:
        out = Path(args.summary_json)
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(record, sort_keys=True), encoding="utf-8")

    print(f"Simulation finished at tick {summary['ticks_elapsed']}")
    print(f"Outcome: {record['outcome']}")
    if summary["swarm_wiped_out"]:
        print("OUTCOME: FAILURE — entire swarm destroyed before objective complete.")
    elif summary["mission_aborted"]:
        print(
            "OUTCOME: MISSION ABORTED — swarm returned to base before completing the objective."
        )
    else:
        print(
            f"Solved: {summary['solved']} "
            f"({summary['targets_destroyed']}/{summary['total_targets']} targets destroyed)"
        )
    print(
        f"Jets destroyed: {summary['jets_destroyed']} / "
        f"returned to base: {summary['jets_returned_to_base']} / "
        f"still flying: {summary['jets_still_flying']}"
    )
    print(f"Total distance traveled: {summary['total_distance_traveled']:.2f}")
    print(
        f"Total missiles fired: {summary['total_missiles_fired']} "
        f"{summary['per_weapon_type_missiles_fired']}"
    )
    print(f"Total jammed-agent-ticks: {summary['total_jammed_agent_ticks']}")
    for target_id, contributors in summary["contributors_by_target"].items():
        print(f"  {target_id}: struck by {contributors}")
    for agent_id, distance in summary["per_jet_distance_traveled"].items():
        print(
            f"  {agent_id}: distance_traveled={distance:.2f}, "
            f"payload_remaining={summary['per_jet_total_payload_remaining'][agent_id]}"
        )

    if trace:
        export_results(bridge.model, output_dir=str(results_dir))
        print(f"Results exported to {results_dir / 'model_vars.csv'} and {results_dir / 'agent_vars.csv'}")


if __name__ == "__main__":
    main()
