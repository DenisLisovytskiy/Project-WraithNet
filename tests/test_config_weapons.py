"""Catalog-level invariants for Phase 21's weapon specs."""

from wraithnet import config


def test_every_weapon_spec_is_well_formed():
    for name, spec in config.WEAPON_CATALOG.items():
        assert spec.range > 0, name
        assert spec.slot_cost >= 1, name
        assert spec.damage_per_hit >= 1, name
        assert spec.firing_arc_deg > 0, name
        assert any(s > 0.0 for s in spec.suitability.values()), name


def test_catalog_includes_all_aspect_and_narrow_forward_cone():
    arcs = [spec.firing_arc_deg for spec in config.WEAPON_CATALOG.values()]
    assert any(a == 360.0 for a in arcs)
    assert any(a < 360.0 for a in arcs)


def test_jdam_and_aim9x_preserve_legacy_range_arc_and_suitability():
    """Default-PoC weapons must keep today's 1.0-unit omnidirectional strike
    and the Phase 6 suitability numbers exactly — not approximately."""
    jdam = config.WEAPON_CATALOG["JDAM"]
    aim9x = config.WEAPON_CATALOG["AIM9X"]
    assert jdam.range == 1.0
    assert aim9x.range == 1.0
    assert jdam.firing_arc_deg == 360.0
    assert aim9x.firing_arc_deg == 360.0
    assert jdam.damage_per_hit == 1
    assert aim9x.damage_per_hit == 1
    assert jdam.suitability == {
        "HARDENED_BUNKER": 1.0,
        "MOBILE_RADAR": 0.0,
        "SAM_SITE": 1.0,
    }
    assert aim9x.suitability == {
        "HARDENED_BUNKER": 0.0,
        "MOBILE_RADAR": 1.0,
        "SAM_SITE": 0.0,
    }


def test_amraam_is_longer_range_all_aspect_and_not_a_strict_aim9x_upgrade():
    amraam = config.WEAPON_CATALOG["AMRAAM"]
    assert amraam.firing_arc_deg == 360.0
    assert amraam.range > config.WEAPON_CATALOG["JDAM"].range
    assert amraam.suitability["MOBILE_RADAR"] == 1.0
    assert amraam.suitability["SAM_SITE"] == 1.0
    assert 0.0 < amraam.suitability["HARDENED_BUNKER"] < 1.0


def test_gun_pod_is_shorter_range_narrow_forward_cone():
    gun = config.WEAPON_CATALOG["GUN_POD"]
    assert gun.firing_arc_deg < 360.0
    assert gun.range < config.WEAPON_CATALOG["JDAM"].range
    assert gun.suitability["HARDENED_BUNKER"] == 1.0
    assert gun.damage_per_hit > config.WEAPON_CATALOG["JDAM"].damage_per_hit
