from __future__ import annotations

import math
import struct
from dataclasses import dataclass

from .hud_atlas import TARGET_CARET_BY_DIRECTION
from .hud_sprites import (
    HudSprite,
    resource_hud_sprite,
)
from .projection import perspective_projection_info


ADDR_RADAR_CONFIG_TABLE = 0x000B4CF0
ADDR_RADAR_MODE_STATE = 0x000B4D04
ADDR_ACTIVE_CAMERA = 0x000A70E8
ADDR_COCKPIT_ZOOM = 0x000A6FA0
ADDR_ENTITY_COUNT = 0x000A6270
ADDR_SECONDARY_COUNT = 0x000A6274
ADDR_SECONDARY_MAX_INDEX = 0x000A6D50
ADDR_ENTITY_TABLE = 0x00108B00
ADDR_DIRECT_TARGETS = 0x00108BF0
ADDR_DIRECT_TARGET_COUNT = 0x000A6330
ADDR_DIRECT_TARGET_GROUP = 0x000A633C
ADDR_PRIMARY_CLASSIFICATION = 0x0010B631
ADDR_SECONDARY_TABLE = 0x00104B80
ADDR_SECONDARY_POSITION_TABLE = 0x00111FA0
ADDR_HUD_STYLE_OFFSET = 0x000B4E80
ADDR_SECONDARY_CLASSIFICATION = 0x0010C6C4

RADAR_MODE_OFF = 0
RADAR_MODES = (1, 2, 4)
ENTITY_LIMIT = 4096
SECONDARY_LIMIT = 4096
DIRECT_TARGET_LIMIT = 4096
PRIMARY_CLASSIFICATION_SLOTS = 16
FP29_SCALE = 1 << 29
ANGLE_FULL_TURN = 0x01680000
RADAR_REFERENCE_WIDTH = 1024.0

@dataclass(frozen=True, slots=True)
class RadarLine:
    color_index: int
    x0: float
    y0: float
    x1: float
    y1: float


@dataclass(frozen=True, slots=True)
class RadarEllipse:
    color_index: int
    center_x: float
    center_y: float
    radius_x: float
    radius_y: float


@dataclass(frozen=True, slots=True)
class RadarBlipRect:
    color_index: int
    left: float
    top: float
    right: float
    bottom: float


@dataclass(frozen=True, slots=True)
class RadarSprite:
    sprite: HudSprite
    x: float
    y: float
    clip_rect: tuple[int, int, int, int]
    output_space: bool = False


@dataclass(frozen=True, slots=True)
class RadarRangeText:
    text: str
    color_index: int
    x: int
    y: int


@dataclass(frozen=True, slots=True)
class RadarTargetMarker:
    shape_index: int
    x: float
    y: float
    clip_rect: tuple[int, int, int, int]
    color_index: int


@dataclass(frozen=True, slots=True)
class RadarCross:
    x: float
    y: float
    color_index: int


@dataclass(frozen=True, slots=True)
class RadarFov:
    color_index: int
    heading_radians: float
    half_angle_radians: float


@dataclass(frozen=True, slots=True)
class RadarHudSnapshot:
    mode: int
    lines: tuple[RadarLine, ...]
    ellipse: RadarEllipse | None
    sprites: tuple[RadarSprite, ...]
    blips: tuple[RadarBlipRect, ...]
    target_sprites: tuple[RadarSprite, ...]
    range_text: RadarRangeText | None
    bearing_text: RadarRangeText | None
    reference_bounds: tuple[int, int, int, int]
    animation_extent: tuple[float, float]
    fov: RadarFov | None = None
    target_markers: tuple[RadarTargetMarker, ...] = ()
    target_cross: RadarCross | None = None


EMPTY_RADAR_HUD = RadarHudSnapshot(
    0, (), None, (), (), (), None, None, (0, 0, 0, 0), (1.0, 1.0)
)


def _idiv(numerator, denominator):
    if denominator == 0:
        return 0
    quotient = abs(int(numerator)) // abs(int(denominator))
    return -quotient if (numerator < 0) != (denominator < 0) else quotient


def _ascii_string(gamemem, address, maximum=64):
    if address == 0:
        return ""
    raw = bytes(gamemem.read_runtime_bytes(address, maximum))
    return raw.split(b"\x00", 1)[0].decode("cp437", errors="replace")


def _blip_color(sub_index):
    return (0x0E, 0x0A, 0x0B)[max(0, min(2, int(sub_index)))]


def _append_rect(blips, color_index, left, top, right, bottom, bounds):
    bound_left, bound_top, bound_right, bound_bottom = bounds
    left = max(float(left), bound_left)
    top = max(float(top), bound_top)
    right = min(float(right), bound_right + 1)
    bottom = min(float(bottom), bound_bottom + 1)
    if right > left and bottom > top:
        blips.append(RadarBlipRect(color_index, left, top, right, bottom))


