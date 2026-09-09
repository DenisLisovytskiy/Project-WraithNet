"""Naval frames, carriers, corvettes, water-constrained movement, recovery.

Phase 40 — carriers as dynamic airbases (docs/19 §10.1).
Phase 41 — enemy corvettes with layered defense (docs/19 §10.2).
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field

from wraithnet.config import SensorSignature
from wraithnet.geometry import angular_distance, bearing_to, shortest_signed_angle
from wraithnet.terrain import (
    TerrainGrid,
    is_passable,
    segment_supercover_cells,
    world_to_cell,
)

CarrierFrameType = Literal["ESCORT_CARRIER", "FLEET_CARRIER"]
CorvetteFrameType = Literal["CORVETTE_LIGHT", "CORVETTE_STANDARD"]
NavalFrameType = Literal[
    "ESCORT_CARRIER", "FLEET_CARRIER", "CORVETTE_LIGHT", "CORVETTE_STANDARD"
]
RecoveryPhase = Literal["RTB", "APPROACH", "RECOVERED"]


@dataclass(frozen=True)
class NavalFrameSpec:
    """Catalog entry for a surface vessel frame."""

    max_speed: float
    turn_rate_deg_per_tick: float
    hp: int
    sight_height: float
    footprint_radius: float
    recovery_radius: float
    hangar_capacity: int = 0
    sensor_range: float = 0.0
    sam_engagement_range: float = 0.0


NAVAL_FRAME_CATALOG: dict[NavalFrameType, NavalFrameSpec] = {
    "ESCORT_CARRIER": NavalFrameSpec(
        max_speed=1.5,
        turn_rate_deg_per_tick=8.0,
        hp=12,
        sight_height=2.0,
        footprint_radius=1.5,
        recovery_radius=2.0,
        hangar_capacity=4,
    ),
    "FLEET_CARRIER": NavalFrameSpec(
        max_speed=1.2,
        turn_rate_deg_per_tick=5.0,
        hp=20,
        sight_height=2.5,
        footprint_radius=2.5,
        recovery_radius=2.0,
        hangar_capacity=8,
    ),
    "CORVETTE_LIGHT": NavalFrameSpec(
        max_speed=2.0,
        turn_rate_deg_per_tick=12.0,
        hp=4,
        sight_height=1.5,
        footprint_radius=1.0,
        recovery_radius=0.0,
        hangar_capacity=0,
        sensor_range=18.0,
        sam_engagement_range=15.0,
    ),
    "CORVETTE_STANDARD": NavalFrameSpec(
        max_speed=1.8,
        turn_rate_deg_per_tick=10.0,
        hp=6,
        sight_height=1.8,
        footprint_radius=1.2,
        recovery_radius=0.0,
        hangar_capacity=0,
        sensor_range=20.0,
        sam_engagement_range=15.0,
    ),
}

DEFAULT_CORVETTE_LOADOUT: dict[str, int] = {
    "SAM_RADAR": 8,
    "CIWS": 40,
    "DECK_GUN": 20,
}

DEFAULT_CORVETTE_SIGNATURE = SensorSignature(
    heat=0.25, radar_cross_section=0.85, radar_emitter=True
)


class CarrierSpec(BaseModel):
    """Friendly aircraft carrier scenario entity (Phase 40)."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str
    faction: Literal["FRIENDLY"] = "FRIENDLY"
    position: tuple[float, float]
    heading: float = Field(default=0.0, ge=0.0, lt=360.0)
    frame_type: CarrierFrameType = "ESCORT_CARRIER"
    route: list[tuple[float, float]] = Field(default_factory=list)
    hold_position: bool = True


class CorvetteSpec(BaseModel):
    """Enemy corvette scenario entity (Phase 41)."""

    model_config = ConfigDict(extra="forbid")

    unit_id: str
    faction: Literal["ENEMY"] = "ENEMY"
    position: tuple[float, float]
    heading: float = Field(default=0.0, ge=0.0, lt=360.0)
    frame_type: CorvetteFrameType = "CORVETTE_STANDARD"
    route: list[tuple[float, float]] = Field(default_factory=list)
    hold_position: bool = True
    # Finite magazines: SAM_RADAR missiles, CIWS rounds, deck-gun rounds.
    loadout: dict[str, int] = Field(
        default_factory=lambda: dict(DEFAULT_CORVETTE_LOADOUT)
    )
    sam_cooldown_ticks: int = Field(default=1, ge=0)
    ciws_cooldown_ticks: int = Field(default=1, ge=0)
    deck_gun_cooldown_ticks: int = Field(default=2, ge=0)


