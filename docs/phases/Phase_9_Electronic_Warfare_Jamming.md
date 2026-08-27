# Phase 9 — Electronic Warfare: Comms Jamming

> **How to use this document:** This is a complete, standalone prompt. Give an LLM coding agent this entire file as its task. Assume Phases 0–8 are complete and passing: weapon typing (Phase 6), Fog of War with `DATALINK_CONTACT` (Phase 7), and SAM sites/jet destruction/Manager Fallback (Phase 8) all work. Read `docs/04_6th-Gen_Warfare_Sim_Design_Document_v3_Phase2.md` §8 before starting. This phase modifies `config.py`, `broker.py`, `world.py`, `agents.py`, and `bridge.py`. It adds **no new `Message` or `ActionIntent` fields** and **no new negotiation branching in `_broadcast_cfp_and_collect`/`_resolve_contract`** — jamming is implemented almost entirely as a `MessageBroker` routing rule; see Part D for the one small, genuinely new piece of agent-side state this phase does need, and why.

## Context

Jamming is **not** a new transport (there is no WebSocket anywhere in this project, and this phase does not add one — see `docs/03_Phase2_Expansion_Design_Review_Findings.md` finding G1). It's a routing rule on the existing in-process `MessageBroker`: while an agent is jammed, its outgoing messages never leave its mailbox queue and it never receives incoming ones. It does **not** affect `Observation` delivery — the Bridge still calls `agent.set_observation(...)` directly (bypassing the Broker) for every jet every tick regardless of jamming, exactly as before, since that represents onboard sensors/GPS, not radio (finding G4).

**Read this before touching `agents.py`:** a naive implementation might try to add explicit "am I jammed?" branches inside the CNP negotiation methods. Don't. Trace through what already happens to a jammed Manager first (Part C) — you'll find the *existing* Phase 3/8 timeout and give-up machinery already handles it correctly with zero new code. The *one* place that genuinely needs a new mechanism is a bidder stuck in `CONTRACTOR_PENDING` waiting for an `ACCEPT`/`REJECT` that jamming can now cause to never arrive (Part D) — implement that, and resist the urge to add anything else.

## Part A — `wraithnet/config.py` additions

```python
JAM_RADIUS: float = 12.0
CONTRACTOR_PENDING_TTL_TICKS: int = 10   # mirrors CONTRACT_CLAIM_TTL_TICKS's existing pattern — see Part D

# Extend the existing SCENARIO dict (do not replace targets/jets/sam_sites, add a new key):
SCENARIO["jammers"] = [
    {"jammer_id": "EW_Jammer_1", "position": (46.0, 19.0)},
]
```

Note jammers have **no CNP identity at all** — no `target_id` in the `targets` sense, no `hp_required`, never destroyable or biddable-on in this expansion (design doc finding K2). `"jammer_id"` here is just a human-readable label for logging/tests, not a `TargetInfo.target_id`.

## Part B — `wraithnet/broker.py`: jamming-aware routing

```python
class MessageBroker:
    def __init__(self) -> None:
        self._mailboxes: dict[str, asyncio.Queue[Message]] = {}
        self.open_contracts: dict[str, dict] = {}
        self._jammed: set[str] = set()   # NEW

    def set_jammed(self, agent_id: str, jammed: bool) -> None:   # NEW
        if jammed:
            self._jammed.add(agent_id)
        else:
            self._jammed.discard(agent_id)

    def is_jammed(self, agent_id: str) -> bool:   # NEW
        return agent_id in self._jammed

    # ... register/send/receive/inbox_size/try_open_contract/close_contract/
    #     is_claimed/expire_stale_contracts all UNCHANGED ...

    async def _route(self, message: Message) -> None:
        if message.sender_id in self._jammed:   # NEW: a jammed sender's traffic never leaves
            return
        if message.receiver_id == BROADCAST_ID:
            for agent_id, mailbox in self._mailboxes.items():
                if agent_id != message.sender_id and agent_id not in self._jammed:   # NEW: skip jammed recipients
                    await mailbox.put(message)
        else:
            if message.receiver_id in self._jammed:   # NEW
                return
            mailbox = self._mailboxes.get(message.receiver_id)
            if mailbox is not None:
                await mailbox.put(message)
```

This is the entire jamming mechanism from the Broker's point of view: a jammed agent's messages are dropped exactly like the existing "unregistered receiver" case (no exception, no retry, no log spam) — silently and cheaply, same failure-handling philosophy as the rest of `MessageBroker`.

