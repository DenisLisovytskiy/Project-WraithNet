# Phase 15 — Live Tick Streaming Protocol

> **How to use this document:** This is a complete, standalone prompt. Give an LLM coding agent this entire file as its task. Assume Phases 0–14 are complete and passing (scenarios are data-driven; `python -m wraithnet.main --scenario <path>` works). Read `docs/09_3D_Live_Visualization_and_Scenario_Editor_Design_Document.md` §5 in full before starting. This phase adds a second, optional way to consume the exact same `TraceHeader`/`TraceTick` data `wraithnet/trace_schemas.py` (Phase 11) already defines — over a TCP socket, as it's produced, instead of/in addition to a file. **This phase does not touch Panda3D, does not add any GUI, and does not change simulation behavior.** It is pure `asyncio` + Pydantic, exactly like Phase 11 was pure-Python for the file recorder.

## Context

Phases 11–13 gave WraithNet a *replay* viewer: run the sim, get a `results/trace.jsonl` file, then load and animate that file afterward with `viewer/app.py`. This phase makes it possible to watch a run **as it happens**, from a separate process, without ever touching a file — the foundation Phase 16's Panda3D viewer needs. It does this by generalizing `wraithnet/bridge.py`'s single `recorder` hook into a list of interchangeable "sinks," and adding a new sink that pushes each header/tick over a TCP connection to whoever is listening, instead of (or alongside) writing it to disk.

## Part A — `wraithnet/trace_sink.py` (new file): the `TraceSink` protocol

```python
"""A TraceSink receives the same header/tick data TraceRecorder already
writes to a file (Phase 11) via some other transport. wraithnet/bridge.py
holds a list of these instead of a single optional recorder, so a run can
write to a file AND stream live at the same time, or either alone.
"""

from typing import Protocol, runtime_checkable

from wraithnet.bridge import WraithNetBridge  # for typing only


@runtime_checkable
class TraceSink(Protocol):
    def record_header(self, bridge: "WraithNetBridge") -> None: ...
    def record_tick(self, bridge: "WraithNetBridge") -> None: ...
    def close(self) -> None: ...
```

Use the same `TYPE_CHECKING`/forward-reference discipline Phase 11 established for `recorder.py`'s `WraithNetBridge` reference — do not introduce an eager circular import between `trace_sink.py` and `bridge.py`.

