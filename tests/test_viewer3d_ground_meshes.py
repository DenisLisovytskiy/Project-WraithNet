"""Procedural ground meshes and friend/enemy paint."""

from __future__ import annotations

import pytest

pytest.importorskip("panda3d")

from panda3d.core import BillboardEffect, GeomVertexReader, NodePath, loadPrcFileData

loadPrcFileData("", "window-type none")
loadPrcFileData("", "audio-library-name null")
loadPrcFileData("", "notify-level error")

from viewer3d.scene import build_static_scene, sync_static_unit_glyphs
from viewer3d.textures import (
    ENEMY,
    FRIENDLY,
    attach_aaa_light,
    attach_aaa_turreted,
    attach_corvette_light,
    attach_corvette_standard,
    attach_escort_carrier,
    attach_fleet_carrier,
    attach_ground_mesh,
    attach_hardened_bunker,
    attach_home_base,
    attach_jammer,
    attach_mbt_light,
    attach_mbt_standard,
    attach_mobile_radar,
    attach_sam_site,
    attach_strike_fighter,
    faction_paint,
)
from wraithnet.trace_schemas import TankFrame, TraceHeader


def _rgb_of(node: NodePath):
    c = node.get_color()
    return float(c[0]), float(c[1]), float(c[2])


def _extent(node: NodePath):
    box = node.get_tight_bounds()
    assert box is not None
    lo, hi = box
    return hi.x - lo.x, hi.y - lo.y, hi.z - lo.z


def _fill_vertex_colors(node: NodePath) -> set[tuple[float, float, float]]:
    colors: set[tuple[float, float, float]] = set()
    fill = node.find("**/sym-fill")
    assert not fill.is_empty()
    geom_node = fill.node()
    for gi in range(geom_node.get_num_geoms()):
        reader = GeomVertexReader(geom_node.get_geom(gi).get_vertex_data(), "color")
        while not reader.is_at_end():
            c = reader.get_data4()
            colors.add((round(float(c[0]), 2), round(float(c[1]), 2), round(float(c[2]), 2)))
    return colors


def _fill_vertex_count(node: NodePath) -> int:
    fill = node.find("**/sym-fill")
    assert not fill.is_empty()
    geom_node = fill.node()
    return sum(geom_node.get_geom(i).get_vertex_data().get_num_rows() for i in range(geom_node.get_num_geoms()))


def _header() -> TraceHeader:
    return TraceHeader(
        grid_width=100.0,
        grid_height=100.0,
        sensor_radius=65.0,
        jam_radius=12.0,
        sam_engagement_range=15.0,
        strike_range=1.0,
        jet_ids=["Jet_1"],
        initial_payload={"Jet_1": {"JDAM": 1, "AIM9X": 0}},
        target_ids=["Bunker", "Radar", "Sam", "Aaa"],
        target_types={
            "Bunker": "HARDENED_BUNKER",
            "Radar": "MOBILE_RADAR",
            "Sam": "SAM_SITE",
            "Aaa": "AAA_SITE",
        },
        target_hp_required={"Bunker": 5, "Radar": 1, "Sam": 1, "Aaa": 2},
        sam_site_ids=["Sam"],
        aaa_site_ids=["Aaa"],
        jammer_positions={"EW_1": (40.0, 40.0)},
        target_positions={
            "Bunker": (10.0, 10.0),
            "Radar": (20.0, 10.0),
            "Sam": (30.0, 10.0),
            "Aaa": (40.0, 10.0),
        },
        jet_frame_types={"Jet_1": "STRIKE_FIGHTER"},
    )


def test_faction_paints_split_teal_from_rust():
    fr = faction_paint("FRIENDLY")
    en = faction_paint("ENEMY")
    assert fr[1] + fr[2] > fr[0]
    assert en[0] > en[1]


def test_friendly_and_enemy_tanks_share_hull_but_not_paint():
    root = NodePath("mbts")
    friend = attach_mbt_standard(root, "f", FRIENDLY)
    foe = attach_mbt_standard(root, "e", ENEMY)
    fr, fg, fb = _rgb_of(friend)
    er, eg, eb = _rgb_of(foe)
    assert fg > fr
    assert er > eg
    fw, fl, fh = _extent(friend)
    ew, el, eh = _extent(foe)
    assert (fw, fl, fh) == pytest.approx((ew, el, eh), rel=0.01)


def test_standard_tank_is_bulkier_with_a_longer_gun_than_light():
    root = NodePath("mbts")
    light = attach_mbt_light(root, "l", FRIENDLY)
    heavy = attach_mbt_standard(root, "h", FRIENDLY)
    lw, ll, lh = _extent(light)
    hw, hl, hh = _extent(heavy)
    assert hl > ll
    assert hw > lw
    assert hh >= lh


