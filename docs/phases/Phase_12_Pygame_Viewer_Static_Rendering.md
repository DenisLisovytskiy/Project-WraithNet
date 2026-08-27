# Phase 12 — Pygame Viewer: Static Rendering & Manual Stepping

> **How to use this document:** This is a complete, standalone prompt. Give an LLM coding agent this entire file as its task. Assume Phase 11 is complete and passing: `wraithnet/trace_schemas.py`/`wraithnet/recorder.py` exist, `WraithNetBridge` can optionally record a trace, and `python -m wraithnet.main` produces a valid `results/trace.jsonl`. Read `docs/07_Visualization_Design_Document.md` in full (especially §5 and §7) before starting. This phase adds a **new, separate top-level package**, `viewer/`, and a new optional dependency file, `requirements-viz.txt`. It must not modify anything under `wraithnet/` or `tests/` (Phase 11's work is already done and must not regress). This phase renders exactly **one tick at a time**, stepped manually — no auto-play, no animation, no event highlighting. That's Phase 13.

## Context

This is Phase 12 of 3 (11–13). Phase 11 gave you a recorded trace file (`results/trace.jsonl`) that fully describes a completed run, tick by tick. This phase's job is narrow and mechanical: open a Pygame window, load that file, and correctly draw the state of whichever tick the user is currently looking at, with keyboard controls to move forward/backward through the recorded ticks one at a time. Getting the coordinate mapping, shapes, and colors right *here* is what makes Phase 13's animation trustworthy — don't rush this phase to get to the "fun" playback part.

## Part A — `requirements-viz.txt` (new file)

```
-r requirements.txt
pygame>=2.5,<3.0
```

Do **not** add Pygame to `requirements.txt` or `requirements-dev.txt` — it stays isolated to this file, so running the core simulation or its test suite never requires Pygame to be installed (design doc §5).

## Part B — `viewer/` package layout

```
viewer/
    __init__.py
    trace_loader.py   # reads a .jsonl trace file into (TraceHeader, list[TraceTick])
    render.py          # coordinate mapping + all drawing primitives
    app.py             # entrypoint + main loop + keyboard handling
```

### 1. `viewer/trace_loader.py`

```python
"""Loads a recorded trace (Phase 11's wraithnet/recorder.py output) for the
Pygame viewer. Imports only wraithnet.trace_schemas + stdlib -- never pygame."""

from pathlib import Path

from wraithnet.trace_schemas import TraceHeader, TraceTick


def load_trace(path: str | Path) -> tuple[TraceHeader, list[TraceTick]]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    if not lines:
        raise ValueError(f"Trace file {path} is empty")
    header = TraceHeader.model_validate_json(lines[0])
    ticks = [TraceTick.model_validate_json(line) for line in lines[1:]]
    if not ticks:
        raise ValueError(f"Trace file {path} has a header but no recorded ticks")
    return header, ticks
```

Loading the whole file into memory is intentional and sufficient at this project's scale (a few dozen ticks, a handful of entities per tick — see design doc §5, "small enough that streaming isn't needed").

### 2. `viewer/render.py` — coordinate mapping (pure function, no Pygame surface needed to test the math)

```python
def world_to_screen(
    pos: tuple[float, float],
    grid_w: float,
    grid_h: float,
    canvas_size: int,
    margin: int,
) -> tuple[int, int]:
    """Maps a world-space (x, y) in [0, grid_w] x [0, grid_h] to a screen-space
    pixel coordinate inside a canvas_size x canvas_size square, inset by
    margin pixels on every side. Flips y: world's +y is "up" (matching
    docs/04_..._v3_Phase2.md's position tuples, e.g. Target_A=(75,75) is
    drawn toward the top-right, not bottom-right) while Pygame's surface
    origin is top-left with +y "down"."""
    x, y = pos
    usable = canvas_size - 2 * margin
    sx = margin + (x / grid_w) * usable
    sy = margin + usable - (y / grid_h) * usable
    return int(sx), int(sy)


def world_length_to_screen(length: float, grid_w: float, canvas_size: int, margin: int) -> int:
    """Scales a world-space *distance* (e.g. SENSOR_RADIUS, JAM_RADIUS) into
    pixels, using the same scale factor world_to_screen uses for x. Grid is
    square (GRID_WIDTH == GRID_HEIGHT in this project) so one scale factor
    is correct for both axes; do not special-case a non-square grid here."""
    usable = canvas_size - 2 * margin
    return int(length / grid_w * usable)
```

### 3. `viewer/render.py` — layout constants and drawing primitives

Define these as module-level constants (do not hard-code magic numbers inline in `app.py`):

```python
CANVAS_SIZE = 900
MARGIN = 40
HUD_WIDTH = 300
WINDOW_SIZE = (CANVAS_SIZE + HUD_WIDTH, CANVAS_SIZE)

BACKGROUND_COLOR = (18, 18, 24)
GRID_LINE_COLOR = (40, 40, 50)

JET_PALETTE = [(80, 200, 255), (255, 160, 60), (170, 255, 120)]  # indexed by TraceHeader.jet_ids position
ROLE_RING_COLOR = {
    "IDLE": (140, 140, 140),
    "MANAGER": (230, 200, 60),
    "CONTRACTOR": (90, 220, 90),
    "CONTRACTOR_PENDING": (230, 140, 40),
}

TARGET_TYPE_STYLE = {
    "HARDENED_BUNKER": {"shape": "square", "color": (150, 100, 60)},
    "MOBILE_RADAR": {"shape": "diamond", "color": (70, 120, 220)},
    "SAM_SITE": {"shape": "octagon", "color": (210, 60, 60)},
}
DESTROYED_COLOR = (70, 70, 70)
JAMMER_COLOR = (170, 90, 220)
STRIKE_RANGE_UNUSED_HINT = None  # STRIKE_RANGE is small (1.0) relative to the map; not drawn as a ring (would be nearly invisible) -- do not add one
```

Implement drawing functions that take a Pygame `Surface`, the already-loaded `TraceHeader`, and the *current* `TraceTick` (plus a `show_sensor_radius: bool` toggle state — see Part D), each responsible for one concern:

* `draw_grid_background(surface, header)` — background fill + light reference grid lines (e.g. every 10 world units) so positions are visually legible, plus the world's outer border at `MARGIN`.
* `draw_jammer(surface, header)` — fixed purple marker at each `jammer_positions` entry + a dashed circle at `jam_radius` (always drawn; jammers are static and never destroyed — draw unconditionally, no per-tick check needed).
* `draw_targets(surface, header, tick)` — for each `TargetFrame` in `tick.targets`, look up its static `position`/`target_type`/`hp_required` from `header` (position is not itself in `TraceHeader` — see note below) and draw per `TARGET_TYPE_STYLE`; if `hp_remaining <= 0`, draw in `DESTROYED_COLOR` instead (outline only, per design doc §5's "faded/greyed-out outline" rule) and skip the SAM engagement ring for a destroyed `SAM_SITE`; otherwise draw the `hp_remaining/hp_required` label above it, and for a live `SAM_SITE` also draw a dashed ring at `sam_engagement_range`.
* `draw_jets(surface, header, tick, show_sensor_radius)` — for each `JetFrame` in `tick.jets`, draw a filled circle in its `JET_PALETTE` color (indexed by its position in `header.jet_ids`), an outline ring colored by `ROLE_RING_COLOR[frame.role]` (skip the ring if `role is None`), a thin HP bar above it (`hp / JET_HP`... — `JET_HP` isn't in the trace header either, see note below), and — only if `show_sensor_radius` is on — a faint dotted circle at `header.sensor_radius`; if `frame.jammed`, draw a small distinct-colored ring/icon indicating jammed status.
* `draw_hud(surface, header, tick, tick_index, total_ticks, show_sensor_radius)` — right-side panel: current tick / total ticks, a legend (role ring colors, target-type shapes, jammed indicator), and live counts derived from `tick` (`targets_destroyed = sum(1 for t in tick.targets if t.hp_remaining <= 0)` out of `len(tick.targets)`, `jets_alive = len(tick.jets)` out of `len(header.jet_ids)`).

**Two data points you need but that are not directly in `TraceHeader`/`TraceTick` as specified — resolve them like this, do not go back and change Phase 11's schema:**

* **Target `position`**: not stored per-tick (targets never move) and not explicitly in `TraceHeader` either per the schema as written in Phase 11. Add a `target_positions: dict[str, tuple[float, float]]` field to `TraceHeader` in `wraithnet/trace_schemas.py` and populate it in `wraithnet/recorder.py::record_header` (`{tid: t.position for tid, t in model.targets.items()}`) — this is a small, backward-compatible *addition* to Phase 11's schema that Phase 11's own doc missed; do it now rather than working around its absence with a hack in the viewer. Re-run Phase 11's test suite after this change to confirm nothing broke (it shouldn't — it's a strictly additive field).
* **`JET_HP`**: a `wraithnet.config` constant, not part of the trace schema (the trace is meant to be self-contained and independent of `wraithnet` internals wherever reasonably possible, but this one constant is small enough and stable enough — see design doc's own non-goal about not over-engineering this feature — that importing `from wraithnet.config import JET_HP` directly in `viewer/render.py` is acceptable and simpler than adding it to the header). Use it only for the HP-bar denominator.

## Part C — `viewer/app.py` — entrypoint and manual step loop

```python
"""python -m viewer.app <path-to-trace.jsonl>"""

import sys

import pygame

from viewer.render import CANVAS_SIZE, WINDOW_SIZE, draw_grid_background, draw_hud, draw_jammer, draw_jets, draw_targets
from viewer.trace_loader import load_trace


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python -m viewer.app <path-to-trace.jsonl>")
        sys.exit(1)

    header, ticks = load_trace(sys.argv[1])
    tick_index = 0
    show_sensor_radius = False

    pygame.init()
    screen = pygame.display.set_mode(WINDOW_SIZE)
    pygame.display.set_caption("WraithNet Replay Viewer")
    clock = pygame.time.Clock()

    running = True
    while running:
        for event in pygame.event.get():
            if event.type == pygame.QUIT:
                running = False
            elif event.type == pygame.KEYDOWN:
                if event.key in (pygame.K_RIGHT, pygame.K_SPACE):
                    tick_index = min(tick_index + 1, len(ticks) - 1)
                elif event.key == pygame.K_LEFT:
                    tick_index = max(tick_index - 1, 0)
                elif event.key == pygame.K_s:
                    show_sensor_radius = not show_sensor_radius
                elif event.key == pygame.K_ESCAPE:
                    running = False

        current = ticks[tick_index]
        draw_grid_background(screen, header)
        draw_jammer(screen, header)
        draw_targets(screen, header, current)
        draw_jets(screen, header, current, show_sensor_radius)
        draw_hud(screen, header, current, tick_index, len(ticks), show_sensor_radius)
        pygame.display.flip()
        clock.tick(30)

    pygame.quit()


if __name__ == "__main__":
    main()
```

Keyboard controls for this phase: `→`/`space` = next tick, `←` = previous tick, `S` = toggle sensor-radius overlay, `Esc`/window-close = quit. No play/pause, no speed control — those are Phase 13.

## Part D — Tests

Pygame rendering itself can't be meaningfully unit-tested without a display, but most of this phase's logic can and must be tested headlessly:

### `tests/test_viewer_render.py` (new file — note this lives under the existing `tests/` even though `viewer/` is a separate top-level package, consistent with how the project already tests other modules)

* `world_to_screen`: hand-computed cases — the grid's four corners map to the four corners of the inset canvas (accounting for `margin` and the `y`-flip), and the center of the grid `(grid_w/2, grid_h/2)` maps to the center of the canvas. Explicitly assert `(0, 0)` (world's bottom-left) maps to the **bottom-left** of the canvas in screen coordinates (i.e. `sy` near `canvas_size - margin`, not near `margin`) — this is the test that would catch an inverted/forgotten `y`-flip.
* `world_length_to_screen`: a known distance (e.g. `SENSOR_RADIUS`) scales proportionally to the canvas's usable size.
* A synthetic `TraceHeader`/`TraceTick` pair (hand-built, not loaded from a file) round-trips correctly through `viewer/trace_loader.py::load_trace` when written to a `tmp_path` file first.

### `tests/test_viewer_smoke.py` (new file — requires Pygame; skip cleanly if unavailable)

```python
import os
os.environ.setdefault("SDL_VIDEODRIVER", "dummy")  # headless-safe, no real display needed -- must be set BEFORE `import pygame`

import pytest

pygame = pytest.importorskip("pygame")
```

* Build a tiny synthetic trace (1 header + 3 ticks, one jet, one target, one jammer, hand-constructed `TraceHeader`/`TraceTick` objects — do not require a real `wraithnet` run for this test), write it to a `tmp_path` file, `load_trace` it, then call each `draw_*` function directly against a real (but dummy-driver) `pygame.Surface` for every tick in the trace, asserting only that **no exception is raised**. This is a smoke test, not a pixel-correctness test — pixel-level assertions on rendered output are explicitly out of scope for this phase.
* If `pytest.importorskip("pygame")` skips (Pygame not installed, e.g. in a CI job that only installs `requirements-dev.txt`), this file's tests must be skipped, not fail — confirm the rest of `pytest` still passes cleanly in an environment without `requirements-viz.txt` installed (this is the concrete proof that Phase 11/the rest of the suite truly has zero Pygame dependency).

## Explicit non-goals for this phase

* No auto-play/animation loop, no speed control, no strike/destruction/jam-transition visual highlighting — all Phase 13.
* No end-of-run summary overlay — Phase 13.
* No changes to `wraithnet/` or `tests/test_recorder.py`/`test_trace_schemas.py` from Phase 11, **except** the one additive `target_positions` field on `TraceHeader` called out in Part B (and its corresponding one-line addition in `wraithnet/recorder.py::record_header`) — that is the only sanctioned change outside `viewer/`.
* No pixel-level rendering-correctness tests (comparing rendered surfaces against reference images) — the smoke test in Part D is sufficient for this phase.

## Acceptance criteria

1. `pip install -r requirements-viz.txt && python -m viewer.app results/trace.jsonl` (using a real trace produced by Phase 11's recorder against a full `python -m wraithnet.main` run) opens a `1200×900` window showing tick 0's state: `Jet_1`/`Jet_2`/`Jet_3` at their scenario start positions, `Target_A`/`Target_B`/`SAM_Site_1` at their scenario positions with correct shapes, `EW_Jammer_1` with its dashed jam-radius ring visible.
2. `→`/`←` step forward/backward through every recorded tick without error, all the way to the final tick and back to tick 0; `S` toggles the sensor-radius overlay on/off; `Esc` or closing the window exits cleanly (exit code `0`, no traceback).
3. `pytest` passes in an environment with `requirements-dev.txt` installed but **without** `requirements-viz.txt`/Pygame — `test_viewer_render.py`'s pure coordinate-math tests still run and pass (they don't need Pygame), and `test_viewer_smoke.py`'s Pygame-dependent tests skip cleanly rather than erroring.
4. `pytest` also passes with `requirements-viz.txt` installed, exercising the smoke tests for real (via `SDL_VIDEODRIVER=dummy`).
5. The one sanctioned schema addition (`TraceHeader.target_positions`) is applied consistently in `wraithnet/trace_schemas.py` and `wraithnet/recorder.py`, and Phase 11's existing tests (`tests/test_trace_schemas.py`, `tests/test_recorder.py`) still pass unmodified against it.
