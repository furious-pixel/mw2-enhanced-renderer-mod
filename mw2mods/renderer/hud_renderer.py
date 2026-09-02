import math
import struct
from array import array
from dataclasses import fields, is_dataclass

import moderngl

from . import compositor, scene_renderer
from .font_rendering import TextLayoutSlot
from .hud_cockpit import (
    ALTIMETER_LABEL_FONT_SIZE,
    ALTIMETER_TEXT_SLOT_BASE,
    ALTIMETER_TEXT_SLOT_COUNT,
    COMPASS_EDGE_CARET_GAP,
    COMPASS_LABEL_FONT_SIZE,
    COMPASS_LABEL_HORIZONTAL_SCALE,
    COMPASS_LONG_TICK_HEIGHT,
    COMPASS_TEXT_SLOT_BASE,
    COMPASS_TEXT_SLOT_COUNT,
    CockpitHudThrottleMeter,
    CockpitHudText,
    CockpitHudVerticalMeter,
    MFD_CAMERA_TEXT_SLOT_BASE,
    MFD_CAMERA_TEXT_SLOT_COUNT,
    STATIC_SHAPE_LEFT_EDGE,
    STATIC_SHAPE_RIGHT_EDGE,
    _shade_bands,
)
from .hud_layout import BASE_FONT_SIZE, HudLayoutContext
from .hud_atlas import (
    TARGET_BRACKET_BOTTOM_LEFT,
    TARGET_BRACKET_BOTTOM_RIGHT,
    TARGET_BRACKET_TOP_LEFT,
    TARGET_BRACKET_TOP_RIGHT,
)
from .hud_menus import MENU_HANDLER_COMMAND, MENU_HANDLER_USER_SYSTEMS
from .hud_radar import satellite_fov_lines
from .hud_sprites import HudSprite
from .projection import perspective_projection_info
from .scene_state import palette_color_float


HUD_PANEL_TEXT_SLOT_COUNT = (
    MFD_CAMERA_TEXT_SLOT_BASE + MFD_CAMERA_TEXT_SLOT_COUNT
)
OBJECTIVE_TEXT_ROW_COUNT = 128
HUD_BASE_WIDTH = 1024.0
HUD_BASE_HEIGHT = 768.0
HUD_FONT_SIZE = BASE_FONT_SIZE
TARGET_NAV_FONT_SIZE = 16
TARGET_NAV_TOP_OFFSET = -14.0


def _hud_layout(viewport_size):
    """Return the centered 4:3 HUD origin and height-derived position scale."""
    width, height = (max(1.0, float(value)) for value in viewport_size)
    scale = height / HUD_BASE_HEIGHT
    return (width - HUD_BASE_WIDTH * scale) * 0.5, scale


def _hud_x(value, origin_x, scale):
    return origin_x + float(value) * scale


def _hud_y(value, scale):
    return float(value) * scale


def _hud_clip_rect(rect, origin_x, scale):
    left, top, right, bottom = rect
    return (
        _hud_x(left, origin_x, scale),
        _hud_y(top, scale),
        _hud_x(right, origin_x, scale),
        _hud_y(bottom, scale),
    )


def _round_output_pixel(value):
    return int(math.floor(float(value) + 0.5))


def _pixel_border_geometry(
    rect,
    origin_x,
    origin_y,
    scale_x,
    scale_y,
):
    outer = (
        _round_output_pixel(origin_x + rect.left * scale_x),
        _round_output_pixel(origin_y + rect.top * scale_y),
        _round_output_pixel(origin_x + rect.right * scale_x),
        _round_output_pixel(origin_y + rect.bottom * scale_y),
    )
    outer_left, outer_top, outer_right, outer_bottom = outer
    border_width = max(1, _round_output_pixel(min(scale_x, scale_y)))
    border_width = min(
        border_width,
        (outer_right - outer_left) // 2,
        (outer_bottom - outer_top) // 2,
    )
    inner = (
        outer_left + border_width,
        outer_top + border_width,
        outer_right - border_width,
        outer_bottom - border_width,
    )
    return outer, inner


def _throttle_meter_pixel_geometry(
    meter,
    origin_x,
    origin_y,
    scale_x,
    scale_y,
):
    outer, inner = _pixel_border_geometry(
        meter,
        origin_x,
        origin_y,
        scale_x,
        scale_y,
    )
    inner_left, inner_top, inner_right, inner_bottom = inner
    reference_height = max(1, meter.bottom - meter.top - 2)
    output_height = max(0, inner_bottom - inner_top)
    fill_top = inner_top + _round_output_pixel(
        (meter.fill_top - meter.top - 1) * output_height / reference_height
    )
    fill_bottom = inner_top + _round_output_pixel(
        (meter.fill_bottom - meter.top - 1) * output_height / reference_height
    )
    if fill_bottom <= fill_top:
        fill_bottom = min(inner_bottom, fill_top + 1)
    return (
        outer,
        inner,
        (inner_left, fill_top, inner_right, fill_bottom),
    )


def _scene_projected_transform(
    viewport_size,
    camera,
    max_horizontal_fov,
    projection_info=None,
):
    width = max(1.0, float(viewport_size[0]))
    height = max(1.0, float(viewport_size[1]))
    native_focal = max(
        1.0,
        float((camera or {}).get("focal_length_pixels", 512.0)),
    )
    if (
        projection_info is None
        or projection_info.width != int(width)
        or projection_info.height != int(height)
        or not math.isclose(
            projection_info.native_focal_length_pixels,
            native_focal,
            rel_tol=1.0e-9,
        )
    ):
        projection_info = perspective_projection_info(
            width,
            height,
            focal_length_pixels=native_focal,
            max_horizontal_fov_degrees=max_horizontal_fov,
        )
    scale = projection_info.output_focal_length_pixels / native_focal
    return (
        width * 0.5 - HUD_BASE_WIDTH * 0.5 * scale,
        height * 0.5 - HUD_BASE_HEIGHT * 0.5 * scale,
        scale,
    )


class _ObjectiveRowTextSlots:
    __slots__ = ("label", "text", "status", "continuation")

    def __init__(self):
        self.label = TextLayoutSlot()
        self.text = TextLayoutSlot()
        self.status = TextLayoutSlot()
        self.continuation = TextLayoutSlot()


class _MenuPageTextSlots:
    __slots__ = ("title", "marker", "item_labels", "item_values")

    def __init__(self, item_count):
        self.title = TextLayoutSlot()
        self.marker = TextLayoutSlot()
        self.item_labels = [TextLayoutSlot() for _ in range(item_count)]
        self.item_values = [TextLayoutSlot() for _ in range(item_count)]

    def ensure_item_count(self, item_count):
        missing = int(item_count) - len(self.item_labels)
        if missing <= 0:
            return
        self.item_labels.extend(TextLayoutSlot() for _ in range(missing))
        self.item_values.extend(TextLayoutSlot() for _ in range(missing))


def _draw_overlay_rect(resources, left, top, right, bottom, color):
    resources.overlay_line_program["u_viewport_size"].value = resources.size
    resources.overlay_line_program["u_color"].value = (*color, 1.0)
    vertices = array(
        "f",
        (
            left,
            top,
            right,
            top,
            right,
            bottom,
            left,
            bottom,
            left,
            top,
        ),
    )
    resources.overlay_line_buffer.write(vertices.tobytes())
    resources.overlay_line_vao.render(mode=moderngl.LINE_STRIP)


def _fill_overlay_rect(resources, left, top, right, bottom, color):
    resources.overlay_line_program["u_viewport_size"].value = resources.size
    resources.overlay_line_program["u_color"].value = (*color, 1.0)
    vertices = array(
        "f",
        (
            left,
            top,
            right,
            top,
            left,
            bottom,
            right,
            bottom,
        ),
    )
    resources.overlay_line_buffer.write(vertices.tobytes())
    resources.overlay_line_vao.render(mode=moderngl.TRIANGLE_STRIP, vertices=4)


_RECT_VERTEX_LAYOUT = (
    (False, False, 0),
    (True, False, 1),
    (False, True, 2),
    (False, True, 2),
    (True, False, 1),
    (True, True, 3),
)


def _append_static_scale_rect(
    vertices,
    color_index,
    left,
    top,
    right,
    bottom,
):
    for use_right, use_bottom, _color in _RECT_VERTEX_LAYOUT:
        vertices.extend(
            (
                right if use_right else left,
                bottom if use_bottom else top,
                float(color_index),
            )
        )


_STATIC_HUD_SHAPE_RECTS = [
    ((0, 0, 6, 2, 0x0F),),
    ((0, 0, 6, 2, 0x06),),
    ((-3, 0, -1, 14, 0x0D), (-1, 0, 1, 14, 0x0E), (1, 0, 3, 14, 0x0D)),
    ((-3, 0, -1, 14, 0x09), (-1, 0, 1, 14, 0x0A), (1, 0, 3, 14, 0x09)),
    ((-1, -5, 1, 0, 0x0E), (-1, 14, 1, 19, 0x0E)),
    ((-1, -5, 1, 0, 0x0A), (-1, 14, 1, 19, 0x0A)),
]
_STATIC_HUD_SHAPE_RECTS = tuple(_STATIC_HUD_SHAPE_RECTS)


def _static_hud_vertex_data():
    vertices = array("f")
    for tick in range(144):
        top = 4 if tick % 2 == 0 else 0
        _append_static_scale_rect(
            vertices,
            0x0E,
            tick * 10,
            top,
            tick * 10 + 2,
            COMPASS_LONG_TICK_HEIGHT,
        )
    for tick in range(93):
        y = 3 + tick * 10
        major = tick % 2 == 0
        left = 40 if major else 43
        _append_static_scale_rect(vertices, 0x0D, left, y, 52, y + 1)
        _append_static_scale_rect(vertices, 0x0D, left, y + 2, 52, y + 3)
        _append_static_scale_rect(vertices, 0x0D, left, y + 1, left + 1, y + 2)
        _append_static_scale_rect(vertices, 0x0E, left + 1, y + 1, 51, y + 2)
        _append_static_scale_rect(vertices, 0x0D, 51, y + 1, 52, y + 2)
        _append_static_scale_rect(
            vertices, 0x0D, 39, y, 40 if major else 39, y + 3
        )
    shape_ranges = []
    for rects in _STATIC_HUD_SHAPE_RECTS:
        first = len(vertices) // 3
        for left, top, right, bottom, color_index in rects:
            _append_static_scale_rect(
                vertices, color_index, left, top, right, bottom
            )
        shape_ranges.append((first, len(rects) * 6))
    return vertices.tobytes(), tuple(shape_ranges)


