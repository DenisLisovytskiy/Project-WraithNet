"""Simulation constants and fixed PoC scenario for WraithNet.

Implemented in Phase 0 (Project Setup).
See docs/phases/Phase_0_Project_Setup.md.
Phase 6 extends this with heterogeneous weapons and target types.
Phase 20 adds a named jet-airframe catalog (speed/HP/turn-rate per frame).
Phase 21 replaces the flat global strike-range / suitability table with
a per-weapon catalog (range, firing arc, slot cost, damage).
"""

import math
from dataclasses import dataclass
from typing import Literal

# --- Grid ---
GRID_WIDTH: float = 100.0
GRID_HEIGHT: float = 100.0
GRID_TORUS: bool = False
MAX_GRID_DISTANCE: float = math.sqrt(GRID_WIDTH**2 + GRID_HEIGHT**2)

# --- Movement / combat ---
JET_SPEED: float = 5.0        # max distance a jet can move per tick
# Per-weapon strike range lives on WEAPON_CATALOG (Phase 21). JDAM/AIM9X
# keep range=1.0 so the default PoC is unchanged.

# --- Weapon / target typing (Phase 6 / Phase 21) ---
WeaponType = Literal["JDAM", "AIM9X", "AMRAAM", "GUN_POD"]
TargetType = Literal["HARDENED_BUNKER", "MOBILE_RADAR", "SAM_SITE"]

# --- Jet airframes (Phase 20) ---
# world.py spawn/movement/HP reads this catalog exclusively. JET_SPEED / JET_HP
# remain as module-level constants because viewer/render.py (HP bar) and tests
# still read them directly; they must stay numerically identical to STRIKE_FIGHTER.
JetFrameType = Literal["INTERCEPTOR", "STRIKE_FIGHTER", "HEAVY_BOMBER"]


@dataclass(frozen=True)
class JetFrameSpec:
    max_speed: float
    turn_rate_deg_per_tick: float
    fuel_capacity: float          # consumed per tick starting Phase 22
    fuel_burn_per_tick: float     # consumed per tick starting Phase 22
    ordnance_slots: int           # enforced by ScenarioConfig (Phase 21)
    hp: int
    evasion_bonus: int            # NOT consumed until Phase 23


@dataclass(frozen=True)
class WeaponSpec:
    range: float
    firing_arc_deg: float   # 360 = all-aspect/self-guided: fires and tracks
                            # regardless of the jet's heading (including a
                            # target behind the jet — a guidance property of
                            # the weapon, not a mounting direction); <360 =
                            # the jet's actual heading must be within this
                            # cone of the target before the weapon can fire
    slot_cost: int          # consumes this many of the frame's ordnance_slots per unit
    damage_per_hit: int
    suitability: dict[TargetType, float]  # 0.0 = physically cannot damage this type


