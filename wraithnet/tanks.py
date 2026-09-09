"""Friendly/enemy tanks and ground pathing (Phase 43).

Tanks are GROUND / GROUND_VEHICLE platforms. Cannon adjudication allows
only GROUND_VEHICLE targets — never aircraft, ships, or installations.
"""

from __future__ import annotations

import heapq
import math
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from wraithnet.geometry import bearing_to, is_in_firing_arc, shortest_signed_angle
from wraithnet.guns import TANK_CANNON, GunSpec
from wraithnet.taxonomy import Faction
from wraithnet.terrain import (
    TerrainGrid,
    TerrainType,
    cell_center,
    validate_position_for_domain,
    world_to_cell,
)

TankFrameType = Literal["MBT_LIGHT", "MBT_STANDARD"]
TankOrder = Literal["HOLD", "PATROL", "ADVANCE"]

_LAND_TYPES: frozenset[TerrainType] = frozenset({"PLAINS", "FOREST", "CITY"})
TANK_MIN_SEPARATION: float = 1.5
WAYPOINT_CAPTURE_RADIUS: float = 0.75


def heading_unit_vector(heading_deg: float) -> tuple[float, float]:
    """World displacement unit for heading (0=+y, 90=+x)."""
    h = math.radians(heading_deg % 360.0)
    return (math.sin(h), math.cos(h))


@dataclass(frozen=True)
class TankFrameSpec:
    frame_id: TankFrameType
    hp: int
    speed: float
    turn_rate_deg: float
    sight_height: float
    sight_range: float
    armor: int
    evasion: int
    default_ammo: int
    cooldown_ticks: int
    gun: GunSpec


TANK_FRAME_CATALOG: dict[TankFrameType, TankFrameSpec] = {
    "MBT_LIGHT": TankFrameSpec(
        frame_id="MBT_LIGHT",
        hp=4,
        speed=1.2,
        turn_rate_deg=45.0,
        sight_height=1.5,
        sight_range=12.0,
        armor=0,
        evasion=0,
        default_ammo=20,
        cooldown_ticks=2,
        gun=TANK_CANNON,
    ),
    "MBT_STANDARD": TankFrameSpec(
        frame_id="MBT_STANDARD",
        hp=6,
        speed=1.0,
        turn_rate_deg=30.0,
        sight_height=1.8,
        sight_range=14.0,
        armor=1,
        evasion=0,
        default_ammo=24,
        cooldown_ticks=2,
        gun=TANK_CANNON,
    ),
}


