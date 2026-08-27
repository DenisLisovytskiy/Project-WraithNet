# Phase 2 — The Message Broker & Agent Async Loop Skeleton

> **How to use this document:** This is a complete, standalone prompt. Give an LLM coding agent this entire file as its task. Assume Phases 0–1 are already complete: `wraithnet/config.py` (constants + scenario), `wraithnet/schemas.py` (`TargetInfo`, `Observation`, `ActionIntent`, `Message`), and `wraithnet/world.py` (`WraithNetModel`, `JetAgent`, `TargetState`) all exist and are tested. Do not modify those files. Only edit `wraithnet/broker.py` and `wraithnet/agents.py`, and add tests.

## Context

WraithNet is a Proof-of-Concept multi-agent simulator: autonomous jets negotiate over radio (Contract Net Protocol) to decide who strikes which target. This is **Phase 2 of 6**. This phase builds pure **message-passing plumbing** — a Message Broker and a generic async agent loop that can send and receive `Message` objects — with **no negotiation semantics yet** (no utility function, no bid evaluation, no accept/reject decisions). Those come in Phase 3. This phase must prove the pipes work before Phase 3 puts real protocol logic through them.

Concurrency model (do not deviate — see `docs/00_Design_Review_Findings.md` item A3): **single-threaded asyncio**. There is exactly one event loop. The Broker and every agent's mailbox use `asyncio.Queue`. Do not introduce OS threads, `queue.Queue`, multiprocessing, or any network transport (no ZeroMQ/WebSockets) — this is intentionally in-process.

Recall the relevant schema (from Phase 1, already implemented in `wraithnet/schemas.py` — import it, do not redefine it):

```python
class Message(BaseModel):
    message_id: str
    contract_id: str
    tick: int
    sender_id: str
    receiver_id: str  # BROADCAST_ID sentinel or a specific agent_id
    message_type: Literal["CFP", "BID", "ACCEPT", "REJECT"]
    target_id: str
    hp_remaining: Optional[int] = None
    payload_offered: Optional[int] = None
    bid_utility: Optional[float] = None
```

`BROADCAST_ID = "BROADCAST"` is defined in `wraithnet/config.py`.

## Goal

1. Implement `MessageBroker` in `wraithnet/broker.py`: agent registration, broadcast fan-out (to everyone except the sender), and direct point-to-point delivery, all via per-agent `asyncio.Queue` mailboxes.
2. Implement `JetAgentRuntime` in `wraithnet/agents.py`: a coroutine-based loop per agent that continuously receives messages from its mailbox and dispatches them to an (as-yet-trivial) handler. Structure it so Phase 3 can plug in real CNP behavior without restructuring the loop.
3. Prove round-trip delivery (broadcast and direct) works with tests using real `Message` objects — not literal "ping" strings. (The original design doc said "simple ping messages"; here that means messages that exercise only the *routing* mechanics, not the CNP *decision* logic, since our schema only defines CFP/BID/ACCEPT/REJECT message types — there is no separate "PING" type, and we should not invent one that later phases would have to remove.)

## Part A — `wraithnet/broker.py`

```python
"""Message Broker: in-process asyncio pub/sub for agent-to-agent CNP messages.

Phase 2: pure routing (broadcast fan-out + direct delivery).
Phase 3 extends this class with negotiation-aware behavior (open-contract
tracking / manager-election arbitration) — keep routing centralized in
`_route()` so that extension has a single place to add a guard clause.
"""

import asyncio

from wraithnet.config import BROADCAST_ID
from wraithnet.schemas import Message


class MessageBroker:
    def __init__(self) -> None:
        self._mailboxes: dict[str, asyncio.Queue[Message]] = {}

    def register(self, agent_id: str) -> None:
        """Create a mailbox for agent_id. Safe to call once per agent at startup."""
        if agent_id not in self._mailboxes:
            self._mailboxes[agent_id] = asyncio.Queue()

    def is_registered(self, agent_id: str) -> bool:
        return agent_id in self._mailboxes

    async def send(self, message: Message) -> None:
        """Route a message. Broadcasts go to every registered agent except the
        sender. Direct messages go only to the named receiver. A direct message
        to an unregistered receiver_id is silently dropped (logged, not raised)
        — this mirrors real radio behavior (no ack of delivery)."""
        await self._route(message)

    async def _route(self, message: Message) -> None:
        if message.receiver_id == BROADCAST_ID:
            for agent_id, mailbox in self._mailboxes.items():
                if agent_id != message.sender_id:
                    await mailbox.put(message)
        else:
            mailbox = self._mailboxes.get(message.receiver_id)
            if mailbox is not None:
                await mailbox.put(message)
            # else: dropped, no receiver registered under that id

    async def receive(self, agent_id: str) -> Message:
        """Block until a message arrives for agent_id."""
        return await self._mailboxes[agent_id].get()

    def inbox_size(self, agent_id: str) -> int:
        return self._mailboxes[agent_id].qsize()
```

## Part B — `wraithnet/agents.py`

