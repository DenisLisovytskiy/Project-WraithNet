# Code Review: Phases 0–2 (Project Setup, Schemas & World, Message Broker & Agent Loop)

Reviewed against: `docs/phases/Phase_0_Project_Setup.md`, `docs/phases/Phase_1_Schemas_and_World.md`, `docs/phases/Phase_2_Message_Broker_and_Agent_Loop.md`.

Files reviewed: `wraithnet/config.py`, `wraithnet/schemas.py`, `wraithnet/world.py`, `wraithnet/broker.py`, `wraithnet/agents.py`, `tests/test_smoke.py`, `tests/test_schemas.py`, `tests/test_world.py`, `tests/test_broker.py`, `tests/test_agents.py`, plus `requirements*.txt`, `README.md`, `.gitignore`.

**Verification performed:** ran the full suite (`pytest -q`) — **32/32 passed**. Re-ran with `-W error` (fails on *any* warning, not just the default-filtered ones) — still 32/32 passed, zero warnings. Re-ran `test_agents.py`/`test_broker.py` with `PYTHONASYNCIODEBUG=1` and unfiltered output — no "Task exception was never retrieved," "coroutine was never awaited," or similar asyncio-level leaks. Installed versions (`python 3.14.0`, `mesa 3.5.1`, `pydantic 2.13.4`) all fall within the pinned ranges in `requirements.txt`.

**Overall assessment: high quality, closely follows the phase specs, no functional bugs found.** The corrections below are one important documentation/robustness gap worth closing, plus several minor/cosmetic items. Nothing here blocks starting Phase 3.

---

## 1. [Important — document & guard, don't just leave it] The Mesa 3.5 `step()`-shadowing fix needs a regression test and a note in the design docs

`wraithnet/world.py` contains this, right after `super().__init__(seed=seed)`:

```python
# Mesa 3.x replaces self.step with a no-arg _wrapped_step for its
# event scheduler. Restore our intents-based step so callers (and
# Phase 4's Bridge) can use model.step(intents) directly.
self.step = self._user_step
```

This is a **real, non-obvious issue the implementer correctly discovered and fixed**, and it's outside what the original Phase 1 spec anticipated (the spec's sample code just defined `def step(self, intents=None)` and assumed that would be `model.step`). I verified the root cause directly in the installed `mesa` package: `mesa.Model.__init__` does

```python
self._user_step = self.step        # captures the subclass's `step` override
...
self.step = self._wrapped_step     # then SHADOWS it with Mesa's own no-arg event-scheduler step
```

So without this fix, `model.step(intents)` would actually call Mesa's internal `_wrapped_step(self)` — which takes no extra arguments — and raise `TypeError: _wrapped_step() takes 1 positional argument but 2 were given` the first time anything (including Phase 4's Bridge) called `model.step(intents)`. The fix correctly reuses the `_user_step` reference Mesa itself stashes for exactly this purpose. **This is good work, not a bug** — but two things should happen so it doesn't get silently lost later:

- **Add a regression test.** This one line is easy for a future refactor to "clean up" as apparent dead code, since nothing currently *fails loudly* if it's removed except a confusing `TypeError` several phases later. Add to `tests/test_world.py`:
  ```python
  def test_step_is_not_shadowed_by_mesas_internal_wrapped_step():
      model = WraithNetModel()
      assert model.step.__func__ is WraithNetModel.step
  ```
- **Fix a stale cross-reference in the design docs, not the code.** `docs/phases/Phase_1_Schemas_and_World.md` told the implementer to write the comment `# Broker fills this in correctly from Phase 3 onward` on `TargetInfo.claimed=False` inside `build_observation`. That's a mistake in *my* Phase 1 doc — the fix that actually wires `broker.is_claimed()` into `build_observation` is specified in Phase 4's doc ("Small required fix" section), not Phase 3. The implementer correctly copied what the spec said, so the resulting comment in `wraithnet/world.py` (`# Broker fills this correctly from Phase 3 onward`) is now slightly wrong. **I've already corrected the source doc** (`Phase_1_Schemas_and_World.md` now says "Phase 4"); update the comment in `wraithnet/world.py` to match: `# Broker fills this correctly from Phase 4 onward`.

