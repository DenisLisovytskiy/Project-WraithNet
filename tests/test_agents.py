"""Unit tests for JetAgentRuntime message loop."""

import asyncio
import uuid

import pytest

from wraithnet.agents import JetAgentRuntime
from wraithnet.broker import MessageBroker
from wraithnet.config import BROADCAST_ID
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
        weapon_type="JDAM",
    )


def make_cfp(sender_id, target_id="Target_A", tick=1):
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


async def _wait_until(predicate, timeout: float = 0.5) -> None:
    async def _poll() -> None:
        while not predicate():
            await asyncio.sleep(0.01)

    await asyncio.wait_for(_poll(), timeout=timeout)


@pytest.mark.asyncio
async def test_broadcast_cfp_received_by_other_runtimes():
    broker = MessageBroker()
    jet1 = JetAgentRuntime("Jet_1", broker)
    jet2 = JetAgentRuntime("Jet_2", broker)
    jet3 = JetAgentRuntime("Jet_3", broker)
    tasks = [jet1.start(), jet2.start(), jet3.start()]

    try:
        await broker.send(make_cfp("Jet_1"))
        await _wait_until(
            lambda: len(jet2.received_messages) == 1 and len(jet3.received_messages) == 1
        )
        assert jet2.received_messages[0].message_type == "CFP"
        assert jet2.received_messages[0].sender_id == "Jet_1"
        assert jet3.received_messages[0].message_type == "CFP"
        assert jet3.received_messages[0].sender_id == "Jet_1"
        assert jet1.received_messages == []
    finally:
        for jet in (jet1, jet2, jet3):
            jet.stop()
        await asyncio.gather(*tasks, return_exceptions=True)


@pytest.mark.asyncio
async def test_direct_bid_recorded_only_by_receiver_runtime():
    broker = MessageBroker()
    jet1 = JetAgentRuntime("Jet_1", broker)
    jet2 = JetAgentRuntime("Jet_2", broker)
    jet3 = JetAgentRuntime("Jet_3", broker)
    tasks = [jet1.start(), jet2.start(), jet3.start()]

    try:
        await broker.send(make_bid("Jet_2", "Jet_1"))
        await _wait_until(lambda: len(jet1.received_messages) == 1)
        assert jet1.received_messages[0].message_type == "BID"
        assert jet1.received_messages[0].sender_id == "Jet_2"
        assert jet3.received_messages == []
    finally:
        for jet in (jet1, jet2, jet3):
            jet.stop()
        await asyncio.gather(*tasks, return_exceptions=True)
