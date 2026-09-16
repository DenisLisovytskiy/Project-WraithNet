"""Style/regression checks for the live 3D HUD (`viewer3d.hud`).

The headline regression these lock down: every `DirectFrame` in the HUD used to
be built with `relief=None`, which makes DirectGUI ignore `frameColor`
outright, so the four corner panels, the minimap box, all 48 minimap dots and
the role swatches painted *nothing* — the live HUD was unbacked text floating
over the wireframe ground, and no test noticed for months. These tests assert
the pixels-would-exist properties a screenshot check would catch: a real
relief, a visible fill, text parented into its panel, panels sized to their
content and kept inside their corner.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

pytest.importorskip("panda3d")

from panda3d.core import loadPrcFileData

loadPrcFileData("", "window-type none")
loadPrcFileData("", "audio-library-name null")
loadPrcFileData("", "notify-level error")

from direct.showbase.ShowBase import ShowBase

from viewer3d import fonts, hud as hud_module
from viewer3d.highlights import RunSummary
from viewer3d.hud import _MAX_MINIMAP_DOTS, ROLE_RING_COLOR, Hud
from viewer3d.ui_chrome import STATUS_FRIENDLY, STATUS_HOSTILE
from wraithnet.trace_schemas import JetFrame, TargetFrame, TraceHeader, TraceTick

ROOT = Path(__file__).resolve().parent.parent


# --- fixtures / fakes ----------------------------------------------------


@pytest.fixture(scope="module")
def base():
    app = ShowBase(windowType="none")
    yield app
    app.destroy()


@pytest.fixture
def hud(base):
    return Hud(base)


def _header(jets: int = 3) -> TraceHeader:
    jet_ids = [f"Jet_{i + 1}" for i in range(jets)]
    return TraceHeader(
        grid_width=100.0,
        grid_height=100.0,
        sensor_radius=65.0,
        jam_radius=12.0,
        sam_engagement_range=15.0,
        strike_range=1.0,
        jet_ids=jet_ids,
        initial_payload={jid: {"JDAM": 2, "AIM9X": 2} for jid in jet_ids},
        target_ids=["Bunker_1", "Radar_1", "SAM_Site_1"],
        target_types={
            "Bunker_1": "HARDENED_BUNKER",
            "Radar_1": "MOBILE_RADAR",
            "SAM_Site_1": "SAM_SITE",
        },
        target_hp_required={"Bunker_1": 5, "Radar_1": 2, "SAM_Site_1": 1},
        sam_site_ids=["SAM_Site_1"],
        jammer_positions={"EW_Jammer_1": (46.0, 19.0)},
        target_positions={
            "Bunker_1": (75.0, 75.0),
            "Radar_1": (28.0, 62.0),
            "SAM_Site_1": (40.0, 85.0),
        },
        jet_frame_types={jid: "STRIKE_FIGHTER" for jid in jet_ids},
        jet_frame_stats={"STRIKE_FIGHTER": {"hp": 5.0, "fuel_capacity": 1200.0}},
    )


def _jet(agent_id: str, position, *, hp: int = 5, fuel: float = 900.0, role="IDLE") -> JetFrame:
    return JetFrame(
        agent_id=agent_id,
        position=position,
        hp=hp,
        payload={"JDAM": 1, "AIM9X": 1},
        destroyed=False,
        jammed=False,
        role=role,
        fuel=fuel,
    )


def _tick(n: int, jets, *, running: bool = True, destroyed_first: bool = True) -> TraceTick:
    return TraceTick(
        tick=n,
        running=running,
        jets=jets,
        targets=[
            TargetFrame(target_id="Bunker_1", hp_remaining=0 if destroyed_first else 5, claimed=False),
            TargetFrame(target_id="Radar_1", hp_remaining=2, claimed=False),
            TargetFrame(target_id="SAM_Site_1", hp_remaining=1, claimed=False),
        ],
        strikes=[],
        swarm_wiped_out=False,
    )


def _live_jets():
    return [
        _jet("Jet_1", (20.0, 30.0), hp=5, fuel=1100.0, role="MANAGER"),
        _jet("Jet_2", (55.0, 44.0), hp=3, fuel=520.0, role="CONTRACTOR"),
        _jet("Jet_3", (72.0, 68.0), hp=1, fuel=180.0, role="RTB"),
    ]


def _go_live(hud: Hud) -> None:
    header = _header()
    hud.update(
        mode="live",
        header=header,
        latest_tick=_tick(42, _live_jets()),
        camera_mode="orbit",
        selected_jet_id="Jet_1",
        sampled_xy={"Jet_1": (20.0, 30.0)},
    )


def _fill(panel):
    return getattr(panel, "_chrome_fill", None)


def _filled_widgets(hud: Hud) -> dict:
    """Every HUD widget that is supposed to put color on the screen."""
    widgets = {
        "mission panel": hud._mission_frame,
        "mission fill": _fill(hud._mission_frame),
        "mission mode stripe": hud._mission_stripe,
        "force panel": hud._force_frame,
        "force fill": _fill(hud._force_frame),
        "force rule": hud._force_rule,
        "coord panel": hud._coord_frame,
        "coord fill": _fill(hud._coord_frame),
        "minimap box": hud._minimap_frame,
        "minimap fill": _fill(hud._minimap_frame),
        "controls panel": hud._controls_frame,
        "controls fill": _fill(hud._controls_frame),
        "summary card": hud._summary_frame,
        "summary fill": _fill(hud._summary_frame),
        "summary rule": hud._summary_rule,
        "debug card": hud._debug_frame,
        "debug fill": _fill(hud._debug_frame),
    }
    widgets.update({f"minimap dot {i}": d for i, d in enumerate(hud._minimap_dots)})
    widgets.update({f"role swatch {i}": s for i, s in enumerate(hud._role_swatches)})
    return widgets


def _text_color(widget, state: int = 0):
    """DirectGUI's `text_fg` is write-only; read the glyph color instead."""
    return tuple(widget.component(f"text{state}").textNode.get_text_color())


