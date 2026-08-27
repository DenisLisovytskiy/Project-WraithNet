# Phase 7 — Sensor Fusion & Fog of War

> **How to use this document:** This is a complete, standalone prompt. Give an LLM coding agent this entire file as its task. Assume Phase 6 (Weapon System & Schema Foundations) is complete and passing: `TargetInfo.target_type`, `Observation.payload: dict[WeaponType, int]`/`sensor_radius`/`comms_jammed`, `ActionIntent.weapon_type`, `Message.weapon_type`, and the weapon-suitability-aware `compute_bid` all exist. Read `docs/04_6th-Gen_Warfare_Sim_Design_Document_v3_Phase2.md` §5 before starting. This phase makes `sensor_radius` and `comms_jammed`'s sibling concept — "what does this agent actually know" — real. It modifies `world.py`, `schemas.py`, `broker.py` (Message only, not routing), `agents.py`, and `bridge.py`.

## Context

As of Phase 6, `build_observation` still returns *every* live target regardless of distance, and `sensor_radius` sits unused in the schema. This phase makes target visibility genuinely distance-limited, and introduces a `DATALINK_CONTACT` broadcast so agents that can't currently see a target can still learn about it (and bid on it) from a squadmate that can. **A hard safety rule this phase must enforce:** an agent may `MOVE` toward, and `BID` on, a target it only knows about from memory/relay — but it may never `STRIKE` a target unless it is *currently, directly* sensing it this tick. This prevents an agent from firing blind at stale, possibly-wrong coordinates.

## Part A — `wraithnet/config.py` addition

```python
DATALINK_CONTACT_TTL_TICKS: int = 15  # a remembered-but-not-currently-sensed target older than this is forgotten
```

## Part B — `wraithnet/schemas.py` changes

```python
class Message(BaseModel):
    message_id: str
    contract_id: Optional[str] = None   # CHANGED: was required str; None only for DATALINK_CONTACT
    tick: int = Field(ge=0)
    sender_id: str
    receiver_id: str
    message_type: Literal["CFP", "BID", "ACCEPT", "REJECT", "DATALINK_CONTACT"]  # NEW member
    target_id: str
    hp_remaining: Optional[int] = None
    payload_offered: Optional[int] = None
    weapon_type: Optional[WeaponType] = None
    bid_utility: Optional[float] = None
    target_info: Optional[TargetInfo] = None   # NEW — required on DATALINK_CONTACT

    @model_validator(mode="after")
    def _check_required_fields_for_message_type(self) -> "Message":
        if self.message_type == "DATALINK_CONTACT":
            if self.target_info is None:
                raise ValueError("DATALINK_CONTACT requires target_info")
            if self.contract_id is not None:
                raise ValueError("DATALINK_CONTACT must not carry a contract_id")
            if self.receiver_id != BROADCAST_ID:
                raise ValueError("DATALINK_CONTACT must be broadcast")
            return self
        if self.contract_id is None:
            raise ValueError(f"{self.message_type} requires contract_id")
        if self.message_type == "CFP":
            if self.hp_remaining is None:
                raise ValueError("CFP requires hp_remaining")
            if self.receiver_id != BROADCAST_ID:
                raise ValueError("CFP must be broadcast")
        if self.message_type == "BID":
            if self.payload_offered is None or self.bid_utility is None or self.weapon_type is None:
                raise ValueError("BID requires payload_offered, bid_utility, and weapon_type")
        if self.message_type in ("BID", "ACCEPT", "REJECT"):
            if self.receiver_id == BROADCAST_ID:
                raise ValueError(f"{self.message_type} must not be broadcast")
        return self
```

`MessageBroker`'s routing logic (`_route`, `send`, `register`, etc.) is **unchanged** by this phase — `DATALINK_CONTACT` is just another broadcast message type; the Broker doesn't need to know it exists. Do not touch `broker.py` in this phase.

