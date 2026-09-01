"""Incremental (single-tick-at-a-time) equivalent of
viewer/playback.py::compute_highlights, adapted for a live stream where
ticks arrive one at a time rather than being pre-loaded into a list.
Deliberately does NOT import from viewer/ (viewer3d must not depend on the
Pygame viewer package) -- reimplements the same small amount of comparison
logic directly against TraceTick. No panda3d/direct import anywhere in this
file -- it is pure data comparison, unit-tested headlessly.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field

from wraithnet.trace_schemas import StrikeEvent, TraceHeader, TraceTick


@dataclass(frozen=True)
class TickHighlights:
    strikes: list[StrikeEvent] = field(default_factory=list)
    newly_destroyed_target_ids: list[str] = field(default_factory=list)
    newly_destroyed_jet_ids: list[str] = field(default_factory=list)
    newly_jammed_jet_ids: list[str] = field(default_factory=list)
    newly_unjammed_jet_ids: list[str] = field(default_factory=list)
    newly_rtb_jet_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class RunSummary:
    """Headline numbers for the 3D viewer's end-of-run overlay.

    Same fields as viewer/playback.py::RunSummary (copied, not imported).
    """

    solved: bool
    swarm_wiped_out: bool
    targets_destroyed: int
    total_targets: int
    jets_destroyed: int
    total_jets: int
    total_missiles_fired: int
    per_weapon_type_missiles_fired: dict[str, int]
    mission_aborted: bool = False
    jets_returned_to_base: int = 0
    jets_still_flying: int = 0


def compute_highlights(previous: TraceTick | None, current: TraceTick) -> TickHighlights:
    """Match viewer/playback.py::compute_highlights field-level semantics.

    previous=None (the first tick ever seen) must not synthesize false
    "newly destroyed/jammed" events. strikes are per-tick and always
    reported verbatim from current.strikes.
    """
    prev_targets = {t.target_id: t for t in previous.targets} if previous else {}
    prev_jets = {j.agent_id: j for j in previous.jets} if previous else {}
    prev_jet_ids = set(prev_jets)
    curr_jet_ids = {j.agent_id for j in current.jets}

    newly_destroyed_target_ids = (
        [
            t.target_id
            for t in current.targets
            if t.hp_remaining <= 0
            and (t.target_id not in prev_targets or prev_targets[t.target_id].hp_remaining > 0)
        ]
        if previous is not None
        else []
    )
    newly_jammed_jet_ids = (
        [
            j.agent_id
            for j in current.jets
            if j.jammed and (j.agent_id not in prev_jets or not prev_jets[j.agent_id].jammed)
        ]
        if previous is not None
        else []
    )
    newly_unjammed_jet_ids = [
        j.agent_id
        for j in current.jets
        if not j.jammed and j.agent_id in prev_jets and prev_jets[j.agent_id].jammed
    ]
    events = list(getattr(current, "jet_outcomes", None) or [])
    if previous is not None and events:
        newly_rtb_jet_ids = [
            e.agent_id for e in events if e.outcome == "returned_to_base"
        ]
        newly_destroyed_jet_ids = sorted(
            e.agent_id for e in events if e.outcome == "destroyed"
        )
    else:
        newly_rtb_jet_ids = []
        newly_destroyed_jet_ids = sorted(prev_jet_ids - curr_jet_ids) if previous else []
    return TickHighlights(
        strikes=list(current.strikes),
        newly_destroyed_target_ids=newly_destroyed_target_ids,
        newly_destroyed_jet_ids=newly_destroyed_jet_ids,
        newly_jammed_jet_ids=newly_jammed_jet_ids,
        newly_unjammed_jet_ids=newly_unjammed_jet_ids,
        newly_rtb_jet_ids=newly_rtb_jet_ids,
    )


def compute_run_summary(header: TraceHeader, ticks: list[TraceTick]) -> RunSummary:
    """Headline numbers for the end-of-run overlay, derived only from received ticks."""
    if not ticks:
        total_targets = len(header.target_ids) if header is not None else 0
        total_jets = len(header.jet_ids) if header is not None else 0
        return RunSummary(
            solved=False,
            swarm_wiped_out=False,
            mission_aborted=False,
            targets_destroyed=0,
            total_targets=total_targets,
            jets_destroyed=0,
            jets_returned_to_base=0,
            jets_still_flying=total_jets,
            total_jets=total_jets,
            total_missiles_fired=0,
            per_weapon_type_missiles_fired={},
        )
    final = ticks[-1]
    missiles: Counter[str] = Counter()
    for tick in ticks:
        for strike in tick.strikes:
            missiles[strike.weapon_type] += 1
    total_targets = len(header.target_ids)
    targets_destroyed = sum(1 for t in final.targets if t.hp_remaining <= 0)
    total_jets = len(header.jet_ids)
    returned = 0
    destroyed_events = 0
    for tick in ticks:
        for event in getattr(tick, "jet_outcomes", None) or []:
            if event.outcome == "returned_to_base":
                returned += 1
            elif event.outcome == "destroyed":
                destroyed_events += 1
    if returned or destroyed_events:
        jets_destroyed = destroyed_events
        jets_returned_to_base = returned
        jets_still_flying = len(final.jets)
    else:
        jets_destroyed = total_jets - len(final.jets)
        jets_returned_to_base = 0
        jets_still_flying = len(final.jets)
    return RunSummary(
        solved=targets_destroyed == total_targets,
        swarm_wiped_out=final.swarm_wiped_out,
        mission_aborted=bool(getattr(final, "mission_aborted", False)),
        targets_destroyed=targets_destroyed,
        total_targets=total_targets,
        jets_destroyed=jets_destroyed,
        jets_returned_to_base=jets_returned_to_base,
        jets_still_flying=jets_still_flying,
        total_jets=total_jets,
        total_missiles_fired=sum(missiles.values()),
        per_weapon_type_missiles_fired=dict(missiles),
    )
