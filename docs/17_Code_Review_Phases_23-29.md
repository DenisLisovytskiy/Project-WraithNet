# Code Review — Phases 23–29

## Verdict

**Conditional pass: fourteen corrections are needed.** The implementation is broadly complete and the source/frozen headless paths reproduce the established default-scenario baseline, but passing tests conceal several visual-state and launcher/output defects. The most serious additional issue is that cosmetic takeoff progression can remain near ground level when streamed ticks arrive in a burst.

Scope reviewed:

* Phase 23: evasion, persistent jet outcomes, metrics, and termination.
* Phase 24: trace extensions, both viewers, and scenario-editor updates.
* Phases 25–26: cosmetic altitude/attitude and weapon/destruction effects.
* Phase 27: automated GUI test extraction and dependency-isolation checks.
* Phases 28–29: unified launcher, frozen worker dispatch, writable paths, and PyInstaller packaging.

The review covered the phase/design documents, current implementation, related tests, acceptance greps, source compilation, the complete pytest suite, and the existing frozen build.

## Findings

### 1. High — RTB highlighting occurs at landing, not when RTB begins

Phase 24 requires `newly_rtb_jet_ids` to represent the transition into the `"RTB"` role so viewers flash the moment a jet detaches from its task and starts flying home. Both highlight implementations instead derive it from `JetOutcomeEvent(outcome="returned_to_base")`:

* `viewer3d/highlights.py::compute_highlights()`
* `viewer/playback.py::compute_highlights()`

`JetOutcomeEvent` is emitted only when the world removes a jet after it reaches home. The visual notification therefore appears at landing—potentially many ticks late, when the jet also disappears—instead of at RTB initiation. The current tests encode this incorrect landing-event behavior rather than constructing a previous/current pair whose role changes from a non-RTB value to `"RTB"`.

**Correction:** detect `previous JetFrame.role != "RTB"` → `current JetFrame.role == "RTB"` directly from adjacent ticks. Keep `JetOutcomeEvent` exclusively for the distinct final outcome/removal accounting. Add equivalent transition tests for both 2D and 3D highlight implementations.

### 2. Medium — `model_vars.csv` still loses distance from removed jets

Phase 23 correctly fixed `metrics.summarize()` by combining live jets with `model.jet_outcomes`, but the `DataCollector` reporter in `wraithnet/world.py` still sums only live jets:

```python
"TotalDistanceTraveled": lambda m: sum(
    j.distance_traveled for j in m.jets.values()
),
```

As soon as a jet is destroyed or returns to base, its accumulated distance disappears from the next `model_vars.csv` row. This makes the exported time series decrease and disagree with the console/final summary—the same class of removed-jet metrics bug Phase 23 was intended to eliminate.

Reproduced directly by setting `Jet_1.distance_traveled = 12.0`, landing it, and collecting the next tick:

```text
DataCollector final row: TotalDistanceTraveled = 0.0
JetOutcome snapshot:     distance_traveled = 12.0
```

There is no test mentioning `TotalDistanceTraveled`, so the full suite does not catch this.

**Correction:** include both live and outcome snapshots in the reporter, preferably through one shared model helper used by both `world.py` and `metrics.summarize()`. Add a regression test that removes a jet after it has moved and asserts the model time series remains cumulative/non-decreasing.

### 3. Medium — the editor silently drops `AMRAAM` and `GUN_POD` payloads

The simulation catalog and scenario schema support four weapons, but the editor's jet form exposes only `JDAM` and `AIM9X`. Applying an edit reconstructs and overwrites the entire payload dictionary from those two fields. Loading a valid scenario containing `AMRAAM` or `GUN_POD`, editing that jet, and applying the form therefore silently deletes those weapons.

This violates the editor's role as a round-trip-safe scenario editor and makes the Phase 21 weapon additions only partially customizable through the GUI. Existing editor tests cover non-default airframes and slot usage but never load/edit/save a payload containing either newer weapon.

**Correction:** generate payload controls from `WEAPON_CATALOG` (or at minimum add all current catalog entries), preserve unknown/future keys when applying unrelated changes, and add a load → edit → save round-trip regression containing both `AMRAAM` and `GUN_POD`.

### 4. Medium — Phase 28 duplicates and narrows the headless CLI parser

`docs/phases/Phase_28_Unified_Launcher_and_Sim_Worker_Dispatch.md` explicitly says the launcher must capture tokens after `--headless` and forward them verbatim to `wraithnet.main` so there is only one parser/source of truth. Instead, `launcher/cli.py::_parse_headless()` reimplements the four options manually, and `launcher/dispatch.py::_headless_argv()` reconstructs them.

That already causes observable drift from the underlying `argparse` CLI:

