from .projection import (
    PROJECTION_FAR,
    PROJECTION_FOCAL_LENGTH_PIXELS,
    PROJECTION_NEAR,
)


CAMERA_POSITION_ADDR = 0x0015FFA4
CAMERA_ROTATION_ADDR = 0x000A705C
CAMERA_MODE_ADDR = 0x000A6FB4
CAMERA_FAR_DEPTH_ADDR = 0x0015FF80
CAMERA_NEAR_DEPTH_ADDR = 0x0015FFC8
# camera_struct+0x18: game-selected and clamped 16.16 zoom/focal scale.
CAMERA_FOCAL_LENGTH_ADDR = 0x000A7020
PALETTE_SIZE = 256 * 3
FIXED_16_16_SCALE = 65536.0
NATIVE_VIEW_DEPTH_MULTIPLIER = 4.0
ROTATION_SCALE = float(1 << 29)
FOCAL_LENGTH_FIXED_MIN = 0x00008000
FOCAL_LENGTH_FIXED_MAX = 0x00100000


def vga_dac_to_u8(value):
    return ((int(value) & 0x3F) * 255) // 63


def palette_dac_to_rgb(raw_palette):
    if len(raw_palette) != PALETTE_SIZE:
        raise ValueError("expected 768 bytes of VGA DAC palette data")

    rgb = bytearray(PALETTE_SIZE)
    for index, value in enumerate(raw_palette):
        rgb[index] = vga_dac_to_u8(value)
    return bytes(rgb)


def palette_color_float(palette_rgb, palette_index):
    palette_index = max(0, min(255, int(palette_index)))
    offset = palette_index * 3
    return (
        palette_rgb[offset] / 255.0,
        palette_rgb[offset + 1] / 255.0,
        palette_rgb[offset + 2] / 255.0,
    )


def palette_index_to_u(palette_index):
    return (max(0, min(255, int(palette_index))) + 0.5) / 256.0


def focal_length_pixels_from_fixed(raw_focal_length):
    raw_focal_length = int(raw_focal_length)
    if raw_focal_length <= 0:
        raw_focal_length = int(FIXED_16_16_SCALE)
    raw_focal_length = max(
        FOCAL_LENGTH_FIXED_MIN,
        min(FOCAL_LENGTH_FIXED_MAX, raw_focal_length),
    )
    return PROJECTION_FOCAL_LENGTH_PIXELS * (raw_focal_length / FIXED_16_16_SCALE)


def read_focal_length_pixels(gamemem):
    return focal_length_pixels_from_fixed(
        gamemem.read_reloc_i32(CAMERA_FOCAL_LENGTH_ADDR)
    )


def read_camera(gamemem):
    camera_mode = int(gamemem.read_reloc_i32(CAMERA_MODE_ADDR))
    raw_pos = [
        gamemem.read_reloc_i32(CAMERA_POSITION_ADDR + 0),
        gamemem.read_reloc_i32(CAMERA_POSITION_ADDR + 4),
        gamemem.read_reloc_i32(CAMERA_POSITION_ADDR + 8),
    ]
    position_fixed = (raw_pos[1], raw_pos[0], raw_pos[2])
    pos = (
        position_fixed[0] / FIXED_16_16_SCALE,
        position_fixed[1] / FIXED_16_16_SCALE,
        position_fixed[2] / FIXED_16_16_SCALE,
    )

    rotation_fixed = [
        gamemem.read_reloc_i32(CAMERA_ROTATION_ADDR + index * 4)
        for index in range(9)
    ]
    rotation = [value / ROTATION_SCALE for value in rotation_fixed]
    right = (rotation[0], rotation[1], rotation[2])
    up = (rotation[3], rotation[4], rotation[5])
    forward = (rotation[6], rotation[7], rotation[8])

    camera = {
        "position": pos,
        "position_fixed": position_fixed,
        "right": right,
        "right_fixed": tuple(rotation_fixed[0:3]),
        "up": up,
        "up_fixed": tuple(rotation_fixed[3:6]),
        "forward": forward,
        "forward_fixed": tuple(rotation_fixed[6:9]),
        "focal_length_pixels": read_focal_length_pixels(gamemem),
        "camera_mode": camera_mode,
        "near_plane": PROJECTION_NEAR,
        "far_plane": PROJECTION_FAR,
    }
    near_depth = int(gamemem.read_reloc_i32(CAMERA_NEAR_DEPTH_ADDR))
    far_depth = int(gamemem.read_reloc_i32(CAMERA_FAR_DEPTH_ADDR))
    if near_depth > 0:
        camera["near_depth_fixed"] = near_depth
    if camera_mode == 0 and near_depth > 0:
        # Native polygon clipping compares the exported plane against the
        # camera-forward dot product multiplied by four. Renderer coordinates
        # store that dot product directly in 16.16 world units.
        camera["clip_near_plane"] = near_depth / (
            FIXED_16_16_SCALE * NATIVE_VIEW_DEPTH_MULTIPLIER
        )
    if far_depth > near_depth:
        # Retain the native far value for diagnostics and selection policy, but
        # keep the enhanced renderer's far plane for drawing.
        camera["far_depth_fixed"] = far_depth
    return camera
