"""Spawns wraithnet.main as a subprocess with --scenario and --live, and
hands back everything viewer3d/app.py needs to connect a StreamClient to
it. Deliberately a subprocess, not a thread or in-process import -- keeps
the sim's asyncio event loop and viewer3d's Panda3D loop in genuinely
separate processes/interpreters, matching design doc §1/§9's "cross-
process" decision: a sim crash can't take the viewer down, and vice versa.

Port strategy: each launch binds an ephemeral TCP port (host, 0) and
passes that as --live-port, so a leftover sim from a previous launch
cannot collide with a new one. Launching again while one sim is still
running terminate-and-replaces the previous handle (one live view at a
time -- see Phase 19 non-goals).

Each subprocess gets its own temporary cwd so concurrent launches do not
fight over results/trace.jsonl; PYTHONPATH is pointed at the project root
so `python -m wraithnet.main` still resolves.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import threading
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def _ephemeral_port(host: str = "127.0.0.1") -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((host, 0))
        return int(sock.getsockname()[1])


class SimLaunchHandle:
    def __init__(self, process: subprocess.Popen, host: str, port: int) -> None:
        self.process = process
        self.host = host
        self.port = port
        self._stderr_lines: list[str] = []
        if process.stderr is not None:
            threading.Thread(target=self._drain_stderr, daemon=True).start()

    def _drain_stderr(self) -> None:
        try:
            assert self.process.stderr is not None
            for line in self.process.stderr:
                self._stderr_lines.append(line)
        except (OSError, ValueError):
            return

    def is_running(self) -> bool:
        return self.process.poll() is None

    def stderr_tail(self, n: int = 6) -> str:
        return "".join(self._stderr_lines[-n:]).strip()

    def terminate(self) -> None:
        if not self.is_running():
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)


def launch_scenario(
    scenario_path: str,
    host: str = "127.0.0.1",
    port: int | None = None,
) -> SimLaunchHandle:
    if port is None:
        port = _ephemeral_port(host)
    scenario_path = str(Path(scenario_path).resolve())
    cwd = tempfile.mkdtemp(prefix="wraithnet-launch-")
    env = os.environ.copy()
    root = str(_project_root())
    env["PYTHONPATH"] = root + os.pathsep + env.get("PYTHONPATH", "")
    kwargs: dict = dict(
        args=[
            sys.executable,
            "-m",
            "wraithnet.main",
            "--scenario",
            scenario_path,
            "--live",
            "--live-host",
            host,
            "--live-port",
            str(port),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        cwd=cwd,
        env=env,
    )
    if sys.platform == "win32":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    process = subprocess.Popen(**kwargs)
    return SimLaunchHandle(process, host, port)
