"""Pure-logic tests for viewer playback highlights and auto-play stepping."""

from viewer.playback import (
    advance_playback,
    compute_highlights,
    compute_run_summary,
)
from wraithnet.trace_schemas import (
    JetFrame,
    JetOutcomeEvent,
    StrikeEvent,
    TargetFrame,
    TraceHeader,
    TraceTick,
)


def _jet(agent_id="Jet_1", jammed=False, hp=5, **kwargs) -> JetFrame:
    data = dict(
        agent_id=agent_id,
        position=(10.0, 10.0),
        hp=hp,
        payload={"JDAM": 1, "AIM9X": 0},
        destroyed=False,
        jammed=jammed,
        role="IDLE",
    )
    data.update(kwargs)
    return JetFrame(**data)


def _target(target_id="Target_A", hp_remaining=5, claimed=False) -> TargetFrame:
    return TargetFrame(target_id=target_id, hp_remaining=hp_remaining, claimed=claimed)


def _tick(n, jets=None, targets=None, strikes=None, **kwargs) -> TraceTick:
    data = dict(
        tick=n,
        jets=jets if jets is not None else [_jet()],
        targets=targets if targets is not None else [_target()],
        strikes=strikes if strikes is not None else [],
        running=True,
        swarm_wiped_out=False,
    )
    data.update(kwargs)
    return TraceTick(**data)


def test_compute_highlights_none_previous_has_no_false_positives():
    strike = StrikeEvent(agent_id="Jet_1", target_id="Target_A", weapon_type="JDAM")
    tick0 = _tick(
        0,
        jets=[_jet(jammed=True)],
        targets=[_target(hp_remaining=0)],
        strikes=[strike],
    )
    highlights = compute_highlights(None, tick0)
    assert highlights.strikes == [strike]
    assert highlights.newly_destroyed_target_ids == []
    assert highlights.newly_destroyed_jet_ids == []
    assert highlights.newly_jammed_jet_ids == []
    assert highlights.newly_unjammed_jet_ids == []


def test_newly_destroyed_target_is_the_transition_only():
    prev = _tick(1, targets=[_target(hp_remaining=1)])
    curr = _tick(2, targets=[_target(hp_remaining=0)])
    highlights = compute_highlights(prev, curr)
    assert highlights.newly_destroyed_target_ids == ["Target_A"]

    already_dead = compute_highlights(curr, _tick(3, targets=[_target(hp_remaining=0)]))
    assert already_dead.newly_destroyed_target_ids == []


def test_newly_destroyed_jet_is_absent_from_current():
    prev = _tick(4, jets=[_jet("Jet_1"), _jet("Jet_2", position=(20.0, 20.0))])
    curr = _tick(5, jets=[_jet("Jet_2", position=(20.0, 20.0))])
    highlights = compute_highlights(prev, curr)
    assert highlights.newly_destroyed_jet_ids == ["Jet_1"]


def test_jam_false_to_true_and_reverse():
    prev = _tick(6, jets=[_jet(jammed=False)])
    curr = _tick(7, jets=[_jet(jammed=True)])
    into_jam = compute_highlights(prev, curr)
    assert into_jam.newly_jammed_jet_ids == ["Jet_1"]
    assert into_jam.newly_unjammed_jet_ids == []

    out_of_jam = compute_highlights(curr, _tick(8, jets=[_jet(jammed=False)]))
    assert out_of_jam.newly_unjammed_jet_ids == ["Jet_1"]
    assert out_of_jam.newly_jammed_jet_ids == []


def test_sustained_jam_is_not_a_transition():
    prev = _tick(9, jets=[_jet(jammed=True)])
    curr = _tick(10, jets=[_jet(jammed=True)])
    highlights = compute_highlights(prev, curr)
    assert highlights.newly_jammed_jet_ids == []
    assert highlights.newly_unjammed_jet_ids == []


def test_advance_playback_steps_then_auto_pauses_at_end():
    last_index = 4
    tick_index, playing, acc = 0, True, 0.0
    # 4.0 tps => 250 ms per tick. Five 250 ms frames should land on 4 and pause.
    for _ in range(5):
        tick_index, playing, acc = advance_playback(
            tick_index, playing, acc, 250.0, 4.0, last_index
        )
    assert tick_index == last_index
    assert playing is False
    # Further time must not wrap or go out of bounds.
    tick_index, playing, acc = advance_playback(
        tick_index, True, 0.0, 1000.0, 4.0, last_index
    )
    assert tick_index == last_index
    assert playing is False


