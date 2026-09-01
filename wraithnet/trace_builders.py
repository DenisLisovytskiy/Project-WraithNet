"""Build TraceHeader / TraceTick from a WraithNetBridge.

Shared by TraceRecorder (file sink) and LiveStreamServer (socket sink) so
both emit the same data for the same bridge state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from wraithnet import config
from wraithnet.trace_schemas import (
    JetFrame,
    JetOutcomeEvent,
    StrikeEvent,
    TargetFrame,
    TraceHeader,
    TraceTick,
)

if TYPE_CHECKING:
    from wraithnet.bridge import WraithNetBridge


def catalog_header_fields() -> dict:
    """Weapon/airframe overlay tables shared by the recorder and the editor."""
    return {
        "weapon_ranges": {wt: spec.range for wt, spec in config.WEAPON_CATALOG.items()},
        "weapon_firing_arcs_deg": {
            wt: spec.firing_arc_deg for wt, spec in config.WEAPON_CATALOG.items()
        },
        "jet_frame_stats": {
            name: {
                "fuel_capacity": spec.fuel_capacity,
                "max_speed": spec.max_speed,
                "hp": float(spec.hp),
            }
            for name, spec in config.JET_FRAME_CATALOG.items()
        },
    }


def build_trace_header(bridge: WraithNetBridge) -> TraceHeader:
    model = bridge.model
    return TraceHeader(
        grid_width=model.space.x_max,
        grid_height=model.space.y_max,
        sensor_radius=config.SENSOR_RADIUS,
        jam_radius=config.JAM_RADIUS,
        sam_engagement_range=config.SAM_ENGAGEMENT_RANGE,
        strike_range=config.WEAPON_CATALOG["JDAM"].range,
        jet_ids=list(model.jets.keys()),
        initial_payload={aid: dict(j.payload) for aid, j in model.jets.items()},
        target_ids=list(model.targets.keys()),
        target_types={tid: t.target_type for tid, t in model.targets.items()},
        target_hp_required={tid: t.hp_required for tid, t in model.targets.items()},
        sam_site_ids=list(model.sam_sites.keys()),
        jammer_positions={jid: j.position for jid, j in model.jammers.items()},
        target_positions={tid: t.position for tid, t in model.targets.items()},
        home_base=model._home_base,
        jet_frame_types={aid: j.frame_type for aid, j in model.jets.items()},
        **catalog_header_fields(),
    )


def build_trace_tick(
    bridge: WraithNetBridge,
    *,
    seen_outcome_ids: set[str] | None = None,
) -> TraceTick:
    model = bridge.model
    jets = [
        JetFrame(
            agent_id=aid,
            position=(float(jet.pos[0]), float(jet.pos[1])),
            hp=jet.hp,
            payload=dict(jet.payload),
            destroyed=False,
            jammed=model._jet_is_jammed(jet),
            role=bridge.agents[aid].role if aid in bridge.agents else None,
            heading=float(jet.heading),
            fuel=max(0.0, float(jet.fuel)),
        )
        for aid, jet in model.jets.items()
    ]
    targets = [
        TargetFrame(
            target_id=tid,
            hp_remaining=t.hp_remaining,
            claimed=bridge.broker.is_claimed(tid),
        )
        for tid, t in model.targets.items()
    ]
    strikes = [
        StrikeEvent(
            agent_id=e["agent_id"],
            target_id=e["target_id"],
            weapon_type=e["weapon_type"],
        )
        for e in model.strike_log
        if e["tick"] == model.tick
    ]
    seen = seen_outcome_ids if seen_outcome_ids is not None else set()
    jet_outcomes: list[JetOutcomeEvent] = []
    for aid, outcome in getattr(model, "jet_outcomes", {}).items():
        if aid in seen:
            continue
        jet_outcomes.append(
            JetOutcomeEvent(agent_id=aid, tick=model.tick, outcome=outcome.outcome)
        )
        seen.add(aid)
    return TraceTick(
        tick=model.tick,
        jets=jets,
        targets=targets,
        strikes=strikes,
        running=model.running,
        swarm_wiped_out=model.swarm_wiped_out,
        mission_aborted=getattr(model, "mission_aborted", False),
        jet_outcomes=jet_outcomes,
    )
