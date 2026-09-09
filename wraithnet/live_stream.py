"""Streams the same TraceHeader/TraceTick data TraceRecorder writes to a
file, live, over a TCP socket, to any number of connected clients -- see
docs/09_..._Design_Document.md §5 for the full protocol rationale.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from wraithnet.trace_builders import build_trace_header, build_trace_tick

if TYPE_CHECKING:
    from wraithnet.bridge import WraithNetBridge


class LiveStreamServer:
    def __init__(self, host: str = "127.0.0.1", port: int = 8765) -> None:
        self._host = host
        self._port = port
        self._server: asyncio.AbstractServer | None = None
        self._writers: list[asyncio.StreamWriter] = []
        self._backlog: list[str] = []  # every JSON line ever sent, header first
        self._closed = False
        self._previously_seen_outcome_ids: set[str] = set()

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        """The port the server is bound to. After start() with port=0 this is
        the ephemeral port assigned by the OS, not the constructor argument."""
        if self._server is None or not self._server.sockets:
            return self._port
        return int(self._server.sockets[0].getsockname()[1])

    async def start(self) -> None:
        """Must be awaited once before record_header/record_tick are called.
        Binds and starts listening; does not block."""
        self._server = await asyncio.start_server(
            self._handle_client, self._host, self._port
        )
        self._closed = False

    async def _handle_client(
        self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter
    ) -> None:
        # Send the full backlog first, then subscribe for live ticks. The
        # snapshot-and-subscribe is synchronous so a record_* call on this
        # same event-loop thread cannot slip a line between the two.
        try:
            for line in list(self._backlog):
                writer.write(line.encode("utf-8"))
            self._writers.append(writer)
            await writer.drain()
            while True:
                data = await reader.read(1024)
                if not data:
                    break
        except (ConnectionResetError, ConnectionAbortedError, BrokenPipeError):
            pass
        except asyncio.CancelledError:
            raise
        finally:
            if writer in self._writers:
                self._writers.remove(writer)
            try:
                writer.close()
            except Exception:
                pass

    def record_header(self, bridge: WraithNetBridge) -> None:
        header = build_trace_header(bridge)
        self._send({"type": "header", "data": header.model_dump(mode="json")})

    def record_tick(self, bridge: WraithNetBridge) -> None:
        tick = build_trace_tick(
            bridge, seen_outcome_ids=self._previously_seen_outcome_ids
        )
        self._send({"type": "tick", "data": tick.model_dump(mode="json")})

    def _send(self, message: dict) -> None:
        line = json.dumps(message) + "\n"
        self._backlog.append(line)
        payload = line.encode("utf-8")
        for w in list(self._writers):
            try:
                w.write(payload)
            except Exception:
                self._writers.remove(w)

    async def wait_for_client_or_timeout(self, timeout: float = 10.0) -> bool:
        """Keep the backlog available briefly for late-connecting viewers."""
        loop = asyncio.get_running_loop()
        deadline = loop.time() + timeout
        while loop.time() < deadline:
            if self._writers:
                # Give the client a moment to drain the backlog.
                await asyncio.sleep(0.25)
                return True
            await asyncio.sleep(0.05)
        return False

    async def close(self) -> None:
        if self._closed:
            return
        self._send({"type": "end"})
        writers = list(self._writers)
        for w in writers:
            try:
                await w.drain()
            except Exception:
                pass
            w.close()
        for w in writers:
            try:
                await w.wait_closed()
            except Exception:
                pass
        self._writers.clear()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        self._closed = True
