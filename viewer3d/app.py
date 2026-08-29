"""python -m viewer3d.app <host> <port>

Minimal skeleton for this phase: connects, builds the static scene the
moment a header arrives, and updates it once per frame by draining
whatever tick events have queued up since the last frame. One fixed
camera. No HUD, no camera modes, no editor -- see Phase 17+.
"""

from __future__ import annotations

import sys

from panda3d.core import loadPrcFileData

loadPrcFileData("", "window-title WraithNet 3D")
loadPrcFileData("", "audio-library-name null")

from direct.showbase.ShowBase import ShowBase

from viewer3d.scene import build_static_scene, update_tick
from viewer3d.stream_client import (
    DisconnectedEvent,
    EndEvent,
    HeaderEvent,
    StreamClient,
    TickEvent,
)


class WraithNetViewer3D(ShowBase):
    def __init__(self, host: str, port: int) -> None:
        super().__init__()
        self.disable_mouse()  # replaced by a real camera controller in Phase 17
        if self.win is not None:
            self.set_background_color(0.02, 0.03, 0.06, 1.0)
        if self.camera is not None:
            self.camera.set_pos(50, -80, 60)
            self.camera.look_at(50, 50, 0)

        self._client = StreamClient(host, port)
        self._client.start()
        self._header = None
        self._handles = None
        self._latest_tick = None

        self.task_mgr.add(self._drain_queue_task, "drain-stream-queue")

    def _drain_queue_task(self, task):
        while not self._client.events.empty():
            event = self._client.events.get_nowait()
            if isinstance(event, HeaderEvent):
                self._header = event.header
                self._handles = build_static_scene(self.render, self._header)
            elif isinstance(event, TickEvent):
                self._latest_tick = event.tick
                if self._handles is not None:
                    update_tick(self._handles, self._header, event.tick)
            elif isinstance(event, (EndEvent, DisconnectedEvent)):
                pass  # Phase 17's HUD will surface this; for now, no-op
        return task.cont


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: python -m viewer3d.app <host> <port>")
        sys.exit(1)
    app = WraithNetViewer3D(sys.argv[1], int(sys.argv[2]))
    app.run()


if __name__ == "__main__":
    main()
