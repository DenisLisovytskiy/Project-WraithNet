# Phase 4 — The Hybrid Bridge (Connecting Mesa to the Agents)

> **How to use this document:** This is a complete, standalone prompt. Give an LLM coding agent this entire file as its task. Assume Phases 0–3 are complete: `config.py`, `schemas.py`, `world.py` (`WraithNetModel` with `step(intents)`, `build_observation(agent_id)`, and `model.running` termination flag), `broker.py` (`MessageBroker` with routing + `try_open_contract`/`close_contract`/`is_claimed`/`expire_stale_contracts`), and `agents.py` (`JetAgentRuntime` with the full CNP state machine: `set_observation`, `scan_and_negotiate`, `decide_intent`, `handle_message`, `run`/`start`/`stop`). This phase creates `wraithnet/bridge.py` and fills in `wraithnet/main.py`. Do not modify `world.py`, `schemas.py`, `broker.py`, or `agents.py` except as noted in "Small required fix" below.

## Context

WraithNet is a Proof-of-Concept multi-agent simulator: autonomous jets negotiate over radio (Contract Net Protocol) to decide who strikes which target, then physically execute those decisions inside a Mesa model. This is **Phase 4 of 6**. Everything needed exists in isolation (World in Phase 1, Broker/agent-loop plumbing in Phase 2, negotiation intelligence in Phase 3) but nothing has ever been run together end-to-end. This phase is pure wiring — do not add new negotiation rules or physics rules here; if something seems to be missing from an earlier phase, add the minimal fix and note it, rather than reinventing logic that belongs in `world.py` or `agents.py`.

**Concurrency model recap (do not deviate):** one asyncio event loop, no threads. `World.step()` is synchronous and is simply called directly from a coroutine.

## Small required fix (apply first)

Phase 1's `WraithNetModel.build_observation(agent_id)` currently always sets `TargetInfo.claimed=False`. Update it to reflect real Broker state:

```python
def build_observation(self, agent_id: str, broker: "MessageBroker") -> Observation:
    jet = self.jets[agent_id]
    known_targets = [
        TargetInfo(
            target_id=t.target_id,
            position=t.position,
            hp_required=t.hp_required,
            hp_remaining=t.hp_remaining,
            claimed=broker.is_claimed(t.target_id),
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

This changes `build_observation`'s signature (it now requires a `broker` argument). Update Phase 1's existing tests in `tests/test_world.py` that call `build_observation` to pass a `MessageBroker()` instance (a fresh, empty broker means `is_claimed` is `False` for everything, preserving the old test behavior). Do not change anything else about `world.py`.

## Goal

Implement `wraithnet/bridge.py` with a `WraithNetBridge` class that runs the full tick loop described in `docs/6th-Gen_Warfare_Sim_Design_Document_v2.md` §6, and a `wraithnet/main.py` that constructs one and runs the fixed PoC scenario to completion.

## Part A — `wraithnet/bridge.py`

```python
"""The Hybrid Bridge: connects the synchronous Mesa World to the asynchronous
agent negotiation loops. One coroutine, one event loop, no threads.
"""

import asyncio

from wraithnet import config
from wraithnet.broker import MessageBroker
from wraithnet.world import WraithNetModel
from wraithnet.agents import JetAgentRuntime


class WraithNetBridge:
    def __init__(self) -> None:
        self.broker = MessageBroker()
        self.model = WraithNetModel()
        self.agents: dict[str, JetAgentRuntime] = {
            agent_id: JetAgentRuntime(agent_id, self.broker)
            for agent_id in self.model.jets
        }
        self._tasks: list[asyncio.Task] = []

    def start_agent_loops(self) -> None:
        """Start each agent's message-receive loop (handle_message dispatch)
        as a background task. Must be called once before run()."""
        self._tasks = [agent.start() for agent in self.agents.values()]

    async def stop_agent_loops(self) -> None:
        for agent in self.agents.values():
            agent.stop()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def run(self, max_ticks: int = 1000) -> None:
        """Advance ticks until the scenario is solved or max_ticks is hit.
        max_ticks is a safety cap against an unforeseen infinite loop — it
        should never actually be reached if Phase 3's CNP logic is correct
        for this scenario."""
        self.start_agent_loops()
        try:
            for _ in range(max_ticks):
                await self._tick()
                if not self.model.running:
                    break
        finally:
            await self.stop_agent_loops()

    async def _tick(self) -> None:
        # 1. Broadcast state
        for agent_id, agent in self.agents.items():
            agent.set_observation(self.model.build_observation(agent_id, self.broker))

        # 2. Negotiate (idle agents only), bounded by MAX_TICK_WAIT_SECONDS
        idle_agents = [a for a in self.agents.values() if a.role == "IDLE"]
        if idle_agents:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*(a.scan_and_negotiate() for a in idle_agents)),
                    timeout=config.MAX_TICK_WAIT_SECONDS,
                )
            except asyncio.TimeoutError:
                pass  # any agent still IDLE will just decide_intent() -> IDLE below

        # 3. Collect intents (pull, synchronous)
        intents = {agent_id: agent.decide_intent() for agent_id, agent in self.agents.items()}

        # 4. Physics tick
        self.model.step(intents)

        # 5. Contract cleanup
        for target_id, target in self.model.targets.items():
            if target.is_destroyed:
                self.broker.close_contract(target_id)
        self.broker.expire_stale_contracts(self.model.tick)
