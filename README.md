# WraithNet

WraithNet is a proof-of-concept multi-agent simulator: autonomous jets ("loyal wingmen") negotiate over radio using the Contract Net Protocol to decide who strikes which target, running on the Mesa agent-based modeling framework.

## Setup

Requires Python 3.11+.

```bash
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

pip install -r requirements-dev.txt
```

## Tests

```bash
pytest
```

## Running the simulation

```bash
python -m wraithnet.main
```

This runs the fixed PoC scenario to completion, prints a summary (targets destroyed, contributors, distance, missiles), and writes CSV time series under `./results/`.

## Quick random scenario

```bash
python -m launcher
```

1. Choose a difficulty preset (Training / Standard / Contested / Nightmare) and optionally a seed or **New Seed**.
2. Pick friendly forces: **Auto-balanced** (default), **Experimental random**, or **Custom roster**.
3. Click **Generate** — the map fills with jets, objectives, SAMs/jammers, and (by preset) AAA, tanks, and optional naval units.
4. Inspect, then optionally edit entities (SAM IR/RADAR loadouts, jet flares, tank orders) with the manual editor.
5. **Save**, **Preview**, and **Launch** as usual.

Same seed + same resolved parameters reproduce the same scenario (generator v3). Changing tank counts uses a separate RNG stream and does not reshuffle terrain. Auto-balanced aims for static feasibility, not a guaranteed win. Experimental random and custom roster can be statically unwinnable; the UI warns before installing.

## Combined arms (Phases 40–44)

Land: tanks (HOLD/PATROL/ADVANCE), AAA gun sites, city LOS. Naval: carriers and corvettes when counts/presets enable water. Metrics export tank/AAA/CIWS/carrier recovery fields. Feasibility codes cover domain spawn, anti-armor/anti-ship, and ground discovery deadlock.

Default missions require **RETURN**: after DESTROY subjects are done, surviving friendlies RTB before SUCCESS. Live view is paced (~4 ticks/sec) with pause/rewind so Launch is watchable.

## Live view controls

While watching a Launch:

- **Space** — pause / resume (presentation is paced at ~3 ticks/sec; the sim may finish buffering earlier)
- **← / →** — step one tick back / forward
- **R** — restart from tick 0
- **+ / −** — speed up / slow down
- **O / C** — orbit / chase camera
- **Esc** — return to editor

## Risk-aware CNP (Phase 45)

Bids include flare/IR risk when known IR threats exist; with no threat exposure, utilities match the legacy PoC scoring so default assignments stay stable. Managers broadcast `RISK_UPDATE` before collecting BIDs.

## Smooth live view (Phases 46–49)

Simulation ticks stay discrete (CNP, strikes, and outcomes still resolve on
tick boundaries). The 3D viewer keeps a small presentation delay and
**interpolates** jet XY/heading between buffered ticks so motion looks
continuous at roughly 30–60 FPS. Smooth motion is cosmetic presentation
only — it is not continuous combat physics.

```bash
python -m launcher          # Generate → Launch (live stream + interpolator)
python -m viewer3d.app --replay path/to/trace.jsonl [--rate 2]
```

Press **F3** in the 3D view for a compact motion debug line (backlog /
catch-up / extrapolating). The 2D Pygame replay path remains discrete-tick.

## Documentation

- Authoritative design: [`docs/6th-Gen_Warfare_Sim_Design_Document_v2.md`](docs/6th-Gen_Warfare_Sim_Design_Document_v2.md)
- Build plan (phases): [`docs/phases/`](docs/phases/)
- Random scenario generation: [`docs/18_Random_Scenario_Generation_Design_Document.md`](docs/18_Random_Scenario_Generation_Design_Document.md)
- Terrain / combined arms: [`docs/19_Terrain_Naval_and_Combined_Arms_Design_Document.md`](docs/19_Terrain_Naval_and_Combined_Arms_Design_Document.md)
- Risk-aware CNP: [`docs/20_Risk_Aware_CNP_Negotiation_Design_Document.md`](docs/20_Risk_Aware_CNP_Negotiation_Design_Document.md)
- Smooth live motion (dual-clock interpolation, Phases 46–49): [`docs/21_Smooth_Live_Motion_Design_Document.md`](docs/21_Smooth_Live_Motion_Design_Document.md)
- Authoritative 3D flight attitude & weapon release gates (**Phases 50–55 implemented**): [`docs/22_Authoritative_3D_Flight_Attitude_Design_Document.md`](docs/22_Authoritative_3D_Flight_Attitude_Design_Document.md)
