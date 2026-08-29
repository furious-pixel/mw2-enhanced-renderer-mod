from __future__ import annotations

import math
import struct
from dataclasses import dataclass, field

from .hud_3d_views import (
    TARGET_VIEW_PANE_RECT,
    snapshot_mfd_camera_view,
    snapshot_target_camera_view,
)
from .hud_atlas import TARGET_CARET_BY_DIRECTION, TARGET_NAV_CIRCLE
from .hud_radar import EMPTY_RADAR_HUD, snapshot_radar_hud
from .projection import perspective_projection_info
from .hud_sprites import (
    HudSprite,
    decode_hud_sprite,
    hud_sprite_frame_count,
    preloaded_hud_frame_count,
    preloaded_hud_sprite,
    resource_hud_sprite,
    resolve_cached_shape,
)


ADDR_GAME_TICK = 0x000A58C4
ADDR_PLAYER_SLOT = 0x000A5918
ADDR_MASC_ACTIVE = 0x000A62A0
ADDR_MASTER_HUD_ENABLE = 0x000A6314
ADDR_ENTITY_BODY_TABLE = 0x00108B00
ADDR_PANEL_TABLE = 0x0010E23C
ADDR_CURRENT_HUD_TEXT_COLOR = 0x000B4E92
# Captured as runtime 0x266900 with delta 0x1C0000 in the clean-room trace.
ADDR_METER_STATE = 0x000A6900
ADDR_MFD_DAMAGE_SCALE = 0x000A6280
ADDR_MFD_DAMAGE_UI = 0x000A66AC
ADDR_MFD_HTAL_LABELS = 0x000A0B20
ADDR_MFD_HTAL_POSITIONS = 0x000A66EC
ADDR_MFD_MODE = 0x000A839C
ADDR_MFD_STATIC_CONFIG = 0x0010E020
ADDR_MFD_DAMAGE_SHAPE_BASE = 0x0010E1A8
ADDR_HUD_STYLE_OFFSET = 0x000B4E80
ADDR_COMPASS_ENABLE = 0x000A6320
ADDR_COMPASS_CENTER = 0x000A62DC
ADDR_ALTIMETER_LAYOUT = 0x000A62D4
ADDR_COMPASS_BAR_CONFIG = 0x0010B608
ADDR_ALTIMETER_CONFIG = 0x0010B614
ADDR_HUD_POST_CONFIGS = 0x0010E298
ADDR_RETICLE_ENABLE = 0x000A6318
ADDR_CAMERA_RETICLE_GATE = 0x000A6FBC
ADDR_RADAR_MODE = 0x000B4D08
ADDR_TARGET_DISPLAY_ENABLE = 0x000A68E8
ADDR_TARGET_GLITCH_LATCH = 0x000A68F8
ADDR_MFD_GLITCH_LATCH = 0x000A68FC
ADDR_HUD_ANIMATION_TABLE = 0x000B4AD0
ADDR_RETICLE_PANE = 0x000B4768
ADDR_ACTIVE_CAMERA = 0x000A70E8
ADDR_WEAPON_DEFINITIONS = 0x000AEF1C
ADDR_PRIMARY_CLASSIFICATION = 0x0010B631
ADDR_SECONDARY_TARGETS = 0x00104B80
ADDR_DIRECT_TARGETS = 0x00108BF0
ADDR_SECONDARY_POSITION_TABLE = 0x00111FA0
ADDR_SECONDARY_MAX_INDEX = 0x000A6D50
ADDR_SECONDARY_CLASSIFICATION = 0x0010C6C4
ADDR_TARGET_PROJECTOR = 0x0015FF48
ADDR_AIM_Y_OFFSET = 0x000A6FD0
ADDR_COMPASS_TARGET_CONFIG = 0x0010B5FC
ADDR_ALTIMETER_TARGET_X = 0x000A62CC
ADDR_AUTOPILOT_TEXT = 0x000A0D28

METER_STATE_SIZE = 0x80
MFD_DAMAGE_UI_SIZE = 0x238
MFD_STATIC_CONFIG_SIZE = 0x188

PANEL_COUNT = 25
PANEL_SIZE_NAME = 0x20
WEAPON_SIZE = 0x68

CALLBACK_WEAPON_STEADY = 0x00046D00
CALLBACK_WEAPON_STARTUP = 0x00046E50
CALLBACK_THROTTLE = 0x00049560
CALLBACK_MASC = 0x00049690
CALLBACK_HEAT = 0x00049710
CALLBACK_HEAT_RATE = 0x00049790
CALLBACK_JUMP_JETS = 0x00049810
CALLBACK_MFD = 0x00047AD0
CALLBACK_MFD_STARTUP = 0x00048080
CALLBACK_MFD_SHUTDOWN = 0x00048150
CALLBACK_TARGET_PANEL = 0x00047490
CALLBACK_TARGET_TEXT_PANEL = 0x00046F00
CALLBACK_TARGET_PANEL_STARTUP = 0x00047900
CALLBACK_TARGET_PANEL_SHUTDOWN = 0x000479C0
CALLBACK_AUTOPILOT = 0x000494C0
NORMAL_HUD_TEXT_COLOR = 0x0E
MFD_TEXT_SLOT_BASE = 25
TARGET_NAME_TEXT_SLOT = 40
TARGET_RANGE_TEXT_SLOT = 41
ANGLE_FULL_TURN = 0x01680000
HUD_TRANSITION_DURATION_SECONDS = 1.0
COMPASS_NATIVE_STRIP_X_OFFSET = -710
ALTIMETER_ENHANCED_X_OFFSET = -10
ATTACHED_LABEL_TOP_OFFSET = 9
ATTACHED_LABEL_ROW_HEIGHT = 16
ALT_HTAL_DAMAGE_GAP = 10
COMPASS_TEXT_SLOT_BASE = 42
COMPASS_TEXT_SLOT_COUNT = 12
ALTIMETER_TEXT_SLOT_BASE = 54
ALTIMETER_TEXT_SLOT_COUNT = 47
MFD_CAMERA_TEXT_SLOT_BASE = ALTIMETER_TEXT_SLOT_BASE + ALTIMETER_TEXT_SLOT_COUNT
MFD_CAMERA_TEXT_SLOT_COUNT = 3
COMPASS_LABEL_FONT_SIZE = 19
COMPASS_LABEL_HORIZONTAL_SCALE = 0.68
COMPASS_LONG_TICK_HEIGHT = 12
COMPASS_EDGE_CARET_GAP = 2
THROTTLE_OUTER_WIDTH = 17
# 1024x768 reference geometry. The bounds mirror the native altimeter pane.
ALT_THROTTLE_PANEL_BOUNDS = (911, 285, 1021, 429)
# The bar itself mirrors the altimeter's enhanced scale origin.
ALT_THROTTLE_BAR_LEFT = 980
ALT_THROTTLE_BAR_TOP = ALT_THROTTLE_PANEL_BOUNDS[1]
ALT_THROTTLE_BAR_BOTTOM = ALT_THROTTLE_PANEL_BOUNDS[3] - 1
ALT_THROTTLE_NEUTRAL_Y = ALT_THROTTLE_BAR_TOP + (
    (ALT_THROTTLE_BAR_BOTTOM - ALT_THROTTLE_BAR_TOP) * 2 // 3
)
ALT_THROTTLE_TEXT_RIGHT = 972
ALT_THROTTLE_MASC_Y = ALT_THROTTLE_NEUTRAL_Y + 24
STATIC_SHAPE_LEFT_EDGE = -1
STATIC_SHAPE_FREE = 0
STATIC_SHAPE_RIGHT_EDGE = 1
_MFD_CAMERA_LABELS = (
    None,
    None,
    None,
    ("rear", MFD_CAMERA_TEXT_SLOT_BASE),
    ("down", MFD_CAMERA_TEXT_SLOT_BASE + 1),
    ("wpn", MFD_CAMERA_TEXT_SLOT_BASE + 2),
)
ALTIMETER_LABEL_FONT_SIZE = 16
HUD_SCALE_SHAPE_INDEX = {
    resource: index
    for index, resource in enumerate((0x01, 0x04, 0x0D, 0x10, 0x13, 0x16,
                                      0x1C, 0x1F, 0x22, 0x25))
}

class _HudLifecycleState:
    pass


_HUD_LIFECYCLE_STATE = _HudLifecycleState()

POWER_METER_CALLBACKS = {
    CALLBACK_THROTTLE,
    CALLBACK_MASC,
    CALLBACK_HEAT,
    CALLBACK_HEAT_RATE,
    CALLBACK_JUMP_JETS,
}


@dataclass(frozen=True, slots=True)
class WeaponHudRowLayout:
    panel_index: int
    weapon_index: int
    name: str
    reveal_threshold: int
    rect: tuple[int, int, int, int]
    text_position: tuple[int, int]


@dataclass(frozen=True, slots=True)
class WeaponHudPanelLayout:
    reference_bounds: tuple[int, int, int, int]
    panel_addresses: frozenset[int]
    rows: tuple[WeaponHudRowLayout, ...]


@dataclass(frozen=True, slots=True)
class CockpitHudText:
    panel_index: int
    text: str
    color_index: int
    x: float
    y: float
    horizontal_alignment: str = "left"
    vertical_alignment: str = "top"
    base_font_size: int = 16
    horizontal_scale: float = 1.0


@dataclass(frozen=True, slots=True)
class CockpitHudRect:
    color_index: int
    left: float
    top: float
    right: float
    bottom: float


@dataclass(frozen=True, slots=True)
class CockpitHudMeterRect:
    base_color_index: int
    left: float
    top: float
    right: float
    bottom: float
    shade_axis: str


@dataclass(frozen=True, slots=True)
class CockpitHudVerticalMeter:
    current_color_index: int
    remaining_color_index: int
    left: float
    top: float
    right: float
    split: float
    bottom: float
    enhanced: bool


@dataclass(frozen=True, slots=True)
class CockpitHudThrottleMeter:
    border_color_index: int
    base_color_index: int
    left: float
    top: float
    right: float
    bottom: float
    fill_top: float
    fill_bottom: float
    enhanced: bool


@dataclass(frozen=True, slots=True)
class CockpitHudSprite:
    sprite: HudSprite
    x: float
    y: float
    clip_rect: tuple[int, int, int, int]
    color_override: int | None
    draw_width: int | None = None
    draw_height: int | None = None
    source_x: float = 0.0
    source_y: float = 0.0
    repeat_x: bool = False
    repeat_y: bool = False


@dataclass(frozen=True, slots=True)
class CockpitHudAtlasMarker:
    shape_index: int
    x: float
    y: float
    clip_rect: tuple[int, int, int, int]
    color_index: int
    label: str = ""


@dataclass(frozen=True, slots=True)
class CockpitHudTargetBracket:
    x: float
    y: float
    radius: float
    color_index: int


@dataclass(frozen=True, slots=True)
class CockpitHudVideoNoise:
    record: int
    resource_index: int
    state: int
    ticks_per_frame: int
    start_tick: int
    flags: int
    tick: int
    x: int
    y: int
    clip_rect: tuple[int, int, int, int]
    native_state: int


@dataclass(frozen=True, slots=True)
class CockpitHudAttachedText:
    panel_index: int
    text: str
    color_index: int
    left_offset: float
    bottom_offset: float
    horizontal_alignment: str = "left"


@dataclass(frozen=True, slots=True)
class CockpitHudPanel:
    panel_id: str
    reference_bounds: tuple[int, int, int, int]
    content_role: str
    animation_extent: tuple[float, float]
    texts: tuple[CockpitHudText, ...]
    attached_texts: tuple[CockpitHudAttachedText, ...]
    outlines: tuple[CockpitHudRect, ...]
    fills: tuple[CockpitHudRect, ...]
    static_shapes: tuple[
        tuple[int, float, float, tuple[int, int, int, int], int], ...
    ]
    sprites: tuple[CockpitHudSprite, ...]
    video_noise: tuple[CockpitHudVideoNoise, ...]
    scale_origin_x: float
    scale_origin_y: float
    clip_text: bool
    clip_fills: bool
    damage_sprite_center_x: float | None
    damage_sprite_bottom_y: float
    damage_sprite_gap: float


@dataclass(frozen=True, slots=True)
class CockpitHudSnapshot:
    panels: tuple[CockpitHudPanel, ...]
    scene_sprites: tuple[CockpitHudSprite, ...]
    scene_markers: tuple[CockpitHudAtlasMarker, ...]
    target_bracket: CockpitHudTargetBracket | None
    edge_indicators: tuple[CockpitHudAtlasMarker, ...]
    radar: object
    target_view: object | None
    mfd_view: object | None


EMPTY_COCKPIT_HUD = CockpitHudSnapshot(
    (),
    (),
    (),
    None,
    (),
    EMPTY_RADAR_HUD,
    None,
    None,
)


