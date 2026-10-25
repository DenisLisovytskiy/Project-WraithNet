"""Simulation constants and fixed PoC scenario for WraithNet.



Implemented in Phase 0 (Project Setup).

See docs/phases/Phase_0_Project_Setup.md.

Phase 6 extends this with heterogeneous weapons and target types.

Phase 20 adds a named jet-airframe catalog (speed/HP/turn-rate per frame).

Phase 21 replaces the flat global strike-range / suitability table with

a per-weapon catalog (range, firing arc, slot cost, damage).

Phase 38 adds guidance types, seeker params, and SensorSignature.

"""



import math

from dataclasses import dataclass

from typing import Literal



# --- Grid ---

# PoC / hand-authored default scenario size (scenarios/default.json). Generated
# missions default to DEFAULT_GENERATION_GRID_* (2× larger) via GenerationParameters.

GRID_WIDTH: float = 100.0

GRID_HEIGHT: float = 100.0

# Default battlespace for Quick Generate / GenerationParameters (jet transit scale).

DEFAULT_GENERATION_GRID_WIDTH: float = 200.0

DEFAULT_GENERATION_GRID_HEIGHT: float = 200.0

GRID_TORUS: bool = False

MAX_GRID_DISTANCE: float = math.sqrt(GRID_WIDTH**2 + GRID_HEIGHT**2)



# --- Movement / combat ---

JET_SPEED: float = 5.0        # max distance a jet can move per tick

# Per-weapon strike range lives on WEAPON_CATALOG (Phase 21). JDAM/AGM65

# keep range=1.0 so the default PoC is unchanged.



# --- Weapon / target typing (Phase 6 / Phase 21 / Phase 38) ---

WeaponType = Literal[

    "JDAM",

    "AIM9X",

    "AMRAAM",

    "GUN_POD",

    "AGM65",

    "AGM88",

    "AGM84",

    "SAM_IR",

    "SAM_RADAR",

]

GuidanceType = Literal["NONE", "INFRARED", "RADAR"]

TargetType = Literal[
    "HARDENED_BUNKER",
    "MOBILE_RADAR",
    "SAM_SITE",
    "AAA_SITE",
    "SURFACE_VESSEL",
    "GROUND_VEHICLE",
]



# --- Jet airframes (Phase 20) ---

# world.py spawn/movement/HP reads this catalog exclusively. JET_SPEED / JET_HP

# remain as module-level constants because viewer/render.py (HP bar) and tests

# still read them directly; they must stay numerically identical to STRIKE_FIGHTER.

JetFrameType = Literal["INTERCEPTOR", "STRIKE_FIGHTER", "HEAVY_BOMBER"]





@dataclass(frozen=True)

class SensorSignature:

    """Seeker-facing platform signature (docs/19 §8.1)."""



    heat: float = 0.0

    radar_cross_section: float = 0.0

    radar_emitter: bool = False





@dataclass(frozen=True)

class JetFrameSpec:

    max_speed: float

    # Yaw-axis rate cap (°/tick). Keep the field name for compatibility;

    # prefer yaw_rate_deg_per_tick when reading intent in new code.

    turn_rate_deg_per_tick: float

    fuel_capacity: float          # consumed per tick starting Phase 22

    fuel_burn_per_tick: float     # consumed per tick starting Phase 22

    ordnance_slots: int           # enforced by ScenarioConfig (Phase 21)

    hp: int

    evasion_bonus: int            # flat reduction to SAM damage, floored at 0

    # Phase 37: authoritative 2.5D altitude (world units). Defaults preserve

    # Phase 34 LOS endpoint (JET_COMBAT_ALTITUDE == cruise for all frames).

    cruise_altitude: float = 20.0

    max_climb_per_tick: float = 5.0

    min_combat_altitude: float = 2.0

    # Phase 50: per-axis attitude rates / limits (docs/22 §5.2).

    # Defaults match STRIKE_FIGHTER so ad-hoc JetFrameSpec(...) stays valid.

    pitch_rate_deg_per_tick: float = 12.0

    roll_rate_deg_per_tick: float = 40.0

    pitch_limit_deg: float = 30.0  # symmetric ± nose-up/down clamp

    roll_limit_deg: float = 180.0  # max |roll|; 180 allows inverted flight

    # Phase 38: default IR/radar signature for this airframe.

    signature: SensorSignature = SensorSignature(

        heat=0.7, radar_cross_section=0.6, radar_emitter=False

    )

    # Phase 39: countermeasure defaults (per-jet overrides on JetSpec).

    flare_capacity: int = 6

    flare_cooldown_ticks: int = 2

    flare_quality: float = 0.75

    radar_warning_receiver: bool = True

    notch_angle_window_deg: float = 30.0

    notch_min_speed: float = 2.0

    notch_hold_ticks: int = 2

    @property

    def yaw_rate_deg_per_tick(self) -> float:

        """Alias for turn_rate_deg_per_tick (yaw-axis cap)."""

        return self.turn_rate_deg_per_tick





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

    # Phase 38 guided-weapon fields (defaults keep JDAM/GUN_POD direct-fire).

    guidance: GuidanceType = "NONE"

    seeker_range: float = 0.0

    speed_per_tick: float = 0.0

    fuel_ticks: int = 0

    base_hit_probability: float = 1.0

    terminal_range: float = 0.0

    requires_support: bool = False

    countermeasure_resistance: float = 0.0

    min_heat: float = 0.0

    min_rcs: float = 0.0

    requires_radar_emitter: bool = False

    # Phase 52: release attitude envelope (None = no gate on that axis).

    max_release_abs_roll_deg: float | None = None

    max_release_abs_pitch_deg: float | None = None

    # Unguided deliveries must come down to this altitude to release, which
    # pulls attack runs into AAA reach (None = release from any altitude).
    max_release_altitude: float | None = None

    requires_upright: bool = False  # reject |roll| > 90° when True





