"""Post-run metrics summary and CSV export for the WraithNet PoC."""

from collections import Counter
from pathlib import Path


def summarize(model) -> dict:
    """Human/test-friendly summary of a completed (or in-progress) run."""
    total_distance = sum(j.distance_traveled for j in model.jets.values())
    contributors_by_target: dict[str, set[str]] = {}
    missiles_by_weapon_type: Counter[str] = Counter()
    for entry in model.strike_log:
        contributors_by_target.setdefault(entry["target_id"], set()).add(entry["agent_id"])
        missiles_by_weapon_type[entry["weapon_type"]] += 1

    initial_jet_ids = getattr(model, "_initial_jet_ids", set(model.jets.keys()))
    return {
        "ticks_elapsed": model.tick,
        "solved": all(t.is_destroyed for t in model.targets.values()),
        "swarm_wiped_out": model.swarm_wiped_out,
        "targets_destroyed": sum(1 for t in model.targets.values() if t.is_destroyed),
        "total_targets": len(model.targets),
        "jets_destroyed": len(initial_jet_ids) - len(model.jets),
        "jets_survived": len(model.jets),
        "total_distance_traveled": total_distance,
        "total_missiles_fired": len(model.strike_log),
        "per_weapon_type_missiles_fired": dict(missiles_by_weapon_type),
        "total_jammed_agent_ticks": getattr(model, "total_jammed_agent_ticks", 0),
        "contributors_by_target": {k: sorted(v) for k, v in contributors_by_target.items()},
        "per_jet_distance_traveled": {
            aid: j.distance_traveled for aid, j in model.jets.items()
        },
        "per_jet_total_payload_remaining": {
            aid: j.total_payload_remaining for aid, j in model.jets.items()
        },
    }


def export_results(model, output_dir: str = "results") -> None:
    """Write Mesa's collected time series to CSV for offline inspection."""
    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)
    model.datacollector.get_model_vars_dataframe().to_csv(
        out / "model_vars.csv", index_label="step"
    )
    from wraithnet.world import JetAgent

    # JetAgent-scoped: SamSiteAgent/EwJammerAgent share model.agents (Phase 8/9).
    model.datacollector.get_agenttype_vars_dataframe(JetAgent).to_csv(
        out / "agent_vars.csv"
    )
