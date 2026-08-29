# Phase 16 — Panda3D Viewer Skeleton: Static Scene & Live Stream Client

> **How to use this document:** This is a complete, standalone prompt. Give an LLM coding agent this entire file as its task. Assume Phases 0–15 are complete and passing (`python -m wraithnet.main --live` streams `TraceHeader`/`TraceTick` data over TCP to any connected client, with full backfill). Read `docs/09_3D_Live_Visualization_and_Scenario_Editor_Design_Document.md` §6 in full before starting. **This is the first phase to introduce Panda3D.** Keep it deliberately minimal: one fixed camera, placeholder primitives, no HUD polish, no camera modes, no editor — just prove the pipe from "sim streams a tick" to "3D scene updates" actually works end to end. Phase 17 builds the polish on top of what this phase proves out.

## Context

Phase 15 gave any process the ability to connect to a running (or already-finished) simulation and receive its full header + tick history live, over a socket. This phase builds the first consumer of that stream: a new top-level `viewer3d/` package (sibling to `viewer/`, never imported by `wraithnet/`) that connects, builds a static 3D scene from the header, and updates it as ticks arrive.

## Part A — `requirements-3d.txt` (new file)

```
-r requirements.txt
panda3d>=1.10,<2.0
```

Install it and confirm `python -c "import panda3d; from direct.showbase.ShowBase import ShowBase"` works in this environment before writing any other code in this phase — if Panda3D has no wheel for the installed Python version (check the same way Phase 12 had to work around Pygame's lack of a Python 3.14 wheel — see `requirements-viz.txt`'s comment and `docs/08_Code_Review_Phases_11-13.md` §2), document the substitute/workaround the same way `requirements-viz.txt` documents `pygame-ce`, with a comment explaining why.

## Part B — `viewer3d/stream_client.py` (new file)

```python
"""Background-thread TCP client for the live tick stream (Phase 15). Reads
newline-delimited JSON, decodes into TraceHeader/TraceTick via the exact
same Pydantic models the simulation side uses, and pushes them onto a
thread-safe queue for the Panda3D main thread to drain once per frame.

Deliberately uses a plain blocking socket + threading.Thread, not asyncio --
see docs/09_..._Design_Document.md §5 for why (Panda3D's main loop is not
asyncio-native; a drained queue is the simplest reliable pattern).
"""

from __future__ import annotations

import json
import queue
import socket
import threading
from dataclasses import dataclass
from typing import Union

from wraithnet.trace_schemas import TraceHeader, TraceTick


@dataclass(frozen=True)
class HeaderEvent:
    header: TraceHeader


@dataclass(frozen=True)
class TickEvent:
    tick: TraceTick


@dataclass(frozen=True)
class EndEvent:
    pass


@dataclass(frozen=True)
class DisconnectedEvent:
    """Pushed if the connection drops or the connect attempt fails after
    retrying -- lets the scene/app show a clear connection-status state
    instead of silently freezing on the last received frame."""
    reason: str


StreamEvent = Union[HeaderEvent, TickEvent, EndEvent, DisconnectedEvent]


class StreamClient:
    def __init__(self, host: str, port: int, connect_retries: int = 20, retry_delay_s: float = 0.5) -> None:
        self._host = host
        self._port = port
        self._connect_retries = connect_retries
        self._retry_delay_s = retry_delay_s
        self.events: "queue.Queue[StreamEvent]" = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._stop = threading.Event()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        sock = self._connect_with_retry()
        if sock is None:
            self.events.put(DisconnectedEvent(reason="could not connect after retries"))
            return
        buffer = b""
        try:
            with sock:
                while not self._stop.is_set():
                    chunk = sock.recv(65536)
                    if not chunk:
                        break
                    buffer += chunk
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        self._handle_line(line)
        except OSError as exc:
            self.events.put(DisconnectedEvent(reason=str(exc)))
            return
        self.events.put(DisconnectedEvent(reason="connection closed"))

    def _connect_with_retry(self) -> "socket.socket | None":
        # Bounded retry loop -- important for Phase 19's launch sequencing,
        # where viewer3d may try to connect before wraithnet.main has opened
        # its listening socket yet. Must NOT hang forever; give up after
        # self._connect_retries attempts and let the caller decide what to
        # show (see DisconnectedEvent).
        ...

    def _handle_line(self, line: bytes) -> None:
        if not line.strip():
            return
        message = json.loads(line)
        if message["type"] == "header":
            self.events.put(HeaderEvent(header=TraceHeader.model_validate(message["data"])))
        elif message["type"] == "tick":
            self.events.put(TickEvent(tick=TraceTick.model_validate(message["data"])))
        elif message["type"] == "end":
            self.events.put(EndEvent())
```

