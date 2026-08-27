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

## Documentation

- Authoritative design: [`docs/6th-Gen_Warfare_Sim_Design_Document_v2.md`](docs/6th-Gen_Warfare_Sim_Design_Document_v2.md)
- Build plan (phases 0–5): [`docs/phases/`](docs/phases/)
