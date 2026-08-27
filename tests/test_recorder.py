"""Tests for the optional simulation-trace recorder."""

import asyncio

import pytest

from wraithnet import config
from wraithnet.bridge import WraithNetBridge
from wraithnet.metrics import summarize
from wraithnet.recorder import TraceRecorder
from wraithnet.schemas import ActionIntent
from wraithnet.trace_schemas import TraceHeader, TraceTick


@pytest.fixture
def fast_cnp(monkeypatch):
    monkeypatch.setattr(config, "BID_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(config, "MAX_TICK_WAIT_SECONDS", 1.0)


def _read_trace(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    header = TraceHeader.model_validate_json(lines[0])
    ticks = [TraceTick.model_validate_json(line) for line in lines[1:]]
    return header, ticks


def test_header_matches_scenario_and_ticks_start_at_zero(tmp_path):
    path = tmp_path / "trace.jsonl"
    recorder = TraceRecorder(path)
    try:
        bridge = WraithNetBridge(recorder=recorder)
    finally:
        recorder.close()

    header, ticks = _read_trace(path)
    assert header.jet_ids == list(bridge.model.jets.keys())
    assert header.target_ids == list(bridge.model.targets.keys())
    assert header.sam_site_ids == list(bridge.model.sam_sites.keys())
    assert header.jammer_positions == {
        jid: j.position for jid, j in bridge.model.jammers.items()
    }
    assert header.target_positions == {
        tid: t.position for tid, t in bridge.model.targets.items()
    }
    assert ticks, "tick-0 baseline must be recorded at construction"
    assert [t.tick for t in ticks] == [0]
    assert all(ticks[i].tick <= ticks[i + 1].tick for i in range(len(ticks) - 1))


@pytest.mark.asyncio
async def test_strikes_are_per_tick_not_cumulative(tmp_path):
    """A landed strike appears only on the tick it was applied — not the next.

    Regression guard mirroring docs/06_Code_Review_Full_Project.md §1's
    cumulative-vs-per-tick counting lesson.
    """
    path = tmp_path / "trace.jsonl"
    recorder = TraceRecorder(path)
    bridge = WraithNetBridge(recorder=recorder)
    try:
        jet = bridge.model.jets["Jet_1"]
        target = bridge.model.targets["Target_A"]
        bridge.model.space.move_agent(jet, target.position)
        intent = ActionIntent(
            agent_id="Jet_1",
            tick=0,
            action_type="STRIKE",
            target_id="Target_A",
            weapon_type="JDAM",
        )
        # Same force-strike pattern as test_world.test_strike_in_range_damages_and_logs,
        # then record through the recorder the Bridge itself uses after each _tick.
        bridge.model.step({"Jet_1": intent})
        recorder.record_tick(bridge)
        strike_tick = bridge.model.tick
        bridge.model.step({})
        recorder.record_tick(bridge)
    finally:
        recorder.close()

    _, ticks = _read_trace(path)
    by_tick = {t.tick: t for t in ticks}
    assert strike_tick in by_tick
    assert len(by_tick[strike_tick].strikes) == 1
    strike = by_tick[strike_tick].strikes[0]
    assert strike.agent_id == "Jet_1"
    assert strike.target_id == "Target_A"
    assert strike.weapon_type == "JDAM"
    next_tick = by_tick[strike_tick + 1]
    assert next_tick.strikes == []


def test_jammed_flag_reflects_jam_radius(tmp_path):
    path = tmp_path / "trace.jsonl"
    recorder = TraceRecorder(path)
    bridge = WraithNetBridge(recorder=recorder)
    try:
        jammer = next(iter(bridge.model.jammers.values()))
        bridge.model.space.move_agent(bridge.model.jets["Jet_1"], jammer.position)
        # Jet_2 stays at its spawn, well outside JAM_RADIUS of (46, 19).
        recorder.record_tick(bridge)
    finally:
        recorder.close()

    _, ticks = _read_trace(path)
    frames = {j.agent_id: j for j in ticks[-1].jets}
    assert frames["Jet_1"].jammed is True
    assert frames["Jet_2"].jammed is False


@pytest.mark.asyncio
async def test_destroyed_jet_is_absent_from_subsequent_tick(tmp_path, fast_cnp):
    path = tmp_path / "trace.jsonl"
    recorder = TraceRecorder(path)
    bridge = WraithNetBridge(recorder=recorder)
    bridge.start_agent_loops()
    try:
        victim_id = "Jet_1"
        assert any(j.agent_id == victim_id for j in _peek_last_tick_jets(path))
        bridge.model.jets[victim_id].hp = 0
        await bridge._tick()
    finally:
        await bridge.stop_agent_loops()
        recorder.close()

    _, ticks = _read_trace(path)
    last = ticks[-1]
    assert victim_id not in {j.agent_id for j in last.jets}
    assert all(j.destroyed is False for j in last.jets)


def _peek_last_tick_jets(path):
    """Read jets from the last already-flushed tick without closing the recorder."""
    lines = path.read_text(encoding="utf-8").splitlines()
    return TraceTick.model_validate_json(lines[-1]).jets


@pytest.mark.asyncio
async def test_recorded_role_matches_runtime_for_two_roles(tmp_path, fast_cnp):
    path = tmp_path / "trace.jsonl"
    recorder = TraceRecorder(path)
    bridge = WraithNetBridge(recorder=recorder)
    seen_roles: set[str] = set()
    tick0 = TraceTick.model_validate_json(path.read_text(encoding="utf-8").splitlines()[-1])
    seen_roles.update(j.role for j in tick0.jets if j.role)
    bridge.start_agent_loops()
    try:
        for _ in range(8):
            await bridge._tick()
            tick = TraceTick.model_validate_json(path.read_text(encoding="utf-8").splitlines()[-1])
            for jet in tick.jets:
                assert jet.role == bridge.agents[jet.agent_id].role
                if jet.role:
                    seen_roles.add(jet.role)
            if "IDLE" in seen_roles and "CONTRACTOR" in seen_roles:
                break
            if not bridge.model.running:
                break
    finally:
        await bridge.stop_agent_loops()
        recorder.close()

    assert "IDLE" in seen_roles
    assert "CONTRACTOR" in seen_roles


@pytest.mark.asyncio
async def test_recorder_has_zero_effect_on_summarize(tmp_path, fast_cnp):
    """Attaching a TraceRecorder must not change simulation outcomes."""
    bridge_plain = WraithNetBridge()
    await asyncio.wait_for(bridge_plain.run(max_ticks=1000), timeout=60)
    summary_plain = summarize(bridge_plain.model)

    path = tmp_path / "trace.jsonl"
    recorder = TraceRecorder(path)
    try:
        bridge_recorded = WraithNetBridge(recorder=recorder)
        await asyncio.wait_for(bridge_recorded.run(max_ticks=1000), timeout=60)
    finally:
        recorder.close()
    summary_recorded = summarize(bridge_recorded.model)

    assert summary_plain == summary_recorded


@pytest.mark.asyncio
async def test_full_run_trace_is_parseable_and_matches_final_state(tmp_path, fast_cnp):
    path = tmp_path / "trace.jsonl"
    recorder = TraceRecorder(path)
    try:
        bridge = WraithNetBridge(recorder=recorder)
        await asyncio.wait_for(bridge.run(), timeout=60)
    finally:
        recorder.close()

    header, ticks = _read_trace(path)
    assert header.jet_ids
    assert ticks
    assert [t.tick for t in ticks] == sorted(t.tick for t in ticks)
    assert ticks[0].tick == 0
    final = ticks[-1]
    assert final.tick == bridge.model.tick
    assert final.running == bridge.model.running
    assert final.swarm_wiped_out == bridge.model.swarm_wiped_out
