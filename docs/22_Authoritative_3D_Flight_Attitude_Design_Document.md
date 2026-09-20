# Design Document: Authoritative 3D Flight Attitude & Weapon Release Gates

> This document is the source of truth for reworking jet movement into **authoritative** pitch / roll / yaw flight in world space, with per-airframe maneuver limits and attitude-gated weapon release. **Nothing in this document is implemented yet.** See Phases 50–55 for LLM-ready implementation prompts.
>
> Numbering note: Phases 46–49 remain the smooth live-motion presentation track. This expansion is an independent **gameplay flight** track starting at **Phase 50**.
>
> **Supersedes (for attitude / altitude authority):** the “altitude forever cosmetic / sim stays 2D” non-goals in `docs/09_...md` §6.2/§10 and `docs/12_Flight_Dynamics_and_Weapon_Effects_Design_Document.md` §1/§3/§4.1. Phase 37 already made altitude gameplay-relevant for LOS / AAA / missiles; this doc completes that direction for **attitude**. Viewer cosmetics from Phases 25–26 remain valid only as presentation easing until Phase 54 wires them to authoritative pose.

## 0. Goal

Jets must fly in **3D space** with controllable **pitch, roll, and yaw**, change altitude as a consequence of attitude and climb limits, and respect **sharper / duller turn rates per airframe**. Weapon employment must respect orientation — in particular, **gravity bombs cannot release when the jet is rolled (or pitched) past a documented upright envelope** (e.g. inverted).

Success looks like:

1. Each airframe has explicit per-axis rate limits (yaw already exists; pitch and roll are added; climb remains bounded).
2. Jet pose in the sim is `(x, y, altitude, yaw, pitch, roll)` and advances on discrete ticks.
3. Horizontal motion remains nose-along-track; vertical motion is driven primarily by **pitch** (and frame climb rate), not by an independent “teleport Z” intent that ignores attitude.
4. `STRIKE` / `LAUNCH` reject illegal release attitudes with a clear, testable reason code.
5. Agents can command attitude (or destination + desired attitude) and complete missions under the new constraints.
6. Live/replay 3D view shows the same pitch/roll/yaw the sim uses (interpolated for presentation).
7. Headless determinism and existing PoC summarize contracts either hold under a documented compatibility mode or are updated with intentional golden-file refreshes in Phase 55 — never silently.

## 1. Why the current model is insufficient

| Layer | Today | Gap |
|---|---|---|
| Pose | XY + heading (yaw) + altitude | No pitch/roll DOF; bank in `viewer3d` is cosmetic fiction |
| Turning | `turn_rate_deg_per_tick` yaw only | No pitch/roll rate differentiation by frame |
| Climb | `_apply_altitude` eases toward `desired_altitude` | Uncoupled from nose attitude; agents rarely set it |
| Weapons | Horizontal range + horizontal firing arc + LOS | No upright / belly-down release gate for bombs |
| Viewer | `set_hpr(-heading, 0, -bank)` with pitch often 0 | Attitude does not match combat authority |

## 2. Existing capabilities this expansion builds on

* `JET_FRAME_CATALOG` with `max_speed`, `turn_rate_deg_per_tick`, `cruise_altitude`, `max_climb_per_tick`, `min_combat_altitude` (`wraithnet/config.py`).
* Nose-along-track XY cruise and yaw-capped `_apply_heading_rotation` (`wraithnet/world.py`).
* Authoritative altitude for LOS / AAA ceilings / missile Z / carrier recovery (Phase 37+).
* Weapon catalog with `guidance`, `firing_arc_deg`, suitability (`WEAPON_CATALOG`).
* Pre-move weapon release pose (`_weapon_release_xy`) so coast-after-fire does not cheat range.
* Trace `JetFrame.heading` / optional `altitude`; smooth presentation interpolates XY + heading (Phases 46–49).
* Pure geometry helpers (`wraithnet/geometry.py`) shared by world and agents.

## 3. Core decisions

