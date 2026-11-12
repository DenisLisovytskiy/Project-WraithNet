"""End-to-end tests that assert on mission results (Phase 58 Part C).

Every mission runs on a fixed seed, so a failure here is reproducible with
``python -m wraithnet.main --generate <PRESET> --seed <N> --max-ticks 1000``.
"""

from __future__ import annotations

import asyncio
import functools
import hashlib
import json
import math
import os
from pathlib import Path

import pytest

from wraithnet import config
from wraithnet.bridge import WraithNetBridge
from wraithnet.generation_presets import parameters_for_preset
from wraithnet.geometry import is_in_firing_arc
from wraithnet.metrics import classify_outcome, summarize
from wraithnet.scenario_generator import generate_scenario
from wraithnet.scenario_io import load_scenario
from wraithnet.trace_builders import build_trace_header, build_trace_tick
from wraithnet.weapons_release import release_attitude_ok

pytestmark = pytest.mark.e2e

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SCENARIO = REPO_ROOT / "scenarios" / "default.json"
GOLDEN_SUMMARY = Path(__file__).resolve().parent / "golden" / "default_summary.json"
MAX_TICKS = 1000

# Seeds that hit the 1000-tick cap on main after Phase 57 (baseline sweep,
# seeds 0-7). Phase 59 owns these; strict xfail turns each one into a failure
# the moment it starts to terminate, so the list must then be updated.
STALLED = {
    ("STANDARD", 0),
    ("STANDARD", 2),
    ("STANDARD", 6),
    ("CONTESTED", 0),
    ("NIGHTMARE", 5),
}
# Missions where a claim outlives the jet that manages it (the manager was
# shot down or landed and nobody closed its claim). Phase 59.
DEAD_MANAGER_CLAIMS = {
    ("STANDARD", 1),
    ("STANDARD", 4),
    ("CONTESTED", 0),
    ("NIGHTMARE", 5),
}
DEAD_MANAGER = "names dead manager"
MISSIONS = [
    *[("TRAINING", seed) for seed in range(8)],
    *[("STANDARD", seed) for seed in range(8)],
    ("CONTESTED", 0),
    ("CONTESTED", 6),
    ("NIGHTMARE", 5),
]


class _TraceHash:
    """Trace sink that hashes the header and every tick the viewer would get."""

    def __init__(self) -> None:
        self._sha = hashlib.sha256()
        self._seen: set[str] = set()

    def record_header(self, bridge) -> None:
        self._sha.update(build_trace_header(bridge).model_dump_json().encode())

    def record_tick(self, bridge) -> None:
        tick = build_trace_tick(bridge, seen_outcome_ids=self._seen)
        self._sha.update(tick.model_dump_json().encode())

    def hexdigest(self) -> str:
        return self._sha.hexdigest()


def _target_xy(model, target_id: str) -> tuple[float, float] | None:
    for table in (model.targets, model.tanks, model.corvettes, model.carriers):
        unit = table.get(target_id)
        if unit is not None:
            return tuple(unit.position)
    return None


def _poses(model) -> dict[str, tuple[float, float, float, float, float, float]]:
    """Each jet's pose before the world moves it: the pose a release uses."""
    return {
        aid: (
            float(jet.pos[0]),
            float(jet.pos[1]),
            float(jet.altitude),
            float(jet.heading),
            float(jet.pitch_deg),
            float(jet.roll_deg),
        )
        for aid, jet in model.jets.items()
    }