def _append_point(blips, x, y, color_index, bounds):
    _append_rect(
        blips,
        color_index,
        float(x) - 1.0,
        float(y) - 1.0,
        float(x) + 1.0,
        float(y) + 1.0,
        bounds,
    )


def _project_radar_raw(world_xyz, projection):
    world_x, world_y, world_z = (int(value) for value in world_xyz)
    dx = world_x - projection["cam_x"]
    dy = world_y - projection["cam_y"]
    dz = world_z - projection["cam_z"]
    rot_x = (dx * projection["mx"] + dz * projection["mz"]) >> 27
    rot_y = (dx * projection["nz"] + dz * projection["mx"]) >> 27
    rot_z = (dy * -0x20000000) >> 27
    divisor = projection["range_divisor"]
    if divisor == 0:
        return None

    pane_x = projection["center_x"] + 2.0 * rot_x / divisor
    pane_y_raw = projection["center_y"] + 2.0 * rot_y / divisor
    pane_y = projection["height"] - 1 - pane_y_raw
    return pane_x, pane_y, rot_z


def _inside_radar_ellipse(pane_x, pane_y, projection):
    radius_x = projection["radius_x"]
    ratio = projection["ratio"]
    if radius_x <= 0 or ratio == 0:
        return False
    x_relative = float(pane_x) - projection["center_x"]
    y_relative = float(pane_y) - projection["center_y"]
    y_circle = y_relative * 65536.0 / ratio
    return x_relative * x_relative + y_circle * y_circle <= radius_x * radius_x


def _project_radar(world_xyz, projection):
    projected = _project_radar_raw(world_xyz, projection)
    if projected is None:
        return None
    pane_x, pane_y, rot_z = projected
    if rot_z <= 0 or not _inside_radar_ellipse(pane_x, pane_y, projection):
        return None
    return projection["left"] + pane_x, projection["top"] + pane_y


def _project_target_radar(world_xyz, projection):
    projected = _project_radar_raw(world_xyz, projection)
    if projected is None:
        return None
    pane_x, pane_y, rot_z = projected
    if rot_z <= 0:
        return None
    if _inside_radar_ellipse(pane_x, pane_y, projection):
        return projection["left"] + pane_x, projection["top"] + pane_y
    edge_x, edge_y = _ray_to_ellipse_edge(
        projection["center_x"],
        projection["center_y"],
        pane_x - projection["center_x"],
        pane_y - projection["center_y"],
        projection["radius_x"],
        projection["radius_y"],
    )
    return projection["left"] + edge_x, projection["top"] + edge_y


def _project_target_radar_mode4(world_xyz, projection):
    projected = _project_radar_raw(world_xyz, projection)
    if projected is None:
        return None
    pane_x, pane_y, rot_z = projected
    if rot_z <= 0:
        return None
    if 0 <= pane_x < projection["width"] and 0 <= pane_y < projection["height"]:
        return (
            projection["left"] + pane_x,
            projection["top"] + pane_y,
            True,
            None,
        )
    direction_x = pane_x - projection["center_x"]
    direction_y = pane_y - projection["center_y"]
    edge_x, edge_y, edge = _ray_to_pane_edge_native(
        projection["center_x"],
        projection["center_y"],
        direction_x,
        direction_y,
        projection["width"],
        projection["height"],
    )
    edge_x = max(3, min(projection["width"] - 4, edge_x))
    edge_y = max(3, min(projection["height"] - 4, edge_y))
    return (
        projection["left"] + edge_x,
        projection["top"] + edge_y,
        False,
        edge,
    )


def _project_target_satellite_output(world_xyz, camera, viewport_size):
    if not camera or not camera.get("satellite_view", False):
        return None
    width = max(1, int(viewport_size[0]))
    height = max(1, int(viewport_size[1]))
    world = tuple(float(value) / 65536.0 for value in world_xyz)
    delta = tuple(
        world[axis] - camera["position"][axis]
        for axis in range(3)
    )
    view_x = sum(
        delta[axis] * camera["right"][axis]
        for axis in range(3)
    )
    view_y = sum(
        delta[axis] * camera["up"][axis]
        for axis in range(3)
    )
    half_width = max(1.0e-9, float(camera["orthographic_half_width"]))
    half_height = half_width * float(height) / float(width)
    screen_x = width * (0.5 + view_x / (2.0 * half_width))
    screen_y = height * (0.5 - view_y / (2.0 * half_height))
    if 0.0 <= screen_x < width and 0.0 <= screen_y < height:
        return screen_x, screen_y, True, None
    edge_x, edge_y, edge = _ray_to_pane_edge_native(
        width * 0.5,
        height * 0.5,
        screen_x - width * 0.5,
        screen_y - height * 0.5,
        width,
        height,
    )
    return (
        max(3, min(width - 4, edge_x)),
        max(3, min(height - 4, edge_y)),
        False,
        edge,
    )


