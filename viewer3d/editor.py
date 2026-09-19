"""Scenario editor mode for viewer3d. A top-down orthographic camera (not
the perspective 'Ace Combat' camera used for live/brief modes -- design doc
§8) plus DirectGUI panels for adding/editing/deleting jets, targets, SAM
sites, and jammers, and Save/Load buttons wired to wraithnet.scenario_io.

Precision-first: the sim's own coordinate space is 2D, so an orthographic
top-down view maps 1:1 onto "click here to place this entity" with no
depth ambiguity -- deliberately simpler than the live-view camera, not a
missing feature.

Range fields on TraceHeader (sensor_radius, jam_radius,
sam_engagement_range, strike_range) are not part of ScenarioConfig.
scenario_to_header reads them from wraithnet.config -- the same constants
wraithnet.trace_builders.build_trace_header uses -- rather than expanding
the Phase 14 schema.
"""

from __future__ import annotations

import copy
import math
import secrets
from dataclasses import dataclass
from typing import Literal

from direct.gui.DirectGui import DirectEntry, DirectLabel
from direct.gui.OnscreenText import OnscreenText
from panda3d.core import MouseButton, OrthographicLens, PandaNode, TextNode
from pydantic import ValidationError

from viewer3d.cameras import _camera_node
from viewer3d.scene import build_static_scene, jet_altitude, sync_static_unit_glyphs
from viewer3d.ui_chrome import (
    ACCENT_DIM,
    BORDER_W,
    CONTENT_W,
    CYAN,
    PANEL_PAD,
    RED,
    ROW_H,
    ROW_H_TIGHT,
    SPACE_1,
    SPACE_2,
    SPACE_3,
    TEXT,
    TEXT_MUTED,
    TEXT_SECONDARY,
    TYPE_BODY,
    TYPE_HEADER,
    TYPE_LABEL,
    TYPE_MICRO,
    Stack,
    clamp_scale,
    make_button,
    make_entry,
    make_field_grid,
    make_field_row,
    make_label,
    make_panel,
    make_section,
    panel_content_top,
)
from wraithnet import config as wn_config
from wraithnet.generation_presets import parameters_for_preset
from wraithnet.generation_schemas import (
    FeasibilityReport,
    GenerationParameters,
    GroupStyle,
    GroupTemplateName,
    TerrainGenerationParameters,
)
from wraithnet.naval import (
    DEFAULT_CORVETTE_LOADOUT,
    NAVAL_FRAME_CATALOG,
)
from wraithnet.paths import user_data_dir
from wraithnet.scenario_generator import (
    GenerationError,
    GenerationResult,
    generate_scenario,
)
from wraithnet.scenario_io import load_scenario, save_scenario
from wraithnet.bases import HOME_BASE_ID, default_home_airbase, default_jet_assignment
from wraithnet.objectives import (
    synthesize_legacy_destroy_objective,
    synthesize_legacy_return_objective,
)
from wraithnet.scenario_schema import (
    ScenarioConfig,
    SUPPORTED_SCENARIO_VERSION,
    new_scenario_data,
    scenario_from_data,
)
from wraithnet.tanks import TANK_FRAME_CATALOG
from wraithnet.terrain import OutOfTerrainBoundsError, is_passable
from wraithnet.terrain_generator import TerrainGenerationError, generate_terrain
from wraithnet.terrain_paint import (
    BrushType,
    CityHeightMode,
    apply_paint_to_cells,
    fill_terrain,
    invalid_ground_entities,
    relocate_invalid_entities,
    stroke_cells,
    terrain_from_draft,
)
from wraithnet.trace_builders import catalog_header_fields
from wraithnet.trace_schemas import CarrierFrame, CorvetteFrame, TankFrame, TraceHeader

EditMode = Literal["ENTITY", "TERRAIN"]
_INVALID_ENTITY_COLOR = (0.95, 0.12, 0.12, 1.0)
_BRUSH_CYCLE: tuple[BrushType, ...] = ("WATER", "PLAINS", "FOREST", "CITY")

_VALUE_ERROR_PREFIX = "Value error, "
_FRAME_TYPES = list(wn_config.JET_FRAME_CATALOG)
_CARRIER_FRAME_TYPES = [ft for ft in NAVAL_FRAME_CATALOG if "CARRIER" in ft]
_CORVETTE_FRAME_TYPES = [ft for ft in NAVAL_FRAME_CATALOG if "CORVETTE" in ft]
_TANK_FRAME_TYPES = list(TANK_FRAME_CATALOG)
_DIFFICULTY_CYCLE = ("TRAINING", "STANDARD", "CONTESTED", "NIGHTMARE")
_FRIENDLY_MODE_CYCLE = ("AUTO_BALANCED", "RANDOM_LEGAL", "CUSTOM_ROSTER")
_FRIENDLY_MODE_LABELS = {
    "AUTO_BALANCED": "AUTO-BALANCED",
    "RANDOM_LEGAL": "EXPERIMENTAL RANDOM",
    "CUSTOM_ROSTER": "CUSTOM ROSTER",
}
_GROUP_STYLES: tuple[GroupStyle, ...] = ("DISPERSED", "CLUSTERED", "MIXED")
_GROUP_TEMPLATES: tuple[GroupTemplateName, ...] = (
    "OBJECTIVE_ONLY",
    "DEFENDED_OBJECTIVE",
    "JAMMED_OBJECTIVE",
    "IADS_CLUSTER",
    "SAM_PAIR",
)
# Phase 32 generation is reliably under 100 ms (STANDARD ~2 ms, NIGHTMARE
# tens of ms). Quick Generate therefore calls generate_scenario synchronously
# on the Panda3D thread — no worker queue.
_SYNC_GENERATION_BUDGET_MS = 100


def ordnance_slots_used(payload: dict, frame_type: str) -> tuple[int, int]:
    """Live preview of ScenarioConfig's slot-capacity rule. Does not validate."""
    used = 0
    for weapon, count in (payload or {}).items():
        spec = wn_config.WEAPON_CATALOG.get(weapon)
        if spec is None:
            continue
        used += spec.slot_cost * int(count)
    cap = wn_config.JET_FRAME_CATALOG[frame_type].ordnance_slots
    return used, cap


def slots_usage_text(payload: dict, frame_type: str) -> str:
    used, cap = ordnance_slots_used(payload, frame_type)
    return f"{used} / {cap} slots"


def scenario_to_header(config: ScenarioConfig) -> TraceHeader:
    """Build a TraceHeader from a ScenarioConfig with no sim/socket involved.

    sensor_radius / jam_radius / sam_engagement_range / strike_range are
    taken from wraithnet.config (they are not ScenarioConfig fields).
    SAM sites are merged into the target_* maps, matching
    wraithnet.trace_builders.build_trace_header / WraithNetModel._spawn_scenario.
    """
    targets = list(config.targets) + list(config.sam_sites) + list(
        getattr(config, "aaa_sites", None) or []
    )
    aaa_specs = list(getattr(config, "aaa_sites", None) or [])
    # AaaSiteSpec uses resolved_hp for TargetState; TraceHeader needs int hp.
    target_hp = {}
    for t in list(config.targets) + list(config.sam_sites):
        target_hp[t.target_id] = t.hp_required
    for a in aaa_specs:
        target_hp[a.target_id] = a.resolved_hp()
    return TraceHeader(
        grid_width=config.grid_width,
        grid_height=config.grid_height,
        sensor_radius=wn_config.SENSOR_RADIUS,
        jam_radius=wn_config.JAM_RADIUS,
        sam_engagement_range=wn_config.SAM_ENGAGEMENT_RANGE,
        strike_range=wn_config.WEAPON_CATALOG["JDAM"].range,
        jet_ids=[j.agent_id for j in config.jets],
        initial_payload={j.agent_id: dict(j.payload) for j in config.jets},
        target_ids=[t.target_id for t in targets],
        target_types={t.target_id: t.target_type for t in targets},
        # Data plumbing (TraceHeader field population), not a re-implementation
        # of ScenarioConfig's hp_required >= 1 validation rule -- see Part A/B.
        target_hp_required=target_hp,
        sam_site_ids=[s.target_id for s in config.sam_sites],
        aaa_site_ids=[a.target_id for a in aaa_specs],
        jammer_positions={j.jammer_id: tuple(j.position) for j in config.jammers},
        target_positions={t.target_id: tuple(t.position) for t in targets},
        home_base=tuple(config.home_base),
        jet_frame_types={j.agent_id: j.frame_type for j in config.jets},
        terrain=config.terrain,
        trace_version=3,
        **catalog_header_fields(),
    )


def _humanize_validation_error(err: dict) -> str:
    """Turn one pydantic ErrorDetails dict into a scenario-builder sentence.

    Does not dump a traceback or a raw ValidationError repr. Phase 14's
    model_validator already writes human sentences; we prefer those (via
    ctx['error']) and otherwise strip pydantic's 'Value error, ' wrapper.
    """
    ctx = err.get("ctx") or {}
    inner = ctx.get("error")
    if isinstance(inner, BaseException) and str(inner).strip():
        msg = str(inner).strip()
    else:
        msg = str(err.get("msg") or "").strip()
        if msg.startswith(_VALUE_ERROR_PREFIX):
            msg = msg[len(_VALUE_ERROR_PREFIX) :].strip()
    loc = err.get("loc") or ()
    loc_bits = [str(part) for part in loc if part not in ("__root__",)]
    if loc_bits and msg and loc_bits[0] not in msg:
        # Field-level type errors ("jets -> Field required") — attach loc.
        if "error" not in str(err.get("type") or ""):
            return f"{' / '.join(loc_bits)}: {msg}"
    return msg or "This scenario is not valid."


def _messages_from_validation(exc: ValidationError) -> list[str]:
    lines: list[str] = []
    for err in exc.errors():
        text = _humanize_validation_error(err)
        for line in text.splitlines():
            line = line.strip()
            if line:
                lines.append(line)
    return lines or ["This scenario is not valid."]


class OrthoEditorCamera:
    """Fixed top-down orthographic projection over the whole grid, panned/
    zoomed with the mouse (scroll to zoom, middle-drag or arrow keys to
    pan). No orbit, no tilt -- this is a 2D placement tool wearing a 3D
    engine, not a 3D placement tool.

    At zoom=1 with the camera centered on the grid, mouse (-1, -1) maps to
    world (0, 0) and (1, 1) maps to (grid_width, grid_height).
    """

    def __init__(self, base, grid_width: float, grid_height: float) -> None:
        self.base = base
        self.grid_width = float(grid_width)
        self.grid_height = float(grid_height)
        self.center_x = self.grid_width / 2.0
        self.center_y = self.grid_height / 2.0
        self.zoom = 1.0
        self._last_mouse = None
        self.apply()

    @property
    def film_width(self) -> float:
        return self.grid_width / self.zoom

    @property
    def film_height(self) -> float:
        return self.grid_height / self.zoom

    def screen_to_world(self, mouse_x: float, mouse_y: float) -> tuple[float, float]:
        """Inverse-projects a screen-space click into a (world_x, world_y)
        point on the z=0 ground plane -- the core operation every 'place an
        entity here' interaction in this phase depends on.

        mouse_x/mouse_y are Panda3D mouseWatcher coordinates in [-1, 1].
        """
        wx = self.center_x + mouse_x * (self.film_width / 2.0)
        wy = self.center_y + mouse_y * (self.film_height / 2.0)
        return (wx, wy)

    def pan(self, dx: float, dy: float) -> None:
        self.center_x += dx
        self.center_y += dy
        self.apply()

    def zoom_by(self, factor: float) -> None:
        self.zoom = max(0.4, min(8.0, self.zoom * factor))
        self.apply()

    def update(self, dt: float) -> None:
        mw = getattr(self.base, "mouseWatcherNode", None)
        if mw is None:
            self._last_mouse = None
            return
        try:
            if not mw.has_mouse():
                self._last_mouse = None
                return
            from panda3d.core import Point2

            mpos = Point2(mw.get_mouse())
            dragging = mw.is_button_down(MouseButton.two()) or mw.is_button_down(
                MouseButton.three()
            )
        except (AttributeError, AssertionError, TypeError):
            self._last_mouse = None
            return
        if dragging and self._last_mouse is not None:
            dx = mpos.get_x() - self._last_mouse.get_x()
            dy = mpos.get_y() - self._last_mouse.get_y()
            self.center_x -= dx * (self.film_width / 2.0)
            self.center_y -= dy * (self.film_height / 2.0)
            self.apply()
        self._last_mouse = mpos if dragging else None

    def apply(self) -> None:
        cam = _camera_node(self.base)
        cam.set_pos(self.center_x, self.center_y, 80.0)
        cam.set_hpr(0, -90, 0)
        lens_np = getattr(self.base, "cam", None)
        if lens_np is None:
            return
        try:
            lens = OrthographicLens()
            lens.set_film_size(self.film_width, self.film_height)
            lens.set_near_far(-1000.0, 1000.0)
            lens_np.node().set_lens(lens)
        except (AttributeError, TypeError):
            return


# =========================================================================
# Panel geometry
#
# Every number below derives from the ui_chrome spacing scale, so the editor
# shares one rhythm with the HUD instead of carrying a hand-tuned `z` per
# widget. Rows come from `Stack`/`ROW_H`; columns come from `_columns()`;
# panel heights are *measured* from the rows that were actually laid out
# (`_set_panel_bottom`) rather than guessed, which is what keeps nested cards
# from spilling past their parent and leaves no empty box behind a short form.
# =========================================================================

#: Gap between a panel's outer border and the screen edge.
_EDGE = SPACE_3
#: Offset from a panel edge to its content: border ring + inner padding.
_INSET = BORDER_W + PANEL_PAD
#: Both editor panels are exactly one `CONTENT_W` column wide.
_PANEL_W = CONTENT_W + 2.0 * _INSET
_PANEL_TOP = -_EDGE
#: Provisional bottom; every panel is shrunk to its content after layout.
_PANEL_DRAFT_BOTTOM = -1.98

# Left panel, in a2dTopLeft space (+x runs right from the left screen edge).
_L_LEFT = _EDGE
_L_RIGHT = _EDGE + _PANEL_W
_L_X = _L_LEFT + _INSET

# Right panel, in a2dTopRight space (x is negative, running left from the edge).
_R_RIGHT = -_EDGE
_R_LEFT = -_EDGE - _PANEL_W
_R_X = _R_LEFT + _INSET

#: Inset of a card nested one step inside a panel, and that card's content.
_NEST_PAD = SPACE_2
_CARD_W = _PANEL_W - 2.0 * _NEST_PAD
_CARD_CONTENT_W = _CARD_W - 2.0 * _INSET

