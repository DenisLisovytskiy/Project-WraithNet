# Design Document: Smooth Live Motion (Dual-Clock Presentation)

> This document is the source of truth for making live (and replay) viewing feel continuous at roughly **30–60 FPS**, without turning WraithNet’s discrete Mesa/CNP combat model into continuous physics. **Nothing in this document is implemented yet.** See Phases 46–49 for LLM-ready implementation prompts.
>
> Numbering note: Phases 34–45 remain the terrain / naval / combined-arms track. This expansion is an independent presentation track that starts at **Phase 46** so agents can implement it without waiting on terrain.

## 0. Goal

A user watching a live run should see jets (and other moving entities) glide smoothly across the map at a steady frame rate, instead of teleporting whenever a simulation tick arrives.

Success looks like:

1. Open `python -m launcher`, generate or load a scenario, Launch.
2. The 3D view holds a stable ~30+ FPS render loop.
3. Jet XY motion and heading look continuous between sim ticks.
4. Combat outcomes (strikes, SAM damage, CNP role changes, fuel, RTB) still occur on authoritative tick boundaries.
5. Replay of a saved `trace.jsonl` can use the same presentation path so live and replay feel consistent.
6. Determinism and headless summarize outcomes are unchanged.

## 1. Why it feels choppy today

WraithNet already separates **simulation** from **rendering**, but live presentation still couples them too tightly:

| Layer | What it does today | Why that hurts smoothness |
|---|---|---|
| `WraithNetBridge` / Mesa | Advances one discrete combat tick when CNP/timeouts allow | Tick wall-clock spacing is irregular (negotiation waits, asyncio, machine load). |
| Live stream | Ships each `TraceTick` as soon as it is produced | Bursts of ticks can queue up. |
| `viewer3d` drain task | Often applies **every queued tick immediately** in one frame via `update_tick` | XY positions snap to the latest tick; intermediate ticks may never be seen. |
| Flight easing | Already eases **altitude/bank** every frame | Altitude can look smooth while ground-track motion still jumps. |

So the problem is mostly **presentation**, not “the sim is too slow.” The render loop already has a per-frame `dt`; XY pose update does not yet use a dual-clock interpolator.

## 2. Existing capabilities this expansion builds on

* Frozen `TraceHeader` / `TraceTick` schemas and JSONL / TCP streaming (Phases 11, 15).
* Panda3D viewer with a continuous `task_mgr` drain/render task (Phase 16+).
* Cosmetic flight dynamics and weapon effects already framed as viewer-side (Phases 25–26).
* Chase/orbit cameras that already update every frame from a selected jet pose.
* Headless GUI testing patterns (Phase 27) for viewer state without pixel assertions.

## 3. Core decisions

| Concern | Decision |
|---|---|
| Where smoothness lives | **Viewer / presentation layer first.** Prefer zero gameplay change in `wraithnet/world.py` / agents / CNP. |
| Sim model | Remains discrete ticks. No continuous physics, no sub-tick combat resolution. |
| Render clock | Independent ~30–60 Hz loop (Panda3D already provides this). |
| Motion method | **Interpolate** (and optionally lightly extrapolate) between authoritative tick snapshots. |
| Burst handling | Never apply an unbounded tick burst as N teleports in one frame. Buffer ticks and release them to the interpolator on a presentation schedule. |
| Authority | Tick state remains authoritative. Interpolation is visual only; HUD combat numbers come from the latest fully-presented tick. |
| Delay budget | Accept a small, documented **presentation delay** (for example 50–150 ms, or 1 tick) so the viewer almost always has a “next” snapshot to lerp toward. |
| Determinism | Sim outcomes unchanged. Presentation may use wall-clock `dt` and is allowed to be non-bit-identical across machines. |
| Late joiner / catch-up | Catch-up may fast-forward the buffer, then resume interpolation; do not leave the camera stuck on tick 0 while hundreds of ticks drain. |
| Replay | Use the same interpolator. Replay’s “playback speed” scales how fast presentation time advances through ticks. |
| 2D Pygame viewer | Optional follow-on; Phase docs may leave it untouched unless explicitly in scope. Default: **3D live + 3D/trace replay path first**. |
| Terrain / missiles | If later phases stream moving missiles/ships/tanks, they must plug into the same pose buffer. Until then, interpolate jets (and any already-streamed moving missile frames if present). |

## 4. Architecture: two clocks

```
  Sim clock (irregular)                    Render clock (~30–60 Hz)
  -----------------------                  -------------------------
  Bridge tick N                            Panda3D frame
       │                                        │
       ▼                                        ▼
  TraceTick snapshot ──stream/queue──▶  TickBuffer (ring)
                                         prev_tick, next_tick, alpha
                                                │
                                                ▼
                                         PoseSampler.sample(t_present)
                                                │
                                                ▼
                                         scene / cameras / effects easing
```

