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
        self.agents: dict[str, JetAgentRuntime] = {
            agent_id: JetAgentRuntime(agent_id, self.broker)
            for agent_id in self.model.jets
        }
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

    async def _tick(self) -> None:
        # 1. Broadcast state (+ push jamming status to the Broker — Phase 9)
        for agent_id, agent in self.agents.items():
            obs = self.model.build_observation(agent_id, self.broker)
            agent.set_observation(obs)
            self.broker.set_jammed(agent_id, obs.comms_jammed)

        # 1b. Refresh world-view memory and share new contacts (Phase 7 — all agents)
        await asyncio.gather(*(a.share_new_contacts() for a in self.agents.values()))
        await asyncio.sleep(0)  # let run() loops drain freshly-broadcast DATALINK_CONTACTs

        # 1c. Self-heal any CONTRACTOR_PENDING bid that's timed out (Phase 9)
        for agent in self.agents.values():
            agent.check_pending_bid_timeout()

        # 2. Negotiate (idle agents only), bounded by MAX_TICK_WAIT_SECONDS
        idle_agents = [a for a in self.agents.values() if a.role == "IDLE"]
        if idle_agents:
            try:
                await asyncio.wait_for(
                    asyncio.gather(*(a.scan_and_negotiate() for a in idle_agents)),
                    timeout=config.MAX_TICK_WAIT_SECONDS,
                )
            except asyncio.TimeoutError:
                pass  # any agent still IDLE will just decide_intent() -> IDLE below

        # 3. Collect intents (pull, synchronous)
        intents = {
            agent_id: agent.decide_intent() for agent_id, agent in self.agents.items()
        }

        # 4. Physics tick
        alive_before = set(self.model.jets)
        self.model.step(intents)
        destroyed_this_tick = alive_before - set(self.model.jets)

        # 4b. Cancel destroyed runtimes and trigger Manager Fallback (Phase 8)
        if destroyed_this_tick:
            for agent_id in destroyed_this_tick:
                self.agents[agent_id].stop()
            await self._notify_managers_of_losses(destroyed_this_tick)
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
            for manager_id, manager in self.agents.items()
            if manager_id not in destroyed_agent_ids
            for target_id, contractor_ids in manager.managed_contracts.items()
            for lost_id in destroyed_agent_ids & contractor_ids
        ]
        if notifications:
            await asyncio.gather(*notifications)