def test_aaa_turreted_is_taller_than_the_gun_pit():
    root = NodePath("aaa")
    pit = attach_aaa_light(root, "p", ENEMY)
    spaag = attach_aaa_turreted(root, "s", ENEMY)
    _pw, _pl, ph = _extent(pit)
    _sw, _sl, sh = _extent(spaag)
    assert sh > ph


def test_aaa_is_skyward_not_a_hull():
    root = NodePath("aaa")
    pit = attach_aaa_light(root, "p", ENEMY)
    spaag = attach_aaa_turreted(root, "s", ENEMY)
    pw, pl, ph = _extent(pit)
    sw, sl, sh = _extent(spaag)
    assert ph > pw and ph > pl
    assert sh > sw and sh > sl


def test_tank_has_volume_and_a_horizontal_gun():
    root = NodePath("mbts")
    tank = attach_mbt_standard(root, "h", FRIENDLY)
    w, length, h = _extent(tank)
    assert h > 1.25
    assert w > 1.45
    assert length > h
    assert length > w


def test_bunker_is_a_squat_mass_bulkier_than_tank_and_aaa():
    root = NodePath("mass")
    bunker = attach_hardened_bunker(root, "b", ENEMY)
    tank = attach_mbt_standard(root, "t", ENEMY)
    aaa = attach_aaa_turreted(root, "a", ENEMY)
    bw, bl, bh = _extent(bunker)
    tw, tl, th = _extent(tank)
    aw, al, ah = _extent(aaa)
    assert bh > 2.2
    assert bw > tw * 1.6 and bl > tl * 1.15
    assert bw > aw * 1.6 and bl > al * 1.15
    assert bw > bh and bl > bh
    assert ah > bh
    assert th < bh


def test_radar_is_taller_than_the_bunker():
    root = NodePath("inst")
    bunker = attach_hardened_bunker(root, "b", ENEMY)
    radar = attach_mobile_radar(root, "r", ENEMY)
    _bw, _bl, bh = _extent(bunker)
    _rw, _rl, rh = _extent(radar)
    assert rh > bh
    assert rh > 3.0


def test_radar_is_a_long_truck_with_a_wide_panel():
    root = NodePath("radar")
    radar = attach_mobile_radar(root, "r", ENEMY)
    w, length, h = _extent(radar)
    assert h > 3.5
    assert w > 2.2
    assert length > 2.0


def test_mobile_radar_has_a_spinning_rotor_head():
    from viewer3d.scene import SceneHandles, animate_radars

    root = NodePath("radar")
    radar = attach_mobile_radar(root, "r", ENEMY)
    rotor = radar.find("**/radar-rotor")
    assert not rotor.is_empty()
    assert rotor.get_tag("radar-spin") == "1"
    before = float(rotor.get_h())
    handles = SceneHandles(root=root, fx_root=root.attach_new_node("fx"))
    animate_radars(handles, 1.0)
    assert float(rotor.get_h()) == pytest.approx(before + handles.radar_spin_deg_per_sec)
    # Yaw only — the AESA face stays skyward (no pitch/roll).
    assert float(rotor.get_p()) == pytest.approx(0.0)
    assert float(rotor.get_r()) == pytest.approx(0.0)
    rotor.set_tag("radar-spin", "0")
    mid = float(rotor.get_h())
    animate_radars(handles, 1.0)
    assert float(rotor.get_h()) == pytest.approx(mid)

def test_bunker_radar_jammer_have_baked_panel_textures():
    root = NodePath("tex")
    bunker = attach_hardened_bunker(root, "b", ENEMY)
    radar = attach_mobile_radar(root, "r", ENEMY)
    jammer = attach_jammer(root, "j", ENEMY)
    b_cols = _fill_vertex_colors(bunker)
    r_cols = _fill_vertex_colors(radar)
    j_cols = _fill_vertex_colors(jammer)
    assert len(b_cols) >= 6
    assert len(r_cols) >= 8
    assert len(j_cols) >= 6
    assert any(r > g + 0.08 and r > b for r, g, b in b_cols)
    assert any(r > 0.55 and r > b + 0.20 for r, g, b in r_cols)
    assert any(g > b and r < 0.70 for r, g, b in r_cols)
    assert any((r + g + b) / 3.0 > 0.55 for r, g, b in r_cols)
    assert any(b > r + 0.08 and b >= g for r, g, b in j_cols)
    assert not any(r > 0.55 and b > 0.70 and g < r * 0.75 for r, g, b in j_cols)
    assert _fill_vertex_count(bunker) >= 200
    assert _fill_vertex_count(radar) >= 400
    assert _fill_vertex_count(jammer) >= 200


