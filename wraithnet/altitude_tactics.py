"""Where a jet flies vertically: climb capability and tactical altitude bands.

Shared by agents (what to command), the world (terrain look-ahead) and
scenario feasibility (what a jet can see from where it actually flies).
"""

from __future__ import annotations

import math

from wraithnet import config
from wraithnet.schemas import AltitudeRules, Observation, TargetInfo


def climb_rate(frame: config.JetFrameSpec) -> float:
    """Sustained altitude change per tick: dz = max_speed * sin(pitch_limit)."""
    return min(
        frame.max_climb_per_tick,
        frame.max_speed * math.sin(math.radians(frame.pitch_limit_deg)),
    )


def pitch_lag_ticks(frame: config.JetFrameSpec) -> int:
    """Ticks to rotate the nose from full nose-down to full nose-up."""
    return math.ceil(2.0 * frame.pitch_limit_deg / frame.pitch_rate_deg_per_tick)


def sam_low_level_radius(frame: config.JetFrameSpec, rules: AltitudeRules) -> float:
    """Distance from a SAM site inside which jets descend to fly under radar.

    SAM_RADAR range plus the ground covered while nosing over from level and
    descending from cruise to the radar clutter floor, so the jet is already
    under it when it enters the engagement envelope.
    """
    drop = max(0.0, frame.cruise_altitude - rules.sam_radar_min_engagement_agl)
    descent_ticks = math.ceil(drop / climb_rate(frame)) + math.ceil(
        frame.pitch_limit_deg / frame.pitch_rate_deg_per_tick
    )
    return config.WEAPON_CATALOG["SAM_RADAR"].range + frame.max_speed * descent_ticks


def transit_altitude(obs: Observation, known_targets: list[TargetInfo]) -> float:
    """Tactical altitude band for transit/egress from the known threat picture.

    Known guns: climb over the highest ceiling (guns kill low). Approaching a
    live SAM site: go low — the world floor holds terrain clearance and
    surface radar loses jets below the scenario's radar clutter floor.
    Otherwise cruise, where sensors see over buildings.
    """
    frame = config.JET_FRAME_CATALOG[obs.frame_type]
    cruise = float(frame.cruise_altitude)
    ceilings = [
        float(g.altitude_ceiling)
        for g in obs.observed_gun_threats
        if g.altitude_ceiling is not None
    ]
    if ceilings:
        return max(cruise, max(ceilings) + obs.altitude_rules.gun_ceiling_margin)
    low_radius = sam_low_level_radius(frame, obs.altitude_rules)
    if any(
        t.target_type == "SAM_SITE"
        and t.hp_remaining > 0
        and math.dist(obs.current_position, t.position) <= low_radius
        for t in known_targets
    ):
        return 0.0
    return cruise