@dataclass(slots=True)
class _PanelBuilder:
    panel_id: str
    reference_bounds: tuple[int, int, int, int]
    content_role: str = "panel"
    animation_extent: tuple[float, float] = (1.0, 1.0)
    texts: list = field(default_factory=list)
    attached_texts: list = field(default_factory=list)
    outlines: list = field(default_factory=list)
    fills: list = field(default_factory=list)
    static_shapes: list = field(default_factory=list)
    sprites: list = field(default_factory=list)
    video_noise: list = field(default_factory=list)
    scale_origin_x: float = 0.0
    scale_origin_y: float = 0.0
    clip_text: bool = True
    clip_fills: bool = True
    damage_sprite_center_x: float | None = None
    damage_sprite_bottom_y: float = 0.0
    damage_sprite_gap: float = 0.0

    def include(self, rect):
        left, top, right, bottom = (int(value) for value in rect)
        old = self.reference_bounds
        self.reference_bounds = (
            min(old[0], left),
            min(old[1], top),
            max(old[2], right),
            max(old[3], bottom),
        )

    def freeze(self):
        return CockpitHudPanel(
            self.panel_id,
            self.reference_bounds,
            self.content_role,
            self.animation_extent,
            tuple(self.texts),
            tuple(self.attached_texts),
            tuple(self.outlines),
            tuple(self.fills),
            tuple(self.static_shapes),
            tuple(self.sprites),
            tuple(self.video_noise),
            self.scale_origin_x,
            self.scale_origin_y,
            self.clip_text,
            self.clip_fills,
            self.damage_sprite_center_x,
            self.damage_sprite_bottom_y,
            self.damage_sprite_gap,
        )


def _weapon_panel_layout(gamemem, lifecycle, mech, panel_addresses):
    key = (int(mech), tuple(panel_addresses))
    cached = getattr(lifecycle, "mw2_weapon_panel_layout", None)
    if cached is not None and cached[0] == key:
        return cached[1]

    delta = int(gamemem.delta)
    rows = []
    weapon_panels = set()
    for panel_index, panel in enumerate(panel_addresses):
        if panel == 0:
            continue
        panel_data = bytes(gamemem.read_runtime_bytes(panel, 0x84))
        startup, steady = struct.unpack_from("<2I", panel_data, 0x78)
        callbacks = tuple(value - delta if value else 0 for value in (startup, steady))
        if callbacks != (CALLBACK_WEAPON_STARTUP, CALLBACK_WEAPON_STEADY):
            continue
        weapon_panels.add(panel)
        weapon_index = struct.unpack_from("<i", panel_data, 0x0C)[0]
        pane, text_position = struct.unpack_from("<2I", panel_data, 0x30)
        if (
            struct.unpack_from("<H", panel_data, 0x04)[0] == 0
            or weapon_index < 0
            or pane == 0
            or text_position == 0
        ):
            continue
        left, top, right, bottom = struct.unpack(
            "<4i", bytes(gamemem.read_runtime_bytes(pane + 0x04, 0x10))
        )
        local_x, local_y = struct.unpack(
            "<2i", bytes(gamemem.read_runtime_bytes(text_position, 0x08))
        )
        rows.append(
            WeaponHudRowLayout(
                panel_index,
                weapon_index,
                _ascii_string(panel_data[0x10 : 0x10 + PANEL_SIZE_NAME]),
                struct.unpack_from("<i", panel_data, 0x08)[0],
                (left, top, right, bottom),
                (left + local_x, top + local_y),
            )
        )

    layout = None
    if rows:
        layout = WeaponHudPanelLayout(
            (
                min(row.rect[0] for row in rows),
                min(row.rect[1] for row in rows),
                max(row.rect[2] for row in rows),
                max(row.rect[3] for row in rows),
            ),
            frozenset(weapon_panels),
            tuple(rows),
        )
    result = (key, layout)
    lifecycle.mw2_weapon_panel_layout = result
    return layout


def _snapshot_weapon_panel(
    gamemem,
    layout,
    weapon_base,
    active_weapon,
    hud_mode,
    game_tick,
    startup_text_color,
):
    if layout is None or weapon_base == 0 or hud_mode not in (1, 2):
        return None
    panel = _PanelBuilder("weapon", layout.reference_bounds)
    for row in layout.rows:
        weapon = weapon_base + row.weapon_index * WEAPON_SIZE
        weapon_data = bytes(gamemem.read_runtime_bytes(weapon + 0x04, 0x20))
        if struct.unpack_from("<i", weapon_data, 0x00)[0] < 0:
            continue
        if hud_mode == 1:
            if game_tick <= row.reveal_threshold:
                continue
            label = row.name
            color_index = startup_text_color
        else:
            ammo = struct.unpack_from("<i", weapon_data, 0x1C)[0]
            label = row.name if ammo < 0 else f"{row.name} {ammo}"
            color_index = _weapon_color(
                struct.unpack_from("<i", weapon_data, 0x04)[0],
                struct.unpack_from("<i", weapon_data, 0x08)[0],
            )
        panel.texts.append(
            CockpitHudText(
                row.panel_index,
                label,
                color_index,
                *row.text_position,
            )
        )
        if hud_mode == 2 and row.weapon_index == active_weapon:
            panel.outlines.append(CockpitHudRect(color_index, *row.rect))
    return panel if panel.texts else None


def _ascii_string(raw):
    return bytes(raw).split(b"\x00", 1)[0].decode("cp437", errors="replace")


def _append_meter_text(
    texts,
    panel_index,
    text,
    color_index,
    x,
    y,
    alternate_y=None,
):
    alternate = alternate_y is not None
    texts.append(
        CockpitHudText(
            panel_index=panel_index,
            text=text,
            color_index=color_index,
            x=ALT_THROTTLE_TEXT_RIGHT if alternate else x,
            y=alternate_y if alternate else y,
            horizontal_alignment="right" if alternate else "left",
            vertical_alignment="center" if alternate else "top",
        )
    )


def _weapon_color(weapon_type, readiness):
    if weapon_type == -1:
        return 0x08
    if weapon_type == 0:
        return 0x0B
    if weapon_type == 1:
        if readiness == 1:
            return 0xFE
        if readiness == 2:
            return 0x03
        return 0x0E
    if weapon_type == 2:
        return 0x0E
    return 0x0B


def _append_fill(fills, color_index, left, top, right, bottom):
    if right <= left or bottom <= top:
        return
    fills.append(
        CockpitHudRect(
            color_index=max(0, min(0xFF, color_index)),
            left=float(left),
            top=float(top),
            right=float(right),
            bottom=float(bottom),
        )
    )


def _append_meter_fill(
    fills,
    base_color_index,
    left,
    top,
    right,
    bottom,
    shade_axis,
):
    if right <= left or bottom <= top:
        return
    fills.append(
        CockpitHudMeterRect(
            base_color_index=max(0, min(0xFF, int(base_color_index))),
            left=float(left),
            top=float(top),
            right=float(right),
            bottom=float(bottom),
            shade_axis=str(shade_axis),
        )
    )


def _append_clipped_fill(
    fills,
    color_index,
    left,
    top,
    right,
    bottom,
    clip_rect,
):
    clip_left, clip_top, clip_right, clip_bottom = clip_rect
    _append_fill(
        fills,
        color_index,
        max(left, clip_left),
        max(top, clip_top),
        min(right, clip_right),
        min(bottom, clip_bottom),
    )


def _append_one_pixel_border(fills, color_index, left, top, right, bottom):
    _append_fill(fills, color_index, left, top, right + 1, top + 1)
    _append_fill(fills, color_index, left, bottom, right + 1, bottom + 1)
    _append_fill(fills, color_index, left, top, left + 1, bottom + 1)
    _append_fill(fills, color_index, right, top, right + 1, bottom + 1)


def _shade_bands(thickness, base_color):
    first = thickness // 4
    second = thickness // 2
    third = thickness * 3 // 4
    return (
        (0, first, base_color - 1),
        (first, second, base_color),
        (second, third, base_color - 1),
        (third, thickness, base_color - 2),
    )


def _speed_kph(mech_data):
    a, b, c = (
        abs(value) for value in struct.unpack_from("<3i", mech_data, 0xF4)
    )
    largest = max(a, b, c)
    approx_raw = ((largest << 2) + a + b + c - largest) >> 2
    scaled = approx_raw // 10002
    magnitude = (scaled * 3) // 2
    reverse = struct.unpack_from("<i", mech_data, 0x2C)[0] < 0
    return (-magnitude if reverse else magnitude), reverse


