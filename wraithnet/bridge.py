"""The Hybrid Bridge: connects the synchronous Mesa World to the asynchronous
agent negotiation loops. One coroutine, one event loop, no threads.
"""

from __future__ import annotations

import asyncio

from wraithnet import config
from wraithnet.agents import JetAgentRuntime
from wraithnet.broker import MessageBroker
from wraithnet.scenario_schema import ScenarioConfig
from wraithnet.trace_sink import TraceSink
from wraithnet.world import WraithNetModel


class WraithNetBridge:
    def __init__(
        self,
        sinks: list[TraceSink] | None = None,
        scenario: ScenarioConfig | None = None,
    ) -> None:
        self.broker = MessageBroker()
        self.model = WraithNetModel(scenario=scenario)
        # combat_seed is loaded on the model from the scenario (Phase 37).
        assert self.model.combat_seed == int(
            getattr(self.model._scenario, "combat_seed", 0) or 0
        )
        # Jetless scenarios: zero CNP runtimes; world still ticks via step().
        self.agents: dict[str, JetAgentRuntime] = {
            agent_id: JetAgentRuntime(agent_id, self.broker)
            for agent_id in self.model.jets
        }
        for agent in self.agents.values():
            agent._model = self.model
            agent._use_tick_bid_timeout = True
        self._tasks: list[asyncio.Task] = []
        self.sinks: list[TraceSink] = sinks or []
        for sink in self.sinks:
            sink.record_header(self)
            sink.record_tick(self)  # tick-0 baseline, mirrors DataCollector's own tick-0 collect() call

    def start_agent_loops(self) -> None:
        """Start each agent's message-receive loop (handle_message dispatch)
        as a background task. Must be called once before run()."""
        self._tasks = [agent.start() for agent in self.agents.values()]

    async def stop_agent_loops(self) -> None:
        for agent in self.agents.values():
            agent.stop()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def run(self, max_ticks: int = 1000) -> None:
        """Advance ticks until the scenario is solved or max_ticks is hit.
        max_ticks is a safety cap against an unforeseen infinite loop — it
        should never actually be reached if Phase 3's CNP logic is correct
        for this scenario."""
        self.start_agent_loops()
        try:
            for _ in range(max_ticks):
                await self._tick()
                if not self.model.running:
                    break
        finally:
            await self.stop_agent_loops()

    def _agents_in_id_order(self) -> list[JetAgentRuntime]:
        return [self.agents[aid] for aid in sorted(self.agents)]

    async def _tick(self) -> None:
        # Jetless / no CNP runtimes: still advance the staged world wrapper.
        if not self.agents:
            self.model.step({})
            for sink in self.sinks:
                sink.record_tick(self)
            return

        # 1. Broadcast state (+ push jamming status to the Broker — Phase 9)
        for agent_id in sorted(self.agents):
            agent = self.agents[agent_id]
            obs = self.model.build_observation(agent_id, self.broker)
            agent.set_observation(obs)
            self.broker.set_jammed(agent_id, obs.comms_jammed)

        # 1b. Refresh world-view memory and share new contacts (Phase 7 — all agents)
        await asyncio.gather(
            *(a.share_new_contacts() for a in self._agents_in_id_order())
        )
        await asyncio.sleep(0)  # let run() loops drain freshly-broadcast DATALINK_CONTACTs

        # 1c. Self-heal any CONTRACTOR_PENDING bid that's timed out (Phase 9)
        for agent in self._agents_in_id_order():
            agent.check_pending_bid_timeout()

        # 1d. Bingo-fuel check — before negotiation so a jet that just decided
        # to RTB this tick doesn't also try to bid/manage this same tick.
        began_rtb_this_tick: set[str] = set()
        for agent_id in sorted(self.agents):
            agent = self.agents[agent_id]
            if agent.bingo_fuel_reached():
                agent.begin_rtb()
                self.model.jets[agent_id].is_rtb = True
                began_rtb_this_tick.add(agent_id)

        # 1e. End-of-mission RTB — once required DESTROY objectives are done,
        # every remaining friendly recovers before SUCCESS (RETURN objective).
        if self.model.required_destroy_objectives_complete():
            for agent_id in sorted(self.agents):
                if agent_id in began_rtb_this_tick:
                    continue
                agent = self.agents[agent_id]
                if agent.role == "RTB":
                    continue
                agent.begin_rtb()
                if agent_id in self.model.jets:
                    self.model.jets[agent_id].is_rtb = True
                began_rtb_this_tick.add(agent_id)

        # 2. Negotiate (idle agents only), bounded by MAX_TICK_WAIT_SECONDS
        idle_agents = [a for a in self._agents_in_id_order() if a.role == "IDLE"]
        if idle_agents:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*(a.scan_and_negotiate() for a in idle_agents)),
                    timeout=config.MAX_TICK_WAIT_SECONDS,
                )
            except asyncio.TimeoutError:
                pass  # any agent still IDLE will just decide_intent() -> IDLE below

        # 3. Collect intents (pull, synchronous) in stable agent-id order
        intents = {
            agent_id: self.agents[agent_id].decide_intent()
            for agent_id in sorted(self.agents)
        }

        # 4. Physics tick — staged methods already run inside step()
        alive_before = set(self.model.jets)
        self.model.step(intents)
        destroyed_this_tick = alive_before - set(self.model.jets)

        # 4b. Cancel destroyed/landed runtimes and trigger Manager Fallback
        # (Phase 8). RTB-this-tick IDs reuse the same notify path (design §2.5)
        # so a contractor flying home doesn't strand its manager; those jets
        # stay in self.agents until they later land or run dry.
        if destroyed_this_tick:
            for agent_id in destroyed_this_tick:
                self.agents[agent_id].stop()
        leaving = destroyed_this_tick | began_rtb_this_tick
        if leaving:
            await self._notify_managers_of_losses(leaving)
        if destroyed_this_tick:
            for agent_id in destroyed_this_tick:
                del self.agents[agent_id]

        # 5. Contract cleanup
        for target_id, target in self.model.targets.items():
            if target.is_destroyed:
                self.broker.close_contract(target_id)
        self.broker.expire_stale_contracts(self.model.tick)
        for sink in self.sinks:
            sink.record_tick(self)

    async def _notify_managers_of_losses(self, destroyed_agent_ids: set[str]) -> None:
        # Only surviving managers may re-open bidding. A manager destroyed this
        # same tick is still in self.agents (del happens after this call) but
        # must not receive notify_contractor_lost — its run() loop is already
        # cancelled and calling notify would broadcast a zombie CFP (see
        # docs/05_Code_Review_Phases_6-8.md §1). When manager and contractor
        # die together, nobody re-tasks immediately; expire_stale_contracts
        # reclaims the claim after CONTRACT_CLAIM_TTL_TICKS.
        notifications = [
            manager.notify_contractor_lost(lost_id, target_id)
            for manager_id, manager in sorted(self.agents.items())
            if manager_id not in destroyed_agent_ids
            for target_id, contractor_ids in manager.managed_contracts.items()
            for lost_id in destroyed_agent_ids & contractor_ids
        ]
        if notifications:
            await asyncio.gather(*notifications)