STATIC_HUD_SCALE_VERTEX_BYTES, STATIC_HUD_SHAPE_RANGES = (
    _static_hud_vertex_data()
)
COMPASS_STATIC_TEXTS = tuple(
    CockpitHudText(
        COMPASS_TEXT_SLOT_BASE + label % COMPASS_TEXT_SLOT_COUNT,
        f"{(3 * (label + 1)) % 36:02d}",
        0x0E,
        50 + label * 60 + 0.5,
        13,
        "center",
        base_font_size=COMPASS_LABEL_FONT_SIZE,
        horizontal_scale=COMPASS_LABEL_HORIZONTAL_SCALE,
    )
    for label in range(24)
)
ALTIMETER_STATIC_TEXTS = tuple(
    CockpitHudText(
        ALTIMETER_TEXT_SLOT_BASE + label,
        str(200 - label * 5),
        0x0E,
        37,
        4.5 + label * 20,
        "right",
        "center",
        ALTIMETER_LABEL_FONT_SIZE,
    )
    for label in range(ALTIMETER_TEXT_SLOT_COUNT)
)


_HUD_RECT_VERTEX = struct.Struct("<6f")


class _HudRectWriter:
    __slots__ = ("data", "used")

    def __init__(self, initial_size=4096):
        self.data = bytearray(
            max(6 * _HUD_RECT_VERTEX.size, int(initial_size))
        )
        self.used = 0

    def reset(self):
        self.used = 0

    def _reserve(self, byte_count):
        offset = self.used
        required = offset + byte_count
        if required > len(self.data):
            size = len(self.data)
            while size < required:
                size *= 2
            self.data.extend(bytearray(size - len(self.data)))
        self.used = required
        return offset

    def rect(self, left, top, right, bottom, color):
        red, green, blue, alpha = color
        stride = _HUD_RECT_VERTEX.size
        offset = self._reserve(6 * stride)
        _HUD_RECT_VERTEX.pack_into(
            self.data, offset, left, top, red, green, blue, alpha
        )
        _HUD_RECT_VERTEX.pack_into(
            self.data, offset + stride, right, top, red, green, blue, alpha
        )
        _HUD_RECT_VERTEX.pack_into(
            self.data, offset + 2 * stride, left, bottom, red, green, blue, alpha
        )
        _HUD_RECT_VERTEX.pack_into(
            self.data, offset + 3 * stride, left, bottom, red, green, blue, alpha
        )
        _HUD_RECT_VERTEX.pack_into(
            self.data, offset + 4 * stride, right, top, red, green, blue, alpha
        )
        _HUD_RECT_VERTEX.pack_into(
            self.data, offset + 5 * stride, right, bottom, red, green, blue, alpha
        )

    def gradient(
        self,
        left,
        top,
        right,
        bottom,
        top_left,
        top_right,
        bottom_left,
        bottom_right,
    ):
        tlr, tlg, tlb, tla = top_left
        trr, trg, trb, tra = top_right
        blr, blg, blb, bla = bottom_left
        brr, brg, brb, bra = bottom_right
        stride = _HUD_RECT_VERTEX.size
        offset = self._reserve(6 * stride)
        _HUD_RECT_VERTEX.pack_into(
            self.data, offset, left, top, tlr, tlg, tlb, tla
        )
        _HUD_RECT_VERTEX.pack_into(
            self.data, offset + stride, right, top, trr, trg, trb, tra
        )
        _HUD_RECT_VERTEX.pack_into(
            self.data, offset + 2 * stride, left, bottom, blr, blg, blb, bla
        )
        _HUD_RECT_VERTEX.pack_into(
            self.data, offset + 3 * stride, left, bottom, blr, blg, blb, bla
        )
        _HUD_RECT_VERTEX.pack_into(
            self.data, offset + 4 * stride, right, top, trr, trg, trb, tra
        )
        _HUD_RECT_VERTEX.pack_into(
            self.data, offset + 5 * stride, right, bottom, brr, brg, brb, bra
        )

    def border(self, outer, inner, color):
        outer_left, outer_top, outer_right, outer_bottom = outer
        inner_left, inner_top, inner_right, inner_bottom = inner
        self.rect(outer_left, outer_top, outer_right, inner_top, color)
        self.rect(outer_left, inner_bottom, outer_right, outer_bottom, color)
        self.rect(outer_left, inner_top, inner_left, inner_bottom, color)
        self.rect(inner_right, inner_top, outer_right, inner_bottom, color)


def _render_hud_rect_vertices(resources, writer, viewport_size=None):
    required = writer.used
    if required > resources.overlay_rect_buffer_size:
        while resources.overlay_rect_buffer_size < required:
            resources.overlay_rect_buffer_size *= 2
        resources.overlay_rect_buffer.orphan(resources.overlay_rect_buffer_size)
    resources.overlay_rect_program["u_viewport_size"].value = (
        viewport_size or resources.size
    )
    vertex_view = memoryview(writer.data)[:required]
    try:
        resources.overlay_rect_buffer.write(vertex_view)
    finally:
        vertex_view.release()
    resources.overlay_rect_vao.render(
        mode=moderngl.TRIANGLES,
        vertices=required // _HUD_RECT_VERTEX.size,
    )


def _write_enhanced_meter_segment(
    writer,
    palette_rgb,
    left,
    top,
    right,
    bottom,
    base_index,
    shade_axis,
    dark_offset,
    light_offset,
    peak_offset,
    peak_position,
):
    if right <= left or bottom <= top:
        return
    edge_index = max(0, min(255, int(base_index) + dark_offset))
    light_index = max(0, min(255, int(base_index) + light_offset))
    peak_index = max(0, min(255, int(base_index) + peak_offset))
    edge_color = (*palette_color_float(palette_rgb, edge_index), 1.0)
    light_color = (*palette_color_float(palette_rgb, light_index), 1.0)
    peak_color = (*palette_color_float(palette_rgb, peak_index), 1.0)
    if shade_axis == "x":
        peak = _round_output_pixel(left + (right - left) * peak_position)
        writer.gradient(
            left, top, peak, bottom,
            light_color, peak_color, light_color, peak_color,
        )
        writer.gradient(
            peak, top, right, bottom,
            peak_color, edge_color, peak_color, edge_color,
        )
    else:
        peak = _round_output_pixel(top + (bottom - top) * peak_position)
        writer.gradient(
            left, top, right, peak,
            light_color, light_color, peak_color, peak_color,
        )
        writer.gradient(
            left, peak, right, bottom,
            peak_color, peak_color, edge_color, edge_color,
        )


def _write_native_vertical_meter_segment(
    writer,
    palette_rgb,
    left,
    top,
    right,
    bottom,
    base_index,
):
    if right <= left or bottom <= top:
        return
    width = int(right - left)
    first = width // 4
    second = width // 2
    third = width * 3 // 4
    writer.rect(
        left,
        top,
        left + first,
        bottom,
        (*palette_color_float(palette_rgb, int(base_index) - 1), 1.0),
    )
    writer.rect(
        left + first,
        top,
        left + second,
        bottom,
        (*palette_color_float(palette_rgb, int(base_index)), 1.0),
    )
    writer.rect(
        left + second,
        top,
        left + third,
        bottom,
        (*palette_color_float(palette_rgb, int(base_index) - 1), 1.0),
    )
    writer.rect(
        left + third,
        top,
        right,
        bottom,
        (*palette_color_float(palette_rgb, int(base_index) - 2), 1.0),
    )