## Part C — `wraithnet/world.py`: sensor-limited `build_observation`

```python
def build_observation(self, agent_id: str, broker: "MessageBroker") -> Observation:
    jet = self.jets[agent_id]
    known_targets = [
        TargetInfo(
            target_id=t.target_id, position=t.position, target_type=t.target_type,
            hp_required=t.hp_required, hp_remaining=t.hp_remaining,
            claimed=broker.is_claimed(t.target_id),
        )
        for t in self.targets.values()
        if math.dist(jet.pos, t.position) <= config.SENSOR_RADIUS   # NEW: sensor-limited
    ]
    return Observation(
        agent_id=agent_id, tick=self.tick, current_position=jet.pos,
        sensor_radius=config.SENSOR_RADIUS, payload=dict(jet.payload),
        known_targets=known_targets, comms_jammed=False,
    )
```

`Observation.known_targets` now means, precisely, "targets this jet is **directly, currently sensing**." It is deliberately *not* where remembered/relayed targets live — that's the agent's own `world_view` (Part D). Do not merge memory into `build_observation`'s output; the World has no concept of what an agent remembers, and shouldn't need one.

## Part D — `wraithnet/agents.py`: `world_view` memory, contact sharing, and the direct-sensing `STRIKE` rule

### 1. New state on `JetAgentRuntime.__init__`

```python
self.world_view: dict[str, tuple[TargetInfo, int]] = {}  # target_id -> (info, last_updated_tick)
```

### 2. `effective_known_targets()` — the merged view everything else should use instead of raw `Observation.known_targets`

```python
def effective_known_targets(self) -> list[TargetInfo]:
    """Direct sensor readings, merged with remembered (datalink/relay) targets
    not currently in direct sensor range. Safe for MOVE/BID decisions;
    NOT safe for deciding whether to STRIKE — see decide_intent()."""
    return [info for info, _tick in self.world_view.values()]
```

### 3. `refresh_world_view()` — called once per tick, right after `set_observation`, for *every* agent regardless of role

```python
def refresh_world_view(self) -> list[TargetInfo]:
    """Update world_view from this tick's direct Observation, expire stale
    entries, and return the list of targets whose info is NEW or CHANGED
    since last tick (for share_new_contacts() to broadcast)."""
    obs = self.latest_observation
    assert obs is not None
    changed: list[TargetInfo] = []
    for target in obs.known_targets:  # direct sensor readings only
        previous = self.world_view.get(target.target_id)
        if previous is None or previous[0].hp_remaining != target.hp_remaining or previous[0].claimed != target.claimed:
            changed.append(target)
        self.world_view[target.target_id] = (target, obs.tick)
    stale_ids = [
        tid for tid, (_info, last_tick) in self.world_view.items()
        if obs.tick - last_tick > config.DATALINK_CONTACT_TTL_TICKS
    ]
    for tid in stale_ids:
        del self.world_view[tid]
    return changed
```

### 4. `share_new_contacts()` — broadcasts `DATALINK_CONTACT` for whatever `refresh_world_view()` flagged as new/changed

```python
async def share_new_contacts(self) -> None:
    changed = self.refresh_world_view()
    obs = self.latest_observation
    assert obs is not None
    for target in changed:
        await self.broker.send(Message(
            message_id=uuid.uuid4().hex, contract_id=None, tick=obs.tick,
            sender_id=self.agent_id, receiver_id=BROADCAST_ID, message_type="DATALINK_CONTACT",
            target_id=target.target_id, target_info=target,
        ))
```

Call this **every tick, for every agent**, regardless of `role` — a `MANAGER` or `CONTRACTOR` mid-negotiation can still see and should still share new intel. This is different from `scan_and_negotiate`, which stays `IDLE`-only.

### 5. `handle_message` gains a `DATALINK_CONTACT` branch

