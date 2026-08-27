# Phase 8 — Dynamic Threats: SAM Sites

> **How to use this document:** This is a complete, standalone prompt. Give an LLM coding agent this entire file as its task. Assume Phases 0–7 are complete and passing: heterogeneous weaponry (Phase 6) and sensor-limited Fog of War with `DATALINK_CONTACT` sharing (Phase 7) both work. Read `docs/04_6th-Gen_Warfare_Sim_Design_Document_v3_Phase2.md` §7 before starting. This phase adds jet health/destruction, one `SamSiteAgent`, and "Manager Fallback" re-tasking when an accepted contractor is destroyed. It modifies `config.py`, `world.py`, `agents.py`, and `bridge.py`. It does **not** touch `broker.py` or add any new `Message`/`ActionIntent` fields — SAM sites are physics-layer, Mesa-only entities (see design doc finding G5); the *response* to losing a contractor reuses the existing CNP machinery entirely (finding J4/G2), so no negotiation-protocol schema changes are needed.

## Context

Two closely related but distinct additions:
1. **Jets can now be destroyed.** They gain `hp`, and a SAM site deals damage to any jet within its engagement range every tick. A destroyed jet is fully removed from the simulation — space, active-agent set, negotiation, everything.
2. **A Manager whose accepted contractor is destroyed must notice and re-open bidding for the shortfall.** This requires the Manager to keep a small, persistent record of "who did I accept for this contract" that survives past the moment the contract resolves (the current code intentionally forgets `managed_target_id` as soon as a contract is fully covered — see Part C.1 for why a new, separate piece of state is needed instead of resurrecting that field).

**Scope simplification (deliberate, documented here rather than left implicit):** the source proposal says a Manager should react to a wingman that "dropped off comms **or** failed to destroy its target." This phase implements exactly one trigger: **destruction**. A jet that is merely damaged but alive continues its existing commitment unchanged — no early-warning/health-based re-tasking is implemented, since a partially-damaged-but-alive jet is still capable of completing its pledge and re-tasking it preemptively would just waste negotiation effort. If a future scenario needs it, extending the trigger condition is straightforward given the mechanism built here.

## Part A — `wraithnet/config.py` additions

```python
JET_HP: int = 5
SAM_ENGAGEMENT_RANGE: float = 15.0
SAM_DAMAGE_PER_TICK: int = 1   # deterministic, no RNG

# Extend the existing SCENARIO dict (do not replace targets/jets, add a new key):
SCENARIO["sam_sites"] = [
    {
        "target_id": "SAM_Site_1",
        "position": (40.0, 85.0),
        "hp_required": 1,
        "target_type": "SAM_SITE",
    },
]
```