def _fill_hud_rects(
    resources,
    rects,
    palette_rgb,
    scale_x,
    scale_y,
    viewport_size=None,
    *,
    meter_style,
    origin_x=0.0,
    origin_y=0.0,
    size_scale_x=None,
    size_scale_y=None,
    minimum_extent=0.0,
    clip_rect=None,
    outlines=False,
):
    if not rects:
        return
    meter_dark_offset = int(meter_style["meter_dark_offset"])
    meter_light_offset = int(meter_style["meter_light_offset"])
    meter_peak_offset = int(meter_style["meter_peak_offset"])
    meter_peak_position = float(meter_style["meter_peak_position"])
    writer = resources.overlay_rect_writer
    writer.reset()
    size_scale_x = scale_x if size_scale_x is None else size_scale_x
    size_scale_y = scale_y if size_scale_y is None else size_scale_y
    if clip_rect is None:
        clip_left = clip_top = clip_right = clip_bottom = None
    else:
        clip_left = _round_output_pixel(clip_rect[0])
        clip_top = _round_output_pixel(clip_rect[1])
        clip_right = _round_output_pixel(clip_rect[2])
        clip_bottom = _round_output_pixel(clip_rect[3])

    for rect in rects:
        rect_origin_x = origin_x
        rect_origin_y = origin_y
        rect_scale_x = scale_x
        rect_scale_y = scale_y
        if outlines:
            outer, inner = _pixel_border_geometry(
                rect,
                rect_origin_x,
                rect_origin_y,
                rect_scale_x,
                rect_scale_y,
            )
            color = (
                *palette_color_float(palette_rgb, rect.color_index),
                1.0,
            )
            writer.border(outer, inner, color)
            continue
        if isinstance(rect, CockpitHudVerticalMeter):
            left = _round_output_pixel(
                rect_origin_x + rect.left * rect_scale_x
            )
            top = _round_output_pixel(
                rect_origin_y + rect.top * rect_scale_y
            )
            right = _round_output_pixel(
                rect_origin_x + rect.right * rect_scale_x
            )
            split = _round_output_pixel(
                rect_origin_y + rect.split * rect_scale_y
            )
            bottom = _round_output_pixel(
                rect_origin_y + rect.bottom * rect_scale_y
            )
            if clip_left is not None:
                left = max(left, clip_left)
                top = max(top, clip_top)
                right = min(right, clip_right)
                bottom = min(bottom, clip_bottom)
            split = max(top, min(split, bottom))
            if right <= left or bottom <= top:
                continue
            if rect.enhanced:
                _write_enhanced_meter_segment(
                    writer,
                    palette_rgb,
                    left,
                    top,
                    right,
                    split,
                    rect.current_color_index,
                    "x",
                    meter_dark_offset,
                    meter_light_offset,
                    meter_peak_offset,
                    meter_peak_position,
                )
                _write_enhanced_meter_segment(
                    writer,
                    palette_rgb,
                    left,
                    split,
                    right,
                    bottom,
                    rect.remaining_color_index,
                    "x",
                    meter_dark_offset,
                    meter_light_offset,
                    meter_peak_offset,
                    meter_peak_position,
                )
            else:
                _write_native_vertical_meter_segment(
                    writer,
                    palette_rgb,
                    left,
                    top,
                    right,
                    split,
                    rect.current_color_index,
                )
                _write_native_vertical_meter_segment(
                    writer,
                    palette_rgb,
                    left,
                    split,
                    right,
                    bottom,
                    rect.remaining_color_index,
                )
            continue
        if isinstance(rect, CockpitHudThrottleMeter):
            outer, inner, fill = _throttle_meter_pixel_geometry(
                rect,
                rect_origin_x,
                rect_origin_y,
                rect_scale_x,
                rect_scale_y,
            )
            border_color = (
                *palette_color_float(palette_rgb, rect.border_color_index),
                1.0,
            )
            writer.border(outer, inner, border_color)

            left, top, right, bottom = fill
            base_index = int(rect.base_color_index)
            shade_axis = "x"
            if not rect.enhanced and right > left and bottom > top:
                for shade_left, shade_right, color_index in _shade_bands(
                    int(right - left),
                    base_index,
                ):
                    writer.rect(
                        left + shade_left,
                        top,
                        left + shade_right,
                        bottom,
                        (*palette_color_float(palette_rgb, color_index), 1.0),
                    )
                continue
        else:
            shade_axis = getattr(rect, "shade_axis", None)
        if shade_axis in ("x", "y") and not isinstance(
            rect,
            CockpitHudThrottleMeter,
        ):
            left = rect_origin_x + rect.left * rect_scale_x
            top = rect_origin_y + rect.top * rect_scale_y
            right = rect_origin_x + rect.right * rect_scale_x
            bottom = rect_origin_y + rect.bottom * rect_scale_y
            # Preserve fractional HUD motion through layout scaling, then snap
            # only the completed physical-pixel edges.
            left = _round_output_pixel(left)
            top = _round_output_pixel(top)
            right = _round_output_pixel(right)
            bottom = _round_output_pixel(bottom)
            if clip_left is not None:
                left = max(left, clip_left)
                top = max(top, clip_top)
                right = min(right, clip_right)
                bottom = min(bottom, clip_bottom)
            base_index = int(rect.base_color_index)
        if shade_axis in ("x", "y"):
            if right <= left or bottom <= top:
                continue
            _write_enhanced_meter_segment(
                writer,
                palette_rgb,
                left,
                top,
                right,
                bottom,
                base_index,
                shade_axis,
                meter_dark_offset,
                meter_light_offset,
                meter_peak_offset,
                meter_peak_position,
            )
            continue

        left = rect_origin_x + rect.left * rect_scale_x
        top = rect_origin_y + rect.top * rect_scale_y
        right = left + (rect.right - rect.left) * size_scale_x
        bottom = top + (rect.bottom - rect.top) * size_scale_y
        if rect.right > rect.left and right - left < minimum_extent:
            right = left + minimum_extent
        if rect.bottom > rect.top and bottom - top < minimum_extent:
            bottom = top + minimum_extent
        left = _round_output_pixel(left)
        top = _round_output_pixel(top)
        right = _round_output_pixel(right)
        bottom = _round_output_pixel(bottom)
        if clip_left is not None:
            left = max(left, clip_left)
            top = max(top, clip_top)
            right = min(right, clip_right)
            bottom = min(bottom, clip_bottom)
        if right <= left or bottom <= top:
            continue
        red, green, blue = palette_color_float(
            palette_rgb, rect.color_index
        )
        color = (red, green, blue, 1.0)
        writer.rect(
            left,
            top,
            right,
            bottom,
            color,
        )

    _render_hud_rect_vertices(resources, writer, viewport_size)


def _overlay_sprite_texture(resources, sprite):
    texture_key = _overlay_sprite_texture_key(sprite)
    texture = resources.overlay_sprite_textures.get(texture_key)
    if texture is not None:
        return texture
    texture = resources.ctx.texture(
        (sprite.width, sprite.height),
        components=2,
        data=sprite.indexed_alpha,
        alignment=1,
    )
    texture.filter = (moderngl.NEAREST, moderngl.NEAREST)
    texture.repeat_x = False
    texture.repeat_y = False
    resources.overlay_sprite_textures[texture_key] = texture
    return texture


def _overlay_sprite_texture_key(sprite):
    return (
        int(sprite.width),
        int(sprite.height),
        sprite.indexed_alpha,
    )


def preload_hud_sprites(resources, generation, *roots):
    if generation is None:
        return 0
    generation = tuple(generation)
    if resources.hud_texture_preload_generation == generation:
        return 0
    uploaded = 0
    seen = set()
    pending = list(roots)
    while pending:
        value = pending.pop()
        if value is None:
            continue
        if isinstance(value, HudSprite):
            if (
                _overlay_sprite_texture_key(value)
                not in resources.overlay_sprite_textures
            ):
                _overlay_sprite_texture(resources, value)
                uploaded += 1
            continue
        if isinstance(value, dict):
            pending.extend(value.values())
            continue
        if isinstance(value, (tuple, list, set, frozenset)):
            pending.extend(value)
            continue
        if not is_dataclass(value):
            continue
        identity = id(value)
        if identity in seen:
            continue
        seen.add(identity)
        pending.extend(getattr(value, field.name) for field in fields(value))
    resources.hud_texture_preload_generation = generation
    return uploaded


def _scissor_box(rect, viewport_size):
    left, top, right, bottom = rect
    viewport_width, viewport_height = viewport_size
    left = max(0, min(viewport_width, int(math.floor(left))))
    right = max(0, min(viewport_width, int(math.ceil(right))))
    top = max(0, min(viewport_height, int(math.floor(top))))
    bottom = max(0, min(viewport_height, int(math.ceil(bottom))))
    return left, viewport_height - bottom, right - left, bottom - top


def _draw_static_hud_range(resources, first, count, origin, scale, clip_rect):
    resources.hud_scale_program["u_viewport_size"].value = resources.size
    resources.hud_scale_program["u_origin"].value = origin
    resources.hud_scale_program["u_scale"].value = scale
    resources.palette_texture.use(location=3)
    resources.ctx.scissor = _scissor_box(clip_rect, resources.size)
    resources.hud_scale_vao.render(
        mode=moderngl.TRIANGLES, vertices=count, first=first
    )
    resources.ctx.scissor = None


def _snapped_static_hud_origin(panel, transform):
    origin_x = (
        transform.origin_x + panel.scale_origin_x * transform.scale_x
    )
    origin_y = (
        transform.origin_y + panel.scale_origin_y * transform.scale_y
    )
    return _round_output_pixel(origin_x), _round_output_pixel(origin_y)


