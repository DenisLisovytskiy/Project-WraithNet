"""Camera controllers for viewer3d. All three read jet/target positions
from the same SceneHandles Phase 16 already builds/updates -- no new scene
data is needed, only new ways of pointing a camera at it.

Jet selection for chase mode uses Tab/N cycling over `header.jet_ids`
rather than 3D collision picking (Phase 17's explicitly sanctioned
fallback -- picking is fiddly under `window-type none` and not required).

Orbit mode is a free-look camera (WASD pan, +/- and wheel zoom, LMB look)
that starts framed on the map and can `reset()` to that framing.
"""

from __future__ import annotations

import math

from panda3d.core import KeyboardButton, MouseButton, NodePath, Point2


def _camera_node(base) -> NodePath:
    """Return ShowBase.camera, or a dummy NodePath when headless.

    Phase 16 established that `window-type none` leaves `ShowBase.camera`
    as None. Controllers still call set_pos/look_at so tests can assert
    the transform changed; the dummy lives on `base._viewer3d_dummy_camera`.
    """
    cam = getattr(base, "camera", None)
    if cam is not None:
        return cam
    dummy = getattr(base, "_viewer3d_dummy_camera", None)
    if dummy is not None:
        return dummy
    render = getattr(base, "render", None)
    dummy = (
        render.attach_new_node("viewer3d-dummy-camera")
        if render is not None
        else NodePath("viewer3d-dummy-camera")
    )
    base._viewer3d_dummy_camera = dummy
    return dummy


def _orbit_pose(center, radius: float, azimuth_deg: float, elevation_deg: float):
    """World position on a sphere looking toward `center` (map framing)."""
    az = math.radians(azimuth_deg)
    el = math.radians(elevation_deg)
    cx, cy, cz = center
    x = cx + radius * math.cos(el) * math.sin(az)
    y = cy - radius * math.cos(el) * math.cos(az)
    z = cz + radius * math.sin(el)
    return (x, y, z)


# Keep overview cameras beside the battlefield, at a recognisably
# third-person angle, rather than almost directly above it.  At 1.5 map
# diagonals the complete ground plane fits inside Panda3D's default
# perspective lens on a widescreen window with a useful amount of margin.
SCENE_CAMERA_RADIUS_DIAGONALS = 1.5
SCENE_CAMERA_ELEVATION_DEG = 25.0
_ZOOM_FACTOR = 0.85


def scene_camera_radius(
    grid_width: float,
    grid_height: float,
    *,
    minimum: float = 90.0,
) -> float:
    """Return a perspective overview distance that frames the whole scene."""
    diagonal = math.hypot(float(grid_width), float(grid_height))
    return max(float(minimum), diagonal * SCENE_CAMERA_RADIUS_DIAGONALS)


def _zoom_distance(current: float, original: float, steps: float) -> float:
    """Scale a camera distance with practical near/far limits."""
    value = float(current) * (_ZOOM_FACTOR ** float(steps))
    return max(float(original) * 0.2, min(float(original) * 4.0, value))


def _look_axes(heading_deg: float, pitch_deg: float):
    """Unit forward / right / up for a Panda3D camera (HPR, no roll).

    Heading 0 looks along +Y; positive heading toward +X. Used for mouse-wheel
    nudges along the true look vector.
    """
    h = math.radians(heading_deg)
    p = math.radians(pitch_deg)
    ch, sh = math.cos(h), math.sin(h)
    cp, sp = math.cos(p), math.sin(p)
    forward = (sh * cp, ch * cp, sp)
    right = (ch, -sh, 0.0)
    up = (-sh * sp, -ch * sp, cp)
    return forward, right, up


def _ground_move_axes(heading_deg: float):
    """Horizontal forward/right from yaw only — movement slides over the map.

    Ignores pitch so looking down at the battlefield still moves along the
    ground plane instead of diving into it. Q/E use world +Z separately.
    """
    h = math.radians(heading_deg)
    ch, sh = math.cos(h), math.sin(h)
    forward = (sh, ch, 0.0)
    right = (ch, -sh, 0.0)
    return forward, right


