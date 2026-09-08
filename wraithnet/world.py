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
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal

import mesa
from mesa.datacollection import DataCollector

from wraithnet import config
from wraithnet.broker import MessageBroker
from wraithnet.geometry import is_in_firing_arc, shortest_signed_angle
from wraithnet.scenario_schema import ScenarioConfig, _default_scenario
from wraithnet.schemas import ActionIntent, Observation, TargetInfo


@dataclass
class TargetState:
    target_id: str
    position: tuple[float, float]
    hp_required: int
    target_type: str
    hp_remaining: int = field(init=False)

    def __post_init__(self) -> None:
        self.hp_remaining = self.hp_required

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
    ) -> None:
        super().__init__(model)
        self.agent_id = agent_id
        self.payload = dict(payload)  # mutable copy; never share the scenario dict
        self.distance_traveled = 0.0
        self.frame_type = frame_type
        self.hp: int = config.JET_FRAME_CATALOG[frame_type].hp
        self.fuel: float = config.JET_FRAME_CATALOG[frame_type].fuel_capacity
        self.is_rtb: bool = False
        # Degrees: 0 = facing +y (world "up"), increasing clockwise when viewed
        # from above (90 = facing +x). Independent of travel direction.
        self.heading: float = initial_heading % 360.0
        self.desired_heading: float | None = None

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
        self, model: WraithNetModel, target_id: str, position: tuple[float, float]
    ) -> None:
        super().__init__(model)
        self.target_id = target_id
        self.position = position


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
        self.strike_log: list[dict] = []  # ground truth for Phase 5 validation
        self.swarm_wiped_out: bool = False
        self.mission_aborted: bool = False
        self.jet_outcomes: dict[str, JetOutcome] = {}
        self._home_base: tuple[float, float] = self._scenario.home_base
        self.jets_returned_to_base: set[str] = set()
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
            self.sam_sites[s.target_id] = SamSiteAgent(
                self, s.target_id, s.position
            )
        for jam in self._scenario.jammers:
            self.jammers[jam.jammer_id] = EwJammerAgent(
                self, jam.jammer_id, jam.position
            )
        for j in self._scenario.jets:
            agent = JetAgent(
                self,
                agent_id=j.agent_id,
                payload=dict(j.payload),
                frame_type=j.frame_type,
                initial_heading=j.initial_heading,
            )
            self.space.place_agent(agent, j.position)
            self.jets[j.agent_id] = agent

    def step(self, intents: dict[str, ActionIntent] | None = None) -> None:
        intents = intents or {}
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
        for agent_id, intent in intents.items():
            jet = self.jets.get(agent_id)
            if jet is None:
                continue
            # Heading rotation is independent of action_type (a jet can MOVE
            # one way while turning to face another). Applied *before*
            # MOVE/STRIKE so Phase 21's firing-arc check sees this tick's
            # post-rotation heading — a jet that finishes turning can fire
            # the same tick. (Order is a no-op for gameplay this phase;
            # nothing reads heading for a combat decision until Phase 21.)
            jet.desired_heading = intent.desired_heading
            self._apply_heading_rotation(jet)
            if intent.action_type == "MOVE":
                self._apply_move(jet, intent.target_position)
            elif intent.action_type == "STRIKE":
                self._apply_strike(jet, intent.target_id, intent.weapon_type)
            # IDLE: no-op (heading may still have rotated above)
        self._apply_sam_damage()
        # Fuel burn — flat per tick, every live jet, regardless of movement
        # (design doc §5). SAM-killed jets were already reaped above.
        for jet in self.jets.values():
            jet.fuel -= config.JET_FRAME_CATALOG[jet.frame_type].fuel_burn_per_tick
        # Landing must run BEFORE the fuel-exhaustion reap so a jet that
        # arrives home with exactly 0 fuel this tick is a safe landing, not a
        # dry-tank loss. Snapshot the dict because this mutates self.jets.
        for jet in list(self.jets.values()):
            if (
                jet.is_rtb
                and math.dist(jet.pos, self._home_base) <= config.RTB_LANDING_RADIUS
            ):
                self._remove_jet(jet, "returned_to_base")
        # Fuel exhaustion is an independent condition — do not zero hp.
        destroyed_ids = [
            aid for aid, jet in self.jets.items() if jet.is_destroyed or jet.fuel <= 0
        ]
        for aid in destroyed_ids:
            self._remove_jet(self.jets[aid], "destroyed")
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
        self.total_jammed_agent_ticks += sum(
            1 for jet in self.jets.values() if self._jet_is_jammed(jet)
        )
        self.datacollector.collect(self)
        assert self.all_jet_ids() == self._initial_jet_ids

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

    def _apply_sam_damage(self) -> None:
        for sam_target_id, sam in self.sam_sites.items():
            if self.targets[sam_target_id].is_destroyed:
                continue
            for jet in self.jets.values():
                if math.dist(jet.pos, sam.position) <= config.SAM_ENGAGEMENT_RANGE:
                    evasion = config.JET_FRAME_CATALOG[jet.frame_type].evasion_bonus
                    # Flat integer reduction, never a probability roll —
                    # preserves SAM_DAMAGE_PER_TICK's deterministic invariant.
                    damage = max(0, config.SAM_DAMAGE_PER_TICK - evasion)
                    jet.hp -= damage
        destroyed_ids = [aid for aid, jet in self.jets.items() if jet.is_destroyed]
        for aid in destroyed_ids:
            self._remove_jet(self.jets[aid], "destroyed")

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
        target = self.targets.get(target_id) if target_id else None
        if target is None or weapon_type is None or jet.payload.get(weapon_type, 0) <= 0:
            return
        weapon = config.WEAPON_CATALOG[weapon_type]
        if weapon.suitability.get(target.target_type, 0.0) <= 0.0:
            return  # physically cannot damage this target type with this weapon — no-op
        if math.dist(jet.pos, target.position) > weapon.range:
            return
        if weapon.firing_arc_deg < 360.0 and not self._target_in_firing_arc(
            jet, target, weapon
        ):
            return  # in range but not pointed at it — decide_intent rotates to bear
        # Stale STRIKE on an already-destroyed target still consumes payload while
        # in range; hp_remaining stays clamped at 0 (documented Phase 1 semantics).
        jet.payload[weapon_type] -= 1
        target.hp_remaining = max(0, target.hp_remaining - weapon.damage_per_hit)
        self.strike_log.append(
            {
                "tick": self.tick,
                "agent_id": jet.agent_id,
                "target_id": target.target_id,
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
                target_type=t.target_type,
                hp_required=t.hp_required,
                hp_remaining=t.hp_remaining,
                claimed=broker.is_claimed(t.target_id),
            )
            for t in self.targets.values()
            if math.dist(jet.pos, t.position) <= config.SENSOR_RADIUS
        ]
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
            home_base=self._home_base,
        )
