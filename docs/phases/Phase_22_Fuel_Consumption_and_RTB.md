# Phase 22 — Fuel Consumption & Bingo-Fuel RTB

> **How to use this document:** This is a complete, standalone prompt. Give an LLM coding agent this entire file as its task. Assume Phases 0–21 are complete and passing (jets have named airframes with fuel stats defined-but-unused; weapons have range/arc/slot-cost; `decide_intent` already sets `desired_heading`). Read `docs/10_Airframes_Weapons_and_Fuel_Design_Document.md` §1, §2.4, §2.5, §4, §5, and §8 in full before starting. This phase makes fuel a real, consumed resource: every jet burns fuel every tick, and an autonomous "bingo fuel" check forces a jet to abandon its current task and fly home before it runs dry — with the same contract hand-off Phase 8 already built for a jet destroyed in combat.

> **Automated GUI tests:** Follow `docs/14_Automated_GUI_Testing_Design_Document.md`. This phase is simulation-core, but any new viewer-facing state must be covered by automated `viewer3d` tests in the phase that visualizes it (Phase 24) — and if you touch `viewer3d/` here, add those tests in *this* phase. Do not leave 3D GUI coverage as a later follow-up or as manual-only. Pixel/screenshot tests remain out of scope; headless Layer-1 / Layer-2 tests are required for GUI changes.

## Context

