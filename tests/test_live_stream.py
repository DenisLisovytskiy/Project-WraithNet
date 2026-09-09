"""Panda3D-free tests for the live TCP tick stream (Phase 15)."""

from __future__ import annotations

import asyncio
import json

import pytest

from wraithnet import config
from wraithnet.bridge import WraithNetBridge
from wraithnet.live_stream import LiveStreamServer
from wraithnet.metrics import summarize
from wraithnet.recorder import TraceRecorder
from wraithnet.scenario_schema import JetSpec, ScenarioConfig, TargetSpec, new_scenario_data, scenario_from_data
from wraithnet.trace_schemas import TraceHeader, TraceTick


@pytest.fixture
def fast_cnp(monkeypatch):
    monkeypatch.setattr(config, "BID_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(config, "MAX_TICK_WAIT_SECONDS", 1.0)


def _tiny_scenario() -> ScenarioConfig:
    return scenario_from_data(new_scenario_data(
        jets=[
            JetSpec(
                agent_id="Solo_Jet",
                position=(20.0, 20.0),
                payload={"JDAM": 1, "AIM9X": 0},
            )
        ],
        targets=[
            TargetSpec(
                target_id="Near_Bunker",
                position=(20.5, 20.0),
                hp_required=1,
                target_type="HARDENED_BUNKER",
            )
        ],
    ))


async def _read_stream_messages(host: str, port: int, count: int) -> list[dict]:
    """Connects, reads `count` newline-delimited JSON messages, returns them
    decoded. Test-only helper -- not part of the production package."""
    reader, writer = await asyncio.open_connection(host, port)
    messages = []
    try:
        for _ in range(count):
            line = await asyncio.wait_for(reader.readline(), timeout=5.0)
            if not line:
                break
            messages.append(json.loads(line))
        return messages
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def _read_until_end(host: str, port: int) -> list[dict]:
    reader, writer = await asyncio.open_connection(host, port)
    messages = []
    try:
        while True:
            line = await asyncio.wait_for(reader.readline(), timeout=10.0)
            if not line:
                break
            msg = json.loads(line)
            messages.append(msg)
            if msg.get("type") == "end":
                break
        return messages
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def _wait_until_subscribed(server: LiveStreamServer, n: int = 1) -> None:
    for _ in range(100):
        if len(server._writers) >= n:
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"expected {n} subscribed client(s), got {len(server._writers)}")


@pytest.mark.asyncio
async def test_early_client_receives_header_then_ticks_in_order(fast_cnp):
    server = LiveStreamServer(port=0)
    await server.start()
    try:
        reader_task = asyncio.create_task(
            _read_stream_messages("127.0.0.1", server.port, count=4)
        )
        await _wait_until_subscribed(server)

        bridge = WraithNetBridge(sinks=[server], scenario=_tiny_scenario())
        bridge.start_agent_loops()
        try:
            await bridge._tick()
            await bridge._tick()
        finally:
            await bridge.stop_agent_loops()

        messages = await reader_task
        assert [m["type"] for m in messages] == ["header", "tick", "tick", "tick"]
        header = TraceHeader.model_validate(messages[0]["data"])
        assert header.jet_ids == ["Solo_Jet"]
        assert header.target_ids == ["Near_Bunker"]
        ticks = [TraceTick.model_validate(m["data"]) for m in messages[1:]]
        assert [t.tick for t in ticks] == [0, 1, 2]
        assert ticks[0].tick <= ticks[1].tick <= ticks[2].tick
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_late_client_receives_full_backlog_without_further_ticks(fast_cnp):
    """Backfill-on-connect: a client that attaches mid-run gets everything so far."""
    server = LiveStreamServer(port=0)
    await server.start()
    try:
        bridge = WraithNetBridge(sinks=[server], scenario=_tiny_scenario())
        bridge.start_agent_loops()
        try:
            await bridge._tick()
            await bridge._tick()
            await bridge._tick()
        finally:
            await bridge.stop_agent_loops()

        # Header + ticks 0,1,2,3 = 5 messages already in the backlog; sim is idle.
        messages = await _read_stream_messages("127.0.0.1", server.port, count=5)
        assert [m["type"] for m in messages] == ["header", "tick", "tick", "tick", "tick"]
        ticks = [TraceTick.model_validate(m["data"]) for m in messages[1:]]
        assert [t.tick for t in ticks] == [0, 1, 2, 3]
        header = TraceHeader.model_validate(messages[0]["data"])
        assert header.jet_ids == ["Solo_Jet"]
        assert header.target_ids == ["Near_Bunker"]
    finally:
        await server.close()


