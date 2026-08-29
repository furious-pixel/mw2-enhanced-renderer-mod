import struct
import zlib

from . import diagnostics
from .geometry import (
    ADDR_FOG_DISTANCE,
    ADDR_FOG_MIN,
    ADDR_LIGHT_DIR_FLAG,
    ADDR_LIGHT_X,
    ADDR_LIGHT_Y,
    ADDR_LIGHT_Z,
)
from .texture import (
    ADDR_TEXTURE_CELL_TABLE,
    ADDR_TEXTURE_DESCRIPTOR_TABLE,
    ADDR_TEXTURE_REMAP_TABLE_PTR,
    TEXTURE_CELL_PAGE_STRIDE,
    TEXTURE_CELL_SUB_ENTRY_COUNT,
    TEXTURE_CELL_SUB_ENTRY_STRIDE,
    TEXTURE_DESCRIPTOR_COUNT,
    TEXTURE_DESCRIPTOR_STRIDE,
    TEXTURE_REMAP_TABLE_COUNT,
    TEXTURE_REMAP_TABLE_SIZE,
)


SCENE_TEXTURE_DESCRIPTOR_BASE = 0x100
DEFAULT_INTERVAL_FRAMES = 300
MAX_CHANGE_EVENTS_PER_SAMPLE = 24

_STATE_ATTRIBUTE = "mw2_renderer_texture_state_audit"
_LAST_FRAME_ATTRIBUTE = "mw2_renderer_texture_state_audit_frame"

_SCENE_GROUP_ATTRIBUTE = "indexed_texmap_indices"
_BILLBOARD_GROUP_ATTRIBUTE = "billboard_instances"


def maybe_log_texture_state_audit(
    modstate,
    gamemem,
    geometry,
    raw_palette,
    interval_frames=DEFAULT_INTERVAL_FRAMES,
):
    frame = int(getattr(modstate, "frame", 0))
    interval_frames = max(1, int(interval_frames))
    last_frame = int(getattr(modstate, _LAST_FRAME_ATTRIBUTE, -1000000))
    if frame >= last_frame and frame - last_frame < interval_frames:
        return
    setattr(modstate, _LAST_FRAME_ATTRIBUTE, frame)

    try:
        snapshot = _snapshot_texture_state(gamemem, geometry, raw_palette)
    except Exception as error:
        diagnostics.debug_log(
            modstate,
            "texture_state_audit kind=error "
            f"error={type(error).__name__}:{error}",
        )
        return

    mission_generation = int(
        geometry.get("stats", {}).get("mission_generation", 0) or 0
    )
    state = getattr(modstate, _STATE_ATTRIBUTE, None)
    if state is not None and int(state.get("mission_generation", -1)) != mission_generation:
        state = None

    if state is None:
        state = {
            "mission_generation": mission_generation,
            "sample_count": 1,
            "previous": snapshot,
            "cumulative_membership": 0,
            "cumulative_display_animated": 0,
            "cumulative_display_nontimed": 0,
            "cumulative_tick_only": 0,
            "cumulative_residency_only": 0,
            "cumulative_palette": 0,
            "cumulative_remap": 0,
            "cumulative_light": 0,
            "cumulative_fog": 0,
            "ever_display_animated_changed": set(),
            "ever_display_nontimed_changed": set(),
            "ever_tick_only_changed": set(),
            "ever_residency_only_changed": set(),
        }
        setattr(modstate, _STATE_ATTRIBUTE, state)
        diagnostics.debug_log(
            modstate,
            _format_baseline(snapshot, mission_generation, interval_frames),
        )
        return

    changes = _compare_texture_state(state["previous"], snapshot)
    state["sample_count"] += 1
    state["previous"] = snapshot
    state["cumulative_membership"] += len(changes["membership"])
    state["cumulative_display_animated"] += len(changes["display_animated"])
    state["cumulative_display_nontimed"] += len(changes["display_nontimed"])
    state["cumulative_tick_only"] += len(changes["tick_only"])
    state["cumulative_residency_only"] += len(changes["residency_only"])
    state["cumulative_palette"] += int(changes["palette_changed"])
    state["cumulative_remap"] += int(changes["remap_changed"])
    state["cumulative_light"] += int(changes["light_changed"])
    state["cumulative_fog"] += int(changes["fog_changed"])
    state["ever_display_animated_changed"].update(
        event[0] for event in changes["display_animated"]
    )
    state["ever_display_nontimed_changed"].update(
        event[0] for event in changes["display_nontimed"]
    )
    state["ever_tick_only_changed"].update(
        event[0] for event in changes["tick_only"]
    )
    state["ever_residency_only_changed"].update(
        event[0] for event in changes["residency_only"]
    )

    diagnostics.debug_log(
        modstate,
        _format_sample(snapshot, state, changes),
    )
    events = (
        [("membership", event) for event in changes["membership"]]
        + [("display_nontimed", event) for event in changes["display_nontimed"]]
        + [("residency_only", event) for event in changes["residency_only"]]
    )
    for event_kind, event in events[:MAX_CHANGE_EVENTS_PER_SAMPLE]:
        diagnostics.debug_log(
            modstate,
            _format_change_event(event_kind, event),
        )
    if len(events) > MAX_CHANGE_EVENTS_PER_SAMPLE:
        diagnostics.debug_log(
            modstate,
            "texture_state_audit kind=change_limit "
            f"logged={MAX_CHANGE_EVENTS_PER_SAMPLE} "
            f"omitted={len(events) - MAX_CHANGE_EVENTS_PER_SAMPLE}",
        )


