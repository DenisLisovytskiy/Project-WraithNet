# Phase 13 — Pygame Viewer: Playback, Speed Control & Event Highlights

> **How to use this document:** This is a complete, standalone prompt. Give an LLM coding agent this entire file as its task. Assume Phase 12 is complete and passing: the `viewer/` package correctly loads a trace file and renders any single tick with correct coordinates, shapes, and colors, with manual step controls. Read `docs/07_Visualization_Design_Document.md` §6 before starting. This is the **final phase** of the 3-phase visualization feature (11–13). It only touches `viewer/app.py` and `viewer/render.py` (and adds one new pure-logic module) — Phase 12's drawing primitives for a single tick are correct and must not be rewritten, only reused and extended.

## Context

Phase 12 gave you a viewer that can display any one recorded tick, stepped manually. This phase turns that into an actual "replay" experience: press a key to have it play through the whole run automatically at an adjustable speed, and make the four Phase 2 Expansion mechanics — strikes landing, entities being destroyed, jamming starting/stopping — visually pop instead of blending into the background noise of "yet another frame."

## Part A — `viewer/playback.py` (new file): pure, display-free state logic

Keep the "what should be highlighted right now" decision entirely separate from Pygame drawing calls, so it's testable without a display — this mirrors the project's existing discipline of keeping negotiation logic (`wraithnet/agents.py`) independent of I/O (`wraithnet/broker.py`).

```python
"""Pure playback/highlight logic for the replay viewer -- no pygame import
in this module. Given the previous and current TraceTick, determines which
transient visual events should be shown this frame."""

from dataclasses import dataclass

from wraithnet.trace_schemas import TraceTick


@dataclass(frozen=True)
class TickHighlights:
    strikes: list  # this tick's StrikeEvent list, verbatim
    newly_destroyed_target_ids: list[str]
    newly_destroyed_jet_ids: list[str]   # jets present in prev tick, absent this tick
    newly_jammed_jet_ids: list[str]
    newly_unjammed_jet_ids: list[str]


def compute_highlights(previous: TraceTick | None, current: TraceTick) -> TickHighlights:
    prev_targets = {t.target_id: t for t in previous.targets} if previous else {}
    prev_jets = {j.agent_id: j for j in previous.jets} if previous else {}
    prev_jet_ids = set(prev_jets)
    curr_jet_ids = {j.agent_id for j in current.jets}

    newly_destroyed_target_ids = [
        t.target_id
        for t in current.targets
        if t.hp_remaining <= 0
        and (t.target_id not in prev_targets or prev_targets[t.target_id].hp_remaining > 0)
    ]
    newly_destroyed_jet_ids = sorted(prev_jet_ids - curr_jet_ids) if previous else []
    newly_jammed_jet_ids = [
        j.agent_id for j in current.jets
        if j.jammed and (j.agent_id not in prev_jets or not prev_jets[j.agent_id].jammed)
    ]
    newly_unjammed_jet_ids = [
        j.agent_id for j in current.jets
        if not j.jammed and j.agent_id in prev_jets and prev_jets[j.agent_id].jammed
    ]
    return TickHighlights(
        strikes=list(current.strikes),
        newly_destroyed_target_ids=newly_destroyed_target_ids,
        newly_destroyed_jet_ids=newly_destroyed_jet_ids,
        newly_jammed_jet_ids=newly_jammed_jet_ids,
        newly_unjammed_jet_ids=newly_unjammed_jet_ids,
    )
```

`previous=None` (tick 0, or whenever the user has jumped backward and there's no meaningful "previous" to diff against — see Part B) must produce **no** highlights except `strikes` (a tick's own strikes are absolute, not diffed) — do not synthesize false "newly destroyed/jammed" events from a `None` previous state.

## Part B — `viewer/app.py`: playback loop, speed control, and highlight rendering

### 1. New state and controls

```python
tick_index = 0
show_sensor_radius = False
playing = False
ticks_per_second = 4.0   # adjustable, see below
MIN_TPS, MAX_TPS = 0.5, 20.0
playback_accumulator_ms = 0
```

Extend the keyboard handling from Phase 12:

