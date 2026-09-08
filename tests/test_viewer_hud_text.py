"""Layer-1 tests for viewer.hud_text. No pygame import."""

from __future__ import annotations

from viewer.hud_text import (
    jets_line,
    overlay_lines,
    sensor_line,
    status_line,
    targets_line,
    tick_line,
)
from viewer.playback import compute_run_summary
from wraithnet.trace_schemas import JetFrame, StrikeEvent, TargetFrame, TraceHeader, TraceTick


def _header() -> TraceHeader:
    return TraceHeader(
        grid_width=100.0,
        grid_height=100.0,
        sensor_radius=65.0,
        jam_radius=12.0,
        sam_engagement_range=15.0,
        strike_range=1.0,
        jet_ids=["Jet_1"],
        initial_payload={"Jet_1": {"JDAM": 1, "AIM9X": 0}},
        target_ids=["Target_A", "SAM_Site_1"],
        target_types={"Target_A": "HARDENED_BUNKER", "SAM_Site_1": "SAM_SITE"},
        target_hp_required={"Target_A": 5, "SAM_Site_1": 1},
        sam_site_ids=["SAM_Site_1"],
        jammer_positions={},
        target_positions={"Target_A": (75.0, 75.0), "SAM_Site_1": (40.0, 85.0)},
    )


def _tick() -> TraceTick:
    return TraceTick(
        tick=3,
        jets=[
            JetFrame(
                agent_id="Jet_1",
                position=(10.0, 10.0),
                hp=5,
                payload={"JDAM": 1, "AIM9X": 0},
                destroyed=False,
                jammed=False,
                role="IDLE",
            )
        ],
        targets=[
            TargetFrame(target_id="Target_A", hp_remaining=0, claimed=False),
            TargetFrame(target_id="SAM_Site_1", hp_remaining=1, claimed=False),
        ],
        strikes=[],
        running=True,
        swarm_wiped_out=False,
    )


def test_hud_lines_match_current_formats():
    header = _header()
    tick = _tick()
    assert "Tick 3" in tick_line(tick, tick_index=3, total_ticks=10)
    assert "(4/10)" in tick_line(tick, tick_index=3, total_ticks=10)
    assert targets_line(tick) == "Targets  1/2 destroyed"
    assert jets_line(header, tick) == "Jets     1/1 alive"
    assert sensor_line(True) == "Sensor rings: ON"
    assert sensor_line(False) == "Sensor rings: OFF"


def test_status_line_playing_paused_and_none():
    assert "PLAYING" in status_line(True, 4.0)
    assert "PAUSED" in status_line(False, 4.0)
    assert status_line(False, 0.0) is None


def test_overlay_lines_match_compute_run_summary():
    header = _header()
    t0 = TraceTick(
        tick=0,
        jets=[
            JetFrame(
                agent_id="Jet_1",
                position=(10.0, 10.0),
                hp=5,
                payload={"JDAM": 1, "AIM9X": 0},
                destroyed=False,
                jammed=False,
                role="IDLE",
            )
        ],
        targets=[
            TargetFrame(target_id="Target_A", hp_remaining=5, claimed=False),
            TargetFrame(target_id="SAM_Site_1", hp_remaining=1, claimed=False),
        ],
        strikes=[],
        running=True,
        swarm_wiped_out=False,
    )
    t1 = TraceTick(
        tick=1,
        jets=[],
        targets=[
            TargetFrame(target_id="Target_A", hp_remaining=0, claimed=False),
            TargetFrame(target_id="SAM_Site_1", hp_remaining=0, claimed=False),
        ],
        strikes=[
            StrikeEvent(agent_id="Jet_1", target_id="Target_A", weapon_type="JDAM"),
            StrikeEvent(agent_id="Jet_1", target_id="SAM_Site_1", weapon_type="AIM9X"),
        ],
        running=False,
        swarm_wiped_out=True,
    )
    summary = compute_run_summary(header, [t0, t1])
    lines = overlay_lines(header, [t0, t1])
    assert lines[0] == "SWARM WIPED OUT"
    assert (
        f"Targets destroyed  {summary.targets_destroyed}/{summary.total_targets}"
        in lines
    )
    assert f"Jets destroyed     {summary.jets_destroyed}/{summary.total_jets}" in lines
    assert f"Missiles fired     {summary.total_missiles_fired}" in lines[5]
    for weapon, count in summary.per_weapon_type_missiles_fired.items():
        assert f"{weapon} {count}" in lines[5]
