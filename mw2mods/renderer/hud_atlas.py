from __future__ import annotations

import math
from array import array
from dataclasses import dataclass
from functools import lru_cache

import numpy as np


TARGET_CARET_DOWN = 4
TARGET_CARET_LEFT = 5
TARGET_CARET_RIGHT = 6
TARGET_CARET_UP = 7
TARGET_CARET_BY_DIRECTION = {
    "down": TARGET_CARET_DOWN,
    "left": TARGET_CARET_LEFT,
    "right": TARGET_CARET_RIGHT,
    "up": TARGET_CARET_UP,
}
TARGET_BRACKET_TOP_LEFT = 8
TARGET_BRACKET_TOP_RIGHT = 9
TARGET_BRACKET_BOTTOM_LEFT = 10
TARGET_BRACKET_BOTTOM_RIGHT = 11
TARGET_NAV_CIRCLE = 12

COMPASS_STROKE = 1.75
TARGET_CARET_STROKE = 3.0
COMPASS_PALETTE_INDEX = 0x0A
TARGET_PALETTE_INDEX = 0x0E
ATLAS_PAD = 1
GUTTER_TEXELS = 2
BRACKET_SUPERSAMPLES = 8
BRACKET_LOGICAL_SIZE = 9.0
BRACKET_MINIMUM_BOX_SIZE = 21.0


@dataclass(frozen=True, slots=True)
class HudAtlasEntry:
    offset_x: int
    offset_y: int
    draw_width: int
    draw_height: int


@dataclass(frozen=True, slots=True)
class HudAtlas:
    width: int
    height: int
    indexed_alpha: bytes
    vertex_bytes: bytes
    entries: tuple[HudAtlasEntry, ...]
    size_key: tuple[int, ...]


_COMPASS_CARETS = (
    (10, 6, ((0.75, 0.75), (5.0, 5.25), (9.25, 0.75))),
    (6, 10, ((5.25, 0.75), (0.75, 5.0), (5.25, 9.25))),
    (6, 10, ((0.75, 0.75), (5.25, 5.0), (0.75, 9.25))),
    (10, 6, ((0.75, 5.25), (5.0, 0.75), (9.25, 5.25))),
)
_TARGET_CARETS = (
    (25, 15, ((1.0, 1.0), (12.5, 14.25), (24.0, 1.0))),
    (15, 25, ((14.0, 1.0), (0.75, 12.5), (14.0, 24.0))),
    (15, 25, ((1.0, 1.0), (14.25, 12.5), (1.0, 24.0))),
    (25, 15, ((1.0, 14.0), (12.5, 0.75), (24.0, 14.0))),
)
_QUAD_LAYOUT = (
    (0.0, 0.0),
    (1.0, 0.0),
    (0.0, 1.0),
    (0.0, 1.0),
    (1.0, 0.0),
    (1.0, 1.0),
)


def _distance_to_segment(px, py, x0, y0, x1, y1):
    dx = x1 - x0
    dy = y1 - y0
    length_squared = dx * dx + dy * dy
    projection = np.clip(
        ((px - x0) * dx + (py - y0) * dy) / length_squared,
        0.0,
        1.0,
    )
    return np.hypot(
        px - (x0 + projection * dx),
        py - (y0 + projection * dy),
    )


def _rasterize_caret(definition, width, height, stroke):
    logical_width, logical_height, points = definition
    scale_x = width / logical_width
    scale_y = height / logical_height
    antialias_scale = 0.5 * (scale_x + scale_y)
    pixel_y, pixel_x = np.mgrid[0:height, 0:width]
    logical_x = (pixel_x + 0.5) / scale_x
    logical_y = (pixel_y + 0.5) / scale_y
    point_a, point_b, point_c = points
    distance = np.minimum(
        _distance_to_segment(logical_x, logical_y, *point_a, *point_b),
        _distance_to_segment(logical_x, logical_y, *point_b, *point_c),
    )
    signed_distance_pixels = (distance - stroke * 0.5) * antialias_scale
    return np.clip(0.5 - signed_distance_pixels, 0.0, 1.0)


def target_bracket_box_size(panel_scale):
    return int(math.floor(BRACKET_MINIMUM_BOX_SIZE * panel_scale + 0.5))


def _rasterize_bracket(size, box_size, flip_x, flip_y):
    samples = BRACKET_SUPERSAMPLES
    sample_y, sample_x = np.mgrid[0:size * samples, 0:size * samples]
    scale = size / BRACKET_LOGICAL_SIZE
    physical_x = (sample_x + 0.5) / samples - 0.5
    physical_y = (sample_y + 0.5) / samples - 0.5
    logical_x = physical_x / scale
    logical_y = physical_y / scale
    target_center = (box_size - 1.0) * 0.5
    hole_radius_squared = (box_size * 0.5) ** 2
    distance_squared = (
        (target_center - physical_x) ** 2
        + (target_center - physical_y) ** 2
    )
    # Preserve the one-pixel right-angle legs. The remaining inner edge is a
    # true arc centered on the target, so four corners form one circular hole.
    leg_end = BRACKET_LOGICAL_SIZE - 1.0
    inside_tip_planes = (logical_x < leg_end) & (logical_y < leg_end)
    inside = (
        ((logical_x < 0.5) & (logical_y < leg_end))
        | ((logical_y < 0.5) & (logical_x < leg_end))
        | (
            (distance_squared >= hole_radius_squared)
            & inside_tip_planes
        )
    )
    alpha = inside.reshape(size, samples, size, samples).mean(axis=(1, 3))
    if flip_x:
        alpha = np.fliplr(alpha)
    if flip_y:
        alpha = np.flipud(alpha)
    return alpha