* `python -m launcher --headless --help` exits with the launcher's generic usage and status 1 instead of showing `wraithnet.main` help.
* `python -m launcher --headless --scenario=scenarios/default.json` is rejected, although `argparse` accepts the standard `--option=value` form.
* Future flags added to `wraithnet.main` will remain unavailable through the canonical launcher until a second parser and adapter are also updated.

Both failures were reproduced during this review. Existing tests only exercise the hand-written subset, so they confirm the duplicate implementation rather than guarding the intended forwarding contract.

**Correction:** store the untouched tail as `role_argv: tuple[str, ...]` (or equivalent) in `LauncherArgs` and pass it directly to `wraithnet.main.main(list(role_argv))`. Keep parsing only the first role selector in `launcher/cli.py`. Add tests for `--help`, `--scenario=...`, and an arbitrary future token reaching the delegated parser unchanged.

### 5. Medium — Phase 29's mandatory frozen-GUI acceptance flow was not actually performed

`packaging/BUILDING.md` provides useful evidence and the frozen worker itself is valid, but its verification log explicitly says:

* the scenario file was copied into `%LOCALAPPDATA%` rather than edited and saved by clicking the frozen editor's **Save** control; and
* the frozen worker argv was spawned directly rather than clicking the frozen editor's **Launch** control and observing the resulting live scene.

Those substitutions validate the lower-level pieces but not the required integrated path:

```text
frozen editor → Save → Load → Launch → child WraithNet.exe → live ticks
```

Phase 29 states that these five manual checks are mandatory and may not be replaced by claims that the components should compose. Consequently, Phase 29 cannot yet be marked fully accepted even though the packaged executable's headless role works.

**Correction:** perform the missing actions against `dist/WraithNet/WraithNet.exe`: edit a field, click Save, change/reload it, click Launch, and visually confirm ticks enter the 3D scene. Record the observed file paths, child process, and result in `packaging/BUILDING.md`.

### 6. Low — a jet that strikes and is destroyed in the same tick gets no weapon effect

World ordering permits this: intents (including strikes) resolve before SAM damage. The resulting `TraceTick` can therefore contain both a `StrikeEvent` for a jet and a `JetOutcomeEvent(outcome="destroyed")`, while that jet is absent from `tick.jets`.

`viewer3d/app.py::_spawn_weapon_effects()` looks up launch positions only in `tick.jets`:

```python
jets_now = {j.agent_id: j for j in tick.jets}
frame = jets_now.get(strike.agent_id)
if frame is None:
    continue
```

The valid strike is silently skipped. `apply_tick_highlights()` already demonstrates the correct approach by merging previous/current jet positions before handling strike highlights.

**Correction:** fall back to the matching frame from `self._previous_tick` (or a persistent last-known-position map) when the striker is absent from the current tick. Add a test containing a strike and destroyed outcome for the same jet on one tick.

### 7. High — takeoff progression can freeze near ground level when ticks arrive in a burst

`viewer3d/app.py::_drain_events()` consumes every queued tick in one frame. `seconds_airborne` and the desired altitude are recalculated only while a tick is being processed; the per-frame task subsequently eases toward the last stored target but never recomputes that target as wall-clock time advances.

If a short simulation is already queued—particularly the late-connect/backfill case the live protocol explicitly supports—all ticks are assigned almost the same `time.monotonic()` value. The final desired altitude can remain near ground level forever after the queue empties, violating Phase 25's requirement that takeoff animation remain consistent regardless of simulation speed. The current live smoke test only requires a 0.05-unit altitude change and does not exercise a completed backlog.

**Correction:** update the time-dependent takeoff target per rendered frame, or base takeoff progression on a stable simulation-time/tick mapping rather than queue-drain wall-clock timestamps. Add a test that queues an entire run before one frame drain, then advances render frames without additional ticks and verifies the jet reaches cruise altitude.

### 8. Medium — a nearby target raises a newly spawned jet above ground immediately

`desired_altitude()` interpolates from the takeoff baseline toward `ATTACK_ALTITUDE=2.0`. At `seconds_airborne=0`, the baseline is `GROUND_ALTITUDE=0`, so a nearby target raises the result above ground (about `1.67` at distance `0.5`) before takeoff has begun.

**Correction:** ensure the proximity/attack target cannot exceed the current takeoff baseline—for example, interpolate toward `min(ATTACK_ALTITUDE, baseline)`. Add a near-target assertion at exactly `seconds_airborne=0`.

### 9. Medium — JDAM animation waypoints can pass below the ground plane

`viewer3d/effects.py::_spawn_bomb()` subtracts fixed offsets from both midpoint altitudes without clamping. With a normal attack altitude near `2.0` and impact altitude `0.25`, the computed waypoints are below zero, so a significant part of the bomb path runs underground.