```python
async def handle_message(self, message: Message) -> None:
    self.received_messages.append(message)
    if message.message_type == "DATALINK_CONTACT":
        existing = self.world_view.get(message.target_id)
        if existing is None or existing[1] <= message.tick:
            self.world_view[message.target_id] = (message.target_info, message.tick)
        return
    # ... existing CFP/BID/ACCEPT/REJECT branches unchanged ...
```

The `existing[1] <= message.tick` guard prevents an old, delayed contact from clobbering a more recent direct sighting or a more recent contact from someone else — always keep the freshest-tick data.

### 6. Every place that reads `obs.known_targets` to make a **bid or movement/management** decision switches to `self.effective_known_targets()`

Update `scan_and_negotiate`, `try_become_manager`'s self-bid call inside `_broadcast_cfp_and_collect`, and `_maybe_bid`'s target lookup, to search `self.effective_known_targets()` instead of `self.latest_observation.known_targets`. This is what makes fog of war actually matter: `Jet_2`/`Jet_3` (per the v3 scenario, initially outside `SENSOR_RADIUS` of both targets) can now discover `Target_A`/`Target_B` purely from `Jet_1`'s `DATALINK_CONTACT` broadcasts, populate their own `world_view`, and bid — despite never directly sensing anything themselves at that point.

Example (`_maybe_bid`):
```python
async def _maybe_bid(self, cfp: Message) -> None:
    obs = self.latest_observation
    if self.role != "IDLE" or obs is None or obs.total_payload_remaining <= 0:
        return
    if cfp.hp_remaining is None or cfp.hp_remaining <= 0:
        return
    target = next((t for t in self.effective_known_targets() if t.target_id == cfp.target_id), None)  # CHANGED
    if target is None or target.hp_remaining <= 0:
        return
    result = compute_bid(obs, target, hp_needed=cfp.hp_remaining)
    if result is None:
        return
    utility, payload_offered, weapon_type = result
    ...
```

### 7. `decide_intent` — direct-sensing rule for `STRIKE` (the safety-critical change)

```python
def decide_intent(self) -> ActionIntent:
    obs = self.latest_observation
    assert obs is not None
    if self.committed_target_id is None:
        return ActionIntent(agent_id=self.agent_id, tick=obs.tick, action_type="IDLE")
    target = next(
        (t for t in self.effective_known_targets() if t.target_id == self.committed_target_id), None
    )
    if target is None or target.hp_remaining <= 0 or self.committed_payload <= 0:
        self.committed_target_id = None
        self.role = "IDLE"
        return ActionIntent(agent_id=self.agent_id, tick=obs.tick, action_type="IDLE")
    distance = math.dist(obs.current_position, target.position)
    directly_sensed = any(t.target_id == target.target_id for t in obs.known_targets)  # NEW: direct-only check
    if distance <= config.STRIKE_RANGE and directly_sensed:
        self.committed_payload -= 1
        return ActionIntent(
            agent_id=self.agent_id, tick=obs.tick, action_type="STRIKE",
            target_id=target.target_id, weapon_type=self.committed_weapon_type,
        )
    return ActionIntent(
        agent_id=self.agent_id, tick=obs.tick, action_type="MOVE", target_position=target.position
    )
```

Note the position/HP used for navigation and the `hp_remaining <= 0` reset check still come from `effective_known_targets()` (possibly memory) — only the final `STRIKE`-vs-`MOVE` branch gates on direct sensing. In practice, given `STRIKE_RANGE (1.0) << SENSOR_RADIUS (65.0)` in the fixed scenario, a jet within strike range of something is essentially always also directly sensing it — this guard exists for correctness/robustness (and for a future scenario where that inequality might not hold), not because it's expected to change behavior in the current numbers. Write a test that would catch a regression here anyway (Part F).

## Part E — `wraithnet/bridge.py`: new per-tick step

Insert a new step **between** "Broadcast state" and "Negotiate" in `_tick()`:

