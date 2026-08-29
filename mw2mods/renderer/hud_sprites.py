from __future__ import annotations

import struct
from dataclasses import dataclass

from .assets import load_indexed_alpha_png


ADDR_RESOURCE_HASH_TABLE = 0x000A6D08
ADDR_RESOURCE_TABLE = 0x000AE9CC

_SPRITE_CACHE = {}
_ARCHIVE_FRAME_COUNTS = {}
_RESOURCE_SPRITE_CACHE = {}
_RESOURCE_SPRITE_DELTA = None
_HUD_SPRITE_GENERATION = 0


def begin_hud_sprite_generation():
    global _HUD_SPRITE_GENERATION, _RESOURCE_SPRITE_DELTA
    _HUD_SPRITE_GENERATION += 1
    _SPRITE_CACHE.clear()
    _ARCHIVE_FRAME_COUNTS.clear()
    _RESOURCE_SPRITE_CACHE.clear()
    _RESOURCE_SPRITE_DELTA = None
    return _HUD_SPRITE_GENERATION


@dataclass(frozen=True, slots=True)
class HudSprite:
    cache_key: object
    width: int
    height: int
    x_offset: int
    y_offset: int
    indexed_alpha: bytes


def load_indexed_alpha_hud_sprite(
    path,
    cache_key=None,
    x_offset=0,
    y_offset=0,
):
    width, height, indexed_alpha = load_indexed_alpha_png(path)
    return HudSprite(
        cache_key=cache_key if cache_key is not None else ("PNG", str(path)),
        width=width,
        height=height,
        x_offset=int(x_offset),
        y_offset=int(y_offset),
        indexed_alpha=indexed_alpha,
    )


class _RuntimeByteReader:
    def __init__(self, gamemem, address, chunk_size=0x10000):
        self.gamemem = gamemem
        self.address = int(address)
        self.chunk_size = int(chunk_size)
        self.chunk_start = -1
        self.chunk = b""

    def _ensure(self):
        if not (self.chunk_start <= self.address < self.chunk_start + len(self.chunk)):
            self.chunk_start = self.address
            self.chunk = bytes(
                self.gamemem.read_runtime_bytes(self.chunk_start, self.chunk_size)
            )

    def byte(self):
        self._ensure()
        value = self.chunk[self.address - self.chunk_start]
        self.address += 1
        return value

    def take(self, count):
        return bytes(self.byte() for _ in range(int(count)))


class _BytesReader:
    def __init__(self, data, address, end):
        self.data = data
        self.address = int(address)
        self.end = min(len(data), int(end))

    def byte(self):
        if self.address < 0 or self.address >= self.end:
            raise ValueError("shape frame data is truncated")
        value = self.data[self.address]
        self.address += 1
        return value

    def take(self, count):
        count = int(count)
        end = self.address + count
        if count < 0 or end > self.end:
            raise ValueError("shape frame data is truncated")
        value = bytes(self.data[self.address:end])
        self.address = end
        return value


def hud_sprite_frame_count(gamemem, shape_table):
    if shape_table == 0:
        return 0
    header = bytes(gamemem.read_runtime_bytes(shape_table, 8))
    if header[:4] != b"1.10":
        return 1
    shape_count = struct.unpack_from("<I", header, 4)[0]
    return int(shape_count) if 0 < shape_count <= 4096 else 0


def decode_hud_sprite(gamemem, shape_table, frame_index=0, use_cache=True):
    if shape_table == 0:
        return None
    frame_index = int(frame_index)
    cache_key = (
        "MEM",
        _HUD_SPRITE_GENERATION,
        int(gamemem.delta),
        int(shape_table),
        frame_index,
    )
    if use_cache:
        cached = _SPRITE_CACHE.get(cache_key)
        if cached is not None:
            return cached

    header = bytes(gamemem.read_runtime_bytes(shape_table, 24))
    if header[:4] == b"1.10":
        shape_count = struct.unpack_from("<I", header, 4)[0]
        if (
            shape_count <= 0
            or shape_count > 4096
            or frame_index < 0
            or frame_index >= shape_count
        ):
            return None
        # A 1.10 shape table stores 8-byte frame descriptors. The first dword
        # is the relative shape offset; a 4-byte stride would read the zero
        # second dword for every odd animation frame.
        shape_offset = int(
            gamemem.read_runtime_u32(shape_table + 8 + frame_index * 8)
        )
        if shape_offset == 0:
            return None
        shape = shape_table + shape_offset
    else:
        if frame_index != 0:
            return None
        shape = shape_table
    shape_header = bytes(gamemem.read_runtime_bytes(shape, 24))
    x_start, y_start, x_end, y_end = struct.unpack_from("<4i", shape_header, 8)
    width = x_end - x_start + 1
    height = y_end - y_start + 1
    if width <= 0 or height <= 0 or width > 4096 or height > 4096:
        return None

    pixels = bytearray(width * height * 2)
    reader = _RuntimeByteReader(gamemem, shape + 0x18)
    for row in range(height):
        x = 0
        while True:
            opcode = reader.byte()
            if opcode == 0:
                break
            if opcode == 1:
                x += reader.byte()
                continue
            if opcode & 1:
                count = (opcode - 1) // 2
                colors = reader.take(count)
            else:
                count = opcode // 2
                colors = bytes((reader.byte(),)) * count
            visible = max(0, min(count, width - x))
            for index in range(visible):
                destination = (row * width + x + index) * 2
                pixels[destination] = colors[index]
                pixels[destination + 1] = 0xFF
            x += count

    decoded = HudSprite(
        cache_key=cache_key,
        width=width,
        height=height,
        x_offset=x_start,
        y_offset=y_start,
        indexed_alpha=bytes(pixels),
    )
    if use_cache:
        _SPRITE_CACHE[cache_key] = decoded
    return decoded