def tick_invariant_violations(bridge, runtimes, poses, fuel_before, strikes_before) -> list[str]:
    """Everything that must hold after every tick, as readable messages.

    ``runtimes``, ``poses`` and ``fuel_before`` are taken before the tick;
    ``strikes_before`` is the strike-log length before the tick.
    """
    model = bridge.model
    tick = model.tick
    bad: list[str] = []

    for tid, target in model.targets.items():
        if target.hp_remaining < 0:
            bad.append(f"t{tick}: {tid} hp {target.hp_remaining} < 0")

    for aid, jet in model.jets.items():
        for weapon, count in jet.payload.items():
            if count < 0:
                bad.append(f"t{tick}: {aid} {weapon} payload {count} < 0")
        if aid in fuel_before and jet.fuel > fuel_before[aid] + 1e-9:
            bad.append(f"t{tick}: {aid} fuel rose {fuel_before[aid]:.3f} -> {jet.fuel:.3f}")

    for aid, agent in bridge.agents.items():
        if agent.role == "RTB" and agent.committed_target_id is not None:
            bad.append(f"t{tick}: {aid} is RTB and committed to {agent.committed_target_id}")

    for tid, claim in bridge.broker.open_contracts.items():
        if claim["manager_id"] not in bridge.agents:
            bad.append(f"t{tick}: claim on {tid} names dead manager {claim['manager_id']}")

    for entry in model.strike_log[strikes_before:]:
        aid, tid = entry["agent_id"], entry["target_id"]
        weapon = config.WEAPON_CATALOG[entry["weapon_type"]]
        x, y, alt, heading, pitch, roll = poses[aid]
        target_xy = _target_xy(model, tid)
        obs = runtimes[aid].latest_observation
        if not any(t.target_id == tid for t in obs.known_targets):
            bad.append(f"t{tick}: {aid} struck {tid} without sensing it")
        if target_xy is None:
            continue
        distance = math.dist((x, y), target_xy)
        if distance > weapon.range + 1e-9:
            bad.append(f"t{tick}: {aid} released {entry['weapon_type']} at {distance:.2f} > range {weapon.range}")
        if not is_in_firing_arc(heading, (x, y), target_xy, weapon.firing_arc_deg):
            bad.append(f"t{tick}: {aid} released {entry['weapon_type']} on {tid} outside its arc")
        ok, reason = release_attitude_ok(weapon, pitch_deg=pitch, roll_deg=roll, altitude=alt)
        if not ok:
            bad.append(f"t{tick}: {aid} released {entry['weapon_type']} with {reason}")
    return bad


async def _fly(scenario, *, max_ticks: int = MAX_TICKS, check: bool = True):
    """Fly a scenario like ``bridge.run`` and check the invariants every tick."""
    trace = _TraceHash()
    bridge = WraithNetBridge(sinks=[trace], scenario=scenario)
    violations: list[str] = []
    bridge.start_agent_loops()
    try:
        for _ in range(max_ticks):
            runtimes = dict(bridge.agents)
            poses = _poses(bridge.model)
            fuel = {aid: jet.fuel for aid, jet in bridge.model.jets.items()}
            strikes = len(bridge.model.strike_log)
            await bridge._tick()
            if check:
                violations += tick_invariant_violations(bridge, runtimes, poses, fuel, strikes)
            if not bridge.model.running:
                break
    finally:
        await bridge.stop_agent_loops()
    return bridge, violations, trace.hexdigest()


@functools.cache
def _generated_run(preset: str, seed: int):
    scenario = generate_scenario(parameters_for_preset(preset, seed=seed)).scenario
    bridge, violations, digest = asyncio.run(_fly(scenario))
    return classify_outcome(bridge.model), violations, summarize(bridge.model), digest


def _mission_id(mission) -> str:
    return f"{mission[0]}-{mission[1]}"


def _known_bad(known: set, reason: str) -> list:
    """MISSIONS, with the known-bad ones as strict xfails owned by Phase 59."""
    mark = pytest.mark.xfail(strict=True, reason=f"{reason}; Phase 59")
    return [pytest.param(m, marks=mark) if m in known else m for m in MISSIONS]


@pytest.mark.slow
@pytest.mark.parametrize("mission", _known_bad(STALLED, "stalls at the tick cap"), ids=_mission_id)
def test_generated_mission_ends_with_an_explicit_outcome(mission):
    outcome, _, summary, _ = _generated_run(*mission)
    assert outcome in ("SOLVED", "ABORTED", "WIPED"), (outcome, summary["ticks_elapsed"])


@pytest.mark.slow
@pytest.mark.parametrize("mission", MISSIONS, ids=_mission_id)
def test_generated_mission_keeps_every_tick_invariant(mission):
    _, violations, _, _ = _generated_run(*mission)
    assert [v for v in violations if DEAD_MANAGER not in v] == []


