# Phase 1 — Data Schemas & Mesa World Scaffolding

> **How to use this document:** This is a complete, standalone prompt. Give an LLM coding agent this entire file as its task. Assume Phase 0 has already been completed (project skeleton, `requirements.txt`, `wraithnet/config.py` fully implemented, placeholder modules for everything else). Do not re-create Phase 0 files; only edit `wraithnet/schemas.py`, `wraithnet/world.py`, and add tests.

## Context

WraithNet is a Proof-of-Concept multi-agent simulator: autonomous jets negotiate over radio (Contract Net Protocol) to decide who strikes which target, on top of the Mesa agent-based-modeling framework. This is **Phase 1 of 6**. Prior phase (0) produced the project skeleton and `wraithnet/config.py` with all shared constants and the fixed scenario data. This phase builds the **data schemas** and the **Mesa World** (model + jet agents) — spawning the exact PoC scenario, applying movement/strike physics — with **no networking, no asyncio, no negotiation logic**. Those come in Phases 2–4.

Assume `wraithnet/config.py` already contains (do not redefine these, `import wraithnet.config as config` and use them):

```python
GRID_WIDTH: float = 100.0
GRID_HEIGHT: float = 100.0
GRID_TORUS: bool = False
MAX_GRID_DISTANCE: float  # = sqrt(GRID_WIDTH**2 + GRID_HEIGHT**2)
JET_SPEED: float = 5.0
STRIKE_RANGE: float = 1.0
UTILITY_W1: float = 1.0
UTILITY_W2: float = 0.5
BID_TIMEOUT_SECONDS: float = 0.5
BID_TIMEOUT_TICKS: int = 3
MAX_TICK_WAIT_SECONDS: float = 3.0
CONTRACT_CLAIM_TTL_TICKS: int = 50
BROADCAST_ID: str = "BROADCAST"
SCENARIO: dict  # {"targets": [...], "jets": [...]} — see Phase 0 doc for exact contents
```

## Goal

1. Implement all Pydantic schemas in `wraithnet/schemas.py`.
2. Implement a Mesa `Model` subclass and `Agent` subclass in `wraithnet/world.py` that spawns the exact scenario from `config.SCENARIO`, supports a synchronous `step(intents)` call that applies movement and strikes, and can produce an `Observation` for any jet.
3. Cover both with unit tests.

## Part A — `wraithnet/schemas.py`

Implement exactly these models (field types and names must match — later phases depend on this exact contract):

```python
from pydantic import BaseModel, Field, model_validator
from typing import Optional, Literal

from wraithnet.config import BROADCAST_ID


class TargetInfo(BaseModel):
    target_id: str
    position: tuple[float, float]
    hp_required: int = Field(ge=1)
    hp_remaining: int = Field(ge=0)
    claimed: bool = False


class Observation(BaseModel):
    agent_id: str
    tick: int = Field(ge=0)
    current_position: tuple[float, float]
    payload_remaining: int = Field(ge=0)
    known_targets: list[TargetInfo]


class ActionIntent(BaseModel):
    agent_id: str
    tick: int = Field(ge=0)
    action_type: Literal["MOVE", "STRIKE", "IDLE"]
    target_position: Optional[tuple[float, float]] = None
    target_id: Optional[str] = None

    @model_validator(mode="after")
    def _check_required_fields_for_action(self) -> "ActionIntent":
        if self.action_type == "MOVE" and self.target_position is None:
            raise ValueError("MOVE requires target_position")
        if self.action_type == "STRIKE" and self.target_id is None:
            raise ValueError("STRIKE requires target_id")
        return self


class Message(BaseModel):
    message_id: str
    contract_id: str
    tick: int = Field(ge=0)
    sender_id: str
    receiver_id: str  # BROADCAST_ID sentinel, or a specific agent_id
    message_type: Literal["CFP", "BID", "ACCEPT", "REJECT"]
    target_id: str
    hp_remaining: Optional[int] = None
    payload_offered: Optional[int] = None
    bid_utility: Optional[float] = None

    @model_validator(mode="after")
    def _check_required_fields_for_message_type(self) -> "Message":
        if self.message_type == "CFP":
            if self.hp_remaining is None:
                raise ValueError("CFP requires hp_remaining")
            if self.receiver_id != BROADCAST_ID:
                raise ValueError("CFP must be broadcast")
        if self.message_type == "BID":
            if self.payload_offered is None or self.bid_utility is None:
                raise ValueError("BID requires payload_offered and bid_utility")
        return self
```

