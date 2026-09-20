"""Build TraceHeader / TraceTick from a WraithNetBridge.

Shared by TraceRecorder (file sink) and LiveStreamServer (socket sink) so
both emit the same data for the same bridge state.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from wraithnet import config
from wraithnet.trace_schemas import (
    AaaSiteFrame,
    CarrierFrame,
    CorvetteFrame,
    CountermeasureEvent,
    JetFrame,
    JetOutcomeEvent,
    MissileFrame,
    NegotiationEvent,
    StrikeEvent,
    TankFrame,
    TargetFrame,
    TraceHeader,
    TraceTick,
    WeaponImpactEvent,
    WeaponLaunchEvent,
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
                # Phase 54: attitude rate / limit overlay for viewers.
                "pitch_rate_deg_per_tick": spec.pitch_rate_deg_per_tick,
                "roll_rate_deg_per_tick": spec.roll_rate_deg_per_tick,
                "pitch_limit_deg": spec.pitch_limit_deg,
                "roll_limit_deg": spec.roll_limit_deg,
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
        aaa_site_ids=list(getattr(model, "aaa_sites", {}).keys()),
        jammer_positions={jid: j.position for jid, j in model.jammers.items()},
        target_positions={tid: t.position for tid, t in model.targets.items()},
        home_base=model._home_base,
        jet_frame_types={aid: j.frame_type for aid, j in model.jets.items()},
        terrain=model.terrain,
        # Phase 38/39/54: keep v3; attitude fields are additive with defaults.
        trace_version=3,
        mission_objectives=list(getattr(model, "mission_objectives", None) or []),
        fixed_airbases=dict(getattr(model, "base_positions", None) or {}),
        combat_seed=int(getattr(model, "combat_seed", 0) or 0),
        **catalog_header_fields(),
    )


def build_trace_tick(
    bridge: WraithNetBridge,
    *,
    seen_outcome_ids: set[str] | None = None,
) -> TraceTick:
    model = bridge.model
    jets = []
    for aid, jet in model.jets.items():
        inbound_n = 0
        notch_cue = None
        if hasattr(model, "_inbound_threats_for"):
            threats = model._inbound_threats_for(jet)
            inbound_n = len(threats)
        if getattr(jet, "defensive_state", "NONE") == "NOTCH":
            notch_cue = float(jet.heading)
        jets.append(
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
                altitude=float(getattr(jet, "altitude", config.JET_COMBAT_ALTITUDE)),
                pitch_deg=float(getattr(jet, "pitch_deg", 0.0)),
                roll_deg=float(getattr(jet, "roll_deg", 0.0)),
                flares_remaining=int(getattr(jet, "flares_remaining", 0)),
                flare_capacity=int(getattr(jet, "flare_capacity", 0)),
                defensive_state=getattr(jet, "defensive_state", "NONE"),
                defensive_threat_id=getattr(jet, "defensive_threat_id", None),
                inbound_threat_count=inbound_n,
                notch_heading_cue=notch_cue,
            )
        )

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

    events = getattr(model, "missile_events", None) or []

    weapon_launches = [
        WeaponLaunchEvent(
            missile_id=e["missile_id"],
            shooter_id=e["shooter_id"],
            target_id=e["target_id"],
            weapon_type=e["weapon_type"],
            guidance=e.get("guidance")
            or config.WEAPON_CATALOG[e["weapon_type"]].guidance,
        )
        for e in events
        if e.get("event") == "launch" and e.get("tick") == model.tick
    ]

    weapon_impacts = [
        WeaponImpactEvent(
            missile_id=e["missile_id"],
            shooter_id=e.get("shooter_id", ""),
            target_id=e["target_id"],
            weapon_type=e["weapon_type"],
            outcome=e["outcome"],
        )
        for e in events
        if e.get("event") == "impact" and e.get("tick") == model.tick
    ]

    countermeasures: list[CountermeasureEvent] = []
    for e in events:
        if e.get("tick") != model.tick:
            continue
        if e.get("event") == "flare":
            countermeasures.append(
                CountermeasureEvent(
                    event="flare",
                    agent_id=e["agent_id"],
                    threat_id=e.get("threat_id"),
                    accepted=e.get("accepted"),
                    flares_remaining=e.get("flares_remaining"),
                )
            )
        elif e.get("event") == "notch":
            agent = model.jets.get(e["agent_id"])
            countermeasures.append(
                CountermeasureEvent(
                    event="notch",
                    agent_id=e["agent_id"],
                    threat_id=e.get("threat_id"),
                    result=e.get("result"),
                    notch_heading_cue=(
                        float(agent.heading) if agent is not None else None
                    ),
                )
            )

    active = (
        list(model.active_missiles)
        if hasattr(model, "active_missiles")
        else [m for m in getattr(model, "missiles", []) if m.status == "IN_FLIGHT"]
    )

    missiles = [
        MissileFrame(
            missile_id=m.missile_id,
            shooter_id=m.shooter_id,
            target_id=m.target_id,
            weapon_type=m.weapon_type,
            guidance=m.guidance,
            position=tuple(float(c) for c in m.position),  # type: ignore[arg-type]
            status=m.status,
            remaining_fuel_ticks=int(m.remaining_fuel_ticks),
        )
        for m in active
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

    carriers = [
        CarrierFrame(
            unit_id=c.unit_id,
            position=tuple(c.position),
            heading=float(c.heading),
            hp=int(c.hp),
            hp_max=int(c.hp_max),
            destroyed=bool(c.destroyed),
            hold_position=bool(c.hold_position),
            route=[tuple(p) for p in c.route],
            frame_type=str(c.frame_type),
        )
        for c in sorted(
            getattr(model, "carriers", {}).values(), key=lambda x: x.unit_id
        )
    ]

    from wraithnet.guns import CIWS_GUN
    from wraithnet.naval import NAVAL_FRAME_CATALOG

    corvettes = [
        CorvetteFrame(
            unit_id=c.unit_id,
            position=tuple(c.position),
            heading=float(c.heading),
            hp=int(c.hp),
            hp_max=int(c.hp_max),
            destroyed=bool(c.is_destroyed),
            hold_position=bool(c.hold_position),
            route=[tuple(p) for p in c.route],
            frame_type=str(c.frame_type),
            loadout={k: int(v) for k, v in dict(c.loadout).items()},
            sam_range=float(NAVAL_FRAME_CATALOG[c.frame_type].sam_engagement_range),
            ciws_range=float(CIWS_GUN.range),
        )
        for c in sorted(
            getattr(model, "corvettes", {}).values(), key=lambda x: x.unit_id
        )
    ]

    aaa_sites = []
    for a in sorted(
        getattr(model, "aaa_sites", {}).values(), key=lambda x: x.target_id
    ):
        tgt = model.targets.get(a.target_id)
        gun = a.gun
        aaa_sites.append(
            AaaSiteFrame(
                target_id=a.target_id,
                position=tuple(a.position),
                heading=float(a.heading),
                hp=int(tgt.hp_remaining) if tgt is not None else 0,
                hp_max=int(tgt.hp_required) if tgt is not None else 0,
                destroyed=bool(tgt.is_destroyed) if tgt is not None else True,
                faction=str(a.faction),
                frame_type=str(a.frame_type),
                ammo=int(a.ammo),
                gun_range=float(gun.range),
                altitude_ceiling=gun.altitude_ceiling,
            )
        )

    from wraithnet.guns import TANK_CANNON

    tanks = [
        TankFrame(
            unit_id=t.unit_id,
            position=tuple(t.position),
            heading=float(t.heading),
            hp=int(t.hp),
            hp_max=int(t.hp_max),
            destroyed=bool(t.is_destroyed),
            faction=str(t.faction),
            frame_type=str(t.frame_type),
            order=str(t.order),
            ammo=int(t.ammo),
            current_target_id=t.current_target_id,
            gun_range=float(TANK_CANNON.range),
        )
        for t in sorted(
            getattr(model, "tanks", {}).values(), key=lambda x: x.unit_id
        )
    ]

    negotiation: list[NegotiationEvent] = []
    for e in getattr(model, "negotiation_events", None) or []:
        if int(e.get("tick", -1)) != model.tick:
            continue
        et = str(e.get("event") or "")
        if et not in ("cfp", "bid", "award", "decline", "reservation_release"):
            continue
        detail = {
            k: v
            for k, v in e.items()
            if k
            not in {
                "event",
                "tick",
                "context_hash",
                "contract_id",
                "target_id",
                "bidder_id",
                "reason",
            }
        }
        negotiation.append(
            NegotiationEvent(
                event=et,  # type: ignore[arg-type]
                tick=int(e.get("tick", model.tick)),
                context_hash=str(e.get("context_hash") or ""),
                contract_id=e.get("contract_id"),
                target_id=e.get("target_id"),
                bidder_id=e.get("bidder_id"),
                reason=e.get("reason"),
                detail=detail,
            )
        )

    return TraceTick(
        tick=model.tick,
        jets=jets,
        targets=targets,
        strikes=strikes,
        running=model.running,
        swarm_wiped_out=model.swarm_wiped_out,
        mission_aborted=getattr(model, "mission_aborted", False),
        jet_outcomes=jet_outcomes,
        weapon_launches=weapon_launches,
        weapon_impacts=weapon_impacts,
        missiles=missiles,
        countermeasures=countermeasures,
        carriers=carriers,
        corvettes=corvettes,
        aaa_sites=aaa_sites,
        tanks=tanks,
        negotiation=negotiation,
    )
