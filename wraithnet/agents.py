"""Agent async runtime: one coroutine loop per jet.

Phase 2: generic message-receive loop with a trivial handler (store + count).
Phase 3 replaces `handle_message` with the full CNP state machine (CFP/BID/
ACCEPT/REJECT handling, utility function, bidding). Keep `run()`'s loop
structure (receive -> dispatch to handle_message -> repeat) unchanged so
Phase 3 is a pure extension, not a rewrite.
Phase 6: weapon-suitability-aware bidding and STRIKE weapon_type.
Phase 7: world_view memory, DATALINK_CONTACT sharing, direct-sensing STRIKE rule.
Phase 21: catalog-driven bidding; rotate-to-bear before striking a narrow-arc weapon.
Phase 22: bingo-fuel RTB role; fly home, ignore new contracts.
"""

from __future__ import annotations

import asyncio
import math
import uuid

from wraithnet import config
from wraithnet.broker import MessageBroker
from wraithnet.config import BROADCAST_ID
from wraithnet.geometry import bearing_to, is_in_firing_arc
from wraithnet.schemas import ActionIntent, Message, Observation, TargetInfo

MAX_CFP_ROUNDS = 5  # defensive cap so a manager can't re-broadcast forever


def compute_bid(
    observation: Observation,
    target: TargetInfo,
    hp_needed: int | None = None,
) -> tuple[float, int, str] | None:
    """Returns (bid_utility, payload_offered, weapon_type), or None if this
    jet has no weapon suitable for target.target_type (hard disqualification —
    do not fold this into the utility score, see docs/03_..., finding I3).

    ``hp_needed`` is the ask for this CFP round (the manager's announced
    shortfall). When omitted, falls back to ``target.hp_remaining`` — correct
    for round 1, but later rounds must pass the CFP's ``hp_remaining``.
    """
    ask = target.hp_remaining if hp_needed is None else hp_needed
    suitable = [
        (wt, count)
        for wt, count in observation.payload.items()
        if count > 0
        and config.WEAPON_CATALOG[wt].suitability.get(target.target_type, 0.0) > 0.0
    ]
    if not suitable:
        return None
    # Prefer the suitable weapon this jet holds the most of; tie-break alphabetically.
    weapon_type, count = max(suitable, key=lambda pair: (pair[1], pair[0]))
    suitability = config.WEAPON_CATALOG[weapon_type].suitability[target.target_type]
    payload_score = min(1.0, count / ask)
    distance = math.dist(observation.current_position, target.position)
    distance_score = distance / config.MAX_GRID_DISTANCE
    utility = (
        config.UTILITY_W_WEAPON * suitability
        + config.UTILITY_W1 * payload_score
        - config.UTILITY_W2 * distance_score
    )
    payload_offered = min(count, ask)
    return utility, payload_offered, weapon_type