class OrbitCamera:
    """Free-look tactical camera. Starts framed on the map center; LMB looks,
    WASD pans on the ground plane, +/- and wheel zoom toward the framed center.
    `reset()` restores the original framing.
    """

    _DEFAULT_AZIMUTH_DEG = 40.0
    _DEFAULT_ELEVATION_DEG = SCENE_CAMERA_ELEVATION_DEG
    _MOVE_SPEED = 55.0
    _FAST_MULTIPLIER = 2.5
    _LOOK_GAIN = 90.0
    _PITCH_MIN = -89.0
    _PITCH_MAX = 89.0
    _WHEEL_STEP = 8.0

    def __init__(self, base, center=(50.0, 50.0, 0.0), radius=90.0, scale=1.0) -> None:
        self.base = base
        self.center = tuple(float(c) for c in center)
        self.radius = float(radius)
        # Movement is in world units, so it has to grow with the battlespace:
        # 55 u/s crosses the PoC map in ~2s and a 5000-unit one in 91s.
        self.scale = max(1.0, float(scale))
        self.move_speed = self._MOVE_SPEED * self.scale
        self.wheel_step = self._WHEEL_STEP * self.scale
        self._default_center = self.center
        self._default_radius = self.radius
        self._pos = (0.0, 0.0, 0.0)
        self._heading_deg = 0.0
        self._pitch_deg = 0.0
        self._last_mouse: Point2 | None = None
        # Test-injected held keys; when None, live input is polled each frame.
        self._held: dict[str, bool] | None = None
        self.reset()

    def reset(self) -> None:
        """Snap back to the original map-centered framing."""
        self.center = self._default_center
        self.radius = self._default_radius
        self._pos = _orbit_pose(
            self._default_center,
            self._default_radius,
            self._DEFAULT_AZIMUTH_DEG,
            self._DEFAULT_ELEVATION_DEG,
        )
        cam = _camera_node(self.base)
        cam.set_pos(*self._pos)
        cx, cy, cz = self._default_center
        cam.look_at(cx, cy, cz)
        hpr = cam.get_hpr()
        self._heading_deg = float(hpr[0])
        self._pitch_deg = float(hpr[1])
        self._last_mouse = None
        self._apply()

    def nudge_along_look(self, steps: float) -> None:
        """Deprecated alias — prefer :meth:`zoom` (true dolly toward center)."""
        self.zoom(steps)

    def zoom(self, steps: float) -> None:
        """Dolly toward/away from the framed map center; positive steps zoom in.

        A look-axis nudge was nearly invisible on large maps and felt broken.
        Scaling distance to ``center`` matches chase/brief zoom behavior.
        """
        cx, cy, cz = self.center
        x, y, z = self._pos
        dx, dy, dz = x - cx, y - cy, z - cz
        dist = math.sqrt(dx * dx + dy * dy + dz * dz) or 1.0
        new_dist = _zoom_distance(dist, self._default_radius, steps)
        scale = new_dist / dist
        self._pos = (cx + dx * scale, cy + dy * scale, cz + dz * scale)
        self._apply()

    def update(self, dt: float) -> None:
        self._read_mouse_look()
        self._apply_movement(dt)
        self._apply()

    def _read_mouse_look(self) -> None:
        mw = getattr(self.base, "mouseWatcherNode", None)
        if mw is None:
            self._last_mouse = None
            return
        try:
            if not mw.has_mouse():
                self._last_mouse = None
                return
            mpos = Point2(mw.get_mouse())
            dragging = mw.is_button_down(MouseButton.one())
        except (AttributeError, AssertionError, TypeError):
            self._last_mouse = None
            return
        if dragging and self._last_mouse is not None:
            dx = mpos.get_x() - self._last_mouse.get_x()
            dy = mpos.get_y() - self._last_mouse.get_y()
            self._heading_deg = (self._heading_deg - dx * self._LOOK_GAIN) % 360.0
            self._pitch_deg = max(
                self._PITCH_MIN,
                min(self._PITCH_MAX, self._pitch_deg + dy * self._LOOK_GAIN),
            )
        self._last_mouse = mpos if dragging else None

    def _key_down(self, name: str) -> bool:
        if self._held is not None:
            return bool(self._held.get(name, False))
        mw = getattr(self.base, "mouseWatcherNode", None)
        if mw is None:
            return False
        try:
            buttons = {
                "forward": (KeyboardButton.ascii_key("w"), KeyboardButton.up()),
                "back": (KeyboardButton.ascii_key("s"), KeyboardButton.down()),
                "left": (KeyboardButton.ascii_key("a"), KeyboardButton.left()),
                "right": (KeyboardButton.ascii_key("d"), KeyboardButton.right()),
                "up": (KeyboardButton.space(),),
                "down": (KeyboardButton.control(),),
                "fast": (KeyboardButton.shift(),),
            }
            for button in buttons.get(name, ()):
                if mw.is_button_down(button):
                    return True
        except (AttributeError, AssertionError, TypeError, ValueError):
            return False
        return False

    def _apply_movement(self, dt: float) -> None:
        if dt <= 0.0:
            return
        forward_axis = (1.0 if self._key_down("forward") else 0.0) - (
            1.0 if self._key_down("back") else 0.0
        )
        strafe_axis = (1.0 if self._key_down("right") else 0.0) - (
            1.0 if self._key_down("left") else 0.0
        )
        up_axis = (1.0 if self._key_down("up") else 0.0) - (
            1.0 if self._key_down("down") else 0.0
        )
        if forward_axis == 0.0 and strafe_axis == 0.0 and up_axis == 0.0:
            return
        forward, right = _ground_move_axes(self._heading_deg)
        speed = self.move_speed * (self._FAST_MULTIPLIER if self._key_down("fast") else 1.0)
        scale = speed * dt
        x, y, z = self._pos
        self._pos = (
            x + (forward[0] * forward_axis + right[0] * strafe_axis) * scale,
            y + (forward[1] * forward_axis + right[1] * strafe_axis) * scale,
            z + up_axis * scale,
        )

    def _apply(self) -> None:
        cam = _camera_node(self.base)
        cam.set_pos(*self._pos)
        cam.set_hpr(self._heading_deg, self._pitch_deg, 0.0)