### 4.1 Definitions

* **Authoritative tick** `T_k`: one `TraceTick` from the sim/recorder/stream.
* **Presentation time** `t_p`: monotonic viewer time used only for display.
* **Tick timestamp** `t_k`: when the viewer *accepted* tick `k` into the buffer (wall clock), or a synthetic schedule time under paced release.
* **Alpha** `α ∈ [0,1]`: blend factor from pose at `T_k` toward pose at `T_{k+1}`.

### 4.2 What is interpolated vs snapped

| Quantity | Presentation rule |
|---|---|
| Jet XY position | Interpolate |
| Jet heading | Interpolate via shortest-angle lerp |
| Cosmetic altitude / bank | Keep existing easing; drive targets from interpolated or tick-desired values consistently (document one rule) |
| Target / SAM / jammer positions | Snap (static today). If a future phase moves them, use the same buffer |
| HP, payload, fuel, roles, jam flags | Snap on tick boundary when that tick becomes “current” for HUD |
| Strike / destruction / outcome events | Fire when the tick that contains them is presented (not when first drained into the buffer) |
| Moving missile frames (if present) | Interpolate like jets |

## 5. Tick buffer & pose sampling

Create a small pure module (suggested: `viewer3d/motion.py` or `viewer3d/tick_buffer.py`) with **no Panda3D imports**, testable headlessly:

```python
class TickBuffer:
    """Holds recent TraceTicks with presentation timestamps."""

    def push(self, tick: TraceTick, t_accept: float) -> None: ...
    def set_presentation_time(self, t_p: float) -> None: ...
    def sample_poses(self) -> dict[str, SampledPose]: ...
    def current_tick_for_hud(self) -> TraceTick | None: ...
    def pending_events(self) -> list[PresentedEvent]: ...  # strikes etc. newly crossed

class SampledPose(BaseModel):
    entity_id: str
    x: float
    y: float
    heading_deg: float
    alpha: float
    from_tick: int
    to_tick: int
```

### 5.1 Interpolation math

For positions:

* `x = x0 + α * (x1 - x0)` (same for `y`)

For heading:

* use existing `shortest_signed_angle` / equivalent from `wraithnet.geometry` (viewer may import that pure helper, or duplicate a tiny local copy to avoid unwanted coupling — prefer reusing the pure geometry helper).

Alpha:

* If paced between accepted timestamps: `α = (t_p - t_k) / max(ε, t_{k+1} - t_k)`
* Clamp to `[0, 1]` while waiting for the next tick
* Optional short extrapolation (`α > 1` capped, e.g. 1.05–1.15) only when the next tick is late; document that extrapolation is visual-only and must not invent strikes

### 5.2 Entity lifetime edges

* Jet appears: fade/spawn at first presented tick; do not lerp from `(0,0)`.
* Jet destroyed / RTB removed: finish any in-flight presentation of that tick’s events, then remove glyph; do not lerp toward a missing next pose.
* Missing ID in `T_{k+1}`: hold last pose or remove according to outcome events on `T_{k+1}` / `T_k`.

## 6. Drain policy (live viewer)

Replace “while queue not empty: apply tick immediately” as the *pose* authority with:

1. **Ingest**: drain socket/queue into `TickBuffer` as fast as possible (catch-up OK).
2. **Present**: each frame, advance `t_p` by frame `dt` (scaled by optional playback rate).
3. **Sample**: compute poses from buffer; update scene nodes from sampled poses.
4. **Commit HUD/events**: when `t_p` crosses a tick boundary, apply that tick’s discrete events exactly once.

### 6.1 Burst absorption

If 20 ticks arrive in one frame during catch-up:

* Do **not** call `update_tick` 20 times for rendering.
* Either:
  * **Fast-forward presentation time** to near live with a capped catch-up rate, or
  * Snap buffer to “latest − 1” / “latest” and restart interpolation after join/reconnect.

Choose one primary policy and test it. Recommended default:

* **Live**: keep a 1-tick (or ~100 ms) delay; if backlog exceeds `N` ticks (e.g. 30), snap to latest−1 and clear older entries.
* **Replay**: no network bursts; advance `t_p` from playback speed only.

### 6.2 Relationship to existing altitude easing

Today altitude easing runs every frame and is already correct in spirit. After this expansion:

* XY/heading come from `TickBuffer.sample_poses`.
* Altitude/bank may continue to ease toward desires derived from the **HUD current tick** or from sampled XY proximity — pick one and keep it deterministic given the presented tick sequence.

