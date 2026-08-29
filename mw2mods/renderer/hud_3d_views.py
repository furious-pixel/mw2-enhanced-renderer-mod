from __future__ import annotations

import math
import struct
from dataclasses import dataclass

from .projection import PROJECTION_FAR, PROJECTION_NEAR


ADDR_ACTIVE_CAMERA = 0x000A70E8
ADDR_EXPORTED_FAR_DEPTH = 0x0015FF80
ADDR_CAMERA_MODE = 0x000A6FB4
ADDR_SPECIAL_CAMERA_LATCH = 0x000A62B4
ADDR_ENTITY_BODY_TABLE = 0x00108B00
ADDR_MFD_REAR_SPECIAL = 0x0010E1D0
ADDR_PLAYER_SLOT = 0x000A5918
ADDR_RADAR_CONFIG_TABLE = 0x000B4CF0
ADDR_RADAR_CAMERA_NODE_FALLBACK = 0x000B4CFC
ADDR_RADAR_MODE = 0x000B4D08
ADDR_SATELLITE_SHADE_BIAS = 0x000A5660
ADDR_SATELLITE_SHADE_DIVISOR = 0x000A5668
ADDR_SATELLITE_DAMAGE_STATE = 0x000A5648
ADDR_SATELLITE_DEGRADED_WINDOW = 0x000B4768
ADDR_SATELLITE_DEGRADED_WINDOW_VALID = 0x000B4790
ADDR_LINE_DRAW_ENABLED = 0x000A7100
ADDR_POINT_DRAW_ENABLED = 0x000A7104
ADDR_SECONDARY_MAX_INDEX = 0x000A6D50
ADDR_SECONDARY_POSITION_TABLE = 0x00111FA0
ADDR_SECONDARY_TARGETS = 0x00104B80
ADDR_WEAPON_VIEW_ACTIVE_SLOT = 0x000AEF08
ADDR_WEAPON_VIEW_STATE = 0x00163A30

ANGLE_FULL_TURN = 0x01680000
FIXED_16_16_SCALE = 65536.0
NATIVE_VIEW_DEPTH_MULTIPLIER = 4.0
FP29_SCALE = 1 << 29
HUD_VIEW_FOCAL_RATIO = 2.0
HUD_VIEW_FOCAL_FIXED = 0x00020000
HUD_VIEW_PROJECTION_ASPECT_SCALE = 0xFFFF / 65536.0
TARGET_VIEW_PANE_RECT = (32, 559, 196, 686)
SATELLITE_CAMERA_PITCH = 0x005A0000
# The native satellite near plane can reject peaks close to the overhead
# camera, cutting holes in mountains. Use the enhanced renderer's smaller
# standard near plane while retaining the tighter satellite far plane for
# useful orthographic depth precision.
SATELLITE_NEAR_PLANE = PROJECTION_NEAR
SATELLITE_FAR_PLANE = 8.0


@dataclass(frozen=True, slots=True)
class HudCameraView:
    kind: str
    pane_rect: tuple[int, int, int, int]
    camera: dict
    mirror_horizontal: bool = False
    model_root: int = 0
    excluded_node_addrs: tuple[int, ...] = ()
    display_mode: int = 0
    filter_center: tuple[float, float, float] | None = None
    filter_radius: float = 0.0
    include_static: bool = False
    target_id: object = None


@dataclass(frozen=True, slots=True)
class TargetCameraState:
    view: HudCameraView | None
    fallback_shape: int | None


def resolve_target_model_root(gamemem, target_kind, target_index):
    target_kind = int(target_kind)
    target_index = int(target_index)
    if target_kind == 0x0200:
        target_entity = int(
            gamemem.read_reloc_u32(ADDR_ENTITY_BODY_TABLE + target_index * 4)
        )
        if target_entity == 0:
            return 0
        return int(gamemem.read_runtime_u32(target_entity + 0x40))

    if target_kind != 0x0400:
        return 0
    secondary = ADDR_SECONDARY_TARGETS + target_index * 0x40
    resource_index = int(gamemem.read_reloc_i32(secondary + 0x04))
    resource_count = int(gamemem.read_reloc_i32(ADDR_SECONDARY_MAX_INDEX))
    if not 0 <= resource_index < resource_count:
        return 0
    record = ADDR_SECONDARY_POSITION_TABLE + resource_index * 0x7C
    return int(gamemem.read_reloc_u32(record + 0x20))


