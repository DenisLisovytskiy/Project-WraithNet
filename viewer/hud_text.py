"""HUD / end-of-run overlay copy for the 2D replay viewer.
draw_hud / draw_end_of_run_overlay blit these strings; they do not invent them.
"""

from __future__ import annotations

from wraithnet.trace_schemas import TraceHeader, TraceTick
from viewer.playback import RunSummary, compute_run_summary


def tick_line(tick: TraceTick, tick_index: int, total_ticks: int) -> str:
    """e.g. 'Tick 3   (4/33)' -- keep whatever format draw_hud already uses."""
    return f"Tick {tick.tick}   ({tick_index + 1}/{total_ticks})"


def status_line(playing: bool, ticks_per_second: float) -> str | None:
    """None when ticks_per_second is 0 (Phase 12 HUD had no play/pause line).
    Otherwise 'PLAYING  4.0 tps' / 'PAUSED  4.0 tps' matching current format."""
    if not ticks_per_second:
        return None
    status = "PLAYING" if playing else "PAUSED"
    return f"{status}  {ticks_per_second:.1f} tps"


def sensor_line(show_sensor_radius: bool) -> str:
    return f"Sensor rings: {'ON' if show_sensor_radius else 'OFF'}"


def targets_line(tick: TraceTick) -> str:
    targets_destroyed = sum(1 for t in tick.targets if t.hp_remaining <= 0)
    return f"Targets  {targets_destroyed}/{len(tick.targets)} destroyed"


def jets_line(header: TraceHeader, tick: TraceTick) -> str:
    return f"Jets     {len(tick.jets)}/{len(header.jet_ids)} alive"


def overlay_title(summary: RunSummary) -> str:
    if summary.swarm_wiped_out:
        return "SWARM WIPED OUT"
    if getattr(summary, "mission_aborted", False):
        return "MISSION ABORTED"
    if summary.solved:
        return "MISSION COMPLETE"
    return "END OF TRACE"


def overlay_lines(
    header: TraceHeader,
    ticks: list[TraceTick],
    summary: RunSummary | None = None,
) -> list[str]:
    """The visible lines of the end-of-run overlay, in draw order.

    Derived from compute_run_summary(...) -- do not re-count strikes here.
    """
    if summary is None:
        summary = compute_run_summary(header, ticks)
    weapon_bits = ", ".join(
        f"{k} {v}" for k, v in sorted(summary.per_weapon_type_missiles_fired.items())
    ) or "none"
    still_flying = getattr(
        summary,
        "jets_still_flying",
        len(ticks[-1].jets) if ticks else 0,
    )
    return [
        overlay_title(summary),
        f"Targets destroyed  {summary.targets_destroyed}/{summary.total_targets}",
        f"Jets destroyed     {summary.jets_destroyed}/{summary.total_jets}",
        f"Returned to base   {getattr(summary, 'jets_returned_to_base', 0)}",
        f"Still flying       {still_flying}",
        f"Missiles fired     {summary.total_missiles_fired}  ({weapon_bits})",
    ]
