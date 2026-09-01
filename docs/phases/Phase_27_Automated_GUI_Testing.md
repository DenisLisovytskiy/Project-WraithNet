# Phase 27 — Automated GUI Testing

> **How to use this document:** This is a complete, standalone prompt. Give an LLM coding agent this entire file as its task. Assume Phases 0–19 are complete and passing (the 2D Pygame replay viewer and the 3D Panda3D live viewer + scenario editor both exist, with the test files listed in `docs/14_Automated_GUI_Testing_Design_Document.md` §4 already in `tests/`). Read that design document in full before starting. **This phase adds no GUI feature and must not change how either window looks.** It extracts two remaining untestable blobs out of `viewer/app.py` / `viewer/render.py`, then adds the Layer-1 and Layer-2 tests the design doc §5.2 lists. Phases 20–26 are *not* prerequisites and must not be implemented here.

> **Status:** Automated tests for the *currently implemented* 3D GUI (`viewer3d/`, Phases 16–19) are already in `tests/test_viewer3d_*.py` (cameras, CLI, isolation, DirectGUI commands, keybindings, HUD, window-close). Remaining work in this phase is the 2D Pygame viewer (`viewer/controls.py`, `viewer/hud_text.py`, and the Layer-1 tests in the design doc §5.2). Phases 20–26 must add their own 3D GUI tests when they land — this phase does not cover them.

> **Automated GUI tests are required** for any further GUI work, following `docs/14_Automated_GUI_Testing_Design_Document.md`. Pixel/screenshot tests remain out of scope.

## Context

Phases 12–19 already test most GUI *logic* headlessly: coordinate mapping, highlight diffs, playback accumulator, stream-client retry, editor save/load/preview, and the editor→launch→live pipeline. Three gaps remain, all called out in the design doc §4.3: the 2D viewer's keyboard table still lives inside `viewer/app.py`'s Pygame loop; HUD/overlay *strings* are composed only at blit time; 3D camera geometry and the remaining keybindings/DirectGUI `command=` hooks are only tested as "did not raise." This phase closes those gaps using the same three-layer model every prior GUI phase already used (pure logic / headless smoke / manual eyeball). Pixel tests, screenshot diffs, and OS-level click bots are explicit non-goals — do not add them.

## Part A — `viewer/controls.py` (new file, Pygame-free)

Extract Phase 13's keyboard table from `viewer/app.py` into a pure state machine. `viewer/app.py` keeps the Pygame event loop but must not duplicate the rules.

```python
"""Pure replay-viewer input/state. No pygame import -- tests/test_viewer_controls.py
drives this with string key names so the keyboard table is Layer-1 testable.
See docs/14_Automated_GUI_Testing_Design_Document.md §5.1.
"""

from dataclasses import dataclass

MIN_TPS = 0.5
MAX_TPS = 20.0
DEFAULT_TPS = 4.0


@dataclass(frozen=True)
class ReplayViewState:
    tick_index: int
    last_index: int
    playing: bool
    ticks_per_second: float
    show_sensor_radius: bool
    playback_accumulator_ms: float
    quit_requested: bool = False


def apply_keydown(state: ReplayViewState, key: str) -> ReplayViewState:
    """`key` is a stable, pygame-free name from this closed set:
    'right', 'left', 'space', 'plus', 'minus', 's', 'r', 'escape'.
    Unknown keys are a no-op (return state unchanged).
    """
    ...
```

Rules — copy these from the existing `viewer/app.py` KEYDOWN branch, do not invent new ones:

| `key` | Effect |
|---|---|
| `right` | `playing=False`, `playback_accumulator_ms=0`, `tick_index = min(tick_index+1, last_index)` |
| `left` | same pause/reset-accumulator, `tick_index = max(tick_index-1, 0)` |
| `space` | toggle `playing`, `playback_accumulator_ms=0` |
| `plus` | `ticks_per_second = min(MAX_TPS, ticks_per_second * 1.5)` |
| `minus` | `ticks_per_second = max(MIN_TPS, ticks_per_second / 1.5)` |
| `s` | toggle `show_sensor_radius` |
| `r` | `tick_index=0`, `playing=False`, `playback_accumulator_ms=0` |
| `escape` | `quit_requested=True` (do **not** call `sys.exit` / `pygame.quit` here) |