def _camera_from_pose(x, y, z, yaw, pitch, roll):
    yaw_radians = float(yaw) * (math.tau / ANGLE_FULL_TURN)
    pitch_radians = float(pitch) * (math.tau / ANGLE_FULL_TURN)
    roll_radians = float(roll) * (math.tau / ANGLE_FULL_TURN)

    sin_yaw = math.sin(yaw_radians)
    cos_yaw = math.cos(yaw_radians)
    sin_pitch = math.sin(pitch_radians)
    cos_pitch = math.cos(pitch_radians)
    sin_roll = math.sin(roll_radians)
    cos_roll = math.cos(roll_radians)

    forward = (
        sin_yaw * cos_pitch,
        -sin_pitch,
        cos_yaw * cos_pitch,
    )
    level_right = (cos_yaw, 0.0, -sin_yaw)
    level_up = (
        sin_yaw * sin_pitch,
        cos_pitch,
        cos_yaw * sin_pitch,
    )
    right = tuple(
        level_right[index] * cos_roll + level_up[index] * sin_roll
        for index in range(3)
    )
    up = tuple(
        level_up[index] * cos_roll - level_right[index] * sin_roll
        for index in range(3)
    )
    position_fixed = (int(x), int(y), int(z))
    return {
        "position": tuple(value / FIXED_16_16_SCALE for value in position_fixed),
        "position_fixed": position_fixed,
        "right": right,
        "right_fixed": tuple(int(round(value * FP29_SCALE)) for value in right),
        "up": up,
        "up_fixed": tuple(int(round(value * FP29_SCALE)) for value in up),
        "forward": forward,
        "forward_fixed": tuple(
            int(round(value * FP29_SCALE)) for value in forward
        ),
        "focal_length_pixels": 1024.0,
        "far_plane": PROJECTION_FAR,
    }


def _read_active_pose(gamemem):
    active_camera = int(gamemem.read_reloc_u32(ADDR_ACTIVE_CAMERA))
    if active_camera == 0:
        return None
    return struct.unpack(
        "<7i",
        bytes(gamemem.read_runtime_bytes(active_camera, 0x1C)),
    )


def _apply_hud_near_plane(camera, pane_width):
    focal = max(0x00008000, min(0x00100000, HUD_VIEW_FOCAL_FIXED))
    half_width = max(1, int(pane_width) >> 1)
    near_source = ((focal * half_width) >> 17) + 1
    near_depth = near_source * 4
    camera["near_plane"] = near_depth / (
        FIXED_16_16_SCALE * NATIVE_VIEW_DEPTH_MULTIPLIER
    )
    camera["near_depth_fixed"] = near_depth


def _attach_inherited_far_depth(gamemem, camera):
    far_depth = int(gamemem.read_reloc_i32(ADDR_EXPORTED_FAR_DEPTH))
    if far_depth > 0:
        camera["far_depth_fixed"] = far_depth


def _player_model_render_nodes(gamemem):
    try:
        player_slot = int(gamemem.read_reloc_u32(ADDR_PLAYER_SLOT))
        player_entity = int(
            gamemem.read_reloc_u32(ADDR_ENTITY_BODY_TABLE + player_slot * 4)
        )
        if player_entity == 0:
            return ()
        model_root = int(gamemem.read_runtime_u32(player_entity + 0x40))
    except Exception:
        return ()

    attached = set()
    seen = set()
    stack = [model_root]
    while stack and len(seen) < 4096:
        node = int(stack.pop())
        if node == 0 or node in seen:
            continue
        seen.add(node)
        try:
            node_data = bytes(gamemem.read_runtime_bytes(node, 0x70))
        except Exception:
            continue
        child, sibling = struct.unpack_from("<II", node_data, 0x04)
        render_node = struct.unpack_from("<I", node_data, 0x6C)[0]
        if sibling:
            stack.append(sibling)
        if child:
            stack.append(child)
        if render_node:
            attached.add(int(render_node))
    return tuple(sorted(attached))


