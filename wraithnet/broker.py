"""Message Broker: in-process asyncio pub/sub for agent-to-agent CNP messages.

Phase 2: pure routing (broadcast fan-out + direct delivery).
Phase 3 extends this class with negotiation-aware behavior (open-contract
tracking / manager-election arbitration) — keep routing centralized in
`_route()` so that extension has a single place to add a guard clause.
Phase 9: jamming-aware routing — jammed senders/recipients drop silently.
"""

import asyncio

from wraithnet import config
from wraithnet.config import BROADCAST_ID
from wraithnet.schemas import Message


class MessageBroker:
    def __init__(self) -> None:
        self._mailboxes: dict[str, asyncio.Queue[Message]] = {}
        self.open_contracts: dict[str, dict] = {}
        # target_id -> {"contract_id": str, "manager_id": str, "opened_tick": int}
        self._jammed: set[str] = set()

    def set_jammed(self, agent_id: str, jammed: bool) -> None:
        if jammed:
            self._jammed.add(agent_id)
        else:
            self._jammed.discard(agent_id)

    def is_jammed(self, agent_id: str) -> bool:
        return agent_id in self._jammed

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
        # Jammed sender's traffic never leaves (Phase 9).
        if message.sender_id in self._jammed:
            return
        if message.receiver_id == BROADCAST_ID:
            for agent_id, mailbox in self._mailboxes.items():
                if agent_id != message.sender_id and agent_id not in self._jammed:
                    await mailbox.put(message)
        else:
            if message.receiver_id in self._jammed:
                return
            mailbox = self._mailboxes.get(message.receiver_id)
            if mailbox is not None:
                await mailbox.put(message)
            # else: dropped, no receiver registered under that id

    async def receive(self, agent_id: str) -> Message:
        """Block until a message arrives for agent_id."""
        return await self._mailboxes[agent_id].get()

    def inbox_size(self, agent_id: str) -> int:
        return self._mailboxes[agent_id].qsize()

    def try_open_contract(
        self, target_id: str, contract_id: str, manager_id: str, tick: int
    ) -> bool:
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
            tid
            for tid, info in self.open_contracts.items()
            if current_tick - info["opened_tick"] > config.CONTRACT_CLAIM_TTL_TICKS
        ]
        for tid in expired:
            del self.open_contracts[tid]
        return expired
