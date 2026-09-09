"""Panda3D-free tests for viewer3d.launch (Phase 19).

This file must not import panda3d or direct.
"""

from __future__ import annotations

import queue
import sys
import time
from pathlib import Path

import pytest

from viewer3d.launch import build_sim_worker_argv, launch_scenario
from viewer3d.stream_client import HeaderEvent, StreamClient
from wraithnet.scenario_io import save_scenario
from wraithnet.scenario_schema import ScenarioConfig, new_scenario_data, scenario_from_data
from wraithnet.trace_schemas import TraceHeader, TraceTick

DEFAULT_SCENARIO = Path(__file__).resolve().parent.parent / "scenarios" / "default.json"

_HEADLESS_TAIL = [
    "--headless",
    "--scenario",
    "s.json",
    "--live",
    "--live-host",
    "127.0.0.1",
    "--live-port",
    "9000",
]


def test_build_sim_worker_argv_dev_uses_module_launcher():
    argv = build_sim_worker_argv("s.json", "127.0.0.1", 9000, frozen=False)
    assert argv == [sys.executable, "-m", "launcher", *_HEADLESS_TAIL]


def test_build_sim_worker_argv_frozen_omits_module_launcher():
    dev = build_sim_worker_argv("s.json", "127.0.0.1", 9000, frozen=False)
    frozen = build_sim_worker_argv("s.json", "127.0.0.1", 9000, frozen=True)
    assert frozen == [sys.executable, *_HEADLESS_TAIL]
    assert dev[1:3] == ["-m", "launcher"]
    assert frozen == [dev[0], *dev[3:]]


def test_build_sim_worker_argv_includes_output_dir():
    argv = build_sim_worker_argv(
        "s.json", "127.0.0.1", 9000, frozen=False, output_dir="C:/runs/a"
    )
    assert "--output-dir" in argv
    assert argv[argv.index("--output-dir") + 1] == "C:/runs/a"


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


def _tiny_launch_scenario(tmp_path):
    custom = scenario_from_data(new_scenario_data(
        jets=[
            {
                "agent_id": "Solo_Jet",
                "position": (20.0, 20.0),
                "payload": {"JDAM": 1, "AIM9X": 0},
            }
        ],
        targets=[
            {
                "target_id": "Near_Bunker",
                "position": (20.5, 20.0),
                "hp_required": 1,
                "target_type": "HARDENED_BUNKER",
            }
        ],
    ))
    path = tmp_path / "tiny.json"
    save_scenario(custom, path)
    return path


def _load_run_outputs(handle) -> tuple:
    assert handle.output_dir is not None
    trace_path = handle.output_dir / "trace.jsonl"
    model_csv = handle.output_dir / "model_vars.csv"
    agent_csv = handle.output_dir / "agent_vars.csv"
    assert trace_path.is_file(), f"missing {trace_path}"
    assert model_csv.is_file(), f"missing {model_csv}"
    assert agent_csv.is_file(), f"missing {agent_csv}"
    lines = trace_path.read_text(encoding="utf-8").splitlines()
    assert len(lines) >= 2
    header = TraceHeader.model_validate_json(lines[0])
    ticks = [TraceTick.model_validate_json(line) for line in lines[1:]]
    assert header.jet_ids
    assert ticks
    return header, ticks, model_csv.read_text(encoding="utf-8")


def test_concurrent_launches_write_distinct_result_dirs(tmp_path):
    scenario = _tiny_launch_scenario(tmp_path)
    first = launch_scenario(str(scenario))
    second = launch_scenario(str(scenario))
    try:
        assert first.output_dir != second.output_dir
        first.process.wait(timeout=60)
        second.process.wait(timeout=60)
        assert first.process.returncode == 0
        assert second.process.returncode == 0
        h1, t1, csv1 = _load_run_outputs(first)
        h2, t2, csv2 = _load_run_outputs(second)
        assert h1.jet_ids == h2.jet_ids == ["Solo_Jet"]
        assert t1[-1].running is False or t1[-1].tick >= 0
        assert t2[-1].running is False or t2[-1].tick >= 0
        assert "TotalDistanceTraveled" in csv1
        assert "TotalDistanceTraveled" in csv2
    finally:
        first.terminate()
        second.terminate()
