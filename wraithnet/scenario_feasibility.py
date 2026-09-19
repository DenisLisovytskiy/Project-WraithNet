"""Static feasibility analysis and explainable challenge scoring (Phase 30).

This is an analyzer, not a solver and not a victory guarantee. It does not
simulate CNP negotiation, SAM attrition, jamming message loss, firing-arc turn
time, or dynamic assignment. Approximate fuel reach uses straight-line
base → target → base distance under the flat per-tick burn model plus
``RTB_FUEL_SAFETY_MARGIN``.

Issue ``code`` values are stable constants for GUI/tests — never parse
``message`` prose.
"""

from __future__ import annotations

import math
from collections import defaultdict, deque
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from wraithnet import config
from wraithnet.config import JetFrameType, TargetType, WeaponType
from wraithnet.generation_schemas import (
    FeasibilityIssue,
    FeasibilityReport,
    ObjectiveFeasibility,
)
from wraithnet.scenario_schema import JetSpec, ScenarioConfig
from wraithnet.terrain import (
    TerrainGrid,
    has_line_of_sight,
    is_passable,
    segment_supercover_cells,
)

# Stable issue codes (public contract).
NO_SUITABLE_WEAPON = "NO_SUITABLE_WEAPON"
INSUFFICIENT_DAMAGE = "INSUFFICIENT_DAMAGE"
FUEL_REACH = "FUEL_REACH"
DISCOVERY_CHAIN_GAP = "DISCOVERY_CHAIN_GAP"
EMPTY_LOADOUT = "EMPTY_LOADOUT"
LAUNCH_AREA_THREATENED = "LAUNCH_AREA_THREATENED"
NO_LAND_SPAWN_COMPONENT = "NO_LAND_SPAWN_COMPONENT"
ENTITY_ON_IMPASSABLE_TERRAIN = "ENTITY_ON_IMPASSABLE_TERRAIN"
LOS_DISCOVERY_CHAIN_GAP = "LOS_DISCOVERY_CHAIN_GAP"
# Phase 43/44 combined-arms codes.
GROUND_DISCOVERY_DEADLOCK = "GROUND_DISCOVERY_DEADLOCK"
NO_VALID_LAND_COMPONENT = "NO_VALID_LAND_COMPONENT"
NO_VALID_WATER_COMPONENT = "NO_VALID_WATER_COMPONENT"
INVALID_DOMAIN_SPAWN = "INVALID_DOMAIN_SPAWN"
NO_ANTI_ARMOR_CAPABILITY = "NO_ANTI_ARMOR_CAPABILITY"
OBJECTIVE_UNACHIEVABLE = "OBJECTIVE_UNACHIEVABLE"
NO_RECOVERY_BASE = "NO_RECOVERY_BASE"
UNREACHABLE_CARRIER_ROUTE = "UNREACHABLE_CARRIER_ROUTE"
NO_ANTI_SHIP_CAPABILITY = "NO_ANTI_SHIP_CAPABILITY"
MISSILE_DEFENSE_OVERMATCH = "MISSILE_DEFENSE_OVERMATCH"
# Phase 44 alias used in docs; emitted alongside LOS_DISCOVERY_CHAIN_GAP.
LOS_DISCOVERY_GAP = "LOS_DISCOVERY_GAP"

# Conservative discovery-chain geometry (shared intent with Phase 31 constants).
DISCOVERY_MARGIN: float = 2.0
LAUNCH_RING_OUTER_RADIUS: float = 6.0

# Named challenge components (each intended in roughly [0, 100] before weighting).
CHALLENGE_COMPONENT_NAMES: tuple[str, ...] = (
    "objective_burden",
    "sam_pressure",
    "jamming_pressure",
    "route_distance",
    "cooperation_pressure",
    "friendly_reserve_penalty",
    "fuel_margin_penalty",
)

_CHALLENGE_WEIGHTS: dict[str, float] = {
    "objective_burden": 0.22,
    "sam_pressure": 0.18,
    "jamming_pressure": 0.10,
    "route_distance": 0.12,
    "cooperation_pressure": 0.14,
    "friendly_reserve_penalty": 0.12,
    "fuel_margin_penalty": 0.12,
}


@dataclass(frozen=True)
class _Destroyable:
    entity_id: str
    position: tuple[float, float]
    hp_required: int
    target_type: TargetType


@dataclass(frozen=True)
class ChallengeScoreInputs:
    """Intermediate values for ``compute_challenge_components``."""

    required_hp_by_target_type: dict[TargetType, int]
    total_damage_capacity_by_target_type: dict[TargetType, int]
    objective_count: int
    sam_count: int
    jammer_count: int
    mean_objective_distance_fraction: float
    cooperative_objective_count: int
    destroyable_count: int
    friendly_reserve_ratio: float
    min_fuel_margin_ratio: float


def weapon_eligible_damage(weapon: WeaponType, target_type: TargetType) -> int:
    """Damage contributed by one unit when suitability is strictly positive.

    Positive suitability does not scale damage — it is an eligibility gate only.
    """
    spec = config.WEAPON_CATALOG[weapon]
    if spec.suitability.get(target_type, 0.0) <= 0.0:
        return 0
    return spec.damage_per_hit


def approximate_fuel_range(frame_type: JetFrameType) -> float:
    """One-way distance a full tank can cover at max speed (no RTB margin)."""
    frame = config.JET_FRAME_CATALOG[frame_type]
    if frame.fuel_burn_per_tick <= 0.0:
        return math.inf
    endurance_ticks = frame.fuel_capacity / frame.fuel_burn_per_tick
    return endurance_ticks * frame.max_speed


