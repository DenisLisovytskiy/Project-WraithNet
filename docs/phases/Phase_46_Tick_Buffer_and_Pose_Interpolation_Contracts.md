# Phase 46 — Tick Buffer & Pose Interpolation Contracts

> **LLM implementation prompt:** Assume Phases 0–33 (and whatever of 34–45 is present) are complete enough that `TraceTick` / `TraceHeader` and `viewer3d` exist. Read `docs/21_Smooth_Live_Motion_Design_Document.md` §§0–5, 9, 11–13 before coding. This phase defines and tests the **pure** presentation-time buffer and pose sampler. It does **not** wire Panda3D scene nodes, change the live drain loop, or modify `wraithnet/` combat timing.

## Goal

Create a strict, headless-testable contract so later phases do not pass ad-hoc `(prev_tick, next_tick, dt)` logic between stream client, scene, and cameras. Smooth motion must be expressible as: push ticks → advance presentation time → sample poses / pending events.

## Part A — module placement

Add a Panda3D-free module, preferred path:

```text
viewer3d/motion.py
```

Allowed imports: stdlib, Pydantic (if the repo already uses it in viewer helpers), `wraithnet.trace_schemas`, and pure helpers such as `wraithnet.geometry.shortest_signed_angle` / `bearing_to` if needed.

Disallowed: `panda3d`, `direct`, `pygame`, sockets, filesystem, wall-clock sleeps inside sampler math.

If importing `wraithnet.geometry` is awkward for packaging isolation tests, duplicate the minimal shortest-angle helper locally and note that in a one-line comment — do not invent a new shared dependency graph.

## Part B — public types

Implement at least:

```python
@dataclass(frozen=True)  # or Pydantic BaseModel; match nearby viewer style
class SampledPose:
    entity_id: str
    x: float
    y: float
    heading_deg: float  # [0, 360) display heading
    alpha: float         # blend factor used
    from_tick: int
    to_tick: int

@dataclass(frozen=True)
class PresentedEvent:
    """Discrete trace events released exactly once when presentation crosses a tick."""
    tick: int
    kind: Literal["STRIKE", "JET_OUTCOME", "COUNTERMEASURE", "OTHER"]
    payload: object  # prefer typed refs to TraceTick sub-objects; avoid loose dicts at boundaries

class TickBuffer:
    def __init__(
        self,
        *,
        interp_delay_s: float = 0.10,
        max_extrapolation_alpha: float = 1.10,
        max_backlog_ticks: int = 128,
    ) -> None: ...

    def reset(self) -> None: ...
    def push(self, tick: TraceTick, t_accept: float) -> None: ...
    def set_presentation_time(self, t_p: float) -> None: ...
    def advance(self, dt: float) -> None: ...  # t_p += dt

    def sample_jet_poses(self) -> dict[str, SampledPose]: ...
    def current_tick_for_hud(self) -> TraceTick | None: ...
    def pop_newly_presented_events(self) -> list[PresentedEvent]: ...

    # Introspection for tests / HUD later
    @property
    def backlog_len(self) -> int: ...
    @property
    def is_extrapolating(self) -> bool: ...
```

Rules:

* `push` stores ticks in order; reject or ignore out-of-order older ticks with a clear policy (document: drop stale).
* `interp_delay_s` means presentation aims at `now - delay` so a `next` tick usually exists.
* `sample_jet_poses` interpolates XY and heading between the bracketing ticks for that presentation time.
* Heading uses shortest-arc interpolation; result normalized to `[0, 360)`.
* Entities only in `from` tick: hold or omit per documented spawn rule (recommend: include at α=0 from first tick; do not lerp from origin).
* Entities missing in `to` tick: do not invent a destination; hold last pose until HUD tick commits removal via events, or omit once the end tick is current — pick one and test it.
* `pop_newly_presented_events` returns strikes / jet_outcomes / similar **once** when presentation crosses into that tick; never re-fire on later samples.

## Part C — interpolation helpers

Expose pure functions (unit-test directly):

```python
def lerp(a: float, b: float, alpha: float) -> float: ...
def lerp_heading_deg(a: float, b: float, alpha: float) -> float: ...
def clamp_alpha(alpha: float, *, max_extrapolation: float) -> float: ...
```

Document that α may slightly exceed 1.0 only when extrapolating, then clamp to `max_extrapolation`.

## Part D — tests

Create `tests/test_viewer3d_motion.py` (no Panda3D import).

Cover:

1. Midpoint lerp of XY between two ticks.
2. Heading wrap 350° → 10° passes through 0°, not the long way.
3. α=0 returns exactly from-pose; α=1 returns exactly to-pose (within float tolerance).
4. Missing next tick clamps and sets extrapolating only per policy.
5. Pushing a burst of ticks then sampling at an early `t_p` still returns an early segment (buffer retains history until trimmed).
6. `pop_newly_presented_events` fires once per crossed tick and is empty on the next call without time advance.
7. Jet that disappears on tick N does not NaN or jump to (0,0).
8. Reset clears backlog and presentation state.
9. Importing the module does not require Panda3D (`pytest` without `panda3d` installed must still collect/pass this file, or skip only if the package layout forces viewer3d `__init__` to import Panda — prefer lazy imports so this file stays pure).

## Non-goals

* No `viewer3d/app.py` drain-loop changes (Phase 47).
* No catch-up snap policy beyond basic max backlog trim if needed for memory (full catch-up is Phase 48).
* No combat/sim changes.
* No README packaging pass yet.

## Acceptance criteria

1. New motion tests pass headlessly without a display.
2. `grep` on the new module finds no `panda3d` / `direct` / `pygame` imports.
3. Public sampler APIs do not take raw untyped pose dictionaries at the boundary.
4. Existing full `pytest` suite still passes.