def _scaled_throttle_extent(
    value,
    source_maximum,
    draw_maximum,
    fractional=True,
):
    if not fractional:
        source_maximum = max(1, int(source_maximum))
        draw_maximum = max(0, int(draw_maximum))
        value = max(0, min(int(value), source_maximum))
        if value == 0:
            return 0
        scaled = (value * draw_maximum + source_maximum // 2) // source_maximum
        return min(draw_maximum, scaled + 1)
    source_maximum = max(1, source_maximum)
    draw_maximum = max(0, draw_maximum)
    value = max(0.0, min(value, source_maximum))
    if value <= 0.0:
        return 0.0
    return min(draw_maximum, value * draw_maximum / source_maximum + 1.0)


def _append_horizontal_meter(
    fills,
    pane_left,
    pane_top,
    x,
    y,
    width,
    height,
    segments,
    enhanced=False,
):
    if width <= 0 or height <= 0:
        return
    cursor = 0
    for segment_width, base_color in segments:
        if enhanced:
            segment_width = max(
                0.0,
                min(float(segment_width), width - cursor),
            )
        else:
            segment_width = max(0, min(int(segment_width), width - cursor))
        if segment_width > 0:
            if enhanced:
                _append_meter_fill(
                    fills,
                    base_color,
                    pane_left + x + cursor,
                    pane_top + y,
                    pane_left + x + cursor + segment_width,
                    pane_top + y + height,
                    "y",
                )
            else:
                for shade_top, shade_bottom, color_index in _shade_bands(
                    height, base_color
                ):
                    _append_fill(
                        fills,
                        color_index,
                        pane_left + x + cursor,
                        pane_top + y + shade_top,
                        pane_left + x + cursor + segment_width,
                        pane_top + y + shade_bottom,
                    )
            cursor += segment_width
        if cursor >= width:
            break


def _append_clipped_split_vertical_meter(
    fills,
    x,
    y,
    current,
    maximum,
    width,
    current_color,
    remaining_color,
    clip_rect,
    *,
    enhanced,
):
    if width <= 0 or maximum <= 0:
        return
    clip_left, clip_top, clip_right, clip_bottom = clip_rect
    left = max(x, clip_left)
    top = max(y, clip_top)
    right = min(x + width, clip_right)
    bottom = min(y + maximum, clip_bottom)
    if right <= left or bottom <= top:
        return
    split = max(top, min(y + current, bottom))
    fills.append(
        CockpitHudVerticalMeter(
            current_color_index=max(0, min(0xFF, int(current_color))),
            remaining_color_index=max(0, min(0xFF, int(remaining_color))),
            left=float(left),
            top=float(top),
            right=float(right),
            split=float(split),
            bottom=float(bottom),
            enhanced=bool(enhanced),
        )
    )


def _append_clipped_horizontal_meter(
    fills,
    x,
    y,
    width,
    height,
    base_color,
    clip_rect,
):
    if width <= 0 or height <= 0:
        return
    for shade_top, shade_bottom, color_index in _shade_bands(height, base_color):
        _append_clipped_fill(
            fills,
            color_index,
            x,
            y + shade_top,
            x + width,
            y + shade_bottom,
            clip_rect,
        )


def _idiv(numerator, denominator):
    if denominator == 0:
        return 0
    quotient = abs(int(numerator)) // abs(int(denominator))
    return -quotient if (numerator < 0) != (denominator < 0) else quotient


def _video_noise_request(gamemem, pane_rect):
    record = int(gamemem.read_reloc_u32(ADDR_HUD_ANIMATION_TABLE))
    if record == 0:
        return None
    record_data = bytes(gamemem.read_runtime_bytes(record, 0x1C))
    native_state, _native_frame_count, ticks_per_frame, native_start_tick = (
        struct.unpack_from("<4i", record_data, 0x00)
    )
    flags, shape_base, _loaded_shape = struct.unpack_from(
        "<2iI",
        record_data,
        0x10,
    )
    now = int(gamemem.read_reloc_i32(ADDR_GAME_TICK))

    state = int(native_state)
    if state == 0:
        state = 2
    left, top, right, bottom = pane_rect
    return CockpitHudVideoNoise(
        record=record,
        resource_index=(
            shape_base + int(gamemem.read_reloc_u8(ADDR_HUD_STYLE_OFFSET))
        ),
        state=state,
        ticks_per_frame=int(ticks_per_frame),
        start_tick=int(native_start_tick),
        flags=int(flags),
        tick=now,
        x=left,
        y=top,
        clip_rect=(left, top, right + 1, bottom + 1),
        native_state=int(native_state),
    )


def _video_noise_frame_index(entry, frame_count):
    if frame_count <= 0:
        return None
    if entry.state == 3:
        return frame_count - 1
    start_tick = entry.start_tick if entry.start_tick != 0 else entry.tick
    elapsed_ticks = max(0, entry.tick - start_tick)
    elapsed_frame = (
        _idiv(elapsed_ticks, entry.ticks_per_frame)
        if entry.ticks_per_frame > 0
        else 0
    )
    if entry.flags & 0x01:
        if elapsed_frame >= frame_count:
            return frame_count - 1 if entry.flags & 0x02 else None
        return elapsed_frame
    return elapsed_frame % frame_count


def resolve_hud_video_noise_sprites(gamemem, panels):
    resolved = []
    for panel in panels:
        sprites = []
        for entry in panel.video_noise:
            try:
                loaded_shape = int(
                    gamemem.read_runtime_u32(entry.record + 0x18)
                )
                shape_table = loaded_shape or resolve_cached_shape(
                    gamemem,
                    entry.resource_index,
                )
                frame_count = (
                    hud_sprite_frame_count(gamemem, shape_table)
                    if shape_table
                    else preloaded_hud_frame_count(entry.resource_index)
                )
                if entry.native_state == 1:
                    continue
                frame = _video_noise_frame_index(entry, frame_count)
                if frame is None:
                    sprite = None
                elif shape_table:
                    sprite = decode_hud_sprite(gamemem, shape_table, frame)
                else:
                    sprite = preloaded_hud_sprite(entry.resource_index, frame)
                if sprite is not None:
                    sprites.append(
                        CockpitHudSprite(
                            sprite=sprite,
                            x=entry.x,
                            y=entry.y,
                            clip_rect=entry.clip_rect,
                            color_override=None,
                        )
                    )
            except Exception:
                pass
        if sprites:
            resolved.append((panel, tuple(sprites)))
    return tuple(resolved)


def _substitute_damaged_video(
    gamemem,
    panel_data,
    pane_rect,
    latch_address,
    video_noise,
):
    state = struct.unpack_from("<h", panel_data, 0x06)[0]
    # Snapshot runs immediately before the native HUD callback. Its current
    # latch determines this frame; native RNG/latch updates govern later ones.
    substitute = False
    if state == 1:
        if int(gamemem.read_reloc_i32(latch_address)) != 0:
            substitute = True
    elif state > 2:
        substitute = True

    if not substitute:
        return False
    # Native HUD runs immediately after this snapshot and synchronously loads
    # the animation shape on the first corrupted frame. Defer sprite decoding
    # to the existing post-HUD hook so that transition frame is not blank.
    request = _video_noise_request(gamemem, pane_rect)
    if request is not None:
        video_noise.append(request)
    return True


_hud_sprite = resource_hud_sprite
_mfd_damage_layout_delta = None
_mfd_damage_layout = None


def _append_hud_sprite(
    sprites,
    sprite,
    x,
    y,
    clip_rect,
    color_override=None,
    draw_width=None,
    draw_height=None,
    source_x=0.0,
    source_y=0.0,
    repeat_x=False,
    repeat_y=False,
):
    if sprite is None:
        return
    sprites.append(
        CockpitHudSprite(
            sprite=sprite,
            x=float(x),
            y=float(y),
            clip_rect=(
                clip_rect
                if isinstance(clip_rect, tuple)
                else tuple(int(value) for value in clip_rect)
            ),
            color_override=color_override,
            draw_width=(None if draw_width is None else int(draw_width)),
            draw_height=(None if draw_height is None else int(draw_height)),
            source_x=float(source_x),
            source_y=float(source_y),
            repeat_x=bool(repeat_x),
            repeat_y=bool(repeat_y),
        )
    )


def _cached_mfd_damage_layout(gamemem):
    global _mfd_damage_layout_delta, _mfd_damage_layout
    delta = int(gamemem.delta)
    if _mfd_damage_layout_delta == delta:
        return _mfd_damage_layout

    ui_data = bytes(
        gamemem.read_reloc_bytes(ADDR_MFD_DAMAGE_UI, MFD_DAMAGE_UI_SIZE)
    )
    part_map = struct.unpack_from("<16i", ui_data, 0x00)
    mapped_slots = [
        (slot, map_index)
        for slot, map_index in enumerate(part_map)
        if 1 <= map_index <= 64
    ]
    if not mapped_slots:
        layout = None
    else:
        base_slot = mapped_slots[0][0]
        base_window_offset = 0x78 + base_slot * 20
        base_clip_left, base_clip_top = struct.unpack_from(
            "<2i", ui_data, base_window_offset + 4
        )
        base_source_x, base_source_y = struct.unpack_from(
            "<2i", ui_data, 0x1B8 + base_slot * 8
        )
        overlays = []
        for slot, map_index in mapped_slots:
            window_offset = 0x78 + slot * 20
            clip_left, clip_top, clip_right, clip_bottom = struct.unpack_from(
                "<4i", ui_data, window_offset + 4
            )
            source_x, source_y = struct.unpack_from(
                "<2i", ui_data, 0x1B8 + slot * 8
            )
            overlays.append(
                (
                    map_index,
                    clip_left + source_x,
                    clip_top + source_y,
                    (
                        clip_left,
                        clip_top,
                        clip_right + 1,
                        clip_bottom + 1,
                    ),
                )
            )
        layout = (
            base_clip_left + base_source_x,
            base_clip_top + base_source_y,
            max(map_index for _slot, map_index in mapped_slots),
            tuple(overlays),
        )

    _mfd_damage_layout_delta = delta
    _mfd_damage_layout = layout
    return layout


def _append_mfd_damage_wireframe(
    gamemem,
    mech_data,
    pane_rect,
    panel,
):
    style_offset = int(gamemem.read_reloc_u8(ADDR_HUD_STYLE_OFFSET))
    shape_index = (
        int(gamemem.read_reloc_i32(ADDR_MFD_DAMAGE_SHAPE_BASE))
        + style_offset
    )
    sprite = _hud_sprite(gamemem, shape_index)
    if sprite is None:
        return False

    part_base = struct.unpack_from("<I", mech_data, 0x58)[0]
    if part_base == 0:
        return False
    layout = _cached_mfd_damage_layout(gamemem)
    if layout is None:
        return False
    base_x, base_y, max_part, overlays = layout

    left, top, right, bottom = pane_rect
    clip_rect = (left, top, right + 1, bottom + 1)
    _append_hud_sprite(
        panel.sprites,
        sprite,
        base_x,
        base_y,
        clip_rect,
    )

    part_data = bytes(gamemem.read_runtime_bytes(part_base, max_part * 0x28))
    damage_scale = int(gamemem.read_reloc_i32(ADDR_MFD_DAMAGE_SCALE))
    for map_index, draw_x, draw_y, overlay_clip in overlays:
        part_offset = (map_index - 1) * 0x28
        current_low, current_high, shared = struct.unpack_from(
            "<3i", part_data, part_offset
        )
        flags = struct.unpack_from("<H", part_data, part_offset + 0x26)[0]

        high_damage = 0
        high_scale = (flags & 0x00F0) >> 4
        if high_scale != 0 and damage_scale != 0:
            high_damage = 15 - _idiv(
                3 * (_idiv(current_high, damage_scale) + shared),
                high_scale << 16,
            )
            high_damage = max(0, min(15, high_damage))

        low_damage = 0
        low_scale = flags & 0x000F
        if low_scale != 0 and damage_scale != 0:
            low_damage = 15 - _idiv(
                3 * (_idiv(current_low, damage_scale) + shared),
                low_scale << 16,
            )
            low_damage = max(0, min(15, low_damage))

        damage = max(high_damage, low_damage)
        if flags & 0x2000:
            color_index = 0x00
        elif damage > 0x0B:
            color_index = 0x0B
        elif damage > 0:
            color_index = 0x03
        else:
            continue

        _append_hud_sprite(
            panel.sprites,
            sprite,
            draw_x,
            draw_y,
            overlay_clip,
            color_override=color_index,
        )
    return True


def _snapshot_mfd(
    gamemem,
    panel_data,
    mech,
    mech_data,
    player_body,
    pane_rect,
    panel,
    fully_started,
    enhanced_htal_meters,
    alt_htal_view,
    rear_camera_mirror,
):
    mode = int(gamemem.read_reloc_i32(ADDR_MFD_MODE))
    if not 1 <= mode <= 5:
        return None
    camera_mode = mode in (3, 4, 5)
    if not camera_mode and not fully_started:
        return None
    panel.content_role = "viewport" if camera_mode else "panel"
    texts = panel.texts
    outlines = panel.outlines
    fills = panel.fills
    sprites = panel.sprites
    video_noise = panel.video_noise

    left, top, right, bottom = pane_rect
    clip_rect = (left, top, right + 1, bottom + 1)

    if camera_mode and fully_started:
        label, text_slot = _MFD_CAMERA_LABELS[mode]
        frame_left = panel.reference_bounds[0]
        panel.attached_texts.append(
            CockpitHudAttachedText(
                panel_index=text_slot,
                text=label,
                color_index=0x06,
                left_offset=(left + right + 1) * 0.5 - frame_left,
                bottom_offset=ATTACHED_LABEL_TOP_OFFSET,
                horizontal_alignment="center",
            )
        )

    if camera_mode and _substitute_damaged_video(
        gamemem,
        panel_data,
        pane_rect,
        ADDR_MFD_GLITCH_LATCH,
        video_noise,
    ):
        return None

    if mode == 1:
        damage_drawn = _append_mfd_damage_wireframe(
            gamemem,
            mech_data,
            pane_rect,
            panel,
        )
        if damage_drawn:
            panel.content_role = "damage_sprite"
        return None

    part_base = struct.unpack_from("<I", mech_data, 0x58)[0]
    if mode == 2:
        label_data = bytes(gamemem.read_reloc_bytes(ADDR_MFD_HTAL_LABELS, 16))
        position_data = bytes(
            gamemem.read_reloc_bytes(ADDR_MFD_HTAL_POSITIONS, 36)
        )
        config = bytes(
            gamemem.read_reloc_bytes(
                ADDR_MFD_STATIC_CONFIG,
                MFD_STATIC_CONFIG_SIZE,
            )
        )
        base_width, bar_scale = struct.unpack_from("<2i", config, 0x00)
        half_width = _idiv(base_width, 2)
        denominator = struct.unpack_from("<i", position_data, 0x20)[0]
        htal_y_offset = 0.0
        htal_label_y_offset = 0.0
        htal_clip_rect = clip_rect
        if alt_htal_view and base_width > 0 and denominator != 0:
            maximum_bottom = None
            minimum_bar_top = None
            for part_index in range(8):
                origin_y = struct.unpack_from(
                    "<i", config, 0x0C + part_index * 8
                )[0]
                minimum_bar_top = (
                    origin_y
                    if minimum_bar_top is None
                    else min(minimum_bar_top, origin_y)
                )
                maximum_offsets = (0, 4) if part_index in (1, 2, 3) else (0,)
                for maximum_offset in maximum_offsets:
                    raw_max = struct.unpack_from(
                        "<i",
                        config,
                        0x148 + part_index * 8 + maximum_offset,
                    )[0]
                    if enhanced_htal_meters:
                        maximum = max(0.0, raw_max * bar_scale / denominator)
                    else:
                        maximum = max(
                            0,
                            _idiv(raw_max * bar_scale, denominator),
                        )
                    endpoint = origin_y + maximum
                    maximum_bottom = (
                        endpoint
                        if maximum_bottom is None
                        else max(maximum_bottom, endpoint)
                    )
            if maximum_bottom is not None:
                camera_label_bottom = (
                    bottom
                    + 1
                    + ATTACHED_LABEL_TOP_OFFSET
                    + ATTACHED_LABEL_ROW_HEIGHT
                )
                htal_y_offset = camera_label_bottom - (top + maximum_bottom)
                htal_clip_rect = (
                    left,
                    top,
                    right + 1,
                    camera_label_bottom,
                )
                panel.clip_text = False
                panel.clip_fills = False
                label_bottom = max(
                    struct.unpack_from("<i", position_data, index * 8 + 4)[0]
                    for index in range(4)
                ) + ATTACHED_LABEL_ROW_HEIGHT
                htal_label_y_offset = max(
                    0.0,
                    (minimum_bar_top - label_bottom) * 0.5,
                )

            _append_mfd_damage_wireframe(
                gamemem,
                mech_data,
                pane_rect,
                panel,
            )
        group_parts = ((0,), (1, 2, 3), (4, 5), (6, 7))
        group_centers = []
        htal_left = None
        htal_right = None
        for group in group_parts:
            group_left = None
            group_right = None
            for part_index in group:
                origin_x = struct.unpack_from(
                    "<i",
                    config,
                    0x08 + part_index * 8,
                )[0]
                part_width = (
                    half_width * 2
                    if part_index in (1, 2, 3)
                    else base_width
                )
                part_right = origin_x + part_width
                group_left = (
                    origin_x
                    if group_left is None
                    else min(group_left, origin_x)
                )
                group_right = (
                    part_right
                    if group_right is None
                    else max(group_right, part_right)
                )
            group_centers.append((group_left + group_right) * 0.5)
            htal_left = group_left if htal_left is None else min(
                htal_left, group_left
            )
            htal_right = group_right if htal_right is None else max(
                htal_right, group_right
            )
        if alt_htal_view:
            damage_center_x = left + (htal_left + htal_right) * 0.5
            damage_bottom_y = min(
                top
                + struct.unpack_from("<i", position_data, index * 8 + 4)[0]
                + htal_y_offset
                + htal_label_y_offset
                for index in range(4)
            )
            panel.damage_sprite_center_x = damage_center_x
            panel.damage_sprite_bottom_y = damage_bottom_y
            panel.damage_sprite_gap = ALT_HTAL_DAMAGE_GAP
        for index in range(4):
            label = _ascii_string(label_data[index * 4 : index * 4 + 4])
            _x, y = struct.unpack_from("<2i", position_data, index * 8)
            texts.append(
                CockpitHudText(
                    panel_index=MFD_TEXT_SLOT_BASE + index,
                    text=label,
                    color_index=0x06,
                    x=left + group_centers[index],
                    y=top + y + htal_y_offset + htal_label_y_offset,
                    horizontal_alignment="center",
                )
            )
        if part_base == 0:
            return

        if base_width <= 0 or denominator == 0:
            return
        part_data = bytes(gamemem.read_runtime_bytes(part_base, 8 * 0x28))
        for part_index in range(8):
            part_offset = part_index * 0x28
            origin_x, origin_y = struct.unpack_from(
                "<2i", config, 0x08 + part_index * 8
            )
            flags = struct.unpack_from("<H", part_data, part_offset + 0x26)[0]
            bars = ((0, 0, 0, base_width),)
            if part_index in (1, 2, 3):
                bars = (
                    (0, 0, 0, half_width),
                    (4, 4, half_width, half_width),
                )
            for value_offset, maximum_offset, x_offset, width in bars:
                raw_current = struct.unpack_from(
                    "<i", part_data, part_offset + value_offset
                )[0]
                raw_max = struct.unpack_from(
                    "<i", config, 0x148 + part_index * 8 + maximum_offset
                )[0]
                if enhanced_htal_meters:
                    maximum = max(
                        0.0,
                        raw_max * bar_scale / denominator,
                    )
                else:
                    maximum = max(
                        0,
                        _idiv(raw_max * bar_scale, denominator),
                    )
                if flags & 0x2000:
                    current = 0.0 if enhanced_htal_meters else 0
                    remaining_color = 0xF3
                else:
                    if enhanced_htal_meters:
                        current = (
                            raw_current * bar_scale / denominator
                        )
                    else:
                        current = _idiv(
                            raw_current * bar_scale,
                            denominator,
                        )
                    remaining_color = 0x0B if (raw_max >> 2) >= raw_current else 0x03
                current = max(0, min(current, maximum))
                x = left + origin_x + x_offset
                y = top + origin_y + htal_y_offset
                _append_clipped_split_vertical_meter(
                    fills,
                    x,
                    y,
                    current,
                    maximum,
                    width,
                    0x0F,
                    remaining_color,
                    htal_clip_rect,
                    enhanced=enhanced_htal_meters,
                )
        return None

    camera_view = snapshot_mfd_camera_view(
        gamemem,
        mode,
        player_body,
        pane_rect,
        rear_camera_mirror=rear_camera_mirror,
    )
    if camera_view is None:
        _append_fill(fills, 0x00, left, top, right + 1, bottom + 1)
        _append_one_pixel_border(fills, 0x06, left, top, right, bottom)
    return camera_view


def _read_hud_post_config(gamemem, config):
    if config == 0:
        return None
    config_data = bytes(gamemem.read_runtime_bytes(config + 0x30, 0x18))
    pane = struct.unpack_from("<I", config_data, 0x00)[0]
    if pane == 0:
        return None
    pane_width, pane_height = struct.unpack_from("<2h", config_data, 0x14)
    pane_data = bytes(gamemem.read_runtime_bytes(pane + 0x04, 0x10))
    left, top, right, bottom = struct.unpack_from("<4i", pane_data)
    if pane_width <= 0:
        pane_width = right - left + 1
    if pane_height <= 0:
        pane_height = bottom - top + 1
    return left, top, right, bottom, pane_width, pane_height


def _append_resource_sprite(
    gamemem,
    sprites,
    style_offset,
    resource_base,
    x,
    y,
    clip_rect,
):
    _append_hud_sprite(
        sprites,
        _hud_sprite(gamemem, resource_base + style_offset),
        x,
        y,
        clip_rect,
    )


def _append_scale_shape(
    gamemem,
    panel,
    native_style_offset,
    resource_base,
    x,
    y,
    clip_rect,
    edge_attachment=STATIC_SHAPE_FREE,
):
    # None selects the generated enhanced path. It must never probe the game's
    # resource cache or make visibility depend on native RLE readiness.
    if native_style_offset is None:
        panel.static_shapes.append(
            (
                HUD_SCALE_SHAPE_INDEX[resource_base],
                x,
                y,
                clip_rect,
                edge_attachment,
            )
        )
        return
    _append_resource_sprite(
        gamemem,
        panel.sprites,
        native_style_offset,
        resource_base,
        x,
        y,
        clip_rect,
    )


def _append_edge_indicator(
    indicators,
    x,
    y,
    clip_rect,
    direction,
    color_index,
):
    indicators.append(
        CockpitHudAtlasMarker(
            shape_index=TARGET_CARET_BY_DIRECTION[direction],
            x=float(x),
            y=float(y),
            clip_rect=tuple(int(value) for value in clip_rect),
            color_index=int(color_index),
        )
    )


def _round_fixed_16(value, scale):
    product = int(value) * int(scale)
    return (product >> 16) + ((product >> 15) & 1)


def _scale_fixed_16(value, scale):
    return value * scale / 65536.0


def _altimeter_offset(value, scale, fractional):
    if fractional:
        return value * scale / (100.0 * 65536.0)
    return _idiv(int(value) * int(scale), 100) >> 16


def _signed_remainder(value, divisor):
    return int(value) - _idiv(int(value), int(divisor)) * int(divisor)


def _signed_remainder_float(value, divisor):
    return math.fmod(value, divisor)


def _wrap_degrees_180(value):
    value = float(value)
    if value > 180:
        value -= 360
    if value < -180:
        value += 360
    return value


def _target_dispatch_inputs(mech_data, body_data, fractional=True):
    heading_raw = struct.unpack_from("<i", body_data, 0x60)[0]
    torso_raw = struct.unpack_from("<i", mech_data, 0x0C)[0]
    target_bearing_raw = struct.unpack_from("<i", body_data, 0xD4)[0]
    if fractional:
        heading = (heading_raw / 65536.0) % 360.0
        torso = _signed_remainder_float(torso_raw / 65536.0, 360)
        target_bearing = target_bearing_raw / 65536.0
        target_remainder = _signed_remainder_float(target_bearing, 360)
    else:
        heading = (heading_raw >> 16) % 360
        torso = _signed_remainder(torso_raw >> 16, 360)
        target_bearing = target_bearing_raw >> 16
        target_remainder = _signed_remainder(target_bearing, 360)
    body_relative = _wrap_degrees_180(target_remainder - heading)
    torso_relative = _wrap_degrees_180(body_relative - torso)
    vertical = _signed_remainder(
        struct.unpack_from("<i", body_data, 0xD8)[0]
        + struct.unpack_from("<i", mech_data, 0x1C)[0],
        ANGLE_FULL_TURN,
    )
    deltas = sorted(
        (
            abs(
                struct.unpack_from("<i", body_data, target_offset)[0]
                - struct.unpack_from("<i", body_data, body_offset)[0]
            )
            for target_offset, body_offset in (
                (0xC8, 0x50),
                (0xCC, 0x54),
                (0xD0, 0x58),
            )
        ),
        reverse=True,
    )
    target_range = (4 * deltas[0] + deltas[1] + deltas[2]) >> 2
    return heading, torso, body_relative, torso_relative, vertical, target_range


def _target_altitude(gamemem, handle):
    target_kind = int(handle) & 0x0F00
    target_index = int(handle) & 0x00FF
    if target_kind == 0x0100:
        return int(
            gamemem.read_reloc_i32(ADDR_DIRECT_TARGETS + target_index * 0x54 + 0x1C)
        )
    if target_kind == 0x0200:
        target_entity = int(
            gamemem.read_reloc_u32(ADDR_ENTITY_BODY_TABLE + target_index * 4)
        )
        if target_entity == 0:
            return None
        target_mech = int(gamemem.read_runtime_u32(target_entity + 0x20))
        if target_mech == 0:
            return None
        target_body = int(gamemem.read_runtime_u32(target_mech))
        if target_body == 0:
            return None
        return int(gamemem.read_runtime_i32(target_body + 0x54)) - int(
            gamemem.read_runtime_i32(target_mech + 0xCC)
        )
    if target_kind != 0x0400:
        return None
    object_index = int(
        gamemem.read_reloc_i32(
            ADDR_SECONDARY_TARGETS + target_index * 0x40 + 0x04
        )
    )
    maximum = int(gamemem.read_reloc_i32(ADDR_SECONDARY_MAX_INDEX))
    if object_index < 0 or object_index > maximum:
        return None
    record = ADDR_SECONDARY_POSITION_TABLE + object_index * 0x7C
    position = int(gamemem.read_reloc_u32(record + 0x1C))
    if position != 0:
        return int(gamemem.read_runtime_i32(position + 0x38))
    node = int(gamemem.read_reloc_u32(record + 0x20))
    return int(gamemem.read_runtime_i32(node + 0x64)) if node else 0


def _snapshot_compass_altimeter(
    gamemem,
    mech_data,
    mech_body,
    target_data,
    camera,
    compass_altimeter,
):
    enhanced_scales = str(compass_altimeter).strip().lower() == "enhanced"
    enable_data = bytes(gamemem.read_reloc_bytes(ADDR_COMPASS_ENABLE, 12))
    compass_enabled = struct.unpack_from("<I", enable_data, 0x00)[0] != 0
    altimeter_enabled = struct.unpack_from("<I", enable_data, 0x08)[0] != 0
    if not compass_enabled and not altimeter_enabled:
        return ()
    panels = []

    config_data = bytes(gamemem.read_reloc_bytes(ADDR_HUD_POST_CONFIGS, 8))
    altimeter_config, compass_config = struct.unpack("<2I", config_data)
    native_style_offset = (
        None
        if enhanced_scales
        else int(gamemem.read_reloc_u8(ADDR_HUD_STYLE_OFFSET))
    )
    body_data = bytes(gamemem.read_runtime_bytes(mech_body, 0xE0))
    target_inputs = _target_dispatch_inputs(
        mech_data,
        body_data,
        fractional=enhanced_scales,
    )

    if compass_enabled:
        config = _read_hud_post_config(gamemem, compass_config)
        if config is not None:
            left, top, right, bottom, pane_width, _pane_height = config
            clip_rect = (left, top, right + 1, bottom + 1)
            panel = _PanelBuilder(
                "compass_scaled" if enhanced_scales else "compass",
                clip_rect,
                content_role="panel" if enhanced_scales else "legacy",
            )
            fills, sprites = panel.fills, panel.sprites
            panels.append(panel)
            center_x, draw_y = struct.unpack(
                "<2i",
                bytes(gamemem.read_reloc_bytes(ADDR_COMPASS_CENTER, 8)),
            )
            bar_inset, compass_scale = struct.unpack(
                "<iI",
                bytes(gamemem.read_reloc_bytes(ADDR_COMPASS_BAR_CONFIG, 8)),
            )
            if enhanced_scales:
                heading = (
                    struct.unpack_from("<i", body_data, 0x60)[0] / 65536.0
                ) % 360.0
                heading_source_offset = _scale_fixed_16(
                    heading,
                    compass_scale,
                )
                generated_source_x = -(
                    center_x + COMPASS_NATIVE_STRIP_X_OFFSET
                ) + heading_source_offset
                strip_width = max(
                    1.0,
                    _scale_fixed_16(360.0, compass_scale),
                )
                panel.scale_origin_x = left - generated_source_x % strip_width
                panel.scale_origin_y = top + draw_y
            else:
                heading = (
                    struct.unpack_from("<i", body_data, 0x60)[0] >> 16
                ) % 360
                heading_source_offset = _round_fixed_16(
                    heading,
                    compass_scale,
                )
                draw_x1 = center_x + heading_source_offset
                strip_width = _round_fixed_16(360, compass_scale)
                draw_x2 = (
                    draw_x1 + strip_width
                    if pane_width > draw_x1
                    else draw_x1 - strip_width
                )
                native_compass_sprite = _hud_sprite(
                    gamemem,
                    0x19 + native_style_offset,
                )
                _append_hud_sprite(
                    sprites,
                    native_compass_sprite,
                    left + draw_x1,
                    top + draw_y,
                    clip_rect,
                )
                _append_hud_sprite(
                    sprites,
                    native_compass_sprite,
                    left + draw_x2,
                    top + draw_y,
                    clip_rect,
                )

            _heading, torso_yaw, body_bearing, torso_bearing, target_vertical, _range = (
                target_inputs
            )
            yaw_bar_offset = torso_yaw
            if yaw_bar_offset != 0 and bar_inset > 0:
                bar_x = center_x + min(0, yaw_bar_offset)
                _append_clipped_horizontal_meter(
                    fills,
                    left + bar_x,
                    top + draw_y - bar_inset,
                    abs(yaw_bar_offset),
                    bar_inset,
                    0x0F,
                    clip_rect,
                )
            _append_scale_shape(
                gamemem,
                panel,
                native_style_offset,
                0x13,
                left + center_x,
                top + draw_y,
                clip_rect,
            )

            target_status = body_data[0xDD]
            special_state = struct.unpack_from("<i", mech_data, 0xBC)[0] == 2
            normal_target = (
                (target_status & 0x0F) != 0 and (target_status & 0x10) == 0
            )
            if normal_target or special_state:
                bearing = body_bearing if (target_status & 0x01) else torso_bearing
                reticle_clip = _reticle_pane(gamemem)
                reticle_clip = (
                    reticle_clip[0],
                    reticle_clip[1],
                    reticle_clip[2] + 1,
                    reticle_clip[3] + 1,
                )
                marker_config = struct.unpack(
                    "<6i",
                    bytes(
                        gamemem.read_reloc_bytes(ADDR_COMPASS_TARGET_CONFIG, 0x18)
                    ),
                )
                top_offset, side_outset, side_height, _, _, bottom_offset = (
                    marker_config
                )
                if normal_target and not special_state:
                    marker_x = left + center_x
                    if target_vertical > -0x30000:
                        _append_scale_shape(
                            gamemem,
                            panel,
                            native_style_offset,
                            0x25,
                            marker_x,
                            top + draw_y - top_offset - 6,
                            reticle_clip,
                        )
                    if target_vertical < 0x30000:
                        _append_scale_shape(
                            gamemem,
                            panel,
                            native_style_offset,
                            0x1C,
                            marker_x,
                            top + draw_y + bottom_offset + 6,
                            reticle_clip,
                        )

                if bearing == 0:
                    target_shapes = (0x10, 0x16)
                    target_x = left + center_x
                else:
                    target_shapes = (0x0D,)
                    target_x = left + center_x + (
                        _scale_fixed_16(bearing, compass_scale)
                        if enhanced_scales
                        else _round_fixed_16(bearing, compass_scale)
                    )
                for resource_base in target_shapes:
                    _append_scale_shape(
                        gamemem,
                        panel,
                        native_style_offset,
                        resource_base,
                        target_x,
                        top + draw_y,
                        clip_rect,
                    )

                pane_width_i16 = pane_width
                caret_y = top + draw_y + (
                    COMPASS_LONG_TICK_HEIGHT // 2
                    if enhanced_scales
                    else _idiv(side_height + 1, 2)
                )
                if bearing > -3:
                    right_x = (
                        left + pane_width_i16
                        if enhanced_scales
                        else left + pane_width_i16 + side_outset - 1
                    )
                    _append_scale_shape(
                        gamemem,
                        panel,
                        native_style_offset,
                        0x22,
                        right_x,
                        caret_y,
                        reticle_clip,
                        STATIC_SHAPE_RIGHT_EDGE,
                    )
                    if bearing > 90:
                        _append_scale_shape(
                            gamemem,
                            panel,
                            native_style_offset,
                            0x22,
                            right_x + 1,
                            caret_y,
                            reticle_clip,
                            STATIC_SHAPE_RIGHT_EDGE,
                        )
                if bearing < 3:
                    left_x = left if enhanced_scales else left - side_outset
                    _append_scale_shape(
                        gamemem,
                        panel,
                        native_style_offset,
                        0x1F,
                        left_x,
                        caret_y,
                        reticle_clip,
                        STATIC_SHAPE_LEFT_EDGE,
                    )
                    if bearing < -90:
                        _append_scale_shape(
                            gamemem,
                            panel,
                            native_style_offset,
                            0x1F,
                            left_x - 1,
                            caret_y,
                            reticle_clip,
                            STATIC_SHAPE_LEFT_EDGE,
                        )

    if altimeter_enabled:
        config = _read_hud_post_config(gamemem, altimeter_config)
        if config is None:
            return tuple(panels)
        left, top, right, bottom, _pane_width, _pane_height = config
        clip_rect = (left, top, right + 1, bottom + 1)
        panel = _PanelBuilder(
            "altimeter_scaled" if enhanced_scales else "altimeter",
            clip_rect,
            content_role="panel" if enhanced_scales else "legacy",
        )
        fills, sprites = panel.fills, panel.sprites
        panels.append(panel)
        scale_draw_x, ref_y = struct.unpack(
            "<2i",
            bytes(gamemem.read_reloc_bytes(ADDR_ALTIMETER_LAYOUT, 8)),
        )
        indicator_x, alt_scale, ref_marker_x, target_marker_x = struct.unpack(
            "<4i",
            bytes(gamemem.read_reloc_bytes(ADDR_ALTIMETER_CONFIG, 16)),
        )
        raw_altitude = struct.unpack_from("<i", body_data, 0x54)[0]
        ground_reference = struct.unpack_from("<i", mech_data, 0xCC)[0]
        altitude_offset = raw_altitude - ground_reference
        draw_y = ref_y + _altimeter_offset(
            altitude_offset - 0x4E84,
            alt_scale,
            enhanced_scales,
        )
        scale_resource = 0x07
        if draw_y > ref_y:
            scale_resource = 0x0115
            draw_y = ref_y
        if enhanced_scales:
            panel.scale_origin_x = (
                left + scale_draw_x + ALTIMETER_ENHANCED_X_OFFSET
            )
            panel.scale_origin_y = top + draw_y
        else:
            _append_resource_sprite(
                gamemem,
                sprites,
                native_style_offset,
                scale_resource,
                left + scale_draw_x,
                top + draw_y,
                clip_rect,
            )
        _append_scale_shape(
            gamemem,
            panel,
            native_style_offset,
            0x01,
            left + ref_marker_x,
            top + ref_y,
            clip_rect,
        )
        ground_under_mech = struct.unpack_from("<i", body_data, 0x74)[0]
        indicator_altitude = altitude_offset - ground_under_mech
        indicator_y = ref_y + _altimeter_offset(
            indicator_altitude,
            alt_scale,
            enhanced_scales,
        )
        _append_scale_shape(
            gamemem,
            panel,
            native_style_offset,
            0x04,
            left + indicator_x,
            top + indicator_y,
            clip_rect,
        )
        target_status = body_data[0xDD]
        if (target_status & 0x0F) != 0 and (target_status & 0x10) == 0:
            handle = _target_handle(target_data)
            target_altitude = _target_altitude(gamemem, handle)
            if target_altitude is not None:
                player_altitude = raw_altitude - ground_reference
                marker_y = ref_y + _altimeter_offset(
                    player_altitude - target_altitude,
                    alt_scale,
                    enhanced_scales,
                )
                pane_height = struct.unpack(
                    "<h",
                    bytes(
                        gamemem.read_runtime_bytes(altimeter_config + 0x46, 2)
                    ),
                )[0]
                if marker_y < 0:
                    marker_y = 0
                    marker_base = 0x25
                elif marker_y > pane_height:
                    marker_y = pane_height
                    marker_base = 0x1C
                else:
                    marker_base = 0x1F
                target_x = int(
                    gamemem.read_reloc_i32(ADDR_ALTIMETER_TARGET_X)
                ) + target_marker_x
                _append_scale_shape(
                    gamemem,
                    panel,
                    native_style_offset,
                    marker_base,
                    left + target_x,
                    top + marker_y,
                    clip_rect,
                )
    return tuple(panels)


def _target_handle(target_data):
    return struct.unpack_from("<I", target_data, 0x14)[0]


def _append_target_panel_background(fills, pane_rect):
    left, top, right, bottom = pane_rect
    _append_fill(fills, 0x00, left, top, right + 1, bottom + 1)
    _append_one_pixel_border(fills, 0x08, left, top, right, bottom)


def _snapshot_target_panel(
    gamemem,
    panel_data,
    pane_rect,
    target_data,
    fully_started,
    panel,
):
    fills = panel.fills
    sprites = panel.sprites
    video_noise = panel.video_noise
    display_mode = int(gamemem.read_reloc_u32(ADDR_TARGET_DISPLAY_ENABLE))
    if display_mode == 0:
        return None

    if fully_started:
        pane_rect = TARGET_VIEW_PANE_RECT
    left, top, right, bottom = pane_rect
    clip_rect = (left, top, right + 1, bottom + 1)

    if fully_started and _substitute_damaged_video(
        gamemem,
        panel_data,
        pane_rect,
        ADDR_TARGET_GLITCH_LATCH,
        video_noise,
    ):
        return None

    # Native startup and shutdown animate an empty target-display pane even
    # when a valid target is already selected. Target imagery appears only
    # after the panel reaches its steady callback.
    if not fully_started:
        _append_target_panel_background(fills, pane_rect)
        return None

    handle = _target_handle(target_data)
    target_kind = handle & 0x0F00
    if (
        display_mode not in (1, 2)
        or target_kind == 0
        or (handle & 0x1000)
    ):
        _append_target_panel_background(fills, pane_rect)
        return None

    pane_width = right - left + 1
    panel_width = struct.unpack_from("<i", panel_data, 0x44)[0]
    display_width = panel_width if 0 < panel_width <= pane_width else pane_width
    center_x = left + (display_width >> 1)
    center_y = top + ((bottom - top) >> 1)
    if target_kind != 0x0100:
        target_state = snapshot_target_camera_view(
            gamemem,
            target_data,
            pane_rect,
            display_mode,
        )
        if target_state.view is not None:
            return target_state.view
        return None

    _append_target_panel_background(fills, pane_rect)
    target_index = handle & 0x00FF
    target_flags = int(
        gamemem.read_reloc_u16(ADDR_DIRECT_TARGETS + target_index * 0x54 + 0x24)
    )
    _append_resource_sprite(
        gamemem,
        sprites,
        int(gamemem.read_reloc_u8(ADDR_HUD_STYLE_OFFSET)),
        0x103 if (target_flags & 0x20) else 0x100,
        center_x,
        center_y,
        clip_rect,
    )
    return None


def _target_name_and_color(gamemem, handle):
    target_kind = int(handle) & 0x0F00
    target_index = int(handle) & 0x00FF
    if target_kind == 0x0100:
        record = ADDR_DIRECT_TARGETS + target_index * 0x54
        record_data = bytes(gamemem.read_reloc_bytes(record, 0x54))
        flags = struct.unpack_from("<H", record_data, 0x24)[0]
        use_default = (flags & 0x0020) == 0 and (flags & 0x0100) != 0
        name = "" if use_default else _ascii_string(record_data[0x28:0x54])
        return name, (0x01 if (flags & 0x0020) else 0x02)

    if target_kind == 0x0200:
        target_body = int(
            gamemem.read_reloc_u32(ADDR_ENTITY_BODY_TABLE + target_index * 4)
        )
        if target_body == 0:
            return "", NORMAL_HUD_TEXT_COLOR
        name = _ascii_string(gamemem.read_runtime_bytes(target_body + 0xE8, 64))
        entity_data = bytes(gamemem.read_runtime_bytes(target_body + 0x08, 0x08))
        slot = struct.unpack_from("<I", entity_data, 0x00)[0]
        sub_index = max(
            0,
            min(
                2,
                int(gamemem.read_reloc_u8(ADDR_PRIMARY_CLASSIFICATION + slot * 0x26)),
            ),
        )
        return name, (0x0E, 0x0A, 0x06)[sub_index]

    if target_kind == 0x0400:
        record = ADDR_SECONDARY_TARGETS + target_index * 0x40
        record_data = bytes(gamemem.read_reloc_bytes(record, 0x40))
        name = _ascii_string(record_data[0x14:0x40])
        reference = struct.unpack_from("<i", record_data, 0x0C)[0]
        if reference < 0:
            sub_index = 2
        else:
            sub_index = max(
                0,
                min(
                    2,
                    int(gamemem.read_reloc_u8(ADDR_SECONDARY_CLASSIFICATION + reference)),
                ),
            )
        return name, (0x0E, 0x0A, 0x06)[sub_index]

    return "", NORMAL_HUD_TEXT_COLOR


def _target_range_text(gamemem, player_entity):
    range_int = _idiv(int(gamemem.read_runtime_i32(player_entity + 0xC4)), 100)
    if range_int > 1000:
        return f"{range_int * 0.001:3.2f}k"
    return f"{range_int}m"


def _snapshot_target_text_panel(
    gamemem,
    player_entity,
    pane_rect,
    target_data,
    panel,
):
    handle = _target_handle(target_data)
    target_kind = handle & 0x0F00
    if target_kind == 0 or (handle & 0x1000):
        return
    target_status = int(gamemem.read_runtime_u8(player_entity + 0xDD))
    if target_status & 0x10:
        return
    left, _top, _right, _bottom = pane_rect
    frame_left = panel.reference_bounds[0]
    name, name_color = _target_name_and_color(gamemem, handle)
    if name:
        panel.attached_texts.append(
            CockpitHudAttachedText(
                panel_index=TARGET_NAME_TEXT_SLOT,
                text=name,
                color_index=name_color,
                left_offset=left - frame_left,
                bottom_offset=ATTACHED_LABEL_TOP_OFFSET,
            )
        )
    panel.attached_texts.append(
        CockpitHudAttachedText(
            panel_index=TARGET_RANGE_TEXT_SLOT,
            text=_target_range_text(gamemem, player_entity),
            color_index=NORMAL_HUD_TEXT_COLOR,
            left_offset=left - frame_left,
            bottom_offset=(
                ATTACHED_LABEL_TOP_OFFSET + ATTACHED_LABEL_ROW_HEIGHT
            ),
        )
    )


def _snapshot_autopilot_panel(
    gamemem,
    panel_index,
    panel_data,
    pane_rect,
    mech_data,
    texts,
):
    autopilot_state = struct.unpack_from("<i", mech_data, 0xBC)[0]
    text_position = struct.unpack_from("<I", panel_data, 0x34)[0]
    if autopilot_state not in (1, 2) or text_position == 0:
        return
    local_x, local_y = struct.unpack(
        "<2i",
        bytes(gamemem.read_runtime_bytes(text_position, 0x08)),
    )
    label = _ascii_string(gamemem.read_reloc_bytes(ADDR_AUTOPILOT_TEXT, 16))
    texts.append(
        CockpitHudText(
            panel_index=panel_index,
            text=label or "AUTOPILOT",
            color_index=NORMAL_HUD_TEXT_COLOR,
            x=pane_rect[0] + local_x,
            y=pane_rect[1] + local_y,
        )
    )


def _hud_transition_timeline(lifecycle, hud_mode, previous_hud_mode):
    now = float(getattr(lifecycle, "time", 0.0))

    initial_state = getattr(
        lifecycle,
        "mw2_hud_initial_startup_state",
        "complete",
    )
    if initial_state == "armed":
        if hud_mode != 1:
            return "hidden", 0.0
        setattr(lifecycle, "mw2_hud_initial_startup_state", "complete")
        setattr(lifecycle, "mw2_hud_transition_start_time", now)
        return "startup", 0.0

    if hud_mode == 1:
        phase = "startup"
        if previous_hud_mode != 1:
            setattr(lifecycle, "mw2_hud_transition_start_time", now)
    elif hud_mode in (0, 3):
        phase = "shutdown"
        if previous_hud_mode not in (0, 3):
            setattr(lifecycle, "mw2_hud_transition_start_time", now)
    else:
        return "steady", 1.0
    started = float(
        getattr(lifecycle, "mw2_hud_transition_start_time", now)
    )
    progress = (now - started) / HUD_TRANSITION_DURATION_SECONDS
    return phase, max(0.0, min(1.0, progress))


def _panel_transition_extent(phase, progress):
    progress = max(0.0, min(1.0, float(progress)))
    if phase == "startup":
        return min(1.0, progress * 2.0), max(0.0, progress * 2.0 - 1.0)
    if phase == "shutdown":
        return max(0.0, 2.0 - progress * 2.0), max(0.0, 1.0 - progress * 2.0)
    return 1.0, 1.0


def _panel_destination_bounds(gamemem, panel_data, fallback):
    controller = struct.unpack_from("<I", panel_data, 0x38)[0]
    if controller == 0:
        return fallback
    descriptor = int(gamemem.read_runtime_u32(controller + 0x04))
    if descriptor == 0:
        return fallback
    destination = int(gamemem.read_runtime_u32(descriptor + 0x08))
    if destination == 0:
        return fallback
    left, top, right, bottom = struct.unpack(
        "<4i",
        bytes(gamemem.read_runtime_bytes(destination + 0x04, 0x10)),
    )
    if left > right or top > bottom:
        return fallback
    return left, top, right, bottom




def _reticle_pane(gamemem):
    pane_data = bytes(gamemem.read_reloc_bytes(ADDR_RETICLE_PANE + 0x04, 0x10))
    left, top, right, bottom = struct.unpack("<4i", pane_data)
    return left, top, right, bottom


def _center_reticle_base(gamemem, mech_data, target_data, target_inputs):
    weapon_base = struct.unpack_from("<I", mech_data, 0x54)[0]
    weapon_index = struct.unpack_from("<i", mech_data, 0xAC)[0]
    if weapon_base == 0 or weapon_index < 0:
        return 0x67
    weapon_data = bytes(
        gamemem.read_runtime_bytes(weapon_base + weapon_index * WEAPON_SIZE + 0x04, 8)
    )
    weapon_id, weapon_state = struct.unpack("<2i", weapon_data)
    if weapon_state != 1 or weapon_id < 0:
        return 0x67

    definition = bytes(
        gamemem.read_reloc_bytes(
            ADDR_WEAPON_DEFINITIONS + weapon_id * 0x58,
            0x44,
        )
    )
    weapon_class = struct.unpack_from("<i", definition, 0x00)[0]
    alternate_mode = struct.unpack_from("<i", definition, 0x18)[0] != 0
    if alternate_mode:
        reticle_status = struct.unpack_from("<I", mech_data, 0x10C)[0]
        if reticle_status & 0x0080:
            return 0x61
        if reticle_status & 0x8000:
            return 0x70
        return 0x6D
    handle = _target_handle(target_data)
    target_status = (handle >> 8) & 0xFF
    _heading, _torso, _body_bearing, torso_bearing, vertical, target_range = (
        target_inputs
    )
    lower_range, upper_range = struct.unpack_from("<2i", definition, 0x3C)
    valid_solution = (
        handle != 0
        and (target_status & 0x01) == 0
        and (target_status & 0x10) == 0
        and lower_range < target_range < upper_range
        and -3 < torso_bearing < 3
        and -0x30000 < vertical < 0x30000
    )
    if weapon_class == 3:
        return 0x6A if valid_solution else 0x6D
    return 0x73 if valid_solution else 0x76


def _reticle_aim_point(gamemem, mech_data, mech_body):
    body_data = bytes(gamemem.read_runtime_bytes(mech_body + 0x44, 0x60))
    aim_node = struct.unpack_from("<I", body_data, 0x00)[0]
    distance = struct.unpack_from("<i", body_data, 0x5C)[0]
    if aim_node == 0:
        return None
    node_data = bytes(gamemem.read_runtime_bytes(aim_node + 0x3C, 0x38))
    matrix = struct.unpack_from("<9i", node_data, 0x00)
    origin_x, origin_y, origin_z = struct.unpack_from("<3i", node_data, 0x24)
    offset_pointer = int(gamemem.read_reloc_u32(ADDR_AIM_Y_OFFSET))
    if offset_pointer != 0:
        origin_y += int(gamemem.read_runtime_i32(offset_pointer))

    # Only the aim direction is required here. For the game's row-major FP29
    # node matrix, local +Z is entries 2/5/8. Its yaw plus the mech's explicit
    # turret pitch reproduces the forward ray; roll does not change local +Z.
    node_yaw = math.atan2(matrix[2], matrix[8])
    pitch = struct.unpack_from("<i", mech_data, 0x1C)[0] * (
        math.tau / ANGLE_FULL_TURN
    )
    cos_pitch = math.cos(pitch)
    direction = (
        int(round(math.sin(node_yaw) * cos_pitch * 65536.0)),
        int(round(-math.sin(pitch) * 65536.0)),
        int(round(math.cos(node_yaw) * cos_pitch * 65536.0)),
    )
    return (
        origin_x + _round_fixed_16(direction[0], distance),
        origin_y + _round_fixed_16(direction[1], distance),
        origin_z + _round_fixed_16(direction[2], distance),
    )


def _target_projector(gamemem):
    data = bytes(gamemem.read_reloc_bytes(ADDR_TARGET_PROJECTOR, 0xB8))

    def i32(address):
        return struct.unpack_from("<i", data, address - ADDR_TARGET_PROJECTOR)[0]

    return {
        "x_shift": i32(0x0015FF48),
        "y_shift": i32(0x0015FF68),
        "center_y": i32(0x0015FF6C),
        "center_x": i32(0x0015FF70),
        "top": i32(0x0015FFA0),
        "camera_y": i32(0x0015FFA4),
        "camera_x": i32(0x0015FFA8),
        "camera_z": i32(0x0015FFAC),
        "bottom": i32(0x0015FFB0),
        "left": i32(0x0015FFB4),
        "depth_x": i32(0x0015FFBC),
        "depth_z": i32(0x0015FFC0),
        "depth_y": i32(0x0015FFC4),
        "near": i32(0x0015FFC8),
        "right": i32(0x0015FFD4),
        "view_y_z": i32(0x0015FFE8),
        "view_y_y": i32(0x0015FFEC),
        "view_y_x": i32(0x0015FFF0),
        "view_x_z": i32(0x0015FFF4),
        "view_x_y": i32(0x0015FFF8),
        "view_x_x": i32(0x0015FFFC),
    }


def _round_fixed_27(value):
    return (int(value) >> 27) + ((int(value) >> 26) & 1)


def _clip_direction_to_pane(direction_x, direction_y, pane_rect):
    left, top, right, bottom = pane_rect
    center_x = left + ((right - left + 1) >> 1)
    center_y = top + ((bottom - top + 1) >> 1)
    direction_x = float(direction_x)
    direction_y = float(direction_y)
    if abs(direction_x) < 1.0e-12 and abs(direction_y) < 1.0e-12:
        direction_y = -1.0
    intersections = []
    if direction_x > 0:
        intersections.append(((right - center_x) / direction_x, "right"))
    elif direction_x < 0:
        intersections.append(((left - center_x) / direction_x, "left"))
    if direction_y > 0:
        intersections.append(((bottom - center_y) / direction_y, "down"))
    elif direction_y < 0:
        intersections.append(((top - center_y) / direction_y, "up"))
    positive = [entry for entry in intersections if entry[0] >= 0.0]
    if not positive:
        return center_x, center_y, "up"
    scale, edge = min(positive, key=lambda entry: entry[0])
    return (
        center_x + direction_x * scale,
        center_y + direction_y * scale,
        edge,
    )


def _clip_target_to_pane(screen_x, screen_y, pane_rect):
    left, top, right, bottom = pane_rect
    center_x = left + ((right - left + 1) >> 1)
    center_y = top + ((bottom - top + 1) >> 1)
    clipped_x, clipped_y, _edge = _clip_direction_to_pane(
        float(screen_x) - center_x,
        float(screen_y) - center_y,
        pane_rect,
    )
    return clipped_x, clipped_y


def _project_target(projector, world_xyz, pane_rect, clip_offscreen=True):
    world_x, world_y, world_z = (int(value) for value in world_xyz)
    dx = world_x - projector["camera_x"]
    dy = world_y - projector["camera_y"]
    dz = world_z - projector["camera_z"]
    view_x = _round_fixed_27(
        dx * projector["view_x_x"]
        + dy * projector["view_x_y"]
        + dz * projector["view_x_z"]
    )
    view_y = _round_fixed_27(
        dx * projector["view_y_x"]
        + dy * projector["view_y_y"]
        + dz * projector["view_y_z"]
    )
    depth = _round_fixed_27(
        dx * projector["depth_x"]
        + dy * projector["depth_y"]
        + dz * projector["depth_z"]
    )
    behind = depth <= projector["near"]
    divisor = abs(depth) if behind else depth
    if divisor == 0:
        divisor = 1

    def project(value, shift):
        return (float(value) * float(1 << int(shift))) / (float(divisor) * 4.0)

    screen_x = projector["center_x"] + project(view_x, projector["x_shift"])
    screen_y = (
        projector["bottom"]
        - projector["top"]
        - (projector["center_y"] + project(view_y, projector["y_shift"]))
    )
    on_screen = (
        not behind
        and projector["left"] <= screen_x <= projector["right"]
        and projector["top"] <= screen_y <= projector["bottom"]
    )
    if not on_screen and clip_offscreen:
        screen_x, screen_y = _clip_target_to_pane(screen_x, screen_y, pane_rect)
    return screen_x, screen_y, depth, on_screen


def _project_scene_target(
    camera,
    world_xyz,
    pane_rect,
    *,
    max_horizontal_fov_degrees,
    clip_offscreen=True,
    viewport_size=None,
    projection_info=None,
):
    """Return fractional native-space coordinates from the scene camera."""
    world = tuple(float(value) / 65536.0 for value in world_xyz)
    position = camera["position"]
    delta = tuple(world[axis] - position[axis] for axis in range(3))
    view_x = sum(delta[axis] * camera["right"][axis] for axis in range(3))
    view_y = sum(delta[axis] * camera["up"][axis] for axis in range(3))
    depth_world = sum(
        delta[axis] * camera["forward"][axis] for axis in range(3)
    )
    focal = max(1.0, float(camera.get("focal_length_pixels", 512.0)))
    behind = depth_world <= 0.0
    divisor = abs(depth_world) if behind else depth_world
    if divisor < 1.0e-12:
        divisor = 1.0e-12
    screen_x = 512.0 + focal * view_x / divisor
    screen_y = 384.0 - focal * view_y / divisor
    if viewport_size is None:
        left, top, right, bottom = pane_rect
        on_screen = (
            not behind
            and left <= screen_x <= right
            and top <= screen_y <= bottom
        )
    else:
        viewport_width = max(1, int(viewport_size[0]))
        viewport_height = max(1, int(viewport_size[1]))
        if projection_info is None:
            projection_info = perspective_projection_info(
                viewport_width,
                viewport_height,
                focal_length_pixels=focal,
                max_horizontal_fov_degrees=max_horizontal_fov_degrees,
            )
        output_focal = projection_info.output_focal_length_pixels
        output_x = viewport_width * 0.5 + output_focal * view_x / divisor
        output_y = viewport_height * 0.5 - output_focal * view_y / divisor
        on_screen = (
            not behind
            and 0.0 <= output_x < viewport_width
            and 0.0 <= output_y < viewport_height
        )
    if not on_screen and clip_offscreen:
        screen_x, screen_y = _clip_target_to_pane(
            screen_x, screen_y, pane_rect
        )
    # Native target sizing consumes camera-forward depth in its exported
    # dot-product-times-four convention.
    depth_fixed = depth_world * 65536.0 * 4.0
    return screen_x, screen_y, depth_fixed, on_screen


def _resolve_target_marker(gamemem, target_kind, target_index, target_data):
    if target_kind == 0x0100:
        return struct.unpack_from("<3i", target_data, 0x00), 0, None, False
    if target_kind == 0x0200:
        target_body = int(
            gamemem.read_reloc_u32(ADDR_ENTITY_BODY_TABLE + target_index * 4)
        )
        if target_body == 0:
            return None
        body_data = bytes(gamemem.read_runtime_bytes(target_body + 0x08, 0x54))
        slot = struct.unpack_from("<I", body_data, 0x00)[0]
        target_mech = struct.unpack_from("<I", body_data, 0x18)[0]
        sub_index = int(
            gamemem.read_reloc_u8(ADDR_PRIMARY_CLASSIFICATION + slot * 0x26)
        )
        if target_mech == 0:
            return None
        extent = int(gamemem.read_runtime_i32(target_mech + 0xE8))
        return (
            struct.unpack_from("<3i", body_data, 0x48),
            max(0, min(2, sub_index)),
            extent,
            False,
        )
    if target_kind != 0x0400:
        return None
    secondary = ADDR_SECONDARY_TARGETS + target_index * 0x40
    entry = bytes(gamemem.read_reloc_bytes(secondary, 0x10))
    object_index = struct.unpack_from("<i", entry, 0x04)[0]
    maximum = int(gamemem.read_reloc_i32(ADDR_SECONDARY_MAX_INDEX))
    if object_index < 0 or object_index >= maximum:
        return None
    object_pointer = int(
        gamemem.read_reloc_u32(
            ADDR_SECONDARY_POSITION_TABLE + object_index * 0x7C + 0x20
        )
    )
    if object_pointer == 0:
        return None
    bounds = int(gamemem.read_runtime_u32(object_pointer + 0x6C))
    if bounds == 0:
        return None
    bounds_data = bytes(gamemem.read_runtime_bytes(bounds + 0x34, 0x10))
    reference = struct.unpack_from("<i", entry, 0x0C)[0]
    sub_index = (
        2
        if reference < 0
        else int(
            gamemem.read_reloc_u8(ADDR_SECONDARY_CLASSIFICATION + reference)
        )
    )
    return (
        struct.unpack_from("<3i", bounds_data, 0x00),
        max(0, min(2, sub_index)),
        struct.unpack_from("<i", bounds_data, 0x0C)[0] >> 1,
        True,
    )


def _target_marker_radius(gamemem, extent, depth, clamp_maximum):
    if extent is None or depth == 0:
        return 12
    camera = int(gamemem.read_reloc_u32(ADDR_ACTIVE_CAMERA))
    if camera == 0:
        return 12
    camera_data = bytes(gamemem.read_runtime_bytes(camera + 0x84, 0x14))
    maximum = struct.unpack_from("<i", camera_data, 0x00)[0] >> 1
    projection_scale = struct.unpack_from("<i", camera_data, 0x10)[0]
    radius = (float(extent) * float(projection_scale)) / (
        16384.0 * float(depth)
    )
    if clamp_maximum and maximum > 0:
        radius = min(radius, maximum)
    return radius


def _snapshot_targeting(
    gamemem,
    mech_data,
    mech_body,
    target_data,
    camera,
    scene_sprites,
    scene_markers,
    edge_indicators,
    viewport_size,
    max_horizontal_fov_degrees,
):
    gates = bytes(gamemem.read_reloc_bytes(ADDR_RETICLE_ENABLE, 8))
    reticle_enabled, markers_enabled = struct.unpack("<2I", gates)
    is_satellite = int(gamemem.read_reloc_u8(ADDR_RADAR_MODE)) == 4
    reticle_enabled = (
        reticle_enabled
        and int(gamemem.read_reloc_i32(ADDR_CAMERA_RETICLE_GATE)) != 0
        and not is_satellite
    )
    markers_enabled = markers_enabled and not is_satellite
    if not reticle_enabled and not markers_enabled:
        return
    pane_rect = _reticle_pane(gamemem)
    left, top, right, bottom = pane_rect
    clip_rect = (left, top, right + 1, bottom + 1)
    style_offset = (
        int(gamemem.read_reloc_u8(ADDR_HUD_STYLE_OFFSET))
        if reticle_enabled
        else 0
    )
    body_data = bytes(gamemem.read_runtime_bytes(mech_body, 0xE0))
    target_inputs = _target_dispatch_inputs(mech_data, body_data)
    projector = _target_projector(gamemem)
    native_focal = max(1.0, float(
        (camera or {}).get("focal_length_pixels", 512.0)
    ))
    scene_projection = perspective_projection_info(
        viewport_size[0],
        viewport_size[1],
        focal_length_pixels=native_focal,
        max_horizontal_fov_degrees=max_horizontal_fov_degrees,
    )

    def project(world_xyz, clip_offscreen=True):
        if camera is not None and not camera.get("satellite_view", False):
            return _project_scene_target(
                camera,
                world_xyz,
                pane_rect,
                clip_offscreen=clip_offscreen,
                viewport_size=viewport_size,
                max_horizontal_fov_degrees=max_horizontal_fov_degrees,
                projection_info=scene_projection,
            )
        return _project_target(
            projector,
            world_xyz,
            pane_rect,
            clip_offscreen=clip_offscreen,
        )

    if reticle_enabled:
        aim_point = _reticle_aim_point(gamemem, mech_data, mech_body)
        if aim_point is not None:
            reticle_projection = project(
                aim_point,
                clip_offscreen=False,
            )
            if reticle_projection[3]:
                _append_resource_sprite(
                    gamemem,
                    scene_sprites,
                    style_offset,
                    _center_reticle_base(
                        gamemem,
                        mech_data,
                        target_data,
                        target_inputs,
                    ),
                    reticle_projection[0],
                    reticle_projection[1],
                    clip_rect,
                )

    handle = _target_handle(target_data)
    if not markers_enabled or handle == 0 or (handle & 0x1000):
        return
    target_kind = handle & 0x0F00
    target_index = handle & 0x00FF
    if target_kind not in (0x0100, 0x0200, 0x0400):
        return
    target_info = _resolve_target_marker(
        gamemem,
        target_kind,
        target_index,
        target_data,
    )
    if target_info is None:
        return
    world_position, sub_index, extent, clamp_maximum = target_info
    projection = project(world_position, clip_offscreen=False)
    screen_x, screen_y, depth, on_screen = projection

    if not on_screen:
        if camera is not None and not camera.get("satellite_view", False):
            world = tuple(float(value) / 65536.0 for value in world_position)
            delta = tuple(
                world[axis] - camera["position"][axis] for axis in range(3)
            )
            direction_x = sum(
                delta[axis] * camera["right"][axis] for axis in range(3)
            )
            direction_y = -sum(
                delta[axis] * camera["up"][axis] for axis in range(3)
            )
            if abs(direction_x) < 1.0e-12 and abs(direction_y) < 1.0e-12:
                direction_y = -1.0
            screen_x, screen_y, direction = _clip_direction_to_pane(
                direction_x,
                direction_y,
                pane_rect,
            )
        else:
            center_x = (pane_rect[0] + pane_rect[2] + 1) * 0.5
            center_y = (pane_rect[1] + pane_rect[3] + 1) * 0.5
            direction_x = screen_x - center_x
            direction_y = screen_y - center_y
            if direction_x == 0 and direction_y == 0:
                direction_y = -1
            screen_x, screen_y, direction = _clip_direction_to_pane(
                direction_x,
                direction_y,
                pane_rect,
            )
        _append_edge_indicator(
            edge_indicators,
            screen_x,
            screen_y,
            clip_rect,
            direction,
            0x0E if target_kind == 0x0100 else (0x0E, 0x0A, 0x06)[sub_index],
        )
        return

    if target_kind == 0x0100:
        scene_markers.append(
            CockpitHudAtlasMarker(
                TARGET_NAV_CIRCLE,
                float(screen_x),
                float(screen_y),
                clip_rect,
                0x0E,
                "NAV",
            )
        )
        return

    radius = _target_marker_radius(
        gamemem,
        extent,
        depth,
        clamp_maximum,
    )
    return CockpitHudTargetBracket(
        float(screen_x),
        float(screen_y),
        max(0.0, float(radius)),
        (0x0E, 0x0A, 0x06)[sub_index],
    )


def snapshot_cockpit_hud(
    gamemem,
    camera=None,
    modstate=None,
    *,
    compass_altimeter,
    power_meters,
    htal_meters,
    alt_htal_view,
    rear_camera_mirror,
    alt_throttle_indicator_position,
    panel_scaling,
    viewport_size,
    max_horizontal_fov_degrees,
):
    lifecycle = modstate if modstate is not None else _HUD_LIFECYCLE_STATE
    enhanced_power_meters = (
        str(power_meters).strip().lower() == "enhanced"
    )
    enhanced_htal_meters = (
        str(htal_meters).strip().lower() == "enhanced"
    )
    player_slot = int(gamemem.read_reloc_u32(ADDR_PLAYER_SLOT))
    player_entity = int(
        gamemem.read_reloc_u32(ADDR_ENTITY_BODY_TABLE + player_slot * 4)
    )
    if player_entity == 0:
        return EMPTY_COCKPIT_HUD
    player_data = bytes(gamemem.read_runtime_bytes(player_entity + 0x20, 0x30))
    mech = struct.unpack_from("<I", player_data, 0x00)[0]
    if struct.unpack_from("<I", player_data, 0x18)[0] == 0:
        return EMPTY_COCKPIT_HUD

    if mech == 0:
        return EMPTY_COCKPIT_HUD
    mech_data = bytes(gamemem.read_runtime_bytes(mech, 0x110))
    mech_body = struct.unpack_from("<I", mech_data, 0x00)[0]
    if mech_body == 0:
        return EMPTY_COCKPIT_HUD
    mech_body_data = bytes(gamemem.read_runtime_bytes(mech_body, 0x08))
    if player_slot != struct.unpack_from("<I", mech_body_data, 0x04)[0]:
        return EMPTY_COCKPIT_HUD
    hud_mode = struct.unpack_from("<i", mech_data, 0xA0)[0]
    satellite_damage = bool(
        camera and camera.get("satellite_damage_viewport")
    )
    if satellite_damage:
        # Native satellite state 2 clears the master cockpit HUD gate, then
        # draws mode-4 radar overlays directly into the degraded window. Keep
        # only that radar content; all ordinary cockpit panels stay omitted.
        target_data = bytes(
            gamemem.read_runtime_bytes(player_entity + 0xC8, 0x18)
        )
        radar = snapshot_radar_hud(
            gamemem,
            player_slot,
            mech_body,
            mech_data,
            target_data,
            camera,
            hud_mode=hud_mode,
            lifecycle=lifecycle,
            viewport_size=viewport_size,
            max_horizontal_fov_degrees=max_horizontal_fov_degrees,
            selected_target_indicators=False,
        )
        return CockpitHudSnapshot(
            panels=(),
            scene_sprites=(),
            scene_markers=(),
            target_bracket=None,
            edge_indicators=(),
            radar=radar,
            target_view=None,
            mfd_view=None,
        )
    if gamemem.read_reloc_u32(ADDR_MASTER_HUD_ENABLE) == 0:
        return EMPTY_COCKPIT_HUD

    previous_hud_mode = getattr(
        lifecycle,
        "mw2_target_panel_last_hud_mode",
        None,
    )
    transition_phase, transition_progress = _hud_transition_timeline(
        lifecycle,
        hud_mode,
        previous_hud_mode,
    )
    if transition_phase == "hidden":
        setattr(lifecycle, "mw2_target_panel_last_hud_mode", hud_mode)
        return EMPTY_COCKPIT_HUD
    panels_fully_started = hud_mode == 2 and transition_phase == "steady"
    if hud_mode == 1:
        callback_offset = 0x78
        expected_target_callback = CALLBACK_TARGET_PANEL_STARTUP
        expected_mfd_callback = CALLBACK_MFD_STARTUP
    elif hud_mode == 2:
        callback_offset = 0x7C
        expected_target_callback = CALLBACK_TARGET_PANEL
        expected_mfd_callback = CALLBACK_MFD
    elif hud_mode in (0, 3) or hud_mode > 5:
        callback_offset = 0x80
        expected_target_callback = CALLBACK_TARGET_PANEL_SHUTDOWN
        expected_mfd_callback = CALLBACK_MFD_SHUTDOWN
    else:
        return EMPTY_COCKPIT_HUD

    weapon_base = struct.unpack_from("<I", mech_data, 0x54)[0]
    active_weapon = struct.unpack_from("<i", mech_data, 0xAC)[0]
    if hud_mode == 1:
        game_tick = int(gamemem.read_reloc_i32(ADDR_GAME_TICK))
        startup_text_color = int(
            gamemem.read_reloc_u8(ADDR_CURRENT_HUD_TEXT_COLOR)
        )
    else:
        game_tick = 0
        startup_text_color = NORMAL_HUD_TEXT_COLOR
    delta = int(gamemem.delta)

    panels = []
    panel_by_id = {}
    scene_sprites = []
    scene_markers = []
    target_bracket = None
    edge_indicators = []
    meter_data = None
    masc_active = None
    target_data = None
    target_view = None
    mfd_view = None
    radar = EMPTY_RADAR_HUD
    panel_addresses = struct.unpack(
        f"<{PANEL_COUNT}I",
        bytes(gamemem.read_reloc_bytes(ADDR_PANEL_TABLE, PANEL_COUNT * 4)),
    )
    weapon_layout = _weapon_panel_layout(
        gamemem,
        lifecycle,
        mech,
        panel_addresses,
    )
    weapon_panel = _snapshot_weapon_panel(
        gamemem,
        weapon_layout,
        weapon_base,
        active_weapon,
        hud_mode,
        game_tick,
        startup_text_color,
    )
    if weapon_panel is not None:
        panels.append(weapon_panel)

    def get_panel(panel_id, bounds, content_role="panel"):
        result = panel_by_id.get(panel_id)
        if result is None:
            result = _PanelBuilder(
                panel_id,
                bounds,
                content_role=content_role,
            )
            panel_by_id[panel_id] = result
            panels.append(result)
        else:
            result.include(bounds)
        return result

    transition_extent = _panel_transition_extent(
        transition_phase,
        transition_progress,
    )

    for panel_index, panel in enumerate(panel_addresses):
        if panel == 0 or (
            weapon_layout is not None
            and panel in weapon_layout.panel_addresses
        ):
            continue

        panel_data = bytes(gamemem.read_runtime_bytes(panel, 0x84))
        if struct.unpack_from("<H", panel_data, 0x04)[0] == 0:
            continue
        callback_pointer = struct.unpack_from("<I", panel_data, callback_offset)[0]
        callback = callback_pointer - delta if callback_pointer else 0
        is_power_meter = hud_mode == 2 and callback in POWER_METER_CALLBACKS
        is_mfd = callback == expected_mfd_callback
        is_target_panel = callback == expected_target_callback
        is_target_text_panel = (
            panels_fully_started and callback == CALLBACK_TARGET_TEXT_PANEL
        )
        is_autopilot = (
            hud_mode == 2
            and panel_index == 17
            and callback == CALLBACK_AUTOPILOT
        )
        if (
            not is_power_meter
            and not is_mfd
            and not is_target_panel
            and not is_target_text_panel
            and not is_autopilot
        ):
            continue

        pane = struct.unpack_from("<I", panel_data, 0x30)[0]
        if pane == 0:
            continue
        pane_data = bytes(gamemem.read_runtime_bytes(pane + 0x04, 0x10))
        left, top, right, bottom = struct.unpack_from("<4i", pane_data)
        bounds = (left, top, right + 1, bottom + 1)

        if is_target_panel:
            left, top, right, bottom = TARGET_VIEW_PANE_RECT
            if max(transition_extent) <= 0.0:
                continue
            target_panel = get_panel(
                "target",
                (left, top, right + 1, bottom + 1),
                content_role="viewport",
            )
            target_panel.animation_extent = transition_extent
            if target_data is None:
                target_data = bytes(
                    gamemem.read_runtime_bytes(player_entity + 0xC8, 0x18)
                )
            target_view = _snapshot_target_panel(
                gamemem,
                panel_data,
                (left, top, right, bottom),
                target_data,
                panels_fully_started,
                target_panel,
            )
            continue

        if is_target_text_panel:
            if target_data is None:
                target_data = bytes(
                    gamemem.read_runtime_bytes(player_entity + 0xC8, 0x18)
                )
            target_panel = get_panel(
                "target",
                (
                    TARGET_VIEW_PANE_RECT[0],
                    TARGET_VIEW_PANE_RECT[1],
                    TARGET_VIEW_PANE_RECT[2] + 1,
                    TARGET_VIEW_PANE_RECT[3] + 1,
                ),
                content_role="viewport",
            )
            _snapshot_target_text_panel(
                gamemem,
                player_entity,
                (left, top, right, bottom),
                target_data,
                target_panel,
            )
            continue

        if is_mfd:
            left, top, right, bottom = _panel_destination_bounds(
                gamemem,
                panel_data,
                (left, top, right, bottom),
            )
            bounds = (left, top, right + 1, bottom + 1)
            if max(transition_extent) <= 0.0:
                continue
            mfd_panel = get_panel(
                "mfd",
                bounds,
            )
            mfd_panel.animation_extent = transition_extent
            mfd_view = _snapshot_mfd(
                gamemem,
                panel_data,
                mech,
                mech_data,
                mech_body,
                (left, top, right, bottom),
                mfd_panel,
                panels_fully_started,
                enhanced_htal_meters,
                alt_htal_view,
                rear_camera_mirror,
            )
            continue

        if is_autopilot:
            autopilot_panel = get_panel("autopilot", bounds)
            _snapshot_autopilot_panel(
                gamemem,
                panel_index,
                panel_data,
                (left, top, right, bottom),
                mech_data,
                autopilot_panel.texts,
            )
            continue

        text_position = struct.unpack_from("<I", panel_data, 0x34)[0]
        if text_position != 0:
            text_position_data = bytes(
                gamemem.read_runtime_bytes(text_position, 0x08)
            )
            local_x, local_y = struct.unpack_from("<2i", text_position_data)
        else:
            local_x = local_y = 0

        alternate_throttle = (
            alt_throttle_indicator_position
            and callback in (CALLBACK_THROTTLE, CALLBACK_MASC)
        )
        if alternate_throttle:
            meter_panel = get_panel(
                "throttle_alt",
                ALT_THROTTLE_PANEL_BOUNDS,
            )
            meter_panel.clip_text = False
        elif callback in (
            CALLBACK_HEAT,
            CALLBACK_HEAT_RATE,
            CALLBACK_JUMP_JETS,
        ):
            meter_panel = get_panel("heat_jump", bounds)
        elif callback == CALLBACK_THROTTLE:
            meter_panel = get_panel("throttle", bounds)
        elif callback == CALLBACK_MASC:
            meter_panel = get_panel("masc", bounds)
        else:
            raise AssertionError(f"unregistered HUD meter callback {callback:#x}")
        texts = meter_panel.texts
        fills = meter_panel.fills

        if callback == CALLBACK_MASC:
            if masc_active is None:
                masc_active = int(gamemem.read_reloc_i32(ADDR_MASC_ACTIVE)) != 0
            if masc_active and (alternate_throttle or text_position != 0):
                _append_meter_text(
                    texts,
                    panel_index,
                    "MASC",
                    NORMAL_HUD_TEXT_COLOR,
                    left + local_x,
                    top + local_y,
                    ALT_THROTTLE_MASC_Y if alternate_throttle else None,
                )
            continue

        if meter_data is None:
            meter_data = bytes(
                gamemem.read_reloc_bytes(ADDR_METER_STATE, METER_STATE_SIZE)
            )

        label = ""
        label_color = NORMAL_HUD_TEXT_COLOR
        if callback == CALLBACK_THROTTLE:
            displayed_kph, speed_is_reverse = _speed_kph(mech_data)
            label = f"{displayed_kph} kph"
            if speed_is_reverse:
                label_color = 0x06
        elif callback == CALLBACK_HEAT_RATE:
            label = "ΔH/Δt"
        else:
            label = _ascii_string(panel_data[0x10 : 0x10 + PANEL_SIZE_NAME])
        if text_position != 0 or alternate_throttle:
            _append_meter_text(
                texts,
                panel_index,
                label,
                label_color,
                left + local_x,
                top + local_y,
                ALT_THROTTLE_NEUTRAL_Y if alternate_throttle else None,
            )

        if callback == CALLBACK_THROTTLE:
            (
                _fill_width,
                max_height,
                outline_left,
                outline_top,
                _outline_right,
                outline_bottom,
                _fill_x,
                neutral_y,
            ) = struct.unpack_from("<8i", meter_data, 0x30)
            outer_left = (
                ALT_THROTTLE_BAR_LEFT
                if alternate_throttle
                else left + outline_left
            )
            outer_top = (
                ALT_THROTTLE_BAR_TOP
                if alternate_throttle
                else top + outline_top
            )
            outer_right = outer_left + THROTTLE_OUTER_WIDTH - 1
            outer_bottom = (
                ALT_THROTTLE_BAR_BOTTOM
                if alternate_throttle
                else top + outline_bottom
            )
            neutral_y = (
                ALT_THROTTLE_NEUTRAL_Y
                if alternate_throttle
                else top + neutral_y
            )
            throttle_current = struct.unpack_from("<i", meter_data, 0x0C)[0]
            control = struct.unpack_from("<I", player_data, 0x2C)[0]
            reverse = False
            if control != 0:
                control_data = bytes(
                    gamemem.read_runtime_bytes(control + 0x08, 0x28)
                )
                reverse = control_data[0x27] != 0
            inner_top = outer_top + 1
            inner_bottom = outer_bottom
            if reverse:
                height = _scaled_throttle_extent(
                    (
                        -throttle_current / 65536.0
                        if enhanced_power_meters
                        else -(throttle_current >> 16)
                    ),
                    max_height // 2,
                    inner_bottom - neutral_y - 1,
                    enhanced_power_meters,
                )
                fill_top = max(inner_top, neutral_y)
                fill_bottom = min(inner_bottom, neutral_y + height + 1)
                fill_color = 0x07
            else:
                height = _scaled_throttle_extent(
                    (
                        throttle_current / 65536.0
                        if enhanced_power_meters
                        else throttle_current >> 16
                    ),
                    max_height,
                    neutral_y - inner_top,
                    enhanced_power_meters,
                )
                fill_top = max(inner_top, neutral_y - height)
                fill_bottom = min(inner_bottom, neutral_y + 1)
                fill_color = 0x0F
            fills.append(
                CockpitHudThrottleMeter(
                    border_color_index=0x0A,
                    base_color_index=fill_color,
                    left=outer_left,
                    top=outer_top,
                    right=outer_right + 1,
                    bottom=outer_bottom + 1,
                    fill_top=fill_top,
                    fill_bottom=fill_bottom,
                    enhanced=bool(enhanced_power_meters),
                )
            )
            continue

        if callback == CALLBACK_HEAT:
            bar_x, bar_y, bar_width, bar_height = struct.unpack_from(
                "<4i", meter_data, 0x50
            )
            current_fp = struct.unpack_from("<i", meter_data, 0x00)[0]
            fill_pixels = (
                current_fp / 65536.0
                if enhanced_power_meters
                else current_fp >> 16
            )
            fill = max(
                0,
                min(fill_pixels, bar_width),
            )
            if fill == 0:
                segments = ((bar_width, 0x07),)
            elif fill >= bar_width:
                segments = ((bar_width, 0x0B),)
            else:
                edge = min(fill, bar_width - fill)
                middle_color = 0x07 if fill * 2 < bar_width else 0x0B
                segments = (
                    (edge, 0x03),
                    (bar_width - edge * 2, middle_color),
                    (edge, 0x03),
                )
        elif callback == CALLBACK_HEAT_RATE:
            bar_x, bar_y, bar_width, bar_height = struct.unpack_from(
                "<4i", meter_data, 0x60
            )
            current = struct.unpack_from("<i", meter_data, 0x18)[0]
            if enhanced_power_meters:
                fill = max(
                    0.0,
                    min(
                        bar_width,
                        current * bar_width / 0x300,
                    ),
                )
            else:
                fill = max(
                    0,
                    min(bar_width, current * bar_width // 0x300),
                )
            if current < 1:
                segments = ((bar_width, 0x07),)
            elif current < 0x300:
                segments = ((fill, 0x03), (bar_width - fill, 0x07))
            else:
                segments = ((fill, 0x0B), (bar_width - fill, 0x03))
        else:
            bar_x, bar_y, bar_width, bar_height = struct.unpack_from(
                "<4i", meter_data, 0x70
            )
            if struct.unpack_from("<i", mech_data, 0xC0)[0] < 0:
                continue
            current = struct.unpack_from("<i", meter_data, 0x24)[0]
            if current < 0:
                continue
            fill = 0
            if current > 0:
                if enhanced_power_meters:
                    fill = min(
                        bar_width,
                        current * bar_width / 0x71C,
                    )
                else:
                    fill = min(
                        bar_width,
                        (current * bar_width + 0x71B) // 0x71C,
                    )
            segments = ((fill, 0x0F), (bar_width - fill, 0x0B))

        _append_horizontal_meter(
            fills,
            left,
            top,
            bar_x,
            bar_y,
            bar_width,
            bar_height,
            segments,
            enhanced=enhanced_power_meters,
        )

    if hud_mode in (0, 1, 2, 3):
        if target_data is None:
            target_data = bytes(
                gamemem.read_runtime_bytes(player_entity + 0xC8, 0x18)
            )
        radar = snapshot_radar_hud(
            gamemem,
            player_slot,
            mech_body,
            mech_data,
            target_data,
            camera,
            hud_mode=hud_mode,
            lifecycle=lifecycle,
            transition_phase=transition_phase,
            transition_progress=transition_progress,
            viewport_size=viewport_size,
            max_horizontal_fov_degrees=max_horizontal_fov_degrees,
        )
    if hud_mode == 2:
        target_bracket = _snapshot_targeting(
            gamemem,
            mech_data,
            mech_body,
            target_data,
            camera,
            scene_sprites,
            scene_markers,
            edge_indicators,
            viewport_size,
            max_horizontal_fov_degrees,
        )
        panels.extend(
            _snapshot_compass_altimeter(
                gamemem,
                mech_data,
                mech_body,
                target_data,
                camera,
                compass_altimeter,
            )
        )

    setattr(lifecycle, "mw2_target_panel_last_hud_mode", hud_mode)
    return CockpitHudSnapshot(
        panels=tuple(panel.freeze() for panel in panels),
        scene_sprites=tuple(scene_sprites),
        scene_markers=tuple(scene_markers),
        target_bracket=target_bracket,
        edge_indicators=tuple(edge_indicators),
        radar=radar,
        target_view=target_view,
        mfd_view=mfd_view,
    )
