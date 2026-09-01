"""Headless Pygame smoke tests for the replay viewer.

Skipped cleanly when pygame is not installed (CI that only has
requirements-dev.txt). Must set SDL_VIDEODRIVER before pygame is imported.
"""

import os

os.environ.setdefault("SDL_VIDEODRIVER", "dummy")

import pytest

pygame = pytest.importorskip("pygame")

from wraithnet.trace_schemas import JetFrame, StrikeEvent, TargetFrame, TraceHeader, TraceTick
from viewer.render import (
    WINDOW_SIZE,
    draw_destruction_pulses,
    draw_end_of_run_overlay,
    draw_grid_background,
    draw_home_base,
    draw_hud,
    draw_jam_transition_flash,
    draw_jammer,
    draw_jets,
    draw_strike_highlights,
    draw_targets,
)
from viewer.playback import RunSummary, advance_playback, compute_highlights
from viewer.trace_loader import load_trace


def _header() -> TraceHeader:
    return TraceHeader(
        grid_width=100.0,
        grid_height=100.0,
        sensor_radius=65.0,
        jam_radius=12.0,
        sam_engagement_range=15.0,
        strike_range=1.0,
        jet_ids=["Jet_1", "Jet_2"],
        initial_payload={
            "Jet_1": {"JDAM": 1, "AIM9X": 0},
            "Jet_2": {"JDAM": 4, "AIM9X": 0},
        },
        target_ids=["Target_A", "SAM_Site_1"],
        target_types={"Target_A": "HARDENED_BUNKER", "SAM_Site_1": "SAM_SITE"},
        target_hp_required={"Target_A": 5, "SAM_Site_1": 1},
        sam_site_ids=["SAM_Site_1"],
        jammer_positions={"EW_Jammer_1": (46.0, 19.0)},
        target_positions={"Target_A": (75.0, 75.0), "SAM_Site_1": (40.0, 85.0)},
    )


def _jet(agent_id, position, jammed=False, role="IDLE", hp=5) -> JetFrame:
    return JetFrame(
        agent_id=agent_id,
        position=position,
        hp=hp,
        payload={"JDAM": 1, "AIM9X": 0},
        destroyed=False,
        jammed=jammed,
        role=role,
    )


def _rich_trace() -> tuple[TraceHeader, list[TraceTick]]:
    """≥5 ticks covering a strike, a target-destruction, and a jam transition."""
    header = _header()
    j1 = lambda **kw: _jet("Jet_1", (70.0, 70.0), **kw)
    j2 = lambda **kw: _jet("Jet_2", (5.0, 95.0), **kw)
    t_a = lambda hp, claimed=False: TargetFrame(
        target_id="Target_A", hp_remaining=hp, claimed=claimed
    )
    t_sam = lambda hp: TargetFrame(target_id="SAM_Site_1", hp_remaining=hp, claimed=False)

    ticks = [
        TraceTick(
            tick=0,
            jets=[j1(), j2()],
            targets=[t_a(5), t_sam(1)],
            strikes=[],
            running=True,
            swarm_wiped_out=False,
        ),
        TraceTick(
            tick=1,
            jets=[j1(role="MANAGER"), j2(role="CONTRACTOR")],
            targets=[t_a(5, claimed=True), t_sam(1)],
            strikes=[],
            running=True,
            swarm_wiped_out=False,
        ),
        TraceTick(
            tick=2,
            jets=[j1(jammed=True, role="MANAGER"), j2(role="CONTRACTOR")],
            targets=[t_a(4, claimed=True), t_sam(1)],
            strikes=[
                StrikeEvent(agent_id="Jet_1", target_id="Target_A", weapon_type="JDAM"),
            ],
            running=True,
            swarm_wiped_out=False,
        ),
        TraceTick(
            tick=3,
            jets=[j1(jammed=True, role="IDLE"), j2(role="CONTRACTOR")],
            targets=[t_a(0), t_sam(0)],
            strikes=[],
            running=True,
            swarm_wiped_out=False,
        ),
        TraceTick(
            tick=4,
            jets=[j2(role="IDLE")],
            targets=[t_a(0), t_sam(0)],
            strikes=[],
            running=False,
            swarm_wiped_out=False,
        ),
    ]
    return header, ticks


def _three_tick_trace() -> tuple[TraceHeader, list[TraceTick]]:
    """Phase 12 Part D: 1 header + 3 ticks, one jet, one target, one jammer."""
    header = TraceHeader(
        grid_width=100.0,
        grid_height=100.0,
        sensor_radius=65.0,
        jam_radius=12.0,
        sam_engagement_range=15.0,
        strike_range=1.0,
        jet_ids=["Jet_1"],
        initial_payload={"Jet_1": {"JDAM": 1, "AIM9X": 0}},
        target_ids=["Target_A"],
        target_types={"Target_A": "HARDENED_BUNKER"},
        target_hp_required={"Target_A": 5},
        sam_site_ids=[],
        jammer_positions={"EW_Jammer_1": (46.0, 19.0)},
        target_positions={"Target_A": (75.0, 75.0)},
    )
    ticks = [
        TraceTick(
            tick=n,
            jets=[
                _jet(
                    "Jet_1",
                    (70.0 + n, 70.0),
                    jammed=n == 1,
                    role="IDLE" if n == 0 else "CONTRACTOR",
                    hp=5 - n,
                )
            ],
            targets=[
                TargetFrame(target_id="Target_A", hp_remaining=5 - n, claimed=n > 0)
            ],
            strikes=[],
            running=True,
            swarm_wiped_out=False,
        )
        for n in range(3)
    ]
    return header, ticks


