import re


# Stock CEL identities currently used by the enhanced rotor paths. Keep this
# catalog at the resource boundary so resolved textures carry human-readable
# identity instead of making render features depend on descriptor slots.
CEL_NAMES_BY_RESOURCE_ID = {
    610: "JSCAMOIA",
    611: "JSCAMOIE",
    612: "JSCAMOIF",
    613: "JSCAMO_E",
    614: "JSCAMO_F",
    706: "V1CRUISE",
    **{
        0x019E + frame - 1: f"A9HELE{frame:02d}"
        for frame in range(1, 10)
    },
    **{
        0x01A7 + frame - 1: f"A9HELI{frame:02d}"
        for frame in range(1, 6)
    },
}

CAMO_RESOURCE_IDS = frozenset((610, 611, 612, 613, 614))
CRUISE_RESOURCE_IDS = frozenset((706,))

# Explosion, weapon, jump-jet, and flag CEL families identified in
# cel_gallery/patterns.txt. Rotor ids 414..427 are intentionally excluded.
ENHANCED_IMAGING_EFFECT_RESOURCE_RANGES = (
    (1, 413),
    (428, 593),
    (634, 647),
)


def cel_name_for_resource_id(resource_id):
    return CEL_NAMES_BY_RESOURCE_ID.get(int(resource_id), "")


def is_enhanced_imaging_effect_cel(resource_id):
    resource_id = int(resource_id)
    return any(
        first <= resource_id <= last
        for first, last in ENHANCED_IMAGING_EFFECT_RESOURCE_RANGES
    )


def enhanced_texture_role(resource_id, name):
    normalized = str(name or "").strip().upper()
    resource_id = int(resource_id)
    if "CAMO" in normalized or resource_id in CAMO_RESOURCE_IDS:
        return "camo"
    if "CRUISE" in normalized or resource_id in CRUISE_RESOURCE_IDS:
        return "cruise"
    return ""


def is_aero_lift_fan_cel_name(name):
    normalized = str(name or "").strip().upper()
    return bool(
        re.search(r"HELI\d", normalized)
        or normalized.startswith("A9HELI")
    )