def reset_texture_state_audit(modstate):
    setattr(modstate, _STATE_ATTRIBUTE, None)
    setattr(modstate, _LAST_FRAME_ATTRIBUTE, -1000000)


def _snapshot_texture_state(gamemem, geometry, raw_palette):
    textures = geometry.get("textures", {})
    scene_descs = _group_descriptor_indices(
        geometry,
        _SCENE_GROUP_ATTRIBUTE,
    )
    scene_descs.update(
        int(desc_idx)
        for desc_idx in textures.keys()
        if SCENE_TEXTURE_DESCRIPTOR_BASE <= int(desc_idx) < TEXTURE_DESCRIPTOR_COUNT
    )
    scene_descs = {
        desc_idx
        for desc_idx in scene_descs
        if SCENE_TEXTURE_DESCRIPTOR_BASE <= desc_idx < TEXTURE_DESCRIPTOR_COUNT
    }
    billboard_descs = {
        desc_idx
        for desc_idx in _group_descriptor_indices(
            geometry,
            _BILLBOARD_GROUP_ATTRIBUTE,
        )
        if 0 <= desc_idx < SCENE_TEXTURE_DESCRIPTOR_BASE
    }

    descriptors = _read_scene_descriptors(gamemem, scene_descs)
    pages = {
        int(descriptor["page"])
        for descriptor in descriptors.values()
        if 0 <= int(descriptor["page"]) < 512
    }
    page_states = {
        page: _read_cell_page_state(gamemem, page)
        for page in sorted(pages)
    }

    desc_states = {}
    for desc_idx in sorted(scene_descs):
        descriptor = descriptors.get(desc_idx)
        texture = textures.get(desc_idx)
        desc_states[desc_idx] = _build_descriptor_state(
            descriptor,
            page_states,
            texture,
        )

    return {
        "palette_crc": _crc32(raw_palette),
        "remap": _read_remap_state(gamemem),
        "lighting": _read_lighting_state(gamemem),
        "descriptors": desc_states,
        "billboard_descs_excluded": len(billboard_descs),
    }


def _group_descriptor_indices(geometry, attribute):
    result = set()
    for partition_name in ("static", "dynamic"):
        partition = geometry.get(partition_name)
        grouped = getattr(partition, attribute, {})
        if hasattr(grouped, "keys"):
            result.update(int(value) for value in grouped.keys())
    return result