Fill in `_connect_with_retry` (loop `socket.create_connection((host, port), timeout=...)`, catching `ConnectionRefusedError`/`OSError`, sleeping `retry_delay_s` between attempts, up to `connect_retries` times, returning `None` if exhausted). This class must have **zero import of `panda3d`** — it is tested headlessly (Part E) exactly like `viewer/trace_loader.py` was in Phase 12.

## Part C — `viewer3d/scene.py` (new file)

Owns the Panda3D node graph. Import `panda3d`/`direct` freely here (unlike `stream_client.py`, this module's whole job requires Panda3D).

**Visual style target for this phase**: `docs/09_3D_Live_Visualization_and_Scenario_Editor_Design_Document.md` §6.3 has a concrete reference image (Ace Combat's mission-briefing map: dark background, glowing cyan wireframe grid ground, small camera-facing glyph markers rather than solid 3D shapes per entity). Build toward that, not toward realistic shaded 3D models:

* **Ground plane**: a flat grid rendered as glowing/emissive thin lines over a near-black plane (an unshaded material with a grid texture, or literally many thin `LineSegs`-drawn grid lines — either is fine, pick whichever is simpler to get looking crisp in this Panda3D version), sized to `header.grid_width` × `header.grid_height`. No heightmap, no terrain data — the sim's world is flat (design doc §6.2).
* **Entity markers are camera-facing billboards, not solid 3D primitives.** Use Panda3D's billboard effect (`node.set_billboard_point_eye()` or equivalent for this version) on a small flat quad (`CardMaker`) per entity, textured or vertex-colored with a simple glyph: a chevron/triangle for jets (rotated in-plane toward heading), a square for `HARDENED_BUNKER`, a diamond for `MOBILE_RADAR`, a circle-with-slash for `SAM_SITE` (direct reference to the image's hostile-target marker), a small hex/pylon glyph for jammers. If drawing custom glyph textures is more effort than this phase warrants, flat-colored geometric quads (triangle/square/diamond/circle shapes built directly from vertices, no texture) are an acceptable simplification for this phase — get the *silhouette* right; texturing/glow shader polish can come later without changing any other phase's contract.

```python
"""Builds and updates the 3D scene graph from TraceHeader/TraceTick data.
Coordinate mapping: sim (x, y) -> Panda3D (x, y), with z reserved for a
synthetic, presentation-only flight altitude -- see design doc §6.1-6.2.
No gameplay logic reads z; it is purely cosmetic.
"""

JET_ALTITUDE = 8.0  # world-units above the ground plane; presentation only

def build_static_scene(render_root, header: "TraceHeader") -> "SceneHandles":
    """Called once, when the header first arrives. Builds:
    - a ground plane sized to header.grid_width x header.grid_height
    - one placeholder primitive per target_id (shape/color keyed by
      header.target_types[target_id] -- see design doc §6.3 table)
    - a translucent disc at header.sam_engagement_range around each SAM site
    - one placeholder primitive per jammer position, + a translucent disc
      at header.jam_radius
    - one placeholder primitive per jet_id, positioned at its position in
      TraceHeader.initial_payload's keys (i.e. header.jet_ids), NOT yet
      knowing a real position -- initial jet position is not in the header
      (Phase 11 deliberately never added it there, only target/jammer
      positions are static-for-the-run and thus header fields; jet position
      is a per-tick field). Place jets at a sensible default (e.g. the
      world center, or hidden/scaled-to-zero) until the first TraceTick
      arrives with real positions -- do not block scene construction on
      having a tick yet, since mission-brief mode (Phase 17) needs a
      header-only scene with no jets positioned at all, or jets simply
      omitted until tick 0 arrives.
    Returns a small dataclass/dict of node references (SceneHandles) that
    update_tick() below needs to move/recolor/show/hide later.
    """
    ...


def update_tick(handles: "SceneHandles", header: "TraceHeader", tick: "TraceTick") -> None:
    """Called once per received TraceTick. Moves each jet node to its new
    (x, y, JET_ALTITUDE) position, updates each target's remaining-HP-driven
    appearance (see design doc §6.3: destroyed -> grey wireframe outline),
    removes any jet node whose agent_id is no longer present in tick.jets
    (destroyed -- no tombstone, matching Phase 11's TraceTick semantics
    exactly), and orients each jet to face its heading (derived from the
    displacement between its previous and current position; if this is the
    jet's first-ever tick, default to a fixed heading, e.g. facing +y).
    """
    ...
```

Fill in both functions per the design doc §6.3's revised billboard-glyph table (not solid shapes). Use `CardMaker` + the billboard effect for each entity's glyph quad — pick whichever concrete construction is simplest and reliable in the installed Panda3D version; document your choice with a one-line comment, since Phase 17/18 will extend this file and need to know the pattern already established here. Keep the destroyed-target-outline and jet-removal behavior exactly analogous to `viewer/render.py`'s Phase 12/13 rules — re-read that file for the precise semantics if unsure (destroyed → dim grey outline glyph in place; destroyed jet → removed entirely, no tombstone).

## Part D — `viewer3d/app.py` (new file, minimal skeleton)

```python
"""python -m viewer3d.app <host> <port>

Minimal skeleton for this phase: connects, builds the static scene the
moment a header arrives, and updates it once per frame by draining
whatever tick events have queued up since the last frame. One fixed
camera. No HUD, no camera modes, no editor -- see Phase 17+.
"""

import sys

from direct.showbase.ShowBase import ShowBase

from viewer3d.scene import build_static_scene, update_tick
from viewer3d.stream_client import DisconnectedEvent, EndEvent, HeaderEvent, StreamClient, TickEvent


class WraithNetViewer3D(ShowBase):
    def __init__(self, host: str, port: int) -> None:
        super().__init__()
        self.disable_mouse()  # replaced by a real camera controller in Phase 17
        self.camera.set_pos(50, -80, 60)
        self.camera.look_at(50, 50, 0)

        self._client = StreamClient(host, port)
        self._client.start()
        self._header = None
        self._handles = None
        self._latest_tick = None

        self.task_mgr.add(self._drain_queue_task, "drain-stream-queue")

    def _drain_queue_task(self, task):
        while not self._client.events.empty():
            event = self._client.events.get_nowait()
            if isinstance(event, HeaderEvent):
                self._header = event.header
                self._handles = build_static_scene(self.render, self._header)
            elif isinstance(event, TickEvent):
                self._latest_tick = event.tick
                if self._handles is not None:
                    update_tick(self._handles, self._header, event.tick)
            elif isinstance(event, (EndEvent, DisconnectedEvent)):
                pass  # Phase 17's HUD will surface this; for now, no-op
        return task.cont


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: python -m viewer3d.app <host> <port>")
        sys.exit(1)
    app = WraithNetViewer3D(sys.argv[1], int(sys.argv[2]))
    app.run()


if __name__ == "__main__":
    main()
```

This is intentionally close to final for this phase — Phase 17 will replace the fixed camera with `viewer3d/cameras.py` and add `viewer3d/hud.py`, but the queue-draining task structure established here should not need to change shape, only grow.

## Part E — Tests

Panda3D's `ShowBase` opens a real window by default, which is awkward in CI/headless environments. Follow the same pattern Phase 12's `tests/test_viewer_smoke.py` used for Pygame (`SDL_VIDEODRIVER=dummy`) — Panda3D supports an offscreen/headless windowless config (research the exact `PandaSystem`/`loadPrcFileData` setting for the installed version, e.g. `load_prc_file_data("", "window-type none")` or `"window-type offscreen"`) and document whichever one you confirm actually works in this environment before relying on it in tests.

### `tests/test_viewer3d_stream_client.py` (new file, Panda3D-free)

* `StreamClient` connects to a real `LiveStreamServer` (from Phase 15, run in-process via `asyncio` in a background thread/loop for the test, or reuse whatever test harness Phase 15's `tests/test_live_stream.py` already established) and its `events` queue receives, in order: one `HeaderEvent`, then one `TickEvent` per tick sent, matching the server's actual data.
* Connect-retry: point a `StreamClient` at a port with nothing listening yet, start it, then start a `LiveStreamServer` on that port ~1 second later — assert the client still successfully connects and receives events (proves the retry loop works, not just that a live connection works).
* Exhausted-retry: point a `StreamClient` at a port that never gets a listener, with a small `connect_retries`/`retry_delay_s`, and assert a `DisconnectedEvent` eventually appears on the queue within a bounded time (do not let this test hang).
* This test file imports nothing from `panda3d` or `direct` — verify with `grep`.

### `tests/test_viewer3d_smoke.py` (new file, needs Panda3D + headless config)

* `pytest.importorskip("panda3d")` at the top, mirroring Phase 12's `pytest.importorskip("pygame")` convention.
* Using the headless windowless mode you confirmed in this Part's preamble, instantiate the app against a real `LiveStreamServer`/`WraithNetBridge` run (a handful of ticks is enough), tick the Panda3D task manager a few times (`app.task_mgr.step()` or equivalent), and assert no exception is raised and `self._handles`/`self._header` end up populated as expected.
* A dedicated test for `viewer3d/scene.py::build_static_scene`/`update_tick` against a synthetic `TraceHeader`/`TraceTick` (constructed directly, not from a real running sim) that exercises: a destroyed target rendering differently than an alive one, and a jet present in tick N but absent in tick N+1 actually being removed from the scene (query the relevant Panda3D node count/visibility before and after, however this Panda3D version exposes that).

## Explicit non-goals for this phase

* No camera modes, no mouse/keyboard controls beyond whatever `ShowBase` gives for free — one fixed camera position, full stop. Phase 17.
* No HUD/overlay text at all. Phase 17.
* No mission-brief cinematic behavior — a header with zero ticks yet should still build and show a valid (if static) scene, but no scripted camera motion is required in this phase.
* No scenario editor, no `viewer3d/editor.py` — Phase 18.
* No real 3D models/textures — placeholder primitives only (design doc §6.3, §10).
* No change to `wraithnet/` at all in this phase — `viewer3d/` only reads from the Phase 15 live-stream protocol; it must not need any new field or method added to `wraithnet/trace_schemas.py`, `live_stream.py`, or elsewhere. If you find yourself needing one, stop and re-read design doc §6 — everything needed already exists.

## Acceptance criteria

1. `pytest` passes, including both new test files (the Panda3D-dependent one via `importorskip`, so it's skippable on a machine without `requirements-3d.txt` installed — verify this by confirming `tests/test_viewer3d_stream_client.py` alone passes with Panda3D **not** installed).
2. `python -m wraithnet.main --live` (in one process/terminal) followed by `python -m viewer3d.app 127.0.0.1 8765` (in a second) results in a window showing a static 3D scene that populates once the header arrives and visibly updates (jet positions move, a target eventually disappears/turns to outline) as the sim progresses — confirm this manually if you have a display available; if not, rely on the headless smoke test in Part E as the closest available proxy and note in your final summary that manual visual confirmation is still recommended.
3. `grep -rn "import panda3d\|from panda3d\|from direct" viewer3d/stream_client.py` returns no matches.
4. `grep -rn "panda3d" wraithnet/ requirements.txt requirements-dev.txt requirements-viz.txt` returns no matches — the core simulation and the existing 2D viewer remain completely unaware this package exists.