```

Notes:
* `scan_and_negotiate()` internally sleeps for `BID_TIMEOUT_SECONDS` per round (Phase 3), so step 2 above will naturally take real wall-clock time; this is expected and correct for a PoC. `MAX_TICK_WAIT_SECONDS` exists purely as an upper bound safety net, not as the normal-path timing mechanism.
* Only agents that are `"IDLE"` at the start of the tick get a chance to `scan_and_negotiate()`. Agents that are `"MANAGER"`, `"CONTRACTOR_PENDING"`, or `"CONTRACTOR"` don't need to scan — they're already mid-negotiation or already committed, and their state advances via `handle_message` (running in the background task started by `start_agent_loops()`) and `decide_intent()`.
* Do not call `agent.decide_intent()` before every agent has had a chance to receive pending `ACCEPT`/`REJECT` messages for the tick — since the Broker's `asyncio.Queue.put`/`get` and `scan_and_negotiate`'s internal `await asyncio.sleep` yield control back to the event loop, the background `run()` loops (started in `start_agent_loops`) get scheduled and process their mailboxes during that same `await`. If you observe agents not seeing messages promptly, add a brief `await asyncio.sleep(0)` (or a couple of them) after step 2 to let any last mailbox messages drain before step 3 — but try without it first, since `scan_and_negotiate`'s existing sleeps are normally enough.

## Part B — `wraithnet/main.py`

```python
"""Entrypoint: run the fixed WraithNet PoC scenario end-to-end."""

import asyncio

from wraithnet.bridge import WraithNetBridge


async def run_scenario() -> WraithNetBridge:
    bridge = WraithNetBridge()
    await bridge.run()
    return bridge


def main() -> None:
    bridge = asyncio.run(run_scenario())
    print(f"Simulation finished at tick {bridge.model.tick}")
    for target_id, target in bridge.model.targets.items():
        print(f"  {target_id}: hp_remaining={target.hp_remaining}")
    for agent_id, jet in bridge.model.jets.items():
        print(f"  {agent_id}: payload_remaining={jet.payload_remaining}, "
              f"distance_traveled={jet.distance_traveled:.2f}")


if __name__ == "__main__":
    main()
```

## Part C — Tests

### `tests/test_bridge.py`

* Construct a `WraithNetBridge()`, call `await bridge.run(max_ticks=500)` (mark test `@pytest.mark.asyncio`, and give it a generous real-world timeout via `pytest.mark.timeout` or `asyncio.wait_for` around the call — e.g. 30 seconds — so a genuine deadlock fails the test suite instead of hanging it forever).
* Assert `bridge.model.running is False` (i.e. it terminated on its own, not because it hit `max_ticks`) — assert this by also checking the loop did not run all `500` ticks (e.g. `bridge.model.tick < 500`).
* Assert both `Target_A` and `Target_B` end with `hp_remaining == 0`.
* Assert total missiles fired equals total missiles required: `sum(jet.payload_remaining for jet in bridge.model.jets.values())` decreased from the starting total (`1+4+4=9`) by exactly `6` (5 for `Target_A` + 1 for `Target_B`) — i.e. ends at `3`. (This will only hold if no jet fires a wasted/extra strike; if your implementation allows a committed jet to strike once more than needed due to a race between `decide_intent` and the target dying that same tick, either tighten `decide_intent`'s guard so it never emits a `STRIKE` intent whose execution would be redundant, or accept and explicitly assert the actual, documented number of missiles consumed — do not silently leave this unchecked.)
* **Assert genuine cooperation occurred on `Target_A`:** using `bridge.model.strike_log` (added in Phase 1), collect the distinct `agent_id`s with at least one entry where `target_id == "Target_A"`. Assert this set has **size >= 2**. Do not assert that `Jet_1` specifically must be one of them — see `docs/00_Design_Review_Findings.md` item F1 and the design doc v2 §2 rationale: which jets cooperate is an emergent property of the utility function; only the fact that more than one must is a guaranteed invariant (since `Target_A.hp_required=5` exceeds any single jet's payload of `4`).
* Assert no agent task raised an unhandled exception (inspect `bridge._tasks` after `stop_agent_loops()` — for tasks that were cancelled cleanly, `task.cancelled()` should be `True`; there should be no task where `task.exception()` raises something other than `asyncio.CancelledError`).

### `tests/test_world.py` (update, per the "Small required fix")

* Update any existing calls to `model.build_observation(agent_id)` to `model.build_observation(agent_id, MessageBroker())`.
* Add: with a `MessageBroker` where `try_open_contract("Target_A", ...)` has been called, `build_observation(..., broker)` returns `TargetInfo(target_id="Target_A", claimed=True, ...)`; `Target_B` (not claimed) still shows `claimed=False`.

## Explicit non-goals for this phase

* No metrics/`DataCollector` (Phase 5).
* No changes to the negotiation rules themselves (utility function, multi-winner acceptance, tie-break) — those are frozen from Phase 3. If the scenario doesn't resolve correctly, first suspect a wiring bug in the Bridge (e.g. observations not refreshed, negotiation not given enough of a chance to run before `decide_intent`) before changing Phase 3 logic.

## Acceptance criteria

1. `pytest` passes, including `test_bridge.py`, and the full suite finishes in well under a minute (no accidental infinite loops/hangs).
2. `python -m wraithnet.main` runs to completion, printing both targets at `hp_remaining=0`.
3. `bridge.py` contains no direct manipulation of agents' internal negotiation state (`role`, `_collected_bids`, etc.) from outside `agents.py` — the Bridge only calls the public methods `set_observation`, `scan_and_negotiate`, `decide_intent`, `start`, `stop`.
4. Re-running `python -m wraithnet.main` multiple times produces the same tick count and same final `hp_remaining`/`payload_remaining` values each time (the scenario has no randomness at t=0 and the tie-break rule is deterministic, so outcomes should be reproducible; wall-clock-timing-dependent behavior, if any creeps in, should not change the final result, only possibly the exact tick count within a small range — if you observe outcome nondeterminism beyond tick-count jitter, that's a bug to fix, not a fact of life to document).