`viewer/app.py` maps `pygame.K_RIGHT`/`K_LEFT`/`K_SPACE`/`K_PLUS`/`K_EQUALS`/`K_KP_PLUS`/`K_MINUS`/`K_KP_MINUS`/`K_s`/`K_r`/`K_ESCAPE` onto those string names (plus/equals/kp-plus all become `'plus'`; minus/kp-minus both become `'minus'`), then assigns `state = apply_keydown(state, name)`. Auto-play still goes through the existing `advance_playback` (do not merge the two). After the event loop, if `state.quit_requested`, break out the same way `K_ESCAPE` does today.

`grep -n "pygame" viewer/controls.py` must return no matches.

## Part B — `viewer/hud_text.py` (new file, Pygame-free)

Pull the *strings* `draw_hud` and `draw_end_of_run_overlay` currently compose inline into pure functions. `draw_*` still blits; it just blits what these functions return.

```python
"""HUD / end-of-run overlay copy for the 2D replay viewer. No pygame import.
draw_hud / draw_end_of_run_overlay blit these strings; they do not invent them.
"""

from wraithnet.trace_schemas import TraceHeader, TraceTick
from viewer.playback import RunSummary, compute_run_summary


def tick_line(tick: TraceTick, tick_index: int, total_ticks: int) -> str:
    """e.g. 'Tick 3   (4/33)' -- keep whatever format draw_hud already uses."""
    ...


def status_line(playing: bool, ticks_per_second: float) -> str | None:
    """None when ticks_per_second is 0 (Phase 12 HUD had no play/pause line).
    Otherwise 'PLAYING  4.0 tps' / 'PAUSED  4.0 tps' matching current format."""
    ...


def sensor_line(show_sensor_radius: bool) -> str: ...
def targets_line(tick: TraceTick) -> str: ...
def jets_line(header: TraceHeader, tick: TraceTick) -> str: ...


def overlay_lines(header: TraceHeader, ticks: list[TraceTick]) -> list[str]:
    """The visible lines of the end-of-run overlay, in draw order, derived
    from compute_run_summary(...) -- do not re-count strikes in this module.
    Include the same title case (solved / swarm_wiped_out) the overlay
    already shows. Skip distance_traveled, still a deliberate Phase 13 cut.
    """
    ...
```

Match the *existing* wording in `viewer/render.py` byte-for-byte (e.g. `"Targets  {n}/{m} destroyed"`, `"Jets     {n}/{m} alive"`, `"Sensor rings: ON"` / `"OFF"`). This is a move, not a copy-edit. After the move, `draw_hud` / `draw_end_of_run_overlay` call these helpers; they do not keep a second copy of the f-strings.

`grep -n "pygame" viewer/hud_text.py` must return no matches.

## Part C — Argv helpers (both viewers)

Add a small, side-effect-free parser next to each `main()` so CLI handling is Layer-1 testable. Do **not** change the supported invocations.

```python
# viewer/app.py
def parse_viewer_argv(argv: list[str]) -> str:
    """Return the trace-file path. Raise SystemExit (or a small dedicated
    exception you then map to sys.exit in main) if len(argv) != 1 after
    the script name has been stripped -- same usage string main() already prints.
    """
    ...

# viewer3d/app.py
@dataclass(frozen=True)
class Viewer3DArgs:
    editor_mode: bool
    edit_path: str | None
    host: str | None
    port: int | None


def parse_viewer3d_argv(argv: list[str]) -> Viewer3DArgs:
    """[] or ['--edit'] -> editor_mode=True, edit_path=None
    ['--edit', 'path.json'] -> editor_mode=True, edit_path='path.json'
    ['127.0.0.1', '8765'] -> editor_mode=False, host/port set
    anything else -> SystemExit with the existing usage string
    """
    ...
```

