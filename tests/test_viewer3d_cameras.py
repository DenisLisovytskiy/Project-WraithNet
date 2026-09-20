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
    _look_axes,
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


def _cos_angle(a, b) -> float:
    """Cosine between two vectors — 1.0 means "same direction"."""
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    return sum(x * y for x, y in zip(a, b)) / (na * nb)


def _delta(orbit, keys, seconds=1.0, fps=60.0):
    """Hold `keys` for `seconds` of 1/fps frames; return the net move."""
    start = orbit._pos
    orbit._held = keys
    for _ in range(int(seconds * fps)):
        orbit.update(1.0 / fps)
    return tuple(a - b for a, b in zip(orbit._pos, start))


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
    start_dist = _distance(start, orbit.center)
    orbit.zoom(1.0)
    assert _distance(orbit._pos, orbit.center) < start_dist
    # Direction from center preserved (dolly, not look-nudge). Compared as
    # unit vectors: a component-wise ratio divides by zero once the default
    # framing sits square on an axis.
    before = tuple(a - b for a, b in zip(start, orbit.center))
    after = tuple(a - b for a, b in zip(orbit._pos, orbit.center))
    assert _cos_angle(before, after) == pytest.approx(1.0, abs=1e-9)


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


def test_look_axes_match_panda3d():
    """Pin the HPR convention: Panda3D's positive heading turns LEFT.

    Deriving these by hand the intuitive way mirrors every camera-relative
    axis about +Y, which sent WASD off at an angle to what was on screen.
    """
    for heading, pitch in ((0.0, 0.0), (40.0, -25.0), (-115.0, 30.0), (200.0, -70.0)):
        node = NodePath("axis-probe")
        node.set_hpr(heading, pitch, 0.0)
        quat = node.get_quat()
        forward, right, up = _look_axes(heading, pitch)
        assert forward == pytest.approx(tuple(quat.get_forward()), abs=1e-6)
        assert right == pytest.approx(tuple(quat.get_right()), abs=1e-6)
        assert up == pytest.approx(tuple(quat.get_up()), abs=1e-6)


def test_orbit_camera_starts_looking_north_and_w_pans_north():
    """W must go due north (+Y) from the default framing, not off at an angle."""
    base = _StubBase()
    orbit = OrbitCamera(base, center=(50.0, 50.0, 0.0), radius=90.0)
    assert orbit._heading_deg == pytest.approx(0.0, abs=1e-6)

    north = _delta(orbit, {"forward": True})
    assert north[0] == pytest.approx(0.0, abs=1e-9)
    assert north[1] > 0.0

    # D is due east, with no north/south drift.
    orbit._vel = (0.0, 0.0, 0.0)
    east = _delta(orbit, {"right": True})
    assert east[1] == pytest.approx(0.0, abs=1e-9)
    assert east[0] > 0.0


def test_orbit_camera_pans_along_its_own_view_axes():
    base = _StubBase()
    orbit = OrbitCamera(base, center=(50.0, 50.0, 0.0), radius=90.0)
    _, right, up = _look_axes(orbit._heading_deg, orbit._pitch_deg)

    # W rides the camera's up axis, tilt included — not the flat ground plane.
    forward_delta = _delta(orbit, {"forward": True})
    assert _cos_angle(forward_delta, up) == pytest.approx(1.0, abs=1e-9)
    assert forward_delta[2] > 0.0

    orbit._vel = (0.0, 0.0, 0.0)
    strafe_delta = _delta(orbit, {"right": True})
    assert _cos_angle(strafe_delta, right) == pytest.approx(1.0, abs=1e-9)
    assert strafe_delta[2] == pytest.approx(0.0, abs=1e-9)

    # Space stays world up regardless of where the camera points.
    orbit._vel = (0.0, 0.0, 0.0)
    lift_delta = _delta(orbit, {"up": True})
    assert lift_delta[0] == pytest.approx(0.0, abs=1e-9)
    assert lift_delta[1] == pytest.approx(0.0, abs=1e-9)
    assert lift_delta[2] > 0.0


def test_orbit_camera_pan_axes_rotate_with_the_camera():
    base = _StubBase()
    orbit = OrbitCamera(base, center=(50.0, 50.0, 0.0), radius=90.0)
    before = _delta(orbit, {"right": True})

    orbit._vel = (0.0, 0.0, 0.0)
    orbit._heading_deg = (orbit._heading_deg + 90.0) % 360.0
    _, right, _up = _look_axes(orbit._heading_deg, orbit._pitch_deg)
    after = _delta(orbit, {"right": True})

    assert _cos_angle(after, right) == pytest.approx(1.0, abs=1e-9)
    # Turning 90° puts the strafe at a right angle to where it used to go.
    assert _cos_angle(before, after) == pytest.approx(0.0, abs=1e-6)


def test_orbit_camera_pan_speed_scales_with_height():
    base = _StubBase()
    orbit = OrbitCamera(base, center=(50.0, 50.0, 0.0), radius=90.0)
    x, y, z = orbit._pos
    high = _distance(_delta(orbit, {"right": True}), (0.0, 0.0, 0.0))

    orbit._vel = (0.0, 0.0, 0.0)
    orbit._pos = (x, y, z * 0.25)
    low = _distance(_delta(orbit, {"right": True}), (0.0, 0.0, 0.0))
    assert low < high * 0.5


def test_orbit_camera_eases_in_and_coasts_to_a_stop():
    base = _StubBase()
    orbit = OrbitCamera(base, center=(50.0, 50.0, 0.0), radius=90.0)
    step = 1.0 / 60.0

    # First frame of a key press moves far less than a frame at full speed.
    before = orbit._pos
    orbit._held = {"right": True}
    orbit.update(step)
    assert _distance(orbit._pos, before) < orbit.move_speed * step * 0.5

    for _ in range(60):
        orbit.update(step)
    before = orbit._pos
    orbit.update(step)
    cruising = _distance(orbit._pos, before)
    assert cruising == pytest.approx(orbit.move_speed * step, rel=0.02)

    # Releasing coasts rather than stopping dead, then settles exactly.
    orbit._held = {}
    before = orbit._pos
    orbit.update(step)
    assert 0.0 < _distance(orbit._pos, before) < cruising

    for _ in range(120):
        orbit.update(step)
    settled = orbit._pos
    orbit.update(step)
    assert orbit._pos == pytest.approx(settled, abs=1e-12)


def test_orbit_camera_smoothing_is_frame_rate_independent():
    slow = OrbitCamera(_StubBase(), center=(50.0, 50.0, 0.0), radius=90.0)
    fast = OrbitCamera(_StubBase(), center=(50.0, 50.0, 0.0), radius=90.0)
    # Strafe, not W: W changes altitude, which feeds the height-based speed
    # factor back into itself and leaves a ~1% integration difference.
    at_30 = _delta(slow, {"right": True}, seconds=0.5, fps=30.0)
    at_240 = _delta(fast, {"right": True}, seconds=0.5, fps=240.0)
    assert _distance(at_30, at_240) < 1e-9


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
