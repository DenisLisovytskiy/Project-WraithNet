"""Headless tests for viewer coordinate mapping and trace loading (no Pygame)."""

from wraithnet.config import SENSOR_RADIUS
from wraithnet.trace_schemas import JetFrame, TargetFrame, TraceHeader, TraceTick
from viewer.render import CANVAS_SIZE, MARGIN, world_length_to_screen, world_to_screen
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
