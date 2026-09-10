"""Phase 49: offline replay rate×dt, snap FX suppression, CLI."""

from __future__ import annotations

from pathlib import Path

import pytest

from viewer3d.argv import USAGE, Viewer3DArgs, parse_viewer3d_argv
from viewer3d.motion import (
    CATCHUP_BACKLOG_TICKS,
    DEFAULT_REPLAY_TICK_SPACING_S,
    TickBuffer,
)
from viewer3d.trace_io import load_trace
from wraithnet.trace_schemas import (
    JetFrame,
    StrikeEvent,
    TargetFrame,
    TraceHeader,
    TraceTick,
)


def _jet(x: float, y: float, heading: float = 0.0) -> JetFrame:
    return JetFrame(
        agent_id="Jet_1",
        position=(x, y),
        hp=5,
        payload={"JDAM": 1},
        destroyed=False,
        jammed=False,
        role="IDLE",
        heading=heading,
    )


def _tick(n: int, x: float, y: float, **overrides) -> TraceTick:
    data = {
        "tick": n,
        "jets": [_jet(x, y)],
        "targets": [TargetFrame(target_id="T1", hp_remaining=1, claimed=False)],
        "strikes": [],
        "running": True,
        "swarm_wiped_out": False,
    }
    data.update(overrides)
    return TraceTick(**data)


def test_replay_cli_parses_path_and_rate():
    parsed = parse_viewer3d_argv(["--replay", "results/trace.jsonl", "--rate", "2"])
    assert parsed == Viewer3DArgs(
        editor_mode=False,
        replay_path="results/trace.jsonl",
        playback_rate=2.0,
    )


def test_replay_cli_requires_path():
    with pytest.raises(SystemExit) as exc:
        parse_viewer3d_argv(["--replay"])
    assert USAGE in str(exc.value)


def test_offline_sampler_advances_with_rate_times_dt():
    buf = TickBuffer(interp_delay_s=0.0)
    ticks = [_tick(i, float(i * 10), 0.0) for i in range(5)]
    buf.load_scheduled_ticks(ticks, t0=0.0, spacing_s=1.0)
    assert buf.backlog_len == 5

    buf.advance(0.25, catchup=False)  # rate baked into dt by caller
    pose = buf.sample_jet_poses()["Jet_1"]
    assert pose.from_tick == 0
    assert pose.to_tick == 1
    assert pose.x == pytest.approx(2.5)

    # 2× playback: same wall dt covers twice the presentation distance.
    buf2 = TickBuffer(interp_delay_s=0.0)
    buf2.load_scheduled_ticks(ticks, t0=0.0, spacing_s=1.0)
    rate = 2.0
    buf2.advance(0.25 * rate, catchup=False)
    pose2 = buf2.sample_jet_poses()["Jet_1"]
    assert pose2.x == pytest.approx(5.0)


def test_load_scheduled_ticks_uses_default_spacing_constant():
    buf = TickBuffer(interp_delay_s=0.0)
    ticks = [_tick(0, 0.0, 0.0), _tick(1, 10.0, 0.0)]
    buf.load_scheduled_ticks(ticks)
    assert buf._entries[1][0] == pytest.approx(DEFAULT_REPLAY_TICK_SPACING_S)


def test_snap_skips_discarded_strike_events():
    """Catch-up snap must not emit strikes from discarded backlog ticks."""
    strike = StrikeEvent(agent_id="Jet_1", target_id="T1", weapon_type="JDAM")
    buf = TickBuffer(interp_delay_s=0.0, catchup_backlog_ticks=CATCHUP_BACKLOG_TICKS)
    for i in range(CATCHUP_BACKLOG_TICKS + 5):
        strikes = [strike] if i == 5 else []
        buf.push(_tick(i, float(i), 0.0, strikes=strikes), t_accept=float(i))
    assert buf.consume_snap_flag() is True
    buf.set_presentation_time(float(CATCHUP_BACKLOG_TICKS + 5))
    events = buf.pop_newly_presented_events()
    assert all(e.tick != 5 for e in events if e.kind == "STRIKE")


def test_trace_io_roundtrip(tmp_path: Path):
    header = TraceHeader(
        grid_width=100.0,
        grid_height=100.0,
        sensor_radius=65.0,
        jam_radius=12.0,
        sam_engagement_range=15.0,
        strike_range=1.0,
        jet_ids=["Jet_1"],
        initial_payload={"Jet_1": {"JDAM": 1}},
        target_ids=["T1"],
        target_types={"T1": "HARDENED_BUNKER"},
        target_hp_required={"T1": 1},
        sam_site_ids=[],
        jammer_positions={},
        target_positions={"T1": (50.0, 50.0)},
    )
    ticks = [_tick(0, 1.0, 2.0), _tick(1, 3.0, 4.0, running=False)]
    path = tmp_path / "trace.jsonl"
    path.write_text(
        header.model_dump_json() + "\n" + "\n".join(t.model_dump_json() for t in ticks),
        encoding="utf-8",
    )
    h2, t2 = load_trace(path)
    assert h2.jet_ids == ["Jet_1"]
    assert len(t2) == 2
    assert t2[1].running is False