## 2. [Minor] Test gap: the zero-payload branch of `_apply_strike` is never actually exercised

`_apply_strike` has two independent no-op guards:

```python
def _apply_strike(self, jet, target_id):
    target = self.targets.get(target_id) if target_id else None
    if target is None or jet.payload_remaining <= 0:   # <-- two different reasons to bail
        return
    if math.dist(jet.pos, target.position) > config.STRIKE_RANGE:
        return
    ...
```

`tests/test_world.py::test_failed_strike_does_not_append_strike_log` was clearly intended to cover a "failed strike" case, but it never moves the jet into range — so it actually re-tests the **out-of-range** guard (already covered by `test_strike_out_of_range_is_noop`), not the **zero-payload-while-in-range** guard. That second branch currently has zero test coverage. Add:

```python
def test_strike_with_zero_payload_in_range_is_noop():
    model = WraithNetModel()
    jet = model.jets["Jet_1"]
    target = model.targets["Target_A"]
    model.space.move_agent(jet, target.position)
    jet.payload_remaining = 0
    intent = ActionIntent(agent_id="Jet_1", tick=0, action_type="STRIKE", target_id="Target_A")
    model.step({"Jet_1": intent})
    assert target.hp_remaining == 5
    assert model.strike_log == []
```

(Either rename the existing `test_failed_strike_does_not_append_strike_log` to `test_strike_out_of_range_does_not_append_strike_log` to make clear it's a duplicate-by-design sanity check on `strike_log` specifically, or just add the new test above alongside it — either is fine, just don't leave the zero-payload branch untested.)

## 3. [Minor] Repository layout doesn't quite match Phase 0's spec: `tests/__init__.py` is missing

`docs/phases/Phase_0_Project_Setup.md` §2 specifies `tests/__init__.py` as part of the layout. It's absent — harmless for `pytest` (rootdir-based discovery doesn't need it, and all 32 tests do run), but for exact spec compliance and to avoid any future ambiguity if the test layout ever needs to support relative imports between test modules, add the empty file:

```
tests/__init__.py   # empty
```

## 4. [Minor] `config.py` is missing the rationale comment the spec asked for

`docs/phases/Phase_0_Project_Setup.md` explicitly asks for a comment on the `SCENARIO` dict explaining why `Target_A.hp_required = 5` is deliberately greater than any single jet's max payload (not just `Jet_1`'s). The values are correct, but the comment was dropped. Add it back so a future reader of `config.py` alone (without cross-referencing the docs) doesn't "simplify" the number:

```python
SCENARIO = {
    "targets": [
        # hp_required=5 is deliberately > the max single-jet payload (4), not just
        # > Jet_1's payload (1) — this makes destroying Target_A require at least
        # two jets no matter how the CNP utility weights are tuned.
        # See docs/00_Design_Review_Findings.md item F1. Do not "simplify" this number.
        {"target_id": "Target_A", "position": (75.0, 75.0), "hp_required": 5},
        {"target_id": "Target_B", "position": (25.0, 25.0), "hp_required": 1},
    ],
    ...
}
```

## 5. [Trivial / cosmetic] Redundant condition in `_apply_move`

```python
if distance <= config.JET_SPEED or distance == 0:
```

Since `config.JET_SPEED > 0`, `distance == 0` is already implied by `distance <= config.JET_SPEED`; the `or distance == 0` clause is dead code. Harmless, but simplify to:

```python
if distance <= config.JET_SPEED:
```

## 6. [Trivial / cosmetic] Mixed use of `math.hypot` and `math.dist` for the same computation

