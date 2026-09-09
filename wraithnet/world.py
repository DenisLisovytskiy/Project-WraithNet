"""Mesa World model and spatial state for WraithNet.

Implemented in Phase 1 (Schemas & World Scaffolding).
See docs/phases/Phase_1_Schemas_and_World.md.
Phase 6: dict-based payload, target_type, weapon suitability on strike.
Phase 7: sensor-limited build_observation.
Phase 8: jet HP, SAM sites, swarm-wipeout termination.
Phase 20: per-jet airframe catalog, independent heading / turn-rate rotation.
Phase 21: per-weapon range / firing-arc / damage on strike.
Phase 22: fuel burn, bingo-fuel RTB, landing vs fuel-exhaustion reap.
Phase 23: evasion-reduced SAM damage, persistent JetOutcome snapshots,
          mission_aborted vs swarm_wiped_out termination.
Phase 34: terrain grid + 2.5D LOS for sensing, strikes, and legacy SAM.
Phase 37: authoritative altitude, platform taxonomy gates, base registry,
          mission-objective termination, staged tick API.
Phase 38: guided missiles; SAM sites launch SAM_RADAR/SAM_IR instead of
          instant passive damage. LAUNCH (and STRIKE-compat for guided)
          goes through LaunchAcknowledgement; ammo decrements only after
          accept; nominal damage reservations release on non-HIT.
Phase 39: flares (IR) and notching (radar) in the countermeasures stage.
Phase 40: friendly carriers as dynamic airbases; naval movement; recovery SM.
Phase 41: enemy corvettes, AGM84, shared gun resolver, layered defense/CIWS.
Phase 42: land AAA gun sites via shared gun resolver (no missiles).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

import mesa
from mesa.datacollection import DataCollector

from wraithnet import config
from wraithnet.aaa import (
    AAA_FRAME_CATALOG,
    AaaSiteState,
    select_aaa_target,
)
from wraithnet.bases import HOME_BASE_ID, default_jet_assignment
from wraithnet.broker import MessageBroker
from wraithnet.corvette_doctrine import select_doctrine_actions
from wraithnet.countermeasures import (
    FLARE_EFFECT_RANGE,
    closing_aspect,
    flare_decoy_probability,
    notch_break_probability,
    notch_geometry_ok,
    notch_reference_bearing_deg,
    pick_notch_heading,
    roll_flare_decoy,
    roll_notch_break,
)
from wraithnet.geometry import bearing_to, is_in_firing_arc, shortest_signed_angle
from wraithnet.guns import (
    CIWS_GUN,
    DECK_GUN,
    TANK_CANNON,
    GunBurstRequest,
    resolve_gun_burst,
)
from wraithnet.tanks import (
    TANK_FRAME_CATALOG,
    TankState,
    resolve_tank_separation,
    select_tank_target,
    step_tank_movement,
)
from wraithnet.missiles import (
    LaunchAcknowledgement,
    LaunchRequest,
    MissileState,
    advance_missile,
    apply_fuel_expiry,
    create_missile,
    is_active_terminal_guidance,
    resolve_impact,
    terminal_contact,
    validate_launch,
)
from wraithnet.naval import (
    NAVAL_FRAME_CATALOG,
    CarrierState,
    CorvetteState,
    RecoveryPhase,
    approach_point_astern,
    carrier_velocity,
    recovery_heading_aligned,
    recovery_relative_speed,
    step_carrier_movement,
    step_corvette_movement,
)
from wraithnet.objectives import MissionObjective, evaluate_mission
from wraithnet.scenario_schema import ScenarioConfig, _default_scenario
from wraithnet.schemas import (
    ActionIntent,
    InboundThreat,
    Observation,
    ObservedGunThreat,
    TargetInfo,
)
from wraithnet.taxonomy import are_hostile, reject_engagement, target_class_for_target_type
from wraithnet.terrain import TerrainGrid, has_line_of_sight

# Phase 37 F — six staged tick phases (bridge may call these individually).
_STAGE_PRE_MOVE_OBS = "pre_move_obs"
_STAGE_APPLY_MOVEMENT = "apply_movement"
_STAGE_POST_MOVE_OBS = "post_move_obs"
_STAGE_REGISTER_LAUNCHES = "register_launches"
_STAGE_COUNTERMEASURES = "countermeasures"
_STAGE_RESOLVE_COMBAT = "resolve_combat"

_STAGE_ORDER = (
    _STAGE_PRE_MOVE_OBS,
    _STAGE_APPLY_MOVEMENT,
    _STAGE_POST_MOVE_OBS,
    _STAGE_REGISTER_LAUNCHES,
    _STAGE_COUNTERMEASURES,
    _STAGE_RESOLVE_COMBAT,
)


@dataclass
class TargetState:
    target_id: str
    position: tuple[float, float]
    hp_required: int
    target_type: str
    hp_remaining: int = field(init=False)
    signature: config.SensorSignature = field(default_factory=config.SensorSignature)

    def __post_init__(self) -> None:
        self.hp_remaining = self.hp_required
        if self.signature == config.SensorSignature():
            default = config.TARGET_SIGNATURE_DEFAULTS.get(self.target_type)  # type: ignore[arg-type]
            if default is not None:
                self.signature = default

    @property
    def is_destroyed(self) -> bool:
        return self.hp_remaining <= 0


@dataclass(frozen=True)
class JetOutcome:
    """Snapshot of a jet taken the tick it leaves active play.

    Survives deletion from ``WraithNetModel.jets`` so metrics can still
    report how far a destroyed or RTB'd jet flew and what it brought home.
    """

    agent_id: str
    frame_type: config.JetFrameType
    outcome: Literal["destroyed", "returned_to_base"]
    distance_traveled: float
    final_payload_remaining: dict[str, int]


class JetAgent(mesa.Agent):
    def __init__(
        self,
        model: WraithNetModel,
        agent_id: str,
        payload: dict[str, int],
        frame_type: config.JetFrameType,
        initial_heading: float = 0.0,
        base_assignment=None,
        *,
        flare_capacity: int | None = None,
        flares_remaining: int | None = None,
        flare_cooldown_ticks: int | None = None,
        flare_quality: float | None = None,
        radar_warning_receiver: bool | None = None,
        notch_angle_window_deg: float | None = None,
        notch_min_speed: float | None = None,
        notch_hold_ticks: int | None = None,
    ) -> None:
        super().__init__(model)
        self.agent_id = agent_id
        self.payload = dict(payload)  # mutable copy; never share the scenario dict
        self.distance_traveled = 0.0
        self.frame_type = frame_type
        frame = config.JET_FRAME_CATALOG[frame_type]
        self.hp: int = frame.hp
        self.fuel: float = frame.fuel_capacity
        self.is_rtb: bool = False
        # Degrees: 0 = facing +y (world "up"), increasing clockwise when viewed
        # from above (90 = facing +x). Independent of travel direction.
        self.heading: float = initial_heading % 360.0
        self.desired_heading: float | None = None
        # Phase 37: authoritative altitude. Cruise preserves Phase 34 LOS height.
        self.altitude: float = frame.cruise_altitude
        self.desired_altitude: float | None = frame.cruise_altitude
        self.base_assignment = base_assignment or default_jet_assignment()
        self.signature: config.SensorSignature = frame.signature
        # Phase 39 countermeasures.
        self.flare_capacity: int = (
            int(frame.flare_capacity) if flare_capacity is None else int(flare_capacity)
        )
        self.flares_remaining: int = (
            self.flare_capacity if flares_remaining is None else int(flares_remaining)
        )
        self.flare_cooldown_ticks: int = (
            int(frame.flare_cooldown_ticks)
            if flare_cooldown_ticks is None
            else int(flare_cooldown_ticks)
        )
        self.flare_cooldown_remaining: int = 0
        self.flare_quality: float = (
            float(frame.flare_quality) if flare_quality is None else float(flare_quality)
        )
        self.radar_warning_receiver: bool = (
            bool(frame.radar_warning_receiver)
            if radar_warning_receiver is None
            else bool(radar_warning_receiver)
        )
        self.notch_angle_window_deg: float = (
            float(frame.notch_angle_window_deg)
            if notch_angle_window_deg is None
            else float(notch_angle_window_deg)
        )
        self.notch_min_speed: float = (
            float(frame.notch_min_speed)
            if notch_min_speed is None
            else float(notch_min_speed)
        )
        self.notch_hold_ticks: int = (
            int(frame.notch_hold_ticks)
            if notch_hold_ticks is None
            else int(notch_hold_ticks)
        )
        self.defensive_state: Literal["NONE", "NOTCH", "FLARE"] = "NONE"
        self.defensive_threat_id: str | None = None
        self.notch_hold_progress: int = 0
        self.velocity_xy: tuple[float, float] = (0.0, 0.0)
        self._pos_at_move_start: tuple[float, float] | None = None
        # Phase 40: carrier recovery state machine.
        self.active_base_id: str = self.base_assignment.primary_base_id
        self.recovery_phase: RecoveryPhase | None = None
        self.recovery_abort: bool = False

    # --- PlatformIdentity adapter surface (FRIENDLY / AIR / AIRCRAFT) ---
    @property
    def platform_id(self) -> str:
        return self.agent_id

    @property
    def faction(self) -> Literal["FRIENDLY"]:
        return "FRIENDLY"

    @property
    def domain(self) -> Literal["AIR"]:
        return "AIR"

    @property
    def target_class(self) -> Literal["AIRCRAFT"]:
        return "AIRCRAFT"

    def position_xy(self) -> tuple[float, float]:
        return (float(self.pos[0]), float(self.pos[1]))

    def altitude_z(self) -> float:
        return float(self.altitude)

    @property
    def is_destroyed(self) -> bool:
        return self.hp <= 0

    @property
    def total_payload_remaining(self) -> int:
        return sum(self.payload.values())

    # NOTE: self.pos is managed by ContinuousSpace via place_agent/move_agent.


class SamSiteAgent(mesa.Agent):
    """Plain Mesa hazard entity — never a JetAgentRuntime, never on the Broker."""

    def __init__(
        self,
        model: WraithNetModel,
        target_id: str,
        position: tuple[float, float],
        missile_loadout: dict[str, int] | None = None,
        cooldown_ticks: int = config.DEFAULT_SAM_COOLDOWN_TICKS,
        sam_frame_ref: str = config.DEFAULT_SAM_FRAME_REF,
    ) -> None:
        super().__init__(model)
        self.target_id = target_id
        self.position = position
        self.missile_loadout: dict[str, int] = dict(
            missile_loadout or config.DEFAULT_SAM_MISSILE_LOADOUT
        )
        self.cooldown_ticks: int = int(cooldown_ticks)
        self.cooldown_remaining: int = 0
        self.sam_frame_ref: str = sam_frame_ref


class EwJammerAgent(mesa.Agent):
    """Static EW hazard — never a CNP target, never destroyable (Phase 9)."""

    def __init__(
        self, model: WraithNetModel, jammer_id: str, position: tuple[float, float]
    ) -> None:
        super().__init__(model)
        self.jammer_id = jammer_id
        self.position = position


class WraithNetModel(mesa.Model):
    def __init__(
        self, seed: int | None = None, scenario: ScenarioConfig | None = None
    ) -> None:
        # Mesa 3.5 prefers `rng=`; passing `seed=` directly is soft-deprecated.
        super().__init__(rng=seed)
        # Mesa 3.x replaces self.step with a no-arg _wrapped_step for its
        # event scheduler. Restore our intents-based step so callers (and
        # Phase 4's Bridge) can use model.step(intents) directly.
        self.step = self._user_step
        self._scenario = scenario or _default_scenario()
        self.space = mesa.space.ContinuousSpace(
            self._scenario.grid_width, self._scenario.grid_height, config.GRID_TORUS
        )
        self.tick = 0
        self.targets: dict[str, TargetState] = {}
        self.jets: dict[str, JetAgent] = {}
        self.sam_sites: dict[str, SamSiteAgent] = {}
        self.jammers: dict[str, EwJammerAgent] = {}
        self.carriers: dict[str, CarrierState] = {}
        self.corvettes: dict[str, CorvetteState] = {}
        self.aaa_sites: dict[str, AaaSiteState] = {}
        self.tanks: dict[str, TankState] = {}
        self.carrier_recoveries: int = 0
        self.strike_log: list[dict] = []  # ground truth for Phase 5 validation
        self.missiles: list[MissileState] = []
        self._next_missile_seq: int = 0
        self.missile_events: list[dict] = []  # launch/impact/flare/notch audit trail
        self.gun_events: list[dict] = []  # Phase 41+ gun burst / CIWS / AAA audit trail
        # Phase 45: CNP negotiation audit (CFP/BID/award/decline/release).
        self.negotiation_events: list[dict] = []
        # Phase 41 layer metrics (also mirrored into summarize()).
        self.corvette_sam_launches: int = 0
        self.ciws_attempts: int = 0
        self.ciws_intercepts: int = 0
        self.deck_gun_hits: int = 0
        self.corvette_losses: int = 0
        self.agm84_launches: int = 0
        self.agm84_hits: int = 0
        # Phase 42 AAA metrics.
        self.aaa_bursts: int = 0
        self.aaa_rounds: int = 0
        self.aaa_hits: int = 0
        self.aaa_damage: int = 0
        self.aaa_kills: int = 0
        self.aircraft_losses_to_aaa: int = 0
        # Phase 43 tank metrics.
        self.tank_rounds: int = 0
        self.tank_hits: int = 0
        self.tank_kills_friendly: int = 0
        self.tank_kills_enemy: int = 0
        self.tank_losses_friendly: int = 0
        self.tank_losses_enemy: int = 0
        # Phase 38: nominal damage reserved by in-flight missiles (target_id →
        # list of {missile_id, amount}). Prevents over-launch vs remaining HP.
        self.damage_reservations: dict[str, list[dict]] = {}
        self.launch_acks: list[LaunchAcknowledgement] = []
        # Phase 39 metrics counters (also mirrored into summarize()).
        self.flare_attempts: int = 0
        self.flare_successes: int = 0
        self.notch_attempts: int = 0
        self.notch_completions: int = 0
        self.notch_lock_breaks: int = 0
        self.deaths_by_guidance: dict[str, int] = {}
        self.swarm_wiped_out: bool = False
        self.mission_aborted: bool = False
        self.jet_outcomes: dict[str, JetOutcome] = {}
        self._home_base: tuple[float, float] = self._scenario.home_base
        self.terrain: TerrainGrid = self._scenario.terrain
        self.jets_returned_to_base: set[str] = set()
        # Phase 37: combat seed + objectives + base registry (v2/v3 safe).
        self.combat_seed: int = int(getattr(self._scenario, "combat_seed", 0) or 0)
        self.mission_objectives: list[MissionObjective] = list(
            getattr(self._scenario, "mission_objectives", None) or []
        )
        self.base_positions: dict[str, tuple[float, float]] = self._build_base_registry()
        self._tick_stages_done: set[str] = set()
        self._stage_intents: dict[str, ActionIntent] = {}
        self._pending_launch_intents: dict[str, ActionIntent] = {}
        self._spawn_scenario()
        self._initial_jet_ids: set[str] = set(self.jets.keys())
        self.total_jammed_agent_ticks: int = 0
        self.datacollector = DataCollector(
            model_reporters={
                "Tick": lambda m: m.tick,
                "TotalDistanceTraveled": lambda m: m.total_distance_traveled(),
                # strike_log — correct even if jets die with leftover payload (Phase 8/10)
                "TotalMissilesFired": lambda m: len(m.strike_log),
                "TargetsDestroyed": lambda m: sum(
                    1 for t in m.targets.values() if t.is_destroyed
                ),
                "JetsDestroyed": lambda m: sum(
                    1 for o in m.jet_outcomes.values() if o.outcome == "destroyed"
                ),
                "SamSitesDestroyed": lambda m: sum(
                    1 for tid in m.sam_sites if m.targets[tid].is_destroyed
                ),
                "AaaSitesDestroyed": lambda m: sum(
                    1
                    for tid in m.aaa_sites
                    if tid in m.targets and m.targets[tid].is_destroyed
                ),
                "TotalJammedAgentTicks": lambda m: m.total_jammed_agent_ticks,
            },
            # agenttype_reporters (not flat agent_reporters): SamSiteAgent/EwJammerAgent
            # share model.agents and lack jet attributes (see docs/05_Code_Review §3).
            agenttype_reporters={
                JetAgent: {
                    "TotalPayloadRemaining": "total_payload_remaining",
                    "DistanceTraveled": "distance_traveled",
                },
            },
        )
        self.datacollector.collect(self)  # capture the tick-0 baseline before any steps

    def _build_base_registry(self) -> dict[str, tuple[float, float]]:
        registry: dict[str, tuple[float, float]] = {}
        fixed = getattr(self._scenario, "fixed_airbases", None) or []
        if fixed:
            registry.update({b.base_id: tuple(b.position) for b in fixed})
        else:
            registry[HOME_BASE_ID] = self._home_base
        for cid, carrier in getattr(self, "carriers", {}).items():
            if carrier.available:
                registry[cid] = tuple(carrier.position)
        return registry

    def _refresh_base_positions(self) -> None:
        """Update dynamic carrier base XY after naval movement each tick."""
        for cid, carrier in self.carriers.items():
            if carrier.available:
                self.base_positions[cid] = tuple(carrier.position)
            else:
                self.base_positions.pop(cid, None)

    def base_position(self, base_id: str) -> tuple[float, float]:
        if base_id not in self.base_positions:
            raise KeyError(f"Unknown base_id '{base_id}'")
        return self.base_positions[base_id]

    def _base_available(self, base_id: str) -> bool:
        if base_id in self.carriers:
            return self.carriers[base_id].available
        return base_id in self.base_positions

    def _ensure_active_base(self, jet: JetAgent) -> str | None:
        """Resolve primary→alternate when the active base is destroyed.

        Returns the usable base_id, or None if the jet must abort recovery.
        """
        if self._base_available(jet.active_base_id):
            return jet.active_base_id
        alt = jet.base_assignment.alternate_base_id
        if alt is not None and self._base_available(alt):
            jet.active_base_id = alt
            jet.recovery_phase = "RTB" if jet.is_rtb else None
            return alt
        jet.recovery_abort = True
        self.mission_aborted = True
        return None

    def _active_base_xy(self, jet: JetAgent) -> tuple[float, float]:
        base_id = self._ensure_active_base(jet)
        if base_id is None:
            return self._home_base
        try:
            return self.base_position(base_id)
        except KeyError:
            return self._home_base

    def _primary_base_xy(self, jet: JetAgent) -> tuple[float, float]:
        """Compatibility accessor: current recovery base XY (carrier-aware)."""
        return self._active_base_xy(jet)

    def _is_carrier_base(self, base_id: str) -> bool:
        return base_id in self.carriers

    def _recovery_destination(self, jet: JetAgent) -> tuple[float, float]:
        base_id = self._ensure_active_base(jet)
        if base_id is None:
            return self._home_base
        if not self._is_carrier_base(base_id):
            return self.base_position(base_id)
        carrier = self.carriers[base_id]
        if jet.recovery_phase == "APPROACH":
            return approach_point_astern(
                carrier.position,
                carrier.heading,
                standoff=config.RECOVERY_APPROACH_STANDOFF,
            )
        return tuple(carrier.position)

    def _spawn_scenario(self) -> None:
        for t in self._scenario.targets:
            self.targets[t.target_id] = TargetState(
                target_id=t.target_id,
                position=t.position,
                hp_required=t.hp_required,
                target_type=t.target_type,
            )
        for s in self._scenario.sam_sites:
            self.targets[s.target_id] = TargetState(
                target_id=s.target_id,
                position=s.position,
                hp_required=s.hp_required,
                target_type=s.target_type,
            )
            loadout = getattr(s, "missile_loadout", None)
            self.sam_sites[s.target_id] = SamSiteAgent(
                self,
                s.target_id,
                s.position,
                missile_loadout=dict(loadout) if loadout is not None else None,
                cooldown_ticks=int(
                    getattr(s, "cooldown_ticks", config.DEFAULT_SAM_COOLDOWN_TICKS)
                ),
                sam_frame_ref=str(
                    getattr(s, "sam_frame_ref", config.DEFAULT_SAM_FRAME_REF)
                ),
            )
        for jam in self._scenario.jammers:
            self.jammers[jam.jammer_id] = EwJammerAgent(
                self, jam.jammer_id, jam.position
            )
        for c in getattr(self._scenario, "carriers", None) or []:
            frame = NAVAL_FRAME_CATALOG[c.frame_type]
            self.carriers[c.unit_id] = CarrierState(
                unit_id=c.unit_id,
                position=tuple(c.position),
                heading=float(c.heading) % 360.0,
                frame_type=c.frame_type,
                route=[tuple(p) for p in c.route],
                hold_position=bool(c.hold_position),
                hp=int(frame.hp),
                hp_max=int(frame.hp),
                prev_position=tuple(c.position),
            )
        for cv in getattr(self._scenario, "corvettes", None) or []:
            frame = NAVAL_FRAME_CATALOG[cv.frame_type]
            self.corvettes[cv.unit_id] = CorvetteState(
                unit_id=cv.unit_id,
                position=tuple(cv.position),
                heading=float(cv.heading) % 360.0,
                frame_type=cv.frame_type,
                route=[tuple(p) for p in cv.route],
                hold_position=bool(cv.hold_position),
                hp=int(frame.hp),
                hp_max=int(frame.hp),
                loadout={k: int(v) for k, v in dict(cv.loadout).items()},
                sam_cooldown_ticks=int(cv.sam_cooldown_ticks),
                ciws_cooldown_ticks=int(cv.ciws_cooldown_ticks),
                deck_gun_cooldown_ticks=int(cv.deck_gun_cooldown_ticks),
                prev_position=tuple(cv.position),
            )
        for aaa in getattr(self._scenario, "aaa_sites", None) or []:
            frame = AAA_FRAME_CATALOG[aaa.frame_type]
            hp = aaa.resolved_hp()
            self.targets[aaa.target_id] = TargetState(
                target_id=aaa.target_id,
                position=tuple(aaa.position),
                hp_required=hp,
                target_type="AAA_SITE",
            )
            self.aaa_sites[aaa.target_id] = AaaSiteState(
                target_id=aaa.target_id,
                position=tuple(aaa.position),
                heading=float(aaa.heading) % 360.0,
                faction=aaa.faction,
                frame_type=aaa.frame_type,
                ammo=aaa.resolved_ammo(),
                cooldown_ticks=int(frame.cooldown_ticks),
            )
        for tank in getattr(self._scenario, "tanks", None) or []:
            frame = TANK_FRAME_CATALOG[tank.frame_type]
            hp = tank.resolved_hp()
            self.tanks[tank.unit_id] = TankState(
                unit_id=tank.unit_id,
                faction=tank.faction,
                position=tuple(tank.position),
                heading=float(tank.heading) % 360.0,
                frame_type=tank.frame_type,
                hp=hp,
                hp_max=hp,
                ammo=tank.resolved_ammo(),
                order=tank.order,
                waypoints=[tuple(p) for p in tank.waypoints],
                cooldown_ticks=int(frame.cooldown_ticks),
            )
        # Register carrier bases after spawn so jets can resolve assignments.
        self._refresh_base_positions()
        for j in self._scenario.jets:
            assignment = getattr(j, "base_assignment", None)
            agent = JetAgent(
                self,
                agent_id=j.agent_id,
                payload=dict(j.payload),
                frame_type=j.frame_type,
                initial_heading=j.initial_heading,
                base_assignment=assignment,
                flare_capacity=getattr(j, "flare_capacity", None),
                flares_remaining=getattr(j, "flares_remaining", None),
                flare_cooldown_ticks=getattr(j, "flare_cooldown_ticks", None),
                flare_quality=getattr(j, "flare_quality", None),
                radar_warning_receiver=getattr(j, "radar_warning_receiver", None),
                notch_angle_window_deg=getattr(j, "notch_angle_window_deg", None),
                notch_min_speed=getattr(j, "notch_min_speed", None),
                notch_hold_ticks=getattr(j, "notch_hold_ticks", None),
            )
            self.space.place_agent(agent, j.position)
            self.jets[j.agent_id] = agent

    # ------------------------------------------------------------------
    # Staged tick API (Phase 37 F)
    # ------------------------------------------------------------------

    def _guard_stage(self, stage: str, *, requires: str | None = None) -> None:
        if stage in self._tick_stages_done:
            raise RuntimeError(f"stage '{stage}' already called this tick")
        if requires is not None and requires not in self._tick_stages_done:
            raise RuntimeError(
                f"stage '{stage}' requires '{requires}' to have run this tick"
            )
        if stage not in _STAGE_ORDER:
            raise RuntimeError(f"unknown stage '{stage}'")
        expected_idx = _STAGE_ORDER.index(stage)
        for prior in _STAGE_ORDER[:expected_idx]:
            if prior not in self._tick_stages_done:
                raise RuntimeError(
                    f"stage '{stage}' called out of order; missing '{prior}'"
                )
        self._tick_stages_done.add(stage)

    def stage_pre_move_obs(
        self, intents: dict[str, ActionIntent] | None = None
    ) -> None:
        """Stage 1: begin tick; apply heading / desired altitude from intents."""
        # Duplicate mid-tick call: pre_move already ran and the tick is incomplete.
        if (
            _STAGE_PRE_MOVE_OBS in self._tick_stages_done
            and _STAGE_RESOLVE_COMBAT not in self._tick_stages_done
        ):
            raise RuntimeError(
                f"stage '{_STAGE_PRE_MOVE_OBS}' already called this tick"
            )
        # New tick (fresh run or previous tick fully resolved).
        self._tick_stages_done = {_STAGE_PRE_MOVE_OBS}
        intents = intents or {}
        self._stage_intents = intents
        self.tick += 1
        # Mesa's own step scheduler (which normally advances these) is bypassed
        # entirely by the self.step = self._user_step workaround in __init__, so
        # self.steps/self.time are otherwise frozen at 0 forever. DataCollector's
        # agent_reporters/agenttype_reporters key their per-tick storage by
        # model.time internally, so leaving it frozen silently collapses every
        # tick's agent-level snapshot into a single overwritten entry — model
        # reporters are unaffected (they append to a plain list instead).
        self.steps = self.tick
        self.time = float(self.tick)
        # Tick flare cooldown for every live jet (including those without intents).
        for jet in self.jets.values():
            if jet.flare_cooldown_remaining > 0:
                jet.flare_cooldown_remaining -= 1
        for agent_id, intent in intents.items():
            jet = self.jets.get(agent_id)
            if jet is None:
                continue
            # NOTCH may override desired heading before rotation (docs/19 §13).
            if intent.maneuver_defense == "NOTCH" and intent.defensive_threat_id:
                self._apply_notch_heading_override(jet, intent.defensive_threat_id)
                jet.defensive_state = "NOTCH"
                jet.defensive_threat_id = intent.defensive_threat_id
            else:
                jet.desired_heading = intent.desired_heading
                if intent.maneuver_defense == "NONE" and jet.defensive_state == "NOTCH":
                    # Clear stale notch state when agent stops notching.
                    jet.defensive_state = "NONE"
                    jet.defensive_threat_id = None
                    jet.notch_hold_progress = 0
            if intent.desired_altitude is not None:
                jet.desired_altitude = intent.desired_altitude
            self._apply_heading_rotation(jet)

    def stage_apply_movement(
        self, intents: dict[str, ActionIntent] | None = None
    ) -> None:
        """Stage 2: naval platform movement, then jet MOVE (XY + altitude)."""
        self._guard_stage(_STAGE_APPLY_MOVEMENT, requires=_STAGE_PRE_MOVE_OBS)
        intents = intents if intents is not None else self._stage_intents

        # Phase 40/41: move naval platforms first so RTB / doctrine see live positions.
        ordered_carriers = sorted(self.carriers.values(), key=lambda c: c.unit_id)
        ordered_corvettes = sorted(self.corvettes.values(), key=lambda c: c.unit_id)
        surface_others = [*ordered_carriers, *ordered_corvettes]
        for carrier in ordered_carriers:
            step_carrier_movement(carrier, self.terrain, others=ordered_carriers)
            if carrier.hp <= 0:
                carrier.destroyed = True
        for corvette in ordered_corvettes:
            step_corvette_movement(corvette, self.terrain, others=surface_others)
            if corvette.hp <= 0:
                corvette.mark_destroyed()
        self._refresh_base_positions()

        # Phase 43: tank AI movement (select → turn/path → separate).
        self._step_all_tanks()

        for jet in self.jets.values():
            jet._pos_at_move_start = (float(jet.pos[0]), float(jet.pos[1]))
            jet.velocity_xy = (0.0, 0.0)
            # Fail over destroyed carrier bases before steering home.
            self._ensure_active_base(jet)
            if jet.is_rtb and jet.recovery_phase is None and not jet.recovery_abort:
                jet.recovery_phase = "RTB"

        moved: set[str] = set()
        for agent_id, intent in intents.items():
            jet = self.jets.get(agent_id)
            if jet is None:
                continue
            destination = intent.target_position
            # Notch requires motion — force a step along notch heading when IDLE.
            if intent.maneuver_defense == "NOTCH" and intent.desired_heading is None:
                # Heading already overridden in pre_move; synthesize destination.
                pass
            if intent.maneuver_defense == "NOTCH":
                max_speed = config.JET_FRAME_CATALOG[jet.frame_type].max_speed
                # World heading: 0=+y, 90=+x → displacement (sin, cos) in that frame.
                h = math.radians(jet.heading)
                destination = (
                    jet.pos[0] + max_speed * math.sin(h),
                    jet.pos[1] + max_speed * math.cos(h),
                )
            elif intent.action_type == "MOVE":
                destination = intent.target_position
            else:
                destination = None
            # Carrier RTB: retarget to live recovery geometry each tick.
            if jet.is_rtb and not jet.recovery_abort:
                destination = self._recovery_destination(jet)
                if jet.recovery_phase == "APPROACH":
                    jet.desired_altitude = config.RECOVERY_ALTITUDE
                    base_id = jet.active_base_id
                    if self._is_carrier_base(base_id):
                        jet.desired_heading = self.carriers[base_id].heading
                        self._apply_heading_rotation(jet)
            if destination is not None:
                self._apply_move(jet, destination)
                self._apply_altitude(jet)
                moved.add(agent_id)

        # Carrier-based RTB jets with no intent this tick still home to the
        # live deck. Fixed-base RTB keeps prior semantics (MOVE intent required)
        # so landing-radius boundary tests remain meaningful.
        for jet in self.jets.values():
            if jet.agent_id in moved or not jet.is_rtb or jet.recovery_abort:
                continue
            if not self._is_carrier_base(jet.active_base_id):
                continue
            destination = self._recovery_destination(jet)
            if jet.recovery_phase == "APPROACH":
                jet.desired_altitude = config.RECOVERY_ALTITUDE
                base_id = jet.active_base_id
                jet.desired_heading = self.carriers[base_id].heading
                self._apply_heading_rotation(jet)
            self._apply_move(jet, destination)
            self._apply_altitude(jet)

        # Record velocity from this tick's displacement (IDLE → zero).
        for jet in self.jets.values():
            if jet._pos_at_move_start is None:
                continue
            ox, oy = jet._pos_at_move_start
            jet.velocity_xy = (float(jet.pos[0]) - ox, float(jet.pos[1]) - oy)
            self._update_recovery_phase(jet)

    def stage_post_move_obs(
        self, intents: dict[str, ActionIntent] | None = None
    ) -> None:
        """Stage 3: post-movement observation hook (combat intents buffered)."""
        self._guard_stage(_STAGE_POST_MOVE_OBS, requires=_STAGE_APPLY_MOVEMENT)
        if intents is not None:
            self._stage_intents = intents
        # Bridge will gather launch/direct-fire intents here in later phases.

    def stage_register_launches(self) -> None:
        """Stage 4: register jet guided launches + SAM missile launches."""
        self._guard_stage(_STAGE_REGISTER_LAUNCHES, requires=_STAGE_POST_MOVE_OBS)
        intents = self._stage_intents
        self.launch_acks = []
        # Stable order: sorted agent ids then SAM ids.
        for agent_id in sorted(intents.keys()):
            intent = intents[agent_id]
            jet = self.jets.get(agent_id)
            if jet is None:
                continue
            weapon_type = intent.weapon_type
            if weapon_type is None:
                continue
            weapon = config.WEAPON_CATALOG.get(weapon_type)  # type: ignore[arg-type]
            if weapon is None or weapon.guidance == "NONE":
                continue
            # LAUNCH is the preferred path; STRIKE with a guided weapon is
            # accepted as compatibility and still goes through the ack API.
            if intent.action_type not in ("LAUNCH", "STRIKE"):
                continue
            ack = self.try_launch_from_jet(jet, intent.target_id, weapon_type)
            self.launch_acks.append(ack)
        self._register_sam_launches()
        self._register_corvette_sam_launches()

    def stage_countermeasures(self) -> None:
        """Stage 5: flares (IR) and notch geometry (radar) before combat."""
        self._guard_stage(_STAGE_COUNTERMEASURES, requires=_STAGE_REGISTER_LAUNCHES)
        intents = self._stage_intents
        # Stable agent order for insertion-order determinism.
        for agent_id in sorted(intents.keys()):
            intent = intents[agent_id]
            jet = self.jets.get(agent_id)
            if jet is None:
                continue
            if intent.countermeasure_action == "FLARE":
                self._apply_flare(jet, intent.defensive_threat_id)
            if intent.maneuver_defense == "NOTCH" and intent.defensive_threat_id:
                self._evaluate_notch(jet, intent.defensive_threat_id)

    def _missile_by_id(self, missile_id: str) -> MissileState | None:
        for m in self.missiles:
            if m.missile_id == missile_id:
                return m
        return None

    def _support_xy(self, missile: MissileState) -> tuple[float, float] | None:
        sid = missile.support_source_id
        if sid is None:
            return None
        if sid in self.jets:
            j = self.jets[sid]
            return (float(j.pos[0]), float(j.pos[1]))
        if sid in self.sam_sites:
            return self.sam_sites[sid].position
        if sid in self.targets:
            return self.targets[sid].position
        return None

    def _apply_notch_heading_override(self, jet: JetAgent, threat_id: str) -> None:
        missile = self._missile_by_id(threat_id)
        if missile is None or missile.status != "IN_FLIGHT":
            return
        if missile.guidance != "RADAR":
            return
        tgt_pos = (float(jet.pos[0]), float(jet.pos[1]), float(jet.altitude))
        active = is_active_terminal_guidance(missile, tgt_pos)
        los = notch_reference_bearing_deg(
            jet_pos=(float(jet.pos[0]), float(jet.pos[1])),
            jet_velocity_xy=jet.velocity_xy,
            support_pos=self._support_xy(missile),
            missile_pos=missile.position,
            active_terminal=active,
        )
        if los is None:
            return
        jet.desired_heading = pick_notch_heading(jet.heading, los)

    def _apply_flare(self, jet: JetAgent, threat_id: str | None) -> None:
        self.flare_attempts += 1
        if jet.flares_remaining <= 0 or jet.flare_cooldown_remaining > 0:
            self.missile_events.append(
                {
                    "event": "flare",
                    "tick": self.tick,
                    "agent_id": jet.agent_id,
                    "threat_id": threat_id,
                    "accepted": False,
                    "reason": (
                        "no_flares" if jet.flares_remaining <= 0 else "cooldown"
                    ),
                }
            )
            return
        jet.flares_remaining -= 1
        jet.flare_cooldown_remaining = jet.flare_cooldown_ticks
        if jet.defensive_state != "NOTCH":
            jet.defensive_state = "FLARE"
        if threat_id:
            jet.defensive_threat_id = threat_id
        self.missile_events.append(
            {
                "event": "flare",
                "tick": self.tick,
                "agent_id": jet.agent_id,
                "threat_id": threat_id,
                "accepted": True,
                "flares_remaining": jet.flares_remaining,
            }
        )
        # Affect nearby inbound IR missiles targeting this jet (short window).
        jet_xy = (float(jet.pos[0]), float(jet.pos[1]))
        for missile in sorted(
            [m for m in self.missiles if m.status == "IN_FLIGHT"],
            key=lambda m: m.missile_id,
        ):
            if missile.target_id != jet.agent_id:
                continue
            if missile.guidance != "INFRARED":
                continue  # flares never affect radar
            dist = math.hypot(
                missile.position[0] - jet_xy[0],
                missile.position[1] - jet_xy[1],
            )
            if dist > FLARE_EFFECT_RANGE:
                continue
            weapon = config.WEAPON_CATALOG[missile.weapon_type]
            aspect = closing_aspect(
                jet_xy,
                (missile.position[0], missile.position[1]),
                missile.heading_xy_rad,
            )
            p = flare_decoy_probability(
                flare_quality=jet.flare_quality,
                countermeasure_resistance=weapon.countermeasure_resistance,
                range_to_missile=dist,
                closing_aspect=aspect,
            )
            if roll_flare_decoy(
                self.combat_seed,
                missile_id=missile.missile_id,
                jet_id=jet.agent_id,
                tick=self.tick,
                probability=p,
            ):
                missile.status = "DECOYED"
                self.flare_successes += 1
                self._record_missile_terminal(missile, "DECOYED")

    def _evaluate_notch(self, jet: JetAgent, threat_id: str) -> None:
        self.notch_attempts += 1
        missile = self._missile_by_id(threat_id)
        event_base = {
            "event": "notch",
            "tick": self.tick,
            "agent_id": jet.agent_id,
            "threat_id": threat_id,
        }
        if missile is None or missile.status != "IN_FLIGHT":
            self.missile_events.append({**event_base, "result": "no_threat"})
            jet.notch_hold_progress = 0
            return
        if missile.guidance != "RADAR":
            # Notching has zero IR effect.
            self.missile_events.append({**event_base, "result": "wrong_guidance"})
            jet.notch_hold_progress = 0
            return
        if missile.target_id != jet.agent_id:
            self.missile_events.append({**event_base, "result": "not_targeted"})
            jet.notch_hold_progress = 0
            return
        tgt_pos = (float(jet.pos[0]), float(jet.pos[1]), float(jet.altitude))
        active = is_active_terminal_guidance(missile, tgt_pos)
        los = notch_reference_bearing_deg(
            jet_pos=(float(jet.pos[0]), float(jet.pos[1])),
            jet_velocity_xy=jet.velocity_xy,
            support_pos=self._support_xy(missile),
            missile_pos=missile.position,
            active_terminal=active,
        )
        if los is None:
            self.missile_events.append({**event_base, "result": "no_los_ref"})
            jet.notch_hold_progress = 0
            return
        ok = notch_geometry_ok(
            velocity_xy=jet.velocity_xy,
            los_bearing_deg=los,
            angle_window_deg=jet.notch_angle_window_deg,
            min_speed=jet.notch_min_speed,
        )
        if not ok:
            jet.notch_hold_progress = 0
            self.missile_events.append(
                {
                    **event_base,
                    "result": "geometry_fail",
                    "active_terminal": active,
                    "speed": math.hypot(*jet.velocity_xy),
                }
            )
            return
        jet.notch_hold_progress += 1
        if jet.notch_hold_progress < jet.notch_hold_ticks:
            self.missile_events.append(
                {
                    **event_base,
                    "result": "holding",
                    "hold": jet.notch_hold_progress,
                    "required": jet.notch_hold_ticks,
                    "active_terminal": active,
                }
            )
            return
        # Hold complete — keyed break-lock roll.
        self.notch_completions += 1
        weapon = config.WEAPON_CATALOG[missile.weapon_type]
        evasion = config.JET_FRAME_CATALOG[jet.frame_type].evasion_bonus
        p = notch_break_probability(
            countermeasure_resistance=weapon.countermeasure_resistance,
            active_terminal=active,
            evasion_bonus=evasion,
        )
        broke = roll_notch_break(
            self.combat_seed,
            missile_id=missile.missile_id,
            jet_id=jet.agent_id,
            tick=self.tick,
            probability=p,
        )
        jet.notch_hold_progress = 0
        if broke:
            missile.status = "LOST_LOCK"
            self.notch_lock_breaks += 1
            self._record_missile_terminal(missile, "LOST_LOCK")
            self.missile_events.append(
                {
                    **event_base,
                    "result": "lock_break",
                    "active_terminal": active,
                    "probability": p,
                }
            )
        else:
            self.missile_events.append(
                {
                    **event_base,
                    "result": "lock_hold",
                    "active_terminal": active,
                    "probability": p,
                }
            )

    def stage_resolve_combat(
        self, intents: dict[str, ActionIntent] | None = None
    ) -> None:
        """Stage 6: direct strikes, missile flyout/impact, fuel/landing, metrics."""
        self._guard_stage(_STAGE_RESOLVE_COMBAT, requires=_STAGE_COUNTERMEASURES)
        intents = intents if intents is not None else self._stage_intents
        for agent_id, intent in intents.items():
            jet = self.jets.get(agent_id)
            if jet is None:
                continue
            if intent.action_type == "STRIKE":
                weapon_type = intent.weapon_type
                weapon = (
                    config.WEAPON_CATALOG.get(weapon_type)  # type: ignore[arg-type]
                    if weapon_type
                    else None
                )
                # Guided weapons already handled in stage_register_launches.
                if weapon is not None and weapon.guidance != "NONE":
                    continue
                self._apply_strike(jet, intent.target_id, intent.weapon_type)
            # LAUNCH never applies instant damage here.
        # Phase 41/42: gun bursts (corvette CIWS/deck + AAA), then missiles.
        pending_gun = self._resolve_corvette_gun_layers()
        pending_gun.extend(self._resolve_aaa_gun_bursts())
        pending_gun.extend(self._resolve_tank_gun_bursts())
        self._advance_and_resolve_missiles(pending_gun_damage=pending_gun)
        # Fuel burn — flat per tick, every live jet, regardless of movement
        # (design doc §5). SAM-killed jets were already reaped above.
        for jet in self.jets.values():
            jet.fuel -= config.JET_FRAME_CATALOG[jet.frame_type].fuel_burn_per_tick
        # Landing must run BEFORE the fuel-exhaustion reap so a jet that
        # arrives home with exactly 0 fuel this tick is a safe landing, not a
        # dry-tank loss. Snapshot the dict because this mutates self.jets.
        for jet in list(self.jets.values()):
            if not jet.is_rtb or jet.recovery_abort:
                continue
            if self._try_recover_jet(jet):
                continue
        # Fuel exhaustion is an independent condition — do not zero hp.
        destroyed_ids = [
            aid for aid, jet in self.jets.items() if jet.is_destroyed or jet.fuel <= 0
        ]
        for aid in destroyed_ids:
            self._remove_jet(self.jets[aid], "destroyed")
        self._evaluate_termination()
        self.total_jammed_agent_ticks += sum(
            1 for jet in self.jets.values() if self._jet_is_jammed(jet)
        )
        self.datacollector.collect(self)
        assert self.all_jet_ids() == self._initial_jet_ids

    def step(self, intents: dict[str, ActionIntent] | None = None) -> None:
        """Compatibility wrapper: run all six stages once in order."""
        intents = intents or {}
        self.stage_pre_move_obs(intents)
        self.stage_apply_movement(intents)
        self.stage_post_move_obs(intents)
        self.stage_register_launches()
        self.stage_countermeasures()
        self.stage_resolve_combat(intents)

    def _evaluate_termination(self) -> None:
        if self.mission_objectives:
            destroyed_map = {
                tid: t.is_destroyed for tid, t in self.targets.items()
            }
            for aid, outcome in self.jet_outcomes.items():
                if outcome.outcome == "destroyed":
                    destroyed_map[aid] = True
            for cid, carrier in self.carriers.items():
                if carrier.destroyed or carrier.hp <= 0:
                    destroyed_map[cid] = True
            for vid, corvette in self.corvettes.items():
                if corvette.is_destroyed:
                    destroyed_map[vid] = True
            for tid, tank in self.tanks.items():
                if tank.is_destroyed:
                    destroyed_map[tid] = True
            evaluation = evaluate_mission(
                self.mission_objectives,
                tick=self.tick,
                destroyed_ids=destroyed_map,
                returned_ids=set(self.jets_returned_to_base),
                no_friendly_platforms=len(self.jets) == 0,
                any_friendly_returned=bool(self.jets_returned_to_base),
                friendly_can_progress=len(self.jets) > 0 and not self.mission_aborted,
            )
            if evaluation.result != "RUNNING":
                self.running = False
            self.mission_aborted = evaluation.mission_aborted or self.mission_aborted
            self.swarm_wiped_out = evaluation.swarm_wiped_out
            return

        # Legacy destroy-all / wipeout (v2 scenarios without mission_objectives).
        if all(t.is_destroyed for t in self.targets.values()):
            self.running = False  # success — includes SAM_Site_1 now
        elif len(self.jets) == 0:
            self.running = False
            if self.jets_returned_to_base:
                # At least one jet made it home; not a wipeout. "mission
                # aborted" specifically means the swarm ran out of fuel and
                # had to go home before finishing the objective — not a
                # general-purpose abort flag.
                self.mission_aborted = True
            else:
                self.swarm_wiped_out = True  # every jet that left play was destroyed

    def all_jet_ids(self) -> set[str]:
        """Every jet that ever existed this run, alive or not. Must always equal
        self._initial_jet_ids -- if it doesn't, a jet was removed from self.jets
        without a corresponding JetOutcome being recorded, which is exactly the
        class of bug this phase exists to fix. See tests/test_world.py."""
        return set(self.jets) | set(self.jet_outcomes)

    def jet_travel_stats(self) -> dict[str, tuple[float, dict[str, int]]]:
        """Distance and leftover payload for every jet that has existed this run.

        Live jets contribute their current values; removed jets contribute the
        snapshot recorded in ``jet_outcomes``. Shared by DataCollector and
        ``metrics.summarize`` so a landing/destruction cannot drop distance
        from the exported time series.
        """
        live = {
            aid: (jet.distance_traveled, dict(jet.payload))
            for aid, jet in self.jets.items()
        }
        past = {
            aid: (o.distance_traveled, dict(o.final_payload_remaining))
            for aid, o in self.jet_outcomes.items()
        }
        return {**past, **live}

    def total_distance_traveled(self) -> float:
        return sum(distance for distance, _ in self.jet_travel_stats().values())

    def _remove_jet(
        self, jet: JetAgent, outcome: Literal["destroyed", "returned_to_base"]
    ) -> None:
        """Snapshot then drop from play. Every removal path must go through here
        so all_jet_ids() stays equal to _initial_jet_ids."""
        self.jet_outcomes[jet.agent_id] = JetOutcome(
            agent_id=jet.agent_id,
            frame_type=jet.frame_type,
            outcome=outcome,
            distance_traveled=jet.distance_traveled,
            final_payload_remaining=dict(jet.payload),
        )
        if outcome == "returned_to_base":
            self.jets_returned_to_base.add(jet.agent_id)
        self.space.remove_agent(jet)
        jet.remove()
        del self.jets[jet.agent_id]
        assert self.all_jet_ids() == self._initial_jet_ids

    def has_combat_los(
        self,
        start_xy: tuple[float, float],
        start_z: float,
        end_xy: tuple[float, float],
        end_z: float,
    ) -> bool:
        """Authoritative 2.5D LOS against ``self.terrain`` (Phase 34)."""
        return has_line_of_sight(
            self.terrain,
            (start_xy[0], start_xy[1], start_z),
            (end_xy[0], end_xy[1], end_z),
        )

    def _allocate_missile_id(self, shooter_id: str, weapon_type: str) -> str:
        self._next_missile_seq += 1
        return f"M-{self.tick}-{shooter_id}-{weapon_type}-{self._next_missile_seq}"

    @property
    def active_missiles(self) -> list[MissileState]:
        """In-flight missiles only (authoritative viewer/trace source)."""
        return [m for m in self.missiles if m.status == "IN_FLIGHT"]

    def reserved_damage_for(self, target_id: str) -> int:
        return sum(int(r["amount"]) for r in self.damage_reservations.get(target_id, []))

    def uncovered_hp(self, target_id: str) -> int:
        """HP still not covered by in-flight nominal reservations."""
        if target_id in self.targets:
            remaining = self.targets[target_id].hp_remaining
        elif target_id in self.jets:
            remaining = self.jets[target_id].hp
        elif target_id in self.carriers:
            c = self.carriers[target_id]
            remaining = 0 if c.destroyed else c.hp
        elif target_id in self.corvettes:
            c = self.corvettes[target_id]
            remaining = 0 if c.is_destroyed else c.hp
        elif target_id in self.tanks:
            t = self.tanks[target_id]
            remaining = 0 if t.is_destroyed else t.hp
        else:
            return 0
        return max(0, int(remaining) - self.reserved_damage_for(target_id))

    def _reserve_damage(self, missile_id: str, target_id: str, amount: int) -> None:
        self.damage_reservations.setdefault(target_id, []).append(
            {"missile_id": missile_id, "amount": int(amount)}
        )

    def _release_reservation(self, missile_id: str, target_id: str) -> None:
        entries = self.damage_reservations.get(target_id, [])
        self.damage_reservations[target_id] = [
            e for e in entries if e["missile_id"] != missile_id
        ]
        if not self.damage_reservations[target_id]:
            del self.damage_reservations[target_id]

    def try_launch(
        self,
        req: LaunchRequest,
        *,
        consume_ammo=None,
        log_strike: bool = True,
    ) -> LaunchAcknowledgement:
        """Validate and create a missile. Ammo is consumed only on accept.

        ``consume_ammo`` is called with no args after validation succeeds and
        must decrement the shooter's magazine.
        """
        reason = validate_launch(req)
        if reason is not None:
            return LaunchAcknowledgement(
                accepted=False, missile_id=None, reason=reason
            )
        weapon = config.WEAPON_CATALOG[req.weapon_type]
        # Refuse over-launch only while the target still has HP that is
        # already fully covered by in-flight reservations. A stale shot on an
        # already-dead target is still accepted (ammo consumed) so guided
        # weapons match Phase 1 STRIKE semantics.
        live_hp = 0
        if req.target_id in self.targets:
            live_hp = self.targets[req.target_id].hp_remaining
        elif req.target_id in self.jets:
            live_hp = self.jets[req.target_id].hp
        elif req.target_id in self.carriers:
            c = self.carriers[req.target_id]
            live_hp = 0 if c.destroyed else c.hp
        elif req.target_id in self.corvettes:
            c = self.corvettes[req.target_id]
            live_hp = 0 if c.is_destroyed else c.hp
        elif req.target_id in self.tanks:
            t = self.tanks[req.target_id]
            live_hp = 0 if t.is_destroyed else t.hp
        if live_hp > 0 and self.uncovered_hp(req.target_id) <= 0:
            return LaunchAcknowledgement(
                accepted=False, missile_id=None, reason="target_hp_covered"
            )
        if consume_ammo is not None:
            consume_ammo()
        mid = self._allocate_missile_id(req.shooter_id, req.weapon_type)
        missile = create_missile(missile_id=mid, req=req, launch_tick=self.tick)
        self.missiles.append(missile)
        self._reserve_damage(mid, req.target_id, weapon.damage_per_hit)
        self.missile_events.append(
            {
                "event": "launch",
                "tick": self.tick,
                "missile_id": mid,
                "shooter_id": req.shooter_id,
                "target_id": req.target_id,
                "weapon_type": req.weapon_type,
                "guidance": weapon.guidance,
            }
        )
        if req.weapon_type == "AGM84":
            self.agm84_launches += 1
        if req.shooter_id in self.corvettes and req.weapon_type == "SAM_RADAR":
            self.corvette_sam_launches += 1
        # strike_log remains the jet-ordnance fired counter for metrics/PoC.
        if log_strike:
            self.strike_log.append(
                {
                    "tick": self.tick,
                    "agent_id": req.shooter_id,
                    "target_id": req.target_id,
                    "weapon_type": req.weapon_type,
                }
            )
        return LaunchAcknowledgement(accepted=True, missile_id=mid, reason=None)

    def try_launch_from_jet(
        self, jet: JetAgent, target_id: str | None, weapon_type: str | None
    ) -> LaunchAcknowledgement:
        if target_id is None or weapon_type is None:
            return LaunchAcknowledgement(
                accepted=False, missile_id=None, reason="missing_args"
            )
        corvette = self.corvettes.get(target_id)
        carrier = self.carriers.get(target_id)
        target = self.targets.get(target_id)
        if corvette is not None and not corvette.is_destroyed:
            tgt_pos = corvette.position
            tgt_z = 0.0
            tgt_faction = corvette.faction
            tgt_class = corvette.target_class
            tgt_sig = corvette.signature
            tgt_type: str | None = "SURFACE_VESSEL"
        elif carrier is not None and carrier.available:
            tgt_pos = carrier.position
            tgt_z = 0.0
            tgt_faction = carrier.faction
            tgt_class = carrier.target_class
            tgt_sig = config.TARGET_SIGNATURE_DEFAULTS["SURFACE_VESSEL"]
            tgt_type = "SURFACE_VESSEL"
        elif target is not None:
            tgt_pos = target.position
            tgt_z = config.GROUND_TARGET_HEIGHT
            tgt_faction = "ENEMY"
            tgt_class = target_class_for_target_type(target.target_type)
            tgt_sig = target.signature
            tgt_type = target.target_type
        else:
            return LaunchAcknowledgement(
                accepted=False, missile_id=None, reason="unknown_target"
            )
        has_los = self.has_combat_los(
            jet.pos,
            jet.altitude,
            tgt_pos,
            tgt_z,
        )
        req = LaunchRequest(
            shooter_id=jet.agent_id,
            target_id=target_id,
            weapon_type=weapon_type,  # type: ignore[arg-type]
            shooter_position=(jet.pos[0], jet.pos[1], jet.altitude),
            target_position=(tgt_pos[0], tgt_pos[1], tgt_z),
            shooter_faction=jet.faction,
            target_faction=tgt_faction,
            target_class=tgt_class,
            target_signature=tgt_sig,
            target_type=tgt_type,
            has_los=has_los,
            support_source_id=jet.agent_id,
            support_alive=True,
            firing_heading_deg=jet.heading,
            ammo_available=jet.payload.get(weapon_type, 0),
        )

        def _consume() -> None:
            jet.payload[weapon_type] = jet.payload.get(weapon_type, 0) - 1

        return self.try_launch(req, consume_ammo=_consume)

    def _sam_preferred_weapon(self, sam: SamSiteAgent) -> str | None:
        for wt in ("SAM_RADAR", "SAM_IR"):
            if sam.missile_loadout.get(wt, 0) > 0:
                return wt
        return None

    def _register_sam_launches(self) -> None:
        for sam_id in sorted(self.sam_sites.keys()):
            sam = self.sam_sites[sam_id]
            if self.targets[sam_id].is_destroyed:
                continue
            if sam.cooldown_remaining > 0:
                sam.cooldown_remaining -= 1
                continue
            weapon_type = self._sam_preferred_weapon(sam)
            if weapon_type is None:
                continue
            # Engage nearest in-range hostile jet with LOS (stable jet id order).
            candidates: list[tuple[float, str]] = []
            for jet_id in sorted(self.jets.keys()):
                jet = self.jets[jet_id]
                dist = math.dist(jet.pos, sam.position)
                if dist > config.SAM_ENGAGEMENT_RANGE:
                    continue
                if not self.has_combat_los(
                    sam.position,
                    config.SAM_SENSOR_HEIGHT,
                    jet.pos,
                    jet.altitude,
                ):
                    continue
                candidates.append((dist, jet_id))
            if not candidates:
                continue
            candidates.sort()
            jet = self.jets[candidates[0][1]]
            req = LaunchRequest(
                shooter_id=sam_id,
                target_id=jet.agent_id,
                weapon_type=weapon_type,  # type: ignore[arg-type]
                shooter_position=(
                    sam.position[0],
                    sam.position[1],
                    config.SAM_SENSOR_HEIGHT,
                ),
                target_position=(jet.pos[0], jet.pos[1], jet.altitude),
                shooter_faction="ENEMY",
                target_faction=jet.faction,
                target_class=jet.target_class,
                target_signature=jet.signature,
                target_type=None,
                has_los=True,
                support_source_id=sam_id,
                support_alive=True,
                firing_heading_deg=None,
                ammo_available=sam.missile_loadout.get(weapon_type, 0),
            )

            def _consume(s=sam, wt=weapon_type) -> None:
                s.missile_loadout[wt] = s.missile_loadout.get(wt, 0) - 1
                s.cooldown_remaining = s.cooldown_ticks

            self.try_launch(req, consume_ammo=_consume, log_strike=False)

    def _target_position_for_missile(
        self, missile: MissileState
    ) -> tuple[float, float, float] | None:
        jet = self.jets.get(missile.target_id)
        if jet is not None:
            return (jet.pos[0], jet.pos[1], jet.altitude)
        target = self.targets.get(missile.target_id)
        if target is not None:
            return (
                target.position[0],
                target.position[1],
                config.GROUND_TARGET_HEIGHT,
            )
        corvette = self.corvettes.get(missile.target_id)
        if corvette is not None and not corvette.is_destroyed:
            return (corvette.position[0], corvette.position[1], 0.0)
        carrier = self.carriers.get(missile.target_id)
        if carrier is not None and not carrier.destroyed:
            return (carrier.position[0], carrier.position[1], 0.0)
        tank = self.tanks.get(missile.target_id)
        if tank is not None and not tank.is_destroyed:
            return (tank.position[0], tank.position[1], 0.0)
        return None

    def _support_alive(self, missile: MissileState) -> bool:
        if missile.support_source_id is None:
            return True
        sid = missile.support_source_id
        if sid in self.jets:
            return not self.jets[sid].is_destroyed
        if sid in self.sam_sites:
            return not self.targets[sid].is_destroyed
        if sid in self.corvettes:
            return not self.corvettes[sid].is_destroyed
        return False

    def _record_missile_terminal(self, missile: MissileState, outcome: str) -> None:
        self.missile_events.append(
            {
                "event": "impact",
                "tick": self.tick,
                "missile_id": missile.missile_id,
                "outcome": outcome,
                "target_id": missile.target_id,
                "weapon_type": missile.weapon_type,
                "shooter_id": missile.shooter_id,
            }
        )
        # Reservation becomes real damage on HIT (released after apply);
        # every non-HIT outcome releases immediately.
        if outcome != "HIT":
            self._release_reservation(missile.missile_id, missile.target_id)

    def _advance_and_resolve_missiles(
        self,
        pending_gun_damage: list[tuple[str, str, int]] | None = None,
    ) -> None:
        # Stable processing order by missile_id.
        in_flight = sorted(
            [m for m in self.missiles if m.status == "IN_FLIGHT"],
            key=lambda m: m.missile_id,
        )
        pending_damage: list[tuple[MissileState, str, int]] = []
        for missile in in_flight:
            tgt_pos = self._target_position_for_missile(missile)
            if tgt_pos is None:
                missile.status = "LOST_LOCK"
                self._record_missile_terminal(missile, missile.status)
                continue
            old = advance_missile(
                missile, tgt_pos, support_alive=self._support_alive(missile)
            )
            if missile.status == "LOST_LOCK":
                self._record_missile_terminal(missile, missile.status)
                continue
            if missile.status == "IN_FLIGHT" and terminal_contact(
                missile, old, tgt_pos
            ):
                outcome = resolve_impact(missile, self.combat_seed)
                # Phase 41: CIWS may intercept HIT-bound anti-ship missiles
                # before simultaneous impact resolution.
                if outcome == "HIT" and missile.weapon_type == "AGM84":
                    if self._try_ciws_intercept_missile(missile):
                        continue
                self._record_missile_terminal(missile, outcome)
                if outcome == "HIT" and not missile.damage_applied:
                    weapon = config.WEAPON_CATALOG[missile.weapon_type]
                    pending_damage.append(
                        (missile, missile.target_id, weapon.damage_per_hit)
                    )
                continue
            apply_fuel_expiry(missile)
            if missile.status != "IN_FLIGHT":
                self._record_missile_terminal(missile, missile.status)
                continue

        # Simultaneous gun + missile damage application (docs/19 §13).
        for shooter_id, target_id, raw_damage in pending_gun_damage or []:
            self._apply_platform_damage(target_id, raw_damage, shooter_id=shooter_id)

        for missile, target_id, raw_damage in pending_damage:
            if missile.damage_applied:
                continue
            missile.damage_applied = True
            self._apply_platform_damage(
                target_id, raw_damage, shooter_id=missile.shooter_id, missile=missile
            )
            self._release_reservation(missile.missile_id, target_id)

        destroyed_ids = [aid for aid, jet in self.jets.items() if jet.is_destroyed]
        for aid in destroyed_ids:
            self._remove_jet(self.jets[aid], "destroyed")

        # Drop resolved missiles from the active list.
        self.missiles = [m for m in self.missiles if m.status == "IN_FLIGHT"]

    def _apply_platform_damage(
        self,
        target_id: str,
        raw_damage: int,
        *,
        shooter_id: str | None = None,
        missile: MissileState | None = None,
    ) -> None:
        if target_id in self.targets:
            t = self.targets[target_id]
            t.hp_remaining = max(0, t.hp_remaining - raw_damage)
            return
        if target_id in self.jets:
            jet = self.jets[target_id]
            evasion = config.JET_FRAME_CATALOG[jet.frame_type].evasion_bonus
            damage = max(0, raw_damage - evasion)
            jet.hp -= damage
            if jet.hp <= 0 and missile is not None:
                g = str(missile.guidance)
                self.deaths_by_guidance[g] = self.deaths_by_guidance.get(g, 0) + 1
            if jet.hp <= 0 and shooter_id is not None and shooter_id in self.aaa_sites:
                self.aircraft_losses_to_aaa += 1
                self.aaa_kills += 1
            return
        if target_id in self.carriers:
            c = self.carriers[target_id]
            if c.destroyed:
                return
            c.hp = max(0, c.hp - raw_damage)
            if c.hp <= 0:
                c.destroyed = True
            return
        if target_id in self.corvettes:
            c = self.corvettes[target_id]
            if c.is_destroyed:
                return
            was_alive = True
            c.hp = max(0, c.hp - raw_damage)
            if c.hp <= 0:
                c.mark_destroyed()
                if was_alive:
                    self.corvette_losses += 1
            if missile is not None and missile.weapon_type == "AGM84":
                self.agm84_hits += 1
            return
        if target_id in self.tanks:
            tank = self.tanks[target_id]
            if tank.is_destroyed:
                return
            damage = max(0, raw_damage - tank.armor)
            tank.hp = max(0, tank.hp - damage)
            if tank.hp <= 0:
                tank.mark_destroyed()
                if tank.faction == "FRIENDLY":
                    self.tank_losses_friendly += 1
                else:
                    self.tank_losses_enemy += 1
                if shooter_id is not None and shooter_id in self.tanks:
                    shooter = self.tanks[shooter_id]
                    if shooter.faction == "FRIENDLY":
                        self.tank_kills_friendly += 1
                    else:
                        self.tank_kills_enemy += 1
            return

    def _try_ciws_intercept_missile(self, missile: MissileState) -> bool:
        """Attempt CIWS intercept on a HIT-bound inbound anti-ship missile.

        Returns True if intercepted (terminal recorded as INTERCEPTED).
        """
        defender = self.corvettes.get(missile.target_id)
        if defender is None or defender.is_destroyed:
            return False
        if defender.loadout.get("CIWS", 0) <= 0:
            return False
        if defender.ciws_cooldown_remaining > 0:
            return False
        dist = math.dist(
            defender.position, (missile.position[0], missile.position[1])
        )
        if dist > CIWS_GUN.range:
            return False
        los = self.has_combat_los(
            defender.position,
            NAVAL_FRAME_CATALOG[defender.frame_type].sight_height,
            (missile.position[0], missile.position[1]),
            float(missile.position[2]),
        )
        defender.burst_seq += 1
        req = GunBurstRequest(
            shooter_id=defender.unit_id,
            target_id=missile.missile_id,
            gun=CIWS_GUN,
            shooter_faction=defender.faction,
            target_faction="FRIENDLY",
            target_class="IN_FLIGHT_MISSILE",
            shooter_position=defender.position,
            target_position=(missile.position[0], missile.position[1]),
            shooter_heading_deg=defender.heading,
            target_altitude=float(missile.position[2]),
            ammo_available=defender.loadout.get("CIWS", 0),
            has_los=los,
            combat_seed=self.combat_seed,
            tick=self.tick,
            burst_index=defender.burst_seq,
        )
        result = resolve_gun_burst(req)
        self.ciws_attempts += 1
        self.gun_events.append(
            {
                "event": "gun_burst",
                "tick": self.tick,
                "shooter_id": defender.unit_id,
                "target_id": missile.missile_id,
                "gun_id": "CIWS",
                "layer": "CIWS_MISSILE",
                "accepted": result.accepted,
                "hits": result.hits,
                "damage": result.damage,
                "reason": result.reason,
            }
        )
        if not result.accepted:
            return False
        defender.loadout["CIWS"] = defender.loadout.get("CIWS", 0) - result.ammo_consumed
        defender.ciws_cooldown_remaining = defender.ciws_cooldown_ticks
        if result.hits > 0:
            missile.status = "INTERCEPTED"
            self.ciws_intercepts += 1
            self._record_missile_terminal(missile, "INTERCEPTED")
            self.gun_events.append(
                {
                    "event": "ciws_intercept",
                    "tick": self.tick,
                    "shooter_id": defender.unit_id,
                    "missile_id": missile.missile_id,
                    "target_id": missile.target_id,
                }
            )
            return True
        return False

    def _register_corvette_sam_launches(self) -> None:
        for cid in sorted(self.corvettes.keys()):
            corvette = self.corvettes[cid]
            if corvette.is_destroyed:
                continue
            if corvette.sam_cooldown_remaining > 0:
                corvette.sam_cooldown_remaining -= 1
            # Tick other cooldowns here so gun layers see fresh values.
            if corvette.ciws_cooldown_remaining > 0:
                corvette.ciws_cooldown_remaining -= 1
            if corvette.deck_gun_cooldown_remaining > 0:
                corvette.deck_gun_cooldown_remaining -= 1

            aircraft = self._hostile_aircraft_for_corvette(corvette)
            vessels = self._hostile_vessels_for_corvette(corvette)
            inbound = self._inbound_antiship_for_corvette(corvette)
            actions = select_doctrine_actions(
                corvette,
                inbound_missiles=inbound,
                aircraft=aircraft,
                vessels=vessels,
            )
            for action in actions:
                if action.layer != "SAM_RADAR":
                    continue
                if corvette.sam_cooldown_remaining > 0:
                    continue
                if corvette.loadout.get("SAM_RADAR", 0) <= 0:
                    continue
                jet = self.jets.get(action.target_id)
                if jet is None:
                    continue
                frame = NAVAL_FRAME_CATALOG[corvette.frame_type]
                req = LaunchRequest(
                    shooter_id=corvette.unit_id,
                    target_id=jet.agent_id,
                    weapon_type="SAM_RADAR",
                    shooter_position=(
                        corvette.position[0],
                        corvette.position[1],
                        frame.sight_height,
                    ),
                    target_position=(jet.pos[0], jet.pos[1], jet.altitude),
                    shooter_faction=corvette.faction,
                    target_faction=jet.faction,
                    target_class=jet.target_class,
                    target_signature=jet.signature,
                    target_type=None,
                    has_los=True,
                    support_source_id=corvette.unit_id,
                    support_alive=True,
                    firing_heading_deg=corvette.heading,
                    ammo_available=corvette.loadout.get("SAM_RADAR", 0),
                )

                def _consume(c=corvette) -> None:
                    c.loadout["SAM_RADAR"] = c.loadout.get("SAM_RADAR", 0) - 1
                    c.sam_cooldown_remaining = c.sam_cooldown_ticks

                self.try_launch(req, consume_ammo=_consume, log_strike=False)

    def _hostile_aircraft_for_corvette(
        self, corvette: CorvetteState
    ) -> list[tuple[str, tuple[float, float], float, bool]]:
        frame = NAVAL_FRAME_CATALOG[corvette.frame_type]
        out: list[tuple[str, tuple[float, float], float, bool]] = []
        for jid in sorted(self.jets.keys()):
            jet = self.jets[jid]
            if jet.faction == corvette.faction:
                continue
            has_los = self.has_combat_los(
                corvette.position,
                frame.sight_height,
                jet.pos,
                jet.altitude,
            )
            out.append((jid, (float(jet.pos[0]), float(jet.pos[1])), jet.altitude, has_los))
        return out

    def _hostile_vessels_for_corvette(
        self, corvette: CorvetteState
    ) -> list[tuple[str, tuple[float, float], bool]]:
        frame = NAVAL_FRAME_CATALOG[corvette.frame_type]
        out: list[tuple[str, tuple[float, float], bool]] = []
        for cid in sorted(self.carriers.keys()):
            carrier = self.carriers[cid]
            if carrier.destroyed or carrier.faction == corvette.faction:
                continue
            has_los = self.has_combat_los(
                corvette.position,
                frame.sight_height,
                carrier.position,
                0.0,
            )
            out.append((cid, tuple(carrier.position), has_los))
        return out

    def _inbound_antiship_for_corvette(
        self, corvette: CorvetteState
    ) -> list[tuple[str, tuple[float, float], float]]:
        out: list[tuple[str, tuple[float, float], float]] = []
        for m in self.missiles:
            if m.status != "IN_FLIGHT":
                continue
            if m.target_id != corvette.unit_id:
                continue
            if m.weapon_type != "AGM84":
                continue
            out.append(
                (
                    m.missile_id,
                    (float(m.position[0]), float(m.position[1])),
                    float(m.position[2]),
                )
            )
        out.sort(key=lambda x: x[0])
        return out

    def _resolve_corvette_gun_layers(self) -> list[tuple[str, str, int]]:
        """Fire CIWS-vs-air and deck-gun layers; return pending (shooter, target, dmg)."""
        pending: list[tuple[str, str, int]] = []
        for cid in sorted(self.corvettes.keys()):
            corvette = self.corvettes[cid]
            if corvette.is_destroyed:
                continue
            aircraft = self._hostile_aircraft_for_corvette(corvette)
            vessels = self._hostile_vessels_for_corvette(corvette)
            inbound = self._inbound_antiship_for_corvette(corvette)
            actions = select_doctrine_actions(
                corvette,
                inbound_missiles=inbound,
                aircraft=aircraft,
                vessels=vessels,
            )
            for action in actions:
                if action.layer == "SAM_RADAR":
                    continue  # handled in register_launches
                if action.layer == "CIWS_MISSILE":
                    continue  # intercept runs during missile resolution
                if action.layer == "CIWS_AIRCRAFT":
                    dmg = self._fire_corvette_ciws_air(corvette, action.target_id)
                    if dmg > 0:
                        pending.append((corvette.unit_id, action.target_id, dmg))
                elif action.layer == "DECK_GUN":
                    dmg = self._fire_corvette_deck_gun(corvette, action.target_id)
                    if dmg > 0:
                        pending.append((corvette.unit_id, action.target_id, dmg))
        return pending

    def _fire_corvette_ciws_air(self, corvette: CorvetteState, jet_id: str) -> int:
        jet = self.jets.get(jet_id)
        if jet is None or corvette.ciws_cooldown_remaining > 0:
            return 0
        frame = NAVAL_FRAME_CATALOG[corvette.frame_type]
        los = self.has_combat_los(
            corvette.position, frame.sight_height, jet.pos, jet.altitude
        )
        corvette.burst_seq += 1
        req = GunBurstRequest(
            shooter_id=corvette.unit_id,
            target_id=jet_id,
            gun=CIWS_GUN,
            shooter_faction=corvette.faction,
            target_faction=jet.faction,
            target_class="AIRCRAFT",
            shooter_position=corvette.position,
            target_position=(float(jet.pos[0]), float(jet.pos[1])),
            shooter_heading_deg=corvette.heading,
            target_altitude=jet.altitude,
            ammo_available=corvette.loadout.get("CIWS", 0),
            has_los=los,
            combat_seed=self.combat_seed,
            tick=self.tick,
            burst_index=corvette.burst_seq,
        )
        result = resolve_gun_burst(req)
        self.ciws_attempts += 1
        self.gun_events.append(
            {
                "event": "gun_burst",
                "tick": self.tick,
                "shooter_id": corvette.unit_id,
                "target_id": jet_id,
                "gun_id": "CIWS",
                "layer": "CIWS_AIRCRAFT",
                "accepted": result.accepted,
                "hits": result.hits,
                "damage": result.damage,
                "reason": result.reason,
            }
        )
        if not result.accepted:
            return 0
        corvette.loadout["CIWS"] = corvette.loadout.get("CIWS", 0) - result.ammo_consumed
        corvette.ciws_cooldown_remaining = corvette.ciws_cooldown_ticks
        return result.damage

    def _fire_corvette_deck_gun(self, corvette: CorvetteState, vessel_id: str) -> int:
        carrier = self.carriers.get(vessel_id)
        if carrier is None or carrier.destroyed or corvette.deck_gun_cooldown_remaining > 0:
            return 0
        frame = NAVAL_FRAME_CATALOG[corvette.frame_type]
        los = self.has_combat_los(
            corvette.position, frame.sight_height, carrier.position, 0.0
        )
        corvette.burst_seq += 1
        req = GunBurstRequest(
            shooter_id=corvette.unit_id,
            target_id=vessel_id,
            gun=DECK_GUN,
            shooter_faction=corvette.faction,
            target_faction=carrier.faction,
            target_class="SURFACE_VESSEL",
            shooter_position=corvette.position,
            target_position=tuple(carrier.position),
            shooter_heading_deg=corvette.heading,
            target_altitude=0.0,
            ammo_available=corvette.loadout.get("DECK_GUN", 0),
            has_los=los,
            combat_seed=self.combat_seed,
            tick=self.tick,
            burst_index=corvette.burst_seq,
        )
        result = resolve_gun_burst(req)
        self.gun_events.append(
            {
                "event": "gun_burst",
                "tick": self.tick,
                "shooter_id": corvette.unit_id,
                "target_id": vessel_id,
                "gun_id": "DECK_GUN",
                "layer": "DECK_GUN",
                "accepted": result.accepted,
                "hits": result.hits,
                "damage": result.damage,
                "reason": result.reason,
            }
        )
        if not result.accepted:
            return 0
        corvette.loadout["DECK_GUN"] = (
            corvette.loadout.get("DECK_GUN", 0) - result.ammo_consumed
        )
        corvette.deck_gun_cooldown_remaining = corvette.deck_gun_cooldown_ticks
        if result.hits > 0:
            self.deck_gun_hits += result.hits
        return result.damage

    def _resolve_aaa_gun_bursts(self) -> list[tuple[str, str, int]]:
        """Fire ready AAA sites (at most one target per burst); return pending damage."""
        pending: list[tuple[str, str, int]] = []
        for aid in sorted(self.aaa_sites.keys()):
            site = self.aaa_sites[aid]
            tgt = self.targets.get(aid)
            if tgt is None or tgt.is_destroyed:
                continue
            if site.cooldown_remaining > 0:
                site.cooldown_remaining -= 1
                continue
            aircraft = self._hostile_aircraft_for_aaa(site)
            target_id = select_aaa_target(site, aircraft)
            if target_id is None:
                continue
            dmg = self._fire_aaa_burst(site, target_id)
            if dmg > 0:
                pending.append((site.target_id, target_id, dmg))
        return pending

    def _hostile_aircraft_for_aaa(
        self, site: AaaSiteState
    ) -> list[tuple[str, tuple[float, float], float, bool]]:
        out: list[tuple[str, tuple[float, float], float, bool]] = []
        for jid in sorted(self.jets.keys()):
            jet = self.jets[jid]
            if not are_hostile(site.faction, jet.faction):
                continue
            has_los = self.has_combat_los(
                site.position, site.sight_height, jet.pos, jet.altitude
            )
            out.append(
                (jid, (float(jet.pos[0]), float(jet.pos[1])), jet.altitude, has_los)
            )
        return out

    def _fire_aaa_burst(self, site: AaaSiteState, jet_id: str) -> int:
        jet = self.jets.get(jet_id)
        if jet is None or site.ammo <= 0:
            return 0
        los = self.has_combat_los(
            site.position, site.sight_height, jet.pos, jet.altitude
        )
        site.burst_seq += 1
        gun = site.gun
        req = GunBurstRequest(
            shooter_id=site.target_id,
            target_id=jet_id,
            gun=gun,
            shooter_faction=site.faction,
            target_faction=jet.faction,
            target_class="AIRCRAFT",
            shooter_position=site.position,
            target_position=(float(jet.pos[0]), float(jet.pos[1])),
            shooter_heading_deg=site.heading,
            target_altitude=jet.altitude,
            ammo_available=site.ammo,
            has_los=los,
            combat_seed=self.combat_seed,
            tick=self.tick,
            burst_index=site.burst_seq,
        )
        result = resolve_gun_burst(req)
        self.aaa_bursts += 1
        self.gun_events.append(
            {
                "event": "gun_burst",
                "tick": self.tick,
                "shooter_id": site.target_id,
                "target_id": jet_id,
                "gun_id": gun.gun_id,
                "layer": "AAA",
                "accepted": result.accepted,
                "hits": result.hits,
                "damage": result.damage,
                "ammo_consumed": result.ammo_consumed,
                "reason": result.reason,
            }
        )
        if not result.accepted:
            return 0
        site.ammo = max(0, site.ammo - result.ammo_consumed)
        site.cooldown_remaining = site.cooldown_ticks
        site.bursts_fired += 1
        site.rounds_fired += result.ammo_consumed
        site.hits += result.hits
        site.damage_dealt += result.damage
        self.aaa_rounds += result.ammo_consumed
        self.aaa_hits += result.hits
        self.aaa_damage += result.damage
        return result.damage

    def _step_all_tanks(self) -> None:
        """Move every live tank with identical faction-symmetric AI."""
        ordered = sorted(self.tanks.values(), key=lambda t: t.unit_id)
        # Snapshot positions for LOS/target selection before any moves.
        for tank in ordered:
            if tank.is_destroyed:
                continue
            vehicles = self._hostile_ground_vehicles_for_tank(tank)
            target_id = select_tank_target(tank, vehicles)
            tank.current_target_id = target_id
            target_pos = None
            target_los = False
            if target_id is not None:
                other = self.tanks.get(target_id)
                if other is not None and not other.is_destroyed:
                    target_pos = tuple(other.position)
                    target_los = self.has_combat_los(
                        tank.position,
                        TANK_FRAME_CATALOG[tank.frame_type].sight_height,
                        other.position,
                        TANK_FRAME_CATALOG[other.frame_type].sight_height,
                    )
            step_tank_movement(
                tank,
                self.terrain,
                target_pos=target_pos,
                target_has_los=target_los,
            )
        resolve_tank_separation([t for t in ordered if not t.is_destroyed])

    def _resolve_tank_gun_bursts(self) -> list[tuple[str, str, int]]:
        """Fire tank cannons at hostile GROUND_VEHICLE only (Phase 43)."""
        pending: list[tuple[str, str, int]] = []
        for tid in sorted(self.tanks.keys()):
            tank = self.tanks[tid]
            if tank.is_destroyed:
                continue
            if tank.cooldown_remaining > 0:
                tank.cooldown_remaining -= 1
                continue
            vehicles = self._hostile_ground_vehicles_for_tank(tank)
            target_id = select_tank_target(tank, vehicles)
            tank.current_target_id = target_id
            if target_id is None:
                continue
            # Only fire when in weapon range/arc (select uses sight range).
            other = self.tanks.get(target_id)
            if other is None or other.is_destroyed:
                continue
            gun = tank.gun
            dist = math.dist(tank.position, other.position)
            if dist > gun.range or dist < gun.min_range:
                continue
            if not is_in_firing_arc(
                tank.heading, tank.position, other.position, gun.firing_arc_deg
            ):
                continue
            los = self.has_combat_los(
                tank.position,
                TANK_FRAME_CATALOG[tank.frame_type].sight_height,
                other.position,
                TANK_FRAME_CATALOG[other.frame_type].sight_height,
            )
            if not los:
                continue
            dmg = self._fire_tank_cannon(tank, other)
            if dmg > 0:
                pending.append((tank.unit_id, other.unit_id, dmg))
        return pending

    def _hostile_ground_vehicles_for_tank(
        self, tank: TankState
    ) -> list[tuple[str, tuple[float, float], bool]]:
        out: list[tuple[str, tuple[float, float], bool]] = []
        frame = TANK_FRAME_CATALOG[tank.frame_type]
        for oid in sorted(self.tanks.keys()):
            other = self.tanks[oid]
            if other.is_destroyed or other.unit_id == tank.unit_id:
                continue
            if not are_hostile(tank.faction, other.faction):
                continue
            los = self.has_combat_los(
                tank.position,
                frame.sight_height,
                other.position,
                TANK_FRAME_CATALOG[other.frame_type].sight_height,
            )
            out.append((other.unit_id, tuple(other.position), los))
        return out

    def _fire_tank_cannon(self, tank: TankState, target: TankState) -> int:
        if tank.ammo <= 0:
            return 0
        frame = TANK_FRAME_CATALOG[tank.frame_type]
        los = self.has_combat_los(
            tank.position,
            frame.sight_height,
            target.position,
            TANK_FRAME_CATALOG[target.frame_type].sight_height,
        )
        tank.burst_seq += 1
        req = GunBurstRequest(
            shooter_id=tank.unit_id,
            target_id=target.unit_id,
            gun=TANK_CANNON,
            shooter_faction=tank.faction,
            target_faction=target.faction,
            target_class="GROUND_VEHICLE",
            shooter_position=tank.position,
            target_position=tuple(target.position),
            shooter_heading_deg=tank.heading,
            target_altitude=0.0,
            ammo_available=tank.ammo,
            has_los=los,
            combat_seed=self.combat_seed,
            tick=self.tick,
            burst_index=tank.burst_seq,
        )
        result = resolve_gun_burst(req)
        self.gun_events.append(
            {
                "event": "gun_burst",
                "tick": self.tick,
                "shooter_id": tank.unit_id,
                "target_id": target.unit_id,
                "gun_id": "TANK_CANNON",
                "layer": "TANK_CANNON",
                "accepted": result.accepted,
                "hits": result.hits,
                "damage": result.damage,
                "reason": result.reason,
            }
        )
        if not result.accepted:
            return 0
        tank.ammo = max(0, tank.ammo - result.ammo_consumed)
        tank.cooldown_remaining = tank.cooldown_ticks
        tank.rounds_fired += result.ammo_consumed
        tank.hits += result.hits
        self.tank_rounds += result.ammo_consumed
        self.tank_hits += result.hits
        return result.damage

    def _apply_sam_damage(self) -> None:
        """Deprecated Phase 8 path — SAMs launch missiles instead (Phase 38).

        Kept as a no-op shim so older tests that call it directly do not
        reintroduce passive damage. Prefer ``_register_sam_launches``.
        """
        return

    def _update_recovery_phase(self, jet: JetAgent) -> None:
        """Advance RTB → APPROACH based on distance to the live recovery base."""
        if not jet.is_rtb or jet.recovery_abort:
            return
        base_id = self._ensure_active_base(jet)
        if base_id is None:
            return
        if not self._is_carrier_base(base_id):
            # Fixed bases stay in RTB until capture radius in resolve_combat.
            jet.recovery_phase = "RTB"
            return
        carrier = self.carriers[base_id]
        dist = math.dist(jet.pos, carrier.position)
        if jet.recovery_phase in (None, "RTB") and dist <= config.RECOVERY_APPROACH_RADIUS:
            jet.recovery_phase = "APPROACH"
        elif jet.recovery_phase is None:
            jet.recovery_phase = "RTB"

    def _try_recover_jet(self, jet: JetAgent) -> bool:
        """Capture RECOVERED / returned_to_base when envelope conditions hold."""
        base_id = self._ensure_active_base(jet)
        if base_id is None:
            return False

        if not self._is_carrier_base(base_id):
            if math.dist(jet.pos, self.base_position(base_id)) <= config.RTB_LANDING_RADIUS:
                jet.recovery_phase = "RECOVERED"
                self._remove_jet(jet, "returned_to_base")
                return True
            return False

        carrier = self.carriers[base_id]
        dist = math.dist(jet.pos, carrier.position)
        if dist > config.RECOVERY_CAPTURE_RADIUS:
            return False
        if jet.altitude > config.RECOVERY_ALTITUDE + 1e-9:
            return False
        if not recovery_heading_aligned(
            jet.heading,
            carrier.heading,
            config.RECOVERY_HEADING_TOLERANCE_DEG,
        ):
            return False
        rel = recovery_relative_speed(jet.velocity_xy, carrier_velocity(carrier))
        if rel > config.RECOVERY_RELATIVE_SPEED_MAX + 1e-9:
            return False
        # Require APPROACH first so RTB→APPROACH→RECOVERED is observable.
        if jet.recovery_phase != "APPROACH":
            if dist <= config.RECOVERY_APPROACH_RADIUS:
                jet.recovery_phase = "APPROACH"
            return False
        jet.recovery_phase = "RECOVERED"
        self.carrier_recoveries += 1
        self.missile_events.append(
            {
                "tick": self.tick,
                "event": "carrier_recovery",
                "agent_id": jet.agent_id,
                "base_id": base_id,
                "carrier_position": tuple(carrier.position),
            }
        )
        self._remove_jet(jet, "returned_to_base")
        return True

    def _apply_heading_rotation(self, jet: JetAgent) -> None:
        if jet.desired_heading is None:
            return
        turn_rate = config.JET_FRAME_CATALOG[jet.frame_type].turn_rate_deg_per_tick
        delta = shortest_signed_angle(jet.heading, jet.desired_heading)
        if abs(delta) <= turn_rate:
            jet.heading = jet.desired_heading % 360.0
        else:
            jet.heading = (jet.heading + turn_rate * (1.0 if delta > 0 else -1.0)) % 360.0

    def _apply_move(self, jet: JetAgent, destination: tuple[float, float]) -> None:
        max_speed = config.JET_FRAME_CATALOG[jet.frame_type].max_speed
        distance = math.dist(jet.pos, destination)
        if distance <= max_speed:
            new_pos = destination
            moved = distance
        else:
            dx, dy = destination[0] - jet.pos[0], destination[1] - jet.pos[1]
            ratio = max_speed / distance
            new_pos = (jet.pos[0] + dx * ratio, jet.pos[1] + dy * ratio)
            moved = max_speed
        self.space.move_agent(jet, new_pos)
        jet.distance_traveled += moved

    def _apply_altitude(self, jet: JetAgent) -> None:
        """Climb/descend toward desired_altitude, clamped by frame limits."""
        if jet.desired_altitude is None:
            return
        frame = config.JET_FRAME_CATALOG[jet.frame_type]
        delta = jet.desired_altitude - jet.altitude
        max_step = frame.max_climb_per_tick
        if abs(delta) > max_step:
            delta = math.copysign(max_step, delta)
        new_alt = jet.altitude + delta
        if jet.is_rtb and not jet.recovery_abort:
            base_id = jet.active_base_id
            if self._is_carrier_base(base_id):
                # Carrier recovery may descend to RECOVERY_ALTITUDE / deck.
                floor = 0.0
            else:
                near_base = (
                    math.dist(jet.pos, self._primary_base_xy(jet))
                    <= config.RTB_LANDING_RADIUS
                )
                floor = 0.0 if near_base else frame.min_combat_altitude
        else:
            floor = frame.min_combat_altitude
        jet.altitude = max(floor, new_alt)

    def _target_in_firing_arc(
        self, jet: JetAgent, target: TargetState, weapon: config.WeaponSpec
    ) -> bool:
        # Delegates to wraithnet.geometry so decide_intent cannot drift.
        return is_in_firing_arc(
            jet.heading, jet.pos, target.position, weapon.firing_arc_deg
        )

    def _apply_strike(
        self, jet: JetAgent, target_id: str | None, weapon_type: str | None
    ) -> None:
        if target_id is None or weapon_type is None or jet.payload.get(weapon_type, 0) <= 0:
            return
        weapon = config.WEAPON_CATALOG.get(weapon_type)  # type: ignore[arg-type]
        if weapon is None:
            return
        # Guided weapons are handled by try_launch_from_jet in register_launches.
        if weapon.guidance != "NONE":
            return

        target = self.targets.get(target_id)
        tank = self.tanks.get(target_id) if target is None else None
        if target is None and tank is None:
            return

        if target is not None:
            target_faction = "ENEMY"
            target_type = target.target_type
            target_class = target_class_for_target_type(target_type)
            tgt_pos = target.position
            tgt_height = config.GROUND_TARGET_HEIGHT

            def _apply_damage(amount: int) -> None:
                target.hp_remaining = max(0, target.hp_remaining - amount)

            strike_target_id = target.target_id
        else:
            assert tank is not None
            if tank.is_destroyed:
                return
            target_faction = tank.faction
            target_type = "GROUND_VEHICLE"
            target_class = "GROUND_VEHICLE"
            tgt_pos = tank.position
            tgt_height = 0.0

            def _apply_damage(amount: int) -> None:
                damage = max(0, amount - tank.armor)
                tank.hp = max(0, tank.hp - damage)
                if tank.hp <= 0:
                    tank.mark_destroyed()
                    if tank.faction == "FRIENDLY":
                        self.tank_losses_friendly += 1
                    else:
                        self.tank_losses_enemy += 1

            strike_target_id = tank.unit_id

        # Phase 37 taxonomy gate: friendly fire / illegal class → no-op, no ammo.
        rejection = reject_engagement(
            shooter_faction=jet.faction,
            target_faction=target_faction,
            weapon=weapon_type,
            target_class=target_class,
        )
        if rejection is not None:
            return
        if weapon.suitability.get(target_type, 0.0) <= 0.0:
            return  # physically cannot damage this target type with this weapon — no-op
        if math.dist(jet.pos, tgt_pos) > weapon.range:
            return
        if weapon.firing_arc_deg < 360.0:
            # Reuse installation arc helper via a thin position adapter.
            if target is not None and not self._target_in_firing_arc(
                jet, target, weapon
            ):
                return
            if tank is not None:
                bearing = math.degrees(
                    math.atan2(tgt_pos[1] - jet.pos[1], tgt_pos[0] - jet.pos[0])
                )
                delta = (bearing - jet.heading + 180.0) % 360.0 - 180.0
                if abs(delta) > weapon.firing_arc_deg / 2.0:
                    return
        if not self.has_combat_los(
            jet.pos,
            jet.altitude,
            tgt_pos,
            tgt_height,
        ):
            return  # city column blocks — no ammo consumed
        # Stale STRIKE on an already-destroyed target still consumes payload while
        # in range; hp_remaining stays clamped at 0 (documented Phase 1 semantics).
        jet.payload[weapon_type] -= 1
        _apply_damage(weapon.damage_per_hit)
        self.strike_log.append(
            {
                "tick": self.tick,
                "agent_id": jet.agent_id,
                "target_id": strike_target_id,
                "weapon_type": weapon_type,
            }
        )

    def _jet_is_jammed(self, jet: JetAgent) -> bool:
        return any(
            math.dist(jet.pos, jammer.position) <= config.JAM_RADIUS
            for jammer in self.jammers.values()
        )

    def build_observation(self, agent_id: str, broker: MessageBroker) -> Observation:
        jet = self.jets[agent_id]  # KeyError if destroyed — Bridge must not call this
        # Direct sensor readings only — remembered/relayed targets live in the
        # agent's world_view (Phase 7), not here.
        known_targets = [
            TargetInfo(
                target_id=t.target_id,
                position=t.position,
                target_type=t.target_type,  # type: ignore[arg-type]
                hp_required=t.hp_required,
                hp_remaining=t.hp_remaining,
                claimed=broker.is_claimed(t.target_id),
            )
            for t in self.targets.values()
            if math.dist(jet.pos, t.position) <= config.SENSOR_RADIUS
            and self.has_combat_los(
                jet.pos,
                jet.altitude,
                t.position,
                config.GROUND_TARGET_HEIGHT,
            )
        ]
        # Phase 41: expose live corvettes as SURFACE_VESSEL CNP targets.
        for cv in self.corvettes.values():
            if cv.is_destroyed:
                continue
            if math.dist(jet.pos, cv.position) > config.SENSOR_RADIUS:
                continue
            if not self.has_combat_los(
                jet.pos, jet.altitude, cv.position, 0.0
            ):
                continue
            known_targets.append(
                TargetInfo(
                    target_id=cv.unit_id,
                    position=tuple(cv.position),
                    target_type="SURFACE_VESSEL",
                    hp_required=cv.hp_max,
                    hp_remaining=max(0, cv.hp),
                    claimed=broker.is_claimed(cv.unit_id),
                )
            )
        # Phase 43: expose live tanks as GROUND_VEHICLE CNP/strike targets.
        for tank in self.tanks.values():
            if tank.is_destroyed:
                continue
            if math.dist(jet.pos, tank.position) > config.SENSOR_RADIUS:
                continue
            if not self.has_combat_los(
                jet.pos,
                jet.altitude,
                tank.position,
                TANK_FRAME_CATALOG[tank.frame_type].sight_height,
            ):
                continue
            known_targets.append(
                TargetInfo(
                    target_id=tank.unit_id,
                    position=tuple(tank.position),
                    target_type="GROUND_VEHICLE",
                    hp_required=tank.hp_max,
                    hp_remaining=max(0, tank.hp),
                    claimed=broker.is_claimed(tank.unit_id),
                )
            )
        observed_gun_threats = self._observed_aaa_threats_for(jet)
        inbound = self._inbound_threats_for(jet)
        base_id = jet.active_base_id
        self._ensure_active_base(jet)
        base_id = jet.active_base_id
        recovery_xy = self._recovery_destination(jet)
        carrier_hp = None
        carrier_available = None
        route_forecast: list[tuple[float, float]] = []
        if self._is_carrier_base(base_id):
            carrier = self.carriers[base_id]
            carrier_hp = carrier.hp
            carrier_available = carrier.available
            if carrier.route_index < len(carrier.route):
                route_forecast = list(carrier.route[carrier.route_index :])
        alt_id = jet.base_assignment.alternate_base_id
        alt_available = (
            alt_id is not None and self._base_available(alt_id)
        )
        return Observation(
            agent_id=agent_id,
            tick=self.tick,
            current_position=jet.pos,
            sensor_radius=config.SENSOR_RADIUS,
            payload=dict(jet.payload),
            known_targets=known_targets,
            comms_jammed=self._jet_is_jammed(jet),
            current_heading=jet.heading,
            frame_type=jet.frame_type,
            fuel=max(0.0, jet.fuel),
            home_base=recovery_xy,
            current_altitude=jet.altitude,
            inbound_threats=inbound,
            flares_remaining=jet.flares_remaining,
            flare_capacity=jet.flare_capacity,
            flare_cooldown_remaining=jet.flare_cooldown_remaining,
            radar_warning_receiver=jet.radar_warning_receiver,
            notch_angle_window_deg=jet.notch_angle_window_deg,
            notch_min_speed=jet.notch_min_speed,
            notch_hold_ticks=jet.notch_hold_ticks,
            current_speed=math.hypot(*jet.velocity_xy),
            defensive_state=jet.defensive_state,
            defensive_threat_id=jet.defensive_threat_id,
            assigned_base_id=base_id,
            recovery_phase=jet.recovery_phase,
            recovery_base_hp=carrier_hp,
            recovery_base_available=carrier_available
            if carrier_available is not None
            else self._base_available(base_id),
            alternate_base_available=alt_available,
            recovery_route_forecast=route_forecast,
            observed_gun_threats=observed_gun_threats,
        )

    def _observed_aaa_threats_for(self, jet: JetAgent) -> list[ObservedGunThreat]:
        """Expose sensed AAA envelopes as typed threat contacts (Phase 42)."""
        contacts: list[ObservedGunThreat] = []
        for aid in sorted(self.aaa_sites.keys()):
            site = self.aaa_sites[aid]
            tgt = self.targets.get(aid)
            if tgt is None or tgt.is_destroyed:
                continue
            if not are_hostile(jet.faction, site.faction):
                continue
            if math.dist(jet.pos, site.position) > config.SENSOR_RADIUS:
                continue
            if not self.has_combat_los(
                jet.pos, jet.altitude, site.position, site.sight_height
            ):
                continue
            gun = site.gun
            frame = AAA_FRAME_CATALOG[site.frame_type]
            contacts.append(
                ObservedGunThreat(
                    threat_id=site.target_id,
                    threat_kind="AAA_GUN",
                    position=tuple(site.position),
                    engagement_range=float(gun.range),
                    altitude_ceiling=gun.altitude_ceiling,
                    requires_los=True,
                    confidence=float(frame.default_confidence),
                    faction=site.faction,
                )
            )
        return contacts

    def _inbound_threats_for(self, jet: JetAgent) -> list[InboundThreat]:
        threats: list[InboundThreat] = []
        jet_xy = (float(jet.pos[0]), float(jet.pos[1]))
        for missile in sorted(
            [m for m in self.missiles if m.status == "IN_FLIGHT"],
            key=lambda m: m.missile_id,
        ):
            if missile.target_id != jet.agent_id:
                continue
            if missile.guidance == "RADAR" and not jet.radar_warning_receiver:
                continue
            if missile.guidance not in ("INFRARED", "RADAR"):
                continue
            speed = max(missile.speed, 1e-6)
            dist = math.hypot(
                missile.position[0] - jet_xy[0],
                missile.position[1] - jet_xy[1],
            )
            bearing = bearing_to(jet_xy, (missile.position[0], missile.position[1]))
            threats.append(
                InboundThreat(
                    missile_id=missile.missile_id,
                    guidance=missile.guidance,
                    estimated_bearing=bearing,
                    estimated_time_to_impact=dist / speed,
                    support_emitter_id=missile.support_source_id,
                )
            )
        return threats