## 7. Optional sim-side pacing (secondary)

Primary smoothness must work **even if the sim still emits ticks as fast as CNP allows**.

Optional later knob (only if measurement shows ticks are *too sparse* rather than too bursty):

* A viewer or launcher setting for **minimum presentable tick spacing** is still viewer-side (hold pose longer).
* A sim-side `await asyncio.sleep(target_tick_period)` after each tick is **optional** and must be off by default for headless CI speed. If added, gate it behind an explicit live-view / “cinematic” flag so `pytest` and headless runs stay fast.

Do **not** make combat correctness depend on wall-clock pacing.

## 8. Cameras, effects, HUD

* Chase/orbit cameras follow **sampled** pose each frame (already dt-based).
* Weapon cosmetic effects should spawn when the strike’s tick is **presented**, matching the jet’s presented position that frame.
* HUD tick counter / role / fuel read from `current_tick_for_hud()`.
* Show a subtle indicator if presentation is catching up or extrapolating (optional advanced HUD; not required for MVP).

## 9. Testing strategy

### Pure Layer 1 (no Panda3D)

* Alpha edges: 0, 1, mid, clamp, missing next tick.
* Heading wrap across 359° → 1°.
* Spawn/despawn identity edges.
* Burst push then sample does not require applying every intermediate tick to poses.
* Event presentation fires once when boundary crossed.
* Global `random` / catalogs untouched.

### Headless Layer 2 (Panda3D window-type none)

* Live app uses buffer; scene node XY changes across frames between two ticks without a new network event.
* Draining many queued ticks in one frame does not teleport more than the catch-up policy allows.
* Existing editor/preview/launch paths still work.
* No pixel assertions.

### Manual acceptance

* Launch a Standard generated scenario; motion looks continuous at a glance.
* Pause/reconnect/catch-up does not freeze the camera indefinitely.
* Headless summarize of the same scenario remains unchanged.

## 10. Packaging and isolation

* New motion modules live under `viewer3d/` (or a tiny shared pure package if replay needs it without Panda). Prefer `viewer3d/` pure modules with lazy Panda usage elsewhere, matching `flight_dynamics.py` / `highlights.py`.
* `wraithnet/` gains **no** rendering imports.
* Frozen build must include new modules through existing import graphs.
* No new third-party dependency.

## 11. Explicit non-goals

* Continuous CNP, continuous SAM DPS, or sub-tick strike resolution.
* Making altitude gameplay-affecting.
* Guaranteeing identical pixels across GPUs.
* Replacing Mesa with a general physics engine.
* Networked multiplayer interpolation against cheating (single-machine local viewer only).
* Forcing every headless test to run in wall-clock real time.

## 12. Implementation phases

* **Phase 46 — Tick Buffer & Pose Interpolation Contracts**: pure buffer/sampler types, math, event-boundary rules, headless tests. No Panda3D scene wiring.
* **Phase 47 — Live 3D Dual-Clock Integration**: wire buffer into `viewer3d` drain/render loop; sampled poses drive jets; HUD/events on present boundaries; keep editor path intact.
* **Phase 48 — Burst Absorption, Catch-up, and Presentation Delay**: backlog policy, join/reconnect fast-forward, configurable delay; stress tests with synthetic tick floods.
* **Phase 49 — Replay Parity, Camera/Effects Alignment, and Acceptance**: reuse sampler for trace replay / brief→live polish; camera & effect timing aligned to presented ticks; README note; packaging smoke.

## 13. Suggested constants (calibration starting points)

Document these in code; tune in Phase 48/49 without changing combat:

| Name | Starting value | Role |
|---|---|---|
| `TARGET_PRESENTATION_FPS` | 60 | Render loop expectation (Panda may vary) |
| `MIN_ACCEPTABLE_VIEW_FPS` | 30 | Manual/soft metric, not a hard CI fail |
| `DEFAULT_INTERP_DELAY_S` | 0.10 | Hold back one short beat so `next` exists |
| `MAX_EXTRAPOLATION_ALPHA` | 1.10 | Cap when next tick is late |
| `CATCHUP_BACKLOG_TICKS` | 30 | Above this, snap toward live |
| `CATCHUP_MAX_RATE` | 8× | Max presentation-time speedup while catching up |

## 14. Relationship to other tracks

* **Terrain / naval / tanks (34–45)**: moving platforms should eventually publish poses into the same buffer. This track should keep the sampler entity-agnostic (`entity_id` + pose), not jet-hardcoded forever.
* **Flight dynamics (25)**: remains cosmetic; this track supplies smooth XY/heading underneath it.
* **Random generation (30–33)**: unrelated; generated scenarios are simply good demos for manual acceptance.
