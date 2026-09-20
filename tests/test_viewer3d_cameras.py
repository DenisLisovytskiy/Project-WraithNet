"""Geometric tests for viewer3d camera controllers (Phase 17).

Skipped cleanly when panda3d is not installed. Uses window-type none so
Orbit/Chase/MissionBrief write to the dummy camera node.
"""

from __future__ import annotations

import math

import pytest

pytest.importorskip("panda3d")

from panda3d.core import NodePath, PerspectiveLens, Point2, Point3, loadPrcFileData

loadPrcFileData("", "window-type none")
loadPrcFileData("", "audio-library-name null")
loadPrcFileData("", "notify-level error")

from viewer3d.cameras import (
    SCENE_CAMERA_ELEVATION_DEG,
    ChaseCamera,
    MissionBriefCamera,
    OrbitCamera,
    _camera_node,
    scene_camera_radius,
)


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


def test_chase_camera_zoom_changes_follow_distance_and_height():
    base = _StubBase()
    chase = ChaseCamera(base, follow_distance=12.0, follow_height=5.0)
    chase.zoom(1.0)
    assert chase.follow_distance < 12.0
    assert chase.follow_height < 5.0
    chase.update(0.016, (50.0, 50.0, 8.0), 0.0)
    pos = _camera_node(base).get_pos()
    assert pos.y > 38.0
    assert pos.z < 13.0

    chase.zoom(-1.0)
    assert chase.follow_distance == pytest.approx(12.0)
    assert chase.follow_height == pytest.approx(5.0)


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


def test_mission_brief_camera_zoom_changes_orbit_radius():
    brief = MissionBriefCamera(_StubBase(), radius=70.0)
    brief.zoom(1.0)
    assert brief.radius < 70.0
    brief.zoom(-1.0)
    assert brief.radius == pytest.approx(70.0)


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


def test_orbit_camera_zoom_dollies_along_view():
    base = _StubBase()
    orbit = OrbitCamera(base, center=(50.0, 50.0, 0.0), radius=90.0)
    start = orbit._pos
    orbit.zoom(1.0)
    assert _distance(start, orbit._pos) == pytest.approx(orbit.wheel_step)
    assert _distance(orbit._pos, orbit.center) < _distance(start, orbit.center)


def test_scene_camera_radius_places_overview_beside_scene():
    width = 100.0
    height = 100.0
    center = (width / 2.0, height / 2.0, 0.0)
    radius = scene_camera_radius(width, height)
    base = _StubBase()
    orbit = OrbitCamera(base, center=center, radius=radius)
    x, y, z = orbit._pos

    # The camera is outside the battlefield footprint and looks in from a
    # low third-person angle instead of hovering in a top-down presentation.
    horizontal_distance = math.hypot(x - center[0], y - center[1])
    assert horizontal_distance > math.hypot(width, height) / 2.0
    assert math.degrees(math.atan2(z - center[2], horizontal_distance)) == pytest.approx(
        SCENE_CAMERA_ELEVATION_DEG
    )
    assert radius == pytest.approx(math.hypot(width, height) * 1.5)

    lens = PerspectiveLens()
    lens.set_aspect_ratio(16.0 / 9.0)
    camera = _camera_node(base)
    for corner in ((0.0, 0.0), (0.0, height), (width, 0.0), (width, height)):
        camera_point = camera.get_relative_point(
            base.render, Point3(corner[0], corner[1], 0.0)
        )
        screen_point = Point2()
        assert lens.project(camera_point, screen_point)
        assert abs(screen_point.x) <= 1.0
        assert abs(screen_point.y) <= 1.0


def test_mission_brief_uses_same_third_person_elevation():
    base = _StubBase()
    brief = MissionBriefCamera(base)
    assert brief._elevation_deg == pytest.approx(SCENE_CAMERA_ELEVATION_DEG)


def test_orbit_camera_moves_on_ground_plane():
    base = _StubBase()
    orbit = OrbitCamera(base, center=(50.0, 50.0, 0.0), radius=90.0)
    start = orbit._pos
    orbit._held = {"forward": True}
    orbit.update(1.0)
    after_forward = orbit._pos
    assert _distance(start, after_forward) == pytest.approx(OrbitCamera._MOVE_SPEED, abs=1e-3)
    # Move stays level — looking down must not dive into the map.
    assert after_forward[2] == pytest.approx(start[2], abs=1e-6)

    orbit._held = {"up": True}
    orbit.update(1.0)
    after_up = orbit._pos
    assert after_up[0] == pytest.approx(after_forward[0], abs=1e-6)
    assert after_up[1] == pytest.approx(after_forward[1], abs=1e-6)
    assert after_up[2] == pytest.approx(after_forward[2] + OrbitCamera._MOVE_SPEED, abs=1e-3)

def test_orbit_camera_reset_restores_original_framing():
    base = _StubBase()
    center = (50.0, 50.0, 0.0)
    radius = 90.0
    orbit = OrbitCamera(base, center=center, radius=radius)
    original = orbit._pos
    original_h = orbit._heading_deg
    original_p = orbit._pitch_deg
    orbit._held = {"forward": True, "up": True}
    orbit.update(2.0)
    orbit._heading_deg = (orbit._heading_deg + 45.0) % 360.0
    orbit._pitch_deg = max(OrbitCamera._PITCH_MIN, orbit._pitch_deg - 20.0)
    orbit._apply()
    assert _distance(orbit._pos, original) > 1.0
    orbit.reset()
    assert orbit._pos == pytest.approx(original, abs=1e-4)
    assert orbit._heading_deg == pytest.approx(original_h, abs=1e-3)
    assert orbit._pitch_deg == pytest.approx(original_p, abs=1e-3)
    cam = _camera_node(base)
    pos = cam.get_pos()
    assert _distance((pos.x, pos.y, pos.z), center) == pytest.approx(radius, abs=1e-3)