## Part C — `wraithnet/world.py`: `EwJammerAgent` and `comms_jammed` computation

### 1. New agent (plain Mesa agent, static, never a CNP target — same category as `SamSiteAgent`)

```python
class EwJammerAgent(mesa.Agent):
    def __init__(self, model, jammer_id: str, position: tuple[float, float]) -> None:
        super().__init__(model)
        self.jammer_id = jammer_id
        self.position = position
```

### 2. `WraithNetModel.__init__`/`_spawn_scenario`

Add `self.jammers: dict[str, EwJammerAgent] = {}` (initialized alongside `self.sam_sites`), and spawn from `config.SCENARIO.get("jammers", [])`:
```python
for j in config.SCENARIO.get("jammers", []):
    self.jammers[j["jammer_id"]] = EwJammerAgent(self, j["jammer_id"], j["position"])
```

### 3. `build_observation` computes `comms_jammed` (World owns this computation — same reasoning as the SAM-engagement check already living in `_apply_sam_damage`, not scattered across the Bridge)

```python
def build_observation(self, agent_id: str, broker: "MessageBroker") -> Observation:
    jet = self.jets[agent_id]
    known_targets = [...]   # unchanged from Phase 7
    comms_jammed = any(
        math.dist(jet.pos, jammer.position) <= config.JAM_RADIUS
        for jammer in self.jammers.values()
    )
    return Observation(
        agent_id=agent_id, tick=self.tick, current_position=jet.pos,
        sensor_radius=config.SENSOR_RADIUS, payload=dict(jet.payload),
        known_targets=known_targets, comms_jammed=comms_jammed,   # CHANGED: was hard-coded False
    )
```

## Part D — `wraithnet/agents.py`: closing the one genuinely new zombie risk jamming introduces