class TankSpec(BaseModel):
    """Scenario tank entity (Phase 43)."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str
    faction: Faction = "ENEMY"
    position: tuple[float, float]
    heading: float = Field(default=0.0, ge=0.0, lt=360.0)
    frame_type: TankFrameType = "MBT_STANDARD"
    hp: int | None = Field(default=None, ge=1)
    ammo: int | None = Field(default=None, ge=0)
    order: TankOrder = "HOLD"
    waypoints: list[tuple[float, float]] = Field(default_factory=list)

    def resolved_hp(self) -> int:
        if self.hp is not None:
            return int(self.hp)
        return int(TANK_FRAME_CATALOG[self.frame_type].hp)

    def resolved_ammo(self) -> int:
        if self.ammo is not None:
            return int(self.ammo)
        return int(TANK_FRAME_CATALOG[self.frame_type].default_ammo)


@dataclass
class TankState:
    """Live tank runtime state (no CNP / broker)."""

    unit_id: str
    faction: Faction
    position: tuple[float, float]
    heading: float
    frame_type: TankFrameType
    hp: int
    hp_max: int
    ammo: int
    order: TankOrder
    waypoints: list[tuple[float, float]] = field(default_factory=list)
    waypoint_index: int = 0
    cooldown_ticks: int = 2
    cooldown_remaining: int = 0
    burst_seq: int = 0
    destroyed: bool = False
    current_target_id: str | None = None
    path_cells: list[tuple[int, int]] = field(default_factory=list)
    path_goal: tuple[float, float] | None = None
    no_path: bool = False
    distance_traveled: float = 0.0
    rounds_fired: int = 0
    hits: int = 0
    prev_position: tuple[float, float] = field(default=(0.0, 0.0))

    @property
    def platform_id(self) -> str:
        return self.unit_id

    @property
    def domain(self) -> Literal["GROUND"]:
        return "GROUND"

    @property
    def target_class(self) -> Literal["GROUND_VEHICLE"]:
        return "GROUND_VEHICLE"

    @property
    def is_destroyed(self) -> bool:
        return self.destroyed or self.hp <= 0

    def mark_destroyed(self) -> None:
        self.destroyed = True
        self.hp = 0

    @property
    def gun(self) -> GunSpec:
        return TANK_FRAME_CATALOG[self.frame_type].gun

    @property
    def armor(self) -> int:
        return int(TANK_FRAME_CATALOG[self.frame_type].armor)


def validate_tank_placement(terrain: TerrainGrid, tank: TankSpec) -> list[str]:
    errors: list[str] = []
    try:
        validate_position_for_domain(terrain, tank.position, "GROUND")
    except ValueError as exc:
        errors.append(f"Tank '{tank.unit_id}': {exc}")
    for i, wp in enumerate(tank.waypoints):
        try:
            validate_position_for_domain(terrain, wp, "GROUND")
        except ValueError as exc:
            errors.append(f"Tank '{tank.unit_id}' waypoint[{i}]: {exc}")
    return errors


def _neighbors(
    col: int, row: int, cols: int, rows: int
) -> list[tuple[int, int]]:
    # Stable cardinal order: N, E, S, W (no diagonals — no water corner cut).
    out: list[tuple[int, int]] = []
    for dc, dr in ((0, -1), (1, 0), (0, 1), (-1, 0)):
        nc, nr = col + dc, row + dr
        if 0 <= nc < cols and 0 <= nr < rows:
            out.append((nc, nr))
    return out


def astar_land_path(
    terrain: TerrainGrid,
    start_xy: tuple[float, float],
    goal_xy: tuple[float, float],
    *,
    max_expansions: int = 5000,
) -> list[tuple[int, int]] | None:
    """Deterministic tile A* on PLAINS/FOREST/CITY. WATER impassable.

    Returns cell path inclusive of start/goal cells, or None if no path.
    Equal terrain cost; stable neighbor order and (f, h, col, row) tie-break.
    """
    try:
        start = world_to_cell(terrain, start_xy[0], start_xy[1])
        goal = world_to_cell(terrain, goal_xy[0], goal_xy[1])
    except Exception:
        return None
    rows = terrain.rows
    cols = terrain.columns

    def cell_ok(c: tuple[int, int]) -> bool:
        if not (0 <= c[0] < cols and 0 <= c[1] < rows):
            return False
        ttype = terrain.cells[c[1]][c[0]].terrain_type
        return ttype in _LAND_TYPES

    if not cell_ok(start) or not cell_ok(goal):
        return None
    if start == goal:
        return [start]

    def heuristic(c: tuple[int, int]) -> float:
        return float(abs(c[0] - goal[0]) + abs(c[1] - goal[1]))

    open_heap: list[tuple[float, float, int, int, int]] = []
    counter = 0
    heapq.heappush(
        open_heap, (heuristic(start), heuristic(start), start[0], start[1], counter)
    )
    came_from: dict[tuple[int, int], tuple[int, int] | None] = {start: None}
    g_score: dict[tuple[int, int], float] = {start: 0.0}
    expansions = 0

    while open_heap and expansions < max_expansions:
        _f, _h, col, row, _c = heapq.heappop(open_heap)
        current = (col, row)
        expansions += 1
        if current == goal:
            path = [current]
            while came_from[path[-1]] is not None:
                path.append(came_from[path[-1]])  # type: ignore[arg-type]
            path.reverse()
            return path
        for nxt in _neighbors(col, row, cols, rows):
            if not cell_ok(nxt):
                continue
            tentative = g_score[current] + 1.0
            if tentative < g_score.get(nxt, float("inf")):
                came_from[nxt] = current
                g_score[nxt] = tentative
                h = heuristic(nxt)
                counter += 1
                heapq.heappush(
                    open_heap, (tentative + h, h, nxt[0], nxt[1], counter)
                )
    return None


def select_tank_target(
    tank: TankState,
    vehicles: list[tuple[str, tuple[float, float], bool]],
) -> str | None:
    """Nearest visible hostile GROUND_VEHICLE; retain current if still valid."""
    valid: dict[str, tuple[float, tuple[float, float], bool]] = {}
    for vid, pos, has_los in vehicles:
        if not has_los:
            continue
        dist = math.dist(tank.position, pos)
        if dist > TANK_FRAME_CATALOG[tank.frame_type].sight_range:
            continue
        valid[vid] = (dist, pos, has_los)

    if tank.current_target_id and tank.current_target_id in valid:
        return tank.current_target_id
    if not valid:
        return None
    ranked = sorted(valid.items(), key=lambda kv: (kv[1][0], kv[0]))
    return ranked[0][0]


def _rotate_toward(tank: TankState, desired_heading: float) -> None:
    frame = TANK_FRAME_CATALOG[tank.frame_type]
    delta = shortest_signed_angle(tank.heading, desired_heading)
    turn = frame.turn_rate_deg
    if abs(delta) <= turn:
        tank.heading = desired_heading % 360.0
    else:
        tank.heading = (tank.heading + turn * (1.0 if delta > 0 else -1.0)) % 360.0


def _invalidate_path(tank: TankState) -> None:
    tank.path_cells = []
    tank.path_goal = None
    tank.no_path = False


def _ensure_path(
    tank: TankState,
    terrain: TerrainGrid,
    goal_xy: tuple[float, float],
) -> bool:
    """Build/refresh A* path toward goal. Returns False on bounded failure."""
    if (
        tank.path_goal is not None
        and tank.path_cells
        and math.dist(tank.path_goal, goal_xy) < 0.5
        and not tank.no_path
    ):
        return True
    path = astar_land_path(terrain, tank.position, goal_xy)
    if path is None:
        tank.path_cells = []
        tank.path_goal = goal_xy
        tank.no_path = True
        return False
    # Drop current cell if present so we chase the next center.
    if path and path[0] == world_to_cell(terrain, tank.position[0], tank.position[1]):
        path = path[1:]
    tank.path_cells = list(path)
    tank.path_goal = goal_xy
    tank.no_path = False
    return True


def _move_along_path(tank: TankState, terrain: TerrainGrid) -> None:
    """Continuous movement toward successive cell centers."""
    if not tank.path_cells:
        return
    frame = TANK_FRAME_CATALOG[tank.frame_type]
    budget = float(frame.speed)
    while budget > 1e-9 and tank.path_cells:
        col, row = tank.path_cells[0]
        target = cell_center(terrain, col, row)
        desired = bearing_to(tank.position, target)
        _rotate_toward(tank, desired)
        if abs(shortest_signed_angle(tank.heading, desired)) > 45.0:
            return
        remaining = math.dist(tank.position, target)
        step = min(budget, remaining)
        fx, fy = heading_unit_vector(tank.heading)
        candidate = (
            tank.position[0] + fx * step,
            tank.position[1] + fy * step,
        )
        candidate = (
            max(0.0, min(terrain.world_width, candidate[0])),
            max(0.0, min(terrain.world_height, candidate[1])),
        )
        try:
            validate_position_for_domain(terrain, candidate, "GROUND")
        except ValueError:
            _invalidate_path(tank)
            tank.no_path = True
            return
        dist_moved = math.dist(tank.position, candidate)
        tank.position = candidate
        tank.distance_traveled += dist_moved
        budget -= dist_moved
        if math.dist(tank.position, target) <= WAYPOINT_CAPTURE_RADIUS:
            tank.path_cells.pop(0)


def _order_goal(tank: TankState) -> tuple[float, float] | None:
    """Next HOLD/PATROL/ADVANCE waypoint, or None to stay put."""
    if tank.order == "HOLD" or not tank.waypoints:
        return None
    if tank.waypoint_index >= len(tank.waypoints):
        if tank.order == "PATROL" and tank.waypoints:
            tank.waypoint_index = 0
        else:
            return None  # ADVANCE complete → hold
    return tuple(tank.waypoints[tank.waypoint_index])


def _advance_waypoint_if_captured(tank: TankState) -> None:
    if not tank.waypoints or tank.waypoint_index >= len(tank.waypoints):
        return
    wp = tank.waypoints[tank.waypoint_index]
    if math.dist(tank.position, wp) <= WAYPOINT_CAPTURE_RADIUS:
        tank.waypoint_index += 1
        if tank.order == "PATROL" and tank.waypoint_index >= len(tank.waypoints):
            tank.waypoint_index = 0
        _invalidate_path(tank)


def can_tank_engage(
    tank: TankState,
    target_pos: tuple[float, float],
    *,
    has_los: bool,
) -> bool:
    """True when range/arc/LOS allow a legal cannon burst."""
    if not has_los or tank.ammo <= 0 or tank.cooldown_remaining > 0:
        return False
    gun = tank.gun
    dist = math.dist(tank.position, target_pos)
    if dist > gun.range or dist < gun.min_range:
        return False
    return is_in_firing_arc(
        tank.heading, tank.position, target_pos, gun.firing_arc_deg
    )


def step_tank_movement(
    tank: TankState,
    terrain: TerrainGrid,
    *,
    target_pos: tuple[float, float] | None,
    target_has_los: bool,
) -> None:
    """One tick of tank AI movement (docs/19 §12 / Phase 43).

    1. If engaging: turn to face target (do not chase once in weapon range).
    2. Else if target known: path toward it.
    3. Else follow HOLD / PATROL / ADVANCE orders.
    Bounded path failure leaves the tank in place (no teleport).
    """
    tank.prev_position = tank.position
    if tank.is_destroyed:
        return

    gun = tank.gun
    if target_pos is not None:
        dist = math.dist(tank.position, target_pos)
        if dist <= gun.range and target_has_los:
            desired = bearing_to(tank.position, target_pos)
            _rotate_toward(tank, desired)
            _invalidate_path(tank)
            return
        # Chase toward last seen position.
        if _ensure_path(tank, terrain, target_pos):
            _move_along_path(tank, terrain)
        return

    goal = _order_goal(tank)
    if goal is None:
        return
    if _ensure_path(tank, terrain, goal):
        _move_along_path(tank, terrain)
        _advance_waypoint_if_captured(tank)


def resolve_tank_separation(
    tanks: list[TankState],
    *,
    min_separation: float = TANK_MIN_SEPARATION,
) -> None:
    """Simple occupancy push in stable unit_id order (no physics)."""
    ordered = sorted(tanks, key=lambda t: t.unit_id)
    for i, a in enumerate(ordered):
        if a.is_destroyed:
            continue
        for b in ordered[:i]:
            if b.is_destroyed:
                continue
            dist = math.dist(a.position, b.position)
            if dist >= min_separation or dist < 1e-9:
                continue
            # Push the later ID away along the separation vector.
            dx = a.position[0] - b.position[0]
            dy = a.position[1] - b.position[1]
            scale = (min_separation - dist) / dist
            a.position = (
                a.position[0] + dx * scale,
                a.position[1] + dy * scale,
            )


def tanks_mutually_reachable(
    terrain: TerrainGrid,
    a: tuple[float, float],
    b: tuple[float, float],
) -> bool:
    """True if land A* connects the two positions."""
    return astar_land_path(terrain, a, b) is not None


def ground_discovery_deadlock(
    terrain: TerrainGrid,
    friendly: list[tuple[str, tuple[float, float]]],
    enemy: list[tuple[str, tuple[float, float]]],
    *,
    sight_range: float,
) -> list[str]:
    """Return enemy IDs that can never enter sight of any friendly on land.

    Used for ``GROUND_DISCOVERY_DEADLOCK`` feasibility notes.
    """
    stuck: list[str] = []
    for eid, epos in enemy:
        ok = False
        for _fid, fpos in friendly:
            if not tanks_mutually_reachable(terrain, epos, fpos):
                continue
            # Conservatively: if path exists, they can close to sight_range
            # unless already farther than any connected component allows —
            # path existence is the connectivity gate; sight is runtime.
            path = astar_land_path(terrain, epos, fpos)
            if path is not None:
                # Estimate path length in cells * cell_size as travel budget proxy.
                ok = True
                break
            if math.dist(epos, fpos) <= sight_range:
                ok = True
                break
        if not ok:
            stuck.append(eid)
    return stuck
