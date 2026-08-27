"""Optional trace recorder for the replay viewer. Attaching a
TraceRecorder to a WraithNetBridge has zero effect on simulation logic,
timing, or determinism -- see docs/07_Visualization_Design_Document.md §4
and this phase's acceptance criteria for the tests that prove it.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from wraithnet import config
from wraithnet.trace_schemas import JetFrame, StrikeEvent, TargetFrame, TraceHeader, TraceTick

if TYPE_CHECKING:
    from wraithnet.bridge import WraithNetBridge


class TraceRecorder:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self._path.open("w", encoding="utf-8")

    def record_header(self, bridge: WraithNetBridge) -> None:
        model = bridge.model
        header = TraceHeader(
            grid_width=model.space.x_max,
            grid_height=model.space.y_max,
            sensor_radius=config.SENSOR_RADIUS,
            jam_radius=config.JAM_RADIUS,
            sam_engagement_range=config.SAM_ENGAGEMENT_RANGE,
            strike_range=config.STRIKE_RANGE,
            jet_ids=list(model.jets.keys()),
            initial_payload={aid: dict(j.payload) for aid, j in model.jets.items()},
            target_ids=list(model.targets.keys()),
            target_types={tid: t.target_type for tid, t in model.targets.items()},
            target_hp_required={tid: t.hp_required for tid, t in model.targets.items()},
            sam_site_ids=list(model.sam_sites.keys()),
            jammer_positions={jid: j.position for jid, j in model.jammers.items()},
            target_positions={tid: t.position for tid, t in model.targets.items()},
        )
        self._file.write(header.model_dump_json() + "\n")
        self._file.flush()

    def record_tick(self, bridge: WraithNetBridge) -> None:
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
        tick = TraceTick(
            tick=model.tick,
            jets=jets,
            targets=targets,
            strikes=strikes,
            running=model.running,
            swarm_wiped_out=model.swarm_wiped_out,
        )
        self._file.write(tick.model_dump_json() + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()