def _ray_to_pane_edge_native(
    center_x,
    center_y,
    direction_x,
    direction_y,
    width,
    height,
):
    candidates = []
    if direction_x > 0.0:
        candidates.append(((width - center_x) / direction_x, "right"))
    elif direction_x < 0.0:
        candidates.append((-center_x / direction_x, "left"))
    if direction_y > 0.0:
        candidates.append(((height - center_y) / direction_y, "down"))
    elif direction_y < 0.0:
        candidates.append((-center_y / direction_y, "up"))
    positive = [candidate for candidate in candidates if candidate[0] >= 0.0]
    if not positive:
        return int(center_x), int(center_y), "up"
    scale, edge = min(positive, key=lambda candidate: candidate[0])
    return (
        int(center_x + direction_x * scale),
        int(center_y + direction_y * scale),
        edge,
    )


def effective_radar_fov_half_angle(
    native_half_angle,
    viewport_size,
    max_horizontal_fov_degrees,
):
    native_half_angle = max(
        0.0,
        min(math.pi * 0.5 - 1.0e-6, float(native_half_angle)),
    )
    native_focal_length = (
        RADAR_REFERENCE_WIDTH
        * 0.5
        / max(1.0e-6, math.tan(native_half_angle))
    )
    projection_info = perspective_projection_info(
        viewport_size[0],
        viewport_size[1],
        focal_length_pixels=native_focal_length,
        max_horizontal_fov_degrees=max_horizontal_fov_degrees,
    )
    return math.radians(projection_info.effective_horizontal_fov_degrees) * 0.5


def _ray_past_pane_edge(
    center_x,
    center_y,
    direction_x,
    direction_y,
    width,
    height,
    edge_overshoot,
):
    maximum_x = width - 1.0
    maximum_y = height - 1.0
    candidates = []
    if direction_x > 0.0:
        candidates.append(
            ((maximum_x - center_x) / direction_x, abs(direction_x))
        )
    elif direction_x < 0.0:
        candidates.append((-center_x / direction_x, abs(direction_x)))
    if direction_y > 0.0:
        candidates.append(
            ((maximum_y - center_y) / direction_y, abs(direction_y))
        )
    elif direction_y < 0.0:
        candidates.append((-center_y / direction_y, abs(direction_y)))
    positive_candidates = [
        candidate for candidate in candidates if candidate[0] >= 0.0
    ]
    if not positive_candidates:
        return center_x, center_y
    edge_scale, crossing_component = min(positive_candidates)
    outside_scale = max(0.0, float(edge_overshoot)) / max(
        1.0e-9,
        crossing_component,
    )
    scale = edge_scale + outside_scale
    return (
        center_x + direction_x * scale,
        center_y + direction_y * scale,
    )


def satellite_fov_lines(fov, viewport_size, edge_overshoot=1.0):
    width = max(1.0, float(viewport_size[0]))
    height = max(1.0, float(viewport_size[1]))
    effective_half_angle = max(
        0.0,
        min(math.pi * 0.5 - 1.0e-6, float(fov.half_angle_radians)),
    )
    center_x = width * 0.5
    center_y = height * 0.5
    lines = []
    for angle in (
        float(fov.heading_radians) - effective_half_angle,
        float(fov.heading_radians) + effective_half_angle,
    ):
        direction_x = math.cos(angle)
        direction_y = -math.sin(angle)
        end_x, end_y = _ray_past_pane_edge(
            center_x,
            center_y,
            direction_x,
            direction_y,
            width,
            height,
            edge_overshoot,
        )
        lines.append(RadarLine(
            int(fov.color_index),
            center_x,
            center_y,
            end_x,
            end_y,
        ))
    return tuple(lines)


def _ray_to_ellipse_edge(
    center_x,
    center_y,
    direction_x,
    direction_y,
    radius_x,
    radius_y,
):
    if radius_x <= 0 or radius_y <= 0:
        return center_x, center_y
    denominator = math.sqrt(
        (float(direction_x) / radius_x) ** 2
        + (float(direction_y) / radius_y) ** 2
    )
    if denominator <= 0.0:
        return center_x, center_y
    scale = 1.0 / denominator
    return center_x + direction_x * scale, center_y + direction_y * scale


def _primary_sub_index(classification_data, slot):
    slot = int(slot)
    if 0 <= slot < PRIMARY_CLASSIFICATION_SLOTS:
        return max(0, min(2, classification_data[slot * 0x26]))
    return 2