def round_trip_fuel_required(
    frame_type: JetFrameType,
    home_base: tuple[float, float],
    target_position: tuple[float, float],
) -> float:
    """Fuel units required for approximate base → target → base with RTB margin."""
    frame = config.JET_FRAME_CATALOG[frame_type]
    distance = math.dist(home_base, target_position)
    ticks_one_way = distance / frame.max_speed if frame.max_speed > 0.0 else math.inf
    return 2.0 * ticks_one_way * frame.fuel_burn_per_tick * config.RTB_FUEL_SAFETY_MARGIN


def jet_can_reach_round_trip(
    jet: JetSpec,
    home_base: tuple[float, float],
    target_position: tuple[float, float],
) -> bool:
    required = round_trip_fuel_required(jet.frame_type, home_base, target_position)
    capacity = config.JET_FRAME_CATALOG[jet.frame_type].fuel_capacity
    return required <= capacity


def max_weapon_range() -> float:
    return max(spec.range for spec in config.WEAPON_CATALOG.values())


def discovery_link_max_distance() -> float:
    return config.SENSOR_RADIUS - max_weapon_range() - DISCOVERY_MARGIN


def has_discovery_los(
    terrain: TerrainGrid,
    observer_xy: tuple[float, float],
    target_xy: tuple[float, float],
) -> bool:
    """Jet-altitude observer → ground-height target through city columns."""
    return has_line_of_sight(
        terrain,
        (observer_xy[0], observer_xy[1], config.JET_COMBAT_ALTITUDE),
        (target_xy[0], target_xy[1], config.GROUND_TARGET_HEIGHT),
    )


def discovery_link_clear(
    terrain: TerrainGrid,
    a: tuple[float, float],
    b: tuple[float, float],
    *,
    link_max: float | None = None,
) -> bool:
    """Distance + city-LOS gate for a conservative discovery edge."""
    max_d = discovery_link_max_distance() if link_max is None else link_max
    if math.dist(a, b) > max_d:
        return False
    return has_discovery_los(terrain, a, b) or has_discovery_los(terrain, b, a)


def legal_los_firing_points(
    terrain: TerrainGrid,
    shooter_xy: tuple[float, float],
    target_xy: tuple[float, float],
    *,
    shooter_z: float,
    target_z: float,
) -> list[tuple[int, int]]:
    """Intermediate supercover cells that do not block the given sight segment.

    Reusable by Phase 45 bid planning — does not score or negotiate.
    """
    if not has_line_of_sight(
        terrain,
        (shooter_xy[0], shooter_xy[1], shooter_z),
        (target_xy[0], target_xy[1], target_z),
    ):
        return []
    cells = segment_supercover_cells(terrain, shooter_xy, target_xy)
    return list(cells)


def land_cells(terrain: TerrainGrid) -> list[tuple[int, int]]:
    """Column/row indices of non-WATER cells (GROUND-passable land)."""
    return [
        (c, r)
        for r in range(terrain.rows)
        for c in range(terrain.columns)
        if terrain.cells[r][c].terrain_type != "WATER"
    ]


def largest_land_component_size(terrain: TerrainGrid) -> int:
    """Cell count of the largest 4-connected land component."""
    from collections import deque

    rows, cols = terrain.rows, terrain.columns
    seen = [[False] * cols for _ in range(rows)]
    best = 0
    for r in range(rows):
        for c in range(cols):
            if terrain.cells[r][c].terrain_type == "WATER" or seen[r][c]:
                continue
            size = 0
            queue: deque[tuple[int, int]] = deque([(c, r)])
            seen[r][c] = True
            while queue:
                cc, rr = queue.popleft()
                size += 1
                for nc, nr in (
                    (cc - 1, rr),
                    (cc + 1, rr),
                    (cc, rr - 1),
                    (cc, rr + 1),
                ):
                    if 0 <= nc < cols and 0 <= nr < rows:
                        if (
                            not seen[nr][nc]
                            and terrain.cells[nr][nc].terrain_type != "WATER"
                        ):
                            seen[nr][nc] = True
                            queue.append((nc, nr))
            best = max(best, size)
    return best


def ground_entity_on_passable_terrain(
    terrain: TerrainGrid, position: tuple[float, float]
) -> bool:
    try:
        return is_passable(terrain, position, "GROUND")
    except Exception:
        return False


def _destroy_subject_ids(scenario: ScenarioConfig) -> set[str]:
    return {
        sid
        for obj in (getattr(scenario, "mission_objectives", None) or [])
        if str(getattr(obj, "objective_type", "")) == "DESTROY"
        for sid in (getattr(obj, "subject_ids", None) or [])
    }