```python
async def _tick(self) -> None:
    # 1. Broadcast state
    for agent_id, agent in self.agents.items():
        agent.set_observation(self.model.build_observation(agent_id, self.broker))

    # 1b. Refresh world-view memory and share new contacts (Phase 7 — all agents, any role)
    await asyncio.gather(*(a.share_new_contacts() for a in self.agents.values()))
    await asyncio.sleep(0)  # let background run() loops drain freshly-broadcast DATALINK_CONTACTs
                            # before scan_and_negotiate reads effective_known_targets() below

    # 2. Negotiate (idle agents only), bounded by MAX_TICK_WAIT_SECONDS
    idle_agents = [a for a in self.agents.values() if a.role == "IDLE"]
    ... # unchanged from Phase 4
```

Everything else in `_tick()` (steps 2–5) is unchanged.

## Part F — Tests

### `tests/test_world.py` (updates)

* `build_observation` for a jet only returns targets within `config.SENSOR_RADIUS`: construct a model, temporarily reduce `config.SENSOR_RADIUS` (monkeypatch) to a small value, confirm a far target is excluded and a near one is included.
* Using the real (unmonkeypatched) v3 scenario numbers, confirm `Jet_1`'s `Observation.known_targets` includes both `Target_A` and `Target_B` at tick 0 (both within `65.0`), and `Jet_2`'s / `Jet_3`'s are both empty at tick 0 (both `≈72.8` away, outside `65.0`) — this is the load-bearing geometric property the whole feature depends on; assert it explicitly so a future scenario-number change can't silently break Fog of War's relevance without a test noticing.

### `tests/test_schemas.py` (additions)

