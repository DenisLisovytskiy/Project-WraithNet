# Phase 20 — Jet Frame Catalog & Independent Heading

> **How to use this document:** This is a complete, standalone prompt. Give an LLM coding agent this entire file as its task. Assume Phases 0–19 are complete and passing (full simulation + 2D/3D visualization + scenario editor). Read `docs/10_Airframes_Weapons_and_Fuel_Design_Document.md` §1, §2.1, §2.3, and §8 in full before starting. This phase introduces jet **airframes** (a fixed named catalog replacing the current global `JET_SPEED`/`JET_HP`) and **independent heading** (new, persisted, agent-controlled per-tick state, separate from travel direction). It does **not** touch weapons, fuel consumption, RTB, or evasion damage yet — those fields exist on the new catalog but are explicitly unused until Phases 21–23. **The fixed PoC scenario's outcome must not change** — every jet in `scenarios/default.json`/`config.SCENARIO` gets the frame whose `max_speed`/`hp` exactly reproduce today's `JET_SPEED=5.0`/`JET_HP=5`.

> **Automated GUI tests:** Follow `docs/14_Automated_GUI_Testing_Design_Document.md`. This phase is simulation-core, but any new viewer-facing state must be covered by automated `viewer3d` tests in the phase that visualizes it (Phase 24) — and if you touch `viewer3d/` here, add those tests in *this* phase. Do not leave 3D GUI coverage as a later follow-up or as manual-only. Pixel/screenshot tests remain out of scope; headless Layer-1 / Layer-2 tests are required for GUI changes.

## Context

