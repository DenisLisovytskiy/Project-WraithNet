"""Post-run metrics summary and CSV export for the WraithNet PoC."""

from collections import Counter
from pathlib import Path


def summarize(model) -> dict:
    """Human/test-friendly summary of a completed (or in-progress) run.

    Distances and leftover payload are unioned over live jets *and* any jet
    that already left play (destroyed or returned-to-base). Iterating only
    ``model.jets`` silently dropped those numbers the moment a jet was
    reaped — a latent bug this phase closes.
    """
    if hasattr(model, "jet_travel_stats"):
        all_stats = model.jet_travel_stats()
    else:
        live_stats = {
            aid: (jet.distance_traveled, dict(jet.payload))
            for aid, jet in model.jets.items()
        }
        past_stats = {
            aid: (o.distance_traveled, dict(o.final_payload_remaining))
            for aid, o in getattr(model, "jet_outcomes", {}).items()
        }
        all_stats = {**past_stats, **live_stats}

    if hasattr(model, "total_distance_traveled"):
        total_distance = model.total_distance_traveled()
    else:
        total_distance = sum(d for d, _ in all_stats.values())
    contributors_by_target: dict[str, set[str]] = {}
    missiles_by_weapon_type: Counter[str] = Counter()
    for entry in model.strike_log:
        contributors_by_target.setdefault(entry["target_id"], set()).add(entry["agent_id"])
        missiles_by_weapon_type[entry["weapon_type"]] += 1

    outcomes = getattr(model, "jet_outcomes", {})
    jets_destroyed = sum(1 for o in outcomes.values() if o.outcome == "destroyed")
    jets_returned_to_base = sum(
        1 for o in outcomes.values() if o.outcome == "returned_to_base"
    )

    return {
        "ticks_elapsed": model.tick,
        "solved": all(t.is_destroyed for t in model.targets.values()),
        "swarm_wiped_out": model.swarm_wiped_out,
        "mission_aborted": getattr(model, "mission_aborted", False),
        "targets_destroyed": sum(1 for t in model.targets.values() if t.is_destroyed),
        "total_targets": len(model.targets),
        "jets_destroyed": jets_destroyed,
        "jets_returned_to_base": jets_returned_to_base,
        "jets_still_flying": len(model.jets),
        "total_distance_traveled": total_distance,
        "total_missiles_fired": len(model.strike_log),
        "per_weapon_type_missiles_fired": dict(missiles_by_weapon_type),
        "total_jammed_agent_ticks": getattr(model, "total_jammed_agent_ticks", 0),
        "contributors_by_target": {k: sorted(v) for k, v in contributors_by_target.items()},
        "per_jet_distance_traveled": {aid: d for aid, (d, _) in all_stats.items()},
        "per_jet_total_payload_remaining": {
            aid: sum(p.values()) for aid, (_, p) in all_stats.items()
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
