"""Entrypoint: run the fixed WraithNet PoC scenario end-to-end and report results."""

import argparse
import asyncio
from pathlib import Path

from wraithnet.bridge import WraithNetBridge
from wraithnet.live_stream import LiveStreamServer
from wraithnet.metrics import export_results, summarize
from wraithnet.paths import user_data_dir
from wraithnet.recorder import TraceRecorder
from wraithnet.scenario_io import load_scenario
from wraithnet.scenario_schema import ScenarioConfig
from wraithnet.trace_sink import TraceSink


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the WraithNet PoC scenario.")
    parser.add_argument(
        "--scenario",
        type=str,
        default=None,
        help="Path to a scenario JSON file (wraithnet.scenario_schema.ScenarioConfig). "
        "Defaults to the built-in fixed PoC scenario if omitted.",
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
    return parser.parse_args(argv)


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
) -> WraithNetBridge:
    results_dir = _results_dir(output_dir)
    recorder = TraceRecorder(str(results_dir / "trace.jsonl"))
    sinks: list[TraceSink] = [recorder]
    live_server = None
    if live:
        live_server = LiveStreamServer(live_host, live_port)
        await live_server.start()
        sinks.append(live_server)
        print(f"Live stream listening on {live_host}:{live_server.port}")

    bridge = WraithNetBridge(sinks=sinks, scenario=scenario)
    try:
        await bridge.run()
    finally:
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
    results_dir = _results_dir(args.output_dir)
    bridge = asyncio.run(
        run_scenario(
            scenario,
            live=args.live,
            live_host=args.live_host,
            live_port=args.live_port,
            output_dir=str(results_dir),
        )
    )
    summary = summarize(bridge.model)

    print(f"Simulation finished at tick {summary['ticks_elapsed']}")
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

    export_results(bridge.model, output_dir=str(results_dir))
    print(f"Results exported to {results_dir / 'model_vars.csv'} and {results_dir / 'agent_vars.csv'}")


if __name__ == "__main__":
    main()