def test_carriers_tanks_aaa_have_shattered_textures():
    root = NodePath("more")
    escort = attach_escort_carrier(root, "cv", FRIENDLY)
    fleet = attach_fleet_carrier(root, "cvn", FRIENDLY)
    tank_f = attach_mbt_standard(root, "tf", FRIENDLY)
    tank_e = attach_mbt_standard(root, "te", ENEMY)
    pit = attach_aaa_light(root, "pit", ENEMY)
    spaag = attach_aaa_turreted(root, "spaag", ENEMY)
    for node, minimum in (
        (escort, 8),
        (fleet, 8),
        (tank_f, 6),
        (pit, 6),
        (spaag, 6),
    ):
        cols = _fill_vertex_colors(node)
        assert len(cols) >= minimum, node.get_name()
        assert _fill_vertex_count(node) >= 200, node.get_name()
    cv_cols = _fill_vertex_colors(escort)
    # Haze-gray hull stays cool/neutral; weathering shards may tint slightly
    # greenish (deck lines, rust, window glow) so allow a soft green margin.
    assert all(g <= b + 0.12 for r, g, b in cv_cols)
    assert any(r > 0.50 and b >= g for r, g, b in cv_cols)
    aaa_cols = _fill_vertex_colors(spaag)
    assert any(r > g + 0.08 and r > b for r, g, b in aaa_cols)
    friend_g = max(g for _r, g, _b in _fill_vertex_colors(tank_f))
    foe_r = max(r for r, _g, _b in _fill_vertex_colors(tank_e))
    assert friend_g > 0.35
    assert foe_r > 0.35


def test_jammer_rings_spin_independently():
    from viewer3d.scene import SceneHandles, animate_jammers

    root = NodePath("ew")
    jammer = attach_jammer(root, "j", ENEMY)
    rings = [jammer.find(f"**/jammer-ring-{i}") for i in range(3)]
    assert all(not r.is_empty() for r in rings)
    assert all(r.get_tag("jammer-spin") == "1" for r in rings)
    rates = [float(r.get_python_tag("spin_deg_per_sec")) for r in rings]
    assert len(set(rates)) == 3
    assert any(rate > 0 for rate in rates) and any(rate < 0 for rate in rates)
    before = [float(r.get_h()) for r in rings]
    handles = SceneHandles(root=root, fx_root=root.attach_new_node("fx"))
    animate_jammers(handles, 1.0)
    after = [float(r.get_h()) for r in rings]
    for i, rate in enumerate(rates):
        assert after[i] == pytest.approx(before[i] + rate)
    # Stopping one ring must not freeze the others.
    rings[1].set_tag("jammer-spin", "0")
    mid = [float(r.get_h()) for r in rings]
    animate_jammers(handles, 1.0)
    assert float(rings[1].get_h()) == pytest.approx(mid[1])
    assert float(rings[0].get_h()) == pytest.approx(mid[0] + rates[0])
    assert float(rings[2].get_h()) == pytest.approx(mid[2] + rates[2])


def test_jammer_and_sam_have_shattered_textures():
    root = NodePath("ew-sam")
    jammer = attach_jammer(root, "j", ENEMY)
    sam = attach_sam_site(root, "s", ENEMY)
    j_cols = _fill_vertex_colors(jammer)
    s_cols = _fill_vertex_colors(sam)
    assert len(j_cols) >= 8
    assert len(s_cols) >= 8
    assert _fill_vertex_count(jammer) >= 400
    assert _fill_vertex_count(sam) >= 400
    assert any(b > r + 0.08 and b >= g for r, g, b in j_cols)
    assert not any(r > 0.55 and b > 0.70 and g < r * 0.75 for r, g, b in j_cols)
    assert any(r > g + 0.20 and r > b for r, g, b in s_cols)


def test_home_base_and_corvettes_have_baked_textures():
    root = NodePath("rest")
    home = attach_home_base(root, "hb", FRIENDLY)
    light = attach_corvette_light(root, "ffl", ENEMY)
    standard = attach_corvette_standard(root, "ff", ENEMY)
    h_cols = _fill_vertex_colors(home)
    c_cols = _fill_vertex_colors(standard)
    assert len(h_cols) >= 8
    assert len(c_cols) >= 6
    assert _fill_vertex_count(home) >= 200
    assert _fill_vertex_count(light) >= 200
    assert _fill_vertex_count(standard) >= 200
    assert any(g > r + 0.12 for r, g, _b in h_cols)
    assert any(abs(r - g) < 0.10 and r > 0.30 for r, g, _b in h_cols)
    assert any(r > g + 0.12 and r > b for r, g, b in c_cols)


