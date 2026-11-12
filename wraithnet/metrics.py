"""Post-run metrics summary and CSV export for the WraithNet PoC."""

import hashlib
import subprocess
from collections import Counter
from pathlib import Path

from wraithnet.generation_rng import GENERATOR_VERSION
from wraithnet.scenario_feasibility import analyze_feasibility


def summarize(model) -> dict:
    """Human/test-friendly summary of a completed (or in-progress) run.

    Distances and leftover payload are unioned over live jets *and* any jet
    that already left play (destroyed or returned-to-base). Iterating only
    ``model.jets`` silently dropped those numbers the moment a jet was
    reaped — a latent bug this phase closes.
    """
    all_stats = model.jet_travel_stats()
    total_distance = model.total_distance_traveled()
    contributors_by_target: dict[str, set[str]] = {}
    missiles_by_weapon_type: Counter[str] = Counter()
    for entry in model.strike_log:
        contributors_by_target.setdefault(entry["target_id"], set()).add(entry["agent_id"])
        missiles_by_weapon_type[entry["weapon_type"]] += 1

    outcomes = model.jet_outcomes
    jets_destroyed = sum(1 for o in outcomes.values() if o.outcome == "destroyed")
    jets_returned_to_base = sum(
        1 for o in outcomes.values() if o.outcome == "returned_to_base"
    )

    missile_events = model.missile_events
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
    flare_attempts = int(model.flare_attempts)
    flare_successes = int(model.flare_successes)
    notch_attempts = int(model.notch_attempts)
    notch_completions = int(model.notch_completions)
    notch_lock_breaks = int(model.notch_lock_breaks)
    flares_remaining = {
        aid: int(jet.flares_remaining)
        for aid, jet in model.jets.items()
    }
    deaths_by_guidance = dict(model.deaths_by_guidance)
    agm84_launches = int(model.agm84_launches)
    agm84_hits = int(model.agm84_hits)
    agm84_hit_rate = (
        (agm84_hits / agm84_launches) if agm84_launches > 0 else 0.0
    )

    negotiation_events = model.negotiation_events
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
        "solved": bool(model.mission_solved),
        "swarm_wiped_out": model.swarm_wiped_out,
        "mission_aborted": model.mission_aborted,
        "targets_destroyed": sum(1 for t in model.targets.values() if t.is_destroyed),
        "total_targets": len(model.targets),
        "jets_destroyed": jets_destroyed,
        "jets_returned_to_base": jets_returned_to_base,
        "jets_crashed": len(model.crash_log),
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
        "corvette_sam_launches": int(model.corvette_sam_launches),
        "ciws_attempts": int(model.ciws_attempts),
        "ciws_intercepts": int(model.ciws_intercepts),
        "deck_gun_hits": int(model.deck_gun_hits),
        "corvette_losses": int(model.corvette_losses),
        "aaa_bursts": int(model.aaa_bursts),
        "aaa_rounds": int(model.aaa_rounds),
        "aaa_hits": int(model.aaa_hits),
        "aaa_damage": int(model.aaa_damage),
        "aaa_kills": int(model.aaa_kills),
        "aaa_ammo_remaining": sum(
            int(s.ammo) for s in model.aaa_sites.values()
        ),
        "aircraft_losses_to_aaa": int(model.aircraft_losses_to_aaa),
        "tank_rounds": int(model.tank_rounds),
        "tank_hits": int(model.tank_hits),
        "tank_kills_friendly": int(model.tank_kills_friendly),
        "tank_kills_enemy": int(model.tank_kills_enemy),
        "tank_losses_friendly": int(model.tank_losses_friendly),
        "tank_losses_enemy": int(model.tank_losses_enemy),
        "tank_distance_traveled": sum(
            float(t.distance_traveled) for t in model.tanks.values()
        ),
        "tank_ammo_remaining": sum(
            int(t.ammo) for t in model.tanks.values() if not t.is_destroyed
        ),
        "agm84_launches": agm84_launches,
        "agm84_hits": agm84_hits,
        "agm84_hit_rate": agm84_hit_rate,
        "negotiation_event_count": len(negotiation_events),
        "awards_by_flare_readiness": dict(awards_by_flare_readiness),
        "negotiation_decline_reasons": dict(decline_reasons),
        "reservation_overkill_milli": overkill_avoided_milli,
        "low_confidence_coverage_awards": low_confidence_awards,
        "carrier_recoveries": int(model.carrier_recoveries),
        "carrier_count": len(model.carriers),
        "corvette_count": len(model.corvettes),
        "objective_outcomes": dict(model.objective_status),
        "missile_outcomes_by_seeker": dict(
            Counter(
                str(e.get("guidance") or e.get("weapon_type") or "UNKNOWN")
                for e in missile_events
                if e.get("event") == "impact"
            )
        ),
        "total_jammed_agent_ticks": model.total_jammed_agent_ticks,
        "contributors_by_target": {k: sorted(v) for k, v in contributors_by_target.items()},
        "per_jet_distance_traveled": {aid: d for aid, (d, _) in all_stats.items()},
        "per_jet_total_payload_remaining": {
            aid: sum(p.values()) for aid, (_, p) in all_stats.items()
        },
    }


def classify_outcome(model) -> str:
    """Exactly one of SOLVED, ABORTED, WIPED, TICK_CAP.

    A model that is still running when the caller stopped ticking it hit the
    tick cap; that is not the same thing as a mission that ended unsolved.

    ABORTED is every other unsolved ending with a friendly left alive or
    home: the swarm went home unfinished, or a required PROTECT/SURVIVE
    objective failed. ``mission_aborted`` in the summary tells the two apart.
    """
    if model.running:
        return "TICK_CAP"
    if model.mission_solved:
        return "SOLVED"
    if model.swarm_wiped_out:
        return "WIPED"
    return "ABORTED"


def git_commit() -> str | None:
    """Short hash of the checkout this code runs from, or None outside git."""
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if out.returncode != 0:
        return None
    return out.stdout.strip() or None


def build_run_record(
    model,
    *,
    max_ticks: int,
    wall_time_s: float,
    preset: str | None = None,
    generation_seed: int | None = None,
    feasible: bool | None = None,
    commit: str | None = None,
) -> dict:
    """``summarize(model)`` plus run metadata, flat and JSON-serialisable.

    ``wall_time_s`` is the only field that differs between two runs of the
    same scenario on the same code.
    """
    scenario = model._scenario
    if feasible is None:
        feasible = analyze_feasibility(scenario).feasible
    return {
        "outcome": classify_outcome(model),
        "tick_cap_hit": bool(model.running),
        "max_ticks": int(max_ticks),
        "preset": preset,
        "generation_seed": generation_seed,
        "combat_seed": int(model.combat_seed),
        "generator_version": GENERATOR_VERSION,
        "scenario_hash": hashlib.sha256(
            scenario.model_dump_json().encode("utf-8")
        ).hexdigest()[:16],
        "feasible": bool(feasible),
        "git_commit": commit if commit is not None else git_commit(),
        "wall_time_s": round(float(wall_time_s), 3),
        **summarize(model),
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
