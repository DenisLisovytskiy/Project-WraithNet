# Code Review — Full Project (Phases 0–10, V1 + Phase 2 Expansion)

> Scope: this review covers the **entire** WraithNet codebase as it stands after Phases 9 (Electronic Warfare / Jamming) and 10 (Integration, Metrics & V2 Scenario Validation) were reported complete. It re-verifies everything from `docs/01_Code_Review_Phases_0-2.md`, `docs/02_Code_Review_Phases_3-5.md`, and `docs/05_Code_Review_Phases_6-8.md` still holds, confirms the fixes those documents recommended were actually applied, and audits Phases 9–10 fresh. One new, previously-undetected, project-wide bug was found and fixed during this review (§1).

## Method

* Read every module fresh: `config.py`, `schemas.py`, `world.py`, `broker.py`, `agents.py`, `bridge.py`, `metrics.py`, `main.py`.
* Read every test file (12 files, 130 tests) including the two new Phase 9/10 files (`test_electronic_warfare.py`, `test_scenario_v2.py`).
* Ran the full `pytest` suite twice (before and after the fix in §1): **130/130 passing**, ~175s.
* Ran `python -m wraithnet.main` twice (before and after the fix): identical simulation outcome both times, confirming the fix is side-effect-free for gameplay.
* Diffed live behavior against `docs/04_6th-Gen_Warfare_Sim_Design_Document_v3_Phase2.md` and all five `docs/phases/Phase_6..10_*.md` documents, line by line.
* Used a throwaway script to inspect Mesa 3.5.1's `DataCollector` internals (`collect()`, `get_agenttype_vars_dataframe()`) to root-cause §1 rather than guess at it.

## 1. Bug found and fixed this session: per-agent time-series metrics were silently collapsed to a single row

**Severity: significant.** This predates Phase 9/10 — it has existed since Phase 6 first added `agenttype_reporters`/per-agent `DataCollector` reporters — but was only surfaced now because this is the first review that inspected the actual exported CSV contents (not just column presence) across a full multi-tick run.

**Root cause.** `WraithNetModel.__init__` contains the (correct, necessary) Phase 1 workaround:

```python
self.step = self._user_step
```

This lets callers invoke `model.step(intents)` directly instead of Mesa's own no-arg scheduled `step()`. But Mesa's `Model.__init__` *also* sets up `self.steps = 0` / `self.time = 0.0`, which are normally advanced only by its internal `_wrapped_step()` → `_do_step()` machinery — the exact machinery this workaround bypasses. Nothing in `WraithNetModel.step()` ever touched `self.steps`/`self.time`, so both stayed frozen at `0`/`0.0` for the entire run.

Mesa's `DataCollector.collect()` uses `model.time` as the dict key for **agent-level** records:

```python
self._agenttype_records[model.time] = {...}   # mesa/datacollection.py
```

Since `model.time` never changed, every tick's `collect()` call overwrote the *same* dict entry (`0.0`) instead of adding a new one. `get_agenttype_vars_dataframe()` therefore returned only the **last** tick's per-jet snapshot, not a time series. (`model_reporters` were unaffected — `model_vars[var]` is a plain `list.append`, independent of `model.time` — which is why `model_vars.csv` always looked correct and this went unnoticed.)

**Empirical confirmation before the fix:** a full `python -m wraithnet.main` run (32 ticks) produced an `agent_vars.csv` with exactly **3 rows** (one per jet, all stamped `Step=0.0`), holding the *final* tick's `DistanceTraveled`/`TotalPayloadRemaining` values, not tick 0's.

**Fix applied** (`wraithnet/world.py::WraithNetModel.step`):

```python
def step(self, intents: dict[str, ActionIntent] | None = None) -> None:
    intents = intents or {}
    self.tick += 1
    self.steps = self.tick
    self.time = float(self.tick)
    ...
```

