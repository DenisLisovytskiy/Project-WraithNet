"""Final PoC scenario validation — proves decentralized multi-agent cooperation."""

import asyncio

import pytest

from wraithnet import config
from wraithnet.bridge import WraithNetBridge
from wraithnet.metrics import export_results, summarize


@pytest.fixture
def fast_cnp(monkeypatch):
    """Keep wall-clock short under pytest; CNP logic is unchanged."""
    monkeypatch.setattr(config, "BID_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(config, "MAX_TICK_WAIT_SECONDS", 1.0)


@pytest.mark.asyncio
async def test_scenario_fully_resolves(fast_cnp):
    bridge = WraithNetBridge()
    await asyncio.wait_for(bridge.run(max_ticks=500), timeout=60)
    summary = summarize(bridge.model)

    assert summary["solved"] is True
    assert summary["swarm_wiped_out"] is False
    assert summary["targets_destroyed"] == 3  # A, B, SAM_Site_1
    assert bridge.model.tick < 500


@pytest.mark.asyncio
async def test_target_a_required_genuine_cooperation(fast_cnp):
    """Target_A (HARDENED_BUNKER, hp=5) needs ≥2 JDAM-carrying contributors."""
    bridge = WraithNetBridge()
    await asyncio.wait_for(bridge.run(max_ticks=500), timeout=60)
    summary = summarize(bridge.model)

    contributors_a = summary["contributors_by_target"].get("Target_A", [])
    assert len(contributors_a) >= 2
    a_strikes = [e for e in bridge.model.strike_log if e["target_id"] == "Target_A"]
    assert all(e["weapon_type"] == "JDAM" for e in a_strikes)
    assert len(a_strikes) >= 5


@pytest.mark.asyncio
async def test_target_b_does_not_require_unnecessary_cooperation(fast_cnp):
    bridge = WraithNetBridge()
    await asyncio.wait_for(bridge.run(max_ticks=500), timeout=60)
    summary = summarize(bridge.model)

    contributors_b = summary["contributors_by_target"].get("Target_B", [])
    assert len(contributors_b) == 1
    b_strikes = [e for e in bridge.model.strike_log if e["target_id"] == "Target_B"]
    assert all(e["weapon_type"] == "AGM65" for e in b_strikes)
    assert len(b_strikes) == 1


@pytest.mark.asyncio
async def test_sam_site_destroyed_with_jdam(fast_cnp):
    bridge = WraithNetBridge()
    await asyncio.wait_for(bridge.run(max_ticks=500), timeout=60)

    assert bridge.model.targets["SAM_Site_1"].hp_remaining == 0
    sam_strikes = [
        e for e in bridge.model.strike_log if e["target_id"] == "SAM_Site_1"
    ]
    assert len(sam_strikes) >= 1
    assert all(e["weapon_type"] == "JDAM" for e in sam_strikes)


@pytest.mark.asyncio
async def test_total_missiles_fired_covers_all_targets(fast_cnp):
    bridge = WraithNetBridge()
    await asyncio.wait_for(bridge.run(max_ticks=500), timeout=60)
    summary = summarize(bridge.model)

    # 5 (A) + 1 (B) + 1 (SAM) = 7 minimum; allow small overstrike slack
    assert summary["total_missiles_fired"] >= 7
    assert summary["total_missiles_fired"] <= 10


@pytest.mark.asyncio
async def test_total_distance_traveled_is_sane(fast_cnp):
    """Loose runaway-loop guard for the fixed PoC (strike + mandatory RTB).

    Heading-cruise return of all three jets lands near ~1100 on current
    defaults; keep a wide ceiling so path lengthening stays visible without
    pinning a geometric optimum.
    """
    bridge = WraithNetBridge()
    await asyncio.wait_for(bridge.run(max_ticks=500), timeout=60)
    summary = summarize(bridge.model)

    assert 400.0 <= summary["total_distance_traveled"] < 2000.0


@pytest.mark.asyncio
async def test_distant_jets_received_datalink_from_jet1(fast_cnp):
    bridge = WraithNetBridge()
    await asyncio.wait_for(bridge.run(max_ticks=500), timeout=60)

    for aid in ("Jet_2", "Jet_3"):
        if aid not in bridge.agents:
            continue  # may have been destroyed by SAM
        contacts_from_jet1 = [
            m
            for m in bridge.agents[aid].received_messages
            if m.message_type == "DATALINK_CONTACT" and m.sender_id == "Jet_1"
        ]
        assert contacts_from_jet1, f"{aid} never received DATALINK_CONTACT from Jet_1"


@pytest.mark.asyncio
async def test_scenario_exercises_jamming_and_still_resolves(fast_cnp):
    """Prove EW_Jammer_1 actually jammed someone, and Target_B still falls."""
    bridge = WraithNetBridge()
    jammed_ticks: dict[str, int] = {}
    original_set_jammed = bridge.broker.set_jammed

    def tracking_set_jammed(agent_id: str, jammed: bool) -> None:
        if jammed:
            jammed_ticks[agent_id] = jammed_ticks.get(agent_id, 0) + 1
        original_set_jammed(agent_id, jammed)

    bridge.broker.set_jammed = tracking_set_jammed  # type: ignore[method-assign]
    await asyncio.wait_for(bridge.run(max_ticks=500), timeout=60)
    summary = summarize(bridge.model)

    assert summary["solved"] is True
    assert sum(jammed_ticks.values()) >= 1, (
        "No agent was ever marked jammed during the run — Fog/EW geometry broken?"
    )
    # Jet_3's path to Target_B is the intended jam encounter.
    assert jammed_ticks.get("Jet_3", 0) >= 1
    b_strikes = [e for e in bridge.model.strike_log if e["target_id"] == "Target_B"]
    assert len(b_strikes) == 1
    assert b_strikes[0]["weapon_type"] == "AGM65"
    assert b_strikes[0]["agent_id"] == "Jet_3"


def test_export_results_writes_csv(tmp_path):
    from wraithnet.world import WraithNetModel

    model = WraithNetModel()
    model.step({})
    model.step({})
    export_results(model, output_dir=str(tmp_path))

    assert (tmp_path / "model_vars.csv").exists()
    assert (tmp_path / "agent_vars.csv").exists()
    agent_csv = (tmp_path / "agent_vars.csv").read_text()
    assert "TotalPayloadRemaining" in agent_csv
