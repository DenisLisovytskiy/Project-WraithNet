"""Optional trace recorder for the replay viewer. Attaching a
TraceRecorder to a WraithNetBridge has zero effect on simulation logic,
timing, or determinism -- see docs/07_Visualization_Design_Document.md §4
and this phase's acceptance criteria for the tests that prove it.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from wraithnet.trace_builders import build_trace_header, build_trace_tick

if TYPE_CHECKING:
    from wraithnet.bridge import WraithNetBridge


class TraceRecorder:
    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self._path.open("w", encoding="utf-8")

    def record_header(self, bridge: WraithNetBridge) -> None:
        header = build_trace_header(bridge)
        self._file.write(header.model_dump_json() + "\n")
        self._file.flush()

    def record_tick(self, bridge: WraithNetBridge) -> None:
        tick = build_trace_tick(bridge)
        self._file.write(tick.model_dump_json() + "\n")
        self._file.flush()

    def close(self) -> None:
        self._file.close()