def _draw_static_hud_scale(resources, panel, frame, transform):
    left, top, right, bottom = panel.reference_bounds
    if panel.panel_id == "compass_scaled":
        source_x = int(left - panel.scale_origin_x)
        group_start = -((-source_x) // 10)
        group_stop = (source_x + right - left - 1) // 10 + 1
        vertex_base, vertices_per_group = 0, 6
    else:
        draw_y = int(panel.scale_origin_y - top)
        group_start = max(0, -((draw_y + 3) // 10))
        group_stop = min(92, (bottom - top - draw_y - 4) // 10) + 1
        vertex_base, vertices_per_group = 144 * 6, 36
    group_count = group_stop - group_start
    if group_count > 0:
        _draw_static_hud_range(
            resources,
            vertex_base + group_start * vertices_per_group,
            group_count * vertices_per_group,
            _snapped_static_hud_origin(panel, transform),
            (transform.scale_x, transform.scale_y),
            frame.rect(panel.reference_bounds),
        )


def _draw_static_hud_shape(resources, entry, transform):
    shape_index, x, y, clip_rect, _edge_attachment = entry
    first, count = STATIC_HUD_SHAPE_RANGES[shape_index]
    origin_x, origin_y = transform.point(x, y)
    origin = (
        _round_output_pixel(origin_x),
        _round_output_pixel(origin_y),
    )
    _draw_static_hud_range(
        resources,
        first,
        count,
        origin,
        (transform.scale_x, transform.scale_y),
        transform.rect(clip_rect),
    )


def _draw_static_hud_caret(resources, entry, transform):
    shape_index, x, y, clip_rect, edge_attachment = entry
    atlas_index = shape_index - len(STATIC_HUD_SHAPE_RANGES)
    atlas_entry = resources.hud_atlas.entries[atlas_index]
    anchor_x, anchor_y = transform.point(x, y)
    anchor_x = int(math.floor(anchor_x + 0.5))
    anchor_y = int(math.floor(anchor_y + 0.5))
    if edge_attachment != 0:
        gap = max(
            1,
            int(math.floor(
                COMPASS_EDGE_CARET_GAP * transform.scale_x + 0.5
            )),
        )
        if edge_attachment == STATIC_SHAPE_LEFT_EDGE:
            x0 = anchor_x - gap - atlas_entry.draw_width
        elif edge_attachment == STATIC_SHAPE_RIGHT_EDGE:
            x0 = anchor_x + gap
    else:
        x0 = anchor_x + atlas_entry.offset_x
    y0 = anchor_y + atlas_entry.offset_y
    _draw_hud_atlas_quad(
        resources,
        atlas_index,
        x0,
        y0,
        transform.rect(clip_rect),
    )


def _draw_hud_atlas_quad(
    resources,
    atlas_index,
    x0,
    y0,
    clip_rect,
    color_override=None,
    viewport_size=None,
):
    atlas_entry = resources.hud_atlas.entries[atlas_index]
    viewport_size = viewport_size or resources.size
    scissor = _scissor_box(clip_rect, viewport_size)
    if scissor[2] <= 0 or scissor[3] <= 0:
        return
    resources.ctx.scissor = scissor
    resources.overlay_sprite_program["u_override_index"].value = (
        -1 if color_override is None else int(color_override)
    )
    resources.overlay_sprite_program["u_vertex_origin"].value = (x0, y0)
    resources.overlay_sprite_program["u_vertex_scale"].value = (
        atlas_entry.draw_width,
        atlas_entry.draw_height,
    )
    resources.hud_atlas_vao.render(
        mode=moderngl.TRIANGLES,
        vertices=6,
        first=atlas_index * 6,
    )
    resources.ctx.scissor = None


def _prepare_hud_atlas(resources, viewport_size=None):
    resources.overlay_sprite_program["u_viewport_size"].value = (
        viewport_size or resources.size
    )
    resources.overlay_sprite_program["u_override_index"].value = -1
    resources.overlay_sprite_program["u_brightness"].value = 1.0
    resources.hud_atlas_texture.use(location=2)
    resources.palette_texture.use(location=3)


def _draw_hud_atlas_marker(
    resources,
    marker,
    anchor_x,
    anchor_y,
    clip_rect,
    viewport_size=None,
):
    atlas_entry = resources.hud_atlas.entries[marker.shape_index]
    anchor_x = int(math.floor(anchor_x + 0.5))
    anchor_y = int(math.floor(anchor_y + 0.5))
    _draw_hud_atlas_quad(
        resources,
        marker.shape_index,
        anchor_x + atlas_entry.offset_x,
        anchor_y + atlas_entry.offset_y,
        clip_rect,
        marker.color_index,
        viewport_size,
    )


def _draw_target_bracket(
    resources,
    bracket,
    scene_origin_x,
    scene_origin_y,
    scene_scale,
    clip_rect,
):
    center_x = _round_output_pixel(
        scene_origin_x + bracket.x * scene_scale
    )
    center_y = _round_output_pixel(
        scene_origin_y + bracket.y * scene_scale
    )
    radius = max(0, int(math.floor(bracket.radius * scene_scale)))
    for shape_index, x_direction, y_direction in (
        (TARGET_BRACKET_TOP_LEFT, -1, -1),
        (TARGET_BRACKET_TOP_RIGHT, 1, -1),
        (TARGET_BRACKET_BOTTOM_LEFT, -1, 1),
        (TARGET_BRACKET_BOTTOM_RIGHT, 1, 1),
    ):
        atlas_entry = resources.hud_atlas.entries[shape_index]
        _draw_hud_atlas_quad(
            resources,
            shape_index,
            center_x + x_direction * radius + atlas_entry.offset_x,
            center_y + y_direction * radius + atlas_entry.offset_y,
            clip_rect,
            bracket.color_index,
        )


def _draw_target_nav_label(
    resources,
    marker,
    anchor_x,
    anchor_y,
    marker_scale,
    palette_rgb,
    clip_rect,
):
    if not marker.label:
        return
    font_size = max(1, int(round(TARGET_NAV_FONT_SIZE * marker_scale)))
    text_width, _text_height = resources.font_renderer.measure(
        marker.label,
        size_px=font_size,
        cache_slot=resources.target_nav_text_slot,
    )
    resources.ctx.scissor = _scissor_box(clip_rect, resources.size)
    resources.font_renderer.draw_text(
        marker.label,
        math.floor(anchor_x - text_width * 0.5 + 0.5),
        math.floor(anchor_y + TARGET_NAV_TOP_OFFSET * marker_scale + 0.5),
        resources.size,
        color=(*palette_color_float(palette_rgb, marker.color_index), 1.0),
        size_px=font_size,
        cache_slot=resources.target_nav_text_slot,
    )
    resources.ctx.scissor = None


def _draw_panel_text(
    resources,
    entry,
    transform,
    layout,
    palette_rgb,
    offset_x=0.0,
    offset_y=0.0,
):
    cache_slot = resources.hud_panel_text_slots[entry.panel_index]
    text_x, text_y = transform.point(
        entry.x + offset_x,
        entry.y + offset_y,
    )
    font_size = layout.font_size_for(entry.base_font_size)
    if (
        entry.horizontal_alignment != "left"
        or entry.vertical_alignment != "top"
    ):
        text_width, text_height = resources.font_renderer.measure(
            entry.text,
            size_px=font_size,
            cache_slot=cache_slot,
        )
        text_width *= entry.horizontal_scale
    if entry.horizontal_alignment == "center":
        text_x -= text_width * 0.5
    elif entry.horizontal_alignment == "right":
        text_x -= text_width
    if entry.vertical_alignment == "center":
        text_y -= text_height * 0.5
    color = palette_color_float(palette_rgb, entry.color_index)
    resources.font_renderer.draw_text(
        entry.text,
        text_x,
        text_y,
        resources.size,
        color=(*color, 1.0),
        size_px=font_size,
        cache_slot=cache_slot,
        horizontal_scale=entry.horizontal_scale,
    )


def _preload_static_text(resources, cache_index, texts, layout):
    font_size = layout.font_size_for(texts[0].base_font_size)
    if resources.hud_static_text_sizes[cache_index] == font_size:
        return
    for entry in texts:
        resources.font_renderer.measure(
            entry.text,
            size_px=font_size,
            cache_slot=resources.hud_panel_text_slots[entry.panel_index],
        )
    resources.hud_static_text_sizes[cache_index] = font_size


def _draw_static_hud_text(resources, panel, transform, layout, palette_rgb):
    left, top, right, bottom = panel.reference_bounds
    if panel.panel_id == "compass_scaled":
        source_x = int(left - panel.scale_origin_x)
        texts = COMPASS_STATIC_TEXTS
        cache_index = 0
        text_index = -((50 - source_x) // 60)
        text_stop = (source_x + right - left - 51) // 60 + 1
    else:
        draw_y = int(panel.scale_origin_y - top)
        first_tick = max(0, -((draw_y + 3) // 10))
        last_tick = min(92, (bottom - top - draw_y - 4) // 10)
        texts = ALTIMETER_STATIC_TEXTS
        cache_index = 1
        text_index = (first_tick + 1) // 2
        text_stop = last_tick // 2 + 1
    _preload_static_text(resources, cache_index, texts, layout)
    snapped_origin_x, snapped_origin_y = _snapped_static_hud_origin(
        panel,
        transform,
    )
    reference_origin_x = (
        snapped_origin_x - transform.origin_x
    ) / transform.scale_x
    reference_origin_y = (
        snapped_origin_y - transform.origin_y
    ) / transform.scale_y
    while text_index < text_stop:
        _draw_panel_text(
            resources,
            texts[text_index],
            transform,
            layout,
            palette_rgb,
            reference_origin_x,
            reference_origin_y,
        )
        text_index += 1


def _draw_overlay_sprite(
    resources,
    sprite,
    x,
    y,
    clip_rect,
    scale_x=1.0,
    scale_y=1.0,
    color_override=None,
    viewport_size=None,
    draw_width=None,
    draw_height=None,
    source_x=0.0,
    source_y=0.0,
    repeat_x=False,
    repeat_y=False,
    origin_x=0.0,
    size_scale_x=1.0,
    size_scale_y=1.0,
    origin_y=0.0,
    snap_to_pixels=False,
):
    if sprite is None:
        return
    x0 = origin_x + float(x) * scale_x + float(sprite.x_offset) * size_scale_x
    y0 = origin_y + float(y) * scale_y + float(sprite.y_offset) * size_scale_y
    if snap_to_pixels:
        x0 = math.floor(x0 + 0.5)
        y0 = math.floor(y0 + 0.5)
    native_width = (
        float(sprite.width) if draw_width is None else float(draw_width)
    )
    native_height = (
        float(sprite.height) if draw_height is None else float(draw_height)
    )
    x1 = x0 + native_width * size_scale_x
    y1 = y0 + native_height * size_scale_y
    u0 = float(source_x) / float(sprite.width)
    v0 = float(source_y) / float(sprite.height)
    u1 = (float(source_x) + native_width) / float(sprite.width)
    v1 = (float(source_y) + native_height) / float(sprite.height)
    vertices = array(
        "f",
        (
            x0, y0, u0, v0,
            x1, y0, u1, v0,
            x0, y1, u0, v1,
            x0, y1, u0, v1,
            x1, y0, u1, v0,
            x1, y1, u1, v1,
        ),
    )
    viewport_size = viewport_size or resources.size
    scissor_x, scissor_y, scissor_width, scissor_height = _scissor_box(
        clip_rect, viewport_size
    )
    if scissor_width <= 0 or scissor_height <= 0:
        return

    resources.ctx.scissor = (
        scissor_x,
        scissor_y,
        scissor_width,
        scissor_height,
    )
    try:
        resources.overlay_sprite_program["u_viewport_size"].value = viewport_size
        resources.overlay_sprite_program["u_override_index"].value = (
            -1 if color_override is None else int(color_override)
        )
        resources.overlay_sprite_program["u_brightness"].value = 1.0
        resources.overlay_sprite_buffer.write(vertices.tobytes())
        texture = _overlay_sprite_texture(resources, sprite)
        texture.repeat_x = bool(repeat_x)
        texture.repeat_y = bool(repeat_y)
        texture.use(location=2)
        resources.palette_texture.use(location=3)
        resources.overlay_sprite_vao.render(mode=moderngl.TRIANGLES)
    finally:
        resources.ctx.scissor = None


def _draw_panel_sprite(resources, entry, transform, native_size=False):
    size_scale_x = 1.0 if native_size else transform.scale_x
    size_scale_y = 1.0 if native_size else transform.scale_y
    _draw_overlay_sprite(
        resources,
        entry.sprite,
        entry.x,
        entry.y,
        transform.rect(entry.clip_rect),
        transform.scale_x,
        transform.scale_y,
        color_override=entry.color_override,
        draw_width=entry.draw_width,
        draw_height=entry.draw_height,
        source_x=entry.source_x,
        source_y=entry.source_y,
        repeat_x=entry.repeat_x,
        repeat_y=entry.repeat_y,
        origin_x=transform.origin_x,
        origin_y=transform.origin_y,
        size_scale_x=size_scale_x,
        size_scale_y=size_scale_y,
    )


def _draw_radar_lines(
    resources,
    lines,
    palette_rgb,
    scale_x,
    scale_y,
    stroke_width,
    viewport_size=None,
    origin_x=0.0,
    origin_y=0.0,
):
    if not lines:
        return
    vertices = array("f")
    fringe = max(0.25, float(stroke_width) * 0.5) + 1.0
    for line in lines:
        x0 = origin_x + float(line.x0) * scale_x
        y0 = origin_y + float(line.y0) * scale_y
        x1 = origin_x + float(line.x1) * scale_x
        y1 = origin_y + float(line.y1) * scale_y
        dx = x1 - x0
        dy = y1 - y0
        length = math.hypot(dx, dy)
        if length <= 1e-6:
            continue
        nx = -dy * fringe / length
        ny = dx * fringe / length
        red, green, blue = palette_color_float(
            palette_rgb, line.color_index
        )
        color = (red, green, blue, 1.0)
        corners = (
            (x0 + nx, y0 + ny),
            (x1 + nx, y1 + ny),
            (x0 - nx, y0 - ny),
            (x0 - nx, y0 - ny),
            (x1 + nx, y1 + ny),
            (x1 - nx, y1 - ny),
        )
        for x, y in corners:
            vertices.extend((x, y, x0, y0, x1, y1, *color))
    if not vertices:
        return
    vertex_bytes = vertices.tobytes()
    required = len(vertex_bytes)
    if required > resources.radar_line_buffer_size:
        while resources.radar_line_buffer_size < required:
            resources.radar_line_buffer_size *= 2
        resources.radar_line_buffer.orphan(resources.radar_line_buffer_size)
    resources.radar_line_program["u_viewport_size"].value = viewport_size or resources.size
    resources.radar_line_program["u_stroke_width"].value = float(stroke_width)
    resources.radar_line_buffer.write(vertex_bytes)
    resources.radar_line_vao.render(
        mode=moderngl.TRIANGLES,
        vertices=len(vertices) // 10,
    )


def _draw_radar_ellipse(
    resources,
    ellipse,
    palette_rgb,
    scale_x,
    scale_y,
    stroke_width,
    viewport_size=None,
    origin_x=0.0,
    origin_y=0.0,
):
    if ellipse is None:
        return
    center_x = origin_x + float(ellipse.center_x) * scale_x
    center_y = origin_y + float(ellipse.center_y) * scale_y
    radius_x = float(ellipse.radius_x) * scale_x
    radius_y = float(ellipse.radius_y) * scale_y
    if radius_x <= 0.5 or radius_y <= 0.5:
        return
    fringe = max(0.25, float(stroke_width) * 0.5) + 1.0
    left = center_x - radius_x - fringe
    top = center_y - radius_y - fringe
    right = center_x + radius_x + fringe
    bottom = center_y + radius_y + fringe
    vertices = array(
        "f",
        (
            left, top,
            right, top,
            left, bottom,
            left, bottom,
            right, top,
            right, bottom,
        ),
    )
    color = palette_color_float(palette_rgb, ellipse.color_index)
    resources.radar_ellipse_program["u_viewport_size"].value = (
        viewport_size or resources.size
    )
    resources.radar_ellipse_program["u_center"].value = (center_x, center_y)
    resources.radar_ellipse_program["u_radii"].value = (radius_x, radius_y)
    resources.radar_ellipse_program["u_color"].value = (*color, 1.0)
    resources.radar_ellipse_program["u_stroke_width"].value = float(stroke_width)
    resources.radar_ellipse_buffer.write(vertices.tobytes())
    resources.radar_ellipse_vao.render(mode=moderngl.TRIANGLES)


def _use_premultiplied_blend(resources):
    resources.ctx.enable(moderngl.BLEND)
    resources.ctx.blend_func = moderngl.ONE, moderngl.ONE_MINUS_SRC_ALPHA


def _use_transparent_alpha_replace_blend(resources):
    resources.ctx.enable(moderngl.BLEND)
    resources.ctx.blend_func = (
        moderngl.ONE,
        moderngl.ONE_MINUS_SRC_ALPHA,
        moderngl.ONE,
        moderngl.ZERO,
    )


def _use_no_blend(resources):
    resources.ctx.disable(moderngl.BLEND)


def _hud_panel(cockpit_hud, panel_id):
    for panel in cockpit_hud.panels:
        if panel.panel_id == panel_id:
            return panel
    raise AssertionError(f"missing HUD panel {panel_id!r}")


def _hud_view_rect_pixels(view, transform):
    left, top, right, bottom = view.pane_rect
    pixel_rect = transform.rect((left, top, right + 1, bottom + 1))
    pixel_left, pixel_top, pixel_right, pixel_bottom = (
        int(round(value)) for value in pixel_rect
    )
    pixel_right = max(pixel_left + 1, pixel_right)
    pixel_bottom = max(pixel_top + 1, pixel_bottom)
    return pixel_left, pixel_top, pixel_right, pixel_bottom


def _hud_view_metrics(view, transform):
    pixel_rect = _hud_view_rect_pixels(view, transform)
    return (
        pixel_rect,
        (
            pixel_rect[2] - pixel_rect[0],
            pixel_rect[3] - pixel_rect[1],
        ),
        (
            view.pane_rect[2] - view.pane_rect[0] + 1,
            view.pane_rect[3] - view.pane_rect[1] + 1,
        ),
    )


def hud_camera_view_metrics(
    cockpit_hud,
    view,
    viewport_size,
    layout_settings,
):
    """Return final output and logical pane sizes without requiring GL state."""
    layout = HudLayoutContext(viewport_size, layout_settings)
    panel_id = "target" if view.kind == "target" else "mfd"
    panel = _hud_panel(cockpit_hud, panel_id)
    transform = layout.final_frame_transform(panel)
    return _hud_view_metrics(view, transform)


def _render_hud_camera_target(
    resources,
    target,
    target_size,
    logical_size,
    camera,
    fog_distance_world,
    partitions,
    background=None,
    imaging_active=0,
    draw_static=True,
):
    scene_renderer._ensure_scene_render_target(resources, target, target_size)
    render_size = target.render_size
    background = background or {}
    scene_renderer._clear_scene_target(
        resources,
        target.fbo,
        background.get("clear_color", (0.0, 0.0, 0.0)),
        render_size,
    )
    if background.get("sky_visible"):
        scene_renderer._draw_sky_to_scene(
            resources,
            camera,
            background.get("sky_palette_index", 0),
            background.get("draw_gradient", False),
            background.get("ground_palette_index", 0),
            background.get("gradient_height", 0),
            framebuffer=target.fbo,
            render_size=render_size,
            projection_size=logical_size,
        )
    return scene_renderer._draw_geometry_to_scene(
        resources,
        camera,
        fog_distance_world,
        imaging_active=imaging_active,
        framebuffer=target.fbo,
        render_size=render_size,
        projection_size=logical_size,
        draw_static=draw_static,
        depth_func="<",
        dynamic_resources=partitions,
    )


def _render_camera_view(
    resources,
    cockpit_hud,
    view,
    target,
    fog_distance_world,
    partitions,
    layout_settings,
    **options,
):
    if view is None:
        return None
    _pixel_rect, target_size, logical_size = hud_camera_view_metrics(
        cockpit_hud,
        view,
        resources.size,
        layout_settings,
    )
    try:
        _render_hud_camera_target(
            resources,
            target,
            target_size,
            logical_size,
            view.camera,
            fog_distance_world,
            partitions,
            **options,
        )
        return _hud_view_key(view)
    finally:
        resources.ctx.disable(moderngl.CULL_FACE)
        resources.ctx.screen.use()
        resources.ctx.enable_only(moderngl.NOTHING)


def _draw_hud_camera_view_border(resources, view, color, transform):
    outer_left, outer_top, outer_right, outer_bottom = (
        _hud_view_rect_pixels(view, transform)
    )
    border = max(1, int(round(min(transform.scale_x, transform.scale_y))))
    inner_left = min(outer_right, outer_left + border)
    inner_top = min(outer_bottom, outer_top + border)
    inner_right = max(outer_left, outer_right - border)
    inner_bottom = max(outer_top, outer_bottom - border)
    for rect in (
        (outer_left, outer_top, outer_right, inner_top),
        (outer_left, inner_bottom, outer_right, outer_bottom),
        (outer_left, inner_top, inner_left, inner_bottom),
        (inner_right, inner_top, outer_right, inner_bottom),
    ):
        _fill_overlay_rect(resources, *rect, color)


def render_prepared_target_camera_view(
    resources,
    cockpit_hud,
    target_view,
    fog_distance_world,
    layout_settings,
):
    resources.target_view_ready = False
    resources.target_view_key = None
    if target_view is None:
        return

    display_mode = int(target_view.display_mode)
    resources.target_view_key = _render_camera_view(
        resources,
        cockpit_hud,
        target_view,
        resources.target_view_target,
        fog_distance_world,
        (
            (resources.geometry_resources["target"],)
            if display_mode in (1, 2)
            else ()
        ),
        layout_settings,
        imaging_active=1 if display_mode == 1 else 0,
        draw_static=False,
    )
    resources.target_view_ready = resources.target_view_key is not None


def refresh_target_camera_overlay(
    resources,
    cockpit_hud,
    target_view,
    palette_rgb,
    layout_settings,
):
    if target_view is None:
        return
    layout = HudLayoutContext(resources.size, layout_settings)
    panel = _hud_panel(cockpit_hud, "target")
    transform = layout.frame_transform(panel)
    pixel_rect = _hud_view_rect_pixels(target_view, transform)
    try:
        resources.overlay_fbo.use()
        resources.overlay_fbo.viewport = (0, 0, resources.size[0], resources.size[1])
        resources.ctx.enable_only(moderngl.NOTHING)
        _draw_hud_camera_view(resources,
            target_view,
            resources.target_view_target,
            resources.target_view_ready,
            resources.target_view_key,
            pixel_rect,
        )
        _draw_hud_camera_view_border(resources,
            target_view,
            palette_color_float(palette_rgb, 0x08),
            transform,
        )
    finally:
        resources.ctx.screen.use()
        resources.ctx.enable_only(moderngl.NOTHING)


def render_hud_camera_views(
    resources,
    cockpit_hud,
    fog_distance_world,
    scene_background=None,
    *,
    layout_settings,
):
    resources.target_view_ready = False
    resources.target_view_key = None
    resources.mfd_view_ready = False
    if cockpit_hud is None:
        return

    resources.mfd_view_ready = _render_camera_view(
        resources,
        cockpit_hud,
        cockpit_hud.mfd_view,
        resources.mfd_view_target,
        fog_distance_world,
        (
            resources.geometry_resources["scene"],
            resources.geometry_resources["entity"],
        ),
        layout_settings,
        background=scene_background,
    ) is not None


def _hud_view_key(view):
    return (
        view.kind,
        int(getattr(view, "display_mode", 0)),
        getattr(view, "target_id", None),
        tuple(view.pane_rect),
        tuple(view.camera.get("position_fixed", ())),
    )


def _draw_hud_camera_view(
    resources,
    view,
    target,
    ready,
    rendered_key=None,
    pixel_rect=None,
):
    if view is None or not ready or target.texture is None:
        return
    if rendered_key is not None and rendered_key != _hud_view_key(view):
        return
    if pixel_rect is None:
        raise AssertionError("camera view requires a resolved panel rectangle")
    left, top, right, bottom = pixel_rect
    if view.mirror_horizontal:
        texture_left, texture_right = 1.0, 0.0
    else:
        texture_left, texture_right = 0.0, 1.0
    vertices = array(
        "f",
        (
            left, top, texture_left, 1.0,
            left, bottom, texture_left, 0.0,
            right, top, texture_right, 1.0,
            right, bottom, texture_right, 0.0,
        ),
    )
    resources.camera_view_blit_program["u_viewport_size"].value = resources.size
    resources.camera_view_blit_program[
        "u_resolve_satellite_damage"
    ].value = False
    resources.camera_view_blit_buffer.write(vertices.tobytes())
    target.texture.use(location=3)
    _use_no_blend(resources)
    resources.camera_view_blit_vao.render(
        mode=moderngl.TRIANGLE_STRIP,
        vertices=4,
    )


def _draw_radar_sprites(
    resources,
    entries,
    satellite,
    transform,
    scale_x,
    scale_y,
    viewport_size,
    origin_x,
    origin_y,
    artwork_scale,
):
    size_scale = 1.0 if satellite else artwork_scale
    for entry in entries:
        output_space = satellite and bool(
            getattr(entry, "output_space", False)
        )
        if output_space:
            clip_rect = entry.clip_rect
            entry_scale_x = 1.0
            entry_scale_y = 1.0
            entry_origin_x = 0.0
            entry_origin_y = 0.0
        elif satellite:
            left, top, right, bottom = entry.clip_rect
            clip_rect = (
                left * scale_x,
                top * scale_y,
                right * scale_x,
                bottom * scale_y,
            )
            entry_scale_x = scale_x
            entry_scale_y = scale_y
            entry_origin_x = origin_x
            entry_origin_y = origin_y
        else:
            clip_rect = transform.rect(entry.clip_rect)
            entry_scale_x = scale_x
            entry_scale_y = scale_y
            entry_origin_x = origin_x
            entry_origin_y = origin_y
        _draw_overlay_sprite(
            resources,
            entry.sprite,
            entry.x,
            entry.y,
            clip_rect,
            entry_scale_x,
            entry_scale_y,
            viewport_size=viewport_size,
            origin_x=entry_origin_x,
            origin_y=entry_origin_y,
            size_scale_x=size_scale,
            size_scale_y=size_scale,
            snap_to_pixels=True,
        )


def _draw_radar_cross(
    resources,
    cross,
    palette_rgb,
    viewport_size,
    marker_scale,
):
    if cross is None:
        return
    scale = float(marker_scale)
    stroke = max(1, _round_output_pixel(scale))
    span = max(stroke, _round_output_pixel(5.0 * scale))
    if (span - stroke) % 2:
        span += 1
    center_x = _round_output_pixel(cross.x)
    center_y = _round_output_pixel(cross.y)
    outer_left = center_x - span // 2
    outer_top = center_y - span // 2
    center_left = center_x - stroke // 2
    center_top = center_y - stroke // 2
    viewport_width, viewport_height = viewport_size
    color = (
        *palette_color_float(palette_rgb, cross.color_index),
        1.0,
    )
    writer = resources.overlay_rect_writer
    writer.reset()
    writer.rect(
        max(0, outer_left),
        max(0, center_top),
        min(viewport_width, outer_left + span),
        min(viewport_height, center_top + stroke),
        color,
    )
    writer.rect(
        max(0, center_left),
        max(0, outer_top),
        min(viewport_width, center_left + stroke),
        min(viewport_height, outer_top + span),
        color,
    )
    _render_hud_rect_vertices(resources, writer, viewport_size)


def _draw_radar_overlay(
    resources,
    radar,
    palette_rgb,
    viewport_size,
    radar_stroke_width,
    draw_text=True,
    *,
    layout,
    meter_style,
):
    satellite = int(radar.mode) == 4
    fullscreen_2d = int(radar.mode) == 2
    transform = None
    if satellite:
        origin_x = 0.0
        scale_x = viewport_size[0] / HUD_BASE_WIDTH
        scale_y = viewport_size[1] / HUD_BASE_HEIGHT
        origin_y = 0.0
        artwork_scale = 1.0
    elif fullscreen_2d:
        transform = layout.centered_transform(
            radar.reference_bounds,
            "panel",
            radar.animation_extent,
        )
        origin_x = transform.origin_x
        origin_y = transform.origin_y
        scale_x = transform.scale_x
        scale_y = transform.scale_y
        artwork_scale = layout.panel_scale
    else:
        transform = layout.resolve_transform(
            "radar",
            radar.reference_bounds,
            "panel",
            radar.animation_extent,
        )
        origin_x = transform.origin_x
        origin_y = transform.origin_y
        scale_x = transform.scale_x
        scale_y = transform.scale_y
        artwork_scale = layout.panel_scale
    stroke_scale = (
        min(scale_x, scale_y)
        if satellite
        else layout.panel_scale
    )
    scaled_stroke_width = float(radar_stroke_width) * stroke_scale
    radar_lines = radar.lines
    line_scale_x = scale_x
    line_scale_y = scale_y
    line_origin_x = origin_x
    line_origin_y = origin_y
    radar_fov = getattr(radar, "fov", None)
    if satellite and radar_fov is not None:
        line_edge_overshoot = (
            max(0.25, scaled_stroke_width * 0.5) + 2.0
        )
        radar_lines = satellite_fov_lines(
            radar_fov,
            viewport_size,
            edge_overshoot=line_edge_overshoot,
        )
        line_scale_x = 1.0
        line_scale_y = 1.0
        line_origin_x = 0.0
        line_origin_y = 0.0
    _use_transparent_alpha_replace_blend(resources)
    _draw_radar_lines(resources,
        radar_lines,
        palette_rgb,
        line_scale_x,
        line_scale_y,
        scaled_stroke_width,
        viewport_size,
        line_origin_x,
        line_origin_y,
    )
    _use_no_blend(resources)
    _draw_radar_sprites(
        resources,
        radar.sprites,
        satellite,
        transform,
        scale_x,
        scale_y,
        viewport_size,
        origin_x,
        origin_y,
        artwork_scale,
    )
    _fill_hud_rects(resources,
        radar.blips,
        palette_rgb,
        scale_x,
        scale_y,
        viewport_size,
        meter_style=meter_style,
        origin_x=origin_x,
        origin_y=origin_y,
        size_scale_x=artwork_scale,
        size_scale_y=artwork_scale,
    )
    _draw_radar_sprites(
        resources,
        radar.target_sprites,
        satellite,
        transform,
        scale_x,
        scale_y,
        viewport_size,
        origin_x,
        origin_y,
        artwork_scale,
    )
    _draw_radar_cross(
        resources,
        getattr(radar, "target_cross", None),
        palette_rgb,
        viewport_size,
        layout.target_marker_scale,
    )
    target_markers = getattr(radar, "target_markers", ())
    if target_markers:
        _use_transparent_alpha_replace_blend(resources)
        _prepare_hud_atlas(resources, viewport_size)
        for marker in target_markers:
            _draw_hud_atlas_marker(
                resources,
                marker,
                marker.x,
                marker.y,
                marker.clip_rect,
                viewport_size,
            )
        resources.overlay_sprite_program["u_vertex_origin"].value = (0.0, 0.0)
        resources.overlay_sprite_program["u_vertex_scale"].value = (1.0, 1.0)
    _use_transparent_alpha_replace_blend(resources)
    _draw_radar_ellipse(resources,
        radar.ellipse,
        palette_rgb,
        scale_x,
        scale_y,
        scaled_stroke_width,
        viewport_size,
        origin_x,
        origin_y,
    )
    font_size = layout.font_size if draw_text else 0
    if draw_text:
        for entry, slot in (
            (radar.range_text, resources.radar_range_text_slot),
            (radar.bearing_text, resources.radar_bearing_text_slot),
        ):
            if entry is None:
                continue
            color = palette_color_float(palette_rgb, entry.color_index)
            resources.font_renderer.draw_text(
                entry.text,
                origin_x + entry.x * scale_x,
                origin_y + entry.y * scale_y,
                viewport_size,
                color=(*color, 1.0),
                size_px=font_size,
                cache_slot=slot,
            )


def render_hud_overlay(
    resources,
    cockpit_hud,
    palette_rgb,
    *,
    radar_stroke_width,
    meter_style,
    camera=None,
    layout_settings,
):
    try:
        compositor.clear_overlay_target(resources)
        resources.ctx.enable_only(moderngl.NOTHING)
        if cockpit_hud is None:
            return
        layout = HudLayoutContext(resources.size, layout_settings)
        resolved_panels = tuple(
            (panel, *layout.panel_transforms(panel))
            for panel in cockpit_hud.panels
        )
        panel_by_id = {
            panel.panel_id: (panel, frame, content)
            for panel, frame, content in resolved_panels
        }

        _draw_radar_overlay(
            resources,
            cockpit_hud.radar,
            palette_rgb,
            resources.size,
            radar_stroke_width,
            layout=layout,
            meter_style=meter_style,
        )
        _use_no_blend(resources)
        for panel, _frame, transform in resolved_panels:
            clip_rect = (
                _frame.rect(panel.reference_bounds)
                if panel.clip_fills
                else None
            )
            _fill_hud_rects(
                resources,
                panel.fills,
                palette_rgb,
                transform.scale_x,
                transform.scale_y,
                meter_style=meter_style,
                origin_x=transform.origin_x,
                origin_y=transform.origin_y,
                minimum_extent=layout.panel_scale,
                clip_rect=clip_rect,
            )
            if panel.panel_id in ("compass_scaled", "altimeter_scaled"):
                _draw_static_hud_scale(resources, panel, _frame, transform)

        target_view = cockpit_hud.target_view
        if target_view is not None:
            _target_panel, target_frame, _target_content = panel_by_id["target"]
            _draw_hud_camera_view(
                resources,
                target_view,
                resources.target_view_target,
                resources.target_view_ready,
                resources.target_view_key,
                _hud_view_rect_pixels(target_view, target_frame),
            )
        mfd_view = cockpit_hud.mfd_view
        if mfd_view is not None:
            _mfd_panel, mfd_frame, _mfd_content = panel_by_id["mfd"]
            _draw_hud_camera_view(
                resources,
                mfd_view,
                resources.mfd_view_target,
                resources.mfd_view_ready,
                pixel_rect=_hud_view_rect_pixels(mfd_view, mfd_frame),
            )
        mfd_view = cockpit_hud.mfd_view
        if mfd_view is not None and resources.mfd_view_ready:
            _draw_hud_camera_view_border(resources,
                mfd_view,
                palette_color_float(palette_rgb, 0x06),
                mfd_frame,
            )
        _use_no_blend(resources)
        for panel, _frame, transform in resolved_panels:
            _fill_hud_rects(
                resources,
                panel.outlines,
                palette_rgb,
                transform.scale_x,
                transform.scale_y,
                meter_style=meter_style,
                origin_x=transform.origin_x,
                origin_y=transform.origin_y,
                outlines=True,
            )

        _use_transparent_alpha_replace_blend(resources)
        for panel, frame, transform in resolved_panels:
            frame_rect = frame.rect(panel.reference_bounds)
            resources.ctx.scissor = (
                _scissor_box(frame_rect, resources.size)
                if panel.clip_text
                else None
            )
            for entry in panel.texts:
                _draw_panel_text(
                    resources,
                    entry,
                    transform,
                    layout,
                    palette_rgb,
                )
            if panel.panel_id in ("compass_scaled", "altimeter_scaled"):
                _draw_static_hud_text(
                    resources,
                    panel,
                    transform,
                    layout,
                    palette_rgb,
                )
            resources.ctx.scissor = None
            for entry in panel.attached_texts:
                cache_slot = resources.hud_panel_text_slots[entry.panel_index]
                text_x = frame_rect[0] + entry.left_offset * frame.scale_x
                if entry.horizontal_alignment != "left":
                    text_width, _text_height = resources.font_renderer.measure(
                        entry.text,
                        size_px=layout.font_size,
                        cache_slot=cache_slot,
                    )
                if entry.horizontal_alignment == "center":
                    text_x -= text_width * 0.5
                elif entry.horizontal_alignment == "right":
                    text_x -= text_width
                color = palette_color_float(palette_rgb, entry.color_index)
                resources.font_renderer.draw_text(
                    entry.text,
                    text_x,
                    frame_rect[3] + entry.bottom_offset * frame.scale_y,
                    resources.size,
                    color=(*color, 1.0),
                    size_px=layout.font_size,
                    cache_slot=cache_slot,
                )

        _use_transparent_alpha_replace_blend(resources)
        caret_drawn = False
        for panel, frame, transform in resolved_panels:
            for entry in panel.static_shapes:
                if entry[0] >= len(STATIC_HUD_SHAPE_RANGES):
                    if not caret_drawn:
                        _prepare_hud_atlas(resources)
                    _draw_static_hud_caret(
                        resources,
                        entry,
                        transform,
                    )
                    caret_drawn = True
        if caret_drawn:
            resources.overlay_sprite_program["u_vertex_origin"].value = (0.0, 0.0)
            resources.overlay_sprite_program["u_vertex_scale"].value = (1.0, 1.0)

        _use_no_blend(resources)
        for panel, frame, transform in resolved_panels:
            native_sprite_size = panel.content_role == "panel_native_sprites"
            integral_sprite_transform = (
                layout.damage_sprite_transform(
                    panel,
                    alignment_transform=transform,
                )
                if panel.content_role != "damage_sprite"
                and panel.damage_sprite_center_x is not None
                else transform
            )
            for entry in panel.static_shapes:
                if entry[0] < len(STATIC_HUD_SHAPE_RANGES):
                    _draw_static_hud_shape(resources, entry, transform)
            for entry in panel.sprites:
                _draw_panel_sprite(
                    resources,
                    entry,
                    integral_sprite_transform,
                    native_size=native_sprite_size,
                )

        scene_origin_x, scene_origin_y, scene_scale = _scene_projected_transform(
            resources.size,
            camera,
            resources.max_horizontal_fov_degrees,
            getattr(resources, "main_perspective_projection_info", None),
        )
        for entry in cockpit_hud.scene_sprites:
            _draw_overlay_sprite(
                resources,
                entry.sprite,
                entry.x,
                entry.y,
                (0.0, 0.0, float(resources.size[0]), float(resources.size[1])),
                scene_scale,
                scene_scale,
                color_override=entry.color_override,
                origin_x=scene_origin_x,
                origin_y=scene_origin_y,
                size_scale_x=layout.target_marker_scale,
                size_scale_y=layout.target_marker_scale,
                snap_to_pixels=True,
            )
        scene_clip = (
            0.0,
            0.0,
            float(resources.size[0]),
            float(resources.size[1]),
        )
        if (
            cockpit_hud.scene_markers
            or cockpit_hud.target_bracket is not None
            or cockpit_hud.edge_indicators
        ):
            _use_transparent_alpha_replace_blend(resources)
            _prepare_hud_atlas(resources)
            for marker in cockpit_hud.scene_markers:
                anchor_x = scene_origin_x + marker.x * scene_scale
                anchor_y = scene_origin_y + marker.y * scene_scale
                _draw_hud_atlas_marker(
                    resources,
                    marker,
                    anchor_x,
                    anchor_y,
                    scene_clip,
                )
            if cockpit_hud.target_bracket is not None:
                _draw_target_bracket(
                    resources,
                    cockpit_hud.target_bracket,
                    scene_origin_x,
                    scene_origin_y,
                    scene_scale,
                    scene_clip,
                )
            for marker in cockpit_hud.edge_indicators:
                anchor_x, anchor_y = layout.reference_point(marker.x, marker.y)
                _draw_hud_atlas_marker(
                    resources,
                    marker,
                    anchor_x,
                    anchor_y,
                    layout.reference_rect(marker.clip_rect),
                )
            resources.overlay_sprite_program["u_vertex_origin"].value = (0.0, 0.0)
            resources.overlay_sprite_program["u_vertex_scale"].value = (1.0, 1.0)
            for marker in cockpit_hud.scene_markers:
                if marker.label:
                    _draw_target_nav_label(
                        resources,
                        marker,
                        scene_origin_x + marker.x * scene_scale,
                        scene_origin_y + marker.y * scene_scale,
                        layout.target_marker_scale,
                        palette_rgb,
                        scene_clip,
                    )
    finally:
        resources.ctx.screen.use()
        resources.ctx.enable_only(moderngl.NOTHING)


def render_hud_video_noise_overlay(resources, resolved_groups, layout_settings):
    if not resolved_groups:
        return
    try:
        resources.overlay_fbo.use()
        resources.ctx.enable_only(moderngl.NOTHING)
        layout = HudLayoutContext(resources.size, layout_settings)
        _use_no_blend(resources)
        for panel, entries in resolved_groups:
            transform = layout.content_transform(panel)
            for entry in entries:
                _draw_panel_sprite(resources, entry, transform)
    finally:
        resources.ctx.screen.use()
        resources.ctx.enable_only(moderngl.NOTHING)


def _draw_menu_slider(
    resources,
    slider,
    x,
    y,
    clip_rect,
    scale_x,
    scale_y,
    fallback_color,
    origin_x,
    origin_y,
):
    left = slider.left_sprite
    track = slider.track_sprite
    right = slider.right_sprite
    thumb = slider.thumb_sprite
    if left is not None:
        _draw_overlay_sprite(
            resources, left, x, y, clip_rect, scale_x, scale_y,
            origin_x=origin_x, origin_y=origin_y,
        )
        x += left.width / scale_x
    track_width_pixels = track.width if track is not None else 316
    track_width = track_width_pixels / scale_x
    if track is not None:
        _draw_overlay_sprite(
            resources, track, x, y, clip_rect, scale_x, scale_y,
            origin_x=origin_x, origin_y=origin_y,
        )
    if right is not None:
        _draw_overlay_sprite(resources,
            right,
            x + track_width,
            y,
            clip_rect,
            scale_x,
            scale_y,
            origin_x=origin_x,
            origin_y=origin_y,
        )
    thumb_width = thumb.width if thumb is not None else 21
    thumb_offset = slider.value * track_width_pixels / 0x10000
    thumb_x = x + (thumb_offset - thumb_width * 0.5) / scale_x
    if thumb is not None:
        _draw_overlay_sprite(resources,
            thumb,
            thumb_x,
            y,
            clip_rect,
            scale_x,
            scale_y,
            origin_x=origin_x,
            origin_y=origin_y,
        )


def _menu_page_origin(resources, page, layout):
    if page.handler_id in (MENU_HANDLER_USER_SYSTEMS, MENU_HANDLER_COMMAND):
        center_x = (page.left + page.right) * 0.5
        center_y = (page.top + page.bottom) * 0.5
        return (
            resources.size[0] * 0.5
            + (center_x - HUD_BASE_WIDTH * 0.5) * layout.position_scale
            - center_x,
            resources.size[1] * 0.5
            + (center_y - HUD_BASE_HEIGHT * 0.5) * layout.position_scale
            - center_y,
        )
    if page.background_sprite is not None:
        visual_left = page.background_left + page.background_sprite.x_offset
        visual_top = page.background_top + page.background_sprite.y_offset
        return (
            resources.size[0] * 0.5
            - visual_left
            - page.background_sprite.width * 0.5,
            resources.size[1] * 0.5
            - visual_top
            - page.background_sprite.height * 0.5,
        )
    return (
        (resources.size[0] - (page.right - page.left)) * 0.5 - page.left,
        (resources.size[1] - (page.bottom - page.top)) * 0.5 - page.top,
    )


def render_menu_overlay(resources, menu_pages, palette_rgb, layout_settings):
    if not menu_pages:
        return

    try:
        resources.overlay_fbo.use()
        resources.overlay_fbo.viewport = (0, 0, resources.size[0], resources.size[1])
        resources.ctx.enable_only(moderngl.NOTHING)
        layout = HudLayoutContext(resources.size, layout_settings)
        font_size = HUD_FONT_SIZE
        label_indent, _ = resources.font_renderer.measure(
            "0  ",
            size_px=font_size,
            cache_slot=resources.menu_label_indent_text_slot,
        )

        for page in menu_pages:
            scale_x = 1.0
            scale_y = 1.0
            origin_x, origin_y = _menu_page_origin(resources, page, layout)
            page_text_slots = resources.menu_page_text_slots.get(page.page_address)
            if page_text_slots is None:
                page_text_slots = _MenuPageTextSlots(len(page.items))
                resources.menu_page_text_slots[page.page_address] = page_text_slots
            else:
                page_text_slots.ensure_item_count(len(page.items))
            left = origin_x + page.left * scale_x
            top = origin_y + page.top
            right = origin_x + page.right * scale_x
            bottom = origin_y + page.bottom
            if page.clear_background:
                _use_no_blend(resources)
                _fill_overlay_rect(resources,
                    left,
                    top,
                    right,
                    bottom,
                    palette_color_float(palette_rgb, 0x00),
                )
            if page.background_sprite is not None:
                _use_no_blend(resources)
                background_clip = _hud_clip_rect(
                    (
                        page.background_left,
                        page.background_top,
                        page.background_right + 1,
                        page.background_bottom + 1,
                    ),
                    origin_x,
                    scale_y,
                )
                background_clip = (
                    background_clip[0],
                    background_clip[1] + origin_y,
                    background_clip[2],
                    background_clip[3] + origin_y,
                )
                _draw_overlay_sprite(resources,
                    page.background_sprite,
                    page.background_left,
                    page.background_top,
                    background_clip,
                    scale_x,
                    scale_y,
                    origin_x=origin_x,
                    origin_y=origin_y,
                )
            if page.draw_border:
                _use_no_blend(resources)
                _draw_overlay_rect(resources,
                    left,
                    top,
                    right,
                    bottom,
                    palette_color_float(palette_rgb, 0x01),
                )

            normal_color = palette_color_float(
                palette_rgb,
                page.normal_color_index,
            )
            text_over_opaque = page.clear_background or page.background_sprite is not None
            if page.title:
                if page.draw_border:
                    underline_right = right - scale_x
                else:
                    title_width, _ = resources.font_renderer.measure(
                        page.title,
                        size_px=font_size,
                        cache_slot=page_text_slots.title,
                    )
                    underline_right = origin_x + page.title_x * scale_x + title_width
                underline_y = origin_y + page.title_y + 14
                _use_no_blend(resources)
                _fill_overlay_rect(resources,
                    origin_x + page.title_x,
                    underline_y,
                    underline_right,
                    underline_y + max(1.0, scale_y),
                    palette_color_float(palette_rgb, 0x01),
                )

            selected_item = None
            for item_index, item in enumerate(page.items):
                if item.slider is not None:
                    _use_no_blend(resources)
                    color = palette_color_float(
                        palette_rgb,
                        item.color_index,
                    )
                    _draw_menu_slider(resources,
                        item.slider,
                        item.text_x,
                        item.text_y + 7,
                        (
                            left,
                            top,
                            origin_x + (page.right + 1) * scale_x,
                            origin_y + page.bottom + 1,
                        ),
                        scale_x,
                        scale_y,
                        color,
                        origin_x,
                        origin_y,
                    )
                if item.selected:
                    selected_item = item

            if page.show_marker and selected_item is not None:
                if page.marker_sprite is not None:
                    _use_no_blend(resources)
                    _draw_overlay_sprite(resources,
                        page.marker_sprite,
                        page.marker_x,
                        page.marker_y,
                        (
                            left,
                            top,
                            origin_x + (page.right + 1) * scale_x,
                            origin_y + page.bottom + 1,
                        ),
                        scale_x,
                        scale_y,
                        origin_x=origin_x,
                        origin_y=origin_y,
                    )

            if text_over_opaque:
                _use_premultiplied_blend(resources)
            else:
                _use_transparent_alpha_replace_blend(resources)
            resources.font_renderer.draw_text(
                page.title,
                origin_x + page.title_x * scale_x,
                origin_y + page.title_y,
                resources.size,
                color=(*normal_color, 1.0),
                size_px=font_size,
                cache_slot=page_text_slots.title,
            )
            for item_index, item in enumerate(page.items):
                color = palette_color_float(palette_rgb, item.color_index)
                label = (
                    f"{item.prefix}  {item.text}"
                    if item.prefix
                    else item.text
                )
                label_x = origin_x + item.prefix_x * scale_x
                if not item.prefix:
                    label_x += label_indent
                resources.font_renderer.draw_text(
                    label,
                    label_x,
                    origin_y + item.prefix_y,
                    resources.size,
                    color=(*color, 1.0),
                    size_px=font_size,
                    cache_slot=page_text_slots.item_labels[item_index],
                )
                if item.value_text:
                    resources.font_renderer.draw_text(
                        item.value_text,
                        origin_x + item.text_x * scale_x,
                        origin_y + item.text_y,
                        resources.size,
                        color=(*color, 1.0),
                        size_px=font_size,
                        cache_slot=page_text_slots.item_values[item_index],
                    )
            if (
                page.show_marker
                and selected_item is not None
                and page.marker_sprite is None
            ):
                marker_color = palette_color_float(
                    palette_rgb,
                    page.marker_color_index,
                )
                resources.font_renderer.draw_text(
                    ">",
                    origin_x + page.marker_x * scale_x,
                    origin_y + page.marker_y,
                    resources.size,
                    color=(*marker_color, 1.0),
                    size_px=font_size,
                    cache_slot=page_text_slots.marker,
                )
    finally:
        resources.ctx.screen.use()
        resources.ctx.enable_only(moderngl.NOTHING)


def render_objectives_overlay(resources, objectives, palette_rgb):
    if objectives is None:
        return

    try:
        resources.overlay_fbo.use()
        resources.overlay_fbo.viewport = (0, 0, resources.size[0], resources.size[1])
        resources.ctx.enable_only(moderngl.NOTHING)
        origin_x, scale_x = _hud_layout(resources.size)
        scale_y = scale_x
        font_size = HUD_FONT_SIZE
        blue = palette_color_float(palette_rgb, 0x06)
        green = palette_color_float(palette_rgb, 0x0E)

        objectives_title = "MISSION OBJECTIVES"
        _use_transparent_alpha_replace_blend(resources)
        resources.font_renderer.draw_text(
            objectives_title,
            origin_x + 96 * scale_x,
            270 * scale_y,
            resources.size,
            color=(*blue, 1.0),
            size_px=font_size,
            cache_slot=resources.objectives_title_text_slot,
        )
        title_width, _ = resources.font_renderer.measure(
            objectives_title,
            size_px=font_size,
            cache_slot=resources.objectives_title_text_slot,
        )
        _use_no_blend(resources)
        _fill_overlay_rect(resources,
            origin_x + 96 * scale_x,
            284 * scale_y,
            origin_x + 96 * scale_x + title_width,
            284 * scale_y + max(1.0, scale_y),
            blue,
        )

        for row_index, row in enumerate(objectives.rows):
            row_text_slots = resources.objective_row_text_slots[row_index]
            row_y = row.y * scale_y
            _use_transparent_alpha_replace_blend(resources)
            resources.font_renderer.draw_text(
                row.label,
                origin_x + 96 * scale_x,
                row_y,
                resources.size,
                color=(*blue, 1.0),
                size_px=font_size,
                cache_slot=row_text_slots.label,
            )
            label_width, _ = resources.font_renderer.measure(
                row.label,
                size_px=font_size,
                cache_slot=row_text_slots.label,
            )
            resources.font_renderer.draw_text(
                row.text,
                origin_x + 96 * scale_x + label_width,
                row_y,
                resources.size,
                color=(*green, 1.0),
                size_px=font_size,
                cache_slot=row_text_slots.text,
            )
            status_color = palette_color_float(
                palette_rgb,
                row.status_color_index,
            )
            status_width, _ = resources.font_renderer.measure(
                row.status,
                size_px=font_size,
                cache_slot=row_text_slots.status,
            )
            resources.font_renderer.draw_text(
                row.status,
                origin_x + 961 * scale_x - status_width,
                row_y,
                resources.size,
                color=(*status_color, 1.0),
                size_px=font_size,
                cache_slot=row_text_slots.status,
            )
            if row.continuation:
                resources.font_renderer.draw_text(
                    row.continuation,
                    origin_x + 166 * scale_x,
                    (row.y + 14) * scale_y,
                    resources.size,
                    color=(*green, 1.0),
                    size_px=font_size,
                    cache_slot=row_text_slots.continuation,
                )

        if objectives.footer:
            resources.font_renderer.draw_text(
                objectives.footer,
                origin_x + 96 * scale_x,
                403 * scale_y,
                resources.size,
                color=(*blue, 1.0),
                size_px=font_size,
                cache_slot=resources.objectives_footer_text_slot,
            )
    finally:
        resources.ctx.screen.use()
        resources.ctx.enable_only(moderngl.NOTHING)


def _message_bar_layout(resources, slot, font_size):
    width = HUD_BASE_WIDTH * float(resources.size[1]) / HUD_BASE_HEIGHT
    height = max(1.0, float(font_size) * 3.0)
    left = (float(resources.size[0]) - width) * 0.5
    top = 0.0 if int(slot) == 0 else float(resources.size[1]) - height
    return left, top, width, height


def _draw_message_bar(resources, slot, font_size):
    left, top, width, height = _message_bar_layout(
        resources, slot, font_size
    )
    x_values = (left, left + 24.0, left + width - 25.0, left + width)
    u_values = (0.0 / 47.0, 24.0 / 47.0, 24.0 / 47.0, 47.0 / 47.0)
    bottom = top + height
    vertices = array("f")
    for segment in range(3):
        x0 = x_values[segment]
        x1 = x_values[segment + 1]
        y0 = top
        y1 = bottom
        u0 = u_values[segment]
        u1 = u_values[segment + 1]
        # The PNG bytes are uploaded top row first. With direct upload,
        # texture row 0 is sampled at v=0, so use v=0 for the screen top.
        vertices.extend((x0, y0, u0, 0.0))
        vertices.extend((x1, y0, u1, 0.0))
        vertices.extend((x0, y1, u0, 1.0))
        vertices.extend((x0, y1, u0, 1.0))
        vertices.extend((x1, y0, u1, 0.0))
        vertices.extend((x1, y1, u1, 1.0))
    resources.message_bar_program["u_viewport_size"].value = resources.size
    resources.message_bar_buffer.write(vertices.tobytes())
    resources.message_bar_texture.use(location=4)
    resources.message_bar_vao.render(mode=moderngl.TRIANGLES, vertices=18)


def render_short_messages_overlay(resources, messages, palette_rgb):
    if messages is None or not messages.messages:
        return

    try:
        resources.overlay_fbo.use()
        resources.overlay_fbo.viewport = (0, 0, resources.size[0], resources.size[1])
        resources.ctx.enable_only(moderngl.NOTHING)
        font_size = HUD_FONT_SIZE
        text_color = palette_color_float(palette_rgb, 0x0E)
        _use_no_blend(resources)
        for message in messages.messages:
            _draw_message_bar(resources, message.slot, font_size)

        _use_premultiplied_blend(resources)
        for message in messages.messages:
            slot = resources.short_message_text_slots[
                max(0, min(1, int(message.slot)))
            ]
            resources.font_renderer.measure(
                message.text,
                size_px=font_size,
                cache_slot=slot,
            )
            bar_left, bar_top, _bar_width, _bar_height = _message_bar_layout(
                resources,
                message.slot,
                font_size,
            )
            font_scale = float(font_size) / float(HUD_FONT_SIZE)
            text_x = bar_left + float(message.text_x - message.left)
            text_y = bar_top + float(message.text_y - message.top) * font_scale
            resources.font_renderer.draw_text(
                message.text,
                text_x,
                text_y,
                resources.size,
                color=(*text_color, 1.0),
                size_px=font_size,
                cache_slot=slot,
            )
    finally:
        resources.ctx.screen.use()
        resources.ctx.enable_only(moderngl.NOTHING)
