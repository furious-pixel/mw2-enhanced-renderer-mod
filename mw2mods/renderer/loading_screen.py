from __future__ import annotations

from dataclasses import dataclass, replace

from .hud_sprites import HudSprite, decode_hud_sprite, hud_sprite_frame_count


ADDR_LOADING_SHAPE_BACKGROUND = 0x000A5884
ADDR_LOADING_SHAPE_STRIP = 0x000A5888
ADDR_LOADING_SHAPE_COUNT = 0x000A588C
ADDR_LOADING_UI_RAMP = 0x000A5890
ADDR_LOADING_PANE = 0x000B56B0
ADDR_LOADING_STRIP_X = 0x000B56C4
ADDR_LOADING_STRIP_Y = 0x000B56C8

LOADING_STRIP_PERIOD_SECONDS = 0.330


@dataclass(frozen=True, slots=True)
class LoadingScreenVisual:
    cache_key: object
    background: HudSprite
    strips: tuple[HudSprite, ...]
    palette_rgb: bytes
    clip_x: int
    clip_y: int
    strip_x: int
    strip_y: int


def capture_loading_background(gamemem):
    background_table = int(
        gamemem.read_reloc_u32(ADDR_LOADING_SHAPE_BACKGROUND)
    )
    background = _decode_loading_sprite(gamemem, background_table, 0)
    if background is None:
        raise RuntimeError("loading background shape is unavailable")

    palette_rgb = _read_loading_palette_rgb(gamemem, background_table)
    clip_x = int(gamemem.read_reloc_i32(ADDR_LOADING_PANE + 0x04))
    clip_y = int(gamemem.read_reloc_i32(ADDR_LOADING_PANE + 0x08))
    return LoadingScreenVisual(
        cache_key=("background", background.cache_key),
        background=background,
        strips=(),
        palette_rgb=palette_rgb,
        clip_x=clip_x,
        clip_y=clip_y,
        strip_x=0,
        strip_y=0,
    )


def capture_loading_screen(gamemem, previous=None):
    background_table = int(
        gamemem.read_reloc_u32(ADDR_LOADING_SHAPE_BACKGROUND)
    )
    strip_table = int(gamemem.read_reloc_u32(ADDR_LOADING_SHAPE_STRIP))
    background = (
        previous.background
        if previous is not None
        and previous.background.cache_key[1] == int(background_table)
        else _decode_loading_sprite(gamemem, background_table, 0)
    )
    if background is None:
        raise RuntimeError("loading background shape is unavailable")

    declared_count = int(gamemem.read_reloc_u32(ADDR_LOADING_SHAPE_COUNT))
    resource_count = hud_sprite_frame_count(gamemem, strip_table)
    strip_count = min(declared_count, resource_count)
    if strip_count < 2 or strip_count > 64:
        raise RuntimeError(
            f"invalid loading strip count {strip_count} "
            f"(global={declared_count}, resource={resource_count})"
        )
    strips = tuple(
        _decode_loading_sprite(gamemem, strip_table, frame)
        for frame in range(strip_count)
    )
    if any(strip is None for strip in strips):
        raise RuntimeError("one or more loading strip shapes failed to decode")

    palette_rgb = bytearray(
        previous.palette_rgb
        if previous is not None
        else _read_loading_palette_rgb(gamemem, background_table)
    )
    ramp = bytes(gamemem.read_reloc_bytes(ADDR_LOADING_UI_RAMP, 16 * 3))
    for offset, value in enumerate(ramp):
        channel6 = (value >> 2) if value > 63 else value
        palette_rgb[offset] = (channel6 & 0x3F) * 255 // 63

    clip_x = int(gamemem.read_reloc_i32(ADDR_LOADING_PANE + 0x04))
    clip_y = int(gamemem.read_reloc_i32(ADDR_LOADING_PANE + 0x08))
    strip_x = int(gamemem.read_reloc_i32(ADDR_LOADING_STRIP_X))
    strip_y = int(gamemem.read_reloc_i32(ADDR_LOADING_STRIP_Y))
    return LoadingScreenVisual(
        cache_key=(
            "complete",
            background.cache_key,
            tuple(strip.cache_key for strip in strips),
        ),
        background=background,
        strips=strips,
        palette_rgb=bytes(palette_rgb),
        clip_x=clip_x,
        clip_y=clip_y,
        strip_x=strip_x,
        strip_y=strip_y,
    )


def _read_loading_palette_rgb(gamemem, background_table):
    if background_table == 0:
        raise RuntimeError("loading background table is null")
    header = bytes(gamemem.read_runtime_bytes(background_table, 16))
    if header[:4] != b"1.10":
        raise RuntimeError("loading background is not a 1.10 resource")

    palette_offset = int(gamemem.read_runtime_u32(background_table + 0x0C))
    if palette_offset == 0:
        raise RuntimeError("loading background has no embedded palette")
    palette_address = background_table + palette_offset
    palette_count = int(gamemem.read_runtime_u32(palette_address))
    if palette_count <= 0 or palette_count > 256:
        raise RuntimeError(f"invalid loading palette count {palette_count}")

    records = bytes(
        gamemem.read_runtime_bytes(palette_address + 4, palette_count * 4)
    )
    palette_rgb = bytearray(256 * 3)
    for record in range(palette_count):
        source = record * 4
        palette_index = records[source]
        destination = palette_index * 3
        for channel in range(3):
            channel6 = records[source + 1 + channel] & 0x3F
            palette_rgb[destination + channel] = channel6 * 255 // 63
    return bytes(palette_rgb)


def _decode_loading_sprite(gamemem, shape_table, frame_index):
    # Python modules survive executable restarts even though modstate and the
    # OpenGL context do not. Loading art is mission-dependent, so never use
    # hud_sprites' process-global pointer-keyed cache here.
    sprite = decode_hud_sprite(
        gamemem,
        shape_table,
        frame_index,
        use_cache=False,
    )
    if sprite is None:
        return None
    return replace(
        sprite,
        cache_key=(
            "loading",
            int(shape_table),
            int(frame_index),
        ),
    )
