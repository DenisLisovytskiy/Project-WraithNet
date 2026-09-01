# Phase 19 — Editor → Mission Brief → Live Launch Integration

> **How to use this document:** This is a complete, standalone prompt. Give an LLM coding agent this entire file as its task. Assume Phases 0–18 are complete and passing: scenarios are data-driven (Phase 14), the sim can stream live over TCP with full backfill (Phase 15), `viewer3d` can render a live stream with camera modes/HUD/highlights (Phases 16–17), and `viewer3d` has a scenario editor mode that can save/load/preview a `ScenarioConfig` (Phase 18). Read `docs/09_3D_Live_Visualization_and_Scenario_Editor_Design_Document.md` §9 in full before starting. **This is the final phase of the expansion** — it wires everything built in Phases 14–18 into the single end-to-end user flow the original request asked for: customize a scenario, preview it, launch it, watch it live, all inside one running `viewer3d` window.

## Context

Every prior phase in this expansion built one piece in isolation: a loadable scenario format, a live data feed, a 3D renderer with camera modes, an editor. None of them yet talk to each other end-to-end inside one running application. This phase adds the "Launch" button and the mode-switching sequencing that makes `viewer3d/app.py` a single coherent tool: start in editor mode, hit Launch, watch the same window transition through mission-brief into live tactical, with the actual simulation running underneath as an independent subprocess.

## Part A — `viewer3d/launch.py` (new file)

```python
"""Spawns wraithnet.main as a subprocess with --scenario and --live, and
hands back everything viewer3d/app.py needs to connect a StreamClient to
it. Deliberately a subprocess, not a thread or in-process import -- keeps
the sim's asyncio event loop and viewer3d's Panda3D loop in genuinely
separate processes/interpreters, matching design doc §1/§9's "cross-
process" decision: a sim crash can't take the viewer down, and vice versa.
"""

import subprocess
import sys


class SimLaunchHandle:
    def __init__(self, process: subprocess.Popen, host: str, port: int) -> None:
        self.process = process
        self.host = host
        self.port = port

    def is_running(self) -> bool:
        return self.process.poll() is None

    def terminate(self) -> None:
        if self.is_running():
            self.process.terminate()


def launch_scenario(scenario_path: str, host: str = "127.0.0.1", port: int = 8765) -> SimLaunchHandle:
    process = subprocess.Popen(
        [sys.executable, "-m", "wraithnet.main",
         "--scenario", scenario_path,
         "--live", "--live-host", host, "--live-port", port_as_str_if_needed],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return SimLaunchHandle(process, host, port)
```