def _satellite_center_position(gamemem, active_camera):
    try:
        if int(gamemem.read_reloc_i32(ADDR_RADAR_CAMERA_NODE_FALLBACK)) == 0:
            player_slot = int(gamemem.read_reloc_u32(ADDR_PLAYER_SLOT))
            player_entity = int(
                gamemem.read_reloc_u32(ADDR_ENTITY_BODY_TABLE + player_slot * 4)
            )
            if player_entity:
                camera_node = int(
                    gamemem.read_runtime_u32(player_entity + 0x44)
                )
                if camera_node:
                    return struct.unpack(
                        "<3i",
                        bytes(
                            gamemem.read_runtime_bytes(
                                camera_node + 0x60,
                                0x0C,
                            )
                        ),
                    )
        active_camera_ptr = int(gamemem.read_reloc_u32(ADDR_ACTIVE_CAMERA))
        if active_camera_ptr:
            return struct.unpack(
                "<3i",
                bytes(gamemem.read_runtime_bytes(active_camera_ptr, 0x0C)),
            )
    except Exception:
        pass
    return active_camera.get("position_fixed", (0, 0, 0))


def _satellite_camera_position(gamemem, active_camera, range_fixed):
    center = _satellite_center_position(gamemem, active_camera)
    return (
        int(center[0]),
        int(range_fixed),
        int(center[2]),
    )


def snapshot_satellite_damage_viewport(gamemem):
    if int(gamemem.read_reloc_u8(ADDR_RADAR_MODE)) != 4:
        return None
    if int(gamemem.read_reloc_u8(ADDR_SATELLITE_DAMAGE_STATE)) != 2:
        return None
    if int(gamemem.read_reloc_i32(ADDR_SATELLITE_DEGRADED_WINDOW_VALID)) == 0:
        return None
    try:
        _buffer, left, top, right, bottom = struct.unpack(
            "<I4i",
            bytes(
                gamemem.read_reloc_bytes(
                    ADDR_SATELLITE_DEGRADED_WINDOW,
                    0x14,
                )
            ),
        )
    except Exception:
        return None
    if right < left or bottom < top:
        return None
    width = right - left + 1
    height = bottom - top + 1
    if width <= 0 or height <= 0 or width > 1024 or height > 768:
        return None
    return (int(left), int(top), int(right), int(bottom))


def snapshot_satellite_camera(gamemem, active_camera):
    if int(gamemem.read_reloc_u8(ADDR_RADAR_MODE)) != 4:
        return None
    # Player destruction leaves the radar mode latched at 4, moves the
    # persistent camera controller to external mode 1, and sets the automatic
    # camera latch. Camera update has already written that external pose into
    # the active camera, so declining the temporary satellite override only
    # for that combination renders the correct death view without breaking a
    # manual external-to-satellite transition.
    # Do not use the reticle gate: weapon mode 3 also clears it, while native
    # satellite rendering still takes precedence over the weapon camera.
    if (
        int(gamemem.read_reloc_i32(ADDR_CAMERA_MODE)) == 1
        and int(gamemem.read_reloc_i32(ADDR_SPECIAL_CAMERA_LATCH)) != 0
    ):
        return None
    config_table = int(gamemem.read_reloc_u32(ADDR_RADAR_CONFIG_TABLE))
    if config_table == 0:
        return None
    config = int(gamemem.read_runtime_u32(config_table + 4 * 4))
    if config == 0:
        return None
    config_data = bytes(gamemem.read_runtime_bytes(config, 0x78))
    pane = struct.unpack_from("<I", config_data, 0x00)[0]
    width_fixed = struct.unpack_from("<i", config_data, 0x18)[0]
    color_config = struct.unpack_from("<I", config_data, 0x74)[0]
    if pane == 0 or width_fixed <= 0:
        return None
    left, top, right, bottom = struct.unpack(
        "<4i",
        bytes(gamemem.read_runtime_bytes(pane + 0x04, 0x10)),
    )
    pane_width = right - left + 1
    pane_height = bottom - top + 1
    if pane_width <= 0 or pane_height <= 0:
        return None

    camera_position = _satellite_camera_position(
        gamemem,
        active_camera,
        width_fixed,
    )
    camera = _camera_from_pose(
        *camera_position,
        0,
        SATELLITE_CAMERA_PITCH,
        0,
    )
    half_width = width_fixed / (2.0 * FIXED_16_16_SCALE)
    shade_bias = int(gamemem.read_reloc_i32(ADDR_SATELLITE_SHADE_BIAS))
    satellite_colors = ()
    if color_config:
        color_values = struct.unpack(
            "<11I",
            bytes(gamemem.read_runtime_bytes(color_config, 0x2C)),
        )
        satellite_colors = tuple(int(value) for value in color_values)
    camera.update(
        {
            "projection_type": "orthographic",
            "orthographic_half_width": half_width,
            "orthographic_half_height": half_width * pane_height / pane_width,
            "near_plane": SATELLITE_NEAR_PLANE,
            "far_plane": SATELLITE_FAR_PLANE,
            "far_depth_fixed": (
                int(width_fixed) - min(shade_bias, 0)
            ) * 4,
            "satellite_view": True,
            "satellite_colors": satellite_colors,
            "satellite_primitive_gates": (
                bool(gamemem.read_reloc_i32(ADDR_POINT_DRAW_ENABLED)),
                bool(gamemem.read_reloc_i32(ADDR_LINE_DRAW_ENABLED)),
            ),
            "satellite_width_fixed": int(width_fixed),
            "satellite_shade_bias": shade_bias,
            "satellite_shade_divisor": int(
                gamemem.read_reloc_i32(ADDR_SATELLITE_SHADE_DIVISOR)
            ),
        }
    )
    return camera