Today every `JetAgent` is mechanically identical: one global speed, one global HP pool, no concept of facing/heading at all (the 3D viewer, Phase 16, only ever *inferred* a display heading from position deltas — that was a rendering-only fiction, and stays exactly as a fallback for jets that haven't started moving yet; this phase makes heading a first-class, independently-controlled simulation concept for actual jets). This phase is purely additive plumbing — no combat behavior changes yet.

## Part A — `wraithnet/config.py` changes

Add, near the existing weapon/target section:

```python
from dataclasses import dataclass

JetFrameType = Literal["INTERCEPTOR", "STRIKE_FIGHTER", "HEAVY_BOMBER"]

@dataclass(frozen=True)
class JetFrameSpec:
    max_speed: float
    turn_rate_deg_per_tick: float
    fuel_capacity: float          # NOT consumed until Phase 22 -- see note below
    fuel_burn_per_tick: float     # NOT consumed until Phase 22 -- see note below
    ordnance_slots: int           # NOT consumed until Phase 21 -- see note below
    hp: int
    evasion_bonus: int            # NOT consumed until Phase 23 -- see note below

JET_FRAME_CATALOG: dict[JetFrameType, JetFrameSpec] = {
    "INTERCEPTOR": JetFrameSpec(
        max_speed=7.0, turn_rate_deg_per_tick=45.0, fuel_capacity=..., fuel_burn_per_tick=...,
        ordnance_slots=4, hp=4, evasion_bonus=2,
    ),
    "STRIKE_FIGHTER": JetFrameSpec(
        max_speed=5.0, turn_rate_deg_per_tick=25.0, fuel_capacity=..., fuel_burn_per_tick=...,
        ordnance_slots=7, hp=5, evasion_bonus=1,
    ),
    "HEAVY_BOMBER": JetFrameSpec(
        max_speed=3.0, turn_rate_deg_per_tick=12.0, fuel_capacity=..., fuel_burn_per_tick=...,
        ordnance_slots=10, hp=7, evasion_bonus=0,
    ),
}
```

Fill in the `...` placeholders yourself — pick `fuel_capacity`/`fuel_burn_per_tick` values that give each frame a sensible number of ticks of range relative to `GRID_WIDTH`/`GRID_HEIGHT` (Phase 22 will define exactly how these get consumed; this phase only needs *plausible, documented* numbers, not perfectly balanced ones — leave a comment explaining your reasoning, matching this codebase's existing convention of justifying every scenario constant with a comment, e.g. `config.SCENARIO`'s `hp_required=5` comment).

**Critically**: `STRIKE_FIGHTER`'s `max_speed` must be exactly `5.0` (today's `JET_SPEED`) and `hp` exactly `5` (today's `JET_HP`) — this is the frame every existing scenario jet will use (Part C), and it must reproduce today's numbers exactly, not approximately.

Keep `config.JET_SPEED`/`config.JET_HP` as module-level constants **for now** if anything outside `world.py` still reads them directly after your changes — but the goal is that `world.py`'s own spawn/movement/damage logic reads from `JET_FRAME_CATALOG[jet.frame_type]` exclusively, not from the old globals, once this phase is done. If nothing else in the codebase reads `config.JET_SPEED`/`JET_HP` after your changes, remove them — check with `grep` before deciding.

## Part B — `wraithnet/world.py` changes

`JetAgent.__init__` gains a `frame_type` parameter and derived state:

```python
def __init__(
    self, model: WraithNetModel, agent_id: str, payload: dict[str, int],
    frame_type: "JetFrameType", initial_heading: float = 0.0,
) -> None:
    super().__init__(model)
    self.agent_id = agent_id
    self.payload = dict(payload)
    self.distance_traveled = 0.0
    self.frame_type = frame_type
    self.hp: int = config.JET_FRAME_CATALOG[frame_type].hp
    self.heading: float = initial_heading % 360.0
    self.desired_heading: float | None = None
```

* **Heading convention**: `0.0` degrees = facing `+y` (matching the world's existing "up" convention from `docs/07_..._Design_Document.md` §5's coordinate mapping), increasing **clockwise** when viewed from above (i.e. `90.0` = facing `+x`) — pick this convention explicitly and document it with a one-line comment on `JetAgent.heading`, since Phase 21 (arc checks) and Phase 24 (visualizing a heading indicator) both depend on getting this right and consistent.
* **`_apply_move` now reads `frame.max_speed`** instead of `config.JET_SPEED`:

  ```python
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
  ```

* **New `_apply_heading_rotation` method**, called once per jet per tick from `step()` (regardless of `action_type` — heading rotation is independent of movement/strike intents, per the design's "independent heading" decision):

  ```python
  def _apply_heading_rotation(self, jet: JetAgent) -> None:
      if jet.desired_heading is None:
          return
      turn_rate = config.JET_FRAME_CATALOG[jet.frame_type].turn_rate_deg_per_tick
      delta = (jet.desired_heading - jet.heading + 180.0) % 360.0 - 180.0  # shortest signed angular distance, in (-180, 180]
      if abs(delta) <= turn_rate:
          jet.heading = jet.desired_heading % 360.0
      else:
          jet.heading = (jet.heading + turn_rate * (1.0 if delta > 0 else -1.0)) % 360.0
  ```

  Call this for every live jet in `step()`, in the same loop that currently dispatches `MOVE`/`STRIKE`/`IDLE` — do this **before** `_apply_move`/`_apply_strike` are dispatched for that jet, or after; document which order you chose and why (neither order changes this phase's behavior since nothing yet reads `heading` for a gameplay decision — that starts in Phase 21 — but get the ordering decision made and documented now so Phase 21 doesn't have to relitigate it).

## Part C — `wraithnet/schemas.py` changes

`ActionIntent` gains an optional `desired_heading`:

```python
class ActionIntent(BaseModel):
    agent_id: str
    tick: int = Field(ge=0)
    action_type: Literal["MOVE", "STRIKE", "IDLE"]
    target_position: Optional[tuple[float, float]] = None
    target_id: Optional[str] = None
    weapon_type: Optional[WeaponType] = None
    desired_heading: Optional[float] = Field(default=None, ge=0.0, lt=360.0)
```

No new validator rule is required — `desired_heading` is optional and independent of `action_type` (per the design's decision that a jet can `MOVE` toward one point while separately commanding a heading change toward another). `Observation` also needs to expose the jet's current `heading` back to its own runtime (agents don't have direct access to `world.py`'s `JetAgent` — they only see what `Observation` gives them): add `current_heading: float = Field(ge=0.0, lt=360.0)` to `Observation`, populated by `build_observation`.

**No agent logic in `wraithnet/agents.py` sets `desired_heading` yet in this phase** — `decide_intent` continues to never populate it (defaults to `None`), so `_apply_heading_rotation` is a no-op for every jet all phase. This is intentional; Phase 21 is where an agent first has a reason to command a heading change (turning to bring a narrow-arc weapon to bear). Do not add speculative heading-setting logic to `agents.py` in this phase.

## Part D — `wraithnet/scenario_schema.py` changes (Phase 14)

```python
class JetSpec(BaseModel):
    agent_id: str
    position: tuple[float, float]
    payload: dict[WeaponType, int]
    frame_type: JetFrameType = "STRIKE_FIGHTER"
    initial_heading: float = Field(default=0.0, ge=0.0, lt=360.0)
```

Default `frame_type="STRIKE_FIGHTER"` (whose stats exactly reproduce today's global constants, per Part A) is what makes this phase backward-compatible: `_default_scenario()`'s existing jets need **zero** value changes, only the new field appearing with its default. `WraithNetModel._spawn_scenario`'s jet-spawning loop passes `j.frame_type`/`j.initial_heading` through to `JetAgent.__init__` (Part B).

## Part E — Tests

### `tests/test_world.py` additions

* `WraithNetModel()` (no scenario argument) — every jet's `hp` and effective max-move-per-tick still exactly match today's `JET_HP=5`/`JET_SPEED=5.0` (i.e. `STRIKE_FIGHTER`'s catalog values are correct and the default scenario's jets use that frame) — **this is the single most important test in this phase**, mirroring the exact "must not change default behavior" discipline Phase 14 required for its own scenario refactor.
* A jet constructed with `frame_type="INTERCEPTOR"` (or `"HEAVY_BOMBER"`) moves at that frame's `max_speed`, not `STRIKE_FIGHTER`'s — construct two jets with different frames at the same start position with the same distant destination, step both, and assert they end up different distances along the path.
* `_apply_heading_rotation`: a jet with `desired_heading` set to a value within `turn_rate_deg_per_tick` of its current heading snaps exactly to it in one tick; a jet with `desired_heading` further away only advances by `turn_rate_deg_per_tick` (in the shorter angular direction — test both a clockwise-shorter and counter-clockwise-shorter case, e.g. current heading `10°`→desired `350°` should rotate *backward* through `0°`, not forward through `180°`); a jet with `desired_heading=None` never changes `heading` regardless of how many ticks pass.
* `Observation.current_heading` reflects the jet's actual `heading` at observation-build time.

### `tests/test_scenario_schema.py` / `tests/test_scenario_io.py` additions (Phase 14 files)

* `JetSpec` without an explicit `frame_type` defaults to `"STRIKE_FIGHTER"`; round-trips through `save_scenario`/`load_scenario` with an explicit non-default `frame_type` correctly.
* An invalid `frame_type` (not in `JetFrameType`'s Literal) is rejected by Pydantic validation.

### Full-suite regression

* Run the entire existing `pytest` suite (all Phases 0–19 tests) and confirm zero failures/behavior changes — this phase must be a pure, additive refactor from every existing test's point of view.

## Explicit non-goals for this phase

* No fuel consumption, no RTB, no evasion damage reduction — `fuel_capacity`/`fuel_burn_per_tick`/`evasion_bonus` exist on `JetFrameSpec` but are read by nothing yet (Phases 22–23).
* No weapon range/firing-arc changes — `_apply_strike` is untouched in this phase (Phase 21).
* No agent logic ever sets `ActionIntent.desired_heading` yet — that starts in Phase 21.
* No editor/viewer changes — `viewer3d/editor.py`'s frame-type picker and any heading-indicator rendering are Phase 24's job, not this one.

## Acceptance criteria

1. `pytest` passes in full, with zero behavior change for the default scenario (verified by the Part E regression test).
2. `python -m wraithnet.main` (no flags) produces the identical printed summary it did before this phase.
3. A scenario JSON file with a jet explicitly set to `frame_type="HEAVY_BOMBER"` runs via `python -m wraithnet.main --scenario <path>` and that jet visibly moves slower / has more HP than a `STRIKE_FIGHTER` jet in the same run (spot-check via the printed per-jet distance-traveled summary, or a quick ad-hoc script).
4. `grep -rn "config.JET_SPEED\|config.JET_HP" wraithnet/world.py` returns no matches — `world.py`'s own logic is fully catalog-driven.
