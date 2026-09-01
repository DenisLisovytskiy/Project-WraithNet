"""Camera controllers for viewer3d. All three read jet/target positions
from the same SceneHandles Phase 16 already builds/updates -- no new scene
data is needed, only new ways of pointing a camera at it.

Jet selection for chase mode uses Tab/N cycling over `header.jet_ids`
rather than 3D collision picking (Phase 17's explicitly sanctioned
fallback -- picking is fiddly under `window-type none` and not required).
"""

from __future__ import annotations

import math

from panda3d.core import MouseButton, NodePath, Point2


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


class OrbitCamera:
    """Mouse-drag orbits around the map's center at a fixed radius/tilt.
    Default mode once live-tactical mode is active with no jet selected.
    """

    def __init__(self, base, center=(50.0, 50.0, 0.0), radius=90.0) -> None:
        self.base = base
        self.center = tuple(center)
        self.radius = float(radius)
        self._azimuth_deg = 40.0
        self._elevation_deg = 38.0
        self._last_mouse: Point2 | None = None
        self._drag_gain = 90.0

    def update(self, dt: float) -> None:
        self._read_mouse_drag()
        self._apply()

    def _read_mouse_drag(self) -> None:
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
            self._azimuth_deg = (self._azimuth_deg - dx * self._drag_gain) % 360.0
            self._elevation_deg = max(
                8.0, min(85.0, self._elevation_deg + dy * self._drag_gain)
            )
        self._last_mouse = mpos if dragging else None

    def _apply(self) -> None:
        cam = _camera_node(self.base)
        az = math.radians(self._azimuth_deg)
        el = math.radians(self._elevation_deg)
        cx, cy, cz = self.center
        x = cx + self.radius * math.cos(el) * math.sin(az)
        y = cy - self.radius * math.cos(el) * math.cos(az)
        z = cz + self.radius * math.sin(el)
        cam.set_pos(x, y, z)
        cam.look_at(cx, cy, cz)


class ChaseCamera:
    """Trails behind and above a selected jet, banking toward its heading.
    The explicit 'Ace Combat' reference -- see design doc §6.4.

    `jet_heading` is degrees from +y (sim "north"), matching scene.py's
    billboard yaw convention (`atan2(dx, dy)`).
    """

    def __init__(self, base, follow_distance=12.0, follow_height=5.0) -> None:
        self.base = base
        self.follow_distance = float(follow_distance)
        self.follow_height = float(follow_height)

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
        self.angular_speed_deg_s = float(angular_speed_deg_s)
        self._angle_deg = 15.0
        self._elevation_deg = 32.0

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
