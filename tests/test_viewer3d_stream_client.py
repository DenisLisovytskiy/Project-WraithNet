"""Panda3D-free tests for viewer3d.stream_client.

This file must not import panda3d or direct — StreamClient is the
headless half of the 3D viewer, analogous to viewer/trace_loader.py.
"""

from __future__ import annotations

import asyncio
import queue
import socket
import time

import pytest

from wraithnet import config
from wraithnet.bridge import WraithNetBridge
from wraithnet.live_stream import LiveStreamServer
from wraithnet.scenario_schema import JetSpec, ScenarioConfig, TargetSpec
from viewer3d.stream_client import (
    DisconnectedEvent,
    HeaderEvent,
    StreamClient,
    TickEvent,
)


@pytest.fixture
def fast_cnp(monkeypatch):
    monkeypatch.setattr(config, "BID_TIMEOUT_SECONDS", 0.05)
    monkeypatch.setattr(config, "MAX_TICK_WAIT_SECONDS", 1.0)


def _tiny_scenario() -> ScenarioConfig:
    return ScenarioConfig(
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
    )


async def _drain_events(client: StreamClient, min_count: int, timeout: float = 5.0):
    events = []
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        await asyncio.sleep(0.05)
        while True:
            try:
                events.append(client.events.get_nowait())
            except queue.Empty:
                break
        if len(events) >= min_count:
            return events
    return events


def _free_port() -> int:
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


@pytest.mark.asyncio
async def test_stream_client_receives_header_then_ticks(fast_cnp):
    server = LiveStreamServer(port=0)
    await server.start()
    client = None
    try:
        bridge = WraithNetBridge(sinks=[server], scenario=_tiny_scenario())
        client = StreamClient(
            "127.0.0.1", server.port, connect_retries=10, retry_delay_s=0.1
        )
        client.start()
        events = await _drain_events(client, min_count=2)
        types = [type(e) for e in events]
        assert HeaderEvent in types
        assert TickEvent in types
        header_event = next(e for e in events if isinstance(e, HeaderEvent))
        assert header_event.header.jet_ids == ["Solo_Jet"]
        assert header_event.header.target_ids == ["Near_Bunker"]
        ticks = [e.tick for e in events if isinstance(e, TickEvent)]
        assert ticks[0].tick == 0
        assert ticks[0].jets[0].agent_id == "Solo_Jet"
        assert set(bridge.model.jets) == {"Solo_Jet"}
    finally:
        if client is not None:
            client.stop()
        await server.close()


@pytest.mark.asyncio
async def test_stream_client_retries_until_server_starts(fast_cnp):
    port = _free_port()
    client = StreamClient(
        "127.0.0.1", port, connect_retries=20, retry_delay_s=0.15
    )
    client.start()
    await asyncio.sleep(0.4)

    server = LiveStreamServer(host="127.0.0.1", port=port)
    await server.start()
    try:
        WraithNetBridge(sinks=[server], scenario=_tiny_scenario())
        events = await _drain_events(client, min_count=2, timeout=6.0)
        assert any(isinstance(e, HeaderEvent) for e in events)
        assert any(isinstance(e, TickEvent) for e in events)
    finally:
        client.stop()
        await server.close()


@pytest.mark.asyncio
async def test_stream_client_exhausted_retries_emits_disconnected():
    port = _free_port()
    client = StreamClient(
        "127.0.0.1", port, connect_retries=3, retry_delay_s=0.1
    )
    client.start()
    try:
        events = await _drain_events(client, min_count=1, timeout=3.0)
        assert events, "expected a DisconnectedEvent within the retry budget"
        assert isinstance(events[0], DisconnectedEvent)
        assert "could not connect" in events[0].reason
    finally:
        client.stop()