def _font_name(onscreen_text) -> str | None:
    font = onscreen_text.textNode.get_font()
    return None if font is None else font.get_name()


# --- the relief regression ----------------------------------------------


def test_every_filled_hud_widget_paints(hud):
    for label, widget in _filled_widgets(hud).items():
        assert widget is not None, f"{label}: missing"
        assert widget["relief"] is not None, f"{label}: relief=None paints nothing"
        color = widget["frameColor"]
        assert color[3] > 0.0, f"{label}: fully transparent fill"


def test_panel_fills_are_opaque_enough_to_read_over_the_3d_scene(hud):
    for label in ("mission fill", "force fill", "coord fill", "controls fill"):
        fill = _filled_widgets(hud)[label]
        assert fill["frameColor"][3] > 0.5, label


def test_minimap_dots_are_pooled_reused_and_stay_inside_the_box(hud):
    assert len(hud._minimap_dots) == _MAX_MINIMAP_DOTS == 48
    pooled = list(hud._minimap_dots)
    assert all(dot.is_hidden() for dot in pooled)

    _go_live(hud)
    shown = [dot for dot in hud._minimap_dots if not dot.is_hidden()]
    # 1 jammer + 3 targets + 3 jets
    assert len(shown) == 7
    half = hud._minimap_half
    for dot in shown:
        assert abs(dot.get_x()) <= half, "dot escaped the minimap box"
        assert abs(dot.get_z()) <= half, "dot escaped the minimap box"
        assert hud._minimap_frame.is_ancestor_of(dot)

    hud.update(
        mode="live",
        header=_header(),
        latest_tick=_tick(43, _live_jets()[:1]),
        camera_mode="orbit",
        selected_jet_id="Jet_1",
    )
    assert hud._minimap_dots == pooled, "the dot pool must be reused, not rebuilt"
    assert len([d for d in hud._minimap_dots if not d.is_hidden()]) == 5


def test_role_swatches_are_visible_and_keep_the_shared_ring_palette(hud):
    assert len(hud._role_swatches) == len(ROLE_RING_COLOR) == 5
    for swatch, color in zip(hud._role_swatches, ROLE_RING_COLOR.values()):
        assert tuple(swatch["frameColor"]) == pytest.approx(color)
        assert swatch["relief"] is not None
        assert not swatch.is_hidden()
        assert hud._controls_frame.is_ancestor_of(swatch)


# --- layout --------------------------------------------------------------


def test_every_readout_is_parented_into_its_panel(hud):
    pairs = (
        (hud._mission_frame, hud._mission_node),
        (hud._force_frame, hud._force_node),
        (hud._coord_frame, hud._coord_node),
        (hud._controls_frame, hud._controls_node),
        (hud._summary_frame, hud._summary_node),
        (hud._debug_frame, hud._debug_node),
    )
    for panel, node in pairs:
        assert panel.is_ancestor_of(node), f"{node} floats outside its panel"


def _rect(panel):
    left, right, bottom, top = (float(v) for v in panel["frameSize"])
    return left, right, bottom, top


def _height(panel) -> float:
    _left, _right, bottom, top = _rect(panel)
    return top - bottom


def test_panels_fit_their_corner_at_any_aspect(hud):
    # Corner-anchored x/z are relative to the corner, and the aspect ratio
    # changes with the window, so nothing may rely on a 16:9 x budget.
    for panel in (hud._mission_frame, hud._force_frame, hud._coord_frame, hud._controls_frame):
        left, right, bottom, top = _rect(panel)
        assert 0.0 < right - left <= 0.90
        assert 0.0 < top - bottom <= 1.00

    # Same-side panels share the 2.0-unit screen height.
    assert _height(hud._mission_frame) + _height(hud._controls_frame) < 1.9
    assert _height(hud._force_frame) + _height(hud._coord_frame) < 1.9


