"""Pure tick buffer and pose sampler for smooth live / replay presentation.

Phases 46–48: headless-testable contracts — no Panda3D, sockets, or wall-clock
sleeps inside sampler math.

Presentation aims at ``t_p - interp_delay_s`` so a ``next`` tick usually exists.
When the buffer falls behind live accept time, ``advance`` may run faster (up to
``CATCHUP_MAX_RATE``). If backlog exceeds ``CATCHUP_BACKLOG_TICKS``, the buffer
snaps to the latest pair and discards older ticks so the scene never walks every
intermediate snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from wraithnet.geometry import shortest_signed_angle
from wraithnet.trace_schemas import (
    CountermeasureEvent,
    JetFrame,
    JetOutcomeEvent,
    StrikeEvent,
    TraceTick,
)

# Design-doc calibration starting points (Phases 48–49).
DEFAULT_INTERP_DELAY_S = 0.10
MAX_EXTRAPOLATION_ALPHA = 1.10
CATCHUP_BACKLOG_TICKS = 30
CATCHUP_MAX_RATE = 8.0
# Hard memory ceiling; snap engages earlier at CATCHUP_BACKLOG_TICKS.
DEFAULT_MAX_BACKLOG_TICKS = 128
TARGET_PRESENTATION_FPS = 60
MIN_ACCEPTABLE_VIEW_FPS = 30
DEFAULT_PLAYBACK_RATE = 1.0
DEFAULT_REPLAY_TICK_SPACING_S = 0.10

_EPS = 1e-9

EventKind = Literal["STRIKE", "JET_OUTCOME", "COUNTERMEASURE", "OTHER"]
PresentedPayload = StrikeEvent | JetOutcomeEvent | CountermeasureEvent


@dataclass(frozen=True)
class SampledPose:
    entity_id: str
    x: float
    y: float
    heading_deg: float  # [0, 360) display heading
    alpha: float  # blend factor used
    from_tick: int
    to_tick: int


@dataclass(frozen=True)
class PresentedEvent:
    """Discrete trace events released exactly once when presentation crosses a tick."""

    tick: int
    kind: EventKind
    payload: PresentedPayload


def lerp(a: float, b: float, alpha: float) -> float:
    """Linear blend: ``a + alpha * (b - a)``."""
    return a + alpha * (b - a)


def lerp_heading_deg(a: float, b: float, alpha: float) -> float:
    """Shortest-arc heading blend; result normalized to ``[0, 360)``."""
    return (a + shortest_signed_angle(a, b) * alpha) % 360.0


def clamp_alpha(alpha: float, *, max_extrapolation: float) -> float:
    """Clamp α to ``[0, max_extrapolation]``.

    α may slightly exceed 1.0 only when extrapolating past the newest tick;
    callers pass ``max_extrapolation`` (typically ``MAX_EXTRAPOLATION_ALPHA``).
    """
    if alpha < 0.0:
        return 0.0
    if alpha > max_extrapolation:
        return max_extrapolation
    return alpha


def _jet_map(tick: TraceTick) -> dict[str, JetFrame]:
    return {j.agent_id: j for j in tick.jets}


def _pose_from_jet(
    jet: JetFrame,
    *,
    alpha: float,
    from_tick: int,
    to_tick: int,
) -> SampledPose:
    return SampledPose(
        entity_id=jet.agent_id,
        x=float(jet.position[0]),
        y=float(jet.position[1]),
        heading_deg=float(jet.heading) % 360.0,
        alpha=alpha,
        from_tick=from_tick,
        to_tick=to_tick,
    )


def _lerp_jets(
    from_tick: TraceTick,
    to_tick: TraceTick,
    alpha: float,
    *,
    hold_missing_in_to: bool,
) -> dict[str, SampledPose]:
    """Interpolate jets between two ticks.

    Spawn (only in ``to``): place at the ``to`` pose — never lerp from origin.
    Despawn (only in ``from``): hold last pose while ``hold_missing_in_to``;
    omit once the end tick is current for HUD (caller sets the flag).
    """
    from_jets = _jet_map(from_tick)
    to_jets = _jet_map(to_tick)
    out: dict[str, SampledPose] = {}
    ft, tt = from_tick.tick, to_tick.tick

    for agent_id, fj in from_jets.items():
        tj = to_jets.get(agent_id)
        if tj is not None:
            out[agent_id] = SampledPose(
                entity_id=agent_id,
                x=lerp(float(fj.position[0]), float(tj.position[0]), alpha),
                y=lerp(float(fj.position[1]), float(tj.position[1]), alpha),
                heading_deg=lerp_heading_deg(float(fj.heading), float(tj.heading), alpha),
                alpha=alpha,
                from_tick=ft,
                to_tick=tt,
            )
        elif hold_missing_in_to:
            out[agent_id] = _pose_from_jet(fj, alpha=alpha, from_tick=ft, to_tick=tt)

    for agent_id, tj in to_jets.items():
        if agent_id in out:
            continue
        # Newly appeared on ``to``: solid at destination pose (no origin lerp).
        out[agent_id] = _pose_from_jet(tj, alpha=alpha, from_tick=ft, to_tick=tt)

    return out


def _events_for_tick(tick: TraceTick) -> list[PresentedEvent]:
    events: list[PresentedEvent] = []
    for strike in tick.strikes:
        events.append(PresentedEvent(tick=tick.tick, kind="STRIKE", payload=strike))
    for outcome in tick.jet_outcomes:
        events.append(
            PresentedEvent(tick=tick.tick, kind="JET_OUTCOME", payload=outcome)
        )
    for cm in tick.countermeasures:
        events.append(
            PresentedEvent(tick=tick.tick, kind="COUNTERMEASURE", payload=cm)
        )
    return events


class TickBuffer:
    """Holds recent ``TraceTick``s with accept timestamps for pose sampling.

    Catch-up policy (Phase 48)
    --------------------------
    * Modest lag: advance presentation at ``1×`` wall ``dt``.
    * Behind the live edge (``last_accept - t_p`` > delay cushion): speed up to
      ``CATCHUP_MAX_RATE``.
    * ``backlog_len > CATCHUP_BACKLOG_TICKS``: snap to the latest pair
      ``(T_{n-1}, T_n)``, discard older entries, jump ``t_p`` near live, and
      skip events for discarded ticks (never invent strikes while catching up
      or extrapolating).
    """

    def __init__(
        self,
        *,
        interp_delay_s: float = DEFAULT_INTERP_DELAY_S,
        max_extrapolation_alpha: float = MAX_EXTRAPOLATION_ALPHA,
        max_backlog_ticks: int = DEFAULT_MAX_BACKLOG_TICKS,
        catchup_backlog_ticks: int = CATCHUP_BACKLOG_TICKS,
        catchup_max_rate: float = CATCHUP_MAX_RATE,
    ) -> None:
        self._interp_delay_s = float(interp_delay_s)
        self._max_extrapolation_alpha = float(max_extrapolation_alpha)
        self._max_backlog_ticks = int(max_backlog_ticks)
        self._catchup_backlog_ticks = int(catchup_backlog_ticks)
        self._catchup_max_rate = float(catchup_max_rate)
        self._entries: list[tuple[float, TraceTick]] = []
        self._t_p: float = 0.0
        self._last_event_tick: int | None = None
        self._extrapolating: bool = False
        self._catching_up: bool = False
        self._did_snap: bool = False

    @property
    def interp_delay_s(self) -> float:
        return self._interp_delay_s

    @interp_delay_s.setter
    def interp_delay_s(self, value: float) -> None:
        self._interp_delay_s = float(value)

    def reset(self) -> None:
        self._entries.clear()
        self._t_p = 0.0
        self._last_event_tick = None
        self._extrapolating = False
        self._catching_up = False
        self._did_snap = False

    def push(self, tick: TraceTick, t_accept: float) -> None:
        """Store ``tick`` at ``t_accept``. Out-of-order older ticks are dropped (stale)."""
        if self._entries and tick.tick <= self._entries[-1][1].tick:
            return  # drop stale / duplicate
        self._entries.append((float(t_accept), tick))
        # New data clears extrapolation until the next sample past the tip.
        self._extrapolating = False
        if len(self._entries) > self._catchup_backlog_ticks:
            self.snap_to_latest_pair()
        overflow = len(self._entries) - self._max_backlog_ticks
        if overflow > 0:
            del self._entries[:overflow]

    def set_presentation_time(self, t_p: float) -> None:
        self._t_p = float(t_p)

    def presentation_lag_s(self) -> float:
        """Seconds presentation clock is behind the newest accept time (0 if none)."""
        if not self._entries:
            return 0.0
        return max(0.0, self._entries[-1][0] - self._t_p)

    def catchup_rate(self) -> float:
        """Presentation time multiplier for the next ``advance`` (1× … max rate)."""
        lag = self.presentation_lag_s()
        # Stay at 1× while within the delay cushion of the live edge.
        if lag <= self._interp_delay_s:
            return 1.0
        # Scale up with lag; cap at CATCHUP_MAX_RATE.
        return min(
            self._catchup_max_rate,
            1.0 + lag / max(_EPS, self._interp_delay_s),
        )

    def advance(self, dt: float, *, catchup: bool = True) -> None:
        """Advance presentation time by ``dt``.

        When ``catchup`` is True (live), multiply by ``catchup_rate()`` while
        behind the live edge. Offline / replay passes ``catchup=False`` and
        scales ``dt`` by playback rate in the caller instead.
        """
        rate = self.catchup_rate() if catchup else 1.0
        self._catching_up = bool(catchup) and rate > 1.0 + _EPS
        self._t_p += float(dt) * rate
        if catchup and len(self._entries) > self._catchup_backlog_ticks:
            self.snap_to_latest_pair()

    def load_scheduled_ticks(
        self,
        ticks: list[TraceTick],
        *,
        t0: float = 0.0,
        spacing_s: float = DEFAULT_REPLAY_TICK_SPACING_S,
    ) -> None:
        """Push an offline tick list with synthetic accept times (replay path).

        Disables snap pressure by raising the catch-up backlog ceiling for the
        duration of the load so a full trace is retained for interpolation.
        """
        if not ticks:
            return
        self._catchup_backlog_ticks = max(self._catchup_backlog_ticks, len(ticks) + 1)
        self._max_backlog_ticks = max(self._max_backlog_ticks, len(ticks) + 1)
        for i, tick in enumerate(ticks):
            self.push(tick, t_accept=float(t0) + float(i) * float(spacing_s))
        self.set_presentation_time(float(t0))
        self._did_snap = False
        self._catching_up = False
        self._extrapolating = False

    def snap_to_latest_pair(self) -> bool:
        """Keep only the latest pair (or single tick); jump presentation near live.

        Returns True if a snap occurred. Discarded ticks never emit presented
        events (``_last_event_tick`` advances past them).
        """
        n = len(self._entries)
        if n <= self._catchup_backlog_ticks or n == 0:
            return False

        keep = 2 if n >= 2 else 1
        discarded = self._entries[:-keep]
        self._entries = list(self._entries[-keep:])
        if discarded:
            # Skip events for discarded ticks — do not walk them through the scene.
            self._last_event_tick = discarded[-1][1].tick
        # Aim t_p at last accept so t_eff = last_accept - delay (delay cushion).
        last_t = self._entries[-1][0]
        self._t_p = last_t
        self._catching_up = False
        self._extrapolating = False
        self._did_snap = True
        return True

    def consume_snap_flag(self) -> bool:
        """True once after a snap; clears so the viewer can skip intermediate presents."""
        flagged = self._did_snap
        self._did_snap = False
        return flagged

    @property
    def backlog_len(self) -> int:
        return len(self._entries)

    @property
    def is_extrapolating(self) -> bool:
        return self._extrapolating

    @property
    def is_catching_up(self) -> bool:
        return self._catching_up

    def buffered_ticks(self) -> list[TraceTick]:
        """Ticks currently retained for presentation (post-snap subset)."""
        return [tick for _t, tick in self._entries]

    def _effective_time(self) -> float:
        return self._t_p - self._interp_delay_s

    def _brackets(
        self, t_eff: float
    ) -> tuple[TraceTick | None, TraceTick | None, float, bool]:
        """Return ``(from_tick, to_tick, alpha, extrapolating)`` for ``t_eff``."""
        entries = self._entries
        if not entries:
            return None, None, 0.0, False

        if t_eff <= entries[0][0]:
            tick = entries[0][1]
            return tick, tick, 0.0, False

        last_t, last_tick = entries[-1]
        if t_eff >= last_t:
            if len(entries) >= 2:
                t0, tick0 = entries[-2]
                span = max(_EPS, last_t - t0)
                raw = (t_eff - t0) / span
                alpha = clamp_alpha(raw, max_extrapolation=self._max_extrapolation_alpha)
                return tick0, last_tick, alpha, raw > 1.0
            return last_tick, last_tick, 0.0, False

        # t_eff is strictly inside (first, last)
        for i in range(len(entries) - 1):
            t0, tick0 = entries[i]
            t1, tick1 = entries[i + 1]
            if t0 <= t_eff <= t1:
                span = max(_EPS, t1 - t0)
                alpha = (t_eff - t0) / span
                return tick0, tick1, alpha, False

        # Should be unreachable given the guards above.
        return last_tick, last_tick, 0.0, False

    def current_tick_for_hud(self) -> TraceTick | None:
        """Latest tick whose accept time is at or before effective presentation time."""
        t_eff = self._effective_time()
        current: TraceTick | None = None
        for t_accept, tick in self._entries:
            if t_accept <= t_eff:
                current = tick
            else:
                break
        return current

    def sample_jet_poses(self) -> dict[str, SampledPose]:
        t_eff = self._effective_time()
        from_tick, to_tick, alpha, extrapolating = self._brackets(t_eff)
        self._extrapolating = extrapolating
        if from_tick is None or to_tick is None:
            return {}

        hud = self.current_tick_for_hud()
        # Hold despawned jets until the end tick is HUD-current; then omit.
        hold_missing = hud is None or hud.tick < to_tick.tick
        if from_tick.tick == to_tick.tick:
            return {
                j.agent_id: _pose_from_jet(
                    j,
                    alpha=alpha,
                    from_tick=from_tick.tick,
                    to_tick=to_tick.tick,
                )
                for j in from_tick.jets
            }
        return _lerp_jets(
            from_tick, to_tick, alpha, hold_missing_in_to=hold_missing
        )

    def pop_newly_presented_events(self) -> list[PresentedEvent]:
        """Return discrete events for ticks newly crossed by presentation; once each.

        Extrapolation past the newest tick does not invent or re-fire events.
        """
        hud = self.current_tick_for_hud()
        if hud is None:
            return []

        out: list[PresentedEvent] = []
        for _t_accept, tick in self._entries:
            if tick.tick > hud.tick:
                break
            if self._last_event_tick is not None and tick.tick <= self._last_event_tick:
                continue
            out.extend(_events_for_tick(tick))
            self._last_event_tick = tick.tick
        return out
