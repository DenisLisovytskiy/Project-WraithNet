"""Pydantic message/data schemas for WraithNet.

Implemented in Phase 1 (Schemas & World Scaffolding).
See docs/phases/Phase_1_Schemas_and_World.md.
Phase 6 adds weapon/target typing and Observation sensor/jam fields.
Phase 7: DATALINK_CONTACT message type; contract_id optional for contacts.
"""

from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

from wraithnet.config import BROADCAST_ID, TargetType, WeaponType


class TargetInfo(BaseModel):
    target_id: str
    position: tuple[float, float]
    target_type: TargetType
    hp_required: int = Field(ge=1)
    hp_remaining: int = Field(ge=0)
    claimed: bool = False

    @model_validator(mode="after")
    def _check_hp_remaining_not_above_required(self) -> "TargetInfo":
        if self.hp_remaining > self.hp_required:
            raise ValueError("hp_remaining cannot exceed hp_required")
        return self


class Observation(BaseModel):
    agent_id: str
    tick: int = Field(ge=0)
    current_position: tuple[float, float]
    sensor_radius: float = Field(gt=0)
    payload: dict[WeaponType, int]
    known_targets: list[TargetInfo]
    comms_jammed: bool = False

    @property
    def total_payload_remaining(self) -> int:
        return sum(self.payload.values())


class ActionIntent(BaseModel):
    agent_id: str
    tick: int = Field(ge=0)
    action_type: Literal["MOVE", "STRIKE", "IDLE"]
    target_position: Optional[tuple[float, float]] = None
    target_id: Optional[str] = None
    weapon_type: Optional[WeaponType] = None

    @model_validator(mode="after")
    def _check_required_fields_for_action(self) -> "ActionIntent":
        if self.action_type == "MOVE" and self.target_position is None:
            raise ValueError("MOVE requires target_position")
        if self.action_type == "STRIKE":
            if self.target_id is None:
                raise ValueError("STRIKE requires target_id")
            if self.weapon_type is None:
                raise ValueError("STRIKE requires weapon_type")
        return self


class Message(BaseModel):
    message_id: str
    contract_id: Optional[str] = None  # None only for DATALINK_CONTACT
    tick: int = Field(ge=0)
    sender_id: str
    receiver_id: str  # BROADCAST_ID sentinel, or a specific agent_id
    message_type: Literal["CFP", "BID", "ACCEPT", "REJECT", "DATALINK_CONTACT"]
    target_id: str
    hp_remaining: Optional[int] = None
    payload_offered: Optional[int] = None
    weapon_type: Optional[WeaponType] = None
    bid_utility: Optional[float] = None
    target_info: Optional[TargetInfo] = None  # required on DATALINK_CONTACT

    @model_validator(mode="after")
    def _check_required_fields_for_message_type(self) -> "Message":
        if self.message_type == "DATALINK_CONTACT":
            if self.target_info is None:
                raise ValueError("DATALINK_CONTACT requires target_info")
            if self.contract_id is not None:
                raise ValueError("DATALINK_CONTACT must not carry a contract_id")
            if self.receiver_id != BROADCAST_ID:
                raise ValueError("DATALINK_CONTACT must be broadcast")
            return self
        if self.contract_id is None:
            raise ValueError(f"{self.message_type} requires contract_id")
        if self.message_type == "CFP":
            if self.hp_remaining is None:
                raise ValueError("CFP requires hp_remaining")
            if self.receiver_id != BROADCAST_ID:
                raise ValueError("CFP must be broadcast")
        if self.message_type == "BID":
            if self.payload_offered is None or self.bid_utility is None or self.weapon_type is None:
                raise ValueError("BID requires payload_offered, bid_utility, and weapon_type")
        if self.message_type in ("BID", "ACCEPT", "REJECT"):
            if self.receiver_id == BROADCAST_ID:
                raise ValueError(f"{self.message_type} must not be broadcast")
        return self