# JDAM keeps range=1.0 and firing_arc_deg=360.0 so the fixed PoC outcome does

# not change. AGM65 inherits the legacy AIM9X *ground* role (MOBILE_RADAR) plus

# installation attack so migrated AMRAAM demand stays feasible. AIM9X/AMRAAM

# are air-to-air only (suitability all-zero vs TargetType; taxonomy gates AIRCRAFT).

# GUN_POD remains the narrow-forward-cone direct-fire demonstration.

_ZERO_GROUND: dict[TargetType, float] = {

    "HARDENED_BUNKER": 0.0,

    "MOBILE_RADAR": 0.0,

    "SAM_SITE": 0.0,

    "AAA_SITE": 0.0,

    "SURFACE_VESSEL": 0.0,

    "GROUND_VEHICLE": 0.0,

}



WEAPON_CATALOG: dict[WeaponType, WeaponSpec] = {

    "JDAM": WeaponSpec(

        range=1.0,

        firing_arc_deg=360.0,

        slot_cost=1,

        damage_per_hit=1,

        suitability={
            "HARDENED_BUNKER": 1.0,
            "MOBILE_RADAR": 0.0,
            "SAM_SITE": 1.0,
            "AAA_SITE": 1.0,
            "SURFACE_VESSEL": 1.0,
            "GROUND_VEHICLE": 1.0,
        },

        guidance="NONE",

        requires_upright=True,

        max_release_abs_roll_deg=45.0,

        max_release_abs_pitch_deg=30.0,

        max_release_altitude=15.0,

    ),

    "AIM9X": WeaponSpec(

        range=15.0,

        firing_arc_deg=360.0,

        slot_cost=1,

        damage_per_hit=1,

        suitability=dict(_ZERO_GROUND),

        guidance="INFRARED",

        seeker_range=15.0,

        speed_per_tick=12.0,

        fuel_ticks=8,

        base_hit_probability=0.85,

        terminal_range=1.0,

        requires_support=False,

        countermeasure_resistance=0.3,

        min_heat=0.4,

        min_rcs=0.0,

        requires_radar_emitter=False,

    ),

    "AMRAAM": WeaponSpec(

        range=20.0,

        firing_arc_deg=360.0,

        slot_cost=2,

        damage_per_hit=1,

        suitability=dict(_ZERO_GROUND),

        guidance="RADAR",

        seeker_range=20.0,

        speed_per_tick=14.0,

        fuel_ticks=10,

        base_hit_probability=0.8,

        terminal_range=3.0,

        requires_support=True,

        countermeasure_resistance=0.4,

        min_heat=0.0,

        min_rcs=0.2,

        requires_radar_emitter=False,

    ),

    "GUN_POD": WeaponSpec(

        range=0.5,

        firing_arc_deg=60.0,

        slot_cost=1,

        damage_per_hit=2,

        suitability={
            "HARDENED_BUNKER": 1.0,
            "MOBILE_RADAR": 0.0,
            "SAM_SITE": 1.0,
            "AAA_SITE": 1.0,
            "SURFACE_VESSEL": 0.0,
            "GROUND_VEHICLE": 1.0,
        },

        guidance="NONE",

        requires_upright=False,

        max_release_abs_roll_deg=90.0,

        max_release_abs_pitch_deg=45.0,

        max_release_altitude=10.0,

    ),

    # Legacy AIM9X ground role + imaging-IR A2G (incl. bunkers for AMRAAM migration).

    "AGM65": WeaponSpec(

        range=1.0,

        firing_arc_deg=360.0,

        slot_cost=1,

        damage_per_hit=1,

        suitability={

            "HARDENED_BUNKER": 1.0,

            "MOBILE_RADAR": 1.0,

            "SAM_SITE": 1.0,

            "AAA_SITE": 1.0,

            "SURFACE_VESSEL": 0.0,

            "GROUND_VEHICLE": 1.0,

        },

        guidance="INFRARED",

        seeker_range=1.0,

        speed_per_tick=20.0,

        fuel_ticks=2,

        base_hit_probability=1.0,

        terminal_range=0.5,

        requires_support=False,

        countermeasure_resistance=0.2,

        min_heat=0.05,

        min_rcs=0.0,

        requires_radar_emitter=False,

        requires_upright=True,

        max_release_abs_roll_deg=60.0,

        max_release_abs_pitch_deg=40.0,

    ),

    # Anti-radiation: radar-emitting installations only.

    "AGM88": WeaponSpec(

        range=20.0,

        firing_arc_deg=360.0,

        slot_cost=1,

        damage_per_hit=1,

        suitability={

            "HARDENED_BUNKER": 0.0,

            "MOBILE_RADAR": 1.0,

            "SAM_SITE": 1.0,

            "AAA_SITE": 0.0,

            "SURFACE_VESSEL": 0.0,

            "GROUND_VEHICLE": 0.0,

        },

        guidance="RADAR",

        seeker_range=20.0,

        speed_per_tick=14.0,

        fuel_ticks=10,

        base_hit_probability=0.9,

        terminal_range=2.0,

        requires_support=False,

        countermeasure_resistance=0.35,

        min_heat=0.0,

        min_rcs=0.0,

        requires_radar_emitter=True,

        requires_upright=True,

        max_release_abs_roll_deg=60.0,

        max_release_abs_pitch_deg=40.0,

    ),

    # Active-radar anti-ship missile (Phase 41). No suitability vs air/ground.

    "AGM84": WeaponSpec(

        range=25.0,

        firing_arc_deg=360.0,

        slot_cost=2,

        damage_per_hit=3,

        suitability={

            "HARDENED_BUNKER": 0.0,

            "MOBILE_RADAR": 0.0,

            "SAM_SITE": 0.0,

            "AAA_SITE": 0.0,

            "SURFACE_VESSEL": 1.0,

            "GROUND_VEHICLE": 0.0,

        },

        guidance="RADAR",

        seeker_range=25.0,

        speed_per_tick=10.0,

        fuel_ticks=12,

        base_hit_probability=0.85,

        terminal_range=2.5,

        requires_support=False,

        countermeasure_resistance=0.4,

        min_heat=0.0,

        min_rcs=0.3,

        requires_radar_emitter=False,

        requires_upright=True,

        max_release_abs_roll_deg=60.0,

        max_release_abs_pitch_deg=40.0,

    ),

    "SAM_IR": WeaponSpec(

        range=15.0,

        firing_arc_deg=360.0,

        slot_cost=1,

        damage_per_hit=1,

        suitability=dict(_ZERO_GROUND),

        guidance="INFRARED",

        seeker_range=15.0,

        speed_per_tick=10.0,

        fuel_ticks=6,

        base_hit_probability=1.0,

        terminal_range=1.0,

        requires_support=False,

        countermeasure_resistance=0.25,

        min_heat=0.4,

        min_rcs=0.0,

        requires_radar_emitter=False,

    ),

    "SAM_RADAR": WeaponSpec(

        range=15.0,

        firing_arc_deg=360.0,

        slot_cost=1,

        damage_per_hit=1,

        suitability=dict(_ZERO_GROUND),

        guidance="RADAR",

        seeker_range=15.0,

        speed_per_tick=10.0,

        fuel_ticks=6,

        base_hit_probability=1.0,

        terminal_range=2.0,

        requires_support=True,

        countermeasure_resistance=0.3,

        min_heat=0.0,

        min_rcs=0.2,

        requires_radar_emitter=False,

    ),

}