| Key | Action |
|---|---|
| `→` / `←` | Step one tick forward/backward (unchanged from Phase 12; pressing either while `playing` also pauses — manual stepping while auto-playing would fight the clock) |
| `Space` | Toggle `playing` |
| `+`/`=` | `ticks_per_second = min(MAX_TPS, ticks_per_second * 1.5)` |
| `-` | `ticks_per_second = max(MIN_TPS, ticks_per_second / 1.5)` |
| `S` | Toggle sensor-radius overlay (unchanged from Phase 12) |
| `R` | Jump back to tick 0, `playing = False` |
| `Esc` | Quit (unchanged) |

### 2. Auto-advance loop

Use the render loop's own frame delta (from `clock.tick(60)`'s return value, milliseconds since last frame — raise the loop's frame rate from Phase 12's `30` to `60` now that per-frame work includes a timed accumulator, for smoother speed control) to drive tick advancement independently of the display frame rate:

```python
dt_ms = clock.tick(60)
if playing:
    playback_accumulator_ms += dt_ms
    ms_per_tick = 1000.0 / ticks_per_second
    while playback_accumulator_ms >= ms_per_tick and tick_index < len(ticks) - 1:
        tick_index += 1
        playback_accumulator_ms -= ms_per_tick
    if tick_index >= len(ticks) - 1:
        playing = False   # auto-pause at the end; do not loop back to tick 0 automatically
```

### 3. Highlight computation and rendering, each frame

```python
previous_tick = ticks[tick_index - 1] if tick_index > 0 else None
highlights = compute_highlights(previous_tick, ticks[tick_index])
```

