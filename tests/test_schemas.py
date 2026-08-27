"""Unit tests for WraithNet Pydantic schemas."""

import pytest
from pydantic import ValidationError

from wraithnet.config import BROADCAST_ID
from wraithnet.schemas import ActionIntent, Message, Observation, TargetInfo


def _valid_target_info(**overrides) -> TargetInfo:
    data = {
        "target_id": "Target_A",
        "position": (75.0, 75.0),
        "target_type": "HARDENED_BUNKER",
        "hp_required": 5,
        "hp_remaining": 5,
        "claimed": False,
    }
    data.update(overrides)
    return TargetInfo(**data)


def test_target_info_constructs_with_valid_data():
    t = _valid_target_info()
    assert t.target_id == "Target_A"
    assert t.position == (75.0, 75.0)
    assert t.target_type == "HARDENED_BUNKER"
    assert t.hp_required == 5
    assert t.hp_remaining == 5
    assert t.claimed is False


@pytest.mark.parametrize(
    "target_type", ["HARDENED_BUNKER", "MOBILE_RADAR", "SAM_SITE"]
)
def test_target_info_accepts_each_target_type(target_type):
    t = _valid_target_info(target_type=target_type)
    assert t.target_type == target_type


def test_target_info_rejects_invalid_target_type():
    with pytest.raises(ValidationError):
        _valid_target_info(target_type="SOFT_TENT")


def test_observation_constructs_with_valid_data():
    obs = Observation(
        agent_id="Jet_1",
        tick=0,
        current_position=(70.0, 70.0),
        sensor_radius=65.0,
        payload={"JDAM": 1, "AIM9X": 0},
        known_targets=[
            _valid_target_info(),
            _valid_target_info(
                target_id="Target_B",
                position=(25.0, 25.0),
                target_type="MOBILE_RADAR",
                hp_required=1,
                hp_remaining=1,
            ),
        ],
    )
    assert obs.agent_id == "Jet_1"
    assert len(obs.known_targets) == 2
    assert all(isinstance(t, TargetInfo) for t in obs.known_targets)
    assert obs.comms_jammed is False


def test_observation_total_payload_remaining_sums_dict():
    obs = Observation(
        agent_id="Jet_X",
        tick=0,
        current_position=(0.0, 0.0),
        sensor_radius=65.0,
        payload={"JDAM": 2, "AIM9X": 3},
        known_targets=[],
    )
    assert obs.total_payload_remaining == 5


def test_action_intent_constructs_for_each_action_type():
    move = ActionIntent(
        agent_id="Jet_1",
        tick=0,
        action_type="MOVE",
        target_position=(75.0, 75.0),
    )
    strike = ActionIntent(
        agent_id="Jet_1",
        tick=0,
        action_type="STRIKE",
        target_id="Target_A",
        weapon_type="JDAM",
    )
    idle = ActionIntent(agent_id="Jet_1", tick=0, action_type="IDLE")
    assert move.action_type == "MOVE"
    assert strike.action_type == "STRIKE"
    assert idle.action_type == "IDLE"


def test_message_constructs_cfp_and_bid():
    cfp = Message(
        message_id="m1",
        contract_id="c1",
        tick=0,
        sender_id="Jet_1",
        receiver_id=BROADCAST_ID,
        message_type="CFP",
        target_id="Target_A",
        hp_remaining=5,
    )
    bid = Message(
        message_id="m2",
        contract_id="c1",
        tick=0,
        sender_id="Jet_2",
        receiver_id="Jet_1",
        message_type="BID",
        target_id="Target_A",
        payload_offered=4,
        bid_utility=0.75,
        weapon_type="JDAM",
    )
    assert cfp.message_type == "CFP"
    assert bid.message_type == "BID"
    assert bid.weapon_type == "JDAM"


def test_move_without_target_position_raises():
    with pytest.raises(ValidationError):
        ActionIntent(agent_id="Jet_1", tick=0, action_type="MOVE")


def test_strike_without_target_id_raises():
    with pytest.raises(ValidationError):
        ActionIntent(agent_id="Jet_1", tick=0, action_type="STRIKE", weapon_type="JDAM")


def test_strike_without_weapon_type_raises():
    with pytest.raises(ValidationError):
        ActionIntent(agent_id="Jet_1", tick=0, action_type="STRIKE", target_id="Target_A")


def test_cfp_without_hp_remaining_raises():
    with pytest.raises(ValidationError):
        Message(
            message_id="m1",
            contract_id="c1",
            tick=0,
            sender_id="Jet_1",
            receiver_id=BROADCAST_ID,
            message_type="CFP",
            target_id="Target_A",
        )


