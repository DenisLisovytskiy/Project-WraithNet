"""A TraceSink receives the same header/tick data TraceRecorder already
writes to a file (Phase 11) via some other transport. wraithnet/bridge.py
holds a list of these instead of a single optional recorder, so a run can
write to a file AND stream live at the same time, or either alone.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from wraithnet.bridge import WraithNetBridge


@runtime_checkable
class TraceSink(Protocol):
    def record_header(self, bridge: WraithNetBridge) -> None: ...
    def record_tick(self, bridge: WraithNetBridge) -> None: ...
    def close(self) -> None: ...
