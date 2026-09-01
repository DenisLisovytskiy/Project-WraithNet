# Phase 17 — Camera Modes, HUD & Mission Brief Mode

> **How to use this document:** This is a complete, standalone prompt. Give an LLM coding agent this entire file as its task. Assume Phases 0–16 are complete and passing (`viewer3d/app.py` connects to a live stream, builds a static 3D scene from the header, and updates it per tick, with one fixed camera and no HUD). Read `docs/09_3D_Live_Visualization_and_Scenario_Editor_Design_Document.md` §6.4, §6.5, and §7 in full before starting. This phase adds the "Ace Combat" camera feel, a real HUD, event highlights, and the mission-brief/live-tactical mode handoff — no scenario editor yet (Phase 18).

## Context

Phase 16 proved the data pipe works but is deliberately bare: one fixed camera, no overlay, no sense of "which mode am I in." This phase makes `viewer3d` feel like an actual viewer: three camera modes (free orbit, chase/follow, cinematic mission-brief), a HUD, event flashes for strikes/destructions/jam transitions, and the automatic handoff between "watching a scenario that hasn't started yet" and "watching it live."

## Part A — `viewer3d/cameras.py` (new file)

Three camera controllers, all operating on the same `ShowBase.camera` node, switchable at runtime:

```python
"""Camera controllers for viewer3d. All three read jet/target positions
from the same SceneHandles Phase 16 already builds/updates -- no new scene
data is needed, only new ways of pointing a camera at it.
"""

class OrbitCamera:
    """Mouse-drag orbits around the map's center at a fixed radius/tilt.
    Default mode once live-tactical mode is active with no jet selected."""
    def __init__(self, base, center=(50.0, 50.0, 0.0), radius=90.0): ...
    def update(self, dt: float) -> None: ...  # reads mouse delta via base.mouseWatcherNode


class ChaseCamera:
    """Trails behind and above a selected jet, banking toward its heading.
    The explicit 'Ace Combat' reference -- see design doc §6.4."""
    def __init__(self, base, follow_distance=12.0, follow_height=5.0): ...
    def update(self, dt: float, jet_position, jet_heading) -> None: ...


class MissionBriefCamera:
    """Slow, scripted orbit/pan over the whole map. Active automatically
    whenever a header has been received but zero ticks have arrived yet --
    see design doc §7. Purely time-driven, needs no player input."""
    def __init__(self, base, center=(50.0, 50.0, 0.0), radius=70.0, angular_speed_deg_s=6.0): ...
    def update(self, dt: float) -> None: ...
```

Fill in each `update` method using standard Panda3D camera math (`self.camera.set_pos(...)`, `.look_at(...)`, trigonometry against elapsed time/mouse delta). Keep each class's public surface exactly as sketched (an `__init__` and an `update(dt, ...)` called once per frame) — `viewer3d/app.py`'s per-frame task (Part D) needs a uniform interface to call whichever camera is currently active without caring which one it is.

**Camera switching**: a small enum/string (`"orbit" | "chase" | "brief"`) tracked in `app.py`, changed via keybindings (`O` = orbit, `C` = chase-selected-jet, no direct keybinding for `brief` — it is only ever entered automatically, per Part C). Selecting a jet for chase mode: clicking on a jet's 3D node (Panda3D's `collision`/mouse-picking, or simpler — cycling through `header.jet_ids` with a `Tab`/`N` hotkey if full 3D mouse-picking proves too fiddly to get right reliably; pick whichever you can make robust and document the choice).

## Part B — `viewer3d/hud.py` (new file)

`DirectGUI`/`OnscreenText`-based overlay, rendered on Panda3D's 2D `aspect2d` layer (always screen-aligned regardless of the 3D camera). **Visual style target**: `docs/09_..._Design_Document.md` §6.3/§6.5 has a concrete reference image (Ace Combat's mission-briefing HUD: small, corner-anchored panels using a `[ BRACKETED LABEL ]` + monospace-value convention, not one dense sidebar like the 2D viewer's `viewer/render.py::draw_hud`). Build four independent panels, not one combined block:

```python
"""2D HUD overlay for viewer3d: four corner-anchored panels styled after the
Ace Combat mission-briefing reference (design doc §6.3/§6.5), each using a
'[ BRACKETED LABEL ]' + monospace-value convention. Distinct from
viewer/render.py's single-sidebar layout by design -- see design doc §6.5.
"""

class Hud:
    def __init__(self, base) -> None:
        # Build four DirectFrame/OnscreenText groups once, positioned at the
        # four screen corners via aspect2d anchoring: top-left (mission
        # info), top-right (force status table), bottom-right (coordinates
        # + minimap), bottom-left (camera-mode indicator + key bindings).
        ...

    def update(
        self,
        mode: str,                    # "connecting" | "brief" | "live" | "finished"
        header: "TraceHeader | None",
        latest_tick: "TraceTick | None",
        camera_mode: str,             # "orbit" | "chase" | "brief"
        selected_jet_id: "str | None",
        disconnect_reason: "str | None" = None,  # from DisconnectedEvent, Phase 16
    ) -> None:
        self._update_mission_info(mode, header, latest_tick, disconnect_reason)   # top-left
        self._update_force_status(header, latest_tick)                            # top-right
        self._update_coordinates_and_minimap(header, latest_tick, selected_jet_id) # bottom-right
        self._update_controls(camera_mode)                                        # bottom-left
        if latest_tick is not None and not latest_tick.running:
            self._show_end_of_run_summary(header, latest_tick)  # overlays on top of the four panels
```

