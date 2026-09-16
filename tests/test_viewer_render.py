"""Headless tests for viewer coordinate mapping and trace loading (no Pygame)."""

import pytest

from wraithnet.config import SENSOR_RADIUS
from wraithnet.trace_schemas import JetFrame, TargetFrame, TraceHeader, TraceTick
from viewer.render import (
    CANVAS_SIZE,
    FUEL_BAR_WIDTH,
    HUD_WIDTH,
    MARGIN,
    WINDOW_SIZE,
    fuel_bar_fill_width,
    heading_indicator_endpoints,
    overlay_card_origin,
    world_length_to_screen,
    world_to_screen,
)
from viewer.trace_loader import load_trace


def _sample_header() -> TraceHeader:
    return TraceHeader(
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


def test_world_to_screen_corners_and_y_flip():
    gw, gh = 100.0, 100.0
    # World's (0, 0) is bottom-left in screen space (y-flip).
    assert world_to_screen((0.0, 0.0), gw, gh, CANVAS_SIZE, MARGIN) == (
        MARGIN,
        CANVAS_SIZE - MARGIN,
    )
    assert world_to_screen((gw, 0.0), gw, gh, CANVAS_SIZE, MARGIN) == (
        CANVAS_SIZE - MARGIN,
        CANVAS_SIZE - MARGIN,
    )
    assert world_to_screen((0.0, gh), gw, gh, CANVAS_SIZE, MARGIN) == (MARGIN, MARGIN)
    assert world_to_screen((gw, gh), gw, gh, CANVAS_SIZE, MARGIN) == (
        CANVAS_SIZE - MARGIN,
        MARGIN,
    )
    assert world_to_screen((gw / 2, gh / 2), gw, gh, CANVAS_SIZE, MARGIN) == (
        CANVAS_SIZE // 2,
        CANVAS_SIZE // 2,
    )


def test_world_length_to_screen_scales_sensor_radius():
    usable = CANVAS_SIZE - 2 * MARGIN
    expected = int(SENSOR_RADIUS / 100.0 * usable)
    assert world_length_to_screen(SENSOR_RADIUS, 100.0, CANVAS_SIZE, MARGIN) == expected


def test_window_is_1200_by_900():
    assert WINDOW_SIZE == (CANVAS_SIZE + HUD_WIDTH, CANVAS_SIZE) == (1200, 900)


def test_load_trace_round_trips_synthetic_file(tmp_path):
    header = _sample_header()
    ticks = [
        TraceTick(
            tick=0,
            jets=[
                JetFrame(
                    agent_id="Jet_1",
                    position=(70.0, 70.0),
                    hp=5,
                    payload={"JDAM": 1, "AIM9X": 0},
                    destroyed=False,
                    jammed=False,
                    role="IDLE",
                )
            ],
            targets=[TargetFrame(target_id="Target_A", hp_remaining=5, claimed=False)],
            strikes=[],
            running=True,
            swarm_wiped_out=False,
        )
    ]
    path = tmp_path / "trace.jsonl"
    path.write_text(
        header.model_dump_json() + "\n" + "\n".join(t.model_dump_json() for t in ticks) + "\n",
        encoding="utf-8",
    )
    loaded_header, loaded_ticks = load_trace(path)
    assert loaded_header == header
    assert loaded_ticks == ticks


def test_load_trace_rejects_empty_file(tmp_path):
    path = tmp_path / "empty.jsonl"
    path.write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="empty"):
        load_trace(path)


def test_load_trace_rejects_header_only(tmp_path):
    path = tmp_path / "header_only.jsonl"
    path.write_text(_sample_header().model_dump_json() + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="no recorded ticks"):
        load_trace(path)


def test_heading_indicator_accounts_for_y_flip():
    header = _sample_header()
    origin, tip = heading_indicator_endpoints(
        (50.0, 50.0), 0.0, header, length_world=10.0
    )
    # Heading 0° is +y in world space, which is "up" (smaller screen y).
    assert origin[0] == tip[0]
    assert tip[1] < origin[1]

    origin90, tip90 = heading_indicator_endpoints(
        (50.0, 50.0), 90.0, header, length_world=10.0
    )
    # Heading 90° is +x, which is right on screen; y-flip does not reverse x.
    assert tip90[0] > origin90[0]
    assert tip90[1] == origin90[1]


def test_fuel_bar_fill_width_is_proportional():
    assert fuel_bar_fill_width(0.0, 80.0) == 0
    assert fuel_bar_fill_width(80.0, 80.0) == FUEL_BAR_WIDTH
    assert fuel_bar_fill_width(40.0, 80.0) == FUEL_BAR_WIDTH // 2
    assert fuel_bar_fill_width(100.0, 80.0) == FUEL_BAR_WIDTH
    assert fuel_bar_fill_width(10.0, 0.0) == 0


def test_overlay_card_origin_keeps_the_card_inside_the_canvas():
    ox, oy = overlay_card_origin(440, 220)
    assert ox >= 0 and oy >= 0
    assert ox + 440 <= CANVAS_SIZE
    assert oy + 220 <= CANVAS_SIZE
    assert ox == (CANVAS_SIZE - 440) // 2
    assert oy == (CANVAS_SIZE - 220) // 2


def test_overlay_card_origin_clamps_an_oversized_card():
    ox, oy = overlay_card_origin(CANVAS_SIZE + 80, CANVAS_SIZE + 40)
    assert (ox, oy) == (0, 0)