def _destroyables(scenario: ScenarioConfig) -> list[_Destroyable]:
    items: list[_Destroyable] = [
        _Destroyable(
            entity_id=t.target_id,
            position=t.position,
            hp_required=t.hp_required,
            target_type=t.target_type,
        )
        for t in scenario.targets
    ]
    items.extend(
        _Destroyable(
            entity_id=s.target_id,
            position=s.position,
            hp_required=s.hp_required,
            target_type=s.target_type,
        )
        for s in scenario.sam_sites
    )
    # Phase 42/44: AAA sites listed as DESTROY subjects.
    destroy_ids = _destroy_subject_ids(scenario)
    for site in getattr(scenario, "aaa_sites", []) or []:
        if site.target_id not in destroy_ids:
            continue
        items.append(
            _Destroyable(
                entity_id=site.target_id,
                position=site.position,
                hp_required=int(site.resolved_hp()),
                target_type="AAA_SITE",
            )
        )
    # Phase 43/44: enemy tanks that are DESTROY subjects.
    for tank in getattr(scenario, "tanks", []) or []:
        if tank.faction != "ENEMY" or tank.unit_id not in destroy_ids:
            continue
        from wraithnet.tanks import TANK_FRAME_CATALOG

        frame = TANK_FRAME_CATALOG[tank.frame_type]
        hp = int(tank.hp if tank.hp is not None else frame.hp)
        items.append(
            _Destroyable(
                entity_id=tank.unit_id,
                position=tank.position,
                hp_required=hp,
                target_type="GROUND_VEHICLE",
            )
        )
    # Phase 41/44: enemy corvettes that are DESTROY subjects.
    for corvette in getattr(scenario, "corvettes", []) or []:
        if corvette.unit_id not in destroy_ids:
            continue
        from wraithnet.naval import NAVAL_FRAME_CATALOG

        frame = NAVAL_FRAME_CATALOG[corvette.frame_type]
        items.append(
            _Destroyable(
                entity_id=corvette.unit_id,
                position=corvette.position,
                hp_required=int(frame.hp),
                target_type="SURFACE_VESSEL",
            )
        )
    return items


def _empty_type_capacity() -> dict[TargetType, int]:
    return {
        "HARDENED_BUNKER": 0,
        "MOBILE_RADAR": 0,
        "SAM_SITE": 0,
        "AAA_SITE": 0,
        "SURFACE_VESSEL": 0,
        "GROUND_VEHICLE": 0,
    }


def _required_hp_by_type(destroyables: Sequence[_Destroyable]) -> dict[TargetType, int]:
    required = _empty_type_capacity()
    for item in destroyables:
        required[item.target_type] = required.get(item.target_type, 0) + item.hp_required
    return required


def _theoretical_capacity_by_type(
    jets: Sequence[JetSpec],
) -> dict[TargetType, int]:
    """Capacity if every eligible weapon unit were spent on one type alone."""
    capacity = _empty_type_capacity()
    for jet in jets:
        for weapon, count in jet.payload.items():
            if count <= 0:
                continue
            for target_type in capacity:
                dmg = weapon_eligible_damage(weapon, target_type)
                if dmg > 0:
                    capacity[target_type] += dmg * count
    return capacity


def _payload_total_damage(payload: Mapping[WeaponType, int]) -> int:
    return sum(
        config.WEAPON_CATALOG[weapon].damage_per_hit * count
        for weapon, count in payload.items()
        if count > 0
    )


def _jet_contribution_to_target(jet: JetSpec, target_type: TargetType) -> int:
    total = 0
    for weapon, count in jet.payload.items():
        if count <= 0:
            continue
        dmg = weapon_eligible_damage(weapon, target_type)
        if dmg > 0:
            total += dmg * count
    return total


@dataclass
class _WeaponUnit:
    unit_id: int
    jet_id: str
    weapon: WeaponType
    damage: int
    eligible_target_ids: frozenset[str]


def _build_weapon_units(
    scenario: ScenarioConfig,
    destroyables: Sequence[_Destroyable],
) -> list[_WeaponUnit]:
    units: list[_WeaponUnit] = []
    next_id = 0
    for jet in scenario.jets:
        for weapon, count in sorted(jet.payload.items()):
            if count <= 0:
                continue
            for _ in range(count):
                eligible: list[str] = []
                for item in destroyables:
                    if weapon_eligible_damage(weapon, item.target_type) <= 0:
                        continue
                    if not jet_can_reach_round_trip(
                        jet, scenario.home_base, item.position
                    ):
                        continue
                    eligible.append(item.entity_id)
                units.append(
                    _WeaponUnit(
                        unit_id=next_id,
                        jet_id=jet.agent_id,
                        weapon=weapon,
                        damage=config.WEAPON_CATALOG[weapon].damage_per_hit,
                        eligible_target_ids=frozenset(eligible),
                    )
                )
                next_id += 1
    return units


def _max_flow_covers_hp(
    units: Sequence[_WeaponUnit],
    destroyables: Sequence[_Destroyable],
) -> bool:
    """Exact joint allocation via Edmonds–Karp on a unit/target flow network.

    Graph: source → each unit (cap 1) → eligible targets (cap damage) →
    sink (cap hp_required). Feasible iff max flow equals total HP.
    """
    if not destroyables:
        return True
    total_hp = sum(item.hp_required for item in destroyables)
    if total_hp <= 0:
        return True

    # Node indexing: source=0, units 1..U, targets U+1..U+T, sink=U+T+1
    unit_count = len(units)
    target_ids = [item.entity_id for item in destroyables]
    target_index = {tid: i for i, tid in enumerate(target_ids)}
    source = 0
    unit_offset = 1
    target_offset = unit_offset + unit_count
    sink = target_offset + len(destroyables)
    node_count = sink + 1

    capacity = [[0] * node_count for _ in range(node_count)]

    for unit in units:
        u = unit_offset + unit.unit_id
        capacity[source][u] = 1
        for tid in unit.eligible_target_ids:
            if tid not in target_index:
                continue
            t = target_offset + target_index[tid]
            capacity[u][t] = unit.damage

    for item in destroyables:
        t = target_offset + target_index[item.entity_id]
        capacity[t][sink] = item.hp_required

    def bfs(parent: list[int]) -> int:
        parent[:] = [-1] * node_count
        parent[source] = source
        queue: deque[tuple[int, int]] = deque([(source, 10**9)])
        while queue:
            node, flow = queue.popleft()
            for nxt in range(node_count):
                if parent[nxt] == -1 and capacity[node][nxt] > 0:
                    parent[nxt] = node
                    new_flow = min(flow, capacity[node][nxt])
                    if nxt == sink:
                        return new_flow
                    queue.append((nxt, new_flow))
        return 0

    flow = 0
    parent = [-1] * node_count
    while True:
        bottled = bfs(parent)
        if bottled == 0:
            break
        flow += bottled
        cur = sink
        while cur != source:
            prev = parent[cur]
            capacity[prev][cur] -= bottled
            capacity[cur][prev] += bottled
            cur = prev
        if flow >= total_hp:
            return True
    return flow >= total_hp