`wraithnet/recorder.py::TraceRecorder` is **not modified** — it already exposes exactly these three methods with this exact shape; it now simply satisfies `TraceSink` structurally (Python `Protocol`s don't require inheritance).

## Part B — `wraithnet/live_stream.py` (new file): `LiveStreamServer`

```python
"""Streams the same TraceHeader/TraceTick data TraceRecorder writes to a
file, live, over a TCP socket, to any number of connected clients -- see
docs/09_..._Design_Document.md §5 for the full protocol rationale.
"""

from __future__ import annotations

import asyncio
import json
from typing import TYPE_CHECKING

from wraithnet.trace_schemas import TraceHeader, TraceTick

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

    async def start(self) -> None:
        """Must be awaited once before record_header/record_tick are called.
        Binds and starts listening; does not block."""
        self._server = await asyncio.start_server(self._handle_client, self._host, self._port)

    async def _handle_client(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        # On connect: send the full backlog immediately (catch-up), then keep
        # `writer` around for future record_header/record_tick calls to also
        # write to. Detach it from self._writers on disconnect/error so a
        # dead client doesn't accumulate forever or crash future sends.
        ...

    def record_header(self, bridge: "WraithNetBridge") -> None:
        header = TraceHeader(...)          # identical construction to TraceRecorder.record_header
        self._send({"type": "header", "data": header.model_dump()})

    def record_tick(self, bridge: "WraithNetBridge") -> None:
        tick = TraceTick(...)              # identical construction to TraceRecorder.record_tick
        self._send({"type": "tick", "data": tick.model_dump()})

    def _send(self, message: dict) -> None:
        line = json.dumps(message) + "\n"
        self._backlog.append(line)
        for w in list(self._writers):
            try:
                w.write(line.encode("utf-8"))
            except Exception:
                self._writers.remove(w)  # drop dead connections silently

    async def close(self) -> None:
        self._send({"type": "end"})
        for w in list(self._writers):
            w.close()
        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
        self._closed = True
```

Fill in `_handle_client` and the two `record_*` methods' construction logic — **do not duplicate the field-mapping logic from `wraithnet/recorder.py::TraceRecorder.record_header`/`record_tick`**. Either:

* (a) factor the shared "build a `TraceHeader`/`TraceTick` from a `WraithNetBridge`" logic out of `TraceRecorder` into two small module-level functions in `wraithnet/trace_schemas.py` (or a new `wraithnet/trace_builders.py`) that both `TraceRecorder` and `LiveStreamServer` call, or
* (b) have `LiveStreamServer` construct a `TraceRecorder`-like object internally purely to reuse its builder methods without writing to a file.

Pick (a) — it's cleaner and the design doc's whole premise (§2) is that both sinks consume the *same* data; a shared builder makes that structurally true instead of "true by convention, currently, until someone edits one copy and not the other." Update `wraithnet/recorder.py` to call the same shared builder functions, and add a regression test proving `TraceRecorder` and `LiveStreamServer` produce byte-identical `TraceHeader`/`TraceTick` JSON for the same bridge state (see Part E).

**Wire format**, one JSON object per line, `\n`-terminated:

```json
{"type": "header", "data": { ...TraceHeader fields... }}
{"type": "tick",   "data": { ...TraceTick fields...   }}
{"type": "end"}
```

**Backfill-on-connect is mandatory, not an optimization**: a client that connects after the header and 10 ticks have already been sent must receive the header and all 10 ticks (in order) immediately upon connecting, before any new live ticks. This is what makes "connect early," "connect mid-run," and "connect after it's finished" all work identically (design doc §5) — do not skip this to save code; it's the single most important behavior this phase implements.

## Part C — `wraithnet/bridge.py` changes

Replace the single `recorder: TraceRecorder | None` parameter with `sinks: list[TraceSink] | None = None`:

```python
def __init__(
    self,
    sinks: list[TraceSink] | None = None,
    scenario: ScenarioConfig | None = None,
) -> None:
    self.broker = MessageBroker()
    self.model = WraithNetModel(scenario=scenario)
    self.agents: dict[str, JetAgentRuntime] = {
        agent_id: JetAgentRuntime(agent_id, self.broker)
        for agent_id in self.model.jets
    }
    self._tasks: list[asyncio.Task] = []
    self.sinks: list[TraceSink] = sinks or []
    for sink in self.sinks:
        sink.record_header(self)
        sink.record_tick(self)  # tick-0 baseline
```

And in `_tick()`, replace the single `if self.recorder is not None: self.recorder.record_tick(self)` with:

```python
for sink in self.sinks:
    sink.record_tick(self)
```

**Breaking rename, deliberately**: this removes the `recorder` keyword argument entirely in favor of `sinks`. Update every call site in the codebase (`wraithnet/main.py`, and any test that currently does `WraithNetBridge(recorder=...)`) to pass `sinks=[...]` instead. Search the whole repo for `recorder=` before considering this phase done.

## Part D — `wraithnet/main.py` changes

```python
def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the WraithNet PoC scenario.")
    parser.add_argument("--scenario", type=str, default=None, help="...")  # from Phase 14
    parser.add_argument("--live", action="store_true", help="Also stream ticks live over TCP.")
    parser.add_argument("--live-host", type=str, default="127.0.0.1")
    parser.add_argument("--live-port", type=int, default=8765)
    return parser.parse_args()


async def run_scenario(scenario=None, live: bool = False, live_host="127.0.0.1", live_port=8765) -> WraithNetBridge:
    recorder = TraceRecorder("results/trace.jsonl")
    sinks: list[TraceSink] = [recorder]
    live_server = None
    if live:
        live_server = LiveStreamServer(live_host, live_port)
        await live_server.start()
        sinks.append(live_server)
        print(f"Live stream listening on {live_host}:{live_port}")

    bridge = WraithNetBridge(sinks=sinks, scenario=scenario)
    try:
        await bridge.run()
    finally:
        recorder.close()
        if live_server is not None:
            await live_server.close()
    return bridge
```

`results/trace.jsonl` is still always produced (the file sink is unconditional) — `--live` only *adds* the socket sink; it does not replace the file. This preserves the existing `viewer/app.py` (2D replay) workflow completely unchanged, per the design doc's §3 guarantee.

## Part E — Tests

### `tests/test_live_stream.py` (new file)

Write a small, **Panda3D-free** test client for this file only (do not put it in `viewer3d/` — that package doesn't exist yet and shouldn't be created by this phase):

```python
async def _read_stream_messages(host: str, port: int, count: int) -> list[dict]:
    """Connects, reads `count` newline-delimited JSON messages, returns them
    decoded. Test-only helper -- not part of the production package."""
    reader, writer = await asyncio.open_connection(host, port)
    messages = []
    for _ in range(count):
        line = await reader.readline()
        messages.append(json.loads(line))
    writer.close()
    return messages
```

Using this helper (or equivalent):

* Start a `LiveStreamServer` on an ephemeral port (`port=0` if `asyncio.start_server` supports it on this Python/OS combination — check; if not, pick a high, unlikely-to-collide fixed test port and accept that as a minor limitation), attach it as a `WraithNetBridge` sink, run a few ticks, and confirm a client connected *before* the run started receives, in order: one `header` message matching the bridge's actual scenario data, then one `tick` message per `record_tick` call, with `data.tick` values non-decreasing starting at `0`.
* **Backfill test (the most important test in this phase)**: run a bridge with a `LiveStreamServer` sink for several ticks *before* any client connects, **then** connect a client — assert it immediately receives the full header + all-ticks-so-far backlog, in the correct order, without needing the sim to advance any further.
* **Multi-client fan-out**: connect two clients at different times (one early, one after a few ticks); both eventually see the complete, identical sequence of messages by the time the run ends.
* **Byte-identical builder test**: run the same fixed scenario twice — once with only a `TraceRecorder` sink, once with only a `LiveStreamServer` sink (client reading its messages via the helper above) — and assert the recorder's file content and the live stream's received `tick`/`header` payloads describe the same data (compare the parsed Pydantic objects field-by-field, not raw JSON text, since key ordering isn't guaranteed to match — see Part B's shared-builder requirement).
* **`end` message**: after `await live_server.close()`, any already-connected client eventually reads a final `{"type": "end"}` line.
* **Zero effect on simulation behavior**: `summarize(bridge.model)` is identical whether `sinks=[]`, `sinks=[TraceRecorder(...)]`, `sinks=[LiveStreamServer(...)]`, or all of the above together — same pattern as Phase 11's `test_recorder_has_zero_effect_on_summarize`, extended to cover the new sink and combinations.

### `tests/test_recorder.py` / `tests/test_bridge.py` updates

* Update every existing `WraithNetBridge(recorder=...)` call site to `WraithNetBridge(sinks=[...])` per Part C's rename. Do not leave any test passing the old `recorder=` keyword — it no longer exists.

### `tests/test_main_cli.py` (new, or extend an existing CLI test if Phase 14 created one)

* `python -m wraithnet.main --live --live-port <ephemeral>` starts, prints the "Live stream listening on ..." line, and still completes and prints the normal summary (use a short timeout/`subprocess` pattern; do not require an actual client to connect for the run to complete — a live server with zero connected clients must behave identically to having no live server at all, other than the extra `_send` calls being no-ops against an empty `_writers` list).

## Explicit non-goals for this phase

* No Panda3D, no `viewer3d/` package — Phase 16.
* No authentication, encryption, or non-`localhost` binding by default (design doc §10) — `host`/`port` are configurable, but nothing in this phase needs to exercise anything beyond `127.0.0.1`.
* No reconnection/resume-from-a-specific-tick logic beyond full backfill — a client either gets everything from the start, or (design doc's non-goal) nothing; there is no partial/"resume from tick N" protocol.
* No change to `wraithnet/agents.py`, `wraithnet/broker.py`, `wraithnet/world.py` — this phase only changes how/where already-existing state is exported, exactly like Phase 11 did.

## Acceptance criteria

1. `pytest` passes in full, including `tests/test_live_stream.py`, with **no new dependency** beyond what Phase 0–14 already required (`asyncio` and `json` are stdlib).
2. `python -m wraithnet.main` (no `--live`) behaves identically to before this phase — same summary, same `results/trace.jsonl`.
3. `python -m wraithnet.main --live` runs to completion, still produces `results/trace.jsonl`, and a manually-attached test client (or the test-helper pattern from Part E, run interactively if you want to sanity-check by hand) receives the full header+tick sequence.
4. The backfill test and the byte-identical-builder test (Part E) both pass — these are the two properties Phase 16 will depend on being true.
5. `grep -rn "recorder=" wraithnet/ tests/` returns no matches — the rename to `sinks=` is complete everywhere.