# Default SAM site magazine / cooldown (Phase 38 SamSiteSpec migration).

DEFAULT_SAM_MISSILE_LOADOUT: dict[WeaponType, int] = {"SAM_RADAR": 12}

DEFAULT_SAM_COOLDOWN_TICKS: int = 1

DEFAULT_SAM_FRAME_REF: str = "SAM_SITE_DEFAULT"



# Per-TargetType seeker signatures (installations / ground facilities).

TARGET_SIGNATURE_DEFAULTS: dict[TargetType, SensorSignature] = {

    "HARDENED_BUNKER": SensorSignature(

        heat=0.05, radar_cross_section=0.3, radar_emitter=False

    ),

    "MOBILE_RADAR": SensorSignature(

        heat=0.4, radar_cross_section=0.8, radar_emitter=True

    ),

    "SAM_SITE": SensorSignature(

        heat=0.3, radar_cross_section=0.7, radar_emitter=True

    ),

    "AAA_SITE": SensorSignature(

        heat=0.2, radar_cross_section=0.4, radar_emitter=False

    ),

    "SURFACE_VESSEL": SensorSignature(

        heat=0.25, radar_cross_section=0.85, radar_emitter=True

    ),

    "GROUND_VEHICLE": SensorSignature(

        heat=0.35, radar_cross_section=0.5, radar_emitter=False

    ),

}





