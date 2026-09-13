"""Pydantic schema for a WraithNet scenario: the full set of jets, targets,
SAM sites, and jammers that make up one runnable scenario. This is the
data-driven replacement for the hardcoded wraithnet.config.SCENARIO dict —
see docs/09_..._Design_Document.md §4.

Phase 34 adds ``scenario_version`` / ``terrain`` (v2). Phase 37 adds combat
seed, mission objectives, and fixed airbases (v3). Phase 38 adds guided
loadouts and SAM magazines (v4). Phase 39 adds countermeasure state (v5).
Phase 40 adds friendly carriers as dynamic airbases (v6).
Phase 41 adds enemy corvettes with layered naval defense (v7).
Phase 42 adds land AAA gun sites (v8).
Phase 43 adds friendly/enemy tanks (v9).
Load legacy data only through ``scenario_from_data`` — direct
``ScenarioConfig.model_validate`` on a v1/v2/v3/v4/v5/v6/v7/v8 dict fails loudly instead
of silently bypassing migration.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from wraithnet import config
from wraithnet.aaa import AaaSiteSpec, validate_aaa_placement
from wraithnet.bases import (
    HOME_BASE_ID,
    BaseAssignment,
    FixedAirbaseSpec,
    default_home_airbase,
    default_jet_assignment,
)
from wraithnet.config import JetFrameType, TargetType, WeaponType
from wraithnet.generation_schemas import GenerationMetadata
from wraithnet.naval import (
    CarrierSpec,
    CorvetteSpec,
    validate_carrier_placement,
    validate_corvette_placement,
)
from wraithnet.objectives import (
    MissionObjective,
    default_strike_and_return_objectives,
    synthesize_legacy_destroy_objective,
    synthesize_legacy_return_objective,
)
from wraithnet.tanks import TankSpec, validate_tank_placement
from wraithnet.terrain import (
    TerrainGrid,
    all_plains_terrain,
    validate_position_for_domain,
)

SUPPORTED_SCENARIO_VERSION: int = 9

# Pre-guidance ground suitability used only by migrate_v3_to_v4 feasibility
# comparison (catalog AIM9X/AMRAAM are air-to-air after Phase 38).
_LEGACY_V3_GROUND_SUITABILITY: dict[str, dict[TargetType, float]] = {
    "JDAM": {"HARDENED_BUNKER": 1.0, "MOBILE_RADAR": 0.0, "SAM_SITE": 1.0},
    "AIM9X": {"HARDENED_BUNKER": 0.0, "MOBILE_RADAR": 1.0, "SAM_SITE": 0.0},
    "AMRAAM": {"HARDENED_BUNKER": 0.3, "MOBILE_RADAR": 1.0, "SAM_SITE": 1.0},
    "GUN_POD": {"HARDENED_BUNKER": 1.0, "MOBILE_RADAR": 0.0, "SAM_SITE": 1.0},
}
_LEGACY_V3_DAMAGE: dict[str, int] = {
    "JDAM": 1,
    "AIM9X": 1,
    "AMRAAM": 1,
    "GUN_POD": 2,
}


def _as_xy(position: Any) -> tuple[float, float]:
    if isinstance(position, tuple):
        return (float(position[0]), float(position[1]))
    if isinstance(position, list):
        return (float(position[0]), float(position[1]))
    raise TypeError(f"Expected position pair, got {type(position)!r}")


def _entity_field(entity: Any, attr: str) -> Any:
    if isinstance(entity, dict):
        return entity[attr]
    return getattr(entity, attr)


def _collect_destroy_subject_ids(data: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for target in data.get("targets", []) or []:
        ids.append(str(_entity_field(target, "target_id")))
    for sam in data.get("sam_sites", []) or []:
        ids.append(str(_entity_field(sam, "target_id")))
    return ids


def _collect_friendly_jet_ids(data: dict[str, Any]) -> list[str]:
    ids: list[str] = []
    for jet in data.get("jets", []) or []:
        ids.append(str(_entity_field(jet, "agent_id")))
    return ids


def _default_mission_objective_dicts(data: dict[str, Any]) -> list[dict[str, Any]]:
    return [
        o.model_dump()
        for o in default_strike_and_return_objectives(
            _collect_destroy_subject_ids(data),
            _collect_friendly_jet_ids(data),
        )
    ]


class JetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    agent_id: str
    position: tuple[float, float]
    payload: dict[WeaponType, int]
    frame_type: JetFrameType = "STRIKE_FIGHTER"
    initial_heading: float = Field(default=0.0, ge=0.0, lt=360.0)
    base_assignment: BaseAssignment = Field(default_factory=default_jet_assignment)
    # Phase 39: countermeasure inventory / notch config (frame defaults via migrate).
    flare_capacity: int = Field(default=6, ge=0)
    flares_remaining: int = Field(default=6, ge=0)
    flare_cooldown_ticks: int = Field(default=2, ge=0)
    flare_quality: float = Field(default=0.75, ge=0.0, le=1.0)
    radar_warning_receiver: bool = True
    notch_angle_window_deg: float = Field(default=30.0, ge=0.0)
    notch_min_speed: float = Field(default=2.0, ge=0.0)
    notch_hold_ticks: int = Field(default=2, ge=1)

    @model_validator(mode="after")
    def _check_flares_vs_capacity(self) -> "JetSpec":
        if self.flares_remaining > self.flare_capacity:
            raise ValueError(
                f"Jet '{self.agent_id}' flares_remaining "
                f"({self.flares_remaining}) exceeds flare_capacity "
                f"({self.flare_capacity})."
            )
        return self


class TargetSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: str
    position: tuple[float, float]
    hp_required: int
    target_type: TargetType


class SamSiteSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: str
    position: tuple[float, float]
    hp_required: int
    target_type: TargetType = "SAM_SITE"
    # Phase 38: finite magazine + cooldown (legacy sites get DEFAULT_SAM_*).
    missile_loadout: dict[WeaponType, int] = Field(
        default_factory=lambda: dict(config.DEFAULT_SAM_MISSILE_LOADOUT)
    )
    cooldown_ticks: int = Field(default=config.DEFAULT_SAM_COOLDOWN_TICKS, ge=0)
    sam_frame_ref: str = config.DEFAULT_SAM_FRAME_REF


class JammerSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    jammer_id: str
    position: tuple[float, float]


class ScenarioConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    scenario_version: Literal[9]
    grid_width: float = config.GRID_WIDTH
    grid_height: float = config.GRID_HEIGHT
    jets: list[JetSpec]
    targets: list[TargetSpec]
    sam_sites: list[SamSiteSpec] = []
    jammers: list[JammerSpec] = []
    home_base: tuple[float, float] = config.DEFAULT_HOME_BASE
    terrain: TerrainGrid
    combat_seed: int = Field(default=0, ge=0)
    mission_objectives: list[MissionObjective] = Field(default_factory=list)
    fixed_airbases: list[FixedAirbaseSpec] = Field(default_factory=list)
    carriers: list[CarrierSpec] = Field(default_factory=list)
    corvettes: list[CorvetteSpec] = Field(default_factory=list)
    aaa_sites: list[AaaSiteSpec] = Field(default_factory=list)
    tanks: list[TankSpec] = Field(default_factory=list)
    # Informational reproducibility metadata from the random generator (Phase 30).
    # Simulation, bridge, and trace paths ignore this field.
    generation: GenerationMetadata | None = None

    @model_validator(mode="after")
    def _validate(self) -> ScenarioConfig:
        errors: list[str] = []

        if (
            self.terrain.world_width != self.grid_width
            or self.terrain.world_height != self.grid_height
        ):
            errors.append(
                f"terrain world dimensions ({self.terrain.world_width}x"
                f"{self.terrain.world_height}) must match scenario grid "
                f"({self.grid_width}x{self.grid_height})."
            )

        # Phase 37: jetless scenarios are schema-legal (tanks/naval later).
        # Require at least one required mission objective.
        required_objectives = [o for o in self.mission_objectives if o.required]
        if not required_objectives:
            errors.append(
                "A scenario needs at least one required mission objective."
            )

        seen: dict[str, str] = {}
        for entity_id, kind in _entity_ids(self):
            if entity_id in seen:
                errors.append(
                    f"The ID '{entity_id}' is used more than once "
                    f"(as a {seen[entity_id]} and a {kind}). "
                    "Every jet, target, SAM site, jammer, base, and naval unit "
                    "must have a unique ID."
                )
            else:
                seen[entity_id] = kind

        base_ids = {base.base_id for base in self.fixed_airbases}
        if len(base_ids) != len(self.fixed_airbases):
            errors.append("fixed_airbases must have unique base_id values.")

        carrier_ids = {c.unit_id for c in self.carriers}
        if len(carrier_ids) != len(self.carriers):
            errors.append("carriers must have unique unit_id values.")
        corvette_ids = {c.unit_id for c in self.corvettes}
        if len(corvette_ids) != len(self.corvettes):
            errors.append("corvettes must have unique unit_id values.")
        aaa_ids = {a.target_id for a in self.aaa_sites}
        if len(aaa_ids) != len(self.aaa_sites):
            errors.append("aaa_sites must have unique target_id values.")
        tank_ids = {t.unit_id for t in self.tanks}
        if len(tank_ids) != len(self.tanks):
            errors.append("tanks must have unique unit_id values.")
        # Carrier IDs register as dynamic airbases (Phase 40).
        recoverable_base_ids = base_ids | carrier_ids

        home_matches = [
            b for b in self.fixed_airbases if b.base_id == HOME_BASE_ID
        ]
        if home_matches and home_matches[0].position != self.home_base:
            errors.append(
                f"fixed_airbases '{HOME_BASE_ID}' position "
                f"{home_matches[0].position} must match home_base {self.home_base}."
            )

        for jet in self.jets:
            primary = jet.base_assignment.primary_base_id
            if primary not in recoverable_base_ids:
                errors.append(
                    f"Jet '{jet.agent_id}' primary_base_id '{primary}' "
                    "does not match any fixed_airbases or carriers entry."
                )
            alt = jet.base_assignment.alternate_base_id
            if alt is not None and alt not in recoverable_base_ids:
                errors.append(
                    f"Jet '{jet.agent_id}' alternate_base_id '{alt}' "
                    "does not match any fixed_airbases or carriers entry."
                )

        known_subject_ids = set(seen.keys())
        objective_ids: set[str] = set()
        for obj in self.mission_objectives:
            if obj.objective_id in objective_ids:
                errors.append(
                    f"mission_objectives ID '{obj.objective_id}' is used more than once."
                )
            else:
                objective_ids.add(obj.objective_id)
            if not obj.subject_ids:
                errors.append(
                    f"Objective '{obj.objective_id}' must have nonempty subject_ids."
                )
            for subject_id in obj.subject_ids:
                if subject_id not in known_subject_ids:
                    errors.append(
                        f"Objective '{obj.objective_id}' subject '{subject_id}' "
                        "does not match any jet, target, SAM site, jammer, "
                        "base, carrier, corvette, AAA site, or tank ID."
                    )

        for label, position in _entity_positions(self):
            x, y = position
            if not (0.0 <= x <= self.grid_width and 0.0 <= y <= self.grid_height):
                errors.append(
                    f"{label} is at ({x}, {y}), which is outside the map "
                    f"(0 to {self.grid_width} wide, 0 to {self.grid_height} tall)."
                )

        for spec in (*self.targets, *self.sam_sites):
            if spec.hp_required < 1:
                kind = "SAM site" if isinstance(spec, SamSiteSpec) else "Target"
                errors.append(
                    f"{kind} '{spec.target_id}' must have hp_required of at least 1 "
                    f"(currently {spec.hp_required})."
                )
        for aaa in self.aaa_sites:
            if aaa.resolved_hp() < 1:
                errors.append(
                    f"AAA site '{aaa.target_id}' must have hp_required of at least 1 "
                    f"(currently {aaa.resolved_hp()})."
                )

        for jet in self.jets:
            for weapon, count in jet.payload.items():
                if count < 0:
                    errors.append(
                        f"Jet '{jet.agent_id}' has a {weapon} count of {count}. "
                        "Weapon counts cannot be negative."
                    )
                if weapon in ("SAM_IR", "SAM_RADAR") and count > 0:
                    errors.append(
                        f"Jet '{jet.agent_id}' cannot carry surface-to-air weapon {weapon}."
                    )
            slots_used = sum(
                config.WEAPON_CATALOG[wt].slot_cost * count
                for wt, count in jet.payload.items()
            )
            slots_max = config.JET_FRAME_CATALOG[jet.frame_type].ordnance_slots
            if slots_used > slots_max:
                errors.append(
                    f"Jet '{jet.agent_id}' payload requires {slots_used} ordnance slots "
                    f"but its {jet.frame_type} frame only has {slots_max}"
                )

        for sam in self.sam_sites:
            for weapon, count in sam.missile_loadout.items():
                if count < 0:
                    errors.append(
                        f"SAM '{sam.target_id}' has a {weapon} count of {count}. "
                        "Weapon counts cannot be negative."
                    )
                if weapon not in ("SAM_IR", "SAM_RADAR"):
                    errors.append(
                        f"SAM '{sam.target_id}' loadout weapon '{weapon}' must be "
                        "SAM_IR or SAM_RADAR."
                    )

        # Domain passability (Phase 34). Jets are AIR and may sit on any tile.
        for label, position, domain in (
            *(
                (f"Target '{t.target_id}'", t.position, "GROUND")
                for t in self.targets
            ),
            *(
                (f"SAM site '{s.target_id}'", s.position, "GROUND")
                for s in self.sam_sites
            ),
            *(
                (f"AAA site '{a.target_id}'", a.position, "GROUND")
                for a in self.aaa_sites
            ),
            *(
                (f"Jammer '{j.jammer_id}'", j.position, "GROUND")
                for j in self.jammers
            ),
            ("Home base", self.home_base, "GROUND"),
            *(
                (f"Airbase '{b.base_id}'", b.position, "GROUND")
                for b in self.fixed_airbases
            ),
        ):
            try:
                validate_position_for_domain(self.terrain, position, domain)
            except ValueError as exc:
                errors.append(f"{label}: {exc}")

        for carrier in self.carriers:
            errors.extend(validate_carrier_placement(self.terrain, carrier))

        for corvette in self.corvettes:
            errors.extend(validate_corvette_placement(self.terrain, corvette))

        for aaa in self.aaa_sites:
            errors.extend(validate_aaa_placement(self.terrain, aaa))

        for tank in self.tanks:
            errors.extend(validate_tank_placement(self.terrain, tank))

        if errors:
            raise ValueError("\n".join(errors))
        return self


def migrate_v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    """Pure migration: add all-plains terrain and set scenario_version=2."""
    out = dict(data)
    grid_width = float(out.get("grid_width", config.GRID_WIDTH))
    grid_height = float(out.get("grid_height", config.GRID_HEIGHT))
    out["scenario_version"] = 2
    if "terrain" not in out:
        out["terrain"] = all_plains_terrain(grid_width, grid_height).model_dump()
    return out


def migrate_v2_to_v3(data: dict[str, Any]) -> dict[str, Any]:
    """Pure migration: combat_seed, fixed airbases, jet assignments, objectives."""
    out = dict(data)
    out["scenario_version"] = 3

    if "combat_seed" not in out:
        out["combat_seed"] = 0

    home_base = _as_xy(out.get("home_base", config.DEFAULT_HOME_BASE))
    out["home_base"] = home_base

    if "fixed_airbases" not in out:
        out["fixed_airbases"] = [
            default_home_airbase(home_base).model_dump()
        ]

    jets_in = out.get("jets", []) or []
    jets_out: list[Any] = []
    for jet in jets_in:
        if isinstance(jet, dict):
            j = dict(jet)
            if "base_assignment" not in j:
                j["base_assignment"] = default_jet_assignment().model_dump()
            jets_out.append(j)
        else:
            # JetSpec / other model: ensure assignment via dump+default.
            dumped = jet.model_dump() if hasattr(jet, "model_dump") else dict(jet)
            if "base_assignment" not in dumped or dumped["base_assignment"] is None:
                dumped["base_assignment"] = default_jet_assignment().model_dump()
            jets_out.append(dumped)
    out["jets"] = jets_out

    objectives = out.get("mission_objectives")
    if objectives is None or objectives == []:
        out["mission_objectives"] = _default_mission_objective_dicts(out)

    return out


def _payload_as_dict(jet: Any) -> dict[str, int]:
    if isinstance(jet, dict):
        raw = jet.get("payload", {}) or {}
    else:
        raw = getattr(jet, "payload", {}) or {}
    return {str(k): int(v) for k, v in dict(raw).items()}


def _legacy_v3_eligible_damage(weapon: str, target_type: TargetType) -> int:
    suitability = _LEGACY_V3_GROUND_SUITABILITY.get(weapon, {})
    if suitability.get(target_type, 0.0) <= 0.0:
        return 0
    return _LEGACY_V3_DAMAGE.get(weapon, 0)


def _catalog_eligible_damage(weapon: str, target_type: TargetType) -> int:
    spec = config.WEAPON_CATALOG.get(weapon)  # type: ignore[arg-type]
    if spec is None:
        return 0
    if spec.suitability.get(target_type, 0.0) <= 0.0:
        return 0
    return int(spec.damage_per_hit)


def _damage_capacity_by_type(
    payloads: list[dict[str, int]],
    *,
    legacy: bool,
) -> dict[TargetType, int]:
    totals: dict[TargetType, int] = {
        "HARDENED_BUNKER": 0,
        "MOBILE_RADAR": 0,
        "SAM_SITE": 0,
    }
    for payload in payloads:
        for weapon, count in payload.items():
            if count <= 0:
                continue
            for tt in totals:
                dmg = (
                    _legacy_v3_eligible_damage(weapon, tt)
                    if legacy
                    else _catalog_eligible_damage(weapon, tt)
                )
                totals[tt] += dmg * count
    return totals


def _destroyable_hp_by_type(data: dict[str, Any]) -> dict[TargetType, int]:
    totals: dict[TargetType, int] = {
        "HARDENED_BUNKER": 0,
        "MOBILE_RADAR": 0,
        "SAM_SITE": 0,
    }
    for entity in (*(data.get("targets") or []), *(data.get("sam_sites") or [])):
        tt = str(_entity_field(entity, "target_type"))
        hp = int(_entity_field(entity, "hp_required"))
        if tt in totals:
            totals[tt] += hp  # type: ignore[index]
    return totals


def _scenario_has_radar_emitters(data: dict[str, Any]) -> bool:
    """True if any destroyable is a catalog radar emitter (SAM / MOBILE_RADAR)."""
    for entity in (*(data.get("targets") or []), *(data.get("sam_sites") or [])):
        tt = str(_entity_field(entity, "target_type"))
        if tt in ("SAM_SITE", "MOBILE_RADAR"):
            return True
    return False


def _emitter_hp_demand(data: dict[str, Any]) -> int:
    demand = 0
    for entity in (*(data.get("targets") or []), *(data.get("sam_sites") or [])):
        tt = str(_entity_field(entity, "target_type"))
        if tt in ("SAM_SITE", "MOBILE_RADAR"):
            demand += int(_entity_field(entity, "hp_required"))
    return demand


def _convert_jet_payload_v3_to_v4(
    payload: dict[str, int],
    *,
    agm88_budget: int,
) -> tuple[dict[str, int], int]:
    """Convert one jet payload; returns (new_payload, remaining_agm88_budget).

    Algorithm (documented for Phase 38):
    1. Every AIM9X round becomes exactly one AGM65 (1:1, same slot_cost).
    2. Every AMRAAM round becomes exactly one AGM65 *or* AGM88 (never both,
       never duplicated). Slot use does not increase (AMRAAM cost 2 → A2G cost 1).
    3. When the scenario has radar-emitting targets, AMRAAM rounds consume the
       shared ``agm88_budget`` (initially = total emitter HP) first as AGM88;
       leftover AMRAAM rounds become AGM65. With no emitters, all AMRAAM → AGM65.
    4. Other weapons (JDAM, GUN_POD, already-v4 AGM*) are copied through.
    """
    out = {k: int(v) for k, v in payload.items()}
    aim9x = int(out.pop("AIM9X", 0))
    amraam = int(out.pop("AMRAAM", 0))

    agm65 = int(out.get("AGM65", 0)) + aim9x
    agm88 = int(out.get("AGM88", 0))

    to_88 = min(amraam, max(0, agm88_budget))
    to_65 = amraam - to_88
    agm65 += to_65
    agm88 += to_88
    remaining_budget = max(0, agm88_budget - to_88)

    if agm65 > 0:
        out["AGM65"] = agm65
    else:
        out.pop("AGM65", None)
    if agm88 > 0:
        out["AGM88"] = agm88
    else:
        out.pop("AGM88", None)

    return {k: v for k, v in out.items() if v > 0}, remaining_budget


def migrate_v3_to_v4(data: dict[str, Any]) -> dict[str, Any]:
    """Pure migration: guided loadouts, AIM9X→AGM65, AMRAAM split, SAM magazines.

    See ``_convert_jet_payload_v3_to_v4`` for the weapon conversion algorithm.
    Raises ``ValueError`` if post-migration ground damage capacity cannot cover
    the same TargetType HP demand the legacy loadout could (actionable
    compatibility error).
    """
    out = dict(data)
    out["scenario_version"] = 4

    jets_in = list(out.get("jets", []) or [])
    old_payloads = [_payload_as_dict(j) for j in jets_in]
    old_capacity = _damage_capacity_by_type(old_payloads, legacy=True)

    has_emitters = _scenario_has_radar_emitters(out)
    agm88_budget = _emitter_hp_demand(out) if has_emitters else 0

    # Stable jet order: scenario list order (not dict insertion of payloads).
    jets_out: list[Any] = []
    new_payloads: list[dict[str, int]] = []
    for jet in jets_in:
        if isinstance(jet, dict):
            j = dict(jet)
        else:
            j = jet.model_dump() if hasattr(jet, "model_dump") else dict(jet)
        payload = _payload_as_dict(j)
        converted, agm88_budget = _convert_jet_payload_v3_to_v4(
            payload, agm88_budget=agm88_budget
        )
        # Preserve explicit zero JDAM/AGM65 keys when the legacy jet listed them
        # so PoC dict shape stays readable (AIM9X:0 → AGM65 omitted if zero).
        if "AIM9X" in payload and payload.get("AIM9X", 0) == 0 and "AGM65" not in converted:
            # Intentionally omit zero AGM65; JDAM-only jets stay JDAM-only.
            pass
        j["payload"] = converted
        jets_out.append(j)
        new_payloads.append(converted)
    out["jets"] = jets_out

    new_capacity = _damage_capacity_by_type(new_payloads, legacy=False)
    demand = _destroyable_hp_by_type(out)
    for tt, hp in demand.items():
        if hp <= 0:
            continue
        if old_capacity[tt] >= hp and new_capacity[tt] < hp:
            raise ValueError(
                f"migrate_v3_to_v4 cannot preserve feasibility for {tt}: "
                f"demand hp={hp}, legacy capacity={old_capacity[tt]}, "
                f"v4 capacity={new_capacity[tt]}. Add AGM65/AGM88 or adjust "
                f"loadouts before loading this scenario."
            )

    sams_in = list(out.get("sam_sites", []) or [])
    sams_out: list[Any] = []
    for sam in sams_in:
        if isinstance(sam, dict):
            s = dict(sam)
        else:
            s = sam.model_dump() if hasattr(sam, "model_dump") else dict(sam)
        if "missile_loadout" not in s:
            s["missile_loadout"] = dict(config.DEFAULT_SAM_MISSILE_LOADOUT)
        if "cooldown_ticks" not in s:
            s["cooldown_ticks"] = config.DEFAULT_SAM_COOLDOWN_TICKS
        if "sam_frame_ref" not in s:
            s["sam_frame_ref"] = config.DEFAULT_SAM_FRAME_REF
        sams_out.append(s)
    out["sam_sites"] = sams_out

    return out


def _frame_countermeasure_defaults(frame_type: str) -> dict[str, Any]:
    frame = config.JET_FRAME_CATALOG.get(frame_type)  # type: ignore[arg-type]
    if frame is None:
        frame = config.JET_FRAME_CATALOG["STRIKE_FIGHTER"]
    return {
        "flare_capacity": int(frame.flare_capacity),
        "flares_remaining": int(frame.flare_capacity),
        "flare_cooldown_ticks": int(frame.flare_cooldown_ticks),
        "flare_quality": float(frame.flare_quality),
        "radar_warning_receiver": bool(frame.radar_warning_receiver),
        "notch_angle_window_deg": float(frame.notch_angle_window_deg),
        "notch_min_speed": float(frame.notch_min_speed),
        "notch_hold_ticks": int(frame.notch_hold_ticks),
    }


def migrate_v4_to_v5(data: dict[str, Any]) -> dict[str, Any]:
    """Pure migration: supply frame-default countermeasure fields on each jet."""
    out = dict(data)
    out["scenario_version"] = 5
    jets_in = list(out.get("jets", []) or [])
    jets_out: list[Any] = []
    for jet in jets_in:
        if isinstance(jet, dict):
            j = dict(jet)
        else:
            j = jet.model_dump() if hasattr(jet, "model_dump") else dict(jet)
        frame_type = str(j.get("frame_type") or "STRIKE_FIGHTER")
        defaults = _frame_countermeasure_defaults(frame_type)
        for key, value in defaults.items():
            if key not in j:
                j[key] = value
        if "flares_remaining" not in j and "flare_capacity" in j:
            j["flares_remaining"] = int(j["flare_capacity"])
        jets_out.append(j)
    out["jets"] = jets_out
    return out


def migrate_v5_to_v6(data: dict[str, Any]) -> dict[str, Any]:
    """Pure migration: add empty carriers list; preserve fixed-base assignments."""
    out = dict(data)
    out["scenario_version"] = 6
    if "carriers" not in out:
        out["carriers"] = []
    return out


def migrate_v6_to_v7(data: dict[str, Any]) -> dict[str, Any]:
    """Pure migration: add empty corvettes list."""
    out = dict(data)
    out["scenario_version"] = 7
    if "corvettes" not in out:
        out["corvettes"] = []
    return out


def migrate_v7_to_v8(data: dict[str, Any]) -> dict[str, Any]:
    """Pure migration: add empty aaa_sites list."""
    out = dict(data)
    out["scenario_version"] = 8
    if "aaa_sites" not in out:
        out["aaa_sites"] = []
    return out


def migrate_v8_to_v9(data: dict[str, Any]) -> dict[str, Any]:
    """Pure migration: add empty tanks list."""
    out = dict(data)
    out["scenario_version"] = 9
    if "tanks" not in out:
        out["tanks"] = []
    return out


def new_scenario_data(**fields: Any) -> dict[str, Any]:
    """Build a latest-version scenario dict for new programmatic drafts.

    Fills ``scenario_version``, all-plains ``terrain``, ``combat_seed``,
    ``fixed_airbases``, empty ``carriers``/``corvettes``/``aaa_sites``/``tanks``,
    and legacy DESTROY+RETURN ``mission_objectives`` when omitted.
    """
    data = dict(fields)
    grid_width = float(data.get("grid_width", config.GRID_WIDTH))
    grid_height = float(data.get("grid_height", config.GRID_HEIGHT))
    data["scenario_version"] = SUPPORTED_SCENARIO_VERSION
    if "terrain" not in data:
        data["terrain"] = all_plains_terrain(grid_width, grid_height)
    if "combat_seed" not in data:
        data["combat_seed"] = 0
    home_base = _as_xy(data.get("home_base", config.DEFAULT_HOME_BASE))
    data["home_base"] = home_base
    if "fixed_airbases" not in data:
        data["fixed_airbases"] = [default_home_airbase(home_base)]
    if "carriers" not in data:
        data["carriers"] = []
    if "corvettes" not in data:
        data["corvettes"] = []
    if "aaa_sites" not in data:
        data["aaa_sites"] = []
    if "tanks" not in data:
        data["tanks"] = []
    if "mission_objectives" not in data:
        data["mission_objectives"] = _default_mission_objective_dicts(data)
    data = migrate_v4_to_v5({**data, "scenario_version": 4})
    data = migrate_v5_to_v6(data)
    data = migrate_v6_to_v7(data)
    data = migrate_v7_to_v8(data)
    data = migrate_v8_to_v9(data)
    return data


def scenario_from_data(raw: dict[str, Any] | ScenarioConfig) -> ScenarioConfig:
    """Public load/adapt path: migrate legacy data, then validate.

    Missing ``scenario_version`` is treated as legacy v1. Versions newer than
    ``SUPPORTED_SCENARIO_VERSION`` are rejected. Direct
    ``ScenarioConfig.model_validate(legacy_dict)`` is intentionally unsupported.
    """
    if isinstance(raw, ScenarioConfig):
        return raw
    data = dict(raw)
    version = data.get("scenario_version")
    if version is None:
        data = migrate_v1_to_v2(data)
        version = data["scenario_version"]
    if not isinstance(version, int):
        raise ValueError(f"scenario_version must be an int, got {version!r}")
    if version > SUPPORTED_SCENARIO_VERSION:
        raise ValueError(
            f"Unsupported scenario_version {version}; "
            f"this build supports up to {SUPPORTED_SCENARIO_VERSION}."
        )
    while version < SUPPORTED_SCENARIO_VERSION:
        if version == 1:
            data = migrate_v1_to_v2(data)
            version = 2
        elif version == 2:
            data = migrate_v2_to_v3(data)
            version = 3
        elif version == 3:
            data = migrate_v3_to_v4(data)
            version = 4
        elif version == 4:
            data = migrate_v4_to_v5(data)
            version = 5
        elif version == 5:
            data = migrate_v5_to_v6(data)
            version = 6
        elif version == 6:
            data = migrate_v6_to_v7(data)
            version = 7
        elif version == 7:
            data = migrate_v7_to_v8(data)
            version = 8
        elif version == 8:
            data = migrate_v8_to_v9(data)
            version = 9
        else:
            raise ValueError(f"No migration path from scenario_version {version}")
    return ScenarioConfig.model_validate(data)


def _entity_ids(scenario: ScenarioConfig) -> list[tuple[str, str]]:
    ids: list[tuple[str, str]] = []
    ids.extend((jet.agent_id, "jet") for jet in scenario.jets)
    ids.extend((target.target_id, "target") for target in scenario.targets)
    ids.extend((sam.target_id, "SAM site") for sam in scenario.sam_sites)
    ids.extend((jammer.jammer_id, "jammer") for jammer in scenario.jammers)
    ids.extend((base.base_id, "airbase") for base in scenario.fixed_airbases)
    ids.extend((carrier.unit_id, "carrier") for carrier in scenario.carriers)
    ids.extend((corvette.unit_id, "corvette") for corvette in scenario.corvettes)
    ids.extend((aaa.target_id, "AAA site") for aaa in scenario.aaa_sites)
    ids.extend((tank.unit_id, "tank") for tank in scenario.tanks)
    return ids


def _entity_positions(
    scenario: ScenarioConfig,
) -> list[tuple[str, tuple[float, float]]]:
    positions: list[tuple[str, tuple[float, float]]] = []
    positions.extend(
        (f"Jet '{jet.agent_id}'", jet.position) for jet in scenario.jets
    )
    positions.extend(
        (f"Target '{target.target_id}'", target.position)
        for target in scenario.targets
    )
    positions.extend(
        (f"SAM site '{sam.target_id}'", sam.position) for sam in scenario.sam_sites
    )
    positions.extend(
        (f"Jammer '{jammer.jammer_id}'", jammer.position)
        for jammer in scenario.jammers
    )
    positions.append(("Home base", scenario.home_base))
    positions.extend(
        (f"Airbase '{base.base_id}'", base.position)
        for base in scenario.fixed_airbases
    )
    positions.extend(
        (f"Carrier '{carrier.unit_id}'", carrier.position)
        for carrier in scenario.carriers
    )
    positions.extend(
        (f"Corvette '{corvette.unit_id}'", corvette.position)
        for corvette in scenario.corvettes
    )
    positions.extend(
        (f"AAA site '{aaa.target_id}'", aaa.position) for aaa in scenario.aaa_sites
    )
    positions.extend(
        (f"Tank '{tank.unit_id}'", tank.position) for tank in scenario.tanks
    )
    return positions


def _default_scenario() -> ScenarioConfig:
    # config.SCENARIO remains the single literal source of truth (legacy v1);
    # scenario_from_data migrates it through v2→…→v9 (tanks).
    return scenario_from_data(config.SCENARIO)
