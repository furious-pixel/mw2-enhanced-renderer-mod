from __future__ import annotations

import struct
from dataclasses import dataclass

from .hud_sprites import (
    HudSprite as MenuSprite,
    decode_hud_sprite as _decode_menu_sprite,
    preloaded_hud_sprite as _preloaded_menu_sprite,
    resolve_cached_shape as _resolve_cached_shape,
)


ADDR_HANDLER_LIST = 0x000E4104
ADDR_GAME_TICK = 0x000A58C4
ADDR_OBJECTIVES_VISIBLE = 0x000A6984
ADDR_OBJECTIVE_BLOCK_INDEX = 0x000A6988
ADDR_OBJECTIVE_BLOCKS = 0x000B5810
ADDR_PLAYER_SLOT = 0x000A5918
ADDR_MASTER_HUD_ENABLE = 0x000A6314
ADDR_HUD_STYLE_OFFSET = 0x000B4E80
ADDR_ENTITY_BODY_TABLE = 0x00108B00
ADDR_PANEL_TABLE = 0x0010E23C

CALLBACK_OBJECTIVES_STATUS = 0x00048FD0
CALLBACK_FORMATTED_LIST = 0x0002FD00
CALLBACK_ENUM_LIST = 0x0002FF40
CALLBACK_SLIDER = 0x0002F920
OBJECTIVE_BLOCK_STRIDE = 0x2E8A
OBJECTIVE_RECORD_OFFSET = 0x3A
OBJECTIVE_RECORD_STRIDE = 0xF7
OBJECTIVE_CLASS_ORDER = (1, 2, 4, 0, 8)
TICKS_PER_SECOND = 182.0

MAX_HANDLERS = 64
MAX_MENU_ITEMS = 128
MAX_STRING_BYTES = 256
MENU_ITEM_STRIDE = 0x11
MENU_HANDLER_ESC = 4
MENU_HANDLER_USER_SYSTEMS = 5
MENU_HANDLER_COMMAND = 6

@dataclass(frozen=True, slots=True)
class MenuSlider:
    value: int
    left_sprite: MenuSprite | None
    track_sprite: MenuSprite | None
    right_sprite: MenuSprite | None
    thumb_sprite: MenuSprite | None


@dataclass(frozen=True, slots=True)
class MenuHudItem:
    text: str
    prefix: str
    item_type: int
    callback_reloc: int
    callback_parameter: int
    value_text: str
    slider: MenuSlider | None
    selected: bool
    prefix_x: int
    prefix_y: int
    text_x: int
    text_y: int
    color_index: int


@dataclass(frozen=True, slots=True)
class MenuHudPage:
    handler_id: int
    page_address: int
    left: int
    top: int
    right: int
    bottom: int
    clear_background: bool
    draw_border: bool
    background_sprite: MenuSprite | None
    background_left: int
    background_top: int
    background_right: int
    background_bottom: int
    title: str
    title_x: int
    title_y: int
    normal_color_index: int
    marker_x: int
    marker_y: int
    marker_color_index: int
    show_marker: bool
    marker_sprite: MenuSprite | None
    items: tuple[MenuHudItem, ...]


@dataclass(frozen=True, slots=True)
class ObjectiveHudRow:
    label: str
    text: str
    status: str
    status_color_index: int
    y: int
    continuation: str


@dataclass(frozen=True, slots=True)
class ObjectivesHud:
    rows: tuple[ObjectiveHudRow, ...]
    footer: str


def _string(gamemem, runtime_address):
    if runtime_address == 0:
        return ""
    raw = gamemem.read_runtime_bytes(runtime_address, MAX_STRING_BYTES)
    return bytes(raw).split(b"\x00", 1)[0].decode("cp437", errors="replace")


def _code_reloc(gamemem, pointer):
    return int(pointer) - int(gamemem.delta) if pointer else 0


def _runtime_i16(gamemem, address):
    value = int(gamemem.read_runtime_u16(address))
    return value - 0x10000 if value & 0x8000 else value


