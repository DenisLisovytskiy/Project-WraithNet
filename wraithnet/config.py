"""Simulation constants and fixed PoC scenario for WraithNet.

Implemented in Phase 0 (Project Setup).
See docs/phases/Phase_0_Project_Setup.md.
Phase 6 extends this with heterogeneous weapons and target types.
"""

import math
from typing import Literal

# --- Grid ---
GRID_WIDTH: float = 100.0
GRID_HEIGHT: float = 100.0
GRID_TORUS: bool = False
MAX_GRID_DISTANCE: float = math.sqrt(GRID_WIDTH**2 + GRID_HEIGHT**2)

# --- Movement / combat ---
JET_SPEED: float = 5.0        # max distance a jet can move per tick
STRIKE_RANGE: float = 1.0     # must be within this distance of a target to STRIKE

# --- Weapon / target typing (Phase 6) ---
WeaponType = Literal["JDAM", "AIM9X"]
TargetType = Literal["HARDENED_BUNKER", "MOBILE_RADAR", "SAM_SITE"]

WEAPON_SUITABILITY: dict[tuple[str, str], float] = {
    ("JDAM", "HARDENED_BUNKER"): 1.0,
    ("JDAM", "MOBILE_RADAR"): 0.0,
    ("JDAM", "SAM_SITE"): 1.0,
    ("AIM9X", "HARDENED_BUNKER"): 0.0,
    ("AIM9X", "MOBILE_RADAR"): 1.0,
    ("AIM9X", "SAM_SITE"): 0.0,
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
}
