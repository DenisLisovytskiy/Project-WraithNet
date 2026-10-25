"""Phase 52 — weapon release attitude envelope gates."""

from __future__ import annotations

import pytest

from wraithnet import config
from wraithnet.config import WeaponSpec
from wraithnet.schemas import ActionIntent
from wraithnet.weapons_release import release_attitude_ok
from wraithnet.world import WraithNetModel


def test_helper_rejects_inverted_when_requires_upright():
    jdam = config.WEAPON_CATALOG["JDAM"]
    ok, reason = release_attitude_ok(jdam, pitch_deg=0.0, roll_deg=170.0)
    assert ok is False
    assert reason == "jet_inverted_release_forbidden"


def test_helper_rejects_excessive_roll_and_pitch():
    jdam = config.WEAPON_CATALOG["JDAM"]
    ok, reason = release_attitude_ok(jdam, pitch_deg=0.0, roll_deg=50.0)
    assert ok is False
    assert reason == "outside_release_attitude_roll"
    ok, reason = release_attitude_ok(jdam, pitch_deg=35.0, roll_deg=0.0)
    assert ok is False
    assert reason == "outside_release_attitude_pitch"


def test_helper_unconstrained_aam_ok_at_extreme_roll():
    aim = config.WEAPON_CATALOG["AIM9X"]
    amraam = config.WEAPON_CATALOG["AMRAAM"]
    assert aim.max_release_abs_roll_deg is None
    assert aim.requires_upright is False
    ok, reason = release_attitude_ok(aim, pitch_deg=80.0, roll_deg=170.0)
    assert ok is True and reason is None
    ok, reason = release_attitude_ok(amraam, pitch_deg=-70.0, roll_deg=-160.0)
    assert ok is True and reason is None


def test_helper_custom_none_caps_allow_extreme_attitude():
    loose = WeaponSpec(
        range=1.0,
        firing_arc_deg=360.0,
        slot_cost=1,
        damage_per_hit=1,
        suitability={"HARDENED_BUNKER": 1.0},
        requires_upright=False,
        max_release_abs_roll_deg=None,
        max_release_abs_pitch_deg=None,
    )
    ok, reason = release_attitude_ok(loose, pitch_deg=89.0, roll_deg=179.0)
    assert ok is True and reason is None


def test_inverted_jdam_strike_rejected_no_ammo_no_damage():
    model = WraithNetModel()
    jet = model.jets["Jet_2"]
    target = model.targets["Target_A"]
    model.space.move_agent(jet, target.position)
    jet.roll_deg = 170.0
    jet.pitch_deg = 0.0
    jdam_before = jet.payload["JDAM"]
    hp_before = target.hp_remaining
    model._apply_strike(jet, "Target_A", "JDAM")
    assert jet.payload["JDAM"] == jdam_before
    assert target.hp_remaining == hp_before
    assert model.strike_log == []


def test_upright_jdam_strike_succeeds_after_inverted_reject():
    model = WraithNetModel()
    jet = model.jets["Jet_2"]
    target = model.targets["Target_A"]
    model.space.move_agent(jet, target.position)
    jet.altitude = 10.0  # JDAM releases at or below max_release_altitude
    jet.roll_deg = 170.0
    before = jet.payload["JDAM"]
    hp = target.hp_remaining
    model._apply_strike(jet, "Target_A", "JDAM")
    assert jet.payload["JDAM"] == before
    assert target.hp_remaining == hp

    jet.roll_deg = 0.0
    jet.pitch_deg = 0.0
    model._apply_strike(jet, "Target_A", "JDAM")
    assert jet.payload["JDAM"] == before - 1
    assert target.hp_remaining == hp - 1
    assert model.strike_log[-1]["weapon_type"] == "JDAM"


def test_excessive_pitch_rejects_jdam():
    model = WraithNetModel()
    jet = model.jets["Jet_2"]
    target = model.targets["Target_A"]
    model.space.move_agent(jet, target.position)
    jet.roll_deg = 0.0
    jet.pitch_deg = 35.0  # > JDAM max 30
    before = jet.payload["JDAM"]
    hp = target.hp_remaining
    model._apply_strike(jet, "Target_A", "JDAM")
    assert jet.payload["JDAM"] == before
    assert target.hp_remaining == hp


def test_aim9x_attitude_unconstrained_vs_jdam_at_high_roll():
    """Missile contrast: AIM9X attitude gate passes where JDAM refuses."""
    model = WraithNetModel()
    jet = model.jets["Jet_3"]
    jet.payload = {"AIM9X": 1, "JDAM": 1, "AGM65": 0}
    jet.roll_deg = 120.0
    jet.pitch_deg = 0.0
    jet.altitude = 10.0  # JDAM releases at or below max_release_altitude

    assert model._jet_release_attitude_reason(jet, "JDAM") == (
        "jet_inverted_release_forbidden"
    )
    assert model._jet_release_attitude_reason(jet, "AIM9X") is None

    # Guided launch path returns the attitude reason for bombs/ASMs, not AAMs.
    agm_ack = model.try_launch_from_jet(jet, "Target_A", "AGM65")
    assert agm_ack.accepted is False
    assert agm_ack.reason == "jet_inverted_release_forbidden"

    aim_ack = model.try_launch_from_jet(jet, "Target_A", "AIM9X")
    # May fail taxonomy/suitability, but must not fail on attitude.
    assert aim_ack.reason not in {
        "jet_inverted_release_forbidden",
        "outside_release_attitude_roll",
        "outside_release_attitude_pitch",
    }


def test_wings_level_jdam_still_works_via_step():
    model = WraithNetModel()
    jet = model.jets["Jet_2"]
    target = model.targets["Target_A"]
    model.space.move_agent(jet, target.position)
    jet.altitude = 10.0  # JDAM releases at or below max_release_altitude
    jet.roll_deg = 0.0
    jet.pitch_deg = 0.0
    before = jet.payload["JDAM"]
    model.step(
        {
            "Jet_2": ActionIntent(
                agent_id="Jet_2",
                tick=0,
                action_type="STRIKE",
                target_id="Target_A",
                weapon_type="JDAM",
            )
        }
    )
    assert jet.payload["JDAM"] == before - 1
    assert target.hp_remaining == 4


def test_catalog_policy_matches_design_doc_section_6_2():
    jdam = config.WEAPON_CATALOG["JDAM"]
    assert jdam.requires_upright is True
    assert jdam.max_release_abs_roll_deg == 45.0
    assert jdam.max_release_abs_pitch_deg == 30.0
    gun = config.WEAPON_CATALOG["GUN_POD"]
    assert gun.requires_upright is False
    assert gun.max_release_abs_roll_deg == 90.0
    for name in ("AGM65", "AGM88", "AGM84"):
        w = config.WEAPON_CATALOG[name]
        assert w.requires_upright is True
        assert w.max_release_abs_roll_deg == 60.0
        assert w.max_release_abs_pitch_deg == 40.0