# JDAM / AIM9X keep range=1.0 and firing_arc_deg=360.0 (today's implicit
# omnidirectional 1.0-unit strike range) so the fixed PoC outcome does not change.
# AMRAAM is the long-range all-aspect demonstration: range 20 sits outside
# SAM_ENGAGEMENT_RANGE (15), so a jet can kill a SAM without entering its
# envelope, and firing_arc_deg=360 means it can do so while facing away.
# Suitability is deliberately not a strict upgrade of AIM9X — mediocre vs
# bunkers (0.3) so a JDAM is still the bunker weapon of choice.
# GUN_POD is the narrow-forward-cone demonstration: range 0.5 (shorter than
# JDAM) and a 60° cone (±30° of heading). damage_per_hit=2 against bunkers
# is why a rational agent would ever turn to use it instead of JDAM's 1 HP
# at slightly longer, all-aspect range. slot_cost=1 keeps it cheap.
WEAPON_CATALOG: dict[WeaponType, WeaponSpec] = {
    "JDAM": WeaponSpec(
        range=1.0,
        firing_arc_deg=360.0,
        slot_cost=1,
        damage_per_hit=1,
        suitability={"HARDENED_BUNKER": 1.0, "MOBILE_RADAR": 0.0, "SAM_SITE": 1.0},
    ),
    "AIM9X": WeaponSpec(
        range=1.0,
        firing_arc_deg=360.0,
        slot_cost=1,
        damage_per_hit=1,
        suitability={"HARDENED_BUNKER": 0.0, "MOBILE_RADAR": 1.0, "SAM_SITE": 0.0},
    ),
    "AMRAAM": WeaponSpec(
        range=20.0,
        firing_arc_deg=360.0,
        slot_cost=2,
        damage_per_hit=1,
        suitability={"HARDENED_BUNKER": 0.3, "MOBILE_RADAR": 1.0, "SAM_SITE": 1.0},
    ),
    "GUN_POD": WeaponSpec(
        range=0.5,
        firing_arc_deg=60.0,
        slot_cost=1,
        damage_per_hit=2,
        suitability={"HARDENED_BUNKER": 1.0, "MOBILE_RADAR": 0.0, "SAM_SITE": 1.0},
    ),
}


# Fuel burn is flat per tick, not distance-scaled (design doc §5). Capacity is
# endurance in ticks; distance-range at max_speed is endurance * max_speed.
# burn_per_tick=1.0 for every frame, matching SAM_DAMAGE_PER_TICK's "1 unit per
# tick" simplicity. capacity is then just endurance in ticks; distance-range at
# max_speed is endurance * max_speed. Chosen against GRID_WIDTH=100 so a
# round-trip across the map (2*GRID_WIDTH / max_speed ticks) plus loiter fits:
#   INTERCEPTOR    40 ticks × 7.0 = 280 units (~1.4 width round-trips) — short-legged dash
#   STRIKE_FIGHTER 80 ticks × 5.0 = 400 units; ~2.5× the 32-tick PoC so Phase 22
#                  bingo-fuel has headroom to fly home without changing the
#                  default scenario's outcome
#   HEAVY_BOMBER  120 ticks × 3.0 = 360 units — long loiter, similar distance-range
#                  to the fighter despite crawling
JET_FRAME_CATALOG: dict[JetFrameType, JetFrameSpec] = {
    "INTERCEPTOR": JetFrameSpec(
        max_speed=7.0,
        turn_rate_deg_per_tick=45.0,
        fuel_capacity=40.0,
        fuel_burn_per_tick=1.0,
        ordnance_slots=4,
        hp=4,
        evasion_bonus=2,
    ),
    "STRIKE_FIGHTER": JetFrameSpec(
        max_speed=5.0,  # must equal JET_SPEED — default-scenario jets use this frame
        turn_rate_deg_per_tick=25.0,
        fuel_capacity=80.0,
        fuel_burn_per_tick=1.0,
        ordnance_slots=7,
        hp=5,  # must equal JET_HP — default-scenario jets use this frame
        evasion_bonus=1,
    ),
    "HEAVY_BOMBER": JetFrameSpec(
        max_speed=3.0,
        turn_rate_deg_per_tick=12.0,
        fuel_capacity=120.0,
        fuel_burn_per_tick=1.0,
        ordnance_slots=10,
        hp=7,
        evasion_bonus=0,
    ),
}

# --- CNP utility function weights ---
UTILITY_W1: float = 1.0       # payload_score weight
UTILITY_W2: float = 0.5       # distance_score weight
UTILITY_W_WEAPON: float = 10.0  # dominates W1+W2's <=1.5 combined range; see docs/03_..., finding I2/I3

# --- Sensor radius (populated in Observation starting Phase 6; filtering starts Phase 7) ---
SENSOR_RADIUS: float = 65.0
# A remembered-but-not-currently-sensed target older than this is forgotten.
DATALINK_CONTACT_TTL_TICKS: int = 15