def _group_centers(
    destroyables: Sequence[_Destroyable],
    group_ids_by_entity: Mapping[str, str] | None,
) -> dict[str, tuple[float, float]]:
    """Map group_id → representative position (mean of member positions)."""
    members: dict[str, list[tuple[float, float]]] = defaultdict(list)
    if group_ids_by_entity is None:
        for item in destroyables:
            members[item.entity_id].append(item.position)
    else:
        for item in destroyables:
            gid = group_ids_by_entity.get(item.entity_id, item.entity_id)
            members[gid].append(item.position)
    centers: dict[str, tuple[float, float]] = {}
    for gid, positions in members.items():
        xs = sum(p[0] for p in positions) / len(positions)
        ys = sum(p[1] for p in positions) / len(positions)
        centers[gid] = (xs, ys)
    return centers


def _groups_reachable_from_launch(
    scenario: ScenarioConfig,
    group_centers: Mapping[str, tuple[float, float]],
) -> set[str]:
    """Groups with a rooted discovery chain from the launch ring (distance + LOS)."""
    if not group_centers:
        return set()

    link_max = discovery_link_max_distance()
    terrain = scenario.terrain
    home = scenario.home_base
    roots: list[str] = []
    for gid, center in group_centers.items():
        dist = math.dist(home, center)
        approach = max(0.0, dist - LAUNCH_RING_OUTER_RADIUS)
        if approach > config.SENSOR_RADIUS:
            # Over-horizon AO: mission brief provides transit waypoints
            # (Observation.briefed_seek_positions). Local discovery applies on arrival.
            roots.append(gid)
            continue
        # Approximate the approach point on the launch ring toward the group.
        if dist <= 1e-9:
            approach_xy = home
        else:
            t = min(1.0, LAUNCH_RING_OUTER_RADIUS / dist)
            approach_xy = (
                home[0] + (center[0] - home[0]) * t,
                home[1] + (center[1] - home[1]) * t,
            )
        if has_discovery_los(terrain, approach_xy, center):
            roots.append(gid)

    if not roots:
        return set()

    ids = sorted(group_centers)
    adj: dict[str, list[str]] = {gid: [] for gid in ids}
    for i, a in enumerate(ids):
        for b in ids[i + 1 :]:
            if discovery_link_clear(
                terrain, group_centers[a], group_centers[b], link_max=link_max
            ):
                adj[a].append(b)
                adj[b].append(a)

    reachable: set[str] = set()
    queue = deque(sorted(roots))
    for r in roots:
        reachable.add(r)
    while queue:
        cur = queue.popleft()
        for nxt in adj[cur]:
            if nxt not in reachable:
                reachable.add(nxt)
                queue.append(nxt)
    return reachable


def _cooperative_objective_ids(
    scenario: ScenarioConfig,
    destroyables: Sequence[_Destroyable],
) -> list[str]:
    """Objectives whose HP exceeds any single jet's eligible contribution."""
    cooperative: list[str] = []
    for item in destroyables:
        contributions = [
            _jet_contribution_to_target(jet, item.target_type)
            for jet in scenario.jets
            if jet_can_reach_round_trip(jet, scenario.home_base, item.position)
        ]
        if not contributions:
            continue
        best = max(contributions)
        total = sum(contributions)
        # True cooperation: needs more than one jet, but fleet can cover it
        # when ignoring joint ammunition contention across other targets.
        if best < item.hp_required <= total:
            cooperative.append(item.entity_id)
    return sorted(cooperative)


def compute_challenge_components(
    scenario: ScenarioConfig,
    report_inputs: ChallengeScoreInputs,
) -> dict[str, float]:
    """Named challenge components, each clamped to ``[0, 100]``."""
    grid_diag = math.hypot(scenario.grid_width, scenario.grid_height) or 1.0

    total_required = sum(report_inputs.required_hp_by_target_type.values())
    total_capacity = sum(report_inputs.total_damage_capacity_by_target_type.values())
    # Burden rises with HP demand relative to a soft reference of 20 HP.
    objective_burden = min(100.0, (total_required / 20.0) * 100.0)

    sam_pressure = min(100.0, (report_inputs.sam_count / 6.0) * 100.0)
    jamming_pressure = min(100.0, (report_inputs.jammer_count / 4.0) * 100.0)
    route_distance = min(
        100.0, report_inputs.mean_objective_distance_fraction * 100.0
    )

    denom = max(1, report_inputs.destroyable_count)
    cooperation_pressure = min(
        100.0, (report_inputs.cooperative_objective_count / denom) * 100.0
    )

    # Reserve ratio >= 1.5 → low penalty; <= 1.0 → high penalty.
    reserve = report_inputs.friendly_reserve_ratio
    if total_capacity <= 0:
        friendly_reserve_penalty = 100.0
    else:
        friendly_reserve_penalty = min(
            100.0, max(0.0, (1.5 - reserve) / 0.5 * 100.0)
        )

    # Fuel margin: ratio of capacity to required round-trip; 2.0+ → 0 penalty.
    margin = report_inputs.min_fuel_margin_ratio
    if math.isinf(margin):
        fuel_margin_penalty = 0.0
    else:
        fuel_margin_penalty = min(100.0, max(0.0, (2.0 - margin) / 1.0 * 100.0))

    # Silence unused diag (kept for future calibration hooks).
    _ = grid_diag

    raw = {
        "objective_burden": objective_burden,
        "sam_pressure": sam_pressure,
        "jamming_pressure": jamming_pressure,
        "route_distance": route_distance,
        "cooperation_pressure": cooperation_pressure,
        "friendly_reserve_penalty": friendly_reserve_penalty,
        "fuel_margin_penalty": fuel_margin_penalty,
    }
    return {name: max(0.0, min(100.0, raw[name])) for name in CHALLENGE_COMPONENT_NAMES}


