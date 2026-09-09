"""Taxonomy hostility and weapon/target-class gate tests (Phase 37)."""

from __future__ import annotations

from wraithnet.taxonomy import (
    are_hostile,
    is_legal_weapon_target,
    reject_engagement,
    target_class_for_target_type,
)


def test_are_hostile_only_across_factions():
    assert are_hostile("FRIENDLY", "ENEMY") is True
    assert are_hostile("ENEMY", "FRIENDLY") is True
    assert are_hostile("FRIENDLY", "FRIENDLY") is False
    assert are_hostile("ENEMY", "ENEMY") is False


def test_target_class_maps_static_facilities_to_installation():
    assert target_class_for_target_type("HARDENED_BUNKER") == "INSTALLATION"
    assert target_class_for_target_type("MOBILE_RADAR") == "INSTALLATION"
    assert target_class_for_target_type("SAM_SITE") == "INSTALLATION"


def test_reject_engagement_friendly_fire():
    reason = reject_engagement(
        shooter_faction="FRIENDLY",
        target_faction="FRIENDLY",
        weapon="JDAM",
        target_class="INSTALLATION",
    )
    assert reason == "friendly_fire"


def test_reject_engagement_illegal_target_class():
    # JDAM may not engage AIRCRAFT.
    assert is_legal_weapon_target("JDAM", "AIRCRAFT") is False
    reason = reject_engagement(
        shooter_faction="FRIENDLY",
        target_faction="ENEMY",
        weapon="JDAM",
        target_class="AIRCRAFT",
    )
    assert reason == "illegal_target_class"


def test_reject_engagement_allows_legal_hostile_shot():
    assert (
        reject_engagement(
            shooter_faction="FRIENDLY",
            target_faction="ENEMY",
            weapon="JDAM",
            target_class="INSTALLATION",
        )
        is None
    )
    assert (
        reject_engagement(
            shooter_faction="FRIENDLY",
            target_faction="ENEMY",
            weapon="AIM9X",
            target_class="AIRCRAFT",
        )
        is None
    )