Fix the placeholder `port_as_str_if_needed` (pass `str(port)` — `subprocess.Popen`'s argv must be strings). Use a distinct port per launch if you want to support launching multiple scenarios without colliding (e.g. pick an ephemeral free port via `socket.socket().bind(("127.0.0.1", 0))` then `.getsockname()[1]` before spawning, and pass that as `--live-port`) — recommended, since it means a leftover sim process from a previous launch can never collide with a new one. Document whichever port-selection strategy you pick.

## Part B — `viewer3d/app.py` changes: wiring the Launch button

`ScenarioEditor` (Phase 18) gains a `Launch` button alongside its existing Save/Load/Preview buttons, and `WraithNetViewer3D` (Phase 16's app class, extended by Phase 17) gains the sequencing logic:

```python
def on_launch_clicked(self) -> None:
    """Called when the editor's Launch button is pressed."""
    errors = self.editor.save(self._current_scenario_path())  # Phase 18's save(), reused verbatim
    if errors is not None:
        return  # validation banner (Phase 18, Part B) already shows the errors; do nothing else

    # 1. Preview immediately -- reuses Phase 18's preview(), which itself reuses
    #    Phase 17's mission-brief mode. Does NOT wait on the subprocess.
    self.editor.preview()
    self.view_mode = "brief"

    # 2. Spawn the sim as an independent subprocess.
    handle = launch_scenario(self._current_scenario_path())
    self._sim_handle = handle

    # 3. Point a fresh StreamClient at the subprocess's live-stream port and
    #    start it -- its bounded connect-retry loop (Phase 16, Part B) is
    #    exactly what absorbs the sim subprocess's startup time; nothing
    #    here needs to block/wait manually.
    self._client = StreamClient(handle.host, handle.port)
    self._client.start()

    # 4. From here on, the existing Phase 16/17 queue-draining task takes
    #    over completely unchanged: it will see a HeaderEvent (confirming
    #    the sim started and its own header matches what preview() already
    #    showed), then TickEvents (triggering the existing "brief" -> "live"
    #    transition from Phase 17, Part C), with no new logic needed here.
```

**This is the entire integration** — if you find yourself needing to change anything in `viewer3d/scene.py`, `cameras.py`, `hud.py`, or `highlights.py` (Phases 16–17) to make this work, stop and reconsider; those modules' existing contracts (header-only scenes, the `"brief"->"live"` handoff, per-tick highlight computation) were deliberately designed in earlier phases so that this phase is pure sequencing/glue, not new rendering logic.

## Part C — Handling a slow/failed subprocess start

`StreamClient`'s bounded retry loop (Phase 16, Part B) already handles "the subprocess hasn't opened its socket yet" gracefully — but this phase should also handle "the subprocess never starts at all" (e.g. a bad `--scenario` path, a Python exception in `wraithnet.main` itself) distinctly from "still starting, be patient":

* After `launch_scenario`, poll `handle.is_running()` alongside waiting for the `StreamClient`'s events — if the subprocess has already exited (crashed) before a `HeaderEvent` ever arrives, surface this clearly in the HUD (a new, distinct status beyond Phase 17's existing `"connecting"`/`DisconnectedEvent` states — e.g. read `handle.process.stderr` and show the last line or two) rather than silently retrying forever or showing a generic "disconnected" message that gives the user no idea why.
* Add a "Cancel launch" / "Back to editor" control that calls `handle.terminate()` and `self._client.stop()`, and returns `view_mode` to `"editor"` — the user should never be stuck watching an indefinite "connecting..." spinner with no way out.

## Part D — Clean shutdown

When the `viewer3d` window closes (or the user explicitly stops a live run), terminate any still-running sim subprocess rather than leaving it orphaned:

```python
def on_window_close(self) -> None:
    if self._sim_handle is not None and self._sim_handle.is_running():
        self._sim_handle.terminate()
    ...
```

Wire this into whatever shutdown hook `ShowBase` exposes for window-close events (or, at minimum, ensure it's called from `WraithNetViewer3D.__del__`/an explicit `app.shutdown()` the test suite calls, and document if a real window-close signal wasn't reliably hookable in this Panda3D version).

## Part E — Tests

### `tests/test_viewer3d_launch.py` (new file, Panda3D-free where possible)

* `launch_scenario` against a real, valid scenario file (e.g. `scenarios/default.json` from Phase 14) actually starts a subprocess whose live stream becomes connectable within a reasonable bounded time (use the same kind of `StreamClient`/test-helper pattern Phase 15/16 already established) — assert a `HeaderEvent` matching that scenario's data is eventually received, then terminate the handle and assert `is_running()` becomes `False` shortly after.
* `launch_scenario` against a nonexistent scenario path results in the subprocess exiting quickly with a non-zero return code (confirm `wraithnet.main`'s existing `argparse`/`load_scenario` error handling from Phase 14 already produces a clean failure here — this phase should not need to add new error handling inside `wraithnet/` itself; if it does, that's a sign Phase 14 was incomplete, not a task for this phase to silently patch over — flag it instead).
* Two sequential `launch_scenario` calls (simulating "launch, then later launch again") each get their own live-stream port (per Part A's port-selection strategy) and do not collide, even if the first subprocess is still running when the second starts.

### `tests/test_viewer3d_smoke.py` additions (extends Phases 16–17's file)

* Full end-to-end smoke test, headless: build a small scenario draft in a `ScenarioEditor` (Phase 18), click through the equivalent of Save → Launch, and assert `view_mode` transitions `"editor" -> "brief"` (immediately, before the subprocess necessarily has anything running) `-> "live"` (once real ticks start arriving from the spawned subprocess) `-> "finished"` (once the subprocess's run completes) — this is the single test that proves the entire expansion's headline promise (design doc §0: "customize, preview, launch, watch it live") actually works, end to end, in one process's lifetime. Give it a generous but bounded timeout (a full run takes ~100s wall-clock per `docs/06_Code_Review_Full_Project.md`'s prior finding — do not shorten the real scenario's timing just to make this test fast; instead, either use a small custom scenario that resolves quickly, matching the pattern Phase 14's own tests already used for a "resolves fast" fixture, or explicitly mark this test slow/skip-by-default with a clear reason if a full-scenario run is judged too slow for routine `pytest` runs).
* Crash-handling: launch against a scenario file that will fail (Phase 14's `load_scenario` raising, or a scenario JSON deliberately malformed), and assert the HUD reaches a distinct "launch failed" status rather than hanging in `"connecting"` — mirrors Part C's requirement.

## Explicit non-goals for this phase

* No change to `wraithnet/main.py`'s CLI beyond what Phase 14/15 already added (`--scenario`, `--live`, `--live-host`, `--live-port`) — if this phase needs a new flag, that's a sign the design should be reconsidered, not a green light to casually expand `main.py`'s surface further.
* No support for launching against a *remote* (non-`localhost`) sim process — matches design doc §10's non-goal; `launch_scenario`'s `host` parameter exists for symmetry/testability, not as a supported remote-launch feature.
* No "multiple simultaneous live views in one window" — one active sim connection/live view at a time; launching again while one is already running should either be disallowed (grey out Launch) or implicitly terminate-and-replace the previous one — pick one behavior and document it clearly, since the design doc does not mandate which.
* No packaging/installer work (a single executable, a Start Menu shortcut, etc.) — running via `python -m viewer3d.app` remains the supported entrypoint, exactly like every other tool in this project (`python -m wraithnet.main`, `python -m viewer.app`).

## Acceptance criteria

1. `pytest` passes, including all new/extended test files.
2. Manually: open `viewer3d.app`, use the editor (Phase 18) to place a small custom scenario, click Preview to sanity-check placement, click Launch, and watch the same window transition from the mission-brief camera into a live 3D view of the actual simulation running underneath, with HUD/camera modes/event highlights all working exactly as Phase 17 already proved they do — the only new thing this phase adds is that the data feeding all of that came from a scenario the user just built, launched from inside the viewer itself, rather than a pre-existing file and a separately-started `wraithnet.main` process. Note in your final summary if a display wasn't available to confirm this manually, and rely on the headless end-to-end smoke test as the closest available proxy.
3. Closing the `viewer3d` window while a launched sim subprocess is still running leaves no orphaned Python process behind (verify via the OS process list during manual testing, if possible).
4. `grep -rn "panda3d\|viewer3d" wraithnet/ requirements.txt requirements-dev.txt requirements-viz.txt` returns no matches — even after this final integration phase, the core simulation package and the existing 2D viewer remain completely unaware `viewer3d` exists, exactly as every prior phase's isolation guarantee required.