def decode_hud_sprite_bytes(payload, resource_id, frame_index=0, use_cache=True):
    payload = bytes(payload)
    frame_index = int(frame_index)
    cache_key = (
        "SHP",
        _HUD_SPRITE_GENERATION,
        int(resource_id),
        frame_index,
    )
    if use_cache:
        cached = _SPRITE_CACHE.get(cache_key)
        if cached is not None:
            return cached
    if len(payload) < 24 or payload[:4] != b"1.10":
        return None
    shape_count = struct.unpack_from("<I", payload, 4)[0]
    if (
        shape_count <= 0
        or shape_count > 4096
        or frame_index < 0
        or frame_index >= shape_count
        or 8 + shape_count * 8 > len(payload)
    ):
        return None
    _ARCHIVE_FRAME_COUNTS[int(resource_id)] = int(shape_count)
    shape = int(struct.unpack_from("<I", payload, 8 + frame_index * 8)[0])
    if shape <= 0 or shape + 24 > len(payload):
        return None
    frame_end = len(payload)
    if frame_index + 1 < shape_count:
        next_shape = int(
            struct.unpack_from("<I", payload, 8 + (frame_index + 1) * 8)[0]
        )
        if shape < next_shape <= len(payload):
            frame_end = next_shape

    x_start, y_start, x_end, y_end = struct.unpack_from("<4i", payload, shape + 8)
    width = x_end - x_start + 1
    height = y_end - y_start + 1
    if width <= 0 or height <= 0 or width > 4096 or height > 4096:
        return None

    pixels = bytearray(width * height * 2)
    reader = _BytesReader(payload, shape + 24, frame_end)
    try:
        for row in range(height):
            x = 0
            while True:
                opcode = reader.byte()
                if opcode == 0:
                    break
                if opcode == 1:
                    x += reader.byte()
                    continue
                if opcode & 1:
                    count = (opcode - 1) // 2
                    colors = reader.take(count)
                else:
                    count = opcode // 2
                    colors = bytes((reader.byte(),)) * count
                visible = max(0, min(count, width - x))
                for index in range(visible):
                    destination = (row * width + x + index) * 2
                    pixels[destination] = colors[index]
                    pixels[destination + 1] = 0xFF
                x += count
    except (IndexError, ValueError, struct.error):
        return None

    decoded = HudSprite(
        cache_key=cache_key,
        width=width,
        height=height,
        x_offset=x_start,
        y_offset=y_start,
        indexed_alpha=bytes(pixels),
    )
    if use_cache:
        _SPRITE_CACHE[cache_key] = decoded
    return decoded


def preloaded_hud_sprite(resource_id, frame_index=0):
    return _SPRITE_CACHE.get(
        (
            "SHP",
            _HUD_SPRITE_GENERATION,
            int(resource_id),
            int(frame_index),
        )
    )


def preloaded_hud_frame_count(resource_id):
    return int(_ARCHIVE_FRAME_COUNTS.get(int(resource_id), 0))


def resolve_cached_shape(gamemem, resource_index):
    resource_table = int(gamemem.read_reloc_u32(ADDR_RESOURCE_TABLE))
    hash_table = int(gamemem.read_reloc_u32(ADDR_RESOURCE_HASH_TABLE))
    if resource_table == 0 or hash_table == 0:
        return 0
    key = int(gamemem.read_runtime_u32(resource_table))
    key_sum = sum((key >> shift) & 0xFF for shift in (0, 8, 16, 24))
    bucket = (int(resource_index) + key_sum) % 0x3F1
    entry = int(gamemem.read_runtime_u32(hash_table + bucket * 4))
    visited = set()
    while entry != 0 and len(visited) < 4096:
        if entry in visited:
            break
        visited.add(entry)
        entry_resource = int(gamemem.read_runtime_u16(entry + 0x00))
        entry_key = int(gamemem.read_runtime_u32(entry + 0x04))
        if entry_resource == (int(resource_index) & 0xFFFF) and entry_key == key:
            payload = entry + 0x14
            header = bytes(gamemem.read_runtime_bytes(payload, 4))
            return payload if header == b"1.10" else 0
        entry = int(gamemem.read_runtime_u32(entry + 0x08))
    return 0


def resource_hud_sprite(gamemem, resource_index):
    global _RESOURCE_SPRITE_DELTA
    delta = int(gamemem.delta)
    if delta != _RESOURCE_SPRITE_DELTA:
        _RESOURCE_SPRITE_CACHE.clear()
        _RESOURCE_SPRITE_DELTA = delta
    resource_index = int(resource_index)
    sprite = _RESOURCE_SPRITE_CACHE.get(resource_index)
    if sprite is not None:
        return sprite
    shape = resolve_cached_shape(gamemem, resource_index)
    sprite = (
        decode_hud_sprite(gamemem, shape)
        if shape
        else preloaded_hud_sprite(resource_index)
    )
    if sprite is not None:
        _RESOURCE_SPRITE_CACHE[resource_index] = sprite
    return sprite
