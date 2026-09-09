# Phase 47 — Live 3D Dual-Clock Integration

> **LLM implementation prompt:** Assume Phase 46 is complete and passing. Read `docs/21_Smooth_Live_Motion_Design_Document.md` §§3–6, 8–10 before coding. Wire the pure `TickBuffer` into the live `viewer3d` render loop so jet XY/heading are sampled every frame. Do **not** implement full burst catch-up policy yet beyond whatever Phase 46 already provides. Do **not** change Mesa/CNP timing.

## Goal

In live view, jet ground tracks move continuously between streamed ticks at the Panda3D frame rate, while combat HUD fields still reflect authoritative presented ticks.

## Part A — integrate buffer into `viewer3d/app.py`

On entering live streaming / receiving header:

* Construct a `TickBuffer` with documented defaults from the design doc.
* `reset()` on new `HeaderEvent`.

On `TickEvent` ingest:

* `buffer.push(tick, t_accept=time.monotonic())` (or ClockObject time — pick one clock domain and use it consistently for push + presentation).
* Do **not** treat queue-drain as immediate pose authority anymore.

Each frame in the existing drain/render task (already has `dt`):

1. Drain network/queue events into the buffer (ingest).
2. `buffer.advance(dt)` (presentation clock).
3. `poses = buffer.sample_jet_poses()`.
4. Update scene jet nodes from **sampled poses**, not from a raw latest-tick snap.
5. `events = buffer.pop_newly_presented_events()` and drive highlights/effects/HUD transitions from those.
6. Keep existing altitude/bank easing, but ensure it does not fight XY sampling (document the chosen rule in a short comment).

Preserve editor mode: editor placement must not require the live interpolator. If the same app class hosts both, gate motion sampling to live/brief→live paths only.

## Part B — scene API adjustments

Extend `viewer3d/scene.py` (or adjacent helper) with a clear function, for example:

```python
def apply_sampled_jet_poses(handles: SceneHandles, poses: Mapping[str, SampledPose]) -> None:
    ...
```

Requirements:

* Sets XY from samples every frame.
* Applies heading from samples (replace any residual position-delta heading estimate for live motion).
* Leaves targets/SAMs/jammers behavior unchanged unless they already move.
* Remains safe when a jet id is missing from handles (late spawn) or missing from poses (despawn).

Deprecate or stop calling tick-snap XY updates for jets on the live path. If `update_tick` still applies HP/colors/rings, split “discrete tick visual state” from “continuous pose” rather than double-setting XY.

## Part C — cameras & selection

Chase / follow cameras must read the **sampled** pose for the selected jet each frame. Do not chase the unsampled latest tick while the mesh is interpolated elsewhere.

## Part D — effects timing

Spawn strike/destruction cosmetic effects when their tick is presented via `pop_newly_presented_events`, using the jet’s sampled position that frame when possible.

Do not reintroduce “effect fires on socket receipt while mesh is still showing an older pose” for the common case.

## Part E — tests

Extend/create headless Panda3D tests (window-type none), e.g. in `tests/test_viewer3d_motion_live.py` or extend existing viewer3d tests:

1. After two ticks pushed with distinct positions, advancing frames with `dt` moves the jet node gradually (assert position monotonic toward target, not equal to end on first frame unless α already 1).
2. Heading updates across frames between ticks.
3. HUD current tick does not jump to the newest buffered tick before presentation time catches up (when delay > 0).
4. Editor mode still places entities without requiring stream ticks.
5. Existing smoke/launch tests still pass.

No screenshot/pixel assertions.

## Non-goals

* Advanced catch-up snap / backlog flood policy (Phase 48).
* Trace-file replay wiring (Phase 49).
* Sim-side `asyncio.sleep` pacing.
* 2D Pygame viewer changes.

## Acceptance criteria

1. Live path uses `TickBuffer` for jet XY/heading.
2. Frame loop still returns `task.cont` and remains dt-based.
3. Headless viewer3d tests for this phase pass.
4. Full `pytest` passes.
5. Manual spot-check (when a display is available): Launch a short scenario; jets no longer visibly teleport each tick under normal load.