| Concern | Decision |
|---|---|
| Authority | **Sim-authoritative** pitch, roll, yaw, altitude. Viewer eases/interpolates only. |
| Physics fidelity | **Discrete tick aircraft kinematics**, not continuous aero / G / stall sim. No energy management in this track unless a later doc adds it. |
| Degrees of freedom | Full attitude triple: **yaw (heading), pitch, roll**. Translation: cruise along the body forward axis projected into world; climb component from pitch, clamped by `max_climb_per_tick`. |
| Roll semantics | Roll is a first-class state used for (a) visual attitude, (b) weapon upright gates, (c) optional coordinated-turn assist (see §5.3). It is **not** required to be the only way to change yaw. |
| Yaw rate | Keep existing `turn_rate_deg_per_tick` as the **yaw** rate cap; rename in docs/API comments to `yaw_rate_deg_per_tick` while keeping the field name or adding an alias — Phase 50 picks one approach and tests both spellings if aliased. |
| Pitch / roll rates | New per-frame fields: `pitch_rate_deg_per_tick`, `roll_rate_deg_per_tick`. |
| Weapon gates | New per-weapon (or per-guidance-class) **release attitude envelope**. Gravity bombs refuse inverted / excessive bank or pitch. |
| Agent API | Extend `Observation` / `ActionIntent` with current and desired pitch/roll (and keep `desired_heading` / `desired_altitude` with documented precedence — §5.4). |
| Determinism | Same seed + scenario → same outcomes. Attitude math uses the same float style as existing heading code. |
| CNP / bidding | Phase 53 may lightly bias bids when a strike requires a dive/level pass; do **not** redesign CNP message shapes in this track. |
| Non-goals | Full 6DOF space-fighter freedom (no hovering, no pure lateral strafe). No continuous sub-tick combat. No new airframe types. No rewriting naval/tank movement. No replacing guided-missile pursuit math beyond reading launcher attitude at release. |

## 4. Architecture overview

```
  ActionIntent
    desired_heading / desired_pitch / desired_roll
    desired_altitude (advisory / recovery override — see §5.4)
        │
        ▼
  stage_pre_move_obs
    apply yaw/pitch/roll rate caps (per JetFrameSpec)
        │
        ▼
  stage_apply_movement
    translate along body forward (XY from yaw; Δalt from pitch, climb-clamped)
    recovery / RTB floors unchanged in spirit
        │
        ▼
  stage launches / strikes
    range + LOS + horizontal arc (existing)
    + release_attitude_ok(weapon, pitch, roll)   ← NEW
        │
        ▼
  TraceTick.JetFrame { heading, pitch, roll, altitude }
        │
        ▼
  viewer3d motion interpolator + scene.set_hpr(yaw, pitch, roll)
```

## 5. Flight model (authoritative)

### 5.1 Pose

On `JetAgent` (names illustrative; Phase 50 freezes exact fields):

| Field | Unit | Notes |
|---|---|---|
| `pos` (x, y) | world | Mesa ContinuousSpace |
| `altitude` | world | existing |
| `heading` | deg `[0,360)` | yaw; existing convention **0° = +y, clockwise** |
| `pitch_deg` | deg | **0** = level; **+** = nose up; clamp to frame `pitch_limit_deg` |
| `roll_deg` | deg | **0** = wings level; **+** = roll right; typically `(-180, 180]` or `[0,360)` — Phase 50 freezes one convention and uses it everywhere |

### 5.2 Per-airframe maneuver catalog

Extend `JetFrameSpec` (suggested defaults — Phase 50 may tune numbers but must keep **Interceptor sharpest, Bomber dullest**):

| Frame | yaw °/tick (existing) | pitch °/tick | roll °/tick | pitch limit ° | notes |
|---|---|---|---|---|---|
| `INTERCEPTOR` | 45 | 20 | 60 | ±35 | agile fighter |
| `STRIKE_FIGHTER` | 25 | 12 | 40 | ±30 | baseline |
| `HEAVY_BOMBER` | 12 | 6 | 20 | ±20 | ponderous |

Keep existing `max_climb_per_tick` / `min_combat_altitude` / `cruise_altitude`. Climb rate remains the **vertical speed clamp**; pitch commands the **intent**, climb clamp enforces feasibility.

### 5.3 Integrating pitch into altitude (no more pure Z slider as primary)

Each movement tick after attitude update:

1. Compute a commanded vertical component from pitch, e.g.  
   `raw_dz = max_speed * sin(radians(pitch_deg))`  
   (Phase 51 may use a slightly softer mapping; document the chosen formula in code and tests.)
2. Clamp `|dz|` by `max_climb_per_tick`.
3. Apply RTB / carrier recovery floors exactly as today’s `_apply_altitude` floor logic.
4. XY step remains approximately `max_speed * cos(radians(pitch_deg))` along heading (or full `max_speed` on the ground track with dz separate — **pick one in Phase 51** and test energy-ish tradeoff lightly; prefer simple and deterministic).

**`desired_altitude` precedence (compatibility):**