`main()` in both files becomes: parse, then construct the app, then `run()`. No change to default behavior (`python -m viewer3d.app` still opens the editor; `python -m viewer.app <path>` still opens the replay).

## Part D — Tests

### `tests/test_viewer_controls.py` (new, no Pygame)

* Right at `tick_index=0` of `last_index=4` → `tick_index==1`, `playing is False`.
* Right while `playing=True` pauses (the Phase 13 "manual step fights the clock" rule).
* Right at `last_index` stays at `last_index` (no wrap).
* Left at `0` stays at `0`; left at `2` goes to `1` and pauses.
* Space toggles `playing` both directions and zeroes the accumulator.
* `plus` from `DEFAULT_TPS` increases it; repeated `plus` never exceeds `MAX_TPS`; repeated `minus` never goes below `MIN_TPS`.
* `s` toggles `show_sensor_radius`; `r` resets to tick 0 and pauses; `escape` sets `quit_requested=True` without changing `tick_index`.
* An unknown key (`"q"`) returns a state equal to the input.

### `tests/test_viewer_hud_text.py` (new, no Pygame)

* A synthetic 1-jet / 2-target tick with `tick=3`, `tick_index=3`, `total_ticks=10`, one target at `hp_remaining=0`, produces exactly the current formats: tick line contains `"Tick 3"` and `"(4/10)"`; targets line is `"Targets  1/2 destroyed"`; jets line is `"Jets     1/1 alive"` (adjust only if the *existing* `draw_hud` format differs — match production, don't "improve" it).
* `status_line(True, 4.0)` contains `PLAYING`; `status_line(False, 4.0)` contains `PAUSED`; `status_line(False, 0.0)` is `None`.
* `overlay_lines` on a tiny finished trace equals a hand-built expectation derived from `compute_run_summary` of that same trace (title reflects `solved` vs `swarm_wiped_out`; missile counts match). Do not reimplement summarization in the test.

### `tests/test_viewer_cli.py` (new, no Pygame)

* `parse_viewer_argv(["trace.jsonl"])` returns `"trace.jsonl"`.
* `parse_viewer_argv([])` and `parse_viewer_argv(["a", "b"])` raise `SystemExit`.

### `tests/test_viewer3d_cameras.py` (new, Panda3D + `window-type none` / `importorskip`)

Reuse the same `loadPrcFileData` preamble as `tests/test_viewer3d_smoke.py` (must run before `ShowBase`). Construct cameras with a tiny stub `base` that has `camera=None` so `_camera_node` builds the dummy node — or instantiate a headless `WraithNetViewer3D(..., start_client=False)` if a stub is more awkward; pick whichever is simpler and destroy the app in a `finally`.

* `ChaseCamera.update` with jet `(50, 50, 8)`, heading `0`, `follow_distance=12`, `follow_height=5`: dummy camera `y < 50`, `z == pytest.approx(13.0)`, and after `look_at` the camera is actually pointing at the jet (assert camera pos is 12 world-units behind along −y: `pytest.approx((50.0, 38.0, 13.0), abs=1e-4)`). Repeat with heading `90` and assert the camera moved along a different axis (`x` changes, `y` stays ~50).
* `MissionBriefCamera.update(1.0)` twice: `_angle_deg` advances by `angular_speed_deg_s` each time (mod 360); dummy camera `xy` after the second call differs from after the first.
* `OrbitCamera` with a known `center`/`radius`, call `_apply()` (or `update(0.0)` with no mouse): distance from camera pos to center equals `radius` within `1e-3`; `look_at` was aimed at `center` (camera's forward axis / `get_pos()`+orientation — whatever is reliable on the dummy node; asserting distance-to-center plus "z > center.z" is enough if full look-at is awkward under `window-type none`).

### `tests/test_viewer3d_smoke.py` additions (same file, same headless config)

* On a headless live-mode app with a two-jet synthetic header already installed (`app._header`, `view_mode="live"`): `_on_orbit` sets `camera_mode=="orbit"`; `_on_chase` sets `"chase"` and populates `selected_jet_id`; `_cycle_selected_jet` twice walks both ids and wraps back to the first. Do not require a real mouse.
* Editor DirectGUI: on an `editor_mode=True` app, invoke the actual button `command` callable for at least `Add Jet` (or whatever the attribute is — find it, don't guess a name that isn't there) and then `on_ground_clicked((30, 40))`, asserting a jet appears in the draft — proving the widget is wired to `begin_placement`, not just that the method exists. Similarly invoke `Save` against a valid draft (`tmp_path`) and `Preview` / `Back` (`exit_preview`) if those buttons expose a `command`. If a given button is only created in some modes, test the ones that exist and note any skip in a one-line comment.

### `tests/test_viewer3d_cli.py` (new, Panda3D-free — import `parse_viewer3d_argv` only)

Confirm `grep` on this file finds no `panda3d`/`direct`. Cases from Part C: `[]`, `["--edit"]`, `["--edit", "x.json"]`, `["127.0.0.1", "8765"]` succeed as specified; `["--edit", "a", "b"]` and `["only-one-token"]` raise `SystemExit`.

### `tests/test_gui_isolation.py` (new, stdlib-only)

Read files as text from the repo root (do not import `viewer3d.scene` etc.). Assert:

* Every `wraithnet/**/*.py` source file has no match for `pygame`, `panda3d`, `from direct`, `import direct`, `viewer3d`, or `from viewer` / `import viewer` (be careful not to flag the word "preview" — use import-like patterns, e.g. `^(from|import).*viewer\b` and `pygame` / `panda3d` / `direct.` as whole tokens).
* `requirements.txt` and `requirements-dev.txt` contain neither `pygame` nor `panda3d`.
* `viewer3d/stream_client.py`, `viewer3d/highlights.py`, `viewer3d/launch.py` contain no `panda3d` / `direct`.
* `viewer/trace_loader.py`, `viewer/playback.py`, `viewer/controls.py`, `viewer/hud_text.py` contain no `pygame`.

If Phase 25/26 have already added `viewer3d/flight_dynamics.py`, include it in the Panda3D-free list; if they have not, do not create that file here.

## Explicit non-goals for this phase

* No pixel / screenshot / golden-image assertions (design doc §6).
* No `pyautogui` / real-window click tests, no new pytest plugin, no new dependency of any kind.
* No visual redesign, no HUD wording changes, no new keybindings, no `wraithnet/` changes.
* No implementation of Phase 24/25/26 features or of those phases' own tests.
* Do not merge `viewer/` and `viewer3d/` helpers; do not import `viewer` from `viewer3d` or vice versa.
* Do not rewrite `tests/test_viewer_smoke.py`'s existing draw-pipeline tests — they keep proving "no exception." The new HUD-copy tests sit *alongside* them.

## Acceptance criteria

1. `pytest` passes in an environment with `requirements-dev.txt` + `requirements-3d.txt` installed. `tests/test_viewer_controls.py`, `test_viewer_hud_text.py`, `test_viewer_cli.py`, `test_viewer3d_cli.py`, and `test_gui_isolation.py` all run without Pygame. `test_viewer3d_cameras.py` and the new smoke-test cases skip cleanly via `importorskip("panda3d")` when Panda3D is absent, and run for real when it is present.
2. `pytest` also still passes in an environment with only `requirements-dev.txt` (no viz, no 3D): every Pygame/Panda3D-dependent file skips, nothing errors, and the new Layer-1 files plus `test_gui_isolation.py` pass.
3. `grep -n "pygame" viewer/controls.py viewer/hud_text.py` returns no matches.
4. `python -m viewer.app results/trace.jsonl` and `python -m viewer3d.app --edit` still launch (same flags, same window contents) — this phase is a behavior-preserving extract. If you have no display, rely on the existing Layer-2 smoke tests plus the new Layer-1 tests as the proxy, and say so in the summary.
5. `git diff --stat` shows no changes under `wraithnet/`. Existing test files may be extended; do not delete or weaken an existing assertion to make a new one pass.