class JetAgentRuntime:
    def __init__(self, agent_id: str, broker: MessageBroker) -> None:
        self.agent_id = agent_id
        self.broker = broker
        self.broker.register(agent_id)
        self.received_messages: list[Message] = []
        self._task: asyncio.Task | None = None

        self.role: str = "IDLE"
        self.latest_observation: Observation | None = None
        # target_id -> (info, last_updated_tick); Phase 7 fog-of-war memory
        self.world_view: dict[str, tuple[TargetInfo, int]] = {}

        # Manager-side state
        self.open_contract_id: str | None = None
        self.managed_target_id: str | None = None
        self._collected_bids: list[Message] = []
        self._cfp_round: int = 0

        # Contractor-side state
        self.committed_target_id: str | None = None
        self.committed_payload: int = 0
        self.committed_weapon_type: str | None = None
        # Tick we entered CONTRACTOR_PENDING; cleared on ACCEPT/REJECT/TTL (Phase 9).
        self.pending_bid_since_tick: int | None = None

        # Survives past managed_target_id cleanup — who we ACCEPTed for each target
        # (never includes self). Used by notify_contractor_lost (Phase 8).
        self.managed_contracts: dict[str, set[str]] = {}

    def set_observation(self, observation: Observation) -> None:
        """Called by test code in this phase, and by the Bridge in Phase 4,
        once per tick, before negotiation/intent logic runs for that tick."""
        self.latest_observation = observation

    def effective_known_targets(self) -> list[TargetInfo]:
        """Direct sensor readings, merged with remembered (datalink/relay) targets
        not currently in direct sensor range. Safe for MOVE/BID decisions;
        NOT safe for deciding whether to STRIKE — see decide_intent()."""
        return [info for info, _tick in self.world_view.values()]

    def refresh_world_view(self) -> list[TargetInfo]:
        """Update world_view from this tick's direct Observation, expire stale
        entries, and return the list of targets whose info is NEW or CHANGED
        since last tick (for share_new_contacts() to broadcast)."""
        obs = self.latest_observation
        assert obs is not None
        changed: list[TargetInfo] = []
        for target in obs.known_targets:  # direct sensor readings only
            previous = self.world_view.get(target.target_id)
            if (
                previous is None
                or previous[0].hp_remaining != target.hp_remaining
                or previous[0].claimed != target.claimed
            ):
                changed.append(target)
            self.world_view[target.target_id] = (target, obs.tick)
        stale_ids = [
            tid
            for tid, (_info, last_tick) in self.world_view.items()
            if obs.tick - last_tick > config.DATALINK_CONTACT_TTL_TICKS
        ]
        for tid in stale_ids:
            del self.world_view[tid]
        return changed

    async def share_new_contacts(self) -> None:
        changed = self.refresh_world_view()
        obs = self.latest_observation
        assert obs is not None
        for target in changed:
            await self.broker.send(
                Message(
                    message_id=uuid.uuid4().hex,
                    contract_id=None,
                    tick=obs.tick,
                    sender_id=self.agent_id,
                    receiver_id=BROADCAST_ID,
                    message_type="DATALINK_CONTACT",
                    target_id=target.target_id,
                    target_info=target,
                )
            )

    async def run(self) -> None:
        """Main loop: forever receive and dispatch. Cancel the wrapping Task to stop."""
        while True:
            message = await self.broker.receive(self.agent_id)
            await self.handle_message(message)

    async def handle_message(self, message: Message) -> None:
        self.received_messages.append(message)
        if message.message_type == "DATALINK_CONTACT":
            existing = self.world_view.get(message.target_id)
            if existing is None or existing[1] <= message.tick:
                assert message.target_info is not None
                self.world_view[message.target_id] = (message.target_info, message.tick)
            return
        if message.message_type == "CFP":
            await self._maybe_bid(message)
        elif (
            message.message_type == "BID"
            and self.managed_target_id is not None
            and message.target_id == self.managed_target_id
        ):
            # Key off managed_target_id, not role==MANAGER: a self-commit in an
            # earlier round sets role=CONTRACTOR while this agent is still
            # collecting bids for later CFP rounds.
            self._collected_bids.append(message)
        elif message.message_type == "ACCEPT" and self.role == "CONTRACTOR_PENDING":
            self._commit(
                message.target_id,
                message.payload_offered or 0,
                message.weapon_type,
            )
        elif message.message_type == "REJECT" and self.role == "CONTRACTOR_PENDING":
            self.role = "IDLE"
            self.pending_bid_since_tick = None

    def start(self) -> asyncio.Task:
        self._task = asyncio.create_task(self.run())
        return self._task

    def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()

    async def try_become_manager(self, target: TargetInfo) -> bool:
        """Call this when idle and aware of an unclaimed, un-destroyed target.
        Returns True if this agent won the race to manage it."""
        obs = self.latest_observation
        if self.role != "IDLE" or obs is None or target.hp_remaining <= 0:
            return False
        contract_id = uuid.uuid4().hex
        if not self.broker.try_open_contract(
            target.target_id, contract_id, self.agent_id, obs.tick
        ):
            return False  # someone else already manages this target
        self.role = "MANAGER"
        self.open_contract_id = contract_id
        self.managed_target_id = target.target_id
        self._cfp_round = 0
        await self._broadcast_cfp_and_collect(target.hp_remaining)
        return True

    async def _broadcast_cfp_and_collect(self, hp_needed: int) -> None:
        self._cfp_round += 1
        obs = self.latest_observation
        assert obs is not None
        self._collected_bids = []
        # Self-bid: the manager may also strike its own managed target. Without
        # this, a jet that spots and manages a target it's well-placed to hit
        # itself would never fire, which contradicts minimizing total distance.
        # Skip if we already committed to this target in a prior round — re-bidding
        # the same missiles would double-count them against the remaining shortfall.
        already_committed = (
            self.committed_target_id == self.managed_target_id
            and self.committed_payload > 0
        )
        if obs.total_payload_remaining > 0 and not already_committed:
            target = next(
                (
                    t
                    for t in self.effective_known_targets()
                    if t.target_id == self.managed_target_id
                ),
                None,
            )
            if target is not None:
                bid = compute_bid(obs, target, hp_needed=hp_needed)
                if bid is not None:
                    utility, payload_offered, weapon_type = bid
                    self._collected_bids.append(
                        Message(
                            message_id=uuid.uuid4().hex,
                            contract_id=self.open_contract_id,
                            tick=obs.tick,
                            sender_id=self.agent_id,
                            receiver_id=self.agent_id,
                            message_type="BID",
                            target_id=self.managed_target_id,
                            payload_offered=payload_offered,
                            bid_utility=utility,
                            weapon_type=weapon_type,
                        )
                    )
        cfp = Message(
            message_id=uuid.uuid4().hex,
            contract_id=self.open_contract_id,
            tick=obs.tick,
            sender_id=self.agent_id,
            receiver_id=BROADCAST_ID,
            message_type="CFP",
            target_id=self.managed_target_id,
            hp_remaining=hp_needed,
        )
        await self.broker.send(cfp)
        try:
            await asyncio.sleep(config.BID_TIMEOUT_SECONDS)
        except asyncio.CancelledError:
            self._abort_open_management()
            return
        await self._resolve_contract(hp_needed)

    def _abort_open_management(self) -> None:
        """Release a mid-negotiation claim if the tick wait cancels us."""
        if self.managed_target_id is not None:
            self.broker.close_contract(self.managed_target_id)
            self.open_contract_id = None
            self.managed_target_id = None
        if self.role == "MANAGER":
            self.role = "IDLE"

    async def _resolve_contract(self, hp_needed: int) -> None:
        assert self.latest_observation is not None
        bids = sorted(self._collected_bids, key=lambda b: (-b.bid_utility, b.sender_id))
        # Use this round's announced ask — Observation.hp_remaining is stale
        # mid-negotiation (no strikes have landed yet this tick).
        remaining = hp_needed
        winners: list[Message] = []
        for bid in bids:
            if remaining <= 0:
                break
            winners.append(bid)
            remaining -= bid.payload_offered
        winner_ids = {b.sender_id for b in winners}

        for bid in bids:
            if bid.sender_id == self.agent_id:
                if bid.sender_id in winner_ids:
                    self._commit(
                        self.managed_target_id,
                        bid.payload_offered,
                        bid.weapon_type,
                    )
                continue
            decision = "ACCEPT" if bid.sender_id in winner_ids else "REJECT"
            if decision == "ACCEPT":
                self.managed_contracts.setdefault(self.managed_target_id, set()).add(
                    bid.sender_id
                )
            await self.broker.send(
                Message(
                    message_id=uuid.uuid4().hex,
                    contract_id=self.open_contract_id,
                    tick=self.latest_observation.tick,
                    sender_id=self.agent_id,
                    receiver_id=bid.sender_id,
                    message_type=decision,
                    target_id=self.managed_target_id,
                    payload_offered=bid.payload_offered if decision == "ACCEPT" else None,
                    weapon_type=bid.weapon_type if decision == "ACCEPT" else None,
                )
            )

        if remaining > 0 and self._cfp_round < MAX_CFP_ROUNDS:
            await self._broadcast_cfp_and_collect(remaining)  # re-negotiate the shortfall
            return

        # Only free the claim on give-up. A fully-covered contract stays claimed
        # until the target is destroyed (Bridge) or TTL expires — otherwise IDLE
        # agents would reopen CFPs every tick for targets already being struck.
        if remaining > 0:
            self.broker.close_contract(self.managed_target_id)
        self.open_contract_id = None
        self.managed_target_id = None
        if self.role == "MANAGER":  # did not win our own self-bid
            self.role = "IDLE"

    async def _maybe_bid(self, cfp: Message) -> None:
        obs = self.latest_observation
        if self.role != "IDLE" or obs is None or obs.total_payload_remaining <= 0:
            return
        if cfp.hp_remaining is None or cfp.hp_remaining <= 0:
            return
        target = next(
            (t for t in self.effective_known_targets() if t.target_id == cfp.target_id),
            None,
        )
        if target is None or target.hp_remaining <= 0:
            return
        bid = compute_bid(obs, target, hp_needed=cfp.hp_remaining)
        if bid is None:
            return
        utility, payload_offered, weapon_type = bid
        self.role = "CONTRACTOR_PENDING"
        self.pending_bid_since_tick = obs.tick
        await self.broker.send(
            Message(
                message_id=uuid.uuid4().hex,
                contract_id=cfp.contract_id,
                tick=obs.tick,
                sender_id=self.agent_id,
                receiver_id=cfp.sender_id,
                message_type="BID",
                target_id=cfp.target_id,
                payload_offered=payload_offered,
                bid_utility=utility,
                weapon_type=weapon_type,
            )
        )

    def _commit(
        self, target_id: str, payload_offered: int, weapon_type: str | None
    ) -> None:
        self.role = "CONTRACTOR"
        self.committed_target_id = target_id
        self.committed_payload = payload_offered
        self.committed_weapon_type = weapon_type
        self.pending_bid_since_tick = None

    def check_pending_bid_timeout(self) -> None:
        """Self-heal a CONTRACTOR_PENDING bid that's been waiting too long for a
        decision — the only realistic cause is ACCEPT/REJECT dropped by jamming.
        Mirrors CONTRACT_CLAIM_TTL_TICKS (Phase 9 Part D)."""
        if self.role != "CONTRACTOR_PENDING" or self.pending_bid_since_tick is None:
            return
        obs = self.latest_observation
        if (
            obs is not None
            and obs.tick - self.pending_bid_since_tick > config.CONTRACTOR_PENDING_TTL_TICKS
        ):
            self.role = "IDLE"
            self.pending_bid_since_tick = None

    async def notify_contractor_lost(self, contractor_id: str, target_id: str) -> None:
        """Called by the Bridge when a jet this agent accepted as a contractor
        for target_id has just been destroyed. Re-opens bidding for whatever
        shortfall remains, reusing the existing re-negotiation machinery."""
        contractors = self.managed_contracts.get(target_id)
        if contractors is None or contractor_id not in contractors:
            return  # not our contract, or already handled
        contractors.discard(contractor_id)

        target = next(
            (t for t in self.effective_known_targets() if t.target_id == target_id),
            None,
        )
        if target is None or target.hp_remaining <= 0:
            self.managed_contracts.pop(target_id, None)
            return  # target already destroyed — nothing to re-task

        if self.role != "IDLE":
            return  # busy; do not force-interrupt (Phase 8 scope)

        self.role = "MANAGER"
        self.managed_target_id = target_id
        self.open_contract_id = uuid.uuid4().hex
        self._cfp_round = 0
        await self._broadcast_cfp_and_collect(target.hp_remaining)

    def begin_rtb(self) -> None:
        """Called once, the tick bingo fuel is first crossed. Idempotent — a
        second call is a no-op so the Bridge cannot double-abort a claim."""
        if self.role == "RTB":
            return
        if self.role == "MANAGER" or self.managed_target_id is not None:
            self._abort_open_management()
        self.role = "RTB"
        self.committed_target_id = None
        self.committed_weapon_type = None

    def bingo_fuel_reached(self) -> bool:
        obs = self.latest_observation
        if obs is None or self.role == "RTB":
            return False
        frame = config.JET_FRAME_CATALOG[obs.frame_type]
        distance_home = math.dist(obs.current_position, obs.home_base)
        ticks_home = distance_home / frame.max_speed
        bingo = ticks_home * frame.fuel_burn_per_tick * config.RTB_FUEL_SAFETY_MARGIN
        return obs.fuel <= bingo

    def decide_intent(self) -> ActionIntent:
        """Pure function of current state + latest_observation. Phase 4's
        Bridge calls this once per tick per agent to get its next ActionIntent."""
        obs = self.latest_observation
        assert obs is not None
        if self.role == "RTB":
            bearing = bearing_to(obs.current_position, obs.home_base)
            return ActionIntent(
                agent_id=self.agent_id,
                tick=obs.tick,
                action_type="MOVE",
                target_position=obs.home_base,
                desired_heading=bearing,
            )
        if self.committed_target_id is None:
            return ActionIntent(agent_id=self.agent_id, tick=obs.tick, action_type="IDLE")
        target = next(
            (
                t
                for t in self.effective_known_targets()
                if t.target_id == self.committed_target_id
            ),
            None,
        )
        if (
            target is None
            or target.hp_remaining <= 0
            or self.committed_payload <= 0
            or self.committed_weapon_type is None
        ):
            self.committed_target_id = None
            self.committed_weapon_type = None
            self.role = "IDLE"
            return ActionIntent(agent_id=self.agent_id, tick=obs.tick, action_type="IDLE")
        distance = math.dist(obs.current_position, target.position)
        # Safety: never STRIKE a memory-only target — must be directly sensing it.
        directly_sensed = any(t.target_id == target.target_id for t in obs.known_targets)
        weapon = config.WEAPON_CATALOG[self.committed_weapon_type]
        bearing = bearing_to(obs.current_position, target.position)
        in_range = distance <= weapon.range
        if in_range and directly_sensed:
            in_arc = is_in_firing_arc(
                obs.current_heading,
                obs.current_position,
                target.position,
                weapon.firing_arc_deg,
            )
            if in_arc:
                self.committed_payload -= 1
                return ActionIntent(
                    agent_id=self.agent_id,
                    tick=obs.tick,
                    action_type="STRIKE",
                    target_id=target.target_id,
                    weapon_type=self.committed_weapon_type,
                )
            # In range but not pointed at the target yet: turn toward it this
            # tick instead of striking. Still in position, just not yet able
            # to fire — does not consume payload, does not lose the contract.
            return ActionIntent(
                agent_id=self.agent_id,
                tick=obs.tick,
                action_type="IDLE",
                desired_heading=bearing,
            )
        return ActionIntent(
            agent_id=self.agent_id,
            tick=obs.tick,
            action_type="MOVE",
            target_position=target.position,
            desired_heading=bearing,
        )

    async def scan_and_negotiate(self) -> None:
        """Call once per tick when IDLE: look for an unclaimed, live target and
        try to manage it.

        Deliberate deviation from Phase 6 Part D.5: jets carrying payload but
        nothing suitable for a target skip it here (a JDAM-only jet must not
        burn the full multi-round CFP timeout on a MOBILE_RADAR). Zero-payload
        jets may still become pure coordinators (see tie-break tests). Agents
        with the wrong weapon type cannot coordinate via this scan path —
        ``try_become_manager`` itself stays unfiltered for direct callers.

        Uses ``effective_known_targets()`` so Fog-of-War memory/relay contacts
        count as awareness for management and bidding decisions.
        """
        if self.role != "IDLE" or self.latest_observation is None:
            return
        obs = self.latest_observation
        for target in self.effective_known_targets():
            if target.hp_remaining <= 0:
                continue
            # Live claim check: Observation.claimed is a tick-start snapshot and
            # can be stale when multiple IDLE agents scan concurrently.
            if target.claimed or self.broker.is_claimed(target.target_id):
                continue
            if (
                obs.total_payload_remaining > 0
                and compute_bid(obs, target) is None
            ):
                continue
            won = await self.try_become_manager(target)
            if won:
                return