```python
"""Agent async runtime: one coroutine loop per jet.

Phase 2: generic message-receive loop with a trivial handler (store + count).
Phase 3 replaces `handle_message` with the full CNP state machine (CFP/BID/
ACCEPT/REJECT handling, utility function, bidding). Keep `run()`'s loop
structure (receive -> dispatch to handle_message -> repeat) unchanged so
Phase 3 is a pure extension, not a rewrite.
"""

import asyncio

from wraithnet.broker import MessageBroker
from wraithnet.schemas import Message


class JetAgentRuntime:
    def __init__(self, agent_id: str, broker: MessageBroker) -> None:
        self.agent_id = agent_id
        self.broker = broker
        self.broker.register(agent_id)
        self.received_messages: list[Message] = []
        self._task: asyncio.Task | None = None

    async def run(self) -> None:
        """Main loop: forever receive and dispatch. Cancel the wrapping Task to stop."""
        while True:
            message = await self.broker.receive(self.agent_id)
            await self.handle_message(message)

    async def handle_message(self, message: Message) -> None:
        """Phase 2 placeholder: just record what arrived. No decisions made here yet."""
        self.received_messages.append(message)

    def start(self) -> asyncio.Task:
        self._task = asyncio.create_task(self.run())
        return self._task

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
```

Notes:
* `run()` is an infinite loop by design (an agent is always listening); tests must start it as a `Task` and cancel it when done (see tests below), not expect it to return.
* Do not add any CFP/BID/ACCEPT/REJECT-specific branching in `handle_message` yet — that's Phase 3. This phase's `handle_message` must remain protocol-agnostic.
* Do not import `wraithnet.world` here — Phase 2 has no connection to the Mesa model at all; that link is built in Phase 4.

## Part C — Tests

### `tests/test_broker.py` (use `pytest-asyncio`; mark async tests with `@pytest.mark.asyncio`)

Helper to build a valid `Message` quickly (put this in the test file):

```python
import uuid
from wraithnet.schemas import Message

def make_bid(sender_id, receiver_id, target_id="Target_A", tick=1):
    return Message(
        message_id=uuid.uuid4().hex,
        contract_id="contract-1",
        tick=tick,
        sender_id=sender_id,
        receiver_id=receiver_id,
        message_type="BID",
        target_id=target_id,
        payload_offered=1,
        bid_utility=0.5,
    )

def make_cfp(sender_id, target_id="Target_A", tick=1):
    from wraithnet.config import BROADCAST_ID
    return Message(
        message_id=uuid.uuid4().hex,
        contract_id="contract-1",
        tick=tick,
        sender_id=sender_id,
        receiver_id=BROADCAST_ID,
        message_type="CFP",
        target_id=target_id,
        hp_remaining=2,
    )
```

Tests:
* Registering `"Jet_1"`, `"Jet_2"`, `"Jet_3"`, then broadcasting a CFP from `"Jet_1"`: `"Jet_2"` and `"Jet_3"`'s mailboxes each receive exactly one message (assert via `await broker.receive(...)`), and `"Jet_1"` does **not** receive its own broadcast (assert `broker.inbox_size("Jet_1") == 0` immediately after).
* Sending a direct BID from `"Jet_2"` to `"Jet_1"`: only `"Jet_1"`'s mailbox receives it; `"Jet_3"`'s inbox size stays `0`.
* Sending a direct message to an unregistered `receiver_id` (e.g. `"Ghost_Jet"`) does not raise and does not affect any registered agent's inbox.
* `broker.is_registered("Jet_1")` is `True` after registration, `False` for an unregistered id.

### `tests/test_agents.py`

* Wire three `JetAgentRuntime`s to one shared `MessageBroker`, `.start()` all three.
* Have `Jet_1` call `await broker.send(make_cfp("Jet_1"))` directly (bypassing its own runtime, simulating "an agent decided to send this").
* `await asyncio.wait_for(...)` with a short timeout (e.g. `0.5`s) polling until `Jet_2.received_messages` and `Jet_3.received_messages` each have length `1`, then assert the received message's `message_type == "CFP"` and `sender_id == "Jet_1"`.
* Clean up: call `.stop()` on all three runtimes and `await asyncio.gather(*tasks, return_exceptions=True)` to let cancellation settle, so the test suite doesn't leak tasks/warnings.
* A second test: direct BID from `Jet_2` to `Jet_1` is recorded in `Jet_1.received_messages` but never appears in `Jet_3.received_messages`.

## Explicit non-goals for this phase

* No utility function, no bid evaluation/acceptance logic, no manager-election/open-contract tracking (all Phase 3).
* No connection to `wraithnet.world` / Mesa (`ActionIntent`/`Observation` are untouched here) — that's Phase 4.
* No `main.py` changes.

## Acceptance criteria

1. `pytest` passes, including all async tests (requires `pytest-asyncio`, already in `requirements-dev.txt` from Phase 0).
2. `MessageBroker` correctly distinguishes broadcast vs. direct delivery and never delivers a broadcast back to its own sender.
3. `JetAgentRuntime.run()` can be started as a background `Task` and cleanly cancelled without raising unhandled exceptions in the test output.
4. No test leaves a dangling/warning `Task` (all started tasks are cancelled and awaited before the test ends).
5. Neither `broker.py` nor `agents.py` imports anything from `wraithnet.world`.
