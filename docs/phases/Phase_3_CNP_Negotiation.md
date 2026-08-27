# Phase 3 — The Contract Net Protocol (Negotiation Logic)

> **How to use this document:** This is a complete, standalone prompt. Give an LLM coding agent this entire file as its task. Assume Phases 0–2 are complete: `config.py`, `schemas.py` (`TargetInfo`, `Observation`, `ActionIntent`, `Message`), `world.py` (`WraithNetModel`, `JetAgent`, `TargetState`), `broker.py` (`MessageBroker` with routing), and `agents.py` (`JetAgentRuntime` with a generic receive loop) all exist and are tested. This phase **extends** `broker.py` and `agents.py` — it does not create new files. Do not modify `world.py` or `schemas.py`.

## Context

WraithNet is a Proof-of-Concept multi-agent simulator: autonomous jets negotiate over radio (Contract Net Protocol, "CNP") to decide who strikes which target. This is **Phase 3 of 6**, the most important one: it implements the actual negotiation intelligence. There is still **no live Mesa/Bridge integration** — `Observation`s are handed to agents directly (by tests, and later by Phase 4's Bridge) via a setter method, not read from a live running model. That is intentional: it lets this phase be fully tested in isolation before Phase 4 wires it to the real World.

**This phase must support partial/multi-winner contracts.** This is the load-bearing requirement of the whole scenario: `Target_A` requires 5 missile strikes, but no single jet carries more than 4 (`Jet_1` has only 1), so no single jet can win a `Target_A` contract alone — the Manager must be able to accept bids from *multiple* contractors for the same target and sum their `payload_offered` until the requirement is covered. If you implement classic single-winner-only CNP, the fixed scenario (see `docs/6th-Gen_Warfare_Sim_Design_Document_v2.md` §2 and `docs/00_Design_Review_Findings.md` item F1) is mathematically unsolvable and Phase 5's validation will fail.

## Part A — Extend `wraithnet/broker.py`: contract arbitration (manager election)

Add **open-contract tracking** to `MessageBroker`. This solves "who is allowed to manage a negotiation for a given target" without any real race condition, because everything runs on one event loop (a dict lookup-then-insert here is effectively atomic — no `await` happens between the check and the write).

```python
from wraithnet import config

class MessageBroker:
    def __init__(self) -> None:
        self._mailboxes: dict[str, asyncio.Queue[Message]] = {}
        self.open_contracts: dict[str, dict] = {}
        # target_id -> {"contract_id": str, "manager_id": str, "opened_tick": int}

    # ... existing register/send/_route/receive/inbox_size unchanged from Phase 2 ...

    def try_open_contract(self, target_id: str, contract_id: str, manager_id: str, tick: int) -> bool:
        """Attempt to become the sole Manager for target_id. Returns True if granted."""
        if target_id in self.open_contracts:
            return False
        self.open_contracts[target_id] = {
            "contract_id": contract_id,
            "manager_id": manager_id,
            "opened_tick": tick,
        }
        return True

    def close_contract(self, target_id: str) -> None:
        """Release a target so a new CFP can be opened for it. Called when a
        target is confirmed destroyed, or when a Manager gives up."""
        self.open_contracts.pop(target_id, None)

    def is_claimed(self, target_id: str) -> bool:
        return target_id in self.open_contracts

    def expire_stale_contracts(self, current_tick: int) -> list[str]:
        """Force-clear contracts open longer than config.CONTRACT_CLAIM_TTL_TICKS
        (protects against a Manager that stalls/crashes forever). Returns the
        list of target_ids that were expired, for logging/testing."""
        expired = [
            tid for tid, info in self.open_contracts.items()
            if current_tick - info["opened_tick"] > config.CONTRACT_CLAIM_TTL_TICKS
        ]
        for tid in expired:
            del self.open_contracts[tid]
        return expired
```

Note: dedup happens because agents check `try_open_contract` themselves **before** broadcasting a CFP (Part B) — the Broker does not need to inspect message contents in `_route`.

## Part B — Extend `wraithnet/agents.py`: the CNP state machine

### Agent roles (state machine)

Each `JetAgentRuntime` is in exactly one role at a time:

* `"IDLE"` — no assignment, listening.
* `"MANAGER"` — has an open CFP out for a target it is trying to get destroyed; collecting bids.
* `"CONTRACTOR_PENDING"` — has submitted a bid for someone else's CFP and is waiting for ACCEPT/REJECT.
* `"CONTRACTOR"` — has an accepted contract; committed to move to and strike a specific target with a specific missile budget.

Transitions:
```
IDLE --(sees unclaimed target, wins contract race via broker)--> MANAGER
MANAGER --(bid window closes, fully covered)--> IDLE (if it did not also win its own bid) or CONTRACTOR
MANAGER --(bid window closes, not fully covered)--> MANAGER (re-broadcasts with reduced hp_remaining, bounded retries)
IDLE --(receives CFP, has payload, decides to bid)--> CONTRACTOR_PENDING
CONTRACTOR_PENDING --(receives ACCEPT)--> CONTRACTOR
CONTRACTOR_PENDING --(receives REJECT)--> IDLE
CONTRACTOR --(committed payload exhausted or target destroyed)--> IDLE
```

### The utility function (standalone, pure, testable function)

```python
import math
from wraithnet import config
from wraithnet.schemas import Observation, TargetInfo

def compute_bid(
    observation: Observation,
    target: TargetInfo,
    hp_needed: int | None = None,
) -> tuple[float, int]:
    """Returns (bid_utility, payload_offered). Caller must not call this if
    observation.payload_remaining <= 0.

    ``hp_needed`` is the ask for this CFP round (the manager's announced
    shortfall). When omitted, falls back to ``target.hp_remaining``.
    Later rounds must pass the CFP's ``hp_remaining`` so bidders don't
    pledge more than the manager still needs.
    """
    ask = target.hp_remaining if hp_needed is None else hp_needed
    payload_score = min(1.0, observation.payload_remaining / ask)
    distance = math.dist(observation.current_position, target.position)
    distance_score = distance / config.MAX_GRID_DISTANCE
    utility = config.UTILITY_W1 * payload_score - config.UTILITY_W2 * distance_score
    payload_offered = min(observation.payload_remaining, ask)
    return utility, payload_offered
```

Key design rules (do not deviate — see Findings items C2/C3):
* Both terms are normalized to roughly `[0, 1]` before weighting (`payload_score` capped at `1.0`; `distance_score` divided by `config.MAX_GRID_DISTANCE`). Do not use raw, un-normalized distance.
* There is **no "zero out if underfilled" rule**. A jet with less payload than a target's `hp_remaining` still computes a valid, comparable utility and a smaller `payload_offered` — this is what makes multi-winner contracts work. The only reason not to bid at all is `payload_remaining <= 0` (nothing to offer).
* Tie-break for equal `bid_utility` (within `1e-9`): smaller `agent_id` string wins. Implement this in the sort key, not as a special case: `sorted(bids, key=lambda b: (-b.bid_utility, b.sender_id))`.

### `JetAgentRuntime` extensions

Add to the class from Phase 2 (keep `run()`'s loop shape; extend `handle_message`):

```python
import uuid
from wraithnet import config
from wraithnet.config import BROADCAST_ID
from wraithnet.schemas import ActionIntent, Message, Observation

MAX_CFP_ROUNDS = 5  # defensive cap so a manager can't re-broadcast forever

class JetAgentRuntime:
    def __init__(self, agent_id: str, broker: MessageBroker) -> None:
        self.agent_id = agent_id
        self.broker = broker
        self.broker.register(agent_id)
        self.received_messages: list[Message] = []
        self._task: asyncio.Task | None = None

        self.role: str = "IDLE"
        self.latest_observation: Observation | None = None

        # Manager-side state
        self.open_contract_id: str | None = None
        self.managed_target_id: str | None = None
        self._collected_bids: list[Message] = []
        self._cfp_round: int = 0

        # Contractor-side state
        self.committed_target_id: str | None = None
        self.committed_payload: int = 0

    def set_observation(self, observation: Observation) -> None:
        """Called by test code in this phase, and by the Bridge in Phase 4,
        once per tick, before negotiation/intent logic runs for that tick."""
        self.latest_observation = observation

    async def handle_message(self, message: Message) -> None:
        self.received_messages.append(message)
        if message.message_type == "CFP":
            await self._maybe_bid(message)
        elif (
            message.message_type == "BID"
            and self.managed_target_id is not None
            and message.target_id == self.managed_target_id
        ):
            # Key off managed_target_id, not role==MANAGER: a self-commit sets
            # role=CONTRACTOR while later CFP rounds are still collecting bids.
            self._collected_bids.append(message)
        elif message.message_type == "ACCEPT" and self.role == "CONTRACTOR_PENDING":
            self._commit(message.target_id, message.payload_offered)
        elif message.message_type == "REJECT" and self.role == "CONTRACTOR_PENDING":
            self.role = "IDLE"

    async def try_become_manager(self, target: TargetInfo) -> bool:
        """Call this when idle and aware of an unclaimed, un-destroyed target.
        Returns True if this agent won the race to manage it."""
        obs = self.latest_observation
        if self.role != "IDLE" or obs is None or target.hp_remaining <= 0:
            return False
        contract_id = uuid.uuid4().hex
        if not self.broker.try_open_contract(target.target_id, contract_id, self.agent_id, obs.tick):
            return False  # someone else already manages this target
        self.role = "MANAGER"
        self.open_contract_id = contract_id
        self.managed_target_id = target.target_id
        self._cfp_round = 0
        await self._broadcast_cfp_and_collect(target.hp_remaining)
        return True

    async def _broadcast_cfp_and_collect(self, hp_needed: int) -> None:
        self._cfp_round += 1
        obs = self.latest_observation
        self._collected_bids = []
        # Self-bid: the manager may also strike its own managed target. Without
        # this, a jet that spots and manages a target it's well-placed to hit
        # itself would never fire, which contradicts minimizing total distance.
        # Skip if already committed to this target in a prior round (would
        # double-count the same missiles against the remaining shortfall).
        already_committed = (
            self.committed_target_id == self.managed_target_id
            and self.committed_payload > 0
        )
        if obs.payload_remaining > 0 and not already_committed:
            target = next(t for t in obs.known_targets if t.target_id == self.managed_target_id)
            utility, payload_offered = compute_bid(obs, target, hp_needed=hp_needed)
            self._collected_bids.append(Message(
                message_id=uuid.uuid4().hex, contract_id=self.open_contract_id, tick=obs.tick,
                sender_id=self.agent_id, receiver_id=self.agent_id, message_type="BID",
                target_id=self.managed_target_id, payload_offered=payload_offered, bid_utility=utility,
            ))
        cfp = Message(
            message_id=uuid.uuid4().hex, contract_id=self.open_contract_id, tick=obs.tick,
            sender_id=self.agent_id, receiver_id=BROADCAST_ID, message_type="CFP",
            target_id=self.managed_target_id, hp_remaining=hp_needed,
        )
        await self.broker.send(cfp)
        try:
            await asyncio.sleep(config.BID_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            return
        await self._resolve_contract(hp_needed)

    async def _resolve_contract(self, hp_needed: int) -> None:
        bids = sorted(self._collected_bids, key=lambda b: (-b.bid_utility, b.sender_id))
        # Use this round's announced ask — Observation.hp_remaining is stale
        # mid-negotiation (no strikes have landed yet this tick).
        remaining = hp_needed
        winners: list[Message] = []
        for bid in bids:
            if remaining <= 0:
                break
            winners.append(bid)
            remaining -= bid.payload_offered
        winner_ids = {b.sender_id for b in winners}

        for bid in bids:
            if bid.sender_id == self.agent_id:
                if bid.sender_id in winner_ids:
                    self._commit(self.managed_target_id, bid.payload_offered)
                continue
            decision = "ACCEPT" if bid.sender_id in winner_ids else "REJECT"
            await self.broker.send(Message(
                message_id=uuid.uuid4().hex, contract_id=self.open_contract_id, tick=self.latest_observation.tick,
                sender_id=self.agent_id, receiver_id=bid.sender_id, message_type=decision,
                target_id=self.managed_target_id,
                payload_offered=bid.payload_offered if decision == "ACCEPT" else None,
            ))

        if remaining > 0 and self._cfp_round < MAX_CFP_ROUNDS:
            await self._broadcast_cfp_and_collect(remaining)  # re-negotiate the shortfall
            return

        # Only free the claim on give-up. A fully-covered contract stays claimed
        # until the target is destroyed (Bridge) or TTL expires — otherwise IDLE
        # agents would reopen CFPs every tick for targets already being struck.
        if remaining > 0:
            self.broker.close_contract(self.managed_target_id)
        self.open_contract_id = None
        self.managed_target_id = None
        if self.role == "MANAGER":  # did not win our own self-bid
            self.role = "IDLE"

    async def _maybe_bid(self, cfp: Message) -> None:
        obs = self.latest_observation
        if self.role != "IDLE" or obs is None or obs.payload_remaining <= 0:
            return
        if cfp.hp_remaining is None or cfp.hp_remaining <= 0:
            return
        target = next((t for t in obs.known_targets if t.target_id == cfp.target_id), None)
        if target is None or target.hp_remaining <= 0:
            return
        utility, payload_offered = compute_bid(obs, target, hp_needed=cfp.hp_remaining)
        self.role = "CONTRACTOR_PENDING"
        await self.broker.send(Message(
            message_id=uuid.uuid4().hex, contract_id=cfp.contract_id, tick=obs.tick,
            sender_id=self.agent_id, receiver_id=cfp.sender_id, message_type="BID",
            target_id=cfp.target_id, payload_offered=payload_offered, bid_utility=utility,
        ))

    def _commit(self, target_id: str, payload_offered: int) -> None:
        self.role = "CONTRACTOR"
        self.committed_target_id = target_id
        self.committed_payload = payload_offered

    def decide_intent(self) -> ActionIntent:
        """Pure function of current state + latest_observation. Phase 4's
        Bridge calls this once per tick per agent to get its next ActionIntent."""
        obs = self.latest_observation
        if self.committed_target_id is None:
            return ActionIntent(agent_id=self.agent_id, tick=obs.tick, action_type="IDLE")
        target = next(
            (t for t in obs.known_targets if t.target_id == self.committed_target_id), None
        )
        if target is None or target.hp_remaining <= 0 or self.committed_payload <= 0:
            self.committed_target_id = None
            self.role = "IDLE"
            return ActionIntent(agent_id=self.agent_id, tick=obs.tick, action_type="IDLE")
        distance = math.dist(obs.current_position, target.position)
        if distance <= config.STRIKE_RANGE:
            self.committed_payload -= 1
            return ActionIntent(
                agent_id=self.agent_id, tick=obs.tick, action_type="STRIKE", target_id=target.target_id
            )
        return ActionIntent(
            agent_id=self.agent_id, tick=obs.tick, action_type="MOVE", target_position=target.position
        )
```

Also add a small idle-scan helper agents will call each tick (Phase 4 will invoke it; tests can call it directly):

```python
    async def scan_and_negotiate(self) -> None:
        """Call once per tick when IDLE: look for an unclaimed, live target and
        try to manage it."""
        if self.role != "IDLE" or self.latest_observation is None:
            return
        for target in self.latest_observation.known_targets:
            if target.hp_remaining > 0 and not target.claimed:
                won = await self.try_become_manager(target)
                if won:
                    return
```

`target.claimed` in the `Observation` is expected to be populated correctly (from `broker.is_claimed(target_id)`) once Phase 4's Bridge builds Observations — in this phase's own tests, construct `TargetInfo(claimed=...)` by hand to exercise both branches.

## Part C — Tests

### `tests/test_broker.py` (additions)

* `try_open_contract` returns `True` the first time for a given `target_id`, `False` on a second attempt before `close_contract`/expiry.
* `close_contract` frees the target: a subsequent `try_open_contract` for the same `target_id` returns `True` again.
* `expire_stale_contracts(current_tick)` removes entries older than `config.CONTRACT_CLAIM_TTL_TICKS` and leaves fresher ones untouched; returns the expired `target_id`s.

### `tests/test_cnp.py`

Use `pytest-asyncio`. Build `Observation`/`TargetInfo` objects by hand (no live `WraithNetModel` needed in this phase).

1. **Utility function unit tests:**
   * A jet with full payload (>= `hp_remaining`) and zero distance scores `w1 * 1.0 - 0 = 1.0`.
   * A jet with payload exactly half of `hp_remaining` scores `payload_score == 0.5`.
   * A jet with payload double the `hp_remaining` scores the same `payload_score` as a jet with payload exactly equal (both capped at `1.0`) — this specifically locks in the "cap at 1.0" fix.
   * Increasing distance strictly decreases utility, holding payload constant.
   * `payload_offered` never exceeds either `observation.payload_remaining` or `target.hp_remaining`.

2. **Manager election / no duplicate CFPs:** two `JetAgentRuntime`s both see the same unclaimed target and both call `scan_and_negotiate()` "simultaneously" (call both coroutines via `asyncio.gather`) — assert exactly one of them ends up with `role in ("MANAGER", "CONTRACTOR")` owning the contract (i.e. exactly one call to `try_open_contract` for that `target_id` succeeded), and the other remains `IDLE` or becomes a normal contractor via bidding (not a second manager).

3. **Multi-winner partial fulfillment (the scenario-critical test):** use the real scenario numbers (`config.SCENARIO`), isolated to `Target_A` only (don't give these observations a `Target_B` entry at all, so this test stays focused purely on the multi-winner mechanic rather than also exercising the two-target scheduling interplay — that full interplay is covered end-to-end in Phase 5):
   * `Jet_1` observation: `payload_remaining=1`, near `Target_A` (`hp_required=hp_remaining=5`, `claimed=False`).
   * `Jet_2`, `Jet_3` observations: `payload_remaining=4`, farther away, same target info.
   * Wire all three to one `MessageBroker`, start all three runtimes, feed each its observation via `set_observation`.
   * Have `Jet_1` call `scan_and_negotiate()` so it becomes Manager for `Target_A`.
   * Wait for negotiation to settle (poll roles / use `await asyncio.sleep` slightly longer than `config.BID_TIMEOUT_SECONDS`, or monkeypatch `config.BID_TIMEOUT_SECONDS` down to e.g. `0.05` for fast tests).
   * **Do not hardcode which agents win.** Because `Jet_2` and `Jet_3` both offer `min(4, 5) = 4` at a higher utility than `Jet_1`'s own `min(1, 5) = 1` self-bid (their full-payload ratio outweighs the extra distance penalty at these weights — this is expected and is exactly the F1 dynamic described in `docs/00_Design_Review_Findings.md`), the greedy acceptance algorithm may legitimately settle on **either** `{Jet_2, Jet_3}` (4+4=8 >= 5, `Jet_1` stays `IDLE`) **or** `{Jet_1, one of Jet_2/Jet_3}` (1+4=5 exactly), depending on bid arrival order. Assert only the invariants that must hold regardless: (a) the set of agents with `role == "CONTRACTOR"` and `committed_target_id == "Target_A"` has size `>= 2`; (b) the sum of their `committed_payload` is `>= target.hp_remaining` (`5`); (c) every agent not in that winning set is back to `role == "IDLE"`.

4. **Tie-break determinism:** construct a scenario where two bidders compute *exactly* equal `bid_utility` (e.g. identical distance and payload ratio by construction) and assert the lexicographically smaller `agent_id` is chosen as the (sole, if `hp_remaining` only needs one) winner, deterministically, across repeated runs.

5. **`decide_intent` unit tests (no broker/negotiation involved):**
   * An agent with no commitment returns `IDLE`.
   * A committed agent farther than `STRIKE_RANGE` from its target returns `MOVE` toward the target's position.
   * A committed agent within `STRIKE_RANGE` returns `STRIKE` on the committed `target_id` and decrements `committed_payload` by 1.
   * A committed agent whose target's `hp_remaining` has dropped to `0` (as reflected in a fresh `Observation`) or whose `committed_payload` has hit `0` resets to `IDLE`/`role="IDLE"` instead of continuing to strike.

## Explicit non-goals for this phase

* No live `WraithNetModel`/Mesa integration — `Observation`s are still hand-built or injected via `set_observation`, never read from a running model. That wiring is Phase 4.
* No handling of bid retraction or contractor failure after `ACCEPT` (explicit PoC simplification — see design doc v2 §5.3 point 8).
* Do not hardcode `Jet_2` or `Jet_3` specifically as "the winner" of the symmetric tie — assert the invariant, not a specific identity, unless you are specifically testing the tie-break rule with a constructed scenario designed to produce a known winner.

## Acceptance criteria

1. `pytest` passes, including all new tests in `test_cnp.py` and the additions to `test_broker.py`.
2. The multi-winner partial-fulfillment test (Part C.3) passes without any hardcoded "target hp_remaining is secretly 1" workaround — it must genuinely require two contributors.
3. `compute_bid` is a pure function with no side effects and no dependency on `asyncio`.
4. No test hangs — every `asyncio.sleep`/timeout path in the code under test must actually resolve within a few seconds using the real or monkeypatched `config.BID_TIMEOUT_SECONDS`.
