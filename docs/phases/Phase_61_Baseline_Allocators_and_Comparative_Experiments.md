# Phase 61 — Baseline Allocators & Comparative Experiments

> **LLM implementation prompt:** Assume Phases 0–60 are complete. This phase comes before the backbone changes on purpose: Phases 62–65 each rerun the matrix defined here and report against it. Read `docs/23_Project_State_Audit_and_Next_Phases_Roadmap.md` §6. This phase adds no new swarm behaviour. It adds reference points and a fixed experiment, so that every claim about the negotiation ("risk-aware bidding helps", "it degrades gracefully under jamming") is a number from a reproducible command. Baselines live in their own module and never touch `agents.py` logic; they plug in at the single point where targets are assigned.

## Goal

Answer, with data: is the Contract Net swarm better than something trivial, how far is it from a planner that sees everything, and what does it lose as the radio gets worse?

## Why

The project's claim is decentralised coordination. Today there is nothing to compare it against. A solve rate of 60% means nothing alone: a greedy "nearest jet takes nearest target" rule might score 55% or 80% on the same seeds. Without baselines, every weight change in `bid_utility.py` is tuned by watching one replay.

## Part A — allocator seam

One narrow interface, selected per run (`--allocator NAME` on the Phase 57 runner, stored in the result row):

```python
class Allocator(Protocol):
    def assign(self, tick: int, jets: list[JetView], targets: list[TargetView]) -> dict[str, Assignment]: ...
```

The bridge calls it in place of the negotiation step and writes the result into each runtime's commitment. Everything downstream (`decide_intent`, world, metrics, trace) is shared, so differences in outcome come from allocation only.

`cnp` is the default and is simply "do what the bridge does now".

## Part B — baselines

| Name | Rule | What it tells you |
|---|---|---|
| `random` | each idle jet with a suitable weapon takes a random unassigned live target (seeded) | the floor |
| `greedy_nearest` | repeatedly assign the closest (jet, target) pair with a suitable weapon | whether the auction beats the obvious heuristic |
| `central_oracle` | sees ground truth, every tick solves the assignment with the same utility the bids use (`scipy` is not a dependency: use a small Hungarian implementation or brute force for these sizes) | the ceiling for this utility function, and the price of fog and radio |
| `central_fog` | same solver, but restricted to the union of what the jets currently know | separates "cost of not knowing" from "cost of negotiating" |
| `cnp` | current system | the subject |
| `cnp_legacy_utility` | CNP with the pre-Phase-45 scalar utility (weapon, payload, distance) | whether risk-aware scoring earns its complexity |

Baselines ignore jamming by construction (they are not on the radio). State that next to every result.

## Part C — the experiment

A fixed, versioned matrix in `experiments/allocation_v1.json`: presets × ≥100 seeds × allocators, plus for `cnp` only a degradation sweep over jammer count and (after Phase 62) radio range, latency and loss.

Metrics per cell, all already in `summarize` or added by Phase 57: solve rate, outcome mix, median ticks to completion, jets lost, rounds expended per target HP destroyed, overkill, total distance, messages sent.

Report as medians with bootstrap 95% intervals, and paired per-seed differences against `greedy_nearest` (same seeds, so pairing removes most of the variance). No claim without an interval.

One command reproduces the table; `python -m wraithnet.experiment --matrix experiments/allocation_v1.json`.

## Part D — report

`docs/26_Allocation_Experiment_Report.md`: the table, three or four plots saved as static images, and a short reading of what the numbers say, including the uncomfortable ones. If `greedy_nearest` matches CNP on some preset, say so and say why it probably does.

Plots are produced by one script under `experiments/`; `matplotlib` goes into a new `requirements-analysis.txt`, not into the runtime requirements.

## Part E — weight sensitivity

Using Phase 57 `--config-override`, vary each `bid_utility` weight one at a time over a small grid on STANDARD and CONTESTED. Report which weights move the outcome and which do not. Weights that do not matter are candidates for removal, which is a result.

## Tests

* Each baseline on a 3-jet, 3-target hand-built scenario produces the expected assignment.
* `central_oracle` total utility ≥ every other allocator's on small cases (it is the optimum by construction).
* Allocator seam: with `cnp`, the Phase 60 equivalence fixture is unchanged.
* Statistics helper: bootstrap interval of a constant sample has zero width; paired difference of identical runs is zero.

## Acceptance criteria

1. One command reproduces the full table from a clean checkout.
2. Report 26 exists, states sample sizes and intervals, and names at least one thing the current CNP does worse than a baseline or a cost it pays.
3. README "How the swarm thinks" links the report and quotes two numbers from it.

## Non-goals

* No reinforcement learning, no LLM-driven agents, no auto-tuning of weights.
* Baselines are yardsticks, not shipped behaviours; the GUI does not expose them.
