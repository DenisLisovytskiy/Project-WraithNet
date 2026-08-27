"""Loads a recorded trace (Phase 11's wraithnet/recorder.py output) for the
Pygame viewer. Imports only wraithnet.trace_schemas + stdlib -- never pygame.
"""

from pathlib import Path

from wraithnet.trace_schemas import TraceHeader, TraceTick


def load_trace(path: str | Path) -> tuple[TraceHeader, list[TraceTick]]:
    lines = Path(path).read_text(encoding="utf-8").splitlines()
    if not lines:
        raise ValueError(f"Trace file {path} is empty")
    header = TraceHeader.model_validate_json(lines[0])
    ticks = [TraceTick.model_validate_json(line) for line in lines[1:]]
    if not ticks:
        raise ValueError(f"Trace file {path} has a header but no recorded ticks")
    return header, ticks