@dataclass
class CarrierState:
    """Live carrier runtime state (not a CNP Mesa agent)."""

    unit_id: str
    position: tuple[float, float]
    heading: float
    frame_type: CarrierFrameType
    route: list[tuple[float, float]]
    hold_position: bool
    hp: int
    hp_max: int
    route_index: int = 0
    destroyed: bool = False
    # Prior-tick position for relative-speed checks during recovery.
    prev_position: tuple[float, float] = field(default=(0.0, 0.0))

    @property
    def platform_id(self) -> str:
        return self.unit_id

    @property
    def faction(self) -> Literal["FRIENDLY"]:
        return "FRIENDLY"

    @property
    def domain(self) -> Literal["SURFACE"]:
        return "SURFACE"

    @property
    def target_class(self) -> Literal["SURFACE_VESSEL"]:
        return "SURFACE_VESSEL"

    def position_xy(self) -> tuple[float, float]:
        return self.position

    def altitude_z(self) -> float:
        return 0.0

    @property
    def available(self) -> bool:
        return not self.destroyed and self.hp > 0


@dataclass
class CorvetteState:
    """Live corvette runtime state (not a CNP Mesa agent)."""

    unit_id: str
    position: tuple[float, float]
    heading: float
    frame_type: CorvetteFrameType
    route: list[tuple[float, float]]
    hold_position: bool
    hp: int
    hp_max: int
    loadout: dict[str, int]
    sam_cooldown_ticks: int = 1
    ciws_cooldown_ticks: int = 1
    deck_gun_cooldown_ticks: int = 2
    sam_cooldown_remaining: int = 0
    ciws_cooldown_remaining: int = 0
    deck_gun_cooldown_remaining: int = 0
    route_index: int = 0
    destroyed: bool = False
    prev_position: tuple[float, float] = field(default=(0.0, 0.0))
    signature: SensorSignature = field(
        default_factory=lambda: DEFAULT_CORVETTE_SIGNATURE
    )
    # Monotonic burst counter for keyed gun RNG identity.
    burst_seq: int = 0

    @property
    def platform_id(self) -> str:
        return self.unit_id

    @property
    def faction(self) -> Literal["ENEMY"]:
        return "ENEMY"

    @property
    def domain(self) -> Literal["SURFACE"]:
        return "SURFACE"

    @property
    def target_class(self) -> Literal["SURFACE_VESSEL"]:
        return "SURFACE_VESSEL"

    def position_xy(self) -> tuple[float, float]:
        return self.position

    def altitude_z(self) -> float:
        return 0.0

    @property
    def available(self) -> bool:
        return not self.destroyed and self.hp > 0

    @property
    def is_destroyed(self) -> bool:
        return self.destroyed or self.hp <= 0

    def mark_destroyed(self) -> None:
        self.destroyed = True
        self.hp = 0
        # Radar support stops immediately after destruction.
        self.signature = SensorSignature(
            heat=self.signature.heat,
            radar_cross_section=self.signature.radar_cross_section,
            radar_emitter=False,
        )


class _SurfaceVessel(Protocol):
    unit_id: str
    position: tuple[float, float]
    heading: float
    route: list[tuple[float, float]]
    hold_position: bool
    hp: int
    route_index: int
    destroyed: bool
    prev_position: tuple[float, float]
    frame_type: str


def all_water_terrain(
    width: float, height: float, cell_size: float = 5.0
) -> TerrainGrid:
    """Uniform WATER grid for naval tests and water-only scenarios."""
    from wraithnet.terrain import TerrainCell

    if cell_size <= 0:
        raise ValueError(f"cell_size must be positive, got {cell_size}")
    columns = math.ceil(width / cell_size)
    rows = math.ceil(height / cell_size)
    cells = [
        [TerrainCell(terrain_type="WATER") for _ in range(columns)]
        for _ in range(rows)
    ]
    return TerrainGrid(
        world_width=width,
        world_height=height,
        terrain_edit_seed=0,
        cell_size=cell_size,
        columns=columns,
        rows=rows,
        cells=cells,
    )


def footprint_inflate_cells(footprint_radius: float, cell_size: float) -> int:
    """How many cells of land/shore buffer a hull of ``footprint_radius`` needs."""
    if footprint_radius <= 0:
        return 0
    return max(1, math.ceil(footprint_radius / cell_size))