def test_advance_playback_paused_does_not_advance():
    tick_index, playing, acc = advance_playback(2, False, 0.0, 500.0, 4.0, 10)
    assert tick_index == 2
    assert playing is False


def test_compute_run_summary_from_trace():
    header = TraceHeader(
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
        target_ids=["Target_A", "Target_B"],
        target_types={"Target_A": "HARDENED_BUNKER", "Target_B": "MOBILE_RADAR"},
        target_hp_required={"Target_A": 5, "Target_B": 1},
        sam_site_ids=[],
        jammer_positions={},
        target_positions={"Target_A": (75.0, 75.0), "Target_B": (25.0, 25.0)},
    )
    ticks = [
        _tick(
            0,
            jets=[_jet("Jet_1"), _jet("Jet_2")],
            targets=[_target("Target_A", 5), _target("Target_B", 1)],
        ),
        _tick(
            1,
            jets=[_jet("Jet_2")],
            targets=[_target("Target_A", 0), _target("Target_B", 0)],
            strikes=[
                StrikeEvent(agent_id="Jet_1", target_id="Target_A", weapon_type="JDAM"),
                StrikeEvent(agent_id="Jet_2", target_id="Target_B", weapon_type="AIM9X"),
            ],
        ),
    ]
    summary = compute_run_summary(header, ticks)
    assert summary.solved is True
    assert summary.swarm_wiped_out is False
    assert summary.targets_destroyed == 2
    assert summary.jets_destroyed == 1
    assert summary.total_missiles_fired == 2
    assert summary.per_weapon_type_missiles_fired == {"JDAM": 1, "AIM9X": 1}


def test_rtb_is_not_reported_as_a_destroyed_highlight():
    prev = _tick(4, jets=[_jet("Lander"), _jet("Doomed", position=(20.0, 20.0))])
    curr = _tick(
        5,
        jets=[],
        jet_outcomes=[
            JetOutcomeEvent(agent_id="Lander", tick=5, outcome="returned_to_base"),
            JetOutcomeEvent(agent_id="Doomed", tick=5, outcome="destroyed"),
        ],
    )
    highlights = compute_highlights(prev, curr)
    assert highlights.newly_rtb_jet_ids == []
    assert highlights.newly_destroyed_jet_ids == ["Doomed"]


def test_newly_rtb_jet_ids_is_the_role_transition_not_landing():
    prev = _tick(4, jets=[_jet("Lander", role="CONTRACTOR"), _jet("Stay")])
    curr = _tick(5, jets=[_jet("Lander", role="RTB"), _jet("Stay")])
    highlights = compute_highlights(prev, curr)
    assert highlights.newly_rtb_jet_ids == ["Lander"]
    already = compute_highlights(curr, _tick(6, jets=[_jet("Lander", role="RTB"), _jet("Stay")]))
    assert already.newly_rtb_jet_ids == []


def test_compute_run_summary_three_way_split_and_mission_aborted():
    header = TraceHeader(
        grid_width=100.0,
        grid_height=100.0,
        sensor_radius=65.0,
        jam_radius=12.0,
        sam_engagement_range=15.0,
        strike_range=1.0,
        jet_ids=["Lander", "Flyer"],
        initial_payload={
            "Lander": {"JDAM": 1, "AIM9X": 0},
            "Flyer": {"JDAM": 1, "AIM9X": 0},
        },
        target_ids=["Target_A"],
        target_types={"Target_A": "HARDENED_BUNKER"},
        target_hp_required={"Target_A": 5},
        sam_site_ids=[],
        jammer_positions={},
        target_positions={"Target_A": (75.0, 75.0)},
    )
    ticks = [
        _tick(0, jets=[_jet("Lander"), _jet("Flyer")]),
        _tick(
            1,
            jets=[_jet("Flyer")],
            jet_outcomes=[
                JetOutcomeEvent(agent_id="Lander", tick=1, outcome="returned_to_base"),
            ],
            mission_aborted=False,
            running=True,
        ),
        _tick(
            2,
            jets=[],
            jet_outcomes=[
                JetOutcomeEvent(agent_id="Flyer", tick=2, outcome="returned_to_base"),
            ],
            mission_aborted=True,
            running=False,
        ),
    ]
    summary = compute_run_summary(header, ticks)
    assert summary.jets_destroyed == 0
    assert summary.jets_returned_to_base == 2
    assert summary.jets_still_flying == 0
    assert summary.mission_aborted is True
    assert summary.swarm_wiped_out is False