Add new drawing functions in `viewer/render.py` (reusing Phase 12's `world_to_screen`/color constants, not duplicating them):

* `draw_strike_highlights(surface, header, highlights)` — for each `StrikeEvent` in `highlights.strikes`, draw a short-lived bright line from the striking jet's current position to the struck target's position (both already resolvable via the current tick's/header's data the caller already has in scope). Since there's no sub-tick animation timer needed (a strike is drawn for exactly the one frame-equivalent tick it occurred on, then gone the next tick when `highlights.strikes` is empty for that tick) — do **not** add a fade-out timer that persists across multiple auto-play frames; the fixed `ticks_per_second` cadence already makes a single-tick flash visible enough at any reasonable speed, and a persistent-across-real-time fade would desync from `tick_index` if the user pauses mid-fade.
* `draw_destruction_pulses(surface, header, current_tick, highlights)` — for each id in `newly_destroyed_target_ids`/`newly_destroyed_jet_ids`, draw a bright ring (e.g. white, larger radius than the entity's normal marker) at that entity's last-known position (destroyed jets: use the *previous* tick's position, since they're absent from `current_tick`; destroyed targets: use `header.target_positions`, unaffected either way since targets don't move).
* `draw_jam_transition_flash(surface, header, current_tick, highlights)` — for each id in `newly_jammed_jet_ids`, draw a brief distinct-colored flash around that jet's jam indicator (from Phase 12); `newly_unjammed_jet_ids` can reuse the same visual with a different color, or be skipped if it doesn't add clarity — implementer's call, but don't skip `newly_jammed_jet_ids`, that's the more important direction to highlight.

### 4. End-of-run summary overlay

When `tick_index == len(ticks) - 1` (the final recorded tick), draw an overlay panel (can reuse the HUD panel space, or a centered modal-style box — implementer's call) showing, computed directly from the trace data already loaded in memory (do **not** re-import/re-run `wraithnet.metrics.summarize` — the viewer must not depend on a live `WraithNetModel`, only the trace file):

* `solved` (all `header.target_ids` have `hp_remaining <= 0` in the final tick) vs. `swarm_wiped_out` (final tick's own field).
* Targets destroyed / total.
* Jets destroyed / total (`len(header.jet_ids) - len(final_tick.jets)`).
* Total missiles fired, broken down by weapon type (sum every `StrikeEvent.weapon_type` across every tick's `strikes` list, not just the final tick's).
* Total distance traveled is **not** derivable from the trace as currently specified (positions are recorded but summing per-tick displacement would double-compute what `wraithnet/metrics.py` already tracks via `distance_traveled`, and floating-point movement clamped by `JET_SPEED` isn't guaranteed to reconstruct byte-identically from position deltas alone) — skip this stat in the overlay rather than approximating it. This is a deliberate, small scope cut; do not add a `distance_traveled` field to the trace schema to work around it in this phase — if it's wanted later, it belongs in a schema revision, not a viewer-side reconstruction.

## Part C — Tests

### `tests/test_viewer_playback.py` (new file, no Pygame needed — pure logic)

* `compute_highlights(None, tick0)`: `strikes` reflects `tick0.strikes` verbatim; all four "newly_*" lists are empty regardless of `tick0`'s actual jammed/hp state (no false positives from a missing previous frame).
* A hand-built `previous`/`current` pair where a target's `hp_remaining` drops from `1` to `0`: `newly_destroyed_target_ids` contains that target; a pair where it was already `0` in both: it does **not** reappear (only the *transition* is flagged, not "currently destroyed").
* A jet present in `previous.jets` but absent from `current.jets`: appears in `newly_destroyed_jet_ids`.
* A jet's `jammed` flips `False→True` between `previous`/`current`: appears in `newly_jammed_jet_ids` and not in `newly_unjammed_jet_ids`; the reverse transition appears only in the latter.
* A jet whose `jammed` stays `True` across both ticks (no transition): appears in neither list.

### `tests/test_viewer_smoke.py` (extends Phase 12's file — same `SDL_VIDEODRIVER=dummy` / `importorskip` pattern)

* Build a slightly richer synthetic trace than Phase 12's (≥5 ticks, including at least one tick with a non-empty `strikes` list, one target-destruction transition, and one jet jammed-state transition), run the full render pipeline (Phase 12's per-tick drawing **plus** this phase's three highlight-drawing functions plus the end-of-run overlay on the last tick) across every tick, asserting no exception is raised anywhere in the sequence.
* A short, deterministic simulated "auto-play" test: with a fixed sequence of synthetic `dt_ms` values fed into the accumulator logic from Part B.2 (extract that accumulator stepping into a small pure function in `viewer/playback.py` if it isn't already trivially testable inline — do this refactor if needed, don't leave untestable timing logic buried in `app.py`'s main loop), assert `tick_index` advances by the expected number of ticks for a given `ticks_per_second` and elapsed time, and that it correctly stops (auto-pauses) at `len(ticks) - 1` without going out of bounds or wrapping.

## Explicit non-goals for this phase

* No scrub bar/seek slider, no video/GIF export (design doc §7 — unchanged).
* No looping playback after reaching the end (explicit: auto-pause, not auto-restart) — a user who wants to watch again presses `R`.
* No `distance_traveled` in the end-of-run overlay (Part B.4) — a deliberate scope cut, not a bug to fix later in this same phase.
* No sub-tick interpolation/smoothing of entity movement between recorded ticks (e.g. easing a jet's on-screen position smoothly between tick N and N+1) — entities jump discretely from one recorded position to the next, exactly matching the underlying tick-based simulation; smooth interpolation would visually imply a precision the discrete-tick model doesn't have, and is out of scope.

## Acceptance criteria

1. `python -m viewer.app results/trace.jsonl`: pressing `Space` starts auto-play at the default speed; `+`/`-` visibly change playback speed; playback auto-pauses at the final tick without crashing or wrapping around; `R` resets to tick 0.
2. During playback of the real v3 scenario's trace, at least one strike-highlight, one destruction-pulse, and one jam-transition-flash are each visibly triggered at some point in the run (matches the underlying scenario's known properties: 7 strikes occur, `SAM_Site_1`/`Target_A`/`Target_B` are all eventually destroyed, and `Jet_3` passes through the jammer's radius — see `docs/06_Code_Review_Full_Project.md` for the exact observed numbers this trace should reproduce).
3. The end-of-run overlay's reported targets-destroyed/jets-destroyed/missiles-fired-by-weapon-type numbers exactly match `wraithnet.metrics.summarize(bridge.model)`'s numbers from the same run that produced the trace being viewed (`3` targets, `7` missiles, `{"JDAM": 6, "AIM9X": 1}` for the fixed scenario) — verify this manually once by comparing the viewer's overlay against a fresh `python -m wraithnet.main` console printout from the same trace-producing run.
4. `pytest` passes, including `test_viewer_playback.py` (no Pygame required) and the extended `test_viewer_smoke.py` (skips cleanly without Pygame installed, runs for real with it).
5. Phase 12's own acceptance criteria (manual stepping, sensor-radius toggle, coordinate-mapping tests) still all pass unmodified — this phase only adds to `viewer/`, it does not rewrite Phase 12's rendering primitives.
