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
Phase 39: basic flare/notch reactions to inbound IR/radar threats.
Phase 53: upright-before-release attitude; cruise/RTB pitch bias.
"""

from __future__ import annotations

import asyncio
import hashlib
import math
from typing import TYPE_CHECKING, Callable

from wraithnet import config
from wraithnet.bid_schemas import AwardContext, BidProposal, ThreatContact
from wraithnet.bid_utility import (
    build_bid_proposals,
    flare_readiness,
    proposal_damage_milli,
    select_winning_bids,
)
from wraithnet.altitude_tactics import climb_rate, pitch_lag_ticks, transit_altitude
from wraithnet.broker import MessageBroker
from wraithnet.config import BROADCAST_ID
from wraithnet.geometry import angular_distance, bearing_to, is_in_firing_arc
from wraithnet.weapons_release import (
    attack_altitude,
    needed_attitude_for_release,
    release_attitude_ok,
    release_reachable,
    weapon_has_attitude_envelope,
)
from wraithnet.schemas import ActionIntent, InboundThreat, Message, Observation, TargetInfo

if TYPE_CHECKING:
    from wraithnet.world import WraithNetModel

MAX_CFP_ROUNDS = 5  # defensive cap so a manager can't re-broadcast forever


def stable_id(*parts: object) -> str:
    """Deterministic 32-char hex ID from stable parts (not UUIDs)."""
    raw = "|".join("" if p is None else str(p) for p in parts)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


def _threat_contacts_from_observation(obs: Observation) -> list[ThreatContact]:
    """Build typed contacts from inbound + gun threats (no hidden world leak)."""
    contacts: list[ThreatContact] = []
    for threat in obs.inbound_threats:
        guidance = str(getattr(threat, "guidance", "") or "")
        if guidance == "INFRARED":
            kind = "IR_MISSILE"
        elif guidance == "RADAR":
            kind = "RADAR_MISSILE"
        else:
            kind = "UNKNOWN"
        contacts.append(
            ThreatContact(
                threat_id=str(getattr(threat, "missile_id", "") or f"in_{kind}"),
                kind=kind,  # type: ignore[arg-type]
                position=tuple(obs.current_position),
                altitude=float(obs.current_altitude),
                observed_tick=int(obs.tick),
                confidence=1.0,
                source="DIRECT",
                observer_id=obs.agent_id,
                readiness=1.0,
            )
        )
    for gun in getattr(obs, "observed_gun_threats", None) or []:
        contacts.append(
            ThreatContact(
                threat_id=str(getattr(gun, "threat_id", getattr(gun, "source_id", "gun"))),
                kind="GUN_AAA",
                position=tuple(getattr(gun, "position", obs.current_position)),
                altitude=0.0,
                altitude_ceiling=getattr(gun, "altitude_ceiling", None),
                observed_tick=int(obs.tick),
                confidence=1.0,
                source="DIRECT",
                observer_id=obs.agent_id,
                readiness=1.0,
            )
        )
    return contacts


def compute_bid_proposals(
    observation: Observation,
    target: TargetInfo,
    hp_needed: int | None = None,
    threat_contacts: list[ThreatContact] | None = None,
    context_hash: str = "",
) -> list[BidProposal]:
    """All legal weapon proposals for this bidder (Phase 45)."""
    ask = target.hp_remaining if hp_needed is None else hp_needed
    contacts = threat_contacts if threat_contacts is not None else _threat_contacts_from_observation(
        observation
    )
    frame = config.JET_FRAME_CATALOG.get(observation.frame_type)
    cooldown = int(getattr(frame, "flare_cooldown_ticks", 2) or 2) if frame else 2
    return build_bid_proposals(
        bidder_id=observation.agent_id,
        position=observation.current_position,
        altitude=float(observation.current_altitude),
        heading_deg=float(observation.current_heading),
        velocity=float(observation.current_speed),
        # Unguided weapons whose release ceiling terrain keeps the jet above
        # cannot be delivered here; never offer them.
        payload={
            str(k): int(v)
            for k, v in observation.payload.items()
            if release_reachable(config.WEAPON_CATALOG[k], target.min_overflight_altitude)
        },
        target_id=target.target_id,
        target_type=str(target.target_type),
        target_position=tuple(target.position),
        hp_needed=int(ask),
        flares_remaining=int(observation.flares_remaining),
        flare_capacity=int(observation.flare_capacity),
        flare_cooldown_remaining=int(observation.flare_cooldown_remaining),
        frame_flare_cooldown_ticks=cooldown,
        has_rwr=bool(observation.radar_warning_receiver),
        notch_hold_progress=0,
        notch_hold_ticks=int(observation.notch_hold_ticks),
        hp=3,
        hp_max=3,
        threat_contacts=contacts,
        context_hash=context_hash or "",
        map_diagonal=float(observation.map_diagonal),
        planned_altitude=transit_altitude(observation, list(observation.known_targets)),
    )


def compute_bid(
    observation: Observation,
    target: TargetInfo,
    hp_needed: int | None = None,
    threat_contacts: list[ThreatContact] | None = None,
    context_hash: str = "",
) -> tuple[float, int, str] | None:
    """Returns (bid_utility, payload_offered, weapon_type), or None.

    Uses Phase 45 ``build_bid_proposals``. With zero known exposure the leading
    proposal utility matches the legacy scalar formula (PoC-safe).
    """
    proposals = compute_bid_proposals(
        observation,
        target,
        hp_needed=hp_needed,
        threat_contacts=threat_contacts,
        context_hash=context_hash,
    )
    if not proposals:
        return None
    # Legacy preferred the suitable weapon with most ammo; among equal totals
    # build_bid_proposals already sorts (-utility, -ammo, weapon).
    best = proposals[0]
    return float(best.utility.total), int(best.payload_offered), str(best.weapon_type)

class JetAgentRuntime:
    def __init__(self, agent_id: str, broker: MessageBroker) -> None:
        self.agent_id = agent_id
        self.broker = broker
        self.broker.register(agent_id)
        self.received_messages: list[Message] = []
        self._task: asyncio.Task | None = None
        # Phase 37: per-source sequence for deterministic message/contract IDs.
        self._msg_seq: int = 0
        # When set (bridge), bid windows use logical ticks; else wall-clock shim.
        self._model: WraithNetModel | None = None
        self._use_tick_bid_timeout: bool = False
        self._bid_deadline_tick: int | None = None
        # Optional override clock for tests (returns current logical tick).
        self._tick_clock: Callable[[], int] | None = None

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
        # Phase 45: shared RISK_UPDATE context keyed by contract_id so concurrent
        # managers cannot clobber each other's award/bid pricing context.
        self._risk_context_hash: str = ""
        self._risk_contacts: list[ThreatContact] = []
        self._risk_by_contract: dict[str, tuple[str, list[ThreatContact]]] = {}
        # Local negotiation audit (also mirrored onto model when available).
        self.negotiation_events: list[dict] = []

        # Survives past managed_target_id cleanup — who we ACCEPTed for each target
        # (never includes self). Used by notify_contractor_lost (Phase 8).
        self.managed_contracts: dict[str, set[str]] = {}

    def _record_negotiation(self, event: dict) -> None:
        """Append a negotiation audit event to runtime + model (Part H)."""
        self.negotiation_events.append(event)
        model = self._model
        if model is not None:
            log = getattr(model, "negotiation_events", None)
            if log is None:
                model.negotiation_events = []
                log = model.negotiation_events
            log.append(event)

    def _next_stable_id(self, *parts: object) -> str:
        self._msg_seq += 1
        return stable_id(*parts, self._msg_seq)

    def _current_logical_tick(self) -> int:
        if self._tick_clock is not None:
            return int(self._tick_clock())
        if self._model is not None:
            return int(self._model.tick)
        if self.latest_observation is not None:
            return int(self.latest_observation.tick)
        return 0

    async def _await_bid_window(self) -> bool:
        """Wait for the CFP bid collection window.

        Bridge path (``_use_tick_bid_timeout``): poll/yield until the logical
        clock reaches ``deadline_tick = current_tick + BID_TIMEOUT_TICKS``, or
        until ``BID_TIMEOUT_TICKS`` cooperative yields elapse while the world
        tick is frozen mid-negotiation (in-tick CNP).

        Compat shim (unit tests): ``await asyncio.sleep(BID_TIMEOUT_SECONDS)``
        so existing monkeypatches keep working.

        Returns False if cancelled mid-wait (claim already aborted).
        """
        obs = self.latest_observation
        assert obs is not None
        start_tick = self._current_logical_tick()
        deadline_tick = start_tick + config.BID_TIMEOUT_TICKS
        self._bid_deadline_tick = deadline_tick
        try:
            if self._use_tick_bid_timeout:
                yields = 0
                # Extra yields beyond BID_TIMEOUT_TICKS give mailbox drain room
                # when multiple IDLE agents finish scan_and_negotiate concurrently.
                yield_budget = max(config.BID_TIMEOUT_TICKS, 3) * 2
                while self._current_logical_tick() < deadline_tick:
                    await asyncio.sleep(0)
                    yields += 1
                    if yields >= yield_budget:
                        break
                return True
            await asyncio.sleep(config.BID_TIMEOUT_SECONDS)
            return True
        except asyncio.CancelledError:
            self._abort_open_management()
            return False

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
                    message_id=self._next_stable_id(
                        self.agent_id, target.target_id, obs.tick, "DATALINK"
                    ),
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
        if message.message_type == "RISK_UPDATE":
            contacts = [
                ThreatContact.model_validate(c) for c in (message.threat_contacts or [])
            ]
            context_hash = message.context_hash or ""
            if message.contract_id:
                self._risk_by_contract[message.contract_id] = (context_hash, contacts)
            # Refresh active fields only for our open management contract or when idle
            # (about to bid); never let another manager's update clobber ours mid-award.
            if message.contract_id == self.open_contract_id or self.role == "IDLE":
                self._risk_context_hash = context_hash
                self._risk_contacts = contacts
            return
        if message.message_type == "CFP":
            await self._maybe_bid(message)
        elif (
            message.message_type == "BID"
            and self.managed_target_id is not None
            and message.target_id == self.managed_target_id
        ):
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
        contract_id = self._next_stable_id(
            self.agent_id, target.target_id, obs.tick, "CONTRACT"
        )
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
        contacts = _threat_contacts_from_observation(obs)
        context_hash = stable_id(
            self.open_contract_id,
            self.managed_target_id,
            obs.tick,
            self._cfp_round,
            "RISK",
            *[c.threat_id for c in contacts],
        )
        self._risk_contacts = contacts
        self._risk_context_hash = context_hash
        if self.open_contract_id:
            self._risk_by_contract[self.open_contract_id] = (context_hash, contacts)
        # Phase 45: broadcast normalized threat set before bids.
        await self.broker.send(
            Message(
                message_id=self._next_stable_id(
                    self.agent_id, self.managed_target_id, obs.tick, "RISK_UPDATE"
                ),
                contract_id=self.open_contract_id,
                tick=obs.tick,
                sender_id=self.agent_id,
                receiver_id=BROADCAST_ID,
                message_type="RISK_UPDATE",
                target_id=self.managed_target_id,
                context_hash=context_hash,
                threat_contacts=[c.model_dump() for c in contacts],
            )
        )
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
                proposals = compute_bid_proposals(
                    obs,
                    target,
                    hp_needed=hp_needed,
                    threat_contacts=contacts,
                    context_hash=context_hash,
                )
                if proposals:
                    best = proposals[0]
                    self._collected_bids.append(
                        Message(
                            message_id=self._next_stable_id(
                                self.agent_id,
                                self.managed_target_id,
                                obs.tick,
                                "SELF_BID",
                            ),
                            contract_id=self.open_contract_id,
                            tick=obs.tick,
                            sender_id=self.agent_id,
                            receiver_id=self.agent_id,
                            message_type="BID",
                            target_id=self.managed_target_id,
                            payload_offered=best.payload_offered,
                            bid_utility=float(best.utility.total),
                            weapon_type=best.weapon_type,  # type: ignore[arg-type]
                            context_hash=context_hash,
                            proposals=[p.model_dump() for p in proposals],
                        )
                    )
                    self._record_negotiation(
                        {
                            "event": "bid",
                            "tick": obs.tick,
                            "context_hash": context_hash,
                            "contract_id": self.open_contract_id,
                            "target_id": self.managed_target_id,
                            "bidder_id": self.agent_id,
                            "proposal_count": len(proposals),
                            "flares_remaining": int(obs.flares_remaining),
                            "flare_capacity": int(obs.flare_capacity),
                            "best_utility": float(best.utility.total),
                            "best_weapon": best.weapon_type,
                            "status": "valid",
                        }
                    )
        cfp = Message(
            message_id=self._next_stable_id(
                self.agent_id, self.managed_target_id, obs.tick, "CFP"
            ),
            contract_id=self.open_contract_id,
            tick=obs.tick,
            sender_id=self.agent_id,
            receiver_id=BROADCAST_ID,
            message_type="CFP",
            target_id=self.managed_target_id,
            hp_remaining=hp_needed,
            raw_hp=hp_needed,
            reserved_hp=0,
            context_hash=context_hash,
            threat_contacts=[c.model_dump() for c in contacts],
        )
        await self.broker.send(cfp)
        self._record_negotiation(
            {
                "event": "cfp",
                "tick": obs.tick,
                "context_hash": context_hash,
                "contract_id": self.open_contract_id,
                "target_id": self.managed_target_id,
                "raw_hp": int(hp_needed),
                "unreserved_hp": int(hp_needed),
                "threat_contact_ids": [c.threat_id for c in contacts],
            }
        )
        if not await self._await_bid_window():
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
        # Phase 45: convert BID messages into proposals for award selection.
        from wraithnet.bid_schemas import (
            AttackPlanSummary,
            BidRiskBreakdownModel,
            BidUtilityBreakdownModel,
            BidderStateSnapshot,
        )

        proposals: list[BidProposal] = []
        for bid in self._collected_bids:
            if bid.proposals:
                for raw in bid.proposals:
                    proposals.append(BidProposal.model_validate(raw))
                continue
            util = float(bid.bid_utility or 0.0)
            weapon = str(bid.weapon_type or "JDAM")
            offered = int(bid.payload_offered or 1)
            catalog = config.WEAPON_CATALOG.get(weapon)  # type: ignore[arg-type]
            damage = int(catalog.damage_per_hit) if catalog is not None else 1
            proposals.append(
                BidProposal(
                    bidder_id=bid.sender_id,
                    weapon_type=weapon,
                    payload_offered=offered,
                    plan=AttackPlanSummary(
                        weapon_type=weapon,
                        launch_xy=(0.0, 0.0),
                        launch_altitude=0.0,
                        estimated_ticks_to_launch=0,
                        estimated_ticks_to_impact=1,
                    ),
                    bidder_state=BidderStateSnapshot(
                        agent_id=bid.sender_id,
                        position=(0.0, 0.0),
                        altitude=0.0,
                        heading_deg=0.0,
                        velocity=0.0,
                        hp=3,
                        hp_max=3,
                        flares_remaining=0,
                        flare_capacity=0,
                    ),
                    risk=BidRiskBreakdownModel(
                        ir_risk=0.0, radar_risk=0.0, gun_risk=0.0, combined=0.0
                    ),
                    utility=BidUtilityBreakdownModel(
                        capability=1.0,
                        reach=1.0,
                        survival=1.0,
                        recovery=1.0,
                        total=util,
                    ),
                    normalized_context_hash=bid.context_hash or "",
                    expected_damage_milli=offered * damage * 1000,
                )
            )

        context_hash = self._risk_context_hash or ""
        if self.open_contract_id and self.open_contract_id in self._risk_by_contract:
            context_hash = self._risk_by_contract[self.open_contract_id][0]
        award = select_winning_bids(
            proposals,
            AwardContext(
                target_id=str(self.managed_target_id),
                raw_hp=int(hp_needed),
                reserved_hp=0,
                unreserved_hp=int(hp_needed),
                context_hash=context_hash,
                current_tick=int(self.latest_observation.tick),
            ),
        )
        accepted_by_bidder = {p.bidder_id: p for p in award.accepted}
        winner_ids = set(accepted_by_bidder.keys())
        covered_milli = sum(proposal_damage_milli(p) for p in award.accepted)
        need_milli = max(0, int(hp_needed)) * 1000
        if covered_milli >= need_milli:
            remaining = 0
        else:
            remaining = int(math.ceil((need_milli - covered_milli) / 1000.0))

        self._record_negotiation(
            {
                "event": "award",
                "tick": self.latest_observation.tick,
                "context_hash": context_hash,
                "contract_id": self.open_contract_id,
                "target_id": self.managed_target_id,
                "accepted": [
                    {
                        "bidder_id": p.bidder_id,
                        "weapon_type": p.weapon_type,
                        "payload_offered": p.payload_offered,
                        "expected_damage_milli": proposal_damage_milli(p),
                        "utility": float(p.utility.total),
                        "risk": float(p.risk.combined),
                        "flares_remaining": int(p.bidder_state.flares_remaining),
                        "flare_capacity": int(p.bidder_state.flare_capacity),
                        "flare_readiness": flare_readiness(
                            flares_remaining=int(p.bidder_state.flares_remaining),
                            flare_capacity=int(p.bidder_state.flare_capacity),
                            flare_cooldown_remaining=int(
                                p.bidder_state.flare_cooldown_remaining
                            ),
                            ticks_to_first_ir_encounter=None,
                            frame_flare_cooldown_ticks=2,
                        ),
                    }
                    for p in award.accepted
                ],
                "reject_reasons": dict(award.reject_reasons),
                "low_confidence_coverage": bool(award.low_confidence_coverage),
                "overkill_milli": int(award.overkill_milli),
                "covered_milli": int(covered_milli),
                "raw_need_milli": int(need_milli),
                "award_score": float(award.award_score),
            }
        )
        for bidder_id, reason in sorted(award.reject_reasons.items()):
            self._record_negotiation(
                {
                    "event": "decline",
                    "tick": self.latest_observation.tick,
                    "context_hash": context_hash,
                    "contract_id": self.open_contract_id,
                    "target_id": self.managed_target_id,
                    "bidder_id": bidder_id,
                    "reason": reason,
                }
            )

        for bid in sorted(
            self._collected_bids, key=lambda b: (-(b.bid_utility or 0.0), b.sender_id)
        ):
            winner = accepted_by_bidder.get(bid.sender_id)
            if bid.sender_id == self.agent_id:
                if winner is not None:
                    self._commit(
                        self.managed_target_id,
                        winner.payload_offered,
                        winner.weapon_type,
                    )
                continue
            decision = "ACCEPT" if winner is not None else "REJECT"
            if decision == "ACCEPT":
                self.managed_contracts.setdefault(self.managed_target_id, set()).add(
                    bid.sender_id
                )
            await self.broker.send(
                Message(
                    message_id=self._next_stable_id(
                        self.agent_id,
                        self.managed_target_id,
                        self.latest_observation.tick,
                        decision,
                        bid.sender_id,
                    ),
                    contract_id=self.open_contract_id,
                    tick=self.latest_observation.tick,
                    sender_id=self.agent_id,
                    receiver_id=bid.sender_id,
                    message_type=decision,
                    target_id=self.managed_target_id,
                    payload_offered=winner.payload_offered if winner is not None else None,
                    weapon_type=winner.weapon_type if winner is not None else None,  # type: ignore[arg-type]
                )
            )

        if remaining > 0 and self._cfp_round < MAX_CFP_ROUNDS:
            await self._broadcast_cfp_and_collect(remaining)
            return

        if remaining > 0:
            self.broker.close_contract(self.managed_target_id)
            self._record_negotiation(
                {
                    "event": "reservation_release",
                    "tick": self.latest_observation.tick,
                    "context_hash": context_hash,
                    "contract_id": self.open_contract_id,
                    "target_id": self.managed_target_id,
                    "reason": "UNCOVERED_SHORTFALL",
                    "remaining_hp": remaining,
                }
            )
        self.open_contract_id = None
        self.managed_target_id = None
        if self.role == "MANAGER":
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
        contacts = list(self._risk_contacts)
        context_hash = self._risk_context_hash or ""
        if cfp.contract_id and cfp.contract_id in self._risk_by_contract:
            context_hash, contacts = self._risk_by_contract[cfp.contract_id]
        if cfp.threat_contacts:
            contacts = [
                ThreatContact.model_validate(c) for c in cfp.threat_contacts
            ]
            context_hash = cfp.context_hash or context_hash
        if cfp.contract_id:
            self._risk_by_contract[cfp.contract_id] = (context_hash, contacts)
        bid = compute_bid(
            obs,
            target,
            hp_needed=cfp.hp_remaining,
            threat_contacts=contacts,
            context_hash=context_hash,
        )
        if bid is None:
            return
        proposals = compute_bid_proposals(
            obs,
            target,
            hp_needed=cfp.hp_remaining,
            threat_contacts=contacts,
            context_hash=context_hash,
        )
        utility, payload_offered, weapon_type = bid
        self.role = "CONTRACTOR_PENDING"
        self.pending_bid_since_tick = obs.tick
        await self.broker.send(
            Message(
                message_id=self._next_stable_id(
                    self.agent_id, cfp.target_id, obs.tick, "BID"
                ),
                contract_id=cfp.contract_id,
                tick=obs.tick,
                sender_id=self.agent_id,
                receiver_id=cfp.sender_id,
                message_type="BID",
                target_id=cfp.target_id,
                payload_offered=payload_offered,
                bid_utility=utility,
                weapon_type=weapon_type,  # type: ignore[arg-type]
                context_hash=context_hash,
                proposals=[p.model_dump() for p in proposals],
            )
        )
        self._record_negotiation(
            {
                "event": "bid",
                "tick": obs.tick,
                "context_hash": context_hash,
                "contract_id": cfp.contract_id,
                "target_id": cfp.target_id,
                "bidder_id": self.agent_id,
                "proposal_count": len(proposals),
                "flares_remaining": int(obs.flares_remaining),
                "flare_capacity": int(obs.flare_capacity),
                "best_utility": float(utility),
                "best_weapon": weapon_type,
                "status": "valid",
            }
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
        obs = self.latest_observation
        tick = obs.tick if obs is not None else 0
        self.open_contract_id = self._next_stable_id(
            self.agent_id, target_id, tick, "REOPEN"
        )
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

    def _select_defensive_threat(
        self, obs: Observation
    ) -> InboundThreat | None:
        """Deterministic priority: shortest ETTI, then defendable guidance, then id."""
        threats = list(obs.inbound_threats)
        if not threats:
            return None

        def _can_defend(t: InboundThreat) -> bool:
            if t.guidance == "INFRARED":
                return obs.flares_remaining > 0 and obs.flare_cooldown_remaining <= 0
            if t.guidance == "RADAR":
                return obs.radar_warning_receiver
            return False

        threats.sort(
            key=lambda t: (
                float(t.estimated_time_to_impact),
                0 if _can_defend(t) else 1,
                t.missile_id,
            )
        )
        return threats[0]

    def _cruise_altitude(self, obs: Observation) -> float:
        """Transit/egress altitude (tactical band, see ``transit_altitude``)."""
        return transit_altitude(obs, self.effective_known_targets())

    def _run_in_altitude(
        self, obs: Observation, weapon: config.WeaponSpec, distance: float
    ) -> float:
        """Stay at transit, then descend just in time to release at the target."""
        transit = self._cruise_altitude(obs)
        attack = attack_altitude(weapon, transit)
        if attack >= transit:
            return transit
        frame = config.JET_FRAME_CATALOG[obs.frame_type]
        # Flown descent: nose over, then sink at the pitch-limited rate.
        descent_ticks = math.ceil(
            max(0.0, obs.current_altitude - attack) / climb_rate(frame)
        ) + pitch_lag_ticks(frame)
        start = weapon.range + frame.max_speed * (descent_ticks + 2)
        return attack if distance <= start else transit

    def _with_defense(self, intent: ActionIntent, obs: Observation) -> ActionIntent:
        """Attach FLARE/NOTCH alongside the primary task intent when threatened.

        Low-fuel / RTB jets still defend before continuing home. Strike
        fighters/interceptors whose evasion already nullifies SAM_RADAR
        damage skip notching so PoC routes stay near-optimal; bombers and
        low-evasion frames still notch imminent radar threats.

        Phase 53: when NOTCH is active, keep pitch level and do not command
        an explicit roll — yaw + speed remain the Phase 39 notch authority.
        """
        threat = self._select_defensive_threat(obs)
        if threat is None:
            return intent
        data = intent.model_dump()
        if (
            threat.guidance == "INFRARED"
            and obs.flares_remaining > 0
            and obs.flare_cooldown_remaining <= 0
        ):
            data["countermeasure_action"] = "FLARE"
            data["defensive_threat_id"] = threat.missile_id
        if threat.guidance == "RADAR" and obs.radar_warning_receiver:
            evasion = config.JET_FRAME_CATALOG[obs.frame_type].evasion_bonus
            sam_damage = config.WEAPON_CATALOG["SAM_RADAR"].damage_per_hit
            needs_notch = evasion < sam_damage and threat.estimated_time_to_impact <= 3.0
            if needs_notch:
                data["maneuver_defense"] = "NOTCH"
                data["defensive_threat_id"] = threat.missile_id
                data["desired_heading"] = (threat.estimated_bearing + 90.0) % 360.0
                data["desired_pitch_deg"] = 0.0
                data["desired_roll_deg"] = None
        return ActionIntent(**data)

    def decide_intent(self) -> ActionIntent:
        """Pure function of current state + latest_observation. Phase 4's
        Bridge calls this once per tick per agent to get its next ActionIntent."""
        obs = self.latest_observation
        assert obs is not None
        cruise_alt = self._cruise_altitude(obs)
        if self.role == "RTB":
            bearing = bearing_to(obs.current_position, obs.home_base)
            # Phase 53: level pitch; altitude eases via Phase 51 recovery override
            # on short final (world forces pitch→0 / RECOVERY_ALTITUDE).
            intent = ActionIntent(
                agent_id=self.agent_id,
                tick=obs.tick,
                action_type="MOVE",
                target_position=obs.home_base,
                desired_heading=bearing,
                desired_pitch_deg=0.0,
                desired_altitude=cruise_alt,
            )
            return self._with_defense(intent, obs)
        if self.committed_target_id is None:
            # Mission brief: transit toward incomplete DESTROY subjects until
            # sensors acquire them. Hold only when no brief remains or we are
            # already on station at the nearest waypoint.
            waypoints = list(obs.briefed_seek_positions)
            if waypoints:
                nearest = min(
                    waypoints, key=lambda p: math.dist(obs.current_position, p)
                )
                if math.dist(obs.current_position, nearest) > 1.0:
                    bearing = bearing_to(obs.current_position, nearest)
                    intent = ActionIntent(
                        agent_id=self.agent_id,
                        tick=obs.tick,
                        action_type="MOVE",
                        target_position=nearest,
                        desired_heading=bearing,
                        desired_pitch_deg=0.0,
                        desired_altitude=cruise_alt,
                    )
                    return self._with_defense(intent, obs)
            intent = ActionIntent(
                agent_id=self.agent_id,
                tick=obs.tick,
                action_type="IDLE",
                desired_pitch_deg=0.0,
                desired_altitude=cruise_alt,
            )
            return self._with_defense(intent, obs)
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
            or not release_reachable(
                config.WEAPON_CATALOG[self.committed_weapon_type],
                target.min_overflight_altitude,
            )
        ):
            self.committed_target_id = None
            self.committed_weapon_type = None
            self.role = "IDLE"
            # Fall through to briefed-seek / IDLE via a recursive-free re-entry.
            return self.decide_intent()
        distance = math.dist(obs.current_position, target.position)
        # Safety: never STRIKE a memory-only target — must be directly sensing it.
        directly_sensed = any(t.target_id == target.target_id for t in obs.known_targets)
        weapon = config.WEAPON_CATALOG[self.committed_weapon_type]
        bearing = bearing_to(obs.current_position, target.position)
        # Transit high/low by threat picture; unguided weapons descend late.
        run_in_alt = self._run_in_altitude(obs, weapon, distance)
        gated = weapon_has_attitude_envelope(weapon)
        delivery = (
            needed_attitude_for_release(
                weapon, obs.current_pitch_deg, obs.current_roll_deg
            )
            if gated
            else None
        )
        in_range = distance <= weapon.range
        if in_range and directly_sensed:
            in_arc = is_in_firing_arc(
                obs.current_heading,
                obs.current_position,
                target.position,
                weapon.firing_arc_deg,
            )
            if in_arc:
                ok_att, _reason = release_attitude_ok(
                    weapon,
                    pitch_deg=obs.current_pitch_deg,
                    roll_deg=obs.current_roll_deg,
                    altitude=obs.current_altitude,
                )
                if not ok_att:
                    # Phase 53: roll upright / pitch legally before STRIKE/LAUNCH.
                    att = delivery or needed_attitude_for_release(
                        weapon, obs.current_pitch_deg, obs.current_roll_deg
                    )
                    intent = ActionIntent(
                        agent_id=self.agent_id,
                        tick=obs.tick,
                        action_type="IDLE",
                        desired_heading=bearing,
                        desired_pitch_deg=att.desired_pitch_deg,
                        desired_roll_deg=att.desired_roll_deg,
                        desired_altitude=run_in_alt,
                    )
                    return self._with_defense(intent, obs)
                self.committed_payload -= 1
                # Phase 38: guided weapons emit LAUNCH; direct-fire stays STRIKE.
                action = (
                    "LAUNCH" if weapon.guidance != "NONE" else "STRIKE"
                )
                fire_kwargs: dict = {
                    "agent_id": self.agent_id,
                    "tick": obs.tick,
                    "action_type": action,
                    "target_id": target.target_id,
                    "weapon_type": self.committed_weapon_type,
                    # Keep nose on target while the world advances airspeed.
                    "desired_heading": bearing,
                }
                if delivery is not None:
                    fire_kwargs["desired_pitch_deg"] = delivery.desired_pitch_deg
                    fire_kwargs["desired_roll_deg"] = delivery.desired_roll_deg
                intent = ActionIntent(**fire_kwargs)
                return self._with_defense(intent, obs)
            # In range but not pointed at the target yet: turn toward it this
            # tick instead of striking. Still in position, just not yet able
            # to fire — does not consume payload, does not lose the contract.
            # Line up delivery attitude while rotating to bear (gated weapons).
            bear_kwargs: dict = {
                "agent_id": self.agent_id,
                "tick": obs.tick,
                "action_type": "IDLE",
                "desired_heading": bearing,
                "desired_altitude": run_in_alt,
            }
            if delivery is not None:
                bear_kwargs["desired_pitch_deg"] = delivery.desired_pitch_deg
                bear_kwargs["desired_roll_deg"] = delivery.desired_roll_deg
            intent = ActionIntent(**bear_kwargs)
            return self._with_defense(intent, obs)
        if distance <= 1e-9:
            # Coincident with remembered coords but no live sense — hold the
            # waypoint so the next tick can reacquire without a /0 gate.
            intent = ActionIntent(
                agent_id=self.agent_id,
                tick=obs.tick,
                action_type="MOVE",
                target_position=target.position,
                desired_heading=bearing,
                desired_pitch_deg=0.0,
                desired_altitude=run_in_alt,
            )
            return self._with_defense(intent, obs)
        # Close on a release gate just inside weapon range (not the target
        # centroid). High max_speed vs short JDAM/AGM65 ranges would otherwise
        # overshoot the envelope in one MOVE and never pickle.
        ux = (obs.current_position[0] - target.position[0]) / distance
        uy = (obs.current_position[1] - target.position[1]) / distance
        gate_standoff = max(weapon.range * 0.5, 1e-3)
        gate = (
            target.position[0] + ux * gate_standoff,
            target.position[1] + uy * gate_standoff,
        )
        # A gate well off the nose and inside the turn circle can't be reached
        # by turning — the jet would orbit it forever. Extend straight out to
        # two turn radii, then come back around for a straight-in run.
        frame = config.JET_FRAME_CATALOG[obs.frame_type]
        turn_radius = frame.max_speed / math.radians(frame.turn_rate_deg_per_tick)
        heading = bearing
        if (
            distance < 2.0 * turn_radius
            and angular_distance(obs.current_heading, bearing) > 90.0
        ):
            heading = obs.current_heading
        # En route: level pitch + cruise altitude (Phase 51 wings-level ease).
        # Leave roll unset so bank-assist can help the turn toward the gate.
        intent = ActionIntent(
            agent_id=self.agent_id,
            tick=obs.tick,
            action_type="MOVE",
            target_position=gate,
            desired_heading=heading,
            desired_pitch_deg=0.0,
            desired_altitude=run_in_alt,
        )
        return self._with_defense(intent, obs)

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
