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

    missile_events = getattr(model, "missile_events", None) or []
    launches = sum(1 for e in missile_events if e.get("event") == "launch")
    hits = sum(
        1
        for e in missile_events
        if e.get("event") == "impact" and e.get("outcome") == "HIT"
    )
    misses = sum(
        1
        for e in missile_events
        if e.get("event") == "impact" and e.get("outcome") == "MISS"
    )
    fuel_expired = sum(
        1
        for e in missile_events
        if e.get("event") == "impact" and e.get("outcome") == "FUEL_EXPIRED"
    )
    lost_lock = sum(
        1
        for e in missile_events
        if e.get("event") == "impact" and e.get("outcome") == "LOST_LOCK"
    )
    decoyed = sum(
        1
        for e in missile_events
        if e.get("event") == "impact" and e.get("outcome") == "DECOYED"
    )
    intercepted = sum(
        1
        for e in missile_events
        if e.get("event") == "impact" and e.get("outcome") == "INTERCEPTED"
    )
    flare_attempts = int(getattr(model, "flare_attempts", 0) or 0)
    flare_successes = int(getattr(model, "flare_successes", 0) or 0)
    notch_attempts = int(getattr(model, "notch_attempts", 0) or 0)
    notch_completions = int(getattr(model, "notch_completions", 0) or 0)
    notch_lock_breaks = int(getattr(model, "notch_lock_breaks", 0) or 0)
    flares_remaining = {
        aid: int(getattr(jet, "flares_remaining", 0))
        for aid, jet in model.jets.items()
    }
    deaths_by_guidance = dict(getattr(model, "deaths_by_guidance", {}) or {})
    agm84_launches = int(getattr(model, "agm84_launches", 0) or 0)
    agm84_hits = int(getattr(model, "agm84_hits", 0) or 0)
    agm84_hit_rate = (
        (agm84_hits / agm84_launches) if agm84_launches > 0 else 0.0
    )

    negotiation_events = list(getattr(model, "negotiation_events", None) or [])
    awards_by_flare_readiness: Counter[str] = Counter()
    decline_reasons: Counter[str] = Counter()
    overkill_avoided_milli = 0
    low_confidence_awards = 0
    for event in negotiation_events:
        et = event.get("event")
        if et == "award":
            overkill_avoided_milli += int(event.get("overkill_milli") or 0)
            if event.get("low_confidence_coverage"):
                low_confidence_awards += 1
            for accepted in event.get("accepted") or []:
                fr = float(accepted.get("flare_readiness") or 0.0)
                if fr >= 0.75:
                    bucket = "high"
                elif fr >= 0.25:
                    bucket = "medium"
                else:
                    bucket = "low"
                awards_by_flare_readiness[bucket] += 1
        elif et == "decline":
            decline_reasons[str(event.get("reason") or "UNKNOWN")] += 1

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
        "missile_launches": launches,
        "missile_hits": hits,
        "missile_misses": misses,
        "missile_fuel_expired": fuel_expired,
        "missile_lost_lock": lost_lock,
        "missile_decoyed": decoyed,
        "missile_intercepted": intercepted,
        "flare_attempts": flare_attempts,
        "flare_successes": flare_successes,
        "flares_remaining": flares_remaining,
        "notch_attempts": notch_attempts,
        "notch_completions": notch_completions,
        "notch_lock_breaks": notch_lock_breaks,
        "deaths_by_guidance": deaths_by_guidance,
        "corvette_sam_launches": int(getattr(model, "corvette_sam_launches", 0) or 0),
        "ciws_attempts": int(getattr(model, "ciws_attempts", 0) or 0),
        "ciws_intercepts": int(getattr(model, "ciws_intercepts", 0) or 0),
        "deck_gun_hits": int(getattr(model, "deck_gun_hits", 0) or 0),
        "corvette_losses": int(getattr(model, "corvette_losses", 0) or 0),
        "aaa_bursts": int(getattr(model, "aaa_bursts", 0) or 0),
        "aaa_rounds": int(getattr(model, "aaa_rounds", 0) or 0),
        "aaa_hits": int(getattr(model, "aaa_hits", 0) or 0),
        "aaa_damage": int(getattr(model, "aaa_damage", 0) or 0),
        "aaa_kills": int(getattr(model, "aaa_kills", 0) or 0),
        "aaa_ammo_remaining": sum(
            int(getattr(s, "ammo", 0) or 0)
            for s in getattr(model, "aaa_sites", {}).values()
        ),
        "aircraft_losses_to_aaa": int(
            getattr(model, "aircraft_losses_to_aaa", 0) or 0
        ),
        "tank_rounds": int(getattr(model, "tank_rounds", 0) or 0),
        "tank_hits": int(getattr(model, "tank_hits", 0) or 0),
        "tank_kills_friendly": int(getattr(model, "tank_kills_friendly", 0) or 0),
        "tank_kills_enemy": int(getattr(model, "tank_kills_enemy", 0) or 0),
        "tank_losses_friendly": int(getattr(model, "tank_losses_friendly", 0) or 0),
        "tank_losses_enemy": int(getattr(model, "tank_losses_enemy", 0) or 0),
        "tank_distance_traveled": sum(
            float(getattr(t, "distance_traveled", 0) or 0)
            for t in getattr(model, "tanks", {}).values()
        ),
        "tank_ammo_remaining": sum(
            int(getattr(t, "ammo", 0) or 0)
            for t in getattr(model, "tanks", {}).values()
            if not getattr(t, "is_destroyed", False)
        ),
        "agm84_launches": agm84_launches,
        "agm84_hits": agm84_hits,
        "agm84_hit_rate": agm84_hit_rate,
        "negotiation_event_count": len(negotiation_events),
        "awards_by_flare_readiness": dict(awards_by_flare_readiness),
        "negotiation_decline_reasons": dict(decline_reasons),
        "reservation_overkill_milli": overkill_avoided_milli,
        "low_confidence_coverage_awards": low_confidence_awards,
        "carrier_recoveries": int(getattr(model, "carrier_recoveries", 0) or 0),
        "carrier_count": len(getattr(model, "carriers", {}) or {}),
        "corvette_count": len(getattr(model, "corvettes", {}) or {}),
        "objective_outcomes": {
            oid: {
                "status": str(getattr(st, "status", getattr(st, "state", ""))),
                "reason": str(getattr(st, "reason", "") or ""),
            }
            for oid, st in (getattr(model, "objective_states", None) or {}).items()
        }
        if getattr(model, "objective_states", None)
        else {},
        "missile_outcomes_by_seeker": dict(
            Counter(
                str(e.get("guidance") or e.get("weapon_type") or "UNKNOWN")
                for e in missile_events
                if e.get("event") == "impact"
            )
        ),
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
