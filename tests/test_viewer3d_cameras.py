"""Geometric tests for viewer3d camera controllers (Phase 17).

Skipped cleanly when panda3d is not installed. Uses window-type none so
Orbit/Chase/MissionBrief write to the dummy camera node.
"""

from __future__ import annotations

import math

import pytest

pytest.importorskip("panda3d")

from panda3d.core import NodePath, loadPrcFileData

loadPrcFileData("", "window-type none")
loadPrcFileData("", "audio-library-name null")
loadPrcFileData("", "notify-level error")

from viewer3d.cameras import ChaseCamera, MissionBriefCamera, OrbitCamera, _camera_node


class _StubBase:
    camera = None
    mouseWatcherNode = None
    cam = None

    def __init__(self) -> None:
        self.render = NodePath("camera-test-root")
        self._viewer3d_dummy_camera = None


def _distance(a, b) -> float:
    return math.sqrt(sum((x - y) ** 2 for x, y in zip(a, b)))


def test_chase_camera_trails_behind_heading_zero():
    base = _StubBase()
    chase = ChaseCamera(base, follow_distance=12.0, follow_height=5.0)
    chase.update(0.016, (50.0, 50.0, 8.0), 0.0)
    cam = _camera_node(base)
    pos = cam.get_pos()
    assert (pos.x, pos.y, pos.z) == pytest.approx((50.0, 38.0, 13.0), abs=1e-4)


def test_chase_camera_heading_90_moves_along_x():
    base = _StubBase()
    chase = ChaseCamera(base, follow_distance=12.0, follow_height=5.0)
    chase.update(0.016, (50.0, 50.0, 8.0), 90.0)
    cam = _camera_node(base)
    pos = cam.get_pos()
    assert (pos.x, pos.y, pos.z) == pytest.approx((38.0, 50.0, 13.0), abs=1e-4)


def test_mission_brief_camera_advances_angle_and_moves():
    base = _StubBase()
    brief = MissionBriefCamera(
        base, center=(50.0, 50.0, 0.0), radius=70.0, angular_speed_deg_s=6.0
    )
    start_angle = brief._angle_deg
    brief.update(1.0)
    cam = _camera_node(base)
    first = cam.get_pos()
    assert brief._angle_deg == pytest.approx((start_angle + 6.0) % 360.0)
    brief.update(1.0)
    second = cam.get_pos()
    assert brief._angle_deg == pytest.approx((start_angle + 12.0) % 360.0)
    assert (first.x, first.y) != pytest.approx((second.x, second.y), abs=1e-6)


def test_orbit_camera_sits_on_radius_and_above_center():
    base = _StubBase()
    center = (50.0, 50.0, 0.0)
    radius = 90.0
    orbit = OrbitCamera(base, center=center, radius=radius)
    orbit._apply()
    cam = _camera_node(base)
    pos = cam.get_pos()
    dist = _distance((pos.x, pos.y, pos.z), center)
    assert dist == pytest.approx(radius, abs=1e-3)
    assert pos.z > center[2]