# Fuel burn is flat per tick, not distance-scaled (design doc §5). Capacity is

# endurance in ticks; distance-range at max_speed is endurance * max_speed.

# burn_per_tick=1.0 for every frame, matching SAM_DAMAGE_PER_TICK's "1 unit per

# tick" simplicity. Capacities are sized for DEFAULT_GENERATION_GRID (200) —

# 2× the original PoC map — so a round-trip to a distant AO plus RTB margin fits:

#   INTERCEPTOR    2000 ticks × 7.0 = 14000 units (~1.4 width round-trips)

#   STRIKE_FIGHTER 6000 ticks × 5.0 = 30000 units (generated-map RTB headroom)

#   HEAVY_BOMBER   8000 ticks × 3.0 = 24000 units

# Small PoC maps (100×100) simply have excess fuel.

JET_FRAME_CATALOG: dict[JetFrameType, JetFrameSpec] = {

    "INTERCEPTOR": JetFrameSpec(

        max_speed=7.0,

        turn_rate_deg_per_tick=45.0,

        fuel_capacity=2000.0,

        fuel_burn_per_tick=1.0,

        ordnance_slots=4,

        hp=4,

        evasion_bonus=2,

        pitch_rate_deg_per_tick=20.0,

        roll_rate_deg_per_tick=60.0,

        pitch_limit_deg=35.0,

        roll_limit_deg=180.0,

        signature=SensorSignature(

            heat=0.85, radar_cross_section=0.45, radar_emitter=False

        ),

    ),

    "STRIKE_FIGHTER": JetFrameSpec(

        max_speed=5.0,  # must equal JET_SPEED — default-scenario jets use this frame

        turn_rate_deg_per_tick=25.0,

        fuel_capacity=6000.0,

        fuel_burn_per_tick=1.0,

        ordnance_slots=7,

        hp=5,  # must equal JET_HP — default-scenario jets use this frame

        evasion_bonus=1,

        pitch_rate_deg_per_tick=12.0,

        roll_rate_deg_per_tick=40.0,

        pitch_limit_deg=30.0,

        roll_limit_deg=180.0,

        signature=SensorSignature(

            heat=0.7, radar_cross_section=0.6, radar_emitter=False

        ),

    ),

    "HEAVY_BOMBER": JetFrameSpec(

        max_speed=3.0,

        turn_rate_deg_per_tick=12.0,

        fuel_capacity=8000.0,

        fuel_burn_per_tick=1.0,

        ordnance_slots=10,

        hp=7,

        evasion_bonus=0,

        pitch_rate_deg_per_tick=6.0,

        roll_rate_deg_per_tick=20.0,

        pitch_limit_deg=20.0,

        roll_limit_deg=180.0,

        signature=SensorSignature(

            heat=0.95, radar_cross_section=1.0, radar_emitter=False

        ),

    ),

}



# --- CNP utility function weights ---

UTILITY_W1: float = 1.0       # payload_score weight

UTILITY_W2: float = 0.5       # distance_score weight

UTILITY_W_WEAPON: float = 10.0  # dominates W1+W2's <=1.5 combined range; see docs/03_..., finding I2/I3



# --- Sensor radius (populated in Observation starting Phase 6; filtering starts Phase 7) ---