* `Message(message_type="DATALINK_CONTACT", contract_id=None, target_info=<TargetInfo>, receiver_id=BROADCAST_ID, ...)` validates successfully; omitting `target_info` raises; setting a non-`None` `contract_id` raises; setting `receiver_id` to a specific agent (not broadcast) raises.
* Existing `CFP`/`BID`/`ACCEPT`/`REJECT` construction still requires non-`None` `contract_id` (regression check that the `Optional` relaxation didn't accidentally weaken validation for the other four types).

### `tests/test_fog_of_war.py` (new file)

Use `pytest-asyncio`, hand-built `Observation`s (no live `WraithNetModel` needed), following the same pattern as `test_cnp.py`.

1. **`refresh_world_view` populates and updates from direct sensing:** construct a `JetAgentRuntime`, call `set_observation` with an `Observation` containing one `TargetInfo`, call `refresh_world_view()` — assert it returns that target as "changed" (first sighting) and `self.world_view` now contains it. Call `set_observation`/`refresh_world_view()` again with the *same* target data — assert the second call's "changed" list is empty (no re-broadcast of unchanged info). Change `hp_remaining` and call again — assert it's reported as changed this time.
2. **TTL expiry:** populate `world_view` with a target at tick `0`, then call `refresh_world_view()` with an `Observation` at tick `> DATALINK_CONTACT_TTL_TICKS` whose `known_targets` no longer includes that target (i.e., it's out of direct sensor range again) — assert the stale entry is removed from `world_view` and no longer appears in `effective_known_targets()`.
3. **`share_new_contacts` broadcasts only new/changed targets:** two `JetAgentRuntime`s on one broker; give one an `Observation` containing a target; call `share_new_contacts()`; assert the other receives exactly one `DATALINK_CONTACT` with matching `target_info`. Call `share_new_contacts()` again with unchanged data — assert no second message arrives.
4. **A jet with zero direct sensor coverage can still bid, purely from a received contact:** replicate the v3 scenario's core property at small scale — `Manager` directly senses a target and calls `share_new_contacts()` then `scan_and_negotiate()`; a second agent, `Helper`, starts with an *empty* `Observation.known_targets` (nothing in direct sensor range) but receives the `DATALINK_CONTACT`. After the exchange settles, assert `Helper.world_view` contains the target and that `Helper` successfully participated (bid/won/became a contractor) despite never having the target in its own `Observation.known_targets` at any point.
5. **`decide_intent` refuses to `STRIKE` a memory-only target:** construct a `JetAgentRuntime`, commit it to a target (`_commit`), but give it an `Observation` where `current_position` is within `STRIKE_RANGE` of the target's position **and** `known_targets` is empty (simulating "I remember this target from a contact, but I don't currently, directly see it — e.g., it's just outside my `sensor_radius` right at the boundary, or a contrived test position") while `world_view` (populated via a prior `refresh_world_view()` call, or manually for this test) still has the target. Assert `decide_intent()` returns `MOVE`, not `STRIKE`, in this situation. This directly tests the safety rule in Part D.7 — do not skip it even though it doesn't occur naturally in the fixed v3 scenario's geometry.

### `tests/test_bridge.py` / `tests/test_scenario.py` (updates — read carefully, this phase changes expected behavior)

With `SENSOR_RADIUS=65.0` actually enforced, `Jet_2` and `Jet_3` start with zero direct visibility and must wait for `Jet_1`'s contacts to propagate before they can even attempt to bid. This changes tick-count and timing expectations from the V1-only tests:

* Keep the **outcome invariants** (`Target_A` gets ≥2 contributors, `Target_B` solo, both destroyed, scenario terminates) — these must still hold.
* **Relax or remove any hard-coded exact tick-count or narrow total-distance-traveled bounds** inherited from the V1-only version of these tests (e.g. V1's `total_distance_traveled` window of `140.0`–`165.0`) — Fog of War changes routing/timing dynamics (contact-propagation delay before distant jets can even start moving), so the old bounds are no longer guaranteed to hold and asserting them now would be testing stale, no-longer-true assumptions rather than real behavior. Replace with a much looser sanity bound for now (e.g. "less than some generous multiple of the V1 bound, just to catch a genuinely broken/looping run") and leave a comment that Phase 10 will re-derive tight, empirically-justified bounds once all four V2 features are integrated — do not hand-wave a new tight number in this phase without actually running the simulation to check it first.
* Add: assert that `Jet_2` and `Jet_3` each end up with at least one non-empty `world_view` entry sourced from a contact rather than direct sensing at some point during the run (inspect `received_messages` for a `DATALINK_CONTACT` with `sender_id="Jet_1"`, or equivalent) — i.e., prove Fog of War was actually exercised by the real scenario, not just bypassed by coincidence.

## Explicit non-goals for this phase

* No patrol *patterns* — "no known targets" behavior (move toward own start position) is deferred; it doesn't come up in the fixed v3 scenario since `Jet_1` always has direct visibility from tick 0 and shares promptly. If you choose to implement it anyway for robustness, keep it minimal (see `docs/04_..._v3_Phase2.md` §5) and do not let it interfere with the tests above.
* No SAM sites, no jet destruction, no jamming (Phases 8–9) — `Observation.comms_jammed` stays hard-coded `False`.
* No changes to `compute_bid`, tie-break rules, or `MAX_CFP_ROUNDS`/timeout constants — those are frozen from Phase 3/6.

## Acceptance criteria

1. `pytest` passes, including the new `test_fog_of_war.py` and all updated tests.
2. `build_observation` never returns a target farther than `config.SENSOR_RADIUS` from the requesting jet.
3. No agent ever emits a `STRIKE` `ActionIntent` for a target absent from that same tick's `Observation.known_targets` (direct sensing) — verified by the dedicated unit test in Part F.5, not just by absence of failure in a full run.
4. `python -m wraithnet.main` still runs the scenario to completion (both targets destroyed) — it may take more ticks than the V1-only baseline due to contact-propagation delay; that's expected, not a bug, as long as it terminates well within `max_ticks`.