def snapshot_target_camera_view(
    gamemem,
    target_data,
    pane_rect,
    display_mode,
):
    handle = struct.unpack_from("<I", target_data, 0x14)[0]
    target_kind = handle & 0x0F00
    target_index = handle & 0x00FF
    display_mode = int(display_mode)
    if display_mode not in (1, 2) or target_kind not in (0x0200, 0x0400):
        return TargetCameraState(None, None)

    target_x, target_y, target_z, yaw = struct.unpack_from("<4i", target_data, 0)
    target_object = 0
    model_root = 0
    radius = 0
    include_static = False

    if target_kind == 0x0200:
        target_entity = int(
            gamemem.read_reloc_u32(ADDR_ENTITY_BODY_TABLE + target_index * 4)
        )
        if target_entity != 0:
            target_object = int(gamemem.read_runtime_u32(target_entity + 0x20))
        if target_object == 0:
            return TargetCameraState(None, 0x5B)
        model_root = resolve_target_model_root(
            gamemem,
            target_kind,
            target_index,
        )
        if model_root == 0:
            return TargetCameraState(None, 0x58)
        radius = abs(int(gamemem.read_runtime_i32(target_object + 0xE8)))
    else:
        include_static = True
        model_root = resolve_target_model_root(
            gamemem,
            target_kind,
            target_index,
        )
        if model_root == 0:
            return TargetCameraState(None, 0x5B)
        target_object = model_root
        model_field = int(gamemem.read_runtime_u32(model_root + 0x6C))
        if model_field == 0:
            return TargetCameraState(None, 0x58)
        model_data = bytes(gamemem.read_runtime_bytes(model_field + 0x34, 0x10))
        target_x, target_y, target_z, radius = struct.unpack("<4i", model_data)
        radius = abs(int(radius))

    radius = int(radius)
    pane_width = max(1, int(pane_rect[2]) - int(pane_rect[0]) + 1)
    pane_height = max(1, int(pane_rect[3]) - int(pane_rect[1]) + 1)
    distance = radius * 3
    yaw_radians = float(yaw) * (math.tau / ANGLE_FULL_TURN)
    sin_fp29 = int(round(math.sin(yaw_radians) * FP29_SCALE))
    cos_fp29 = int(round(math.cos(yaw_radians) * FP29_SCALE))

    def rounded_fp29_product(value, scale):
        product = int(value) * int(scale)
        if product >= 0:
            return (product + (FP29_SCALE >> 1)) // FP29_SCALE
        return -((-product + (FP29_SCALE >> 1)) // FP29_SCALE)

    camera_x = target_x - rounded_fp29_product(sin_fp29, distance)
    camera_z = target_z - rounded_fp29_product(cos_fp29, distance)
    camera = _camera_from_pose(camera_x, target_y, camera_z, yaw, 0, 0)
    camera["focal_length_pixels"] = (
        (pane_width >> 1) * HUD_VIEW_FOCAL_RATIO
    )
    camera["projection_aspect_scale"] = HUD_VIEW_PROJECTION_ASPECT_SCALE
    camera["projection_center_pixels"] = (
        pane_width >> 1,
        pane_height >> 1,
    )
    _apply_hud_near_plane(camera, pane_width)
    _attach_inherited_far_depth(gamemem, camera)
    radius_world = max(1, radius) / FIXED_16_16_SCALE
    camera["far_plane"] = max(
        camera["near_plane"] * 2.0,
        radius_world * 8.0,
    )
    return TargetCameraState(
        HudCameraView(
            kind="target",
            pane_rect=tuple(int(value) for value in pane_rect),
            camera=camera,
            model_root=model_root,
            display_mode=display_mode,
            filter_center=tuple(
                value / FIXED_16_16_SCALE
                for value in (target_x, target_y, target_z)
            ),
            filter_radius=(radius * 1.5) / FIXED_16_16_SCALE,
            include_static=include_static,
            target_id=(target_kind, target_index, target_object, model_root),
        ),
        None,
    )


def snapshot_mfd_camera_view(
    gamemem,
    mode,
    player_body,
    pane_rect,
    *,
    rear_camera_mirror,
):
    mode = int(mode)
    if mode not in (3, 4, 5):
        return None

    active_pose = _read_active_pose(gamemem)
    if active_pose is None:
        return None
    active_x, active_y, active_z, active_yaw, _active_pitch, active_roll, _ = (
        active_pose
    )
    body_pose = struct.unpack(
        "<8i",
        bytes(gamemem.read_runtime_bytes(player_body + 0x50, 0x20)),
    )
    body_x, body_y, body_z = body_pose[0:3]
    body_yaw = body_pose[4]
    cockpit_yaw = body_pose[7]

    if mode == 3:
        if int(gamemem.read_reloc_i32(ADDR_MFD_REAR_SPECIAL)) != 0:
            yaw = body_yaw + cockpit_yaw
        else:
            yaw = body_yaw + 0x00B40000
        camera = _camera_from_pose(
            active_x,
            active_y,
            active_z,
            yaw,
            0,
            active_roll,
        )
    elif mode == 4:
        camera = _camera_from_pose(
            body_x,
            body_y,
            body_z,
            active_yaw,
            0x005A0000,
            0,
        )
    else:
        active_slot = int(gamemem.read_reloc_i32(ADDR_WEAPON_VIEW_ACTIVE_SLOT))
        if active_slot < 0:
            return None
        usable = int(
            gamemem.read_reloc_i32(
                ADDR_WEAPON_VIEW_STATE + active_slot * 80 + 0x6C
            )
        )
        if usable == 0:
            return None
        weapon_pose = struct.unpack(
            "<6i",
            bytes(gamemem.read_reloc_bytes(ADDR_WEAPON_VIEW_STATE, 0x18)),
        )
        camera = _camera_from_pose(
            weapon_pose[0],
            weapon_pose[1],
            weapon_pose[2],
            weapon_pose[3],
            0,
            weapon_pose[5],
        )

    pane_width = max(1, int(pane_rect[2]) - int(pane_rect[0]) + 1)
    pane_height = max(1, int(pane_rect[3]) - int(pane_rect[1]) + 1)
    camera["focal_length_pixels"] = (
        (pane_width >> 1) * HUD_VIEW_FOCAL_RATIO
    )
    camera["projection_aspect_scale"] = HUD_VIEW_PROJECTION_ASPECT_SCALE
    camera["projection_center_pixels"] = (
        pane_width >> 1,
        pane_height >> 1,
    )
    _apply_hud_near_plane(camera, pane_width)
    _attach_inherited_far_depth(gamemem, camera)

    excluded_node_addrs = (
        _player_model_render_nodes(gamemem)
        if mode in (3, 4)
        else ()
    )
    return HudCameraView(
        kind={3: "rear", 4: "down", 5: "weapon"}[mode],
        pane_rect=tuple(int(value) for value in pane_rect),
        camera=camera,
        mirror_horizontal=(mode == 3 and bool(rear_camera_mirror)),
        excluded_node_addrs=excluded_node_addrs,
    )
