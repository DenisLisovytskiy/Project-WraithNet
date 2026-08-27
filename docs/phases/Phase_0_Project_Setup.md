# Phase 0 — Project Setup

> **How to use this document:** This is a complete, standalone prompt. Give an LLM coding agent this entire file as its task. It does not assume the agent has seen any other phase document or prior conversation — only the code artifacts this phase produces will be available to future phases.

## Context

We are building a Proof-of-Concept multi-agent simulator called **WraithNet**: autonomous jets ("loyal wingmen") that negotiate over radio (using the Contract Net Protocol) to decide who strikes which target, running on top of the Mesa agent-based-modeling framework. This is Phase 0 of 6 (Phases 0–5). This phase only sets up the project skeleton, dependencies, and tooling — **no simulation logic yet**.

## Goal

Produce a clean, runnable, testable Python project skeleton that later phases will fill in. When this phase is done, `pytest` should run successfully (with zero tests, or one trivial smoke test) and the package should be importable.

## Requirements

### 1. Tech stack (pin exactly these)

* Python `>= 3.11`
* `mesa>=3.1,<4.0` — pin below 4.0. Mesa 4.0 is an alpha release with a different, experimental `ContinuousSpace` API (numpy-vectorized, `agent.position` instead of `agent.pos`). This project must use the **stable** Mesa 3.x `mesa.space.ContinuousSpace` and `AgentSet`-based scheduling (`self.agents` on the Model, not the old `mesa.time.RandomActivation`/`BaseScheduler` classes, which are deprecated/removed).
* `pydantic>=2.0,<3.0`
* `pytest>=8.0` (dev dependency)
* stdlib `asyncio` only for concurrency — do not add `trio`, `curio`, threading, multiprocessing, ZeroMQ, or any network library. This PoC is single-process, single-threaded, single-event-loop.

### 2. Repository layout

Create exactly this structure at the repository root (the repository root already contains `docs/` and `6th-Gen Warfare Sim Design Document.txt` — do not modify or move those):

```
requirements.txt
requirements-dev.txt
README.md
.gitignore
wraithnet/
    __init__.py
    config.py
    schemas.py
    world.py
    broker.py
    agents.py
    bridge.py
    metrics.py
    main.py
tests/
    __init__.py
    test_smoke.py
```

* All of `schemas.py`, `world.py`, `broker.py`, `agents.py`, `bridge.py`, `metrics.py`, `main.py` should exist as **near-empty placeholder modules** in this phase: a module docstring stating which later phase implements it, and nothing else (no `pass`-only classes, no speculative code — literally just the docstring). Example for `schemas.py`:

  ```python
  """Pydantic message/data schemas for WraithNet.

  Implemented in Phase 1 (Schemas & World Scaffolding).
  See docs/phases/Phase_1_Schemas_and_World.md.
  """
  ```

* `config.py` is the one exception — implement it fully now (see §3), since every later phase depends on these constants existing and it involves no simulation logic.

### 3. `wraithnet/config.py` — implement fully now

Define these module-level constants (all are frozen, no logic beyond simple arithmetic), matching the design document exactly:

```python
import math

# --- Grid ---
GRID_WIDTH: float = 100.0
GRID_HEIGHT: float = 100.0
GRID_TORUS: bool = False
MAX_GRID_DISTANCE: float = math.sqrt(GRID_WIDTH**2 + GRID_HEIGHT**2)

# --- Movement / combat ---
JET_SPEED: float = 5.0        # max distance a jet can move per tick
STRIKE_RANGE: float = 1.0     # must be within this distance of a target to STRIKE

# --- CNP utility function weights ---
UTILITY_W1: float = 1.0       # payload_score weight
UTILITY_W2: float = 0.5       # distance_score weight

# --- Timeouts / negotiation ---
BID_TIMEOUT_SECONDS: float = 0.5
BID_TIMEOUT_TICKS: int = 3
MAX_TICK_WAIT_SECONDS: float = 3.0
CONTRACT_CLAIM_TTL_TICKS: int = 50

# --- Messaging ---
BROADCAST_ID: str = "BROADCAST"

# --- Scenario (Section 2 of the design doc) ---
SCENARIO = {
    "targets": [
        {"target_id": "Target_A", "position": (75.0, 75.0), "hp_required": 5},
        {"target_id": "Target_B", "position": (25.0, 25.0), "hp_required": 1},
    ],
    "jets": [
        {"agent_id": "Jet_1", "position": (70.0, 70.0), "payload": 1},
        {"agent_id": "Jet_2", "position": (5.0, 95.0), "payload": 4},
        {"agent_id": "Jet_3", "position": (95.0, 5.0), "payload": 4},
    ],
}
```