**Correction:** clamp intermediate Z values above the impact/ground altitude or use a monotonic downward curve from launch altitude to impact altitude. Add an archetype-specific test that samples the interval and asserts every pre-impact waypoint remains above ground.

### 10. Medium — delayed wreck cleanup can remove a jet from a replacement scene

The wreck interval's completion lambda calls `self._finish_removing_jet()`, which dereferences the mutable current `self._handles`. If a new header/preview/relaunch replaces the scene during the 0.9-second wreck interval, the old callback can remove a same-ID jet from the new scene.

**Correction:** capture the originating `SceneHandles` (or exact old node) in the callback and clean only that scene. Add a regression that replaces the header while a wreck is pending and verifies the replacement jet remains.

### 11. Low — missile effects omit the required engine trail

Phase 26 specifies a short fading trail, but `_spawn_missile()` creates only a billboard disc and positional interval. `LineSegs` is used only for gun tracers.

**Correction:** attach/update a short trail behind the projectile and test its creation and cleanup independently from the missile glyph.

### 12. Low — weapon-specific effects are overlaid with a generic instantaneous beam

Every strike invokes both `_spawn_weapon_effects()` and legacy `apply_tick_highlights()`. The latter draws an immediate attacker-to-target beam for every weapon, so bombs and missiles appear together with a gun-like instantaneous line that undermines the distinct travel effects Phase 26 introduced.

**Correction:** reserve the beam for gun/tracer weapons, or suppress legacy strike beams when a weapon-specific effect is spawned.

### 13. Medium — Phase 29 regresses subprocess result isolation

`viewer3d/launch.py` still creates a unique temporary working directory for every simulation worker, with comments stating this prevents concurrent launches from colliding over `results/trace.jsonl`. Phase 29 changed `wraithnet.main` to ignore that working directory and always write to one absolute `user_data_dir("results")`.

Consequently, concurrent headless runs or separate GUI instances now share and overwrite:

* `trace.jsonl` (opened with mode `"w"`, so the later process truncates the earlier process's trace);
* `model_vars.csv`;
* `agent_vars.csv`.

The existing concurrent-launch test checks only that both network streams connect, not that their recorded outputs remain intact.

**Correction:** allocate a per-run result directory or unique run identifier and pass it explicitly to the worker (for example, `--output-dir`). The GUI can retain the worker's resolved output location if it needs to expose results later. Add a concurrent-run regression asserting distinct, internally valid trace and CSV sets.

### 14. Low — the launcher accepts a pathless replay invocation that always fails

`launcher.cli` advertises and accepts `python -m launcher --replay` with no trace path, then `launcher.dispatch` forwards an empty list to `viewer.app.main()`. The replay viewer requires exactly one path and immediately exits with usage status 1. This makes the launcher's own accepted syntax internally inconsistent even in a source checkout.

**Correction:** require a path at launcher-parse time, or define and implement a real default trace path such as `user_data_dir("results") / "trace.jsonl"`. Add an end-to-end CLI test for the chosen behavior.

## Non-blocking hardening

* `packaging/requirements-build.txt` uses unbounded `pyinstaller>=6.0`, while the spec imports PyInstaller internals and the only verified version is 6.22.2. Pin `pyinstaller==6.22.2` (or at least `>=6.22.2,<7`) and use a packaging constraints file for the verified Panda3D version if reproducible future builds matter.
* `viewer3d/launch.py` creates a new `tempfile.mkdtemp(prefix="wraithnet-launch-")` directory for every GUI launch but never removes it. Store the path on `SimLaunchHandle` and clean it after process termination/exit. Finding 13 means the directory no longer even provides the result isolation its comment uses to justify it.
* The frozen build intentionally excludes Pygame, but the same launcher usage still advertises `--replay <trace>`; that frozen mode fails at runtime even after the pathless-source bug in Finding 14 is fixed. Either omit replay from frozen help with an explicit message or include the optional dependency.
* Phase 24's newly required trace fields use compatibility defaults (`home_base`, heading, fuel, and catalog maps). That keeps old traces loadable, but it also lets a current producer silently omit required data and display plausible-but-false defaults. Consider an explicit trace-schema version with strict current-version validation while retaining a deliberate legacy-upgrade path.
* Phase 26's effect test spawns every archetype together and uses a predicate ending in `or n`, which accepts any non-empty child name. Split it into per-archetype tests and cover the unknown-weapon fallback, missile trail, bomb altitude, and individual lifetimes.
* Phase 25 scene tests assert heading but not bank/roll or absence of billboarding. Phase 27 chase-camera tests assert camera position but not that it actually looks at the selected jet. Add those acceptance assertions.

## Phase-by-phase assessment

### Phase 23 — Evasion, metrics, and termination

The core implementation is strong:

* deterministic SAM damage reduction is catalog-driven and floored at zero;
* every removal path goes through `_remove_jet()` and records a `JetOutcome`;
* `all_jet_ids()` enforces the live/outcome partition;
* `mission_aborted` and `swarm_wiped_out` are mutually exclusive;
* `summarize()` reports the three-way jet split and includes removed jets.

The remaining `DataCollector` defect is Finding 2.

### Phase 24 — Trace, viewer, and editor updates

The implementation satisfies the required scope:

* header/tick schemas carry home base, airframe data, weapon range/arc data, heading, fuel, mission outcome, and per-tick jet-outcome events;
* recorder and live server maintain independent seen-outcome sets, so events are emitted once per sink rather than cumulatively;
* both viewers expose heading, fuel, outcome state, and home base;
* the editor supports frame selection, a single movable home base, and live ordnance-slot feedback;
* the optional 2D firing-arc overlay was legitimately skipped—the phase document explicitly marks it optional.

RTB transition timing and incomplete weapon editing remain incorrect (Findings 1 and 3).

### Phase 25 — Cosmetic 3D altitude and attitude

The pure `flight_dynamics.py` split, non-billboarded jet dart, per-frame easing, real-heading orientation, target-proximity dive, and post-strike boost are structurally present, and no gameplay layer imports cosmetic altitude. Burst timing and initial proximity behavior remain incorrect (Findings 7–8).

### Phase 26 — Weapon and destruction effects

All four weapon types map to sensible archetypes, unknown weapons have a safe missile fallback, interval-driven nodes self-clean, target destruction effects exist, and destroyed jets use deferred wreck removal while RTB exits remain immediate. Findings 6 and 9–12 cover the remaining event/trajectory/lifecycle/presentation defects.

### Phase 27 — Automated GUI testing

The extraction into `viewer/controls.py`, `viewer/hud_text.py`, and argv helpers is behavior-preserving and covered at the intended pure/headless layers. Dependency-isolation checks are now executable tests rather than review-time greps. No blocking defect was found.

### Phase 28 — Unified launcher and frozen worker dispatch

The default GUI route, replay route, frozen/non-frozen worker argv split, deferred GUI imports, and optional-argv entry-point changes are present. The frozen child uses the same executable without `-m`, which is correct. The headless parser duplication is Finding 4.

### Phase 29 — Paths and packaging

`wraithnet.paths`, its runtime call sites, the onedir PyInstaller recipe, Panda3D PRC/DLL handling, exclusions, build helper, and documentation are all present. The local build contains the required files and its headless mode was independently rerun successfully during this review. The missing integrated GUI acceptance pass is Finding 5; shared output-path collisions are Finding 13.

## Verification results

### Full test suite

```text
385 passed, 3 skipped, 1 warning in 396.07s
```

The three skips are all optional-Pygame tests because Pygame is not installed in the current interpreter. Panda3D tests ran. The sole Mesa warning comes from an existing test inspecting an empty `AgentSet`; no failure resulted.

### Static checks

* `git diff --check`: clean.
* `python -m compileall -q launcher viewer viewer3d wraithnet tests`: clean.
* IDE diagnostics: no errors.
* Removed-name/path acceptance searches: no stale `jets_survived` in Python code and no hardcoded `results/` or `scenarios/custom.json` in the migrated runtime call sites.

### Frozen build

The existing build contains:

* `dist/WraithNet/WraithNet.exe`;
* `_internal/panda3d/etc/Config.prc`;
* `_internal/panda3d/libpandagl.dll`;
* `_internal/panda3d/libp3windisplay.dll`.

An independent `dist/WraithNet/WraithNet.exe --headless` run completed successfully:

```text
Simulation finished at tick 32
Solved: True (3/3 targets destroyed)
Jets destroyed: 0 / returned to base: 0 / still flying: 3
Total distance traveled: 222.67
Total missiles fired: 7 {'JDAM': 6, 'AIM9X': 1}
Total jammed-agent-ticks: 5
```

It wrote results to `%LOCALAPPDATA%\WraithNet\results`, confirming the frozen writable-path branch.

## Recommendation

Fix Findings 1–14 before declaring all phases through 29 complete. Findings 1–4, 7–10, and 13 are product/code correctness or API-contract issues; Finding 5 is a required acceptance step; Findings 6 and 11–12 are visible effect defects; Finding 14 is a launcher UX/API inconsistency. After those corrections, strengthen the noted tests, rerun the full suite, test concurrent output isolation, and perform the frozen GUI Save/Load/Launch flow.