def _rasterize_ring(diameter, logical_scale):
    pixel_y, pixel_x = np.mgrid[0:diameter, 0:diameter]
    center = (diameter - 1) * 0.5
    distance = np.hypot(pixel_x - center, pixel_y - center)
    outer_radius = max(1.0, diameter * 0.5 - 0.25)
    inner_radius = max(0.0, outer_radius - 3.0 * logical_scale)
    outer = np.clip(outer_radius + 0.5 - distance, 0.0, 1.0)
    inner = np.clip(distance - inner_radius + 0.5, 0.0, 1.0)
    return np.minimum(outer, inner)


def _indexed_alpha(alpha, palette):
    indexed_alpha = np.empty((*alpha.shape, 2), dtype=np.uint8)
    indexed_alpha[..., 0] = palette
    indexed_alpha[..., 1] = np.rint(alpha * 255.0).astype(np.uint8)
    return indexed_alpha


def _caret_offset(definition, width, height):
    logical_width, logical_height, points = definition
    apex_x, apex_y = points[1]
    return (
        -int(round(apex_x * width / logical_width)),
        -int(round(apex_y * height / logical_height)),
    )


def _pack(entries):
    atlas_height = max(alpha.shape[0] for alpha, _palette, _offset in entries)
    atlas_height += 2 * ATLAS_PAD
    atlas_width = sum(alpha.shape[1] + 2 * ATLAS_PAD for alpha, _p, _o in entries)
    atlas_width += GUTTER_TEXELS * (len(entries) - 1)
    atlas = np.zeros((atlas_height, atlas_width, 2), dtype=np.uint8)
    vertices = array("f")
    metadata = []
    entry_x = 0
    for alpha, palette, offset in entries:
        height, width = alpha.shape
        entry_y = (atlas_height - height - 2 * ATLAS_PAD) // 2
        content_x = entry_x + ATLAS_PAD
        content_y = entry_y + ATLAS_PAD
        atlas[content_y:content_y + height, content_x:content_x + width] = (
            _indexed_alpha(alpha, palette)
        )
        for unit_x, unit_y in _QUAD_LAYOUT:
            vertices.extend((
                unit_x,
                unit_y,
                (content_x + unit_x * width) / atlas_width,
                (content_y + unit_y * height) / atlas_height,
            ))
        metadata.append(HudAtlasEntry(*offset, width, height))
        entry_x += width + 2 * ATLAS_PAD + GUTTER_TEXELS
    return atlas, vertices.tobytes(), tuple(metadata)


@lru_cache(maxsize=16)
def _build_hud_atlas(
    compass_ten,
    compass_six,
    target_long,
    target_short,
    bracket_size,
    bracket_box_size,
    circle_size,
):
    entries = []
    for definition in _COMPASS_CARETS:
        logical_width, logical_height = definition[:2]
        width = compass_ten if logical_width == 10 else compass_six
        height = compass_ten if logical_height == 10 else compass_six
        entries.append((
            _rasterize_caret(definition, width, height, COMPASS_STROKE),
            COMPASS_PALETTE_INDEX,
            _caret_offset(definition, width, height),
        ))
    for target_index, definition in enumerate(_TARGET_CARETS):
        logical_width, logical_height = definition[:2]
        width = target_long if logical_width == 25 else target_short
        height = target_long if logical_height == 25 else target_short
        offset = (
            (-(width // 2), -height + 1),
            (0, -(height // 2)),
            (-width + 1, -(height // 2)),
            (-(width // 2), 0),
        )[target_index]
        entries.append((
            _rasterize_caret(definition, width, height, TARGET_CARET_STROKE),
            TARGET_PALETTE_INDEX,
            offset,
        ))
    for flip_x, flip_y, offset in (
        (False, False, (-bracket_size + 1, -bracket_size + 1)),
        (True, False, (0, -bracket_size + 1)),
        (False, True, (-bracket_size + 1, 0)),
        (True, True, (0, 0)),
    ):
        entries.append((
            _rasterize_bracket(
                bracket_size,
                bracket_box_size,
                flip_x,
                flip_y,
            ),
            TARGET_PALETTE_INDEX,
            offset,
        ))
    target_scale = circle_size / 13.0
    entries.append((
        _rasterize_ring(circle_size, target_scale),
        TARGET_PALETTE_INDEX,
        (-(circle_size // 2), int(round(target_scale))),
    ))
    atlas, vertex_bytes, metadata = _pack(entries)
    size_key = (
        compass_ten,
        compass_six,
        target_long,
        target_short,
        bracket_size,
        bracket_box_size,
        circle_size,
    )
    return HudAtlas(
        atlas.shape[1],
        atlas.shape[0],
        atlas.tobytes(),
        vertex_bytes,
        metadata,
        size_key,
    )


def hud_atlas(panel_scale, target_marker_scale):
    panel_scale = max(0.01, float(panel_scale))
    target_marker_scale = max(0.01, float(target_marker_scale))
    return _build_hud_atlas(
        int(math.ceil(10.0 * panel_scale)),
        int(math.ceil(6.0 * panel_scale)),
        int(math.ceil(25.0 * target_marker_scale)),
        int(math.ceil(15.0 * target_marker_scale)),
        int(math.ceil(BRACKET_LOGICAL_SIZE * panel_scale)),
        target_bracket_box_size(panel_scale),
        int(math.ceil(13.0 * target_marker_scale)),
    )
