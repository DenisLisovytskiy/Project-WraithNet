"""Phase 23: summarize() must account for jets that left play."""

from pathlib import Path

import pytest

from wraithnet import config
from wraithnet.metrics import summarize
from wraithnet.scenario_schema import JetSpec, SamSiteSpec, ScenarioConfig, TargetSpec, new_scenario_data, scenario_from_data
from wraithnet.schemas import ActionIntent
from wraithnet.world import WraithNetModel


REPO_ROOT = Path(__file__).resolve().parents[1]


def _assert_three_way_split(model: WraithNetModel) -> None:
    summary = summarize(model)
    assert (
        summary["jets_destroyed"]
        + summary["jets_returned_to_base"]
        + summary["jets_still_flying"]
        == len(model._initial_jet_ids)
    )
    assert model.all_jet_ids() == model._initial_jet_ids


def _far_bunker_scenario(
    jets: list[JetSpec],
    home_base: tuple[float, float] = (10.0, 10.0),
    sam_sites: list[SamSiteSpec] | None = None,
) -> ScenarioConfig:
    return scenario_from_data(new_scenario_data(
        home_base=home_base,
        jets=jets,
        targets=[
            TargetSpec(
                target_id="Far_Bunker",
                position=(90.0, 90.0),
                hp_required=5,
                target_type="HARDENED_BUNKER",
            )
        ],
        sam_sites=sam_sites or [],
    ))


def test_summarize_keeps_distance_of_a_jet_destroyed_mid_run():
    model = WraithNetModel(
        scenario=_far_bunker_scenario(
            [
                JetSpec(
                    agent_id="Doomed",
                    position=(20.0, 20.0),
                    payload={"JDAM": 2, "AIM9X": 0},
                )
            ]
        )
    )
    intent = ActionIntent(
        agent_id="Doomed",
        tick=0,
        action_type="MOVE",
        target_position=(80.0, 20.0),
    )
    model.step({"Doomed": intent})
    before = summarize(model)
    assert "Doomed" in model.jets
    dist = before["per_jet_distance_traveled"]["Doomed"]
    payload = before["per_jet_total_payload_remaining"]["Doomed"]
    assert dist == config.JET_FRAME_CATALOG["STRIKE_FIGHTER"].max_speed
    assert payload == 2

    model.jets["Doomed"].hp = 0
    model.step({})
    assert "Doomed" not in model.jets
    after = summarize(model)
    assert after["per_jet_distance_traveled"]["Doomed"] == dist
    assert after["per_jet_total_payload_remaining"]["Doomed"] == payload
    assert after["total_distance_traveled"] == dist
    assert after["jets_destroyed"] == 1
    assert after["jets_still_flying"] == 0
    assert after["jets_returned_to_base"] == 0


def test_summarize_keeps_distance_of_a_jet_that_landed():
    home = (20.0, 20.0)
    start = (25.0, 20.0)
    model = WraithNetModel(
        scenario=_far_bunker_scenario(
            [
                JetSpec(
                    agent_id="Lander",
                    position=start,
                    payload={"JDAM": 1, "AIM9X": 1},
                )
            ],
            home_base=home,
        )
    )
    intent = ActionIntent(
        agent_id="Lander",
        tick=0,
        action_type="MOVE",
        target_position=home,
    )
    model.step({"Lander": intent})
    before = summarize(model)
    assert "Lander" in model.jets
    dist = before["per_jet_distance_traveled"]["Lander"]
    payload = before["per_jet_total_payload_remaining"]["Lander"]
    assert dist == 5.0
    assert payload == 2

    model.jets["Lander"].is_rtb = True
    model.step({})
    assert "Lander" not in model.jets
    after = summarize(model)
    assert after["per_jet_distance_traveled"]["Lander"] == dist
    assert after["per_jet_total_payload_remaining"]["Lander"] == payload
    assert after["total_distance_traveled"] == dist
    assert after["jets_returned_to_base"] == 1
    assert after["jets_destroyed"] == 0
    assert after["jets_still_flying"] == 0
    assert after["mission_aborted"] is True
    assert after["swarm_wiped_out"] is False


def test_summarize_accounts_for_one_landed_and_one_destroyed_in_the_same_run():
    home = (10.0, 10.0)
    model = WraithNetModel(
        scenario=_far_bunker_scenario(
            [
                JetSpec(
                    agent_id="Lander",
                    position=(15.0, 10.0),
                    payload={"JDAM": 1, "AIM9X": 0},
                ),
                JetSpec(
                    agent_id="Doomed",
                    position=(15.0, 20.0),
                    payload={"JDAM": 3, "AIM9X": 0},
                ),
            ],
            home_base=home,
        )
    )
    model.step(
        {
            "Lander": ActionIntent(
                agent_id="Lander",
                tick=0,
                action_type="MOVE",
                target_position=home,
            ),
            "Doomed": ActionIntent(
                agent_id="Doomed",
                tick=0,
                action_type="MOVE",
                target_position=(80.0, 20.0),
            ),
        }
    )
    before = summarize(model)
    lander_d = before["per_jet_distance_traveled"]["Lander"]
    doomed_d = before["per_jet_distance_traveled"]["Doomed"]
    assert lander_d > 0.0
    assert doomed_d > 0.0
    total_before = before["total_distance_traveled"]

    model.jets["Lander"].is_rtb = True
    model.jets["Doomed"].hp = 0
    model.step({})
    assert set(model.jets) == set()
    after = summarize(model)
    assert after["per_jet_distance_traveled"]["Lander"] == lander_d
    assert after["per_jet_distance_traveled"]["Doomed"] == doomed_d
    assert after["total_distance_traveled"] == total_before
    assert after["jets_destroyed"] == 1
    assert after["jets_returned_to_base"] == 1
    assert after["jets_still_flying"] == 0
    _assert_three_way_split(model)