Phase 20 added `fuel_capacity`/`fuel_burn_per_tick` to `JetFrameSpec` but nothing reads them. This phase wires fuel all the way through: burn it every tick, compute a "bingo fuel" threshold, autonomously trigger RTB, fly home under the same `MOVE` intent mechanism every other movement already uses, land (a new, successful outcome distinct from destruction), or run out of fuel en route (destroyed, reusing Phase 8's exact removal/notification plumbing).

## Part A — `wraithnet/config.py` additions

```python
RTB_FUEL_SAFETY_MARGIN: float = 1.2  # bingo fuel = (straight-line ticks home) * burn_per_tick * this
RTB_LANDING_RADIUS: float = 2.0       # must be within this distance of home_base to be considered "landed"
```

Document each with a one-line rationale comment, matching this codebase's existing convention (e.g. `config.SCENARIO`'s `hp_required=5` comment, `CONTRACT_CLAIM_TTL_TICKS`'s comment).

## Part B — `wraithnet/scenario_schema.py` / `wraithnet/world.py`: home base

`ScenarioConfig` (Phase 14) gains `home_base: tuple[float, float]`, validated in-bounds exactly like every other position field. `WraithNetModel` stores it (`self._home_base = self._scenario.home_base`) and exposes it wherever `build_observation` needs it (Part D). `_default_scenario()`/`scenarios/default.json` need an explicit `home_base` value added — pick a sensible corner of the existing `100x100` grid (e.g. `(2.0, 2.0)`, near where the existing jets already start) and document why.

## Part C — `wraithnet/world.py`: fuel state, burn, landing, and reaping

`JetAgent.__init__` gains `self.fuel: float = config.JET_FRAME_CATALOG[frame_type].fuel_capacity` and `self.is_rtb: bool = False` (world-model-level state — Part E explains why `is_rtb` lives here, not only in the async agent runtime).

`WraithNetModel.step()` gains, after the existing SAM-damage application and before the current destroyed-jet reaping loop:

```python
# Fuel burn -- flat per tick, every live jet, regardless of movement (design doc §5's explicit scope note).
for jet in self.jets.values():
    jet.fuel -= config.JET_FRAME_CATALOG[jet.frame_type].fuel_burn_per_tick

# Landing check -- must run BEFORE the destroyed/fuel-exhausted reap below, so a
# jet arriving home with exactly 0 fuel left is recorded as a safe landing, not
# a fuel-exhaustion loss. Iterate over a snapshot (list(...)) since this mutates self.jets.
for jet in list(self.jets.values()):
    if jet.is_rtb and math.dist(jet.pos, self._home_base) <= config.RTB_LANDING_RADIUS:
        self.jets_returned_to_base.add(jet.agent_id)
        self.space.remove_agent(jet)
        jet.remove()
        del self.jets[jet.agent_id]

# Reap: generalize the existing SAM-kill-only sweep to also catch fuel exhaustion.
destroyed_ids = [aid for aid, jet in self.jets.items() if jet.is_destroyed or jet.fuel <= 0]
for aid in destroyed_ids:
    jet = self.jets.pop(aid)
    self.space.remove_agent(jet)
    jet.remove()
```

`WraithNetModel.__init__` gains `self.jets_returned_to_base: set[str] = set()` (mirrors `self._initial_jet_ids`'s existing bookkeeping style). **Do not set `jet.hp = 0` to represent fuel exhaustion** — keep `fuel <= 0` as an independent condition in the reap predicate, so a viewer/HUD (Phase 24) can tell "shot down" apart from "ran dry" by inspecting `hp` vs `fuel` separately, rather than conflating the two into one field.

## Part D — `wraithnet/schemas.py`: `Observation` additions

```python
class Observation(BaseModel):
    ...
    frame_type: JetFrameType
    fuel: float = Field(ge=0.0)
    home_base: tuple[float, float]
```

`build_observation` populates all three directly from the jet's/model's existing state — no new computation needed here, this is a pure data-exposure change (matching every other field `Observation` already carries).

## Part E — `wraithnet/agents.py`: bingo-fuel decision & RTB behavior

**Why `role` transitions to a new value, `"RTB"`, rather than reusing `"IDLE"`:** every existing role-gated check in this file (`scan_and_negotiate`'s `if self.role != "IDLE": return`, and the equivalent guard wherever `handle_message`'s CFP-response path decides whether to bid) must stop treating an RTB'ing jet as available for new work, without those checks needing to change — a jet in `"RTB"` is simply never `"IDLE"`, so every existing `role == "IDLE"` gate already excludes it for free. **Audit every place in this file that branches on `self.role`** (both `== "IDLE"` and any `!=`-style negative checks) and add a test (Part G) proving an `"RTB"` jet is never selected to manage or bid on a new contract — do not assume the existing gates are sufficient without verifying each one explicitly.

New method on `JetAgentRuntime`:

```python
def begin_rtb(self) -> None:
    """Called once, the tick bingo fuel is first crossed. Idempotent-safe to
    call again (no-op) if already RTBing -- Part F's Bridge-side check must
    still only call this once per agent, but this method itself should not
    misbehave if it somehow were."""
    if self.role == "RTB":
        return
    if self.role == "MANAGER":
        self._abort_open_management()  # reuse verbatim -- releases any open claim cleanly
    self.role = "RTB"
    self.committed_target_id = None
    self.committed_weapon_type = None
```

New helper, used by the Bridge (Part F) to decide *when* to call `begin_rtb`:

```python
def bingo_fuel_reached(self) -> bool:
    obs = self.latest_observation
    if obs is None or self.role == "RTB":
        return False
    frame = config.JET_FRAME_CATALOG[obs.frame_type]
    distance_home = math.dist(obs.current_position, obs.home_base)
    ticks_home = distance_home / frame.max_speed
    bingo = ticks_home * frame.fuel_burn_per_tick * config.RTB_FUEL_SAFETY_MARGIN
    return obs.fuel <= bingo
```

`decide_intent` gains a first branch, checked before everything else in the method:

```python
def decide_intent(self) -> ActionIntent:
    obs = self.latest_observation
    assert obs is not None
    if self.role == "RTB":
        bearing = _bearing_to(obs.current_position, obs.home_base)
        return ActionIntent(
            agent_id=self.agent_id, tick=obs.tick, action_type="MOVE",
            target_position=obs.home_base, desired_heading=bearing,
        )
    ...  # existing logic, unchanged, for every other role
```

## Part F — `wraithnet/bridge.py`: triggering RTB and syncing `world.py`'s `is_rtb` flag

In `_tick()`, add a new step after the existing "1c. Self-heal any CONTRACTOR_PENDING bid timeout" step and before negotiation (step 2):

```python
# 1d. Bingo-fuel check -- must run before negotiation so a jet that just
# decided to RTB this tick doesn't also try to bid/manage this same tick.
began_rtb_this_tick: set[str] = set()
for agent_id, agent in self.agents.items():
    if agent.bingo_fuel_reached():
        agent.begin_rtb()
        self.model.jets[agent_id].is_rtb = True  # sync into world.py -- see Phase 22 Part C
        began_rtb_this_tick.add(agent_id)
```

And extend the existing post-`model.step()` notification call (today: `await self._notify_managers_of_losses(destroyed_this_tick)`) to also cover jets that began RTB this tick, exactly per the design doc §2.5's "reuse `_notify_managers_of_losses`, don't invent a second pathway" decision:

```python
await self._notify_managers_of_losses(destroyed_this_tick | began_rtb_this_tick)
```

**Do not otherwise modify `_notify_managers_of_losses`'s body** — it already correctly handles "was this ID one of my contractors? re-task if so" for any ID in the set it's given, regardless of *why* that ID is no longer viable; no change to its internals is needed for this to work for RTB as well as destruction.

**Landed and fuel-exhausted-en-route jets are still handled by the existing `destroyed_this_tick = alive_before - set(self.model.jets)` computation and cleanup** (`self.agents[agent_id].stop()`, `del self.agents[agent_id]`) **without any change** — both outcomes remove the jet from `model.jets`, and the Bridge's existing cleanup only cares "is this ID still in `model.jets`," not why it left. Verify this with a test (Part G) rather than assuming it — this is exactly the kind of subtle plumbing-reuse claim that needs empirical proof, not just a design-doc assertion.

## Part G — Tests

### `tests/test_world.py` additions

* A jet's `fuel` decreases by exactly its frame's `fuel_burn_per_tick` every tick, unconditionally (moving, striking, or idle) — the flat-burn scope decision (§5), verified directly.
* A jet with `is_rtb=True` that ends a tick within `RTB_LANDING_RADIUS` of `home_base` is removed from `model.jets` and its `agent_id` appears in `model.jets_returned_to_base`; one that ends the tick just outside that radius is not.
* A jet whose `fuel` reaches `<= 0` while `is_rtb=False` (or while `is_rtb=True` but still outside `RTB_LANDING_RADIUS`) is reaped exactly like a SAM-killed jet — removed from `model.jets`, absent from `model.jets_returned_to_base`.
* A landing jet with `fuel` at or below `0` in the *same* tick it arrives within `RTB_LANDING_RADIUS` is recorded as landed, not as fuel-exhausted (the ordering guarantee in Part C's comment) — construct this exact boundary case explicitly, don't just trust the code order.

### `tests/test_agents.py` additions

* `bingo_fuel_reached()` returns `True`/`False` correctly for hand-computed distance/fuel/frame combinations, including an exact-equality boundary case (`fuel == bingo` should trigger — verify which side of `<=` you actually want and test it deliberately, don't leave it to chance).
* `begin_rtb()` on a `MANAGER` agent correctly calls through to `_abort_open_management`'s existing release behavior (assert the same observable effects — claim released, any open CFP round abandoned — that Phase 3/8's existing tests already check for a tick-timeout abort) and results in `role == "RTB"`, `committed_target_id is None`.
* `begin_rtb()` is idempotent — calling it twice in a row has the same effect as calling it once, does not raise, does not double-abort.
* `decide_intent()` for an `"RTB"` agent always returns `action_type="MOVE"` toward `obs.home_base` with `desired_heading` set toward it, regardless of what `committed_target_id`/`committed_weapon_type` held before RTB began.
* **Audit tests (Part E)**: an `"RTB"` agent is never selected by another agent's `scan_and_negotiate`-style logic as a manager candidate, and never responds with a `BID` to an incoming `CFP` — construct both scenarios explicitly and assert no message/role change occurs.

### `tests/test_bridge.py` additions

* An integration test forcing one jet's fuel down near its bingo threshold (monkeypatch `fuel`/`JET_FRAME_CATALOG` values or just run enough ticks) that was previously a `CONTRACTOR` on some target: after RTB triggers, its manager receives the same re-tasking behavior (`notify_contractor_lost`-driven CFP re-issue) Phase 8's destruction tests already verify — mirror that test's structure with an RTB trigger instead of a SAM kill as the cause.
* A jet that successfully lands (drive it, via a short scripted run, to end up within `RTB_LANDING_RADIUS` of `home_base` while `is_rtb=True`) results in its `JetAgentRuntime` being stopped and removed from `bridge.agents`, exactly like a destroyed jet's runtime is today — verify via `bridge.agents` no longer containing that `agent_id` after the landing tick.
* Full-suite regression: the existing fixed PoC scenario (`python -m wraithnet.main`, no flags) still completes with the identical printed summary as before this phase — the default scenario's fuel capacities (Phase 20) must be generous enough that no default-scenario jet is forced into an RTB it wasn't already going to complete its objective before triggering. If your chosen `fuel_capacity`/`fuel_burn_per_tick` values from Phase 20 don't satisfy this, revisit them now — this phase is where that assumption finally gets tested for real.

## Explicit non-goals for this phase

* No evasion/SAM-damage changes — Phase 23.
* No metrics/termination rewrite beyond the raw `model.jets_returned_to_base` bookkeeping this phase adds — `metrics.py::summarize`'s formula fix is Phase 23's job specifically, not this one's (this phase only needs the *data* to exist; Phase 23 makes `summarize` correct with it).
* No editor/viewer support for placing/visualizing the home base — Phase 24.
* No fuel resupply, no relaunching a landed jet, no multiple home bases (design doc §5).

## Acceptance criteria

1. `pytest` passes in full, including all new/extended tests.
2. `python -m wraithnet.main` (no flags) produces the identical printed summary it did before this phase (per Part G's full-suite regression requirement).
3. A hand-crafted scenario with a jet whose fuel is deliberately set too low to complete its likely task (verify via `--scenario`) results in that jet autonomously abandoning its contract and flying toward `home_base`, landing successfully if its fuel was enough to get home, or being reaped if not — demonstrate both outcomes with two small scenario variants.
4. `grep -rn "jet.hp = 0" wraithnet/world.py` (or equivalent) confirms fuel exhaustion is never implemented by zeroing `hp` — it must remain an independent condition, per Part C.
