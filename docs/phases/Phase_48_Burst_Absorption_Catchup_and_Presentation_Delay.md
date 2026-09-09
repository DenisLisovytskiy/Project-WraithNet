# Phase 48 — Burst Absorption, Catch-up & Presentation Delay

> **LLM implementation prompt:** Assume Phases 46–47 are complete and passing. Read `docs/21_Smooth_Live_Motion_Design_Document.md` §§5–7, 9, 13 before coding. Make live viewing robust when many ticks arrive at once (startup backlog, CNP pause then burst, machine hitch). Keep combat/sim code paths unchanged.

## Goal

Absorb tick floods without multi-teleport frames, keep a small presentation delay so interpolation usually has a `next` tick, and recover toward “live” when the backlog grows too large.

## Part A — presentation delay

Expose configurable delay on `TickBuffer` / viewer settings:

* Default `DEFAULT_INTERP_DELAY_S` (design starting point: `0.10`).
* Presentation time targets `last_accept_time - delay` while live.
* Tests prove that with delay > 0 and two ticks buffered, sampling occurs between them rather than always on the latest.

Keep the knob local to the viewer; do not add a gameplay config constant that headless CI must honor.

## Part B — backlog & catch-up policy

Implement and document one primary policy (recommended):

1. While `backlog_len` is modest, advance presentation time at `1×` real `dt`.
2. While behind beyond a soft threshold, allow presentation time to advance faster up to `CATCHUP_MAX_RATE` (e.g. 8×) until near live.
3. If `backlog_len > CATCHUP_BACKLOG_TICKS` (e.g. 30), **snap** the buffer to the latest pair `(T_{n-1}, T_n)` (or latest only), discard older ticks, and resume interpolating — do not walk every intermediate tick through the scene.

Also handle:

* First live tick after header: spawn without lerping from origin.
* Reconnect / new header: `reset()` then catch-up policy applies to the new backlog.

## Part C — extrapolation cap

When the next tick is late:

* Allow α up to `MAX_EXTRAPOLATION_ALPHA` (e.g. 1.10) along the last segment’s velocity estimate **or** simply hold α=1 (choose one; prefer capped extrapolation only if easy to test).
* Never invent strikes/outcomes while extrapolating.
* Clear extrapolating state when the next tick arrives.

## Part D — optional live-only sim pacing flag (optional sub-part)

Only if needed after measurement:

* Add an **opt-in** launcher/viewer flag such as `--cinematic-pacing` that inserts a small sleep in the sim worker between ticks during live runs.
* Default **off**.
* Headless `pytest` must not enable it.
* Document that Phase 48’s viewer buffer is still the primary smoothness mechanism.

If you skip this sub-part, state that explicitly in the PR/summary and rely on viewer-side delay/catch-up only.

## Part E — tests

Pure + headless coverage:

1. Synthetic flood: push 50 ticks in one instant; one frame of sampling does not require 50 scene snap updates; backlog policy trims or catch-up engages.
2. Delay: with delay=0.1 and ticks at t=1.0 and t=1.05, presentation at t=1.0 still samples the earlier segment (or waits) per the documented delay rule — write the assertion to match the chosen definition of delay.
3. Snap path: exceeding max backlog leaves buffer length bounded.
4. Extrapolation never emits duplicate presented events.
5. Phase 47 live integration tests still pass.
6. Performance smoke: pushing/sampling 1000 ticks in the pure buffer stays well under a generous CI ceiling (e.g. 100 ms) on the motion module alone.

## Non-goals

* Perfect network jitter modeling.
* Multiplayer anti-cheat.
* Changing CNP timeouts to “fix” the view.
* Replay transport (Phase 49).

## Acceptance criteria

1. Tick floods cannot unbounded-grow the buffer.
2. Default live delay is enabled and tested.
3. Catch-up/snap behavior is deterministic given the documented policy.
4. Full `pytest` passes.
5. Manual: Launch after a heavy brief/connect; view reaches smooth live motion without stuttering through every backlog tick.