def test_three_way_split_holds_every_tick_with_neither_removal():
    model = WraithNetModel()
    _assert_three_way_split(model)
    summary = summarize(model)
    assert summary["jets_destroyed"] == 0
    assert summary["jets_returned_to_base"] == 0
    assert summary["jets_still_flying"] == len(model._initial_jet_ids)
    assert summary["mission_aborted"] is False
    for _ in range(8):
        model.step({})
        _assert_three_way_split(model)
        s = summarize(model)
        assert s["jets_destroyed"] == 0
        assert s["jets_returned_to_base"] == 0
        assert s["jets_still_flying"] == 3


def test_three_way_split_holds_every_tick_of_an_rtb_run():
    home = (20.0, 20.0)
    model = WraithNetModel(
        scenario=_far_bunker_scenario(
            [
                JetSpec(
                    agent_id="Homebody",
                    position=(30.0, 20.0),
                    payload={"JDAM": 1, "AIM9X": 0},
                )
            ],
            home_base=home,
        )
    )
    model.jets["Homebody"].is_rtb = True
    _assert_three_way_split(model)
    ticks = 0
    while model.jets:
        ticks += 1
        assert ticks < 20, "RTB jet never landed"
        model.step(
            {
                "Homebody": ActionIntent(
                    agent_id="Homebody",
                    tick=model.tick,
                    action_type="MOVE",
                    target_position=home,
                )
            }
        )
        _assert_three_way_split(model)
    summary = summarize(model)
    assert summary["jets_returned_to_base"] == 1
    assert summary["jets_destroyed"] == 0
    assert summary["jets_still_flying"] == 0
    assert summary["mission_aborted"] is True


def test_three_way_split_holds_every_tick_of_a_sam_kill():
    sam_pos = (50.0, 50.0)
    model = WraithNetModel(
        scenario=_far_bunker_scenario(
            [
                JetSpec(
                    agent_id="Bomber",
                    position=sam_pos,
                    payload={"JDAM": 1, "AIM9X": 0},
                    frame_type="HEAVY_BOMBER",
                )
            ],
            sam_sites=[
                SamSiteSpec(
                    target_id="SAM_Pad",
                    position=sam_pos,
                    hp_required=1,
                    target_type="SAM_SITE",
                )
            ],
        )
    )
    _assert_three_way_split(model)
    ticks = 0
    while model.jets:
        ticks += 1
        assert ticks < 20, "HEAVY_BOMBER on a live SAM never died"
        model.step({})
        _assert_three_way_split(model)
    summary = summarize(model)
    assert summary["jets_destroyed"] == 1
    assert summary["jets_returned_to_base"] == 0
    assert summary["jets_still_flying"] == 0
    assert summary["swarm_wiped_out"] is True
    assert summary["mission_aborted"] is False


def test_legacy_survived_metric_key_is_gone():
    """The still-flying rename is a clean break — no leftover survived key."""
    needle = "jets_" + "survived"
    hits: list[str] = []
    for folder in ("wraithnet", "tests"):
        for path in (REPO_ROOT / folder).rglob("*.py"):
            if needle in path.read_text(encoding="utf-8"):
                hits.append(str(path.relative_to(REPO_ROOT)).replace("\\", "/"))
    assert hits == [], f"stale {needle} references remain: {hits}"


def test_datacollector_total_distance_stays_cumulative_after_jet_removed():
    """model_vars.csv must not drop a landed/destroyed jet's distance."""
    home = (20.0, 20.0)
    model = WraithNetModel(
        scenario=_far_bunker_scenario(
            [
                JetSpec(
                    agent_id="Lander",
                    position=(25.0, 20.0),
                    payload={"JDAM": 1, "AIM9X": 0},
                )
            ],
            home_base=home,
        )
    )
    model.step(
        {
            "Lander": ActionIntent(
                agent_id="Lander",
                tick=0,
                action_type="MOVE",
                target_position=home,
            )
        }
    )
    assert "Lander" in model.jets
    model.jets["Lander"].distance_traveled = 12.0
    model.datacollector.collect(model)
    before_rows = model.datacollector.get_model_vars_dataframe()[
        "TotalDistanceTraveled"
    ].tolist()
    assert before_rows[-1] == pytest.approx(12.0)

    model.jets["Lander"].is_rtb = True
    model.step({})
    assert "Lander" not in model.jets
    assert model.jet_outcomes["Lander"].distance_traveled == pytest.approx(12.0)

    after_rows = model.datacollector.get_model_vars_dataframe()[
        "TotalDistanceTraveled"
    ].tolist()
    assert after_rows[-1] == pytest.approx(12.0)
    assert after_rows == sorted(after_rows)
