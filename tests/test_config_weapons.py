"""Catalog-level invariants for Phase 21/38 weapon specs."""

from wraithnet import config


def test_every_weapon_spec_is_well_formed():
    for name, spec in config.WEAPON_CATALOG.items():
        assert spec.range > 0, name
        assert spec.slot_cost >= 1, name
        assert spec.damage_per_hit >= 1, name
        assert spec.firing_arc_deg > 0, name
        # Air-to-air / SAM weapons may have all-zero ground suitability;
        # guided weapons must declare a guidance mode.
        if any(s > 0.0 for s in spec.suitability.values()):
            continue
        assert spec.guidance in ("INFRARED", "RADAR"), name


def test_catalog_includes_all_aspect_and_narrow_forward_cone():
    arcs = [spec.firing_arc_deg for spec in config.WEAPON_CATALOG.values()]
    assert any(a == 360.0 for a in arcs)
    assert any(a < 360.0 for a in arcs)


def test_jdam_preserves_legacy_range_arc_and_suitability():
    """Default-PoC JDAM must keep today's 1.0-unit omnidirectional strike."""
    jdam = config.WEAPON_CATALOG["JDAM"]
    assert jdam.range == 1.0
    assert jdam.firing_arc_deg == 360.0
    assert jdam.damage_per_hit == 1
    assert jdam.guidance == "NONE"
    assert jdam.suitability == {
        "HARDENED_BUNKER": 1.0,
        "MOBILE_RADAR": 0.0,
        "SAM_SITE": 1.0,
        "AAA_SITE": 1.0,
        "SURFACE_VESSEL": 1.0,
        "GROUND_VEHICLE": 1.0,
    }


def test_agm65_inherits_legacy_aim9x_ground_role():
    """AGM65 replaces pre-guidance AIM9X against installations (Phase 38)."""
    agm = config.WEAPON_CATALOG["AGM65"]
    assert agm.range == 1.0
    assert agm.firing_arc_deg == 360.0
    assert agm.guidance == "INFRARED"
    assert agm.suitability["MOBILE_RADAR"] == 1.0
    assert agm.suitability["HARDENED_BUNKER"] == 1.0
    assert agm.suitability["SAM_SITE"] == 1.0


def test_aim9x_and_amraam_are_air_to_air_guided():
    aim9x = config.WEAPON_CATALOG["AIM9X"]
    amraam = config.WEAPON_CATALOG["AMRAAM"]
    assert aim9x.guidance == "INFRARED"
    assert amraam.guidance == "RADAR"
    assert all(v == 0.0 for v in aim9x.suitability.values())
    assert all(v == 0.0 for v in amraam.suitability.values())
    assert amraam.range > config.WEAPON_CATALOG["JDAM"].range
    assert amraam.firing_arc_deg == 360.0


def test_gun_pod_is_shorter_range_narrow_forward_cone():
    gun = config.WEAPON_CATALOG["GUN_POD"]
    assert gun.firing_arc_deg < 360.0
    assert gun.range < config.WEAPON_CATALOG["JDAM"].range
    assert gun.suitability["HARDENED_BUNKER"] == 1.0
    assert gun.damage_per_hit > config.WEAPON_CATALOG["JDAM"].damage_per_hit
    assert gun.guidance == "NONE"


def test_sam_weapons_and_signatures_exist():
    assert config.WEAPON_CATALOG["SAM_IR"].guidance == "INFRARED"
    assert config.WEAPON_CATALOG["SAM_RADAR"].guidance == "RADAR"
    assert config.TARGET_SIGNATURE_DEFAULTS["SAM_SITE"].radar_emitter is True
    assert config.TARGET_SIGNATURE_DEFAULTS["MOBILE_RADAR"].radar_emitter is True
    assert config.TARGET_SIGNATURE_DEFAULTS["HARDENED_BUNKER"].heat < 0.2
    jet_sig = config.JET_FRAME_CATALOG["STRIKE_FIGHTER"].signature
    assert jet_sig.heat > 0.0
    assert jet_sig.radar_cross_section > 0.0