def _read_scene_descriptors(gamemem, desc_indices):
    if not desc_indices:
        return {}
    count = TEXTURE_DESCRIPTOR_COUNT - SCENE_TEXTURE_DESCRIPTOR_BASE
    data = gamemem.read_reloc_bytes(
        ADDR_TEXTURE_DESCRIPTOR_TABLE
        + SCENE_TEXTURE_DESCRIPTOR_BASE * TEXTURE_DESCRIPTOR_STRIDE,
        count * TEXTURE_DESCRIPTOR_STRIDE,
    )
    if len(data) < count * TEXTURE_DESCRIPTOR_STRIDE:
        raise ValueError("short scene texture descriptor table read")

    result = {}
    for desc_idx in sorted(desc_indices):
        offset = (
            int(desc_idx) - SCENE_TEXTURE_DESCRIPTOR_BASE
        ) * TEXTURE_DESCRIPTOR_STRIDE
        result[int(desc_idx)] = {
            "page": _i16(data, offset),
            "selector": _u16(data, offset + 2),
            "animation_interval": _u16(data, offset + 4),
            "state": _u16(data, offset + 6),
            "active_flag": _i16(data, offset + 8),
            "last_update_tick": _u16(data, offset + 10),
            "unknown_0C": _u16(data, offset + 12),
        }
    return result


def _read_cell_page_state(gamemem, page):
    data = gamemem.read_reloc_bytes(
        ADDR_TEXTURE_CELL_TABLE + int(page) * TEXTURE_CELL_PAGE_STRIDE,
        TEXTURE_CELL_PAGE_STRIDE,
    )
    if len(data) < TEXTURE_CELL_PAGE_STRIDE:
        raise ValueError(f"short texture cell page read for page {int(page)}")

    entries = {}
    identity_bytes = bytearray()
    for sub_entry in range(TEXTURE_CELL_SUB_ENTRY_COUNT):
        offset = sub_entry * TEXTURE_CELL_SUB_ENTRY_STRIDE
        resource_id = _i16(data, offset)
        identity_bytes.extend(data[offset:offset + 2])
        if resource_id < 1:
            continue
        entries[sub_entry] = (
            int(resource_id),
            int(_u16(data, offset + 2)),
            int(_u32(data, offset + 4)),
        )
    return {
        "entries": entries,
        "identity_crc": _crc32(identity_bytes),
        "table_crc": _crc32(data),
    }


def _build_descriptor_state(descriptor, page_states, texture):
    if descriptor is None:
        return {
            "family": "unreadable",
            "control": None,
            "tick": None,
            "selected": None,
            "selected_residency": None,
            "cell_identity_crc": None,
            "cell_table_crc": None,
            "pixel": _pixel_identity(texture),
            "resolved": texture is not None,
        }

    page = int(descriptor["page"])
    selector = int(descriptor["selector"])
    animation_interval = int(descriptor["animation_interval"])
    page_state = page_states.get(page)
    selected, selected_residency = _select_cell_entry(page_state, selector)
    texture_class = ""
    if texture is not None:
        texture_class = str(texture.get("animation_class", ""))
    if animation_interval:
        family = "timed"
    elif selector or texture_class == "animated":
        family = "selector_driven"
    elif texture_class:
        family = texture_class
    else:
        family = "unresolved"

    return {
        "family": family,
        "control": (
            page,
            selector,
            animation_interval,
            int(descriptor["state"]),
            int(descriptor["active_flag"]),
            int(descriptor["unknown_0C"]),
        ),
        "tick": int(descriptor["last_update_tick"]),
        "selected": selected,
        "selected_residency": selected_residency,
        "cell_identity_crc": (
            None if page_state is None else page_state["identity_crc"]
        ),
        "cell_table_crc": (
            None if page_state is None else page_state["table_crc"]
        ),
        "pixel": _pixel_identity(texture),
        "resolved": texture is not None,
    }


def _select_cell_entry(page_state, selector):
    if page_state is None or not page_state["entries"]:
        return None, None
    entries = page_state["entries"]
    if selector in entries:
        selected_sub_entry = selector
    else:
        valid_sub_entries = sorted(entries.keys())
        lower = [sub_entry for sub_entry in valid_sub_entries if sub_entry <= selector]
        selected_sub_entry = lower[-1] if lower else valid_sub_entries[0]
    resource_id, timestamp, data_ptr = entries[selected_sub_entry]
    return (
        (int(selected_sub_entry), int(resource_id)),
        (int(timestamp), int(data_ptr != 0)),
    )