Note `SAM_Site_1` has both a **CNP identity** (`target_id`, `hp_required`, `target_type` — it's a normal `TargetInfo`/`TargetState`, destroyable via the existing multi-winner CNP flow, requiring `JDAM` per the Phase 6 `WEAPON_SUITABILITY` table) and a **hazard identity** (`position`, engagement range/damage — see Part B). These are two facets of the same entity, not two separate objects.

## Part B — `wraithnet/world.py` changes

### 1. `JetAgent` gains health

```python
class JetAgent(mesa.Agent):
    def __init__(self, model, agent_id, payload) -> None:
        super().__init__(model)
        self.agent_id = agent_id
        self.payload = dict(payload)
        self.distance_traveled = 0.0
        self.hp: int = config.JET_HP   # NEW

    @property
    def is_destroyed(self) -> bool:    # NEW
        return self.hp <= 0

    @property
    def total_payload_remaining(self) -> int:
        return sum(self.payload.values())
```

### 2. New `SamSiteAgent` (plain Mesa agent, no `JetAgentRuntime`, never registered with the `MessageBroker` — design doc finding G5)

```python
class SamSiteAgent(mesa.Agent):
    def __init__(self, model, target_id: str, position: tuple[float, float]) -> None:
        super().__init__(model)
        self.target_id = target_id
        self.position = position
```

(The SAM site's `hp_required`/`hp_remaining`/`target_type` live in a `TargetState` entry in `self.targets`, exactly like `Target_A`/`Target_B` — `SamSiteAgent` itself only carries the physical/positional identity needed to compute engagement range. Do not duplicate HP tracking in two places.)

### 3. `WraithNetModel._spawn_scenario` — spawn SAM sites as both a `TargetState` and a `SamSiteAgent`

```python
def _spawn_scenario(self) -> None:
    for t in config.SCENARIO["targets"]:
        self.targets[t["target_id"]] = TargetState(**t)
    for s in config.SCENARIO.get("sam_sites", []):
        self.targets[s["target_id"]] = TargetState(
            target_id=s["target_id"], position=s["position"],
            hp_required=s["hp_required"], target_type=s["target_type"],
        )
        self.sam_sites[s["target_id"]] = SamSiteAgent(self, s["target_id"], s["position"])
    for j in config.SCENARIO["jets"]:
        agent = JetAgent(self, agent_id=j["agent_id"], payload=j["payload"])
        self.space.place_agent(agent, j["position"])
        self.jets[j["agent_id"]] = agent
```

`WraithNetModel.__init__` gains `self.sam_sites: dict[str, SamSiteAgent] = {}` (initialized before `_spawn_scenario()` is called, same pattern as `self.targets`/`self.jets`).

### 4. SAM damage application — new `_apply_sam_damage`, called at the **end** of `step()` (after movement/strikes, so damage reflects each jet's final position for the tick)

```python
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
        jet.remove()   # Mesa 3.x: fully deregisters from model.agents / AgentSet-based collection
```

`step()` becomes:
```python
def step(self, intents: dict[str, ActionIntent] | None = None) -> None:
    intents = intents or {}
    self.tick += 1
    for agent_id, intent in intents.items():
        jet = self.jets.get(agent_id)
        if jet is None:
            continue
        if intent.action_type == "MOVE":
            self._apply_move(jet, intent.target_position)
        elif intent.action_type == "STRIKE":
            self._apply_strike(jet, intent.target_id, intent.weapon_type)
    self._apply_sam_damage()   # NEW — after movement/strikes
    if all(t.is_destroyed for t in self.targets.values()):
        self.running = False        # success termination (now covers SAM_Site_1 too, since it's in self.targets)
    elif len(self.jets) == 0:
        self.running = False        # NEW — failure termination: whole swarm destroyed
        self.swarm_wiped_out = True # NEW flag, initialize to False in __init__
    self.datacollector.collect(self)
```

**Why `len(self.jets) == 0` and not `all(jet.is_destroyed for jet in self.jets.values())`:** destroyed jets are popped out of `self.jets` entirely in `_apply_sam_damage`, so `self.jets` only ever contains survivors — `all(...)` over an already-empty dict would vacuously be `True` even at tick 0 before anything happened, which is wrong. Track total-loss via emptiness of the survivors dict instead, and initialize `self.swarm_wiped_out: bool = False` in `__init__` so it's a well-defined, always-present flag either way.

### 5. `build_observation` — must not crash for an agent whose own jet no longer exists

The Bridge (Part D) is responsible for no longer calling `build_observation` for a destroyed agent's id at all — but as defense-in-depth, `build_observation` should not be the thing that silently misbehaves if it's ever called incorrectly:
```python
def build_observation(self, agent_id: str, broker: "MessageBroker") -> Observation:
    jet = self.jets[agent_id]   # intentionally still a plain KeyError if misused — the Bridge must not call this
    ...                          # for a destroyed agent; a loud KeyError beats a silent wrong-answer Observation.
```
No code change needed here beyond what Phase 7 already has — this note exists so the implementer doesn't "helpfully" add a silent fallback that would mask a real Bridge bug.

## Part C — `wraithnet/agents.py` changes: persistent contract-loss tracking + fallback re-tasking

### 1. Why new state is needed (read before implementing)

`_resolve_contract` currently clears `self.managed_target_id`/`self.open_contract_id` as soon as a contract resolves — **including when it resolves successfully** (fully covered). That's correct for the negotiation *state machine* (the Manager is done negotiating), but it means the Manager currently has **no persistent memory of who it accepted** once negotiation ends — exactly the information needed to notice a loss later. Do not try to resurrect `managed_target_id` for this purpose (that would incorrectly re-enter "actively negotiating" state). Instead, add a separate, independent, longer-lived record:

```python
# in __init__:
self.managed_contracts: dict[str, set[str]] = {}   # target_id -> accepted contractor agent_ids (never includes self)
```

### 2. Populate it in `_resolve_contract`, alongside the existing ACCEPT-sending loop (additive — do not remove any existing logic)

```python
for bid in bids:
    if bid.sender_id == self.agent_id:
        if bid.sender_id in winner_ids:
            self._commit(self.managed_target_id, bid.payload_offered, bid.weapon_type)
        continue
    decision = "ACCEPT" if bid.sender_id in winner_ids else "REJECT"
    if decision == "ACCEPT":
        self.managed_contracts.setdefault(self.managed_target_id, set()).add(bid.sender_id)  # NEW
    await self.broker.send(Message(...))   # unchanged
```

This dict entry now survives past `_resolve_contract`'s existing cleanup of `managed_target_id`/`open_contract_id` — it is only ever removed by `notify_contractor_lost` (below) once the target is confirmed destroyed, or left harmlessly stale otherwise (a leftover empty/resolved entry in a dict keyed by one of 3 fixed target ids in this scenario is not a real resource concern for a PoC).

### 3. `notify_contractor_lost` — the fallback re-tasking method the Bridge calls

```python
async def notify_contractor_lost(self, contractor_id: str, target_id: str) -> None:
    """Called by the Bridge when a jet this agent accepted as a contractor
    for target_id has just been destroyed. Re-opens bidding for whatever
    shortfall remains, reusing the existing re-negotiation machinery — no
    new protocol, see docs/03_..., finding J4."""
    contractors = self.managed_contracts.get(target_id)
    if contractors is None or contractor_id not in contractors:
        return  # not our contract, or already handled
    contractors.discard(contractor_id)

    target = next((t for t in self.effective_known_targets() if t.target_id == target_id), None)
    if target is None or target.hp_remaining <= 0:
        self.managed_contracts.pop(target_id, None)
        return  # target already destroyed (by surviving contributors) — nothing to re-task

    if self.role != "IDLE":
        return  # busy with something else right now; the stale broker claim + our own
                # continued awareness of managed_contracts[target_id] means we can revisit
                # this on a future tick if needed — out of scope to force-interrupt ourselves.

    self.role = "MANAGER"
    self.managed_target_id = target_id
    self.open_contract_id = uuid.uuid4().hex
    self._cfp_round = 0
    await self._broadcast_cfp_and_collect(target.hp_remaining)
```

Notes:
* This does **not** call `broker.try_open_contract` again — the Broker's `open_contracts[target_id]` entry was never closed for a fully-covered contract (existing Phase 3 behavior: `close_contract` is only called on give-up or by the Bridge on confirmed destruction), so this agent is still the recognized manager of record; re-broadcasting is safe and correct without re-electing.
* Deliberately conservative shortfall calculation: re-broadcasts for the target's **current live `hp_remaining`**, not "`hp_remaining` minus what surviving contractors still privately intend to fire." Cross-agent live-pledge tracking would add real complexity for a marginal negotiation-efficiency gain; over-recruiting a replacement contractor is harmless because `decide_intent` already refuses to `STRIKE` once `target.hp_remaining <= 0` regardless of how many jets are separately committed to it (existing Phase 3 guard) — so a mild oversupply of committed contractors wastes at most a little negotiation overhead, never ammunition or correctness.

### 4. `_commit` gains a `weapon_type` parameter (if not already done as part of Phase 6 — verify, don't duplicate)

Phase 6 already required `_commit`/`committed_weapon_type` for weapon typing; this phase's `_resolve_contract` snippet above assumes it exists (`self._commit(self.managed_target_id, bid.payload_offered, bid.weapon_type)`). If your Phase 6 implementation differs, reconcile now — do not introduce a second, inconsistent commit path.

## Part D — `wraithnet/bridge.py` changes: detect destruction, cancel runtimes, trigger fallback

```python
async def _tick(self) -> None:
    for agent_id, agent in self.agents.items():
        agent.set_observation(self.model.build_observation(agent_id, self.broker))

    await asyncio.gather(*(a.share_new_contacts() for a in self.agents.values()))
    await asyncio.sleep(0)

    idle_agents = [a for a in self.agents.values() if a.role == "IDLE"]
    if idle_agents:
        try:
            await asyncio.wait_for(
                asyncio.gather(*(a.scan_and_negotiate() for a in idle_agents)),
                timeout=config.MAX_TICK_WAIT_SECONDS,
            )
        except asyncio.TimeoutError:
            pass

    intents = {agent_id: agent.decide_intent() for agent_id, agent in self.agents.items()}

    alive_before = set(self.model.jets)
    self.model.step(intents)
    destroyed_this_tick = alive_before - set(self.model.jets)   # NEW

    if destroyed_this_tick:                                     # NEW block
        for agent_id in destroyed_this_tick:
            self.agents[agent_id].stop()
        await self._notify_managers_of_losses(destroyed_this_tick)
        for agent_id in destroyed_this_tick:
            del self.agents[agent_id]

    for target_id, target in self.model.targets.items():
        if target.is_destroyed:
            self.broker.close_contract(target_id)
    self.broker.expire_stale_contracts(self.model.tick)

async def _notify_managers_of_losses(self, destroyed_agent_ids: set[str]) -> None:   # NEW
    notifications = [
        manager.notify_contractor_lost(lost_id, target_id)
        for manager_id, manager in self.agents.items()
        if manager_id not in destroyed_agent_ids   # a manager destroyed this same tick is not "surviving" — see note below
        for target_id, contractor_ids in manager.managed_contracts.items()
        for lost_id in destroyed_agent_ids & contractor_ids
    ]
    if notifications:
        await asyncio.gather(*notifications)
```

Notes on ordering:
* `alive_before`/`destroyed_this_tick` is computed by diffing `self.model.jets`'s keys immediately before and after `model.step(...)` — this only works because `_apply_sam_damage` (Part B.4) actually pops destroyed jets out of that dict; if a future change makes destruction "soft" (kept in the dict with `is_destroyed=True`), this diff must be updated to use `is_destroyed` instead of dict membership.
* `agent.stop()` is called **before** `_notify_managers_of_losses` so a destroyed agent's own background `run()` task is cancelled promptly and it can never process another message.
* Destroyed agents are deleted from `self.agents` (the dict `_tick`'s own loops iterate) only **after** notification — this is deliberate (the comprehension above still needs to see every *surviving* manager during this same call), but it means a destroyed-this-tick agent is *still present* in `self.agents` at the moment `_notify_managers_of_losses` runs. **The `if manager_id not in destroyed_agent_ids` guard above is required, not optional**: without it, a manager that dies in the same tick as one of its own accepted contractors (a plausible simultaneous SAM kill, not just a hypothetical) is itself iterated as a candidate "surviving manager," flips `role` `IDLE` → `MANAGER`, and broadcasts a real `CFP` from a `sender_id` whose background `run()` loop was already cancelled by the `agent.stop()` call above. Any genuinely alive agent that answers that CFP with a `BID` will never receive an `ACCEPT`/`REJECT` (the dead manager can never call `handle_message` again) and is permanently stranded in `CONTRACTOR_PENDING` for the rest of the run — there is no Phase 8/pre-Phase-9 mechanism to recover it. This was caught empirically in `docs/05_Code_Review_Phases_6-8.md` §1; implement the guard from the start.
* With the guard in place, a manager+contractor pair that dies together leaves that target's broker claim (`open_contracts[target_id]`) marked but un-renewed — nobody calls `close_contract` for it from this path. It self-heals once `expire_stale_contracts` frees it after `CONTRACT_CLAIM_TTL_TICKS`, at which point a surviving `IDLE` agent's normal `scan_and_negotiate` picks it back up. This is an acceptable, bounded PoC behavior — call it out explicitly in a comment rather than leaving it as an implicit side effect, and add a test for it (see Part E).

## Part E — Tests

### `tests/test_world.py` (additions)

* `JetAgent.hp == config.JET_HP` at spawn; `is_destroyed` is `False` until `hp <= 0`.
* A `SamSiteAgent`/its `TargetState` are spawned from `config.SCENARIO["sam_sites"]`; `model.targets["SAM_Site_1"].target_type == "SAM_SITE"`.
* `_apply_sam_damage` (call directly, or via `model.step({})`): a jet placed within `SAM_ENGAGEMENT_RANGE` of a live SAM site loses exactly `SAM_DAMAGE_PER_TICK` HP per call/tick; a jet outside the range is unaffected; a jet near an **already-destroyed** SAM site (`hp_remaining=0`) takes no damage.
* A jet whose `hp` reaches `0` is removed from `model.jets` and from `model.space` (`model.space.get_agents_in... `/`model.space.agents` no longer includes it — check via whatever Mesa API you used to place it) after the tick that destroys it.
* `model.running` becomes `False` and `model.swarm_wiped_out` becomes `True` when every jet is destroyed (construct a minimal scenario or directly manipulate `jet.hp = 0` for all jets, then call `model.step({})`), while target-destruction success termination (`swarm_wiped_out` stays `False`) still works exactly as it did before this phase (regression check).

### `tests/test_agents.py` / a new `tests/test_dynamic_threats.py`

Use `pytest-asyncio`, hand-built `Observation`/`TargetInfo` objects — no live `WraithNetModel` needed for these (matches the isolation style of `test_cnp.py`).

1. **`managed_contracts` is populated on a successful multi-winner resolution and survives past resolution:** replicate (or reuse) the existing multi-winner test setup; after the contract resolves, assert the Manager's `self.managed_contracts[target_id]` contains the accepted contractor(s)' `agent_id`(s) (not including the Manager's own id), and that this is true **even though** `self.managed_target_id` has already reset to `None` per the existing Phase 3 behavior — this is the core state-lifetime distinction this phase introduces; test it explicitly.
2. **`notify_contractor_lost` re-opens bidding for the correct shortfall:** set up a Manager with a resolved contract whose `hp_required` was only partially covered by one now-"lost" contractor (i.e., contrive a target where a second bidder is available to complete it). Call `notify_contractor_lost(lost_id, target_id)` directly. Assert the Manager re-enters `role == "MANAGER"` and successfully collects/accepts a replacement bid from the still-available second bidder, ending with the target's shortfall covered (same style of invariant-based assertion as the existing multi-winner test — don't hardcode which specific agent must win if more than one candidate is symmetric).
3. **`notify_contractor_lost` is a no-op for an irrelevant/unknown contractor or target:** calling it with a `contractor_id` never actually accepted for that `target_id`, or a `target_id` this agent never managed, does not change `role`/`managed_target_id`/send any messages.
4. **`notify_contractor_lost` is a no-op if the target is already destroyed:** construct the scenario so `effective_known_targets()` shows the target at `hp_remaining=0`; assert no CFP is broadcast and `managed_contracts[target_id]` is removed.

### `tests/test_bridge.py` (additions)

* Construct a scenario (can directly manipulate `model.jets[...].hp` after building a `WraithNetBridge`, before/during a run, to force a deterministic destruction rather than relying on the SAM actually hitting something at an unpredictable tick) where a jet is destroyed mid-run while it was an accepted contractor for a target that isn't otherwise covered. Run the Bridge to completion and assert: (a) the target is still eventually destroyed (fallback re-tasking worked), (b) the destroyed jet's `agent_id` is absent from `bridge.agents` after the tick it died, (c) its `JetAgentRuntime` task was actually cancelled (inspect the corresponding entry that used to be in `bridge._tasks`), (d) `bridge.model.running` still correctly reaches `False` via the normal *success* path (not the swarm-wipeout path) since only one jet was destroyed, not all.
* A full swarm wipeout (force every jet's `hp` to `0` mid-run, or via a contrived scenario with an overwhelming SAM) causes `bridge.model.running` to become `False` with `bridge.model.swarm_wiped_out is True`, and `bridge.run()` returns promptly (does not spin for the rest of `max_ticks`).
* **Simultaneous manager+contractor death regression test:** construct a manager (`Jet_2`) with `managed_contracts["Target_A"] = {"Jet_3"}` (a normal post-resolution state), then force both `Jet_2` and `Jet_3` to `hp = 0` in the same tick (a plausible simultaneous SAM kill) and drive `_notify_managers_of_losses` (directly, or via `_tick()`). Assert `Jet_2`'s `JetAgentRuntime` (captured by reference before its `agent_id` is deleted from `bridge.agents`) never transitions its `role` away from whatever it held pre-destruction — a destroyed manager must never be treated as "surviving" and must never broadcast a `CFP`. Then have a third, alive, `IDLE` agent aware of `Target_A` run a few more real ticks and confirm it is never left permanently stuck in `CONTRACTOR_PENDING`.

## Explicit non-goals for this phase

* No "damaged but alive" early-warning re-tasking trigger (see the Context section's scope simplification) — only outright destruction triggers `notify_contractor_lost`.
* No `EVADE`/evasive-maneuver behavior, no SAM-avoidance pathfinding — jets fly the same straight-line `MOVE` logic as before and simply accept SAM risk en route (design doc finding J5 / §7 non-goals).
* No jamming yet (`Observation.comms_jammed` stays `False` until Phase 9).
* No new `Message`/`ActionIntent` schema fields — this phase reuses 100% of the existing CNP message types.

## Acceptance criteria

1. `pytest` passes, including all new/updated tests.
2. A jet's `hp` only ever decreases due to `_apply_sam_damage`; nothing else in this phase introduces damage.
3. A destroyed jet is fully absent from `model.jets`, `model.space`, `bridge.agents`, and receives no further `Observation`/`decide_intent` calls for the rest of the run — verified directly, not just inferred from the scenario happening to still terminate.
4. `notify_contractor_lost` never re-opens bidding for a target that's already destroyed, and never duplicates an ACCEPT/CFP cycle for a contractor loss it has already processed (calling it twice for the same already-removed `contractor_id` is a no-op the second time).
5. `python -m wraithnet.main` still runs the scenario to completion under the fixed v3 numbers (`docs/04_..._v3_Phase2.md` §2) — it is acceptable (and expected, per that document's own rationale) for a jet to take SAM damage or even be destroyed during a real run; the scenario must still resolve (all three CNP targets destroyed) or cleanly report a swarm-wipeout failure, never hang.