Notes:
* `known_targets` is a list of the **typed** `TargetInfo` model — never a bare `dict`. This is a deliberate, non-negotiable design decision (see `docs/00_Design_Review_Findings.md`, item B1).
* `receiver_id` is a plain `str`; `BROADCAST_ID` (imported from `config`) is just a conventional sentinel value used by convention, not a separate type. Do not attempt to make it a `Literal["BROADCAST"] | str` union — that provides no real type safety and is intentionally avoided (see Findings item B2/cross-ref).
* Do not implement any Broker/negotiation *behavior* here — only the data schema and its own internal field validators.

## Part B — `wraithnet/world.py`

### Design decisions to follow (do not deviate)

* Use `mesa.space.ContinuousSpace` (the **stable** API — `place_agent`, `move_agent`, `get_distance`), not the Mesa 4.0-alpha experimental `mesa.experimental.continuous_space`. If your installed Mesa's `ContinuousSpace` constructor signature differs slightly from below, adapt the call but keep the same `x_max=GRID_WIDTH, y_max=GRID_HEIGHT, torus=GRID_TORUS` semantics.
* **Jets are Mesa `Agent`s**; **targets are not**. Targets never act/step on their own — they are pure mutable state (position, HP) — so modeling them as a plain dataclass stored on the model is simpler and avoids giving them meaningless scheduling behavior. Keep them in `self.targets: dict[str, TargetState]` on the model.
* Mesa 3.x auto-registers any `Agent(model)` instance into `model.agents` (an `AgentSet`) — do not use the deprecated `mesa.time` schedulers.
* `step()` takes an explicit `intents` argument (a `dict[agent_id, ActionIntent]`) rather than pulling from any queue — this keeps `world.py` free of any networking/async concerns. Phase 4's Bridge will call `model.step(intents)` directly once real intents exist; for now, tests will construct `ActionIntent`s by hand.
* **Do implement basic termination detection** (`self.running` becomes `False` once every target's `hp_remaining <= 0`) — Phase 4's Bridge loop needs a real stop condition to know when the scenario is complete, so this can't wait until Phase 5. Mesa's `Model` base class already provides a `self.running` attribute (defaults to `True`); just update it at the end of `step()`.
* **Do not implement a `DataCollector` or any metrics/logging in this phase.** That is Phase 5's job, deliberately kept separate from termination detection so this module only needs one small, additive change (adding `DataCollector`) when metrics arrive, instead of being rewritten. `step()` should apply physics for one tick and then update `self.running`.
* 1 missile strike = 1 HP of damage. A `STRIKE` intent is only effective if the jet is within `config.STRIKE_RANGE` of the target's position; if a jet submits `STRIKE` while out of range, ignore it (no-op — do not decrement anything, do not raise).
* `MOVE` intents move the jet toward `target_position`, clamped to at most `config.JET_SPEED` distance for the tick (don't overshoot the destination). Track cumulative distance moved on the agent (`distance_traveled`) so Phase 5 can report it without needing to modify this file again.

### Implementation

```python
from dataclasses import dataclass, field
import math

import mesa

from wraithnet import config
from wraithnet.schemas import ActionIntent, Observation, TargetInfo


@dataclass
class TargetState:
    target_id: str
    position: tuple[float, float]
    hp_required: int
    hp_remaining: int = field(init=False)

    def __post_init__(self) -> None:
        self.hp_remaining = self.hp_required

    @property
    def is_destroyed(self) -> bool:
        return self.hp_remaining <= 0


class JetAgent(mesa.Agent):
    def __init__(self, model: "WraithNetModel", agent_id: str, payload: int) -> None:
        super().__init__(model)
        self.agent_id = agent_id
        self.payload_remaining = payload
        self.distance_traveled = 0.0

    # NOTE: self.pos is managed by ContinuousSpace via place_agent/move_agent.


class WraithNetModel(mesa.Model):
    def __init__(self, seed: int | None = None) -> None:
        super().__init__(seed=seed)
        self.space = mesa.space.ContinuousSpace(
            config.GRID_WIDTH, config.GRID_HEIGHT, config.GRID_TORUS
        )
        self.tick = 0
        self.targets: dict[str, TargetState] = {}
        self.jets: dict[str, JetAgent] = {}
        self.strike_log: list[dict] = []  # [{"tick", "agent_id", "target_id"}, ...] — ground truth for Phase 5 validation
        self._spawn_scenario()

    def _spawn_scenario(self) -> None:
        for t in config.SCENARIO["targets"]:
            self.targets[t["target_id"]] = TargetState(
                target_id=t["target_id"],
                position=t["position"],
                hp_required=t["hp_required"],
            )
        for j in config.SCENARIO["jets"]:
            agent = JetAgent(self, agent_id=j["agent_id"], payload=j["payload"])
            self.space.place_agent(agent, j["position"])
            self.jets[j["agent_id"]] = agent

    def step(self, intents: dict[str, ActionIntent] | None = None) -> None:
        intents = intents or {}
        self.tick += 1
        for agent_id, intent in intents.items():
            jet = self.jets.get(agent_id)
            if jet is None:
                continue
            if intent.action_type == "MOVE":
                self._apply_move(jet, intent.target_position)
            elif intent.action_type == "STRIKE":
                self._apply_strike(jet, intent.target_id)
            # IDLE: no-op
        if all(t.is_destroyed for t in self.targets.values()):
            self.running = False

    def _apply_move(self, jet: JetAgent, destination: tuple[float, float]) -> None:
        dx = destination[0] - jet.pos[0]
        dy = destination[1] - jet.pos[1]
        distance = math.hypot(dx, dy)
        if distance <= config.JET_SPEED or distance == 0:
            new_pos = destination
            moved = distance
        else:
            ratio = config.JET_SPEED / distance
            new_pos = (jet.pos[0] + dx * ratio, jet.pos[1] + dy * ratio)
            moved = config.JET_SPEED
        self.space.move_agent(jet, new_pos)
        jet.distance_traveled += moved

    def _apply_strike(self, jet: JetAgent, target_id: str | None) -> None:
        target = self.targets.get(target_id) if target_id else None
        if target is None or jet.payload_remaining <= 0:
            return
        if self.space.get_distance(jet.pos, target.position) > config.STRIKE_RANGE:
            return
        jet.payload_remaining -= 1
        target.hp_remaining = max(0, target.hp_remaining - 1)
        self.strike_log.append({"tick": self.tick, "agent_id": jet.agent_id, "target_id": target.target_id})

    def build_observation(self, agent_id: str) -> Observation:
        jet = self.jets[agent_id]
        known_targets = [
            TargetInfo(
                target_id=t.target_id,
                position=t.position,
                hp_required=t.hp_required,
                hp_remaining=t.hp_remaining,
                claimed=False,  # Broker fills this in correctly from Phase 4 onward
            )
            for t in self.targets.values()
        ]
        return Observation(
            agent_id=agent_id,
            tick=self.tick,
            current_position=jet.pos,
            payload_remaining=jet.payload_remaining,
            known_targets=known_targets,
        )
```

Adjust only as needed to match your installed Mesa version's exact method names/signatures (e.g. some versions name it `get_distance` vs. computing Euclidean distance manually with `math.dist` — either is acceptable as long as it's Euclidean and matches `config.STRIKE_RANGE` semantics). Keep the public surface (`WraithNetModel`, `JetAgent`, `TargetState`, `step(intents)`, `build_observation(agent_id)`) exactly as specified, since Phases 2–5 call these directly.

## Part C — Tests

### `tests/test_schemas.py`

* `TargetInfo`/`Observation`/`ActionIntent`/`Message` construct successfully with valid data.
* `ActionIntent(action_type="MOVE", ...)` without `target_position` raises a validation error.
* `ActionIntent(action_type="STRIKE", ...)` without `target_id` raises a validation error.
* `Message(message_type="CFP", ...)` without `hp_remaining` raises a validation error.
* `Message(message_type="CFP", receiver_id="Jet_2", ...)` (not broadcast) raises a validation error.
* `Message(message_type="BID", ...)` without `payload_offered` or `bid_utility` raises a validation error.
* `TargetInfo(hp_required=0, ...)` raises a validation error (must be `>=1`).

### `tests/test_world.py`

* `WraithNetModel()` spawns exactly 3 jets and 2 targets, at the exact positions/payloads/hp specified in `config.SCENARIO`.
* `model.jets["Jet_1"].payload_remaining == 1`; `model.targets["Target_A"].hp_required == 5` and `hp_remaining == 5` initially.
* Calling `model.build_observation("Jet_1")` returns an `Observation` whose `known_targets` has length 2 and includes both `Target_A` and `Target_B` with correct `hp_remaining`.
* A `MOVE` intent for `Jet_2` toward a destination farther than `config.JET_SPEED` away moves it by exactly `config.JET_SPEED` (assert new distance from old position ≈ `JET_SPEED`, within floating point tolerance), and increments `distance_traveled` by that amount.
* A `MOVE` intent toward a destination *closer* than `config.JET_SPEED` moves the jet exactly to that destination (no overshoot) and increments `distance_traveled` by the true (shorter) distance.
* A `STRIKE` intent for `Jet_1` on `Target_A` when `Jet_1` is **not** within `STRIKE_RANGE` of `Target_A` (true at tick 0, since `Jet_1` is at `(70,70)` and `Target_A` is at `(75,75)`, distance ≈7.07 > `STRIKE_RANGE=1.0`) does nothing: `payload_remaining` and `hp_remaining` both unchanged.
* Manually move `Jet_1` adjacent to `Target_A` (e.g. call `space.move_agent` directly in the test, or issue enough `MOVE` intents across ticks) then issue a `STRIKE` intent: `Jet_1.payload_remaining` decrements by 1, `Target_A.hp_remaining` decrements by 1, and `model.strike_log` gains exactly one new entry `{"tick": <current tick>, "agent_id": "Jet_1", "target_id": "Target_A"}`.
* A `STRIKE` intent that does nothing (out of range, or zero payload) does not append to `model.strike_log`.
* Striking a target already at `hp_remaining == 0` does not go negative (stays clamped at 0), and does not consume the jet's payload beyond what's needed — actually per the implementation above it *will* still consume payload even if the target is already dead if a stale STRIKE intent is submitted; assert this exact (documented) behavior so the test locks in current semantics: `hp_remaining` stays at 0, `payload_remaining` still decrements (since payload consumption on strike attempt while in range is unconditional in this implementation). If you judge this edge case undesirable, you may instead guard `_apply_strike` to also skip if `target.hp_remaining <= 0`; if you do, adjust the test to assert payload is *not* consumed. Pick one behavior, implement it, and make the test assert exactly that behavior (no ambiguity between code and test).
* An `IDLE` intent changes nothing.
* Calling `model.step({})` (no intents) still increments `model.tick` by 1 and changes nothing else.
* `model.running` is `True` immediately after construction (targets start alive).
* Manually setting both targets' `hp_remaining` to `0` (or striking them down via repeated intents) and calling `model.step({})` again sets `model.running` to `False`.
* `model.running` stays `True` if only one of the two targets is destroyed.

## Explicit non-goals for this phase

* No `asyncio`, no Message Broker, no CNP logic, no negotiation.
* No `DataCollector` / metrics, no distance or missile-usage reporting beyond the plain `distance_traveled` counter on each `JetAgent`.
* No `main.py` entrypoint changes.

## Acceptance criteria

1. `pytest` passes, including all tests above.
2. `wraithnet/schemas.py` and `wraithnet/world.py` contain no networking or asyncio imports.
3. `known_targets` is typed as `list[TargetInfo]`, never `list[dict]`.
4. Running the scenario manually in a Python shell —
   ```python
   from wraithnet.world import WraithNetModel
   m = WraithNetModel()
   print(m.build_observation("Jet_1"))
   ```
   — produces a valid `Observation` with 2 known targets, matching the values in `config.SCENARIO`.