def _point_entity(gamemem, context):
    active_index = int(gamemem.read_reloc_i32(0x000A5918))
    active_entity = int(
        gamemem.read_reloc_u32(0x00108B00 + active_index * 4)
    )
    if active_entity == 0:
        return None
    active_data = bytes(gamemem.read_runtime_bytes(active_entity + 0x08, 0x08))
    active_group = struct.unpack_from("<i", active_data, 0x00)[0]
    entity_count = int(gamemem.read_reloc_i32(0x000A6270))
    if entity_count < 0 or entity_count > 4096:
        return None
    if entity_count == 0:
        return None
    entity_table = bytes(
        gamemem.read_reloc_bytes(0x00108B00, entity_count * 4)
    )
    for entity_index, entity in enumerate(
        struct.unpack(f"<{entity_count}I", entity_table)
    ):
        if entity == 0:
            continue
        entity_data = bytes(gamemem.read_runtime_bytes(entity + 0x08, 0x08))
        group, entity_context = struct.unpack_from("<2i", entity_data)
        if group != active_group:
            continue
        if entity_context == context:
            return entity_index, entity
    return None


def _known_getter_value(gamemem, getter_reloc, context, cached_value):
    if getter_reloc == 0x00030CF0:
        if context == 0x9D:
            return 0
        if context == 0x9E:
            return int(gamemem.read_reloc_u8(0x000B56A9))
        if context == 0x9F:
            return int(gamemem.read_reloc_i32(0x000A7120) == 1)
        if context == 0x13:
            return int(gamemem.read_reloc_i32(0x000A6314))
        if context == 0x40:
            return int(gamemem.read_reloc_i32(0x000A8390))
        if context == 0x3C:
            return int(gamemem.read_reloc_i32(0x000A6278))
        return 0
    if getter_reloc == 0x00030A70:
        if context < 5:
            return int(gamemem.read_reloc_i32(0x000A5A4C + context * 4))
        return 7
    if getter_reloc == 0x00030A90:
        team = int(gamemem.read_reloc_i32(0x000A633C))
        if 0 <= team < 0x10:
            return int(gamemem.read_reloc_i32(0x0010B632 + team * 0x26))
        return 0
    if getter_reloc == 0x00030AD0:
        resolved = _point_entity(gamemem, context)
        return 0 if resolved is None else _runtime_i16(gamemem, resolved[1] + 0x146) + 1
    if getter_reloc == 0x000307A0:
        brightness = int(gamemem.read_reloc_i32(0x000A5828))
        return max(0, min(0x10000, brightness * (0x10000 // 15)))
    if getter_reloc == 0x0005B000:
        address = {0: 0x000A7ED0, 1: 0x000A7ED4, 2: 0x000A7ED8}.get(context)
        return cached_value if address is None else int(gamemem.read_reloc_i32(address))
    if getter_reloc == 0x00030E90:
        flags = int(gamemem.read_reloc_i32(0x000A713C))
        return int((flags & 0x100) == 0 or (flags & 0x200) == 0)
    if getter_reloc == 0x00030EF0:
        flags = int(gamemem.read_reloc_i32(0x000A713C))
        return int((flags & 0x800) == 0 or gamemem.read_reloc_i32(0x000A5BA0) != 0)
    if getter_reloc == 0x00030F50:
        return int(
            gamemem.read_reloc_i32(0x000A7190) == 1
            or gamemem.read_reloc_i32(0x000A7138) == 0
        )
    if getter_reloc == 0x00030FB0:
        config = int(gamemem.read_reloc_u32(0x000A7F08))
        return cached_value if config == 0 else int(gamemem.read_runtime_i32(config + 0x20))
    if getter_reloc == 0x0004E5B0:
        return int(gamemem.read_reloc_u8(0x000A6DAC))
    return cached_value


def _point_status_suffix(gamemem, context):
    resolved = _point_entity(gamemem, context)
    if resolved is None:
        return ""
    _entity_index, entity = resolved
    status_data = bytes(gamemem.read_runtime_bytes(entity + 0x146, 0x06))
    status, tag = struct.unpack_from("<hxxH", status_data)
    if status == 5:
        return ""
    object_class = tag & 0x0F00
    object_id = tag & 0x00FF
    if object_class == 0x0100:
        return _reloc_string(gamemem, 0x00108BF0 + object_id * 0x54 + 0x28, 64)
    if object_class == 0x0200:
        target = int(gamemem.read_reloc_u32(0x00108B00 + object_id * 4))
        return "" if target == 0 else _string(gamemem, target + 0xE8)
    if object_class == 0x0400:
        return _reloc_string(gamemem, 0x00104B80 + object_id * 0x40 + 0x14, 44)
    return ""


def _decode_slider(
    gamemem,
    style,
    getter_reloc,
    context,
    cached_value,
    use_getter,
):
    if getter_reloc == 0x000307A0:
        live_brightness = int(gamemem.read_reloc_i32(0x000A582C))
        value = max(0, min(15, live_brightness)) * (0x10000 // 15)
    else:
        value = (
            _known_getter_value(gamemem, getter_reloc, context, cached_value)
            if use_getter
            else cached_value
        )
    value = max(0, min(0x10000, int(value)))
    sprites = []
    if style != 0:
        style_offset = int(gamemem.read_reloc_u8(ADDR_HUD_STYLE_OFFSET))
        style_data = bytes(gamemem.read_runtime_bytes(style, 0x1C))
        for resource_base_offset in (0x00, 0x08, 0x10, 0x18):
            resource_base = struct.unpack_from(
                "<i", style_data, resource_base_offset
            )[0]
            resource_index = resource_base + style_offset
            payload = _resolve_cached_shape(gamemem, resource_index)
            sprites.append(
                _decode_menu_sprite(gamemem, payload)
                if payload
                else _preloaded_menu_sprite(resource_index)
            )
    while len(sprites) < 4:
        sprites.append(None)
    return MenuSlider(value, sprites[0], sprites[1], sprites[2], sprites[3])


def _decode_callback_value(gamemem, callback_reloc, parameter):
    if parameter == 0 or callback_reloc not in (
        CALLBACK_FORMATTED_LIST,
        CALLBACK_ENUM_LIST,
        CALLBACK_SLIDER,
    ):
        return "", None
    try:
        parameter_data = bytes(gamemem.read_runtime_bytes(parameter, 0x18))
        flags = parameter_data[0]
        cached_value = struct.unpack_from("<i", parameter_data, 0x04)[0]
        table = struct.unpack_from("<I", parameter_data, 0x08)[0]
        context = struct.unpack_from("<I", parameter_data, 0x0C)[0]
        getter = struct.unpack_from("<I", parameter_data, 0x14)[0]
        getter_reloc = _code_reloc(gamemem, getter)
        use_getter = bool(flags & 0x01) and getter != 0
        if callback_reloc == CALLBACK_SLIDER:
            return "", _decode_slider(
                gamemem,
                table,
                getter_reloc,
                context,
                cached_value,
                use_getter,
            )
        if table == 0:
            return "", None
        table_header = bytes(gamemem.read_runtime_bytes(table, 0x08))
        formatter, choice_count = struct.unpack_from("<Ii", table_header)
        if choice_count <= 0 or choice_count > 256:
            return "", None
        value = (
            _known_getter_value(
                gamemem,
                getter_reloc,
                context,
                cached_value,
            )
            if use_getter
            else cached_value
        ) % choice_count
        choice_pointer = int(
            gamemem.read_runtime_u32(table + 0x08 + value * 4)
        )
        value_text = _string(gamemem, choice_pointer)
        if callback_reloc == CALLBACK_FORMATTED_LIST:
            if _code_reloc(gamemem, formatter) == 0x00030C60:
                value_text += _point_status_suffix(gamemem, context)
        return value_text, None
    except Exception:
        return "", None


def _snapshot_page(
    gamemem,
    handler_data,
    handler_id,
    allow_initial_page=False,
):
    handler_bytes = bytes(gamemem.read_runtime_bytes(handler_data, 0x69))
    pane = struct.unpack_from("<I", handler_bytes, 0x00)[0]
    draw_window = struct.unpack_from("<I", handler_bytes, 0x15)[0]
    stack = struct.unpack_from("<I", handler_bytes, 0x05)[0]
    depth = struct.unpack_from("<i", handler_bytes, 0x09)[0]
    if pane == 0:
        return None

    if stack != 0 and depth > 0:
        page = int(gamemem.read_runtime_u32(stack + (depth - 1) * 4))
    elif allow_initial_page:
        page = struct.unpack_from("<I", handler_bytes, 0x65)[0]
    else:
        return None
    if page == 0:
        return None

    pane_bytes = bytes(gamemem.read_runtime_bytes(pane + 0x04, 0x10))
    left, top, right, bottom = struct.unpack_from("<4i", pane_bytes)
    if draw_window != 0:
        draw_window_bytes = bytes(
            gamemem.read_runtime_bytes(draw_window + 0x04, 0x10)
        )
        background_left, background_top, background_right, background_bottom = (
            struct.unpack_from("<4i", draw_window_bytes)
        )
    else:
        background_left = left
        background_top = top
        background_right = right
        background_bottom = bottom
    flags = handler_bytes[0x04]
    background_base = struct.unpack_from("<i", handler_bytes, 0x0D)[0]
    background_shape = struct.unpack_from("<I", handler_bytes, 0x11)[0]
    marker_base = struct.unpack_from("<i", handler_bytes, 0x19)[0]
    marker_shape = struct.unpack_from("<I", handler_bytes, 0x1D)[0]
    normal_color = handler_bytes[0x31]
    selected_color = handler_bytes[0x35]
    visual_slot_count = struct.unpack_from("<i", handler_bytes, 0x39)[0]
    line_spacing = struct.unpack_from("<i", handler_bytes, 0x41)[0]
    title_x, title_y = struct.unpack_from("<2i", handler_bytes, 0x45)
    marker_x, marker_y = struct.unpack_from("<2i", handler_bytes, 0x4D)
    prefix_x, prefix_y = struct.unpack_from("<2i", handler_bytes, 0x55)
    text_x, text_y = struct.unpack_from("<2i", handler_bytes, 0x5D)

    page_header = bytes(gamemem.read_runtime_bytes(page, 0x15))
    title_pointer = struct.unpack_from("<I", page_header, 0x01)[0]
    item_count = struct.unpack_from("<i", page_header, 0x09)[0]
    selected_index = struct.unpack_from("<i", page_header, 0x0D)[0]
    if item_count < 0 or item_count > MAX_MENU_ITEMS:
        return None

    item_bytes = (
        bytes(
            gamemem.read_runtime_bytes(
                page + 0x15,
                item_count * MENU_ITEM_STRIDE,
            )
        )
        if item_count
        else b""
    )
    item_types = [
        item_bytes[item_index * MENU_ITEM_STRIDE]
        for item_index in range(item_count)
    ]
    last_back_index = -1
    for item_index, item_type in enumerate(item_types):
        if item_type in (2, 6):
            last_back_index = item_index
    selected_visual_index = selected_index
    if (
        not (flags & 0x08)
        and selected_index == last_back_index
        and visual_slot_count > 0
    ):
        selected_visual_index = visual_slot_count - 1

    items = []
    display_number = 1
    delta = int(gamemem.delta)
    for item_index in range(item_count):
        item_offset = item_index * MENU_ITEM_STRIDE
        item_type = item_types[item_index]
        visual_index = item_index
        if (
            not (flags & 0x08)
            and item_index == last_back_index
            and visual_slot_count > 0
        ):
            visual_index = visual_slot_count - 1
        text_pointer = struct.unpack_from(
            "<I", item_bytes, item_offset + 0x01
        )[0]
        text = _string(gamemem, text_pointer)
        if (
            handler_id == MENU_HANDLER_USER_SYSTEMS
            and text == "Image Emhancement"
        ):
            text = "Image Enhancement"
        callback = struct.unpack_from(
            "<I", item_bytes, item_offset + 0x05
        )[0]
        callback_parameter = struct.unpack_from(
            "<I", item_bytes, item_offset + 0x09
        )[0]
        callback_reloc = callback - delta if callback != 0 else 0
        value_text, slider = _decode_callback_value(
            gamemem,
            callback_reloc,
            callback_parameter,
        )
        selected = item_index == selected_index
        if item_type in (2, 6):
            prefix = "0"
        elif item_type == 3:
            prefix = ""
        else:
            prefix = str(display_number % 10)
            display_number += 1
        items.append(
            MenuHudItem(
                text=text,
                prefix=prefix,
                item_type=item_type,
                callback_reloc=callback_reloc,
                callback_parameter=callback_parameter,
                value_text=value_text,
                slider=slider,
                selected=selected,
                prefix_x=left + prefix_x,
                prefix_y=top + prefix_y + visual_index * line_spacing,
                text_x=left + text_x,
                text_y=top + text_y + visual_index * line_spacing,
                color_index=selected_color if selected else normal_color,
            )
        )

    has_background = background_base != -1 and background_shape != 0
    has_marker = marker_base != -1 and marker_shape != 0
    background_sprite = (
        _decode_menu_sprite(gamemem, background_shape) if has_background else None
    )
    marker_sprite = _decode_menu_sprite(gamemem, marker_shape) if has_marker else None
    return MenuHudPage(
        handler_id=int(handler_id),
        page_address=page,
        left=left,
        top=top,
        right=right,
        bottom=bottom,
        clear_background=bool(flags & 0x20),
        draw_border=bool(flags & 0x04),
        background_sprite=background_sprite,
        background_left=background_left,
        background_top=background_top,
        background_right=background_right,
        background_bottom=background_bottom,
        title=_string(gamemem, title_pointer),
        title_x=left + title_x,
        title_y=top + title_y,
        normal_color_index=normal_color,
        marker_x=left + marker_x,
        marker_y=top + marker_y + selected_visual_index * line_spacing,
        marker_color_index=selected_color,
        show_marker=has_marker and not bool(flags & 0x08),
        marker_sprite=marker_sprite,
        items=tuple(items),
    )


def _reloc_string(gamemem, address, max_bytes):
    raw = gamemem.read_reloc_bytes(address, max_bytes)
    return bytes(raw).split(b"\x00", 1)[0].decode("cp437", errors="replace")


def _format_ticks(ticks, hundredths=False):
    seconds = max(0.0, float(ticks) / TICKS_PER_SECOND)
    hours = int(seconds // 3600.0)
    minutes = int(seconds // 60.0) % 60
    whole_seconds = int(seconds) % 60
    if hundredths:
        fraction = int((seconds - int(seconds)) * 100.0)
        return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}.{fraction:02d}"
    return f"{hours:02d}:{minutes:02d}:{whole_seconds:02d}"


def _objectives_panel_enabled(gamemem):
    delta = int(gamemem.delta)
    panel_table = bytes(gamemem.read_reloc_bytes(ADDR_PANEL_TABLE, 25 * 4))
    for panel in struct.unpack("<25I", panel_table):
        if panel == 0:
            continue
        panel_data = bytes(gamemem.read_runtime_bytes(panel, 0x84))
        if struct.unpack_from("<H", panel_data, 0x04)[0] == 0:
            continue
        for callback_offset in (0x78, 0x7C, 0x80):
            callback = struct.unpack_from("<I", panel_data, callback_offset)[0]
            if callback != 0 and callback - delta == CALLBACK_OBJECTIVES_STATUS:
                return True
    return False

def _steady_cockpit_hud_available(gamemem):
    player_slot = int(gamemem.read_reloc_u32(ADDR_PLAYER_SLOT))
    player_entity = int(
        gamemem.read_reloc_u32(ADDR_ENTITY_BODY_TABLE + player_slot * 4)
    )
    if player_entity == 0:
        return False
    player_data = bytes(gamemem.read_runtime_bytes(player_entity + 0x20, 0x1C))
    mech = struct.unpack_from("<I", player_data, 0x00)[0]
    if struct.unpack_from("<I", player_data, 0x18)[0] == 0:
        return False

    if mech == 0:
        return False
    mech_data = bytes(gamemem.read_runtime_bytes(mech, 0xA4))
    mech_body = struct.unpack_from("<I", mech_data, 0x00)[0]
    if mech_body == 0:
        return False
    mech_body_data = bytes(gamemem.read_runtime_bytes(mech_body, 0x08))
    if player_slot != struct.unpack_from("<I", mech_body_data, 0x04)[0]:
        return False
    if gamemem.read_reloc_u32(ADDR_MASTER_HUD_ENABLE) == 0:
        return False
    return struct.unpack_from("<i", mech_data, 0xA0)[0] == 2


def snapshot_objectives_hud(gamemem):
    if gamemem.read_reloc_i32(ADDR_OBJECTIVES_VISIBLE) == 0:
        return None
    if not _steady_cockpit_hud_available(gamemem):
        return None
    if not _objectives_panel_enabled(gamemem):
        return None

    block_index = int(gamemem.read_reloc_i32(ADDR_OBJECTIVE_BLOCK_INDEX))
    objective_block = ADDR_OBJECTIVE_BLOCKS + block_index * OBJECTIVE_BLOCK_STRIDE
    objective_header = bytes(gamemem.read_reloc_bytes(objective_block, 0x3A))
    record_count = struct.unpack_from("<i", objective_header, 0x00)[0]
    if record_count < 0 or record_count > 128:
        return None
    objective_records = (
        bytes(
            gamemem.read_reloc_bytes(
                objective_block + OBJECTIVE_RECORD_OFFSET,
                record_count * OBJECTIVE_RECORD_STRIDE,
            )
        )
        if record_count
        else b""
    )

    rows = []
    y = 291
    records_by_class = {
        objective_class: [] for objective_class in OBJECTIVE_CLASS_ORDER
    }
    for record_index in range(record_count):
        record_offset = record_index * OBJECTIVE_RECORD_STRIDE
        record = objective_records[
            record_offset : record_offset + OBJECTIVE_RECORD_STRIDE
        ]
        if record[0x5C] != 0 and record[0x01] in records_by_class:
            records_by_class[record[0x01]].append(record)

    for objective_class in OBJECTIVE_CLASS_ORDER:
        for record in records_by_class[objective_class]:
            state = record[0x00]
            if state == 5:
                status = "Successful"
                status_color = 0x07
            elif state == 6:
                status = "Failed"
                status_color = 0x0B
            else:
                status = "In progress"
                status_color = 0x0E
            label = (
                "Primary:   "
                if objective_class == 1
                else "Secondary: "
                if objective_class == 2
                else "Tertiary: "
            )
            primary = record[0x95:0xB5]
            terminator = primary.find(b"\x00")
            if terminator >= 0:
                text = primary[:terminator].decode("cp437", errors="replace")
                continuation = ""
            else:
                text = primary.decode("cp437", errors="replace")
                continuation = record[0xB5:0xF7].split(b"\x00", 1)[0].decode(
                    "cp437", errors="replace"
                )
            rows.append(
                ObjectiveHudRow(
                    label=label,
                    text=text,
                    status=status,
                    status_color_index=status_color,
                    y=y,
                    continuation=continuation,
                )
            )
            y += 14
            if continuation:
                y += 14

    selector = objective_header[0x38]
    current_tick = int(gamemem.read_reloc_i32(ADDR_GAME_TICK))
    start_tick_units = struct.unpack_from("<i", objective_header, 0x04)[0]
    duration_units = struct.unpack_from("<i", objective_header, 0x0C)[0]
    elapsed_ticks = current_tick - start_tick_units * 0xB6
    if selector == 0:
        if duration_units > 0:
            remaining_ticks = (duration_units + start_tick_units) * 0xB6 - current_tick
            footer = "Time Remaining: " + _format_ticks(remaining_ticks, True)
        else:
            footer = "Elapsed Time: " + _format_ticks(elapsed_ticks)
    elif selector == 2:
        footer = "Successful at " + _format_ticks(elapsed_ticks)
    elif selector == 3:
        footer = "Out of time at " + _format_ticks(elapsed_ticks)
    elif selector == 4:
        footer = "Failed at " + _format_ticks(elapsed_ticks)
    else:
        footer = ""
    return ObjectivesHud(rows=tuple(rows), footer=footer)


def _iter_menu_handlers(gamemem):
    handler = int(gamemem.read_reloc_u32(ADDR_HANDLER_LIST))
    visited = set()
    while handler != 0 and len(visited) < MAX_HANDLERS:
        if handler in visited:
            break
        visited.add(handler)
        handler_bytes = bytes(gamemem.read_runtime_bytes(handler, 0x12))
        handler_id = struct.unpack_from("<I", handler_bytes, 0x00)[0]
        current_state = handler_bytes[0x04]
        target_state = handler_bytes[0x05]
        handler_data = struct.unpack_from("<I", handler_bytes, 0x06)[0]
        next_handler = struct.unpack_from("<I", handler_bytes, 0x0E)[0]
        yield handler_id, current_state, target_state, handler_data
        handler = next_handler


def _snapshot_menu_states(gamemem, include_current, include_pending):
    current_pages = []
    pending_pages = []
    active_handler_ids = set()
    for handler_id, current_state, target_state, handler_data in _iter_menu_handlers(
        gamemem
    ):
        if current_state != 0 or target_state != 0:
            active_handler_ids.add(handler_id)
        elif handler_data != 0:
            handler_bytes = bytes(
                gamemem.read_runtime_bytes(handler_data + 0x05, 0x08)
            )
            stack, depth = struct.unpack_from("<Ii", handler_bytes)
            if stack != 0 and depth > 0:
                active_handler_ids.add(handler_id)

        if include_current and current_state == 1 and handler_data != 0:
            page = _snapshot_page(gamemem, handler_data, handler_id)
            if page is not None:
                current_pages.append(page)
        if (
            include_pending
            and current_state == 0
            and target_state == 1
            and handler_data != 0
        ):
            page = _snapshot_page(
                gamemem,
                handler_data,
                handler_id,
                allow_initial_page=True,
            )
            if page is not None:
                pending_pages.append(page)
    return current_pages, pending_pages, frozenset(active_handler_ids)


def snapshot_menu_hud_state(gamemem, include_pending=True):
    return _snapshot_menu_states(gamemem, True, bool(include_pending))