**Why this is needed (read before implementing):** every existing wait-for-a-response state in the CNP state machine already has a backstop against permanent staleness — `MANAGER` is bounded by `MAX_CFP_ROUNDS`/`MAX_TICK_WAIT_SECONDS`, an open Broker contract is bounded by `CONTRACT_CLAIM_TTL_TICKS`. **`CONTRACTOR_PENDING` is the one exception:** a bidder that sent a `BID` and is waiting for `ACCEPT`/`REJECT` has no timeout at all, because before jamming existed, that message was *always* reliably delivered (every registered agent's mailbox always received it, unconditionally). Jamming (Part B) is the first mechanism that can make that message never arrive despite both agents being registered — so without a fix, a bidder that gets jammed (or whose Manager gets jammed) between `BID` and the resulting decision is stuck in `CONTRACTOR_PENDING` **forever**: it can never bid again (`_maybe_bid` requires `role == "IDLE"`), never manage anything (`scan_and_negotiate` requires `role == "IDLE"`), and never produces a useful `ActionIntent` (`decide_intent` has nothing committed). This is the exact zombie shape documented in `docs/02_Code_Review_Phases_3-5.md` for the original (now-fixed) bug, arrived at by a new route.

### 1. New state

```python
# in __init__, alongside the other Contractor-side state:
self.pending_bid_since_tick: int | None = None
```

### 2. Set it when entering `CONTRACTOR_PENDING`, in `_maybe_bid`

```python
async def _maybe_bid(self, cfp: Message) -> None:
    ...
    self.role = "CONTRACTOR_PENDING"
    self.pending_bid_since_tick = obs.tick   # NEW
    await self.broker.send(Message(...))
```

### 3. Clear it whenever leaving `CONTRACTOR_PENDING` (both existing exit paths)

```python
def _commit(self, target_id: str, payload_offered: int, weapon_type: str) -> None:
    self.role = "CONTRACTOR"
    self.committed_target_id = target_id
    self.committed_payload = payload_offered
    self.committed_weapon_type = weapon_type
    self.pending_bid_since_tick = None   # NEW
```
```python
elif message.message_type == "REJECT" and self.role == "CONTRACTOR_PENDING":
    self.role = "IDLE"
    self.pending_bid_since_tick = None   # NEW
```

### 4. New method: `check_pending_bid_timeout()` — called once per tick, for every agent, by the Bridge

```python
def check_pending_bid_timeout(self) -> None:
    """Self-heal a CONTRACTOR_PENDING bid that's been waiting too long for a
    decision — the only realistic cause in this project is the ACCEPT/REJECT
    being dropped by jamming (Part B), since delivery was otherwise always
    reliable. Mirrors the existing CONTRACT_CLAIM_TTL_TICKS pattern."""
    if self.role != "CONTRACTOR_PENDING" or self.pending_bid_since_tick is None:
        return
    obs = self.latest_observation
    if obs is not None and obs.tick - self.pending_bid_since_tick > config.CONTRACTOR_PENDING_TTL_TICKS:
        self.role = "IDLE"
        self.pending_bid_since_tick = None
```

This is a **self-check**, not something the Manager or Broker needs to know about — the bidder simply gives up waiting and becomes eligible to bid/manage again on a future tick, exactly like a normal `REJECT` would have produced. No message is sent (there's nothing useful to send — if this bidder is the one jammed, a message wouldn't get out anyway; if the Manager is jammed, there's no one listening).

## Part E — `wraithnet/bridge.py`: push jammed status, call the pending-bid timeout check

```python
async def _tick(self) -> None:
    # 1. Broadcast state (+ push jamming status to the Broker)
    for agent_id, agent in self.agents.items():
        obs = self.model.build_observation(agent_id, self.broker)
        agent.set_observation(obs)
        self.broker.set_jammed(agent_id, obs.comms_jammed)   # NEW

    # 1b. Refresh world-view memory and share new contacts (Phase 7)
    await asyncio.gather(*(a.share_new_contacts() for a in self.agents.values()))
    await asyncio.sleep(0)

    # 1c. Self-heal any CONTRACTOR_PENDING bid that's timed out (Phase 9)   # NEW
    for agent in self.agents.values():
        agent.check_pending_bid_timeout()

    # 1d. SAM/destruction-driven fallback re-tasking (Phase 8) — unchanged from Phase 8,
    #     just renumbered here for clarity; this actually runs after model.step() — see Phase 8 doc.

    # 2. Negotiate (idle agents only), bounded by MAX_TICK_WAIT_SECONDS — unchanged
    ...
```

(The rest of `_tick()` — negotiate, collect intents, `model.step(intents)`, Phase 8's destruction/fallback handling, contract cleanup — is unchanged by this phase; only the two new lines above are added, both before the negotiation phase.)

## Part F — Tests

### `tests/test_broker.py` (additions)

* `set_jammed("Jet_1", True)` then broadcasting a `DATALINK_CONTACT`/`CFP` from `"Jet_1"`: no other agent's mailbox receives it (sender-jammed broadcast reaches no one).
* A jammed agent does not receive a broadcast sent by a *non*-jammed sender: `set_jammed("Jet_2", True)`; `Jet_1` (not jammed) broadcasts; assert `Jet_2`'s mailbox stays empty while `Jet_3` (not jammed) receives it normally.
* A direct message (`BID`/`ACCEPT`/`REJECT`) is dropped if either the sender or the receiver is jammed (test both directions separately), and delivered normally once `set_jammed(..., False)` is called for whichever side was jammed.
* `is_jammed` correctly reflects the latest `set_jammed` call; toggling back and forth works (this will matter for a jet that repeatedly enters/exits the jam radius over several ticks in the full scenario).

### `tests/test_world.py` (additions)

* `EwJammerAgent`/`self.jammers` spawn correctly from `config.SCENARIO["jammers"]`.
* `build_observation`'s `comms_jammed` is `True` for a jet placed within `config.JAM_RADIUS` of a jammer's position, `False` otherwise — test both cases directly, don't rely on the full scenario's exact geometry for this unit-level check (use a jet moved to an arbitrary position via `model.space.move_agent`).
* Using the real v3 scenario numbers: confirm `Jet_3`'s straight-line path toward `Target_B` passes through `EW_Jammer_1`'s radius at some point (e.g., compute `comms_jammed` for `Jet_3` at a few interpolated positions along that segment and assert at least one is `True`) — this is the geometric property the scenario rationale in `docs/04_..._v3_Phase2.md` §2 depends on; assert it explicitly.

### `tests/test_electronic_warfare.py` (new file)

Use `pytest-asyncio`, hand-built `Observation`s and a shared `MessageBroker`, following the isolation style of `test_cnp.py`/`test_fog_of_war.py`.

1. **A jammed Manager's contract eventually frees itself without external help**, using the *existing* machinery (no jamming-specific code should be needed for this to pass — if it doesn't pass without touching `_broadcast_cfp_and_collect`/`_resolve_contract`, something in Part D went further than intended): set up a Manager and one potential bidder; jam the Manager (`broker.set_jammed(manager.agent_id, True)`) for the whole test; call `scan_and_negotiate()`; wait past `MAX_CFP_ROUNDS * BID_TIMEOUT_SECONDS` (monkeypatch both down for a fast test); assert the Manager ends back at `role == "IDLE"` and `broker.is_claimed(target_id) is False` (contract released), and the bidder (never having received a CFP at all, since it was dropped) stays `IDLE` throughout, not stuck anywhere.
2. **An un-jammed bidder whose `ACCEPT` gets jammed in transit self-heals via the TTL, not a hang:** set up a Manager and a bidder; let the bidder successfully send a `BID` (unjammed at that point); then jam the *bidder* before the Manager's `ACCEPT` would arrive (`broker.set_jammed(bidder.agent_id, True)` right after the `BID` is sent, before `_resolve_contract` runs); let the Manager resolve and attempt to send `ACCEPT` (which the Broker will drop, per Part B, since the recipient is jammed); advance simulated ticks (via repeated `set_observation`/`check_pending_bid_timeout()` calls with increasing `tick` values) past `CONTRACTOR_PENDING_TTL_TICKS`; assert the bidder reverts to `role == "IDLE"` on its own, with `pending_bid_since_tick` reset to `None`.
3. **`check_pending_bid_timeout` does nothing to an agent that isn't `CONTRACTOR_PENDING`**, and does nothing to a `CONTRACTOR_PENDING` agent whose wait hasn't exceeded the TTL yet (test both as explicit negative cases, not just inferred from test 2 passing).
4. **Jamming never affects `Observation` delivery:** build a `WraithNetModel`, place a jet within `JAM_RADIUS` of a jammer, call `build_observation` — assert `comms_jammed is True` **and** `known_targets`/`payload`/`current_position` are populated exactly as they would be if unjammed (i.e., jamming is informational on the `Observation` side, never a filter — regression test for design doc finding G4).

### `tests/test_bridge.py` / `tests/test_scenario.py` (updates)

* Confirm the full v3 scenario still resolves to completion with jamming active along `Jet_3`'s real flight path (per the `test_world.py` geometric check above) — i.e., don't just unit-test jamming in isolation; prove the real run actually passes through a jammed period and still finishes correctly (`Target_B` still gets destroyed, by `Jet_3`, eventually).
* Add an assertion that at least one agent's `received_messages`/internal state shows evidence of having been jammed at some point during a real run (e.g., track jammed-tick count via a simple counter in the test, or inspect `Observation.comms_jammed` snapshots if the test harness records them) — same "prove the mechanic was actually exercised, not just bypassed by coincidence" principle used in Phase 7's tests for Fog of War.

## Explicit non-goals for this phase

* No mobile, multiple, or destroyable jammers (finding K2) — one static `EwJammerAgent`, fixed position, never a CNP target.
* No change to `Observation` delivery gating — jamming affects `Message` routing exclusively (finding G4); do not add any jamming check to `build_observation`'s target-visibility logic or to the Bridge's `set_observation` call.
* No new `Message`/`ActionIntent` fields, no new `message_type`, no new jamming-specific branches inside `_broadcast_cfp_and_collect`/`_resolve_contract`/`try_become_manager` — if your implementation adds any of these, re-read the Context section above; it means the existing timeout/give-up machinery wasn't relied on where it should have been.

## Acceptance criteria

1. `pytest` passes, including the new `test_electronic_warfare.py` and all updated tests.
2. `MessageBroker._route` is the only place jamming is enforced at the transport level; `_maybe_bid`/`try_become_manager`/`_broadcast_cfp_and_collect`/`_resolve_contract` are otherwise textually unchanged from Phase 8 except for whatever incidental changes those phases already required (weapon typing, `managed_contracts`) — jamming resilience falls out of the *existing* timeout/TTL/give-up mechanisms, not new branching in these methods.
3. No agent can become permanently stuck in `CONTRACTOR_PENDING` regardless of when jamming starts/stops relative to a bid being sent — verified by the dedicated TTL unit test (Part F.2), not just by the absence of a hang in a full run.
4. `Observation` delivery (`set_observation`) is called for every jet every tick regardless of jamming status — verified directly (Part F.4).
5. `python -m wraithnet.main` still runs the full v3 scenario to completion, passing through at least one jammed period along a real flight path without hanging, stranding an agent, or failing to eventually destroy all three CNP targets.