Fill in each `_update_*` helper (updating existing text-node content in place — no per-frame node creation/destruction):

* **Mission info (top-left)**: `[ SCENARIO ]`, `[ STATUS ]` (surfacing `DisconnectedEvent`/`EndEvent` from Phase 16 — Phase 16 deliberately left this a silent no-op; this phase is where it's finally surfaced, not skipped), `[ TICK ]` current/total-so-far.
* **Force status (top-right)**: a small bracketed table analogous to the reference's `[ ALL ENEMIES ]` box — targets remaining by type, jets remaining — the same underlying counts `viewer/render.py`'s "Targets X/Y destroyed"/"Jets X/Y alive" lines already compute, just laid out as a table instead of prose.
* **Coordinates + minimap (bottom-right)**: `[ COORDINATES ]` of the selected/followed jet (chase mode) or the orbit camera's focus point (orbit mode), plus a small top-down radar-style minimap. Implement the minimap as either (a) a second, tiny orthographic `Camera`/`DisplayRegion` looking straight down at the scene (reuses the exact same node graph Phase 16 already built — no new geometry needed, just a second viewport), or (b) a hand-drawn 2D dot-plot (`DirectGUI`/`OnscreenImage`-based) computed directly from `latest_tick`'s jet/target positions if a second viewport proves fiddly in this Panda3D version. Pick whichever is more reliable and document the choice — (a) is preferred since it needs no new position math, but (b) is an explicitly sanctioned fallback.
* **Controls (bottom-left, not in the reference image but needed for usability)**: active camera mode + key bindings, analogous to `viewer/render.py::draw_hud`'s existing "Controls" section.
* **End-of-run summary**: same headline numbers `viewer/render.py::draw_end_of_run_overlay` (Phase 13) shows — targets destroyed, jets lost, missiles fired by weapon type — computed by summing/diffing the ticks received so far in `app.py` (no new data needed — see Part D). Also reuse the same role→color mapping `viewer/render.py::ROLE_RING_COLOR` already established, for visual consistency between the 2D and 3D viewers even though they're otherwise independent codebases.

## Part C — Mode handoff logic (mostly in `viewer3d/app.py`)

This is the design doc §7 insight made concrete. `viewer3d/app.py`'s state now includes an explicit `view_mode`:

```python
# view_mode transitions:
#   "connecting"  -- no header yet
#   "brief"       -- header received, zero ticks received yet (MissionBriefCamera active)
#   "live"        -- at least one tick received (OrbitCamera or ChaseCamera active,
#                    whichever the user last chose; default OrbitCamera on first entry)
#   "finished"     -- an EndEvent (or a tick with running=False) has been received;
#                    camera stays in whatever live mode it was in, HUD shows the
#                    end-of-run summary on top
```

The transition `"brief" -> "live"` happens **exactly once**, the moment the first `TickEvent` is drained from the queue while `view_mode == "brief"` — at that instant, swap the active camera controller from `MissionBriefCamera` to `OrbitCamera` (do not abruptly snap the camera; a short interpolated blend between the two cameras' final/initial positions is a nice touch but not required — a hard cut is acceptable for this phase). No other transition needs special-casing beyond straightforward state tracking.

## Part D — Event highlights (strike/destruction/jam flashes)

> **Check first**: `docs/phases/Phase_26_Weapon_Effects_Missiles_Bombs_and_Gun_Tracers.md` (an independent, later-numbered phase that has no dependency on this one — see `docs/12_Flight_Dynamics_and_Weapon_Effects_Design_Document.md` §7) also needs exactly this `viewer3d/highlights.py` file, for spawning projectile/explosion effects instead of HUD flashes. If Phase 26 was implemented before this phase, `viewer3d/highlights.py` **already exists** with the shape below — do not create a second, competing copy; just import and use it.

`viewer/playback.py::compute_highlights` (Phase 13) operates on two already-fully-loaded `TraceTick` objects from a file. `viewer3d` instead receives ticks one at a time, live, from a queue — so this phase needs an equivalent that works incrementally:

```python
"""viewer3d/highlights.py (new file) -- incremental version of
viewer/playback.py::compute_highlights, adapted for a live stream where
ticks arrive one at a time rather than being pre-loaded into a list.
Deliberately NOT importing from viewer/ (viewer3d must not depend on the
Pygame viewer package) -- reimplement the small amount of comparison logic
directly against TraceTick, keeping the same field-level semantics
(newly_destroyed_target_ids, newly_destroyed_jet_ids, newly_jammed_jet_ids,
newly_unjammed_jet_ids, strikes) Phase 13 already validated are correct.
"""

def compute_highlights(previous: "TraceTick | None", current: "TraceTick") -> "TickHighlights":
    ...  # same shape/semantics as viewer/playback.py's version; see that file for the exact rules
```

`viewer3d/app.py`'s queue-draining task keeps a `self._previous_tick` reference (starts `None`) and calls `compute_highlights(self._previous_tick, event.tick)` each time a `TickEvent` is drained, then `self._previous_tick = event.tick`. Render the resulting highlights as brief 3D effects in `viewer3d/scene.py` (add functions there, e.g. `flash_strike(handles, strike)`, `pulse_destruction(handles, entity_id)`, `flash_jam_transition(handles, jet_id, jammed)`) — visually, these can be simple: a short-lived colored line/beam for a strike, a brief scale-pulse or color-flash on the affected node for destruction/jam transitions, faded out over a fixed short duration (e.g. using Panda3D's `Sequence`/`LerpFunc` intervals, or a simple frame-counted timer if you prefer not to depend on Panda3D's interval system — either is fine).

## Part E — Tests

### `tests/test_viewer3d_highlights.py` (new file, Panda3D-free)

* Directly port the equivalent test cases from `tests/test_viewer_playback.py` (Phase 13) against `viewer3d/highlights.py::compute_highlights`: `previous=None` produces no false positives; a strike present in `current.strikes` is reported; a target/jet transitioning to destroyed is detected; a jet's jammed flag transitioning `False→True`/`True→False` is detected correctly in each direction. This file must not import `panda3d`/`direct` — verify with `grep`.

### `tests/test_viewer3d_smoke.py` additions (extends Phase 16's file)

* Using the same headless windowless Panda3D config Phase 16 established, drive a full short run through the app (a `LiveStreamServer`-backed `WraithNetBridge` run of a handful of ticks) and assert: `view_mode` starts at `"connecting"`, becomes `"brief"` once the header is drained (before any tick has arrived — you can force this by draining the queue once with only the header event enqueued), then becomes `"live"` the moment the first tick is drained, and becomes `"finished"` once an `EndEvent` (or a `running=False` tick) is drained. This is the single most important test in this phase — it's the concrete proof that design doc §7's "mission brief is just header-with-no-ticks" claim actually behaves correctly in code, not just in the design's prose.
* Camera switching: forcing `view_mode="live"` and toggling between `"orbit"`/`"chase"` (with a selected jet set) doesn't raise, and `ChaseCamera.update` is actually invoked with the currently-tracked jet's real position (assert via a spy/mock or by checking the camera node's resulting transform changed plausibly, whichever is easier to assert reliably in this Panda3D version).
* HUD: `Hud.update(...)` called across a `"connecting" -> "brief" -> "live" -> "finished"` sequence of states doesn't raise, and its text nodes' content actually changes between at least two of those states (a trivial "not literally frozen" check, mirroring the spirit of the Phase 13 rich-fixture smoke test). Additionally assert each of the four corner panels (mission info, force status, coordinates+minimap, controls — Part B) is present and independently non-empty once `mode="live"` with a real header/tick, and that `_update_mission_info` actually surfaces a `disconnect_reason` string when one is passed (proving Phase 16's previously-silent `DisconnectedEvent`/`EndEvent` handling is no longer a no-op).

## Explicit non-goals for this phase

* No scenario editor — Phase 18.
* No true 3D mouse-picking requirement — cycling selection via hotkey is an acceptable, explicitly sanctioned fallback if real picking proves unreliable (Part A).
* No camera-blend/interpolation requirement on the `"brief" -> "live"` handoff — a hard cut is acceptable (Part C).
* No persistence of camera-mode preference across runs/restarts — always starts in `"connecting"`/orbit-by-default, every time.
* No changes to `wraithnet/` or the Phase 15 streaming protocol — this phase only consumes what already exists.

## Acceptance criteria

1. `pytest` passes, including the new/extended test files.
2. Manually running `python -m wraithnet.main --live` then `python -m viewer3d.app 127.0.0.1 8765` shows: a cinematic pan over the map immediately upon connecting (before the sim has produced any ticks, if you can arrange to launch the viewer first — otherwise this may be too brief to notice in a single-terminal test run, which is fine), a visible hand-off to a live tactical camera once ticks start arriving, a working HUD, and visible flashes on strikes/destructions/jam transitions. Note in your final summary if a display wasn't available to confirm this manually, and rely on the headless smoke tests as the closest available proxy.
3. `grep -rn "import panda3d\|from panda3d\|from direct" viewer3d/highlights.py` returns no matches.
4. No change to any file outside `viewer3d/` and `docs/`/`tests/`.
