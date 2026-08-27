"""Unit tests for MessageBroker routing."""

import uuid

import pytest

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


@pytest.mark.asyncio
async def test_broadcast_cfp_fans_out_excluding_sender():
    broker = MessageBroker()
    for agent_id in ("Jet_1", "Jet_2", "Jet_3"):
        broker.register(agent_id)

    await broker.send(make_cfp("Jet_1"))

    msg2 = await broker.receive("Jet_2")
    msg3 = await broker.receive("Jet_3")
    assert msg2.message_type == "CFP"
    assert msg2.sender_id == "Jet_1"
    assert msg3.message_type == "CFP"
    assert msg3.sender_id == "Jet_1"
    assert broker.inbox_size("Jet_1") == 0


@pytest.mark.asyncio
async def test_direct_bid_delivered_only_to_receiver():
    broker = MessageBroker()
    for agent_id in ("Jet_1", "Jet_2", "Jet_3"):
        broker.register(agent_id)

    await broker.send(make_bid("Jet_2", "Jet_1"))

    msg = await broker.receive("Jet_1")
    assert msg.message_type == "BID"
    assert msg.sender_id == "Jet_2"
    assert msg.receiver_id == "Jet_1"
    assert broker.inbox_size("Jet_3") == 0


@pytest.mark.asyncio
async def test_direct_to_unregistered_receiver_is_dropped():
    broker = MessageBroker()
    for agent_id in ("Jet_1", "Jet_2", "Jet_3"):
        broker.register(agent_id)

    sizes_before = {aid: broker.inbox_size(aid) for aid in ("Jet_1", "Jet_2", "Jet_3")}
    await broker.send(make_bid("Jet_1", "Ghost_Jet"))
    sizes_after = {aid: broker.inbox_size(aid) for aid in ("Jet_1", "Jet_2", "Jet_3")}
    assert sizes_after == sizes_before


def test_is_registered():
    broker = MessageBroker()
    assert broker.is_registered("Jet_1") is False
    broker.register("Jet_1")
    assert broker.is_registered("Jet_1") is True
    assert broker.is_registered("Jet_2") is False


def test_try_open_contract_grants_once():
    broker = MessageBroker()
    assert broker.try_open_contract("Target_A", "c1", "Jet_1", tick=0) is True
    assert broker.is_claimed("Target_A") is True
    assert broker.try_open_contract("Target_A", "c2", "Jet_2", tick=1) is False
    assert broker.open_contracts["Target_A"]["manager_id"] == "Jet_1"


def test_close_contract_frees_target():
    broker = MessageBroker()
    assert broker.try_open_contract("Target_A", "c1", "Jet_1", tick=0) is True
    broker.close_contract("Target_A")
    assert broker.is_claimed("Target_A") is False
    assert broker.try_open_contract("Target_A", "c2", "Jet_2", tick=2) is True


def test_expire_stale_contracts():
    from wraithnet import config

    broker = MessageBroker()
    broker.try_open_contract("Target_A", "c1", "Jet_1", tick=0)
    broker.try_open_contract("Target_B", "c2", "Jet_2", tick=8)
    expired = broker.expire_stale_contracts(
        current_tick=config.CONTRACT_CLAIM_TTL_TICKS + 1
    )
    assert expired == ["Target_A"]
    assert broker.is_claimed("Target_A") is False
    assert broker.is_claimed("Target_B") is True


@pytest.mark.asyncio
async def test_jammed_sender_broadcast_reaches_no_one():
    broker = MessageBroker()
    for agent_id in ("Jet_1", "Jet_2", "Jet_3"):
        broker.register(agent_id)
    broker.set_jammed("Jet_1", True)
    await broker.send(make_cfp("Jet_1"))
    assert broker.inbox_size("Jet_2") == 0
    assert broker.inbox_size("Jet_3") == 0
    assert broker.inbox_size("Jet_1") == 0


@pytest.mark.asyncio
async def test_jammed_recipient_skipped_on_broadcast():
    broker = MessageBroker()
    for agent_id in ("Jet_1", "Jet_2", "Jet_3"):
        broker.register(agent_id)
    broker.set_jammed("Jet_2", True)
    await broker.send(make_cfp("Jet_1"))
    assert broker.inbox_size("Jet_2") == 0
    msg3 = await broker.receive("Jet_3")
    assert msg3.message_type == "CFP"
    assert msg3.sender_id == "Jet_1"


@pytest.mark.asyncio
async def test_direct_dropped_when_sender_jammed():
    broker = MessageBroker()
    for agent_id in ("Jet_1", "Jet_2", "Jet_3"):
        broker.register(agent_id)
    broker.set_jammed("Jet_2", True)
    await broker.send(make_bid("Jet_2", "Jet_1"))
    assert broker.inbox_size("Jet_1") == 0


@pytest.mark.asyncio
async def test_direct_dropped_when_receiver_jammed_then_delivered_after_clear():
    broker = MessageBroker()
    for agent_id in ("Jet_1", "Jet_2", "Jet_3"):
        broker.register(agent_id)
    broker.set_jammed("Jet_1", True)
    await broker.send(make_bid("Jet_2", "Jet_1"))
    assert broker.inbox_size("Jet_1") == 0
    broker.set_jammed("Jet_1", False)
    await broker.send(make_bid("Jet_2", "Jet_1"))
    msg = await broker.receive("Jet_1")
    assert msg.message_type == "BID"
    assert msg.sender_id == "Jet_2"


def test_is_jammed_toggles():
    broker = MessageBroker()
    assert broker.is_jammed("Jet_1") is False
    broker.set_jammed("Jet_1", True)
    assert broker.is_jammed("Jet_1") is True
    broker.set_jammed("Jet_1", False)
    assert broker.is_jammed("Jet_1") is False
    broker.set_jammed("Jet_1", True)
    broker.set_jammed("Jet_1", True)  # idempotent
    assert broker.is_jammed("Jet_1") is True