`_apply_move` computes Euclidean distance via `math.hypot(dx, dy)`; `_apply_strike` computes it via `math.dist(jet.pos, target.position)`. Both are correct and equivalent, but pick one for consistency (`math.dist(jet.pos, destination)` reads more clearly in `_apply_move` too, and matches what the test files already use throughout `test_world.py`).

## 7. [Forward-looking, not currently broken] `WraithNetModel(seed=...)` will trigger a Mesa `FutureWarning` if ever actually used with a real seed

Mesa 3.5's `Model.__init__` only takes the "no warning" path when `seed` is left as `None`; passing any real integer `seed` (not `rng`) hits Mesa's own deprecation path (`FutureWarning: The use of the 'seed' keyword argument is deprecated, use 'rng' instead`). Right now this is invisible because every call site in the codebase and test suite uses `WraithNetModel()` with no argument — so this isn't a live bug. But the `seed: int | None = None` parameter on `WraithNetModel.__init__` (per the Phase 1 spec I wrote) is exactly the kind of thing that looks like "the reproducibility knob" and will get used eventually (e.g., if a `--seed` CLI flag is ever added in a later phase). Worth a one-line note in `world.py` now, or switching the parameter to pass through as `rng=seed` internally, so nobody is surprised by a `FutureWarning` later:

```python
def __init__(self, seed: int | None = None) -> None:
    super().__init__(rng=seed)  # Mesa 3.5 prefers `rng=`; passing `seed=` directly is soft-deprecated
```
(Only do this if `rng=<int>` is confirmed to behave equivalently to the old `seed=<int>` for reproducibility in your installed Mesa version — worth a quick check before changing, since this isn't broken today.)

## 8. [Optional hardening, not required by the Phase 1 spec] A couple of schema invariants aren't enforced yet

Not a defect against the spec as written (these weren't required), but worth considering given the project's stated design goal of "strict Pydantic schemas":

- `TargetInfo` doesn't assert `hp_remaining <= hp_required`. A cross-field `model_validator` would catch an accounting bug in `world.py` immediately at the schema boundary instead of silently producing a nonsensical `Observation`.
- `Message` validates that `CFP` must be broadcast, but doesn't validate that `BID`/`ACCEPT`/`REJECT` must **not** be broadcast (i.e. `receiver_id != BROADCAST_ID`). Since Phase 3 is about to start building the negotiation state machine on top of this schema, catching an accidental broadcast-`ACCEPT` at construction time (rather than as a silent broker fan-out bug) would be a cheap safety net to add now, before Phase 3 code starts depending on the schema's guarantees.

## Acceptance-criteria check against each phase doc

| Phase | Criterion | Status |
|---|---|---|
| 0 | `pytest` passes, zero errors/warnings | ✅ |
| 0 | placeholder modules (`bridge.py`, `metrics.py`, `main.py`) import cleanly, docstring-only | ✅ |
| 0 | repo layout matches spec exactly | ⚠️ missing `tests/__init__.py` (item 3) |
| 1 | `known_targets` typed as `list[TargetInfo]`, never `list[dict]` | ✅ |
| 1 | no networking/asyncio imports in `schemas.py`/`world.py` | ✅ |
| 1 | termination (`model.running`) implemented per the corrected design (not deferred to Phase 5) | ✅ |
| 1 | `strike_log` present and correctly populated for Phase 5 | ✅ |
| 2 | `MessageBroker` distinguishes broadcast vs. direct, never echoes broadcast to sender | ✅ |
| 2 | `JetAgentRuntime.run()` starts/cancels cleanly, no leaked tasks/warnings | ✅ (verified with `PYTHONASYNCIODEBUG=1`) |
| 2 | neither `broker.py` nor `agents.py` imports `wraithnet.world` | ✅ |

## Recommendation

None of the above blocks moving on to **Phase 3 (`docs/phases/Phase_3_CNP_Negotiation.md`)**. Suggested order if you want them addressed first: item 1 (regression test) > item 2 (test gap) > items 3–4 (spec-compliance nits) > items 5–8 (cosmetic/optional).
