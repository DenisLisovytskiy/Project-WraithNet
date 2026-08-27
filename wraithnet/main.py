"""Entrypoint: run the fixed WraithNet PoC scenario end-to-end and report results."""

import asyncio

from wraithnet.bridge import WraithNetBridge
from wraithnet.metrics import export_results, summarize
from wraithnet.recorder import TraceRecorder


async def run_scenario() -> WraithNetBridge:
    recorder = TraceRecorder("results/trace.jsonl")
    bridge = WraithNetBridge(recorder=recorder)
    try:
        await bridge.run()
    finally:
        recorder.close()
    return bridge


def main() -> None:
    bridge = asyncio.run(run_scenario())
    summary = summarize(bridge.model)

    print(f"Simulation finished at tick {summary['ticks_elapsed']}")
    if summary["swarm_wiped_out"]:
        print("OUTCOME: FAILURE — entire swarm destroyed before objective complete.")
    else:
        print(
            f"Solved: {summary['solved']} "
            f"({summary['targets_destroyed']}/{summary['total_targets']} targets destroyed)"
        )
    print(
        f"Jets destroyed: {summary['jets_destroyed']} / "
        f"survived: {summary['jets_survived']}"
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

    export_results(bridge.model)
    print("Results exported to ./results/model_vars.csv and ./results/agent_vars.csv")


if __name__ == "__main__":
    main()