def _challenge_score(components: Mapping[str, float]) -> float:
    score = sum(
        _CHALLENGE_WEIGHTS[name] * components.get(name, 0.0)
        for name in CHALLENGE_COMPONENT_NAMES
    )
    return max(0.0, min(100.0, score))


def analyze_feasibility(
    scenario: ScenarioConfig,
    *,
    group_ids_by_entity: Mapping[str, str] | None = None,
    group_centers: Mapping[str, tuple[float, float]] | None = None,
) -> FeasibilityReport:
    """Catalog-driven static feasibility report for a concrete scenario.

    ``feasible`` is False iff at least one issue has severity ERROR.

    Optional ``group_centers`` supplies canonical group positions (Phase 31
    generation centers). When omitted, centers are inferred as the mean of
    member destroyable positions, which can drift when escorts are offset.
    """
    issues: list[FeasibilityIssue] = []
    destroyables = _destroyables(scenario)
    required = _required_hp_by_type(destroyables)
    capacity = _theoretical_capacity_by_type(scenario.jets)

    # Terrain passability / land component (Phase 35 / 44).
    terrain = scenario.terrain
    land_size = largest_land_component_size(terrain)
    if land_size < 1:
        issues.append(
            FeasibilityIssue(
                severity="ERROR",
                code=NO_LAND_SPAWN_COMPONENT,
                message="Terrain has no land cells for ground entities or a fixed base.",
                related_ids=[],
            )
        )
        issues.append(
            FeasibilityIssue(
                severity="ERROR",
                code=NO_VALID_LAND_COMPONENT,
                message="No valid connected land component for GROUND domain spawns.",
                related_ids=[],
            )
        )

    water_cells_n = sum(
        1
        for r in range(terrain.rows)
        for c in range(terrain.columns)
        if terrain.cells[r][c].terrain_type == "WATER"
    )
    needs_water = bool(
        getattr(scenario, "carriers", None) or getattr(scenario, "corvettes", None)
    )
    if needs_water and water_cells_n < 1:
        issues.append(
            FeasibilityIssue(
                severity="ERROR",
                code=NO_VALID_WATER_COMPONENT,
                message="Naval units require a connected water component.",
                related_ids=[
                    *(c.unit_id for c in (getattr(scenario, "carriers", None) or [])),
                    *(c.unit_id for c in (getattr(scenario, "corvettes", None) or [])),
                ],
            )
        )

    for label, position, domain in (
        *((f"Target '{t.target_id}'", t.position, "GROUND") for t in scenario.targets),
        *((f"SAM '{s.target_id}'", s.position, "GROUND") for s in scenario.sam_sites),
        *((f"Jammer '{j.jammer_id}'", j.position, "GROUND") for j in scenario.jammers),
        *((f"Tank '{t.unit_id}'", t.position, "GROUND") for t in getattr(scenario, "tanks", []) or []),
        *((f"AAA '{a.target_id}'", a.position, "GROUND") for a in getattr(scenario, "aaa_sites", []) or []),
        *(
            (f"Carrier '{c.unit_id}'", c.position, "SURFACE")
            for c in getattr(scenario, "carriers", []) or []
        ),
        *(
            (f"Corvette '{c.unit_id}'", c.position, "SURFACE")
            for c in getattr(scenario, "corvettes", []) or []
        ),
        ("Home base", scenario.home_base, "GROUND"),
    ):
        ok = (
            ground_entity_on_passable_terrain(terrain, position)
            if domain == "GROUND"
            else is_passable(terrain, position, "SURFACE")
        )
        if not ok:
            issues.append(
                FeasibilityIssue(
                    severity="ERROR",
                    code=ENTITY_ON_IMPASSABLE_TERRAIN,
                    message=f"{label} is on impassable terrain for {domain} domain.",
                    related_ids=[],
                )
            )
            issues.append(
                FeasibilityIssue(
                    severity="ERROR",
                    code=INVALID_DOMAIN_SPAWN,
                    message=f"{label} violates domain spawn rules ({domain}).",
                    related_ids=[],
                )
            )

    # Phase 43: ground discovery deadlock for opposing tanks.
    from wraithnet.tanks import TANK_FRAME_CATALOG, ground_discovery_deadlock

    friendlies = [
        (t.unit_id, t.position)
        for t in getattr(scenario, "tanks", []) or []
        if t.faction == "FRIENDLY"
    ]
    enemies = [
        (t.unit_id, t.position)
        for t in getattr(scenario, "tanks", []) or []
        if t.faction == "ENEMY"
    ]
    if friendlies and enemies:
        sight = max(
            TANK_FRAME_CATALOG[t.frame_type].sight_range
            for t in scenario.tanks
        )
        stuck = ground_discovery_deadlock(
            terrain, friendlies, enemies, sight_range=sight
        )
        hold_only = all(
            t.order == "HOLD" and not t.waypoints for t in scenario.tanks
        )
        destroy_subjects = {
            sid
            for obj in (getattr(scenario, "mission_objectives", None) or [])
            if str(getattr(obj, "objective_type", "")) == "DESTROY"
            for sid in (getattr(obj, "subject_ids", None) or [])
        }
        # Separated all-HOLD pairs beyond sight: treat as deadlock even if
        # land path exists (they will never close without orders).
        if hold_only:
            for eid, epos in enemies:
                if any(math.dist(epos, fpos) <= sight for _, fpos in friendlies):
                    continue
                if eid not in stuck:
                    stuck.append(eid)
        for eid in stuck:
            severity = "ERROR" if (eid in destroy_subjects or hold_only) else "WARNING"
            issues.append(
                FeasibilityIssue(
                    severity=severity,
                    code=GROUND_DISCOVERY_DEADLOCK,
                    message=(
                        f"Enemy tank '{eid}' cannot reach discovery of any friendly "
                        "ground vehicle on connected land (or all-HOLD deadlock)."
                    ),
                    related_ids=[eid],
                )
            )

    # Empty / zero-damage loadouts.
    for jet in scenario.jets:
        total_weapons = sum(max(0, c) for c in jet.payload.values())
        if total_weapons <= 0 or _payload_total_damage(jet.payload) <= 0:
            issues.append(
                FeasibilityIssue(
                    severity="WARNING",
                    code=EMPTY_LOADOUT,
                    message=(
                        f"Jet '{jet.agent_id}' has an empty or zero-damage loadout."
                    ),
                    related_ids=[jet.agent_id],
                )
            )

    friendly_tanks = [
        t
        for t in (getattr(scenario, "tanks", []) or [])
        if t.faction == "FRIENDLY"
    ]
    has_friendly_platform = bool(scenario.jets) or bool(friendly_tanks) or bool(
        getattr(scenario, "carriers", None) or []
    )
    if destroyables and not has_friendly_platform:
        issues.append(
            FeasibilityIssue(
                severity="ERROR",
                code=OBJECTIVE_UNACHIEVABLE,
                message=(
                    "No friendly active platform (jet, tank, or carrier) is present "
                    "to prosecute destroyable objectives."
                ),
                related_ids=[d.entity_id for d in destroyables],
            )
        )

    # No suitable weapon for an individual destroyable entity.
    for item in destroyables:
        has_suitable = False
        for jet in scenario.jets:
            if _jet_contribution_to_target(jet, item.target_type) > 0:
                has_suitable = True
                break
        # Jetless tank-only: friendly tanks can prosecute GROUND_VEHICLE.
        if (
            not has_suitable
            and item.target_type == "GROUND_VEHICLE"
            and friendly_tanks
        ):
            has_suitable = True
        if not has_suitable:
            issues.append(
                FeasibilityIssue(
                    severity="ERROR",
                    code=NO_SUITABLE_WEAPON,
                    message=(
                        f"No weapon in the friendly roster can damage "
                        f"'{item.entity_id}' ({item.target_type})."
                    ),
                    related_ids=[item.entity_id],
                )
            )

    # Phase 44: jets lack anti-armor while enemy tanks are DESTROY subjects and
    # no friendly tanks are available as a ground alternative.
    armor_hp = required.get("GROUND_VEHICLE", 0)
    if armor_hp > 0 and capacity.get("GROUND_VEHICLE", 0) <= 0 and not friendly_tanks:
        issues.append(
            FeasibilityIssue(
                severity="ERROR",
                code=NO_ANTI_ARMOR_CAPABILITY,
                message=(
                    "Enemy tanks require anti-armor weapons or friendly tanks, "
                    "but neither is available."
                ),
                related_ids=[
                    d.entity_id
                    for d in destroyables
                    if d.target_type == "GROUND_VEHICLE"
                ],
            )
        )

    # Phase 44: anti-ship / recovery / missile-defense burden.
    corvettes = list(getattr(scenario, "corvettes", None) or [])
    carriers = list(getattr(scenario, "carriers", None) or [])
    if corvettes:
        anti_ship = capacity.get("SURFACE_VESSEL", 0)
        # Also count AGM84/JDAM via jet contribution even if type map sparse.
        if anti_ship <= 0:
            anti_ship = sum(
                _jet_contribution_to_target(j, "SURFACE_VESSEL") for j in scenario.jets
            )
        if anti_ship <= 0 and scenario.jets:
            issues.append(
                FeasibilityIssue(
                    severity="WARNING",
                    code=NO_ANTI_SHIP_CAPABILITY,
                    message=(
                        "Enemy corvettes are present but no friendly jet carries "
                        "an anti-ship-capable weapon."
                    ),
                    related_ids=[c.unit_id for c in corvettes],
                )
            )
        # Rough CIWS/SAM magazine overmatch heuristic.
        defense_rounds = sum(
            int((c.loadout or {}).get("CIWS", 0))
            + int((c.loadout or {}).get("SAM_RADAR", 0))
            for c in corvettes
        )
        inbound_asm = sum(
            int(j.payload.get("AGM84", 0) or 0) for j in scenario.jets  # type: ignore[arg-type]
        )
        if defense_rounds > 0 and inbound_asm > 0 and defense_rounds >= inbound_asm * 3:
            issues.append(
                FeasibilityIssue(
                    severity="WARNING",
                    code=MISSILE_DEFENSE_OVERMATCH,
                    message=(
                        "Corvette point-defense magazines heavily outnumber "
                        "friendly anti-ship missiles (static warning)."
                    ),
                    related_ids=[c.unit_id for c in corvettes],
                )
            )

    if carriers and not (getattr(scenario, "fixed_airbases", None) or []):
        # Carrier-only recovery: ensure at least one carrier exists (already true).
        pass
    if not (getattr(scenario, "fixed_airbases", None) or []) and not carriers and scenario.jets:
        issues.append(
            FeasibilityIssue(
                severity="ERROR",
                code=NO_RECOVERY_BASE,
                message="No fixed airbase or carrier is available for recovery.",
                related_ids=[j.agent_id for j in scenario.jets],
            )
        )
    for carrier in carriers:
        route = list(getattr(carrier, "route", None) or [])
        if route:
            bad = False
            for wp in route:
                if not is_passable(terrain, tuple(wp), "SURFACE"):
                    bad = True
                    break
            if bad:
                issues.append(
                    FeasibilityIssue(
                        severity="ERROR",
                        code=UNREACHABLE_CARRIER_ROUTE,
                        message=(
                            f"Carrier '{carrier.unit_id}' route leaves navigable water."
                        ),
                        related_ids=[carrier.unit_id],
                    )
                )

    # Joint integer allocation across jet-prosecutable destroyables (reach-gated).
    # GROUND_VEHICLE subjects covered by friendly tanks are excluded — Phase 44
    # jetless tank-only scenarios are statically feasible without air ammo.
    jet_destroyables = [
        d
        for d in destroyables
        if not (d.target_type == "GROUND_VEHICLE" and friendly_tanks)
    ]
    units = _build_weapon_units(scenario, jet_destroyables)
    if jet_destroyables and not _max_flow_covers_hp(units, jet_destroyables):
        related = [item.entity_id for item in jet_destroyables]
        issues.append(
            FeasibilityIssue(
                severity="ERROR",
                code=INSUFFICIENT_DAMAGE,
                message=(
                    "Friendly ammunition cannot jointly cover all destroyable "
                    "HP under positive suitability and approximate round-trip "
                    "fuel reach (each weapon unit counts toward at most one target)."
                ),
                related_ids=related,
            )
        )

    # Per-type capacity shortfall as additional detail when allocation fails or
    # even when allocation succeeds but theoretical single-type pools look thin.
    for target_type, hp in required.items():
        if hp > 0 and capacity.get(target_type, 0) < hp:
            if target_type == "GROUND_VEHICLE" and friendly_tanks:
                continue
            # Avoid duplicate INSUFFICIENT_DAMAGE noise when joint alloc already failed.
            if not any(i.code == INSUFFICIENT_DAMAGE for i in issues):
                issues.append(
                    FeasibilityIssue(
                        severity="ERROR",
                        code=INSUFFICIENT_DAMAGE,
                        message=(
                            f"Total eligible damage for {target_type} is "
                            f"{capacity.get(target_type, 0)} but required HP is {hp}."
                        ),
                        related_ids=[
                            d.entity_id
                            for d in destroyables
                            if d.target_type == target_type
                        ],
                    )
                )

    # Approximate fuel reach: every destroyable group must be reachable by at
    # least one jet with a suitable weapon.
    inferred_centers = _group_centers(destroyables, group_ids_by_entity)
    if group_centers is not None:
        # Keep inferred ids (e.g. unmapped entities) while preferring canonical
        # centers for known generation groups.
        centers = dict(inferred_centers)
        centers.update(dict(group_centers))
    else:
        centers = inferred_centers
    unreachable: list[str] = []
    for gid, center in sorted(centers.items()):
        member_types = {
            item.target_type
            for item in destroyables
            if (
                group_ids_by_entity.get(item.entity_id, item.entity_id)
                if group_ids_by_entity is not None
                else item.entity_id
            )
            == gid
        }
        # Jetless ground combat: GROUND_VEHICLE-only groups need no air reach.
        if member_types and member_types <= {"GROUND_VEHICLE"} and friendly_tanks:
            continue
        if not scenario.jets:
            unreachable.append(gid)
            issues.append(
                FeasibilityIssue(
                    severity="ERROR",
                    code=FUEL_REACH,
                    message=(
                        f"No suitable jet can approximately reach group '{gid}' "
                        "and return to base under frame fuel capacity and "
                        "RTB_FUEL_SAFETY_MARGIN."
                    ),
                    related_ids=[gid],
                )
            )
            continue
        reachable = False
        for jet in scenario.jets:
            if not jet_can_reach_round_trip(jet, scenario.home_base, center):
                continue
            if any(_jet_contribution_to_target(jet, tt) > 0 for tt in member_types):
                reachable = True
                break
        if not reachable:
            unreachable.append(gid)
            issues.append(
                FeasibilityIssue(
                    severity="ERROR",
                    code=FUEL_REACH,
                    message=(
                        f"No suitable jet can approximately reach group '{gid}' "
                        "and return to base under frame fuel capacity and "
                        "RTB_FUEL_SAFETY_MARGIN."
                    ),
                    related_ids=[gid],
                )
            )

    # Discovery chain (distance + city LOS).
    # Ground vehicles / surface vessels use domain-specific discovery — not the
    # air fog-of-war chain rooted at the launch ring.
    reachable_groups = _groups_reachable_from_launch(scenario, centers)
    link_max = discovery_link_max_distance()
    for gid in sorted(centers):
        member_types = {
            item.target_type
            for item in destroyables
            if (
                group_ids_by_entity.get(item.entity_id, item.entity_id)
                if group_ids_by_entity is not None
                else item.entity_id
            )
            == gid
        }
        if member_types and member_types <= {"GROUND_VEHICLE", "SURFACE_VESSEL"}:
            continue
        if gid not in reachable_groups:
            issues.append(
                FeasibilityIssue(
                    severity="ERROR",
                    code=DISCOVERY_CHAIN_GAP,
                    message=(
                        f"Group '{gid}' is not connected by a conservative "
                        "fog-of-war discovery chain from the launch area."
                    ),
                    related_ids=[gid],
                )
            )
            # Distinguish pure LOS blockage from distance-only isolation.
            center = centers[gid]
            los_blocked = False
            home = scenario.home_base
            dist = math.dist(home, center)
            approach = max(0.0, dist - LAUNCH_RING_OUTER_RADIUS)
            if approach <= config.SENSOR_RADIUS:
                if dist <= 1e-9:
                    approach_xy = home
                else:
                    t = min(1.0, LAUNCH_RING_OUTER_RADIUS / dist)
                    approach_xy = (
                        home[0] + (center[0] - home[0]) * t,
                        home[1] + (center[1] - home[1]) * t,
                    )
                if not has_discovery_los(scenario.terrain, approach_xy, center):
                    los_blocked = True
            for other_id, other in centers.items():
                if other_id == gid:
                    continue
                if math.dist(center, other) <= link_max and not discovery_link_clear(
                    scenario.terrain, center, other, link_max=link_max
                ):
                    los_blocked = True
                    break
            if los_blocked:
                issues.append(
                    FeasibilityIssue(
                        severity="ERROR",
                        code=LOS_DISCOVERY_CHAIN_GAP,
                        message=(
                            f"Group '{gid}' discovery link is blocked by city "
                            "line-of-sight even where distance would allow it."
                        ),
                        related_ids=[gid],
                    )
                )
                issues.append(
                    FeasibilityIssue(
                        severity="ERROR",
                        code=LOS_DISCOVERY_GAP,
                        message=(
                            f"Group '{gid}' discovery link is blocked by city "
                            "line-of-sight even where distance would allow it."
                        ),
                        related_ids=[gid],
                    )
                )

    # Launch-area SAM threat warning (non-fatal).
    for sam in scenario.sam_sites:
        if math.dist(scenario.home_base, sam.position) < (
            config.SAM_ENGAGEMENT_RANGE + LAUNCH_RING_OUTER_RADIUS
        ):
            issues.append(
                FeasibilityIssue(
                    severity="WARNING",
                    code=LAUNCH_AREA_THREATENED,
                    message=(
                        f"SAM '{sam.target_id}' engages inside the friendly "
                        "launch ring at tick 0."
                    ),
                    related_ids=[sam.target_id],
                )
            )

    cooperative = _cooperative_objective_ids(scenario, destroyables)

    # Challenge inputs.
    if destroyables:
        mean_dist_frac = sum(
            math.dist(scenario.home_base, d.position)
            / (math.hypot(scenario.grid_width, scenario.grid_height) or 1.0)
            for d in destroyables
        ) / len(destroyables)
    else:
        mean_dist_frac = 0.0

    total_hp = sum(required.values()) or 1
    total_cap = sum(capacity.values())
    reserve_ratio = total_cap / total_hp if total_hp else math.inf

    fuel_margins: list[float] = []
    for jet in scenario.jets:
        for item in destroyables:
            required_fuel = round_trip_fuel_required(
                jet.frame_type, scenario.home_base, item.position
            )
            capacity_fuel = config.JET_FRAME_CATALOG[jet.frame_type].fuel_capacity
            if required_fuel <= 0:
                fuel_margins.append(math.inf)
            else:
                fuel_margins.append(capacity_fuel / required_fuel)
    min_fuel_margin = min(fuel_margins) if fuel_margins else math.inf

    report_inputs = ChallengeScoreInputs(
        required_hp_by_target_type=required,
        total_damage_capacity_by_target_type=capacity,
        objective_count=len(scenario.targets),
        sam_count=len(scenario.sam_sites),
        jammer_count=len(scenario.jammers),
        mean_objective_distance_fraction=mean_dist_frac,
        cooperative_objective_count=len(cooperative),
        destroyable_count=len(destroyables),
        friendly_reserve_ratio=reserve_ratio,
        min_fuel_margin_ratio=min_fuel_margin,
    )
    components = compute_challenge_components(scenario, report_inputs)
    score = _challenge_score(components)

    feasible = not any(issue.severity == "ERROR" for issue in issues)

    # Phase 37: thin per-objective rows; type totals remain the derived summary.
    by_id = {d.entity_id: d for d in destroyables}
    objective_rows: list[ObjectiveFeasibility] = []
    for obj in getattr(scenario, "mission_objectives", None) or []:
        subject_ids = list(getattr(obj, "subject_ids", None) or [])
        req_hp = 0
        alloc = 0
        for sid in subject_ids:
            item = by_id.get(sid)
            if item is None:
                continue
            req_hp += int(item.hp_required)
            alloc += int(capacity.get(item.target_type, 0))
        objective_rows.append(
            ObjectiveFeasibility(
                objective_id=obj.objective_id,
                objective_type=str(obj.objective_type),
                subject_ids=subject_ids,
                required_hp=req_hp,
                allocated_damage_capacity=alloc,
                reachable=True,
            )
        )

    return FeasibilityReport(
        feasible=feasible,
        issues=issues,
        total_damage_capacity_by_target_type=capacity,
        required_hp_by_target_type=required,
        unreachable_group_ids=unreachable,
        cooperative_objective_ids=cooperative,
        challenge_score=score,
        challenge_components=components,
        objective_feasibility=objective_rows,
    )