def _target_handle(target_data):
    return struct.unpack_from("<I", target_data, 0x14)[0]


def _read_window(gamemem, window):
    if window == 0:
        return None
    data = bytes(gamemem.read_runtime_bytes(window + 0x04, 0x10))
    left, top, right, bottom = struct.unpack("<4i", data)
    if right < left or bottom < top:
        return None
    return left, top, right, bottom


def _radar_player_sprite(gamemem, style_offset):
    return _radar_resource_sprite(gamemem, 0xAC + int(style_offset))


_radar_resource_sprite = resource_hud_sprite


def _radar_table_sprite(gamemem, shape_table, slot, sub_index, style_offset):
    if shape_table == 0:
        return None
    shape_base = int(
        gamemem.read_runtime_u32(
            shape_table + int(slot) * 12 + int(sub_index) * 4
        )
    )
    return _radar_resource_sprite(gamemem, shape_base + int(style_offset))


def _direct_target_position(record_data):
    # HUD_new2 identifies record+0x04 as an optional external position source,
    # but does not yet specify the pointed-to layout. Use inline coordinates
    # until that resolver is known.
    return struct.unpack_from("<3i", record_data, 0x18)


def _append_direct_nav_blips(gamemem, projection, shape_table, style_offset, sprites):
    record_count = int(gamemem.read_reloc_i32(ADDR_DIRECT_TARGET_COUNT))
    if record_count < 0 or record_count > DIRECT_TARGET_LIMIT:
        return
    current_group = int(gamemem.read_reloc_i32(ADDR_DIRECT_TARGET_GROUP))
    record_bytes = bytes(
        gamemem.read_reloc_bytes(ADDR_DIRECT_TARGETS, record_count * 0x54)
    )
    group_bit = 1 << current_group if 0 <= current_group < 31 else 0
    normal_sprite = _radar_table_sprite(
        gamemem,
        shape_table,
        4,
        0,
        style_offset,
    )
    special_sprite = _radar_table_sprite(
        gamemem,
        shape_table,
        4,
        1,
        style_offset,
    )
    for index in range(record_count):
        offset = index * 0x54
        active = struct.unpack_from("<I", record_bytes, offset)[0]
        group = struct.unpack_from("<I", record_bytes, offset + 0x0C)[0]
        if group != current_group or active == 0:
            continue
        flags = struct.unpack_from("<H", record_bytes, offset + 0x24)[0]
        if flags & 0x0001:
            continue
        position = _direct_target_position(
            record_bytes[offset : offset + 0x54],
        )
        point = _project_radar(position, projection)
        if point is None:
            continue
        visibility_mask = struct.unpack_from("<h", record_bytes, offset + 0x26)[0]
        special = bool(flags & 0x0020) and bool(visibility_mask | group_bit)
        sprite = special_sprite if special else normal_sprite
        if sprite is not None:
            sprites.append(
                RadarSprite(
                    sprite,
                    float(point[0]),
                    float(point[1]),
                    (
                        projection["left"],
                        projection["top"],
                        projection["left"] + projection["width"],
                        projection["top"] + projection["height"],
                    ),
                )
            )


def _resolve_primary_target(gamemem, target_index, classification_data):
    entity = int(gamemem.read_reloc_u32(ADDR_ENTITY_TABLE + int(target_index) * 4))
    if entity == 0:
        return None
    entity_data = bytes(gamemem.read_runtime_bytes(entity + 0x08, 0x54))
    position = struct.unpack_from("<3i", entity_data, 0x48)
    slot = struct.unpack_from("<I", entity_data, 0x00)[0]
    return position, _primary_sub_index(classification_data, slot)


def _resolve_secondary_target(gamemem, target_index):
    target_index = int(target_index)
    if target_index < 0:
        return None
    secondary_count = int(gamemem.read_reloc_i32(ADDR_SECONDARY_COUNT))
    if target_index >= secondary_count or secondary_count > SECONDARY_LIMIT:
        return None
    entry = bytes(
        gamemem.read_reloc_bytes(
            ADDR_SECONDARY_TABLE + target_index * 0x40,
            0x40,
        )
    )
    object_index = struct.unpack_from("<i", entry, 0x04)[0]
    maximum_index = int(gamemem.read_reloc_i32(ADDR_SECONDARY_MAX_INDEX))
    if object_index < 0 or object_index > maximum_index:
        return None
    position_pointer = int(
        gamemem.read_reloc_u32(
            ADDR_SECONDARY_POSITION_TABLE + object_index * 0x7C + 0x1C
        )
    )
    if position_pointer == 0:
        node_pointer = int(
            gamemem.read_reloc_u32(
                ADDR_SECONDARY_POSITION_TABLE + object_index * 0x7C + 0x20
            )
        )
        if node_pointer == 0:
            return None
        position = struct.unpack(
            "<3i",
            bytes(gamemem.read_runtime_bytes(node_pointer + 0x60, 12)),
        )
    else:
        position = struct.unpack(
            "<3i",
            bytes(gamemem.read_runtime_bytes(position_pointer + 0x34, 12)),
        )
    reference = struct.unpack_from("<i", entry, 0x0C)[0]
    sub_index = (
        2
        if reference < 0
        else max(
            0,
            min(
                2,
                int(gamemem.read_reloc_u8(ADDR_SECONDARY_CLASSIFICATION + reference)),
            ),
        )
    )
    return position, sub_index