@pytest.mark.asyncio
async def test_multi_client_fan_out_sees_identical_complete_sequence(fast_cnp):
    server = LiveStreamServer(port=0)
    await server.start()
    try:
        early_task = asyncio.create_task(_read_until_end("127.0.0.1", server.port))
        await _wait_until_subscribed(server, n=1)

        bridge = WraithNetBridge(sinks=[server], scenario=_tiny_scenario())
        bridge.start_agent_loops()
        try:
            await bridge._tick()
            await bridge._tick()

            late_task = asyncio.create_task(_read_until_end("127.0.0.1", server.port))
            await _wait_until_subscribed(server, n=2)

            await bridge._tick()
        finally:
            await bridge.stop_agent_loops()

        await server.close()
        early = await early_task
        late = await late_task
        assert early == late
        assert early[0]["type"] == "header"
        assert early[-1] == {"type": "end"}
        tick_msgs = [m for m in early if m["type"] == "tick"]
        assert [TraceTick.model_validate(m["data"]).tick for m in tick_msgs] == [0, 1, 2, 3]
    finally:
        if not server._closed:
            await server.close()


@pytest.mark.asyncio
async def test_recorder_and_live_stream_emit_identical_header_and_ticks(
    tmp_path, fast_cnp
):
    scenario = _tiny_scenario()
    path = tmp_path / "trace.jsonl"
    recorder = TraceRecorder(path)
    try:
        file_bridge = WraithNetBridge(sinks=[recorder], scenario=scenario)
        file_bridge.start_agent_loops()
        try:
            await file_bridge._tick()
            await file_bridge._tick()
        finally:
            await file_bridge.stop_agent_loops()
    finally:
        recorder.close()

    lines = path.read_text(encoding="utf-8").splitlines()
    file_header = TraceHeader.model_validate_json(lines[0])
    file_ticks = [TraceTick.model_validate_json(line) for line in lines[1:]]

    server = LiveStreamServer(port=0)
    await server.start()
    try:
        live_bridge = WraithNetBridge(sinks=[server], scenario=scenario)
        live_bridge.start_agent_loops()
        try:
            await live_bridge._tick()
            await live_bridge._tick()
        finally:
            await live_bridge.stop_agent_loops()

        messages = await _read_stream_messages(
            "127.0.0.1", server.port, count=1 + len(file_ticks)
        )
    finally:
        await server.close()

    assert messages[0]["type"] == "header"
    live_header = TraceHeader.model_validate(messages[0]["data"])
    live_ticks = [
        TraceTick.model_validate(m["data"]) for m in messages[1:] if m["type"] == "tick"
    ]
    assert live_header == file_header
    assert live_ticks == file_ticks


@pytest.mark.asyncio
async def test_close_sends_end_message_to_connected_client():
    server = LiveStreamServer(port=0)
    await server.start()
    try:
        task = asyncio.create_task(_read_until_end("127.0.0.1", server.port))
        await _wait_until_subscribed(server)
        await server.close()
        messages = await task
        assert messages[-1] == {"type": "end"}
    finally:
        if not server._closed:
            await server.close()


@pytest.mark.asyncio
async def test_sinks_have_zero_effect_on_summarize(tmp_path, fast_cnp):
    scenario = _tiny_scenario()

    async def _run_with(sinks) -> dict:
        bridge = WraithNetBridge(sinks=sinks, scenario=scenario)
        await asyncio.wait_for(bridge.run(max_ticks=50), timeout=15.0)
        return summarize(bridge.model)

    summary_none = await _run_with([])

    recorder = TraceRecorder(tmp_path / "trace.jsonl")
    try:
        summary_file = await _run_with([recorder])
    finally:
        recorder.close()

    live = LiveStreamServer(port=0)
    await live.start()
    try:
        summary_live = await _run_with([live])
    finally:
        await live.close()

    recorder2 = TraceRecorder(tmp_path / "trace_both.jsonl")
    live2 = LiveStreamServer(port=0)
    await live2.start()
    try:
        summary_both = await _run_with([recorder2, live2])
    finally:
        recorder2.close()
        await live2.close()

    assert summary_none == summary_file == summary_live == summary_both