def test_panels_are_sized_to_their_content(hud):
    def height(panel):
        left, right, bottom, top = _rect(panel)
        return top - bottom

    header = _header()
    hud.update(
        mode="live",
        header=header,
        latest_tick=_tick(1, _live_jets()[:1]),
        camera_mode="orbit",
        selected_jet_id="Jet_1",
    )
    small_force = _height(hud._force_frame)
    short_mission = _height(hud._mission_frame)

    hud.update(
        mode="live",
        header=header,
        latest_tick=_tick(2, _live_jets()),
        camera_mode="orbit",
        selected_jet_id="Jet_1",
        disconnect_reason="could not connect after retries",
    )
    assert _height(hud._force_frame) > small_force, "force panel must grow with rows"
    assert _height(hud._mission_frame) > short_mission, "mission panel must grow with detail"

    # The border shell and its inset fill must resize together.
    for panel in (hud._force_frame, hud._mission_frame):
        left, right, bottom, top = _rect(panel)
        f_left, f_right, f_bottom, f_top = (float(v) for v in _fill(panel)["frameSize"])
        assert bottom < f_bottom < f_top < top
        assert left < f_left < f_right < right


def test_force_table_rows_are_pooled_mono_and_tone_coded(hud):
    _go_live(hud)
    rows = [row for row in hud._jet_rows if not row.node.is_hidden()]
    assert len(rows) == 3
    assert [row.key["text"] for row in rows] == ["Jet_1", "Jet_2", "Jet_3"]
    for row in rows:
        assert "HP" in row.value["text"] and "FUEL" in row.value["text"]
        # Same baseline, value pushed to a fixed right-hand column.
        assert row.key.get_z() == pytest.approx(row.value.get_z())
        assert row.value.get_x() > row.key.get_x()
    if fonts.mono_font() is not None:
        mono = fonts.mono_font().get_name()
        assert rows[0].value["text_font"].get_name() == mono
        assert _font_name(hud._force_node) == mono
    # Jet_1 is at full HP, Jet_3 at 1/5.
    assert _text_color(rows[0].value) == pytest.approx(STATUS_FRIENDLY, abs=1e-6)
    assert _text_color(rows[2].value) == pytest.approx(STATUS_HOSTILE, abs=1e-6)

    hud.update(
        mode="live",
        header=_header(),
        latest_tick=_tick(43, _live_jets()[:1]),
        camera_mode="orbit",
        selected_jet_id="Jet_1",
    )
    assert len([r for r in hud._jet_rows if not r.node.is_hidden()]) == 1


def test_jet_table_pool_is_bounded_and_reports_overflow(hud):
    jets = [_jet(f"Jet_{i}", (float(i), float(i))) for i in range(1, 21)]
    header = _header(jets=20)
    hud.update(
        mode="live",
        header=header,
        latest_tick=_tick(7, jets),
        camera_mode="orbit",
        selected_jet_id="Jet_1",
    )
    shown = [row for row in hud._jet_rows if not row.node.is_hidden()]
    assert len(shown) <= len(hud._jet_rows)
    assert "MORE" in shown[-1].key["text"]
    # Every jet is still accounted for in the text attribute.
    for jet in jets:
        assert jet.agent_id in hud.force_status_text


# --- content the rest of the app / tests rely on -------------------------


def test_required_tokens_survive_the_restyle(hud):
    hud.update(
        mode="connecting",
        header=None,
        latest_tick=None,
        camera_mode="orbit",
        selected_jet_id=None,
    )
    assert "CONNECTING" in hud.mission_info_text
    assert hud._mission_node.getText() == hud.mission_info_text

    _go_live(hud)
    for token in ("[ SCENARIO ]", "[ STATUS ]", "[ TICK ]", "LIVE"):
        assert token in hud.mission_info_text, token
    for token in ("[ ALL ENEMIES ]", "[ JETS ]", "BUNKER", "0 / 1", "SAM", "1 / 1", "MANAGER"):
        assert token in hud.force_status_text, token
    assert "ALIVE   3 / 3" in hud.force_status_text
    assert "[ COORDINATES ]" in hud.coordinates_text
    assert "[ CAMERA ]" in hud.controls_text
    assert "[ CONTROLS ]" in hud.controls_text
    assert "ORBIT" in hud.controls_text
    assert hud._mission_node.getText() == hud.mission_info_text

    hud.update(
        mode="live",
        header=_header(),
        latest_tick=_tick(50, _live_jets()),
        camera_mode="chase",
        selected_jet_id="Jet_2",
        disconnect_reason="could not connect after retries",
    )
    assert "DISCONNECTED" in hud.mission_info_text
    assert "could not connect after retries" in hud.mission_info_text
    assert hud._mission_node.getText() == hud.mission_info_text

    hud.update(
        mode="launch_failed",
        header=None,
        latest_tick=None,
        camera_mode="brief",
        selected_jet_id=None,
    )
    assert "LAUNCH FAILED" in hud.mission_info_text

    for mode, label in (
        ("editor", "EDITOR"),
        ("brief", "MISSION BRIEF"),
        ("finished", "FINISHED"),
    ):
        hud.update(
            mode=mode,
            header=_header(),
            latest_tick=None,
            camera_mode="brief",
            selected_jet_id=None,
        )
        assert label in hud.mission_info_text, mode


