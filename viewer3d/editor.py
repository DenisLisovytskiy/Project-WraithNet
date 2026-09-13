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
from panda3d.core import MouseButton, OrthographicLens, TextNode
from pydantic import ValidationError

from viewer3d.cameras import _camera_node
from viewer3d.scene import build_static_scene, jet_altitude, sync_static_unit_glyphs
from viewer3d.ui_chrome import (
    CYAN,
    RED,
    TEXT,
    TEXT_MUTED,
    make_button,
    make_entry,
    make_header,
    make_label,
    make_panel,
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


def _btn(parent, text, pos, command, extra_args=None, scale=0.034, style="default"):
    return make_button(
        parent,
        text,
        pos,
        command,
        extra_args=extra_args,
        scale=scale,
        style=style,
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
        parent = getattr(self.base, "a2dTopLeft", None) or getattr(
            self.base, "aspect2d", None
        )
        self._panel = make_panel(
            parent, frame_size=(0.02, 0.92, -1.85, -0.02)
        )
        make_header(
            self._panel,
            "[ SCENARIO EDITOR ]",
            pos=(0.08, 0, -0.08),
            scale=0.040,
        )
        self._btn_add_jet = _btn(
            self._panel, "Add Jet", (0.22, 0, -0.16), self.begin_placement, ["jet"]
        )
        self._btn_add_target = _btn(
            self._panel, "Add Target", (0.52, 0, -0.16), self.begin_placement, ["target"]
        )
        self._btn_add_sam = _btn(
            self._panel, "Add SAM", (0.22, 0, -0.24), self.begin_placement, ["sam_site"]
        )
        self._btn_add_aaa = _btn(
            self._panel, "Add AAA", (0.52, 0, -0.24), self.begin_placement, ["aaa_site"]
        )
        self._btn_add_jammer = _btn(
            self._panel, "Add Jammer", (0.22, 0, -0.32), self.begin_placement, ["jammer"]
        )
        self._btn_add_tank = _btn(
            self._panel, "Add Tank", (0.52, 0, -0.32), self.begin_placement, ["tank"]
        )
        self._btn_add_carrier = _btn(
            self._panel, "Add Carrier", (0.22, 0, -0.40), self.begin_placement, ["carrier"]
        )
        self._btn_add_corvette = _btn(
            self._panel, "Add Corvette", (0.52, 0, -0.40), self.begin_placement, ["corvette"]
        )
        self._btn_home_base = _btn(
            self._panel, "Place Home Base", (0.22, 0, -0.48), self.begin_placement, ["home_base"]
        )
        self._btn_edit_terrain = _btn(
            self._panel, "Edit Terrain", (0.52, 0, -0.48), self.enter_terrain_mode
        )

        self._terrain_panel = make_panel(
            self._panel,
            frame_size=(0.04, 0.88, -0.92, -0.08),
            nested=True,
        )
        make_header(
            self._terrain_panel,
            "[ TERRAIN MODE ]",
            pos=(0.08, 0, -0.14),
            scale=0.034,
        )
        self._btn_brush = _btn(
            self._terrain_panel,
            "Brush: PLAINS",
            (0.30, 0, -0.22),
            self._cycle_terrain_brush,
            scale=0.032,
        )
        self._btn_radius = _btn(
            self._terrain_panel,
            "Radius: 1",
            (0.68, 0, -0.22),
            self._cycle_brush_radius,
            scale=0.032,
        )
        self._btn_city_height = _btn(
            self._terrain_panel,
            "City Ht: Random",
            (0.30, 0, -0.30),
            self._cycle_city_height_mode,
            scale=0.030,
        )
        self._fixed_height_entry = make_entry(
            self._terrain_panel,
            pos=(0.72, 0, -0.30),
            scale=0.028,
            width=5,
            initial="10",
        )
        self._btn_fill = _btn(
            self._terrain_panel, "Fill Map", (0.22, 0, -0.38), self._fill_terrain_clicked, scale=0.030
        )
        self._btn_gen_terrain = _btn(
            self._terrain_panel,
            "Generate Terrain",
            (0.58, 0, -0.38),
            self._toggle_terrain_gen_panel,
            scale=0.028,
        )
        self._btn_terrain_undo = _btn(
            self._terrain_panel, "Undo", (0.18, 0, -0.46), self._terrain_undo, scale=0.030
        )
        self._btn_terrain_redo = _btn(
            self._terrain_panel, "Redo", (0.38, 0, -0.46), self._terrain_redo, scale=0.030
        )
        self._btn_entity_mode = _btn(
            self._terrain_panel,
            "Entity Mode",
            (0.66, 0, -0.46),
            self.enter_entity_mode,
            scale=0.030,
        )
        self._btn_relocate = _btn(
            self._terrain_panel,
            "Relocate Invalid",
            (0.40, 0, -0.54),
            self._relocate_invalid_clicked,
            scale=0.028,
        )
        self._btn_relocate_confirm = _btn(
            self._terrain_panel,
            "Confirm Relocate",
            (0.40, 0, -0.62),
            self._confirm_relocate,
            scale=0.028,
            style="primary",
        )
        self._btn_relocate_confirm.hide()
        self._terrain_gen_frame = make_panel(
            self._terrain_panel,
            frame_size=(0.06, 0.86, -0.90, -0.68),
            nested=True,
        )
        make_label(
            self._terrain_gen_frame,
            "water / forest / city fractions",
            pos=(0.08, 0, -0.72),
            scale=0.024,
        )
        self._terrain_water_entry = make_entry(
            self._terrain_gen_frame,
            pos=(0.20, 0, -0.78),
            scale=0.026,
            width=5,
            initial="0.20",
        )
        self._terrain_forest_entry = make_entry(
            self._terrain_gen_frame,
            pos=(0.42, 0, -0.78),
            scale=0.026,
            width=5,
            initial="0.20",
        )
        self._terrain_city_entry = make_entry(
            self._terrain_gen_frame,
            pos=(0.64, 0, -0.78),
            scale=0.026,
            width=5,
            initial="0.10",
        )
        self._btn_run_terrain_gen = _btn(
            self._terrain_gen_frame,
            "Run Generate",
            (0.40, 0, -0.86),
            self._generate_terrain_clicked,
            scale=0.028,
            style="primary",
        )
        self._terrain_gen_frame.hide()
        self._terrain_panel.hide()

        self._list_anchor_z = -0.60
        self._list_max_visible = 5
        make_header(
            self._panel,
            "[ ENTITIES ]",
            pos=(0.08, 0, -0.545),
            scale=0.032,
        )
        self._hint = OnscreenText(
            text="Click Add, then click the map to place.",
            parent=self._panel,
            pos=(0.08, -0.505),
            scale=0.020,
            fg=TEXT_MUTED,
            align=TextNode.ALeft,
            mayChange=True,
        )
        self._list_overflow_label = make_label(
            self._panel,
            "",
            pos=(0.10, 0, -0.60),
            scale=0.022,
            fg=TEXT_MUTED,
        )
        self._list_overflow_label.hide()

        # Tall properties card: jet payload + flares need ~0.7 of vertical space.
        self._form_top_z = -0.88
        self._form_bottom_z = -1.58
        self._form_content_top_z = -0.94
        self._form_frame = make_panel(
            self._panel,
            frame_size=(0.06, 0.86, self._form_bottom_z, self._form_top_z),
            nested=True,
        )
        make_header(
            self._form_frame,
            "[ PROPERTIES ]",
            pos=(0.08, 0, self._form_top_z - 0.04),
            scale=0.030,
        )
        self._id_label = make_label(
            self._form_frame,
            "ID",
            pos=(0.10, 0, self._form_content_top_z),
            scale=0.028,
            fg=CYAN,
        )
        self._id_entry = make_entry(
            self._form_frame,
            pos=(0.40, 0, self._form_content_top_z),
            scale=0.028,
            width=14,
        )
        self._field_a_entry = make_entry(
            self._form_frame,
            pos=(0.40, 0, self._form_content_top_z - 0.05),
            scale=0.028,
            width=14,
        )
        self._field_a_label = make_label(
            self._form_frame,
            "JDAM",
            pos=(0.10, 0, self._form_content_top_z - 0.05),
            scale=0.026,
            fg=CYAN,
        )
        self._field_b_entry = make_entry(
            self._form_frame,
            pos=(0.40, 0, self._form_content_top_z - 0.10),
            scale=0.028,
            width=14,
        )
        self._field_b_label = make_label(
            self._form_frame,
            "AIM9X",
            pos=(0.10, 0, self._form_content_top_z - 0.10),
            scale=0.026,
            fg=CYAN,
        )
        self._payload_entries: dict[str, DirectEntry] = {}
        self._payload_labels: dict[str, DirectLabel] = {}
        weapons = list(wn_config.WEAPON_CATALOG)
        for weapon in weapons:
            label = make_label(
                self._form_frame,
                weapon,
                pos=(0.10, 0, self._form_content_top_z),
                scale=0.022,
                fg=CYAN,
            )
            entry = make_entry(
                self._form_frame,
                pos=(0.28, 0, self._form_content_top_z),
                scale=0.024,
                width=5,
                initial="0",
                command=self._on_payload_typed,
            )
            label.hide()
            entry.hide()
            self._payload_labels[weapon] = label
            self._payload_entries[weapon] = entry
        self._frame_btn = _btn(
            self._form_frame,
            "Frame: STRIKE_FIGHTER",
            (0.46, 0, self._form_content_top_z),
            self._cycle_frame_type,
            scale=0.026,
        )
        self._slots_label = make_label(
            self._form_frame,
            "0 / 7 slots",
            pos=(0.10, 0, self._form_content_top_z),
            scale=0.024,
            fg=TEXT,
        )
        self._btn_apply = _btn(
            self._form_frame,
            "Apply",
            (0.20, 0, self._form_content_top_z),
            self._apply_form,
            scale=0.026,
            style="primary",
        )
        self._btn_delete = _btn(
            self._form_frame,
            "Delete",
            (0.42, 0, self._form_content_top_z),
            self._delete_selected,
            scale=0.026,
            style="danger",
        )
        self._btn_hold = _btn(
            self._form_frame,
            "Hold: ON",
            (0.68, 0, self._form_content_top_z),
            self._toggle_hold_position,
            scale=0.024,
        )
        self._btn_add_route = _btn(
            self._form_frame,
            "Add Route Point",
            (0.68, 0, self._form_content_top_z),
            self._arm_route_point,
            scale=0.022,
        )
        self._field_a_entry["command"] = self._on_payload_typed
        self._field_b_entry["command"] = self._on_payload_typed
        self._frame_btn.hide()
        self._slots_label.hide()
        self._btn_hold.hide()
        self._btn_add_route.hide()
        self._layout_property_form("jammer")

        self._path_entry = make_entry(
            self._panel,
            pos=(0.48, 0, -1.66),
            scale=0.028,
            width=18,
            initial=self._path,
        )
        make_label(
            self._panel,
            "Path",
            pos=(0.08, 0, -1.66),
            scale=0.028,
            fg=CYAN,
        )
        self._btn_save = _btn(self._panel, "Save", (0.18, 0, -1.74), self._save_clicked, scale=0.030)
        self._btn_load = _btn(self._panel, "Load", (0.40, 0, -1.74), self._load_clicked, scale=0.030)
        self._btn_preview = _btn(
            self._panel, "Preview", (0.66, 0, -1.74), self._preview_clicked, scale=0.030
        )
        self._btn_launch = _btn(
            self._panel, "Launch", (0.40, 0, -1.84), self._launch_clicked, style="primary", scale=0.032
        )

        bl = getattr(self.base, "a2dBottomLeft", None) or parent
        self._banner = OnscreenText(
            text="",
            parent=bl,
            pos=(0.08, 0.08),
            scale=0.032,
            fg=RED,
            align=TextNode.ALeft,
            mayChange=True,
            wordwrap=32,
        )
        self._banner.hide()
        self._back_btn = _btn(
            getattr(self.base, "a2dTopRight", None) or parent,
            "Back to Editor",
            (-0.28, 0, -0.08),
            self._back_to_editing,
            style="primary",
        )
        self._back_btn.hide()
        self._build_generate_panel()

    def _build_generate_panel(self) -> None:
        """Right-side Quick Generate panel (separate from the entity panel)."""
        parent = getattr(self.base, "a2dTopRight", None) or getattr(
            self.base, "aspect2d", None
        )
        self._gen_panel = make_panel(
            parent, frame_size=(-0.92, -0.02, -1.90, -0.02)
        )
        make_header(
            self._gen_panel,
            "[ QUICK GENERATE ]",
            pos=(-0.88, 0, -0.08),
            scale=0.038,
        )
        self._btn_difficulty = _btn(
            self._gen_panel,
            "Difficulty: STANDARD",
            (-0.46, 0, -0.16),
            self._cycle_difficulty,
            scale=0.032,
        )
        self._seed_entry = make_entry(
            self._gen_panel,
            pos=(-0.55, 0, -0.24),
            scale=0.028,
            width=16,
            initial="0",
        )
        make_label(
            self._gen_panel,
            "Seed",
            pos=(-0.88, 0, -0.24),
            scale=0.028,
            fg=CYAN,
        )
        self._btn_new_seed = _btn(
            self._gen_panel, "New Seed", (-0.22, 0, -0.24), self._new_seed, scale=0.028
        )
        self._btn_friendly_mode = _btn(
            self._gen_panel,
            "AUTO-BALANCED",
            (-0.46, 0, -0.32),
            self._cycle_friendly_mode,
            scale=0.028,
        )
        self._btn_generate = _btn(
            self._gen_panel,
            "Generate",
            (-0.72, 0, -0.40),
            self._generate_clicked,
            scale=0.032,
            style="primary",
        )
        self._btn_advanced = _btn(
            self._gen_panel,
            "Advanced…",
            (-0.42, 0, -0.40),
            self._toggle_advanced,
            scale=0.028,
        )
        self._btn_undo_generation = _btn(
            self._gen_panel,
            "Undo Generation",
            (-0.18, 0, -0.40),
            self._undo_generation,
            scale=0.026,
        )
        self._btn_confirm_replace = _btn(
            self._gen_panel,
            "Replace",
            (-0.72, 0, -0.48),
            self._confirm_replace_generation,
            scale=0.028,
            style="primary",
        )
        self._btn_use_anyway = _btn(
            self._gen_panel,
            "Use Anyway",
            (-0.42, 0, -0.48),
            self._confirm_use_anyway,
            scale=0.028,
        )
        self._btn_cancel_pending = _btn(
            self._gen_panel,
            "Cancel",
            (-0.18, 0, -0.48),
            self._cancel_pending_generation,
            scale=0.028,
            style="danger",
        )
        self._btn_confirm_replace.hide()
        self._btn_use_anyway.hide()
        self._btn_cancel_pending.hide()

        self._gen_meta_label = make_label(
            self._gen_panel,
            "Manual scenario",
            pos=(-0.88, 0, -0.56),
            scale=0.026,
            fg=TEXT,
        )
        self._gen_status_label = make_label(
            self._gen_panel,
            "No generation yet.",
            pos=(-0.88, 0, -0.62),
            scale=0.024,
        )
        self._btn_status_details = _btn(
            self._gen_panel,
            "Details",
            (-0.22, 0, -0.70),
            self._toggle_status_details,
            scale=0.026,
        )
        self._gen_details_label = make_label(
            self._gen_panel,
            "",
            pos=(-0.88, 0, -0.76),
            scale=0.022,
        )
        self._gen_details_label.hide()

        self._advanced_frame = make_panel(
            self._gen_panel,
            frame_size=(-0.90, -0.04, -1.88, -0.82),
            nested=True,
        )
        self._advanced_entries: dict[str, DirectEntry] = {}
        adv_fields = [
            ("objective_count", "Objectives", -0.90),
            ("sam_count", "SAMs", -0.96),
            ("jammer_count", "Jammers", -1.02),
            ("aaa_count", "AAA", -1.08),
            ("friendly_tank_count", "Fr. tanks", -1.14),
            ("enemy_tank_count", "En. tanks", -1.20),
            ("enemy_corvette_count", "Corvettes", -1.26),
            ("friendly_carrier_count", "Carriers", -1.32),
            ("objective_hp_min", "HP min", -1.38),
            ("objective_hp_max", "HP max", -1.44),
            ("threat_overlap", "Threat ovlp", -1.50),
            ("base_safety_radius", "Base safety", -1.56),
            ("entity_min_separation", "Entity sep", -1.62),
            ("friendly_count_min", "Fr. jets min", -1.68),
            ("friendly_count_max", "Fr. jets max", -1.74),
            ("friendly_reserve_factor", "Reserve", -1.80),
        ]
        for key, label, z in adv_fields:
            make_label(
                self._advanced_frame,
                label,
                pos=(-0.88, 0, z),
                scale=0.022,
                fg=CYAN,
            )
            entry = make_entry(
                self._advanced_frame,
                pos=(-0.42, 0, z),
                scale=0.022,
                width=10,
                command=self._on_advanced_field_edited,
            )
            self._advanced_entries[key] = entry
        self._btn_group_style = _btn(
            self._advanced_frame,
            "Style: MIXED",
            (-0.42, 0, -1.84),
            self._cycle_group_style,
            scale=0.022,
        )
        self._templates_label = make_label(
            self._advanced_frame,
            "Templates: …",
            pos=(-0.88, 0, -1.88),
            scale=0.018,
        )
        self._btn_cycle_templates = _btn(
            self._advanced_frame,
            "Cycle templates",
            (-0.22, 0, -1.88),
            self._cycle_allowed_templates,
            scale=0.018,
        )
        self._param_summary_label = make_label(
            self._advanced_frame,
            "",
            pos=(-0.88, 0, -0.86),
            scale=0.018,
        )

        self._roster_frame = make_panel(
            self._gen_panel,
            frame_size=(-0.90, -0.04, -1.88, -0.82),
            nested=True,
        )
        make_header(
            self._roster_frame,
            "[ CUSTOM ROSTER ]",
            pos=(-0.88, 0, -0.86),
            scale=0.024,
        )
        self._btn_add_roster_row = _btn(
            self._roster_frame,
            "Add roster row",
            (-0.46, 0, -0.92),
            self._add_roster_row,
            scale=0.024,
        )
        self._roster_anchor_z = -1.00
        self._advanced_frame.hide()
        self._roster_frame.hide()
        self._refresh_undo_button()
        self._refresh_pending_buttons()

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
        """Stack visible property widgets so jet payload never overlaps flares/actions."""
        z = self._form_content_top_z
        row = 0.050
        compact = 0.046

        def _place(widget, x: float, zz: float) -> None:
            widget.setPos(x, 0, zz)

        _place(self._id_label, 0.10, z)
        _place(self._id_entry, 0.38, z)
        z -= row

        if kind == "jet":
            _place(self._field_a_label, 0.10, z)
            _place(self._field_a_entry, 0.38, z)
            z -= row
            _place(self._field_b_label, 0.10, z)
            _place(self._field_b_entry, 0.38, z)
            z -= row + 0.010
            weapons = list(self._payload_entries)
            for i, weapon in enumerate(weapons):
                r, c = divmod(i, 2)
                zz = z - compact * r
                x_label = 0.10 if c == 0 else 0.48
                x_entry = 0.28 if c == 0 else 0.66
                _place(self._payload_labels[weapon], x_label, zz)
                _place(self._payload_entries[weapon], x_entry, zz)
            z -= compact * max(1, (len(weapons) + 1) // 2) + 0.014
            _place(self._frame_btn, 0.46, z)
            z -= 0.054
            _place(self._slots_label, 0.10, z)
            z -= 0.054
            _place(self._btn_apply, 0.20, z)
            _place(self._btn_delete, 0.44, z)
            return

        if kind in ("target", "sam_site", "aaa_site", "tank", "carrier", "corvette"):
            _place(self._field_a_label, 0.10, z)
            _place(self._field_a_entry, 0.38, z)
            z -= row
            _place(self._field_b_label, 0.10, z)
            _place(self._field_b_entry, 0.38, z)
            z -= row + 0.010
            if kind in ("tank", "carrier", "corvette"):
                _place(self._frame_btn, 0.46, z)
                z -= 0.054
            if kind in ("carrier", "corvette"):
                _place(self._btn_hold, 0.22, z)
                _place(self._btn_add_route, 0.58, z)
                z -= 0.054
            elif kind == "tank" and not self._btn_add_route.isHidden():
                _place(self._btn_add_route, 0.46, z)
                z -= 0.054
            _place(self._btn_apply, 0.20, z)
            _place(self._btn_delete, 0.44, z)
            return

        # jammer / home_base / empty selection: ID + actions only
        _place(self._btn_apply, 0.20, z)
        _place(self._btn_delete, 0.44, z)

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
        for widget in (*self._payload_entries.values(), *self._payload_labels.values()):
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
                self._panel,
                label,
                (0.46, 0, z),
                self.open_property_form,
                extra_args=[ent],
                scale=0.028,
                style="ghost",
            )
            self._list_buttons.append(btn)
            z -= 0.048
        overflow = len(entities) - len(visible)
        if overflow > 0:
            self._list_overflow_label["text"] = f"+{overflow} more on map"
            self._list_overflow_label.setPos(0.10, 0, z)
            self._list_overflow_label.show()

    def _set_banner(self, messages: list[str]) -> None:
        self.banner_messages = list(messages)
        if not messages:
            self._banner.setText("")
            self._banner.hide()
            return
        self._banner.setText("\n".join(messages))
        self._banner.show()

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
        if mx < -0.45 or mx > 0.45:
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
        if terrain:
            self._terrain_panel.show()
            self._hint.setText("Drag on the map to paint terrain. Esc cancels stroke.")
        else:
            self._terrain_panel.hide()
            self._hint.setText("Click Add, then click the map to place.")
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

    def _refresh_advanced_visibility(self) -> None:
        mode = self._generation_parameters.friendly_mode
        if self._advanced_expanded:
            self._advanced_frame.show()
            self._btn_advanced["text"] = "Advanced ▲"
        else:
            self._advanced_frame.hide()
            self._btn_advanced["text"] = "Advanced…"
        if mode == "CUSTOM_ROSTER":
            self._roster_frame.show()
            if self._advanced_expanded:
                # Roster shares the advanced region; keep both usable by
                # stacking roster under compact controls when advanced open.
                self._roster_frame.set_pos(0, 0, 0)
        else:
            self._roster_frame.hide()

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
            f"{p.preset} | objs={p.objective_count} sams={p.sam_count} "
            f"jams={p.jammer_count} friendlies={p.friendly_count_min}-{p.friendly_count_max} "
            f"mode={p.friendly_mode}"
        )

    def _rebuild_roster_rows_from_parameters(self) -> None:
        for row in self._roster_rows:
            for widget in row.get("widgets", []):
                try:
                    widget.destroy()
                except Exception:
                    pass
        self._roster_rows = []
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
        z = self._roster_anchor_z - 0.22 * len(self._roster_rows)
        widgets: list = []
        count_label = make_label(
            self._roster_frame,
            "#",
            pos=(-0.88, 0, z),
            scale=0.020,
            fg=CYAN,
        )
        widgets.append(count_label)
        count_entry = make_entry(
            self._roster_frame,
            pos=(-0.72, 0, z),
            scale=0.020,
            width=4,
            initial=str(initial["count"]),
            command=self._on_roster_edited,
        )
        widgets.append(count_entry)
        row_index = len(self._roster_rows)
        frame_btn = _btn(
            self._roster_frame,
            f"Frame: {initial['frame_type']}",
            (-0.40, 0, z),
            self._cycle_roster_frame,
            extra_args=[row_index],
            scale=0.018,
        )
        widgets.append(frame_btn)
        payload_entries: dict[str, DirectEntry] = {}
        weapons = list(wn_config.WEAPON_CATALOG)
        for i, weapon in enumerate(weapons):
            wz = z - 0.05 - 0.04 * i
            wlabel = make_label(
                self._roster_frame,
                weapon,
                pos=(-0.88, 0, wz),
                scale=0.016,
                fg=CYAN,
            )
            widgets.append(wlabel)
            entry = make_entry(
                self._roster_frame,
                pos=(-0.55, 0, wz),
                scale=0.018,
                width=5,
                initial=str(int((initial.get("payload") or {}).get(weapon, 0))),
                command=self._on_roster_edited,
            )
            payload_entries[weapon] = entry
            widgets.append(entry)
        slots_label = make_label(
            self._roster_frame,
            "0 / 0 slots",
            pos=(-0.30, 0, z - 0.05),
            scale=0.016,
            fg=TEXT,
        )
        widgets.append(slots_label)
        remove_btn = _btn(
            self._roster_frame,
            "Remove",
            (-0.14, 0, z),
            self._remove_roster_row,
            extra_args=[row_index],
            scale=0.016,
            style="danger",
        )
        widgets.append(remove_btn)
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