@pytest.mark.slow
@pytest.mark.parametrize(
    "mission", _known_bad(DEAD_MANAGER_CLAIMS, "claim outlives its manager"), ids=_mission_id
)
def test_every_open_claim_names_a_live_manager(mission):
    """A claim whose manager has left play blocks the target until the TTL."""
    _, violations, _, _ = _generated_run(*mission)
    assert [v for v in violations if DEAD_MANAGER in v] == []


def test_default_scenario_keeps_every_tick_invariant():
    bridge, violations, _ = asyncio.run(_fly(load_scenario(DEFAULT_SCENARIO)))
    assert bridge.model.mission_solved
    assert violations == []


def test_same_scenario_twice_gives_the_same_summary_and_trace():
    scenario = load_scenario(DEFAULT_SCENARIO)
    first, _, first_hash = asyncio.run(_fly(scenario, check=False))
    second, _, second_hash = asyncio.run(_fly(scenario, check=False))
    assert summarize(first.model) == summarize(second.model)
    assert first_hash == second_hash


def test_default_scenario_summary_matches_the_golden_file():
    """A change in behaviour shows up here as a reviewed diff.

    After an intended change, regenerate with
    ``WRAITHNET_UPDATE_GOLDEN=1 pytest tests/test_mission_outcomes.py -k golden``
    and review the JSON diff in the commit.
    """
    bridge, _, _ = asyncio.run(_fly(load_scenario(DEFAULT_SCENARIO), check=False))
    summary = json.loads(json.dumps(summarize(bridge.model)))
    if os.environ.get("WRAITHNET_UPDATE_GOLDEN") == "1":
        GOLDEN_SUMMARY.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert summary == json.loads(GOLDEN_SUMMARY.read_text(encoding="utf-8"))


# summarize() keys that no fixture run below moves off its default. Each one
# is a gap in the e2e fixtures, listed so the gap cannot grow unnoticed; a
# new key that nothing sets fails this test. "unit:" names the subsystem test
# file that does drive the counter.
NOT_REACHED_BY_FIXTURES = {
    "aaa_kills": "no test",
    "aircraft_losses_to_aaa": "no test",
    "agm84_hit_rate": "no test",
    "agm84_hits": "unit: test_corvettes.py",
    "agm84_launches": "unit: test_corvettes.py",
    "carrier_recoveries": "unit: test_carriers.py",
    "ciws_intercepts": "unit: test_corvettes.py",
    "corvette_losses": "unit: test_corvettes.py",
    "deck_gun_hits": "no test",
    "jets_crashed": "no test",
    "missile_fuel_expired": "no test",
    "missile_intercepted": "no test",
    "missile_misses": "no test",
    "notch_completions": "unit: test_countermeasures.py",
    "notch_lock_breaks": "unit: test_countermeasures.py",
}


async def _forced_ending(kind: str):
    """Default scenario, every jet shot down ("wipe") or sent home ("abort") at tick 1."""
    bridge = WraithNetBridge(scenario=load_scenario(DEFAULT_SCENARIO))
    bridge.start_agent_loops()
    try:
        await bridge._tick()
        for aid, jet in bridge.model.jets.items():
            if kind == "wipe":
                jet.hp = 0
            else:
                bridge.agents[aid].begin_rtb()
                jet.is_rtb = True
        for _ in range(MAX_TICKS):
            await bridge._tick()
            if not bridge.model.running:
                break
    finally:
        await bridge.stop_agent_loops()
    return bridge.model


@pytest.mark.slow
def test_every_summary_key_moves_off_its_default_in_some_fixture_run():
    """Would have caught objective_outcomes, which was always {}."""
    wiped = asyncio.run(_forced_ending("wipe"))
    aborted = asyncio.run(_forced_ending("abort"))
    assert classify_outcome(wiped) == "WIPED"
    assert classify_outcome(aborted) == "ABORTED"
    summaries = [summarize(wiped), summarize(aborted)]
    summaries += [_generated_run(*m)[2] for m in MISSIONS]
    summaries.append(json.loads(GOLDEN_SUMMARY.read_text(encoding="utf-8")))

    def moved(value) -> bool:
        return value not in (0, 0.0, False, None, {}, [], "")

    never = {k for k in summaries[0] if not any(moved(s[k]) for s in summaries)}
    assert never == set(NOT_REACHED_BY_FIXTURES)