@pytest.mark.parametrize(
    ("wiped", "solved", "title"),
    [
        (True, False, "SWARM WIPED OUT"),
        (False, True, "MISSION COMPLETE"),
        (False, False, "END OF RUN"),
    ],
)
def test_end_of_run_summary_sits_on_a_card(hud, wiped, solved, title, monkeypatch):
    header = _header()
    fake = RunSummary(
        solved=solved,
        swarm_wiped_out=wiped,
        targets_destroyed=1 if not solved else 3,
        total_targets=3,
        jets_destroyed=3 if wiped else 0,
        total_jets=3,
        total_missiles_fired=7,
        per_weapon_type_missiles_fired={"JDAM": 4, "AIM9X": 3},
        mission_aborted=False,
        jets_returned_to_base=1,
        jets_still_flying=1,
    )
    monkeypatch.setattr(hud_module, "compute_run_summary", lambda *a, **k: fake)

    hud.update(
        mode="finished",
        header=header,
        latest_tick=_tick(99, _live_jets(), running=False),
        camera_mode="orbit",
        selected_jet_id="Jet_1",
        ticks=[],
    )
    assert title in hud.summary_text
    assert "Targets destroyed" in hud.summary_text
    assert not hud._summary_frame.is_hidden()
    assert not hud._summary_node.is_hidden()
    assert hud._summary_title["text"] == title
    assert hud._summary_frame.is_ancestor_of(hud._summary_node)
    assert hud._summary_frame["relief"] is not None
    assert _fill(hud._summary_frame)["frameColor"][3] > 0.5

    hud.update(
        mode="live",
        header=header,
        latest_tick=_tick(100, _live_jets()),
        camera_mode="orbit",
        selected_jet_id="Jet_1",
    )
    assert hud.summary_text == ""
    assert hud._summary_frame.is_hidden()


def test_set_visible_hides_and_restores_the_whole_hud(hud):
    _go_live(hud)
    hud.set_visible(False)
    for panel in (
        hud._mission_frame,
        hud._force_frame,
        hud._coord_frame,
        hud._controls_frame,
        hud._minimap_frame,
        hud._summary_frame,
        hud._debug_frame,
    ):
        assert panel.is_hidden()
    hud.set_visible(True)
    for panel in (hud._mission_frame, hud._force_frame, hud._coord_frame, hud._controls_frame):
        assert not panel.is_hidden()
    # Overlays stay off until they have something to say.
    assert hud._summary_frame.is_hidden()
    assert hud._debug_frame.is_hidden()

    hud.update(
        mode="live",
        header=_header(),
        latest_tick=_tick(3, _live_jets()),
        camera_mode="orbit",
        selected_jet_id="Jet_1",
        presentation_debug="[ MOTION ]  LIVE  backlog=0  ok",
    )
    assert not hud._debug_frame.is_hidden()
    assert hud.presentation_debug_text


# --- palette sourcing ----------------------------------------------------


def test_chrome_colors_come_from_ui_chrome_but_tactical_colors_stay_local():
    source = (ROOT / "viewer3d" / "hud.py").read_text(encoding="utf-8")
    # Chrome tokens must not be redefined here anymore.
    for name in ("PANEL_BG", "MUTED", "WHITE", "GREEN", "RED", "CYAN"):
        assert not re.search(rf"^{name}\s*=", source, re.MULTILINE), name
    assert "from viewer3d.ui_chrome import" in source
    # ...while the deliberately duplicated tactical palette stays local, so
    # viewer3d never has to import viewer/ (Pygame) to agree with the 2D map.
    for name in ("ROLE_RING_COLOR", "JET_PALETTE", "TARGET_DOT", "JAMMER_DOT"):
        assert re.search(rf"^{name}\s*[:=]", source, re.MULTILINE), name
    assert not re.search(r"^\s*(?:from|import)\s+(?:pygame|viewer)\b", source, re.MULTILINE)