def build_navigable_water_mask(
    terrain: TerrainGrid, footprint_radius: float
) -> list[list[bool]]:
    """True where a vessel of ``footprint_radius`` may center itself.

    Land and near-shore cells are blocked by inflating non-WATER obstacles.
    """
    rows, cols = terrain.rows, terrain.columns
    water = [
        [
            terrain.cells[r][c].terrain_type == "WATER"
            for c in range(cols)
        ]
        for r in range(rows)
    ]
    inflate = footprint_inflate_cells(footprint_radius, terrain.cell_size)
    if inflate <= 0:
        return water

    blocked = [[False] * cols for _ in range(rows)]
    for r in range(rows):
        for c in range(cols):
            if water[r][c]:
                continue
            for dr in range(-inflate, inflate + 1):
                for dc in range(-inflate, inflate + 1):
                    rr, cc = r + dr, c + dc
                    if 0 <= rr < rows and 0 <= cc < cols:
                        if dr * dr + dc * dc <= inflate * inflate:
                            blocked[rr][cc] = True
    return [
        [water[r][c] and not blocked[r][c] for c in range(cols)]
        for r in range(rows)
    ]


def cell_navigable(
    mask: list[list[bool]], col: int, row: int
) -> bool:
    if not mask or row < 0 or col < 0:
        return False
    if row >= len(mask) or col >= len(mask[0]):
        return False
    return mask[row][col]


def position_navigable(
    terrain: TerrainGrid,
    mask: list[list[bool]],
    position: tuple[float, float],
) -> bool:
    try:
        col, row = world_to_cell(terrain, position[0], position[1])
    except ValueError:
        return False
    return cell_navigable(mask, col, row)


def connected_on_mask(
    terrain: TerrainGrid,
    mask: list[list[bool]],
    a: tuple[float, float],
    b: tuple[float, float],
) -> bool:
    """True if ``a`` and ``b`` share a 4-connected navigable component."""
    try:
        ac, ar = world_to_cell(terrain, a[0], a[1])
        bc, br = world_to_cell(terrain, b[0], b[1])
    except ValueError:
        return False
    if not cell_navigable(mask, ac, ar) or not cell_navigable(mask, bc, br):
        return False
    if (ac, ar) == (bc, br):
        return True
    rows, cols = len(mask), len(mask[0])
    stack = [(ac, ar)]
    seen = {(ac, ar)}
    while stack:
        c, r = stack.pop()
        for dc, dr in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nc, nr = c + dc, r + dr
            if not (0 <= nc < cols and 0 <= nr < rows):
                continue
            if not mask[nr][nc] or (nc, nr) in seen:
                continue
            if (nc, nr) == (bc, br):
                return True
            seen.add((nc, nr))
            stack.append((nc, nr))
    return False


def footprint_clear_along_segment(
    terrain: TerrainGrid,
    mask: list[list[bool]],
    start: tuple[float, float],
    end: tuple[float, float],
) -> bool:
    """True if every cell the segment touches remains navigable for the hull."""
    try:
        cells = segment_supercover_cells(terrain, start, end)
    except ValueError:
        return False
    return all(cell_navigable(mask, c, r) for c, r in cells)


def validate_naval_placement(
    terrain: TerrainGrid,
    *,
    unit_id: str,
    position: tuple[float, float],
    route: list[tuple[float, float]],
    frame_type: NavalFrameType,
    label_kind: str,
) -> list[str]:
    """Schema-time checks: water-only spawn/route with footprint connectivity."""
    errors: list[str] = []
    frame = NAVAL_FRAME_CATALOG[frame_type]
    mask = build_navigable_water_mask(terrain, frame.footprint_radius)
    label = f"{label_kind} '{unit_id}'"

    if not is_passable(terrain, position, "SURFACE"):
        errors.append(f"{label} position must be on WATER.")
    elif not position_navigable(terrain, mask, position):
        errors.append(
            f"{label} position footprint overlaps land/shore "
            f"(footprint_radius={frame.footprint_radius})."
        )

    prev = position
    for i, point in enumerate(route):
        if not is_passable(terrain, point, "SURFACE"):
            errors.append(f"{label} route[{i}] must be on WATER.")
            continue
        if not position_navigable(terrain, mask, point):
            errors.append(
                f"{label} route[{i}] footprint overlaps land/shore."
            )
            continue
        if not connected_on_mask(terrain, mask, prev, point):
            errors.append(
                f"{label} route[{i}] is not connected on the navigable "
                f"water mask (channel too narrow for hull)."
            )
        prev = point
    return errors


def validate_carrier_placement(
    terrain: TerrainGrid,
    carrier: CarrierSpec,
) -> list[str]:
    return validate_naval_placement(
        terrain,
        unit_id=carrier.unit_id,
        position=carrier.position,
        route=carrier.route,
        frame_type=carrier.frame_type,
        label_kind="Carrier",
    )


def validate_corvette_placement(
    terrain: TerrainGrid,
    corvette: CorvetteSpec,
) -> list[str]:
    errors = validate_naval_placement(
        terrain,
        unit_id=corvette.unit_id,
        position=corvette.position,
        route=corvette.route,
        frame_type=corvette.frame_type,
        label_kind="Corvette",
    )
    for weapon, count in corvette.loadout.items():
        if count < 0:
            errors.append(
                f"Corvette '{corvette.unit_id}' loadout '{weapon}' count "
                f"cannot be negative (got {count})."
            )
        if weapon not in ("SAM_RADAR", "CIWS", "DECK_GUN"):
            errors.append(
                f"Corvette '{corvette.unit_id}' loadout weapon '{weapon}' "
                "must be SAM_RADAR, CIWS, or DECK_GUN."
            )
    return errors


