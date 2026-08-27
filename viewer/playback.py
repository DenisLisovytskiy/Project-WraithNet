"""Pure playback/highlight logic for the replay viewer -- no pygame import
in this module. Given the previous and current TraceTick, determines which
transient visual events should be shown this frame.
"""

from collections import Counter
from dataclasses import dataclass

from wraithnet.trace_schemas import StrikeEvent, TraceHeader, TraceTick


@dataclass(frozen=True)
class TickHighlights:
    strikes: list[StrikeEvent]
    newly_destroyed_target_ids: list[str]
    newly_destroyed_jet_ids: list[str]
    newly_jammed_jet_ids: list[str]
    newly_unjammed_jet_ids: list[str]


@dataclass(frozen=True)
class RunSummary:
    solved: bool
    swarm_wiped_out: bool
    targets_destroyed: int
    total_targets: int
    jets_destroyed: int
    total_jets: int
    total_missiles_fired: int
    per_weapon_type_missiles_fired: dict[str, int]


def compute_highlights(previous: TraceTick | None, current: TraceTick) -> TickHighlights:
    prev_targets = {t.target_id: t for t in previous.targets} if previous else {}
    prev_jets = {j.agent_id: j for j in previous.jets} if previous else {}
    prev_jet_ids = set(prev_jets)
    curr_jet_ids = {j.agent_id for j in current.jets}

    # previous=None must not synthesize false "newly destroyed/jammed" events.
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
    newly_destroyed_jet_ids = sorted(prev_jet_ids - curr_jet_ids) if previous else []
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
    return TickHighlights(
        strikes=list(current.strikes),
        newly_destroyed_target_ids=newly_destroyed_target_ids,
        newly_destroyed_jet_ids=newly_destroyed_jet_ids,
        newly_jammed_jet_ids=newly_jammed_jet_ids,
        newly_unjammed_jet_ids=newly_unjammed_jet_ids,
    )


def advance_playback(
    tick_index: int,
    playing: bool,
    accumulator_ms: float,
    dt_ms: float,
    ticks_per_second: float,
    last_index: int,
) -> tuple[int, bool, float]:
    """Advance tick_index from a frame delta. Auto-pauses at last_index.

    Extracted from the Pygame loop so auto-play timing is unit-testable
    without a display (Phase 13 Part C).
    """
    if not playing:
        return tick_index, playing, accumulator_ms
    accumulator_ms += dt_ms
    ms_per_tick = 1000.0 / ticks_per_second
    while accumulator_ms >= ms_per_tick and tick_index < last_index:
        tick_index += 1
        accumulator_ms -= ms_per_tick
    if tick_index >= last_index:
        playing = False
    return tick_index, playing, accumulator_ms


def compute_run_summary(header: TraceHeader, ticks: list[TraceTick]) -> RunSummary:
    """Headline numbers for the end-of-run overlay, derived only from the trace."""
    final = ticks[-1]
    missiles: Counter[str] = Counter()
    for tick in ticks:
        for strike in tick.strikes:
            missiles[strike.weapon_type] += 1
    total_targets = len(header.target_ids)
    targets_destroyed = sum(1 for t in final.targets if t.hp_remaining <= 0)
    total_jets = len(header.jet_ids)
    return RunSummary(
        solved=targets_destroyed == total_targets,
        swarm_wiped_out=final.swarm_wiped_out,
        targets_destroyed=targets_destroyed,
        total_targets=total_targets,
        jets_destroyed=total_jets - len(final.jets),
        total_jets=total_jets,
        total_missiles_fired=sum(missiles.values()),
        per_weapon_type_missiles_fired=dict(missiles),
    )
