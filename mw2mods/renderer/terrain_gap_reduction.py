import json
import os


_DATA_PATH = os.path.join(os.path.dirname(__file__), "terrain_block_deltas.json")


def _load_terrain_block_deltas():
    try:
        with open(_DATA_PATH, "r", encoding="utf-8") as stream:
            payload = json.load(stream)
    except (OSError, TypeError, ValueError):
        return {}

    if (
        payload.get("kind") != "block_position_deltas"
        or payload.get("block_key") != "geometry"
        or payload.get("axis") != "xz"
    ):
        return {}

    levels = {}
    for level_name, entries in payload.get("levels", {}).items():
        level = {}
        for entry in entries:
            try:
                block_id = (
                    int(entry["nvert"]),
                    int(entry["nfaces"]),
                    int(entry["sum_x"]),
                    int(entry["sum_z"]),
                )
                level[block_id] = (
                    float(entry["dx"]),
                    float(entry["dz"]),
                )
            except (KeyError, TypeError, ValueError):
                continue
        if level:
            levels[str(level_name).casefold()] = level
    return levels


TERRAIN_BLOCK_DELTAS = _load_terrain_block_deltas()


def terrain_level_deltas(mission_name):
    return TERRAIN_BLOCK_DELTAS.get(str(mission_name or "").casefold())
