# Design Document: 2D Top-Down Visualization (Pygame Replay Viewer)

> This document is the source of truth for a new, self-contained visualization feature layered on top of the existing WraithNet PoC (Phases 0–10, fully implemented — see `docs/6th-Gen_Warfare_Sim_Design_Document_v2.md` and `docs/04_6th-Gen_Warfare_Sim_Design_Document_v3_Phase2.md`). **Nothing in this document is implemented yet.** See `docs/phases/Phase_11..13_*.md` for the phase-by-phase build plan (each phase is a standalone LLM prompt, mirroring the style already used for Phases 0–10).

## 1. Goal

Give a human observer a 2D top-down view of a WraithNet run: jet positions, targets, SAM sites, the EW jammer, strikes landing, jets/targets being destroyed, and comms-jamming state — the four Phase 2 Expansion mechanics (Fog of War, Heterogeneous Weaponry, SAM Sites, Jamming) made visible, not just numeric. Per the user's request: **a separate Python script reads the Mesa state and draws it with Pygame.**

## 2. Architecture decision: recorded trace + standalone replay viewer (not a live/in-process viewer)

Three architectures were considered:

| Option | Description | Verdict |
|---|---|---|
| **(A) Replay from a recorded trace file** | The simulation (unchanged in its own logic) optionally writes a per-tick JSON Lines trace file while it runs. A completely separate script/package loads that file afterward and renders/animates it with Pygame. | **Chosen.** |
| (B) Live, same-process viewer | Pygame runs inside the same asyncio event loop as the Bridge, redrawing after every tick. | Rejected — see below. |
| (C) Live, cross-process viewer (IPC/socket) | The sim process streams state over a socket/pipe to a separate live viewer process. | Rejected — see below. |

**Why (A):**