* **Primary (combat cruise):** agents set `desired_pitch` to climb/dive; world integrates altitude.
* **Recovery / RTB / carrier:** existing code paths may still set `desired_altitude` (e.g. deck height). When `desired_altitude` is set for recovery, world may synthesize a pitch toward that altitude **or** bypass pitch and ease altitude directly — Phase 51 must choose **one** rule, document it, and cover it with tests. Recommendation: **recovery override** may ease altitude directly while forcing pitch toward level on short final; combat path uses pitch.

### 5.4 Coordinated turn (optional assist, not full aero)

Minimum viable: yaw and roll are commanded independently with rate caps.

Recommended quality-of-life (Phase 51, still discrete): if the agent commands a yaw change and leaves roll at “auto”, apply a **bank assist** `roll ≈ clamp(k * yaw_rate_command, ±roll_limit)` for visuals and bomb-gate realism during turns. Agents may override with explicit `desired_roll`.

Do **not** require bank-to-turn as the only yaw mechanism in v1.

### 5.5 Observation / intent surface

Add to `Observation`: `current_pitch_deg`, `current_roll_deg`, `current_altitude` (if not already present — verify Phase 37 fields).

Add to `ActionIntent`: `desired_pitch_deg`, `desired_roll_deg` (optional floats). Keep `desired_heading`, `desired_altitude`.

Unset desired axes mean “hold current” (match today’s `desired_heading is None` → no yaw change).

## 6. Weapon release attitude gates

### 6.1 Why

Bombs (unguided gravity / JDAM-class direct `guidance=="NONE"` air-to-ground ordnance that is conceptually dropped) leave the jet downward relative to the jet’s **belly**. An inverted jet cannot “drop” onto a ground target in this model.

Missiles that leave along the nose (AIM-9, AMRAAM, many AGMs) care more about **nose pointing / seeker envelope** than belly-down, but still inherit any catalog envelope we attach.

### 6.2 Envelope model

Add optional fields on `WeaponSpec` (defaults preserve today’s fire-anytime-if-range-arc-LOS for guided AAM if envelopes are wide):

```text
max_release_abs_roll_deg: float | None   # None = no roll gate
max_release_abs_pitch_deg: float | None  # None = no pitch gate
requires_upright: bool                  # if True, also reject |roll| > 90° (inverted)
```

Suggested initial catalog policy:

| Weapon | requires_upright | max \|roll\| | max \|pitch\| | Rationale |
|---|---|---|---|---|
| `JDAM` | True | 45° | 30° | Gravity bomb / belly release |
| `GUN_POD` | False | 90° | 45° | Guns follow nose; allow aggressive maneuver but not nonsense |
| `AGM65` / `AGM88` / `AGM84` | True | 60° | 40° | Air-to-surface employment pass |
| `AIM9X` / `AMRAAM` | False | None | None | All-aspect launch; attitude unconstrained beyond existing arc |
| SAM types | n/a | — | — | Not jet-released |

Exact numbers are tunable in Phase 52 tests; the **behavior** (reject inverted JDAM) is not.

### 6.3 Enforcement point

Gate must run in the same place as range/arc/LOS today (`world` strike/launch path and any shared helper used by `decide_intent` pre-checks), using the **pre-move release pose** including pitch/roll frozen at commit time (extend `_weapon_release_xy` → `_weapon_release_pose`).

Reject reasons (stable string codes for metrics/tests), examples:

* `outside_release_attitude_roll`
* `outside_release_attitude_pitch`
* `jet_inverted_release_forbidden`

Agents that are inverted near a target must **roll upright** (and possibly pitch) before `STRIKE` with JDAM — Phase 53 teaches `decide_intent` to do that.

### 6.4 What does *not* change in Phase 52

* Instant same-tick resolution for `guidance=="NONE"` strikes.
* Guided missile flight loop after a legal launch.
* Suitability / damage tables.

## 7. Agent behavior (Phase 53)

`decide_intent` must:

1. When closing for a bomb/ASM pass: command pitch for mild dive or level delivery compatible with the weapon envelope.
2. Before release: if `|roll|` or inverted state blocks the weapon, prefer `IDLE` + `desired_roll` toward 0 (and yaw toward target) over illegal `STRIKE`.
3. After release / RTB: pitch toward cruise / recovery profile.
4. Preserve notch / flare defense behavior; notch still primarily yaw + speed (Phase 39). Optional: avoid commanding extreme pitch during notch hold.

CNP / bid utility: optional small penalty if the only legal attack geometry needs a long roll-out; keep Phase 45 risk terms intact.