Note: `Target_A.hp_required = 5` is deliberately **greater than any single jet's maximum payload (4)**, not just greater than `Jet_1`'s payload (1). This is a load-bearing detail, not an arbitrary number — see `docs/00_Design_Review_Findings.md` item F1 for why `hp_required=2` would fail to actually force multi-agent cooperation. Do not "simplify" this number in a later phase.

Do not add any other logic to `config.py` in this phase.

### 4. `requirements.txt`

```
mesa>=3.1,<4.0
pydantic>=2.0,<3.0
```

### 5. `requirements-dev.txt`

```
-r requirements.txt
pytest>=8.0
pytest-asyncio>=0.24
```

(`pytest-asyncio` is included now because Phase 2 onward will need it; harmless to install early.)

### 6. `tests/test_smoke.py`

A trivial test verifying the package imports and `config.py` values are sane:

```python
import wraithnet.config as config


def test_config_constants_are_sane():
    assert config.GRID_WIDTH > 0
    assert config.GRID_HEIGHT > 0
    assert config.MAX_GRID_DISTANCE == (config.GRID_WIDTH**2 + config.GRID_HEIGHT**2) ** 0.5
    assert config.UTILITY_W1 > 0
    assert len(config.SCENARIO["targets"]) == 2
    assert len(config.SCENARIO["jets"]) == 3


def test_scenario_no_single_jet_can_solo_target_a():
    """Regression guard for the scenario property the whole design depends on
    (see docs/00_Design_Review_Findings.md item F1): Target_A must require
    more payload than any single jet carries, so at least two jets are
    structurally required to destroy it, regardless of utility tuning."""
    jets = config.SCENARIO["jets"]
    targets = {t["target_id"]: t for t in config.SCENARIO["targets"]}
    max_single_payload = max(j["payload"] for j in jets)
    assert max_single_payload < targets["Target_A"]["hp_required"]
```

### 7. `README.md`

Write a short README covering:
* One-paragraph project description (autonomous jet swarms negotiating target assignment via Contract Net Protocol, on Mesa).
* Setup instructions: create a virtualenv, `pip install -r requirements-dev.txt`.
* How to run tests: `pytest`.
* A pointer to `docs/6th-Gen_Warfare_Sim_Design_Document_v2.md` as the authoritative design reference, and `docs/phases/` for the build plan.
* Note that `wraithnet/main.py` will become the entrypoint once Phase 4/5 are complete (do not claim it works yet).

### 8. `.gitignore`

Standard Python `.gitignore` (`__pycache__/`, `*.pyc`, `.venv/`, `.pytest_cache/`, `*.egg-info/`, `.env`, etc.).

## Explicit non-goals for this phase

* Do not implement any Mesa `Model`/`Agent` classes.
* Do not implement any Pydantic schemas beyond what's shown above (none — that's Phase 1).
* Do not implement the Broker, agent negotiation logic, or the Bridge.
* Do not write scenario-running code in `main.py` beyond the placeholder docstring.

## Acceptance criteria

1. `pip install -r requirements-dev.txt` succeeds on Python 3.11+.
2. `pytest` from the repository root discovers and passes all tests in `tests/`, with zero errors and zero warnings about missing plugins.
3. `python -c "import wraithnet.config"` succeeds.
4. Every placeholder module (`schemas.py`, `world.py`, `broker.py`, `agents.py`, `bridge.py`, `metrics.py`) imports without error and contains only a docstring.
5. Repository layout matches §2 exactly (no extra top-level files/folders beyond what's specified and what already existed).