def snapshot_radar_hud(
    gamemem,
    player_slot,
    mech_body,
    mech_data,
    target_data,
    camera,
    *,
    max_horizontal_fov_degrees,
    hud_mode=2,
    lifecycle=None,
    transition_phase="steady",
    transition_progress=1.0,
    viewport_size=(1024, 768),
    selected_target_indicators=True,
):
    mode_data = bytes(gamemem.read_reloc_bytes(ADDR_RADAR_MODE_STATE, 5))
    mode = mode_data[4]
    if mode == RADAR_MODE_OFF or mode not in RADAR_MODES:
        return EMPTY_RADAR_HUD

    config_table = int(gamemem.read_reloc_u32(ADDR_RADAR_CONFIG_TABLE))
    if config_table == 0:
        return EMPTY_RADAR_HUD
    config = int(gamemem.read_runtime_u32(config_table + mode * 4))
    if config == 0:
        return EMPTY_RADAR_HUD
    config_data = bytes(gamemem.read_runtime_bytes(config, 0x8C))
    pane = struct.unpack_from("<I", config_data, 0x00)[0]
    if pane == 0:
        return EMPTY_RADAR_HUD
    final_pane = _read_window(gamemem, pane)
    if final_pane is None:
        return EMPTY_RADAR_HUD
    if (
        mode != 4
        and transition_phase == "shutdown"
        and transition_progress >= 1.0
    ):
        return EMPTY_RADAR_HUD
    reveal_extent = 1.0
    if mode != 4 and transition_phase == "startup":
        reveal_extent = transition_progress
    elif mode != 4 and transition_phase == "shutdown":
        reveal_extent = 1.0 - transition_progress
    left, top, right, bottom = final_pane
    width = right - left + 1
    height = bottom - top + 1
    if width <= 2 or height <= 2:
        return EMPTY_RADAR_HUD
    center_x = width >> 1
    center_y = height >> 1
    radius_x = (width >> 1) - 1

    active_camera = int(gamemem.read_reloc_u32(ADDR_ACTIVE_CAMERA))
    if active_camera == 0:
        return EMPTY_RADAR_HUD
    camera_data = bytes(gamemem.read_runtime_bytes(active_camera + 0x18, 0x30))
    # Satellite represents the player's cockpit field of view even when F10
    # has replaced the active camera and its focal field with weapon-view
    # state. The cockpit zoom global remains authoritative in that case.
    aspect = (
        max(
            0x00008000,
            min(
                0x00100000,
                int(gamemem.read_reloc_i32(ADDR_COCKPIT_ZOOM)),
            ),
        )
        if mode == 4
        else struct.unpack_from("<i", camera_data, 0x00)[0]
    )
    ratio = struct.unpack_from("<I", camera_data, 0x2C)[0]
    radius_y = ((radius_x * ratio) + 0x8000) >> 16
    radius_y = max(1, radius_y)

    sub_config = struct.unpack_from("<I", config_data, 0x74)[0]
    if sub_config != 0:
        colors = bytes(gamemem.read_runtime_bytes(sub_config + 0x2C, 5))
        fov_color = colors[0]
        circle_color = colors[4]
    else:
        fov_color = 0x0E
        circle_color = 0x0E

    lines = []
    body_heading = int(gamemem.read_runtime_i32(mech_body + 0x60))
    cockpit_yaw = struct.unpack_from("<i", mech_data, 0x0C)[0]
    satellite_instruments_visible = mode != 4 or hud_mode == 2
    native_fov_half = (
        math.atan2(0x10000, aspect)
        if aspect != 0
        else math.pi * 0.25
    )
    fov_half = effective_radar_fov_half_angle(
        native_fov_half,
        viewport_size,
        max_horizontal_fov_degrees,
    )
    fov_heading = body_heading + cockpit_yaw if mode == 4 else cockpit_yaw
    base_angle = math.pi * 0.5 - fov_heading * (math.tau / ANGLE_FULL_TURN)
    radar_fov = (
        RadarFov(fov_color, base_angle, fov_half)
        if satellite_instruments_visible
        else None
    )
    if mode != 4:
        for angle in (base_angle - fov_half, base_angle + fov_half):
            direction_x = math.cos(angle)
            direction_y = -math.sin(angle)
            end_x, end_y = _ray_to_ellipse_edge(
                center_x,
                center_y,
                direction_x,
                direction_y,
                radius_x,
                radius_y,
            )
            lines.append(
                RadarLine(
                    fov_color,
                    left + center_x,
                    top + center_y,
                    left + end_x,
                    top + end_y,
                )
            )
    if not satellite_instruments_visible:
        lines.clear()

    circle_callback = struct.unpack_from("<I", config_data, 0x7C)[0]
    ellipse = None
    if circle_callback != 0:
        ellipse = RadarEllipse(
            circle_color,
            left + center_x,
            top + center_y,
            radius_x,
            radius_y,
        )

    range_scale = struct.unpack_from("<i", config_data, 0x18)[0]
    range_divisor = _idiv(range_scale, width)
    if range_divisor == 0:
        return RadarHudSnapshot(
            mode,
            tuple(lines),
            ellipse,
            (),
            (),
            (),
            None,
            None,
            (final_pane[0], final_pane[1], final_pane[2] + 1, final_pane[3] + 1),
            (reveal_extent, reveal_extent),
            radar_fov,
        )

    # Modes 1/2 are cockpit radar panes, so contacts are projected in the
    # player's body-relative radar frame. Mode 4 is the satellite/top-down pane:
    # up is world bearing 0, and the body/cockpit orientation is represented by
    # the FOV rays instead of rotating the contact projection.
    projection_heading = 0 if mode == 4 else body_heading
    yaw = projection_heading * (math.tau / ANGLE_FULL_TURN)
    cos_fp29 = int(round(math.cos(yaw) * FP29_SCALE))
    sin_fp29 = int(round(math.sin(yaw) * FP29_SCALE))
    camera_position = camera.get("position_fixed", (0, 0, 0)) if camera else (0, 0, 0)
    projection = {
        "left": left,
        "top": top,
        "width": width,
        "height": height,
        "center_x": center_x,
        "center_y": center_y,
        "radius_x": radius_x,
        "radius_y": radius_y,
        "ratio": ratio,
        "range_divisor": range_divisor,
        "cam_x": int(camera_position[0]),
        "cam_y": 0x00030D40,
        "cam_z": int(camera_position[2]),
        "mx": cos_fp29 >> 3,
        "mz": int(round(math.cos(yaw + math.pi * 0.5) * FP29_SCALE)) >> 3,
        "nz": sin_fp29 >> 3,
    }
    pane_bounds = (left, top, right, bottom)

    range_text = None
    bearing_text = None
    if (
        transition_phase != "startup"
        and mode_data[0] == 4
        and satellite_instruments_visible
    ):
        half_range = _idiv(range_scale, 2)
        if half_range >= 100000:
            range_value = _idiv(half_range << 16, 100000) / 65536.0
            unit_pointer = struct.unpack_from("<I", config_data, 0x54)[0]
        else:
            range_value = _idiv(half_range << 16, 100) / 65536.0
            unit_pointer = struct.unpack_from("<I", config_data, 0x50)[0]
        prefix_pointer = struct.unpack_from("<I", config_data, 0x3C)[0]
        prefix = _ascii_string(gamemem, prefix_pointer)
        unit = _ascii_string(gamemem, unit_pointer)
        text = f"{prefix}{range_value:3.1f}{unit}"
        if not prefix and not unit:
            text_pointer = struct.unpack_from("<I", config_data, 0x40)[0]
            text = _ascii_string(gamemem, text_pointer)
        text_x, text_y = struct.unpack_from("<2i", config_data, 0x60)
        if mode == 1:
            text_x -= 10
            text_y -= 17
        elif mode == 2:
            text_x -= 16
            text_y -= 17
        range_text = RadarRangeText(
            text,
            circle_color,
            left + text_x,
            top + text_y,
        )
        if mode == 4:
            bearing_prefix_pointer = struct.unpack_from("<I", config_data, 0x44)[0]
            bearing_prefix = _ascii_string(gamemem, bearing_prefix_pointer)
            bearing_x, bearing_y = struct.unpack_from("<2i", config_data, 0x68)
            bearing_text = RadarRangeText(
                f"{bearing_prefix}{(body_heading % ANGLE_FULL_TURN) / 65536.0:3.1f}",
                circle_color,
                left + bearing_x,
                top + bearing_y,
            )

    blips = []
    radar_sprites = []
    target_sprites = []
    target_markers = []
    target_cross = None
    style_offset = int(gamemem.read_reloc_u8(ADDR_HUD_STYLE_OFFSET))
    shape_table = struct.unpack_from("<I", config_data, 0x70)[0]

    if mode == 4:
        # Direct navigation points remain visible in satellite mode whether or
        # not one of them is the selected target. A resolution-aware cross is
        # drawn over the selected onscreen target.
        _append_direct_nav_blips(
            gamemem,
            projection,
            shape_table,
            style_offset,
            radar_sprites,
        )
        handle = (
            _target_handle(target_data)
            if selected_target_indicators
            else 0
        )
        if handle != 0 and (handle & 0x1000) == 0:
            target_kind = handle & 0x0F00
            target_index = handle & 0x00FF
            position = struct.unpack_from("<3i", target_data, 0x00)
            target_sub_index = 0
            if target_kind == 0x0200:
                classification_data = bytes(
                    gamemem.read_reloc_bytes(
                        ADDR_PRIMARY_CLASSIFICATION,
                        PRIMARY_CLASSIFICATION_SLOTS * 0x26,
                    )
                )
                resolved = _resolve_primary_target(
                    gamemem,
                    target_index,
                    classification_data,
                )
                if resolved is not None:
                    _resolved_position, target_sub_index = resolved
            elif target_kind == 0x0400:
                resolved = _resolve_secondary_target(gamemem, target_index)
                if resolved is not None:
                    _resolved_position, target_sub_index = resolved
            if target_kind in (0x0100, 0x0200, 0x0400):
                mode4_point = _project_target_satellite_output(
                    position,
                    camera,
                    viewport_size,
                )
                if mode4_point is None:
                    mode4_point = _project_target_radar_mode4(
                        position,
                        projection,
                    )
                    if mode4_point is not None:
                        native_x, native_y, visible, edge = mode4_point
                        mode4_point = (
                            (native_x - left) * viewport_size[0] / width,
                            (native_y - top) * viewport_size[1] / height,
                            visible,
                            edge,
                        )
                if mode4_point is not None:
                    target_x, target_y, visible, edge = mode4_point
                    if visible:
                        color_index = (
                            0x0E
                            if target_kind == 0x0100
                            else (0x0E, 0x0A, 0x06)[target_sub_index]
                        )
                        target_cross = RadarCross(
                            float(target_x),
                            float(target_y),
                            color_index,
                        )
                    else:
                        color_index = (
                            0x0E
                            if target_kind == 0x0100
                            else (0x0E, 0x0A, 0x06)[target_sub_index]
                        )
                        target_markers.append(
                            RadarTargetMarker(
                                TARGET_CARET_BY_DIRECTION[edge],
                                float(target_x),
                                float(target_y),
                                (0, 0, viewport_size[0], viewport_size[1]),
                                color_index,
                            )
                        )
        return RadarHudSnapshot(
            mode,
            tuple(lines),
            None,
            tuple(radar_sprites),
            (),
            tuple(target_sprites),
            range_text,
            bearing_text,
            (final_pane[0], final_pane[1], final_pane[2] + 1, final_pane[3] + 1),
            (1.0, 1.0),
            radar_fov,
            tuple(target_markers),
            target_cross,
        )

    _append_direct_nav_blips(
        gamemem,
        projection,
        shape_table,
        style_offset,
        radar_sprites,
    )
    player_sprite = _radar_player_sprite(
        gamemem,
        style_offset,
    )
    if player_sprite is not None:
        radar_sprites.append(
            RadarSprite(
                player_sprite,
                left + center_x,
                top + center_y,
                (left, top, right + 1, bottom + 1),
            )
        )
    primary_targets = {}
    classification_data = bytes(
        gamemem.read_reloc_bytes(
            ADDR_PRIMARY_CLASSIFICATION,
            PRIMARY_CLASSIFICATION_SLOTS * 0x26,
        )
    )
    entity_count = int(gamemem.read_reloc_i32(ADDR_ENTITY_COUNT))
    if 0 <= entity_count <= ENTITY_LIMIT:
        entity_table = bytes(
            gamemem.read_reloc_bytes(ADDR_ENTITY_TABLE, entity_count * 4)
        )
        for entity_index, entity in enumerate(
            struct.unpack(f"<{entity_count}I", entity_table)
        ):
            if entity == 0 or entity_index == player_slot:
                continue
            entity_data = bytes(gamemem.read_runtime_bytes(entity + 0x08, 0x54))
            flags = struct.unpack_from("<H", entity_data, 0x0C)[0]
            if (flags & 0x1400) == 0 or (flags & 0x0016) != 0:
                continue
            position = struct.unpack_from("<3i", entity_data, 0x48)
            slot = struct.unpack_from("<I", entity_data, 0x00)[0]
            sub_index = _primary_sub_index(classification_data, slot)
            primary_targets[entity_index] = (position, sub_index)
            point = _project_radar(position, projection)
            if point is None:
                continue
            _append_point(
                blips,
                point[0],
                point[1],
                _blip_color(sub_index),
                pane_bounds,
            )

    secondary_targets = {}
    secondary_count = int(gamemem.read_reloc_i32(ADDR_SECONDARY_COUNT))
    if 0 <= secondary_count <= SECONDARY_LIMIT:
        secondary_table = bytes(
            gamemem.read_reloc_bytes(
                ADDR_SECONDARY_TABLE,
                secondary_count * 0x40,
            )
        )
        maximum_index = int(gamemem.read_reloc_i32(ADDR_SECONDARY_MAX_INDEX))
        for secondary_index in range(secondary_count):
            offset = secondary_index * 0x40
            flags = struct.unpack_from("<H", secondary_table, offset)[0]
            if (flags & 0x1400) == 0 or (flags & 0x001E) != 0:
                continue
            object_index = struct.unpack_from("<i", secondary_table, offset + 0x04)[0]
            if object_index < 0 or object_index > maximum_index:
                continue
            position_pointer = int(
                gamemem.read_reloc_u32(
                    ADDR_SECONDARY_POSITION_TABLE + object_index * 0x7C + 0x1C
                )
            )
            if position_pointer == 0:
                node_pointer = int(
                    gamemem.read_reloc_u32(
                        ADDR_SECONDARY_POSITION_TABLE + object_index * 0x7C + 0x20
                    )
                )
                if node_pointer == 0:
                    continue
                position = struct.unpack(
                    "<3i",
                    bytes(gamemem.read_runtime_bytes(node_pointer + 0x60, 12)),
                )
            else:
                position = struct.unpack(
                    "<3i",
                    bytes(gamemem.read_runtime_bytes(position_pointer + 0x34, 12)),
                )
            reference = struct.unpack_from("<i", secondary_table, offset + 0x0C)[0]
            sub_index = (
                2
                if reference < 0
                else max(
                    0,
                    min(
                        2,
                        int(
                            gamemem.read_reloc_u8(
                                ADDR_SECONDARY_CLASSIFICATION + reference
                            )
                        ),
                    ),
                )
            )
            secondary_targets[secondary_index] = (position, sub_index)
            point = _project_radar(position, projection)
            if point is None:
                continue
            _append_point(
                blips,
                point[0],
                point[1],
                _blip_color(sub_index),
                pane_bounds,
            )

    handle = (
        _target_handle(target_data)
        if selected_target_indicators
        else 0
    )
    if handle != 0 and (handle & 0x1000) == 0:
        target_kind = handle & 0x0F00
        target_index = handle & 0x00FF
        target_point = None
        target_sub_index = 0
        target_slot = None
        if target_kind == 0x0200:
            resolved = primary_targets.get(target_index)
            if resolved is not None:
                position, target_sub_index = resolved
                visible_point = _project_radar(position, projection)
                target_point = (
                    visible_point
                    if visible_point is not None
                    else _project_target_radar(position, projection)
                )
                target_slot = 2 if visible_point is not None else 3
        elif target_kind == 0x0400:
            resolved = secondary_targets.get(target_index)
            if resolved is not None:
                position, target_sub_index = resolved
                visible_point = _project_radar(position, projection)
                target_point = (
                    visible_point
                    if visible_point is not None
                    else _project_target_radar(position, projection)
                )
                target_slot = 2 if visible_point is not None else 3
        elif target_kind == 0x0100:
            direct_position = struct.unpack_from("<3i", target_data, 0x00)
            visible_point = _project_radar(direct_position, projection)
            target_point = (
                visible_point
                if visible_point is not None
                else _project_target_radar(direct_position, projection)
            )
            target_sub_index = 0
            target_slot = 5 if visible_point is not None else 6
        if target_point is not None and target_slot is not None:
            target_sprite = _radar_table_sprite(
                gamemem,
                shape_table,
                target_slot,
                target_sub_index,
                style_offset,
            )
            if target_sprite is not None:
                target_sprites.append(
                    RadarSprite(
                        target_sprite,
                        float(target_point[0]),
                        float(target_point[1]),
                        (left, top, right + 1, bottom + 1),
                    )
                )

    return RadarHudSnapshot(
        mode,
        tuple(lines),
        ellipse,
        tuple(radar_sprites),
        tuple(blips),
        tuple(target_sprites),
        range_text,
        bearing_text,
        (final_pane[0], final_pane[1], final_pane[2] + 1, final_pane[3] + 1),
        (reveal_extent, reveal_extent),
        radar_fov,
    )
