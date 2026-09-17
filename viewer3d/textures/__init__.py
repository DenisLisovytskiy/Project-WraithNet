"""Procedural 3D unit meshes plus a leftover JPEG loader.

The 3D viewer draws airframes, ships, and ground units from sibling modules.
Faction is a paint color (teal-green friendly vs rust-red hostile), not a
JPEG. ``loader`` remains so the 2D replay viewer and tests can still key files.
"""

from __future__ import annotations

from panda3d.core import NodePath

from viewer3d.textures.aaa import attach_aaa_light, attach_aaa_turreted
from viewer3d.textures.carrier import attach_escort_carrier, attach_fleet_carrier
from viewer3d.textures.corvette import attach_corvette_light, attach_corvette_standard
from viewer3d.textures.faction import ENEMY, FRIENDLY, faction_paint, friendly_variant
from viewer3d.textures.hardened_bunker import attach_hardened_bunker
from viewer3d.textures.heavy_bomber import attach_heavy_bomber
from viewer3d.textures.home_base import attach_home_base
from viewer3d.textures.interceptor import attach_interceptor
from viewer3d.textures.jammer import attach_jammer
from viewer3d.textures.loader import (
    SPRITE_FILES,
    SPRITE_HALF,
    TERRAIN_FILES,
    _candidate_dirs,
    clear_cache,
    load_sprite,
    load_terrain,
    sprite_half,
    tank_sprite_key,
    texture_dir,
    texture_path,
    textures_available,
)
from viewer3d.textures.mobile_radar import attach_mobile_radar
from viewer3d.textures.sam_site import attach_sam_site
from viewer3d.textures.strike_fighter import attach_strike_fighter
from viewer3d.textures.tank import attach_mbt_light, attach_mbt_standard

JET_MESHES = {
    "HEAVY_BOMBER": attach_heavy_bomber,
    "STRIKE_FIGHTER": attach_strike_fighter,
    "INTERCEPTOR": attach_interceptor,
}

SHIP_MESHES = {
    "ESCORT_CARRIER": attach_escort_carrier,
    "FLEET_CARRIER": attach_fleet_carrier,
    "CORVETTE_LIGHT": attach_corvette_light,
    "CORVETTE_STANDARD": attach_corvette_standard,
}

GROUND_MESHES = {
    "HARDENED_BUNKER": attach_hardened_bunker,
    "MOBILE_RADAR": attach_mobile_radar,
    "SAM_SITE": attach_sam_site,
    "AAA_SITE": attach_aaa_light,
    "AAA_LIGHT": attach_aaa_light,
    "AAA_TURRETED": attach_aaa_turreted,
    "JAMMER": attach_jammer,
    "HOME_BASE": attach_home_base,
    "MBT_LIGHT": attach_mbt_light,
    "MBT_STANDARD": attach_mbt_standard,
}


def _tagged(node: NodePath, tag: str, key: str) -> NodePath:
    node.set_tag(tag, key)
    return node


def attach_jet_mesh(parent: NodePath, name: str, frame_type: str, color) -> NodePath | None:
    builder = JET_MESHES.get(str(frame_type or "").upper())
    if builder is None:
        return None
    return _tagged(builder(parent, name, color), "jet-mesh", str(frame_type).upper())


def attach_ship_mesh(parent: NodePath, name: str, frame_type: str, color) -> NodePath | None:
    builder = SHIP_MESHES.get(str(frame_type or "").upper())
    if builder is None:
        return None
    return _tagged(builder(parent, name, color), "ship-mesh", str(frame_type).upper())


def attach_ground_mesh(parent: NodePath, name: str, key: str, color) -> NodePath | None:
    builder = GROUND_MESHES.get(str(key or "").upper())
    if builder is None:
        return None
    return _tagged(builder(parent, name, color), "ground-mesh", str(key).upper())


__all__ = [
    "ENEMY",
    "FRIENDLY",
    "GROUND_MESHES",
    "JET_MESHES",
    "SHIP_MESHES",
    "SPRITE_FILES",
    "SPRITE_HALF",
    "TERRAIN_FILES",
    "attach_aaa_light",
    "attach_aaa_turreted",
    "attach_corvette_light",
    "attach_corvette_standard",
    "attach_escort_carrier",
    "attach_fleet_carrier",
    "attach_ground_mesh",
    "attach_hardened_bunker",
    "attach_heavy_bomber",
    "attach_home_base",
    "attach_interceptor",
    "attach_jammer",
    "attach_jet_mesh",
    "attach_mbt_light",
    "attach_mbt_standard",
    "attach_mobile_radar",
    "attach_sam_site",
    "attach_ship_mesh",
    "attach_strike_fighter",
    "clear_cache",
    "faction_paint",
    "friendly_variant",
    "load_sprite",
    "load_terrain",
    "sprite_half",
    "tank_sprite_key",
    "texture_dir",
    "texture_path",
    "textures_available",
    "_candidate_dirs",
]
