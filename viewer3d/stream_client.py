"""Background-thread TCP client for the live tick stream (Phase 15). Reads
newline-delimited JSON, decodes into TraceHeader/TraceTick via the exact
same Pydantic models the simulation side uses, and pushes them onto a
thread-safe queue for the Panda3D main thread to drain once per frame.

Deliberately uses a plain blocking socket + threading.Thread, not asyncio --
see docs/09_..._Design_Document.md §5 for why (Panda3D's main loop is not
asyncio-native; a drained queue is the simplest reliable pattern).
"""

from __future__ import annotations

import json
import queue
import socket
import threading
from dataclasses import dataclass
from typing import Union

from wraithnet.trace_schemas import TraceHeader, TraceTick


@dataclass(frozen=True)
class HeaderEvent:
    header: TraceHeader


@dataclass(frozen=True)
class TickEvent:
    tick: TraceTick


@dataclass(frozen=True)
class EndEvent:
    pass


@dataclass(frozen=True)
class DisconnectedEvent:
    """Pushed if the connection drops or the connect attempt fails after
    retrying -- lets the scene/app show a clear connection-status state
    instead of silently freezing on the last received frame."""
    reason: str


StreamEvent = Union[HeaderEvent, TickEvent, EndEvent, DisconnectedEvent]


class StreamClient:
    def __init__(
        self,
        host: str,
        port: int,
        connect_retries: int = 20,
        retry_delay_s: float = 0.5,
    ) -> None:
        self._host = host
        self._port = port
        self._connect_retries = connect_retries
        self._retry_delay_s = retry_delay_s
        self.events: queue.Queue[StreamEvent] = queue.Queue()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._stop = threading.Event()

    def start(self) -> None:
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _run(self) -> None:
        sock = self._connect_with_retry()
        if sock is None:
            self.events.put(DisconnectedEvent(reason="could not connect after retries"))
            return
        buffer = b""
        try:
            sock.settimeout(0.5)
            with sock:
                while not self._stop.is_set():
                    try:
                        chunk = sock.recv(65536)
                    except socket.timeout:
                        continue
                    if not chunk:
                        break
                    buffer += chunk
                    while b"\n" in buffer:
                        line, buffer = buffer.split(b"\n", 1)
                        self._handle_line(line)
        except OSError as exc:
            self.events.put(DisconnectedEvent(reason=str(exc)))
            return
        self.events.put(DisconnectedEvent(reason="connection closed"))

    def _connect_with_retry(self) -> socket.socket | None:
        for attempt in range(self._connect_retries):
            if self._stop.is_set():
                return None
            try:
                return socket.create_connection(
                    (self._host, self._port), timeout=self._retry_delay_s
                )
            except (ConnectionRefusedError, OSError, TimeoutError, socket.timeout):
                if attempt + 1 >= self._connect_retries:
                    return None
                if self._stop.wait(self._retry_delay_s):
                    return None
        return None

    def _handle_line(self, line: bytes) -> None:
        if not line.strip():
            return
        message = json.loads(line)
        if message["type"] == "header":
            self.events.put(HeaderEvent(header=TraceHeader.model_validate(message["data"])))
        elif message["type"] == "tick":
            self.events.put(TickEvent(tick=TraceTick.model_validate(message["data"])))
        elif message["type"] == "end":
            self.events.put(EndEvent())