def _pixel_identity(texture):
    if texture is None:
        return None
    pixels = texture.get("pixels")
    return (
        int(texture.get("width", 0)),
        int(texture.get("height", 0)),
        None if pixels is None else _crc32(pixels),
    )


def _read_remap_state(gamemem):
    pointer = int(gamemem.read_reloc_u32(ADDR_TEXTURE_REMAP_TABLE_PTR))
    if pointer <= 0:
        return (pointer, None)
    size = TEXTURE_REMAP_TABLE_COUNT * TEXTURE_REMAP_TABLE_SIZE
    data = gamemem.read_runtime_bytes(pointer, size)
    if len(data) < size:
        return (pointer, None)
    return (pointer, _crc32(data[:size]))


def _read_lighting_state(gamemem):
    return {
        "light": (
            int(gamemem.read_reloc_i32(ADDR_LIGHT_X)),
            int(gamemem.read_reloc_i32(ADDR_LIGHT_Y)),
            int(gamemem.read_reloc_i32(ADDR_LIGHT_Z)),
            int(gamemem.read_reloc_i32(ADDR_LIGHT_DIR_FLAG) != 0),
        ),
        "fog": (
            int(gamemem.read_reloc_i32(ADDR_FOG_MIN)),
            int(gamemem.read_reloc_i32(ADDR_FOG_DISTANCE)),
        ),
    }


def _compare_texture_state(previous, current):
    result = {
        "palette_changed": previous["palette_crc"] != current["palette_crc"],
        "remap_changed": previous["remap"] != current["remap"],
        "light_changed": previous["lighting"]["light"] != current["lighting"]["light"],
        "fog_changed": previous["lighting"]["fog"] != current["lighting"]["fog"],
        "membership": [],
        "display_animated": [],
        "display_nontimed": [],
        "tick_only": [],
        "residency_only": [],
    }
    previous_descs = previous["descriptors"]
    current_descs = current["descriptors"]
    for desc_idx in sorted(set(previous_descs) | set(current_descs)):
        old = previous_descs.get(desc_idx)
        new = current_descs.get(desc_idx)
        if old is None or new is None:
            result["membership"].append(
                (desc_idx, ("appeared" if old is None else "disappeared",), old, new)
            )
            continue

        display_fields = tuple(
            field
            for field in (
                "control",
                "selected",
                "cell_identity_crc",
                "pixel",
                "resolved",
                "family",
            )
            if old[field] != new[field]
        )
        tick_changed = old["tick"] != new["tick"]
        residency_changed = (
            old["cell_table_crc"] != new["cell_table_crc"]
            or old["selected_residency"] != new["selected_residency"]
        )
        if display_fields:
            fields = display_fields
            if tick_changed:
                fields += ("tick",)
            if residency_changed:
                fields += ("cell_table_crc",)
            reference = new if new is not None else old
            key = (
                "display_animated"
                if _is_timed_family(reference)
                else "display_nontimed"
            )
            result[key].append((desc_idx, fields, old, new))
        elif tick_changed:
            fields = ("tick",)
            if residency_changed:
                fields += ("cell_table_crc",)
            result["tick_only"].append((desc_idx, fields, old, new))
        elif residency_changed:
            result["residency_only"].append(
                (desc_idx, ("cell_table_crc",), old, new)
            )
    return result


def _format_baseline(snapshot, mission_generation, interval_frames):
    descriptors = snapshot["descriptors"]
    return (
        "texture_state_audit kind=baseline "
        f"mission_generation={mission_generation} "
        f"interval_frames={interval_frames} "
        f"palette={snapshot['palette_crc']} "
        f"remap={_format_remap(snapshot['remap'])} "
        f"light={snapshot['lighting']['light']} "
        f"fog={snapshot['lighting']['fog']} "
        f"scene_descs={len(descriptors)} "
        f"resolved={sum(int(value['resolved']) for value in descriptors.values())} "
        f"timed={sum(int(value['family'] == 'timed') for value in descriptors.values())} "
        f"selector_driven="
        f"{sum(int(value['family'] == 'selector_driven') for value in descriptors.values())} "
        f"nontimed_candidates="
        f"{sum(int(not _is_timed_family(value)) for value in descriptors.values())} "
        f"billboard_descs_excluded={snapshot['billboard_descs_excluded']}"
    )