def test_jammer_is_a_tall_hex_antenna_farm():
    root = NodePath("ew")
    jammer = attach_jammer(root, "j", ENEMY)
    radar = attach_mobile_radar(root, "r", ENEMY)
    jw, jl, jh = _extent(jammer)
    rw, rl, rh = _extent(radar)
    assert jh > 2.8
    assert jw > 2.0
    assert abs(jw - jl) < 0.65
    assert jh > jw * 0.85
    assert abs(rl - rw) > abs(jl - jw)


def test_ground_catalog_meshes_attach_and_are_not_billboards():
    root = NodePath("g")
    for key, fn in (
        ("HARDENED_BUNKER", attach_hardened_bunker),
        ("MOBILE_RADAR", attach_mobile_radar),
        ("SAM_SITE", attach_sam_site),
        ("JAMMER", attach_jammer),
        ("HOME_BASE", attach_home_base),
    ):
        node = fn(root, key, ENEMY if key != "HOME_BASE" else FRIENDLY)
        tagged = attach_ground_mesh(root, f"t-{key}", key, ENEMY)
        assert tagged is not None and tagged.get_tag("ground-mesh") == key
        assert node.get_effect(BillboardEffect) is None


def test_scene_paints_jets_and_carriers_friendly_and_hostiles_enemy():
    root = NodePath("scene")
    handles = build_static_scene(root, _header())
    jet = handles.jet_glyphs["Jet_1"]
    jr, jg, jb = _rgb_of(jet)
    assert jg + jb > jr
    bunker = handles.targets["Bunker"].find("**/glyph-Bunker")
    assert bunker.get_tag("ground-mesh") == "HARDENED_BUNKER"
    br, bg, _bb = _rgb_of(bunker)
    assert br > bg
    jammer = handles.jammers["EW_1"].find("**/glyph-EW_1")
    assert jammer.get_tag("ground-mesh") == "JAMMER"
    home = handles.home_base.find("**/home-flag")
    assert home.get_tag("ground-mesh") == "HOME_BASE"
    hr, hg, _hb = _rgb_of(home)
    assert hg > hr

    sync_static_unit_glyphs(
        handles,
        tanks=[
            TankFrame(
                unit_id="T_F",
                position=(50.0, 50.0),
                hp=6,
                hp_max=6,
                faction="FRIENDLY",
                frame_type="MBT_STANDARD",
            ),
            TankFrame(
                unit_id="T_E",
                position=(55.0, 50.0),
                hp=6,
                hp_max=6,
                faction="ENEMY",
                frame_type="MBT_STANDARD",
            ),
        ],
    )
    tf = handles.tanks["T_F"].find("**/glyph-T_F")
    te = handles.tanks["T_E"].find("**/glyph-T_E")
    tfr, tfg, _tfb = _rgb_of(tf)
    ter, teg, _teb = _rgb_of(te)
    assert tfg > tfr
    assert ter > teg


def test_ships_keep_faction_colors():
    root = NodePath("navy")
    carrier = attach_escort_carrier(root, "cv", FRIENDLY)
    corvette = attach_corvette_standard(root, "ff", ENEMY)
    cr, cg, cb = _rgb_of(carrier)
    vr, vg, _vb = _rgb_of(corvette)
    assert cg + cb > cr
    assert vr > vg
    jet = attach_strike_fighter(root, "j", faction_paint("FRIENDLY"))
    jr, jg, jb = _rgb_of(jet)
    assert jg + jb > jr


def test_faction_markers_spin_and_hide_on_destroy():
    from viewer3d.scene import (
        MARKER_SPIN_DEG_PER_SEC,
        SceneHandles,
        _attach_faction_marker,
        _set_faction_marker_alive,
        animate_faction_markers,
    )

    root = NodePath("markers")
    anchor = root.attach_new_node("unit")
    marker = _attach_faction_marker(anchor, "FRIENDLY")
    assert marker.get_tag("faction-marker-spin") == "1"
    before = float(marker.get_h())
    handles = SceneHandles(root=root, fx_root=root.attach_new_node("fx"))
    animate_faction_markers(handles, 1.0)
    assert float(marker.get_h()) == pytest.approx(before + MARKER_SPIN_DEG_PER_SEC)
    _set_faction_marker_alive(anchor, False)
    assert marker.is_hidden()
    mid = float(marker.get_h())
    animate_faction_markers(handles, 1.0)
    assert float(marker.get_h()) == pytest.approx(mid)
