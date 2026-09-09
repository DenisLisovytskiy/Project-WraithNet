"""Shared faction / platform / target-class taxonomy (Phase 37).



Dependency-neutral: no GUI, no ScenarioConfig. Used by world adjudication,

schema validators, and later combined-arms platforms.

"""



from __future__ import annotations



from typing import Literal



from wraithnet.config import TargetType, WeaponType



Faction = Literal["FRIENDLY", "ENEMY"]

PlatformDomain = Literal["AIR", "GROUND", "SURFACE", "INSTALLATION"]

TargetClass = Literal[

    "AIRCRAFT",

    "GROUND_VEHICLE",

    "SURFACE_VESSEL",

    "INSTALLATION",

    "IN_FLIGHT_MISSILE",

]



# Existing TargetType values map onto INSTALLATION (static ground facilities).

TARGET_TYPE_TO_CLASS: dict[TargetType, TargetClass] = {

    "HARDENED_BUNKER": "INSTALLATION",

    "MOBILE_RADAR": "INSTALLATION",

    "SAM_SITE": "INSTALLATION",

    "AAA_SITE": "INSTALLATION",

    "SURFACE_VESSEL": "SURFACE_VESSEL",

    "GROUND_VEHICLE": "GROUND_VEHICLE",

}



# Weapons may only damage these target classes (Phase 37 gate; Phase 38 guidance).

WEAPON_LEGAL_TARGET_CLASSES: dict[WeaponType, frozenset[TargetClass]] = {

    "JDAM": frozenset({"INSTALLATION", "GROUND_VEHICLE", "SURFACE_VESSEL"}),

    "AIM9X": frozenset({"AIRCRAFT", "IN_FLIGHT_MISSILE"}),

    "AMRAAM": frozenset({"AIRCRAFT", "IN_FLIGHT_MISSILE"}),

    "GUN_POD": frozenset({"INSTALLATION", "GROUND_VEHICLE", "AIRCRAFT"}),

    "AGM65": frozenset({"INSTALLATION", "GROUND_VEHICLE"}),

    "AGM88": frozenset({"INSTALLATION"}),

    "AGM84": frozenset({"SURFACE_VESSEL"}),

    "SAM_IR": frozenset({"AIRCRAFT", "IN_FLIGHT_MISSILE"}),

    "SAM_RADAR": frozenset({"AIRCRAFT", "IN_FLIGHT_MISSILE"}),

}





def are_hostile(a: Faction, b: Faction) -> bool:

    """True iff the two factions may legally engage each other."""

    return a != b





def target_class_for_target_type(target_type: TargetType | str) -> TargetClass:

    return TARGET_TYPE_TO_CLASS.get(target_type, "INSTALLATION")  # type: ignore[arg-type]





def legal_target_classes_for_weapon(weapon: WeaponType | str) -> frozenset[TargetClass]:

    return WEAPON_LEGAL_TARGET_CLASSES.get(weapon, frozenset())  # type: ignore[arg-type]





def is_legal_weapon_target(

    weapon: WeaponType | str,

    target_class: TargetClass,

) -> bool:

    return target_class in legal_target_classes_for_weapon(weapon)





def reject_engagement(

    *,

    shooter_faction: Faction,

    target_faction: Faction,

    weapon: WeaponType | str,

    target_class: TargetClass,

) -> str | None:

    """Return a rejection reason, or None if the engagement is allowed."""

    if not are_hostile(shooter_faction, target_faction):

        return "friendly_fire"

    if not is_legal_weapon_target(weapon, target_class):

        return "illegal_target_class"

    return None