## 8. Trace, live stream, viewer (Phase 54)

* Bump or extend `JetFrame` with `pitch_deg`, `roll_deg` (optional with defaults `0.0` for legacy traces).
* `SampledPose` interpolates pitch/roll with shortest-arc or linear-in-convention rules (document; test wrap if using `[0,360)`).
* `viewer3d/scene.py` orients glyphs from authoritative yaw/pitch/roll.
* Deprecate altitude-rate fiction bank in `flight_dynamics.py` when authoritative roll is present; keep easing as presentation-only smoothing toward authoritative targets.
* 2D Pygame viewer: show heading as today; pitch/roll optional HUD text — not required for Phase 54 acceptance unless cheap.

## 9. What must not change

* Discrete tick combat authority (no sub-tick hit resolution).
* CNP message protocol shapes (CFP/BID/AWARD/…)。
* Mesa + asyncio bridge structure.
* Deterministic headless runs for a fixed scenario **after** Phase 55 golden updates.
* Naval / tank / AAA systems except where they read jet altitude (already) or, later, jet pose for display.
* Packaging entry points (`python -m wraithnet.main`, `python -m launcher`, `python -m viewer3d.app`).

## 10. Phase map & ownership

| Phase | Title | Primary touch | Depends on |
|---|---|---|---|
| **50** | Attitude contracts & airframe rate catalog | `config`, `schemas`, `JetAgent` fields, tests | main @ design merge |
| **51** | Authoritative 3D movement integration | `world.py` movement, geometry helpers, tests | 50 |
| **52** | Weapon release attitude gates | `config` weapon envelopes, strike/launch checks, tests | 50 (51 recommended) |
| **53** | Agent attack / recovery attitude behavior | `agents.py`, light bid tweaks, tests | 51–52 |
| **54** | Trace + smooth viewer attitude parity | `trace_*`, `viewer3d/motion|scene|flight_dynamics` | 51 |
| **55** | End-to-end acceptance & doc/README closeout | integration tests, PoC goldens, README | 50–54 |

Each phase file under `docs/phases/` is a **standalone LLM prompt** with explicit **Done means** criteria.

## 11. Definition of Done — entire track

The track is **done** when all of the following hold:

1. **Per-frame sharpness:** Interceptor yaw/pitch/roll rates are strictly greater than Strike Fighter, which are strictly greater than Heavy Bomber (tests lock the inequalities).
2. **3D pose:** Every living jet in sim state exposes yaw, pitch, roll, altitude; movement updates all four on ticks where intents request change.
3. **Altitude coupling:** Changing pitch away from 0 produces altitude change on subsequent ticks (within climb clamps); agents can leave and return to cruise altitude without the old “altitude-only slider” as the sole combat API.
4. **Bomb gate:** A jet with `|roll| > JDAM envelope` or inverted cannot successfully `STRIKE` with `JDAM`; after rolling upright within envelope, the same geometry can.
5. **Missile contrast:** At least one AAM (`AIM9X` or `AMRAAM`) remains launchable under a roll attitude that would reject `JDAM` (catalog policy in §6.2), proving gates are weapon-specific.
6. **Viewer parity:** Live 3D view pitch/roll track sim values (within interpolator delay); F3 or equivalent debug can show attitude.
7. **Regression:** Full `pytest` green; any intentional PoC summarize drift is documented and accepted in Phase 55.
8. **Docs:** This design doc’s “Nothing implemented yet” banner is updated when Phase 55 lands; README lists this doc.

## 12. Risks & mitigations

| Risk | Mitigation |
|---|---|
| PoC scenario timing/outcomes shift | Phase 55 golden refresh; keep fixed scenario loadouts; prefer attitude defaults that keep wings-level cruise. |
| Agents never roll upright → stuck unable to bomb | Phase 53 explicit upright-before-release policy + tests. |
| Double attitude (sim + cosmetic bank) fights | Phase 54: authoritative wins when present. |
| Over-scoped aero | Hard non-goal: no stall/G/fuel-energy in this track. |
| Trace break for old replays | Optional fields with defaults; legacy traces render pitch/roll = 0. |

## 13. Open parameters (implementers may tune within tests)

* Exact pitch→dz mapping and whether XY speed falls with `|pitch|`.
* Bank-assist gain for auto-roll in turns.
* Numeric envelope degrees in §6.2.
* Whether `desired_altitude` combat path is removed or kept as advisory autopilot.

Freeze choices in Phase 50/51 PR descriptions so Phase 52–55 agents do not fork semantics.