def _format_sample(snapshot, state, changes):
    descriptors = snapshot["descriptors"]
    return (
        "texture_state_audit kind=sample "
        f"sample={state['sample_count']} "
        f"palette_changed={int(changes['palette_changed'])} "
        f"remap_changed={int(changes['remap_changed'])} "
        f"light_changed={int(changes['light_changed'])} "
        f"fog_changed={int(changes['fog_changed'])} "
        f"palette={snapshot['palette_crc']} "
        f"remap={_format_remap(snapshot['remap'])} "
        f"light={snapshot['lighting']['light']} "
        f"fog={snapshot['lighting']['fog']} "
        f"membership_changes={len(changes['membership'])} "
        f"display_animated_changes={len(changes['display_animated'])} "
        f"display_nontimed_changes={len(changes['display_nontimed'])} "
        f"tick_only_changes={len(changes['tick_only'])} "
        f"residency_only_changes={len(changes['residency_only'])} "
        f"scene_descs={len(descriptors)} "
        f"resolved={sum(int(value['resolved']) for value in descriptors.values())} "
        f"timed={sum(int(value['family'] == 'timed') for value in descriptors.values())} "
        f"selector_driven="
        f"{sum(int(value['family'] == 'selector_driven') for value in descriptors.values())} "
        f"nontimed_candidates="
        f"{sum(int(not _is_timed_family(value)) for value in descriptors.values())} "
        f"billboard_descs_excluded={snapshot['billboard_descs_excluded']} "
        f"cumulative_membership={state['cumulative_membership']} "
        f"cumulative_display_animated={state['cumulative_display_animated']} "
        f"cumulative_display_nontimed={state['cumulative_display_nontimed']} "
        f"cumulative_tick_only={state['cumulative_tick_only']} "
        f"cumulative_residency_only={state['cumulative_residency_only']} "
        f"cumulative_palette={state['cumulative_palette']} "
        f"cumulative_remap={state['cumulative_remap']} "
        f"cumulative_light={state['cumulative_light']} "
        f"cumulative_fog={state['cumulative_fog']} "
        f"ever_display_animated_descs="
        f"{len(state['ever_display_animated_changed'])} "
        f"ever_display_nontimed_descs="
        f"{len(state['ever_display_nontimed_changed'])} "
        f"ever_tick_only_descs={len(state['ever_tick_only_changed'])} "
        f"ever_residency_only_descs={len(state['ever_residency_only_changed'])}"
    )


def _format_change_event(kind, event):
    desc_idx, fields, old, new = event
    reference = new if new is not None else old
    family = "missing" if reference is None else reference["family"]
    return (
        "texture_state_change "
        f"desc=0x{int(desc_idx):03X} "
        "scope=scene_texmap "
        f"family={family} "
        f"kind={kind} "
        f"fields={','.join(fields)} "
        f"old={_format_descriptor_state(old)} "
        f"new={_format_descriptor_state(new)}"
    )


def _is_timed_family(state):
    if state is None:
        return False
    return state.get("family") == "timed"


def _format_descriptor_state(state):
    if state is None:
        return "missing"
    return (
        "{"
        f"family={state['family']};"
        f"control={state['control']};"
        f"tick={state['tick']};"
        f"selected={state['selected']};"
        f"selected_residency={state['selected_residency']};"
        f"cell_identity={state['cell_identity_crc']};"
        f"cell_table={state['cell_table_crc']};"
        f"pixel={state['pixel']};"
        f"resolved={int(state['resolved'])}"
        "}"
    )


def _format_remap(remap):
    return f"({int(remap[0])},{remap[1]})"


def _crc32(data):
    return f"{zlib.crc32(bytes(data)) & 0xFFFFFFFF:08X}"


def _u16(data, offset):
    return struct.unpack_from("<H", data, offset)[0]


def _i16(data, offset):
    return struct.unpack_from("<h", data, offset)[0]


def _u32(data, offset):
    return struct.unpack_from("<I", data, offset)[0]
