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

import math

from direct.gui.DirectGui import DirectButton, DirectEntry, DirectFrame, DirectLabel
from direct.gui.OnscreenText import OnscreenText
from panda3d.core import MouseButton, OrthographicLens, TextNode, TransparencyAttrib
from pydantic import ValidationError

from viewer3d.cameras import _camera_node
from viewer3d.scene import build_static_scene, jet_altitude
from wraithnet import config as wn_config
from wraithnet.scenario_io import load_scenario, save_scenario
from wraithnet.scenario_schema import ScenarioConfig
from wraithnet.trace_builders import catalog_header_fields
from wraithnet.trace_schemas import TraceHeader

CYAN = (0.45, 0.88, 1.0, 1.0)
PANEL_BG = (0.02, 0.07, 0.11, 0.78)
RED = (1.0, 0.35, 0.32, 1.0)
BTN_BG = (0.08, 0.18, 0.24, 0.95)
_VALUE_ERROR_PREFIX = "Value error, "
_FRAME_TYPES = list(wn_config.JET_FRAME_CATALOG)


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
    targets = list(config.targets) + list(config.sam_sites)
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
        target_hp_required={t.target_id: t.hp_required for t in targets},
        sam_site_ids=[s.target_id for s in config.sam_sites],
        jammer_positions={j.jammer_id: tuple(j.position) for j in config.jammers},
        target_positions={t.target_id: tuple(t.position) for t in targets},
        home_base=tuple(config.home_base),
        jet_frame_types={j.agent_id: j.frame_type for j in config.jets},
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


def _btn(parent, text, pos, command, extra_args=None, scale=0.038):
    kwargs = dict(
        parent=parent,
        text=text,
        pos=pos,
        scale=scale,
        command=command,
        frameColor=BTN_BG,
        text_fg=CYAN,
        relief=1,
        pad=(0.15, 0.08),
    )
    if extra_args is not None:
        kwargs["extraArgs"] = extra_args
    return DirectButton(**kwargs)


