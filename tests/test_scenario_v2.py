"""Full cross-feature V2/v3 scenario validation (Phase 10).

All four Phase-2 mechanics live simultaneously: weapon typing, Fog of War,
SAM sites, and EW jamming.
"""

import asyncio

import pandas as pd
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
async def test_v2_scenario_fully_resolves(fast_cnp):
    """v3 scenario has supply slack so infeasibility is never the failure mode."""
    bridge = WraithNetBridge()
    await asyncio.wait_for(bridge.run(max_ticks=1000), timeout=60)
    summary = summarize(bridge.model)

    assert summary["swarm_wiped_out"] is False, (
        "unexpected total swarm loss — check SAM_DAMAGE_PER_TICK/JET_HP balance"
    )
    assert summary["solved"] is True
    assert summary["targets_destroyed"] == 3  # Target_A, Target_B, SAM_Site_1
    assert bridge.model.tick < 1000


@pytest.mark.asyncio
async def test_target_a_requires_multi_jet_jdam_cooperation(fast_cnp):
    bridge = WraithNetBridge()
    await asyncio.wait_for(bridge.run(max_ticks=1000), timeout=60)
    summary = summarize(bridge.model)

    contributors_a = summary["contributors_by_target"].get("Target_A", [])
    assert len(contributors_a) >= 2, (
        f"Target_A destroyed by only {contributors_a} — hp_required=5 exceeds any "
        "single jet's max JDAM count (4), so this should be structurally impossible."
    )


@pytest.mark.asyncio
async def test_target_b_solo_with_agm65(fast_cnp):
    bridge = WraithNetBridge()
    await asyncio.wait_for(bridge.run(max_ticks=1000), timeout=60)
    summary = summarize(bridge.model)

    contributors_b = summary["contributors_by_target"].get("Target_B", [])
    assert len(contributors_b) == 1
    b_strikes = [e for e in bridge.model.strike_log if e["target_id"] == "Target_B"]
    assert all(e["weapon_type"] == "AGM65" for e in b_strikes)


@pytest.mark.asyncio
async def test_sam_site_destroyed_with_jdam(fast_cnp):
    bridge = WraithNetBridge()
    await asyncio.wait_for(bridge.run(max_ticks=1000), timeout=60)

    assert bridge.model.targets["SAM_Site_1"].is_destroyed
    sam_strikes = [
        e for e in bridge.model.strike_log if e["target_id"] == "SAM_Site_1"
    ]
    assert all(e["weapon_type"] == "JDAM" for e in sam_strikes)


@pytest.mark.asyncio
async def test_no_wasted_or_misassigned_strikes(fast_cnp):
    bridge = WraithNetBridge()
    await asyncio.wait_for(bridge.run(max_ticks=1000), timeout=60)
    summary = summarize(bridge.model)

    # 5 (Target_A) + 1 (Target_B) + 1 (SAM_Site_1) = 7
    assert summary["total_missiles_fired"] == 7
    assert summary["per_weapon_type_missiles_fired"] == {"JDAM": 6, "AGM65": 1}


@pytest.mark.asyncio
async def test_fog_of_war_and_jamming_were_both_actually_exercised(fast_cnp):
    bridge = WraithNetBridge()
    await asyncio.wait_for(bridge.run(max_ticks=1000), timeout=60)

    datalink_messages = [
        m
        for agent in bridge.agents.values()
        for m in agent.received_messages
        if m.message_type == "DATALINK_CONTACT"
    ]
    assert len(datalink_messages) > 0, (
        "no DATALINK_CONTACT was ever received — Fog of War wasn't exercised"
    )
    assert bridge.model.total_jammed_agent_ticks > 0, (
        "no jet was ever jammed — check EW_Jammer_1 placement/JAM_RADIUS"
    )


@pytest.mark.asyncio
async def test_no_agent_left_in_a_zombie_state(fast_cnp):
    bridge = WraithNetBridge()
    await asyncio.wait_for(bridge.run(max_ticks=1000), timeout=60)

    for agent_id, agent in bridge.agents.items():
        assert agent.role in ("IDLE", "CONTRACTOR"), (
            f"{agent_id} ended in role={agent.role!r} — a surviving agent should "
            "never end a fully-resolved run still MANAGER-ing or CONTRACTOR_PENDING."
        )
        assert agent.pending_bid_since_tick is None

    for task in bridge._tasks:
        if task.cancelled():
            continue
        exc = task.exception()
        assert exc is None or isinstance(exc, asyncio.CancelledError)


@pytest.mark.asyncio
async def test_total_distance_traveled_is_reasonable(fast_cnp):
    """Empirically bounded from real runs (Phase 10 Context).

    Observed under the fixed v3 scenario: total_distance_traveled ≈ 222.67
    (tick 32). Bound is ±~20% around that observed value — wide enough for
    legitimate variation if any, tight enough to catch a looping/broken
    assignment (>600 was the prior loose placeholder).
    """
    bridge = WraithNetBridge()
    await asyncio.wait_for(bridge.run(max_ticks=1000), timeout=60)
    summary = summarize(bridge.model)

    observed = summary["total_distance_traveled"]
    assert 180.0 <= observed <= 280.0, (
        f"Total distance traveled ({observed:.2f}) is outside the observed "
        "near-optimal band (~222.67 ±20%); inspect contributors_by_target."
    )


@pytest.mark.asyncio
async def test_determinism_across_repeated_runs(fast_cnp):
    """Nothing in this scenario is randomized — repeated runs must match."""
    results = []
    for _ in range(2):
        bridge = WraithNetBridge()
        await asyncio.wait_for(bridge.run(max_ticks=1000), timeout=60)
        summary = summarize(bridge.model)
        results.append(
            (
                summary["targets_destroyed"],
                summary["jets_destroyed"],
                summary["total_missiles_fired"],
                summary["per_weapon_type_missiles_fired"],
                round(summary["total_distance_traveled"], 2),
            )
        )
    assert results[0] == results[1], f"non-deterministic outcome across runs: {results}"


def test_export_results_includes_new_columns(tmp_path):
    from wraithnet.world import WraithNetModel

    model = WraithNetModel()
    model.step({})
    model.step({})
    export_results(model, output_dir=str(tmp_path))

    model_vars = pd.read_csv(tmp_path / "model_vars.csv")
    for column in ("JetsDestroyed", "SamSitesDestroyed", "TotalJammedAgentTicks"):
        assert column in model_vars.columns