**Verified after the fix:** the same run now produces **99 rows** (33 ticks × 3 jets) in `agent_vars.csv`, with `DistanceTraveled`/`TotalPayloadRemaining` correctly progressing tick-over-tick (e.g. `Jet_2` climbs `0.0 → 5.0 → 10.0 → ...`). Console summary output and `model_vars.csv` are byte-for-byte identical to the pre-fix run (tick 32, distance 222.67, 7 missiles `{"JDAM": 6, "AIM9X": 1}`, 5 jammed-agent-ticks, 0 jets destroyed) — confirming the fix only corrects Mesa's internal bookkeeping and has zero effect on simulation logic or the already-tested `summarize()` metrics (which read live model state, not the DataCollector).

Added a regression test, `test_step_advances_mesa_time_so_agenttype_records_are_not_collapsed` (`tests/test_world.py`), asserting `model.time`/`model.steps` advance each tick and that `get_agenttype_vars_dataframe(JetAgent)` contains one distinct `Step` value per collected tick. This specifically guards against the failure mode the existing `test_datacollector_total_payload_remaining_column` / `test_export_results_includes_new_columns` tests couldn't catch: both call `step()` only 1–2 times and assert column *presence*, which still passes even with every tick collapsed onto one row.

Also removed `self._initial_total_payload` from `WraithNetModel.__init__` — Phase 10 correctly removed its two usages (the old payload-delta-based `TotalMissilesFired`/`total_missiles_fired` computations, both replaced with `strike_log`-based counts), but left the assignment itself as dead code. Patched `docs/phases/Phase_10_Integration_Metrics_Validation.md` with the root cause and fix shown above, and added a note recommending the stronger regression-test shape, so a future from-scratch implementation of this phase doesn't reintroduce the bug.

## 2. Confirmed: prior review findings were correctly fixed