def test_draw_pipeline_raises_nothing_across_three_tick_trace(tmp_path):
    header, ticks = _three_tick_trace()
    path = tmp_path / "trace.jsonl"
    path.write_text(
        header.model_dump_json()
        + "\n"
        + "\n".join(t.model_dump_json() for t in ticks)
        + "\n",
        encoding="utf-8",
    )
    loaded_header, loaded_ticks = load_trace(path)
    assert loaded_header == header
    assert len(loaded_ticks) == 3

    pygame.init()
    try:
        surface = pygame.Surface(WINDOW_SIZE)
        assert surface.get_size() == (1200, 900)
        for i, current in enumerate(loaded_ticks):
            draw_grid_background(surface, loaded_header)
            draw_jammer(surface, loaded_header)
            draw_home_base(surface, loaded_header)
            draw_targets(surface, loaded_header, current)
            draw_jets(surface, loaded_header, current, show_sensor_radius=i % 2 == 0)
            draw_hud(surface, loaded_header, current, i, len(loaded_ticks), i % 2 == 0)
    finally:
        pygame.quit()


def test_draw_pipeline_raises_nothing_across_rich_trace(tmp_path):
    header, ticks = _rich_trace()
    path = tmp_path / "trace.jsonl"
    path.write_text(
        header.model_dump_json()
        + "\n"
        + "\n".join(t.model_dump_json() for t in ticks)
        + "\n",
        encoding="utf-8",
    )
    loaded_header, loaded_ticks = load_trace(path)
    assert loaded_header == header
    assert len(loaded_ticks) == 5

    pygame.init()
    try:
        surface = pygame.Surface(WINDOW_SIZE)
        for i, current in enumerate(loaded_ticks):
            previous = loaded_ticks[i - 1] if i > 0 else None
            highlights = compute_highlights(previous, current)
            if i == 0:
                assert highlights.newly_jammed_jet_ids == []
                assert highlights.newly_destroyed_target_ids == []
            if i == 2:
                assert highlights.strikes
                assert "Jet_1" in highlights.newly_jammed_jet_ids
            if i == 3:
                assert set(highlights.newly_destroyed_target_ids) == {
                    "Target_A",
                    "SAM_Site_1",
                }
            if i == 4:
                assert highlights.newly_destroyed_jet_ids == ["Jet_1"]
            draw_grid_background(surface, loaded_header)
            draw_jammer(surface, loaded_header)
            draw_home_base(surface, loaded_header)
            draw_targets(surface, loaded_header, current)
            draw_jets(surface, loaded_header, current, show_sensor_radius=True)
            draw_strike_highlights(surface, loaded_header, current, highlights, previous)
            draw_destruction_pulses(surface, loaded_header, current, highlights, previous)
            draw_jam_transition_flash(surface, loaded_header, current, highlights)
            draw_hud(
                surface,
                loaded_header,
                current,
                i,
                len(loaded_ticks),
                True,
                playing=False,
                ticks_per_second=4.0,
            )
            if i == len(loaded_ticks) - 1:
                draw_end_of_run_overlay(surface, loaded_header, loaded_ticks)
    finally:
        pygame.quit()


def test_autoplay_accumulator_advances_then_pauses_at_end():
    """Phase 13 Part C: synthetic dt_ms sequence drives tick_index without a display."""
    last_index = 4
    tick_index, playing, acc = 0, True, 0.0
    for dt_ms in (250.0, 250.0, 250.0, 250.0, 250.0):
        tick_index, playing, acc = advance_playback(
            tick_index, playing, acc, dt_ms, 4.0, last_index
        )
    assert tick_index == last_index
    assert playing is False
    tick_index, playing, acc = advance_playback(
        tick_index, True, 0.0, 5000.0, 20.0, last_index
    )
    assert tick_index == last_index
    assert playing is False


def test_end_of_run_overlay_mission_aborted_and_fuel_bars_do_not_raise():
    pygame.init()
    try:
        surface = pygame.Surface(WINDOW_SIZE)
        header = _header()
        header.jet_frame_types = {"Jet_1": "STRIKE_FIGHTER"}
        header.jet_frame_stats = {
            "STRIKE_FIGHTER": {"fuel_capacity": 80.0, "max_speed": 5.0, "hp": 5.0}
        }
        summary = RunSummary(
            solved=False,
            swarm_wiped_out=False,
            mission_aborted=True,
            targets_destroyed=0,
            total_targets=2,
            jets_destroyed=0,
            jets_returned_to_base=2,
            jets_still_flying=0,
            total_jets=2,
            total_missiles_fired=0,
            per_weapon_type_missiles_fired={},
        )
        empty = TraceTick(
            tick=1,
            jets=[],
            targets=[TargetFrame(target_id="Target_A", hp_remaining=5, claimed=False)],
            strikes=[],
            running=False,
            swarm_wiped_out=False,
            mission_aborted=True,
        )
        draw_end_of_run_overlay(surface, header, [empty], summary=summary)
        for fuel in (0.0, 40.0, 80.0):
            tick = TraceTick(
                tick=0,
                jets=[
                    JetFrame(
                        agent_id="Jet_1",
                        position=(10.0, 10.0),
                        hp=5,
                        payload={"JDAM": 1, "AIM9X": 0},
                        destroyed=False,
                        jammed=False,
                        role="RTB",
                        heading=45.0,
                        fuel=fuel,
                    )
                ],
                targets=[TargetFrame(target_id="Target_A", hp_remaining=5, claimed=False)],
                strikes=[],
                running=True,
                swarm_wiped_out=False,
            )
            draw_jets(surface, header, tick, show_sensor_radius=False)
            draw_home_base(surface, header)
    finally:
        pygame.quit()
