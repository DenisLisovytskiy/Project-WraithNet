"""Panda3D-free tests for viewer3d.launch (Phase 19).

This file must not import panda3d or direct.
"""

from __future__ import annotations

import queue
import time
from pathlib import Path

import pytest

from viewer3d.launch import launch_scenario
from viewer3d.stream_client import HeaderEvent, StreamClient

DEFAULT_SCENARIO = Path(__file__).resolve().parent.parent / "scenarios" / "default.json"


def _wait_for_header(client: StreamClient, timeout: float = 15.0) -> HeaderEvent:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            event = client.events.get(timeout=0.25)
        except queue.Empty:
            continue
        if isinstance(event, HeaderEvent):
            return event
    raise AssertionError("timed out waiting for HeaderEvent from launched sim")


def test_launch_scenario_default_json_is_connectable():
    handle = launch_scenario(str(DEFAULT_SCENARIO))
    client = StreamClient(handle.host, handle.port)
    try:
        assert handle.is_running()
        client.start()
        header_event = _wait_for_header(client)
        assert "Jet_1" in header_event.header.jet_ids
        assert "Target_A" in header_event.header.target_ids
    finally:
        client.stop()
        handle.terminate()
    deadline = time.monotonic() + 8.0
    while time.monotonic() < deadline and handle.is_running():
        time.sleep(0.05)
    assert handle.is_running() is False


def test_launch_nonexistent_scenario_exits_nonzero():
    handle = launch_scenario("this/path/does/not/exist.json")
    try:
        handle.process.wait(timeout=20)
    finally:
        handle.terminate()
    assert handle.process.returncode not in (0, None)
    assert handle.is_running() is False


def test_sequential_launches_get_distinct_ports():
    first = launch_scenario(str(DEFAULT_SCENARIO))
    second = launch_scenario(str(DEFAULT_SCENARIO))
    clients = []
    try:
        assert first.port != second.port
        for handle in (first, second):
            client = StreamClient(handle.host, handle.port)
            client.start()
            clients.append(client)
            event = _wait_for_header(client)
            assert event.header.jet_ids
    finally:
        for client in clients:
            client.stop()
        first.terminate()
        second.terminate()