class ScenarioEditor:
    def __init__(self, base) -> None:
        self.base = base
        # Draft is plain dicts -- not a live ScenarioConfig -- so incomplete
        # placements (e.g. no jets yet) do not fight Pydantic on every click.
        self.scenario_draft: dict = {
            "grid_width": wn_config.GRID_WIDTH,
            "grid_height": wn_config.GRID_HEIGHT,
            "jets": [],
            "targets": [],
            "sam_sites": [],
            "jammers": [],
            "home_base": list(wn_config.DEFAULT_HOME_BASE),
        }
        self.camera = OrthoEditorCamera(
            base, self.scenario_draft["grid_width"], self.scenario_draft["grid_height"]
        )
        self._placement_kind: str | None = None
        self._selected: dict | None = None
        self._handles = None
        self._list_buttons: list = []
        self.banner_messages: list[str] = []
        self._path = "scenarios/custom.json"
        self._build_panels()
        self.rebuild_scene()

    def _build_panels(self) -> None:
        parent = getattr(self.base, "a2dTopLeft", None) or getattr(
            self.base, "aspect2d", None
        )
        self._panel = DirectFrame(
            parent=parent,
            frameColor=PANEL_BG,
            frameSize=(0.02, 0.92, -1.85, -0.02),
            pos=(0, 0, 0),
            relief=None,
        )
        self._panel.set_transparency(TransparencyAttrib.MAlpha)
        DirectLabel(
            parent=self._panel,
            text="[ SCENARIO EDITOR ]",
            pos=(0.08, 0, -0.08),
            scale=0.042,
            text_fg=CYAN,
            text_align=TextNode.ALeft,
            frameColor=(0, 0, 0, 0),
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
        self._btn_add_jammer = _btn(
            self._panel, "Add Jammer", (0.52, 0, -0.24), self.begin_placement, ["jammer"]
        )
        self._btn_home_base = _btn(
            self._panel, "Place Home Base", (0.37, 0, -0.32), self.begin_placement, ["home_base"]
        )

        self._list_anchor_z = -0.42
        DirectLabel(
            parent=self._panel,
            text="[ ENTITIES ]",
            pos=(0.08, 0, -0.40),
            scale=0.034,
            text_fg=CYAN,
            text_align=TextNode.ALeft,
            frameColor=(0, 0, 0, 0),
        )

        self._form_frame = DirectFrame(
            parent=self._panel,
            frameColor=(0.03, 0.09, 0.13, 0.4),
            frameSize=(0.06, 0.86, -1.36, -0.98),
            relief=None,
        )
        DirectLabel(
            parent=self._form_frame,
            text="[ PROPERTIES ]",
            pos=(0.08, 0, -1.02),
            scale=0.032,
            text_fg=CYAN,
            text_align=TextNode.ALeft,
            frameColor=(0, 0, 0, 0),
        )
        self._id_entry = DirectEntry(
            parent=self._form_frame,
            pos=(0.45, 0, -1.08),
            scale=0.035,
            width=12,
            initialText="",
            numLines=1,
            focus=0,
        )
        DirectLabel(
            parent=self._form_frame,
            text="ID",
            pos=(0.10, 0, -1.08),
            scale=0.032,
            text_fg=CYAN,
            text_align=TextNode.ALeft,
            frameColor=(0, 0, 0, 0),
        )
        self._field_a_entry = DirectEntry(
            parent=self._form_frame,
            pos=(0.45, 0, -1.14),
            scale=0.035,
            width=12,
            initialText="",
            numLines=1,
            focus=0,
        )
        self._field_a_label = DirectLabel(
            parent=self._form_frame,
            text="JDAM",
            pos=(0.10, 0, -1.14),
            scale=0.032,
            text_fg=CYAN,
            text_align=TextNode.ALeft,
            frameColor=(0, 0, 0, 0),
        )
        self._field_b_entry = DirectEntry(
            parent=self._form_frame,
            pos=(0.45, 0, -1.20),
            scale=0.035,
            width=12,
            initialText="",
            numLines=1,
            focus=0,
        )
        self._field_b_label = DirectLabel(
            parent=self._form_frame,
            text="AIM9X",
            pos=(0.10, 0, -1.20),
            scale=0.032,
            text_fg=CYAN,
            text_align=TextNode.ALeft,
            frameColor=(0, 0, 0, 0),
        )
        self._frame_btn = _btn(
            self._form_frame, "Frame: STRIKE_FIGHTER", (0.46, 0, -1.26), self._cycle_frame_type
        )
        self._slots_label = DirectLabel(
            parent=self._form_frame,
            text="0 / 7 slots",
            pos=(0.10, 0, -1.32),
            scale=0.030,
            text_fg=CYAN,
            text_align=TextNode.ALeft,
            frameColor=(0, 0, 0, 0),
        )
        self._btn_apply = _btn(
            self._form_frame, "Apply", (0.22, 0, -1.38), self._apply_form
        )
        self._btn_delete = _btn(
            self._form_frame, "Delete", (0.52, 0, -1.38), self._delete_selected
        )
        self._field_a_entry["command"] = self._on_payload_typed
        self._field_b_entry["command"] = self._on_payload_typed
        self._frame_btn.hide()
        self._slots_label.hide()

        self._path_entry = DirectEntry(
            parent=self._panel,
            pos=(0.48, 0, -1.50),
            scale=0.032,
            width=18,
            initialText=self._path,
            numLines=1,
            focus=0,
        )
        DirectLabel(
            parent=self._panel,
            text="Path",
            pos=(0.08, 0, -1.50),
            scale=0.032,
            text_fg=CYAN,
            text_align=TextNode.ALeft,
            frameColor=(0, 0, 0, 0),
        )
        self._btn_save = _btn(self._panel, "Save", (0.18, 0, -1.60), self._save_clicked)
        self._btn_load = _btn(self._panel, "Load", (0.40, 0, -1.60), self._load_clicked)
        self._btn_preview = _btn(
            self._panel, "Preview", (0.66, 0, -1.60), self._preview_clicked
        )
        self._btn_launch = _btn(
            self._panel, "Launch", (0.40, 0, -1.70), self._launch_clicked
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
        self._hint = OnscreenText(
            text="Click Add, then click the map to place.",
            parent=parent,
            pos=(0.08, -0.28),
            scale=0.028,
            fg=CYAN,
            align=TextNode.ALeft,
            mayChange=True,
        )
        self._back_btn = _btn(
            getattr(self.base, "a2dTopRight", None) or parent,
            "Back to editing",
            (-0.28, 0, -0.08),
            self._back_to_editing,
        )
        self._back_btn.hide()

    def hide_panels(self) -> None:
        self._panel.hide()
        self._hint.hide()
        self._banner.hide()
        self._back_btn.show()

    def show_panels(self) -> None:
        self._panel.show()
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
        self._placement_kind = entity_kind
        self._hint.setText(f"Click the map to place a {entity_kind.replace('_', ' ')}.")

    def cancel_placement(self) -> None:
        self._placement_kind = None
        self._hint.setText("Click Add, then click the map to place.")

    def on_ground_clicked(self, world_pos: tuple[float, float]) -> None:
        """Place a new entity, or select the nearest existing one."""
        if self._placement_kind is not None:
            self._place(self._placement_kind, world_pos)
            self._placement_kind = None
            self._hint.setText("Click Add, then click the map to place.")
            return
        nearest = self._nearest(world_pos)
        if nearest is not None:
            self.open_property_form(nearest)

    def _place(self, kind: str, world_pos: tuple[float, float]) -> None:
        pos = (float(world_pos[0]), float(world_pos[1]))
        if kind == "jet":
            ent = {
                "agent_id": self._next_id("Jet_", "agent_id", "jets"),
                "position": pos,
                "payload": {"JDAM": 1, "AIM9X": 0},
                "frame_type": "STRIKE_FIGHTER",
            }
            self.scenario_draft["jets"].append(ent)
        elif kind == "home_base":
            self.scenario_draft["home_base"] = [pos[0], pos[1]]
            self.rebuild_scene()
            self._refresh_entity_list()
            _, errors = self._construct_config()
            self._set_banner(errors or [])
            return
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
        elif kind == "jammer":
            ent = {
                "jammer_id": self._next_id("EW_Jammer_", "jammer_id", "jammers"),
                "position": pos,
            }
            self.scenario_draft["jammers"].append(ent)
        else:
            return
        self.rebuild_scene()
        self._refresh_entity_list()
        self.open_property_form(ent)

    def _next_id(self, prefix: str, id_key: str, bucket: str) -> str:
        existing = {item[id_key] for item in self.scenario_draft[bucket]}
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
            ("jammer", "jammers"),
        ):
            for ent in self.scenario_draft[bucket]:
                out.append((kind, ent))
        return out

    def _id_of(self, kind: str, ent: dict) -> str:
        if kind == "jet":
            return ent["agent_id"]
        if kind == "jammer":
            return ent["jammer_id"]
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
        kind = self._kind_of(entity)
        self._id_entry.enterText(self._id_of(kind, entity))
        if kind == "jet":
            self._field_a_label["text"] = "JDAM"
            self._field_b_label["text"] = "AIM9X"
            payload = entity.get("payload") or {}
            self._field_a_entry.enterText(str(payload.get("JDAM", 0)))
            self._field_b_entry.enterText(str(payload.get("AIM9X", 0)))
            self._field_a_entry.show()
            self._field_b_entry.show()
            self._frame_btn.show()
            self._slots_label.show()
            self._set_frame_button_label(entity.get("frame_type", "STRIKE_FIGHTER"))
            self._refresh_slots_preview()
        elif kind == "target":
            self._field_a_label["text"] = "type"
            self._field_b_label["text"] = "hp"
            self._field_a_entry.enterText(str(entity.get("target_type", "HARDENED_BUNKER")))
            self._field_b_entry.enterText(str(entity.get("hp_required", 1)))
            self._field_a_entry.show()
            self._field_b_entry.show()
            self._frame_btn.hide()
            self._slots_label.hide()
        elif kind == "sam_site":
            self._field_a_label["text"] = "hp"
            self._field_b_label["text"] = ""
            self._field_a_entry.enterText(str(entity.get("hp_required", 1)))
            self._field_b_entry.enterText("")
            self._field_a_entry.show()
            self._field_b_entry.hide()
            self._frame_btn.hide()
            self._slots_label.hide()
        else:
            self._field_a_label["text"] = ""
            self._field_b_label["text"] = ""
            self._field_a_entry.hide()
            self._field_b_entry.hide()
            self._frame_btn.hide()
            self._slots_label.hide()

    def _set_frame_button_label(self, frame_type: str) -> None:
        self._frame_btn["text"] = f"Frame: {frame_type}"

    def set_frame_type(self, frame_type: str) -> None:
        """Test/UI helper: assign a catalog frame to the selected jet."""
        if self._selected is None or self._kind_of(self._selected) != "jet":
            return
        if frame_type not in wn_config.JET_FRAME_CATALOG:
            return
        self._selected["frame_type"] = frame_type
        self._set_frame_button_label(frame_type)
        self._refresh_slots_preview()

    def _cycle_frame_type(self) -> None:
        if self._selected is None or self._kind_of(self._selected) != "jet":
            return
        current = self._selected.get("frame_type", "STRIKE_FIGHTER")
        idx = _FRAME_TYPES.index(current) if current in _FRAME_TYPES else 0
        nxt = _FRAME_TYPES[(idx + 1) % len(_FRAME_TYPES)]
        self.set_frame_type(nxt)

    def _payload_from_form(self) -> dict[str, int]:
        try:
            jdam = int(self._field_a_entry.get().strip() or "0")
            aim = int(self._field_b_entry.get().strip() or "0")
        except ValueError:
            return {}
        return {"JDAM": jdam, "AIM9X": aim}

    def _on_payload_typed(self, _text: str = "") -> None:
        self._refresh_slots_preview()

    def _refresh_slots_preview(self) -> None:
        if self._selected is None or self._kind_of(self._selected) != "jet":
            self._slots_label["text"] = ""
            return
        payload = self._payload_from_form() or (self._selected.get("payload") or {})
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
            try:
                jdam = int(self._field_a_entry.get().strip() or "0")
                aim = int(self._field_b_entry.get().strip() or "0")
            except ValueError:
                self._set_banner(["Payload counts must be integers."])
                return
            entity["payload"] = {"JDAM": jdam, "AIM9X": aim}
            entity["frame_type"] = entity.get("frame_type", "STRIKE_FIGHTER")
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
        else:
            if new_id:
                entity["jammer_id"] = new_id
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
            ("jammers", "jammer_id"),
        ):
            before = len(self.scenario_draft[bucket])
            self.scenario_draft[bucket] = [
                e for e in self.scenario_draft[bucket] if e[key] != entity_id
            ]
            if len(self.scenario_draft[bucket]) != before:
                break
        self._selected = None
        self.rebuild_scene()
        self._refresh_entity_list()
        _, errors = self._construct_config()
        self._set_banner(errors or [])

    def _construct_config(self) -> tuple[ScenarioConfig | None, list[str] | None]:
        data = {
            "grid_width": self.scenario_draft["grid_width"],
            "grid_height": self.scenario_draft["grid_height"],
            "jets": list(self.scenario_draft["jets"]),
            "targets": list(self.scenario_draft["targets"]),
            "sam_sites": list(self.scenario_draft["sam_sites"]),
            "jammers": list(self.scenario_draft["jammers"]),
            "home_base": tuple(self.scenario_draft["home_base"]),
        }
        try:
            return ScenarioConfig.model_validate(data), None
        except ValidationError as exc:
            return None, _messages_from_validation(exc)

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
        self._set_banner([])
        return None

    def load(self, path: str) -> None:
        """Replace the current draft entirely from a saved ScenarioConfig."""
        loaded = load_scenario(path)
        self.scenario_draft = {
            "grid_width": loaded.grid_width,
            "grid_height": loaded.grid_height,
            "jets": [j.model_dump() for j in loaded.jets],
            "targets": [t.model_dump() for t in loaded.targets],
            "sam_sites": [s.model_dump() for s in loaded.sam_sites],
            "jammers": [j.model_dump() for j in loaded.jammers],
            "home_base": list(loaded.home_base),
        }
        self.camera = OrthoEditorCamera(
            self.base, loaded.grid_width, loaded.grid_height
        )
        self._path = path
        self._path_entry.enterText(path)
        self._selected = None
        self._set_banner([])
        self.rebuild_scene()
        self._refresh_entity_list()

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
        jammers = self.scenario_draft["jammers"]
        all_targets = list(targets) + list(sams)
        return TraceHeader(
            grid_width=self.scenario_draft["grid_width"],
            grid_height=self.scenario_draft["grid_height"],
            sensor_radius=wn_config.SENSOR_RADIUS,
            jam_radius=wn_config.JAM_RADIUS,
            sam_engagement_range=wn_config.SAM_ENGAGEMENT_RANGE,
            strike_range=wn_config.WEAPON_CATALOG["JDAM"].range,
            jet_ids=[j["agent_id"] for j in jets],
            initial_payload={j["agent_id"]: dict(j.get("payload") or {}) for j in jets},
            target_ids=[t["target_id"] for t in all_targets],
            target_types={t["target_id"]: t.get("target_type", "HARDENED_BUNKER") for t in all_targets},
            # Data plumbing only -- an in-progress draft may be invalid (e.g.
            # hp_required < 1); real validation happens exclusively in
            # ScenarioConfig's model_validator via _construct_config().
            target_hp_required={t["target_id"]: int(t.get("hp_required") or 1) for t in all_targets},
            sam_site_ids=[s["target_id"] for s in sams],
            jammer_positions={j["jammer_id"]: tuple(j["position"]) for j in jammers},
            target_positions={t["target_id"]: tuple(t["position"]) for t in all_targets},
            home_base=tuple(self.scenario_draft.get("home_base") or wn_config.DEFAULT_HOME_BASE),
            jet_frame_types={
                j["agent_id"]: j.get("frame_type", "STRIKE_FIGHTER") for j in jets
            },
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

    def _refresh_entity_list(self) -> None:
        for btn in self._list_buttons:
            try:
                btn.destroy()
            except Exception:
                pass
        self._list_buttons = []
        z = self._list_anchor_z
        for kind, ent in self._all_entities():
            label = f"{kind.replace('_', ' ')}  {self._id_of(kind, ent)}"
            btn = DirectButton(
                parent=self._panel,
                text=label,
                pos=(0.46, 0, z),
                scale=0.032,
                command=self.open_property_form,
                extraArgs=[ent],
                frameColor=BTN_BG,
                text_fg=CYAN,
                relief=1,
                pad=(0.12, 0.06),
            )
            self._list_buttons.append(btn)
            z -= 0.055

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
        # Ignore clicks over the left-hand editor panel.
        if mx < -0.45:
            return
        world = self.camera.screen_to_world(mx, my)
        self.on_ground_clicked(world)