def heading_unit_vector(heading_deg: float) -> tuple[float, float]:
    """World heading 0=+y, 90=+x → (dx, dy)."""
    h = math.radians(heading_deg % 360.0)
    return (math.sin(h), math.cos(h))


def approach_point_astern(
    carrier_pos: tuple[float, float],
    carrier_heading: float,
    *,
    standoff: float = 6.0,
) -> tuple[float, float]:
    """Point behind the carrier along its longitudinal axis for approach."""
    fx, fy = heading_unit_vector(carrier_heading)
    return (carrier_pos[0] - fx * standoff, carrier_pos[1] - fy * standoff)


def recovery_relative_speed(
    jet_velocity: tuple[float, float],
    carrier_velocity: tuple[float, float],
) -> float:
    dx = jet_velocity[0] - carrier_velocity[0]
    dy = jet_velocity[1] - carrier_velocity[1]
    return math.hypot(dx, dy)


def recovery_heading_aligned(
    jet_heading: float,
    carrier_heading: float,
    tolerance_deg: float,
) -> bool:
    return angular_distance(jet_heading, carrier_heading) <= tolerance_deg


def _footprint_radius(frame_type: str) -> float:
    return NAVAL_FRAME_CATALOG[frame_type].footprint_radius  # type: ignore[index]


def step_surface_movement(
    vessel: _SurfaceVessel,
    terrain: TerrainGrid,
    *,
    others: list[_SurfaceVessel] | None = None,
) -> None:
    """Advance one tick of waypoint following with speed/turn/footprint limits."""
    vessel.prev_position = vessel.position
    if vessel.destroyed or vessel.hp <= 0:
        vessel.destroyed = True
        return
    if vessel.hold_position or not vessel.route:
        return

    frame = NAVAL_FRAME_CATALOG[vessel.frame_type]  # type: ignore[index]
    mask = build_navigable_water_mask(terrain, frame.footprint_radius)
    if vessel.route_index >= len(vessel.route):
        vessel.hold_position = True
        return

    waypoint = vessel.route[vessel.route_index]
    if math.dist(vessel.position, waypoint) <= max(0.5, frame.footprint_radius * 0.25):
        vessel.route_index += 1
        if vessel.route_index >= len(vessel.route):
            vessel.hold_position = True
            return
        waypoint = vessel.route[vessel.route_index]

    desired = bearing_to(vessel.position, waypoint)
    delta = shortest_signed_angle(vessel.heading, desired)
    turn = frame.turn_rate_deg_per_tick
    if abs(delta) <= turn:
        vessel.heading = desired % 360.0
    else:
        vessel.heading = (
            vessel.heading + turn * (1.0 if delta > 0 else -1.0)
        ) % 360.0

    if abs(shortest_signed_angle(vessel.heading, desired)) > 45.0:
        return

    speed = frame.max_speed
    remaining = math.dist(vessel.position, waypoint)
    step = min(speed, remaining)
    fx, fy = heading_unit_vector(vessel.heading)
    candidate = (
        vessel.position[0] + fx * step,
        vessel.position[1] + fy * step,
    )
    candidate = (
        max(0.0, min(terrain.world_width, candidate[0])),
        max(0.0, min(terrain.world_height, candidate[1])),
    )

    if not position_navigable(terrain, mask, candidate):
        return
    if not footprint_clear_along_segment(
        terrain, mask, vessel.position, candidate
    ):
        return

    for other in others or []:
        if other.unit_id == vessel.unit_id or other.destroyed:
            continue
        other_r = _footprint_radius(other.frame_type)
        if math.dist(candidate, other.position) < (
            frame.footprint_radius + other_r
        ):
            return

    vessel.position = candidate


def step_carrier_movement(
    carrier: CarrierState,
    terrain: TerrainGrid,
    *,
    others: list[CarrierState] | None = None,
) -> None:
    step_surface_movement(carrier, terrain, others=list(others or []))


def step_corvette_movement(
    corvette: CorvetteState,
    terrain: TerrainGrid,
    *,
    others: list[_SurfaceVessel] | None = None,
) -> None:
    step_surface_movement(corvette, terrain, others=others)


def carrier_velocity(carrier: CarrierState) -> tuple[float, float]:
    return (
        carrier.position[0] - carrier.prev_position[0],
        carrier.position[1] - carrier.prev_position[1],
    )