#: Horizontal gutter between layout columns.
_COL_GAP = SPACE_2
#: Ink a row leaves below its baseline; what a panel must clear under its last.
_ROW_SINK = SPACE_2
#: Cap-height fraction: a text block's baseline sits this far below its top.
_TEXT_RISE = 0.72
#: Natural vertical extent of a one-line button plate, in text units. Measured
#: from `make_button`, whose bounds are (-0.230, 0.902) at every scale.
_BTN_BOTTOM = -0.23
_BTN_TOP = 0.90
#: Horizontal padding `make_button` leaves either side of its label.
_BTN_PAD_X = 0.28
#: A DirectEntry plate is ~2.0 text units tall, so any row holding one needs
#: the full `ROW_H` pitch; button/label-only rows fit `ROW_H_TIGHT`.
_ENTRY_PAD_X = 0.33
#: Weapon count fields per row, in the property card and each roster row.
#: Two columns (not three) so catalog names like `AMRAAM`/`GUN_POD` keep a
#: readable label column instead of colliding with the count field.
_PAYLOAD_COLUMNS = 2
_ROSTER_PAYLOAD_ROWS = -(-len(wn_config.WEAPON_CATALOG) // _PAYLOAD_COLUMNS)
#: Label vs. numeric-entry split inside one payload cell. Counts are 1-2
#: digits, so the entry can stay narrow and the name gets the rest.
_PAYLOAD_LABEL_RATIO = 0.62
_PAYLOAD_ENTRY_RATIO = 0.34


def _columns(
    x: float, count: int, *, width: float = CONTENT_W, gap: float = _COL_GAP
) -> tuple[list[float], float]:
    """Left edges and span of `count` equal columns filling `width` from `x`."""
    span = (width - (count - 1) * gap) / max(count, 1)
    return [x + i * (span + gap) for i in range(count)], span


def _entry_chars(width: float, scale: float) -> float:
    """`make_entry(width=...)` value whose plate is `width` units wide."""
    return max(width / clamp_scale(scale) - _ENTRY_PAD_X, 1.0)


def _btn(
    parent,
    text,
    pos,
    command,
    extra_args=None,
    scale=TYPE_BODY,
    style="default",
    width=None,
    align=TextNode.ACenter,
):
    """Editor button on the shared type scale.

    `width` pins the plate to a layout column (in aspect2d units) so button
    grids share real edges, instead of every plate sizing itself to its own
    label -- which is why the two columns of Add-* buttons used to be centred
    differently from each other. `pos` is then the column centre for
    `ACenter` or its left edge for `ALeft`.
    """
    frame_size = None
    if width is not None:
        units = float(width) / clamp_scale(scale)
        if align == TextNode.ALeft:
            frame_size = (-_BTN_PAD_X, units - _BTN_PAD_X, _BTN_BOTTOM, _BTN_TOP)
        else:
            frame_size = (-units / 2.0, units / 2.0, _BTN_BOTTOM, _BTN_TOP)
    return make_button(
        parent,
        text,
        pos,
        command,
        extra_args=extra_args,
        scale=scale,
        style=style,
        align=align,
        frame_size=frame_size,
    )


def _section_at(parent, text: str, stack: Stack, *, width: float = CONTENT_W,
                scale: float = TYPE_BODY):
    """`[ BRACKET ]` header + rule that consumes its own height from `stack`."""
    section = make_section(
        parent, text, pos=(stack.x, 0, stack.z), width=width, scale=scale
    )
    stack.gap(stack.z - section.next_z)
    return section


def _panel_bottom_for(content_bottom: float) -> float:
    """Outer edge `z` of a panel whose content ends at `content_bottom`."""
    return content_bottom - PANEL_PAD - BORDER_W


def _set_panel_bottom(panel, content_bottom: float) -> float:
    """Shrink a `make_panel` result to hug content ending at `content_bottom`.

    `make_panel` fixes `frameSize` at construction time and paints the border
    as the outer frame with an inset fill on top, so both rects have to move
    together. Returns the new outer bottom edge.
    """
    frame = panel["frameSize"]
    if frame is None:
        return 0.0
    left, right, _old, top = (float(v) for v in frame)
    bottom = min(_panel_bottom_for(content_bottom), top - 2.0 * BORDER_W - 1e-4)
    panel["frameSize"] = (left, right, bottom, top)
    fill = getattr(panel, "_chrome_fill", None)
    if fill is not None:
        f_left, f_right, _f_bottom, f_top = (float(v) for v in fill["frameSize"])
        fill["frameSize"] = (f_left, f_right, bottom + BORDER_W, f_top)
    panel._chrome_frame_size = (left, right, bottom, top)  # noqa: SLF001
    return bottom


def _text_block_height(widget, scale: float, *, min_rows: int = 1) -> float:
    """Rendered height of a (possibly word-wrapped) label, in aspect2d units.

    The generation panel's status/details/template lines are data-driven, so
    the rows below them are flowed against the measured height instead of a
    reserved guess that either clips or leaves a hole.
    """
    rows, line = min_rows, 1.15
    try:
        node = widget.component("text0").textNode
        rows = max(int(node.get_num_rows()), min_rows)
        line = float(node.get_line_height()) or 1.15
    except Exception:  # noqa: BLE001 — a fontless build still has to lay out
        pass
    return rows * line * clamp_scale(scale)


def _rect_contains(rect, x: float, z: float, margin: float = SPACE_1) -> bool:
    """Is `(x, z)` inside `rect` (left, right, bottom, top), plus `margin`?"""
    left, right, bottom, top = rect
    return (
        left - margin <= x <= right + margin
        and bottom - margin <= z <= top + margin
    )


#: Generator parameters shown in the Advanced card, in grid order. Labels are
#: spelled out (the old "Fr. tanks" / "Fn tanks" pair was unreadable) and the
#: friendly/enemy split is marked consistently with an (FR)/(EN) suffix.
_ADVANCED_FIELDS: tuple[tuple[str, str], ...] = (
    ("grid_width", "Map width"),
    ("grid_height", "Map height"),
    ("objective_count", "Objectives"),
    ("sam_count", "SAM sites"),
    ("jammer_count", "Jammers"),
    ("aaa_count", "AAA sites"),
    ("friendly_tank_count", "Tanks FR"),
    ("enemy_tank_count", "Tanks EN"),
    ("friendly_carrier_count", "Carrier FR"),
    ("enemy_corvette_count", "Corvette EN"),
    ("objective_hp_min", "HP min"),
    ("objective_hp_max", "HP max"),
    ("friendly_count_min", "Jets min"),
    ("friendly_count_max", "Jets max"),
    ("friendly_reserve_factor", "Reserve"),
    ("threat_overlap", "Overlap"),
    ("base_safety_radius", "Base radius"),
    ("entity_min_separation", "Separation"),
)


@dataclass
class EditorSnapshot:
    """One-level undo capture for Quick Generate."""

    scenario_draft: dict
    path: str
    selected_id: str | None
    selected_kind: str | None
    generation_parameters: GenerationParameters
    generation_report: FeasibilityReport | None
    draft_dirty: bool
    camera_center_x: float
    camera_center_y: float
    camera_zoom: float


class ScenarioEditor:
    def __init__(self, base) -> None:
        self.base = base
        # Draft is plain dicts -- not a live ScenarioConfig -- so incomplete
        # placements (e.g. no jets yet) do not fight Pydantic on every click.
        self.scenario_draft: dict = new_scenario_data(
            grid_width=wn_config.GRID_WIDTH,
            grid_height=wn_config.GRID_HEIGHT,
            jets=[],
            targets=[],
            sam_sites=[],
            jammers=[],
            home_base=list(wn_config.DEFAULT_HOME_BASE),
            generation=None,
        )
        self._normalize_draft_plain_dicts()
        self.camera = OrthoEditorCamera(
            base, self.scenario_draft["grid_width"], self.scenario_draft["grid_height"]
        )
        self._placement_kind: str | None = None
        self._selected: dict | None = None
        self._route_arming: bool = False
        self._handles = None
        self._list_buttons: list = []
        self.banner_messages: list[str] = []
        self._path = str(user_data_dir("scenarios") / "custom.json")
        self._generation_parameters: GenerationParameters = parameters_for_preset(
            "STANDARD", seed=0
        )
        self._generation_report: FeasibilityReport | None = None
        self._generation_undo_snapshot: EditorSnapshot | None = None
        self._generation_in_progress: bool = False
        self._draft_dirty: bool = False
        self._pending_generation_result: GenerationResult | None = None
        self._pending_generation_action: Literal["REPLACE", "USE_ANYWAY"] | None = None
        self._pending_undo_snapshot: EditorSnapshot | None = None
        self._advanced_expanded: bool = False
        self._status_details_expanded: bool = False
        self._roster_rows: list[dict] = []
        self._edit_mode: EditMode = "ENTITY"
        self._terrain_brush: BrushType = "PLAINS"
        self._brush_radius: int = 1
        self._city_height_mode: CityHeightMode = "RANDOM"
        self._fixed_city_height: float = 10.0
        self._terrain_undo_stack: list[dict] = []
        self._terrain_redo_stack: list[dict] = []
        self._stroke_active: bool = False
        self._stroke_samples: list[tuple[float, float]] = []
        self._stroke_before: dict | None = None
        self._stroke_sequence: int = 0
        self._pending_relocate_confirm: bool = False
        self._terrain_gen_expanded: bool = False
        self._terrain_gen_params: TerrainGenerationParameters = TerrainGenerationParameters()
        self._build_panels()
        self.rebuild_scene()
        self._sync_generation_widgets_from_parameters()
        self._refresh_generation_status()
        self._refresh_undo_button()
        self._refresh_edit_mode_ui()

    def _build_panels(self) -> None:
        """Left console: instruction line, mode band, entity list, property
        card, file actions -- one column, one row rhythm, sized to content.

        The band is the only region that changes shape: `[ PLACE ]` in entity
        mode, the nested `[ TERRAIN MODE ]` card in terrain mode. Everything
        under it hangs off `_lower_node` so a taller band slides the lower
        half down in one move instead of overlapping it.
        """
        parent = getattr(self.base, "a2dTopLeft", None) or getattr(
            self.base, "aspect2d", None
        )
        self._panel = make_panel(
            parent,
            frame_size=(_L_LEFT, _L_RIGHT, _PANEL_DRAFT_BOTTOM, _PANEL_TOP),
            title="[ SCENARIO EDITOR ]",
            title_scale=TYPE_HEADER,
        )
        stack = Stack(panel_content_top(self._panel), row_h=ROW_H, x=_L_X)

        # What the next map click will do. One line in both edit modes.
        self._hint = OnscreenText(
            text="Click Add, then click the map to place.",
            parent=self._panel,
            pos=(_L_X, stack.z),
            scale=TYPE_LABEL,
            fg=TEXT_MUTED,
            align=TextNode.ALeft,
            mayChange=True,
            wordwrap=CONTENT_W / TYPE_LABEL,
        )
        stack.gap(ROW_H_TIGHT)

        # --- mode band: [ PLACE ] grid or the nested [ TERRAIN MODE ] card ---
        self._band_top_z = stack.z
        self._place_section = _section_at(self._panel, "[ PLACE ]", stack)
        add_cols, add_col_w = _columns(_L_X, 2)
        placements = (
            ("Add Jet", self.begin_placement, ["jet"]),
            ("Add Target", self.begin_placement, ["target"]),
            ("Add SAM", self.begin_placement, ["sam_site"]),
            ("Add AAA", self.begin_placement, ["aaa_site"]),
            ("Add Jammer", self.begin_placement, ["jammer"]),
            ("Add Tank", self.begin_placement, ["tank"]),
            ("Add Carrier", self.begin_placement, ["carrier"]),
            ("Add Corvette", self.begin_placement, ["corvette"]),
            ("Place Home Base", self.begin_placement, ["home_base"]),
            ("Edit Terrain", self.enter_terrain_mode, None),
        )
        place_buttons = []
        row_z = stack.z
        for index, (text, command, extra) in enumerate(placements):
            col = index % 2
            if col == 0:
                row_z = stack.row()
            place_buttons.append(
                _btn(
                    self._panel,
                    text,
                    (add_cols[col] + add_col_w / 2.0, 0, row_z),
                    command,
                    extra_args=extra,
                    width=add_col_w,
                )
            )
        (
            self._btn_add_jet,
            self._btn_add_target,
            self._btn_add_sam,
            self._btn_add_aaa,
            self._btn_add_jammer,
            self._btn_add_tank,
            self._btn_add_carrier,
            self._btn_add_corvette,
            self._btn_home_base,
            self._btn_edit_terrain,
        ) = place_buttons
        self._place_band_h = self._band_top_z - stack.z

        self._build_terrain_card()

        # --- everything below the band, on one slidable node ----------------
        self._lower_node = self._panel.attach_new_node(PandaNode("editor-lower"))
        low = Stack(self._band_top_z - self._place_band_h, row_h=ROW_H, x=_L_X)
        low.gap(SPACE_3)

        self._entities_section = _section_at(self._lower_node, "[ ENTITIES ]", low)
        self._list_anchor_z = low.z
        self._list_max_visible = 5
        low.gap(self._list_max_visible * ROW_H_TIGHT)
        self._list_overflow_z = low.z
        self._list_overflow_label = make_label(
            self._lower_node,
            "",
            pos=(_L_X, 0, self._list_overflow_z),
            scale=TYPE_LABEL,
            fg=TEXT_MUTED,
        )
        self._list_overflow_label.hide()
        low.gap(ROW_H_TIGHT)

        self._build_property_card(low)
        self._build_file_section(low)

        self._lower_content_bottom_z = low.z + ROW_H - _ROW_SINK
        _set_panel_bottom(self._panel, self._lower_content_bottom_z)

        bl = getattr(self.base, "a2dBottomLeft", None) or parent
        self._banner = OnscreenText(
            text="",
            parent=bl,
            pos=(_L_X, _EDGE + SPACE_2),
            scale=TYPE_LABEL,
            fg=RED,
            align=TextNode.ALeft,
            mayChange=True,
            wordwrap=CONTENT_W / TYPE_LABEL,
        )
        self._banner.hide()
        self._back_btn = _btn(
            getattr(self.base, "a2dTopRight", None) or parent,
            "Back to Editor",
            (_R_X + CONTENT_W / 2.0, 0, panel_content_top(self._panel)),
            self._back_to_editing,
            style="primary",
            width=CONTENT_W / 2.0,
        )
        self._back_btn.hide()
        self._build_generate_panel()

    # --- left panel sections ---------------------------------------------

    def _build_terrain_card(self) -> None:
        """Nested `[ TERRAIN MODE ]` card, occupying the mode band.

        It is built at the band's top edge and shrunk to its own rows, so its
        borders can no longer land on the panel header above it or the
        `[ PROPERTIES ]` card below it.
        """
        card_left = _L_LEFT + _NEST_PAD
        card_right = _L_RIGHT - _NEST_PAD
        self._terrain_panel = make_panel(
            self._panel,
            frame_size=(
                card_left,
                card_right,
                self._band_top_z + _PANEL_DRAFT_BOTTOM,
                self._band_top_z,
            ),
            nested=True,
            title="[ TERRAIN MODE ]",
            title_scale=TYPE_BODY,
        )
        x = card_left + _INSET
        width = _CARD_CONTENT_W
        stack = Stack(panel_content_top(self._terrain_panel), row_h=ROW_H_TIGHT, x=x)
        two, two_w = _columns(x, 2, width=width)
        three, three_w = _columns(x, 3, width=width)

        z = stack.row()
        self._btn_brush = _btn(
            self._terrain_panel,
            "Brush: PLAINS",
            (two[0] + two_w / 2.0, 0, z),
            self._cycle_terrain_brush,
            scale=TYPE_LABEL,
            width=two_w,
        )
        self._btn_radius = _btn(
            self._terrain_panel,
            "Radius: 1",
            (two[1] + two_w / 2.0, 0, z),
            self._cycle_brush_radius,
            scale=TYPE_LABEL,
            width=two_w,
        )

        z = stack.row()
        self._btn_city_height = _btn(
            self._terrain_panel,
            "City Ht: Random",
            (two[0] + two_w / 2.0, 0, z),
            self._cycle_city_height_mode,
            scale=TYPE_LABEL,
            width=two_w,
        )
        self._terrain_height_label, self._fixed_height_entry = make_field_row(
            self._terrain_panel,
            "Height",
            pos=(two[1], 0, z),
            width=two_w,
            scale=TYPE_LABEL,
            label_ratio=0.46,
            entry_chars=_entry_chars(two_w * 0.52, TYPE_LABEL),
            initial="10",
        )
        stack.gap(ROW_H - ROW_H_TIGHT)  # an entry plate is taller than a button

        z = stack.row()
        self._btn_fill = _btn(
            self._terrain_panel,
            "Fill Map",
            (two[0] + two_w / 2.0, 0, z),
            self._fill_terrain_clicked,
            scale=TYPE_LABEL,
            width=two_w,
        )
        self._btn_gen_terrain = _btn(
            self._terrain_panel,
            "Generate Terrain",
            (two[1] + two_w / 2.0, 0, z),
            self._toggle_terrain_gen_panel,
            scale=TYPE_LABEL,
            width=two_w,
        )

        z = stack.row()
        self._btn_terrain_undo = _btn(
            self._terrain_panel,
            "Undo",
            (three[0] + three_w / 2.0, 0, z),
            self._terrain_undo,
            scale=TYPE_LABEL,
            width=three_w,
        )
        self._btn_terrain_redo = _btn(
            self._terrain_panel,
            "Redo",
            (three[1] + three_w / 2.0, 0, z),
            self._terrain_redo,
            scale=TYPE_LABEL,
            width=three_w,
        )
        self._btn_entity_mode = _btn(
            self._terrain_panel,
            "Entity Mode",
            (three[2] + three_w / 2.0, 0, z),
            self.enter_entity_mode,
            scale=TYPE_LABEL,
            width=three_w,
        )

        z = stack.row()
        self._btn_relocate = _btn(
            self._terrain_panel,
            "Relocate Invalid",
            (two[0] + two_w / 2.0, 0, z),
            self._relocate_invalid_clicked,
            scale=TYPE_LABEL,
            width=two_w,
        )
        self._btn_relocate_confirm = _btn(
            self._terrain_panel,
            "Confirm Relocate",
            (two[1] + two_w / 2.0, 0, z),
            self._confirm_relocate,
            scale=TYPE_LABEL,
            style="primary",
            width=two_w,
        )
        self._btn_relocate_confirm.hide()
        self._terrain_content_bottom_z = z - _ROW_SINK

        # Nested one step further: the terrain generator's own fractions.
        gen_left = card_left + _NEST_PAD
        gen_right = card_right - _NEST_PAD
        gen_top = self._terrain_content_bottom_z - SPACE_2
        self._terrain_gen_frame = make_panel(
            self._terrain_panel,
            frame_size=(gen_left, gen_right, gen_top + _PANEL_DRAFT_BOTTOM, gen_top),
            nested=True,
            title="[ GENERATE TERRAIN ]",
            title_scale=TYPE_LABEL,
        )
        gen_x = gen_left + _INSET
        gen_w = (gen_right - gen_left) - 2.0 * _INSET
        gen_stack = Stack(panel_content_top(self._terrain_gen_frame), row_h=ROW_H, x=gen_x)
        frac_cols, frac_w = _columns(gen_x, 3, width=gen_w)
        z = gen_stack.row()
        fraction_fields = (
            ("Water", "0.20"),
            ("Forest", "0.20"),
            ("City", "0.10"),
        )
        fraction_entries = []
        for col, (label, initial) in enumerate(fraction_fields):
            _label, entry = make_field_row(
                self._terrain_gen_frame,
                label,
                pos=(frac_cols[col], 0, z),
                width=frac_w,
                scale=TYPE_MICRO,
                label_ratio=0.46,
                entry_chars=_entry_chars(frac_w * 0.52, TYPE_MICRO),
                initial=initial,
            )
            fraction_entries.append(entry)
        (
            self._terrain_water_entry,
            self._terrain_forest_entry,
            self._terrain_city_entry,
        ) = fraction_entries
        z = gen_stack.row()
        self._btn_run_terrain_gen = _btn(
            self._terrain_gen_frame,
            "Run Generate",
            (gen_x + gen_w / 2.0, 0, z),
            self._generate_terrain_clicked,
            scale=TYPE_LABEL,
            style="primary",
            width=gen_w / 2.0,
        )
        gen_bottom = _set_panel_bottom(self._terrain_gen_frame, z - _ROW_SINK)
        self._terrain_gen_frame.hide()

        self._terrain_expanded_bottom_z = gen_bottom - SPACE_1
        _set_panel_bottom(self._terrain_panel, self._terrain_content_bottom_z)
        self._terrain_panel.hide()

    def _build_property_card(self, low: Stack) -> None:
        """Nested `[ PROPERTIES ]` card.

        ID / generic / ordnance rows are static -- every entity kind uses the
        same three label+entry rows -- so only the frame/slots and action rows
        are re-flowed per kind by `_layout_property_form`, which then hugs the
        card to whatever it actually showed.
        """
        low.gap(SPACE_2)
        self._form_top_z = low.z
        self._form_frame = make_panel(
            self._lower_node,
            frame_size=(
                _L_LEFT + _NEST_PAD,
                _L_RIGHT - _NEST_PAD,
                self._form_top_z + _PANEL_DRAFT_BOTTOM,
                self._form_top_z,
            ),
            nested=True,
            title="[ PROPERTIES ]",
            title_scale=TYPE_BODY,
        )
        self._form_x = _L_LEFT + _NEST_PAD + _INSET
        self._form_w = _CARD_CONTENT_W
        stack = Stack(panel_content_top(self._form_frame), row_h=ROW_H, x=self._form_x)
        self._form_content_top_z = stack.z

        label_ratio = 0.32
        value_x = self._form_x + self._form_w * label_ratio
        value_chars = _entry_chars(self._form_w * (1.0 - label_ratio), TYPE_LABEL)

        z = stack.row()
        self._id_label = make_label(
            self._form_frame, "ID", pos=(self._form_x, 0, z), scale=TYPE_LABEL, fg=CYAN
        )
        self._id_entry = make_entry(
            self._form_frame, pos=(value_x, 0, z), scale=TYPE_LABEL, width=value_chars
        )
        z = stack.row()
        self._field_a_label = make_label(
            self._form_frame, "JDAM", pos=(self._form_x, 0, z), scale=TYPE_LABEL, fg=CYAN
        )
        self._field_a_entry = make_entry(
            self._form_frame, pos=(value_x, 0, z), scale=TYPE_LABEL, width=value_chars
        )
        z = stack.row()
        self._field_b_label = make_label(
            self._form_frame, "AIM9X", pos=(self._form_x, 0, z), scale=TYPE_LABEL, fg=CYAN
        )
        self._field_b_entry = make_entry(
            self._form_frame, pos=(value_x, 0, z), scale=TYPE_LABEL, width=value_chars
        )
        self._form_tail_generic_z = stack.z

        self._ordnance_section = _section_at(
            self._form_frame,
            "[ ORDNANCE ]",
            stack,
            width=self._form_w,
            scale=TYPE_LABEL,
        )
        weapons = list(wn_config.WEAPON_CATALOG)
        pay_cols, pay_w = _columns(self._form_x, _PAYLOAD_COLUMNS, width=self._form_w)
        pay_chars = _entry_chars(pay_w * _PAYLOAD_ENTRY_RATIO, TYPE_MICRO)
        self._payload_entries: dict[str, DirectEntry] = {}
        self._payload_labels: dict[str, DirectLabel] = {}
        z = stack.z
        for index, weapon in enumerate(weapons):
            col = index % _PAYLOAD_COLUMNS
            if col == 0:
                z = stack.row()
            label, entry = make_field_row(
                self._form_frame,
                weapon,
                pos=(pay_cols[col], 0, z),
                width=pay_w,
                scale=TYPE_MICRO,
                label_ratio=_PAYLOAD_LABEL_RATIO,
                entry_chars=pay_chars,
                initial="0",
                command=self._on_payload_typed,
            )
            label.hide()
            entry.hide()
            self._payload_labels[weapon] = label
            self._payload_entries[weapon] = entry
        self._form_tail_jet_z = stack.z

        # Kind-dependent tail. Positions are assigned by
        # `_layout_property_form`; the jet form is the tallest, so its two
        # tail rows set how much room the card can ever need.
        self._frame_btn = _btn(
            self._form_frame,
            "Frame: STRIKE_FIGHTER",
            (self._form_x, 0, self._form_tail_jet_z),
            self._cycle_frame_type,
            scale=TYPE_LABEL,
            width=self._form_w * 0.70,
            align=TextNode.ALeft,
        )
        self._slots_label = make_label(
            self._form_frame,
            "0 / 7 slots",
            pos=(self._form_x + self._form_w, 0, self._form_tail_jet_z),
            scale=TYPE_LABEL,
            fg=TEXT,
            align=TextNode.ARight,
        )
        action_cols, action_w = _columns(self._form_x, 2, width=self._form_w)
        self._btn_apply = _btn(
            self._form_frame,
            "Apply",
            (action_cols[0] + action_w / 2.0, 0, self._form_tail_jet_z),
            self._apply_form,
            scale=TYPE_LABEL,
            style="primary",
            width=action_w,
        )
        self._btn_delete = _btn(
            self._form_frame,
            "Delete",
            (action_cols[1] + action_w / 2.0, 0, self._form_tail_jet_z),
            self._delete_selected,
            scale=TYPE_LABEL,
            style="danger",
            width=action_w,
        )
        self._btn_hold = _btn(
            self._form_frame,
            "Hold: ON",
            (action_cols[0] + action_w / 2.0, 0, self._form_tail_jet_z),
            self._toggle_hold_position,
            scale=TYPE_LABEL,
            width=action_w,
        )
        self._btn_add_route = _btn(
            self._form_frame,
            "Add Route Point",
            (action_cols[1] + action_w / 2.0, 0, self._form_tail_jet_z),
            self._arm_route_point,
            scale=TYPE_LABEL,
            width=action_w,
        )
        self._field_a_entry["command"] = self._on_payload_typed
        self._field_b_entry["command"] = self._on_payload_typed
        self._frame_btn.hide()
        self._slots_label.hide()
        self._btn_hold.hide()
        self._btn_add_route.hide()
        self._show_generic_fields(False)
        self._show_payload_fields(False)

        # Reserve the card's worst case so the file actions underneath never
        # move when the form changes. A carrier/corvette is the tallest tail:
        # frame/slots row + hold/route row + Apply/Delete.
        self._form_max_bottom_z = _panel_bottom_for(
            self._form_tail_jet_z - 3 * ROW_H - _ROW_SINK
        )
        low.gap(self._form_top_z - self._form_max_bottom_z)
        self._layout_property_form("jammer")

    def _build_file_section(self, low: Stack) -> None:
        low.gap(SPACE_2)
        self._file_section = _section_at(self._lower_node, "[ SCENARIO FILE ]", low)
        path_ratio = 0.14
        path_x = _L_X + CONTENT_W * path_ratio
        z = low.row()
        self._path_label = make_label(
            self._lower_node, "Path", pos=(_L_X, 0, z), scale=TYPE_LABEL, fg=CYAN
        )
        self._path_entry = make_entry(
            self._lower_node,
            pos=(path_x, 0, z),
            scale=TYPE_MICRO,
            width=_entry_chars(CONTENT_W * (1.0 - path_ratio), TYPE_MICRO),
            initial=self._path,
            overflow=True,
        )
        file_cols, file_w = _columns(_L_X, 3)
        z = low.row()
        self._btn_save = _btn(
            self._lower_node,
            "Save",
            (file_cols[0] + file_w / 2.0, 0, z),
            self._save_clicked,
            width=file_w,
        )
        self._btn_load = _btn(
            self._lower_node,
            "Load",
            (file_cols[1] + file_w / 2.0, 0, z),
            self._load_clicked,
            width=file_w,
        )
        self._btn_preview = _btn(
            self._lower_node,
            "Preview",
            (file_cols[2] + file_w / 2.0, 0, z),
            self._preview_clicked,
            width=file_w,
        )
        z = low.row()
        self._btn_launch = _btn(
            self._lower_node,
            "Launch",
            (_L_X + CONTENT_W / 2.0, 0, z),
            self._launch_clicked,
            style="primary",
            width=CONTENT_W,
        )

    def _build_generate_panel(self) -> None:
        """Right-side Quick Generate console.

        Seed / actions / status stack on the panel's own rhythm; the Advanced
        and Custom Roster cards are expandable and are flowed under the status
        text by `_relayout_generation_panel`, which also shrinks the panel to
        fit. Collapsed, this panel is now a third of its old height.
        """
        parent = getattr(self.base, "a2dTopRight", None) or getattr(
            self.base, "aspect2d", None
        )
        self._gen_panel = make_panel(
            parent,
            frame_size=(_R_LEFT, _R_RIGHT, _PANEL_DRAFT_BOTTOM, _PANEL_TOP),
            title="[ QUICK GENERATE ]",
            title_scale=TYPE_HEADER,
        )
        stack = Stack(panel_content_top(self._gen_panel), row_h=ROW_H, x=_R_X)
        two, two_w = _columns(_R_X, 2)
        three, three_w = _columns(_R_X, 3)

        self._seed_section = _section_at(self._gen_panel, "[ MISSION SEED ]", stack)
        z = stack.row()
        self._btn_difficulty = _btn(
            self._gen_panel,
            "Difficulty: STANDARD",
            (_R_X + CONTENT_W / 2.0, 0, z),
            self._cycle_difficulty,
            width=CONTENT_W,
        )
        z = stack.row()
        seed_label_w = CONTENT_W * 0.14
        seed_btn_w = CONTENT_W * 0.28
        seed_entry_w = CONTENT_W - seed_label_w - seed_btn_w - _COL_GAP
        self._seed_label = make_label(
            self._gen_panel, "Seed", pos=(_R_X, 0, z), scale=TYPE_LABEL, fg=CYAN
        )
        self._seed_entry = make_entry(
            self._gen_panel,
            pos=(_R_X + seed_label_w, 0, z),
            scale=TYPE_LABEL,
            width=_entry_chars(seed_entry_w, TYPE_LABEL),
            initial="0",
        )
        self._btn_new_seed = _btn(
            self._gen_panel,
            "New Seed",
            (_R_X + CONTENT_W - seed_btn_w / 2.0, 0, z),
            self._new_seed,
            scale=TYPE_LABEL,
            width=seed_btn_w,
        )
        z = stack.row()
        self._btn_friendly_mode = _btn(
            self._gen_panel,
            "AUTO-BALANCED",
            (_R_X + CONTENT_W / 2.0, 0, z),
            self._cycle_friendly_mode,
            width=CONTENT_W,
        )

        stack.gap(SPACE_2)
        self._generate_section = _section_at(self._gen_panel, "[ GENERATE ]", stack)
        z = stack.row()
        self._btn_generate = _btn(
            self._gen_panel,
            "Generate",
            (two[0] + two_w / 2.0, 0, z),
            self._generate_clicked,
            style="primary",
            width=two_w,
        )
        self._btn_advanced = _btn(
            self._gen_panel,
            "Advanced...",
            (two[1] + two_w / 2.0, 0, z),
            self._toggle_advanced,
            width=two_w,
        )
        z = stack.row()
        self._btn_undo_generation = _btn(
            self._gen_panel,
            "Undo Generation",
            (_R_X + CONTENT_W / 2.0, 0, z),
            self._undo_generation,
            scale=TYPE_LABEL,
            width=CONTENT_W,
        )
        z = stack.row()
        self._btn_confirm_replace = _btn(
            self._gen_panel,
            "Replace",
            (three[0] + three_w / 2.0, 0, z),
            self._confirm_replace_generation,
            scale=TYPE_LABEL,
            style="primary",
            width=three_w,
        )
        self._btn_use_anyway = _btn(
            self._gen_panel,
            "Use Anyway",
            (three[1] + three_w / 2.0, 0, z),
            self._confirm_use_anyway,
            scale=TYPE_LABEL,
            width=three_w,
        )
        self._btn_cancel_pending = _btn(
            self._gen_panel,
            "Cancel",
            (three[2] + three_w / 2.0, 0, z),
            self._cancel_pending_generation,
            scale=TYPE_LABEL,
            style="danger",
            width=three_w,
        )
        self._btn_confirm_replace.hide()
        self._btn_use_anyway.hide()
        self._btn_cancel_pending.hide()

        stack.gap(SPACE_2)
        self._status_section = _section_at(self._gen_panel, "[ STATUS ]", stack)
        z = stack.row()
        self._gen_meta_label = make_label(
            self._gen_panel,
            "Manual scenario",
            pos=(_R_X, 0, z),
            scale=TYPE_LABEL,
            fg=TEXT,
            wordwrap=CONTENT_W / TYPE_LABEL,
        )
        # Flow region: status text length is data-driven, so the rows under it
        # are measured at refresh time rather than reserved here.
        self._gen_flow_top_z = stack.z + _TEXT_RISE * TYPE_MICRO
        self._gen_status_label = make_label(
            self._gen_panel,
            "No generation yet.",
            pos=(_R_X, 0, stack.z),
            scale=TYPE_MICRO,
            fg=TEXT_SECONDARY,
            wordwrap=CONTENT_W / TYPE_MICRO,
        )
        self._details_btn_w = CONTENT_W * 0.28
        self._btn_status_details = _btn(
            self._gen_panel,
            "Details",
            (_R_X + CONTENT_W - self._details_btn_w / 2.0, 0, stack.z),
            self._toggle_status_details,
            scale=TYPE_LABEL,
            width=self._details_btn_w,
        )
        self._gen_details_label = make_label(
            self._gen_panel,
            "",
            pos=(_R_X, 0, stack.z),
            scale=TYPE_MICRO,
            fg=TEXT_SECONDARY,
            wordwrap=CONTENT_W / TYPE_MICRO,
        )
        self._gen_details_label.hide()

        self._build_advanced_card()
        self._build_roster_card()
        self._refresh_undo_button()
        self._refresh_pending_buttons()
        self._relayout_generation_panel()

    # --- right panel expandable cards ------------------------------------

    def _build_advanced_card(self) -> None:
        """Nested `[ ADVANCED ]` card: the 16 generator parameters as one grid.

        Built against a local origin of `z = 0` so `_relayout_generation_panel`
        can slot it under whatever the status text currently occupies with a
        single `set_z`.
        """
        card_left = _R_LEFT + _NEST_PAD
        card_right = _R_RIGHT - _NEST_PAD
        self._advanced_frame = make_panel(
            self._gen_panel,
            frame_size=(card_left, card_right, _PANEL_DRAFT_BOTTOM, 0.0),
            pos=(0, 0, self._gen_flow_top_z),
            nested=True,
            title="[ ADVANCED ]",
            title_scale=TYPE_LABEL,
        )
        x = card_left + _INSET
        width = _CARD_CONTENT_W
        stack = Stack(panel_content_top(self._advanced_frame), row_h=ROW_H, x=x)
        self._advanced_grid = make_field_grid(
            self._advanced_frame,
            _ADVANCED_FIELDS,
            pos=(x, 0, stack.z),
            width=width,
            columns=2,
            row_h=ROW_H,
            scale=TYPE_MICRO,
            label_ratio=0.60,
            entry_chars=4.0,
            gap=_COL_GAP,
            command=self._on_advanced_field_edited,
        )
        # Contract kept for callers/tests: a plain dict keyed by parameter name.
        self._advanced_entries: dict[str, DirectEntry] = self._advanced_grid.entries
        stack.gap(self._advanced_grid.height)

        two, two_w = _columns(x, 2, width=width)
        z = stack.row()
        self._btn_group_style = _btn(
            self._advanced_frame,
            "Style: MIXED",
            (two[0] + two_w / 2.0, 0, z),
            self._cycle_group_style,
            scale=TYPE_LABEL,
            width=two_w,
        )
        self._btn_cycle_templates = _btn(
            self._advanced_frame,
            "Cycle templates",
            (two[1] + two_w / 2.0, 0, z),
            self._cycle_allowed_templates,
            scale=TYPE_LABEL,
            width=two_w,
        )
        self._advanced_flow_top_z = stack.z + _TEXT_RISE * TYPE_MICRO
        self._templates_label = make_label(
            self._advanced_frame,
            "Templates: ...",
            pos=(x, 0, stack.z),
            scale=TYPE_MICRO,
            fg=TEXT_SECONDARY,
            wordwrap=width / TYPE_MICRO,
        )
        self._param_summary_label = make_label(
            self._advanced_frame,
            "",
            pos=(x, 0, stack.z),
            scale=TYPE_MICRO,
            fg=ACCENT_DIM,
            wordwrap=width / TYPE_MICRO,
        )
        self._advanced_frame.hide()

    def _build_roster_card(self) -> None:
        """Nested `[ CUSTOM ROSTER ]` card; rows are added/removed at runtime."""
        card_left = _R_LEFT + _NEST_PAD
        card_right = _R_RIGHT - _NEST_PAD
        self._roster_frame = make_panel(
            self._gen_panel,
            frame_size=(card_left, card_right, _PANEL_DRAFT_BOTTOM, 0.0),
            pos=(0, 0, self._gen_flow_top_z),
            nested=True,
            title="[ CUSTOM ROSTER ]",
            title_scale=TYPE_LABEL,
        )
        self._roster_x = card_left + _INSET
        self._roster_w = _CARD_CONTENT_W
        stack = Stack(
            panel_content_top(self._roster_frame), row_h=ROW_H, x=self._roster_x
        )
        z = stack.row()
        self._btn_add_roster_row = _btn(
            self._roster_frame,
            "Add roster row",
            (self._roster_x + self._roster_w / 2.0, 0, z),
            self._add_roster_row,
            scale=TYPE_LABEL,
            width=self._roster_w * 0.6,
        )
        self._roster_anchor_z = stack.z
        #: One roster row: count/frame/remove, the payload grid, a slots line.
        self._roster_row_h = ROW_H + _ROSTER_PAYLOAD_ROWS * ROW_H + ROW_H_TIGHT + SPACE_2
        self._roster_frame.hide()
        self._resize_roster_frame()

    def hide_panels(self) -> None:
        self._panel.hide()
        self._gen_panel.hide()
        self._hint.hide()
        self._banner.hide()
        self._back_btn.show()

    def show_panels(self) -> None:
        self._panel.show()
        self._gen_panel.show()
        self._hint.show()
        if self.banner_messages:
            self._banner.show()
        self._back_btn.hide()

    def _back_to_editing(self) -> None:
        cancel = getattr(self.base, "cancel_launch", None)
        if cancel is not None:
            cancel()
            return
        exit_preview = getattr(self.base, "exit_preview", None)
        if exit_preview is not None:
            exit_preview()

    def _save_clicked(self) -> None:
        self.save(self._path_entry.get().strip() or self._path)

    def _load_clicked(self) -> None:
        path = self._path_entry.get().strip() or self._path
        try:
            self.load(path)
        except (OSError, ValidationError, ValueError) as exc:
            self._set_banner([f"Could not load '{path}': {exc}"])

    def _preview_clicked(self) -> None:
        self.preview()

    def _launch_clicked(self) -> None:
        launch = getattr(self.base, "on_launch_clicked", None)
        if launch is not None:
            launch()

    def current_path(self) -> str:
        return self._path_entry.get().strip() or self._path

    def begin_placement(self, entity_kind: str) -> None:
        """Arm placement mode: the next ground-plane click places this kind."""
        if self._edit_mode != "ENTITY":
            return
        self._route_arming = False
        self._placement_kind = entity_kind
        self._hint.setText(f"Click the map to place a {entity_kind.replace('_', ' ')}.")

    def cancel_placement(self) -> None:
        self._placement_kind = None
        self._route_arming = False
        if self._edit_mode == "TERRAIN":
            self._hint.setText("Drag on the map to paint terrain. Esc cancels stroke.")
        else:
            self._hint.setText("Click Add, then click the map to place.")

    def on_ground_clicked(self, world_pos: tuple[float, float]) -> None:
        """Place a new entity, append a route point, or select the nearest one."""
        if self._edit_mode == "TERRAIN":
            return
        if self._route_arming and self._selected is not None:
            self._append_route_or_waypoint(world_pos)
            return
        if self._placement_kind is not None:
            placed = self._place(self._placement_kind, world_pos)
            if placed:
                self._placement_kind = None
                self._hint.setText("Click Add, then click the map to place.")
            return
        nearest = self._nearest(world_pos)
        if nearest is not None:
            self.open_property_form(nearest)

    def _domain_ok_for_place(
        self, kind: str, pos: tuple[float, float]
    ) -> bool:
        terrain = self._draft_terrain()
        try:
            if kind in ("carrier", "corvette"):
                if not is_passable(terrain, pos, "SURFACE"):
                    label = "Carrier" if kind == "carrier" else "Corvette"
                    self._set_banner(
                        [f"{label} must be placed on WATER (SURFACE navigable)."]
                    )
                    return False
            elif kind == "tank":
                if not is_passable(terrain, pos, "GROUND"):
                    self._set_banner(["Tank must be placed on GROUND (not water)."])
                    return False
            elif kind == "aaa_site":
                if not is_passable(terrain, pos, "GROUND"):
                    self._set_banner(["AAA must be placed on GROUND (not water)."])
                    return False
        except OutOfTerrainBoundsError:
            self._set_banner(["Position is outside terrain bounds."])
            return False
        return True

    def _place(self, kind: str, world_pos: tuple[float, float]) -> bool:
        pos = (float(world_pos[0]), float(world_pos[1]))
        if kind in ("carrier", "corvette", "tank", "aaa_site"):
            if not self._domain_ok_for_place(kind, pos):
                return False
        if kind == "jet":
            ent = {
                "agent_id": self._next_id("Jet_", "agent_id", "jets"),
                "position": pos,
                "payload": {"JDAM": 1, "AIM9X": 0},
                "frame_type": "STRIKE_FIGHTER",
                "base_assignment": default_jet_assignment().model_dump(),
            }
            self.scenario_draft["jets"].append(ent)
        elif kind == "home_base":
            self.scenario_draft["home_base"] = [pos[0], pos[1]]
            self._sync_v3_derived_fields(self.scenario_draft)
            self._mark_manual_edit()
            self.rebuild_scene()
            self._refresh_entity_list()
            _, errors = self._construct_config()
            self._set_banner(errors or [])
            return True
        elif kind == "target":
            ent = {
                "target_id": self._next_id("Target_", "target_id", "targets"),
                "position": pos,
                "hp_required": 1,
                "target_type": "HARDENED_BUNKER",
            }
            self.scenario_draft["targets"].append(ent)
        elif kind == "sam_site":
            ent = {
                "target_id": self._next_id("SAM_Site_", "target_id", "sam_sites"),
                "position": pos,
                "hp_required": 1,
                "target_type": "SAM_SITE",
            }
            self.scenario_draft["sam_sites"].append(ent)
        elif kind == "aaa_site":
            ent = {
                "target_id": self._next_id("AAA_Site_", "target_id", "aaa_sites"),
                "position": pos,
                "heading": 0.0,
                "faction": "ENEMY",
                "frame_type": "AAA_LIGHT",
                "hp_required": 2,
                "ammo": 48,
                "target_type": "AAA_SITE",
            }
            self.scenario_draft.setdefault("aaa_sites", []).append(ent)
        elif kind == "tank":
            ent = {
                "unit_id": self._next_id("Tank_", "unit_id", "tanks"),
                "position": pos,
                "heading": 0.0,
                "faction": "ENEMY",
                "frame_type": "MBT_STANDARD",
                "order": "HOLD",
                "ammo": 24,
                "waypoints": [],
            }
            self.scenario_draft.setdefault("tanks", []).append(ent)
        elif kind == "carrier":
            ent = {
                "unit_id": self._next_id("Carrier_", "unit_id", "carriers"),
                "faction": "FRIENDLY",
                "position": pos,
                "heading": 0.0,
                "frame_type": "ESCORT_CARRIER",
                "route": [],
                "hold_position": True,
            }
            self.scenario_draft.setdefault("carriers", []).append(ent)
        elif kind == "corvette":
            ent = {
                "unit_id": self._next_id("Corvette_", "unit_id", "corvettes"),
                "faction": "ENEMY",
                "position": pos,
                "heading": 0.0,
                "frame_type": "CORVETTE_STANDARD",
                "route": [],
                "hold_position": True,
                "loadout": dict(DEFAULT_CORVETTE_LOADOUT),
            }
            self.scenario_draft.setdefault("corvettes", []).append(ent)
        elif kind == "jammer":
            ent = {
                "jammer_id": self._next_id("EW_Jammer_", "jammer_id", "jammers"),
                "position": pos,
            }
            self.scenario_draft["jammers"].append(ent)
        else:
            return False
        self._mark_manual_edit()
        self.rebuild_scene()
        self._refresh_entity_list()
        self.open_property_form(ent)
        self._set_banner([])
        return True

    def _next_id(self, prefix: str, id_key: str, bucket: str) -> str:
        existing = {item[id_key] for item in self.scenario_draft.get(bucket) or []}
        n = 1
        while f"{prefix}{n}" in existing:
            n += 1
        return f"{prefix}{n}"

    def _all_entities(self) -> list[tuple[str, dict]]:
        out: list[tuple[str, dict]] = []
        for kind, bucket in (
            ("jet", "jets"),
            ("target", "targets"),
            ("sam_site", "sam_sites"),
            ("aaa_site", "aaa_sites"),
            ("tank", "tanks"),
            ("carrier", "carriers"),
            ("corvette", "corvettes"),
            ("jammer", "jammers"),
        ):
            for ent in self.scenario_draft.get(bucket) or []:
                out.append((kind, ent))
        return out

    def _id_of(self, kind: str, ent: dict) -> str:
        if kind == "jet":
            return ent["agent_id"]
        if kind == "jammer":
            return ent["jammer_id"]
        if kind in ("tank", "carrier", "corvette"):
            return ent["unit_id"]
        return ent["target_id"]

    def _nearest(self, world_pos: tuple[float, float], max_dist: float = 5.0):
        best = None
        best_d = max_dist
        for kind, ent in self._all_entities():
            x, y = ent["position"]
            d = math.hypot(x - world_pos[0], y - world_pos[1])
            if d < best_d:
                best_d = d
                best = ent
        return best

    def open_property_form(self, entity) -> None:
        """DirectGUI form for editing one entity's fields in place."""
        self._selected = entity
        self._route_arming = False
        kind = self._kind_of(entity)
        self._id_entry.enterText(self._id_of(kind, entity))
        self._btn_hold.hide()
        self._btn_add_route.hide()
        if kind == "jet":
            self._show_payload_fields(True)
            self._show_generic_fields(True)
            payload = entity.get("payload") or {}
            for weapon, entry in self._payload_entries.items():
                entry.enterText(str(int(payload.get(weapon, 0))))
            self._field_a_label["text"] = "flares"
            self._field_b_label["text"] = "flare_cap"
            self._field_a_entry.enterText(str(entity.get("flares_remaining", 6)))
            self._field_b_entry.enterText(str(entity.get("flare_capacity", 6)))
            self._field_a_entry.show()
            self._field_b_entry.show()
            self._field_b_label.show()
            self._frame_btn.show()
            self._slots_label.show()
            self._set_frame_button_label(entity.get("frame_type", "STRIKE_FIGHTER"))
            self._refresh_slots_preview()
        elif kind == "target":
            self._show_payload_fields(False)
            self._show_generic_fields(True)
            self._field_a_label["text"] = "type"
            self._field_b_label["text"] = "hp"
            self._field_a_entry.enterText(str(entity.get("target_type", "HARDENED_BUNKER")))
            self._field_b_entry.enterText(str(entity.get("hp_required", 1)))
            self._field_a_entry.show()
            self._field_b_entry.show()
            self._frame_btn.hide()
            self._slots_label.hide()
        elif kind == "sam_site":
            self._show_payload_fields(False)
            self._show_generic_fields(True)
            self._field_a_label["text"] = "hp"
            self._field_b_label["text"] = "IR/RADAR"
            self._field_a_entry.enterText(str(entity.get("hp_required", 1)))
            loadout = entity.get("missile_loadout") or {}
            mix = "IR" if int(loadout.get("SAM_IR", 0) or 0) > 0 else "RADAR"
            self._field_b_entry.enterText(mix)
            self._field_a_entry.show()
            self._field_b_entry.show()
            self._field_b_label.show()
            self._frame_btn.hide()
            self._slots_label.hide()
        elif kind == "aaa_site":
            self._show_payload_fields(False)
            self._show_generic_fields(True)
            self._field_a_label["text"] = "hp"
            self._field_b_label["text"] = "ammo"
            self._field_a_entry.enterText(str(entity.get("hp_required", 2)))
            self._field_b_entry.enterText(str(entity.get("ammo", 48)))
            self._field_a_entry.show()
            self._field_b_entry.show()
            self._frame_btn.hide()
            self._slots_label.hide()
        elif kind == "tank":
            self._show_payload_fields(False)
            self._show_generic_fields(True)
            self._field_a_label["text"] = "faction"
            self._field_b_label["text"] = "order"
            self._field_a_entry.enterText(str(entity.get("faction", "ENEMY")))
            self._field_b_entry.enterText(str(entity.get("order", "HOLD")))
            self._field_a_entry.show()
            self._field_b_entry.show()
            self._frame_btn.show()
            self._slots_label.hide()
            self._set_frame_button_label(entity.get("frame_type", "MBT_STANDARD"))
            order = str(entity.get("order", "HOLD")).upper()
            if order in ("PATROL", "ADVANCE"):
                self._btn_add_route["text"] = "Add Waypoint"
                self._btn_add_route.show()
        elif kind == "carrier":
            self._show_payload_fields(False)
            self._show_generic_fields(True)
            self._field_a_label["text"] = "heading"
            self._field_b_label["text"] = "route pts"
            self._field_a_entry.enterText(str(entity.get("heading", 0.0)))
            self._field_b_entry.enterText(str(len(entity.get("route") or [])))
            self._field_a_entry.show()
            self._field_b_entry.show()
            self._field_b_label.show()
            self._frame_btn.show()
            self._slots_label.hide()
            self._set_frame_button_label(entity.get("frame_type", "ESCORT_CARRIER"))
            self._sync_hold_button(bool(entity.get("hold_position", True)))
            self._btn_hold.show()
            self._btn_add_route["text"] = "Add Route Point"
            self._btn_add_route.show()
        elif kind == "corvette":
            self._show_payload_fields(False)
            self._show_generic_fields(True)
            loadout = entity.get("loadout") or dict(DEFAULT_CORVETTE_LOADOUT)
            self._field_a_label["text"] = "heading"
            self._field_b_label["text"] = "SAM"
            self._field_a_entry.enterText(str(entity.get("heading", 0.0)))
            self._field_b_entry.enterText(str(int(loadout.get("SAM_RADAR", 0))))
            self._field_a_entry.show()
            self._field_b_entry.show()
            self._field_b_label.show()
            self._frame_btn.show()
            self._slots_label.hide()
            self._set_frame_button_label(entity.get("frame_type", "CORVETTE_STANDARD"))
            self._sync_hold_button(bool(entity.get("hold_position", True)))
            self._btn_hold.show()
            self._btn_add_route["text"] = "Add Route Point"
            self._btn_add_route.show()
        else:
            self._show_payload_fields(False)
            self._show_generic_fields(False)
            self._frame_btn.hide()
            self._slots_label.hide()
        self._layout_property_form(kind)

    def _layout_property_form(self, kind: str) -> None:
        """Flow the kind-dependent tail of the form and hug the card to it.

        ID, the two generic fields and the ordnance grid are built once on the
        shared row rhythm, so only the frame/slots and action rows move. The
        card's bottom edge then follows the last row it actually placed, which
        is what stops `Apply`/`Delete` from being printed over a payload row
        and stops an unselected editor showing an empty bordered box.
        """
        if kind == "jet":
            top = self._form_tail_jet_z
        elif kind in ("target", "sam_site", "aaa_site", "tank", "carrier", "corvette"):
            top = self._form_tail_generic_z
        else:  # jammer / home_base / nothing selected: ID + actions only
            top = self._form_content_top_z - ROW_H
        stack = Stack(top, row_h=ROW_H, x=self._form_x)
        cols, col_w = _columns(self._form_x, 2, width=self._form_w)

        if kind in ("jet", "tank", "carrier", "corvette"):
            z = stack.row()
            self._frame_btn.set_pos(self._form_x, 0, z)
            self._slots_label.set_pos(self._form_x + self._form_w, 0, z)
            stack.gap(SPACE_2)
        if kind in ("carrier", "corvette"):
            z = stack.row()
            self._btn_hold.set_pos(cols[0] + col_w / 2.0, 0, z)
            self._btn_add_route.set_pos(cols[1] + col_w / 2.0, 0, z)
        elif kind == "tank" and not self._btn_add_route.isHidden():
            z = stack.row()
            self._btn_add_route.set_pos(cols[0] + col_w / 2.0, 0, z)

        z = stack.row()
        self._btn_apply.set_pos(cols[0] + col_w / 2.0, 0, z)
        self._btn_delete.set_pos(cols[1] + col_w / 2.0, 0, z)
        self._form_bottom_z = _set_panel_bottom(self._form_frame, z - _ROW_SINK)

    def _sync_hold_button(self, hold: bool) -> None:
        self._btn_hold["text"] = "Hold: ON" if hold else "Hold: OFF"

    def _toggle_hold_position(self) -> None:
        if self._selected is None:
            return
        kind = self._kind_of(self._selected)
        if kind not in ("carrier", "corvette"):
            return
        current = bool(self._selected.get("hold_position", True))
        self._selected["hold_position"] = not current
        self._sync_hold_button(not current)
        self._mark_manual_edit()
        self.rebuild_scene()

    def _arm_route_point(self) -> None:
        if self._selected is None:
            return
        kind = self._kind_of(self._selected)
        if kind in ("carrier", "corvette"):
            self._route_arming = True
            self._placement_kind = None
            self._hint.setText("Click the map to add a WATER route point.")
        elif kind == "tank":
            order = str(self._selected.get("order", "HOLD")).upper()
            if order not in ("PATROL", "ADVANCE"):
                self._set_banner(["Set order to PATROL or ADVANCE before adding waypoints."])
                return
            self._route_arming = True
            self._placement_kind = None
            self._hint.setText("Click the map to add a GROUND waypoint.")
        else:
            return

    def _append_route_or_waypoint(self, world_pos: tuple[float, float]) -> None:
        if self._selected is None:
            self._route_arming = False
            return
        kind = self._kind_of(self._selected)
        pos = (float(world_pos[0]), float(world_pos[1]))
        terrain = self._draft_terrain()
        try:
            if kind in ("carrier", "corvette"):
                if not is_passable(terrain, pos, "SURFACE"):
                    self._set_banner(["Route points must be on WATER."])
                    return
                route = list(self._selected.get("route") or [])
                route.append(pos)
                self._selected["route"] = route
                self._selected["hold_position"] = False
                self._sync_hold_button(False)
                self._field_b_entry.enterText(str(len(route)))
            elif kind == "tank":
                if not is_passable(terrain, pos, "GROUND"):
                    self._set_banner(["Waypoints must be on GROUND."])
                    return
                wps = list(self._selected.get("waypoints") or [])
                wps.append(pos)
                self._selected["waypoints"] = wps
            else:
                self._route_arming = False
                return
        except OutOfTerrainBoundsError:
            self._set_banner(["Position is outside terrain bounds."])
            return
        self._route_arming = False
        self._mark_manual_edit()
        self.rebuild_scene()
        self._hint.setText("Click Add, then click the map to place.")
        self._set_banner([])

    def _show_generic_fields(self, visible: bool) -> None:
        widgets = (
            self._field_a_entry,
            self._field_a_label,
            self._field_b_entry,
            self._field_b_label,
        )
        for widget in widgets:
            if visible:
                widget.show()
            else:
                widget.hide()

    def _show_payload_fields(self, visible: bool) -> None:
        section = self._ordnance_section
        for widget in (
            *self._payload_entries.values(),
            *self._payload_labels.values(),
            section.label,
            section.rule,
        ):
            if visible:
                widget.show()
            else:
                widget.hide()

    def _set_frame_button_label(self, frame_type: str) -> None:
        self._frame_btn["text"] = f"Frame: {frame_type}"

    def set_frame_type(self, frame_type: str) -> None:
        """Test/UI helper: assign a catalog frame to the selected entity."""
        if self._selected is None:
            return
        kind = self._kind_of(self._selected)
        if kind == "jet":
            if frame_type not in wn_config.JET_FRAME_CATALOG:
                return
        elif kind == "carrier":
            if frame_type not in _CARRIER_FRAME_TYPES:
                return
        elif kind == "corvette":
            if frame_type not in _CORVETTE_FRAME_TYPES:
                return
        elif kind == "tank":
            if frame_type not in TANK_FRAME_CATALOG:
                return
        else:
            return
        self._selected["frame_type"] = frame_type
        self._mark_manual_edit()
        self._set_frame_button_label(frame_type)
        if kind == "jet":
            self._refresh_slots_preview()
        self.rebuild_scene()

    def _cycle_frame_type(self) -> None:
        if self._selected is None:
            return
        kind = self._kind_of(self._selected)
        if kind == "jet":
            catalog = _FRAME_TYPES
            default = "STRIKE_FIGHTER"
        elif kind == "carrier":
            catalog = _CARRIER_FRAME_TYPES
            default = "ESCORT_CARRIER"
        elif kind == "corvette":
            catalog = _CORVETTE_FRAME_TYPES
            default = "CORVETTE_STANDARD"
        elif kind == "tank":
            catalog = _TANK_FRAME_TYPES
            default = "MBT_STANDARD"
        else:
            return
        current = self._selected.get("frame_type", default)
        idx = catalog.index(current) if current in catalog else 0
        nxt = catalog[(idx + 1) % len(catalog)]
        self.set_frame_type(nxt)

    def _payload_from_form(self) -> dict[str, int] | None:
        counts: dict[str, int] = {}
        for weapon, entry in self._payload_entries.items():
            try:
                counts[weapon] = int(entry.get().strip() or "0")
            except ValueError:
                return None
        return counts

    def _on_payload_typed(self, _text: str = "") -> None:
        self._refresh_slots_preview()

    def _refresh_slots_preview(self) -> None:
        if self._selected is None or self._kind_of(self._selected) != "jet":
            self._slots_label["text"] = ""
            return
        payload = self._payload_from_form()
        if payload is None:
            payload = dict(self._selected.get("payload") or {})
        frame_type = self._selected.get("frame_type", "STRIKE_FIGHTER")
        self._slots_label["text"] = slots_usage_text(payload, frame_type)

    def _kind_of(self, entity: dict) -> str:
        for kind, ent in self._all_entities():
            if ent is entity:
                return kind
        if "payload" in entity:
            return "jet"
        if entity.get("target_type") == "SAM_SITE":
            return "sam_site"
        if entity.get("target_type") == "AAA_SITE":
            return "aaa_site"
        if "unit_id" in entity and "order" in entity:
            return "tank"
        if "unit_id" in entity and "loadout" in entity:
            return "corvette"
        if "unit_id" in entity and "route" in entity:
            return "carrier"
        if "jammer_id" in entity:
            return "jammer"
        return "target"

    def _apply_form(self) -> None:
        entity = self._selected
        if entity is None:
            return
        kind = self._kind_of(entity)
        new_id = self._id_entry.get().strip()
        if kind == "jet":
            if new_id:
                entity["agent_id"] = new_id
            parsed = self._payload_from_form()
            if parsed is None:
                self._set_banner(["Payload counts must be integers."])
                return
            merged = dict(entity.get("payload") or {})
            merged.update(parsed)
            entity["payload"] = merged
            entity["frame_type"] = entity.get("frame_type", "STRIKE_FIGHTER")
            try:
                entity["flares_remaining"] = int(self._field_a_entry.get().strip() or "6")
                entity["flare_capacity"] = int(self._field_b_entry.get().strip() or "6")
            except ValueError:
                self._set_banner(["flare fields must be integers."])
                return
            self._refresh_slots_preview()
        elif kind == "target":
            if new_id:
                entity["target_id"] = new_id
            ttype = self._field_a_entry.get().strip() or "HARDENED_BUNKER"
            entity["target_type"] = ttype
            try:
                entity["hp_required"] = int(self._field_b_entry.get().strip() or "1")
            except ValueError:
                self._set_banner(["hp_required must be an integer."])
                return
        elif kind == "sam_site":
            if new_id:
                entity["target_id"] = new_id
            try:
                entity["hp_required"] = int(self._field_a_entry.get().strip() or "1")
            except ValueError:
                self._set_banner(["hp_required must be an integer."])
                return
            entity["target_type"] = "SAM_SITE"
            mix = (self._field_b_entry.get().strip() or "RADAR").upper()
            if mix.startswith("IR"):
                entity["missile_loadout"] = {"SAM_IR": 12}
            else:
                entity["missile_loadout"] = {"SAM_RADAR": 12}
        elif kind == "aaa_site":
            if new_id:
                entity["target_id"] = new_id
            try:
                entity["hp_required"] = int(self._field_a_entry.get().strip() or "2")
                entity["ammo"] = int(self._field_b_entry.get().strip() or "48")
            except ValueError:
                self._set_banner(["hp_required and ammo must be integers."])
                return
            entity["target_type"] = "AAA_SITE"
        elif kind == "tank":
            if new_id:
                entity["unit_id"] = new_id
            faction = self._field_a_entry.get().strip().upper() or "ENEMY"
            order = self._field_b_entry.get().strip().upper() or "HOLD"
            if faction not in ("FRIENDLY", "ENEMY"):
                self._set_banner(["faction must be FRIENDLY or ENEMY."])
                return
            if order not in ("HOLD", "PATROL", "ADVANCE"):
                self._set_banner(["order must be HOLD, PATROL, or ADVANCE."])
                return
            entity["faction"] = faction
            entity["order"] = order
            entity["frame_type"] = entity.get("frame_type", "MBT_STANDARD")
            if order == "HOLD":
                entity["waypoints"] = []
            self.open_property_form(entity)
        elif kind == "carrier":
            if new_id:
                entity["unit_id"] = new_id
            try:
                heading = float(self._field_a_entry.get().strip() or "0")
            except ValueError:
                self._set_banner(["heading must be a number."])
                return
            entity["heading"] = heading % 360.0
            entity["frame_type"] = entity.get("frame_type", "ESCORT_CARRIER")
            entity["hold_position"] = bool(entity.get("hold_position", True))
            entity.setdefault("route", [])
        elif kind == "corvette":
            if new_id:
                entity["unit_id"] = new_id
            try:
                heading = float(self._field_a_entry.get().strip() or "0")
                sam = int(self._field_b_entry.get().strip() or "8")
            except ValueError:
                self._set_banner(["heading must be a number; SAM must be an integer."])
                return
            entity["heading"] = heading % 360.0
            entity["frame_type"] = entity.get("frame_type", "CORVETTE_STANDARD")
            entity["hold_position"] = bool(entity.get("hold_position", True))
            entity.setdefault("route", [])
            loadout = dict(entity.get("loadout") or DEFAULT_CORVETTE_LOADOUT)
            loadout["SAM_RADAR"] = max(0, sam)
            loadout.setdefault("CIWS", DEFAULT_CORVETTE_LOADOUT["CIWS"])
            loadout.setdefault("DECK_GUN", DEFAULT_CORVETTE_LOADOUT["DECK_GUN"])
            entity["loadout"] = loadout
        else:
            if new_id:
                entity["jammer_id"] = new_id
        self._mark_manual_edit()
        self.rebuild_scene()
        self._refresh_entity_list()
        _, errors = self._construct_config()
        self._set_banner(errors or [])

    def _delete_selected(self) -> None:
        if self._selected is None:
            return
        kind = self._kind_of(self._selected)
        self.delete_entity(self._id_of(kind, self._selected))

    def delete_entity(self, entity_id: str) -> None:
        for bucket, key in (
            ("jets", "agent_id"),
            ("targets", "target_id"),
            ("sam_sites", "target_id"),
            ("aaa_sites", "target_id"),
            ("tanks", "unit_id"),
            ("carriers", "unit_id"),
            ("corvettes", "unit_id"),
            ("jammers", "jammer_id"),
        ):
            before = len(self.scenario_draft.get(bucket) or [])
            self.scenario_draft[bucket] = [
                e for e in (self.scenario_draft.get(bucket) or []) if e[key] != entity_id
            ]
            if len(self.scenario_draft[bucket]) != before:
                break
        self._selected = None
        self._route_arming = False
        self._mark_manual_edit()
        self.rebuild_scene()
        self._refresh_entity_list()
        _, errors = self._construct_config()
        self._set_banner(errors or [])

    def _normalize_draft_plain_dicts(self) -> None:
        """Ensure nested pydantic models are plain dicts for draft mutability."""
        draft = self.scenario_draft
        terrain = draft.get("terrain")
        if terrain is not None and hasattr(terrain, "model_dump"):
            draft["terrain"] = terrain.model_dump()
        draft["home_base"] = list(draft.get("home_base") or wn_config.DEFAULT_HOME_BASE)
        bases = draft.get("fixed_airbases") or []
        draft["fixed_airbases"] = [
            b.model_dump() if hasattr(b, "model_dump") else dict(b) for b in bases
        ]
        objs = draft.get("mission_objectives") or []
        draft["mission_objectives"] = [
            o.model_dump() if hasattr(o, "model_dump") else dict(o) for o in objs
        ]
        draft.setdefault("combat_seed", 0)
        draft.setdefault("scenario_version", SUPPORTED_SCENARIO_VERSION)
        draft.setdefault("carriers", [])
        draft.setdefault("corvettes", [])
        draft.setdefault("aaa_sites", [])
        draft.setdefault("tanks", [])
        for jet in draft.get("jets") or []:
            if "base_assignment" not in jet:
                jet["base_assignment"] = default_jet_assignment().model_dump()
            elif hasattr(jet["base_assignment"], "model_dump"):
                jet["base_assignment"] = jet["base_assignment"].model_dump()

    def _sync_v3_derived_fields(self, data: dict) -> None:
        """Keep Base_Home and legacy DESTROY subjects aligned with the draft."""
        home = tuple(data.get("home_base") or wn_config.DEFAULT_HOME_BASE)
        data["home_base"] = home
        bases = [
            b.model_dump() if hasattr(b, "model_dump") else dict(b)
            for b in (data.get("fixed_airbases") or [])
        ]
        updated = False
        for base in bases:
            if base.get("base_id") == HOME_BASE_ID:
                base["position"] = list(home) if isinstance(home[0], float) else home
                # Schema expects tuple
                base["position"] = (float(home[0]), float(home[1]))
                updated = True
                break
        if not updated:
            bases.append(default_home_airbase(home).model_dump())
            bases[-1]["position"] = (float(home[0]), float(home[1]))
        data["fixed_airbases"] = bases

        subject_ids = [t["target_id"] for t in (data.get("targets") or [])]
        subject_ids.extend(s["target_id"] for s in (data.get("sam_sites") or []))
        # Only require enemy tanks/corvettes on DESTROY when the draft's jet
        # payloads can actually cover their HP. Otherwise CNP awards nothing,
        # jets idle-burn to bingo, and every Launch aborts.
        armor_cap = 0
        ship_cap = 0
        for jet in data.get("jets") or []:
            for weapon, count in (jet.get("payload") or {}).items():
                spec = wn_config.WEAPON_CATALOG.get(weapon)
                if spec is None:
                    continue
                dmg = int(spec.damage_per_hit) * int(count or 0)
                if float(spec.suitability.get("GROUND_VEHICLE", 0.0) or 0.0) > 0.0:
                    armor_cap += dmg
                if float(spec.suitability.get("SURFACE_VESSEL", 0.0) or 0.0) > 0.0:
                    ship_cap += dmg
        tank_hp = 0
        for tank in data.get("tanks") or []:
            if tank.get("faction") != "ENEMY":
                continue
            frame = str(tank.get("frame_type") or "MBT_STANDARD")
            catalog_hp = int(TANK_FRAME_CATALOG.get(frame, TANK_FRAME_CATALOG["MBT_STANDARD"]).hp)
            hp = tank.get("hp")
            tank_hp += int(hp if hp is not None else catalog_hp)
        if tank_hp > 0 and armor_cap >= tank_hp:
            for tank in data.get("tanks") or []:
                if tank.get("faction") == "ENEMY":
                    subject_ids.append(tank["unit_id"])
        ship_hp = 0
        for ship in data.get("corvettes") or []:
            frame = str(ship.get("frame_type") or "CORVETTE_STANDARD")
            ship_hp += int(NAVAL_FRAME_CATALOG[frame].hp)
        if ship_hp > 0 and ship_cap >= ship_hp:
            for ship in data.get("corvettes") or []:
                subject_ids.append(ship["unit_id"])
        jet_ids = [j["agent_id"] for j in (data.get("jets") or [])]
        objs = [
            o.model_dump() if hasattr(o, "model_dump") else dict(o)
            for o in (data.get("mission_objectives") or [])
        ]
        legacy_idx = next(
            (
                i
                for i, o in enumerate(objs)
                if o.get("objective_id") == "OBJ_DESTROY_ALL"
            ),
            None,
        )
        return_idx = next(
            (
                i
                for i, o in enumerate(objs)
                if o.get("objective_id") == "OBJ_RETURN_ALL"
            ),
            None,
        )
        if subject_ids:
            if legacy_idx is None and not any(
                o.get("objective_type") == "DESTROY" for o in objs
            ):
                objs.insert(
                    0, synthesize_legacy_destroy_objective(subject_ids).model_dump()
                )
                if return_idx is not None:
                    return_idx += 1
                legacy_idx = 0
            elif legacy_idx is None and len(objs) == 1 and objs[0].get("objective_type") == "DESTROY":
                objs[0]["subject_ids"] = list(subject_ids)
            elif legacy_idx is not None:
                objs[legacy_idx]["subject_ids"] = list(subject_ids)
        if jet_ids:
            ret = synthesize_legacy_return_objective(jet_ids).model_dump()
            if return_idx is None:
                objs.append(ret)
            else:
                objs[return_idx] = ret
        elif return_idx is not None:
            objs.pop(return_idx)
        data["mission_objectives"] = objs
        data.setdefault("combat_seed", 0)
        data["scenario_version"] = data.get("scenario_version", SUPPORTED_SCENARIO_VERSION)
        for jet in data.get("jets") or []:
            if "base_assignment" not in jet:
                jet["base_assignment"] = default_jet_assignment().model_dump()

    def _construct_config(self) -> tuple[ScenarioConfig | None, list[str] | None]:
        """Lossless construct: deep-copy draft, sync derived v3 fields, validate."""
        data = copy.deepcopy(self.scenario_draft)
        self._sync_v3_derived_fields(data)
        try:
            return scenario_from_data(data), None
        except (ValidationError, ValueError) as exc:
            if isinstance(exc, ValidationError):
                return None, _messages_from_validation(exc)
            return None, [str(exc)]

    def save(self, path: str) -> list[str] | None:
        """Build a ScenarioConfig and write it. Returns None on success, or
        a list of human-readable errors (and does not write) on failure.
        """
        config, errors = self._construct_config()
        if errors is not None:
            self._set_banner(errors)
            return errors
        assert config is not None
        save_scenario(config, path)
        self._path = path
        self._path_entry.enterText(path)
        # Clear unsaved-edit flag; never reset modified_after_generation.
        self._draft_dirty = False
        self._set_banner([])
        self._refresh_generation_status()
        return None

    def load(self, path: str) -> None:
        """Replace the current draft entirely from a saved ScenarioConfig."""
        loaded = load_scenario(path)
        self.scenario_draft = loaded.model_dump()
        self._normalize_draft_plain_dicts()
        self.camera = OrthoEditorCamera(
            self.base, loaded.grid_width, loaded.grid_height
        )
        self._path = path
        self._path_entry.enterText(path)
        self._selected = None
        self._draft_dirty = False
        self._generation_report = None
        self._generation_undo_snapshot = None
        self._terrain_undo_stack = []
        self._terrain_redo_stack = []
        self._stroke_active = False
        self._stroke_before = None
        self._stroke_samples = []
        self._pending_relocate_confirm = False
        self.enter_entity_mode()
        self._clear_pending_generation()
        if loaded.generation is not None:
            self._generation_parameters = loaded.generation.resolved_parameters
            self._sync_generation_widgets_from_parameters()
        self._set_banner([])
        self.rebuild_scene()
        self._refresh_entity_list()
        self._refresh_generation_status()
        self._refresh_undo_button()

    def preview(self) -> list[str] | None:
        """Validate, then feed a TraceHeader into Phase 17 mission-brief mode."""
        config, errors = self._construct_config()
        if errors is not None:
            self._set_banner(errors)
            return errors
        assert config is not None
        self._set_banner([])
        header = scenario_to_header(config)
        enter = getattr(self.base, "enter_preview", None)
        if enter is not None:
            enter(header)
        else:
            self.base.view_mode = "brief"
            self.base._header = header
        return None

    def _header_from_draft(self) -> TraceHeader:
        """Unchecked header for the in-progress (possibly invalid) draft."""
        jets = self.scenario_draft["jets"]
        targets = self.scenario_draft["targets"]
        sams = self.scenario_draft["sam_sites"]
        aaas = self.scenario_draft.get("aaa_sites") or []
        jammers = self.scenario_draft["jammers"]
        all_targets = list(targets) + list(sams) + list(aaas)
        return TraceHeader(
            grid_width=self.scenario_draft["grid_width"],
            grid_height=self.scenario_draft["grid_height"],
            sensor_radius=wn_config.SENSOR_RADIUS,
            jam_radius=wn_config.JAM_RADIUS,
            sam_engagement_range=wn_config.SAM_ENGAGEMENT_RANGE,
            strike_range=wn_config.WEAPON_CATALOG["JDAM"].range,
            jet_ids=[j["agent_id"] for j in jets],
            initial_payload={
                j["agent_id"]: {
                    k: v
                    for k, v in dict(j.get("payload") or {}).items()
                    if k in wn_config.WEAPON_CATALOG
                }
                for j in jets
            },
            target_ids=[t["target_id"] for t in all_targets],
            target_types={t["target_id"]: t.get("target_type", "HARDENED_BUNKER") for t in all_targets},
            # Data plumbing only -- an in-progress draft may be invalid (e.g.
            # hp_required < 1); real validation happens exclusively in
            # ScenarioConfig's model_validator via _construct_config().
            target_hp_required={t["target_id"]: int(t.get("hp_required") or 1) for t in all_targets},
            sam_site_ids=[s["target_id"] for s in sams],
            aaa_site_ids=[a["target_id"] for a in aaas],
            jammer_positions={j["jammer_id"]: tuple(j["position"]) for j in jammers},
            target_positions={t["target_id"]: tuple(t["position"]) for t in all_targets},
            home_base=tuple(self.scenario_draft.get("home_base") or wn_config.DEFAULT_HOME_BASE),
            jet_frame_types={
                j["agent_id"]: j.get("frame_type", "STRIKE_FIGHTER") for j in jets
            },
            terrain=self._draft_terrain(),
            trace_version=3,
            **catalog_header_fields(),
        )

    def rebuild_scene(self) -> None:
        if self._handles is not None and self._handles.root is not None:
            if not self._handles.root.is_empty():
                self._handles.root.remove_node()
        render = getattr(self.base, "render", None)
        if render is None:
            self._handles = None
            return
        header = self._header_from_draft()
        self._handles = build_static_scene(render, header)
        for jet in self.scenario_draft["jets"]:
            node = self._handles.jets.get(jet["agent_id"])
            if node is None or node.is_empty():
                continue
            x, y = jet["position"]
            node.set_pos(x, y, jet_altitude(jet["agent_id"], header))
            node.show()
        carrier_frames = [
            CarrierFrame(
                unit_id=c["unit_id"],
                position=tuple(c["position"]),
                heading=float(c.get("heading", 0.0)),
                hp=int(
                    NAVAL_FRAME_CATALOG.get(
                        c.get("frame_type", "ESCORT_CARRIER"),
                        NAVAL_FRAME_CATALOG["ESCORT_CARRIER"],
                    ).hp
                ),
                hp_max=int(
                    NAVAL_FRAME_CATALOG.get(
                        c.get("frame_type", "ESCORT_CARRIER"),
                        NAVAL_FRAME_CATALOG["ESCORT_CARRIER"],
                    ).hp
                ),
                destroyed=False,
                hold_position=bool(c.get("hold_position", True)),
                route=[tuple(p) for p in (c.get("route") or [])],
                frame_type=str(c.get("frame_type", "ESCORT_CARRIER")),
            )
            for c in (self.scenario_draft.get("carriers") or [])
        ]
        corvette_frames = []
        for c in self.scenario_draft.get("corvettes") or []:
            ft = str(c.get("frame_type", "CORVETTE_STANDARD"))
            spec = NAVAL_FRAME_CATALOG.get(ft, NAVAL_FRAME_CATALOG["CORVETTE_STANDARD"])
            corvette_frames.append(
                CorvetteFrame(
                    unit_id=c["unit_id"],
                    position=tuple(c["position"]),
                    heading=float(c.get("heading", 0.0)),
                    hp=int(spec.hp),
                    hp_max=int(spec.hp),
                    destroyed=False,
                    hold_position=bool(c.get("hold_position", True)),
                    route=[tuple(p) for p in (c.get("route") or [])],
                    frame_type=ft,
                    loadout=dict(c.get("loadout") or DEFAULT_CORVETTE_LOADOUT),
                    sam_range=float(spec.sam_engagement_range or 15.0),
                )
            )
        tank_frames = [
            TankFrame(
                unit_id=t["unit_id"],
                position=tuple(t["position"]),
                heading=float(t.get("heading", 0.0)),
                hp=int(TANK_FRAME_CATALOG.get(t.get("frame_type", "MBT_STANDARD"), TANK_FRAME_CATALOG["MBT_STANDARD"]).hp),
                hp_max=int(TANK_FRAME_CATALOG.get(t.get("frame_type", "MBT_STANDARD"), TANK_FRAME_CATALOG["MBT_STANDARD"]).hp),
                destroyed=False,
                faction=str(t.get("faction", "ENEMY")),
                frame_type=str(t.get("frame_type", "MBT_STANDARD")),
                order=str(t.get("order", "HOLD")),
                ammo=int(t.get("ammo") or 0),
            )
            for t in (self.scenario_draft.get("tanks") or [])
        ]
        sync_static_unit_glyphs(
            self._handles,
            carriers=carrier_frames,
            corvettes=corvette_frames,
            tanks=tank_frames,
        )
        self._apply_invalid_entity_highlights()

    def _draft_terrain(self):
        return terrain_from_draft(self.scenario_draft["terrain"])

    def _apply_invalid_entity_highlights(self) -> None:
        if self._handles is None:
            return
        issues = invalid_ground_entities(self.scenario_draft)
        bad_labels = {label for label, _ in issues}

        def _mark(node, label: str) -> None:
            if node is None or node.is_empty():
                return
            if label in bad_labels:
                node.set_color(*_INVALID_ENTITY_COLOR)

        for t in self.scenario_draft.get("targets") or []:
            tid = t["target_id"]
            _mark(self._handles.targets.get(tid), f"Target '{tid}'")
        for s in self.scenario_draft.get("sam_sites") or []:
            tid = s["target_id"]
            _mark(self._handles.targets.get(tid), f"SAM '{tid}'")
        for a in self.scenario_draft.get("aaa_sites") or []:
            tid = a["target_id"]
            _mark(self._handles.targets.get(tid), f"AAA '{tid}'")
        for j in self.scenario_draft.get("jammers") or []:
            jid = j["jammer_id"]
            _mark(self._handles.jammers.get(jid), f"Jammer '{jid}'")
        for tank in self.scenario_draft.get("tanks") or []:
            uid = tank["unit_id"]
            _mark(self._handles.tanks.get(uid), f"Tank '{uid}'")
        for c in self.scenario_draft.get("carriers") or []:
            uid = c["unit_id"]
            _mark(self._handles.carriers.get(uid), f"Carrier '{uid}'")
        for c in self.scenario_draft.get("corvettes") or []:
            uid = c["unit_id"]
            _mark(self._handles.corvettes.get(uid), f"Corvette '{uid}'")
        _mark(self._handles.home_base, "Home base")

    def _refresh_entity_list(self) -> None:
        for btn in self._list_buttons:
            try:
                btn.destroy()
            except Exception:
                pass
        self._list_buttons = []
        self._list_overflow_label.hide()
        entities = list(self._all_entities())
        z = self._list_anchor_z
        visible = entities[: self._list_max_visible]
        for kind, ent in visible:
            label = f"{kind.replace('_', ' ')}  {self._id_of(kind, ent)}"
            btn = _btn(
                self._lower_node,
                label,
                (_L_X, 0, z),
                self.open_property_form,
                extra_args=[ent],
                scale=TYPE_LABEL,
                style="ghost",
                width=CONTENT_W,
                align=TextNode.ALeft,
            )
            self._list_buttons.append(btn)
            z -= ROW_H_TIGHT
        overflow = len(entities) - len(visible)
        if overflow > 0:
            self._list_overflow_label["text"] = f"+{overflow} more on map"
            self._list_overflow_label.show()

    def _set_banner(self, messages: list[str]) -> None:
        self.banner_messages = list(messages)
        if not messages:
            self._banner.setText("")
            self._banner.hide()
            return
        self._banner.setText("\n".join(messages))
        self._banner.show()

    def aspect_ratio(self) -> float:
        """Render aspect ratio, or 1.0 when there is no window (headless)."""
        getter = getattr(self.base, "getAspectRatio", None)
        try:
            ratio = float(getter()) if getter is not None else 1.0
        except (AttributeError, AssertionError, TypeError, ValueError):
            ratio = 1.0
        return ratio if ratio > 0.0 else 1.0

    def panel_rects(self) -> list[tuple[float, float, float, float]]:
        """Both console panels as (left, right, bottom, top) in aspect2d units.

        Read from the live `frameSize` and net transform rather than repeated
        as constants, so the mouse guard below cannot drift out of sync when a
        panel is resized (both panels now shrink to their content, and the
        left one grows in terrain mode).
        """
        a2d = getattr(self.base, "aspect2d", None)
        if a2d is None:
            return []
        rects: list[tuple[float, float, float, float]] = []
        for panel in (self._panel, self._gen_panel):
            if panel is None or panel.is_hidden():
                continue
            frame = panel["frameSize"]
            if frame is None:
                continue
            pos = panel.get_pos(a2d)
            left, right, bottom, top = (float(v) for v in frame)
            rects.append(
                (pos[0] + left, pos[0] + right, pos[2] + bottom, pos[2] + top)
            )
        return rects

    def mouse_over_panel(self, mouse_x: float, mouse_y: float) -> bool:
        """Does a mouseWatcher position land on a console panel?

        Rectangular, not a fixed horizontal band: the panels no longer run the
        full height of the screen, so a band guard would make the map dead
        under the (now short) Quick Generate panel while leaving the strip the
        narrower panels gave back unusable.
        """
        x = float(mouse_x) * self.aspect_ratio()
        return any(_rect_contains(rect, x, float(mouse_y)) for rect in self.panel_rects())

    def on_map_click_from_mouse(self) -> None:
        if getattr(self.base, "view_mode", "editor") != "editor":
            return
        mw = getattr(self.base, "mouseWatcherNode", None)
        if mw is None:
            return
        try:
            if not mw.has_mouse():
                return
            mx = mw.get_mouse_x()
            my = mw.get_mouse_y()
        except (AttributeError, AssertionError, TypeError):
            return
        # Ignore clicks over the left entity panel or right Quick Generate panel.
        if self.mouse_over_panel(mx, my):
            return
        world = self.camera.screen_to_world(mx, my)
        self.on_ground_clicked(world)

    # --- Terrain edit mode (Phase 36) ------------------------------------

    def enter_terrain_mode(self) -> None:
        self.cancel_stroke()
        self._placement_kind = None
        self._edit_mode = "TERRAIN"
        self._pending_relocate_confirm = False
        self._refresh_edit_mode_ui()
        self._refresh_terrain_validity_banner()

    def enter_entity_mode(self) -> None:
        self.cancel_stroke()
        self._edit_mode = "ENTITY"
        self._pending_relocate_confirm = False
        self._btn_relocate_confirm.hide()
        self._refresh_edit_mode_ui()
        self._refresh_terrain_validity_banner()

    def _refresh_edit_mode_ui(self) -> None:
        terrain = self._edit_mode == "TERRAIN"
        for btn in (
            self._btn_add_jet,
            self._btn_add_target,
            self._btn_add_sam,
            self._btn_add_aaa,
            self._btn_add_jammer,
            self._btn_add_tank,
            self._btn_add_carrier,
            self._btn_add_corvette,
            self._btn_home_base,
            self._btn_edit_terrain,
        ):
            if terrain:
                btn.hide()
            else:
                btn.show()
        for widget in (self._place_section.label, self._place_section.rule):
            if terrain:
                widget.hide()
            else:
                widget.show()
        if terrain:
            self._terrain_panel.show()
            self._hint.setText("Drag on the map to paint terrain. Esc cancels stroke.")
        else:
            self._terrain_panel.hide()
            self._hint.setText("Click Add, then click the map to place.")
        self._refresh_mode_band()
        self._btn_brush["text"] = f"Brush: {self._terrain_brush}"
        self._btn_radius["text"] = f"Radius: {self._brush_radius}"
        if self._city_height_mode == "RANDOM":
            self._btn_city_height["text"] = "City Ht: Random"
        else:
            self._btn_city_height["text"] = f"City Ht: Fixed"
        if self._terrain_gen_expanded:
            self._terrain_gen_frame.show()
        else:
            self._terrain_gen_frame.hide()

    def _refresh_mode_band(self) -> None:
        """Size the mode band to the card it is showing and slide the rest.

        `[ TERRAIN MODE ]` needs more rows than `[ PLACE ]` -- more again with
        the generator fractions open -- so the band's extra height is pushed
        into `_lower_node` instead of being painted over the entity list and
        the properties card underneath.
        """
        if self._edit_mode == "TERRAIN":
            content_bottom = (
                self._terrain_expanded_bottom_z
                if self._terrain_gen_expanded
                else self._terrain_content_bottom_z
            )
            band_bottom = _set_panel_bottom(self._terrain_panel, content_bottom)
            band_h = self._band_top_z - band_bottom
        else:
            band_h = self._place_band_h
        shift = max(0.0, band_h - self._place_band_h)
        self._lower_node.set_z(-shift)
        _set_panel_bottom(self._panel, self._lower_content_bottom_z - shift)

    def _cycle_terrain_brush(self) -> None:
        idx = _BRUSH_CYCLE.index(self._terrain_brush)
        self._terrain_brush = _BRUSH_CYCLE[(idx + 1) % len(_BRUSH_CYCLE)]
        self._btn_brush["text"] = f"Brush: {self._terrain_brush}"

    def _cycle_brush_radius(self) -> None:
        self._brush_radius = 1 if self._brush_radius >= 5 else self._brush_radius + 1
        self._btn_radius["text"] = f"Radius: {self._brush_radius}"

    def _cycle_city_height_mode(self) -> None:
        if self._city_height_mode == "RANDOM":
            self._city_height_mode = "FIXED"
            self._btn_city_height["text"] = "City Ht: Fixed"
        else:
            self._city_height_mode = "RANDOM"
            self._btn_city_height["text"] = "City Ht: Random"

    def _read_fixed_city_height(self) -> float:
        try:
            return float(self._fixed_height_entry.get().strip() or "10")
        except ValueError:
            return 10.0

    def _push_terrain_undo(self, before: dict) -> None:
        self._terrain_undo_stack.append(before)
        self._terrain_redo_stack.clear()

    def _set_draft_terrain(self, terrain) -> None:
        if hasattr(terrain, "model_dump"):
            self.scenario_draft["terrain"] = terrain.model_dump()
        else:
            self.scenario_draft["terrain"] = copy.deepcopy(terrain)

    def _apply_stroke_preview(self) -> None:
        if self._stroke_before is None:
            return
        base = terrain_from_draft(self._stroke_before)
        cells = stroke_cells(base, self._stroke_samples, self._brush_radius)
        painted = apply_paint_to_cells(
            base,
            cells,
            self._terrain_brush,
            city_height_mode=self._city_height_mode,
            fixed_city_height=self._read_fixed_city_height(),
            stroke_sequence=self._stroke_sequence,
        )
        self._set_draft_terrain(painted)
        self.rebuild_scene()

    def terrain_paint_begin(self, world_pos: tuple[float, float]) -> None:
        if self._edit_mode != "TERRAIN":
            return
        self._stroke_active = True
        self._stroke_before = copy.deepcopy(self.scenario_draft["terrain"])
        self._stroke_samples = [(float(world_pos[0]), float(world_pos[1]))]
        self._apply_stroke_preview()

    def terrain_paint_drag(self, world_pos: tuple[float, float]) -> None:
        if not self._stroke_active:
            return
        sample = (float(world_pos[0]), float(world_pos[1]))
        if self._stroke_samples and self._stroke_samples[-1] == sample:
            return
        self._stroke_samples.append(sample)
        self._apply_stroke_preview()

    def terrain_paint_end(self) -> None:
        if not self._stroke_active:
            return
        self._stroke_active = False
        if self._stroke_before is not None:
            self._push_terrain_undo(self._stroke_before)
        self._stroke_before = None
        self._stroke_samples = []
        self._stroke_sequence += 1
        self._mark_manual_edit()
        self._refresh_terrain_validity_banner()

    def cancel_stroke(self) -> None:
        if not self._stroke_active:
            return
        if self._stroke_before is not None:
            self._set_draft_terrain(self._stroke_before)
        self._stroke_active = False
        self._stroke_before = None
        self._stroke_samples = []
        self.rebuild_scene()
        self._refresh_terrain_validity_banner()

    def handle_escape(self) -> bool:
        """Return True if Escape was consumed (stroke cancel or leave terrain)."""
        if self._stroke_active:
            self.cancel_stroke()
            return True
        if self._edit_mode == "TERRAIN":
            self.enter_entity_mode()
            return True
        if self._placement_kind is not None:
            self.cancel_placement()
            return True
        return False

    def _fill_terrain_clicked(self) -> None:
        before = copy.deepcopy(self.scenario_draft["terrain"])
        filled = fill_terrain(
            self._draft_terrain(),
            self._terrain_brush,
            city_height_mode=self._city_height_mode,
            fixed_city_height=self._read_fixed_city_height(),
            stroke_sequence=self._stroke_sequence,
        )
        self._push_terrain_undo(before)
        self._set_draft_terrain(filled)
        self._stroke_sequence += 1
        self._mark_manual_edit()
        self.rebuild_scene()
        self._refresh_terrain_validity_banner()

    def _toggle_terrain_gen_panel(self) -> None:
        self._terrain_gen_expanded = not self._terrain_gen_expanded
        if self._terrain_gen_expanded:
            self._terrain_gen_frame.show()
        else:
            self._terrain_gen_frame.hide()
        self._refresh_mode_band()

    def _generate_terrain_clicked(self) -> None:
        try:
            water = float(self._terrain_water_entry.get().strip() or "0.20")
            forest = float(self._terrain_forest_entry.get().strip() or "0.20")
            city = float(self._terrain_city_entry.get().strip() or "0.10")
            params = TerrainGenerationParameters(
                water_fraction=water,
                forest_fraction=forest,
                city_fraction=city,
            )
        except (ValueError, ValidationError) as exc:
            if isinstance(exc, ValidationError):
                self._set_banner(_messages_from_validation(exc))
            else:
                self._set_banner([str(exc)])
            return
        seed = int(self.scenario_draft["terrain"].get("terrain_edit_seed", 0))
        try:
            grid = generate_terrain(
                float(self.scenario_draft["grid_width"]),
                float(self.scenario_draft["grid_height"]),
                params,
                seed=seed,
            )
        except TerrainGenerationError as exc:
            self._set_banner([str(exc)])
            return
        before = copy.deepcopy(self.scenario_draft["terrain"])
        self._push_terrain_undo(before)
        self._set_draft_terrain(grid)
        self._terrain_gen_params = params
        self._mark_manual_edit()
        self.rebuild_scene()
        self._refresh_terrain_validity_banner()

    def _refresh_terrain_validity_banner(self) -> None:
        issues = invalid_ground_entities(self.scenario_draft)
        if not issues:
            if self._pending_relocate_confirm:
                self._pending_relocate_confirm = False
                self._btn_relocate_confirm.hide()
            # Keep other banners if not terrain-related; clear only when we own them.
            if self._edit_mode == "TERRAIN":
                self._set_banner([])
            return
        lines = [f"{label}: {reason}" for label, reason in issues[:8]]
        if len(issues) > 8:
            lines.append(f"...and {len(issues) - 8} more")
        lines.append("Save/Preview/Launch blocked until fixed. Use Relocate Invalid.")
        self._set_banner(lines)

    def _relocate_invalid_clicked(self) -> None:
        issues = invalid_ground_entities(self.scenario_draft)
        if not issues:
            self._set_banner(["No invalid ground entities."])
            self._pending_relocate_confirm = False
            self._btn_relocate_confirm.hide()
            return
        self._pending_relocate_confirm = True
        self._btn_relocate_confirm.show()
        self._set_banner(
            [f"{len(issues)} invalid entities. Press Confirm Relocate to move them."]
        )

    def _confirm_relocate(self) -> None:
        if not self._pending_relocate_confirm:
            return
        before = copy.deepcopy(self.scenario_draft)
        relocated = relocate_invalid_entities(self.scenario_draft)
        self._push_terrain_undo(before)
        self.scenario_draft = relocated
        self._pending_relocate_confirm = False
        self._btn_relocate_confirm.hide()
        self._mark_manual_edit()
        self.rebuild_scene()
        self._refresh_terrain_validity_banner()

    def _restore_terrain_snapshot(self, snap) -> None:
        if isinstance(snap, dict) and "jets" in snap and "terrain" in snap:
            self.scenario_draft = copy.deepcopy(snap)
        else:
            self._set_draft_terrain(snap)

    def _terrain_undo(self) -> None:
        if not self._terrain_undo_stack:
            return
        current = copy.deepcopy(self.scenario_draft)
        before = self._terrain_undo_stack.pop()
        self._terrain_redo_stack.append(current)
        self._restore_terrain_snapshot(before)
        self._mark_manual_edit()
        self.rebuild_scene()
        self._refresh_terrain_validity_banner()

    def _terrain_redo(self) -> None:
        if not self._terrain_redo_stack:
            return
        current = copy.deepcopy(self.scenario_draft)
        nxt = self._terrain_redo_stack.pop()
        self._terrain_undo_stack.append(current)
        self._restore_terrain_snapshot(nxt)
        self._mark_manual_edit()
        self.rebuild_scene()
        self._refresh_terrain_validity_banner()

    # --- Quick Generate -------------------------------------------------

    def _mark_manual_edit(self) -> None:
        self._draft_dirty = True
        meta = self.scenario_draft.get("generation")
        if isinstance(meta, dict):
            meta["modified_after_generation"] = True
        self._refresh_generation_status()

    def _draft_has_content(self) -> bool:
        draft = self.scenario_draft
        return bool(
            draft["jets"]
            or draft["targets"]
            or draft["sam_sites"]
            or draft.get("aaa_sites")
            or draft.get("tanks")
            or draft.get("carriers")
            or draft.get("corvettes")
            or draft["jammers"]
        )

    def _generation_metadata_dict(self) -> dict | None:
        meta = self.scenario_draft.get("generation")
        return meta if isinstance(meta, dict) else None

    def _read_seed_from_entry(self) -> int:
        raw = self._seed_entry.get().strip() or "0"
        seed = int(raw)
        if seed < 0 or seed > 2**64 - 1:
            raise ValueError("Seed must be an unsigned 64-bit integer.")
        return seed

    def _apply_preset(self, preset: str, seed: int) -> None:
        mode = self._generation_parameters.friendly_mode
        roster = list(self._generation_parameters.custom_roster)
        params = parameters_for_preset(preset, seed=seed)
        dumped = params.model_dump()
        dumped["friendly_mode"] = mode
        dumped["custom_roster"] = (
            [t.model_dump() for t in roster] if mode == "CUSTOM_ROSTER" else []
        )
        self._generation_parameters = GenerationParameters.model_validate(dumped)
        self._sync_generation_widgets_from_parameters()

    def _cycle_difficulty(self) -> None:
        current = self._generation_parameters.preset
        if current in _DIFFICULTY_CYCLE:
            nxt = _DIFFICULTY_CYCLE[(_DIFFICULTY_CYCLE.index(current) + 1) % len(_DIFFICULTY_CYCLE)]
        else:
            nxt = "TRAINING"
        try:
            seed = self._read_seed_from_entry()
        except ValueError as exc:
            self._set_banner([str(exc)])
            return
        self._apply_preset(nxt, seed)

    def _cycle_friendly_mode(self) -> None:
        current = self._generation_parameters.friendly_mode
        idx = _FRIENDLY_MODE_CYCLE.index(current) if current in _FRIENDLY_MODE_CYCLE else 0
        nxt = _FRIENDLY_MODE_CYCLE[(idx + 1) % len(_FRIENDLY_MODE_CYCLE)]
        dumped = self._generation_parameters.model_dump()
        dumped["friendly_mode"] = nxt
        if nxt != "CUSTOM_ROSTER":
            dumped["custom_roster"] = []
        elif not dumped["custom_roster"]:
            dumped["custom_roster"] = [
                {
                    "frame_type": "STRIKE_FIGHTER",
                    "payload": {w: 0 for w in wn_config.WEAPON_CATALOG},
                    "count": 1,
                }
            ]
            dumped["custom_roster"][0]["payload"]["JDAM"] = 2
        try:
            self._generation_parameters = GenerationParameters.model_validate(dumped)
        except ValidationError as exc:
            self._set_banner(_messages_from_validation(exc))
            return
        self._sync_generation_widgets_from_parameters()

    def _new_seed(self) -> None:
        seed = secrets.randbits(64)
        self._seed_entry.enterText(str(seed))
        preset = self._generation_parameters.preset
        if preset in _DIFFICULTY_CYCLE:
            self._apply_preset(preset, seed)
        else:
            dumped = self._generation_parameters.model_dump()
            dumped["seed"] = seed
            self._generation_parameters = GenerationParameters.model_validate(dumped)
            self._sync_generation_widgets_from_parameters()

    def _toggle_advanced(self) -> None:
        self._advanced_expanded = not self._advanced_expanded
        self._refresh_advanced_visibility()

    def _toggle_status_details(self) -> None:
        self._status_details_expanded = not self._status_details_expanded
        if self._status_details_expanded:
            self._gen_details_label.show()
        else:
            self._gen_details_label.hide()
        self._relayout_generation_panel()

    def _refresh_advanced_visibility(self) -> None:
        mode = self._generation_parameters.friendly_mode
        if self._advanced_expanded:
            self._advanced_frame.show()
            self._btn_advanced["text"] = "Hide..."
        else:
            self._advanced_frame.hide()
            self._btn_advanced["text"] = "Advanced..."
        if mode == "CUSTOM_ROSTER":
            self._roster_frame.show()
        else:
            self._roster_frame.hide()
        self._relayout_generation_panel()

    def _relayout_generation_panel(self) -> None:
        """Flow the status text and the expandable cards, then hug the panel.

        Status, details and template lines all wrap to data-driven lengths, and
        Advanced/Custom Roster can each be open or shut (and used to be drawn
        on top of one another), so this is measured top-down every refresh
        rather than reserved as fixed rows.
        """
        top = self._gen_flow_top_z
        self._gen_status_label.set_pos(_R_X, 0, top - _TEXT_RISE * TYPE_MICRO)
        top -= _text_block_height(self._gen_status_label, TYPE_MICRO) + SPACE_2

        self._btn_status_details.set_pos(
            _R_X + CONTENT_W - self._details_btn_w / 2.0,
            0,
            top - _TEXT_RISE * TYPE_LABEL,
        )
        top -= ROW_H_TIGHT

        if self._status_details_expanded:
            self._gen_details_label.set_pos(_R_X, 0, top - _TEXT_RISE * TYPE_MICRO)
            top -= _text_block_height(self._gen_details_label, TYPE_MICRO) + SPACE_2

        for card, visible in (
            (self._advanced_frame, self._advanced_expanded),
            (
                self._roster_frame,
                self._generation_parameters.friendly_mode == "CUSTOM_ROSTER",
            ),
        ):
            if not visible:
                continue
            card.set_z(top)
            top += float(card["frameSize"][2]) - SPACE_2

        _set_panel_bottom(self._gen_panel, top)

    def _relayout_advanced_card(self) -> None:
        """Flow the templates/summary lines at the foot of `[ ADVANCED ]`."""
        top = self._advanced_flow_top_z
        x = _R_LEFT + _NEST_PAD + _INSET
        self._templates_label.set_pos(x, 0, top - _TEXT_RISE * TYPE_MICRO)
        top -= _text_block_height(self._templates_label, TYPE_MICRO) + SPACE_1
        self._param_summary_label.set_pos(x, 0, top - _TEXT_RISE * TYPE_MICRO)
        top -= _text_block_height(self._param_summary_label, TYPE_MICRO)
        _set_panel_bottom(self._advanced_frame, top)

    def _resize_roster_frame(self) -> None:
        """Size `[ CUSTOM ROSTER ]` to however many roster rows exist."""
        rows = max(len(self._roster_rows), 1)
        bottom = self._roster_anchor_z - rows * self._roster_row_h + SPACE_2
        _set_panel_bottom(self._roster_frame, bottom)

    def _on_advanced_field_edited(self, _text: str = "") -> None:
        self._mark_parameters_custom_from_widgets()

    def _mark_parameters_custom_from_widgets(self) -> None:
        try:
            params = self._parse_generation_parameters(force_custom_preset=True)
        except (ValidationError, ValueError) as exc:
            if isinstance(exc, ValidationError):
                self._set_banner(_messages_from_validation(exc))
            else:
                self._set_banner([str(exc)])
            return
        self._generation_parameters = params
        self._btn_difficulty["text"] = "Difficulty: CUSTOM"
        self._refresh_param_summary()

    def _cycle_group_style(self) -> None:
        current = self._generation_parameters.group_style
        idx = _GROUP_STYLES.index(current) if current in _GROUP_STYLES else 0
        nxt = _GROUP_STYLES[(idx + 1) % len(_GROUP_STYLES)]
        dumped = self._generation_parameters.model_dump()
        dumped["group_style"] = nxt
        dumped["preset"] = "CUSTOM"
        self._generation_parameters = GenerationParameters.model_validate(dumped)
        self._sync_generation_widgets_from_parameters()

    def _cycle_allowed_templates(self) -> None:
        current = list(self._generation_parameters.allowed_group_templates)
        if not current:
            current = list(_GROUP_TEMPLATES[:1])
        # Rotate membership: drop first, append next missing in catalog order.
        first = current.pop(0)
        current.append(first)
        # Ensure uniqueness while cycling through all templates over clicks.
        nxt_name = _GROUP_TEMPLATES[
            (_GROUP_TEMPLATES.index(first) + 1) % len(_GROUP_TEMPLATES)
        ]
        if nxt_name not in current:
            current.append(nxt_name)
        dumped = self._generation_parameters.model_dump()
        dumped["allowed_group_templates"] = current
        dumped["preset"] = "CUSTOM"
        try:
            self._generation_parameters = GenerationParameters.model_validate(dumped)
        except ValidationError as exc:
            self._set_banner(_messages_from_validation(exc))
            return
        self._sync_generation_widgets_from_parameters()

    def _sync_generation_widgets_from_parameters(self) -> None:
        p = self._generation_parameters
        self._seed_entry.enterText(str(p.seed))
        self._btn_difficulty["text"] = f"Difficulty: {p.preset}"
        self._btn_friendly_mode["text"] = _FRIENDLY_MODE_LABELS.get(
            p.friendly_mode, p.friendly_mode
        )
        for key, entry in self._advanced_entries.items():
            value = getattr(p, key)
            if isinstance(value, float):
                entry.enterText(f"{value:g}")
            else:
                entry.enterText(str(value))
        self._btn_group_style["text"] = f"Style: {p.group_style}"
        self._templates_label["text"] = "Templates: " + ", ".join(p.allowed_group_templates)
        self._rebuild_roster_rows_from_parameters()
        self._refresh_advanced_visibility()
        self._refresh_param_summary()

    def _refresh_param_summary(self) -> None:
        p = self._generation_parameters
        self._param_summary_label["text"] = (
            f"{p.preset} | map={p.grid_width:g}x{p.grid_height:g} "
            f"objs={p.objective_count} sams={p.sam_count} "
            f"jams={p.jammer_count} friendlies={p.friendly_count_min}-{p.friendly_count_max} "
            f"mode={p.friendly_mode}"
        )
        self._relayout_advanced_card()
        self._relayout_generation_panel()

    def _rebuild_roster_rows_from_parameters(self) -> None:
        for row in self._roster_rows:
            for widget in row.get("widgets", []):
                try:
                    widget.destroy()
                except Exception:
                    pass
        self._roster_rows = []
        self._resize_roster_frame()
        for template in self._generation_parameters.custom_roster:
            self._add_roster_row(
                initial={
                    "count": template.count,
                    "frame_type": template.frame_type,
                    "payload": dict(template.payload),
                },
                sync_params=False,
            )
        self._refresh_roster_slot_previews()

    def _add_roster_row(
        self,
        initial: dict | None = None,
        *,
        sync_params: bool = True,
    ) -> None:
        initial = initial or {
            "count": 1,
            "frame_type": "STRIKE_FIGHTER",
            "payload": {w: (2 if w == "JDAM" else 0) for w in wn_config.WEAPON_CATALOG},
        }
        row_index = len(self._roster_rows)
        x = self._roster_x
        width = self._roster_w
        stack = Stack(
            self._roster_anchor_z - row_index * self._roster_row_h,
            row_h=ROW_H,
            x=x,
        )
        widgets: list = []

        # Row head: how many of this template, which airframe, and remove.
        count_w = width * 0.20
        frame_w = width * 0.52
        remove_w = width - count_w - frame_w - 2.0 * _COL_GAP
        z = stack.row()
        count_label, count_entry = make_field_row(
            self._roster_frame,
            "#",
            pos=(x, 0, z),
            width=count_w,
            scale=TYPE_LABEL,
            label_ratio=0.28,
            entry_chars=_entry_chars(count_w * 0.66, TYPE_LABEL),
            initial=str(initial["count"]),
            command=self._on_roster_edited,
        )
        widgets.extend((count_label, count_entry))
        frame_x = x + count_w + _COL_GAP
        frame_btn = _btn(
            self._roster_frame,
            f"Frame: {initial['frame_type']}",
            (frame_x + frame_w / 2.0, 0, z),
            self._cycle_roster_frame,
            extra_args=[row_index],
            scale=TYPE_LABEL,
            width=frame_w,
        )
        widgets.append(frame_btn)
        remove_btn = _btn(
            self._roster_frame,
            "Remove",
            (x + width - remove_w / 2.0, 0, z),
            self._remove_roster_row,
            extra_args=[row_index],
            scale=TYPE_LABEL,
            style="danger",
            width=remove_w,
        )
        widgets.append(remove_btn)

        payload_entries: dict[str, DirectEntry] = {}
        pay_cols, pay_w = _columns(x, _PAYLOAD_COLUMNS, width=width)
        pay_chars = _entry_chars(pay_w * _PAYLOAD_ENTRY_RATIO, TYPE_MICRO)
        z = stack.z
        for index, weapon in enumerate(wn_config.WEAPON_CATALOG):
            col = index % _PAYLOAD_COLUMNS
            if col == 0:
                z = stack.row()
            wlabel, entry = make_field_row(
                self._roster_frame,
                weapon,
                pos=(pay_cols[col], 0, z),
                width=pay_w,
                scale=TYPE_MICRO,
                label_ratio=_PAYLOAD_LABEL_RATIO,
                entry_chars=pay_chars,
                initial=str(int((initial.get("payload") or {}).get(weapon, 0))),
                command=self._on_roster_edited,
            )
            payload_entries[weapon] = entry
            widgets.extend((wlabel, entry))
        slots_label = make_label(
            self._roster_frame,
            "0 / 0 slots",
            pos=(x + width, 0, stack.z),
            scale=TYPE_MICRO,
            fg=TEXT,
            align=TextNode.ARight,
        )
        widgets.append(slots_label)
        row = {
            "count_entry": count_entry,
            "frame_btn": frame_btn,
            "frame_type": initial["frame_type"],
            "payload_entries": payload_entries,
            "slots_label": slots_label,
            "widgets": widgets,
            "index": row_index,
        }
        self._roster_rows.append(row)
        self._resize_roster_frame()
        self._relayout_generation_panel()
        self._refresh_roster_slot_previews()
        if sync_params:
            self._sync_parameters_from_roster_widgets()

    def _remove_roster_row(self, index: int) -> None:
        if index < 0 or index >= len(self._roster_rows):
            return
        # Rebuild from remaining templates so indices stay consistent.
        templates = self._roster_templates_from_widgets()
        if 0 <= index < len(templates):
            templates.pop(index)
        dumped = self._generation_parameters.model_dump()
        dumped["custom_roster"] = templates
        dumped["friendly_mode"] = "CUSTOM_ROSTER"
        dumped["preset"] = "CUSTOM"
        if not templates:
            dumped["custom_roster"] = [
                {
                    "frame_type": "STRIKE_FIGHTER",
                    "payload": {w: (1 if w == "JDAM" else 0) for w in wn_config.WEAPON_CATALOG},
                    "count": 1,
                }
            ]
        try:
            self._generation_parameters = GenerationParameters.model_validate(dumped)
        except ValidationError as exc:
            self._set_banner(_messages_from_validation(exc))
            return
        self._sync_generation_widgets_from_parameters()

    def _cycle_roster_frame(self, index: int) -> None:
        if index < 0 or index >= len(self._roster_rows):
            return
        row = self._roster_rows[index]
        current = row["frame_type"]
        idx = _FRAME_TYPES.index(current) if current in _FRAME_TYPES else 0
        nxt = _FRAME_TYPES[(idx + 1) % len(_FRAME_TYPES)]
        row["frame_type"] = nxt
        row["frame_btn"]["text"] = f"Frame: {nxt}"
        self._on_roster_edited()

    def _on_roster_edited(self, _text: str = "") -> None:
        self._refresh_roster_slot_previews()
        self._sync_parameters_from_roster_widgets()

    def _refresh_roster_slot_previews(self) -> None:
        for row in self._roster_rows:
            payload: dict[str, int] = {}
            for weapon, entry in row["payload_entries"].items():
                try:
                    payload[weapon] = int(entry.get().strip() or "0")
                except ValueError:
                    payload[weapon] = 0
            row["slots_label"]["text"] = slots_usage_text(payload, row["frame_type"])

    def _roster_templates_from_widgets(self) -> list[dict]:
        templates: list[dict] = []
        for row in self._roster_rows:
            payload = {}
            for weapon, entry in row["payload_entries"].items():
                payload[weapon] = int(entry.get().strip() or "0")
            templates.append(
                {
                    "count": int(row["count_entry"].get().strip() or "1"),
                    "frame_type": row["frame_type"],
                    "payload": payload,
                }
            )
        return templates

    def _sync_parameters_from_roster_widgets(self) -> None:
        try:
            templates = self._roster_templates_from_widgets()
        except ValueError:
            self._set_banner(["Roster counts must be integers."])
            return
        dumped = self._generation_parameters.model_dump()
        dumped["friendly_mode"] = "CUSTOM_ROSTER"
        dumped["custom_roster"] = templates
        dumped["preset"] = "CUSTOM"
        try:
            self._generation_parameters = GenerationParameters.model_validate(dumped)
        except ValidationError as exc:
            self._set_banner(_messages_from_validation(exc))
            return
        self._btn_difficulty["text"] = "Difficulty: CUSTOM"
        self._refresh_param_summary()

    def _parse_generation_parameters(
        self, *, force_custom_preset: bool = False
    ) -> GenerationParameters:
        seed = self._read_seed_from_entry()
        dumped = self._generation_parameters.model_dump()
        dumped["seed"] = seed
        for key, entry in self._advanced_entries.items():
            raw = entry.get().strip()
            if key in (
                "objective_count",
                "sam_count",
                "jammer_count",
                "objective_hp_min",
                "objective_hp_max",
                "friendly_count_min",
                "friendly_count_max",
                "aaa_count",
                "friendly_tank_count",
                "enemy_tank_count",
                "enemy_corvette_count",
                "friendly_carrier_count",
            ):
                dumped[key] = int(raw)
            else:
                dumped[key] = float(raw)
        dumped["group_style"] = self._generation_parameters.group_style
        dumped["allowed_group_templates"] = list(
            self._generation_parameters.allowed_group_templates
        )
        dumped["friendly_mode"] = self._generation_parameters.friendly_mode
        if dumped["friendly_mode"] == "CUSTOM_ROSTER":
            dumped["custom_roster"] = self._roster_templates_from_widgets()
        else:
            dumped["custom_roster"] = []
        if force_custom_preset:
            dumped["preset"] = "CUSTOM"
        return GenerationParameters.model_validate(dumped)

    def _generate_clicked(self) -> None:
        if self._generation_in_progress:
            return
        self._clear_pending_generation()
        try:
            params = self._parse_generation_parameters()
        except (ValidationError, ValueError) as exc:
            if isinstance(exc, ValidationError):
                self._set_banner(_messages_from_validation(exc))
            else:
                self._set_banner([str(exc)])
            return
        self._generation_parameters = params
        self._sync_generation_widgets_from_parameters()
        if self._draft_dirty or self._draft_has_content():
            self._pending_generation_action = "REPLACE"
            self._pending_generation_result = None
            self._set_banner(
                [
                    "Generate will replace the current draft; Save first or confirm Replace."
                ]
            )
            self._refresh_pending_buttons()
            return
        self._run_generation(params)

    def _confirm_replace_generation(self) -> None:
        if self._pending_generation_action != "REPLACE":
            return
        self._clear_pending_generation()
        try:
            params = self._parse_generation_parameters()
        except (ValidationError, ValueError) as exc:
            if isinstance(exc, ValidationError):
                self._set_banner(_messages_from_validation(exc))
            else:
                self._set_banner([str(exc)])
            return
        self._generation_parameters = params
        self._run_generation(params)

    def _confirm_use_anyway(self) -> None:
        if (
            self._pending_generation_action != "USE_ANYWAY"
            or self._pending_generation_result is None
        ):
            return
        result = self._pending_generation_result
        if self._pending_undo_snapshot is not None:
            self._generation_undo_snapshot = self._pending_undo_snapshot
        self._clear_pending_generation()
        self._install_generation_result(result)

    def _cancel_pending_generation(self) -> None:
        self._clear_pending_generation()
        self._set_banner([])

    def _clear_pending_generation(self) -> None:
        self._pending_generation_result = None
        self._pending_generation_action = None
        self._pending_undo_snapshot = None
        self._refresh_pending_buttons()

    def _refresh_pending_buttons(self) -> None:
        action = self._pending_generation_action
        if action == "REPLACE":
            self._btn_confirm_replace.show()
            self._btn_use_anyway.hide()
            self._btn_cancel_pending.show()
        elif action == "USE_ANYWAY":
            self._btn_confirm_replace.hide()
            self._btn_use_anyway.show()
            self._btn_cancel_pending.show()
        else:
            self._btn_confirm_replace.hide()
            self._btn_use_anyway.hide()
            self._btn_cancel_pending.hide()

    def _run_generation(self, params: GenerationParameters) -> None:
        # Synchronous: Phase 32 generate_scenario stays well under
        # _SYNC_GENERATION_BUDGET_MS; no worker thread.
        self._generation_in_progress = True
        prior_draft = copy.deepcopy(self.scenario_draft)
        snapshot = self._capture_snapshot()
        try:
            result = generate_scenario(params)
        except (GenerationError, ValidationError, ValueError) as exc:
            self.scenario_draft = prior_draft
            self._generation_in_progress = False
            if isinstance(exc, ValidationError):
                self._set_banner(_messages_from_validation(exc))
            elif isinstance(exc, GenerationError):
                self._set_banner([str(exc)])
            else:
                self._set_banner([str(exc)])
            self.rebuild_scene()
            self._refresh_entity_list()
            return
        self._generation_in_progress = False

        if params.friendly_mode == "AUTO_BALANCED" and not result.feasibility.feasible:
            # Should already raise inside generate_scenario; belt-and-suspenders.
            self.scenario_draft = prior_draft
            self._set_banner(
                ["Auto-balanced generation was infeasible; draft unchanged."]
                + [i.message for i in result.feasibility.issues[:3]]
            )
            self.rebuild_scene()
            self._refresh_entity_list()
            return

        if (
            params.friendly_mode in ("RANDOM_LEGAL", "CUSTOM_ROSTER")
            and not result.feasibility.feasible
        ):
            self._pending_generation_result = result
            self._pending_generation_action = "USE_ANYWAY"
            # Keep the pre-generate snapshot on the pending result path; install
            # assigns _generation_undo_snapshot only when the draft changes.
            self._pending_undo_snapshot = snapshot
            issues = [i.message for i in result.feasibility.issues if i.severity == "ERROR"]
            self._set_banner(
                ["Generated scenario is statically infeasible. Use Anyway to install."]
                + (issues[:2] or ["See Details for issues."])
            )
            self._generation_report = result.feasibility
            self._refresh_pending_buttons()
            self._refresh_generation_status()
            return

        self._generation_undo_snapshot = snapshot
        self._install_generation_result(result)

    def _install_generation_result(self, result: GenerationResult) -> None:
        scenario = result.scenario
        self.scenario_draft = scenario.model_dump()
        self._normalize_draft_plain_dicts()
        self._generation_parameters = result.parameters
        self._generation_report = result.feasibility
        self._selected = None
        self._draft_dirty = True
        self.camera = OrthoEditorCamera(
            self.base, scenario.grid_width, scenario.grid_height
        )
        self._sync_generation_widgets_from_parameters()
        self.rebuild_scene()
        self._refresh_entity_list()
        self._set_banner([])
        self._refresh_generation_status()
        self._refresh_undo_button()

    def _capture_snapshot(self) -> EditorSnapshot:
        selected_id = None
        selected_kind = None
        if self._selected is not None:
            selected_kind = self._kind_of(self._selected)
            selected_id = self._id_of(selected_kind, self._selected)
        return EditorSnapshot(
            scenario_draft=copy.deepcopy(self.scenario_draft),
            path=self._path,
            selected_id=selected_id,
            selected_kind=selected_kind,
            generation_parameters=self._generation_parameters.model_copy(deep=True),
            generation_report=(
                self._generation_report.model_copy(deep=True)
                if self._generation_report is not None
                else None
            ),
            draft_dirty=self._draft_dirty,
            camera_center_x=self.camera.center_x,
            camera_center_y=self.camera.center_y,
            camera_zoom=self.camera.zoom,
        )

    def _undo_generation(self) -> None:
        snap = self._generation_undo_snapshot
        if snap is None:
            return
        self.scenario_draft = copy.deepcopy(snap.scenario_draft)
        self._path = snap.path
        self._path_entry.enterText(snap.path)
        self._generation_parameters = snap.generation_parameters.model_copy(deep=True)
        self._generation_report = (
            snap.generation_report.model_copy(deep=True)
            if snap.generation_report is not None
            else None
        )
        self._draft_dirty = snap.draft_dirty
        self._selected = None
        self._generation_undo_snapshot = None
        self._clear_pending_generation()
        self.camera = OrthoEditorCamera(
            self.base,
            self.scenario_draft["grid_width"],
            self.scenario_draft["grid_height"],
        )
        self.camera.center_x = snap.camera_center_x
        self.camera.center_y = snap.camera_center_y
        self.camera.zoom = snap.camera_zoom
        self.camera.apply()
        if snap.selected_id is not None and snap.selected_kind is not None:
            for kind, ent in self._all_entities():
                if kind == snap.selected_kind and self._id_of(kind, ent) == snap.selected_id:
                    self.open_property_form(ent)
                    break
        self._sync_generation_widgets_from_parameters()
        self.rebuild_scene()
        self._refresh_entity_list()
        self._set_banner([])
        self._refresh_generation_status()
        self._refresh_undo_button()

    def _refresh_undo_button(self) -> None:
        if self._generation_undo_snapshot is None:
            self._btn_undo_generation.hide()
        else:
            self._btn_undo_generation.show()

    def _refresh_generation_status(self) -> None:
        meta = self._generation_metadata_dict()
        if meta is None:
            self._gen_meta_label["text"] = "Manual scenario"
        elif meta.get("modified_after_generation"):
            self._gen_meta_label["text"] = (
                f"Generated then manually modified: seed {meta.get('seed')}"
            )
        else:
            self._gen_meta_label["text"] = (
                f"Generated: {meta.get('preset')}, seed {meta.get('seed')}"
            )

        report = self._generation_report
        draft = self.scenario_draft
        counts = (
            f"jets={len(draft['jets'])} objs={len(draft['targets'])} "
            f"SAMs={len(draft['sam_sites'])} jammers={len(draft['jammers'])}"
        )
        if report is None:
            if meta is not None:
                self._gen_status_label["text"] = (
                    f"seed {meta.get('seed')} | {counts} | (no live report)"
                )
                self._gen_details_label["text"] = ""
            else:
                self._gen_status_label["text"] = "No generation yet."
                self._gen_details_label["text"] = ""
            self._relayout_generation_panel()
            return

        if report.feasible:
            feasibility = "Statically feasible"
        else:
            n_err = sum(1 for i in report.issues if i.severity == "ERROR")
            n_warn = sum(1 for i in report.issues if i.severity == "WARNING")
            feasibility = f"{n_err} error(s), {n_warn} warning(s)"
        first = report.issues[0].message if report.issues else ""
        self._gen_status_label["text"] = (
            f"seed {self._generation_parameters.seed} | score "
            f"{report.challenge_score:.0f} | {counts} | {feasibility}"
            + (f" | {first}" if first else "")
        )
        detail_lines = [f"[{i.severity}] {i.code}: {i.message}" for i in report.issues]
        self._gen_details_label["text"] = "\n".join(detail_lines[:12]) or "(no issues)"
        self._relayout_generation_panel()

