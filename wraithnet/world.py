"""Mesa World model and spatial state for WraithNet.

Implemented in Phase 1 (Schemas & World Scaffolding).
See docs/phases/Phase_1_Schemas_and_World.md.
Phase 6: dict-based payload, target_type, weapon suitability on strike.
Phase 7: sensor-limited build_observation.
Phase 8: jet HP, SAM sites, swarm-wipeout termination.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

import mesa
from mesa.datacollection import DataCollector

from wraithnet import config
from wraithnet.broker import MessageBroker
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


class JetAgent(mesa.Agent):
    def __init__(self, model: WraithNetModel, agent_id: str, payload: dict[str, int]) -> None:
        super().__init__(model)
        self.agent_id = agent_id
        self.payload = dict(payload)  # mutable copy; never share the scenario dict
        self.distance_traveled = 0.0
        self.hp: int = config.JET_HP

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
        self._spawn_scenario()
        self._initial_jet_ids: set[str] = set(self.jets.keys())
        self.total_jammed_agent_ticks: int = 0
        self.datacollector = DataCollector(
            model_reporters={
                "Tick": lambda m: m.tick,
                "TotalDistanceTraveled": lambda m: sum(
                    j.distance_traveled for j in m.jets.values()
                ),
                # strike_log — correct even if jets die with leftover payload (Phase 8/10)
                "TotalMissilesFired": lambda m: len(m.strike_log),
                "TargetsDestroyed": lambda m: sum(
                    1 for t in m.targets.values() if t.is_destroyed
                ),
                "JetsDestroyed": lambda m: len(m._initial_jet_ids) - len(m.jets),
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
            agent = JetAgent(self, agent_id=j.agent_id, payload=dict(j.payload))
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
            if intent.action_type == "MOVE":
                self._apply_move(jet, intent.target_position)
            elif intent.action_type == "STRIKE":
                self._apply_strike(jet, intent.target_id, intent.weapon_type)
            # IDLE: no-op
        self._apply_sam_damage()
        if all(t.is_destroyed for t in self.targets.values()):
            self.running = False  # success — includes SAM_Site_1 now
        elif len(self.jets) == 0:
            self.running = False
            self.swarm_wiped_out = True
        self.total_jammed_agent_ticks += sum(
            1 for jet in self.jets.values() if self._jet_is_jammed(jet)
        )
        self.datacollector.collect(self)

    def _apply_sam_damage(self) -> None:
        for sam_target_id, sam in self.sam_sites.items():
            if self.targets[sam_target_id].is_destroyed:
                continue
            for jet in self.jets.values():
                if math.dist(jet.pos, sam.position) <= config.SAM_ENGAGEMENT_RANGE:
                    jet.hp -= config.SAM_DAMAGE_PER_TICK
        destroyed_ids = [aid for aid, jet in self.jets.items() if jet.is_destroyed]
        for aid in destroyed_ids:
            jet = self.jets.pop(aid)
            self.space.remove_agent(jet)
            jet.remove()  # Mesa 3.x: deregister from model.agents / AgentSet

    def _apply_move(self, jet: JetAgent, destination: tuple[float, float]) -> None:
        distance = math.dist(jet.pos, destination)
        if distance <= config.JET_SPEED:
            new_pos = destination
            moved = distance
        else:
            dx = destination[0] - jet.pos[0]
            dy = destination[1] - jet.pos[1]
            ratio = config.JET_SPEED / distance
            new_pos = (jet.pos[0] + dx * ratio, jet.pos[1] + dy * ratio)
            moved = config.JET_SPEED
        self.space.move_agent(jet, new_pos)
        jet.distance_traveled += moved

    def _apply_strike(
        self, jet: JetAgent, target_id: str | None, weapon_type: str | None
    ) -> None:
        target = self.targets.get(target_id) if target_id else None
        if target is None or weapon_type is None or jet.payload.get(weapon_type, 0) <= 0:
            return
        if config.WEAPON_SUITABILITY.get((weapon_type, target.target_type), 0.0) <= 0.0:
            return  # physically cannot damage this target type with this weapon — no-op
        if math.dist(jet.pos, target.position) > config.STRIKE_RANGE:
            return
        # Stale STRIKE on an already-destroyed target still consumes payload while
        # in range; hp_remaining stays clamped at 0 (documented Phase 1 semantics).
        jet.payload[weapon_type] -= 1
        target.hp_remaining = max(0, target.hp_remaining - 1)
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
        )