# --- Timeouts / negotiation ---
BID_TIMEOUT_SECONDS: float = 0.5
BID_TIMEOUT_TICKS: int = 3
# Must be >= BID_TIMEOUT_SECONDS * MAX_CFP_ROUNDS (5 * 0.5 = 2.5) so a manager
# can exhaust multi-round re-negotiation inside a single Bridge tick.
MAX_TICK_WAIT_SECONDS: float = 3.0
# Must exceed worst-case transit time for the fixed PoC (~15 ticks for a
# distant jet at JET_SPEED) so a fulfilled-but-in-flight contract isn't
# cleared mid-approach, forcing IDLE agents to reopen CFPs every few ticks.
CONTRACT_CLAIM_TTL_TICKS: int = 50

# --- Messaging ---
BROADCAST_ID: str = "BROADCAST"

# --- Jet health / SAM (Phase 8) ---
JET_HP: int = 5
SAM_ENGAGEMENT_RANGE: float = 15.0
SAM_DAMAGE_PER_TICK: int = 1  # deterministic, no RNG

# --- Electronic warfare / jamming (Phase 9) ---
JAM_RADIUS: float = 12.0
# Mirrors CONTRACT_CLAIM_TTL_TICKS — self-heal a bidder whose ACCEPT/REJECT
# was dropped by jamming (see Phase 9 Part D).
CONTRACTOR_PENDING_TTL_TICKS: int = 10

# --- Fuel / RTB (Phase 22) ---
# 20% extra over the straight-line ticks-home estimate so bingo is conservative
# about unmodeled turning, jamming, and combat (design doc §5).
RTB_FUEL_SAFETY_MARGIN: float = 1.2
# Generous enough that a jet `_apply_move`s onto home_base (exact snap when
# distance <= max_speed) is counted landed the same tick, without needing a
# second loiter; small relative to SAM_ENGAGEMENT_RANGE so it isn't a "safe
# zone" against SAMs.
RTB_LANDING_RADIUS: float = 2.0
# SW corner of the 100x100 PoC map: away from the fight (Target_A 75,75 /
# Target_B 25,25 / SAM 40,85 / jammer 46,19) and not on a jet spawn. A quiet
# recovery point the swarm can RTB to without flying through the SAM envelope.
DEFAULT_HOME_BASE: tuple[float, float] = (2.0, 2.0)

# --- Scenario (v3 doc §2) ---
SCENARIO = {
    "targets": [
        # hp_required=5 is deliberately > the max single-jet JDAM count (4), not just
        # > Jet_1's JDAM (1) — this makes destroying Target_A require at least
        # two JDAM-carrying jets no matter how the CNP utility weights are tuned.
        # See docs/00_Design_Review_Findings.md item F1. Do not "simplify" this number.
        {"target_id": "Target_A", "position": (75.0, 75.0), "hp_required": 5, "target_type": "HARDENED_BUNKER"},
        {"target_id": "Target_B", "position": (25.0, 25.0), "hp_required": 1, "target_type": "MOBILE_RADAR"},
    ],
    "jets": [
        {"agent_id": "Jet_1", "position": (70.0, 70.0), "payload": {"JDAM": 1, "AIM9X": 0}},
        {"agent_id": "Jet_2", "position": (5.0, 95.0), "payload": {"JDAM": 4, "AIM9X": 0}},
        {"agent_id": "Jet_3", "position": (95.0, 5.0), "payload": {"JDAM": 3, "AIM9X": 2}},
    ],
    "sam_sites": [
        {
            "target_id": "SAM_Site_1",
            "position": (40.0, 85.0),
            "hp_required": 1,
            "target_type": "SAM_SITE",
        },
    ],
    "jammers": [
        {"jammer_id": "EW_Jammer_1", "position": (46.0, 19.0)},
    ],
    # See DEFAULT_HOME_BASE above for why (2, 2).
    "home_base": (2.0, 2.0),
}