# Cap on generator discovery / objective distance from home.

SENSOR_RADIUS: float = 65.0

# A remembered-but-not-currently-sensed target older than this is forgotten.

DATALINK_CONTACT_TTL_TICKS: int = 15



# --- Temporary sight heights for 2.5D LOS (Phase 34; Phase 37 replaces jet Z) ---

# Jets use a fixed combat altitude until authoritative altitude lands.

JET_COMBAT_ALTITUDE: float = 20.0

# Ground targets / installations observed or struck from the air.

GROUND_TARGET_HEIGHT: float = 1.8

# Legacy SAM engagement sensor height (passive damage shortcut).

SAM_SENSOR_HEIGHT: float = 3.0



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


def max_grid_distance(grid_width: float, grid_height: float) -> float:
    """Diagonal of a rectangular battlespace (bid distance normalization)."""
    return math.sqrt(float(grid_width) ** 2 + float(grid_height) ** 2)


def contract_claim_ttl_for_grid(grid_width: float, grid_height: float) -> int:
    """TTL long enough for the slowest frame to fly a round trip on this map.

    Normalizing by ``JET_SPEED`` would only cover the *default* frame: a
    HEAVY_BOMBER cruises at 3.0, so on a 5000-unit map its cross-map round
    trip is 3333 ticks against a 2000-tick TTL. The contract would expire
    mid-transit and the target reopen for bidding while a bomber was still
    inbound. Size the TTL off the slowest frame and the true diagonal.
    """
    slowest = min(
        (spec.max_speed for spec in JET_FRAME_CATALOG.values() if spec.max_speed > 0.0),
        default=JET_SPEED,
    )
    diagonal = math.sqrt(float(grid_width) ** 2 + float(grid_height) ** 2)
    transit = int(math.ceil(2.0 * diagonal / max(slowest, 1e-9)))
    return max(CONTRACT_CLAIM_TTL_TICKS, transit)


# --- Messaging ---

BROADCAST_ID: str = "BROADCAST"



# --- Jet health / SAM (Phase 8) ---

JET_HP: int = 5

SAM_ENGAGEMENT_RANGE: float = 15.0

SAM_DAMAGE_PER_TICK: int = 1  # deterministic, no RNG (legacy; missiles use catalog)



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

# Defaults for schemas.AltitudeRules (each scenario can override them).
# Minimum height above the obstacle under a jet (building roof, treetops,
# ground). The world enforces it with a look-ahead climb; dipping below the
# obstacle itself is a crash.
TERRAIN_CLEARANCE: float = 3.0

# Altitude-hold autopilot: fraction of the altitude error flown per tick
# (lower = smoother capture, less overshoot from pitch-rate lag).
ALTITUDE_HOLD_GAIN: float = 0.5

# Surface-launched radar missiles lose jets below this relative altitude in
# ground clutter: flying low beats radar SAMs, flying high beats guns.
SAM_RADAR_MIN_ENGAGEMENT_AGL: float = 6.0

# Agent altitude tactics: transit this far above the highest known gun ceiling.
GUN_CEILING_MARGIN: float = 3.0

# --- Carrier recovery (Phase 40; catalog constants, not viewer cosmetics) ---

RECOVERY_APPROACH_RADIUS: float = 8.0

RECOVERY_ALTITUDE: float = 2.0

RECOVERY_HEADING_TOLERANCE_DEG: float = 30.0

RECOVERY_CAPTURE_RADIUS: float = 2.0

RECOVERY_RELATIVE_SPEED_MAX: float = 2.0

# Astern standoff for the approach alignment point (world units).

RECOVERY_APPROACH_STANDOFF: float = 6.0

# Phase 51: bank assist when desired_roll is None and the jet yaws this tick.

# Assisted roll target ≈ BANK_ASSIST_GAIN * yaw_step_deg (clamped by roll_limit).

BANK_ASSIST_GAIN: float = 1.0

# SW corner of the 100x100 PoC map: away from the fight (Target_A 75,75 /

# Target_B 25,25 / SAM 40,85 / jammer 46,19) and not on a jet spawn. A quiet

# recovery point the swarm can RTB to without flying through the SAM envelope.

DEFAULT_HOME_BASE: tuple[float, float] = (2.0, 2.0)



# --- Scenario (v3 doc §2) ---

# Literal source remains pre-guidance weapon names; scenario_from_data migrates

# AIM9X→AGM65 (and AMRAAM splits) via migrate_v3_to_v4.

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


