"""Round-trip and validation tests for the simulation trace schemas."""

import pytest
from pydantic import ValidationError

from wraithnet.trace_schemas import (
    JetFrame,
    StrikeEvent,
    TargetFrame,
    TraceHeader,
    TraceTick,
)


def _sample_header(**overrides) -> TraceHeader:
    data = {
        "grid_width": 100.0,
        "grid_height": 100.0,
        "sensor_radius": 65.0,
        "jam_radius": 12.0,
        "sam_engagement_range": 15.0,
        "strike_range": 1.0,
        "jet_ids": ["Jet_1", "Jet_2"],
        "initial_payload": {
            "Jet_1": {"JDAM": 1, "AIM9X": 0},
            "Jet_2": {"JDAM": 4, "AIM9X": 0},
        },
        "target_ids": ["Target_A", "SAM_Site_1"],
        "target_types": {
            "Target_A": "HARDENED_BUNKER",
            "SAM_Site_1": "SAM_SITE",
        },
        "target_hp_required": {"Target_A": 5, "SAM_Site_1": 1},
        "sam_site_ids": ["SAM_Site_1"],
        "jammer_positions": {"EW_Jammer_1": (46.0, 19.0)},
        "target_positions": {
            "Target_A": (75.0, 75.0),
            "SAM_Site_1": (40.0, 85.0),
        },
    }
    data.update(overrides)
    return TraceHeader(**data)


def _sample_jet(**overrides) -> JetFrame:
    data = {
        "agent_id": "Jet_1",
        "position": (70.0, 70.0),
        "hp": 5,
        "payload": {"JDAM": 1, "AIM9X": 0},
        "destroyed": False,
        "jammed": False,
        "role": "IDLE",
    }
    data.update(overrides)
    return JetFrame(**data)


def _sample_tick(**overrides) -> TraceTick:
    data = {
        "tick": 0,
        "jets": [_sample_jet()],
        "targets": [
            TargetFrame(target_id="Target_A", hp_remaining=5, claimed=False),
        ],
        "strikes": [],
        "running": True,
        "swarm_wiped_out": False,
    }
    data.update(overrides)
    return TraceTick(**data)


def test_trace_header_json_round_trip():
    header = _sample_header()
    restored = TraceHeader.model_validate_json(header.model_dump_json())
    assert restored == header


def test_trace_tick_json_round_trip():
    tick = _sample_tick(
        tick=3,
        strikes=[
            StrikeEvent(agent_id="Jet_1", target_id="Target_A", weapon_type="JDAM"),
        ],
        jets=[
            _sample_jet(role="CONTRACTOR", jammed=True, position=(46.0, 19.0)),
        ],
    )
    restored = TraceTick.model_validate_json(tick.model_dump_json())
    assert restored == tick


@pytest.mark.parametrize("weapon_type", ["JDAM", "AIM9X"])
def test_strike_event_accepts_valid_weapon_type(weapon_type):
    event = StrikeEvent(
        agent_id="Jet_1", target_id="Target_A", weapon_type=weapon_type
    )
    assert event.weapon_type == weapon_type


def test_strike_event_rejects_invalid_weapon_type():
    with pytest.raises(ValidationError):
        StrikeEvent(agent_id="Jet_1", target_id="Target_A", weapon_type="TOMAHAWK")


def test_jet_frame_accepts_valid_weapon_payload():
    frame = _sample_jet(payload={"JDAM": 2, "AIM9X": 1})
    assert frame.payload == {"JDAM": 2, "AIM9X": 1}


def test_jet_frame_rejects_invalid_weapon_type():
    with pytest.raises(ValidationError):
        _sample_jet(payload={"TOMAHAWK": 1})


@pytest.mark.parametrize(
    "target_type", ["HARDENED_BUNKER", "MOBILE_RADAR", "SAM_SITE"]
)
def test_trace_header_accepts_each_target_type(target_type):
    header = _sample_header(
        target_ids=["T"],
        target_types={"T": target_type},
        target_hp_required={"T": 1},
        target_positions={"T": (1.0, 2.0)},
        sam_site_ids=["T"] if target_type == "SAM_SITE" else [],
    )
    assert header.target_types["T"] == target_type


def test_trace_header_rejects_invalid_target_type():
    with pytest.raises(ValidationError):
        _sample_header(target_types={"Target_A": "SOFT_TENT"})


def test_target_frame_constructs():
    frame = TargetFrame(target_id="Target_A", hp_remaining=3, claimed=True)
    assert frame.claimed is True
    assert frame.hp_remaining == 3