def test_cfp_not_broadcast_raises():
    with pytest.raises(ValidationError):
        Message(
            message_id="m1",
            contract_id="c1",
            tick=0,
            sender_id="Jet_1",
            receiver_id="Jet_2",
            message_type="CFP",
            target_id="Target_A",
            hp_remaining=5,
        )


def test_bid_without_payload_or_utility_raises():
    with pytest.raises(ValidationError):
        Message(
            message_id="m2",
            contract_id="c1",
            tick=0,
            sender_id="Jet_2",
            receiver_id="Jet_1",
            message_type="BID",
            target_id="Target_A",
            payload_offered=4,
            weapon_type="JDAM",
        )
    with pytest.raises(ValidationError):
        Message(
            message_id="m2",
            contract_id="c1",
            tick=0,
            sender_id="Jet_2",
            receiver_id="Jet_1",
            message_type="BID",
            target_id="Target_A",
            bid_utility=0.5,
            weapon_type="JDAM",
        )


def test_bid_without_weapon_type_raises():
    with pytest.raises(ValidationError):
        Message(
            message_id="m2",
            contract_id="c1",
            tick=0,
            sender_id="Jet_2",
            receiver_id="Jet_1",
            message_type="BID",
            target_id="Target_A",
            payload_offered=4,
            bid_utility=0.5,
        )


def test_target_info_hp_required_must_be_at_least_one():
    with pytest.raises(ValidationError):
        _valid_target_info(hp_required=0)


def test_target_info_hp_remaining_cannot_exceed_required():
    with pytest.raises(ValidationError):
        _valid_target_info(hp_required=5, hp_remaining=6)


@pytest.mark.parametrize("message_type", ["BID", "ACCEPT", "REJECT"])
def test_point_to_point_messages_must_not_be_broadcast(message_type):
    kwargs = {
        "message_id": "m1",
        "contract_id": "c1",
        "tick": 0,
        "sender_id": "Jet_1",
        "receiver_id": BROADCAST_ID,
        "message_type": message_type,
        "target_id": "Target_A",
    }
    if message_type == "BID":
        kwargs["payload_offered"] = 1
        kwargs["bid_utility"] = 0.5
        kwargs["weapon_type"] = "JDAM"
    with pytest.raises(ValidationError):
        Message(**kwargs)


def test_datalink_contact_validates_successfully():
    msg = Message(
        message_id="m1",
        contract_id=None,
        tick=0,
        sender_id="Jet_1",
        receiver_id=BROADCAST_ID,
        message_type="DATALINK_CONTACT",
        target_id="Target_A",
        target_info=_valid_target_info(),
    )
    assert msg.message_type == "DATALINK_CONTACT"
    assert msg.target_info is not None
    assert msg.contract_id is None


def test_datalink_contact_requires_target_info():
    with pytest.raises(ValidationError):
        Message(
            message_id="m1",
            contract_id=None,
            tick=0,
            sender_id="Jet_1",
            receiver_id=BROADCAST_ID,
            message_type="DATALINK_CONTACT",
            target_id="Target_A",
        )


def test_datalink_contact_rejects_contract_id():
    with pytest.raises(ValidationError):
        Message(
            message_id="m1",
            contract_id="c1",
            tick=0,
            sender_id="Jet_1",
            receiver_id=BROADCAST_ID,
            message_type="DATALINK_CONTACT",
            target_id="Target_A",
            target_info=_valid_target_info(),
        )


def test_datalink_contact_must_be_broadcast():
    with pytest.raises(ValidationError):
        Message(
            message_id="m1",
            contract_id=None,
            tick=0,
            sender_id="Jet_1",
            receiver_id="Jet_2",
            message_type="DATALINK_CONTACT",
            target_id="Target_A",
            target_info=_valid_target_info(),
        )


@pytest.mark.parametrize("message_type", ["CFP", "BID", "ACCEPT", "REJECT"])
def test_non_datalink_messages_still_require_contract_id(message_type):
    kwargs = {
        "message_id": "m1",
        "contract_id": None,
        "tick": 0,
        "sender_id": "Jet_1",
        "receiver_id": "Jet_2" if message_type != "CFP" else BROADCAST_ID,
        "message_type": message_type,
        "target_id": "Target_A",
    }
    if message_type == "CFP":
        kwargs["hp_remaining"] = 5
    if message_type == "BID":
        kwargs["payload_offered"] = 1
        kwargs["bid_utility"] = 0.5
        kwargs["weapon_type"] = "JDAM"
    with pytest.raises(ValidationError):
        Message(**kwargs)