class ChaseCamera:
    """Trails behind and above a selected jet, banking toward its heading.
    The explicit 'Ace Combat' reference -- see design doc §6.4.

    `jet_heading` is degrees from +y (sim "north"), matching
    TraceTick.JetFrame.heading (and scene.py's billboard roll).
    """

    def __init__(self, base, follow_distance=12.0, follow_height=5.0, scale=1.0) -> None:
        self.base = base
        # Jet glyphs scale with the battlespace (see viewer3d.world_scale), so
        # a fixed 12-unit trail would sit inside the jet on a large map.
        self.scale = max(1.0, float(scale))
        self.follow_distance = float(follow_distance) * self.scale
        self.follow_height = float(follow_height) * self.scale
        self._default_follow_distance = self.follow_distance
        self._default_follow_height = self.follow_height

    def zoom(self, steps: float) -> None:
        """Change chase distance while keeping the same trailing angle."""
        self.follow_distance = _zoom_distance(
            self.follow_distance, self._default_follow_distance, steps
        )
        self.follow_height = _zoom_distance(
            self.follow_height, self._default_follow_height, steps
        )

    def update(self, dt: float, jet_position, jet_heading) -> None:
        cam = _camera_node(self.base)
        x, y, z = jet_position
        heading_rad = math.radians(float(jet_heading))
        # Trail behind the jet: heading 0 faces +y, so "behind" is -y.
        cam_x = x - math.sin(heading_rad) * self.follow_distance
        cam_y = y - math.cos(heading_rad) * self.follow_distance
        cam_z = z + self.follow_height
        cam.set_pos(cam_x, cam_y, cam_z)
        cam.look_at(x, y, z)


class MissionBriefCamera:
    """Slow, scripted orbit/pan over the whole map. Active automatically
    whenever a header has been received but zero ticks have arrived yet --
    see design doc §7. Purely time-driven, needs no player input.
    """

    def __init__(
        self,
        base,
        center=(50.0, 50.0, 0.0),
        radius=70.0,
        angular_speed_deg_s=6.0,
    ) -> None:
        self.base = base
        self.center = tuple(center)
        self.radius = float(radius)
        self._default_radius = self.radius
        self.angular_speed_deg_s = float(angular_speed_deg_s)
        self._angle_deg = 15.0
        self._elevation_deg = SCENE_CAMERA_ELEVATION_DEG

    def zoom(self, steps: float) -> None:
        """Change the cinematic orbit radius; positive steps zoom in."""
        self.radius = _zoom_distance(self.radius, self._default_radius, steps)

    def update(self, dt: float) -> None:
        self._angle_deg = (self._angle_deg + self.angular_speed_deg_s * dt) % 360.0
        cam = _camera_node(self.base)
        az = math.radians(self._angle_deg)
        el = math.radians(self._elevation_deg)
        cx, cy, cz = self.center
        x = cx + self.radius * math.cos(el) * math.sin(az)
        y = cy - self.radius * math.cos(el) * math.cos(az)
        z = cz + self.radius * math.sin(el)
        cam.set_pos(x, y, z)
        cam.look_at(cx, cy, 0.0)
