"""Build TraceHeader / TraceTick from a WraithNetBridge.

Shared by TraceRecorder (file sink) and LiveStreamServer (socket sink) so
both emit the same data for the same bridge state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from wraithnet import config
from wraithnet.trace_schemas import JetFrame, StrikeEvent, TargetFrame, TraceHeader, TraceTick

if TYPE_CHECKING:
    from wraithnet.bridge import WraithNetBridge


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
    )


def build_trace_tick(bridge: WraithNetBridge) -> TraceTick:
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
    return TraceTick(
        tick=model.tick,
        jets=jets,
        targets=targets,
        strikes=strikes,
        running=model.running,
        swarm_wiped_out=model.swarm_wiped_out,
    )