* **Matches the literal request** ("a *separate* Python script reads the Mesa state") more directly than (B), which would entangle Pygame's own event loop with the Bridge's `asyncio` loop in the same process — nontrivial, since Pygame is not `asyncio`-aware and the Bridge's negotiation phase already has its own real-time-sleeping `asyncio.wait_for`/`asyncio.sleep` calls (see `docs/06_Code_Review_Full_Project.md`'s note that a real run already takes ~100s wall-clock). Interleaving a 60fps render loop with that would require either threading (explicitly banned by the project's own architecture — `docs/6th-Gen_Warfare_Sim_Design_Document_v2.md` §3: "single-threaded, single-event-loop") or a nontrivial `asyncio`-Pygame bridge.
* **Zero risk to the core simulation.** The existing `wraithnet` package has no GUI dependency today (see `docs/phases/Phase_5_Metrics_Termination_Validation.md`/`Phase_10_Integration_Metrics_Validation.md`'s explicit "no visualization/UI" non-goal). Recording a trace is a small, optional, additive hook — Pygame itself is never imported by `wraithnet/`, so `pytest`/`python -m wraithnet.main` keep working exactly as they do today even on a machine without Pygame installed (headless CI, servers, etc.).
* **Deterministic, replayable, scrubbable.** The whole project's design philosophy already leans on determinism (`docs/04_..._v3_Phase2.md` §2, `test_determinism_across_repeated_runs`). A recorded trace lets you pause, step backward, replay the exact same run repeatedly, or hand a trace file to someone without Mesa/asyncio installed at all — none of which a live viewer (B or C) gives you for free.
* **(C)'s added complexity (socket protocol, connection lifecycle, out-of-order/dropped-frame handling) buys nothing here** — this is a fixed, ~30-tick, single-machine PoC, not a distributed system. Rejected as over-engineering for the stated goal.

**Consequence:** this feature is *replay-only*. You run the simulation once (`python -m wraithnet.main`, optionally with a recorder attached), producing a trace file, then run the viewer separately against that file, as many times as you like. Real-time/live spectating is an explicit non-goal (§7).

## 3. Data contract: the trace file

New module `wraithnet/trace_schemas.py` (Pydantic, consistent with `wraithnet/schemas.py`'s existing rigor). The trace file is **JSON Lines** (`results/trace.jsonl` by default): one `TraceHeader` on line 1, followed by one `TraceTick` per collected tick (tick `0` through the final tick, inclusive — same range as `model.datacollector`'s `model_vars.csv`).

```python
from typing import Optional
from pydantic import BaseModel
from wraithnet.config import TargetType, WeaponType


class TraceHeader(BaseModel):
    """Written once, as the first line of the trace file. Everything here is
    static for the life of one run — entities/values that never move or
    change after __init__ (grid size, jammer position, target type/hp_required,
    initial loadouts). Per-tick *state* (position, hp_remaining, etc.) lives in
    TraceTick, never here."""
    grid_width: float
    grid_height: float
    sensor_radius: float
    jam_radius: float
    sam_engagement_range: float
    strike_range: float
    jet_ids: list[str]
    initial_payload: dict[str, dict[WeaponType, int]]      # agent_id -> loadout
    target_ids: list[str]
    target_types: dict[str, TargetType]                    # target_id -> type
    target_hp_required: dict[str, int]                     # target_id -> hp_required
    sam_site_ids: list[str]                                # subset of target_ids
    jammer_positions: dict[str, tuple[float, float]]       # jammer_id -> position


class JetFrame(BaseModel):
    agent_id: str
    position: tuple[float, float]
    hp: int
    payload: dict[WeaponType, int]
    destroyed: bool
    jammed: bool
    role: Optional[str]   # JetAgentRuntime.role ("IDLE"/"MANAGER"/"CONTRACTOR"/
                           # "CONTRACTOR_PENDING"), None once destroyed/removed


class TargetFrame(BaseModel):
    target_id: str
    hp_remaining: int
    claimed: bool
    # position/target_type/hp_required are NOT repeated here — look them up
    # from TraceHeader by target_id; targets never move in this project.


class StrikeEvent(BaseModel):
    agent_id: str
    target_id: str
    weapon_type: WeaponType


class TraceTick(BaseModel):
    """Written once per simulation tick, at the same point Phase 10's
    DataCollector.collect() already runs — i.e. once at t=0 (the baseline,
    before any step) and once after every model.step() call thereafter."""
    tick: int
    jets: list[JetFrame]
    targets: list[TargetFrame]
    strikes: list[StrikeEvent]     # strikes that landed *this* tick, not cumulative
    running: bool
    swarm_wiped_out: bool
```

**Design notes:**

* **SAM sites need no special-casing.** `SamSiteAgent` already has a corresponding `TargetState`/`TargetInfo` entry (`target_type="SAM_SITE"`) — it's just another `TargetFrame`, distinguished for drawing purposes via `TraceHeader.target_types[target_id] == "SAM_SITE"` (and `sam_site_ids` for a quick membership check). No duplicate representation.
* **Jammers get no `TraceTick` entry at all** — `EwJammerAgent` is static and never destroyed (an explicit non-goal since Phase 9: "no mobile, multiple, or destroyable jammers"), so its position belongs entirely in the header. If a future phase ever makes jammers mobile/destroyable, this is the schema that would need a `JammerFrame` added to `TraceTick` — not a concern for this feature.
* **`role` is included because it's cheap and high-value.** The recorder (Phase 11) has access to the `WraithNetBridge`, not just the `WraithNetModel` — reading `bridge.agents[agent_id].role` costs nothing extra and lets the viewer color-code jets by their current CNP role (a direct, at-a-glance visualization of the project's core mechanic: decentralized negotiation), without needing any new simulation-side bookkeeping.
* **No per-agent `world_view` (Fog-of-War memory) in the trace — explicit scope decision, not an oversight.** `Observation.known_targets`/`JetAgentRuntime.world_view` are per-*agent* belief state, not model ground truth. Including "what does Jet_3 currently believe it knows" alongside "what's actually true" would require the recorder to reach into every `JetAgentRuntime`'s private state, roughly doubling the schema's surface area, for a feature the user's request doesn't ask for (they asked for a top-down view of *the Mesa state*, i.e. ground truth). This is called out explicitly as a non-goal (§7) rather than silently dropped, since it's a legitimate, likely-requested future extension — the sensor-radius ring drawn per-jet (§5) at least visually implies *why* fog of war matters, without visualizing each agent's actual belief state.
* **`strikes` is per-tick, not cumulative** — deliberately mirrors the same "avoid the payload-delta footgun" lesson from `docs/06_Code_Review_Full_Project.md`'s §1/Phase 10's `TotalMissilesFired` note: computing this from a cumulative log (`model.strike_log`) by filtering for `entry["tick"] == model.tick` at record-time, not by diffing state across frames at *playback* time, keeps the viewer simple and avoids a whole category of off-by-one bugs.

## 4. Where the recorder hooks in (no change to existing behavior)

`wraithnet/recorder.py`'s `TraceRecorder` is attached **optionally**:

```python
class WraithNetBridge:
    def __init__(self, recorder: "TraceRecorder | None" = None) -> None:
        ...
        self.recorder = recorder
        if self.recorder is not None:
            self.recorder.record_header(self)
```

* Default is `None` — every existing test that does `WraithNetBridge()` with no arguments is completely unaffected; behavior, timing, and determinism are unchanged whether or not a recorder is attached (Phase 11's acceptance criteria require proving this explicitly, not just asserting it).
* `_tick()` gets exactly one new line, at the very end (after contract cleanup, mirroring where `DataCollector.collect()` already runs at the World layer): `if self.recorder is not None: self.recorder.record_tick(self)`.
* `wraithnet/main.py` is the only place that constructs a real `TraceRecorder` by default (writing to `results/trace.jsonl`, alongside the existing `results/model_vars.csv`/`agent_vars.csv`) — `wraithnet/recorder.py` and `wraithnet/trace_schemas.py` import only `pydantic`/stdlib, **never Pygame**, so this stays a zero-cost addition for anyone running tests or `main.py` without the visualization extras installed.

## 5. The viewer (new top-level `viewer/` package, isolated from `wraithnet/`)

```
viewer/
    __init__.py
    render.py     # coordinate mapping + drawing primitives (pure-ish, testable without a live display)
    app.py        # entrypoint: python -m viewer.app results/trace.jsonl
requirements-viz.txt   # -r requirements.txt \n pygame>=2.5,<3.0
```

Kept as a sibling top-level package (not inside `wraithnet/`) so the core simulation package's dependency footprint never grows — matches how `pandas` is already core (used by `metrics.export_results`) while Pygame is visualization-only and optional.

**Coordinate mapping** (world is `100.0 × 100.0`, non-toroidal, Cartesian-style with `+y` "up"; Pygame's surface origin is top-left with `+y` "down" — the mapping must flip `y` or the whole board renders upside down relative to the coordinates in `docs/04_..._v3_Phase2.md` §2):

```python
def world_to_screen(pos: tuple[float, float], grid_w: float, grid_h: float,
                     canvas_size: int, margin: int) -> tuple[int, int]:
    x, y = pos
    sx = margin + (x / grid_w) * (canvas_size - 2 * margin)
    sy = margin + (canvas_size - 2 * margin) - (y / grid_h) * (canvas_size - 2 * margin)
    return int(sx), int(sy)
```

**Window layout:** a square canvas (e.g. `900×900`) for the world map, plus a fixed-width side panel (e.g. `300px`) for the HUD/legend — total window `1200×900`. Exact pixel constants belong in `viewer/render.py`, not hard-coded inline in `app.py`.

**Drawing plan** (see Phase 12/13 for the split of which lands in which phase):

| Entity | Shape/rule |
|---|---|
| Jet | Filled circle, one fixed color per `agent_id` (stable across the whole run — pick from a small fixed palette keyed by index in `TraceHeader.jet_ids`); outline color reflects `role` (e.g. gold = `MANAGER`, green = `CONTRACTOR`, gray = `IDLE`, orange = `CONTRACTOR_PENDING`); a thin ring around a `jammed=True` jet. |
| Target (`HARDENED_BUNKER`) | Brown square. |
| Target (`MOBILE_RADAR`) | Blue diamond. |
| Target (`SAM_SITE`, alive) | Red octagon + a dashed ring at `sam_engagement_range` radius. |
| Destroyed target/SAM site | Rendered as a faded/greyed-out outline only (still positioned, so the viewer doesn't visually "teleport" remaining entities). |
| Jammer | Fixed purple icon + a dashed ring at `jam_radius` radius (always drawn — jammers are never destroyed). |
| Sensor radius | Faint dotted circle per alive jet at `sensor_radius`, toggle-able (key `S`) since it clutters a 3-jet, radius-`65` map fast. |
| HP | Small text label (`hp_remaining/hp_required`) above each target/SAM site; a thin bar above each jet for `hp/JET_HP`. |
| Destroyed jet | Removed from the board from the tick it's destroyed onward (matches `model.jets` no longer containing it) — optionally leave a small persistent wreck marker at its last position (nice-to-have, not required). |

## 6. Playback model (Phase 13)

* **Step mode** (Phase 12, required): arrow keys / space step exactly one recorded tick forward/backward; no timers involved. This is the minimum needed to inspect a run correctly and is fully deterministic to test.
* **Auto-play mode** (Phase 13, adds on top): `space` toggles play/pause; `+`/`-` adjust playback speed (ticks per real second); a Pygame `Clock`-driven loop advances the current tick index at that rate while playing.
* **Event highlights** (Phase 13): a `StrikeEvent` present in the current tick's `TraceTick.strikes` draws a brief attacker→target line/flash; any target/jet whose `destroyed`/`hp_remaining<=0` transitions from `False`→`True` (or a target's hp_remaining hits 0) between the previous and current tick gets a short destruction pulse; a jet's `jammed` flipping `False→True` this tick gets a brief highlight on its jam ring. All of this is derived by comparing tick *N* to tick *N-1* in the already-loaded trace — no new data is needed beyond what §3 already defines.
* **End-of-run summary overlay** (Phase 13): once the last tick is reached, overlay the same headline numbers `main.py` already prints (targets destroyed, jets lost/survived, missiles fired by weapon type, total distance) — computed directly from the trace's own data (final `TraceTick` + a sum over all `strikes` across every tick), not re-run against a live model.

## 7. Explicit non-goals

* **No live/real-time viewing** while the simulation is running (§2) — replay-only, by design, not a missing feature.
* **No Fog-of-War "agent belief" overlay** (what a specific jet currently *thinks* it knows, vs. ground truth) — noted as a deliberate scope cut in §3, not an oversight; a natural, self-contained future phase if wanted later (it would need the recorder to also snapshot each `JetAgentRuntime.world_view`).
* **No message-bus/negotiation visualization** (drawing individual CFP/BID/ACCEPT/REJECT messages as they fly between jets) — `role` per jet per tick (§3) gives a coarse, cheap proxy for "who's negotiating," which is judged sufficient; full message animation is a much larger feature (would need the trace to capture the Broker's per-tick message log, which is not currently exposed outside each agent's own `received_messages`) and is left for a future iteration if actually wanted.
* **No video/GIF export, no scrub bar/seek slider** — keyboard step/play controls only.
* **No 3D, no sound, no networked/multi-viewer sync.**
* **No changes to simulation logic, timing, or determinism** — this feature is purely additive/observational. Any phase whose acceptance criteria would require changing `wraithnet/world.py`, `bridge.py`, `agents.py`, or `broker.py`'s actual *behavior* (as opposed to reading their state) has overstepped this design.

## 8. Implementation phases for the LLM

* **Phase 11 — Simulation Trace Recorder** (`docs/phases/Phase_11_Simulation_Trace_Recorder.md`): `wraithnet/trace_schemas.py` + `wraithnet/recorder.py`, wired optionally into `WraithNetBridge`/`main.py`. Pure Python, no Pygame, fully unit-testable headlessly. Produces the data contract every later phase renders.
* **Phase 12 — Pygame Viewer: Static Rendering & Manual Stepping** (`docs/phases/Phase_12_Pygame_Viewer_Static_Rendering.md`): new `viewer/` package, loads a trace file, renders one tick at a time with correct coordinate mapping, shapes/colors/rings/HUD text, manual step controls only.
* **Phase 13 — Pygame Viewer: Playback, Speed Control & Event Highlights** (`docs/phases/Phase_13_Pygame_Viewer_Playback_and_Events.md`): auto-play loop, speed control, strike/destruction/jam-transition highlights, end-of-run summary overlay.

Build strictly in this order — Phase 12 depends on Phase 11's schema existing and a real trace file to load; Phase 13 depends on Phase 12's static rendering primitives already working correctly.