* **`docs/02_Code_Review_Phases_3-5.md` multi-round CNP bug** (BID-collection guard keyed off `role` instead of `managed_target_id`; `_resolve_contract` re-reading stale `hp_remaining`) — confirmed fixed in current `agents.py` (`handle_message`'s BID branch keys off `managed_target_id`; `_resolve_contract` threads `hp_needed` explicitly).
* **`docs/05_Code_Review_Phases_6-8.md` §1, dead-manager zombie-CFP bug** — confirmed fixed. `bridge.py::_notify_managers_of_losses` now has the `if manager_id not in destroyed_agent_ids` guard, with an inline comment citing the review doc directly. A dedicated regression test, `test_notify_contractor_lost_skips_destroyed_manager` (`tests/test_bridge.py`), reproduces the exact simultaneous-death scenario from that review and asserts the dead manager's zombie CFP never reaches `Jet_1` and `Jet_1` never gets stranded in `CONTRACTOR_PENDING`. This is a clean, direct fix-and-regression-test pairing.
* **`docs/05_Code_Review_Phases_6-8.md` §2, `scan_and_negotiate` suitability pre-filter deviation** — the implementer kept the deviation (a JDAM-only jet still skips a `MOBILE_RADAR` target during the IDLE scan rather than burning a full CFP round on it) but **documented it explicitly** in the method's docstring, citing "Deliberate deviation from Phase 6 Part D.5" and explaining why. This is the right resolution — it's a reasonable efficiency optimization, and the deviation is now traceable rather than silent.
* **`docs/05_Code_Review_Phases_6-8.md` §3, `DataCollector` `agenttype_reporters` fix** — confirmed still correct and consistently applied in both `world.py` (construction) and `metrics.py::export_results` (`get_agenttype_vars_dataframe(JetAgent)`), avoiding the `AttributeError` that a flat `agent_reporters` dict would raise the moment it walks a `SamSiteAgent`/`EwJammerAgent`.

No regressions were found in any previously-reviewed phase.

## 3. Phase 9 (Electronic Warfare / Jamming) — matches spec, acceptance criteria met

* `MessageBroker._route` is confirmed the sole enforcement point for jamming (sender-jammed traffic never leaves; jammed recipients are skipped on broadcast and dropped on direct send) — `_maybe_bid`/`try_become_manager`/`_broadcast_cfp_and_collect`/`_resolve_contract` contain no jamming-specific branches, exactly as the phase doc's acceptance criterion #2 requires.
* The one genuinely new mechanism — `CONTRACTOR_PENDING_TTL_TICKS` self-heal via `check_pending_bid_timeout()` — is implemented exactly as specified, with `pending_bid_since_tick` set on entering `CONTRACTOR_PENDING` (`_maybe_bid`) and cleared on both existing exit paths (`_commit`, `REJECT` handling).
* `Observation` delivery is confirmed unaffected by jamming: `bridge.py::_tick` calls `set_observation` unconditionally for every jet every tick, and `test_jamming_never_filters_observation_contents` / `test_jamming_never_filters_observation_contents` (world + agents variants) both assert `known_targets`/`payload`/`current_position` are populated identically regardless of `comms_jammed`.
* All four `test_electronic_warfare.py` scenarios from the phase doc's Part F are present and pass: jammed-manager self-release, jammed-bidder TTL self-heal, negative-case timeout checks, and the Observation-delivery-is-unaffected check.
* `test_jet3_path_to_target_b_crosses_jammer` and `test_scenario_exercises_jamming_and_still_resolves` confirm the jamming mechanic is actually geometrically exercised by the real scenario, not just unit-tested in isolation — satisfying the phase doc's "prove the mechanic was actually exercised" principle.
* A fresh full run confirms `total_jammed_agent_ticks == 5` (one jet jammed for 5 consecutive ticks along its real flight path) with the scenario still fully resolving — acceptance criterion #5 met.

No issues found in Phase 9.

## 4. Phase 10 (Integration, Metrics, Validation) — matches spec, acceptance criteria met (after §1's fix)

* `metrics.summarize()` matches the phase doc's Part B exactly: `swarm_wiped_out`, `jets_destroyed`, `jets_survived`, `per_weapon_type_missiles_fired`, `total_jammed_agent_ticks` are all present and correctly sourced (`strike_log`-based missile counts, not the fragile payload-delta approach the doc explicitly warned against).
* `tests/test_scenario_v2.py` implements every test from the phase doc's Part D, including the full-resolution check, per-target cooperation/solo assertions, exact missile-count/weapon-breakdown assertions (`7` total, `{"JDAM": 6, "AIM9X": 1}`), the cross-mechanic "both Fog of War and jamming were actually exercised" check, the zombie-state sweep, and determinism across repeated runs.
* **`test_total_distance_traveled_is_reasonable`'s bound was correctly tightened** from the doc's placeholder (`0.0 < x < 600.0`) to an observed-and-justified range (`180.0 <= x <= 280.0`, documented as "~222.67 ± 20%" with the actual observed value recorded in a comment) — satisfying acceptance criterion #3, which explicitly calls out that shipping the wide placeholder would mean the phase wasn't actually finished.
* **Determinism confirmed independently**: this review's own two fresh full runs (before/after the §1 fix) produced byte-identical `ticks_elapsed`, `total_distance_traveled`, `total_missiles_fired`, and `per_weapon_type_missiles_fired` — consistent with `test_determinism_across_repeated_runs` and with the exact `≈222.67`/tick-32 value the test suite's comment documents from its own earlier observed run.
* Part E's consistency checks were re-verified fresh in this review:
  1. `grep -rn payload_remaining` (excluding `total_payload_remaining`) across `wraithnet/`/`tests/` → confirmed zero stray scalar references remain.
  2. `python -m wraithnet.main` → confirmed matches `test_scenario_v2.py`'s asserted values exactly (3 targets, 7 missiles, `{"JDAM": 6, "AIM9X": 1}`).
  3. Full `pytest` suite (Phases 0–10 combined) → confirmed 130/130 passing together.
  4. Design-doc accuracy → found and fixed one drift (below), otherwise accurate.

### 4a. Design doc accuracy fix

`docs/04_6th-Gen_Warfare_Sim_Design_Document_v3_Phase2.md` §5 stated that the merge of direct-sensor and remembered (`world_view`) targets happens *inside* `WraithNetModel.build_observation`, i.e. that it populates `Observation.known_targets` itself. The shipped implementation is actually cleaner than that: `Observation.known_targets` stays strictly direct-sensor-only (ground truth, computed by the World, with no knowledge of any agent's memory), and the merge happens one layer up in `JetAgentRuntime.effective_known_targets()`, which reads from `self.world_view` (itself refreshed each tick from the direct `Observation` by `refresh_world_view()`). This is a better separation of concerns — the World never needs to know about per-agent memory state — but the doc described the wrong component as owning the merge. Corrected the doc's wording to match the actual (and better) architecture rather than changing working, tested code to match the doc.

## 5. Whole-project cross-cutting checks

* **Determinism**: confirmed end-to-end — no `random`/`np.random` calls anywhere outside of Mesa's unused RNG seed plumbing; SAM damage and jamming are purely positional/deterministic as designed.
* **Schema invariants** (`schemas.py`): all `model_validator`s from earlier phases remain intact and consistent with current usage — `TargetInfo.hp_remaining <= hp_required`, `Message` type-specific required-field rules (including the Phase 7 `DATALINK_CONTACT` carve-outs), `ActionIntent` STRIKE requiring both `target_id` and `weapon_type`.
* **Agent lifecycle**: destroyed jets are correctly removed from `model.jets`, `model.space`, and `bridge.agents`, with their `asyncio.Task` cancelled and awaited; `test_bridge.py`'s task-exception sweep and `test_scenario_v2.py`'s `test_no_agent_left_in_a_zombie_state` both confirm no dangling tasks or stuck roles survive a full run.
* **No lingering `payload_remaining` scalar references** anywhere in `wraithnet/`/`tests/` (verified fresh, not just trusted from the Phase 10 doc's own claim).
* **CSV export** (`export_results`): now produces a correct multi-row time series for both `model_vars.csv` (33 rows) and `agent_vars.csv` (99 rows = 33 ticks × 3 jets) after §1's fix; previously `agent_vars.csv` was silently wrong (3 rows total). This was the only correctness gap found in this review.

## 6. Minor / cosmetic observations (no action required)

* `wraithnet/agents.py::compute_bid`'s tie-break for weapon selection (`max(suitable, key=lambda pair: (pair[1], pair[0]))`) picks the suitable weapon a jet holds the *most* of, tie-broken alphabetically by weapon name — reasonable and already covered by existing tests; noted only because it's a subtle enough rule that a future reader modifying weapon types should know it's intentional.
* `tests/test_scenario_v2.py` and `docs/phases/Phase_10_Integration_Metrics_Validation.md`'s own inline copy of the same tests are near-duplicates (the doc's copy uses `contributors_a.get(..., set())` vs. the shipped test's `.get(..., [])`, a harmless artifact of the doc being the *prompt* rather than the final code). No action needed — this is expected for a "prompt document," not a defect in the shipped test suite.

## 7. Final verdict

The full project (Phases 0–10) is in a **correct, consistent, well-tested state**. All previously-identified bugs (multi-round CNP renegotiation, dead-manager zombie CFP) remain fixed with regression tests in place. Phases 9 and 10 both match their design documents faithfully, with only cosmetic/documentation-level deviations. This review's own pass found and fixed one previously-undetected, moderate-severity bug (§1 — per-agent CSV time-series data being silently collapsed to a single row since Phase 6), which is now fixed and regression-tested.

**Final state after this review:**
* `pytest`: **130/130 passing** (129 pre-existing + 1 new regression test).
* `python -m wraithnet.main`: solves the full v3 scenario in 32 ticks, 3/3 targets destroyed, 0 jets lost, 7 missiles fired (`{"JDAM": 6, "AIM9X": 1}`), 5 jammed-agent-ticks, exports correct multi-row `model_vars.csv`/`agent_vars.csv`.
* Documentation (`docs/04_..._v3_Phase2.md`, `docs/phases/Phase_10_...md`) updated to reflect both the fix and the corrected Fog-of-War architecture description.
